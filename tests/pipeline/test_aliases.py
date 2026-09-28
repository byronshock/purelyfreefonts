"""The alias table, merge rules, owner rulings and the "aliases" stage (milestone-1 step 7).

Everything is synthetic: family names are real (they are what the rules are
about), keys and evidence are made up. The methodology §9 known answers are in
``test_known_answers.py``.
"""

import logging
from dataclasses import astuple, dataclass, replace
from datetime import date
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from tff_catalog import aliases, jsonio, reviews, stageio
from tff_catalog.aliases import (
    COLUMNS,
    DEFAULT_AUTO_RULES,
    SIBLING_DETAIL,
    AliasCandidate,
    AliasError,
    AliasRow,
    AliasTable,
    Blocks,
    Decision,
    FamilyRef,
    apply_rulings,
    build_index,
    load_alias_rulings,
    load_aliases,
    merge,
    merge_detailed,
    mine_all,
    write_aliases,
    write_seeds,
)
from tff_catalog.aliases import miners as miners_pkg
from tff_catalog.keys import match_key
from tff_catalog.mapping import entries_of, index_of
from tff_catalog.paths import Paths
from tff_catalog.records import SourceKey
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe

DAY = date(2026, 10, 3)
OLD = date(2026, 9, 25)


def k(ns: str, key: str) -> SourceKey:
    return SourceKey(ns, key)


def fam(fid: str, name: str, *keys: SourceKey) -> FamilyRef:
    return FamilyRef(fid, name, tuple(sorted(keys)))


def cand(
    alias: SourceKey,
    target: SourceKey,
    relation: str = "rename",
    *,
    source: str = "oldlib",
    detail: str = "",
    auto: bool = False,
    evidence: str = "test",
) -> AliasCandidate:
    return AliasCandidate(alias, target, relation, detail, source, evidence, auto)


def row(
    alias: str,
    ns: str,
    family_id: str,
    relation: str = "rename",
    detail: str = "",
    source: str = "hand",
    reviewed_by: str = "owner:2026-09-25",
) -> AliasRow:
    return AliasRow(alias, ns, family_id, relation, detail, source, OLD, reviewed_by)


FAMILIES = (
    fam("inter", "Inter", k("gf-family", "Inter"), k("fs-id", "inter")),
    fam("roboto", "Roboto", k("gf-family", "Roboto"), k("brew-cask", "font-roboto")),
    fam(
        "roboto-slab",
        "Roboto Slab",
        k("gf-family", "Roboto Slab"),
        k("brew-cask", "font-roboto-slab"),
    ),
    fam(
        "source-code-pro",
        "Source Code Pro",
        k("gf-family", "Source Code Pro"),
        k("fs-id", "source-code-pro"),
    ),
    fam(
        "source-sans-3",
        "Source Sans 3",
        k("gf-family", "Source Sans 3"),
        k("fs-id", "source-sans-3"),
        k("gf-dir", "sourcesans3"),
    ),
)
EMPTY = AliasTable(())


def merged(table=EMPTY, cands=(), rules=DEFAULT_AUTO_RULES, families=FAMILIES, **kw):
    return merge_detailed(table, cands, rules, families=families, today=DAY, **kw)


def outcome(result, alias: SourceKey):
    [found] = [o for o in result.outcomes if o.candidate.alias == alias]
    return found


def new_rows(result, table=EMPTY) -> set[AliasRow]:
    return {r for r in result.rows if r.source != aliases.SIBLING_SOURCE} - set(table.rows)


# --- the table ------------------------------------------------------------------------------


def test_table_sorts_dedupes_and_looks_up_by_match_key_within_one_namespace() -> None:
    a = row("Source Sans Pro", "gf-family", "source-sans-3")
    b = row("@fontsource/source-sans-pro", "npm", "source-sans-3", "package")
    table = AliasTable.from_rows([b, a, a])
    assert table.rows == (b, a)  # canonical order: family, relation, ns, key
    assert table.lookup(k("gf-family", "source-sans pro")) == (a,)
    assert table.lookup(k("gf-family", "SOURCE_SANS_PRO")) == (a,)
    assert table.lookup(k("almanac-name", "Source Sans Pro")) == ()
    assert table.lookup(k("npm", "@fontsource/source-sans-pro")) == (b,)


def test_one_key_may_map_to_one_family_only() -> None:
    with pytest.raises(AliasError, match="maps to 2 families"):
        AliasTable.from_rows(
            [row("Fira", "font-name", "fira-sans"), row("FIRA", "font-name", "fira-code")]
        )
    with pytest.raises(AliasError, match="maps to 2 families"):
        AliasTable.from_rows(
            [
                row("Arial", "font-name", "", "ineligible", "proprietary"),
                row("Arial", "font-name", "arimo"),
            ]
        )
    with pytest.raises(AliasError, match="bundle"):
        AliasTable.from_rows(
            [
                row("fonts-urw-base35", "deb-pkg", "nimbus-sans", "bundle"),
                row("fonts-urw-base35", "deb-pkg", "nimbus-sans", "package"),
            ]
        )
    fine = AliasTable.from_rows(
        [
            row("Fira", "font-name", "fira-sans"),
            row("Fira", "font-name", "fira-sans", "postscript"),  # same family, two relations
            row("Fira", "font-name", "fira-code", "distinct"),  # a block is not a mapping
            row("Fira", "gf-family", "fira-code"),  # another namespace
            row("fonts-urw-base35", "deb-pkg", "nimbus-sans", "bundle"),
            row("fonts-urw-base35", "deb-pkg", "nimbus-roman", "bundle"),
        ]
    )
    assert len(fine.lookup(k("font-name", "fira"))) == 3


