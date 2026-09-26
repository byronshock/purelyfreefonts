"""The github_repos alias miner: GitHub release assets of main-channel repos -> families.

The rows are ``assets.jsonl`` (trimmed real rows, see NOTICE) plus the
synthetic ones below, written as a ``github_releases`` snapshot into a
temporary store. One test marked ``store`` runs the miner on the real snapshot
in ``$TFF_STORE``; the committed seed file is checked against the table.
"""

import json
import logging
import pkgutil
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT

from tff_catalog.aliases import AliasCandidate, load_seeds, write_seeds
from tff_catalog.aliases import miners as miners_pkg
from tff_catalog.aliases.miners import MineContext, Miner
from tff_catalog.aliases.miners import github_repos as gr
from tff_catalog.collectors.base import load_settings
from tff_catalog.collectors.ranking.github_releases import NAMESPACE, Repo, Settings, asset_key
from tff_catalog.config_model import ConfigError
from tff_catalog.keys import match_key
from tff_catalog.paths import Paths
from tff_catalog.records import SourceKey
from tff_catalog.store import RawDir, Store

HERE = Path(__file__).parent
NAME = "github_repos"
DAY = date(2026, 9, 26)
LOG = logging.getLogger("tests.github_repos")
SEEDS = ROOT / "data" / "alias-seeds" / "github_repos.csv"
REAL_ROWS = [json.loads(line) for line in (HERE / "assets.jsonl").read_text().splitlines()]


def synthetic(repo: str, asset: str, *, prerelease: bool = False) -> dict[str, Any]:
    """A synthetic asset row (no release carries it)."""
    made = "2026-01-02T03:04:05Z"
    return {
        "repo": repo,
        "tag": "v1.0",
        "asset": asset,
        "published_at": made,
        "prerelease": prerelease,
        "created_at": made,
        "downloads": 10,
    }


SYNTHETIC_ROWS = [
    synthetic("IBM/plex", "ibm-plex-sans-new.zip"),  # a multi-family repo; no rule names it
    synthetic("googlefonts/Inconsolata", "fonts_ttf.zip"),  # not a main channel
    synthetic("example-org/example-font", "ExampleFont-1.0.zip"),  # not in the table; a hint
    synthetic("example-org/no-hint", "NoHint.zip"),  # not in the table; no hint
    synthetic("JetBrains/JetBrainsMono", "SHA256SUMS.txt"),  # not a font: the collector drops it
    synthetic("subframe7536/maple-font", "MapleMonoNL-TTF.zip"),  # the no-ligature build
    synthetic("subframe7536/maple-font", "MapleMonoNL-NF-CN.zip"),  # NF before NL and CN
    synthetic("ahatem/IoskeleyMono", "IoskeleyMono-NL.zip"),  # a repo the settings do not list
]
ROWS = REAL_ROWS + SYNTHETIC_ROWS

# The collector's settings for these rows: the repos and family hints it lists.
HINTS = {
    "JetBrains/JetBrainsMono": "JetBrains Mono",
    "IBM/plex": "IBM Plex",
    "subframe7536/maple-font": "Maple Mono",
    "microsoft/cascadia-code": "Cascadia Code",
    "be5invis/Iosevka": "Iosevka",
    "githubnext/monaspace": "Monaspace",
    "googlefonts/Inconsolata": "Inconsolata",
    "example-org/example-font": "Example Font",
    "example-org/no-hint": "",
}
SETTINGS = Settings(repos=tuple(Repo(r, family=f) for r, f in HINTS.items()))

PLEX_BUNDLE = [
    "IBM Plex Mono",
    "IBM Plex Sans",
    "IBM Plex Sans Arabic",
    "IBM Plex Sans Condensed",
    "IBM Plex Sans Devanagari",
    "IBM Plex Sans Hebrew",
    "IBM Plex Sans JP",
    "IBM Plex Sans KR",
    "IBM Plex Sans TC",
    "IBM Plex Sans Thai",
    "IBM Plex Sans Thai Looped",
    "IBM Plex Serif",
]
MONASPACE = [f"Monaspace {n}" for n in ("Argon", "Krypton", "Neon", "Radon", "Xenon")]

