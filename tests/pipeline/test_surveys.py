"""Stage "rank" (milestone-1 step 11): effective weights, per-survey scores, overall, the stage.

Everything runs on synthetic terms (``synth``): about 80 invented families
with a latent popularity, seen by every enabled engine source of
``config/ranking.toml`` with noise, gaps and censoring. The Linux sources
abstain in ``desktop_chosen`` for a few families (``ABSTAIN``), and two
families (``LINUX_ONLY``) have desktop evidence from Linux sources only.

``test_views.py`` imports ``synth``, ``inputs_of`` and the constants.
"""

import dataclasses
import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.helpers import ROOT

from tff_catalog import jsonio, stageio, surveys
from tff_catalog.config_model import Config, RankingConfig, from_mapping, load_toml
from tff_catalog.corrections import Term
from tff_catalog.engine import equate
from tff_catalog.engine import fuse as fusion
from tff_catalog.facts import Facts
from tff_catalog.license_l3 import L3Result
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, SourceKey, attrs, write_jsonl
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe

RANKING: RankingConfig = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))
SOURCES = RANKING.sources.all()
DESKTOP = tuple(sorted(RANKING.surveys.desktop.weights))
PROJECT = tuple(sorted(RANKING.surveys.project.weights))
LINUX = frozenset(name for name, src in SOURCES.items() if src.linux)
ABSTAIN = frozenset({"fam-003", "fam-010", "fam-021", "fam-042"})
LINUX_ONLY = ("lin-with-project", "lin-desktop-only")  # desktop evidence from Linux sources only
GUARDED = "guard-case"  # high on the desktop, low in projects: the combined guard would fire
RUN_DATE = date(2026, 10, 3)
FULL = dict.fromkeys(SOURCES, 80)  # every source overlaps the ruler fully


@dataclass(frozen=True)
class Synth:
    terms: dict[str, dict[str, dict[str, Term]]]  # terms.json: {rank key: {source: {id: Term}}}
    counts: dict[str, float]  # ruler_counts.json
    names: dict[str, str]
    monospace: frozenset[str]


def _term(value: float, source: str, floor: float = 4.0) -> Term:
    group = SOURCES[source].group
    if value < floor:
        return Term(value=value, state="censored", group=group, reason="below_floor")
    return Term(value=value, state="observed", group=group)


def synth(seed: int = 11, n: int = 80) -> Synth:
    """Synthetic terms.json and ruler counts (module docstring); deterministic by seed."""
    rng = np.random.default_rng(seed)
    ids = [f"fam-{i:03d}" for i in range(n)]
    latent = {fid: float(x) for fid, x in zip(ids, rng.normal(0.0, 1.0, n), strict=True)}
    names = {fid: f"Synth {fid[4:]}" for fid in ids}
    monospace = frozenset(fid for i, fid in enumerate(ids) if i % 3 == 0)
    counts = {fid: float(round(math.exp(3.0 + 1.5 * latent[fid]))) for fid in ids}
    installed: dict[str, dict[str, Term]] = {}
    for source in (*DESKTOP, *PROJECT):
        if not SOURCES[source].enabled:
            continue
        fam: dict[str, Term] = {}
        for fid in ids:
            seen = rng.random() < 0.85 or (source in LINUX and fid in ABSTAIN)
            noise = float(rng.normal(0.0, 0.5))
            if seen:
                fam[fid] = _term(math.exp(2.0 + latent[fid] + noise), source)
        installed[source] = fam
    for i, fid in enumerate(LINUX_ONLY):
        names[fid] = f"Linux Only {i}"
        for source in sorted(LINUX):
            installed[source][fid] = _term(200.0 + 10 * i, source)
    installed["google"][LINUX_ONLY[0]] = _term(50.0, "google")
    installed["npm_fontsource"][LINUX_ONLY[0]] = _term(40.0, "npm_fontsource")
    names[GUARDED] = "Guard Case"
    counts[GUARDED] = 5000.0
    installed["homebrew"][GUARDED] = _term(900.0, "homebrew")
    installed["github"][GUARDED] = _term(800.0, "github")
    for source in ("almanac", "google", "npm_fontsource"):
        installed[source][GUARDED] = _term(4.5, source)
    chosen = {
        s: {f: t for f, t in fam.items() if not (s in LINUX and (f in ABSTAIN or f in LINUX_ONLY))}
        for s, fam in installed.items()
    }
    terms = {
        "desktop_installed": {s: installed[s] for s in DESKTOP},
        "desktop_chosen": {s: chosen[s] for s in DESKTOP},
        "project": {s: installed[s] for s in PROJECT if s in installed},
    }
    return Synth(terms, counts, names, monospace)


