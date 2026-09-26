"""The google_metadata collector: the live Google Fonts family list.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the synthetic fixture (ruling T2), plus one real fetch marked ``network``.
"""

import json
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.universe.google_metadata import (
    AXIS_FIELDS,
    COLLECTOR,
    EXTRACT,
    HOST,
    KEPT_FIELDS,
    URL,
    Settings,
    check_shrink,
    drop_code,
    previous_families,
    universe_record,
)
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record, UniverseRecord
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.google_metadata")
PREFIX = b")]}'\n"


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def universe(recs: list[Record]) -> dict[str, UniverseRecord]:
    return {r.family: r for r in recs if isinstance(r, UniverseRecord)}


def ranks(recs: list[Record]) -> dict[str, float | None]:
    return {r.key.key: r.value for r in recs if isinstance(r, Observation)}


def fixture_doc() -> dict[str, Any]:
    body = (HTTP / "metadata-fonts.json").read_bytes()
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


# --- parse ------------------------------------------------------------------------------------


def test_universe_records_carry_googles_facts() -> None:
    fams = universe(parse(SNAPSHOT))
    aster = fams["Aster Sans"]
    assert aster.subsets == ("cyrillic", "latin", "latin-ext", "vietnamese")  # no "menu"
    assert (aster.category, aster.primary_script, aster.display_name) == ("Sans Serif", None, None)
    assert (aster.variable, aster.is_monospace, aster.added) == (True, False, date(2019, 3, 14))
    assert aster.urls == (("specimen", "https://fonts.google.com/specimen/Aster+Sans"),)
    assert (aster.status, aster.drop, aster.files, aster.names) == ("live", None, (), ())
    assert aster.latin_languages is None  # Google's empty languages list says nothing
    assert dict(aster.attrs) == {
        "axes": "opsz,wght",
        "is_brand_font": False,
        "is_noto": False,
        "last_modified": "2026-05-10",
        "stroke": "Sans Serif",
    }
    fjord = fams["Fjord Sans JP"]
    assert (fjord.primary_script, fjord.display_name) == ("Jpan", "Fjord Sans Japanese")
    assert fjord.latin_languages == 2  # en_Latn and fr_Latn of three languages
    assert fams["Cobalt Serif"].variable is False
    assert fams["Elm Script"].display_name == "Elm Script Handwriting"


def test_monospace_from_category_or_classification() -> None:
    fams = universe(parse(SNAPSHOT))
    mono = sorted(name for name, r in fams.items() if r.is_monospace)
    assert mono == ["Birch Mono", "Lumen Sans Mono"]  # category Monospace; classification only
    assert all(r.is_monospace is not None for r in fams.values())


def test_drop_codes() -> None:
    drops = {name: r.drop for name, r in universe(parse(SNAPSHOT)).items() if r.drop}
    assert drops == {
        "Harbor Barcode 39": "barcode",  # the name beats the Symbols classification
        "Iris Math": "math",
        "Juniper Emoji": "emoji",
        "Kestrel Color": "emoji",  # emoji is its only subset
        "Moss Blocks": "symbol",
        "Nimbus Brand": "proprietary",  # isOpenSource false
        "Quill Musical Notation": "music",
    }


def test_icon_and_emoji_rules() -> None:
    settings = Settings()
    assert drop_code({"family": "Material Symbols Rounded"}, settings) == "icon"
    assert drop_code({"family": "Material Sans"}, settings) is None
    playful = {"family": "Playful Sans", "subsets": ["menu", "emoji", "latin"]}
    assert drop_code(playful, settings) is None  # emoji beside latin is still text


def test_one_rank_per_family() -> None:
    recs = parse(SNAPSHOT)
    fams, rank = universe(recs), ranks(recs)
    assert len(fams) == 16
    assert set(rank) == set(fams)
    obs = [r for r in recs if isinstance(r, Observation)]
    assert {(o.series, o.unit, o.start, o.end) for o in obs} == {("popularity", "rank", DAY, DAY)}
    assert rank["Aster Sans"] == 3.0  # the repeated row (rank 4) was dropped at fetch
    assert rank["Opal Serif"] is None  # no rank: in the frame, no value
    assert universe(recs)["Opal Serif"].added is None  # an empty dateAdded


