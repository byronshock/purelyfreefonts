"""The pkgstats collector: Arch Linux package installs per complete month.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules:
which months are complete, paging, the checks on each month, copying months
from the previous snapshot, the extract format and the parsed Observations.
The fixture is trimmed real data (ruling T1); the rest is synthetic. One real
fetch is marked ``network``.
"""

import dataclasses
import json
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking import pkgstats
from tff_catalog.collectors.ranking.pkgstats import (
    COLLECTOR,
    HEADER,
    HOST,
    Month,
    Row,
    Settings,
    check_month,
    check_settled,
    complete_months,
    list_url,
    month_csv,
    read_month_csv,
    read_page,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.store import RawDir, Snapshot, SnapshotWriter, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
PREVIOUS = regen.load_snapshot(FIXTURE / "previous", COLLECTOR.name)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.pkgstats")
DAY = date(2026, 10, 3)  # synthetic runs: the latest complete month is 2026-09
SEP, AUG, JUL = Month(2026, 9), Month(2026, 8), Month(2026, 7)


def parse(snapshot: Snapshot = SNAPSHOT, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def by_key(recs: list[Record]) -> dict[tuple[str, str], Observation]:
    out = {}
    for r in recs:
        assert isinstance(r, Observation)
        out[(r.key.key, r.series)] = r
    return out


# --- synthetic API answers ----------------------------------------------------------------------


def item(name: str, count: int, samples: int, month: Month) -> dict[str, Any]:
    return {
        "name": name,
        "samples": samples,
        "count": count,
        "popularity": round(100 * count / samples, 2),
        "startMonth": month.api,
        "endMonth": month.api,
    }


def page_doc(
    month: Month,
    rows: list[tuple[str, int]],
    *,
    samples: int = 1000,
    total: int | None = None,
    limit: int = 10_000,
    offset: int = 0,
) -> dict[str, Any]:
    items = [item(n, c, samples, month) for n, c in rows]
    return {
        "total": len(items) if total is None else total,
        "count": len(items),
        "packagePopularities": items,
        "limit": limit,
        "offset": offset,
        "query": "",
    }


def month_pages(
    month: Month, rows: list[tuple[str, int]], *, page_size: int = 10_000, samples: int = 1000
) -> list[tuple[str, dict[str, Any]]]:
    """The answers the API gives for a month with ``rows``, in its order, ``page_size`` a page."""
    ordered = sorted(rows, key=lambda r: (-r[1], r[0]))
    offsets = range(0, max(len(ordered), 1), page_size)
    return [
        (
            list_url(month, page_size, off),
            page_doc(
                month,
                ordered[off : off + page_size],
                samples=samples,
                total=len(ordered),
                limit=page_size,
                offset=off,
            ),
        )
        for off in offsets
    ]


def serve(directory: Path, answers: list[tuple[str, dict[str, Any]]]) -> Path:
    """A mockhttp fixture serving each ``(url, JSON document)``."""
    directory.mkdir(parents=True, exist_ok=True)
    index = []
    for i, (url, doc) in enumerate(answers):
        (directory / f"page{i}.json").write_text(json.dumps(doc) + "\n", encoding="utf-8")
        index.append(
            {"url": url, "headers": {"content-type": "application/json"}, "body": f"page{i}.json"}
        )
    mockhttp.write_index(directory, index)
    return directory


def fetch(
    tmp: Path,
    http: Path,
    *,
    day: date = DAY,
    previous: Snapshot | None = None,
    settings: Settings = SETTINGS,
    today: date | None = None,
) -> tuple[Snapshot | None, list[str]]:
    """Run ``fetch()`` offline into a fresh store; return the snapshot and the requested URLs.

    The clock stands at 06:00 UTC on ``today``, by default the run date.
    """
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    try:
        with (
            clock.frozen(datetime.combine(today or day, time(6), tzinfo=UTC)),
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
                    raw=RawDir(tmp / "raw"),
                    previous=previous,
                    settings=settings,
                    log=LOG,
                )
            )
    finally:
        assert not mock.unmatched, mock.unmatched
    return store.snapshot(COLLECTOR.name, day), mock.urls()


def snapshot_of(
    tmp: Path, day: date, months: dict[Month, list[Row]], *, version: int = COLLECTOR.version
) -> Snapshot:
    """A complete snapshot holding one extract per month."""
    writer = SnapshotWriter(tmp / "made", COLLECTOR.name, day, version)
    for month, rows in months.items():
        writer.write_bytes(month.extract, month_csv(rows), rows=len(rows))
    return writer.close()


def rows_of(month_rows: list[tuple[str, int]], samples: int = 1000) -> list[Row]:
    return [Row(n, c, samples) for n, c in month_rows]


def one_month(**changes: Any) -> Settings:
    return dataclasses.replace(SETTINGS, months=1, **changes)


ROWS = [("ttf-aster-sans", 120), ("glibc", 990), ("ttf-birch-mono", 45), ("firefox", 600)]


def normalized(url: str) -> str:
    return mockhttp.normalize_url(url)


# --- months -------------------------------------------------------------------------------------


def test_months_name_count_and_bound_themselves() -> None:
    feb = Month.parse("2028-02")
    assert (str(feb), feb.api, feb.extract) == ("2028-02", 202802, "month-2028-02.csv.gz")
    assert (feb.first_day(), feb.last_day()) == (date(2028, 2, 1), date(2028, 2, 29))
    assert Month(2026, 1).shift(-2) == Month(2025, 11)
    assert Month(2026, 12).shift(1) == Month(2027, 1)
    assert Month(2026, 3).shift(-15) == Month(2024, 12)
    assert Month.of(date(2026, 9, 26)) == Month(2026, 9)
    for bad in ("2026-13", "2026-00", "26-01", "2026-1", "2026/01"):
        with pytest.raises(ValueError, match="not a month"):
            Month.parse(bad)


@pytest.mark.parametrize(
    ("run_date", "settle_days", "latest"),
    [
        (date(2026, 10, 3), 2, "2026-09"),  # the monthly run: September has settled
        (date(2026, 10, 2), 2, "2026-08"),  # a shared cache may still serve September stale
        (date(2026, 10, 1), 2, "2026-08"),
        (date(2026, 10, 1), 0, "2026-09"),
        (date(2026, 9, 30), 0, "2026-08"),  # the current month is never complete
        (date(2026, 9, 26), 2, "2026-08"),
        (date(2027, 1, 5), 2, "2026-12"),
        (date(2027, 1, 2), 2, "2026-11"),
    ],
)
def test_complete_months_wait_for_the_month_to_settle(
    run_date: date, settle_days: int, latest: str
) -> None:
    got = complete_months(run_date, 12, settle_days)
    assert len(got) == 12
    assert str(got[-1]) == latest
    assert got == tuple(got[-1].shift(i - 11) for i in range(12))  # consecutive, oldest first


def test_a_month_has_settled_only_after_its_settle_days() -> None:
    check_settled(AUG, date(2026, 9, 3), 2)
    check_settled(AUG, date(2026, 9, 1), 0)
    for today, settle_days in ((date(2026, 9, 2), 2), (date(2026, 8, 31), 0)):
        with pytest.raises(ValueError, match="2026-08 is not complete until"):
            check_settled(AUG, today, settle_days)


def test_the_list_url_asks_for_one_month_and_one_page() -> None:
    assert list_url(Month(2026, 8), 10_000, 20_000) == (
        f"https://{HOST}/api/packages?startMonth=202608&endMonth=202608&limit=10000&offset=20000"
    )


# --- settings ----------------------------------------------------------------------------------


def test_config_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert (SETTINGS.months, SETTINGS.settle_days, SETTINGS.page_size) == (12, 2, 10_000)


@pytest.mark.parametrize(
    "bad",
    [
        {"months": 0},
        {"months": 25},
        {"settle_days": -1},
        {"settle_days": 28},
        {"page_size": 0},
        {"page_size": 10_001},
        {"min_share": 1.5},
        {"months": 12.0},
        {"mnths": 12},
    ],
)
def test_settings_refuse_bad_values(bad: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        from_mapping(Settings, bad, where="sources/pkgstats.toml")


# --- the API answer ------------------------------------------------------------------------------


def test_a_page_reads_as_rows_in_api_order() -> None:
    doc = json.loads((FIXTURE / "http" / "page-2026-08.json").read_text(encoding="utf-8"))
    page = read_page(doc, AUG, limit=10_000, offset=0)
    assert page.total == 10
    assert page.rows[0] == Row("glibc", 32731, 32749)
    assert Row("ttf-jetbrains-mono", 5066, 32749) in page.rows
    assert [r.count for r in page.rows] == sorted((r.count for r in page.rows), reverse=True)


def _break(doc: dict[str, Any], change: str) -> None:
    first = doc["packagePopularities"][0]
    match change:
        case "month":
            first["endMonth"] = 202607
        case "count-above-samples":
            first["count"] = first["samples"] + 1
        case "bool-count":
            first["count"] = True
        case "float-samples":
            first["samples"] = 1000.0
        case "zero-samples":
            first["samples"], first["count"] = 0, 0
        case "no-name":
            first["name"] = " "
        case "count-field":
            doc["count"] = 99
        case "offset":
            doc["offset"] = 10_000
        case "limit":
            doc["limit"] = 100
        case "no-list":
            del doc["packagePopularities"]
        case "negative-total":
            doc["total"] = -1
        case "not-an-object":
            doc["packagePopularities"][0] = ["glibc", 990]


@pytest.mark.parametrize(
    "change",
    [
        "month",
        "count-above-samples",
        "bool-count",
        "float-samples",
        "zero-samples",
        "no-name",
        "count-field",
        "offset",
        "limit",
        "no-list",
        "negative-total",
        "not-an-object",
    ],
)
def test_a_page_that_is_not_the_month_asked_for_is_refused(change: str) -> None:
    doc = page_doc(AUG, ROWS)
    read_page(doc, AUG, limit=10_000, offset=0)  # sound before the change
    _break(doc, change)
    with pytest.raises(ValueError, match=r"^2026-08 offset 0"):
        read_page(doc, AUG, limit=10_000, offset=0)
    with pytest.raises(ValueError, match=r"^2026-08 offset 0: expected an object"):
        read_page([doc], AUG, limit=10_000, offset=0)


def test_a_month_has_unique_packages_and_one_samples() -> None:
    check_month(rows_of(ROWS), AUG)
    with pytest.raises(ValueError, match="no packages"):
        check_month([], AUG)
    with pytest.raises(ValueError, match="twice: glibc"):
        check_month(rows_of([*ROWS, ("glibc", 3)]), AUG)
    with pytest.raises(ValueError, match="disagree on samples"):
        check_month([*rows_of(ROWS), Row("ttf-cobalt", 20, 999)], AUG)


# --- the extract -------------------------------------------------------------------------------


def test_the_extract_is_a_csv_sorted_by_name() -> None:
    rows = rows_of(ROWS)
    data = month_csv(rows)
    assert data.decode().splitlines() == [
        ",".join(HEADER),
        "firefox,600,1000",
        "glibc,990,1000",
        "ttf-aster-sans,120,1000",
        "ttf-birch-mono,45,1000",
    ]
    assert read_month_csv(data) == sorted(rows)
    assert month_csv(list(reversed(rows))) == data


def test_odd_package_names_survive_the_csv() -> None:
    rows = [Row('odd,"name', 20, 30), Row("lib32-glibc", 25, 30), Row("r@b+c.d_e", 16, 30)]
    assert read_month_csv(month_csv(rows)) == sorted(rows)


@pytest.mark.parametrize(
    "text",
    [
        "name,samples,count\nglibc,990,1000\n",  # header out of order
        "",  # no header
        "name,count,samples\nglibc,990\n",  # a field missing
        "name,count,samples\nglibc,990,1000\nglibc,3,1000\n",  # a package twice
        "name,count,samples\n,990,1000\n",  # no name
        "name,count,samples\nglibc,-1,1000\n",  # not a count
        "name,count,samples\nglibc,9.5,1000\n",
        "name,count,samples\nglibc,1001,1000\n",  # count above samples
        "name,count,samples\nglibc,0,0\n",  # no samples
    ],
)
def test_a_malformed_extract_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match=r"^month"):
        read_month_csv(text.encode())


# --- fetch -------------------------------------------------------------------------------------


def test_fetch_pages_through_a_month(tmp_path: Path) -> None:
    rows = [*ROWS, ("ttf-cobalt-serif", 45)]
    answers = month_pages(SEP, rows, page_size=2)
    http = serve(tmp_path / "http", answers)
    snap, urls = fetch(tmp_path, http, settings=one_month(page_size=2))
    assert snap is not None
    assert urls == [normalized(u) for u, _ in answers]
    assert len(urls) == 3
    assert [e.path for e in snap.manifest.extracts] == ["month-2026-09.csv.gz"]
    assert snap.manifest.extracts[0].rows == 5
    assert read_month_csv(snap.read_bytes(SEP.extract)) == sorted(rows_of(rows))
    assert [f.url for f in snap.manifest.fetched] == [u for u, _ in answers]
    assert not any(f.kept for f in snap.manifest.fetched)
    assert snap.manifest.window == (date(2026, 9, 1), date(2026, 9, 30))
    assert snap.manifest.data_date == date(2026, 9, 30)
    assert snap.manifest.notes == ("fetched 1 month: 2026-09",)
    # the pages went to the raw directory, not the snapshot
    assert sorted(p.name for p in (tmp_path / "raw" / "2026-09").iterdir()) == [
        "offset-000000.json",
        "offset-000002.json",
        "offset-000004.json",
    ]


def test_a_last_full_page_ends_the_paging(tmp_path: Path) -> None:
    answers = month_pages(SEP, ROWS, page_size=2)  # 4 rows: no request at offset 4
    snap, urls = fetch(tmp_path, serve(tmp_path / "http", answers), settings=one_month(page_size=2))
    assert snap is not None
    assert urls == [normalized(u) for u, _ in answers]
    assert len(urls) == 2


@pytest.mark.parametrize(("packages", "requests"), [(5, 1), (3, 2)])
def test_a_month_past_the_paging_limit_fails_at_the_first_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packages: int, requests: int
) -> None:
    monkeypatch.setattr(pkgstats, "MAX_OFFSET", 2)  # the API's is 100,000
    rows = [(f"pkg-{i}", 100 - i) for i in range(packages)]
    answers = month_pages(SEP, rows, page_size=2)[:requests]
    http = serve(tmp_path / "http", answers)
    if requests == 1:  # 5 packages need offset 4
        with pytest.raises(ValueError, match="5 packages need offset 4, past the API's limit of 2"):
            fetch(tmp_path, http, settings=one_month(page_size=2))
    else:  # 3 packages need offset 2, which the API serves
        snap, urls = fetch(tmp_path, http, settings=one_month(page_size=2))
        assert snap is not None
        assert len(urls) == 2


