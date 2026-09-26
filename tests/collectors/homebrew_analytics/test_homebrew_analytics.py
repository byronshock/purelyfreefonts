"""The homebrew_analytics collector: Homebrew cask installs with cask add dates.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules on
the trimmed real fixture of 2026-09-26 (ruling T1), plus one real fetch marked
``network``.
"""

import dataclasses
import json
import logging
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.ranking.homebrew_analytics import (
    ADDS,
    BASE_URL,
    COLLECTOR,
    WINDOWS_EXTRACT,
    Settings,
    commit_of,
    first_seen_dates,
    link_target,
    parse_count,
    previous_adds,
    renamed_from,
    rows_name,
    split_cask,
    tree_casks,
    window_counts,
)
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import Budget, Fetcher, FetchError
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
SNAPSHOT = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
DAY = SNAPSHOT.date
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.homebrew_analytics")
BASE_DAY = "2025-09-25"  # the last commit before the 365-day window, 2025-09-26
AKT_URL = (
    "https://api.github.com/repos/Homebrew/homebrew-cask/commits?sha=main"
    "&path=Casks%2Ffont%2Ffont-a%2Ffont-akt.rb&since=2025-09-26T00%3A00%3A00Z&per_page=100"
)
SHA = "0123456789abcdef0123456789abcdef01234567"


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def obs(recs: list[Record], series: str = "365d") -> dict[tuple[str, str], Observation]:
    """(token, tap) -> observation of one series."""
    out = {}
    for r in recs:
        assert isinstance(r, Observation)
        if r.series == series:
            out[(r.key.key, dict(r.attrs)["tap"])] = r
    return out


def seen(recs: list[Record], token: str) -> set[str]:
    """Every ``first_seen`` a token's observations carry."""
    return {str(dict(r.attrs)["first_seen"]) for r in recs if r.key.key == token}


def copy_http(tmp: Path, edit: Callable[[list[dict[str, Any]]], None] | None = None) -> Path:
    """The fixture's recorded responses copied to ``tmp/http``, with ``edit`` applied to the index."""
    target = tmp / "http"
    shutil.copytree(HTTP, target)
    index = json.loads((target / mockhttp.INDEX).read_text(encoding="utf-8"))
    if edit is not None:
        edit(index["responses"])
    mockhttp.write_index(target, index["responses"])
    return target


def entry(responses: list[dict[str, Any]], body: str) -> dict[str, Any]:
    return next(r for r in responses if r["body"] == body)


def write_body(http: Path, name: str, doc: object) -> None:
    (http / name).write_text(json.dumps(doc), encoding="utf-8")


def fetch(
    tmp: Path,
    http: Path,
    *,
    previous: Snapshot | None = None,
    settings: object = SETTINGS,
    budgets: tuple[Budget, ...] = (),
    mock: mockhttp.MockHTTP | None = None,
) -> tuple[Snapshot | None, mockhttp.MockHTTP]:
    """Run ``fetch()`` offline against ``http`` (or ``mock``) into a fresh store; return the
    snapshot. Pass ``mock`` to see its requests after a fetch that raises."""
    mock = mockhttp.MockHTTP.from_dir(http) if mock is None else mock
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(DAY, time(6), tzinfo=UTC)),
        Fetcher(
            transport=mock.transport,
            min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0),
            budgets=budgets,
            log=LOG,
        ) as fetcher,
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
    return store.snapshot(COLLECTOR.name, DAY), mock


