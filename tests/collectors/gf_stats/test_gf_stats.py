"""The gf_stats collector: Google Fonts views per family.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the synthetic fixture (ruling T2), plus one real fetch marked ``network``.
"""

import json
import logging
import re
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.ranking.gf_stats import (
    COLLECTOR,
    EXTRACT,
    HOST,
    SERIES,
    URL,
    WINDOW_DAYS,
    Settings,
    view_count,
    window,
)
from tff_catalog.config import load_config
from tff_catalog.config_model import ConfigError
from tff_catalog.corrections import RISING_WINDOWS
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.gf_stats")
PREFIX = b")]}'\n"
FAMILIES = 10  # named, distinct rows of the fixture's response


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def views(recs: list[Record]) -> dict[tuple[str, str], float | None]:
    """(family, series) -> value."""
    return {(r.key.key, r.series): r.value for r in recs if isinstance(r, Observation)}


def fixture_doc() -> list[Any]:
    body = (HTTP / "metadata-stats.json").read_bytes()
    assert body.startswith(PREFIX)
    return json.loads(body.removeprefix(PREFIX))


def write_http(directory: Path, body: bytes) -> Path:
    """A one-response mockhttp fixture serving ``body`` at ``URL``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "body.json").write_bytes(body)
    mockhttp.write_index(directory, [{"url": URL, "body": "body.json"}])
    return directory


def fetch(
    tmp: Path, http: Path, *, previous: Snapshot | None = None, settings: object = SETTINGS
) -> tuple[Store, Snapshot | None]:
    """Run ``fetch()`` offline against ``http`` into a fresh store; return it and the snapshot."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(DAY, time(6), tzinfo=UTC)),
        Fetcher(transport=mock.transport, min_interval={HOST: 0.0}, log=LOG) as fetcher,
        store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=DAY,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp / "raw"),
                previous=previous,
                settings=settings,
                log=LOG,
            )
        )
    return store, store.snapshot(COLLECTOR.name, DAY)


def snapshot_of(
    tmp: Path, rows: list[Any], day: date = DAY, data_date: date | None = None
) -> Snapshot:
    """A snapshot whose extract holds ``rows`` as given (no data date unless given)."""
    store = Store(tmp)
    with store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer:
        writer.write_jsonl(EXTRACT, rows)
        if data_date is not None:
            writer.set_data_date(data_date)
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return snap


# --- parse ------------------------------------------------------------------------------------


def test_one_observation_per_family_and_series() -> None:
    recs = parse(SNAPSHOT)
    assert all(isinstance(r, Observation) for r in recs)
    got = views(recs)
    assert len(recs) == len(got) == FAMILIES * len(SERIES)
    assert {series for _, series in got} == set(SERIES)
    assert {(r.source, r.unit, r.key.ns, r.attrs) for r in recs if isinstance(r, Observation)} == {
        ("gf_stats", "views", "gf-family", ())
    }
    assert got["Aster Sans", "year"] == 912_000_000_000.0  # the repeated row was dropped at fetch
    assert got["Birch Mono", "30day"] == 402_000_000.0
    assert got["Pebble Icons Outlined", "year"] == 1_450_000_000_000.0  # a stats-only family


def test_windows_end_on_the_data_date() -> None:
    for r in parse(SNAPSHOT):
        assert isinstance(r, Observation)
        assert r.end == DAY
        assert (r.end - r.start).days + 1 == WINDOW_DAYS[r.series]
    assert window("year", DAY) == (date(2025, 10, 2), DAY)
    assert window("30day", DAY) == (date(2026, 9, 2), DAY)
    assert window("7day", DAY) == (date(2026, 9, 25), DAY)


def test_windows_end_on_the_manifest_data_date_not_the_snapshot_date(tmp_path: Path) -> None:
    as_of = date(2026, 10, 30)
    snap = snapshot_of(tmp_path, [{"family": "Zinc Sans", "views": {"year": 7}}], DAY, as_of)
    recs = [r for r in parse(snap) if isinstance(r, Observation)]
    assert {r.end for r in recs} == {as_of}
    assert {r.series: r.start for r in recs} == {
        "year": date(2025, 10, 31),
        "90day": date(2026, 8, 2),
        "30day": date(2026, 10, 1),
        "7day": date(2026, 10, 24),
    }


def test_counts_google_left_out_or_garbled_are_none() -> None:
    got = views(parse(SNAPSHOT))
    assert got["Dune Display", "7day"] is None  # no 7-day range
    assert got["Dune Display", "year"] == 512_000_000.0
    assert (got["Lumen Sans Mono", "90day"], got["Lumen Sans Mono", "30day"]) == (None, None)
    assert got["Lumen Sans Mono", "7day"] == 5_900_000.0
    assert all(got["Opal Serif", s] is None for s in SERIES)  # no viewsByDateRange at all
    oresund = {s: got["Øresund Serif", s] for s in SERIES}  # 30day "212,000,000", 7day true
    assert oresund == {"year": 2.5e9, "90day": 6.1e8, "30day": None, "7day": None}


