"""Stage "universe" (milestone-1 step 4): families, stable ids, drops and the report.

Every record here is synthetic: invented family names, reserved ``.example`` hosts.
"""

import logging
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from tests.helpers import synth

from tff_catalog import jsonio, stageio, universe
from tff_catalog.aliases import COLUMNS, AliasError, AliasRow, AliasTable
from tff_catalog.paths import Paths
from tff_catalog.records import (
    DROP_REASONS,
    Observation,
    SourceKey,
    UniverseRecord,
    write_jsonl,
)
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.universe import (
    Family,
    Universe,
    UniverseError,
    build_universe,
    family_drop,
    next_ids,
    report,
)

DAY1 = date(2026, 10, 3)
DAY2 = date(2026, 11, 3)
NO_ALIASES = AliasTable(rows=())


def rec(source: str, ns: str, key: str, family: str, **kw: Any) -> UniverseRecord:
    return UniverseRecord(source=source, key=SourceKey(ns, key), family=family, **kw)


def alias(alias: str, ns: str, family_id: str, relation: str, detail: str = "") -> AliasRow:
    return AliasRow(alias, ns, family_id, relation, detail, "test", date(2026, 9, 1), "owner")


def table(*rows: AliasRow) -> AliasTable:
    return AliasTable(rows=rows)


def aster() -> list[UniverseRecord]:
    """One family listed by three sources under three keys."""
    return [
        rec("google_metadata", "gf-family", "Aster Sans", "Aster Sans"),
        rec(
            "fontsource",
            "fs-id",
            "aster-sans",
            "Aster Sans",
            urls=(("repository", "https://git.synth.example/aster"),),
        ),
        rec(
            "homebrew_casks",
            "brew-cask",
            "font-aster-sans",
            "Aster Sans",
            urls=(
                ("homepage", "https://aster.synth.example/"),
                ("repository", "https://git.synth.example/aster"),
            ),
        ),
    ]


def build(
    recs: list[UniverseRecord],
    aliases: AliasTable = NO_ALIASES,
    ids: dict[str, dict[str, Any]] | None = None,
    day: date = DAY1,
) -> Universe:
    return build_universe(recs, aliases, ids or {}, run_date=day)


def only(u: Universe) -> Family:
    assert len(u.families) == 1, sorted(u.families)
    return next(iter(u.families.values()))


# --- families, memberships and URLs ----------------------------------------------------------------


def test_one_family_per_name_with_memberships_and_urls() -> None:
    fam = only(build(aster()))
    assert fam == Family(
        id="aster-sans",
        family="Aster Sans",
        keys=(
            SourceKey("brew-cask", "font-aster-sans"),
            SourceKey("fs-id", "aster-sans"),
            SourceKey("gf-family", "Aster Sans"),
        ),
        sources=("fontsource", "google_metadata", "homebrew_casks"),
        first_seen=DAY1,
        minted_from="Aster Sans",
        drop=None,
        urls=(
            ("homepage", "https://aster.synth.example/"),
            ("repository", "https://git.synth.example/aster"),
        ),
    )


def test_names_group_by_match_key() -> None:
    recs = [
        rec("google_metadata", "gf-family", "Birch Mono", "Birch Mono"),
        rec("homebrew_casks", "brew-cask", "font-birch-mono", "Birch-Mono"),
        rec("fontist", "fontist-formula", "birch_mono", "BIRCH MONO"),
    ]
    fam = only(build(recs))
    assert (fam.id, fam.family) == ("birch-mono", "Birch Mono")


def test_asserted_build_names_fold_patched_builds_into_the_original() -> None:
    recs = [
        rec(
            "nerdfonts",
            "nerd-folder",
            "BirchMono",
            "Birch Mono",
            names=(("BirchMono Nerd Font", "build"), ("Birch Mono Retro", "related")),
        ),
        rec("homebrew_casks", "brew-cask", "font-birch-mono-nerd-font", "BirchMono Nerd Font"),
        rec("homebrew_casks", "brew-cask", "font-birch-mono-retro", "Birch Mono Retro"),
    ]
    u = build(recs)
    assert sorted(u.families) == ["birch-mono", "birch-mono-retro"]  # "related" never folds
    assert u.families["birch-mono"].keys == (
        SourceKey("brew-cask", "font-birch-mono-nerd-font"),
        SourceKey("nerd-folder", "BirchMono"),
    )
    assert u.families["birch-mono"].family == "Birch Mono"  # nerdfonts outranks homebrew_casks


def test_a_nerd_build_name_never_renames_the_original_family() -> None:
    """The Nerd cask names its parent as the family, and a Nerd folder name that spells
    the original family ("FiraMono") demotes only records ranked no higher than its own."""
    recs = [
        rec("google_metadata", "gf-family", "Fira Mono", "Fira Mono"),
        rec("nerdfonts", "nerd-folder", "FiraMono", "Fira", names=(("FiraMono", "build"),)),
        rec(
            "homebrew_casks",
            "brew-cask",
            "font-fira-mono-nerd-font",
            "Fira",
            names=(("FiraMono Nerd Font", "build"),),
        ),
        rec("google_metadata", "gf-family", "Birch Mono", "Birch Mono"),
        rec(
            "homebrew_casks",
            "brew-cask",
            "font-birch-mono-nerd-font",
            "Birch Mono",
            names=(("BirchMono Nerd Font", "build"),),
        ),
    ]
    u = build(recs)
    assert {fid: f.family for fid, f in u.families.items()} == {
        "birch-mono": "Birch Mono",
        "fira-mono": "Fira Mono",
    }


def test_display_name_prefers_live_records_then_source_priority() -> None:
    recs = [
        rec("homebrew_casks", "brew-cask", "font-cobalt", "Cobalt"),
        rec("fontsource", "fs-id", "cobalt", "COBALT", status="deprecated"),
        rec("nerdfonts", "nerd-folder", "Cobalt", "cobalt"),
    ]
    assert only(build(recs)).family == "cobalt"  # live nerdfonts beats live homebrew_casks
    recs.append(rec("google_metadata", "gf-family", "Cobalt", "Cobalt", status="queued"))
    assert only(build(recs)).family == "cobalt"  # live beats queued, whatever the source


