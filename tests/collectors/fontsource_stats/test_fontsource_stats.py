"""The fontsource_stats collector: monthly jsDelivr hits and npm downloads per Fontsource package.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the trimmed real fixture (ruling T1) and on small bodies built here, plus one
real fetch marked ``network``.
"""

import dataclasses
import json
import logging
import math
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.collectors.fontsource_stats.build_fixture import AGE, DAY, IDS, run_fetch, write_http
from tests.helpers import ROOT, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.ranking.fontsource_stats import (
    COLLECTOR,
    EXTRACT,
    FIELDS,
    SCOPES,
    Settings,
    count,
    extract_rows,
    hit_packages,
    listed,
    package,
    window,
)
from tff_catalog.config import load_config
from tff_catalog.config_model import ConfigError, from_mapping, load_toml
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record, SourceKey
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.fontsource_stats")
HITS, DOWNLOADS = "jsdelivr_monthly", "npm_monthly"
WINDOW = (date(2026, 8, 26), date(2026, 9, 24))  # 06:00 on DAY less AGE, less a day; 30 days
VARIABLE = 9  # fixture ids with a variable package


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def values(recs: list[Record]) -> dict[tuple[str, str, str], float | None]:
    """(key, package attr, series) -> value."""
    out = {}
    for r in recs:
        assert isinstance(r, Observation)
        pkg = str(dict(r.attrs).get("package", r.key.key))
        assert (r.key.key, pkg, r.series) not in out, "one row per key, package and series"
        out[r.key.key, pkg, r.series] = r.value
    return out


def fixture_doc() -> dict[str, Any]:
    return json.loads((HTTP / "stats.json").read_bytes())


def body(doc: object) -> bytes:
    return json.dumps(doc, separators=(",", ":")).encode()


def stats(hits: object = 5, downloads: object = 3, **more: object) -> dict[str, object]:
    """One variant object as Fontsource writes it, lifetime totals included."""
    return {
        "npmDownloadMonthly": downloads,
        "npmDownloadTotal": 900,
        "jsDelivrHitsMonthly": hits,
        "jsDelivrHitsTotal": 9000,
        **more,
    }


def snapshot_of(
    tmp: Path, rows: list[Any], *, day: date = DAY, span: tuple[date, date] | None = WINDOW
) -> Snapshot:
    """A snapshot whose extract holds ``rows`` as given."""
    store = Store(tmp)
    with store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer:
        writer.write_jsonl(EXTRACT, rows)
        if span is not None:
            writer.set_window(*span)
            writer.set_data_date(span[1])
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return snap


# --- settings ---------------------------------------------------------------------------------


def test_config_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS


def test_the_engine_source_reads_the_hits_series() -> None:
    src = load_config(Paths.for_root(ROOT)).ranking.sources.jsdelivr
    assert (src.collector, src.group, src.series) == (COLLECTOR.name, COLLECTOR.group, HITS)
    assert src.rate == "per_month"
    assert SETTINGS.hits_series == HITS


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"url": "https://example.org/v1/stats"}, "https URL on api.fontsource.org"),
        ({"url": "http://api.fontsource.org/v1/stats"}, "https URL on api.fontsource.org"),
        ({"downloads_series": HITS}, "must differ"),
        ({"hits_series": ""}, "non-empty"),
        ({"key_namespace": "gf-family"}, "is not one of"),
        ({"window_days": 365}, "28 to 31"),
        ({"lag_days": -1}, "0 to 7"),
        ({"min_share": 1.5}, "between 0 and 1"),
        ({"totals": True}, "unknown key"),
    ],
)
def test_settings_are_checked(change: dict[str, object], message: str) -> None:
    data = load_toml(ROOT / "config" / "sources" / f"{COLLECTOR.name}.toml") | change
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, data, where="sources/fontsource_stats.toml")


# --- parse -------------------------------------------------------------------------------------


