"""Methodology §9 known answers, as the alias stage delivers them (milestone-1 step 7).

- Source Sans Pro → Source Sans 3;
- Sauce Code Pro → Source Code Pro;
- Roboto Slab gets no Roboto counts (and Fira Sans ≠ Fira Code, Inter ≠ Inter
  Tight, Noto Sans ≠ Noto Sans JP);
- a renamed family keeps its id.

Each answer is read where stage "map" reads it: ``build/stage/alias_index.json``,
looked up exactly (namespace + ``match_key``). The stage runs on a synthetic
universe with real family names, and on candidates shaped like the miners'
(google/fonts history, Fontsource legacy ids, Nerd Fonts, Homebrew, distro
packages); the miners themselves are replaced by fakes.
"""

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import ClassVar

import pytest

from tff_catalog import aliases, jsonio, stageio
from tff_catalog.aliases import AliasCandidate, AliasError, AliasTable, load_aliases, write_aliases
from tff_catalog.aliases import miners as miners_pkg
from tff_catalog.keys import match_key
from tff_catalog.mapping import index_of
from tff_catalog.paths import Paths
from tff_catalog.records import SourceKey, UniverseRecord
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe, build_universe, next_ids

OCT, NOV, DEC = date(2026, 10, 3), date(2026, 11, 3), date(2026, 12, 3)


def k(ns: str, key: str) -> SourceKey:
    return SourceKey(ns, key)


def cand(
    alias: SourceKey,
    target: SourceKey,
    relation: str,
    source: str,
    *,
    detail: str = "",
    auto: bool = False,
) -> AliasCandidate:
    return AliasCandidate(alias, target, relation, detail, source, f"synthetic {source}", auto)


FAMILIES: dict[str, tuple[str, tuple[SourceKey, ...]]] = {
    "source-sans-3": (
        "Source Sans 3",
        (k("gf-family", "Source Sans 3"), k("fs-id", "source-sans-3"), k("gf-dir", "sourcesans3")),
    ),
    "source-code-pro": (
        "Source Code Pro",
        (
            k("gf-family", "Source Code Pro"),
            k("fs-id", "source-code-pro"),
            k("nerd-folder", "SourceCodePro"),
        ),
    ),
    "roboto": (
        "Roboto",
        (k("gf-family", "Roboto"), k("fs-id", "roboto"), k("brew-cask", "font-roboto")),
    ),
    "roboto-slab": (
        "Roboto Slab",
        (
            k("gf-family", "Roboto Slab"),
            k("fs-id", "roboto-slab"),
            k("brew-cask", "font-roboto-slab"),
        ),
    ),
    "fira-sans": ("Fira Sans", (k("gf-family", "Fira Sans"), k("brew-cask", "font-fira-sans"))),
    "fira-code": (
        "Fira Code",
        (
            k("gf-family", "Fira Code"),
            k("brew-cask", "font-fira-code"),
            k("nerd-folder", "FiraCode"),
        ),
    ),
    "inter": (
        "Inter",
        (k("gf-family", "Inter"), k("fs-id", "inter"), k("brew-cask", "font-inter")),
    ),
    "inter-tight": ("Inter Tight", (k("gf-family", "Inter Tight"), k("fs-id", "inter-tight"))),
    "noto-sans": ("Noto Sans", (k("gf-family", "Noto Sans"), k("fs-id", "noto-sans"))),
    "noto-sans-jp": ("Noto Sans JP", (k("gf-family", "Noto Sans JP"), k("fs-id", "noto-sans-jp"))),
}


@dataclass(frozen=True)
class Miner:
    name: ClassVar[str] = "fake"
    found: tuple[AliasCandidate, ...] = ()

    def mine(self, ctx: miners_pkg.MineContext) -> tuple[AliasCandidate, ...]:
        return self.found


def install_miners(monkeypatch: pytest.MonkeyPatch, cands: list[AliasCandidate]) -> None:
    by_source: dict[str, list[AliasCandidate]] = {}
    for c in cands:
        by_source.setdefault(c.source, []).append(c)
    found = {
        name: type(name, (Miner,), {"name": name})(tuple(rows)) for name, rows in by_source.items()
    }
    monkeypatch.setattr(miners_pkg, "discover", lambda: dict(sorted(found.items())))


def write_universe(paths: Paths, families: dict[str, tuple[str, tuple[SourceKey, ...]]]) -> None:
    u = Universe(
        families={
            fid: Family(fid, name, tuple(sorted(keys)), ("google_metadata",), OCT, name)
            for fid, (name, keys) in families.items()
        },
        unmapped=(),
    )
    stageio.dump_stage(paths, "universe", u)