def test_input_order_and_duplicates_do_not_matter() -> None:
    recs = [*aster(), *synth.source_records(synth.UNIVERSE, synth.DAYS[-1])]
    recs = [r for r in recs if isinstance(r, UniverseRecord)]
    first = stageio.encode(build(recs))
    doubled = recs + recs[:5]
    shuffled = [doubled[i] for i in np.random.default_rng(7).permutation(len(doubled))]
    assert stageio.encode(build(shuffled)) == first


# --- stable ids ----------------------------------------------------------------------------------


def test_ids_identical_across_two_runs() -> None:
    recs = [r for r in synth.source_records(synth.UNIVERSE, DAY1) if isinstance(r, UniverseRecord)]
    a, b = build(recs), build(list(reversed(recs)))
    assert jsonio.canonical_bytes(stageio.encode(a)) == jsonio.canonical_bytes(stageio.encode(b))
    assert next_ids(a, {}) == next_ids(b, {})
    assert report(a, {}) == report(b, {})

    # After the run is merged, a later run keeps every id, name and first_seen.
    merged = next_ids(a, {})
    later = build(recs, ids=merged, day=DAY2)
    assert later.families == a.families
    assert next_ids(later, merged) == merged


def test_new_family_gets_a_new_id_and_old_ids_stay() -> None:
    day0, day2 = synth.DAYS[0], synth.DAYS[-1]
    recs0 = [r for r in synth.source_records(synth.UNIVERSE, day0) if isinstance(r, UniverseRecord)]
    recs2 = [r for r in synth.source_records(synth.UNIVERSE, day2) if isinstance(r, UniverseRecord)]
    registry = next_ids(build(recs0, day=day0), {})
    u = build(recs2, ids=registry, day=day2)
    new = set(u.families) - set(registry)
    newest = synth.families()[-1]
    assert new == {newest.slug}
    assert u.families[newest.slug].first_seen == day2
    assert all(u.families[fid].first_seen == day0 for fid in registry)
    # ids match the stub pipeline's (slug of the name) and the icon font is kept, dropped
    assert set(u.eligible()) == {f.slug for f in synth.families()}
    assert u.families["synth-icons"].drop == "icon"


def test_registry_is_append_only() -> None:
    registry = {
        "gone-sans": {
            "family": "Gone Sans",
            "minted_from": "Gone Sans",
            "first_seen": "2025-01-01",
        },
        "aster-sans": {"family": "Aster Sans", "minted_from": "Aster", "first_seen": "2025-02-01"},
    }
    u = build(aster(), ids=registry)
    fam = only(u)
    assert (fam.id, fam.minted_from, fam.first_seen) == ("aster-sans", "Aster", date(2025, 2, 1))
    assert next_ids(u, registry) == registry  # nothing new, nothing removed
    assert "gone-sans" not in u.families


def test_minting_avoids_taken_ids_whatever_the_input_order() -> None:
    registry = {"lodz-sans": {"family": "Lodz Grotesk", "minted_from": "Lodz Sans"}}
    # "Lodz Sans" is the registry family's first name, so it folds there. match_key keeps
    # accents, so the other two are new families whose slug is taken: -2 and -3, by name.
    recs = [
        rec("fontsource", "fs-id", "lodz-sans", "Łódź Sans"),
        rec("fontsource", "fs-id", "lodz-sans-x", "Lodz-Sans X"),
        rec("homebrew_casks", "brew-cask", "font-lodz", "Lódz Sans"),
        rec("homebrew_casks", "brew-cask", "font-lodz-sans", "Lodz Sans"),
    ]
    for order in (recs, list(reversed(recs))):
        u = build(order, ids=registry)
        assert {fid: f.family for fid, f in u.families.items()} == {
            "lodz-sans": "Lodz Sans",
            "lodz-sans-2": "Lódz Sans",
            "lodz-sans-3": "Łódź Sans",
            "lodz-sans-x": "Lodz-Sans X",
        }


@pytest.mark.parametrize(
    ("registry", "message"),
    [
        ({"Aster Sans": {"family": "Aster Sans"}}, "not a family id"),
        ({"aster-sans": "Aster Sans"}, "must map to an object"),
        ({"aster-sans": {"family": ["Aster Sans"]}}, "family must be a string"),
        ({"aster-sans": {"minted_from": None}}, "minted_from must be a string"),
        ({"aster-sans": {"first_seen": "03/09/2026"}}, "first_seen must be YYYY-MM-DD"),
        ({"aster-sans": {"first_seen": "20260903"}}, "first_seen must be YYYY-MM-DD"),
    ],
)
def test_a_malformed_registry_is_refused(registry: dict[str, Any], message: str) -> None:
    # A bad first_seen would otherwise read as the run date and move every month.
    with pytest.raises(UniverseError, match=message):
        build(aster(), ids=registry)


def test_a_registry_entry_missing_fields_is_completed_not_changed() -> None:
    registry: dict[str, dict[str, Any]] = {"aster-sans": {"family": "Aster Sans"}}
    u = build(aster(), ids=registry)
    assert only(u).first_seen == DAY1
    assert next_ids(u, registry) == {
        "aster-sans": {
            "family": "Aster Sans",
            "minted_from": "Aster Sans",
            "first_seen": "2026-10-03",
        }
    }
    assert registry == {"aster-sans": {"family": "Aster Sans"}}  # the input is never modified


