"""The almanac collector: the Web Almanac 2025 Fonts sheet, pages tab and services tab.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the trimmed real fixture (ruling T1), plus one real fetch marked ``network``.
"""

import dataclasses
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.almanac import (
    COLLECTOR,
    EXPORT_HOST,
    EXTRACTS,
    SHEET,
    SHEETS_HOST,
    Settings,
    SheetInfo,
    Tab,
    check_export,
    check_shrink,
    export_url,
    fold_key,
    observations,
    read_table,
    same_sheet,
    to_count,
    trim_export,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher, FetchError, host_allowed
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
LATER = date(2026, 11, 1)
CRAWL = date(2025, 7, 1)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.almanac")
SIGNED_HOST = "doc-04-7g-sheets.googleusercontent.com"  # the host the fixture's 307s name
PAGES_HEADER = ("client", "family", "count", "total", "proportion")
SERVICES_HEADER = ("client", "service", "family", "count", "total", "proportion", "rank")


def parse(snapshot: Snapshot = SNAPSHOT, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def by_key(recs: list[Record]) -> dict[tuple[str, str], Observation]:
    out = {}
    for r in recs:
        assert isinstance(r, Observation)
        assert (r.series, r.key.key) not in out, f"two observations for {r.series} {r.key.key}"
        out[(r.series, r.key.key)] = r
    return out


def export_text(
    tab: Tab, rows: list[tuple[str, ...]], *, header: tuple[str, ...] | None = None
) -> str:
    """A tab export as Google writes it: preamble, blank row, header with a pivot beside it."""
    lines = [
        "Section,Test,,,,,,,",
        f'Question,"{tab.question}",,,,,,,',
        f'Normalization,"{tab.normalization}",,,,,,,',
        ",,,,,,,,",
        ",".join(header or tab.columns) + ",,SUM of proportion,client,",
    ]
    lines += [",".join(f'"{c}"' for c in row) + ",,pivot,1.00%," for row in rows]
    return "\r\n".join(lines)


def page_rows(client: str, n: int, *, start: int = 1000) -> list[tuple[str, ...]]:
    return [(client, f"Family {i}", f"{start - i:,}", "10,000", "1.00%") for i in range(n)]


GOOD_ROWS = page_rows("desktop", 12) + page_rows("mobile", 12)


def write_http(
    directory: Path,
    bodies: dict[str, str],
    *,
    settings: Settings = SETTINGS,
    redirect_host: str = SIGNED_HOST,
) -> Path:
    """A mockhttp fixture: each tab's export answers 307 to ``redirect_host``, then the body."""
    directory.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for name, body in bodies.items():
        gid = settings.tab(name).gid
        signed = f"https://{redirect_host}/export/test/{name}?format=csv&gid={gid}"
        (directory / f"{name}.csv").write_text(body, encoding="utf-8")
        index.append(
            {
                "url": export_url(settings.sheet_id, gid),
                "status": 307,
                "headers": {"location": signed},
                "body": None,
            }
        )
        index.append({"url": signed, "body": f"{name}.csv"})
    mockhttp.write_index(directory, index)
    return directory


def fixture_bodies() -> dict[str, str]:
    return {n: (HTTP / f"{n}.csv").read_text(encoding="utf-8") for n in ("pages", "services")}


def fetch(
    tmp: Path,
    http: Path,
    *,
    day: date = DAY,
    previous: Snapshot | None = None,
    settings: Settings = SETTINGS,
) -> tuple[Snapshot | None, list[str]]:
    """Run ``fetch()`` offline into a fresh store; return the snapshot and the requested URLs."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(
            transport=mock.transport, min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0), log=LOG
        ) as fetcher,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp / "raw", keep=True),
                previous=previous,
                settings=settings,
                log=LOG,
            )
        )
    return store.snapshot(COLLECTOR.name, day), mock.urls()


# --- parse ------------------------------------------------------------------------------------


def test_the_pages_tab_gives_one_observation_per_family_and_client() -> None:
    obs = by_key(parse())
    roboto = obs[("pages/mobile", "Roboto")]
    assert roboto.value == 3658686.0
    assert roboto.unit == "pages"
    assert roboto.key.ns == "almanac-name"
    assert (roboto.start, roboto.end) == (CRAWL, CRAWL)
    assert dict(roboto.attrs) == {"total": 15426041, "rank": 2, "folded_rows": 2}
    assert dict(obs[("pages/desktop", "Inter")].attrs) == {"total": 12155048, "rank": 6}
    pages = {s for s, _ in obs if s.startswith("pages/")}
    assert pages == {"pages/desktop", "pages/mobile"}


def test_case_duplicates_fold_at_the_largest_count_never_the_sum() -> None:
    obs = by_key(parse())
    keys = {k for s, k in obs if s == "pages/mobile"}
    assert {"roboto", "open sans", "poppins", "playfair display"}.isdisjoint(keys)
    assert obs[("pages/mobile", "Open Sans")].value == 2776255.0  # not + 120,540
    assert obs[("pages/mobile", "Playfair Display")].value == 518612.0
    assert dict(obs[("pages/mobile", "Playfair Display")].attrs)["folded_rows"] == 2
    flaticon = obs[("requests/desktop/self-hosted", "Flaticon")]
    assert (flaticon.value, dict(flaticon.attrs)["folded_rows"]) == (70065.0, 2)


def test_exact_repeats_count_once() -> None:
    alright = by_key(parse())[("requests/desktop/typenetwork.com", "Alright Web")]
    assert alright.value == 41.0  # two identical rows of 41, not 82
    assert dict(alright.attrs)["folded_rows"] == 2


def test_rows_without_a_family_are_skipped_but_keep_their_rank() -> None:
    obs = by_key(parse())
    assert all(k for _, k in obs)
    # self-hosted desktop lists Font Awesome, then an empty name, then Roboto.
    assert dict(obs[("requests/desktop/self-hosted", "Roboto")].attrs)["rank"] == 3
    assert ("requests/desktop/typesquare.com", "Adobe Blank") in obs


def test_the_services_tab_gives_a_series_per_client_and_service() -> None:
    obs = by_key(parse())
    google = obs[("requests/desktop/Google", "Roboto")]
    assert (google.value, google.unit) == (5409236.0, "requests")
    assert dict(google.attrs) == {"total": 58750886, "rank": 1}
    assert ("requests/desktop/fontplus.jp", "FOT-筑紫A丸ゴシック Std") in obs
    services = {s for s, _ in obs if s.startswith("requests/")}
    assert "requests/mobile/fontawesome.com" in services
    assert len(obs) == 47


def test_parse_dates_come_from_the_snapshot_not_the_settings() -> None:
    later = dataclasses.replace(SETTINGS, edition=2026, crawl_date=date(2026, 7, 1))
    assert regen.encode(parse(settings=later)) == regen.encode(parse())


@pytest.mark.parametrize("name", ["pages", "services"])
def test_parse_does_not_depend_on_row_order(name: str) -> None:
    header, rows = read_table(SNAPSHOT, EXTRACTS[name])
    forward = observations(name, header, rows, CRAWL)
    assert set(forward) == set(observations(name, header, rows[::-1], CRAWL))
    assert len(set(forward)) == len(forward)


def test_ties_keep_the_first_spelling_in_code_point_order() -> None:
    rows = [("mobile", "abc", "5", "10", "50%"), ("mobile", "ABC", "5", "10", "50%")]
    (obs,) = observations("pages", PAGES_HEADER, rows, CRAWL)
    assert obs.key.key == "ABC"
    assert dict(obs.attrs) == {"total": 10, "rank": 1, "folded_rows": 2}


def test_parse_rejects_extracts_it_cannot_read() -> None:
    with pytest.raises(ValueError, match="lacks column"):
        observations("services", PAGES_HEADER, [], CRAWL)
    with pytest.raises(ValueError, match="not a whole number"):
        observations("pages", PAGES_HEADER, [("mobile", "X", "1.5", "10", "")], CRAWL)
    with pytest.raises(ValueError, match="bad client"):
        observations("pages", PAGES_HEADER, [("a/b", "X", "1", "10", "")], CRAWL)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("Open Sans", "open sans", True),
        ("Open  Sans ", "OPEN SANS", True),
        ("Straße", "STRASSE", True),
        ("OpenSans", "Open Sans", False),
    ],
)
def test_fold_key(a: str, b: str, same: bool) -> None:
    assert (fold_key(a) == fold_key(b)) is same


@pytest.mark.parametrize(
    ("text", "value"), [("2,758,095", 2758095), ("41", 41), (" 1,000 ", 1000), ("0", 0)]
)
def test_to_count_reads_the_sheets_numbers(text: str, value: int) -> None:
    assert to_count(text) == value


@pytest.mark.parametrize("text", ["", "1,2345", "-3", "1.5", "12%", "1 000"])
def test_to_count_rejects_anything_else(text: str) -> None:
    with pytest.raises(ValueError, match="not a whole number"):
        to_count(text)


def test_sheet_info_reads_what_fetch_wrote() -> None:
    info = SheetInfo.from_json(SNAPSHOT.load_json(SHEET))
    assert (info.edition, info.crawl_date) == (2025, CRAWL)
    assert info.gid("pages") == 1668708562
    for bad in [{}, {"schema": 1, "edition": 2025, "sheet_id": "x", "crawl_date": "July"}]:
        with pytest.raises(ValueError, match=SHEET):
            SheetInfo.from_json(bad)


# --- the export ---------------------------------------------------------------------------------


def test_trim_finds_the_header_and_cuts_the_pivot_columns() -> None:
    export = trim_export((HTTP / "pages.csv").read_text(encoding="utf-8"))
    assert export.header == PAGES_HEADER
    assert export.preamble_value("Normalization") == "Pages"
    assert export.preamble_value("Section") == "Design"
    assert len(export.rows) == 30
    assert export.rows[0] == ("desktop", "Font Awesome", "4,702,803", "12,155,048", "38.69%")
    assert all(len(r) == 5 for r in export.rows)


def test_trim_drops_rows_that_hold_only_pivot_cells() -> None:
    text = "client,family,count,,pivot\r\nmobile,A,1,,x\r\n,,,,y\r\n\r\nmobile,B,2,,\r\n"
    export = trim_export(text)
    assert export.header == ("client", "family", "count")
    assert export.rows == (("mobile", "A", "1"), ("mobile", "B", "2"))


def test_trim_refuses_a_page_without_a_header_row() -> None:
    with pytest.raises(ValueError, match="no header row"):
        trim_export("<!DOCTYPE html><html><body>Sign in</body></html>")


def test_check_export_accepts_the_fixture() -> None:
    for name in ("pages", "services"):
        text = (HTTP / f"{name}.csv").read_text(encoding="utf-8")
        check_export(trim_export(text), SETTINGS.tab(name), name, SETTINGS)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"question": "Which families are popular in HTML?"}, "Question"),
        ({"normalization": "Requests"}, "Normalization"),
        ({"header": ("client", "family", "pages", "total", "proportion")}, "columns"),
        ({"rows": [("mobile", "X", "n/a", "10", "1%"), *GOOD_ROWS]}, "count"),
        ({"rows": [("mobile", "X", "1", "", "1%"), *GOOD_ROWS]}, "total"),
        ({"rows": [("mobile/x", "X", "1", "10", "1%"), *GOOD_ROWS]}, "bad client"),
        ({"rows": page_rows("desktop", 12)}, "client 'mobile'"),
        ({"rows": page_rows("desktop", 12) + page_rows("mobile", 9)}, "min_rows"),
    ],
)
def test_check_export_refuses_what_is_not_the_configured_tab(
    change: dict[str, Any], message: str
) -> None:
    tab = SETTINGS.pages
    shown = dataclasses.replace(
        tab,
        question=change.get("question", tab.question),
        normalization=change.get("normalization", tab.normalization),
    )
    text = export_text(shown, change.get("rows", GOOD_ROWS), header=change.get("header"))
    with pytest.raises(ValueError, match=message):
        check_export(trim_export(text), tab, "pages", SETTINGS)


def service_rows(client: str, n: int, service: str = "Google") -> list[tuple[str, ...]]:
    return [
        (client, service, f"Family {i}", f"{1000 - i:,}", "10,000", "1.00%", str(i + 1))
        for i in range(n)
    ]


@pytest.mark.parametrize("service", ["", "fonts/adobe"])
def test_check_export_refuses_a_service_unusable_in_a_series(service: str) -> None:
    tab = SETTINGS.services
    rows = service_rows("desktop", 12) + service_rows("mobile", 12)
    check_export(trim_export(export_text(tab, rows)), tab, "services", SETTINGS)
    rows[3] = service_rows("desktop", 4, service)[3]
    with pytest.raises(ValueError, match="row 4 has a bad service"):
        check_export(trim_export(export_text(tab, rows)), tab, "services", SETTINGS)


def test_the_shrink_check_allows_min_share_and_compares_only_the_same_tab() -> None:
    check_shrink(27, "pages", SNAPSHOT, 0.9)  # 30 rows before, and 27 is exactly 0.9 of them
    with pytest.raises(ValueError, match="26 rows, down from 30"):
        check_shrink(26, "pages", SNAPSHOT, 0.9)
    check_shrink(0, "pages", None, 0.9)
    moved = dataclasses.replace(SETTINGS, pages=dataclasses.replace(SETTINGS.pages, gid=1))
    assert same_sheet(SNAPSHOT, moved, "pages") is None
    assert same_sheet(SNAPSHOT, moved, "services") is SNAPSHOT
    assert same_sheet(None, SETTINGS, "pages") is None


# --- settings ---------------------------------------------------------------------------------


def test_settings_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert SETTINGS.pages.gid == 1668708562  # gate M6 (a): the term
    assert SETTINGS.services.gid == 1594814478


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"sheet_id": "short"}, "sheet_id"),
        ({"crawl_date": date(2024, 6, 1)}, "crawl_date"),
        ({"clients": ["mobile", "mobile"]}, "clients"),
        ({"clients": []}, "clients"),
        ({"clients": ["mobile/lite"]}, "clients"),
        ({"min_rows": 0}, "min_rows"),
        ({"min_share": 1.5}, "min_share"),
        ({"services": {"gid": 1668708562, "question": "q", "normalization": "n",
                       "columns": list(SERVICES_HEADER)}}, "same gid"),
        ({"pages": {"gid": 1, "question": "q", "normalization": "n",
                    "columns": ["client", "count", "total"]}}, "lacks family"),
        ({"pages": {"gid": 1, "question": " ", "normalization": "n",
                    "columns": list(PAGES_HEADER)}}, "question"),
        ({"pages": {"gid": 1, "question": "q", "normalization": "n",
                    "columns": ["client", "family", "family", "count", "total"]}}, "distinct"),
        ({"tab": "pages"}, "tab"),
    ],
)  # fmt: skip
def test_settings_reject_bad_values(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, data, where="sources/almanac.toml")


# --- fetch ------------------------------------------------------------------------------------


def test_fetch_keeps_the_trimmed_tabs_and_the_edition(tmp_path: Path) -> None:
    snap, urls = fetch(tmp_path, HTTP)
    assert snap is not None
    assert [u.split("/")[2] for u in urls] == [SHEETS_HOST, SIGNED_HOST] * 2
    assert [e.path for e in snap.manifest.extracts] == ["pages.csv", "services.csv", SHEET]
    assert snap.manifest.extract("pages.csv").rows == 30
    assert snap.manifest.extract("services.csv").rows == 35
    assert snap.read_bytes("pages.csv").startswith(b"client,family,count,total,proportion\n")
    assert snap.manifest.data_date == CRAWL
    assert snap.manifest.window == (CRAWL, CRAWL)
    assert [f.kept for f in snap.manifest.fetched] == [False] * 4
    assert snap.manifest.notes == ()
    assert SheetInfo.from_json(snap.load_json(SHEET)).crawl_date == CRAWL
    raw = sorted(p.name for p in (tmp_path / "raw").iterdir())
    assert raw == ["pages-1668708562.csv", "services-1594814478.csv"]


def test_an_export_on_another_numbered_sheets_host_is_fetched(tmp_path: Path) -> None:
    """Google may answer an Actions runner with another doc-*-sheets host (EXPORT_HOST)."""
    assert EXPORT_HOST == "doc-*-sheets.googleusercontent.com"
    other = "doc-0s-9x-sheets.googleusercontent.com"
    snap, urls = fetch(
        tmp_path, write_http(tmp_path / "http", fixture_bodies(), redirect_host=other)
    )
    assert snap is not None
    assert [u.split("/")[2] for u in urls] == [SHEETS_HOST, other] * 2


@pytest.mark.parametrize(
    ("host", "hint"),
    [
        ("doc-0s-9x-sheets.example", "EXPORT_HOST"),
        ("lh3.googleusercontent.com", "EXPORT_HOST"),  # the pattern opens no other host there
        ("accounts.google.com", "no longer public"),
    ],
)
def test_an_export_redirected_to_another_host_fails_naming_it(
    tmp_path: Path, host: str, hint: str
) -> None:
    http = write_http(tmp_path / "http", fixture_bodies(), redirect_host=host)
    with pytest.raises(FetchError, match=rf"host '{host}' is not in .*{hint}"):
        fetch(tmp_path, http)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_an_html_page_instead_of_the_export_fails_naming_the_tab(tmp_path: Path) -> None:
    bodies = fixture_bodies()
    bodies["services"] = "<!DOCTYPE html><html><body>Sorry, unable to open the file.</body></html>"
    with pytest.raises(ValueError, match=r"services tab \(gid 1594814478, .*\): no header row"):
        fetch(tmp_path, write_http(tmp_path / "http", bodies))
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_a_byte_order_mark_is_ignored(tmp_path: Path) -> None:
    bodies = {name: "﻿" + body for name, body in fixture_bodies().items()}
    snap, _ = fetch(tmp_path, write_http(tmp_path / "http", bodies))
    assert snap is not None
    assert snap.manifest.extracts == SNAPSHOT.manifest.extracts


def test_an_edition_whose_header_starts_elsewhere_is_a_config_change(tmp_path: Path) -> None:
    tab = dataclasses.replace(SETTINGS.pages, columns=("crawl", *PAGES_HEADER))
    edition = dataclasses.replace(SETTINGS, pages=tab)
    bodies = fixture_bodies()
    bodies["pages"] = export_text(tab, [("2025-07-01", *row) for row in GOOD_ROWS])
    with pytest.raises(ValueError, match=r"no header row .*'client'"):
        fetch(tmp_path / "old", write_http(tmp_path / "http", bodies))
    http = write_http(tmp_path / "http", bodies, settings=edition)
    snap, _ = fetch(tmp_path / "new", http, settings=edition)
    assert snap is not None
    assert snap.read_bytes("pages.csv").startswith(b"crawl,client,family,count,total,proportion\n")
    first = by_key(parse(snap, edition))[("pages/mobile", "Family 0")]
    assert (first.value, dict(first.attrs)) == (1000.0, {"total": 10000, "rank": 1})


def test_a_changed_header_fails_the_fetch(tmp_path: Path) -> None:
    bodies = fixture_bodies()
    bodies["pages"] = bodies["pages"].replace("client,family,count,", "client,family,pages,", 1)
    with pytest.raises(ValueError, match="columns"):
        fetch(tmp_path, write_http(tmp_path / "http", bodies))


def test_the_same_sheet_again_is_noted_as_unchanged(tmp_path: Path) -> None:
    snap, _ = fetch(tmp_path, HTTP, day=LATER, previous=SNAPSHOT)
    assert snap is not None
    assert snap.manifest.extracts == SNAPSHOT.manifest.extracts
    assert snap.manifest.notes == (
        f"pages.csv unchanged since the {DAY} snapshot of the same sheet",
        f"services.csv unchanged since the {DAY} snapshot of the same sheet",
    )


def test_an_edited_sheet_is_noted_as_changed(tmp_path: Path) -> None:
    bodies = fixture_bodies()
    bodies["pages"] = bodies["pages"].replace('"3,658,686"', '"3,658,687"')
    snap, _ = fetch(tmp_path, write_http(tmp_path / "http", bodies), day=LATER, previous=SNAPSHOT)
    assert snap is not None
    assert snap.manifest.notes[0] == f"pages.csv changed since the {DAY} snapshot of the same sheet"
    assert by_key(parse(snap))[("pages/mobile", "Roboto")].value == 3658687.0


def test_a_shrunken_tab_of_the_same_sheet_fails_the_fetch(tmp_path: Path) -> None:
    bodies = fixture_bodies()
    bodies["pages"] = export_text(SETTINGS.pages, GOOD_ROWS)
    http = write_http(tmp_path / "http", bodies)
    with pytest.raises(ValueError, match="down from 30"):
        fetch(tmp_path, http, day=LATER, previous=SNAPSHOT)
    # A new edition (another sheet) is not compared with the old one.
    other = dataclasses.replace(SETTINGS, sheet_id="2026" + SETTINGS.sheet_id[4:])
    http = write_http(tmp_path / "http2", bodies, settings=other)
    snap, _ = fetch(tmp_path / "second", http, day=LATER, previous=SNAPSHOT, settings=other)
    assert snap is not None
    assert snap.manifest.extract("pages.csv").rows == 24
    assert snap.manifest.notes == ()


def test_the_export_url_names_the_sheet_and_tab() -> None:
    assert export_url(SETTINGS.sheet_id, 1668708562) == (
        f"https://docs.google.com/spreadsheets/d/{SETTINGS.sheet_id}/export"
        "?format=csv&gid=1668708562"
    )
    assert EXTRACTS == {"pages": "pages.csv", "services": "services.csv"}


# --- one real fetch ---------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real export of both tabs (4 requests with the redirects) and a parse of it."""
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
    hosts = {f.url.split("/")[2] for f in snap.manifest.fetched}
    assert SHEETS_HOST in hosts
    assert all(host_allowed(h, (EXPORT_HOST,)) for h in hosts - {SHEETS_HOST}), hosts
    assert snap.manifest.extract("pages.csv").rows == 200
    obs = by_key(parse(snap))
    mobile = {k: o for (s, k), o in obs.items() if s == "pages/mobile"}
    assert 90 <= len(mobile) <= 100
    assert mobile["Roboto"].value > mobile["Inter"].value > 0
    assert "roboto" not in mobile
    assert ("requests/mobile/Google", "Roboto") in obs