def test_write_then_load_keeps_the_frozen_header(tmp_path: Path) -> None:
    rows = [
        row("Source Sans Pro", "gf-family", "source-sans-3"),
        row("SF Pro", "font-name", "", "ineligible", "proprietary"),
        row("Roboto Slab", "font-name", "roboto", "distinct", SIBLING_DETAIL),
    ]
    path = tmp_path / "aliases.csv"
    write_aliases(rows, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "alias,ns,family_id,relation,detail,source,first_seen,reviewed_by"
    assert tuple(lines[0].split(",")) == COLUMNS
    assert sorted(load_aliases(path)) == sorted(rows)


# --- blocks -----------------------------------------------------------------------------------


def test_a_sibling_block_covers_every_key_that_contains_the_name() -> None:
    blocks = Blocks.of([row("Roboto Slab", "font-name", "roboto", "distinct", SIBLING_DETAIL)])
    for key in (
        k("gf-family", "Roboto Slab"),
        k("almanac-name", "roboto slab"),
        k("npm", "@fontsource/roboto-slab"),
        k("deb-pkg", "fonts-roboto-slab"),
        k("brew-cask", "font-roboto-slab"),
        k("font-name", "RobotoSlab-Bold"),
    ):
        assert blocks.blocks(key, "roboto"), key
        assert not blocks.blocks(key, "roboto-slab"), key
    assert not blocks.blocks(k("brew-cask", "font-roboto"), "roboto")
    assert not blocks.blocks(k("gf-family", "Roboto Slab"), "")  # ineligible is never blocked


def test_an_exact_block_covers_the_name_in_every_name_namespace_only() -> None:
    blocks = Blocks.of([row("Roboto", "font-name", "roboto-slab", "distinct")])
    assert blocks.blocks(k("gf-family", "Roboto"), "roboto-slab")
    assert blocks.blocks(k("almanac-name", "ROBOTO"), "roboto-slab")
    assert not blocks.blocks(k("npm", "roboto"), "roboto-slab")  # not a name namespace
    assert not blocks.blocks(k("gf-family", "Roboto Slab"), "roboto-slab")


# --- the index --------------------------------------------------------------------------------


def test_index_holds_universe_keys_and_names_in_every_name_namespace() -> None:
    idx = build_index((), FAMILIES).index()
    assert idx[("gf-family", "sourcesans3")] == ("source-sans-3", "direct", "")
    assert idx[("gf-dir", "sourcesans3")] == ("source-sans-3", "direct", "")
    assert idx[("fs-id", "sourcesans3")] == ("source-sans-3", "direct", "")  # match_key form
    for ns in ("almanac-name", "fot-name", "font-name", "foundry-family"):
        assert idx[(ns, "sourcesans3")] == ("source-sans-3", "direct", ""), ns
    assert ("npm", "sourcesans3") not in idx  # npm keys are not names
    assert index_of(entries_of(idx)) == idx  # what stage "map" reads


def test_npm_packages_named_after_universe_keys_map_to_their_family() -> None:
    rows = [row("@fontsource/source-sans-pro", "npm", "source-sans-3", "package", "legacy")]
    built = build_index(rows, FAMILIES)
    idx = built.index()
    assert idx[("npm", match_key("@fontsource/source-sans-3"))] == (
        "source-sans-3",
        "package",
        "fontsource",
    )
    assert idx[("npm", match_key("@fontsource-variable/inter"))] == (
        "inter",
        "package",
        "fontsource",
    )
    # Roboto Slab has no Fontsource key here, so no package of it is guessed.
    assert ("npm", match_key("@fontsource/roboto-slab")) not in idx
    assert idx[("npm", match_key("@expo-google-fonts/source-sans-3"))] == (
        "source-sans-3",
        "package",
        "expo",
    )
    # A row still wins, and no other scope or bare id is read as a package.
    assert idx[("npm", match_key("@fontsource/source-sans-pro"))][1:] == ("package", "legacy")
    assert ("npm", match_key("@other/source-sans-3")) not in idx
    assert ("npm", "sourcesans3") not in idx
    assert not built.violations


def test_a_row_in_the_namespace_beats_universe_keys_and_names() -> None:
    rows = [
        row("Source Sans Pro", "gf-family", "source-sans-3"),
        row("Inter", "almanac-name", "", "ineligible", "generic"),  # made-up ruling
        row("font-roboto", "brew-cask", "", "ineligible", "system"),  # made-up ruling
    ]
    idx = build_index(rows, FAMILIES).index()
    assert idx[("gf-family", "sourcesanspro")] == ("source-sans-3", "rename", "")
    assert idx[("fot-name", "sourcesanspro")] == ("source-sans-3", "rename", "")  # spread
    assert idx[("almanac-name", "inter")] == ("", "ineligible", "generic")
    assert idx[("fot-name", "inter")] == ("", "ineligible", "generic")  # a name row spreads
    assert idx[("gf-family", "inter")] == ("inter", "direct", "")  # but a universe key wins
    assert idx[("brew-cask", "fontroboto")] == ("", "ineligible", "system")


def test_names_of_two_families_are_left_out_never_guessed() -> None:
    fams = (
        fam("sample-a", "Sample", k("foundry-family", "Sample")),
        fam("sample-b", "Sample", k("fontist-formula", "sample")),
    )
    built = build_index((), fams)
    assert ("almanac-name", "sample") not in built.hits
    assert built.is_ambiguous(k("almanac-name", "Sample"))
    assert built.hits[("fontist-formula", "sample")].family_id == "sample-b"
    assert any("2 families" in n for n in built.notes)


def test_bundle_and_information_rows_stay_out_of_the_index() -> None:
    rows = [
        row("fonts-urw-base35", "deb-pkg", "nimbus-sans", "bundle"),
        row("Roboto Serif", "font-name", "roboto", "related"),
        row("Roboto Mono", "font-name", "roboto", "sibling"),
    ]
    built = build_index(rows, (*FAMILIES, fam("nimbus-sans", "Nimbus Sans")))
    assert ("deb-pkg", "fontsurwbase35") not in built.hits
    assert ("font-name", "robotoserif") not in built.hits
    assert ("font-name", "robotomono") not in built.hits
    assert any("bundle row" in n for n in built.notes)


def test_a_row_for_a_family_missing_this_run_is_left_out() -> None:
    built = build_index([row("Gone Sans", "font-name", "gone-sans")], FAMILIES)
    assert ("font-name", "gonesans") not in built.hits
    assert any("no such family" in n for n in built.notes)


def test_a_universe_key_that_breaks_a_distinct_row_is_a_violation() -> None:
    folded = (fam("roboto", "Roboto", k("gf-family", "Roboto"), k("gf-family", "Roboto Slab")),)
    built = build_index(
        [row("Roboto Slab", "font-name", "roboto", "distinct", SIBLING_DETAIL)], folded
    )
    assert built.violations == ("gf-family:robotoslab -> roboto breaks a distinct row",)
    assert ("gf-family", "robotoslab") not in built.hits


# --- merge ------------------------------------------------------------------------------------


def test_auto_accepts_google_renames_and_nerd_unpatched_rows() -> None:
    gf = cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        source="gf_history",
        auto=True,
    )
    nerd = cand(
        k("font-name", "SauceCodePro"),
        k("gf-family", "Source Code Pro"),
        "build",
        source="nerd",
        detail="nerd",
        auto=True,
    )
    result = merged(cands=[nerd, gf])
    assert result.queue == ()
    assert new_rows(result) == {
        AliasRow(
            "Source Sans Pro",
            "gf-family",
            "source-sans-3",
            "rename",
            "",
            "gf_history",
            DAY,
            "auto:gf_history_rename",
        ),
        AliasRow(
            "SauceCodePro",
            "font-name",
            "source-code-pro",
            "build",
            "nerd",
            "nerd",
            DAY,
            "auto:nerd_unpatched",
        ),
    }
    assert {o.status for o in result.outcomes} == {"accepted"}


