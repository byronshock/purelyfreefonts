"""Stage "review" (milestone-1 steps 15-16): the monthly diff, the §9 flags and the pack.

Unit tests build their inputs by hand. The stage tests run the real stages
"rank", "confidence" and "membership" on synthetic terms for 48 families,
with the display and membership sizes scaled down to fit, and a synthetic
snapshot store whose one collector is a stand-in that reads records back.
"""

import dataclasses
import logging
import math
import os
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import numpy as np
import pytest
from tests.helpers import ROOT, synth

from tff_catalog import backtest, jsonio, records, review, stageio, surveys
from tff_catalog.collectors.base import CollectorBase, ParseContext
from tff_catalog.config_model import RankingConfig, from_mapping, load_toml
from tff_catalog.corrections import Term
from tff_catalog.engine.order import Placement
from tff_catalog.facts import Facts
from tff_catalog.keys import match_key
from tff_catalog.mapping import IndexEntry
from tff_catalog.membership import Membership, MemberState
from tff_catalog.names import mint_id
from tff_catalog.parse import SourceSnapshot, StaleSource
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, SourceKey
from tff_catalog.review import Flag
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.store import Store
from tff_catalog.universe import Family, Universe

RANKING: RankingConfig = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))


def small(cfg: RankingConfig = RANKING) -> RankingConfig:
    """ranking.toml scaled to 48 families: a top 10, a catalog of 30, 24 perturbations."""
    r = dataclasses.replace
    return r(
        cfg,
        engine=r(cfg.engine, overlap_full=30, overlap_off=10),
        display=r(cfg.display, exact_top=10, bands=((11, 20), (21, 30)), open_band_from=31),
        membership=r(
            cfg.membership,
            catalog_size=30,
            enter=27,
            leave=33,
            extra_top=10,
            top100=r(cfg.membership.top100, enter=9, leave=11),
        ),
        uncertainty=r(cfg.uncertainty, runs=24),
        review=r(
            cfg.review,
            top100_from_below=25,
            disagreement_top=5,
            disagreement_other=30,
            move_places=3,
            first_run_move_places=3,
        ),
    )


SMALL = small()
GROUP = {name: src.group for name, src in RANKING.sources.all().items()}
DESKTOP = ("homebrew", "arch", "github", "nerd", "debian")
PROJECT = ("fot", "almanac", "google", "npm_fontsource", "ecosystems", "jsdelivr", "npm_expo")
N = 48
IDS = [f"fam-{i:02d}" for i in range(N)]
NAMES = {fid: f"Family {fid[-2:]}" for fid in IDS}
RUN = date(2026, 10, 3)
LAST = date(2026, 9, 3)
BREW = "homebrew_analytics"


# --- rbo -----------------------------------------------------------------------------------------


def test_rbo_bounds_and_a_hand_computed_value() -> None:
    assert review.rbo(["a", "b", "c"], ["a", "b", "c"]) == pytest.approx(1.0)
    assert review.rbo(["a", "b"], ["c", "d"]) == 0.0
    assert review.rbo([], []) == 1.0
    assert review.rbo([], ["a"]) == 0.0
    # X = 0, 2, 3 at p = 0.5: (0 + 2/2·0.25 + 3/3·0.125) + 3/3·0.125 = 0.5
    assert review.rbo(["a", "b", "c"], ["b", "a", "c"], p=0.5) == pytest.approx(0.5)


def test_rbo_extrapolates_a_prefix_to_one_and_is_symmetric() -> None:
    assert review.rbo(["a", "b"], ["a", "b", "c", "d"]) == pytest.approx(1.0)
    a, b = ["a", "b", "c", "x"], ["b", "c", "a", "y", "z", "w"]
    assert review.rbo(a, b) == pytest.approx(review.rbo(b, a))
    assert 0.0 < review.rbo(a, b) < 1.0


def test_rbo_equals_the_backtest_for_equal_lengths() -> None:
    rng = np.random.default_rng(3)
    items = [f"f{i}" for i in range(60)]
    for _ in range(20):
        a = list(rng.permutation(items)[:40])
        b = list(rng.permutation(items)[:40])
        assert review.rbo(a, b, 0.9) == pytest.approx(backtest.rbo(a, b, 0.9), abs=1e-12)


@pytest.mark.parametrize(
    ("a", "b", "p"), [(["a", "a"], ["b"], 0.9), (["a"], ["b"], 1.0), (["a"], ["b"], 0.0)]
)
def test_rbo_refuses_bad_input(a: list[str], b: list[str], p: float) -> None:
    with pytest.raises(ValueError, match="rbo"):
        review.rbo(a, b, p)


# --- the monthly diff ----------------------------------------------------------------------------


def orders(*ids: str) -> dict[str, int]:
    return {fid: i for i, fid in enumerate(ids, 1)}


def test_diff_flags_entries_exits_moves_and_far_below() -> None:
    cfg = small()
    ids = [f"f{i:02d}" for i in range(40)]
    prev = {"overall": orders(*ids)}
    now_ids = [f for f in ids if f not in ("f01", "f35")]
    now_ids.insert(2, "f35")  # 36 -> 3: enters the top 10 from below 25
    now_ids.insert(20, "f01")  # 2 -> 21: leaves the top 10, a big move
    now_ids.insert(5, "new")  # not published last month
    now_ids.remove("f39")  # no longer published
    now = {"overall": orders(*now_ids)}
    names = {"f35": "Thirty-Five"}
    flags = review.diff_flags(prev, now, names, cfg)
    by_kind = {
        k: [f for f in flags if f.kind == k] for k in ("entry", "exit", "move", "from_below")
    }
    assert [f.message for f in by_kind["entry"]] == [
        "Thirty-Five enters at 3 (last month 36)",
        "new enters at 6 (last month not published)",
    ]
    assert [f.message for f in by_kind["exit"]] == [
        "f01 leaves from 2 (now 22)",
        "f09 leaves from 10 (now 11)",
    ]
    assert [f.family_id for f in by_kind["move"]] == ["f01", "f35"]
    assert by_kind["move"][0].message == "f01: 2 → 22"
    assert [f.message for f in by_kind["from_below"]] == [
        "Thirty-Five at 3, from 36",
        "new at 6, not published last month",
    ]
    assert all(f.rank_key == "overall" for f in flags)
    gone = review.diff_flags({"overall": orders("a", "b")}, {"overall": orders("b")}, {}, cfg)
    assert [f.message for f in gone if f.kind == "exit"] == ["a leaves from 1 (now not published)"]


def test_diff_flags_small_moves_deep_down_are_not_big() -> None:
    cfg = small()
    ids = [f"f{i:02d}" for i in range(40)]
    now_ids = list(ids)
    now_ids.remove("f25")
    now_ids.insert(29, "f25")  # 26 -> 30: 4 places, but only 15% of 26
    flags = review.diff_flags({"project": orders(*ids)}, {"project": orders(*now_ids)}, {}, cfg)
    assert not [f for f in flags if f.kind == "move"]