def test_one_row_per_package_field_and_id() -> None:
    recs = parse(SNAPSHOT)
    assert len(recs) == (len(IDS) + VARIABLE) * len(FIELDS)
    got = values(recs)
    assert {r.key.key for r in recs if isinstance(r, Observation)} == set(IDS)
    units = {(r.series, r.unit) for r in recs if isinstance(r, Observation)}
    assert units == {(HITS, "hits"), (DOWNLOADS, "downloads")}
    assert {r.key.ns for r in recs if isinstance(r, Observation)} == {"fs-id"}
    assert got["inter", "@fontsource/inter", HITS] == 149_841_880.0
    assert got["inter", "@fontsource-variable/inter", HITS] == 349_284_410.0
    assert got["inter", "@fontsource/inter", DOWNLOADS] == 7_209_595.0
    assert got["inter", "@fontsource-variable/inter", DOWNLOADS] == 7_871_827.0
    # A static-only font has no variable rows; a retired id is emitted as given.
    assert ("source-sans-pro", "@fontsource-variable/source-sans-pro", HITS) not in got
    assert got["andada", "@fontsource/andada", HITS] == 14.0
    # Zero is a count (the engine censors it), not a missing value.
    assert got["league-gothic-condensed", "@fontsource/league-gothic-condensed", HITS] == 0.0
    assert got["geist-pixel", "@fontsource/geist-pixel", DOWNLOADS] == 0.0


def test_totals_are_never_read() -> None:
    doc = fixture_doc()
    packages = [item for entry in doc.values() for v, item in entry.items() if v in SCOPES]
    monthly = {float(item[f]) for item in packages for f in FIELDS}
    lifetime = {float(v) for item in packages for f, v in item.items() if f.endswith("Total")}
    sums = {  # Fontsource's "total" variant of the fonts with two packages
        float(entry["total"][f]) for entry in doc.values() if "variable" in entry for f in FIELDS
    }
    hits = values(parse(SNAPSHOT))
    assert {v for v in hits.values() if v is not None} == monthly
    # The fixture has lifetime figures and per-font sums that are no package's monthly figure.
    assert lifetime - monthly
    assert sums - monthly
    both = (
        hits["inter", "@fontsource/inter", HITS],
        hits["inter", "@fontsource-variable/inter", HITS],
    )
    assert math.fsum(v or 0.0 for v in both) == doc["inter"]["total"]["jsDelivrHitsMonthly"]


def test_rows_cover_the_manifest_window() -> None:
    assert SNAPSHOT.manifest.window == WINDOW
    assert SNAPSHOT.manifest.data_date == WINDOW[1]
    for r in parse(SNAPSHOT):
        assert isinstance(r, Observation)
        assert (r.start, r.end) == WINDOW
        assert (r.end - r.start).days + 1 == SETTINGS.window_days


def test_npm_keys_name_the_package() -> None:
    settings = dataclasses.replace(SETTINGS, key_namespace="npm")
    recs = [r for r in parse(SNAPSHOT, settings) if isinstance(r, Observation)]
    assert {r.key.ns for r in recs} == {"npm"}
    by_key = {(r.key, r.series): r for r in recs}
    variable = by_key[SourceKey("npm", "@fontsource-variable/inter"), HITS]
    assert (variable.value, variable.attrs) == (349_284_410.0, (("fs_id", "inter"),))
    fs_values = sorted((r.series, r.value) for r in parse(SNAPSHOT) if isinstance(r, Observation))
    assert sorted((r.series, r.value) for r in recs) == fs_values