# Small pools, so that names, keys, rows and registry entries collide often.
_NAMES = ("Alder", "Alder Sans", "alder-sans", "Birch", "Birch Mono", "Cedar", "Cedar NF", "Łódź")
_SOURCE_NS = {
    "fontist": "fontist-formula",
    "fontsource": "fs-id",
    "foundries": "foundry-family",
    "google_metadata": "gf-family",
    "homebrew_casks": "brew-cask",
    "nerdfonts": "nerd-folder",
}
_KEYS = ("k1", "k2", "Alder Sans", "Birch", "Cedar")
_IDS = ("alder", "alder-sans", "birch", "cedar", "cedar-2")
_records = st.builds(
    lambda source, key, family, names, drop: rec(
        source, _SOURCE_NS[source], key, family, names=names, drop=drop
    ),
    st.sampled_from(sorted(_SOURCE_NS)),
    st.sampled_from(_KEYS),
    st.sampled_from(_NAMES),
    st.lists(
        st.tuples(st.sampled_from(_NAMES), st.sampled_from(("rename", "build", "related"))),
        max_size=2,
    ).map(tuple),
    st.sampled_from((None, None, "icon", "proprietary")),
)
_rows = st.builds(
    lambda name, ns, fid, relation, detail: alias(
        name,
        ns,
        "" if relation == "ineligible" else fid,
        relation,
        "proprietary" if relation == "ineligible" else detail,
    ),
    st.sampled_from(_KEYS + _NAMES),
    st.sampled_from(sorted({*_SOURCE_NS.values(), "font-name"})),
    st.sampled_from(_IDS),
    st.sampled_from(("rename", "package", "ineligible", "bundle", "distinct")),
    st.sampled_from(("", "sibling")),
)
_registries = st.dictionaries(
    st.sampled_from(_IDS),
    st.builds(
        lambda family, first, day: {"family": family, "minted_from": first, "first_seen": day},
        st.sampled_from(_NAMES),
        st.sampled_from(_NAMES),
        st.sampled_from(("2025-01-01", "2026-05-05")),
    ),
    max_size=3,
)


@settings(max_examples=300, derandomize=True, deadline=None)
@given(
    st.lists(_records, min_size=1, max_size=8),
    st.lists(_rows, max_size=4),
    _registries,
    st.randoms(use_true_random=False),
)
def test_properties_order_every_key_once_and_the_next_run_agrees(
    recs: list[UniverseRecord],
    rows: list[AliasRow],
    registry: dict[str, dict[str, Any]],
    rnd: Any,
) -> None:
    try:
        aliases = AliasTable.from_rows(rows)
    except AliasError:
        assume(False)
        return
    u = build(recs, aliases, ids=registry)
    shuffled = list(recs)
    rnd.shuffle(shuffled)
    assert build(shuffled, aliases, ids=registry) == u
    listed = [
        *(k for f in u.families.values() for k in f.keys),
        *(k for k, _ in u.excluded),
        *u.unmapped,
    ]
    assert sorted(listed) == sorted({r.key for r in recs})
    merged = next_ids(u, registry)
    for fid, entry in registry.items():  # append-only; minted_from and first_seen kept
        assert {**merged[fid], "family": entry["family"]} == entry
    if u.unmapped:
        return  # the stage fails, so this registry is never merged
    later = build(recs, aliases, ids=merged, day=DAY2)
    assert later == u
    assert next_ids(later, merged) == merged


# --- renames -------------------------------------------------------------------------------------

OLD = rec("fontsource", "fs-id", "old-quill", "Old Quill")
REGISTRY = {
    "old-quill": {"family": "Old Quill", "minted_from": "Old Quill", "first_seen": "2026-09-03"}
}


@pytest.mark.parametrize(
    ("renamed", "aliases"),
    [
        pytest.param(
            [
                rec(
                    "fontsource",
                    "fs-id",
                    "quill-sans",
                    "Quill Sans",
                    names=(("Old Quill", "rename"),),
                )
            ],
            NO_ALIASES,
            id="source-asserts-the-old-name",
        ),
        pytest.param(
            [rec("fontsource", "fs-id", "quill-sans", "Quill Sans")],
            table(alias("quill-sans", "fs-id", "old-quill", "rename")),
            id="alias-row-on-the-key",
        ),
        pytest.param(
            [rec("fontsource", "fs-id", "quill-sans", "Quill Sans")],
            table(alias("Quill Sans", "font-name", "old-quill", "rename")),
            id="alias-row-on-the-name",
        ),
    ],
)
def test_rename_keeps_id(renamed: list[UniverseRecord], aliases: AliasTable) -> None:
    first = build([OLD], day=date(2026, 9, 3))
    assert next_ids(first, {}) == REGISTRY

    u = build(renamed, aliases, ids=REGISTRY)
    fam = only(u)
    assert fam.id == "old-quill"
    assert (fam.family, fam.minted_from, fam.first_seen) == (
        "Quill Sans",
        "Old Quill",
        date(2026, 9, 3),
    )
    assert next_ids(u, REGISTRY) == {
        "old-quill": {
            "family": "Quill Sans",
            "minted_from": "Old Quill",
            "first_seen": "2026-09-03",
        }
    }
    text = report(u, REGISTRY)
    assert "| old-quill | Old Quill | Quill Sans |" in text  # the old name, for the alias table

    # A source still listing the old name folds into the same family.
    stale = rec("homebrew_casks", "brew-cask", "font-old-quill", "Old Quill")
    both = build([*renamed, stale], aliases, ids=next_ids(u, REGISTRY), day=DAY2)
    assert only(both).id == "old-quill"
    assert only(both).family == "Quill Sans"


def test_rename_keeps_id_when_both_names_appear_at_first_sighting() -> None:
    recs = [
        rec("fontsource", "fs-id", "quill-sans", "Quill Sans", names=(("old-quill", "rename"),)),
        rec("homebrew_casks", "brew-cask", "font-old-quill", "Old Quill"),
    ]
    fam = only(build(recs))
    assert (fam.id, fam.family, fam.minted_from) == ("quill-sans", "Quill Sans", "Quill Sans")


def test_asserted_names_never_merge_two_registry_families() -> None:
    registry = {
        "moss-sans": {
            "family": "Moss Sans",
            "minted_from": "Moss Sans",
            "first_seen": "2026-01-01",
        },
        "moss-grotesk": {"family": "Moss Grotesk", "minted_from": "Moss Grotesk"},
    }
    recs = [
        rec("fontsource", "fs-id", "moss-sans", "Moss Sans"),
        rec("nerdfonts", "nerd-folder", "MossG", "Moss Grotesk", names=(("Moss Sans", "rename"),)),
    ]
    u = build(recs, ids=registry)
    assert {fid: f.keys for fid, f in u.families.items()} == {
        "moss-grotesk": (SourceKey("nerd-folder", "MossG"),),
        "moss-sans": (SourceKey("fs-id", "moss-sans"),),
    }
    assert u.unmapped == ()
    # A new name tied to both registry families cannot be placed: it stays unmapped.
    both = rec(
        "fontist",
        "fontist-formula",
        "moss",
        "Moss Neo",
        names=(("Moss Sans", "rename"), ("Moss Grotesk", "rename")),
    )
    u = build([*recs, both], ids=registry)
    assert u.unmapped == (SourceKey("fontist-formula", "moss"),)
    assert sorted(u.families) == ["moss-grotesk", "moss-sans"]