def test_the_move_flag_reads_its_own_key_not_the_first_run_one() -> None:
    """Owner ruling of 2026-09-26 (review_report): review.move_places, not first_run_move_places."""
    ids = [f"f{i:02d}" for i in range(40)]
    now_ids = list(ids)
    now_ids.remove("f01")
    now_ids.insert(9, "f01")  # 2 -> 10: 8 places
    prev, now = {"overall": orders(*ids)}, {"overall": orders(*now_ids)}
    wide = dataclasses.replace(small().review, move_places=10, first_run_move_places=3)
    narrow = dataclasses.replace(small().review, move_places=3, first_run_move_places=10)
    for rev, flagged in ((wide, False), (narrow, True)):
        flags = review.diff_flags(prev, now, {}, dataclasses.replace(small(), review=rev))
        assert any(f.kind == "move" and f.family_id == "f01" for f in flags) is flagged


def test_default_category_catalog_fonts_are_listed_for_gate_r() -> None:
    """Owner ruling of 2026-09-26 (data_defaults): catalog fonts whose category is the
    sans-serif default are listed in the pack for the owner to correct."""
    fx = {
        "a": Facts("sans-serif", False, False, True, "default"),
        "b": Facts("serif", False, False, True, "google_metadata"),
        "c": Facts(
            "sans-serif",
            True,
            False,
            True,
            "category=default;is_monospace=font_file;formats=font_file",
        ),
        "d": Facts("sans-serif", False, False, True, "default"),
    }
    members = SimpleNamespace(members=lambda: ["a", "b", "c"])
    assert review.defaulted_categories(fx, members) == ("a", "c")  # d is not in the catalog
    assert review.defaulted_categories(fx, None) == ("a", "c", "d")
    assert review.defaulted_categories(None, members) == ()
    assert "fira-mono" in review.GATE_R_WATCH


def test_diff_flags_first_run_and_a_newly_published_key() -> None:
    now = {"overall": orders("a", "b"), "rising": orders("c")}
    assert review.diff_flags({}, now, {}, SMALL) == []
    flags = review.diff_flags({"overall": orders("a", "b")}, now, {}, SMALL)
    assert flags == [Flag("entry", "first month with published ranks (1 in the top)", "rising")]


def test_catalog_flags_use_membership_not_orders() -> None:
    committed = {
        "catalog": {
            "a": {"member": True, "entered": "2026-09-03", "runs_outside": 0},
            "b": {"member": True, "entered": "2026-09-03", "runs_outside": 1},
            "c": {"member": True, "entered": "2026-09-03", "runs_outside": 0},
        }
    }
    m = Membership(
        catalog={
            "a": MemberState(True, date(2026, 9, 3), 0),
            "b": MemberState(False, date(2026, 9, 3), 2),
            "d": MemberState(True, RUN, 0),
        },
        top100={},
    )
    flags = review.catalog_flags(committed, m, {"d": "Dee"}, {"a": 1, "d": 7, "b": 40})
    assert [(f.kind, f.message) for f in flags] == [
        ("entry", "Dee joins the catalog (overall 7)"),
        ("exit", "b leaves the catalog (overall 40)"),
        ("exit", "c leaves the catalog (not ranked overall)"),
    ]
    assert {f.rank_key for f in flags} == {review.CATALOG}
    failed = review.catalog_flags(committed, m, {}, {}, frozenset({"c"}))
    assert failed[-1].message == "c leaves the catalog (not ranked overall; failed L3)"
    assert review.catalog_flags({}, m, {}, {}) == []  # first run


def test_license_flags() -> None:
    before = {
        "a": {"level": "L3", "text_sha256": "1" * 64, "text_url": "https://x.example/OFL.txt"},
        "b": {"level": "L3", "text_sha256": "2" * 64, "text_url": "https://x.example/b.txt"},
        "c": {"level": "L3", "text_sha256": "3" * 64, "text_url": "https://x.example/c.txt"},
        "d": {"level": "L3", "text_sha256": "4" * 64, "text_url": "https://x.example/d.txt"},
    }
    after = {
        "a": {**before["a"], "text_sha256": "9" * 64},
        "b": {**before["b"], "level": "failed"},
        "c": {**before["c"], "text_url": "https://y.example/c.txt"},
        "d": before["d"],
        "e": {"level": "L3", "text_sha256": "5" * 64, "text_url": "https://x.example/e.txt"},
    }
    flags = review.license_flags(before, after, {"a": "Alpha"}, {"a": "OFL-1.1"})
    assert [f.message for f in flags] == [
        "Alpha: license text changed (111111111111 → 999999999999); L2 OFL-1.1",
        "b: level L3 → failed",
        "c: license text now read from https://y.example/c.txt",
        "1 font checked for the first time: e",
    ]
    assert review.license_flags({}, after, {}) == []  # first run: nothing to compare


def test_disagreement_flags() -> None:
    filler = {f"q{i:02d}": i for i in range(5, 31)}
    now = {
        "desktop_chosen": orders("a", "b", "c", "d", "e", "f"),
        "project": {"b": 1, "x": 2, "f": 3, "d": 4, "c": 35, "e": 31, **filler},
    }
    flags = review.disagreement_flags(now, {"a": "Alpha"}, SMALL)
    assert [(f.rank_key, f.family_id, f.message) for f in flags] == [
        ("desktop_chosen", "a", "Alpha: desktop_chosen 1, project not ranked"),
        ("desktop_chosen", "c", "c: desktop_chosen 3, project 35"),
        ("desktop_chosen", "e", "e: desktop_chosen 5, project 31"),
        ("project", "x", "x: project 2, desktop_chosen not ranked"),
        ("project", "q05", "q05: project 5, desktop_chosen not ranked"),
    ]
    assert review.disagreement_flags({"desktop_chosen": orders("a")}, {}, SMALL) == []


# --- review.md ----------------------------------------------------------------------------------


def test_render_review_sections_summary_and_escaping() -> None:
    prev = {"overall": orders("a", "b", "c"), "project": orders("a", "b")}
    now = {"overall": orders("a", "c", "d"), "project": orders("b", "a")}
    flags = [
        Flag("info", "Run of 2026-10-03."),
        Flag("entry", "d enters at 3 (last month not ranked)", "overall", "d"),
        Flag("exit", "b leaves from 2 (now not ranked)", "overall", "b"),
        Flag("stale", "`fot`: stale | odd"),
        Flag("brand_new_kind", "something"),
    ]
    text = review.render_review(prev, now, flags)
    assert text.startswith("# Monthly review\n\n- Run of 2026-10-03.\n\n## Summary\n")
    assert "| overall | 3 | 3 | 1 | 1 | 1 | 1 | 0 |" in text
    assert "| project | 2 | 2 | 0 | 0 | 0 | 0 | 0 |" in text
    assert "Flags: 4 (entry 1, exit 1, stale 1, brand_new_kind 1)." in text
    headings = [line[3:] for line in text.splitlines() if line.startswith("## ")]
    public = [h for k, (h, _) in review.KINDS.items() if k not in review.PRIVATE_KINDS]
    assert headings == ["Summary", *public, "Brand new kind"]
    assert "- `fot`: stale \\| odd" in text
    assert "| overall | d enters at 3 (last month not ranked) |" in text
    guard = text.split("## Outlier-guard hits")[1].split("## ")[0]
    assert "None." in guard
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert review.render_review(prev, now, flags) == text