@pytest.mark.parametrize(
    ("source", "relation", "detail", "auto"),
    [
        ("homebrew", "package", "", True),  # auto flag from a miner no rule trusts
        ("fontsource_legacy", "rename", "", True),
        ("oldlib", "rename", "", True),
        ("gf_history", "rename", "", False),  # the miner did not mark it
        ("gf_history", "build", "", True),  # google/fonts: renames only
        ("nerd", "package", "", True),  # Nerd: unpatchedName rows (build or rename) only
        ("oldlib", "ineligible", "proprietary", True),
    ],
)
def test_everything_else_waits_for_the_owner(
    source: str, relation: str, detail: str, auto: bool
) -> None:
    c = cand(
        k("font-name", "Source Sans Variable"),
        k("gf-family", "Source Sans 3"),
        relation,
        source=source,
        detail=detail,
        auto=auto,
    )
    result = merged(cands=[c])
    assert new_rows(result) == set()
    [item] = result.queue
    assert (item.reason, item.alias, item.sources) == ("review", c.alias, (source,))
    assert item.family_id == ("" if relation == "ineligible" else "source-sans-3")


def test_rules_are_named_and_can_be_switched_off() -> None:
    gf = cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        source="gf_history",
        auto=True,
    )
    assert [q.reason for q in merged(cands=[gf], rules=frozenset()).queue] == ["review"]
    with pytest.raises(ValueError, match="unknown auto rules"):
        merged(cands=[gf], rules=frozenset({"everything"}))


def test_queue_reasons() -> None:
    table = AliasTable.from_rows([row("SSP", "font-name", "source-code-pro")])
    cands = [
        cand(k("font-name", "Lost"), k("gf-family", "Nowhere Sans"), auto=True),
        cand(k("font-name", "SSP"), k("gf-family", "Source Sans 3")),
        cand(k("brew-cask", "font-inter"), k("gf-family", "Source Sans 3"), "package"),
        cand(k("font-name", "Inter Variable"), k("gf-family", "Inter"), "build", detail="variable"),
        cand(k("font-name", "Inter Variable"), k("gf-family", "Roboto"), "build", source="nerd"),
        cand(k("font-name", "Arial"), k("font-name", "Arial"), "ineligible", detail="bogus"),
    ]
    # an alias that is itself another family's key or name
    cands.append(cand(k("almanac-name", "Inter"), k("gf-family", "Roboto")))
    result = merged(table, cands)
    reasons = {(o.candidate.alias.key, o.candidate.target.key): o.reason for o in result.outcomes}
    assert reasons == {
        ("Lost", "Nowhere Sans"): "target-unknown",
        ("SSP", "Source Sans 3"): "conflict",
        ("font-inter", "Source Sans 3"): "review",
        ("Inter Variable", "Inter"): "competing",
        ("Inter Variable", "Roboto"): "competing",
        ("Arial", "Arial"): "invalid",
        ("Inter", "Roboto"): "other-family",
    }
    assert new_rows(result, table) == set()
    lost = next(q for q in result.queue if q.alias.key == "Lost")
    assert lost.family_id is None
    assert lost.auto


def test_known_and_blocked_candidates_are_neither_rows_nor_questions() -> None:
    table = AliasTable.from_rows(
        [
            row("Source Sans Pro", "gf-family", "source-sans-3"),
            row("Sauce Sans", "font-name", "source-sans-3", "distinct", "rejected"),
        ]
    )
    result = merged(
        table,
        [
            cand(k("gf-family", "Source Sans Pro"), k("fs-id", "source-sans-3")),  # a row has it
            cand(k("fs-id", "source-sans-3"), k("gf-family", "Source Sans 3")),  # a universe key
            cand(k("almanac-name", "Sauce Sans"), k("gf-family", "Source Sans 3")),  # rejected
        ],
    )
    assert {o.candidate.alias.key: o.status for o in result.outcomes} == {
        "Source Sans Pro": "known",
        "source-sans-3": "known",
        "Sauce Sans": "blocked",
    }
    assert result.queue == ()
    assert new_rows(result, table) == set()


def test_a_bundled_key_blocks_single_family_candidates() -> None:
    """Ruling of 2026-09-26 (gate A): a key the table makes a bundle (D2) is settled, so a
    miner's proposal to send it to one family never reaches the owner; a name bundled in
    one name namespace is bundled in all of them."""
    table = AliasTable.from_rows(
        [
            row("ttf-roboto", "arch-pkg", "roboto", "bundle", "2"),
            row("ttf-roboto", "arch-pkg", "roboto-slab", "bundle", "2"),
            row("Roboto Family", "font-name", "roboto", "bundle", "2"),
            row("Roboto Family", "font-name", "roboto-slab", "bundle", "2"),
        ]
    )
    result = merged(
        table,
        [
            cand(k("arch-pkg", "ttf-roboto"), k("gf-family", "Roboto"), "package"),
            cand(
                k("arch-pkg", "ttf-roboto"),
                k("arch-pkg", "ttf-roboto"),
                "ineligible",
                detail="icon",
            ),
            cand(k("gf-family", "Roboto Family"), k("font-name", "Nowhere"), "build"),
            cand(k("arch-pkg", "ttf-roboto"), k("gf-family", "Roboto Slab"), "bundle", detail="2"),
            cand(k("brew-cask", "font-roboto-family"), k("gf-family", "Roboto"), "package"),
        ],
    )
    status = {(o.candidate.alias.key, o.candidate.relation): o.status for o in result.outcomes}
    assert status == {
        ("ttf-roboto", "package"): "blocked",
        ("ttf-roboto", "ineligible"): "blocked",
        ("Roboto Family", "build"): "blocked",  # its target names no family: still settled
        ("ttf-roboto", "bundle"): "known",  # the table's own bundle row
        ("font-roboto-family", "package"): "queued",  # another key
    }
    assert [i.alias.key for i in result.queue] == ["font-roboto-family"]
    assert new_rows(result, table) == set()