def inputs_of(s: Synth, cfg: RankingConfig = RANKING, **kw: Any) -> surveys.RankInputs:
    """``RankInputs`` for ``s`` under ``cfg`` (keywords go to ``surveys.rank_inputs``)."""
    ruler = equate.build_ruler(s.counts)
    return surveys.rank_inputs(s.terms, ruler, s.names, cfg, monospace=s.monospace, **kw)


def canonical(obj: object) -> bytes:
    return jsonio.canonical_bytes(stageio.encode(obj))


# --- effective weights ----------------------------------------------------------------------------


def test_desktop_weights_scale_with_overlap() -> None:
    overlaps = {"homebrew": 80, "arch": 25, "github": 10, "nerd": 50, "debian": 60}
    w = surveys.effective_weights(RANKING, "desktop_chosen", overlaps, frozenset(), {})
    assert w == pytest.approx(
        {"homebrew": 1.0, "arch": 0.375, "github": 0.0, "nerd": 0.3, "debian": 0.25}
    )


def test_stale_dropped_and_disabled_sources_weigh_nothing() -> None:
    w = surveys.effective_weights(RANKING, "desktop_installed", FULL, frozenset({"nerd"}), {})
    assert w["nerd"] == 0.0
    assert w["homebrew"] == 1.0
    assert not RANKING.sources.flutter.enabled
    w = surveys.effective_weights(RANKING, "dev_apps", FULL, frozenset(), {})
    assert w["flutter"] == 0.0


def test_m9_group_shares_while_fot_phases_in() -> None:
    """Ruling M9 (b): web 0.55 split 0.1375 / 0.20625 / 0.20625; Expo carries apps alone."""
    w = surveys.effective_weights(RANKING, "project", FULL, frozenset(), {"fot": True})
    assert w == pytest.approx(
        {
            "fot": 0.55 * 0.10 / 0.40,
            "almanac": 0.55 * 0.15 / 0.40,
            "google": 0.55 * 0.15 / 0.40,
            "npm_fontsource": 0.15,
            "ecosystems": 0.10,
            "jsdelivr": 0.05,
            "npm_expo": 0.15,
            "flutter": 0.0,
        }
    )
    assert round(w["fot"], 2) == 0.14  # methodology §5: "about 0.14 (FOT), 0.21 (Almanac)"
    assert round(w["almanac"], 2) == 0.21
    assert math.fsum(w.values()) == pytest.approx(1.0)


def test_m9_group_shares_after_phase_in() -> None:
    w = surveys.effective_weights(RANKING, "project", FULL, frozenset(), {"fot": False})
    assert w["fot"] == pytest.approx(0.25)
    assert w["almanac"] == pytest.approx(0.15)
    assert math.fsum(w.values()) == pytest.approx(1.0)


def test_m9_overlap_scaling_stays_inside_the_group() -> None:
    overlaps = FULL | {"almanac": 25}  # w_eff 0.075
    w = surveys.effective_weights(RANKING, "project", overlaps, frozenset(), {"fot": True})
    web = 0.10 + 0.075 + 0.15
    assert w["fot"] == pytest.approx(0.55 * 0.10 / web)
    assert w["almanac"] == pytest.approx(0.55 * 0.075 / web)
    assert w["fot"] + w["almanac"] + w["google"] == pytest.approx(0.55)
    assert w["npm_fontsource"] == pytest.approx(0.15)