def test_distinct_rows_keep_a_same_name_family_apart() -> None:
    registry = {
        "heath-sans": {
            "family": "Heath Sans",
            "minted_from": "Heath Sans",
            "first_seen": "2026-01-05",
        }
    }
    recs = [
        rec("google_metadata", "gf-family", "Heath Sans", "Heath Sans"),
        rec("foundries", "foundry-family", "Heath Sans (Other)", "Heath Sans"),
    ]
    rows = table(alias("Heath Sans (Other)", "foundry-family", "heath-sans", "distinct"))
    u = build(recs, rows, ids=registry)
    assert {fid: f.keys for fid, f in u.families.items()} == {
        "heath-sans": (SourceKey("gf-family", "Heath Sans"),),
        "heath-sans-2": (SourceKey("foundry-family", "Heath Sans (Other)"),),
    }
    # The next run, from the merged registry, keeps both apart with the same ids.
    merged = next_ids(u, registry)
    again = build(recs, rows, ids=merged, day=DAY2)
    assert {fid: f.keys for fid, f in again.families.items()} == {
        fid: f.keys for fid, f in u.families.items()
    }
    assert again.unmapped == ()
    # On a first run too, with no registry, the barred record gets a family of its own.
    fresh = build(recs, rows)
    assert {fid: f.keys for fid, f in fresh.families.items()} == {
        fid: f.keys for fid, f in u.families.items()
    }


def test_a_reused_old_name_goes_to_the_family_that_has_it_now() -> None:
    registry = {
        "garnet": {"family": "Garnet Pro", "minted_from": "Garnet", "first_seen": "2025-01-01"},
        "garnet-2": {"family": "Garnet", "minted_from": "Garnet", "first_seen": "2026-01-01"},
    }
    recs = [
        rec("fontsource", "fs-id", "garnet", "Garnet"),
        rec("fontsource", "fs-id", "garnet-pro", "Garnet Pro"),
    ]
    u = build(recs, ids=registry)
    assert {fid: f.family for fid, f in u.families.items()} == {
        "garnet": "Garnet Pro",
        "garnet-2": "Garnet",
    }


def test_a_split_family_keeps_its_builds_in_the_next_run() -> None:
    # A distinct row splits a same-name foundry family off Heath Sans; the Nerd build
    # reaches the family only through the name the nerdfonts record asserts.
    registry = {
        "heath-sans": {
            "family": "Heath Sans",
            "minted_from": "Heath Sans",
            "first_seen": "2026-01-05",
        }
    }
    recs = [
        rec("google_metadata", "gf-family", "Heath Sans", "Heath Sans"),
        rec("foundries", "foundry-family", "Heath Sans (Other)", "Heath Sans"),
        rec(
            "nerdfonts",
            "nerd-folder",
            "HeathSans",
            "Heath Sans",
            names=(("HeathSans Nerd Font", "build"),),
        ),
        rec("homebrew_casks", "brew-cask", "font-heath-sans-nerd-font", "HeathSans Nerd Font"),
    ]
    rows = table(alias("Heath Sans (Other)", "foundry-family", "heath-sans", "distinct"))
    u = build(recs, rows, ids=registry)
    placed = {fid: {k.key for k in f.keys} for fid, f in u.families.items()}
    assert placed == {
        "heath-sans": {"Heath Sans", "HeathSans", "font-heath-sans-nerd-font"},
        "heath-sans-2": {"Heath Sans (Other)"},
    }
    merged = next_ids(u, registry)
    assert build(recs, rows, ids=merged, day=DAY2) == u
    # On a first run, with no registry, too; and the run after it agrees.
    fresh = build(recs, rows)
    assert {fid: {k.key for k in f.keys} for fid, f in fresh.families.items()} == placed
    assert build(recs, rows, ids=next_ids(fresh, {}), day=DAY2).families == fresh.families


def test_of_two_split_families_sharing_a_name_the_first_seen_keeps_it() -> None:
    twin = {"family": "Heath Sans", "minted_from": "Heath Sans"}
    registry = {
        "heath-sans": {**twin, "first_seen": "2026-01-05"},
        "heath-sans-2": {**twin, "first_seen": "2026-06-01"},
    }
    recs = [
        rec("google_metadata", "gf-family", "Heath Sans", "Heath Sans"),
        rec("foundries", "foundry-family", "Heath Sans (Other)", "Heath Sans"),
        rec("homebrew_casks", "brew-cask", "font-heath-sans", "Heath Sans"),
    ]
    rows = table(
        alias("Heath Sans (Other)", "foundry-family", "heath-sans", "distinct"),
        alias("font-heath-sans", "brew-cask", "heath-sans-2", "distinct", "rejected"),
    )
    u = build(recs, rows, ids=registry)
    assert {fid: {k.key for k in f.keys} for fid, f in u.families.items()} == {
        "heath-sans": {"Heath Sans", "font-heath-sans"},
        "heath-sans-2": {"Heath Sans (Other)"},
    }
    same_day = {fid: {**e, "first_seen": "2026-01-05"} for fid, e in registry.items()}
    assert build(recs, rows, ids=same_day).unmapped == (SourceKey("gf-family", "Heath Sans"),)


def test_a_shared_current_name_without_a_distinct_row_is_a_tie() -> None:
    # Two key rows give two families the display name "Rowan"; a third source's "Rowan"
    # is not guessed, this run or the next (no id "seen first" wins).
    recs = [
        rec("fontsource", "fs-id", "rowan", "Rowan"),
        rec("homebrew_casks", "brew-cask", "font-rowan", "Rowan"),
    ]
    rows = table(
        alias("rowan", "fs-id", "rowan-sans", "package"),
        alias("font-rowan", "brew-cask", "rowan-serif", "package"),
    )
    merged = next_ids(build(recs, rows), {})
    assert {e["family"] for e in merged.values()} == {"Rowan"}
    gf = rec("google_metadata", "gf-family", "Rowan", "Rowan")
    assert build([*recs, gf], rows, ids=merged, day=DAY2).unmapped == (gf.key,)