def test_candidates_that_agree_share_one_question_or_follow_the_accepted_one() -> None:
    alias, target = k("font-name", "Source Sans Pro"), k("gf-family", "Source Sans 3")
    two_miners = merged(
        cands=[cand(alias, target, source="oldlib"), cand(alias, target, source="distro")]
    )
    [item] = two_miners.queue
    assert item.sources == ("distro", "oldlib")
    assert item.id.startswith("A-")
    assert len(item.id) == 12
    with_auto = merged(
        cands=[
            cand(alias, target, source="oldlib"),
            cand(alias, target, source="gf_history", auto=True),
        ]
    )
    assert with_auto.queue == ()
    assert sorted(o.status for o in with_auto.outcomes) == ["accepted", "known"]
    assert len(new_rows(with_auto)) == 1


def test_merge_is_deterministic_whatever_the_candidate_order() -> None:
    cands = [
        cand(
            k("gf-family", "Source Sans Pro"),
            k("gf-family", "Source Sans 3"),
            source="gf_history",
            auto=True,
        ),
        cand(k("npm", "@fontsource/source-sans-pro"), k("fs-id", "source-sans-3"), "package"),
        cand(
            k("font-name", "SauceCodePro"),
            k("gf-family", "Source Code Pro"),
            "build",
            source="nerd",
            detail="nerd",
            auto=True,
        ),
        cand(k("font-name", "Lost"), k("gf-family", "Nowhere")),
    ]
    first = merged(cands=cands)
    rng = np.random.default_rng(7)
    for _ in range(5):
        shuffled = [cands[i] for i in rng.permutation(len(cands))]
        assert merged(cands=shuffled) == first


def reg(*entries: tuple[str, str, str]) -> dict[str, dict[str, str]]:
    return {
        fid: {"family": name, "minted_from": first, "first_seen": "2026-09-03"}
        for fid, name, first in entries
    }


@pytest.mark.parametrize(
    ("registry", "status", "reason", "family_id"),
    [
        # the old name was minted here: both names keep that id
        (
            reg(("source-sans-pro", "Source Sans Pro", "Source Sans Pro")),
            "accepted",
            "",
            "source-sans-pro",
        ),
        # an old display name counts too (a chain of renames)
        (reg(("ssp", "Source Sans Pro", "Adobe Source Sans")), "accepted", "", "ssp"),
        # two registered ids carry the old name: never guessed
        (
            reg(("ssp", "Source Sans Pro", "SSP"), ("ssp-2", "Source Sans Pro", "SSP 2")),
            "queued",
            "registry",
            "source-sans-3",
        ),
        # the new name's family is registered too (a refresh merged before the rename was
        # applied): joining two families is the owner's call, and accepting keeps the old id
        (
            reg(
                ("ssp", "Source Sans Pro", "SSP"),
                ("source-sans-3", "Source Sans 3", "Source Sans 3"),
            ),
            "queued",
            "registry",
            "ssp",
        ),
    ],
)
def test_a_rename_keeps_a_registered_id_only_when_that_is_unambiguous(
    registry: dict, status: str, reason: str, family_id: str
) -> None:
    gf = cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        source="gf_history",
        auto=True,
    )
    result = merged(cands=[gf], registry=registry)
    got = outcome(result, gf.alias)
    assert (got.status, got.reason, got.family_id) == (status, reason, family_id)
    if status == "queued":
        [item] = result.queue
        assert item.current == (gf.target if family_id == "ssp" else None)
        after = apply_rulings(result, {item.id: Decision(True, DAY)})
        if item.current is not None:
            assert {(r.alias, r.family_id, r.detail) for r in new_rows(after)} == {
                ("Source Sans Pro", "ssp", ""),
                ("Source Sans 3", "ssp", "current"),
            }
    if status == "accepted":
        assert {(r.alias, r.ns, r.family_id, r.detail) for r in new_rows(result)} == {
            ("Source Sans Pro", "gf-family", family_id, ""),
            ("Source Sans 3", "font-name", family_id, "current"),
        }
        # until the universe stage reads those rows, both names map to this run's family
        idx = build_index(result.rows, FAMILIES).index()
        assert idx[("gf-family", "sourcesanspro")][0] == "source-sans-3"
        assert idx[("gf-family", "sourcesans3")][0] == "source-sans-3"


def test_a_rename_never_folds_a_live_family_under_the_old_name() -> None:
    fams = (*FAMILIES, fam("ssp", "SSP Legacy", k("fontist-formula", "ssp-legacy")))
    gf = cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        source="gf_history",
        auto=True,
    )
    result = merged(
        cands=[gf], families=fams, registry=reg(("ssp", "Source Sans Pro", "Source Sans Pro"))
    )
    got = outcome(result, gf.alias)
    assert (got.status, got.reason) == ("queued", "other-family")


def test_a_row_for_a_family_missing_this_run_still_holds_its_alias() -> None:
    """The index leaves such a row out, but merge must not add a second family for its key."""
    table = AliasTable.from_rows([row("Source Sans Pro", "gf-family", "gone-sans")])
    gf = cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        source="gf_history",
        auto=True,
    )
    result = merged(table, [gf])  # used to raise AliasError: "maps to 2 families"
    got = outcome(result, gf.alias)
    assert (got.status, got.reason) == ("queued", "conflict")
    assert new_rows(result, table) == set()
    [item] = result.queue
    assert "gone-sans" in item.note
    after = apply_rulings(result, {item.id: Decision(True, DAY)})  # the owner may still move it
    assert [r.family_id for r in AliasTable.from_rows(after.rows).lookup(gf.alias)] == [
        "source-sans-3"
    ]
    # the same for the new name's row when a rename keeps an old id
    table = AliasTable.from_rows([row("Source Sans 3", "font-name", "gone-sans")])
    registry = reg(("ssp", "Source Sans Pro", "Source Sans Pro"))
    kept = outcome(merged(table, [gf], registry=registry), gf.alias)
    assert (kept.status, kept.reason, kept.family_id) == ("queued", "conflict", "ssp")


