"""The github_releases collector: release downloads of main-channel repos (ruling M2).

The generic checks (fetch, parse, records, golden file) are in
``tests/collectors/test_contract.py``; these pin this collector's own rules:
asset keys without versions, REST paging, the GraphQL path for Iosevka (sized
under GitHub's resource limits), the budget, and parse. Most run against
``fakegithub.FakeGitHub``; one real fetch is marked ``network``.
"""

import dataclasses
import gzip
import json
import logging
import os
import shutil
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from tests.collectors.github_releases import build_fixture
from tests.collectors.github_releases.fakegithub import FakeGitHub, release
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock, jsonio
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.github_releases import (
    ASSETS,
    COLLECTOR,
    REPOS,
    AssetRow,
    Before,
    Repo,
    Settings,
    asset_base,
    assets_query,
    check_shrink,
    estimate_requests,
    previous_assets,
    previous_repos,
    releases_per_query,
    releases_url,
    split_extension,
    tag_version,
)
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Budget, BudgetExceeded, Fetcher, FetchError
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, SourceKey, from_json
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
DAY = date(2026, 9, 26)
LOG = logging.getLogger("tests.github_releases")
MADE = "2025-01-02T03:04:05Z"


def settings(*repos: Repo, **changes: Any) -> Settings:
    return Settings(repos=repos, **changes)


def run_fetch(
    tmp: Path,
    fake: FakeGitHub,
    s: Settings,
    *,
    previous: Snapshot | None = None,
    budget: int = 5000,
    day: date = DAY,
) -> Snapshot:
    """Run ``fetch()`` against ``fake`` into a fresh store; return the snapshot."""
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(
            transport=fake.transport,
            min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0),
            budgets=(Budget("github", budget),),
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
                raw=RawDir(tmp / "raw", keep=True),
                previous=previous,
                settings=s,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return snap


def rows(snap: Snapshot) -> list[dict[str, Any]]:
    return list(snap.iter_jsonl(ASSETS))


def repos_json(snap: Snapshot) -> dict[str, dict[str, Any]]:
    return {r["repo"]: r for r in snap.load_json(REPOS)}


def parse(snap: Snapshot, s: Settings = SETTINGS) -> list[Observation]:
    recs = regen.parse_records(COLLECTOR, snap, s)
    assert all(isinstance(r, Observation) for r in recs)
    return recs  # type: ignore[return-value]


def attrs(o: Observation) -> dict[str, Any]:
    return dict(o.attrs)


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")


# --- keys ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "tag", "base"),
    [
        ("JetBrainsMono-2.304.zip", "v2.304", "JetBrainsMono.zip"),
        ("Fira_Code_v6.2.zip", "6.2", "Fira_Code.zip"),
        ("Fira_Code_v6.zip", "6", "Fira_Code.zip"),
        ("Hack-v3.003-ttf.zip", "v3.003", "Hack-ttf.zip"),
        ("Hack-v3.003-webfonts.tar.xz", "v3.003", "Hack-webfonts.tar.xz"),
        ("CascadiaCode-2407.24.zip", "v2407.24", "CascadiaCode.zip"),
        ("Inter-4.1.zip", "v4.1", "Inter.zip"),
        ("Inter-4.0-beta9h.zip", "v4.0-beta9h", "Inter-beta9h.zip"),
        ("Inter-3f174fcef6.zip", "v3.0", "Inter.zip"),  # a commit hash
        ("3270_fonts_d916271.zip", "v3.0.1", "3270_fonts.zip"),
        ("PkgTTC-Iosevka-34.8.1.zip", "v34.8.1", "PkgTTC-Iosevka.zip"),
        ("SGr-IosevkaSS01-34.8.1.zip", "v34.8.1", "SGr-IosevkaSS01.zip"),
        ("monaspace-nerdfonts-v1.400.zip", "v1.400", "monaspace-nerdfonts.zip"),
        ("MapleMono-NF-CN-unhinted.zip", "v7.9", "MapleMono-NF-CN-unhinted.zip"),
        ("0xProto_2_300.zip", "2.300", "0xProto.zip"),
        ("ibm-plex-mono.zip", "@ibm/plex-mono@2.5.0", "ibm-plex-mono.zip"),
        ("v2.0-Font.zip", "v2.0", "Font.zip"),
        ("Font4Mono-v4.zip", "v4", "Font4Mono.zip"),  # the 4 inside the name stays
        ("2.304.zip", "v2.304", "2.304.zip"),  # nothing left: the name as it is
        ("CozetteVector.woff2", "v.1.30.0", "CozetteVector.woff2"),
        ("LICENSE", "v1.0", "LICENSE"),
    ],
)
def test_asset_base_drops_versions(name: str, tag: str, base: str) -> None:
    assert asset_base(name, tag) == base