def commits(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    """A GitHub commit list of (sha, committer date) pairs, newest first."""
    return [{"sha": sha, "commit": {"committer": {"date": day}}} for sha, day in pairs]


# --- parse ------------------------------------------------------------------------------------


def test_counts_are_ints_from_comma_strings() -> None:
    recs = obs(parse(SNAPSHOT))
    assert recs[("font-jetbrains-mono-nerd-font", "homebrew/cask")].value == 144631.0  # "144,631"
    r = recs[("font-inter", "homebrew/cask")]
    assert (r.unit, r.start, r.end, r.value) == (
        "installs",
        date(2025, 9, 26),
        date(2026, 9, 26),
        10639.0,
    )


def test_legacy_taps_are_their_own_rows_on_the_base_token() -> None:
    fira = {
        tap: r.value for (token, tap), r in obs(parse(SNAPSHOT)).items() if "fira-code-n" in token
    }
    assert fira == {"homebrew/cask": 57081.0, "caskroom/cask": 5.0, "caskroom/fonts": 1.0}
    taps = {tap for _, tap in obs(parse(SNAPSHOT))}
    assert taps == {"homebrew/cask", "caskroom/cask", "caskroom/fonts", "homebrew/cask-fonts"}


def test_third_party_taps_and_other_casks_are_left_out() -> None:
    tokens = {r.key.key for r in parse(SNAPSHOT)}
    assert all(t.startswith("font-") for t in tokens)
    assert not {"fontbase", "fontforge", "fonts-google", "claude-code"} & tokens
    kept = [row["cask"] for row in SNAPSHOT.iter_jsonl(rows_name("365d"))]
    third = [c for c in kept if c.startswith("neodgm/neodgm/font-")]
    assert third, "the extract keeps third-party font rows; only parse leaves them out"
    assert "fontforge" not in kept  # the extract holds font rows only


def test_first_seen_is_the_add_date_inside_the_window() -> None:
    recs = parse(SNAPSHOT)
    assert seen(recs, "font-akt") == {"2026-05-03"}  # "font-akt: add"
    assert seen(recs, "font-dejavu-sans") == {"2026-02-05"}
    assert seen(recs, "font-ioskeley-mono") == {"2026-03-30"}


def test_the_add_commit_is_the_earliest_even_on_a_busy_day() -> None:
    """font-dejavu-sans was touched at 20:36:48 and 20:50:07 on its add day; the add
    commit is the earlier one."""
    rows = {r["cask"]: r for r in SNAPSHOT.load_json(ADDS)["added"]}
    assert rows["font-dejavu-sans"]["commit"] == "8aed1d087690c3209fb509b958af3e068ca7f7b4"
    assert rows["font-dejavu-sans"]["first_seen"] == "2026-02-05"


def test_the_add_commit_is_the_earliest_whatever_the_list_order(tmp_path: Path) -> None:
    """GitHub lists commits in history order, and a rebased commit's committer time can be
    out of step with it: the add date is the earliest time, not the last item listed."""
    http = copy_http(tmp_path)
    earliest = "f4d28fc7dbebd9380a2fa3f2a222c9fe99c23f5e"
    write_body(
        http,
        "commits-font-akt.json",
        commits(
            ("a" * 40, "2026-05-17T00:37:18Z"),
            (earliest, "2026-05-03T00:34:18Z"),
            ("b" * 40, "2026-05-03T09:00:00Z"),
        ),
    )
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    akt = next(r for r in snap.load_json(ADDS)["added"] if r["cask"] == "font-akt")
    assert (akt["commit"], akt["first_seen"]) == (earliest, "2026-05-03")


def test_first_seen_before_the_window_is_the_base_commit_day() -> None:
    recs = parse(SNAPSHOT)
    for token in (
        "font-jetbrains-mono",  # in both trees
        "font-ibm-plex",  # removed during the window
        "font-monaspace-nerd-font",  # an old name in neither tree
        "font-opendyslexic",  # renamed in the window from font-open-dyslexic
        "font-opendyslexic-nerd-font",
    ):
        assert seen(recs, token) == {BASE_DAY}, token
    assert seen(recs, "font-fira-code-nerd-font") == {BASE_DAY}  # legacy rows too


def test_each_window_is_its_own_series() -> None:
    recs = parse(SNAPSHOT)
    starts = {r.series: r.start for r in recs if isinstance(r, Observation)}
    assert starts == {"30d": date(2026, 8, 27), "90d": date(2026, 6, 28), "365d": date(2025, 9, 26)}
    assert {r.end for r in recs if isinstance(r, Observation)} == {date(2026, 9, 26)}
    akt = {r.series: r.value for r in recs if r.key.key == "font-akt"}
    assert akt == {"365d": 28.0, "90d": 4.0}  # no 30-day row: not installed that month


def test_parse_reads_only_the_configured_windows() -> None:
    only = dataclasses.replace(SETTINGS, windows=("365d",), dates_window="365d")
    assert {r.series for r in parse(SNAPSHOT, only) if isinstance(r, Observation)} == {"365d"}


def test_window_counts_sum_case_variants_and_skip_bad_rows() -> None:
    rows = [
        {"cask": "font-Aster", "count": 3},
        {"cask": "font-aster", "count": "1,200"},
        {"cask": "Caskroom/Cask/font-aster", "count": 2},
        {"cask": "someone/tap/font-aster", "count": 99},
        {"cask": "font-birch", "count": "n/a"},
        {"cask": 7, "count": 1},
        "not a row",
    ]
    assert window_counts(rows, SETTINGS) == {
        ("font-aster", "caskroom/cask"): 2,
        ("font-aster", "homebrew/cask"): 1203,
    }


def test_first_seen_dates_needs_a_base_commit() -> None:
    assert first_seen_dates(SNAPSHOT.load_json(ADDS))[0] == date(2025, 9, 25)
    with pytest.raises(ValueError, match="no base commit"):
        first_seen_dates({"added": []})
    base = {"base": {"date": "2025-09-25"}, "added": [{"cask": "font-x", "first_seen": None}]}
    assert first_seen_dates(base) == (date(2025, 9, 25), {})


# --- fetch --------------------------------------------------------------------------------------


def test_fetch_keeps_font_rows_as_ints_and_no_personal_data(tmp_path: Path) -> None:
    snap, mock = fetch(tmp_path, HTTP)
    assert snap is not None
    assert len(mock.requests) == 11  # 3 windows, 2 commits, 2 trees, renames, 3 add dates
    rows = list(snap.iter_jsonl(rows_name("365d")))
    assert rows == sorted(rows, key=lambda r: (r["cask"], r["count"]))
    assert all(type(r["count"]) is int and set(r) == {"cask", "count"} for r in rows)
    assert {"cask": "font-jetbrains-mono-nerd-font", "count": 144631} in rows
    windows = snap.load_json(WINDOWS_EXTRACT)
    assert windows["365d"] == {
        "end_date": "2026-09-26",
        "start_date": "2025-09-26",
        "total_count": 24772689,
        "total_items": 23898,
    }
    m = snap.manifest
    assert (m.window, m.data_date) == ((date(2025, 9, 26), date(2026, 9, 26)), date(2026, 9, 26))
    assert any("3 of them from third-party taps" in n for n in m.notes)
    assert any("5 added (3 looked up, 2 renames), 3 removed" in n for n in m.notes)
    for e in m.extracts:
        text = snap.read_bytes(e.path).decode()
        assert "@" not in text, e.path  # no email
        assert "author" not in text, e.path
    assert [e.path for e in m.extracts] == [e.path for e in SNAPSHOT.manifest.extracts]
    for e in m.extracts:  # the fixture snapshot is exactly what fetch() writes
        assert snap.read_bytes(e.path) == SNAPSHOT.read_bytes(e.path), e.path


def test_fetch_reuses_the_previous_snapshots_add_dates(tmp_path: Path) -> None:
    def drop_lookups(responses: list[dict[str, Any]]) -> None:
        responses[:] = [r for r in responses if "&path=" not in r["url"]]

    snap, mock = fetch(tmp_path, copy_http(tmp_path, drop_lookups), previous=SNAPSHOT)
    assert snap is not None
    assert len(mock.requests) == 8  # no commit lookups
    assert snap.load_json(ADDS) == SNAPSHOT.load_json(ADDS)


def test_previous_add_dates_are_reused_only_inside_the_window() -> None:
    known = previous_adds(SNAPSHOT, date(2025, 9, 26))
    assert sorted(known) == ["font-akt", "font-dejavu-sans", "font-ioskeley-mono"]  # no renames
    assert sorted(previous_adds(SNAPSHOT, date(2026, 3, 1))) == ["font-akt", "font-ioskeley-mono"]
    assert previous_adds(None, date(2025, 9, 26)) == {}


def test_a_cask_whose_file_moved_is_looked_up_again(tmp_path: Path) -> None:
    adds = SNAPSHOT.load_json(ADDS)
    for row in adds["added"]:
        if row["cask"] == "font-akt":
            row.update(path="Casks/font-akt.rb", first_seen="2026-06-01")
    store = Store(tmp_path / "earlier")
    with store.writer(COLLECTOR.name, date(2026, 8, 27), COLLECTOR.version) as writer:
        writer.write_json(ADDS, adds)
    earlier = store.snapshot(COLLECTOR.name, date(2026, 8, 27))
    snap, mock = fetch(tmp_path, HTTP, previous=earlier)
    assert snap is not None
    assert [u for u in mock.urls() if "path=" in u] == [mockhttp.normalize_url(AKT_URL)]
    assert snap.load_json(ADDS) == SNAPSHOT.load_json(ADDS)  # 2026-05-03 again, not 06-01


def test_fetch_needs_a_commit_before_the_window(tmp_path: Path) -> None:
    http = copy_http(tmp_path)
    write_body(http, "commits-base.json", [])
    with pytest.raises(ValueError, match="no commit on main before 2025-09-26T00:00:00Z"):
        fetch(tmp_path, http)


def test_fetch_follows_the_last_page_of_a_long_history(tmp_path: Path) -> None:
    last = AKT_URL.replace("repos/Homebrew/homebrew-cask", "repositories/3623050") + "&page=2"

    def paginate(responses: list[dict[str, Any]]) -> None:
        akt = entry(responses, "commits-font-akt.json")
        akt["headers"] = {"link": f'<{last}>; rel="next", <{last}>; rel="last"'}
        akt["body"] = "akt-page1.json"
        responses.append({"url": last, "body": "akt-page2.json"})

    http = copy_http(tmp_path, paginate)
    write_body(http, "akt-page1.json", commits((SHA, "2026-08-31T07:58:22Z")))
    write_body(
        http,
        "akt-page2.json",
        commits(
            ("ef6202c97bf83362733f002fe0c33c48cf82e30b", "2026-05-17T00:37:18Z"),
            ("f4d28fc7dbebd9380a2fa3f2a222c9fe99c23f5e", "2026-05-03T00:34:18Z"),
        ),
    )
    snap, mock = fetch(tmp_path, http)
    assert snap is not None
    assert mockhttp.normalize_url(last) in mock.urls()
    listed = {mockhttp.normalize_url(f.url) for f in snap.manifest.fetched}
    assert mockhttp.normalize_url(last) in listed  # the second request is in the manifest too
    assert seen(parse(snap), "font-akt") == {"2026-05-03"}
    akt = next(r for r in snap.load_json(ADDS)["added"] if r["cask"] == "font-akt")
    assert akt["commit"] == "f4d28fc7dbebd9380a2fa3f2a222c9fe99c23f5e"  # the oldest, not page 1's


def test_a_cask_with_no_commit_counts_as_older(tmp_path: Path) -> None:
    http = copy_http(tmp_path)
    write_body(http, "commits-font-akt.json", [])
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    assert seen(parse(snap), "font-akt") == {BASE_DAY}
    assert any("no commit found for font-akt" in n for n in snap.manifest.notes)


def test_without_the_renames_file_renamed_casks_are_looked_up(tmp_path: Path) -> None:
    lookups = {
        "font-opendyslexic": "2026-01-10T12:00:00Z",
        "font-opendyslexic-nerd-font": "2026-01-11T12:00:00Z",
    }

    def gone(responses: list[dict[str, Any]]) -> None:
        renames = entry(responses, "cask_renames.json")
        renames.update(status=404, body=None)
        for token in lookups:
            url = AKT_URL.replace("font-a%2Ffont-akt.rb", f"font-o%2F{token}.rb")
            responses.append({"url": url, "body": f"commits-{token}.json"})

    http = copy_http(tmp_path, gone)
    for token, day in lookups.items():
        write_body(http, f"commits-{token}.json", commits((SHA, day)))
    snap, _ = fetch(tmp_path, http)
    assert snap is not None
    recs = parse(snap)
    assert seen(recs, "font-opendyslexic") == {"2026-01-10"}
    assert any("cask_renames.json not found" in n for n in snap.manifest.notes)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b'{"error": "unavailable"}', "not a cask_install analytics body"),
        (b'{"category": "install", "items": []}', "not a cask_install analytics body"),
        (
            b'{"category": "cask_install", "start_date": "2026-09-26", "end_date": "2025-09-26",'
            b' "items": []}',
            "is after end_date",
        ),
        (
            b'{"category": "cask_install", "start_date": "2025-09-26", "end_date": "2026-09-26",'
            b' "items": [{"cask": "fontforge", "count": "12"}]}',
            "no font-\\* row",
        ),
        (b"<html>maintenance</html>", "Expecting value"),
    ],
    ids=["no-items", "other-category", "dates-reversed", "no-font-rows", "not-json"],
)
def test_fetch_refuses_a_ruler_body_that_is_not_the_analytics(
    body: bytes, message: str, tmp_path: Path
) -> None:
    http = copy_http(tmp_path)
    (http / "cask-install-365d.json").write_bytes(body)
    with pytest.raises(ValueError, match=message):
        fetch(tmp_path, http)
    assert Store(tmp_path / "store").dates(COLLECTOR.name, complete_only=False) == []


