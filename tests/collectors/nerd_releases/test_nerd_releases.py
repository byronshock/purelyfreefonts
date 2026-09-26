"""The nerd_releases collector: Nerd Fonts release downloads through GitHub GraphQL.

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules.

The fixture's source of truth is ``releases.json``: trimmed real releases in
GraphQL's node shape (ruling T1). ``simulate`` plays GitHub's paging over such
a list, so ``http/`` is derived from it; after changing the queries or
``releases.json``, rebuild ``http/``, ``snapshot/`` and ``expected.jsonl`` with::

    uv run python -m tests.collectors.nerd_releases.test_nerd_releases
"""

import dataclasses
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.nerd_releases import (
    ASSETS,
    ASSETS_QUERY,
    COLLECTOR,
    RELEASES_QUERY,
    RESOURCE_LIMITS,
    Settings,
    asset_base,
    assets_variables,
    check_shrink,
    observations,
    previous_rows,
    releases_variables,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import GITHUB_GRAPHQL_URL, Fetcher, FetchError
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record, SourceKey
from tff_catalog.store import RawDir, Snapshot, SnapshotWriter, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
RELEASES = FIXTURE / "releases.json"
DAY = date(2026, 9, 26)
LATER = date(2026, 10, 26)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
LOG = logging.getLogger("tests.nerd_releases")
TOKEN = "fixture-token"


def fixture_releases() -> list[dict[str, Any]]:
    return json.loads(RELEASES.read_text(encoding="utf-8"))


def fixture_snapshot() -> Snapshot:
    return regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)