def test_asset_base_applies_per_repo_strip_first() -> None:
    adobe = SETTINGS.repos[[r.repo for r in SETTINGS.repos].index("adobe-fonts/source-code-pro")]
    for name, base in [
        ("OTF-source-code-pro-2.042R-u_1.062R-i.zip", "OTF-source-code-pro.zip"),
        ("WOFF2-source-code-pro-2.042R-u_1.062R-i_1.026Rvf.zip", "WOFF2-source-code-pro.zip"),
        ("TTF-source-code-pro-2.032R-ro-1.052R-it.zip", "TTF-source-code-pro.zip"),
        ("source-code-pro-1.009R.zip", "source-code-pro.zip"),
        ("SourceCodePro-Medium_v1020.zip", "SourceCodePro-Medium.zip"),
    ]:
        assert asset_base(name, "2.042R-u/1.062R-i/1.026R-vf", adobe.strip) == base
    cozette = SETTINGS.repos[[r.repo for r in SETTINGS.repos].index("the-moonwitch/Cozette")]
    assert asset_base("CozetteFonts-v-1-30-0.zip", "v.1.30.0", cozette.strip) == "CozetteFonts.zip"


def test_tag_version_and_extensions() -> None:
    assert tag_version("v2.304") == "2.304"
    assert tag_version("@ibm/plex-sans@1.1.0") == "1.1.0"
    assert tag_version("release-2.042R-u/1.062R-i") == "2.042"
    assert tag_version("latest") is None
    assert split_extension("Hack-ttf.tar.xz") == ("Hack-ttf", ".tar.xz")
    assert split_extension("Font.WOFF2") == ("Font", ".WOFF2")
    assert split_extension("LICENSE") == ("LICENSE", "")
    assert split_extension(".zip") == (".zip", "")


def test_skip_assets_default() -> None:
    import re

    skip = re.compile(SETTINGS.skip_assets)
    for name in ["SHA256SUMS", "Font.zip.sha256", "OFL.txt", "LICENSE", "sources.zip", "a.sfd"]:
        assert skip.search(name), name
    for name in ["Font.zip", "cozette.otb", "source-code-pro.zip", "fonts-sudo_all.deb"]:
        assert not skip.search(name), name


# --- settings -----------------------------------------------------------------------------------


def test_settings_file_lists_main_channel_repos() -> None:
    names = [r.repo for r in SETTINGS.repos]
    for repo in ["JetBrains/JetBrainsMono", "tonsky/FiraCode", "rsms/inter", "be5invis/Iosevka"]:
        assert repo in names
    assert "ryanoasis/nerd-fonts" not in names  # nerd_releases counts it
    iosevka = SETTINGS.repos[names.index("be5invis/Iosevka")]
    assert (iosevka.api, iosevka.latest) == ("graphql", 24)  # ruling M2
    assert all(r.latest == 0 for r in SETTINGS.repos if r.repo != "be5invis/Iosevka")
    assert names == sorted(names, key=str.casefold)
    assert all(r.family for r in SETTINGS.repos)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"repos": [{"repo": "no-slash"}]}, "expected 'owner/name'"),
        ({"repos": [{"repo": "a/b"}, {"repo": "A/B"}]}, "listed twice"),
        ({"repos": [{"repo": "a/b", "latest": -1}]}, "latest"),
        ({"repos": [{"repo": "a/b", "api": "soap"}]}, "is not one of"),
        ({"repos": [{"repo": "a/b", "strip": ["("]}]}, "not a regular expression"),
        ({"repos": [{"repo": "a/b", "assets": "["}]}, "not a regular expression"),
        ({"repos": [{"repo": "a/b", "stars": 3}]}, "unknown key"),
        ({"per_page": 101}, "per_page"),
        ({"graphql_nodes": 0}, "graphql_nodes"),
        ({"min_share": 1.5}, "min_share"),
        ({"skip_assets": "("}, "skip_assets"),
        ({"max_requests": 1, "repos": [{"repo": "a/b"}, {"repo": "c/d"}]}, "cannot cover"),
    ],
)
def test_settings_are_checked(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(Settings, data, where="sources/github_releases.toml")


# --- fetch: REST --------------------------------------------------------------------------------


def five_releases(repo: str = "o/r") -> list[dict[str, Any]]:
    return [
        release(f"v1.{n}", [(f"Font-1.{n}.zip", 10 * n, MADE)], repo=repo) for n in range(5, 0, -1)
    ]


def test_rest_pages_follow_the_link_header(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/r": five_releases()})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r"), per_page=2))
    assert [u for _, u in fake.requests] == [
        f"{releases_url('o/r')}?per_page=2&page={p}" for p in (1, 2, 3)
    ]
    assert [r["tag"] for r in rows(snap)] == [f"v1.{n}" for n in range(1, 6)]
    listed = [f.url for f in snap.manifest.fetched]
    assert listed == [u for _, u in fake.requests]
    assert all(f.sha256 and not f.kept for f in snap.manifest.fetched)
    pages = sorted((tmp_path / "raw" / "o__r").iterdir())  # streamed to ctx.raw, not kept
    assert [p.name for p in pages] == [f"releases-000{p}.json" for p in (1, 2, 3)]
    assert snap.manifest.data_date == DAY
    assert repos_json(snap)["o/r"] == {
        "repo": "o/r",
        "api": "rest",
        "latest": 0,
        "status": "ok",
        "releases": 5,
        "assets": 5,
        "total_releases": None,
        "renamed_to": None,
    }