def test_a_total_that_changes_while_paging_fails_the_fetch(tmp_path: Path) -> None:
    answers = month_pages(SEP, ROWS, page_size=2)
    answers[1][1]["total"] = 5
    with pytest.raises(ValueError, match="total went from 4 to 5"):
        fetch(tmp_path, serve(tmp_path / "http", answers), settings=one_month(page_size=2))


def test_a_short_page_fails_the_fetch(tmp_path: Path) -> None:
    answers = month_pages(SEP, ROWS, page_size=2)
    doc = answers[0][1]
    doc["packagePopularities"].pop()
    doc["count"] = 1
    with pytest.raises(ValueError, match="1 rows, expected 2 of 4"):
        fetch(tmp_path, serve(tmp_path / "http", answers), settings=one_month(page_size=2))


def test_an_empty_month_fails_the_fetch(tmp_path: Path) -> None:
    answers = month_pages(SEP, [])
    with pytest.raises(ValueError, match="no packages"):
        fetch(tmp_path, serve(tmp_path / "http", answers), settings=one_month())


def test_a_package_on_two_pages_fails_the_fetch(tmp_path: Path) -> None:
    answers = month_pages(SEP, ROWS, page_size=2)
    answers[1][1]["packagePopularities"][0]["name"] = "glibc"
    with pytest.raises(ValueError, match="twice: glibc"):
        fetch(tmp_path, serve(tmp_path / "http", answers), settings=one_month(page_size=2))