def test_an_asserted_old_name_is_never_the_display_name() -> None:
    recs = [
        rec("google_metadata", "gf-family", "Old Quill", "Old Quill"),  # not yet renamed
        rec("fontsource", "fs-id", "quill-sans", "Quill Sans", names=(("Old Quill", "rename"),)),
    ]
    fam = only(build(recs, ids=REGISTRY))
    assert (fam.id, fam.family) == ("old-quill", "Quill Sans")
    # On a first sighting the id comes from the current name too.
    assert only(build(recs)).id == "quill-sans"


def test_placements_that_never_settle_are_held_back_as_unmapped() -> None:
    # Contradictory input found by a property search: records of one formula assert
    # renames across two registry families that share every name. The run must end,
    # and the keys whose family keeps changing are unmapped (the stage then fails).
    def formula(key: str, family: str, *old: str) -> UniverseRecord:
        return rec(
            "fontist", "fontist-formula", key, family, names=tuple((o, "rename") for o in old)
        )

    recs = [
        formula("k1", "Alder"),
        formula("k1", "Alder Sans", "Alder"),
        formula("k1", "Birch"),
        formula("k2", "Alder", "Alder Sans"),
        formula("k2", "Birch", "Alder"),
    ]
    rows = table(
        alias("k1", "brew-cask", "alder", "rename"),
        alias("k1", "brew-cask", "alder", "distinct", "sibling"),
    )
    twins = {"family": "Alder", "minted_from": "Alder Sans", "first_seen": "2025-01-01"}
    registry = {"alder": dict(twins), "alder-sans": dict(twins)}
    u = build(recs, rows, ids=registry)
    assert u.unmapped == (SourceKey("fontist-formula", "k2"),)
    assert build(list(reversed(recs)), rows, ids=registry) == u


# --- keys the alias table keeps out, and conflicts -------------------------------------------------


def test_alias_rows_keep_ineligible_and_bundle_keys_out() -> None:
    recs = [
        *aster(),
        rec("homebrew_casks", "brew-cask", "font-shiny-pro", "Shiny Pro"),
        rec("homebrew_casks", "brew-cask", "font-aster-family", "Aster Family"),
    ]
    rows = table(
        alias("font-shiny-pro", "brew-cask", "", "ineligible", "proprietary"),
        alias("font-aster-family", "brew-cask", "aster-sans", "bundle"),
        alias("aster-sans", "fs-id", "aster-sans", "package"),
    )
    u = build(recs, rows)
    assert sorted(u.families) == ["aster-sans"]
    assert u.excluded == (
        (SourceKey("brew-cask", "font-aster-family"), "bundle"),
        (SourceKey("brew-cask", "font-shiny-pro"), "ineligible:proprietary"),
    )
    assert u.unmapped == ()


def test_conflicting_alias_rows_leave_the_key_unmapped() -> None:
    rows = (
        alias("aster-sans", "fs-id", "aster-sans", "rename"),
        alias("Aster-Sans", "fs-id", "birch-mono", "rename"),
    )
    with pytest.raises(AliasError, match="maps to 2 families"):
        AliasTable(rows=rows)  # the table refuses it; a hand-built one is still never guessed
    u = build(aster(), SimpleNamespace(rows=rows))  # type: ignore[arg-type]
    assert u.unmapped == (SourceKey("fs-id", "aster-sans"),)
    assert SourceKey("fs-id", "aster-sans") not in only(u).keys


def test_a_fold_row_that_a_distinct_row_forbids_leaves_the_key_unmapped() -> None:
    rows = table(
        alias("aster-sans", "fs-id", "aster-sans", "package"),
        alias("aster-sans", "fs-id", "aster-sans", "distinct"),
    )
    assert build(aster(), rows).unmapped == (SourceKey("fs-id", "aster-sans"),)


def test_name_rows_hold_in_every_name_namespace() -> None:
    registry = {
        "juniper": {"family": "Juniper", "minted_from": "Juniper", "first_seen": "2026-01-01"}
    }
    recs = [
        rec("fontsource", "fs-id", "juniper-text", "Juniper Text"),
        rec("homebrew_casks", "brew-cask", "font-juniper-text", "Juniper Text"),
    ]
    rows = table(alias("Juniper Text", "gf-family", "juniper", "rename"))  # a Google name row
    fam = only(build(recs, rows, ids=registry))
    assert (fam.id, fam.family) == ("juniper", "Juniper Text")


def test_sibling_blocks_keep_a_misnamed_key_out_of_the_parent() -> None:
    # Stage "aliases" writes sibling rows in font-name: every key containing the
    # alias is kept out of the family (aliases.SIBLING_DETAIL).
    recs = [
        rec("fontsource", "fs-id", "kestrel-sans", "Kestrel Sans"),
        rec("homebrew_casks", "brew-cask", "font-kestrel-sans-slab", "Kestrel Sans"),
    ]
    rows = table(alias("Kestrel Sans Slab", "font-name", "kestrel-sans", "distinct", "sibling"))
    u = build(recs, rows)
    assert {fid: f.keys for fid, f in u.families.items()} == {
        "kestrel-sans": (SourceKey("fs-id", "kestrel-sans"),),
        "kestrel-sans-2": (SourceKey("brew-cask", "font-kestrel-sans-slab"),),
    }
    merged = next_ids(u, {})
    assert build(recs, rows, ids=merged, day=DAY2).families.keys() == u.families.keys()


