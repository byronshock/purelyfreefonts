"""The npm collector: daily downloads of the Fontsource and Expo font packages.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the synthetic fixture and on small made-up answers, plus one real fetch marked
``network``.
"""

import dataclasses
import gzip
import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.npm import (
    API_HOST,
    COLLECTOR,
    EXTRACT,
    LAST_DAY_URL,
    MAX_HISTORY_DAYS,
    SERIES,
    Row,
    Settings,
    Window,
    check_last_day,
    complete_months,
    downloads_csv,
    month_bounds,
    observations,
    org_url,
    read_downloads_csv,
    read_last_day,
    read_org,
    read_range,
    sample,
    summarize,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher, FetchError
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
END = DAY - timedelta(days=1)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
SHORT = dataclasses.replace(SETTINGS, history_days=365)  # small made-up answers
LOG = logging.getLogger("tests.npm")
JSON = {"content-type": "application/json"}


# --- helpers ---------------------------------------------------------------------------------


def parse(snapshot: Snapshot = SNAPSHOT) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, SETTINGS)


def by_series(recs: Sequence[Record], package: str) -> dict[str, Observation]:
    out = {}
    for r in recs:
        assert isinstance(r, Observation)
        if r.key.key == package:
            out[r.series] = r
    return out


def range_doc(package: str, window: Window, daily: Sequence[int]) -> dict[str, Any]:
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "package": package,
        "downloads": [
            {"downloads": n, "day": (window.start + timedelta(days=i)).isoformat()}
            for i, n in enumerate(daily)
        ],
    }


def serve(directory: Path, answers: Sequence[tuple[str, int, object]]) -> Path:
    """A mockhttp fixture answering each ``(url, status, json_body)``."""
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, (url, status, body) in enumerate(answers):
        name = f"body-{i}.json"
        (directory / name).write_text(json.dumps(body), encoding="utf-8")
        entries.append({"url": url, "status": status, "headers": JSON, "body": name})
    mockhttp.write_index(directory, entries)
    return directory


def answers(
    orgs: dict[str, list[str]],
    ranges: dict[str, object],
    *,
    end: date = END,
    days: int = 365,
) -> list[tuple[str, int, object]]:
    """Answers for ``last-day``, each org listing and each package's range.

    A ``ranges`` value is a list of daily counts, an ``int`` status with an
    error body, or a ``(status, body)`` pair.
    """
    window = Window.ending(end, days)
    out: list[tuple[str, int, object]] = [
        (LAST_DAY_URL, 200, {"downloads": 5, "start": end.isoformat(), "end": end.isoformat()})
    ]
    out += [(org_url(s), 200, dict.fromkeys(names, "write")) for s, names in orgs.items()]
    for package, spec in ranges.items():
        url = window.range_url(package)
        if isinstance(spec, list):
            out.append((url, 200, range_doc(package, window, spec)))
        elif isinstance(spec, int):
            out.append((url, spec, {"error": f"package {package} not found"}))
        else:
            status, body = spec
            out.append((url, status, body))
    return out


def flaky(mock: mockhttp.MockHTTP, urls: set[str]) -> httpx.MockTransport:
    """``mock``'s transport, except that the first request for each of ``urls`` gets a 503."""
    left = set(urls)

    def handle(request: httpx.Request) -> httpx.Response:
        url = mockhttp.normalize_url(request.url)
        if url in left:
            left.discard(url)
            mock.requests.append(request)
            return httpx.Response(503, json={"error": "busy"})
        return mock.handle(request)

    return httpx.MockTransport(handle)


def fetch(
    tmp: Path,
    http: Path,
    settings: object = SETTINGS,
    *,
    day: date = DAY,
    mock: mockhttp.MockHTTP | None = None,
    fail_once: set[str] | None = None,
) -> tuple[Snapshot, mockhttp.MockHTTP]:
    """Run ``fetch()`` offline (no retries) into a fresh store; return the snapshot.

    ``mock`` lets a caller see the requests of a fetch that raises; each URL in
    ``fail_once`` gets a 503 the first time it is asked for.
    """
    mock = mock or mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(
            transport=flaky(mock, fail_once or set()),
            min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0),
            retries=0,
            log=LOG,
        ) as fetcher,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp / "raw"),
                previous=None,
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return snap, mock