def test_an_error_answer_fails_the_fetch(tmp_path: Path) -> None:
    http = tmp_path / "http"
    http.mkdir()
    (http / "error.html").write_text("<p>invalid endMonth</p>")
    mockhttp.write_index(
        http, [{"url": list_url(SEP, 10_000, 0), "status": 400, "body": "error.html"}]
    )
    with pytest.raises(Exception, match="HTTP 400"):
        fetch(tmp_path, http, settings=one_month())


def test_months_the_previous_snapshot_holds_are_copied(tmp_path: Path) -> None:
    held = {Month(2026, 6): rows_of(ROWS), JUL: rows_of(ROWS), AUG: rows_of(ROWS, 1100)}
    previous = snapshot_of(tmp_path, date(2026, 9, 3), held)
    answers = month_pages(SEP, ROWS, samples=1200)
    snap, urls = fetch(
        tmp_path,
        serve(tmp_path / "http", answers),
        previous=previous,
        settings=dataclasses.replace(SETTINGS, months=3),
    )
    assert snap is not None
    assert urls == [normalized(answers[0][0])]  # only the new month
    assert [e.path for e in snap.manifest.extracts] == [
        "month-2026-07.csv.gz",
        "month-2026-08.csv.gz",
        "month-2026-09.csv.gz",
    ]  # 2026-06 is out of the window and not copied
    for month in (JUL, AUG):
        assert snap.read_bytes(month.extract) == previous.read_bytes(month.extract)
        assert snap.manifest.extract(month.extract) == previous.manifest.extract(month.extract)
    assert snap.manifest.notes == (
        "copied from the 2026-09-03 snapshot: 2 months: 2026-07 to 2026-08",
        "fetched 1 month: 2026-09",
    )
    assert snap.manifest.window == (date(2026, 7, 1), date(2026, 9, 30))