def test_render_review_first_run_summary() -> None:
    text = review.render_review({}, {"overall": orders("a")}, [])
    dash = review.NONE
    assert f"| overall | 1 | {dash} | {dash} | {dash} | 0 | 0 | 0 |" in text
    assert "Flags: 0." in text


# --- flags from the stage files --------------------------------------------------------------


def test_stale_flags() -> None:
    stale = {
        "pkgstats": StaleSource(
            None, RUN, None, None, 1, True, "parse of 2026-10-03 failed (KeyError)"
        ),
        "fot": StaleSource(LAST, RUN, LAST, 30, 2, False, "column check failed"),
    }
    flags = review.stale_flags(stale, {"fot": {"stale_runs": 3}}, SMALL)
    assert [f.message for f in flags] == [
        "`fot`: stale, using the snapshot of 2026-09-03 (30 days old, stale for 2 runs; "
        "column check failed)",
        "`pkgstats`: dropped this run, no usable snapshot (parse of 2026-10-03 failed (KeyError))",
        "`fot`: left out of every rank, stale for over 2 runs",
    ]


def test_overlap_flags() -> None:
    full = dict.fromkeys(PROJECT, 40)
    scores = {
        "desktop_chosen": SimpleNamespace(
            overlaps={"homebrew": 3, "arch": 12, "debian": 5, "nerd": 40}
        ),
        "project": SimpleNamespace(overlaps={**full, "fot": 31, "almanac": 29, "flutter": 0}),
    }
    flags = review.overlap_flags(scores, SMALL, frozenset({"github"}))  # type: ignore[arg-type]
    times = review.TIMES
    assert [(f.rank_key, f.message) for f in flags] == [
        ("desktop_chosen", f"`arch`: 12 fonts in common with the ruler, weight {times} 0.40"),
        ("desktop_chosen", "`debian`: 5 fonts in common with the ruler, under 10: switched off"),
        ("project", f"`almanac`: 29 fonts in common with the ruler, weight {times} 0.97"),
    ]  # homebrew is the ruler, github is stale-dropped, flutter is disabled


def test_guard_flags_stop_at_the_limit() -> None:
    hit = SimpleNamespace(guard=(("debian", 0.5), ("nerd", 0.5)))
    calm = SimpleNamespace(guard=())
    scores = {"desktop_chosen": SimpleNamespace(fused={"a": hit, "b": calm, "c": hit})}
    now = {"desktop_chosen": {"a": 4, "b": 1, "c": 40}}
    flags = review.guard_flags(scores, now, {"a": "Alpha"}, 30)  # type: ignore[arg-type]
    times = review.TIMES
    assert flags == [
        Flag("guard", f"Alpha (4): `debian` {times} 0.5, `nerd` {times} 0.5", "desktop_chosen", "a")
    ]


def test_term_flags_list_fonts_best_first_and_cut_long_lists() -> None:
    sync = Term(1.0, "observed", "npm_registry", flags=("stale_sync",))
    merge = Term(1.0, "observed", "http_archive", factor=0.5, flags=("parent_merge",))
    many = {f"f{i:02d}": sync for i in range(25)}
    terms = {
        "project": {"ecosystems": many, "almanac": {"b": merge}},
        "desktop_chosen": {"almanac": {"b": merge}},  # the same flag twice counts once
    }
    overall = {"f24": 1, "f03": 2}
    flags = review.term_flags(terms, {"f24": "Last"}, overall)
    assert [f.message for f in flags] == [
        "`almanac` parent_merge (width cuts folded into the parent; weight halved): 1 font, b",
        "`ecosystems` stale_sync (ecosyste.ms data not synced recently): 25 fonts, "
        "Last, f03, f00, f01, f02, f04, f05, f06, f07, f08, f09, f10, f11, f12, f13, f14, "
        "f15, f16, f17, f18 and 5 more",
    ]
    assert [f.family_id for f in flags] == ["b", None]


# --- per-source statistics --------------------------------------------------------------------


def test_compare_values_and_share_jumps() -> None:
    then = {f"f{i}": float(100 - i) for i in range(40)}
    now = dict(then)
    now["f30"] = 400.0  # share up about 5.7x
    now.pop("f39")
    now["new"] = 5.0
    st = review.compare_values("homebrew", then, now, SMALL, (LAST, RUN))
    assert st.counted == (40, 40)
    assert st.coverage == 0.0
    assert st.spearman is not None
    assert 0.8 < st.spearman < 1.0
    assert [j.family_id for j in st.jumps] == ["f30"]
    jump = st.jumps[0]
    assert jump.up
    assert jump.ratio > 3
    assert (jump.rank_before, jump.rank_now) == (31, 1)


def test_source_flags_keep_private_sources_to_ranks() -> None:
    jump = review.Jump("fam-01", 4.2, True, 80, 9)
    stats = [
        review.SourceStats("google", LAST, RUN, (50, 70), 0.5, 0.7, 0.4, (jump,)),
        review.SourceStats("homebrew", LAST, RUN, (50, 50), 0.99, 0.95, 0.0, (jump,)),
    ]
    flags = review.source_flags(stats, NAMES, SMALL)
    kinds = [(f.kind, f.message) for f in flags]
    assert ("spearman", "`google`: 0.500, under 0.9 (snapshots 2026-09-03 → 2026-10-03)") in kinds
    assert (
        "coverage",
        "`google`: 50 → 70 fonts with a value (+40%) (snapshots 2026-09-03 → 2026-10-03)",
    ) in kinds
    assert ("share_jump", "Family 01: `google` rank 80 → 9") in kinds
    times = review.TIMES
    assert ("share_jump", f"Family 01: `homebrew` share up 4.2{times}, rank 80 → 9") in kinds
    google = " ".join(f.message for f in flags if "`google`" in f.message)
    assert "share" not in google
    assert times not in google


# --- what if ------------------------------------------------------------------------------------


def synth_terms(seed: int = 11, n: int = N) -> dict[str, dict[str, dict[str, Term]]]:
    rng = np.random.default_rng(seed)
    ids = [f"fam-{i:02d}" for i in range(n)]
    latent = dict(zip(ids, rng.normal(0.0, 1.0, n), strict=True))

    def terms_for(source: str) -> dict[str, Term]:
        out = {}
        for fid in ids:
            if rng.random() < 0.15:
                continue
            value = float(np.exp(3 + latent[fid] + rng.normal(0.0, 0.5)))
            state = "censored" if value < 8 else "observed"
            out[fid] = Term(value=value, state=state, group=GROUP[source])
        return out

    installed = {s: terms_for(s) for s in DESKTOP}
    chosen = {
        s: {f: t for f, t in ts.items() if not (s in ("arch", "debian") and f in ids[:3])}
        for s, ts in installed.items()
    }
    project = {s: terms_for(s) for s in PROJECT}
    fid = next(iter(project["almanac"]))
    project["almanac"][fid] = dataclasses.replace(
        project["almanac"][fid], factor=0.5, flags=("parent_merge",)
    )
    return {"desktop_chosen": chosen, "desktop_installed": installed, "project": project}


def synth_inputs(cfg: RankingConfig = SMALL) -> surveys.RankInputs:
    terms = synth_terms()
    ruler = {fid: float(i) / 10 for i, fid in enumerate(reversed(IDS))}
    return surveys.rank_inputs(terms, ruler, NAMES, cfg, monospace=frozenset(IDS[::2]))