def test_m9_a_group_with_no_weight_contributes_nothing() -> None:
    w = surveys.effective_weights(RANKING, "project", FULL, frozenset({"npm_expo"}), {})
    assert w["npm_expo"] == 0.0
    assert w["flutter"] == 0.0
    assert math.fsum(w.values()) == pytest.approx(0.85)  # W_project without the apps group


@given(
    overlaps=st.dictionaries(st.sampled_from(PROJECT), st.integers(0, 120)),
    phasing=st.booleans(),
    dropped=st.frozensets(st.sampled_from(PROJECT)),
)
def test_m9_groups_keep_their_share_whatever_happens_inside(
    overlaps: dict[str, int], phasing: bool, dropped: frozenset[str]
) -> None:
    w = surveys.effective_weights(RANKING, "project", overlaps, dropped, {"fot": phasing})
    for group in RANKING.project_group_shares.values():
        total = math.fsum(w[s] for s in group.sources)
        assert total == pytest.approx(group.share) or total == 0.0
        assert all(w[s] >= 0 for s in group.sources)


def test_dev_apps_uses_the_rescaled_project_weights_as_they_are() -> None:
    """Ruling M10 (a): npm 0.15, ecosyste.ms 0.10, Expo 0.10, Flutter 0.05 when on; no groups."""
    w = surveys.effective_weights(RANKING, "dev_apps", FULL, frozenset(), {})
    assert w == pytest.approx(
        {"npm_fontsource": 0.15, "ecosystems": 0.10, "npm_expo": 0.10, "flutter": 0.0}
    )


def test_coding_weights_have_no_chocolatey() -> None:
    """Ruling T3 (a): Chocolatey is not a source, so it has no Coding weight."""
    w = surveys.effective_weights(RANKING, "coding", FULL, frozenset(), {})
    assert w == pytest.approx(
        {"nerd": 1.0, "homebrew": 1.0, "arch": 0.75, "github": 0.5, "npm_fontsource": 0.5}
    )
    assert "chocolatey" not in SOURCES


def test_overall_weights_are_the_mix_of_the_survey_weights() -> None:
    w = surveys.effective_weights(RANKING, "overall", FULL, frozenset(), {"fot": True})
    desktop = surveys.effective_weights(RANKING, "desktop_chosen", FULL, frozenset(), {})
    w_desktop = math.fsum(desktop.values())
    assert w["homebrew"] == pytest.approx(0.5 * 1.0 / w_desktop)
    assert math.fsum(w[s] for s in DESKTOP) == pytest.approx(0.5)
    assert math.fsum(w[s] for s in PROJECT) == pytest.approx(0.5)
    assert surveys.effective_weights(RANKING, "rising", FULL, frozenset(), {}) == {}


def test_a_source_may_feed_one_mix_key_only() -> None:
    with pytest.raises(ValueError, match="two keys"):
        surveys.mix_weights({"a": 0.5, "b": 0.5}, {"a": {"homebrew": 1.0}, "b": {"homebrew": 1.0}})


def test_fot_phases_in_until_eight_weekly_snapshots() -> None:
    weeks = [f"2026-W{w}" for w in range(39, 46)]
    assert surveys.phase_in(RANKING, weeks) == {"fot": True}
    assert surveys.phase_in(RANKING, [*weeks, "2026-W46"]) == {"fot": False}


def test_sources_stale_beyond_the_window_are_dropped() -> None:
    assert surveys.stale_dropped(RANKING, {"pkgstats": {"stale_runs": 3}}) == {"arch"}
    assert surveys.stale_dropped(RANKING, {"pkgstats": {"stale_runs": 2}}) == frozenset()
    assert surveys.stale_dropped(RANKING, {"npm": {"stale_runs": 5}}) == {
        "npm_fontsource",
        "npm_expo",
    }