def test_parse_is_defensive_about_old_or_odd_rows(tmp_path: Path) -> None:
    rows = [
        {"family": "Zinc Sans", "popularity": True, "dateAdded": "2026-13-01",
         "subsets": ["", "latin"]},
        {"family": "Zinc Sans", "popularity": 5},
        {"family": "", "popularity": 6},
        {"family": "Yew Serif", "popularity": 0, "classifications": "Serif",
         "axes": [{"min": 1}]},
    ]  # fmt: skip
    store = Store(tmp_path)
    with store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer:
        writer.write_jsonl(EXTRACT, rows)
    snap = store.snapshot(COLLECTOR.name, DAY)
    assert snap is not None
    recs = parse(snap)
    fams = universe(recs)
    assert sorted(fams) == ["Yew Serif", "Zinc Sans"]
    assert ranks(recs) == {"Yew Serif": None, "Zinc Sans": None}  # a bool or 0 is no rank
    zinc, yew = fams["Zinc Sans"], fams["Yew Serif"]
    assert (zinc.added, zinc.subsets) == (None, ("latin",))
    # No category, classification or axes list: no spacing or format claim, which
    # facts.py would otherwise read as "proportional" and "static".
    assert (zinc.is_monospace, zinc.variable, zinc.category) == (None, None, None)
    assert (yew.classifications, yew.is_monospace) == ((), None)
    assert yew.variable is False  # an axes list, but no tagged axis


def test_a_menu_only_family_has_no_subset_and_is_not_dropped() -> None:
    """Google serves some Latin families only as ``menu`` (the Playwrite school scripts
    on 2026-09-26): no subset survives, so rule A, not this collector, decides them."""
    row = {
        "family": "Vale Guides",
        "category": "Handwriting",
        "classifications": [],
        "subsets": ["menu"],
        "axes": [{"tag": "wght", "min": 100.0, "max": 400.0, "defaultValue": 400.0}],
        "primaryScript": "",
        "isOpenSource": True,
    }
    rec = universe_record(row, Settings())
    assert (rec.subsets, rec.primary_script, rec.drop) == ((), None, None)
    assert (rec.is_monospace, rec.variable) == (False, True)


def test_parse_needs_its_settings() -> None:
    ctx = ParseContext(snapshot=SNAPSHOT, settings=object(), log=LOG)
    with pytest.raises(TypeError, match="needs its Settings"):
        list(COLLECTOR.parse(ctx))


# --- fetch ------------------------------------------------------------------------------------


def test_extract_keeps_only_the_listed_fields() -> None:
    rows = list(SNAPSHOT.iter_jsonl(EXTRACT))
    names = [r["family"] for r in rows]
    assert names == sorted(set(names))  # sorted, one row per family
    for row in rows:
        assert set(row) <= set(KEPT_FIELDS), row["family"]
        assert all(set(axis) <= set(AXIS_FIELDS) for axis in row["axes"])
    assert not {"designers", "size", "fonts", "trending", "defaultSort"} & set(KEPT_FIELDS)
    assert SNAPSHOT.manifest.data_date == DAY
    assert SNAPSHOT.manifest.notes == (
        "rows without a family name skipped: 1",
        "repeated families kept once: Aster Sans",
    )


def test_fetch_without_the_xssi_prefix(tmp_path: Path) -> None:
    body = (HTTP / "metadata-fonts.json").read_bytes().removeprefix(PREFIX)
    _, snap = fetch(tmp_path, write_http(tmp_path / "http", body))
    assert snap is not None
    assert regen.encode(parse(snap)) == (FIXTURE / regen.EXPECTED).read_bytes()
    fetched = snap.manifest.fetched
    assert [(f.url, f.kept) for f in fetched] == [(URL, False)]  # the raw body is not kept
    assert [e.path for e in snap.manifest.extracts] == [EXTRACT]


def test_fetch_refuses_a_list_that_shrank(tmp_path: Path) -> None:
    doc = fixture_doc()
    doc["familyMetadataList"] = doc["familyMetadataList"][:3]
    http = write_http(tmp_path / "http", json.dumps(doc).encode())
    with pytest.raises(ValueError, match="3 families, down from 16"):
        fetch(tmp_path, http, previous=SNAPSHOT)
    store = Store(tmp_path / "store")
    assert store.dates(COLLECTOR.name, complete_only=False) == []  # nothing half-written