def test_knobs_cover_every_live_weight() -> None:
    paths = {k.path: k for k in review.knobs(RANKING)}
    assert "surveys.desktop.weights.homebrew" in paths
    assert "surveys.project.weights.fot" in paths
    assert "surveys.project.weights.flutter" not in paths  # disabled
    assert "ranks.dev_apps.weights.flutter" not in paths
    assert "ranks.coding.weights.nerd" in paths
    assert "project_group_shares.web.share" in paths
    assert "ranks.overall.mix.project" in paths
    assert paths["surveys.desktop.weights.arch"].keys == (
        "overall",
        "desktop_chosen",
        "desktop_installed",
    )
    assert paths["project_group_shares.code.share"].keys == ("overall", "project")
    assert paths["ranks.coding.weights.nerd"].keys == ("coding",)


def test_vary_keeps_fixed_group_shares() -> None:
    inputs = synth_inputs()
    by_path = {k.path: k for k in review.knobs(SMALL)}
    group_of = {s: g for g, spec in SMALL.project_group_shares.items() for s in spec.sources}

    def group_totals(ws: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s, w in ws.items():
            out[group_of[s]] = out.get(group_of[s], 0.0) + w
        return out

    varied, _, live = review.vary(inputs, SMALL, by_path["surveys.project.weights.fot"], 2.0)
    before, after = inputs.weights["project"], varied.weights["project"]
    assert live
    assert group_totals(after) == pytest.approx(group_totals(before))
    assert after["fot"] / after["almanac"] == pytest.approx(2 * before["fot"] / before["almanac"])
    assert varied.weights["desktop_chosen"] == inputs.weights["desktop_chosen"]

    varied, _, _ = review.vary(inputs, SMALL, by_path["project_group_shares.web.share"], 0.5)
    totals = group_totals(varied.weights["project"])
    assert totals["web"] == pytest.approx(group_totals(before)["web"] / 2)
    assert totals["code"] == pytest.approx(group_totals(before)["code"])

    varied, _, _ = review.vary(inputs, SMALL, by_path["surveys.desktop.weights.homebrew"], 0.5)
    for key in ("desktop_chosen", "desktop_installed"):
        w0, w1 = inputs.weights[key], varied.weights[key]
        assert w1["homebrew"] == pytest.approx(w0["homebrew"] / 2)
        assert w1["arch"] == w0["arch"]
    assert varied.weights["coding"] == inputs.weights["coding"]

    _, cfg, live = review.vary(inputs, SMALL, by_path["ranks.overall.mix.project"], 2.0)
    assert live
    assert cfg.ranks.overall.mix == {"desktop_chosen": 0.5, "project": 1.0}


def test_what_if_moves_only_the_keys_a_weight_reaches() -> None:
    cfg = dataclasses.replace(
        SMALL, review=dataclasses.replace(SMALL.review, what_if_factors=(1.0, 8.0))
    )
    rows = review.what_if(synth_inputs(cfg), cfg)
    assert len(rows) == 2 * len(review.knobs(cfg))
    for row in rows:
        assert row.live
        assert tuple(e.key for e in row.effects) == row.knob.keys
        if row.factor == 1.0:
            assert all(
                e.rbo == pytest.approx(1.0) and not e.entered and e.largest is None
                for e in row.effects
            )
    moved = [r for r in rows if r.factor == 8.0 and any(e.largest for e in r.effects)]
    assert moved  # a weight times 8 moves something somewhere
    text = [f.message for f in review.what_if_flags(rows, NAMES, 10)]
    assert text[0] == (
        f"`surveys.desktop.weights.arch` {review.TIMES} 1: overall unchanged; desktop_chosen unchanged; "
        "desktop_installed unchanged"
    )


def test_what_if_says_when_a_weight_is_off_this_run() -> None:
    inputs = synth_inputs()
    off = {
        k: {s: 0.0 if s == "debian" else w for s, w in ws.items()}
        for k, ws in inputs.weights.items()
    }
    cfg = dataclasses.replace(
        SMALL, review=dataclasses.replace(SMALL.review, what_if_factors=(2.0,))
    )
    rows = review.what_if(dataclasses.replace(inputs, weights=off), cfg)
    debian = [r for r in rows if r.knob.path == "surveys.desktop.weights.debian"]
    assert [(r.live, r.effects) for r in debian] == [(False, ())]
    [flag] = review.what_if_flags(debian, NAMES, 10)
    assert (
        flag.message
        == f"`surveys.desktop.weights.debian` {review.TIMES} 2: off this run, nothing moves"
    )


def test_effect_counts_entries_and_the_largest_move() -> None:
    base = orders(*(f"f{i}" for i in range(20)))
    new_ids = [f"f{i}" for i in range(20)]
    new_ids.remove("f0")
    new_ids.append("f0")  # 1 -> 20
    e = review.effect("overall", base, orders(*new_ids), SMALL)
    assert e.entered == ("f10",)
    assert e.left == ("f0",)
    assert e.largest == ("f0", 1, 20)
    assert 0 < e.rbo < 1


def test_effect_text_says_unchanged_only_when_nothing_moved() -> None:
    still = review.Effect("overall", 1.0, (), (), None)
    below = review.Effect("overall", 0.978, (), (), None)
    assert review._effect_text(still, {}, 10) == "overall unchanged"
    assert review._effect_text(below, {}, 10) == (
        "overall RBO 0.978, 0 in and 0 out of the top 10, no move inside it"
    )


# --- snapshot growth and the old Top 100 -------------------------------------------------------


def write_snapshot(store: Path, source: str, day: date, recs: list[Any]) -> None:
    synth.write_snapshot(store, source, day, recs)


def obs(fid: str, value: float, day: date) -> Observation:
    return Observation(
        source=BREW,
        series="365d",
        key=SourceKey("brew-cask", f"font-{fid}"),
        value=value,
        unit="installs",
        start=day - timedelta(days=365),
        end=day - timedelta(days=1),
    )


def stage_ctx(paths: Paths, cfg: RankingConfig, state: State, store: Path | None) -> StageContext:
    return StageContext(
        paths=paths,
        config=SimpleNamespace(ranking=cfg),  # type: ignore[arg-type]
        state=state,
        run_date=RUN,
        store=Store(store) if store is not None else None,
        fetcher=None,
        log=logging.getLogger("test.review"),
    )


def test_growth_flags_judge_only_new_snapshots(tmp_path: Path) -> None:
    store = tmp_path / "store"
    small_recs = [obs("fam-00", 1.0, LAST)]
    big_recs = [obs(f"fam-{i:02d}", float(i), RUN) for i in range(30)]
    write_snapshot(store, BREW, LAST - timedelta(days=30), small_recs)
    write_snapshot(store, BREW, LAST, small_recs)
    write_snapshot(store, BREW, RUN, big_recs)
    write_snapshot(store, "synth_packages", LAST - timedelta(days=30), small_recs)
    write_snapshot(store, "synth_packages", LAST, big_recs)  # grew before the last run
    ctx = stage_ctx(Paths.for_root(tmp_path), SMALL, State(), store)
    flags, line = review.growth_flags(ctx, {"run_date": LAST.isoformat()})
    assert [f.message.split(":")[0] for f in flags] == [f"`{BREW}`"]
    assert "2026-09-03 → 2026-10-03" in flags[0].message
    assert " kB → " in flags[0].message  # rounded: an exact byte count could pass for a value
    assert "bytes" not in flags[0].message
    assert line.startswith("Snapshot store: ")
    assert "at the last run" in line
    first, _ = review.growth_flags(ctx, None)
    assert len(first) == 2
    assert review.growth_flags(stage_ctx(Paths.for_root(tmp_path), SMALL, State(), None), None) == (
        [],
        "",
    )


def universe(names: dict[str, str] = NAMES) -> Universe:
    return Universe(
        families={
            fid: Family(
                id=fid,
                family=name,
                keys=(SourceKey("gf-family", name),),
                sources=("google_metadata",),
                first_seen=date(2026, 9, 1),
                minted_from=name,
            )
            for fid, name in names.items()
        },
        unmapped=(),
    )


def write_seed(store: Path) -> None:
    seed = store / "_seed" / "oldlib-2026-09-23"
    old = [{"rank": i + 1, "family": NAMES[f"fam-{i:02d}"]} for i in range(0, 12, 2)]
    old.append({"rank": 13, "family": "Gone Sans"})
    jsonio.dump(old, seed / "final_manifest.json")
    pool = [{"family": NAMES["fam-01"], "inst": "installed"}, {"family": "Family 03", "inst": "x"}]
    jsonio.dump(pool, seed / "rerank.json")


def test_old_top100_compares_like_with_like(tmp_path: Path) -> None:
    store = tmp_path / "store"
    write_seed(store)
    overall = orders(*reversed(IDS[:12]))  # fam-11 first ... fam-00 twelfth
    old = review.old_top100(store, universe(), overall, SMALL)
    assert old is not None
    assert old.seed == "_seed/oldlib-2026-09-23/final_manifest.json"
    assert old.left_out == 1  # fam-01, installed on the old machine
    # new positions skip fam-01: fam-11 1, fam-10 2, ... fam-02 10, fam-00 11
    assert old.compared == (
        ("fam-00", 1, 11),
        ("fam-02", 3, 10),
        ("fam-04", 5, 8),
        ("fam-06", 7, 6),
        ("fam-08", 9, 4),
        ("fam-10", 11, 2),
    )
    assert old.missing == (("Gone Sans", 13),)
    assert old.spearman == pytest.approx(-1.0)
    assert old.new_only == ("fam-11", "fam-09", "fam-07", "fam-05", "fam-03")
    assert old.old_out == (("fam-00", 1, 11),)
    assert review.old_top100(tmp_path / "empty", universe(), overall, SMALL) is None


def test_old_top100_counts_a_family_once_under_two_old_names(tmp_path: Path) -> None:
    store = tmp_path / "store"
    seed = store / "_seed" / "oldlib-2026-09-23"
    old = [
        {"rank": 1, "family": "Family 00"},
        {"rank": 2, "family": "Family 01"},
        {"rank": 3, "family": "Family Zero"},  # fam-00's old name
    ]
    jsonio.dump(old, seed / "final_manifest.json")
    u = universe()
    fam = dataclasses.replace(
        u.families["fam-00"],
        keys=(*u.families["fam-00"].keys, SourceKey("gf-family", "Family Zero")),
    )
    u = dataclasses.replace(u, families={**u.families, "fam-00": fam})
    found = review.old_top100(store, u, orders(*IDS[:12]), SMALL)
    assert found is not None
    assert found.compared == (("fam-00", 1, 1), ("fam-01", 2, 2))
    assert found.repeats == (("Family Zero", 3, "fam-00"),)
    assert found.rbo == pytest.approx(review.rbo(["fam-00", "fam-01"], IDS[:10]))


@pytest.mark.store
def test_old_top100_reads_the_real_seed() -> None:
    """The private seed parses: its 100 rows are all accounted for, and ranking its own
    pool in pool order agrees with it (the old list is that pool, filtered)."""
    store = Path(os.environ["TFF_STORE"]).expanduser()
    seeds = sorted(store.glob(review.OLD_TOP100))
    if not seeds:
        pytest.skip("this store has no copy of the old library")
    rows = jsonio.load(seeds[-1])
    pool = jsonio.load(seeds[-1].parent / review.OLD_POOL)
    taken: set[str] = set()
    ids: dict[str, str] = {}
    for name in [r["family"] for r in pool] + [r["family"] for r in rows]:
        if name not in ids:
            ids[name] = mint_id(name, taken)
            taken.add(ids[name])
    overall = {ids[r["family"]]: i for i, r in enumerate(pool, 1)}
    old = review.old_top100(store, universe({fid: n for n, fid in ids.items()}), overall, RANKING)
    assert old is not None
    assert len(old.compared) + len(old.missing) + len(old.repeats) == len(rows) == 100
    assert old.left_out > 0
    assert old.spearman is not None
    assert old.spearman > 0.9


# --- the stage ----------------------------------------------------------------------------------


class Replay(CollectorBase):
    """A stand-in collector: its snapshots hold records, and parse reads them back."""

    name: ClassVar[str] = BREW
    hosts: ClassVar[tuple[str, ...]] = ()
    group: ClassVar[str | None] = "homebrew"

    def fetch(self, ctx: object) -> None:  # pragma: no cover - never fetches
        raise NotImplementedError

    def parse(self, ctx: ParseContext) -> list[records.Record]:
        return [records.from_json(row) for row in ctx.snapshot.iter_jsonl(synth.EXTRACT)]


def brew_values(day: date) -> dict[str, float]:
    """Installs per family; a month later fam-40 jumps and fam-41..47 appear."""
    base = {fid: float(5000 - 100 * i) for i, fid in enumerate(IDS)}
    if day == LAST:
        return {f: v for f, v in base.items() if int(f[-2:]) <= 40}
    return {**base, "fam-40": base["fam-40"] * 6}


def ranked_build(
    root: Path, *, first_run: bool = False, cfg: RankingConfig = SMALL
) -> StageContext:
    """A build where "rank", "confidence" and "membership" have run, plus the files
    review reads from other stages, and a store holding last month's snapshot.

    Last month's published ranks hold the catalog's fonts only, as stage
    "export" writes them; this month's come from the membership fallback."""
    from tff_catalog import confidence, membership

    paths = Paths.for_root(root)
    terms = synth_terms()
    stageio.dump_stage(paths, "terms", terms)
    stageio.dump_stage(paths, "ruler_counts", {f: 5000.0 - 100 * i for i, f in enumerate(IDS)})
    stageio.dump_stage(paths, "universe", universe())
    facts = {
        fid: Facts(
            category="monospace" if i % 2 == 0 else "sans-serif",
            is_monospace=i % 2 == 0,
            variable=False,
            static=True,
            basis="google_metadata",
        )
        for i, fid in enumerate(IDS)
    }
    stageio.dump_stage(paths, "facts", facts)
    stale = {
        "fot": StaleSource(
            snapshot=LAST,
            stale_of=RUN,
            data_date=LAST,
            age_days=30,
            stale_runs=1,
            dropped=False,
            reason="no snapshot for 2026-10-03",
        )
    }
    stageio.dump(stale, paths.stage / "stale.json")
    snapshots = {
        BREW: SourceSnapshot(RUN, False, RUN, None, None, 1, N, ()),
    }
    stageio.dump(snapshots, paths.stage / "snapshots.json")
    rows = [obs(f, v, RUN) for f, v in brew_values(RUN).items()]
    rows += [obs(f"unlisted-{i:02d}", 10.0, RUN) for i in range(12)]  # the snapshot grows
    records.write_jsonl(sorted(rows, key=records.sort_key), paths.records / f"{BREW}.jsonl")
    index = sorted(IndexEntry("brew-cask", match_key(f"font-{f}"), f, "direct", "") for f in IDS)
    stageio.dump_stage(paths, "alias_index", index)
    (paths.sources_config).mkdir(parents=True)
    (paths.sources_config / f"{BREW}.toml").write_text("enabled = true\n")

    store = root / "store"
    last_rows = [obs(f, v, LAST) for f, v in brew_values(LAST).items()]
    write_snapshot(store, BREW, LAST, sorted(last_rows, key=records.sort_key))
    write_snapshot(store, BREW, RUN, sorted(rows, key=records.sort_key))
    if first_run:
        write_seed(store)
        state = State()
    else:
        state = State(
            run_history=({"run_date": LAST.isoformat(), "snapshots": {BREW: LAST.isoformat()}},),
            license_hashes={
                "fam-05": {
                    "level": "L3",
                    "text_sha256": "a" * 64,
                    "text_url": "https://x.example/OFL.txt",
                }
            },
        )
    ctx = stage_ctx(paths, cfg, state, store)
    surveys.run(ctx)
    confidence.run(ctx)
    if not first_run:
        placed = stageio.load_stage(paths, "ranks")
        now = {k: {f: p.order for f, p in ps.items()} for k, ps in placed.items()}
        prev = {k: dict(v) for k, v in now.items() if k != "rising"}
        top = min(prev["overall"], key=prev["overall"].__getitem__)
        prev["overall"][top] = 28  # last month far down: an entry from below 25
        catalog = {
            f: {"member": True, "entered": LAST.isoformat(), "runs_outside": 0}
            for f, o in prev["overall"].items()
            if o <= 30
        }
        catalog["fam-99"] = {"member": True, "entered": LAST.isoformat(), "runs_outside": 1}
        prev = {k: {f: o for f, o in v.items() if f in catalog} for k, v in prev.items()}
        ctx = dataclasses.replace(
            ctx,
            state=dataclasses.replace(
                ctx.state, published_ranks=prev, membership={"catalog": catalog, "top100": {}}
            ),
        )
        jsonio.dump(
            {
                "fam-05": {
                    "level": "L3",
                    "text_sha256": "b" * 64,
                    "text_url": "https://x.example/OFL.txt",
                }
            },
            paths.next_state / "license_hashes.json",
        )
    membership.run(ctx)
    return ctx


@pytest.fixture
def replay_collector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review, "_load_collector", lambda name: Replay())