def test_a_gap_in_the_previous_snapshot_is_fetched(tmp_path: Path) -> None:
    previous = snapshot_of(tmp_path, date(2026, 9, 3), {JUL: rows_of(ROWS)})
    answers = month_pages(AUG, ROWS) + month_pages(SEP, ROWS)
    snap, urls = fetch(
        tmp_path,
        serve(tmp_path / "http", answers),
        previous=previous,
        settings=dataclasses.replace(SETTINGS, months=3),
    )
    assert snap is not None
    assert urls == [normalized(u) for u, _ in answers]
    assert snap.manifest.notes == (
        "copied from the 2026-09-03 snapshot: 1 month: 2026-07",
        "fetched 2 months: 2026-08 to 2026-09",
    )


def test_extracts_of_another_format_version_are_not_copied(tmp_path: Path) -> None:
    previous = snapshot_of(
        tmp_path, date(2026, 9, 3), {AUG: rows_of(ROWS)}, version=COLLECTOR.version + 1
    )
    answers = month_pages(AUG, ROWS) + month_pages(SEP, ROWS)
    snap, urls = fetch(
        tmp_path,
        serve(tmp_path / "http", answers),
        previous=previous,
        settings=dataclasses.replace(SETTINGS, months=2),
    )
    assert snap is not None
    assert len(urls) == 2
    assert snap.manifest.notes == ("fetched 2 months: 2026-08 to 2026-09",)