# --- one survey -----------------------------------------------------------------------------------


def test_views_rank_every_survey_with_its_effective_weights() -> None:
    s = synth()
    inputs = inputs_of(s, phasing={"fot": True})
    scores = surveys.views(inputs, RANKING)
    assert list(scores) == list(surveys.RANK_KEYS)
    for key in ("desktop_chosen", "desktop_installed", "project"):
        assert scores[key].key == key
        assert scores[key].weights == inputs.weights[key]
        assert scores[key].placements
        orders = sorted(p.order for p in scores[key].placements.values())
        assert orders == list(range(1, len(orders) + 1))
    assert scores["project"].weights["fot"] == pytest.approx(0.55 * 0.10 / 0.40)


def test_scores_follow_the_latent_popularity() -> None:
    s = synth()
    scores = surveys.views(inputs_of(s), RANKING)
    fused = scores["desktop_installed"].fused
    ids = [f for f in fused if f.startswith("fam-")]
    ruler = equate.build_ruler(s.counts)
    top = sorted(ids, key=lambda f: -ruler[f])[:10]
    bottom = sorted(ids, key=lambda f: ruler[f])[:10]
    assert min(fused[f].score for f in top) > max(fused[f].score for f in bottom)


def test_a_font_with_only_censored_terms_is_not_ranked() -> None:
    s = synth()
    for source in DESKTOP:
        for key in ("desktop_chosen", "desktop_installed"):
            s.terms[key][source]["fam-005"] = _term(1.0, source)
    scores = surveys.views(inputs_of(s), RANKING)["desktop_installed"]
    assert scores.fused["fam-005"].observed == 0
    assert scores.fused["fam-005"].terms > 0
    assert "fam-005" not in scores.placements


def test_a_switched_off_source_adds_no_group() -> None:
    """Overlap below 15 switches a source off: its term makes no group for the gate."""
    s = synth()
    for key in ("desktop_chosen", "desktop_installed"):
        for source in DESKTOP:
            s.terms[key][source].pop("fam-007", None)
        s.terms[key]["homebrew"]["fam-007"] = _term(500.0, "homebrew")
        s.terms[key]["debian"] = dict(list(s.terms[key]["debian"].items())[:10])
        s.terms[key]["debian"]["fam-007"] = _term(500.0, "debian")
    inputs = inputs_of(s)
    assert inputs.weights["desktop_installed"]["debian"] == 0.0
    fused = surveys.views(inputs, RANKING)["desktop_installed"].fused["fam-007"]
    assert fused.groups == ("homebrew",)


def test_the_two_group_gate_holds_single_group_fonts_out_of_the_top() -> None:
    """Every synthetic font scores inside the exact top 100, so a one-group font is held."""
    s = synth()
    for key in ("desktop_chosen", "desktop_installed"):
        for source in DESKTOP:
            s.terms[key][source].pop("fam-001", None)
        s.terms[key]["homebrew"]["fam-001"] = _term(1e6, "homebrew")
    s.counts["fam-001"] = 1e7
    scores = surveys.views(inputs_of(s), RANKING)["desktop_installed"]
    placement = scores.placements["fam-001"]
    assert len(scores.placements) < RANKING.display.exact_top
    assert placement.gate_held
    assert placement.rank is None
    assert placement.band == "101-250"
    for fid, p in scores.placements.items():
        assert p.gate_held == (len(scores.fused[fid].groups) < 2), fid


def test_score_survey_on_its_own_matches_views() -> None:
    s = synth()
    inputs = inputs_of(s)
    alone = surveys.score_survey(
        inputs.terms["project"], inputs.weights["project"], inputs.ruler, RANKING
    )
    within = surveys.views(inputs, RANKING)["project"]
    assert alone.key == ""
    assert canonical(alone.fused) == canonical(within.fused)
    assert canonical(dataclasses.replace(alone, key="project")) == canonical(within)