def test_fetch_refuses_a_ruler_window_that_is_gone(tmp_path: Path) -> None:
    def gone(responses: list[dict[str, Any]]) -> None:
        entry(responses, "cask-install-365d.json").update(status=404, body=None)

    with pytest.raises(ValueError, match=r"365d\.json: HTTP 404"):
        fetch(tmp_path, copy_http(tmp_path, gone))
    assert Store(tmp_path / "store").dates(COLLECTOR.name, complete_only=False) == []


def test_fetch_refuses_a_ruler_window_that_shrank(tmp_path: Path) -> None:
    http = copy_http(tmp_path)
    doc = json.loads((http / "cask-install-365d.json").read_text(encoding="utf-8"))
    doc["items"] = [i for i in doc["items"] if i["cask"].startswith("font-j")]
    write_body(http, "cask-install-365d.json", doc)
    with pytest.raises(ValueError, match="min_share"):
        fetch(tmp_path, http, previous=SNAPSHOT)  # 2 font rows, down from 31
    snap, _ = fetch(tmp_path / "fresh", http)  # nothing to compare with
    assert snap is not None


def _not_json(http: Path, responses: list[dict[str, Any]]) -> None:
    (http / "cask-install-90d.json").write_bytes(b"<html>maintenance</html>")


def _gone(http: Path, responses: list[dict[str, Any]]) -> None:
    entry(responses, "cask-install-30d.json").update(status=404, body=None)