def repo(tmp_path: Path, families=FAMILIES) -> Paths:
    paths = Paths.for_root(tmp_path / "repo", raw_root=tmp_path / "raw")
    write_aliases([], paths.aliases_csv)
    write_universe(paths, families)
    return paths


def context(paths: Paths, day: date = OCT, ids: dict | None = None) -> StageContext:
    return StageContext(
        paths=paths,
        config=None,  # type: ignore[arg-type] - the stage reads no config
        state=State(ids=ids or {}),
        run_date=day,
        store=None,
        fetcher=None,
        log=logging.getLogger("test.known_answers"),
    )


def index(paths: Paths) -> dict[tuple[str, str], tuple[str, str, str]]:
    return index_of(stageio.load_stage(paths, "alias_index"))


def family(idx: dict, ns: str, key: str) -> str | None:
    """How stage "map" resolves a key: exact (ns, match_key), never by prefix."""
    hit = idx.get((ns, match_key(key)))
    return None if hit is None else hit[0]


def accept_everything_queued(paths: Paths, day: str, **fixes: str) -> list[dict]:
    """The owner answers "accept all" to the whole queue (gate A)."""
    items = jsonio.load(paths.queues / aliases.QUEUE_FILE)["items"]
    folder = paths.reviews / "aliases"
    folder.mkdir(parents=True, exist_ok=True)
    extra = "".join(f'{key} = "{value}"\n' for key, value in fixes.items())
    tables = "".join(
        f'[{i["id"]}]\nchoice = "a"\nrecommended = true\nruling = "Accept."\nreason = "Test."\n{extra}\n'
        for i in items
    )
    (folder / f"{day}.toml").write_text(tables, encoding="utf-8")
    return items


# --- Source Sans Pro → Source Sans 3 -------------------------------------------------------------

SOURCE_SANS = [
    cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        "rename",
        "gf_history",
        auto=True,
    ),
    cand(k("fs-id", "source-sans-pro"), k("fs-id", "source-sans-3"), "rename", "fontsource_legacy"),
    cand(
        k("npm", "@fontsource/source-sans-pro"),
        k("fs-id", "source-sans-3"),
        "package",
        "fontsource_legacy",
    ),
]


def test_source_sans_pro_is_source_sans_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = repo(tmp_path)
    install_miners(monkeypatch, SOURCE_SANS)
    ctx = context(paths)
    aliases.run(ctx)
    idx = index(paths)
    # the google/fonts rename is accepted on its own, and holds for every survey's names
    for ns, key in (
        ("gf-family", "Source Sans Pro"),
        ("almanac-name", "Source Sans Pro"),
        ("fot-name", "source sans pro"),
        ("font-name", "SourceSansPro"),
    ):
        assert family(idx, ns, key) == "source-sans-3", (ns, key)
    assert idx[("gf-family", "sourcesanspro")][1] == "rename"
    # Fontsource's legacy ids wait for the owner, then map the same way
    assert family(idx, "npm", "@fontsource/source-sans-pro") is None
    queued = accept_everything_queued(paths, "2026-10-05")
    assert {i["alias"]["key"] for i in queued} == {"source-sans-pro", "@fontsource/source-sans-pro"}
    assert {i["family_id"] for i in queued} == {"source-sans-3"}
    assert aliases.cmd_apply(ctx) == 0
    idx = index(paths)
    assert family(idx, "npm", "@fontsource/source-sans-pro") == "source-sans-3"
    assert family(idx, "fs-id", "source-sans-pro") == "source-sans-3"
    assert jsonio.load(paths.queues / aliases.QUEUE_FILE)["items"] == []


# --- Sauce Code Pro → Source Code Pro ------------------------------------------------------------

SAUCE = [
    # Nerd Fonts fonts.json: patchedName and its builds -> unpatchedName (auto-eligible)
    cand(
        k("font-name", "SauceCodePro"),
        k("gf-family", "Source Code Pro"),
        "build",
        "nerd",
        detail="nerd",
        auto=True,
    ),
    cand(
        k("font-name", "SauceCodePro Nerd Font Mono"),
        k("gf-family", "Source Code Pro"),
        "build",
        "nerd",
        detail="nfm",
        auto=True,
    ),
    cand(
        k("font-name", "SauceCodePro NF"),
        k("gf-family", "Source Code Pro"),
        "build",
        "nerd",
        detail="nf",
        auto=True,
    ),
    # the Homebrew cask is not an unpatchedName row: the owner reviews it
    cand(
        k("brew-cask", "font-sauce-code-pro-nerd-font"),
        k("nerd-folder", "SourceCodePro"),
        "build",
        "homebrew",
        detail="nerd",
        auto=True,
    ),
]