def test_a_key_in_several_families_is_kept_out_of_their_keys() -> None:
    recs = [
        rec("fontist", "fontist-formula", "opal", "Opal Sans"),
        rec("fontist", "fontist-formula", "opal", "Opal Serif"),
        rec("fontsource", "fs-id", "opal-serif", "Opal Serif"),
    ]
    u = build(recs)
    assert sorted(u.families) == ["opal-sans", "opal-serif"]
    assert u.families["opal-sans"].keys == ()
    assert u.families["opal-sans"].sources == ("fontist",)
    assert u.families["opal-serif"].keys == (SourceKey("fs-id", "opal-serif"),)
    assert u.excluded == ((SourceKey("fontist-formula", "opal"), "several-families"),)
    assert u.by_key(SourceKey("fontist-formula", "opal")) is None
    # The key maps no counts, but each record's files and the formula's license still
    # reach the family the record was placed in.
    opal = SourceKey("fontist-formula", "opal")
    assert u.families["opal-sans"].shared == ((opal, "Opal Sans"),)
    assert u.families["opal-serif"].shared == ((opal, "Opal Serif"),)
    assert u.record_owner(opal, "Opal Sans") == "opal-sans"
    assert u.record_owner(opal, "Opal Mono") is None
    assert u.record_owner(SourceKey("fs-id", "opal-serif"), "anything") == "opal-serif"
    assert u.sharing(opal) == ("opal-sans", "opal-serif")
    assert u.sharing(SourceKey("fs-id", "opal-serif")) == ()


def test_a_key_row_takes_the_other_sources_of_the_same_name_with_it() -> None:
    # Fontsource renamed its id; the key row keeps the old id. Google and Homebrew list
    # the new name with no row of their own: they must not become a second "Quill Sans".
    recs = [
        rec("fontsource", "fs-id", "quill-sans", "Quill Sans"),
        rec("google_metadata", "gf-family", "Quill Sans", "Quill Sans"),
        rec("homebrew_casks", "brew-cask", "font-quill-sans", "Quill-Sans"),
    ]
    rows = table(alias("quill-sans", "fs-id", "old-quill", "rename"))
    u = build(recs, rows, ids=REGISTRY)
    fam = only(u)
    assert (fam.id, fam.family, fam.minted_from) == ("old-quill", "Quill Sans", "Old Quill")
    assert set(fam.keys) == {r.key for r in recs}
    merged = next_ids(u, REGISTRY)
    assert set(merged) == {"old-quill"}  # no second id for the new name
    assert build(recs, rows, ids=merged, day=DAY2).families == u.families


def test_two_key_rows_that_give_one_name_two_families_leave_the_name_unmapped() -> None:
    recs = [
        rec("fontsource", "fs-id", "rowan", "Rowan"),
        rec("homebrew_casks", "brew-cask", "font-rowan", "Rowan"),
        rec("google_metadata", "gf-family", "Rowan", "Rowan"),
    ]
    rows = table(
        alias("rowan", "fs-id", "rowan-sans", "package"),
        alias("font-rowan", "brew-cask", "rowan-serif", "package"),
    )
    u = build(recs, rows)
    assert u.unmapped == (SourceKey("gf-family", "Rowan"),)  # never guessed
    assert sorted(u.families) == ["rowan-sans", "rowan-serif"]


def test_name_rows_keep_ineligible_and_bundle_names_out_in_every_namespace() -> None:
    recs = [
        *aster(),
        rec("homebrew_casks", "brew-cask", "font-shiny-pro", "Shiny Pro"),
        rec("google_metadata", "gf-family", "Shiny Pro", "Shiny Pro"),
        rec("fontist", "fontist-formula", "aster_family", "Aster Family"),
        rec("fontsource", "fs-id", "shiny-pro", "Shiny Pro"),  # its key row wins
    ]
    rows = table(
        alias("Shiny Pro", "font-name", "", "ineligible", "proprietary"),
        alias("Aster Family", "foundry-family", "aster-sans", "bundle"),
        alias("shiny-pro", "fs-id", "aster-sans", "package"),
    )
    u = build(recs, rows)
    assert sorted(u.families) == ["aster-sans"]
    assert SourceKey("fs-id", "shiny-pro") in u.families["aster-sans"].keys
    assert u.excluded == (
        (SourceKey("brew-cask", "font-shiny-pro"), "ineligible:proprietary"),
        (SourceKey("fontist-formula", "aster_family"), "bundle"),
        (SourceKey("gf-family", "Shiny Pro"), "ineligible:proprietary"),
    )
    assert u.unmapped == ()


def test_a_name_that_is_both_folded_and_ineligible_is_unmapped() -> None:
    # AliasTable checks one namespace at a time; across name namespaces this is a
    # contradiction, and stage "aliases" leaves such a name out of its index too.
    rows = table(
        alias("Aster Sans", "gf-family", "aster-sans", "rename"),
        alias("Aster Sans", "font-name", "", "ineligible", "itf"),
    )
    u = build(aster(), rows)
    assert u.unmapped == (
        SourceKey("brew-cask", "font-aster-sans"),
        SourceKey("fs-id", "aster-sans"),
    )
    assert only(u).keys == (SourceKey("gf-family", "Aster Sans"),)  # its own key row wins


def test_a_key_with_one_record_kept_out_by_name_is_kept_out_once() -> None:
    recs = [
        rec("fontist", "fontist-formula", "mixed", "Shiny Pro"),
        rec("fontist", "fontist-formula", "mixed", "Opal Sans"),
        rec("fontsource", "fs-id", "opal-sans", "Opal Sans"),
    ]
    rows = table(
        alias("Shiny Pro", "font-name", "", "ineligible", "proprietary"),
        alias("Shiny Pro", "gf-family", "", "ineligible", "itf"),
    )
    u = build(recs, rows)
    assert u.excluded == ((SourceKey("fontist-formula", "mixed"), "ineligible:itf+proprietary"),)
    assert only(u).keys == (SourceKey("fs-id", "opal-sans"),)
    assert only(u).sources == ("fontist", "fontsource")