def parse(snapshot: Snapshot, settings: Settings = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def by_release(recs: list[Record]) -> dict[tuple[str, str], Observation]:
    out = {}
    for r in recs:
        assert isinstance(r, Observation)
        out[(dict(r.attrs)["release"], r.key.key)] = r
    return out


# --- a simulated GitHub GraphQL endpoint ----------------------------------------------------------


def _conn(nodes: list[Any], total: int, start: int, cursor: str) -> dict[str, Any]:
    more = start + len(nodes) < total
    return {
        "totalCount": total,
        "pageInfo": {"hasNextPage": more, "endCursor": f"{cursor}:{start + len(nodes)}"},
        "nodes": nodes,
    }


def _payload(query: str, variables: dict[str, object]) -> dict[str, Any]:
    return {"query": query, "variables": variables}


def simulate(
    releases: list[dict[str, Any]], settings: Settings, *, first: int | None = None
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """GitHub's answers to the requests ``fetch()`` makes for ``releases``: (payload, body).

    Release pages of ``first`` (default: the settings' page size), each release
    with its first ``assets_per_page`` assets, then the later asset pages of
    every release. Cursors are invented: ``<what>:<offset>``.
    """
    per_page = first or settings.releases_per_page
    size = settings.assets_per_page
    out = []
    after = None
    for start in range(0, max(len(releases), 1), per_page):
        nodes = []
        for rel in releases[start : start + per_page]:
            node = {k: v for k, v in rel.items() if k != "assets"}
            node["releaseAssets"] = _conn(
                rel["assets"][:size], len(rel["assets"]), 0, f"{rel['tagName']}"
            )
            nodes.append(node)
        body = {
            "data": {"repository": {"releases": _conn(nodes, len(releases), start, "releases")}}
        }
        variables = releases_variables(settings, per_page, after)
        out.append((_payload(RELEASES_QUERY, variables), body))
        after = f"releases:{start + len(nodes)}"
    for rel in releases:
        tag, assets = rel["tagName"], rel["assets"]
        for start in range(size, len(assets), size):
            conn = _conn(assets[start : start + size], len(assets), start, tag)
            body = {"data": {"repository": {"release": {"releaseAssets": conn}}}}
            variables = assets_variables(settings, tag, size, f"{tag}:{start}")
            out.append((_payload(ASSETS_QUERY, variables), body))
    return out


def refused(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """GitHub's answer to a query that touches too many assets."""
    error = {"type": "RESOURCE_LIMITS_EXCEEDED", "message": f"{RESOURCE_LIMITS}."}
    return payload, {"data": {"repository": None}, "errors": [error, error]}


def write_http(directory: Path, exchanges: list[tuple[dict[str, Any], dict[str, Any]]]) -> Path:
    """A mockhttp fixture answering each GraphQL payload with its body."""
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    index = []
    for i, (payload, body) in enumerate(exchanges, start=1):
        name = f"graphql-{i:02d}.json"
        (directory / name).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        index.append(
            {
                "method": "POST",
                "url": GITHUB_GRAPHQL_URL,
                "request_json": payload,
                "headers": {"content-type": "application/json; charset=utf-8"},
                "body": name,
            }
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
) -> tuple[Snapshot | None, list[str]]:
    """Run ``fetch()`` offline into a fresh store; return the snapshot and the requested URLs."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    try:
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
                    raw=RawDir(tmp / "raw"),
                    previous=previous,
                    settings=settings,
                    log=LOG,
                )
            )
    finally:
        assert not mock.unmatched, mock.unmatched
    return store.snapshot(COLLECTOR.name, day), mock.urls()


def snapshot_of(tmp: Path, rows: list[dict[str, Any]], day: date = DAY) -> Snapshot:
    """A snapshot holding ``rows`` as its extract, with ``day`` as the data date."""
    writer = SnapshotWriter(tmp / "made", COLLECTOR.name, day, COLLECTOR.version)
    writer.write_jsonl(ASSETS, rows)
    writer.set_data_date(day)
    return writer.close()


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)


# --- the fixture ----------------------------------------------------------------------------------


def test_http_fixture_is_derived_from_releases_json(tmp_path: Path) -> None:
    fresh = write_http(tmp_path / "http", simulate(fixture_releases(), SETTINGS))
    got = sorted(p.name for p in HTTP.iterdir())
    assert got == sorted(p.name for p in fresh.iterdir())
    for name in got:
        assert (HTTP / name).read_bytes() == (fresh / name).read_bytes(), (
            f"http/{name} is stale; run: uv run python -m "
            "tests.collectors.nerd_releases.test_nerd_releases"
        )


# --- parse ----------------------------------------------------------------------------------------


def test_one_lifetime_observation_per_release_and_asset_base() -> None:
    recs = by_release(parse(fixture_snapshot()))
    assert len(recs) == 17
    hack = recs[("v3.5.1", "Hack")]
    assert hack.key == SourceKey("nerd-folder", "Hack")
    assert hack.value == 76_061 + 18_834  # Hack.tar.xz + Hack.zip
    assert (hack.series, hack.unit, hack.source) == ("lifetime", "downloads", "nerd_releases")
    assert (hack.start, hack.end) == (date(2026, 8, 21), DAY)
    assert dict(hack.attrs) == {
        "first_seen": "2026-08-21",
        "prerelease": False,
        "published_at": "2026-08-21",
        "release": "v3.5.1",
    }
    assert recs[("v3.0.0", "Hack")].value == 41_294  # a zip only
    assert {k for _, k in recs} == {
        "Agave",
        "CascadiaCode",
        "FiraCode",
        "Hack",
        "Iosevka_makegroups",
        "JetBrainsMono",
        "Meslo",
        "Monoid",
    }


def test_non_fonts_are_dropped() -> None:
    keys = {r.key.key for r in parse(fixture_snapshot())}
    assert not keys & {"FontPatcher", "NerdFontsSymbolsOnly", "SHA-256.txt", "SHA-256", "Regular"}
    rows = list(fixture_snapshot().iter_jsonl(ASSETS))
    # 30 assets, less FontPatcher, SHA-256.txt, 3 Symbols Only and v2.0.0's Regular.zip
    assert len(rows) == 24
    dropped = ("FontPatcher", "NerdFontsSymbols", "SHA", "Regular")
    assert not any(r["asset"].startswith(dropped) for r in rows)


def test_prereleases_are_kept_and_marked() -> None:
    recs = by_release(parse(fixture_snapshot()))
    rc = [o for (tag, _), o in recs.items() if tag == "v2.3.0-RC"]
    assert len(rc) == 3
    assert all(dict(o.attrs)["prerelease"] is True for o in rc)
    assert dict(recs[("v2.3.0-RC", "Hack")].attrs)["published_at"] == "2022-10-07"
    assert recs[("v2.3.0-RC", "Hack")].start == date(2022, 10, 10)  # asset created after publish


def test_first_seen_is_the_earliest_archive_of_the_base(tmp_path: Path) -> None:
    rows = [
        row("v1", "Birch.zip", 10, created="2026-01-05T23:30:00-02:00"),  # 2026-01-06 in UTC
        row("v1", "Birch.tar.xz", 5, created="2026-01-07T00:00:00Z"),
    ]
    (obs,) = parse(snapshot_of(tmp_path, rows))
    assert isinstance(obs, Observation)
    assert (obs.value, obs.start, dict(obs.attrs)["first_seen"]) == (
        15.0,
        date(2026, 1, 6),
        "2026-01-06",
    )


def test_an_asset_created_after_the_data_date_starts_on_it() -> None:
    later = row("v1", "Birch.zip", 3, created="2026-10-01T00:00:00Z")
    (obs,) = observations([later], date(2026, 9, 26), SETTINGS)
    assert (obs.start, obs.end, dict(obs.attrs)["first_seen"]) == (
        date(2026, 9, 26),
        date(2026, 9, 26),
        "2026-09-26",
    )


def test_parse_applies_the_current_exclusions(tmp_path: Path) -> None:
    # An older extract (a baseline) may hold assets that today's settings exclude.
    rows = [row("v1", "Birch.zip", 1), row("v1", "Cedar.zip", 2), row("v1", "Regular.zip", 3)]
    snap = snapshot_of(tmp_path, rows)
    assert [r.key.key for r in parse(snap)] == ["Birch", "Cedar"]
    narrower = dataclasses.replace(SETTINGS, exclude=(*SETTINGS.exclude, "Cedar"))
    assert [r.key.key for r in parse(snap, narrower)] == ["Birch"]


def row(
    release: str,
    asset: str,
    downloads: int,
    *,
    created: str = "2026-01-02T03:04:05Z",
    published: str = "2026-01-02T05:00:00Z",
    prerelease: bool = False,
) -> dict[str, Any]:
    return {
        "release": release,
        "published_at": published,
        "prerelease": prerelease,
        "asset": asset,
        "downloads": downloads,
        "created_at": created,
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"downloads": -1}, "downloads"),
        ({"downloads": 1.5}, "downloads"),
        ({"prerelease": "no"}, "prerelease"),
        ({"created_at": "2026-01-02"}, "created_at"),
        ({"published_at": None}, "published_at"),
        ({"asset": ""}, "asset"),
        ({"uploader": "someone"}, "fields"),
    ],
)
def test_parse_rejects_malformed_rows(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    snap = snapshot_of(tmp_path, [row("v1", "Birch.zip", 1), {**row("v1", "A.zip", 1), **change}])
    with pytest.raises(ValueError, match=f"row 2.*{message}|{message}.*row 2"):
        parse(snap)


def test_parse_rejects_an_asset_listed_twice(tmp_path: Path) -> None:
    snap = snapshot_of(tmp_path, [row("v1", "Birch.zip", 1), row("v1", "Birch.zip", 2)])
    with pytest.raises(ValueError, match="listed twice"):
        parse(snap)


@pytest.mark.parametrize(
    ("name", "base"),
    [
        ("JetBrainsMono.zip", "JetBrainsMono"),
        ("JetBrainsMono.tar.xz", "JetBrainsMono"),
        ("Go-Mono.tar.xz", "Go-Mono"),
        ("3270.zip", "3270"),
        ("FontPatcher.zip", None),
        ("NerdFontsSymbolsOnly.tar.xz", None),
        ("SHA-256.txt", None),
        ("Regular.zip", None),
        ("BoldItalic.tar.xz", None),
        ("RegularMono.zip", "RegularMono"),
        ("Hack.xz", None),
        (".zip", None),
    ],
)
def test_asset_base(name: str, base: str | None) -> None:
    assert asset_base(name, SETTINGS) == base


# --- settings -------------------------------------------------------------------------------------


def test_settings_file_spells_out_the_defaults() -> None:
    assert Settings() == SETTINGS
    assert SETTINGS.exclude == (
        "FontPatcher",
        "NerdFontsSymbolsOnly",
        "Bold",
        "BoldItalic",
        "Italic",
        "Regular",
    )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"repo": "nerd-fonts"}, "repo"),
        ({"releases_per_page": 0}, "releases_per_page"),
        ({"assets_per_page": 101}, "assets_per_page"),
        ({"suffixes": []}, "suffixes"),
        ({"suffixes": ["zip"]}, "suffixes"),
        ({"exclude": [""]}, "exclude"),
        ({"min_share": 1.5}, "min_share"),
        ({"sereis": 1}, "sereis"),
    ],
)
def test_settings_reject_bad_values(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, data, where="sources/nerd_releases.toml")


# --- fetch ----------------------------------------------------------------------------------------


def test_fetch_keeps_the_font_assets_of_every_release(tmp_path: Path) -> None:
    snap, urls = fetch(tmp_path, HTTP)
    assert snap is not None
    assert urls == [GITHUB_GRAPHQL_URL] * 2  # 6 releases in pages of 4
    assert snap.read_bytes(ASSETS) == fixture_snapshot().read_bytes(ASSETS)
    assert snap.manifest.extract(ASSETS).rows == 24
    assert snap.manifest.data_date == DAY
    assert [f.kept for f in snap.manifest.fetched] == [False, False]
    assert snap.manifest.notes == ("6 releases, 5 with font assets; 24 of 30 assets kept",)
    rows = list(snap.iter_jsonl(ASSETS))
    assert rows == sorted(rows, key=lambda r: (r["release"], r["asset"]))


def test_small_pages_give_the_same_extract(tmp_path: Path) -> None:
    small = dataclasses.replace(SETTINGS, releases_per_page=2, assets_per_page=3)
    exchanges = simulate(fixture_releases(), small)
    assert len(exchanges) == 3 + 3 + 2 + 1 + 1  # release pages, then v3.5.1, v3.4.0, v3.0.0, v2.0.0
    snap, urls = fetch(tmp_path, write_http(tmp_path / "http", exchanges), settings=small)
    assert snap is not None
    assert len(urls) == len(exchanges) == len(snap.manifest.fetched)
    assert snap.read_bytes(ASSETS) == fixture_snapshot().read_bytes(ASSETS)


def test_a_page_refused_as_too_big_is_asked_again_at_half_the_size(tmp_path: Path) -> None:
    halved = dataclasses.replace(SETTINGS, releases_per_page=2)
    first = simulate(fixture_releases(), SETTINGS)[0][0]
    exchanges = [refused(first), *simulate(fixture_releases(), halved)]
    snap, urls = fetch(tmp_path, write_http(tmp_path / "http", exchanges))
    assert snap is not None
    assert len(urls) == 1 + 3
    assert len(snap.manifest.fetched) == 4  # the refused request is listed too
    assert snap.read_bytes(ASSETS) == fixture_snapshot().read_bytes(ASSETS)


def test_a_page_refused_at_size_one_fails_the_fetch(tmp_path: Path) -> None:
    one = dataclasses.replace(SETTINGS, releases_per_page=1)
    first = simulate(fixture_releases(), one)[0][0]
    http = write_http(tmp_path / "http", [refused(first)])
    with pytest.raises(FetchError, match=RESOURCE_LIMITS):
        fetch(tmp_path, http, settings=one)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_other_graphql_errors_fail_the_fetch(tmp_path: Path) -> None:
    payload = simulate(fixture_releases(), SETTINGS)[0][0]
    body = {"data": None, "errors": [{"type": "NOT_FOUND", "message": "Could not resolve"}]}
    with pytest.raises(FetchError, match="Could not resolve"):
        fetch(tmp_path, write_http(tmp_path / "http", [(payload, body)]))


def test_a_missing_repository_fails_the_fetch(tmp_path: Path) -> None:
    payload = simulate(fixture_releases(), SETTINGS)[0][0]
    with pytest.raises(FetchError, match="no repository ryanoasis/nerd-fonts"):
        fetch(tmp_path, write_http(tmp_path / "http", [(payload, {"data": {"repository": None}})]))


def test_the_fetch_needs_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN")
    with pytest.raises(FetchError, match="GITHUB_TOKEN"):
        fetch(tmp_path, HTTP)


def _exchanges_with(key: str, value: object) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The fixture's answers, with ``key`` of every release page (``node.<k>``: of the
    first release; ``node.assetTotal``: its asset count) set to ``value``."""
    exchanges = simulate(fixture_releases(), SETTINGS)
    pages = [body["data"]["repository"]["releases"] for _, body in exchanges[:2]]
    if key == "node.assetTotal":
        pages[0]["nodes"][0]["releaseAssets"]["totalCount"] = value
    elif key.startswith("node."):
        pages[0]["nodes"][0][key.removeprefix("node.")] = value
    else:
        for page in pages:
            page[key] = value
    return exchanges


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("totalCount", 7, "got 6 releases, GitHub counts 7"),
        ("node.assetTotal", 12, "v3.5.1 assets: got 11, GitHub counts 12"),
        ("node.tagName", "", "a release without a tag"),
        ("nodes", "x", "not a GraphQL connection"),
        ("nodes", [], "a next page after an empty page"),
    ],
)
def test_an_incomplete_answer_fails_the_fetch(
    tmp_path: Path, key: str, value: object, message: str
) -> None:
    http = write_http(tmp_path / "http", _exchanges_with(key, value))
    with pytest.raises(FetchError, match=message):
        fetch(tmp_path, http)


@pytest.mark.parametrize(
    ("page", "message"),
    [
        ("repeat", "brought no new release"),  # the first page again, with a fresh cursor
        ("stuck", "brought no new release"),  # new releases, but the cursor it was asked with
    ],
)
def test_a_releases_page_that_does_not_advance_fails_the_fetch(
    tmp_path: Path, page: str, message: str
) -> None:
    # Unchecked, a repeated page never grows the release list, so paging would go on
    # until the run's shared GitHub budget was spent.
    exchanges = simulate(fixture_releases(), SETTINGS)
    first = exchanges[0][1]["data"]["repository"]["releases"]
    second = exchanges[1][1]["data"]["repository"]["releases"]
    if page == "repeat":
        second["nodes"] = first["nodes"]
        second["pageInfo"] = {"hasNextPage": True, "endCursor": "releases:8"}
    else:
        second["pageInfo"] = {"hasNextPage": True, "endCursor": first["pageInfo"]["endCursor"]}
    with pytest.raises(FetchError, match=message):
        fetch(tmp_path, write_http(tmp_path / "http", exchanges[:2]))
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_drafts_are_skipped(tmp_path: Path) -> None:
    releases = fixture_releases()
    releases[0]["isDraft"] = True
    snap, _ = fetch(tmp_path, write_http(tmp_path / "http", simulate(releases, SETTINGS)))
    assert snap is not None
    assert "v3.5.1" not in {r["release"] for r in snap.iter_jsonl(ASSETS)}


@pytest.mark.parametrize(
    ("asset", "message"),
    [
        (
            {"name": "Birch.zip", "downloadCount": None, "createdAt": "2026-08-21T08:00:38Z"},
            "downloadCount",
        ),
        ({"name": "Birch.zip", "downloadCount": 1, "createdAt": "yesterday"}, "createdAt"),
        (
            {"name": "Hack.zip", "downloadCount": 1, "createdAt": "2026-08-21T08:00:38Z"},
            "listed twice",
        ),
        ({"name": None, "downloadCount": 1, "createdAt": "2026-08-21T08:00:38Z"}, "without a name"),
        ("Birch.zip", "without a name"),
    ],
)
def test_malformed_assets_fail_the_fetch(
    tmp_path: Path, asset: dict[str, Any], message: str
) -> None:
    releases = fixture_releases()
    releases[0]["assets"].append(asset)
    http = write_http(tmp_path / "http", simulate(releases, SETTINGS))
    with pytest.raises(ValueError, match=message):
        fetch(tmp_path, http)


def test_a_shrunken_answer_fails_the_fetch(tmp_path: Path) -> None:
    releases = [r for r in fixture_releases() if r["tagName"] != "v3.5.1"]
    http = write_http(tmp_path / "http", simulate(releases, SETTINGS))
    with pytest.raises(ValueError, match="below min_share"):
        fetch(tmp_path, http, day=LATER, previous=fixture_snapshot())
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, LATER) is None