def test_rest_without_link_header_pages_while_full(tmp_path: Path) -> None:
    class NoLink(FakeGitHub):
        def handle(self, request):  # type: ignore[no-untyped-def]
            response = super().handle(request)
            response.headers.pop("link", None)
            return response

    fake = NoLink({"o/r": five_releases()[:4]})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r"), per_page=2))
    assert len(fake.requests) == 3  # the third page comes back empty
    assert len(rows(snap)) == 4


def test_rest_latest_keeps_the_newest(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/r": five_releases()})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r", latest=3), per_page=2))
    assert len(fake.requests) == 2
    assert sorted(r["tag"] for r in rows(snap)) == ["v1.3", "v1.4", "v1.5"]


def test_rest_max_pages_is_noted(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/r": five_releases()})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r"), per_page=2, max_pages=2))
    assert len(rows(snap)) == 4
    assert any("max_pages" in n for n in snap.manifest.notes)


def test_drafts_states_and_skipped_assets(tmp_path: Path) -> None:
    rel = [
        release("v3", [("Font-3.zip", 1, MADE)], draft=True),
        release(
            "v2",
            [("Font-2.zip", 5, MADE), ("Font-2.zip.sha256", 9, MADE), ("Half.zip", 7, MADE)],
            states={"Half.zip": "starter"},
            prerelease=True,
        ),
        release("v1", [("Font-1.zip", 3, MADE), ("Web-1.zip", 4, MADE)], published=None),
    ]
    fake = FakeGitHub({"o/r": rel})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r", assets=r"^Font")))
    got = {(r["tag"], r["asset"]): r for r in rows(snap)}
    assert set(got) == {("v1", "Font-1.zip"), ("v2", "Font-2.zip")}
    assert got[("v2", "Font-2.zip")]["prerelease"] is True
    assert got[("v1", "Font-1.zip")]["published_at"] is None
    assert repos_json(snap)["o/r"]["releases"] == 2  # the draft is not counted


def test_a_release_repeated_across_pages_is_kept_once(tmp_path: Path) -> None:
    rel = five_releases()
    fake = FakeGitHub({"o/r": [rel[0], rel[1], rel[1], rel[2]]})  # v1.4 published mid-fetch
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r"), per_page=2))
    assert [r["tag"] for r in rows(snap)] == ["v1.3", "v1.4", "v1.5"]
    assert repos_json(snap)["o/r"]["releases"] == 3


def test_missing_and_renamed_repos(tmp_path: Path) -> None:
    fake = FakeGitHub(
        {"o/old": [release("v1", [("A.zip", 1, MADE)], repo="o/new")]},
        missing=frozenset({"o/gone"}),
    )
    snap = run_fetch(tmp_path, fake, settings(Repo("o/gone"), Repo("o/old")))
    summary = repos_json(snap)
    assert summary["o/gone"]["status"] == "missing"
    assert summary["o/old"]["renamed_to"] == "o/new"
    assert any("o/gone: not found" in n for n in snap.manifest.notes)
    assert any("now o/new" in n for n in snap.manifest.notes)
    assert [r["repo"] for r in rows(snap)] == ["o/old"]  # keys keep the listed name


def test_a_later_page_404_fails_the_fetch(tmp_path: Path) -> None:
    class Vanishing(FakeGitHub):
        def _rest(self, repo, query):  # type: ignore[no-untyped-def]
            if query.get("page") == "2":
                self.missing = frozenset({repo})
            return super()._rest(repo, query)

    with pytest.raises(FetchError, match="page 2"):
        run_fetch(tmp_path, Vanishing({"o/r": five_releases()}), settings(Repo("o/r"), per_page=2))


# --- fetch: GraphQL -----------------------------------------------------------------------------


def iosevka(n_releases: int = 4, n_assets: int = 5) -> list[dict[str, Any]]:
    return [
        release(
            f"v34.{r}.0",
            [(f"Pkg-{a}-34.{r}.0.zip", 100 * r + a, MADE) for a in range(n_assets)],
            repo="be5invis/Iosevka",
        )
        for r in range(n_releases, 0, -1)
    ]