def test_sauce_code_pro_is_source_code_pro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = repo(tmp_path)
    install_miners(monkeypatch, SAUCE)
    aliases.run(context(paths))
    idx = index(paths)
    for ns, key in (
        ("font-name", "Sauce Code Pro"),
        ("font-name", "SauceCodePro"),
        ("almanac-name", "Sauce Code Pro"),
        ("font-name", "SauceCodePro Nerd Font Mono"),
        ("font-name", "SauceCodePro NF"),
    ):
        assert family(idx, ns, key) == "source-code-pro", (ns, key)
    assert idx[("font-name", "saucecodepro")] == ("source-code-pro", "build", "nerd")
    [item] = jsonio.load(paths.queues / aliases.QUEUE_FILE)["items"]
    assert (item["alias"]["key"], item["family_id"], item["reason"]) == (
        "font-sauce-code-pro-nerd-font",
        "source-code-pro",
        "review",
    )
    table = AliasTable.from_rows(load_aliases(paths.aliases_csv))
    assert table.rows == ()  # the stage never writes the table; --apply does


# --- Roboto Slab gets no Roboto counts -------------------------------------------------------------

SIBLINGS = [
    pytest.param("Roboto", "Roboto Slab", id="roboto-slab"),
    pytest.param("Fira Sans", "Fira Code", id="fira-code"),
    pytest.param("Fira Code", "Fira Sans", id="fira-sans"),
    pytest.param("Inter", "Inter Tight", id="inter-tight"),
    pytest.param("Noto Sans", "Noto Sans JP", id="noto-sans-jp"),
]


def slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def hostile(parent: str, sibling: str) -> list[AliasCandidate]:
    """Every way a miner could fold the sibling into the parent, some even marked auto."""
    target, s = k("gf-family", parent), slug(sibling)
    return [
        cand(k("gf-family", sibling), target, "rename", "gf_history", auto=True),
        cand(
            k("font-name", f"{sibling.replace(' ', '')} Nerd Font"),
            target,
            "build",
            "nerd",
            detail="nerd",
            auto=True,
        ),
        cand(k("brew-cask", f"font-{s}"), target, "package", "homebrew", auto=True),
        cand(k("deb-pkg", f"fonts-{s}"), target, "package", "distro"),
        cand(k("arch-pkg", f"ttf-{s}"), target, "package", "distro"),
        cand(k("npm", f"@fontsource/{s}"), target, "package", "fontsource_legacy"),
        cand(k("almanac-name", sibling), target, "rename", "oldlib"),
        cand(
            k("gh-asset", f"{sibling.replace(' ', '')}-v2.zip"), target, "package", "github_repos"
        ),
    ]


def rightful(sibling: str) -> list[AliasCandidate]:
    s, target = slug(sibling), k("gf-family", sibling)
    return [
        cand(k("deb-pkg", f"fonts-{s}"), target, "package", "distro"),
        cand(k("npm", f"@fontsource/{s}"), target, "package", "fontsource_legacy"),
    ]


def assert_no_sibling_counts(idx: dict, parent_id: str, sibling_id: str, sibling: str) -> None:
    needle = match_key(sibling)
    leaks = sorted(key for key, (fid, _, _) in idx.items() if needle in key[1] and fid == parent_id)
    assert leaks == []
    for key in FAMILIES[sibling_id][1]:
        assert family(idx, key.ns, key.key) == sibling_id, key


@pytest.mark.parametrize(("parent", "sibling"), SIBLINGS)
def test_a_sibling_gets_no_counts_of_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parent: str, sibling: str
) -> None:
    paths = repo(tmp_path)
    install_miners(monkeypatch, hostile(parent, sibling) + rightful(sibling))
    ctx = context(paths)
    aliases.run(ctx)
    parent_id, sibling_id = slug(parent), slug(sibling)
    assert_no_sibling_counts(index(paths), parent_id, sibling_id, sibling)
    statuses = {
        (line["alias"]["key"], line["target"]["key"]): line["status"]
        for line in jsonio.iter_jsonl(paths.stage / aliases.CANDIDATES_FILE)
    }
    assert {s for (_, t), s in statuses.items() if t == parent} == {"blocked"}
    # the owner accepts the whole queue: only the rightful rows exist to accept
    queued = accept_everything_queued(paths, "2026-10-05")
    assert {i["family_id"] for i in queued} == {sibling_id}
    assert aliases.cmd_apply(ctx) == 0
    idx = index(paths)
    assert_no_sibling_counts(idx, parent_id, sibling_id, sibling)
    assert family(idx, "deb-pkg", f"fonts-{slug(sibling)}") == sibling_id
    assert family(idx, "npm", f"@fontsource/{slug(sibling)}") == sibling_id