def test_the_previous_snapshot_is_counted_under_todays_settings(tmp_path: Path) -> None:
    # Excluding JetBrainsMono drops 6 of the 24 font assets (75%, under min_share). Measured
    # against the previous snapshot as stored, every later fetch would fail as well, because a
    # failed fetch leaves that same snapshot as `previous`.
    narrower = dataclasses.replace(SETTINGS, exclude=(*SETTINGS.exclude, "JetBrainsMono"))
    previous = fixture_snapshot()
    assert previous_rows(previous, SETTINGS) == 24
    assert previous_rows(previous, narrower) == 18
    snap, _ = fetch(tmp_path, HTTP, day=LATER, previous=previous, settings=narrower)
    assert snap is not None
    assert snap.manifest.extract(ASSETS).rows == 18
    assert previous_rows(None, SETTINGS) is None


def test_check_shrink() -> None:
    check_shrink(9, 10, 0.9)
    check_shrink(1, None, 0.9)
    with pytest.raises(ValueError, match="no font assets"):
        check_shrink(0, None, 0.9)
    with pytest.raises(ValueError, match="below min_share"):
        check_shrink(8, 10, 0.9)


# --- the real source ------------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One real fetch (needs a real GITHUB_TOKEN in the environment) and a parse of it."""
    monkeypatch.undo()  # the real token, not the fixture's
    if not os.environ.get("GITHUB_TOKEN"):
        pytest.skip("needs GITHUB_TOKEN (GitHub GraphQL)")
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
    assert snap.manifest.extract(ASSETS).rows >= 2000
    recs = by_release(parse(snap))
    assert ("v3.4.0", "JetBrainsMono") in recs
    assert recs[("v3.4.0", "JetBrainsMono")].value > 1_000_000
    assert len({tag for tag, _ in recs}) >= 26
    keys = {k for _, k in recs}
    assert not keys & {"FontPatcher", "NerdFontsSymbolsOnly"}


# --- rebuilding the fixture -----------------------------------------------------------------------


def rebuild() -> None:
    """Write ``http/`` from ``releases.json``, fetch it into ``snapshot/``, regen the golden file."""
    write_http(HTTP, simulate(fixture_releases(), SETTINGS))
    os.environ["GITHUB_TOKEN"] = TOKEN
    with tempfile.TemporaryDirectory() as tmp:
        snap, _ = fetch(Path(tmp), HTTP)
        assert snap is not None
        target = FIXTURE / "snapshot"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(snap.path, target)
    regen.regen(COLLECTOR.name)


if __name__ == "__main__":
    rebuild()