GQL = Repo("be5invis/Iosevka", api="graphql", latest=3)


@pytest.mark.usefixtures("token")
def test_graphql_latest_releases_with_asset_pages(tmp_path: Path) -> None:
    fake = FakeGitHub({"be5invis/Iosevka": iosevka()})
    s = settings(GQL, asset_page=2, graphql_nodes=4)
    assert releases_per_query(s) == 2
    snap = run_fetch(tmp_path, fake, s)
    got = rows(snap)
    assert sorted({r["tag"] for r in got}) == ["v34.2.0", "v34.3.0", "v34.4.0"]
    assert len(got) == 15  # every asset of the 3 newest releases
    # 2 release queries (2 + 1), then asset pages 2 and 3 of each release, 2 releases a query
    assert len(fake.requests) == 2 + 4
    assert all(u == "https://api.github.com/graphql" for _, u in fake.requests)
    assert len(snap.manifest.fetched) == 6
    summary = repos_json(snap)["be5invis/Iosevka"]
    assert (summary["total_releases"], summary["releases"], summary["assets"]) == (4, 3, 15)


@pytest.mark.usefixtures("token")
def test_graphql_every_release_when_latest_is_0(tmp_path: Path) -> None:
    fake = FakeGitHub({"be5invis/Iosevka": iosevka(5, 1)})
    s = settings(dataclasses.replace(GQL, latest=0), asset_page=1, graphql_nodes=2)
    snap = run_fetch(tmp_path, fake, s)
    assert len(rows(snap)) == 5
    assert len(fake.requests) == 3  # 2 + 2 + 1 releases