@pytest.mark.parametrize("what", ["rows", "samples"])
def test_a_month_far_below_the_one_before_fails_the_fetch(tmp_path: Path, what: str) -> None:
    before = rows_of([(f"pkg-{i:02d}", 100 + i) for i in range(10)])
    previous = snapshot_of(tmp_path, date(2026, 9, 3), {AUG: before})
    if what == "rows":
        answers = month_pages(SEP, ROWS)  # 4 packages, down from 10
    else:
        answers = month_pages(SEP, [(r.name, 10) for r in before], samples=400)
    http = serve(tmp_path / "http", answers)
    settings = dataclasses.replace(SETTINGS, months=2)
    with pytest.raises(ValueError, match=f"{what}, down from"):
        fetch(tmp_path, http, previous=previous, settings=settings)
    # the same answer passes when the check is off
    snap, _ = fetch(
        tmp_path / "off",
        http,
        previous=previous,
        settings=dataclasses.replace(settings, min_share=0.0),
    )
    assert snap is not None


def test_months_fetched_in_one_run_are_compared_too(tmp_path: Path) -> None:
    many = [(f"pkg-{i:02d}", 100 + i) for i in range(10)]
    answers = month_pages(AUG, many) + month_pages(SEP, ROWS)
    with pytest.raises(ValueError, match="2026-09: 4 rows, down from 10"):
        fetch(
            tmp_path,
            serve(tmp_path / "http", answers),
            settings=dataclasses.replace(SETTINGS, months=2),
        )


def test_a_run_date_in_the_future_requests_nothing(tmp_path: Path) -> None:
    # On 2026-09-26 the API already answers for September, but only in part.
    http = serve(tmp_path / "http", month_pages(SEP, ROWS))
    with pytest.raises(ValueError, match=r"2026-09 is not complete until 2026-10-03, and today"):
        fetch(tmp_path, http, settings=one_month(), today=date(2026, 9, 26))
    with pytest.raises(ValueError, match="2026-09 is not complete"):
        fetch(tmp_path / "b", http, settings=one_month(), today=date(2026, 10, 2))
    # a later run date is fine once the month has settled by the clock too
    snap, urls = fetch(
        tmp_path / "c", http, day=date(2026, 10, 9), today=date(2026, 10, 3), settings=one_month()
    )
    assert snap is not None
    assert len(urls) == 1