def steady(n: int, days: int = 365) -> list[int]:
    return [n] * days


# --- months and the window ---------------------------------------------------------------------


def test_month_bounds_and_complete_months() -> None:
    assert month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_bounds("2025-12") == (date(2025, 12, 1), date(2025, 12, 31))
    with pytest.raises(ValueError, match="YYYY-MM"):
        month_bounds("2025-1")
    # part-months at both ends are left out; whole ones at the edges are kept
    assert complete_months(date(2025, 4, 11), date(2025, 7, 2)) == ("2025-05", "2025-06")
    assert complete_months(date(2025, 4, 1), date(2025, 6, 30)) == (
        "2025-04",
        "2025-05",
        "2025-06",
    )
    assert complete_months(date(2025, 12, 2), date(2026, 1, 30)) == ()


def test_window() -> None:
    w = Window.ending(date(2026, 10, 2), 540)
    assert (w.start, w.days) == (date(2025, 4, 11), 540)
    assert w.year_start == date(2025, 10, 3)  # npm's last-year: 365 days ending at end
    assert w.unseen == date(2026, 10, 3)
    assert w.months[0] == "2025-05"
    assert w.months[-1] == "2026-09"
    assert len(w.months) == 17
    assert w.range_url("@fontsource/inter") == (
        "https://api.npmjs.org/downloads/range/2025-04-11:2026-10-02/@fontsource/inter"
    )
    with pytest.raises(ValueError, match="under 365 days"):
        Window.ending(date(2026, 10, 2), 364)


def test_snapshot_window_is_the_manifest_window() -> None:
    assert SNAPSHOT.manifest.window == (date(2025, 4, 11), END)
    assert SNAPSHOT.manifest.data_date == END


# --- reading answers ---------------------------------------------------------------------------


def test_summarize() -> None:
    w = Window.ending(date(2026, 10, 2), 400)
    daily = [0] * 100 + [2] * 300
    row = summarize("@fontsource/aster-sans", daily, w)
    assert row.first_day == w.start + timedelta(days=100)
    assert row.last_year == 2 * 300  # the last 365 days hold every download
    days = [w.start + timedelta(days=i) for i in range(400)]
    assert row.months == tuple(
        sum(n for d, n in zip(days, daily, strict=True) if d.strftime("%Y-%m") == m)
        for m in w.months
    )
    assert sum(row.months) < sum(daily)  # the part-months at the ends are left out
    assert summarize("@fontsource/x", [0] * 400, w).first_day is None
    gone = summarize("@fontsource/x", [5] * 30 + [0] * 370, w)  # no download in the last year
    assert (gone.first_day, gone.last_year) == (w.start, 0)
    ones = summarize("@fontsource/x", [1] * 400, w)
    assert (ones.first_day, ones.last_year) == (w.start, 365)
    with pytest.raises(ValueError, match="399 days"):
        summarize("@fontsource/x", [1] * 399, w)


W365 = Window.ending(END, 365)
GOOD = range_doc("@fontsource/x", W365, steady(3))


def _bad(**changes: object) -> dict[str, Any]:
    return {**GOOD, **changes}