def pack_bytes(paths: Paths) -> dict[str, bytes]:
    pack = paths.build / review.PACK_DIR
    return {p.name: p.read_bytes() for p in sorted(pack.iterdir())}


@pytest.mark.usefixtures("replay_collector")
def test_stage_writes_review_and_pack_deterministically(tmp_path: Path) -> None:
    ctx = ranked_build(tmp_path)
    review.run(ctx)
    text = (ctx.paths.build / review.REVIEW_FILE).read_text()
    pack = pack_bytes(ctx.paths)
    assert set(pack) == set(review.PACK_FILES)
    review.run(ctx)
    assert (ctx.paths.build / review.REVIEW_FILE).read_text() == text
    assert pack_bytes(ctx.paths) == pack

    assert "- Run of 2026-10-03, against the ranks published by the run of 2026-09-03." in text
    assert "from 28" in text  # the entry from below 25
    assert "| catalog | Family 99 leaves the catalog" not in text  # no name: id shown
    assert "| catalog | fam-99 leaves the catalog (not ranked overall) |" in text
    assert "Family 05: license text changed (aaaaaaaaaaaa → bbbbbbbbbbbb)" in text
    assert "`fot`: stale, using the snapshot of 2026-09-03 (30 days old, stale for 1 run" in text
    assert (
        "`almanac` parent_merge (width cuts folded into the parent; weight halved): 1 font" in text
    )
    assert "Family 40: `homebrew` share up" in text  # the jump, found by re-parsing the store
    assert "`homebrew`: 41 → 48 fonts with a value (+17%)" in text  # coverage change
    # The what-if table is the owner's (ruling of 2026-09-26): in the pack, not review.md.
    assert "## What if" not in text
    anomalies = pack["anomalies.md"].decode()
    assert "## What if" in anomalies
    checks = pack["checks.md"].decode()  # the owner's ruling of 2026-09-26 (data_defaults)
    assert "## Catalog fonts with the default category" in checks
    assert "## Families to watch" in checks
    assert f"`ranks.overall.mix.project` {review.TIMES} 2:" in anomalies
    # no Fonts Over Time weeks yet: its weight this run is the phase-in weight, not its key
    phase = "(phasing in: this run's weight is `sources.fot.phase_in_weight`)"
    assert f"`surveys.project.weights.fot` {review.TIMES} 2 {phase}:" in anomalies
    assert f"`surveys.project.weights.almanac` {review.TIMES} 2:" in anomalies
    assert "surveys.project.weights.fot (phasing in" in pack["what-if.md"].decode()
    # One flag total everywhere: review.md's lists are cut, its total is not.
    total = next(line for line in text.splitlines() if line.startswith("Flags: "))
    assert total in pack["README.md"].decode()
    assert total in anomalies
    growth = text.split("## Snapshot growth")[1].split("## ")[0]
    assert f"- `{BREW}`: " in growth
    assert "2026-09-03 → 2026-10-03" in growth
    for kind in ("entry", "exit", "license", "stale", "term"):
        assert any(f.kind == kind for f in review.analyse(ctx).flags), kind
    assert any(f.kind == "what_if" for f in review.analyse(ctx).all_flags)

    sources = pack["sources.md"].decode()
    assert "| homebrew | 2026-09-03 | 2026-10-03 | 41 → 48 |" in sources
    assert "the last run used no snapshot of it" in sources  # every other source
    assert "- Not measured against the last run: almanac, arch, debian," in text
    top100 = pack["top100.md"].decode()
    assert "## overall" in top100
    assert "| Rank | Font | Tier | Range | Groups |" in top100
    assert "Not compared: this is not the first run" in pack["old-top100.md"].decode()
    assert "## The question (gate R)" in pack["README.md"].decode()