def test_every_key_is_listed_exactly_once() -> None:
    recs = [
        *aster(),
        rec("fontist", "fontist-formula", "opal", "Opal Sans"),
        rec("fontist", "fontist-formula", "opal", "Opal Serif"),
        rec("fontist", "fontist-formula", "mixed", "Shiny Pro"),
        rec("fontist", "fontist-formula", "mixed", "Opal Sans"),
        rec("homebrew_casks", "brew-cask", "font-shiny-pro", "Shiny Pro"),
        rec(
            "fontsource",
            "fs-id",
            "tie",
            "Moss Neo",
            names=(("Moss Sans", "rename"), ("Moss Grotesk", "rename")),
        ),
        rec("fontsource", "fs-id", "tie", "Opal Serif"),
        rec("nerdfonts", "nerd-folder", "MossG", "Moss Grotesk"),
        rec("fontsource", "fs-id", "moss-sans", "Moss Sans"),
    ]
    registry = {
        "moss-sans": {"family": "Moss Sans", "minted_from": "Moss Sans"},
        "moss-grotesk": {"family": "Moss Grotesk", "minted_from": "Moss Grotesk"},
    }
    rows = table(alias("Shiny Pro", "font-name", "", "ineligible", "proprietary"))
    u = build(recs, rows, ids=registry)
    listed = [
        *(k for f in u.families.values() for k in f.keys),
        *(k for k, _ in u.excluded),
        *u.unmapped,
    ]
    assert sorted(listed) == sorted({r.key for r in recs})
    assert u.unmapped == (SourceKey("fs-id", "tie"),)  # unmapped wins over several-families
    # Between them, the committed report (kept out, unmapped) and the key table (in a
    # family) list every key exactly once.
    text = report(u, registry) + universe.keys_report(u)
    for k in listed:
        assert sum(f"| {k.ns} | {k.key} |" in line for line in text.splitlines()) == 1, k


# --- drops ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("drops", "scripts", "expected"),
    [
        ((None, None), (None, None), None),
        (("icon", None), (None, None), "icon"),  # one source is enough for non-text codes
        (("music", "icon"), (None, None), "icon"),  # records.DropReason order decides
        (("proprietary", None), (None, None), None),  # a lone license claim: licenses stage
        (("proprietary", "proprietary"), (None, None), "proprietary"),
        (("proprietary", "barcode"), (None, None), "barcode"),
        ((None, None), ("Zsye", "Latn"), "emoji"),  # ISO 15924 symbol scripts
        ((None, None), ("Zmth", None), "math"),
        ((None, "non-font"), ("Zsym", None), "symbol"),
    ],
)
def test_family_drop(
    drops: tuple[str | None, ...], scripts: tuple[str | None, ...], expected: str | None
) -> None:
    recs = [
        rec(f"s{i}", "fs-id", f"k{i}", "Ember", drop=d, primary_script=s)
        for i, (d, s) in enumerate(zip(drops, scripts, strict=True))
    ]
    assert family_drop(recs) == expected


def test_dropped_families_keep_an_id_and_a_reason() -> None:
    recs = [
        *aster(),
        rec("google_metadata", "gf-family", "Ember Icons", "Ember Icons", drop="icon"),
        rec("homebrew_casks", "brew-cask", "font-ember-icons", "Ember Icons"),
        rec("fontsource", "fs-id", "fjord-emoji", "Fjord Emoji", primary_script="Zsye"),
    ]
    u = build(recs)
    assert {fid: f.drop for fid, f in u.families.items()} == {
        "aster-sans": None,
        "ember-icons": "icon",
        "fjord-emoji": "emoji",
    }
    assert sorted(u.eligible()) == ["aster-sans"]
    assert "ember-icons" in next_ids(u, {})  # ids are minted for dropped families too


def test_unknown_drop_code_is_refused() -> None:
    assert "typeface" not in DROP_REASONS
    with pytest.raises(ValueError, match="typeface"):
        build([rec("fontsource", "fs-id", "x", "X", drop="typeface")])


# --- lookups and files ---------------------------------------------------------------------------


def test_by_key_is_exact_then_match_key_within_the_namespace() -> None:
    u = build(aster())
    assert u.by_key(SourceKey("fs-id", "aster-sans")).id == "aster-sans"
    assert u.by_key(SourceKey("fs-id", "Aster_Sans")).id == "aster-sans"
    assert u.by_key(SourceKey("npm", "aster-sans")) is None
    assert u.by_key(SourceKey("fs-id", "birch")) is None


def test_universe_file_round_trips(tmp_path: Path) -> None:
    recs = [*aster(), rec("homebrew_casks", "brew-cask", "font-x", "X Pro")]
    u = build(recs, table(alias("font-x", "brew-cask", "", "ineligible", "itf")))
    path = tmp_path / "universe.json"
    universe.dump_universe(u, path)
    assert universe.load_universe(path) == u
    first = path.read_bytes()
    universe.dump_universe(universe.load_universe(path), path)
    assert path.read_bytes() == first
    paths = Paths.for_root(tmp_path)
    stageio.dump_stage(paths, "universe", u)
    assert stageio.load_stage(paths, "universe") == u


# --- the report ----------------------------------------------------------------------------------


def _sections(text: str) -> list[str]:
    return [line.removeprefix("## ") for line in text.splitlines() if line.startswith("## ")]


def test_report_shape() -> None:
    recs = [
        *aster(),
        rec("google_metadata", "gf-family", "Ember Icons", "Ember Icons", drop="icon"),
        rec("homebrew_casks", "brew-cask", "font-shiny-pro", "Shiny Pro"),
        rec("fontsource", "fs-id", "pipe|name", "Pipe Sans"),
    ]
    rows = table(alias("font-shiny-pro", "brew-cask", "", "ineligible", "proprietary"))
    registry = {
        "aster-sans": {"family": "ASTER SANS", "minted_from": "Aster", "first_seen": "2026-09-03"},
        "gone-sans": {
            "family": "Gone Sans",
            "minted_from": "Gone Sans",
            "first_seen": "2026-09-03",
        },
    }
    u = build(recs, rows, ids=registry)
    text = report(u, registry)
    assert text == report(u, registry)
    assert text.startswith("# Candidate universe\n")
    assert _sections(text) == [
        "Per source",
        "Keys per namespace",
        "Drop reasons",
        "Dropped families",
        "Unmapped keys",
        "Keys kept out of every family",
        "New ids this run",
        "Display names changed this run (record the old name in data/aliases.csv)",
        "Registry ids no source listed this run",
    ]
    lines = text.splitlines()
    assert "| Families | 3 |" in lines
    assert "| Eligible families | 2 |" in lines
    assert "| Unmapped keys (must be 0) | 0 |" in lines
    assert "| google_metadata | 2 | 1 | 1 |" in lines  # per source: families, eligible, dropped
    assert "| homebrew_casks | 1 | 1 | 0 |" in lines
    assert "| brew-cask | 1 | 1 | 0 |" in lines  # per namespace: in a family, kept out, unmapped
    for code in DROP_REASONS:
        assert f"| {code} | {1 if code == 'icon' else 0} |" in lines
    assert "| ember-icons | Ember Icons | icon | google_metadata |" in lines
    assert "| brew-cask | font-shiny-pro | ineligible:proprietary |" in lines
    assert "| ember-icons | Ember Icons | Ember Icons |" in lines  # new id
    assert "| aster-sans | ASTER SANS | Aster Sans |" in lines  # display name changed
    assert "| gone-sans | Gone Sans |" in lines  # not listed this run
    assert "Every key's family is listed in build/review/universe-keys.md" in text
    keys = universe.keys_report(u)
    assert _sections(keys) == ["Every key mapped"]
    lines = keys.splitlines()
    assert "| fs-id | pipe\\|name | pipe-sans | Pipe Sans |" in lines  # cells are escaped
    mapped = lines[lines.index("## Every key mapped") :]
    for fam in u.families.values():
        for k in fam.keys:
            assert any(
                line.startswith(f"| {k.ns} | {k.key.replace('|', '\\|')} | {fam.id} |")
                for line in mapped
            )
    assert sum(line.startswith("| ") for line in mapped) == 1 + sum(
        len(f.keys) for f in u.families.values()
    )