# (key, family, relation, detail): every candidate the rows above give, and nothing else.
EXPECTED = sorted(
    [
        ("IBM/plex/IBM-Plex-Mono.zip", "IBM Plex Mono", "package", ""),  # and ibm-plex-mono.zip
        ("IBM/plex/plex-sans-variable.zip", "IBM Plex Sans", "package", ""),
        *(("IBM/plex/OpenType.zip", f, "bundle", "") for f in PLEX_BUNDLE),
        ("JetBrains/JetBrainsMono/JetBrainsMono.zip", "JetBrains Mono", "package", ""),
        ("ahatem/IoskeleyMono/IoskeleyMono-NL.zip", "Ioskeley Mono", "build", "nl"),
        ("be5invis/Iosevka/PkgTTC-SGr-IosevkaTermSS04.zip", "Iosevka SS04", "package", ""),
        ("be5invis/Iosevka/PkgTTF-Unhinted-IosevkaFixed.zip", "Iosevka", "package", ""),
        ("be5invis/Iosevka/SuperTTC-Iosevka.zip", "Iosevka", "package", ""),
        ("be5invis/Iosevka/SuperTTC-IosevkaCurlySlab.zip", "Iosevka Curly Slab", "package", ""),
        ("example-org/example-font/ExampleFont.zip", "Example Font", "package", ""),
        *(("githubnext/monaspace/monaspace-nerdfonts.zip", f, "bundle", "nerd") for f in MONASPACE),
        ("microsoft/cascadia-code/CascadiaCode.zip", "Cascadia Code", "bundle", ""),
        ("microsoft/cascadia-code/CascadiaCode.zip", "Cascadia Mono", "bundle", ""),
        ("microsoft/cascadia-code/CascadiaPL.ttf", "Cascadia Code", "build", "powerline"),
        ("subframe7536/maple-font/MapleMono-CN.zip", "Maple Mono", "build", "cjk"),
        ("subframe7536/maple-font/MapleMono-NF-CN-unhinted.zip", "Maple Mono", "build", "nf"),
        ("subframe7536/maple-font/MapleMono-TTF.zip", "Maple Mono", "package", ""),
        ("subframe7536/maple-font/MapleMonoNL-NF-CN.zip", "Maple Mono", "build", "nf"),
        ("subframe7536/maple-font/MapleMonoNL-TTF.zip", "Maple Mono", "build", "nl"),
    ]
)


def make_store(root: Path, rows: list[dict[str, Any]], day: date = DAY) -> Store:
    """A store holding one github_releases snapshot of ``rows``."""
    store = Store(root)
    rows = sorted(rows, key=lambda r: (r["repo"].casefold(), r["repo"], r["tag"], r["asset"]))
    with store.writer(gr.COLLECTOR, day, 1) as writer:
        writer.write_jsonl("assets.jsonl.gz", rows)
        writer.write_json("repos.json", [])
        writer.set_data_date(day)
    return store


def settings_toml(hints: dict[str, str]) -> str:
    """``config/sources/github_releases.toml`` listing ``hints``' repos."""
    return "".join(
        f'[[repos]]\nrepo = "{r}"\n' + (f'family = "{f}"\n' if f else "") + "\n"
        for r, f in hints.items()
    )


def make_root(tmp: Path, hints: dict[str, str] = HINTS) -> Paths:
    sources = tmp / "root" / "config" / "sources"
    sources.mkdir(parents=True)
    (sources / f"{gr.COLLECTOR}.toml").write_text(settings_toml(hints))
    return Paths.for_root(tmp / "root", store=tmp / "store")


def context(paths: Paths, store: Store | None, day: date = DAY) -> MineContext:
    return MineContext(paths, store, RawDir(paths.root / "raw"), day, LOG)


def as_tuples(cands: list[AliasCandidate]) -> list[tuple[str, str, str, str]]:
    return sorted((c.alias.key, c.target.key, c.relation, c.detail) for c in cands)


def keys_of(rows: list[dict[str, Any]], tmp: Path) -> dict[str, set[str]]:
    store = make_store(tmp / "store", rows)
    snap = store.latest(gr.COLLECTOR, DAY)
    assert snap is not None
    return gr.asset_keys(snap, SETTINGS, LOG)


# --- the contract ------------------------------------------------------------------------


def test_the_miner_is_found_like_collectors() -> None:
    found = {m.name for m in pkgutil.iter_modules(miners_pkg.__path__)}
    assert "github_repos" in found
    assert miners_pkg.get("github_repos") is gr.MINER
    assert isinstance(gr.MINER, Miner)
    assert gr.MINER.name == "github_repos"
    assert gr.ASSET_NS == NAMESPACE  # the collector's key namespace


def test_the_table_is_well_formed_and_sorted() -> None:
    assert gr.check_table(gr.REPOS) == []
    names = [e.repo for e in gr.REPOS]
    assert names == sorted(names, key=lambda n: (n.casefold(), n))
    index = gr.table_index(gr.REPOS)
    # Never a source of names or counts here (milestone-1 step 7; nerd_releases counts Nerd).
    for name in ("googlefonts/googlefontdirectory-hg", "ryanoasis/nerd-fonts", "google/fonts"):
        assert not index[name.casefold()].main_channel