@pytest.mark.usefixtures("replay_collector")
def test_stage_cuts_capped_kinds_to_the_exact_top(tmp_path: Path) -> None:
    ctx = ranked_build(tmp_path)
    a = review.analyse(ctx)
    top = SMALL.display.exact_top
    for f in a.flags:
        if f.kind in review.CAPPED:
            assert a.now[f.rank_key][f.family_id] <= top
    cut = [f for f in a.all_flags if f.kind in review.CAPPED and f not in a.flags]
    if cut:
        assert any("are listed in build/review-pack/anomalies.md" in f.message for f in a.flags)


@pytest.mark.usefixtures("replay_collector")
def test_first_run_compares_with_the_old_top100(tmp_path: Path) -> None:
    ctx = ranked_build(tmp_path, first_run=True)
    path = review.review_pack(ctx)
    assert path == ctx.paths.build / review.PACK_DIR
    old = (path / "old-top100.md").read_text()
    assert "From `_seed/oldlib-2026-09-23/final_manifest.json` in the store." in old
    assert "| Gone Sans | 13 |" in old
    review.run(ctx)
    text = (ctx.paths.build / review.REVIEW_FILE).read_text()
    assert "First run: there are no published ranks to compare with" in text
    assert "## Entries\n\nFonts new to the exact top of a rank, or to the catalog.\n\nNone." in text
    assert "Gone Sans" not in text  # the old list stays in the private pack