def _days(edit: Any) -> dict[str, Any]:
    days = [dict(d) for d in GOOD["downloads"]]
    edit(days)
    return {**GOOD, "downloads": days}


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ([], "expected an object"),
        (_bad(package="@fontsource/y"), "answer is for"),
        (_bad(start=(W365.start + timedelta(days=1)).isoformat()), "covers"),
        (_bad(end=(END - timedelta(days=1)).isoformat()), "covers"),
        (_days(lambda d: d.pop()), "364 days"),
        (_bad(downloads=None), "no days"),
        (_days(lambda d: d[5].update(day="2020-01-01")), "day 5"),
        (_days(lambda d: d[0].update(downloads=-1)), "expected a count"),
        (_days(lambda d: d[0].update(downloads=True)), "expected a count"),
        (_days(lambda d: d[0].update(downloads=1.5)), "expected a count"),
    ],
    ids=[
        "not-object",
        "other-package",
        "start-moved",
        "end-moved",
        "day-missing",
        "no-list",
        "wrong-day",
        "negative",
        "bool",
        "float",
    ],
)
def test_read_range_rejects(doc: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        read_range(doc, "@fontsource/x", W365)


def test_read_range() -> None:
    assert read_range(GOOD, "@fontsource/x", W365) == steady(3)


def test_read_org() -> None:
    doc = {
        "@fontsource/b": "write",
        "@fontsource/a": "write",
        "@fontsource/Bad Name": "write",
        "fontsource-legacy": "write",  # an org can be given access to any package
        "@fontsource-variable/a": "read",
    }
    assert read_org(doc, "@fontsource") == (
        ["@fontsource/a", "@fontsource/b"],
        ["@fontsource-variable/a", "@fontsource/Bad Name", "fontsource-legacy"],
    )
    with pytest.raises(ValueError, match="names no @fontsource package"):
        read_org({"@fontsource-variable/a": "write", "typeface-a": "write"}, "@fontsource")
    for bad in ({}, [], "x"):
        with pytest.raises(ValueError, match="non-empty object"):
            read_org(bad, "@fontsource")


def test_last_day() -> None:
    assert read_last_day({"downloads": 1, "start": "2026-10-02", "end": "2026-10-02"}) == END
    with pytest.raises(ValueError, match="not one day"):
        read_last_day({"start": "2026-10-01", "end": "2026-10-02"})
    with pytest.raises(ValueError, match="no start and end"):
        read_last_day({"downloads": 1})
    check_last_day(DAY, DAY)  # the run date itself: late in the day in the Americas
    check_last_day(DAY - timedelta(days=7), DAY)
    with pytest.raises(ValueError, match="after the run date"):
        check_last_day(DAY + timedelta(days=1), DAY)
    with pytest.raises(ValueError, match="more than 7 days"):
        check_last_day(DAY - timedelta(days=8), DAY)


def test_sample() -> None:
    names = [f"p{i:02d}" for i in range(10)]
    assert sample(names, 0) == names
    assert sample(names, 10) == names
    assert sample(names, 12) == names
    assert sample(names, 3) == ["p00", "p03", "p06"]
    assert sample(names, 1) == ["p00"]


# --- settings ----------------------------------------------------------------------------------


def test_config_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert SETTINGS.max_packages == 0, "config/sources/npm.toml must fetch every package"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"scopes": []}, "at least one scope"),
        ({"scopes": ["fontsource"]}, "not an npm scope"),
        ({"scopes": ["@fontsource", "@fontsource"]}, "listed twice"),
        ({"exclude": ["dev"]}, "not a scoped package"),
        ({"history_days": 364}, "history_days"),
        ({"history_days": MAX_HISTORY_DAYS + 1}, "history_days"),
        ({"max_packages": -1}, "max_packages"),
        ({"max_failures": -1}, "max_failures"),
        ({"max_package": 3}, "unknown key"),
    ],
)
def test_settings_are_checked(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, {"enabled": True, **changes}, where="sources/npm.toml")


# --- the extract -------------------------------------------------------------------------------