def test_a_stale_source_under_the_old_name_is_folded_into_the_old_id() -> None:
    """Google renamed the family, a cask still lists the old name: two live families.

    Accepting must keep the id registered under the old name (methodology §2),
    which the owner could not ask for if the item offered the new id.
    """
    fams = (*FAMILIES, fam("ssp", "Source Sans Pro", k("brew-cask", "font-source-sans-pro")))
    gf = cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        source="gf_history",
        auto=True,
    )
    registry = reg(("ssp", "Source Sans Pro", "Source Sans Pro"))
    result = merged(cands=[gf], families=fams, registry=registry)
    [item] = result.queue
    assert (item.reason, item.family_id, item.current) == ("other-family", "ssp", gf.target)
    after = apply_rulings(result, {item.id: Decision(True, DAY)})
    assert {(r.alias, r.ns, r.family_id, r.detail) for r in new_rows(after)} == {
        ("Source Sans Pro", "gf-family", "ssp", ""),
        ("Source Sans 3", "font-name", "ssp", "current"),
    }
    # rejecting it says the new name is not that family: the old name stays ssp's
    no = apply_rulings(result, {item.id: Decision(False, DAY)})
    assert {(r.alias, r.ns, r.family_id, r.relation) for r in new_rows(no)} == {
        ("Source Sans 3", "font-name", "ssp", "distinct")
    }
    table = AliasTable.from_rows(no.rows)
    assert outcome(merged(table, [gf], families=fams, registry=registry), gf.alias).status == (
        "blocked"
    )
    assert build_index(no.rows, fams).index()[("gf-family", "sourcesanspro")][0] == "ssp"
    # an old name registered to one family but now held by another is asked plainly
    other = (*FAMILIES, fam("nimbus", "Source Sans Pro", k("fontist-formula", "nimbus")))
    [plain] = merged(cands=[gf], families=other, registry=registry).queue
    assert (plain.reason, plain.family_id, plain.current) == (
        "other-family",
        "source-sans-3",
        None,
    )


def test_two_renames_that_keep_different_ids_for_one_name_wait_for_the_owner() -> None:
    """Google folded two old families into one: which id survives is the owner's call."""
    registry = reg(
        ("source-sans-pro", "Source Sans Pro", "Source Sans Pro"),
        ("source-sans-variable", "Source Sans Variable", "Source Sans Variable"),
    )
    target = k("gf-family", "Source Sans 3")
    cands = [
        cand(k("gf-family", old), target, source="gf_history", auto=True)
        for old in ("Source Sans Pro", "Source Sans Variable")
    ]
    result = merged(cands=cands, registry=registry)  # used to raise "maps to 2 families"
    assert {(q.reason, q.family_id) for q in result.queue} == {
        ("competing", "source-sans-pro"),
        ("competing", "source-sans-variable"),
    }
    assert new_rows(result) == set()


def test_merge_keeps_its_frozen_signature() -> None:
    table = AliasTable.from_rows([row("source-sans-pro", "fs-id", "source-sans-3")])
    c = cand(k("npm", "@fontsource/source-sans-pro"), k("fs-id", "source-sans-pro"), "package")
    rows, queue = merge(table, [c], DEFAULT_AUTO_RULES)  # targets resolve through the table
    assert rows == list(table.rows)
    assert queue == [c]


def test_sibling_rules_become_distinct_rows() -> None:
    fams = (
        *FAMILIES,
        fam("fira-code", "Fira Code", k("gf-family", "Fira Code")),
        fam("fira-sans", "Fira Sans", k("gf-family", "Fira Sans")),
        fam("inter-tight", "Inter Tight", k("gf-family", "Inter Tight")),
        fam("noto-sans", "Noto Sans", k("gf-family", "Noto Sans")),
        fam("noto-sans-jp", "Noto Sans JP", k("gf-family", "Noto Sans JP")),
    )
    result = merged(families=fams)
    blocks = {(r.alias, r.family_id, r.detail) for r in result.rows if r.relation == "distinct"}
    assert blocks == {
        ("Roboto Slab", "roboto", SIBLING_DETAIL),
        ("Roboto", "roboto-slab", ""),
        ("Fira Code", "fira-sans", SIBLING_DETAIL),
        ("Fira Sans", "fira-code", SIBLING_DETAIL),
        ("Inter Tight", "inter", SIBLING_DETAIL),
        ("Inter", "inter-tight", ""),
        ("Noto Sans JP", "noto-sans", SIBLING_DETAIL),
        ("Noto Sans", "noto-sans-jp", ""),
    }
    assert {(r.ns, r.source, r.reviewed_by) for r in result.rows} == {
        ("font-name", "rule:sibling", "auto:sibling")
    }
    # a table that already has them keeps its rows (and their dates)
    again = merge_detailed(
        AliasTable.from_rows(result.rows),
        (),
        DEFAULT_AUTO_RULES,
        families=fams,
        today=date(2026, 11, 3),
    )
    assert again.rows == result.rows


def test_an_exact_rejection_does_not_stand_in_for_a_sibling_block() -> None:
    rejected = row("Roboto Slab", "almanac-name", "roboto", "distinct", "rejected")
    result = merged(AliasTable.from_rows([rejected]))
    assert ("Roboto Slab", "font-name", "roboto", "distinct", SIBLING_DETAIL) in {
        astuple(r)[:5] for r in result.rows
    }
    assert Blocks.of(result.rows).blocks(k("deb-pkg", "fonts-roboto-slab"), "roboto")
    # an exact row does stand in for an exact sibling row
    exact = row("Roboto", "gf-family", "roboto-slab", "distinct", "rejected")
    again = merged(AliasTable.from_rows([exact]))
    assert [r for r in again.rows if r.family_id == "roboto-slab"] == [exact]


# --- owner rulings ----------------------------------------------------------------------------


def write_ruling(paths: Paths, day: str, text: str) -> None:
    folder = paths.reviews / "aliases"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.toml").write_text(text, encoding="utf-8")


REVIEW = [
    cand(k("npm", "@fontsource/source-sans-pro"), k("fs-id", "source-sans-3"), "package"),
    cand(
        k("font-name", "Source Sans Variable"),
        k("gf-family", "Source Sans 3"),
        "build",
        detail="variable",
    ),
    cand(k("font-name", "Sorce Sans"), k("gf-family", "Source Sans 3")),
    cand(k("font-name", "SSP"), k("gf-family", "Nowhere")),
]