@pytest.mark.usefixtures("replay_collector")
def test_what_if_starts_from_the_published_ranks(tmp_path: Path) -> None:
    from tff_catalog import confidence

    ctx = ranked_build(tmp_path)
    scores = stageio.load_stage(ctx.paths, "scores")
    inputs = confidence.load_inputs(ctx, scores)
    rebuilt = {
        k: {f: p.order for f, p in s.placements.items()}
        for k, s in surveys.views(inputs, SMALL).items()
    }
    placed = stageio.load_stage(ctx.paths, "ranks")
    assert rebuilt == {k: {f: p.order for f, p in ps.items()} for k, ps in placed.items()}


def test_stage_without_store_notes_what_it_skipped(tmp_path: Path) -> None:
    ctx = ranked_build(tmp_path)
    ctx = dataclasses.replace(ctx, store=None)
    a = review.analyse(ctx)
    info = [f.message for f in a.flags if f.kind == "info"]
    assert "Sources month on month: skipped, no snapshot store (TFF_STORE is not set)." in info
    assert not [f for f in a.flags if f.kind in ("spearman", "coverage", "share_jump", "growth")]


def test_members_without_an_accepted_link_are_named(tmp_path: Path) -> None:
    from tff_catalog.links import Link, Links

    ctx = ranked_build(tmp_path)
    members = stageio.load_stage(ctx.paths, "membership").members()
    link = Links(Link("https://x.example/"), None, "two_sources")
    stageio.dump_stage(ctx.paths, "links", dict.fromkeys(members[2:], link))
    # members[0] has a candidate link, so gate K asks; members[1] has none: Claude researches.
    candidate = {"candidates": [{"kind": "homepage", "sources": ["x"], "url": "https://y/"}]}
    jsonio.dump({"undecided": {members[0]: candidate}}, ctx.paths.queues / "links.json")
    a = review.analyse(ctx)
    info = [f.message for f in a.flags if f.kind == "info"]
    held = [m for m in info if m.startswith("Held back from the catalog")]
    assert held == [
        "Held back from the catalog until the owner picks a download link at gate K "
        f"(no two sources agree on one): {NAMES[members[0]]} (`{members[0]}`).",
        "Held back from the catalog with no candidate download link at all, so gate K has "
        "nothing to ask yet: Claude researches each official page and proposes an override in "
        f"config/link-overrides.toml, which gate K then asks about: {NAMES[members[1]]} "
        f"(`{members[1]}`).",
    ]
    stageio.dump_stage(ctx.paths, "links", dict.fromkeys(members, link))
    assert not [f for f in review.analyse(ctx).flags if "Held back" in f.message]


def test_held_back_members_name_what_they_wait_for(tmp_path: Path) -> None:
    """A proposed override waits on the owner, a research answer on Claude; neither is
    reported as a pick the owner still has to make."""
    from tff_catalog.links import Link, Links, queue_question

    ctx = ranked_build(tmp_path)
    members = stageio.load_stage(ctx.paths, "membership").members()
    link = Links(Link("https://x.example/"), None, "two_sources")
    stageio.dump_stage(ctx.paths, "links", dict.fromkeys(members[4:], link))
    candidate = {"candidates": [{"kind": "homepage", "sources": ["x"], "url": "https://y/"}]}
    ctx.paths.config.mkdir(parents=True, exist_ok=True)
    (ctx.paths.config / "link-overrides.toml").write_text(
        "schema = 1\n\n[[override]]\n"
        f'family = "{members[0]}"\nname = "{NAMES[members[0]]}"\n'
        f'question = "K-{members[0]}-page"\nprimary = "https://z.example/"\nreason = "r"\n'
    )
    queue = {
        "undecided": dict.fromkeys(members[:3], candidate),
        "overrides": {
            "pending": [f"K-{members[0]}-page"],
            "research": [queue_question(members[1])],
        },
    }
    jsonio.dump(queue, ctx.paths.queues / "links.json")
    info = [f.message for f in review.analyse(ctx).flags if f.kind == "info"]
    held = [m for m in info if m.startswith("Held back from the catalog")]
    assert held == [
        "Held back from the catalog until the owner answers gate K on the link Claude "
        "researched and proposed in config/link-overrides.toml: "
        f"{NAMES[members[0]]} (`{members[0]}`, K-{members[0]}-page).",
        "Held back from the catalog while Claude researches the official page the owner "
        "asked for at gate K, to propose it in config/link-overrides.toml: "
        f"{NAMES[members[1]]} (`{members[1]}`).",
        "Held back from the catalog until the owner picks a download link at gate K "
        f"(no two sources agree on one): {NAMES[members[2]]} (`{members[2]}`).",
        "Held back from the catalog with no candidate download link at all, so gate K has "
        "nothing to ask yet: Claude researches each official page and proposes an override in "
        f"config/link-overrides.toml, which gate K then asks about: {NAMES[members[3]]} "
        f"(`{members[3]}`).",
    ]


def test_stage_needs_ranks(tmp_path: Path) -> None:
    ctx = stage_ctx(Paths.for_root(tmp_path), SMALL, State(), None)
    with pytest.raises(FileNotFoundError, match="run stage 'rank' first"):
        review.run(ctx)