@pytest.mark.usefixtures("token")
def test_graphql_halves_a_query_github_finds_too_big(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeGitHub({"be5invis/Iosevka": iosevka()}, node_limit=2)
    snap = run_fetch(tmp_path, fake, settings(GQL, asset_page=2, graphql_nodes=4))
    assert len(rows(snap)) == 15
    assert "too big for GitHub" in caplog.text
    # the refused queries are in the manifest too
    assert len(snap.manifest.fetched) == len(fake.requests)


@pytest.mark.usefixtures("token")
def test_graphql_other_errors_fail_the_fetch(tmp_path: Path) -> None:
    fake = FakeGitHub({"be5invis/Iosevka": iosevka()}, node_limit=1)
    with pytest.raises(FetchError, match="Resource limits"):
        run_fetch(tmp_path, fake, settings(GQL, asset_page=2, graphql_nodes=4))


@pytest.mark.usefixtures("token")
def test_graphql_missing_repo(tmp_path: Path) -> None:
    fake = FakeGitHub({}, missing=frozenset({"be5invis/Iosevka"}))
    snap = run_fetch(tmp_path, fake, settings(GQL))
    assert repos_json(snap)["be5invis/Iosevka"]["status"] == "missing"
    assert rows(snap) == []


@pytest.mark.usefixtures("token")
def test_graphql_release_gone_mid_fetch_is_dropped_whole(tmp_path: Path) -> None:
    class Deleting(FakeGitHub):
        def _assets(self, repo, v, i):  # type: ignore[no-untyped-def]
            return None if v[f"t{i}"] == "v34.3.0" else super()._assets(repo, v, i)

    fake = Deleting({"be5invis/Iosevka": iosevka()})
    snap = run_fetch(tmp_path, fake, settings(GQL, asset_page=2, graphql_nodes=4))
    assert sorted({r["tag"] for r in rows(snap)}) == ["v34.2.0", "v34.4.0"]
    assert any("v34.3.0: gone mid-fetch" in n for n in snap.manifest.notes)


@pytest.mark.usefixtures("token")
def test_graphql_too_many_asset_pages_fail_the_fetch(tmp_path: Path) -> None:
    fake = FakeGitHub({"be5invis/Iosevka": iosevka(1, 5)})
    s = settings(GQL, asset_page=1, graphql_nodes=1, max_asset_pages=3)
    with pytest.raises(FetchError, match="more than 3 asset pages"):
        run_fetch(tmp_path, fake, s)


@pytest.mark.usefixtures("token")
def test_graphql_cap_without_latest_is_noted(tmp_path: Path) -> None:
    fake = FakeGitHub({"be5invis/Iosevka": iosevka(5, 1)})
    s = settings(dataclasses.replace(GQL, latest=0), per_page=2, max_pages=1, asset_page=1)
    snap = run_fetch(tmp_path, fake, s)
    assert sorted({r["tag"] for r in rows(snap)}) == ["v34.4.0", "v34.5.0"]
    assert any("stopped at 2 releases" in n for n in snap.manifest.notes)


@pytest.mark.usefixtures("token")
def test_graphql_renamed_repo_keeps_the_listed_name(tmp_path: Path) -> None:
    class Renamed(FakeGitHub):
        def _releases(self, repo, v):  # type: ignore[no-untyped-def]
            return super()._releases(repo, v) | {"nameWithOwner": "be5invis/Iosevka2"}

    snap = run_fetch(tmp_path, Renamed({"be5invis/Iosevka": iosevka(1, 1)}), settings(GQL))
    assert repos_json(snap)["be5invis/Iosevka"]["renamed_to"] == "be5invis/Iosevka2"
    assert any("now be5invis/Iosevka2" in n for n in snap.manifest.notes)
    assert {r["repo"] for r in rows(snap)} == {"be5invis/Iosevka"}


def test_assets_query_aliases_each_release() -> None:
    q = assets_query(2)
    assert "$t0: String!, $c0: String!, $t1: String!, $c1: String!" in q
    assert "r1: release(tagName: $t1) { releaseAssets(first: $n, after: $c1)" in q


# --- fetch: budget and safety -------------------------------------------------------------------


def test_graphql_needs_a_token_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    fake = FakeGitHub({"o/r": five_releases()})
    with pytest.raises(FetchError, match="GITHUB_TOKEN"):
        run_fetch(tmp_path, fake, settings(Repo("o/r"), GQL))
    assert fake.requests == []


def test_too_small_a_budget_fails_before_any_request(tmp_path: Path) -> None:
    fake = FakeGitHub({})
    with pytest.raises(BudgetExceeded, match="fewer than the 2 this fetch needs"):
        run_fetch(tmp_path, fake, settings(Repo("o/a"), Repo("o/b")), budget=1)
    assert fake.requests == []


def test_max_requests_caps_this_collector(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/r": five_releases()})
    with pytest.raises(BudgetExceeded, match="max_requests"):
        run_fetch(tmp_path, fake, settings(Repo("o/r"), per_page=2, max_requests=2))
    assert len(fake.requests) == 2


def previous_snapshot(
    tmp: Path, assets: dict[str, list[str]], summary: list[dict[str, Any]] | None = None
) -> Snapshot:
    """A previous snapshot holding ``assets`` (one release each) and a matching repos.json."""
    rows = [
        AssetRow(repo, f"v{i}", name, MADE, False, MADE, 1).to_json()
        for repo, names in assets.items()
        for i, name in enumerate(names)
    ]
    if summary is None:
        summary = [
            {"repo": r, "api": "rest", "latest": 0, "status": "ok", "releases": len(n)}
            | {"assets": len(n), "total_releases": None, "renamed_to": None}
            for r, n in assets.items()
        ]
    store = Store(tmp / "previous")
    with store.writer(COLLECTOR.name, date(2026, 8, 26), COLLECTOR.version) as w:
        w.write_jsonl(ASSETS, rows)
        w.write_json(REPOS, summary)
    snap = store.snapshot(COLLECTOR.name, date(2026, 8, 26))
    assert snap is not None
    return snap


def fonts(n: int, prefix: str = "Font") -> list[str]:
    return [f"{prefix}-1.{i}.zip" for i in range(n)]


def test_a_shrunk_repo_is_noted(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/a": five_releases("o/a"), "o/b": five_releases("o/b")[:1]})
    before = previous_snapshot(tmp_path, {"o/a": fonts(5), "o/b": fonts(5)})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/a"), Repo("o/b")), previous=before)
    assert "o/b: 1 assets, down from 5" in snap.manifest.notes


def test_a_shrunk_total_fails_the_fetch(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/a": five_releases("o/a")[:1]})
    before = previous_snapshot(tmp_path, {"o/a": fonts(5), "o/b": fonts(5)})
    with pytest.raises(ValueError, match="a broken answer"):
        run_fetch(tmp_path, fake, settings(Repo("o/a")), previous=before)


def test_a_tightened_filter_is_not_a_broken_answer(tmp_path: Path) -> None:
    """The previous snapshot is counted under today's filters, so the check does not lock in."""
    fake = FakeGitHub({"o/a": five_releases("o/a")})
    before = previous_snapshot(tmp_path, {"o/a": fonts(5) + fonts(20, "Web")})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/a", assets="^Font")), previous=before)
    assert len(rows(snap)) == 5
    assert snap.manifest.notes == ()
    assert previous_assets(before, settings(Repo("o/a", assets="^Font"))) == {"o/a": 5}
    assert previous_assets(before, settings(skip_assets="^Web")) == {"o/a": 5}
    assert previous_assets(before, settings()) == {"o/a": 25}


def test_an_unreadable_previous_snapshot_is_noted_not_fatal(tmp_path: Path) -> None:
    store = Store(tmp_path / "previous")
    with store.writer(COLLECTOR.name, date(2026, 8, 26), COLLECTOR.version) as w:
        w.write_jsonl(ASSETS, [{"repo": "o/r", "downloads": -1}])
        w.write_json(REPOS, [])
    before = store.snapshot(COLLECTOR.name, date(2026, 8, 26))
    fake = FakeGitHub({"o/r": five_releases()})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r")), previous=before)
    assert len(rows(snap)) == 5
    assert any(n.startswith("previous snapshot unreadable") for n in snap.manifest.notes)


def test_estimate_requests_is_a_lower_bound_from_the_previous_snapshot() -> None:
    s = settings(
        Repo("o/new"),
        Repo("o/rest"),
        Repo("o/huge"),
        Repo("o/capped", latest=24),
        Repo("o/changed", latest=5),
        Repo("o/gql", api="graphql", latest=24),
        Repo("o/gql-all", api="graphql"),
        per_page=100,
        max_pages=20,
        asset_page=100,
        graphql_nodes=500,
    )
    before = {
        "o/rest": Before("rest", 0, 250, 1000),  # 3 pages
        "o/huge": Before("rest", 0, 5000, 5000),  # max_pages
        "o/capped": Before("rest", 24, 24, 50),  # one page of 24
        "o/changed": Before("rest", 0, 900, 900),  # latest changed since: 1
        # 24 releases, 5 a query: 5; ceil(10848 / 100) - 24 = 85 more asset pages, 5 a query: 17
        "o/gql": Before("graphql", 24, 24, 10848),
        # more releases than max_pages x per_page (2,000) fetches: 2,000 at 5 a query, and
        # no claim about asset pages, since the assets were counted over all 2,500
        "o/gql-all": Before("graphql", 0, 2500, 250_000),
    }
    assert estimate_requests(s, before) == 1 + 3 + 20 + 1 + 1 + (5 + 17) + 400
    assert estimate_requests(s, {}) == len(s.repos)


@pytest.mark.usefixtures("token")
def test_estimate_is_at_most_what_the_same_fetch_makes(tmp_path: Path) -> None:
    fake = FakeGitHub({"be5invis/Iosevka": iosevka(), "o/r": five_releases()})
    s = settings(GQL, Repo("o/r"), per_page=2, asset_page=2, graphql_nodes=4)
    first = run_fetch(tmp_path / "a", fake, s)
    made = len(fake.requests)
    assert 0 < estimate_requests(s, previous_repos(first)) <= made
    fake.requests.clear()
    run_fetch(tmp_path / "b", fake, s, previous=first, budget=made)  # exactly enough
    assert len(fake.requests) == made


def test_budget_and_max_requests_are_checked_against_the_estimate(tmp_path: Path) -> None:
    before = previous_snapshot(tmp_path, {"o/r": fonts(500)})  # 5 pages of 100 releases
    fake = FakeGitHub({"o/r": five_releases()})
    with pytest.raises(BudgetExceeded, match="fewer than the 5 this fetch needs"):
        run_fetch(tmp_path / "a", fake, settings(Repo("o/r")), previous=before, budget=4)
    with pytest.raises(BudgetExceeded, match=r"max_requests \(3\) is under the 5 requests"):
        run_fetch(tmp_path / "b", fake, settings(Repo("o/r"), max_requests=3), previous=before)
    assert fake.requests == []


def test_check_shrink_ignores_new_and_missing_repos() -> None:
    from tff_catalog.collectors.ranking.github_releases import RepoResult

    results = [RepoResult(Repo("o/new")), RepoResult(Repo("o/gone"), status="missing")]
    assert check_shrink(results, {"o/gone": 10}, 0.5) == []


# --- parse --------------------------------------------------------------------------------------


def test_parse_makes_one_observation_per_asset(tmp_path: Path) -> None:
    rel = [
        release(
            "v2.304",
            [("JetBrainsMono-2.304.zip", 587668, "2023-01-14T15:20:03Z")],
            repo="JetBrains/JetBrainsMono",
            published="2023-01-14T15:22:41Z",
        ),
        release(
            "v2.0-beta",
            [("JetBrainsMono-2.0-beta.zip", 12, "2020-01-01T23:59:59+02:00")],
            repo="JetBrains/JetBrainsMono",
            published=None,
            prerelease=True,
        ),
    ]
    fake = FakeGitHub({"JetBrains/JetBrainsMono": rel})
    s = settings(Repo("JetBrains/JetBrainsMono"))
    obs = {attrs(o)["release"]: o for o in parse(run_fetch(tmp_path, fake, s), s)}
    o = obs["v2.304"]
    assert o.key == SourceKey("gh-asset", "JetBrains/JetBrainsMono/JetBrainsMono.zip")
    assert (o.series, o.unit, o.value) == ("lifetime", "downloads", 587668.0)
    assert (o.start, o.end) == (date(2023, 1, 14), DAY)
    assert attrs(o) == {
        "asset": "JetBrainsMono-2.304.zip",
        "first_seen": "2023-01-14",
        "prerelease": False,
        "published_at": "2023-01-14",
        "release": "v2.304",
        "repo": "JetBrains/JetBrainsMono",
    }
    beta = obs["v2.0-beta"]
    assert attrs(beta)["first_seen"] == "2020-01-01"  # 21:59:59 UTC
    assert attrs(beta)["prerelease"] is True
    assert "published_at" not in attrs(beta)
    assert beta.key.key == "JetBrains/JetBrainsMono/JetBrainsMono-beta.zip"


def test_parse_applies_todays_filters_to_old_snapshots(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/r": [release("v1", [("A-1.zip", 1, MADE), ("B-1.zip", 2, MADE)])]})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r")))
    assert len(parse(snap, settings(Repo("o/r")))) == 2
    assert [attrs(o)["asset"] for o in parse(snap, settings(Repo("o/r", assets="^A")))] == [
        "A-1.zip"
    ]
    skipped = parse(snap, settings(Repo("o/r"), skip_assets="^B"))
    assert [attrs(o)["asset"] for o in skipped] == ["A-1.zip"]
    assert len(parse(snap, settings())) == 2  # a repo no longer listed keeps the default rules


def test_parse_uses_the_data_date(tmp_path: Path) -> None:
    fake = FakeGitHub({"o/r": [release("v1", [("A-1.zip", 1, MADE)])]})
    snap = run_fetch(tmp_path, fake, settings(Repo("o/r")), day=date(2026, 10, 3))
    snap = dataclasses.replace(
        snap, manifest=dataclasses.replace(snap.manifest, data_date=date(2026, 10, 4))
    )
    assert parse(snap)[0].end == date(2026, 10, 4)
    later_made = FakeGitHub({"o/r": [release("v1", [("A-1.zip", 1, "2026-12-01T00:00:00Z")])]})
    o = parse(run_fetch(tmp_path / "b", later_made, settings(Repo("o/r"))))[0]
    assert o.start == o.end == DAY  # never a window that ends before it starts


def test_two_snapshots_give_the_growth_of_assets_in_both(tmp_path: Path) -> None:
    """Keys and attrs are what the engine source ``github`` needs (ruling M2, methodology §5)."""
    from tff_catalog import corrections
    from tff_catalog.config import load_config

    src = load_config(Paths.for_root(ROOT)).ranking.sources.all()["github"]
    assert src.collector == COLLECTOR.name
    old = [
        release("v2", [("Font-2.zip", 100, MADE)]),
        release("v1", [("Font-1.zip", 1000, MADE), ("Font-1.zip.sha256", 9, MADE)]),
        release("v1-rc", [("Font-1-rc.zip", 50, MADE)], prerelease=True),
    ]
    new = [
        release("v3", [("Font-3.zip", 5, "2026-10-20T00:00:00Z")]),  # not in the baseline
        release("v2", [("Font-2.zip", 130, MADE)]),
        release("v1", [("Font-1.zip", 1006, MADE), ("Font-1.zip.sha256", 99, MADE)]),
        release("v1-rc", [("Font-1-rc.zip", 80, MADE)], prerelease=True),
    ]
    s = settings(Repo("o/r"))
    base = parse(run_fetch(tmp_path / "a", FakeGitHub({"o/r": old}), s, day=date(2026, 9, 26)), s)
    now = parse(run_fetch(tmp_path / "b", FakeGitHub({"o/r": new}), s, day=date(2026, 10, 26)), s)
    got = corrections.count_keys(src, corrections.select(src, now), corrections.select(src, base))
    key = SourceKey("gh-asset", "o/r/Font.zip")
    assert set(got) == {key}  # the prerelease's key and the checksum never count
    # (130 - 100) + (1006 - 1000) downloads in 30 days, per year
    assert got[key].value == pytest.approx(36 * 365 / 30, rel=0.01)


def snapshot_with(tmp: Path, lines: list[bytes]) -> Snapshot:
    store = Store(tmp / "bad")
    with store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as w:
        w.write_bytes(ASSETS, b"".join(line + b"\n" for line in lines))
        w.write_json(REPOS, [])
    snap = store.snapshot(COLLECTOR.name, DAY)
    assert snap is not None
    return snap


GOOD = {
    "repo": "o/r",
    "tag": "v1",
    "published_at": MADE,
    "prerelease": False,
    "asset": "A.zip",
    "created_at": MADE,
    "downloads": 3,
}


@pytest.mark.parametrize(
    "change",
    [
        {"downloads": -1},
        {"downloads": True},
        {"downloads": 1.5},
        {"created_at": "2025-01-02T03:04:05"},  # no zone
        {"created_at": None},
        {"prerelease": "no"},
        {"asset": ""},
        {"extra": 1},
    ],
)
def test_parse_refuses_malformed_rows(tmp_path: Path, change: dict[str, Any]) -> None:
    row = {**GOOD, **change}
    snap = snapshot_with(tmp_path, [jsonio.canonical_bytes(row)])
    with pytest.raises(ValueError, match=r"assets\.jsonl\.gz|created_at"):
        parse(snap)


def test_parse_refuses_a_repeated_asset(tmp_path: Path) -> None:
    line = jsonio.canonical_bytes(GOOD)
    with pytest.raises(ValueError, match="listed twice"):
        parse(snapshot_with(tmp_path, [line, line]))


def test_asset_row_round_trip() -> None:
    row = AssetRow.from_json(GOOD)
    assert AssetRow.from_json(row.to_json()) == row


# --- the contract fixture -----------------------------------------------------------------------


def test_fixture_answers_every_listed_repo() -> None:
    """Adding a repo to the settings needs the fixture rebuilt (build_fixture)."""
    index = json.loads((FIXTURE / "http" / mockhttp.INDEX).read_text(encoding="utf-8"))
    urls = {e["url"] for e in index["responses"]}
    for repo in SETTINGS.repos:
        if repo.api == "rest":
            url = mockhttp.normalize_url(f"{releases_url(repo.repo)}?per_page=100&page=1")
            assert url in urls, (
                f"{repo.repo} has no recorded answer; run "
                "uv run python -m tests.collectors.github_releases.build_fixture"
            )


def test_fixture_is_what_build_fixture_writes(tmp_path: Path) -> None:
    target = tmp_path / COLLECTOR.name
    target.mkdir()
    shutil.copy(FIXTURE / "source.json", target / "source.json")
    token = os.environ.get("GITHUB_TOKEN")
    build_fixture.build(target)
    assert os.environ.get("GITHUB_TOKEN") == token
    for part in ("http", "snapshot"):
        want = sorted(p.relative_to(FIXTURE) for p in (FIXTURE / part).rglob("*"))
        got = sorted(p.relative_to(target) for p in (target / part).rglob("*"))
        assert got == want, f"{part}/ differs; run the build_fixture module"
        for rel in want:
            if (FIXTURE / rel).is_file():
                assert (target / rel).read_bytes() == (FIXTURE / rel).read_bytes(), rel
    assert (target / regen.EXPECTED).read_bytes() == (FIXTURE / regen.EXPECTED).read_bytes()


def test_fixture_rows_are_trimmed_real_data() -> None:
    source = json.loads((FIXTURE / "source.json").read_text(encoding="utf-8"))
    n = sum(len(r["assets"]) for rels in source["repos"].values() for r in rels)
    assert n <= 50  # ruling T1: small trimmed fixtures
    raw = (FIXTURE / "snapshot" / ASSETS).read_bytes()
    text = gzip.decompress(raw).decode()
    for field in ("author", "uploader", "body", "login"):
        assert f'"{field}"' not in text
    recs = [
        from_json(json.loads(line)) for line in (FIXTURE / regen.EXPECTED).read_text().splitlines()
    ]
    assert {r.key.key for r in recs} >= {
        "JetBrains/JetBrainsMono/JetBrainsMono.zip",
        "tonsky/FiraCode/Fira_Code.zip",
        "be5invis/Iosevka/PkgTTC-Iosevka.zip",
        "adobe-fonts/source-code-pro/OTF-source-code-pro.zip",
    }
    assert not any(r.key.key.endswith("/LICENSE") for r in recs)


# --- the real source ----------------------------------------------------------------------------


@pytest.mark.network
def test_real_fetch_and_parse(tmp_path: Path) -> None:
    """One real fetch of three repos (Iosevka through GraphQL only when GITHUB_TOKEN is set)."""
    repos = [Repo("JetBrains/JetBrainsMono"), Repo("tonsky/FiraCode")]
    if os.environ.get("GITHUB_TOKEN"):
        repos.append(Repo("be5invis/Iosevka", api="graphql", latest=2))
    s = settings(*repos)
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
                settings=s,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    obs = parse(snap, s)
    keys = {o.key.key for o in obs}
    assert "JetBrains/JetBrainsMono/JetBrainsMono.zip" in keys
    assert "tonsky/FiraCode/Fira_Code.zip" in keys
    jb = [o for o in obs if attrs(o)["release"] == "v2.304"]
    assert jb
    assert (jb[0].value or 0) > 500_000
    if len(repos) == 3:
        iosevka = [o for o in obs if attrs(o)["repo"] == "be5invis/Iosevka"]
        assert len({attrs(o)["release"] for o in iosevka}) == 2
        assert len(iosevka) > 200  # every asset of both releases, paged