def test_extract_round_trip() -> None:
    w = Window.ending(END, 400)
    rows = [
        summarize("@fontsource/b", [0] * 200 + [4] * 200, w),
        summarize("@fontsource/a", [1] * 400, w),
        summarize("@fontsource/c", [0] * 400, w),
        Row("@fontsource/d", None, None, ()),
        summarize("@fontsource/e", [5] * 30 + [0] * 370, w),
    ]
    data = downloads_csv(rows, w)
    assert data.splitlines()[0] == b"package,first_day,last_year," + ",".join(w.months).encode()
    assert read_downloads_csv(data, w) == sorted(rows)
    assert data.splitlines()[-2] == b"@fontsource/d,,," + b"," * (len(w.months) - 1)
    # a package with no download in the last year but earlier ones: 0, and old
    year, *months = observations(read_downloads_csv(data, w)[-1], w)
    assert (year.series, year.value, dict(year.attrs)["first_seen"]) == (
        SERIES,
        0.0,
        w.start.isoformat(),
    )
    assert [m.series for m in months] == list(w.months)
    assert months[0].value > 0
    assert {m.value for m in months[1:]} == {0.0}


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda t: t.replace("last_year", "total"), "header"),
        (lambda t: t.replace("@fontsource/a,", "fontsource-a,"), "scoped package"),
        (lambda t: t + t.splitlines()[1] + "\n", "listed twice"),
        (lambda t: t.replace("@fontsource/a,2025", "@fontsource/a,2020"), "outside the window"),
        (lambda t: t.replace("@fontsource/d,,,", "@fontsource/d,,,5"), "has no last_year"),
        (lambda t: t.replace("@fontsource/c,,0,", "@fontsource/c,,9,"), "no first_day"),
        (
            lambda t: t.replace("@fontsource/a,2025-09-02,365,", "@fontsource/a,2025-09-02,x,"),
            "not a count",
        ),
        (lambda t: t.replace("@fontsource/c,,0,", "@fontsource/c,,0,0,"), "fields"),
    ],
    ids=[
        "header",
        "name",
        "twice",
        "first-day",
        "unknown-values",
        "no-first-day",
        "count",
        "width",
    ],
)
def test_extract_rejects(edit: Any, message: str) -> None:
    w = Window.ending(END, 396)
    rows = [
        summarize("@fontsource/a", [1] * 396, w),
        summarize("@fontsource/c", [0] * 396, w),
        Row("@fontsource/d", None, None, ()),
    ]
    text = downloads_csv(rows, w).decode()
    assert "@fontsource/a,2025-09-02,365," in text
    with pytest.raises(ValueError, match=message):
        read_downloads_csv(edit(text).encode(), w)


def test_extract_needs_the_window_months() -> None:
    w = Window.ending(END, 400)
    data = downloads_csv([summarize("@fontsource/a", [1] * 400, w)], w)
    with pytest.raises(ValueError, match="header"):
        read_downloads_csv(data, Window.ending(END, 540))


# --- fetch ---------------------------------------------------------------------------------------


def test_fetch_writes_the_fixture_snapshot(tmp_path: Path) -> None:
    snap, mock = fetch(tmp_path, FIXTURE / "http")
    assert snap.read_bytes(EXTRACT) == SNAPSHOT.read_bytes(EXTRACT)
    assert snap.manifest.window == SNAPSHOT.manifest.window
    assert snap.manifest.data_date == END
    assert snap.manifest.notes == ("npm has no counts for 1: @fontsource/ember-script",)
    urls = mock.urls()
    assert urls[0] == LAST_DAY_URL
    assert len(urls) == 1 + 3 + 9  # last-day, three org listings, nine ranges
    assert not any(u.endswith("/@expo-google-fonts/dev") for u in urls), "excluded"
    assert [f.url for f in snap.manifest.fetched] == urls
    assert all(not f.kept for f in snap.manifest.fetched)
    assert {f.status for f in snap.manifest.fetched} == {200, 404}


def test_fetch_samples_packages(tmp_path: Path) -> None:
    settings = dataclasses.replace(SHORT, scopes=("@fontsource",), max_packages=2)
    names = [f"@fontsource/f{i}" for i in range(5)]
    http = serve(
        tmp_path / "http",
        answers({"@fontsource": names}, {n: steady(i + 1) for i, n in enumerate(names)}),
    )
    snap, mock = fetch(tmp_path, http, settings)
    rows = read_downloads_csv(snap.read_bytes(EXTRACT), Window(*snap.manifest.window))
    assert [r.package for r in rows] == ["@fontsource/f0", "@fontsource/f2"]
    assert [r.last_year for r in rows] == [365, 3 * 365]
    assert "@fontsource: a sample of 2 of 5 packages" in snap.manifest.notes
    assert len(mock.urls()) == 1 + 1 + 2