def test_fetch_accepts_a_small_shrink_and_a_first_run(tmp_path: Path) -> None:
    doc = fixture_doc()
    doc["familyMetadataList"] = [
        f for f in doc["familyMetadataList"] if f["family"] != "Moss Blocks"
    ]
    http = write_http(tmp_path / "http", json.dumps(doc).encode())
    _, snap = fetch(tmp_path / "a", http, previous=SNAPSHOT)  # 15 of 16 >= 0.9
    assert snap is not None
    assert "Moss Blocks" not in universe(parse(snap))
    doc["familyMetadataList"] = doc["familyMetadataList"][:1]
    http = write_http(tmp_path / "http1", json.dumps(doc).encode())
    _, snap = fetch(tmp_path / "b", http)  # no previous snapshot: nothing to compare with
    assert snap is not None
    assert len(universe(parse(snap))) == 1


def test_shrink_check_boundary() -> None:
    check_shrink(9, 10, 0.9)  # exactly min_share of the previous families passes
    with pytest.raises(ValueError, match="8 families, down from 10"):
        check_shrink(8, 10, 0.9)
    check_shrink(1, 10, 0.0)  # min_share 0 switches the check off
    check_shrink(1, None, 0.9)  # no previous snapshot


def test_a_previous_snapshot_without_the_extract_is_no_baseline(tmp_path: Path) -> None:
    """An earlier snapshot of another extract layout gives no count to compare with."""
    store = Store(tmp_path)
    with store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer:
        writer.write_json("other.json", [1, 2, 3])
    other = store.snapshot(COLLECTOR.name, DAY)
    assert other is not None
    assert previous_families(other) is None
    assert previous_families(None) is None
    assert previous_families(SNAPSHOT) == 16


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b'{"error": "unavailable"}', "not the Google Fonts family list"),
        (b'[{"family": "Aster Sans"}]', "not the Google Fonts family list"),
        (b'{"familyMetadataList": [{"family": ""}]}', "names no family"),
        (b"<html>maintenance</html>", "Expecting value"),
    ],
    ids=["no-list", "array", "no-family", "not-json"],
)
def test_fetch_refuses_a_body_that_is_not_the_list(
    body: bytes, message: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match=message):
        fetch(tmp_path, write_http(tmp_path / "http", body))
    assert Store(tmp_path / "store").dates(COLLECTOR.name, complete_only=False) == []


# --- settings ---------------------------------------------------------------------------------


def test_settings_file_matches_the_defaults() -> None:
    assert Settings() == SETTINGS


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"drop_names": (("glyph", r"\bGlyph\b"),)}, "'glyph' is not one of"),
        ({"drop_names": (("icon", "(unclosed"),)}, "bad pattern"),
        ({"url": "https://example.com/metadata/fonts"}, "https URL on fonts.google.com"),
        ({"url": "http://fonts.google.com/metadata/fonts"}, "https URL on fonts.google.com"),
        ({"min_share": 1.5}, "between 0 and 1"),
    ],
    ids=["unknown-code", "bad-regex", "other-host", "plain-http", "share-over-1"],
)
def test_settings_are_checked(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**changes)


# --- the real endpoint ------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real GET of /metadata/fonts (about 150 KB on the wire), parsed offline."""
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
    recs = COLLECTOR.parse(ParseContext(snapshot=snap, settings=SETTINGS, log=LOG))
    recs = list(recs)
    fams, rank = universe(recs), ranks(recs)
    assert len(fams) >= 1500
    assert sum(v is not None for v in rank.values()) >= 0.95 * len(fams)
    inter = fams["Inter"]
    assert (inter.category, inter.drop, inter.variable) == ("Sans Serif", None, True)
    assert "latin" in inter.subsets
    assert rank["Inter"] is not None
    assert rank["Inter"] <= 50
    # Milestone-1 spot checks, and the drop rules on families that have long been listed.
    assert fams["JetBrains Mono"].is_monospace is True
    assert fams["Noto Sans JP"].primary_script == "Jpan"
    drops = {
        "Libre Barcode 39": "barcode",
        "Noto Color Emoji": "emoji",
        "Noto Sans Math": "math",
        "Noto Music": "music",
        "Noto Sans Symbols": "symbol",
    }
    assert {name: fams[name].drop for name in drops} == drops
    assert sum(r.drop is not None for r in fams.values()) <= 0.03 * len(fams)