def test_the_owner_cannot_fold_roboto_slab_into_roboto_by_mistake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = repo(tmp_path)
    install_miners(monkeypatch, rightful("Roboto Slab"))
    aliases.run(context(paths))
    accept_everything_queued(paths, "2026-10-05", family_id="roboto")  # a slip of the hand
    with pytest.raises(AliasError, match="a distinct row forbids"):
        aliases.run(context(paths))


def test_a_universe_that_folds_roboto_slab_into_roboto_fails_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folded = dict(FAMILIES)
    _, keys = folded.pop("roboto-slab")
    folded["roboto"] = ("Roboto", (*folded["roboto"][1], *keys))
    paths = repo(tmp_path, folded)
    install_miners(monkeypatch, [])
    with pytest.raises(AliasError, match=r"gf-family:robotoslab -> roboto breaks a distinct row"):
        aliases.run(context(paths))


# --- a renamed family keeps its id -----------------------------------------------------------------

REGISTRY = {"muli": {"family": "Muli", "minted_from": "Muli", "first_seen": "2026-09-03"}}
RENAME = [cand(k("gf-family", "Muli"), k("gf-family", "Mulish"), "rename", "gf_history", auto=True)]
MULISH_KEYS = (k("gf-family", "Mulish"), k("fs-id", "mulish"))


def test_a_renamed_family_keeps_its_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # October: google/fonts renamed Muli to Mulish; the universe stage, which runs
    # first and has no alias row yet, gave the new name a new id.
    paths = repo(tmp_path, {"mulish": ("Mulish", MULISH_KEYS)})
    install_miners(monkeypatch, RENAME)
    ctx = context(paths, OCT, REGISTRY)
    aliases.run(ctx)
    idx = index(paths)
    for ns, key in (("gf-family", "Muli"), ("almanac-name", "Muli"), ("gf-family", "Mulish")):
        assert family(idx, ns, key) == "mulish", (ns, key)  # this run's family, until applied
    assert aliases.cmd_apply(ctx) == 0
    rows = {(r.alias, r.ns, r.family_id, r.detail) for r in load_aliases(paths.aliases_csv)}
    assert rows == {("Muli", "gf-family", "muli", ""), ("Mulish", "font-name", "muli", "current")}

    # November: the universe stage reads those rows, so the family is "muli" again.
    write_universe(paths, {"muli": ("Mulish", MULISH_KEYS)})
    aliases.run(context(paths, NOV, REGISTRY))
    idx = index(paths)
    for ns, key in (
        ("gf-family", "Muli"),
        ("gf-family", "Mulish"),
        ("fs-id", "mulish"),
        ("almanac-name", "Muli"),
        ("fot-name", "Mulish"),
    ):
        assert family(idx, ns, key) == "muli", (ns, key)