def test_parse_keeps_unusable_counts_as_none(tmp_path: Path) -> None:
    rows = [
        {"id": "aster-sans", "static": {"jsDelivrHitsMonthly": 12.0, "npmDownloadMonthly": -4}},
        {"id": "aster-sans", "static": {"jsDelivrHitsMonthly": 99}},  # repeated id: first wins
        {"id": "birch-mono", "static": {}, "variable": {"jsDelivrHitsMonthly": True}},
        {"id": "", "static": {"jsDelivrHitsMonthly": 1}},
        {"id": 7, "static": {"jsDelivrHitsMonthly": 1}},
        ["not", "a", "row"],
        {"id": "cobalt-serif", "static": "garbled", "subset": {"jsDelivrHitsMonthly": 3}},
    ]
    got = values(parse(snapshot_of(tmp_path, rows)))
    assert got == {
        ("aster-sans", "@fontsource/aster-sans", HITS): 12.0,
        ("aster-sans", "@fontsource/aster-sans", DOWNLOADS): None,
        ("birch-mono", "@fontsource/birch-mono", HITS): None,
        ("birch-mono", "@fontsource/birch-mono", DOWNLOADS): None,
        ("birch-mono", "@fontsource-variable/birch-mono", HITS): None,
        ("birch-mono", "@fontsource-variable/birch-mono", DOWNLOADS): None,
    }


def test_parse_without_a_manifest_window(tmp_path: Path) -> None:
    snap = snapshot_of(tmp_path, [{"id": "aster-sans", "static": {}}], span=None)
    starts_ends = {(r.start, r.end) for r in parse(snap) if isinstance(r, Observation)}
    assert starts_ends == {(date(2026, 8, 27), date(2026, 9, 25))}


@pytest.mark.parametrize(
    ("value", "want"),
    [
        (5, 5),
        (0, 0),
        (5.0, 5),
        (5.5, None),
        (-1, None),
        (True, None),
        ("5", None),
        (None, None),
        (math.inf, None),
        (math.nan, None),
    ],
)
def test_count(value: object, want: int | None) -> None:
    assert count(value) == want


def test_package_names_and_window() -> None:
    assert [package(v, "inter") for v in SCOPES] == [
        "@fontsource/inter",
        "@fontsource-variable/inter",
    ]
    assert window(date(2026, 9, 24), 30) == WINDOW


# --- fetch -------------------------------------------------------------------------------------


def test_fetch_keeps_only_the_monthly_fields(tmp_path: Path) -> None:
    snap = run_fetch(tmp_path, HTTP, SETTINGS)
    assert snap.read_bytes(EXTRACT) == SNAPSHOT.read_bytes(EXTRACT)
    rows = list(snap.iter_jsonl(EXTRACT))
    assert [r["id"] for r in rows] == sorted(IDS)
    for row in rows:
        assert set(row) - {"id"} <= set(SCOPES)
        for variant in SCOPES:
            assert set(row.get(variant, {})) <= set(FIELDS)
    assert snap.manifest.window == WINDOW
    assert [f.url for f in snap.manifest.fetched] == [SETTINGS.url]
    assert not snap.manifest.fetched[0].kept
    assert [e.path for e in snap.manifest.extracts] == [EXTRACT]
    assert snap.manifest.extracts[0].rows == len(IDS)
    assert snap.manifest.notes == ()


@pytest.mark.parametrize(
    ("headers", "end"),
    [
        ({}, date(2026, 9, 25)),  # no Age: the data is as of the fetch
        ({"age": "21600"}, date(2026, 9, 25)),  # served at 00:00 on DAY
        ({"age": "21601"}, date(2026, 9, 24)),  # served just before midnight
        ({"age": AGE}, WINDOW[1]),
        ({"age": "soon"}, date(2026, 9, 25)),  # unreadable: ignored
    ],
)
def test_window_ends_a_day_before_the_data_left_fontsource(
    tmp_path: Path, headers: dict[str, str], end: date
) -> None:
    http = write_http(tmp_path / "http", (HTTP / "stats.json").read_bytes(), headers)
    snap = run_fetch(tmp_path, http, SETTINGS)
    assert snap.manifest.window == window(end, 30)
    assert snap.manifest.data_date == end