def _shrunk(http: Path, responses: list[dict[str, Any]]) -> None:
    doc = json.loads((http / "cask-install-30d.json").read_text(encoding="utf-8"))
    doc["items"] = [i for i in doc["items"] if i["cask"].startswith("font-j")]
    write_body(http, "cask-install-30d.json", doc)


@pytest.mark.parametrize(
    ("window", "breakage", "message"),
    [
        ("90d", _not_json, "Expecting value"),
        ("30d", _gone, "HTTP 404"),
        ("30d", _shrunk, "min_share"),
    ],
    ids=["not-json", "http-404", "shrank"],
)
def test_a_broken_window_other_than_the_ruler_is_left_out(
    window: str,
    breakage: Callable[[Path, list[dict[str, Any]]], None],
    message: str,
    tmp_path: Path,
) -> None:
    """Only Rising reads the 30- and 90-day files; losing one must not make the ruler stale."""
    http = copy_http(tmp_path)
    index = json.loads((http / mockhttp.INDEX).read_text(encoding="utf-8"))
    breakage(http, index["responses"])
    mockhttp.write_index(http, index["responses"])
    snap, mock = fetch(tmp_path, http, previous=SNAPSHOT)
    assert snap is not None
    url = f"{BASE_URL}/{window}.json"
    assert url in mock.urls()
    assert url in {f.url for f in snap.manifest.fetched}  # contacted, so listed
    assert not snap.has(rows_name(window))
    assert window not in snap.load_json(WINDOWS_EXTRACT)
    assert any(
        n.startswith(f"{window} window left out:") and message in n for n in snap.manifest.notes
    )
    recs = parse(snap)
    assert window not in {r.series for r in recs if isinstance(r, Observation)}
    for kept in ("30d", "90d", "365d"):
        if kept != window:
            assert snap.read_bytes(rows_name(kept)) == SNAPSHOT.read_bytes(rows_name(kept))
    assert snap.load_json(ADDS) == SNAPSHOT.load_json(ADDS)
    assert snap.manifest.window == SNAPSHOT.manifest.window