def test_the_universe_stage_keeps_the_id_through_those_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rename through the real ``universe.build_universe`` (M1 step 4)."""
    paths = repo(tmp_path, {"mulish": ("Mulish", MULISH_KEYS)})
    install_miners(monkeypatch, RENAME)
    ctx = context(paths, OCT, REGISTRY)
    aliases.run(ctx)
    assert aliases.cmd_apply(ctx) == 0
    table = AliasTable.from_rows(load_aliases(paths.aliases_csv))
    recs = [
        UniverseRecord("google_metadata", k("gf-family", "Mulish"), "Mulish"),
        UniverseRecord("fontsource", k("fs-id", "mulish"), "Mulish"),
    ]
    u = build_universe(recs, table, REGISTRY, run_date=DEC)
    assert sorted(u.families) == ["muli"]
    assert u.families["muli"].family == "Mulish"
    assert set(u.families["muli"].keys) == set(MULISH_KEYS)


GOOGLE_MULISH = UniverseRecord("google_metadata", k("gf-family", "Mulish"), "Mulish")
FONTSOURCE_MULISH = UniverseRecord("fontsource", k("fs-id", "mulish"), "Mulish")


def run_universe(paths: Paths, recs: list[UniverseRecord], ids: dict, day: date) -> Universe:
    """Stage "universe" as it would run: the committed table, the registry, then the stage file."""
    table = AliasTable.from_rows(load_aliases(paths.aliases_csv))
    u = build_universe(recs, table, ids, run_date=day)
    stageio.dump_stage(paths, "universe", u)
    return u


def test_a_renamed_family_keeps_its_id_while_a_stale_source_lists_the_old_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cask still says "Muli" after Google's rename: two live families in October.

    The owner's "accept" must fold Mulish into the old id, never the other way round.
    """
    paths = repo(tmp_path, {})
    stale = UniverseRecord("homebrew_casks", k("brew-cask", "font-muli"), "Muli")
    recs = [GOOGLE_MULISH, FONTSOURCE_MULISH, stale]
    oct_u = run_universe(paths, recs, REGISTRY, OCT)
    assert sorted(oct_u.families) == ["muli", "mulish"]
    install_miners(monkeypatch, RENAME)
    ctx = context(paths, OCT, REGISTRY)
    aliases.run(ctx)
    [item] = jsonio.load(paths.queues / aliases.QUEUE_FILE)["items"]
    assert (item["reason"], item["family_id"], item["current"]) == (
        "other-family",
        "muli",
        {"ns": "gf-family", "key": "Mulish"},
    )
    assert aliases.cmd_queue(ctx) == 0
    assert "gf-family:Muli  +gf-family:Mulish -> muli" in capsys.readouterr().out
    accept_everything_queued(paths, "2026-10-05")
    assert aliases.cmd_apply(ctx) == 0

    # November: the October refresh registered "mulish" too; the rows still win.
    nov_ids = next_ids(oct_u, REGISTRY)
    assert sorted(nov_ids) == ["muli", "mulish"]
    nov_u = run_universe(paths, recs, nov_ids, NOV)
    assert sorted(nov_u.families) == ["muli"]
    assert nov_u.families["muli"].family == "Mulish"
    aliases.run(context(paths, NOV, nov_ids))
    idx = index(paths)
    for ns, key in (
        ("gf-family", "Mulish"),
        ("fs-id", "mulish"),
        ("brew-cask", "font-muli"),
        ("gf-family", "Muli"),
        ("almanac-name", "Mulish"),
    ):
        assert family(idx, ns, key) == "muli", (ns, key)
    assert jsonio.load(paths.queues / aliases.QUEUE_FILE)["items"] == []


def test_a_rename_applied_a_month_late_still_restores_the_old_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The October refresh merged without ``aliases --apply``, so "mulish" is registered.

    November asks the owner (two registered ids), and accepting restores "muli".
    """
    paths = repo(tmp_path, {})
    recs = [GOOGLE_MULISH, FONTSOURCE_MULISH]
    oct_u = run_universe(paths, recs, REGISTRY, OCT)
    assert sorted(oct_u.families) == ["mulish"]
    nov_ids = next_ids(oct_u, REGISTRY)
    run_universe(paths, recs, nov_ids, NOV)
    install_miners(monkeypatch, RENAME)
    ctx = context(paths, NOV, nov_ids)
    aliases.run(ctx)
    [item] = accept_everything_queued(paths, "2026-11-05")
    assert (item["reason"], item["family_id"]) == ("registry", "muli")
    assert family(index(paths), "gf-family", "Muli") is None  # queued: unmatched until ruled
    assert aliases.cmd_apply(ctx) == 0
    assert family(index(paths), "gf-family", "Muli") == "mulish"  # this run's family, until...
    dec_u = run_universe(paths, recs, next_ids(oct_u, nov_ids), DEC)
    assert sorted(dec_u.families) == ["muli"]  # ...the universe stage reads the rows


def test_a_rename_that_names_its_target_by_a_package_key_still_keeps_the_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new name's row is the family's name, which the universe groups records by."""
    rename = [cand(k("gf-family", "Muli"), k("fs-id", "mulish"), "rename", "gf_history", auto=True)]
    paths = repo(tmp_path, {"mulish": ("Mulish", MULISH_KEYS)})
    install_miners(monkeypatch, rename)
    ctx = context(paths, OCT, REGISTRY)
    aliases.run(ctx)
    assert aliases.cmd_apply(ctx) == 0
    rows = {(r.alias, r.ns, r.family_id, r.detail) for r in load_aliases(paths.aliases_csv)}
    assert rows == {("Muli", "gf-family", "muli", ""), ("Mulish", "font-name", "muli", "current")}
    u = run_universe(paths, [GOOGLE_MULISH, FONTSOURCE_MULISH], REGISTRY, DEC)
    assert sorted(u.families) == ["muli"]
    assert set(u.families["muli"].keys) == set(MULISH_KEYS)