def test_scores_do_not_depend_on_mapping_order() -> None:
    s = synth()
    flipped = Synth(
        terms={
            k: {src: dict(reversed(fam.items())) for src, fam in reversed(ts.items())}
            for k, ts in reversed(s.terms.items())
        },
        counts=dict(reversed(s.counts.items())),
        names=dict(reversed(s.names.items())),
        monospace=s.monospace,
    )
    a = surveys.views(inputs_of(s), RANKING)
    b = surveys.views(inputs_of(flipped), RANKING)
    assert canonical(a) == canonical(b)


# --- overall ----------------------------------------------------------------------------------------


def _z(inputs: surveys.RankInputs, key: str, fid: str) -> dict[str, float]:
    out = {}
    for source, fam in inputs.terms[key].items():
        if inputs.weights[key].get(source, 0.0) > 0 and fid in fam:
            xs = {f: t.value if t.state == "observed" else -math.inf for f, t in fam.items()}
            out[source] = equate.equate_source(xs, inputs.ruler)[fid]
    return out


def test_overall_reuses_each_surveys_guard_factors() -> None:
    s = synth()
    inputs = inputs_of(s)
    scores = surveys.views(inputs, RANKING)
    combined = _z(inputs, "desktop_chosen", GUARDED) | _z(inputs, "project", GUARDED)
    g = RANKING.engine.guard
    rerun = fusion.guard_factors(combined, g.gap, g.min_terms, g.factor, g.basis)
    assert any(f != 1.0 for f in rerun.values()), "the combined terms would fire the guard"
    assert scores["overall"].fused[GUARDED].guard == ()
    for fid, fused in scores["overall"].fused.items():
        own: set[tuple[str, float]] = set()
        for key in ("desktop_chosen", "project"):
            if fid in scores[key].fused:
                own |= set(scores[key].fused[fid].guard)
        assert set(fused.guard) == own, fid


def test_overall_is_the_mixed_weighted_mean_shrunk_once() -> None:
    """Methodology §4: S = (κ·ΣM·μ0 + Σ v'z) / (κ·ΣM + Σ v') with v_s = M_g·w_s/W_g
    and each term's guard factor from its own survey, computed here by hand."""
    s = synth()
    inputs = inputs_of(s, phasing={"fot": True})
    scores = surveys.views(inputs, RANKING)
    mix, e = RANKING.ranks.overall.mix, RANKING.engine
    checked = 0
    for fid in ("fam-000", "fam-017", "fam-042", GUARDED, LINUX_ONLY[0]):
        num, den = e.kappa * math.fsum(mix.values()) * e.mu0, e.kappa * math.fsum(mix.values())
        for key, m_g in mix.items():
            w = inputs.weights[key]
            w_total = math.fsum(w.values())
            guard = dict(scores[key].fused[fid].guard) if fid in scores[key].fused else {}
            for source, z in _z(inputs, key, fid).items():
                v = m_g * w[source] / w_total * guard.get(source, 1.0)
                num, den = num + v * z, den + v
                checked += 1
        assert scores["overall"].fused[fid].score == pytest.approx(num / den, abs=1e-12), fid
    assert checked > 20


def test_overall_with_all_of_the_mix_on_desktop_equals_most_chosen() -> None:
    """v_s = M·w_s/W and W = Σ M: with M_desktop = 1 the shrinkage is exactly the survey's."""
    cfg = dataclasses.replace(
        RANKING,
        ranks=dataclasses.replace(
            RANKING.ranks,
            overall=dataclasses.replace(
                RANKING.ranks.overall, mix={"desktop_chosen": 1.0, "project": 0.0}
            ),
        ),
    )
    scores = surveys.views(inputs_of(synth(), cfg), cfg)
    chosen, overall = scores["desktop_chosen"], scores["overall"]
    for fid, fused in chosen.fused.items():
        assert overall.fused[fid].score == pytest.approx(fused.score, abs=1e-12)
    assert {f: p.order for f, p in overall.placements.items()} == {
        f: p.order for f, p in chosen.placements.items()
    }