def test_report_without_registry_and_with_nothing_dropped() -> None:
    text = report(build(aster()))
    assert "New ids this run" not in text
    after = text.split("## Dropped families\n\n", 1)[1]
    assert after.startswith("None.\n")


# --- the stage -----------------------------------------------------------------------------------


def _context(
    root: Path, ids: dict[str, dict[str, Any]] | None = None, day: date = DAY1
) -> StageContext:
    return StageContext(
        paths=Paths.for_root(root),
        config=None,  # type: ignore[arg-type]  # the stage reads no config
        state=State(ids=ids or {}),
        run_date=day,
        store=None,
        fetcher=None,
        log=logging.getLogger("test.universe"),
    )


def _write_inputs(root: Path, alias_rows: list[str] | None = None) -> Paths:
    paths = Paths.for_root(root)
    write_jsonl(aster(), paths.records / "fontsource.jsonl")
    icons = rec("google_metadata", "gf-family", "Ember Icons", "Ember Icons", drop="icon")
    count = Observation(
        source="google_metadata",
        series="popularity",
        key=SourceKey("gf-family", "Not A Universe Record"),
        value=1.0,
        unit="rank",
        start=DAY1,
        end=DAY1,
    )
    write_jsonl([icons, count], paths.records / "google_metadata.jsonl")
    baseline = rec("google_metadata", "gf-family", "Baseline Only", "Baseline Only")
    write_jsonl([baseline], paths.records / "google_metadata@2026-09-03.jsonl")
    lines = [",".join(COLUMNS), *(alias_rows or [])]
    paths.aliases_csv.parent.mkdir(parents=True, exist_ok=True)
    paths.aliases_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths


def _outputs(paths: Paths) -> dict[str, bytes]:
    files = {
        "universe.json": paths.stage / "universe.json",
        "universe.md": paths.build / "universe.md",
        "ids.json": paths.next_state / "ids.json",
    }
    return {name: path.read_bytes() for name, path in files.items()}


def test_stage_writes_its_outputs_twice_the_same(tmp_path: Path) -> None:
    registry = {
        "aster-sans": {
            "family": "Aster Sans",
            "minted_from": "Aster Sans",
            "first_seen": "2026-09-03",
        }
    }
    outputs = []
    for run in ("a", "b"):
        paths = _write_inputs(tmp_path / run)
        universe.run(_context(tmp_path / run, registry))
        outputs.append(_outputs(paths))
        assert not paths.state.exists()  # the stage never writes state/
    assert outputs[0] == outputs[1]

    paths = Paths.for_root(tmp_path / "a")
    u = stageio.load_stage(paths, "universe")
    assert sorted(u.families) == ["aster-sans", "ember-icons"]  # baselines and Observations skipped
    assert jsonio.load(paths.next_state / "ids.json") == {
        **registry,
        "ember-icons": {
            "family": "Ember Icons",
            "minted_from": "Ember Icons",
            "first_seen": "2026-10-03",
        },
    }
    assert (
        "| ember-icons | Ember Icons | icon | google_metadata |"
        in outputs[0]["universe.md"].decode()
    )


def test_stage_reads_the_alias_table(tmp_path: Path) -> None:
    _write_inputs(
        tmp_path, ["font-aster-sans,brew-cask,,ineligible,proprietary,test,2026-09-01,owner"]
    )
    universe.run(_context(tmp_path))
    u = stageio.load_stage(Paths.for_root(tmp_path), "universe")
    assert u.excluded == ((SourceKey("brew-cask", "font-aster-sans"), "ineligible:proprietary"),)


def test_stage_fails_on_unmapped_keys_after_writing_the_report(tmp_path: Path) -> None:
    rows = [
        "aster-sans,fs-id,aster-sans,package,,test,2026-09-01,owner",
        "aster-sans,fs-id,aster-sans,distinct,,test,2026-09-01,owner",
    ]
    paths = _write_inputs(tmp_path, rows)
    with pytest.raises(UniverseError, match="fs-id:aster-sans"):
        universe.run(_context(tmp_path))
    text = (paths.build / "universe.md").read_text("utf-8")
    assert "| Unmapped keys (must be 0) | 1 |" in text
    assert "| fs-id | aster-sans |" in text


def test_stage_needs_parsed_records(tmp_path: Path) -> None:
    with pytest.raises(UniverseError, match="run `tff-catalog parse` first"):
        universe.run(_context(tmp_path))


def test_stage_uses_the_committed_registry_not_an_earlier_runs(tmp_path: Path) -> None:
    paths = _write_inputs(tmp_path)
    universe.run(_context(tmp_path))
    first = _outputs(paths)
    # A rerun on another day still starts from state/ (empty), not from build/state/.
    universe.run(replace(_context(tmp_path), run_date=DAY2))
    ids = jsonio.load(paths.next_state / "ids.json")
    assert {v["first_seen"] for v in ids.values()} == {"2026-11-03"}
    assert first["universe.md"] == _outputs(paths)["universe.md"]  # the report has no dates