def test_review_md_names_no_private_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Google and Fonts Over Time appear only by rank: no share ratios, no raw terms."""
    ctx = ranked_build(tmp_path)
    monkeypatch.setattr(review, "_load_collector", lambda name: Replay())
    review.run(ctx)
    text = (ctx.paths.build / review.REVIEW_FILE).read_text()
    terms = stageio.load_stage(ctx.paths, "terms")
    private = {
        f"{t.value:g}"
        for view in terms.values()
        for s in ("google", "fot")
        for t in view.get(s, {}).values()
        if t.value is not None and not math.isclose(t.value, round(t.value))
    }
    assert not [v for v in private if v in text]
    for line in text.splitlines():
        if "`google`" in line or "`fot`" in line:
            assert "share" not in line


# --- last month against this month: like with like ------------------------------------------


def tight(cfg: RankingConfig = SMALL) -> RankingConfig:
    """A catalog of 12 of the 48 families: the top 10 of coding reaches outside it."""
    r = dataclasses.replace
    m = cfg.membership
    return r(
        cfg,
        membership=r(
            m,
            catalog_size=12,
            enter=11,
            leave=13,
            extra_top=5,
            top100=r(m.top100, enter=4, leave=6),
        ),
    )


def settled_month(root: Path, cfg: RankingConfig) -> StageContext:
    """A month on from a first run with nothing changed: last month published what
    this month publishes (catalog fonts only, as stage "export" writes them)."""
    from tff_catalog import membership

    first = ranked_build(root, first_run=True, cfg=cfg)
    members = set(stageio.load_stage(first.paths, "membership").members())
    # As export published them: a short exact top (dev_apps) moves its unranked places down.
    placed = review.export_orders(stageio.load_stage(first.paths, "ranks"), cfg.display.exact_top)
    last_month = {
        k: {f: o for f, o in v.items() if f in members} for k, v in placed.items() if k != "rising"
    }
    committed = jsonio.load(first.paths.next_state / "membership.json")
    history = ({"run_date": LAST.isoformat(), "snapshots": {BREW: LAST.isoformat()}},)
    state = dataclasses.replace(
        first.state, run_history=history, published_ranks=last_month, membership=committed
    )
    ctx = dataclasses.replace(first, state=state)
    membership.run(ctx)
    return ctx


@pytest.mark.usefixtures("replay_collector")
def test_an_unchanged_month_raises_no_diff(tmp_path: Path) -> None:
    cfg = tight()
    ctx = settled_month(tmp_path, cfg)
    a = review.analyse(ctx)
    members = set(stageio.load_stage(ctx.paths, "membership").members())
    # the catalog leaves out fonts in coding's exact top: they are ranked, never published
    assert set(review._top(a.now["coding"], cfg.display.exact_top)) - members
    assert len(a.now["overall"]) > len(members)
    shifted = review.export_orders(a.placements, cfg.display.exact_top)
    assert shifted["dev_apps"] != a.now["dev_apps"]  # one group only: no font passes the gate
    assert a.published == {
        k: {f: o for f, o in v.items() if f in members} for k, v in shifted.items()
    }
    diff = [f for f in a.flags if f.kind in ("entry", "exit", "move", "from_below", "rbo")]
    assert diff == []
    assert review.rbo_by_key(a.prev, a.published, cfg) == dict.fromkeys(a.prev, 1.0)
    text = review.render_review(a.prev, a.published, a.flags)
    for key, orders_ in a.prev.items():
        n = len(orders_)
        assert f"| {key} | {n} | {n} | 0 | 0 | 0 | 0 | 0 |" in text


def test_export_orders_move_a_short_tops_unranked_places_as_export_does() -> None:
    placed = {
        "a": Placement(1, 1, None, False),
        "b": Placement(2, None, "101-250", True),  # gate held: no rank inside the top
        "c": Placement(3, None, "101-250", True),
    }
    full = {"a": Placement(1, 1, None, False), "d": Placement(101, None, "101-250", False)}
    got = review.export_orders({"dev_apps": placed, "overall": full}, exact_top=100)
    assert got == {"dev_apps": {"a": 1, "b": 101, "c": 102}, "overall": {"a": 1, "d": 101}}


@pytest.mark.usefixtures("replay_collector")
def test_published_prefers_what_export_wrote(tmp_path: Path) -> None:
    ctx = ranked_build(tmp_path)
    now = {
        k: {f: p.order for f, p in ps.items()}
        for k, ps in stageio.load_stage(ctx.paths, "ranks").items()
    }
    members = set(stageio.load_stage(ctx.paths, "membership").members())
    by_members = {k: {f: o for f, o in v.items() if f in members} for k, v in now.items()}
    written = {k: v for k, v in by_members.items() if k != "rising"}  # Rising not yet published
    target = ctx.paths.next_state / "published_ranks.json"
    jsonio.dump(written, target)
    notes: list[str] = []
    assert review.published(ctx, now, notes) == written
    assert notes == []

    fid = next(iter(written["overall"]))
    jsonio.dump({**written, "overall": {**written["overall"], fid: 999}}, target)  # another build's
    assert review.published(ctx, now, notes) == by_members
    assert notes == [
        "This month's published ranks: build/state/published_ranks.json does not match "
        'ranks.json (rerun stage "export"), so the catalog members\' orders stand in.'
    ]

    target.unlink()
    stageio.stage_path(ctx.paths, "membership").unlink()
    notes = []
    assert review.published(ctx, now, notes) == now
    assert "New and Gone overcount" in notes[0]


@pytest.mark.usefixtures("replay_collector")
def test_rbo_flag_when_last_month_differs(tmp_path: Path) -> None:
    ctx = ranked_build(tmp_path)
    prev = {k: dict(v) for k, v in ctx.state.published_ranks.items()}
    ids = sorted(prev["project"], key=prev["project"].__getitem__)
    prev["project"] = {f: i for i, f in enumerate(reversed(ids), 1)}
    ctx = dataclasses.replace(ctx, state=dataclasses.replace(ctx.state, published_ranks=prev))
    a = review.analyse(ctx)
    [flag] = [f for f in a.flags if f.kind == "rbo"]
    assert flag.rank_key == "project"
    assert flag.message.endswith("with last month's top list, under 0.9")
    assert float(flag.message.split()[0]) < 0.9


@pytest.mark.usefixtures("replay_collector")
def test_pack_readme_asks_the_gate_r_question(tmp_path: Path) -> None:
    ctx = ranked_build(tmp_path)
    readme = (review.review_pack(ctx) / "README.md").read_text()
    assert "- (a) Approve.\n- (b) Change weights in ranking.toml." in readme
    assert "- (d) Another round." in readme
    assert "`data/reviews/review/<date>.toml`" in readme
    assert "Up to 3 rounds." in readme


def test_specimen_failures_and_numbering_gaps_are_flagged(tmp_path: Path) -> None:
    from tff_catalog.specimens.stage import Preview

    previews = {
        "a": Preview("specimens/a.svg", "0" * 64),
        "b": Preview(None, None, ("specimen_failed",), "no font file to draw from"),
    }
    flags = review.specimen_flags(previews, {"b": "Bee Sans"})
    assert [(f.kind, f.message) for f in flags] == [
        ("specimen", "Bee Sans: no font file to draw from")
    ]
    catalog = tmp_path / "catalog.json"
    ranks = [{"overall": {"rank": r}, "coding": {"rank": None}} for r in (1, 2, 4)]
    jsonio.dump({"fonts": [{"ranks": r} for r in ranks]}, catalog)
    gaps = review.rank_gap_flags(catalog)
    assert [(f.rank_key, f.message) for f in gaps] == [
        ("overall", "exact ranks 3 are not in the catalog")
    ]


def test_review_md_lists_only_cross_check_moves_of_three_places_or_more() -> None:
    now = {"overall": {"a": 2, "b": 3, "c": 40}}
    flags = [
        review.Flag("crosscheck", "A moves from 2 to 5 (150%) under RRF", "overall", "a"),
        review.Flag("crosscheck", "B moves from 3 to 2 (33%) under RRF", "overall", "b"),
        review.Flag("crosscheck", "C moves from 40 to 90 (125%) under RRF", "overall", "c"),
        review.Flag("what_if", "`w` x 2: nothing moves"),
    ]
    public, everything = review._finish(flags, [], [], now, top=10)
    kept = [f.family_id for f in public if f.kind == "crosscheck"]
    assert kept == ["a"]  # b moves one place; c is below the exact top
    assert not [f for f in public if f.kind == "what_if"]
    assert len([f for f in everything if f.kind != "info"]) == 4