def test_settings_choose_the_series() -> None:
    recs = parse(SNAPSHOT, Settings(series=("year",)))
    assert len(recs) == FAMILIES
    assert {r.series for r in recs if isinstance(r, Observation)} == {"year"}


def test_parse_is_defensive_about_odd_rows(tmp_path: Path) -> None:
    rows = [
        {"family": "Zinc Sans", "views": {"year": 10, "30day": 2.0, "7day": True, "90day": -1}},
        {"family": "Zinc Sans", "views": {"year": 99}},
        {"family": "", "views": {"year": 5}},
        {"family": "Yew Serif", "views": ["year", 7]},
        {"family": "Xeno Grotesk"},
        ["not", "a", "row"],
    ]
    snap = snapshot_of(tmp_path, rows, date(2026, 11, 3))
    got = views(parse(snap, Settings(series=("year", "30day", "7day", "90day"))))
    assert got == {
        ("Zinc Sans", "year"): 10.0,
        ("Zinc Sans", "30day"): 2.0,
        ("Zinc Sans", "7day"): None,
        ("Zinc Sans", "90day"): None,
        **{("Yew Serif", s): None for s in SERIES},
        **{("Xeno Grotesk", s): None for s in SERIES},
    }
    ends = {r.end for r in parse(snap)}
    assert ends == {date(2026, 11, 3)}  # no data date: the snapshot date


def test_parse_needs_its_settings() -> None:
    ctx = ParseContext(snapshot=SNAPSHOT, settings=object(), log=LOG)
    with pytest.raises(TypeError, match="needs its Settings"):
        list(COLLECTOR.parse(ctx))


@pytest.mark.parametrize(
    ("value", "count"),
    [
        (0, 0),
        (1_798_413_537_126, 1_798_413_537_126),
        (2.5e9, 2_500_000_000),
        (-5, None),
        (1.5, None),
        (float("inf"), None),
        (float("nan"), None),
        (True, None),
        ("12,000", None),
        (None, None),
    ],
)
def test_view_count(value: object, count: int | None) -> None:
    assert view_count(value) == count


# --- fetch ------------------------------------------------------------------------------------


def test_extract_keeps_only_family_and_window_counts() -> None:
    rows = list(SNAPSHOT.iter_jsonl(EXTRACT))
    names = [r["family"] for r in rows]
    assert names == sorted(set(names))  # sorted, one row per family
    assert len(rows) == FAMILIES
    for row in rows:
        assert set(row) == {"family", "views"}, row["family"]  # no designers, totals, splits
        assert set(row["views"]) <= set(WINDOW_DAYS)  # "1day" dropped
        assert all(type(v) is int and v >= 0 for v in row["views"].values())
    raw = fixture_doc()
    assert any("designers" in r and "viewsByOS" in r for r in raw if isinstance(r, dict))


def test_manifest_dates_and_notes() -> None:
    m = SNAPSHOT.manifest
    assert m.data_date == DAY
    assert m.window == (date(2025, 10, 2), DAY)
    assert m.notes == (
        "rows without a family name skipped: 3",
        "repeated families kept once: Aster Sans",
        "unknown date ranges ignored: 1day",
    )
    assert [(f.url, f.kept) for f in m.fetched] == [(URL, False)]  # the raw body is not kept
    assert [e.path for e in m.extracts] == [EXTRACT]


def test_fetch_without_the_xssi_prefix(tmp_path: Path) -> None:
    body = (HTTP / "metadata-stats.json").read_bytes().removeprefix(PREFIX)
    _, snap = fetch(tmp_path, write_http(tmp_path / "http", body))
    assert snap is not None
    assert regen.encode(parse(snap)) == (FIXTURE / regen.EXPECTED).read_bytes()


def test_manifest_window_follows_the_longest_series(tmp_path: Path) -> None:
    settings = Settings(series=("7day", "30day"))
    _, snap = fetch(tmp_path, HTTP, settings=settings)
    assert snap is not None
    assert snap.manifest.window == (DAY - timedelta(days=29), DAY)


def test_fetch_refuses_a_list_that_shrank(tmp_path: Path) -> None:
    doc = [r for r in fixture_doc() if isinstance(r, dict)][:3]
    http = write_http(tmp_path / "http", json.dumps(doc).encode())
    with pytest.raises(ValueError, match="3 families, down from 10"):
        fetch(tmp_path, http, previous=SNAPSHOT)
    assert Store(tmp_path / "store").dates(COLLECTOR.name, complete_only=False) == []