def test_fetch_skips_odd_names(tmp_path: Path) -> None:
    settings = dataclasses.replace(SHORT, scopes=("@fontsource",))
    listed = ["@fontsource/a", "@fontsource/.x", "fontsource-a"]
    http = serve(
        tmp_path / "http",
        answers({"@fontsource": listed}, {"@fontsource/a": steady(1)}),
    )
    snap, mock = fetch(tmp_path, http, settings)
    assert not mock.unmatched
    assert len(mock.urls()) == 1 + 1 + 1
    assert (
        "@fontsource: skipped names that are not @fontsource packages: @fontsource/.x, "
        "fontsource-a" in snap.manifest.notes
    )


BROKEN_RANGES: dict[str, object] = {
    "@fontsource/a": steady(1),
    "@fontsource/b": 500,  # fails after the (zero) retries
    "@fontsource/c": (200, {"error": "boom"}),  # an answer that does not check out
    "@fontsource/d": (404, {"error": "rate limited"}),  # a 404 that is not npm's "not found"
    "@fontsource/e": 404,  # npm's "not found": a row without counts
}


def test_fetch_leaves_out_failed_packages(tmp_path: Path) -> None:
    settings = dataclasses.replace(SHORT, scopes=("@fontsource",), max_failures=3)
    http = serve(tmp_path / "http", answers({"@fontsource": list(BROKEN_RANGES)}, BROKEN_RANGES))
    snap, _ = fetch(tmp_path, http, settings)
    rows = read_downloads_csv(snap.read_bytes(EXTRACT), Window(*snap.manifest.window))
    assert [(r.package, r.last_year) for r in rows] == [
        ("@fontsource/a", 365),
        ("@fontsource/e", None),
    ]
    assert (
        "left out after failed requests (3): @fontsource/b, @fontsource/c, @fontsource/d"
        in snap.manifest.notes
    )


def test_fetch_fails_past_max_failures(tmp_path: Path) -> None:
    settings = dataclasses.replace(SHORT, scopes=("@fontsource",), max_failures=2)
    http = serve(tmp_path / "http", answers({"@fontsource": list(BROKEN_RANGES)}, BROKEN_RANGES))
    mock = mockhttp.MockHTTP.from_dir(http)
    with pytest.raises(FetchError, match="3 packages failed, over max_failures = 2"):
        fetch(tmp_path, http, settings, mock=mock)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None
    # it stops at once: no request for the package after the one that went over
    assert mock.urls()[-1] == W365.range_url("@fontsource/d")
    assert len(mock.urls()) == 1 + 1 + 4


def test_fetch_asks_again_for_failed_packages(tmp_path: Path) -> None:
    settings = dataclasses.replace(SHORT, scopes=("@fontsource",), max_failures=1)
    names = ["@fontsource/a", "@fontsource/b", "@fontsource/c"]
    http = serve(tmp_path / "http", answers({"@fontsource": names}, {n: steady(1) for n in names}))
    again = W365.range_url("@fontsource/b")
    snap, mock = fetch(tmp_path, http, settings, fail_once={again})
    rows = read_downloads_csv(snap.read_bytes(EXTRACT), Window(*snap.manifest.window))
    assert [(r.package, r.last_year) for r in rows] == [(n, 365) for n in names]
    # b is asked for again after every other package; only its answer is in the manifest
    assert mock.urls()[-3:] == [again, W365.range_url("@fontsource/c"), again]
    assert [f.url for f in snap.manifest.fetched].count(again) == 1
    assert not any("left out" in n for n in snap.manifest.notes)


@pytest.mark.parametrize(
    ("end", "message"),
    [(DAY + timedelta(days=1), "after the run date"), (DAY - timedelta(days=9), "more than 7")],
)
def test_fetch_refuses_an_odd_last_day(tmp_path: Path, end: date, message: str) -> None:
    http = serve(tmp_path / "http", answers({}, {}, end=end))
    with pytest.raises(ValueError, match=message):
        fetch(tmp_path, http, SHORT)