def ids_by_alias(result) -> dict[str, str]:
    return {q.alias.key: q.id for q in result.queue}


def test_rulings_accept_reject_and_correct(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    result = merged(cands=REVIEW)
    ids = ids_by_alias(result)
    write_ruling(
        paths,
        "2026-10-05",
        f"""
[batch-1]
choice = "a"
recommended = true
ruling = "Accept both."
reason = "Test."
items = ["{ids["@fontsource/source-sans-pro"]}", "{ids["Source Sans Variable"]}"]

[{ids["Sorce Sans"]}]
choice = "a"
recommended = true
ruling = "Accept."
reason = "Test."

[{ids["SSP"]}]
choice = "c"
recommended = false
ruling = "One by one."
reason = "Test."
""",
    )
    write_ruling(
        paths,
        "2026-10-06",
        f"""
[{ids["Sorce Sans"]}]
choice = "b"
recommended = false
ruling = "Reject: a typo, not a name anyone ships."
reason = "Test; a later file wins."

[{ids["SSP"]}]
choice = "a"
recommended = false
ruling = "Accept as Source Code Pro."
reason = "Test."
family_id = "source-code-pro"
""",
    )
    decisions = load_alias_rulings(paths)
    assert decisions[ids["Sorce Sans"]] == Decision(False, date(2026, 10, 6))
    after = apply_rulings(result, decisions)
    assert after.queue == ()
    added = {(r.alias, r.family_id, r.relation, r.detail, r.reviewed_by) for r in new_rows(after)}
    assert added == {
        ("@fontsource/source-sans-pro", "source-sans-3", "package", "", "owner:2026-10-05"),
        ("Source Sans Variable", "source-sans-3", "build", "variable", "owner:2026-10-05"),
        ("Sorce Sans", "source-sans-3", "distinct", "rejected", "owner:2026-10-06"),
        ("SSP", "source-code-pro", "rename", "", "owner:2026-10-06"),
    }
    statuses = {o.candidate.alias.key: o.status for o in after.outcomes}
    assert statuses["Sorce Sans"] == "rejected"
    assert statuses["SSP"] == "accepted"
    # next month: the rejection is a block, so that proposal is dropped, not asked again;
    # the item whose target names no family is settled by its ruling, read every run
    again = merged(AliasTable.from_rows(after.rows), REVIEW)
    assert [q.alias.key for q in again.queue] == ["SSP"]
    assert apply_rulings(again, decisions).queue == ()
    assert outcome(again, k("font-name", "Sorce Sans")).status == "blocked"


def test_an_accepted_conflict_replaces_the_old_row() -> None:
    table = AliasTable.from_rows([row("SSP", "font-name", "source-code-pro")])
    result = merged(table, [cand(k("font-name", "ssp"), k("gf-family", "Source Sans 3"))])
    [item] = result.queue
    assert item.reason == "conflict"
    after = apply_rulings(result, {item.id: Decision(True, date(2026, 10, 5))})
    assert (
        AliasTable.from_rows(after.rows).lookup(k("font-name", "SSP"))[0].family_id
        == "source-sans-3"
    )


def test_rulings_that_contradict_themselves_raise() -> None:
    result = merged(
        cands=[
            cand(k("font-name", "Fira"), k("gf-family", "Inter")),
            cand(k("font-name", "Fira"), k("gf-family", "Roboto")),
        ]
    )
    yes = Decision(True, date(2026, 10, 5))
    with pytest.raises(AliasError, match="two families"):
        apply_rulings(result, dict.fromkeys((q.id for q in result.queue), yes))
    bad = Decision(True, date(2026, 10, 5), relation="ineligible", detail="nonsense")
    with pytest.raises(AliasError, match="ineligible reason"):
        apply_rulings(result, {result.queue[0].id: bad})


def test_accepting_a_block_that_a_sibling_row_already_covers() -> None:
    c = cand(k("npm", "@fontsource/roboto-slab"), k("gf-family", "Roboto"), "distinct")
    result = merged(cands=[c])
    [item] = result.queue
    after = apply_rulings(result, {item.id: Decision(True, DAY)})  # used to raise AliasError
    assert (
        "@fontsource/roboto-slab",
        "roboto",
        "distinct",
    ) in {(r.alias, r.family_id, r.relation) for r in after.rows}


def test_accepting_a_kept_id_checks_the_new_name_against_blocks_too() -> None:
    fams = (*FAMILIES, fam("ssp", "Source Sans Pro", k("brew-cask", "font-source-sans-pro")))
    gf = cand(
        k("gf-family", "Source Sans Pro"),
        k("gf-family", "Source Sans 3"),
        source="gf_history",
        auto=True,
    )
    result = merged(
        cands=[gf], families=fams, registry=reg(("ssp", "Source Sans Pro", "Source Sans Pro"))
    )
    [item] = result.queue
    no = row("Source Sans 3", "fot-name", "ssp", "distinct", "rejected")
    blocked = replace(result, rows=(*result.rows, no))
    with pytest.raises(AliasError, match="forbids Source Sans 3 -> ssp"):
        apply_rulings(blocked, {item.id: Decision(True, DAY)})


def test_an_accepted_item_without_a_family_stays_queued() -> None:
    result = merged(cands=[cand(k("font-name", "SSP"), k("gf-family", "Nowhere"))])
    after = apply_rulings(result, {result.queue[0].id: Decision(True, date(2026, 10, 5))})
    [item] = after.queue
    assert "family_id" in item.note


ANSWER = 'choice = "a"\nrecommended = true\nruling = "Accept."\nreason = "Test."\n'


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("2026-10-05", f'[x]\n{ANSWER}items = "A-1"\n', "list of queue ids"),
        ("2026-10-05", f'[x]\n{ANSWER}items = ["A-1", "A-2"]\nfamily_id = "x"\n', "single item"),
        ("2026-10-05", f"[x]\n{ANSWER}family_id = 3\n", "must be strings"),
        ("2026-10-05", "x = 1\n", "2026-10-05.toml"),  # the rulings schema: not a table
        ("2026-10-05", '[A-1]\nchoice = "a"\nrecommended = true\n', "2026-10-05.toml"),
        ("2026-10-05", "[A-1\n", "2026-10-05.toml"),  # not TOML
        ("october", f"[x]\n{ANSWER}", "october"),
    ],
)
def test_bad_rulings_files_name_the_problem(
    tmp_path: Path, name: str, text: str, message: str
) -> None:
    paths = Paths.for_root(tmp_path)
    write_ruling(paths, name, text)
    with pytest.raises(AliasError, match=message):
        load_alias_rulings(paths)