def test_fetch_refuses_a_truncated_tree(tmp_path: Path) -> None:
    http = copy_http(tmp_path)
    doc = json.loads((http / "tree-head.json").read_text(encoding="utf-8"))
    doc["truncated"] = True
    write_body(http, "tree-head.json", doc)
    with pytest.raises(ValueError, match="truncated"):
        fetch(tmp_path, http)


def test_fetch_caps_the_add_date_lookups(tmp_path: Path) -> None:
    capped = dataclasses.replace(SETTINGS, max_lookups=2)
    with pytest.raises(ValueError, match="3 casks added since 2025-09-26 need an add date"):
        fetch(tmp_path, HTTP, settings=capped)


def test_a_failed_add_date_lookup_fails_the_fetch(tmp_path: Path) -> None:
    """An undated new cask would pass for an old one (full exposure, never "too new"), so
    the fetch fails and the stale policy keeps the last good ruler instead."""

    def gone(responses: list[dict[str, Any]]) -> None:
        entry(responses, "commits-font-dejavu-sans.json").update(status=404, body=None)

    with pytest.raises(FetchError, match="HTTP 404"):
        fetch(tmp_path, copy_http(tmp_path, gone))
    assert Store(tmp_path / "store").dates(COLLECTOR.name, complete_only=False) == []