def test_fetch_accepts_a_small_shrink_and_a_first_run(tmp_path: Path) -> None:
    doc = [r for r in fixture_doc() if isinstance(r, dict) and r.get("family") != "Elm Script"]
    http = write_http(tmp_path / "http", json.dumps(doc).encode())
    _, snap = fetch(tmp_path / "a", http, previous=SNAPSHOT)  # 9 of 10 >= 0.9
    assert snap is not None
    assert "Elm Script" not in {k for k, _ in views(parse(snap))}
    one = [r for r in doc if isinstance(r, dict) and r.get("family") == "Birch Mono"]
    http = write_http(tmp_path / "http1", json.dumps(one).encode())
    _, snap = fetch(tmp_path / "b", http)  # no previous snapshot: nothing to compare with
    assert snap is not None
    assert {k for k, _ in views(parse(snap))} == {"Birch Mono"}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b'{"error": "unavailable"}', "not a JSON array"),
        (b"[]", "names no family"),
        (b'[{"family": ""}, 7]', "names no family"),
        (
            b'[{"family": "Aster Sans", "viewsByDateRange": {"1year": {"views": 5}}}]',
            re.escape("views for year (0 of 1), 90day (0 of 1), 30day (0 of 1), 7day (0 of 1)"),
        ),
        (b"<html>maintenance</html>", "Expecting value"),
    ],
    ids=["object", "empty", "no-family", "renamed-ranges", "not-json"],
)
def test_fetch_refuses_a_body_that_is_not_the_stats(
    body: bytes, message: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match=message):
        fetch(tmp_path, write_http(tmp_path / "http", body))
    assert Store(tmp_path / "store").dates(COLLECTOR.name, complete_only=False) == []


def test_fetch_refuses_a_window_most_families_lack(tmp_path: Path) -> None:
    """A date range Google drops for most families fails the fetch (stale policy), since
    the engine's popularity fallback starts only when this source has no rows at all."""
    doc = fixture_doc()
    named = [r for r in doc if isinstance(r, dict) and "year" in r.get("viewsByDateRange", {})]
    for row in named[:5]:  # Aster Sans to Elm Script; Opal Serif has no ranges at all
        del row["viewsByDateRange"]["year"]
    http = write_http(tmp_path / "http", json.dumps(doc).encode())
    with pytest.raises(ValueError, match=re.escape("year (4 of 10) (min_coverage = 0.5)")):
        fetch(tmp_path / "a", http)
    assert Store(tmp_path / "a" / "store").dates(COLLECTOR.name, complete_only=False) == []
    loose = replace(SETTINGS, min_coverage=0.4)  # 4 of 10 is enough now
    _, snap = fetch(tmp_path / "b", http, settings=loose)
    assert snap is not None
    got = views(parse(snap, loose))
    assert [f for (f, s), v in got.items() if s == "year" and v is None] == [
        "Aster Sans",
        "Birch Mono",
        "Cobalt Serif",
        "Dune Display",
        "Elm Script",
        "Opal Serif",
    ]


def test_fetch_needs_only_the_configured_series(tmp_path: Path) -> None:
    body = b'[{"family": "Aster Sans", "viewsByDateRange": {"year": {"views": 5}}}]'
    settings = Settings(series=("year",))
    _, snap = fetch(tmp_path, write_http(tmp_path / "http", body), settings=settings)
    assert snap is not None
    assert views(parse(snap, settings)) == {("Aster Sans", "year"): 5.0}


# --- settings and the engine ------------------------------------------------------------------


def test_settings_file_matches_the_defaults() -> None:
    assert Settings() == SETTINGS


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"url": "https://example.com/metadata/stats"}, "https URL on fonts.google.com"),
        ({"url": "http://fonts.google.com/metadata/stats"}, "https URL on fonts.google.com"),
        ({"series": ()}, "at least one window"),
        ({"series": ("year", "1day")}, "'1day' not one of"),
        ({"series": ("year", "year")}, "duplicates"),
        ({"min_share": -0.1}, "min_share: must be between 0 and 1"),
        ({"min_coverage": 1.5}, "min_coverage: must be between 0 and 1"),
        ({"min_coverage": float("nan")}, "min_coverage: must be between 0 and 1"),
    ],
    ids=[
        "other-host",
        "plain-http",
        "no-series",
        "unknown-series",
        "repeated-series",
        "share",
        "coverage",
        "coverage-nan",
    ],
)
def test_settings_are_checked(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**changes)


def test_engine_sources_read_series_this_collector_emits() -> None:
    sources = load_config(Paths.for_root(ROOT)).ranking.sources.all()
    reading = {n: s for n, s in sources.items() if s.collector == COLLECTOR.name}
    assert reading, "no engine source reads gf_stats"
    for name, src in reading.items():
        assert src.series in SETTINGS.series, name
        assert src.group == COLLECTOR.group, name
        assert src.publish_raw is False, f"{name}: ruling T2 forbids publishing Google's views"
        rising = RISING_WINDOWS.get(name)
        assert rising is None or rising in SETTINGS.series, f"{name}: Rising reads {rising}"


# --- the real endpoint ------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real GET of /metadata/stats (about 220 KB on the wire), parsed offline."""
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    with (
        Fetcher(log=LOG) as fetcher,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
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
    got = views(recs)
    families = {family for family, _ in got}
    assert len(families) >= 1500
    year = [v for (_, s), v in got.items() if s == "year"]
    assert sum(v is not None for v in year) >= 0.95 * len(families)
    inter = {s: got["Inter", s] for s in SERIES}
    assert all(v is not None and v > 0 for v in inter.values())
    assert inter["7day"] <= inter["30day"] <= inter["90day"] <= inter["year"]  # type: ignore[operator]