def test_overall_uses_most_chosen_not_most_installed() -> None:
    s = synth()
    scores = surveys.views(inputs_of(s), RANKING)
    overall = scores["overall"]
    assert LINUX_ONLY[1] not in overall.fused  # only Linux desktop terms, all abstaining
    assert LINUX_ONLY[0] in overall.placements  # ranked on its project terms
    assert overall.fused[LINUX_ONLY[0]].groups == ("google", "npm_registry")
    assert math.fsum(overall.weights.values()) == pytest.approx(1.0)


# --- the stage ------------------------------------------------------------------------------------


def write_stage_files(paths: Paths, s: Synth, weeks: int = 3) -> None:
    ids = sorted(s.names)
    families = {
        fid: Family(
            id=fid,
            family=s.names[fid],
            keys=(),
            sources=("synth",),
            first_seen=date(2025, 1, 1),
            minted_from=s.names[fid],
        )
        for fid in ids
    }
    stageio.dump_stage(paths, "universe", Universe(families=families, unmapped=()))
    stageio.dump_stage(
        paths,
        "facts",
        {
            fid: Facts(
                category="monospace" if fid in s.monospace else "sans-serif",
                is_monospace=fid in s.monospace,
                variable=False,
                static=True,
                basis="synthetic",
            )
            for fid in ids
        },
    )
    stageio.dump_stage(paths, "terms", s.terms)
    stageio.dump_stage(paths, "ruler_counts", s.counts)
    fot = [
        Observation(
            source="fot",
            series=f"2026-W{39 + w}",
            key=SourceKey("fot-name", "Synth Sans"),
            value=5.0,
            unit="sites",
            start=date(2026, 9, 21),
            end=date(2026, 9, 27),
            attrs=attrs(method="browser"),
        )
        for w in range(weeks)
    ]
    write_jsonl(fot, paths.records / f"{RANKING.sources.fot.collector}.jsonl")


def rank_ctx(
    root: Path, smoothing: dict[str, Any] | None = None, run_date: date = RUN_DATE
) -> StageContext:
    # The rank and membership stages read ranking.toml only.
    config = Config(RANKING, None, None, None, None, None, {})  # type: ignore[arg-type]
    return StageContext(
        paths=Paths.for_root(root),
        config=config,
        state=State(smoothing=smoothing or {}),
        run_date=run_date,
        store=None,
        fetcher=None,
        log=logging.getLogger("test.rank"),
    )


def _outputs(root: Path) -> dict[str, bytes]:
    stage = root / "build" / "stage"
    files = [stage / "ruler.json", stage / "scores.json", stage / "ranks.json"]
    return {p.name: p.read_bytes() for p in [*files, root / "build" / "state" / "smoothing.json"]}


def test_stage_writes_ruler_scores_ranks_and_smoothing(tmp_path: Path) -> None:
    s = synth()
    ctx = rank_ctx(tmp_path)
    write_stage_files(ctx.paths, s)
    surveys.run(ctx)
    scores = stageio.load_stage(ctx.paths, "scores")
    ranks = stageio.load_stage(ctx.paths, "ranks")
    assert sorted(scores) == sorted(surveys.RANK_KEYS)
    assert {k: dict(v.placements) for k, v in scores.items()} == ranks
    assert stageio.load_stage(ctx.paths, "ruler") == pytest.approx(equate.build_ruler(s.counts))
    assert scores["project"].weights["fot"] == pytest.approx(0.55 * 0.10 / 0.40)  # 3 weeks
    smoothing = jsonio.load(ctx.paths.next_state / "smoothing.json")
    assert smoothing["month"] == "2026-10"
    assert smoothing["fot_weeks"] == ["2026-W39", "2026-W40", "2026-W41"]
    assert set(smoothing["fot_ewma"]) == {
        f for f, t in s.terms["project"]["fot"].items() if t.state == "observed"
    }
    coding = scores["coding"]
    assert set(coding.fused) <= s.monospace
    assert coding.placements