def test_fetch_checks_the_github_budget_before_the_first_lookup(tmp_path: Path) -> None:
    """2 commit and 2 tree calls leave 1 of 5; 3 lookups can't fit, so none is made."""
    mock = mockhttp.MockHTTP.from_dir(HTTP)
    with pytest.raises(
        ValueError, match="3 add-date lookups need more GitHub API calls than the 1"
    ):
        fetch(tmp_path, HTTP, budgets=(Budget("github", 5),), mock=mock)
    assert not [u for u in mock.urls() if "path=" in u]
    assert Store(tmp_path / "store").dates(COLLECTOR.name, complete_only=False) == []
    snap, _ = fetch(tmp_path / "enough", HTTP, budgets=(Budget("github", 7),))
    assert snap is not None


# --- helpers --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "count"),
    [
        ("144,631", 144631),
        ("7", 7),
        (12, 12),
        ("1,133,774", 1133774),
        (" 5 ", 5),
        (-1, None),
        (True, None),
        (1.5, None),
        ("", None),
        ("12a", None),
        ("\u0661\u0662", None),  # Arabic-Indic digits
    ],
)
def test_parse_count(value: object, count: int | None) -> None:
    assert parse_count(value) == count


@pytest.mark.parametrize(
    ("cask", "split"),
    [
        ("font-inter", ("homebrew/cask", "font-inter")),
        ("caskroom/fonts/font-Inter", ("caskroom/fonts", "font-inter")),
        ("homebrew/cask/font-inter", ("homebrew/cask", "font-inter")),
        ("user/tap/font-inter", ("user/tap", "font-inter")),
    ],
)
def test_split_cask(cask: str, split: tuple[str, str]) -> None:
    assert split_cask(cask, "homebrew/cask") == split


def test_link_target() -> None:
    header = '<https://a.example/?page=2>; rel="next", <https://a.example/?page=9>; rel="last"'
    assert link_target(header, "last") == "https://a.example/?page=9"
    assert link_target(header, "prev") is None
    assert link_target(None, "last") is None


def test_renamed_from_follows_chains_and_stops_on_cycles() -> None:
    renames = {"font-a": "font-b", "font-b": "font-c", "font-x": "font-y", "font-y": "font-x"}
    assert renamed_from(renames, ["font-a", "font-x"]) == {
        "font-b": "font-a",
        "font-c": "font-a",
        "font-y": "font-x",
    }
    assert renamed_from(renames, []) == {}