def test_no_rulings_folder_means_no_decisions(tmp_path: Path) -> None:
    assert load_alias_rulings(Paths.for_root(tmp_path)) == {}


# --- mining and the stage ---------------------------------------------------------------------


@dataclass(frozen=True)
class FakeMiner:
    name: ClassVar[str] = "fake"
    found: tuple[AliasCandidate, ...] = ()
    fail: bool = False

    def mine(self, ctx: miners_pkg.MineContext) -> list[AliasCandidate]:
        if self.fail:
            raise RuntimeError("upstream is down")
        return list(self.found)


def named(name: str, **kw: object) -> FakeMiner:
    return type(name, (FakeMiner,), {"name": name})(**kw)


def repo(tmp_path: Path, families=FAMILIES, table=()) -> Paths:
    paths = Paths.for_root(tmp_path / "repo", store=None, raw_root=tmp_path / "raw")
    write_aliases(table, paths.aliases_csv)
    u = Universe(
        families={
            f.id: Family(f.id, f.name, f.keys, ("google_metadata",), OLD, f.name) for f in families
        },
        unmapped=(),
    )
    stageio.dump_stage(paths, "universe", u)
    return paths


def context(paths: Paths, *, ids=None, **options: object) -> StageContext:
    return StageContext(
        paths=paths,
        config=None,  # type: ignore[arg-type] - the stage reads no config
        state=State(ids=ids or {}),
        run_date=DAY,
        store=None,
        fetcher=None,
        log=logging.getLogger("test.aliases"),
        options=RunOptions(**options),  # type: ignore[arg-type]
    )


@pytest.fixture
def miners(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeMiner]:
    found: dict[str, FakeMiner] = {}
    monkeypatch.setattr(miners_pkg, "discover", lambda: dict(sorted(found.items())))
    return found


GF = cand(
    k("gf-family", "Source Sans Pro"),
    k("gf-family", "Source Sans 3"),
    source="gf_history",
    auto=True,
)
NPM = cand(
    k("npm", "@fontsource/source-sans-pro"),
    k("fs-id", "source-sans-3"),
    "package",
    source="fontsource_legacy",
)


def test_mine_all_writes_each_miners_seeds_and_reads_every_seed_file(
    tmp_path: Path, miners: dict[str, FakeMiner]
) -> None:
    paths = repo(tmp_path)
    hand = cand(k("font-name", "Sorce Sans"), k("gf-family", "Source Sans 3"), source="hand")
    write_seeds([hand], paths.alias_seeds / "hand.csv")
    miners["gf_history"] = named("gf_history", found=(GF,))
    miners["fontsource_legacy"] = named("fontsource_legacy", found=(NPM, NPM))
    assert mine_all(context(paths)) == sorted([GF, NPM, hand])
    assert sorted(p.name for p in paths.alias_seeds.iterdir()) == [
        "fontsource_legacy.csv",
        "gf_history.csv",
        "hand.csv",
    ]
    # --only runs just those miners; every seed file is still read
    miners["gf_history"] = named("gf_history", found=())
    assert mine_all(context(paths, only=("fontsource_legacy",))) == sorted([GF, NPM, hand])
    # replay runs no miner at all
    miners["fontsource_legacy"] = named("fontsource_legacy", fail=True)
    assert mine_all(context(paths, from_snapshots=DAY)) == sorted([GF, NPM, hand])


def test_a_failed_miner_keeps_its_committed_seeds(
    tmp_path: Path, miners: dict[str, FakeMiner], caplog: pytest.LogCaptureFixture
) -> None:
    paths = repo(tmp_path)
    write_seeds([NPM], paths.alias_seeds / "fontsource_legacy.csv")
    miners["fontsource_legacy"] = named("fontsource_legacy", fail=True)
    ctx = context(paths)
    aliases.run(ctx)
    assert "miner fontsource_legacy failed" in caplog.text
    doc = jsonio.load(paths.queues / aliases.QUEUE_FILE)
    assert doc["failed_miners"] == ["fontsource_legacy"]
    assert [i["alias"]["key"] for i in doc["items"]] == ["@fontsource/source-sans-pro"]


def test_a_candidate_must_come_from_its_own_miner(
    tmp_path: Path, miners: dict[str, FakeMiner]
) -> None:
    paths = repo(tmp_path)
    miners["oldlib"] = named("oldlib", found=(GF,))  # claims to be gf_history
    with pytest.raises(AliasError, match="miner oldlib proposed candidates as"):
        mine_all(context(paths))
    miners.clear()
    write_seeds([GF], paths.alias_seeds / "hand.csv")
    with pytest.raises(AliasError, match="rows name source"):
        mine_all(context(paths))


def test_stage_writes_index_queue_and_candidates_and_never_the_table(
    tmp_path: Path, miners: dict[str, FakeMiner], capsys: pytest.CaptureFixture[str]
) -> None:
    paths = repo(tmp_path)
    miners["gf_history"] = named("gf_history", found=(GF,))
    miners["fontsource_legacy"] = named("fontsource_legacy", found=(NPM,))
    table_before = paths.aliases_csv.read_bytes()
    ctx = context(paths)
    aliases.run(ctx)
    assert paths.aliases_csv.read_bytes() == table_before
    idx = index_of(stageio.load_stage(paths, "alias_index"))
    assert idx[("gf-family", "sourcesanspro")] == ("source-sans-3", "rename", "")
    assert idx[("almanac-name", "sourcesanspro")] == ("source-sans-3", "rename", "")
    assert ("npm", "@fontsource/sourcesanspro") not in idx  # queued, not yet accepted
    lines = list(jsonio.iter_jsonl(paths.stage / aliases.CANDIDATES_FILE))
    assert [(line["source"], line["status"]) for line in lines] == [
        ("gf_history", "accepted"),
        ("fontsource_legacy", "queued"),
    ]
    first = {p: p.read_bytes() for p in paths.stage.rglob("*") if p.is_file()}
    aliases.run(ctx)
    assert {p: p.read_bytes() for p in paths.stage.rglob("*") if p.is_file()} == first

    assert aliases.cmd_queue(ctx, count=True) == 0
    assert capsys.readouterr().out == "1\n"
    assert aliases.cmd_queue(ctx) == 0
    out = capsys.readouterr().out
    assert "review" in out
    assert "npm:@fontsource/source-sans-pro -> source-sans-3" in out
    assert "[package; fontsource_legacy]" in out