def test_check_table_names_each_problem() -> None:
    bad = (
        gr.repo("no-slash", "A"),
        gr.repo("o/dup", "A"),
        gr.repo("O/DUP", "A"),
        gr.repo("o/build", gr.build("A", "not-a-detail")),
        gr.repo("o/bundle", gr.bundle("A", "A")),
        gr.repo("o/package", gr.Target(("A", "B"))),
        gr.repo("o/rule", None, gr.Rule("(", gr.package("A"))),
        gr.repo("o/skip", None, gr.Rule("x", gr.skip(""))),
        gr.repo("o/relation", gr.Target(("A",), "rename")),
        gr.repo("o/space", " A"),
    )
    text = "\n".join(gr.check_table(bad))
    for needle in (
        "no-slash: expected 'owner/name'",
        "O/DUP: listed twice",
        "build detail 'not-a-detail'",
        "a bundle names two or more distinct families",
        "a package names 1 families, not 2",
        "not a regular expression",
        "a skip gives its reason",
        "relation 'rename' not in",
        "empty or has outer spaces",
    ):
        assert needle in text


# --- rules -------------------------------------------------------------------------------


def test_a_rule_matches_the_whole_asset_name_ignoring_case() -> None:
    entry = gr.repo(
        "o/r",
        None,
        gr.Rule(r"Font-NF\.zip", gr.build("Font", "nf")),
        gr.Rule(r"Font(?:-.*)?\.zip", gr.package("Font")),
    )
    assert gr.target_of(entry, "font-nf.ZIP") == gr.build("Font", "nf")  # first match wins
    assert gr.target_of(entry, "Font-TTF.zip") == gr.package("Font")
    # Never by prefix or substring: these name no family.
    assert gr.target_of(entry, "Font.zip.sig") is None
    assert gr.target_of(entry, "OtherFont.zip") is None
    assert gr.target_of(gr.repo("o/r", "Font"), "anything.zip") == gr.package("Font")


def test_iosevka_packagings_and_spacings_count_to_their_variant() -> None:
    entry = gr.table_index(gr.REPOS)["be5invis/iosevka"]
    cases = {
        "SuperTTC-Iosevka.zip": "Iosevka",
        "PkgTTC-SGr-IosevkaTerm.zip": "Iosevka",
        "PkgWebFont-Unhinted-IosevkaFixedSS18.zip": "Iosevka SS18",
        "PkgTTC-IosevkaTermCurlySlab.zip": "Iosevka Curly Slab",
        "PkgTTF-IosevkaAile.zip": "Iosevka Aile",
    }
    for base, family in cases.items():
        assert gr.target_of(entry, base) == gr.package(family), base
    # A variant the table does not know is not guessed from its prefix.
    assert gr.target_of(entry, "SuperTTC-IosevkaSS19.zip") is None
    assert gr.target_of(entry, "SuperTTC-IosevkaCharon.zip") is None


def test_an_iosevka_spacing_can_count_to_its_own_family() -> None:
    """The gate A switch: spacings named in ``IOSEVKA_OWN_FAMILIES`` leave their variant."""
    assert gr.IOSEVKA_OWN_FAMILIES == {}  # the default folds Term and Fixed into the variant
    own = {"Term": "Iosevka Term", "TermSlab": "Iosevka Term Slab"}
    entry = gr.repo("be5invis/Iosevka", None, *gr._iosevka_rules(own))
    assert gr.check_table([entry]) == []
    cases = {
        "PkgTTC-SGr-IosevkaTerm.zip": "Iosevka Term",
        "SuperTTC-IosevkaTermSlab.zip": "Iosevka Term Slab",
        "PkgTTF-IosevkaTermSS04.zip": "Iosevka SS04",  # not named: still its variant
        "PkgTTC-IosevkaFixedSlab.zip": "Iosevka Slab",
        "SuperTTC-Iosevka.zip": "Iosevka",
    }
    for base, family in cases.items():
        assert gr.target_of(entry, base) == gr.package(family), base


def test_bundles_name_the_newest_release_contents() -> None:
    """Checked 2026-09-26 by listing the zips: Plex v6.4.0 adds Sans TC, Libertinus has
    Serif Initials, and geist-font v1.7.2 and later hold Geist Pixel."""
    index = gr.table_index(gr.REPOS)
    for repo, base, member in (
        ("IBM/plex", "TrueType.zip", "IBM Plex Sans TC"),
        ("alerque/libertinus", "Libertinus.zip", "Libertinus Serif Initials"),
        ("vercel/geist-font", "geist-font.zip", "Geist Pixel"),
        ("microsoft/cascadia-code", "CascadiaCode.zip", "Cascadia Mono"),
    ):
        target = gr.target_of(index[repo.casefold()], base)
        assert target is not None, base
        assert (target.relation, member in target.families) == ("bundle", True), base