def test_a_run_date_in_the_future_may_copy_settled_months(tmp_path: Path) -> None:
    held = {m: rows_of(ROWS) for m in (JUL, AUG, SEP)}
    previous = snapshot_of(tmp_path, date(2026, 10, 3), held)
    snap, urls = fetch(
        tmp_path,
        serve(tmp_path / "http", []),
        previous=previous,
        settings=dataclasses.replace(SETTINGS, months=3),
        today=date(2026, 9, 26),
    )
    assert snap is not None
    assert urls == []
    assert [e.path for e in snap.manifest.extracts] == [m.extract for m in (JUL, AUG, SEP)]


def test_the_fixture_run_asks_only_for_the_new_month(tmp_path: Path) -> None:
    snap, urls = fetch(tmp_path, FIXTURE / "http", day=SNAPSHOT.date, previous=PREVIOUS)
    assert snap is not None
    assert urls == [normalized(list_url(AUG, 10_000, 0))]
    assert snap.manifest.extracts == SNAPSHOT.manifest.extracts
    assert snap.manifest.notes == SNAPSHOT.manifest.notes


# --- parse -------------------------------------------------------------------------------------


def test_parse_gives_one_observation_per_package_and_month() -> None:
    recs = by_key(parse())
    assert len(recs) == 35
    jb = recs[("ttf-jetbrains-mono", "2026-08")]
    assert (jb.value, dict(jb.attrs)) == (5066.0, {"month": "2026-08", "samples": 32749})
    assert (jb.unit, jb.key.ns, jb.source) == ("installs", "arch-pkg", "pkgstats")
    assert (jb.start, jb.end) == (date(2026, 8, 1), date(2026, 8, 31))
    assert round(jb.value / 32749, 4) == 0.1547  # the site's 15.47%
    months = sorted(s for k, s in recs if k == "ttf-jetbrains-mono")
    assert months == [str(Month(2025, 9).shift(i)) for i in range(12)]


def test_parse_keeps_packages_that_are_not_fonts_and_late_arrivals() -> None:
    recs = by_key(parse())
    assert ("glibc", "2026-08") in recs
    assert ("firefox", "2026-08") in recs
    departure = sorted(s for k, s in recs if k == "otf-departure-mono-nerd")
    assert departure == ["2026-05", "2026-06", "2026-07", "2026-08"]  # first listed in May
    assert [s for k, s in recs if k == "ttf-monocraft-nerd"] == ["2026-08"]


def test_parse_reads_month_extracts_only(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path / "made", COLLECTOR.name, DAY, COLLECTOR.version)
    writer.write_bytes(Month(2028, 2).extract, month_csv(rows_of(ROWS)), rows=4)
    writer.write_json("other.json", {"not": "a month"})
    recs = parse(writer.close())
    assert len(recs) == 4
    assert {(r.series, r.start, r.end) for r in recs if isinstance(r, Observation)} == {
        ("2028-02", date(2028, 2, 1), date(2028, 2, 29))
    }


def test_parse_refuses_a_snapshot_without_months(tmp_path: Path) -> None:
    writer = SnapshotWriter(tmp_path / "made", COLLECTOR.name, DAY, COLLECTOR.version)
    writer.write_json("other.json", {})
    with pytest.raises(ValueError, match=r"no month-YYYY-MM\.csv\.gz extracts"):
        parse(writer.close())


def test_parse_is_the_same_on_every_run() -> None:
    assert regen.encode(parse()) == regen.encode(parse())
    assert regen.encode(parse()) == (FIXTURE / regen.EXPECTED).read_bytes()


# --- the real source ----------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real month (4 requests of about 1 MB each), then a parse of it."""
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    settings = one_month()
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
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    (month,) = complete_months(day, 1, settings.settle_days)
    recs = by_key(parse(snap, settings))
    assert len(recs) > 20_000
    assert {s for _, s in recs} == {str(month)}
    samples = dict(recs[("glibc", str(month))].attrs)["samples"]
    assert samples > 10_000
    assert recs[("glibc", str(month))].value > 0.9 * samples
    assert 0.02 < recs[("ttf-jetbrains-mono", str(month))].value / samples < 0.5