def test_apply_writes_accepted_rows_and_is_idempotent(
    tmp_path: Path, miners: dict[str, FakeMiner], capsys: pytest.CaptureFixture[str]
) -> None:
    paths = repo(tmp_path)
    miners["gf_history"] = named("gf_history", found=(GF,))
    miners["fontsource_legacy"] = named("fontsource_legacy", found=(NPM,))
    ctx = context(paths)
    aliases.run(ctx)
    [item] = jsonio.load(paths.queues / aliases.QUEUE_FILE)["items"]
    write_ruling(
        paths,
        "2026-10-05",
        f'[{item["id"]}]\nchoice = "a"\nrecommended = true\nruling = "Accept."\nreason = "Test."\n',
    )
    # the auto-accepted rename and three sibling rows (Roboto/Roboto Slab, Inter Tight)
    assert jsonio.load(paths.queues / aliases.QUEUE_FILE)["counts"]["unapplied"] == 4
    assert aliases.cmd_apply(ctx) == 0
    assert "0 still queued" in capsys.readouterr().out
    assert jsonio.load(paths.queues / aliases.QUEUE_FILE)["counts"]["unapplied"] == 0
    table = AliasTable.from_rows(load_aliases(paths.aliases_csv))
    assert paths.aliases_csv.read_text(encoding="utf-8").startswith(",".join(COLUMNS) + "\n")
    assert [r.reviewed_by for r in table.lookup(k("npm", "@fontsource/source-sans-pro"))] == [
        "owner:2026-10-05"
    ]
    assert [r.reviewed_by for r in table.lookup(k("gf-family", "Source Sans Pro"))] == [
        "auto:gf_history_rename"
    ]
    assert aliases.cmd_queue(ctx, count=True) == 0
    assert capsys.readouterr().out == "0\n"
    before = paths.aliases_csv.read_bytes()
    aliases.run(ctx)
    assert aliases.cmd_apply(ctx) == 0
    assert paths.aliases_csv.read_bytes() == before


def test_the_stage_needs_the_universe_and_the_modes_need_a_run(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path)
    ctx = context(paths, from_snapshots=DAY)
    with pytest.raises(FileNotFoundError, match="run `tff-catalog universe` first"):
        aliases.run(ctx)
    assert aliases.cmd_queue(ctx) == 1
    assert aliases.cmd_apply(ctx) == 1


# --- gate A through tff_catalog.reviews ----------------------------------------------------------


def test_gate_a_questions_batch_plain_proposals_and_record_the_answers(
    tmp_path: Path, miners: dict[str, FakeMiner]
) -> None:
    """``questions --gate A`` then ``rulings apply``: the answers settle the queue."""
    paths = repo(tmp_path, table=[row("SSP", "font-name", "source-code-pro")])
    with pytest.raises(FileNotFoundError):
        aliases.questions(paths)  # reviews tells the owner to run the stage first
    target = k("gf-family", "Source Sans 3")
    miners["fontsource_legacy"] = named("fontsource_legacy", found=(NPM,))
    miners["homebrew"] = named(
        "homebrew",
        found=(cand(k("brew-cask", "font-source-sans-pro"), target, "package", source="homebrew"),),
    )
    miners["oldlib"] = named(
        "oldlib",
        found=(
            cand(k("font-name", "Source Sans Variable"), target, "build", detail="variable"),
            cand(k("font-name", "SSP"), target),  # a row maps it elsewhere: one by one
        ),
    )
    ctx = context(paths)
    aliases.run(ctx)
    qs = aliases.questions(paths)
    assert reviews.check_questions("A", qs, "test") == qs
    by_reason = {q.recommended: [] for q in qs}
    for q in qs:
        by_reason[q.recommended].append(q)
    assert sorted(len(v) for v in by_reason.values()) == [1, 3]
    [conflict] = by_reason[None]
    assert "SSP" in conflict.text
    assert "a row already maps the alias to another family" in conflict.text
    assert {q.options for q in qs} == {aliases.QUESTION_OPTIONS}
    assert reviews.questions(paths, "A") == qs  # found through reviews.QUEUE_SOURCES

    plan = reviews.plan(reviews.questions(paths, "A"))
    [group] = [g for g in plan if isinstance(g, reviews.Group)]
    assert len(group.members) == 3
    answers = tmp_path / "answers.toml"
    answers.write_text(
        f'gate = "A"\nday = "2026-10-05"\n\n'
        f'[{group.question.id}]\nchoice = "a"\nreason = "Test: the owner accepts the batch."\n\n'
        f'[{conflict.id}]\nchoice = "b"\nreason = "Test: SSP stays Source Code Pro."\n',
        encoding="utf-8",
    )
    reviews.apply_answers(paths, answers)
    assert len(load_alias_rulings(paths)) == 4
    aliases.run(ctx)
    assert reviews.questions(paths, "A") == []
    assert aliases.cmd_queue(ctx, count=True) == 0
    idx = index_of(stageio.load_stage(paths, "alias_index"))
    assert idx[("brew-cask", "fontsourcesanspro")] == ("source-sans-3", "package", "")
    assert idx[("font-name", "ssp")] == ("source-code-pro", "rename", "")
    assert aliases.cmd_apply(ctx) == 0
    rows = {(r.alias, r.family_id, r.relation, r.detail) for r in load_aliases(paths.aliases_csv)}
    assert ("SSP", "source-sans-3", "distinct", "rejected") in rows
    assert ("Source Sans Variable", "source-sans-3", "build", "variable") in rows


def test_research_keeps_an_item_queued_and_its_question_open(
    tmp_path: Path, miners: dict[str, FakeMiner], capsys: pytest.CaptureFixture[str]
) -> None:
    paths = repo(tmp_path)
    miners["fontsource_legacy"] = named("fontsource_legacy", found=(NPM,))
    ctx = context(paths)
    aliases.run(ctx)
    [q] = aliases.questions(paths)
    write_ruling(
        paths,
        "2026-10-05",
        f'[{q.id}]\nchoice = "c"\nrecommended = false\nruling = "Research."\nreason = "Test."\n',
    )
    aliases.run(ctx)
    assert [x.id for x in reviews.questions(paths, "A")] == [q.id]
    assert aliases.cmd_queue(ctx, count=True) == 0
    assert capsys.readouterr().out == "1\n"