# --- keys --------------------------------------------------------------------------------


def test_keys_are_the_collectors_keys_without_prerelease_only_ones(tmp_path: Path) -> None:
    keys = keys_of(ROWS, tmp_path)
    wanted: dict[str, set[str]] = defaultdict(set)
    prerelease_only = {("subframe7536/maple-font", "MapleMono-VF.zip")}
    for row in ROWS:
        if (row["repo"], row["asset"]) in prerelease_only or row["asset"] == "SHA256SUMS.txt":
            continue
        wanted[row["repo"]].add(asset_key(row["repo"], row["asset"], row["tag"]).key)
    assert keys == wanted
    # OpenType.zip is in a prerelease too, but a release carries it, so it stays.
    assert "IBM/plex/OpenType.zip" in keys["IBM/plex"]


# --- candidates ----------------------------------------------------------------------------


def test_candidates_of_the_fixture(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    keys = keys_of(ROWS, tmp_path)
    hints = gr.family_hints(SETTINGS)
    with caplog.at_level(logging.WARNING):
        found = gr.candidates(keys, gr.table_index(gr.REPOS), hints, LOG)
    assert as_tuples(found) == EXPECTED
    for c in found:
        repo = "/".join(c.alias.key.split("/")[:2])
        assert c.alias.ns == "gh-asset"
        assert c.target.ns == "font-name"
        assert c.source == "github_repos"
        assert c.evidence == f"https://github.com/{repo}/releases"
        assert not c.auto
    text = caplog.text
    assert "IBM/plex has no rule for ibm-plex-sans-new.zip; left out" in text
    assert "googlefonts/Inconsolata is not a main download channel" in text
    assert "example-org/example-font is not in the table; using the config's family hint" in text
    assert "example-org/no-hint is not in the table and has no family hint" in text
    assert found == sorted(found)


def test_skipped_assets_give_no_candidate(tmp_path: Path) -> None:
    keys = keys_of(ROWS, tmp_path)
    found = gr.candidates(keys, gr.table_index(gr.REPOS), {}, LOG)
    assert "subframe7536/maple-font/cn-base-static.zip" in keys["subframe7536/maple-font"]
    assert all(c.alias.key != "subframe7536/maple-font/cn-base-static.zip" for c in found)


def test_googlefontdirectory_hg_never_gives_names(caplog: pytest.LogCaptureFixture) -> None:
    """Milestone-1 step 7: never pair names through it, even when fetched with a hint."""
    name = "googlefonts/googlefontdirectory-hg"
    keys = {name: {f"{name}/Roboto.zip"}, "google/fonts": {"google/fonts/Roboto.zip"}}
    hints = {name.casefold(): "Roboto", "google/fonts": "Roboto"}
    with caplog.at_level(logging.WARNING):
        assert gr.candidates(keys, gr.table_index(gr.REPOS), hints, LOG) == []
    assert f"{name} is not a main download channel" in caplog.text


def test_repos_match_the_table_ignoring_case() -> None:
    """Keys keep the collector's spelling of the repo; the table is found ignoring case."""
    found = gr.candidates(
        {"jetbrains/JETBRAINSMONO": {"jetbrains/JETBRAINSMONO/JetBrainsMono.zip"}},
        gr.table_index(gr.REPOS),
        {},
        LOG,
    )
    assert as_tuples(found) == [
        ("jetbrains/JETBRAINSMONO/JetBrainsMono.zip", "JetBrains Mono", "package", "")
    ]
    assert found[0].evidence == "https://github.com/jetbrains/JETBRAINSMONO/releases"


def test_a_target_is_a_source_key() -> None:
    cand = gr.candidates(
        {"o/r": {"o/r/Font.zip"}}, gr.table_index([gr.repo("o/r", "Font")]), {}, LOG
    )
    assert cand == [
        AliasCandidate(
            SourceKey("gh-asset", "o/r/Font.zip"),
            SourceKey("font-name", "Font"),
            "package",
            "",
            "github_repos",
            "https://github.com/o/r/releases",
            False,
        )
    ]


# --- mine() --------------------------------------------------------------------------------


def test_mine_reads_the_newest_snapshot_and_its_settings(tmp_path: Path) -> None:
    paths = make_root(tmp_path)
    store = make_store(tmp_path / "store", ROWS)
    make_store(
        tmp_path / "store", [synthetic("JetBrains/JetBrainsMono", "Later.zip")], date(2026, 10, 3)
    )
    first = gr.MINER.mine(context(paths, store))
    assert as_tuples(first) == EXPECTED  # the snapshot after the run date is not read
    assert gr.MINER.mine(context(paths, store)) == first  # deterministic

    seeds = paths.alias_seeds / "github_repos.csv"
    write_seeds(first, seeds)  # what the stage does with them
    assert load_seeds(seeds) == first
    assert not paths.aliases_csv.exists()


def test_mine_fails_without_a_store_or_a_snapshot(tmp_path: Path) -> None:
    paths = make_root(tmp_path)
    with pytest.raises(gr.MinerError, match="needs the snapshot store"):
        gr.MINER.mine(context(paths, None))
    store = make_store(tmp_path / "store", ROWS, date(2026, 10, 3))
    with pytest.raises(gr.MinerError, match="no github_releases snapshot on or before 2026-09-26"):
        gr.MINER.mine(context(paths, store))


def test_mine_fails_without_the_collector_settings(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path / "root", store=tmp_path / "store")
    store = make_store(tmp_path / "store", ROWS)
    with pytest.raises(ConfigError, match=r"github_releases\.toml: missing"):
        gr.MINER.mine(context(paths, store))


def test_mine_refuses_a_malformed_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = make_root(tmp_path)
    store = make_store(tmp_path / "store", ROWS)
    monkeypatch.setattr(gr, "REPOS", (*gr.REPOS, gr.repo("o/bad", gr.build("A", "x"))))
    with pytest.raises(gr.MinerError, match="the repo table is malformed"):
        gr.MINER.mine(context(paths, store))


# --- the committed seed file -------------------------------------------------------------


def test_the_committed_seeds_follow_the_table() -> None:
    rows = load_seeds(SEEDS)
    assert rows, "data/alias-seeds/github_repos.csv is empty"
    index = gr.table_index(gr.REPOS)
    by_alias: dict[str, list[AliasCandidate]] = defaultdict(list)
    for c in rows:
        assert (c.source, c.alias.ns, c.target.ns, c.auto) == (NAME, "gh-asset", "font-name", False)
        assert c.relation in {"package", "build", "bundle"}
        assert re.fullmatch(r"https://github\.com/[^/]+/[^/]+/releases", c.evidence)
        by_alias[c.alias.key].append(c)
    keys = {(match_key(k), c.target.key) for k, cs in by_alias.items() for c in cs}
    assert len(keys) == len(rows)  # one spelling per key the stage tells apart
    for key, cands in by_alias.items():
        owner, name, base = key.split("/", 2)
        entry = index.get(f"{owner}/{name}".casefold())
        if entry is None:  # a repo taken from the config's hint
            continue
        assert entry.main_channel, key
        target = gr.target_of(entry, base)
        assert target is not None, key
        assert sorted(c.target.key for c in cands) == sorted(target.families), key
        assert {(c.relation, c.detail) for c in cands} == {(target.relation, target.detail)}, key


# --- the real snapshot ---------------------------------------------------------------------


@pytest.mark.store
def test_the_real_snapshot(caplog: pytest.LogCaptureFixture) -> None:
    paths = Paths.from_env(ROOT)
    store = Store.from_paths(paths)
    snap = store.latest(gr.COLLECTOR, date.max)
    if snap is None:
        pytest.skip("no github_releases snapshot in the store")
    with caplog.at_level(logging.WARNING):
        found = gr.MINER.mine(context(paths, store, snap.date))
    got = {(match_key(c.alias.key), c.target.key): (c.relation, c.detail) for c in found}
    jetbrains = match_key("JetBrains/JetBrainsMono/JetBrainsMono.zip")
    assert got[(jetbrains, "JetBrains Mono")] == ("package", "")
    # Fira_Code.zip and FiraCode.zip are one key to the stage: one row maps both.
    assert got[(match_key("tonsky/FiraCode/Fira_Code.zip"), "Fira Code")] == ("package", "")
    zips = {"tonsky/FiraCode/FiraCode.zip", "tonsky/FiraCode/Fira_Code.zip"}
    assert len({c.alias.key for c in found} & zips) == 1
    # Every asset of a table repo has a rule, a default or a skip.
    assert "has no rule for" not in caplog.text
    settings = load_settings(gr.collector(), paths)
    keys = {k for ks in gr.asset_keys(snap, settings, LOG).values() for k in ks}
    assert {c.alias.key for c in found} <= keys
    assert found == sorted(set(found))