def test_fetch_refuses_a_cut_short_range(tmp_path: Path) -> None:
    # npm silently cuts ranges over 18 months; any other window fails the package.
    settings = dataclasses.replace(SHORT, scopes=("@fontsource",), max_failures=0)
    full = range_doc("@fontsource/a", W365, steady(1))
    doc = {**full, "start": full["downloads"][1]["day"], "downloads": full["downloads"][1:]}
    http = serve(
        tmp_path / "http",
        answers({"@fontsource": ["@fontsource/a"]}, {"@fontsource/a": (200, doc)}),
    )
    with pytest.raises(FetchError, match="max_failures = 0"):
        fetch(tmp_path, http, settings)


# --- parse ---------------------------------------------------------------------------------------


def test_parse_last_year_and_months() -> None:
    recs = parse()
    rows = {
        r.package: r
        for r in read_downloads_csv(SNAPSHOT.read_bytes(EXTRACT), Window(*SNAPSHOT.manifest.window))
    }
    months = Window(*SNAPSHOT.manifest.window).months
    for package, row in rows.items():
        obs = by_series(recs, package)
        year = obs.pop(SERIES)
        assert (year.start, year.end) == (END - timedelta(days=364), END)
        assert year.unit == "downloads"
        assert year.value == (None if row.last_year is None else float(row.last_year))
        for label, o in obs.items():
            assert label in months
            assert (o.start, o.end) == month_bounds(label)
            assert dict(o.attrs)["month"] == label
            assert o.value == float(row.months[months.index(label)])


def test_parse_first_seen() -> None:
    recs = parse()
    assert all("first_seen" in dict(r.attrs) for r in recs)
    old = by_series(recs, "@fontsource/aster-sans")
    assert dict(old[SERIES].attrs)["first_seen"] == "2025-04-11"  # the window's first day
    assert len(old) == 1 + 17
    sporadic = by_series(recs, "@fontsource/cobalt-serif")
    assert dict(sporadic[SERIES].attrs)["first_seen"] == "2025-04-13"
    new = by_series(recs, "@fontsource/dune-display")
    assert sorted(new) == ["2026-08", "2026-09", SERIES], "no months before its first download"
    assert dict(new["2026-08"].attrs)["first_seen"] == "2026-08-14"
    for package, value in (
        ("@fontsource-variable/fjord-sans", 0.0),
        ("@fontsource/ember-script", None),
    ):
        unseen = by_series(recs, package)
        assert list(unseen) == [SERIES], "no download: no months"
        assert unseen[SERIES].value == value
        assert dict(unseen[SERIES].attrs)["first_seen"] == (END + timedelta(days=1)).isoformat()


def test_parse_needs_a_window() -> None:
    snap = dataclasses.replace(
        SNAPSHOT, manifest=dataclasses.replace(SNAPSHOT.manifest, window=None)
    )
    with pytest.raises(ValueError, match="no window"):
        parse(snap)


# --- the real thing ------------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch(tmp_path: Path) -> None:
    """Two real @fontsource packages; their last-year totals equal npm's point API."""
    settings = dataclasses.replace(SETTINGS, scopes=("@fontsource",), max_packages=2)
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    with Fetcher(log=LOG) as fetcher, store.writer(COLLECTOR.name, day, COLLECTOR.version) as out:
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=out,
                raw=RawDir(tmp_path / "raw"),
                previous=None,
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    window = Window(*snap.manifest.window)
    assert window.days == settings.history_days
    rows = read_downloads_csv(snap.read_bytes(EXTRACT), window)
    assert len(rows) == 2
    assert all(r.package.startswith("@fontsource/") for r in rows)
    row = max(rows, key=lambda r: r.last_year or 0)
    with Fetcher(log=LOG) as fetcher:
        point = fetcher.scoped((API_HOST,)).get(
            f"https://{API_HOST}/downloads/point/{window.year_start}:{window.end}/{row.package}"
        )
    assert point.json()["downloads"] == row.last_year
    recs = parse(snap)
    assert {r.series for r in recs} >= {SERIES, window.months[-1]}
    gzip.decompress(snap.path.joinpath(EXTRACT).read_bytes())  # the extract is gzipped