def test_fetch_notes_what_it_skips(tmp_path: Path) -> None:
    doc = {
        "aster-sans": {"static": stats(), "total": stats(), "subset": stats()},
        "birch-mono": {"total": stats()},  # no package stats at all
        "cobalt-serif": ["garbled"],
        " ": {"static": stats()},
        "dune-display": {"static": stats(hits="many", downloads=2), "variable": "garbled"},
    }
    snap = run_fetch(tmp_path, write_http(tmp_path / "http", body(doc)), SETTINGS)
    assert list(snap.iter_jsonl(EXTRACT)) == [
        {"id": "aster-sans", "static": {"jsDelivrHitsMonthly": 5, "npmDownloadMonthly": 3}},
        {"id": "dune-display", "static": {"npmDownloadMonthly": 2}},
    ]
    assert snap.manifest.notes == (
        "entries without an id or a stats object skipped: ' ', 'cobalt-serif'",
        "variants that are not objects skipped: dune-display/variable",
        "ids with neither static nor variable stats skipped: birch-mono",
        "unknown variants ignored: subset",
    )


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([{"id": "inter"}], "not a JSON object"),
        ({}, "no Fontsource id"),
        ({"inter": {"total": stats()}}, "no Fontsource id"),
        ({"inter": {"static": {"jsDelivrHits": 5}}}, "no package has a positive"),
        # Every jsDelivr figure lost: an all-zero source would still count as evidence.
        ({"inter": {"static": stats(0)}, "roboto": {"static": stats(0)}}, "no package has a pos"),
    ],
)
def test_fetch_fails_on_a_broken_answer(tmp_path: Path, doc: object, message: str) -> None:
    http = write_http(tmp_path / "http", body(doc))
    with pytest.raises(ValueError, match=message):
        run_fetch(tmp_path, http, SETTINGS)
    assert not (tmp_path / "store" / COLLECTOR.name / DAY.isoformat()).exists()


def test_fetch_fails_on_a_body_that_is_not_json(tmp_path: Path) -> None:
    http = write_http(tmp_path / "http", b"<!doctype html><title>Bad gateway</title>")
    with pytest.raises(json.JSONDecodeError):
        run_fetch(tmp_path, http, SETTINGS)
    assert not (tmp_path / "store" / COLLECTOR.name / DAY.isoformat()).exists()


def test_fetch_fails_when_the_list_shrinks(tmp_path: Path) -> None:
    rows = [{"id": f"font-{i:03}", "static": {}} for i in range(16)]
    previous = snapshot_of(tmp_path / "before", rows, day=date(2026, 8, 26))
    with pytest.raises(ValueError, match="14 ids, down from 16"):
        run_fetch(tmp_path, HTTP, SETTINGS, previous=previous)
    looser = dataclasses.replace(SETTINGS, min_share=0.8)
    assert run_fetch(tmp_path, HTTP, looser, previous=previous).manifest.extracts[0].rows == 14


def test_fetch_fails_when_the_hits_vanish(tmp_path: Path) -> None:
    """Same ids, but most jsDelivr counts fell to 0: a partial answer, not a real month."""
    ids = [f"font-{i:02}" for i in range(10)]
    before = [{"id": i, "static": {"jsDelivrHitsMonthly": 50_000}} for i in ids]
    previous = snapshot_of(tmp_path / "before", before, day=date(2026, 8, 26))
    doc = {i: {"static": stats(hits=50_000 if n < 2 else 0)} for n, i in enumerate(ids)}
    http = write_http(tmp_path / "http", body(doc))
    with pytest.raises(ValueError, match="2 packages with jsDelivr hits, down from 10"):
        run_fetch(tmp_path, http, SETTINGS, previous=previous)
    looser = dataclasses.replace(SETTINGS, min_share=0.2)
    assert run_fetch(tmp_path, http, looser, previous=previous).manifest.extracts[0].rows == 10