def test_commit_of_reads_the_committer_day_in_utc() -> None:
    assert commit_of(commits((SHA, "2026-05-03T23:30:00-02:00"))[0]).day == date(2026, 5, 4)
    for bad in (
        {"sha": "abc", "commit": {"committer": {"date": "2026-05-03T00:00:00Z"}}},
        {"sha": SHA, "commit": {"author": {"date": "2026-05-03T00:00:00Z"}}},
        {"sha": SHA, "commit": "x"},
        "not a commit",
    ):
        with pytest.raises(ValueError, match="not a GitHub commit"):
            commit_of(bad)
    with pytest.raises(ValueError, match="no time zone"):
        commit_of(commits((SHA, "2026-05-03T00:00:00"))[0])


def test_tree_casks_lists_font_cask_files_only() -> None:
    doc = {
        "truncated": False,
        "tree": [
            {"path": "font-a", "type": "tree"},
            {"path": "font-a/font-aster.rb", "type": "blob"},
            {"path": "font-a/README.md", "type": "blob"},
            {"path": "font-a/fontforge.rb", "type": "blob"},
            "junk",
        ],
    }
    assert tree_casks(doc, SETTINGS, "t") == {"font-aster": "Casks/font/font-a/font-aster.rb"}
    with pytest.raises(ValueError, match="truncated or unmarked"):
        tree_casks({"tree": doc["tree"]}, SETTINGS, "t")
    with pytest.raises(ValueError, match="no font-"):
        tree_casks({"truncated": False, "tree": []}, SETTINGS, "t")
    with pytest.raises(ValueError, match="not a git tree"):
        tree_casks([], SETTINGS, "t")


# --- settings ---------------------------------------------------------------------------------


def test_settings_file_matches_the_defaults() -> None:
    assert Settings() == SETTINGS


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"base_url": "https://example.com/api"}, "https URL on formulae.brew.sh"),
        ({"base_url": BASE_URL + "/"}, "without a trailing slash"),
        ({"windows": ()}, "name each window once"),
        ({"windows": ("30d", "30d")}, "name each window once"),
        ({"windows": ("7d", "365d")}, "not among"),
        ({"windows": ("30d",)}, "dates_window"),
        ({"dates_window": "90d"}, "must be the longest window \\('365d'\\)"),
        ({"token_prefix": ""}, "must not be empty"),
        ({"legacy_taps": ("Caskroom/Cask",)}, "lower-case"),
        ({"legacy_taps": ("homebrew/cask",)}, "names main_tap"),
        ({"min_share": 1.5}, "between 0 and 1"),
        ({"repo": "homebrew-cask"}, "<owner>/<name>"),
        ({"font_dir": "/Casks/font"}, "relative git name"),
        ({"font_dir": "Casks/../font"}, "relative git name"),
        ({"max_lookups": -1}, "must not be negative"),
    ],
    ids=[
        "other-host",
        "trailing-slash",
        "no-window",
        "repeated-window",
        "unknown-window",
        "dates-window-missing",
        "dates-window-not-longest",
        "empty-prefix",
        "tap-case",
        "legacy-is-main",
        "share-over-1",
        "bare-repo",
        "absolute-dir",
        "dotdot-dir",
        "negative-cap",
    ],
)
def test_settings_are_checked(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**changes)


# --- the real endpoints -------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """The real 30-day window (about 670 KB) with its add dates: a few dozen GitHub calls.

    Set ``GITHUB_TOKEN`` for the API (60 anonymous calls an hour may run out).
    """
    settings = dataclasses.replace(SETTINGS, windows=("30d",), dates_window="30d")
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
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    recs = list(COLLECTOR.parse(ParseContext(snapshot=snap, settings=settings, log=LOG)))
    # Legacy-tap rows share their cask's key; keep only the main tap's.
    main = {
        r.key.key: r
        for r in recs
        if isinstance(r, Observation) and dict(r.attrs)["tap"] == settings.main_tap
    }
    assert len(main) >= 500
    assert main["font-jetbrains-mono-nerd-font"].value >= 1000
    adds = snap.load_json(ADDS)
    assert adds["base"]["casks"] >= 2000
    assert all(r["first_seen"] >= adds["base"]["date"] for r in adds["added"] if r["first_seen"])