def test_stage_output_is_byte_identical_on_a_rerun(tmp_path: Path) -> None:
    s = synth()
    a, b = rank_ctx(tmp_path / "a"), rank_ctx(tmp_path / "b")
    for ctx in (a, b):
        write_stage_files(ctx.paths, s)
        surveys.run(ctx)
    surveys.run(a)  # a rerun inside one run reads the committed state, not its own output
    assert _outputs(tmp_path / "a") == _outputs(tmp_path / "b")


def test_fot_is_smoothed_from_last_month(tmp_path: Path) -> None:
    s = synth()
    fot = s.terms["project"]["fot"]
    observed = sorted(f for f, t in fot.items() if t.state == "observed")
    prev = {"month": "2026-09", "fot_ewma": {observed[0]: 3.0}, "fot_weeks": ["2026-W30"]}
    ctx = rank_ctx(tmp_path, prev)
    write_stage_files(ctx.paths, s, weeks=8)
    surveys.run(ctx)
    ruler = equate.build_ruler(s.counts)
    xs = {f: t.value if t.state == "observed" else -math.inf for f, t in fot.items()}
    now = equate.equate_source(xs, ruler)
    lam = RANKING.sources.fot.ewma_lambda
    smoothing = jsonio.load(ctx.paths.next_state / "smoothing.json")
    assert smoothing["fot_ewma"][observed[0]] == pytest.approx(
        lam * now[observed[0]] + (1 - lam) * 3.0
    )
    assert smoothing["fot_ewma"][observed[1]] == pytest.approx(now[observed[1]])
    assert smoothing["fot_ewma_base"] == {observed[0]: 3.0}
    assert len(smoothing["fot_weeks"]) == 9
    scores = stageio.load_stage(ctx.paths, "scores")
    assert scores["project"].weights["fot"] == pytest.approx(0.25)  # phase-in over

    # A second merged run in the same month smooths from the same base again.
    again = rank_ctx(tmp_path / "again", smoothing)
    write_stage_files(again.paths, s, weeks=8)
    surveys.run(again)
    assert jsonio.load(again.paths.next_state / "smoothing.json") == smoothing


def test_l3_failures_leave_every_rank_and_the_ruler(tmp_path: Path) -> None:
    s = synth()
    ctx = rank_ctx(tmp_path)
    write_stage_files(ctx.paths, s)
    failed = L3Result(
        family_id="fam-004",
        level="failed",
        checked_on=RUN_DATE,
        text_url=None,
        text_sha256=None,
        matched=None,
        name_ids=(None, None),
        font_version=None,
        font_file=None,
        problems=("no license text",),
    )
    stageio.dump_stage(ctx.paths, "l3", {"fam-004": failed})
    surveys.run(ctx)
    assert "fam-004" not in stageio.load_stage(ctx.paths, "ruler")
    for scores in stageio.load_stage(ctx.paths, "scores").values():
        assert "fam-004" not in scores.fused


def test_a_source_stale_too_long_is_dropped_by_the_stage(tmp_path: Path) -> None:
    ctx = rank_ctx(tmp_path)
    write_stage_files(ctx.paths, synth())
    jsonio.dump(  # stage "parse"'s part of the next state
        {"pkgstats": {"last_good": "2026-06-03", "stale_runs": 3}},
        ctx.paths.next_state / "stale.json",
    )
    surveys.run(ctx)
    scores = stageio.load_stage(ctx.paths, "scores")
    assert scores["desktop_chosen"].weights["arch"] == 0.0
    assert scores["coding"].weights["arch"] == 0.0
    assert scores["desktop_chosen"].weights["homebrew"] == 1.0