def test_a_damaged_previous_snapshot_does_not_block_the_fetch(tmp_path: Path) -> None:
    both = {"jsDelivrHitsMonthly": 9}
    before = [{"id": f"font-{i:02}", "static": both, "variable": both} for i in range(15)]
    previous = snapshot_of(tmp_path / "before", before, day=date(2026, 8, 26))
    # Readable, it fails the fixture: 14 ids pass (0.9 of 15), 20 hit packages do not (of 30).
    with pytest.raises(ValueError, match="20 packages with jsDelivr hits, down from 30"):
        run_fetch(tmp_path, HTTP, SETTINGS, previous=previous)
    (previous.path / EXTRACT).write_bytes(b"not gzip")  # its sha256 no longer matches
    # Damaged, its hit packages are unknown, so only the id check (from the manifest) runs.
    assert run_fetch(tmp_path, HTTP, SETTINGS, previous=previous).manifest.extracts[0].rows == 14


def test_hit_packages_counts_positive_jsdelivr_counts() -> None:
    rows = [
        {"id": "a", "static": {"jsDelivrHitsMonthly": 3}, "variable": {"jsDelivrHitsMonthly": 1}},
        {"id": "b", "static": {"jsDelivrHitsMonthly": 0, "npmDownloadMonthly": 9}},
        {"id": "c", "static": {}, "variable": "garbled"},
        ["not", "a", "row"],
    ]
    assert hit_packages(rows) == 2
    assert hit_packages(list(SNAPSHOT.iter_jsonl(EXTRACT))) == len(IDS) + VARIABLE - 3


def test_long_notes_are_cut(tmp_path: Path) -> None:
    doc: dict[str, object] = {f"bad-{i:02}": "garbled" for i in range(25)}
    doc["inter"] = {"static": stats()}
    snap = run_fetch(tmp_path, write_http(tmp_path / "http", body(doc)), SETTINGS)
    (note,) = snap.manifest.notes
    shown = ", ".join(repr(f"bad-{i:02}") for i in range(20))
    assert note == f"entries without an id or a stats object skipped: {shown} and 5 more"
    assert listed(["a", "b"], limit=2) == "a, b"
    assert listed(["a", "b", "c"], limit=2) == "a, b and 1 more"


def test_extract_rows_are_sorted_and_trimmed() -> None:
    rows, notes = extract_rows(
        {"b": {"static": stats(7, 1)}, "a": {"variable": stats(0, 0), "static": stats(None, 2)}}
    )
    assert rows == [
        {
            "id": "a",
            "static": {"npmDownloadMonthly": 2},
            "variable": {"jsDelivrHitsMonthly": 0, "npmDownloadMonthly": 0},
        },
        {"id": "b", "static": {"jsDelivrHitsMonthly": 7, "npmDownloadMonthly": 1}},
    ]
    assert notes == []


# --- real data -----------------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real GET of /v1/stats (about 75 KB on the wire), parsed offline."""
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    with Fetcher(log=LOG) as fetcher, store.writer(COLLECTOR.name, day, 1) as writer:
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp_path / "raw"),
                previous=None,
                settings=SETTINGS,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    recs = list(COLLECTOR.parse(ParseContext(snapshot=snap, settings=SETTINGS, log=LOG)))
    got = values(recs)
    ids = {key for key, _, _ in got}
    assert len(ids) >= 2000
    hits = [v for (_, _, s), v in got.items() if s == HITS]
    assert sum(v is not None for v in hits) >= 0.99 * len(hits)
    inter = (
        got["inter", "@fontsource/inter", HITS],
        got["inter", "@fontsource-variable/inter", HITS],
    )
    assert all(v is not None and v > 1_000_000 for v in inter)
    assert snap.manifest.window is not None
    start, end = snap.manifest.window
    assert (end - start).days + 1 == 30
    assert day - date.resolution * 3 <= end < day
