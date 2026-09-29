"""Stage "confidence" (methodology §6, milestone-1 step 13): ranges, tiers, determinism.

The engine and ``surveys`` are other agents' work, so these tests rank with a
small fused-mean ranker over synthetic z values. It has the property that
matters here: its orders depend on the weights.
"""

import dataclasses
import logging
import math
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from tests.helpers import ROOT

from tff_catalog import confidence, stageio
from tff_catalog.confidence import Confidence, Layout
from tff_catalog.config_model import RANK_KEYS, RankingConfig, from_mapping, load_toml
from tff_catalog.corrections import Term
from tff_catalog.license_l3 import L3Result
from tff_catalog.paths import Paths
from tff_catalog.records import SourceKey
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.surveys import RankInputs
from tff_catalog.universe import Family, Universe

RANKING: RankingConfig = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))
UNCERTAINTY = RANKING.uncertainty
TIERS = RANKING.tiers

DESKTOP = {"homebrew": 1.0, "arch": 0.75, "github": 0.5, "nerd": 0.3, "debian": 0.25}
# Project weights after gate M9 (b): web 0.55, code 0.30, apps 0.15 (flutter off).
PROJECT = {
    "fot": 0.25,
    "almanac": 0.15,
    "google": 0.15,
    "npm_fontsource": 0.15,
    "ecosystems": 0.10,
    "jsdelivr": 0.05,
    "npm_expo": 0.15,
    "flutter": 0.0,
}
W_DESKTOP, W_PROJECT = sum(DESKTOP.values()), sum(PROJECT.values())
WEIGHTS = {
    "desktop_chosen": DESKTOP,
    "desktop_installed": DESKTOP,
    "project": PROJECT,
    "overall": {
        **{s: 0.5 * w / W_DESKTOP for s, w in DESKTOP.items()},
        **{s: 0.5 * w / W_PROJECT for s, w in PROJECT.items()},
    },
    "coding": {"nerd": 1.0, "homebrew": 1.0, "arch": 0.75, "github": 0.5, "npm_fontsource": 0.5},
    "dev_apps": {"npm_fontsource": 0.15, "ecosystems": 0.10, "npm_expo": 0.10, "flutter": 0.0},
}
GROUP = {
    "homebrew": "homebrew",
    "arch": "arch",
    "github": "github_counters",
    "nerd": "nerd",  # its own group: owner ruling of 2026-09-29 (gate R, R1)
    "debian": "debian",
    "fot": "fot",
    "almanac": "http_archive",
    "google": "google",
    "npm_fontsource": "npm_registry",
    "ecosystems": "npm_registry",
    "jsdelivr": "jsdelivr",
    "npm_expo": "npm_registry",
    "flutter": "flutter",
}
N_FAMILIES = 30


def synth_inputs(seed: int = 7, n: int = N_FAMILIES) -> RankInputs:
    """Families with a latent popularity, seen by each source with noise and gaps."""
    rng = np.random.default_rng(seed)
    ids = [f"fam-{i:02d}" for i in range(n)]
    latent = dict(zip(ids, rng.normal(0.0, 1.0, n), strict=True))
    by_source: dict[str, dict[str, Term]] = {}
    for source in sorted(GROUP):
        terms = {}
        for fid in ids:
            if rng.random() < 0.8:
                z = float(latent[fid] + rng.normal(0.0, 0.6))
                terms[fid] = Term(value=z, state="observed", group=GROUP[source])
        by_source[source] = terms
    terms_by_key = {
        key: {s: dict(by_source[s]) for s in weights} for key, weights in WEIGHTS.items()
    }
    return RankInputs(
        terms=terms_by_key,
        ruler={fid: float(latent[fid]) for fid in ids},
        weights={key: dict(ws) for key, ws in WEIGHTS.items()},
        names={fid: fid.replace("-", " ").title() for fid in ids},
    )


def fused_rank(inputs: RankInputs, kappa: float = 0.2) -> dict[str, dict[str, int]]:
    """Shrunk weighted mean (methodology §4, μ0 = 0, no guard); ties by id."""
    out = {}
    for key in sorted(inputs.weights):
        weights = inputs.weights[key]
        total = sum(weights.values())
        sums: dict[str, list[float]] = {}
        for source in sorted(inputs.terms.get(key, {})):
            w = weights.get(source, 0.0)
            for fid, term in inputs.terms[key][source].items():
                if w > 0 and term.value is not None:
                    acc = sums.setdefault(fid, [0.0, 0.0])
                    acc[0] += w * term.value
                    acc[1] += w
        score = {fid: num / (kappa * total + den) for fid, (num, den) in sums.items()}
        ordered = sorted(score, key=lambda f: (-score[f], f))
        out[key] = {fid: i for i, fid in enumerate(ordered, 1)}
    return out


class Spy:
    """A ranker that remembers every input it was given."""

    def __init__(self) -> None:
        self.calls: list[RankInputs] = []

    def __call__(self, inputs: RankInputs) -> dict[str, dict[str, int]]:
        self.calls.append(inputs)
        return fused_rank(inputs)


def reversed_inputs(inputs: RankInputs) -> RankInputs:
    """The same inputs with every mapping in reverse insertion order."""

    def rev(m: Mapping[str, Any]) -> dict[str, Any]:
        return {k: (rev(v) if isinstance(v, Mapping) else v) for k, v in reversed(m.items())}

    return RankInputs(
        terms=rev(inputs.terms),
        ruler=rev(inputs.ruler),
        weights=rev(inputs.weights),
        names=rev(inputs.names),
    )


def plan_for(inputs: RankInputs) -> Layout:
    return confidence.layout(RANKING, inputs.weights)


# --- tiers --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rank", "width", "groups", "expected"),
    [
        (1, 0, 2, "A"),
        (1, 0, 1, "B"),  # one group can never be A
        (5, 10, 2, "A"),  # max(10, 0.3 * 5) = 10
        (5, 11, 2, "C"),  # wider than the floor of 10 and than the rank
        (50, 15, 2, "A"),  # max(10, 15) = 15
        (50, 16, 2, "B"),
        (50, 50, 3, "B"),  # B: no wider than the rank
        (50, 51, 3, "C"),
        (200, 60, 2, "A"),
        (200, 61, 2, "B"),
        (20, 12, 1, "B"),
        (3, 4, 0, "C"),
    ],
)
def test_tier_rules(rank: int, width: int, groups: int, expected: str) -> None:
    assert confidence.tier(rank, width, groups, TIERS) == expected


def test_tiers_cover_every_ranked_font() -> None:
    orders = {"overall": {"a": 1, "b": 2, "c": 30}, "project": {"a": 2}}
    ranges = {"overall": {"a": (1, 1), "b": (1, 4), "c": (10, 60)}, "project": {"a": (1, 3)}}
    groups = {"a": 3, "b": 1}
    got = confidence.tiers(ranges, orders, groups, TIERS)
    assert got == {"overall": {"a": "A", "b": "C", "c": "C"}, "project": {"a": "A"}}


def test_rising_is_tier_c_while_in_beta() -> None:
    """Owner ruling of 2026-09-26 (rising_tier): Rising's point ranges would read as A."""
    orders = {"rising": {"a": 1, "b": 2}, "overall": {"a": 1}}
    ranges = {"rising": {"a": (1, 1), "b": (2, 2)}, "overall": {"a": (1, 1)}}
    got = confidence.tiers(ranges, orders, {"a": 3, "b": 2}, TIERS)
    assert got == {"overall": {"a": "A"}, "rising": {"a": "C", "b": "C"}}
    assert confidence.FIXED_TIERS == {"rising": "C"}


def test_tiers_refuse_a_ranked_font_without_a_range() -> None:
    with pytest.raises(KeyError, match="'b'"):
        confidence.tiers({"overall": {"a": (1, 1)}}, {"overall": {"a": 1, "b": 2}}, {}, TIERS)


# --- ranges ---------------------------------------------------------------------------------


def test_ranges_contain_the_point_rank() -> None:
    inputs = synth_inputs()
    point = fused_rank(inputs)
    ranges = confidence.perturb(inputs, UNCERTAINTY, ranker=fused_rank, plan=plan_for(inputs))
    assert set(ranges) == set(point)
    for key, orders in point.items():
        assert set(ranges[key]) == set(orders)
        for fid, order in orders.items():
            lo, hi = ranges[key][fid]
            assert 1 <= lo <= order <= hi, (key, fid, lo, order, hi)


def test_contain_widens_only_when_needed() -> None:
    assert confidence.contain((3, 9), 5) == (3, 9)
    assert confidence.contain((3, 9), 1) == (1, 9)
    assert confidence.contain((3, 9), 12) == (3, 12)


def test_perturb_is_deterministic() -> None:
    inputs = synth_inputs()
    first = confidence.perturb(inputs, UNCERTAINTY, ranker=fused_rank, plan=plan_for(inputs))
    again = confidence.perturb(inputs, UNCERTAINTY, ranker=fused_rank, plan=plan_for(inputs))
    shuffled = reversed_inputs(inputs)
    other_order = confidence.perturb(
        shuffled, UNCERTAINTY, ranker=fused_rank, plan=plan_for(shuffled)
    )
    assert first == again == other_order
    reseeded = dataclasses.replace(UNCERTAINTY, seed=UNCERTAINTY.seed + 1)
    assert confidence.perturb(inputs, reseeded, ranker=fused_rank, plan=plan_for(inputs)) != first


def test_ranges_reflect_disagreement() -> None:
    """A font every source puts first is sure; one only half the weight likes is not."""
    base = synth_inputs()
    terms = {key: {s: dict(t) for s, t in ts.items()} for key, ts in base.terms.items()}
    for ts in terms.values():
        for i, source in enumerate(sorted(ts)):
            ts[source]["sure"] = Term(value=9.0, state="observed", group=GROUP[source])
            split = 6.0 if i % 2 == 0 else -6.0
            ts[source]["split"] = Term(value=split, state="observed", group=GROUP[source])
    inputs = dataclasses.replace(base, terms=terms)
    ranges = confidence.perturb(inputs, UNCERTAINTY, ranker=fused_rank, plan=plan_for(inputs))
    assert ranges["desktop_chosen"]["sure"] == (1, 1)
    lo, hi = ranges["desktop_chosen"]["split"]
    assert hi - lo >= 3


def test_no_runs_gives_point_ranges() -> None:
    inputs = synth_inputs()
    cfg = dataclasses.replace(UNCERTAINTY, runs=0, leave_one_out=False)
    ranges = confidence.perturb(inputs, cfg, ranker=fused_rank)
    point = fused_rank(inputs)
    assert ranges == {key: {f: (o, o) for f, o in orders.items()} for key, orders in point.items()}


# --- the perturbed weights ------------------------------------------------------------------


def test_dirichlet_runs_keep_totals_and_share_survey_draws() -> None:
    inputs = synth_inputs()
    spy = Spy()
    confidence.perturb(inputs, UNCERTAINTY, ranker=spy, plan=plan_for(inputs))
    runs = spy.calls[1 : 1 + UNCERTAINTY.runs]
    assert len(runs) == UNCERTAINTY.runs
    shares: dict[str, list[float]] = {s: [] for s in DESKTOP}
    for run in runs:
        w = run.weights
        for key, base in WEIGHTS.items():
            assert sum(w[key].values()) == pytest.approx(sum(base.values()), abs=1e-12)
        assert w["desktop_chosen"] == w["desktop_installed"]  # one draw per survey
        # overall keeps M_g for each survey and moves with that survey's draw (M_g fixed)
        assert sum(w["overall"][s] for s in DESKTOP) == pytest.approx(0.5, abs=1e-12)
        assert sum(w["overall"][s] for s in PROJECT) == pytest.approx(0.5, abs=1e-12)
        for s in DESKTOP:
            assert w["overall"][s] / 0.5 == pytest.approx(w["desktop_chosen"][s] / W_DESKTOP)
            shares[s].append(w["desktop_chosen"][s] / W_DESKTOP)
        assert w["project"]["flutter"] == 0.0  # a switched-off source stays off
    # Dirichlet(20 · w / W): E[d] = w / W and Var[d] = m (1 - m) / 21 with m = w / W, so
    # the spread pins the concentration (20 · w, without / W, would give 0.064 for homebrew)
    for s, values in shares.items():
        m = DESKTOP[s] / W_DESKTOP
        assert np.mean(values) == pytest.approx(m, abs=0.03)
        expected_sd = math.sqrt(m * (1 - m) / (UNCERTAINTY.concentration + 1))
        assert np.std(values) == pytest.approx(expected_sd, rel=0.2), s


def test_leave_one_source_out_runs() -> None:
    inputs = synth_inputs()
    spy = Spy()
    confidence.perturb(inputs, UNCERTAINTY, ranker=spy, plan=plan_for(inputs))
    loo = spy.calls[1 + UNCERTAINTY.runs :]
    expected = sorted(s for s in GROUP if s != "flutter")  # flutter has no weight anywhere
    assert len(loo) == len(expected)
    for source, run in zip(expected, loo, strict=True):
        for key, ws in run.weights.items():
            if source in WEIGHTS[key]:
                assert ws[source] == 0.0
                assert run.terms[key][source] == {}
    google = loo[expected.index("google")].weights
    # gate M9 (b): the web group keeps 0.55; fot and almanac take google's share pro rata
    assert google["project"]["fot"] == pytest.approx(0.25 * 0.55 / 0.40)
    assert google["project"]["almanac"] == pytest.approx(0.15 * 0.55 / 0.40)
    assert sum(google["project"].values()) == pytest.approx(W_PROJECT)
    assert sum(google["overall"][s] for s in PROJECT) == pytest.approx(0.5)
    expo = loo[expected.index("npm_expo")].weights
    # apps has no other weighted source, so its share is lost
    assert sum(expo["project"].values()) == pytest.approx(W_PROJECT - 0.15)
    assert expo["dev_apps"]["npm_fontsource"] == 0.15  # views have no fixed groups
    brew = loo[expected.index("homebrew")].weights
    assert sum(brew["desktop_chosen"].values()) == pytest.approx(W_DESKTOP - 1.0)


def test_a_run_that_unranks_a_font_counts_it_last() -> None:
    inputs = synth_inputs(n=4)
    terms = {key: {s: dict(by) for s, by in ts.items()} for key, ts in inputs.terms.items()}
    terms["desktop_chosen"]["homebrew"] = {
        "lonely": Term(value=50.0, state="observed", group="homebrew")
    }
    inputs = dataclasses.replace(inputs, terms=terms, weights={"desktop_chosen": dict(DESKTOP)})
    # leave-one-out runs only: arch, debian, github, homebrew, nerd
    loo_only = dataclasses.replace(UNCERTAINTY, runs=0)
    ranges = confidence.perturb(inputs, loo_only, ranker=fused_rank)
    # first in 4 runs; unranked without homebrew, so one place below the 5 fonts of the
    # unperturbed list (that run ranks only 4)
    assert ranges["desktop_chosen"]["lonely"] == (1, 6)
    # with the Dirichlet runs, that one run of 205 falls outside the 5-95% range
    lo, hi = confidence.perturb(inputs, UNCERTAINTY, ranker=fused_rank)["desktop_chosen"]["lonely"]
    assert (lo, hi) == (1, 1)


def test_a_run_that_ranks_nothing_puts_every_font_last() -> None:
    """Losing evidence never moves a font up: an empty run is not 'everyone first'."""
    inputs = synth_inputs(n=6)
    point = fused_rank(inputs)["desktop_chosen"]

    def nothing_without_homebrew(run: RankInputs) -> dict[str, dict[str, int]]:
        orders = fused_rank(run)
        if run.weights["desktop_chosen"].get("homebrew", 1.0) == 0:
            orders["desktop_chosen"] = {}
        return orders

    loo_only = dataclasses.replace(UNCERTAINTY, runs=0)
    ranges = confidence.perturb(
        inputs, loo_only, ranker=nothing_without_homebrew, plan=plan_for(inputs)
    )["desktop_chosen"]
    # 12 leave-one-out runs: the 95% point is the worst run, the empty one
    assert all(hi == len(point) + 1 for _, hi in ranges.values())
    last = max(point, key=point.__getitem__)
    assert ranges[last][0] > 1  # the empty run used to put even the last font first


def test_ranges_are_inverted_cdf_quantiles_of_every_run() -> None:
    """Recompute each range from the runs the ranker saw: 200 Dirichlet + leave-one-out."""
    inputs = synth_inputs()
    spy = Spy()
    ranges = confidence.perturb(inputs, UNCERTAINTY, ranker=spy, plan=plan_for(inputs))
    point = fused_rank(inputs)
    runs = [fused_rank(call) for call in spy.calls[1:]]
    assert len(runs) == UNCERTAINTY.runs + len(GROUP) - 1  # every source but flutter
    lo_q, hi_q = UNCERTAINTY.quantiles
    n = len(runs)
    # no exact hits, so the ceil rule below is exact
    assert (lo_q * n) % 1
    assert (hi_q * n) % 1
    for key, orders in point.items():
        for fid, order in orders.items():
            seen = sorted(run[key].get(fid, max(len(run[key]), len(orders)) + 1) for run in runs)
            # inverted_cdf: the smallest value whose empirical CDF reaches q
            lo, hi = seen[math.ceil(lo_q * n) - 1], seen[math.ceil(hi_q * n) - 1]
            assert ranges[key][fid] == (min(lo, order), max(hi, order)), (key, fid)


def test_a_rank_key_missing_from_a_run_is_skipped() -> None:
    inputs = synth_inputs()
    point = fused_rank(inputs)

    def forgetful(run: RankInputs) -> dict[str, dict[str, int]]:
        orders = fused_rank(run)
        return orders if run is inputs else {k: v for k, v in orders.items() if k != "coding"}

    ranges = confidence.perturb(inputs, UNCERTAINTY, ranker=forgetful, plan=plan_for(inputs))
    assert ranges["coding"] == {f: (o, o) for f, o in point["coding"].items()}
    assert any(lo < hi for lo, hi in ranges["overall"].values())


def test_layout_from_ranking_toml() -> None:
    plan = confidence.layout(RANKING, WEIGHTS)
    assert set(plan.draws["desktop_chosen"].values()) == {"survey:desktop"}
    assert plan.draws["desktop_installed"] == plan.draws["desktop_chosen"]
    assert set(plan.draws["project"].values()) == {"survey:project"}
    assert plan.draws["overall"]["homebrew"] == "survey:desktop"
    assert plan.draws["overall"]["fot"] == "survey:project"
    assert set(plan.draws["coding"].values()) == {"key:coding"}
    assert set(plan.draws["dev_apps"].values()) == {"key:dev_apps"}
    assert plan.shares["project"]["google"] == "web"
    assert plan.shares["overall"]["jsdelivr"] == "code"
    assert "homebrew" not in plan.shares["overall"]
    assert "coding" not in plan.shares
    assert "dev_apps" not in plan.shares


# --- the stage ------------------------------------------------------------------------------

DESKTOP_SOURCES = tuple(DESKTOP)
PROJECT_SOURCES = (
    "fot",
    "almanac",
    "google",
    "npm_fontsource",
    "ecosystems",
    "jsdelivr",
    "npm_expo",
)
RISING_SOURCES = RANKING.ranks.rising.sources
N_STAGE = 48
FAST = dataclasses.replace(RANKING, uncertainty=dataclasses.replace(RANKING.uncertainty, runs=24))


def stage_terms(seed: int = 11) -> tuple[dict[str, Any], dict[str, float], dict[str, Any]]:
    """terms.json, ruler_counts.json and the committed smoothing for N_STAGE families.

    Linux sources abstain for a few families in desktop_chosen; some terms are
    censored; Rising has this month's values and two earlier months of shares.
    """
    rng = np.random.default_rng(seed)
    ids = [f"fam-{i:02d}" for i in range(N_STAGE)]
    latent = dict(zip(ids, rng.normal(0.0, 1.0, N_STAGE), strict=True))

    def terms_for(source: str) -> dict[str, Term]:
        out = {}
        for fid in ids:
            if rng.random() < 0.15:
                continue
            value = float(np.exp(3 + latent[fid] + rng.normal(0.0, 0.5)))
            state = "censored" if value < 8 else "observed"
            out[fid] = Term(value=value, state=state, group=GROUP[source])
        return out

    installed = {s: terms_for(s) for s in DESKTOP_SOURCES}
    abstaining = set(ids[:4])
    chosen = {
        s: {f: t for f, t in ts.items() if not (s in ("arch", "debian") and f in abstaining)}
        for s, ts in installed.items()
    }
    rising_now = {s: terms_for(s) for s in RISING_SOURCES}
    terms = {
        "desktop_chosen": chosen,
        "desktop_installed": installed,
        "project": {s: terms_for(s) for s in PROJECT_SOURCES},
        "rising": rising_now,
    }
    counts = {fid: float(np.exp(4 + latent[fid])) for fid in ids}
    history = {
        s: {f: [float(rng.uniform(0.001, 0.05)) for _ in range(2)] for f in rising_now[s]}
        for s in RISING_SOURCES
    }
    fot_prev = {f: -2.0 * float(latent[f]) for f in ids[::2]}  # far from this month's z
    smoothing = {"month": "2026-09", "rising": history, "fot_ewma": fot_prev}
    return terms, counts, smoothing


def ranked_build(root: Path, store: Path | None = None) -> StageContext:
    """A build where stage "rank" (the real ``surveys.run``) has run on synthetic terms.

    With ``store``, Google's Rising history is where the ruling of 2026-09-26 keeps
    it: in the private store, as the file of a merged run of 2026-09-05.
    """
    from tff_catalog import jsonio, surveys
    from tff_catalog.facts import Facts
    from tff_catalog.store import Store

    paths = Paths.for_root(root)
    terms, counts, smoothing = stage_terms()
    run_history: tuple[dict[str, Any], ...] = ()
    if store is not None:
        merged = date(2026, 9, 5)
        google = smoothing["rising"].pop("google")
        doc = {"schema": 1, "source": "google", "run_date": merged.isoformat()}
        path = surveys.private_history_path(store, "google", merged)
        jsonio.dump({**doc, "month": "2026-09", "history": google}, path)
        run_history = ({"run_date": merged.isoformat()},)
    names = {fid: f"Family {fid[-2:]}" for fid in counts}
    stageio.dump_stage(paths, "terms", terms)
    stageio.dump_stage(paths, "ruler_counts", counts)
    families = {
        fid: Family(
            id=fid,
            family=name,
            keys=(SourceKey("gf-family", name),),
            sources=("google_metadata",),
            first_seen=date(2026, 9, 1),
            minted_from=name,
        )
        for fid, name in names.items()
    }
    stageio.dump_stage(paths, "universe", Universe(families=families, unmapped=()))
    facts = {
        fid: Facts(
            category="monospace" if i % 2 == 0 else "sans-serif",
            is_monospace=i % 2 == 0,  # enough for coding's sources to pass the overlap floor
            variable=False,
            static=True,
            basis="google_metadata",
        )
        for i, fid in enumerate(sorted(names))
    }
    stageio.dump_stage(paths, "facts", facts)
    failed = L3Result(
        family_id="fam-05",
        level="failed",
        checked_on=date(2026, 10, 3),
        text_url=None,
        text_sha256=None,
        matched=None,
        name_ids=(None, None),
        font_version=None,
        font_file=None,
        problems=("no license text",),
    )
    stageio.dump_stage(paths, "l3", {"fam-05": failed})
    ctx = StageContext(
        paths=paths,
        config=SimpleNamespace(ranking=FAST),  # type: ignore[arg-type]
        state=State(smoothing=smoothing, run_history=run_history),
        run_date=date(2026, 10, 3),
        store=None if store is None else Store(store),
        fetcher=None,
        log=logging.getLogger("test.confidence"),
    )
    surveys.run(ctx)
    return ctx


def test_stage_gives_every_ranked_font_a_range_and_a_tier(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = ranked_build(tmp_path)
    with caplog.at_level(logging.WARNING):
        confidence.run(ctx)
    assert "rebuilt ranks differ" not in caplog.text  # load_inputs reproduces ranks.json
    ranks = stageio.load_stage(ctx.paths, "ranks")
    scores = stageio.load_stage(ctx.paths, "scores")
    out = stageio.load_stage(ctx.paths, "confidence")
    assert set(out) == set(ranks) == set(RANK_KEYS)
    assert len(ranks["overall"]) > 30
    assert len(ranks["coding"]) > 10
    assert ranks["rising"]  # the history makes some fonts rise
    assert all("fam-05" not in placements for placements in ranks.values())
    widths = []
    for key, placements in ranks.items():
        assert set(out[key]) == set(placements)
        for fid, placement in placements.items():
            conf = out[key][fid]
            assert isinstance(conf, Confidence)
            lo, hi = conf.range
            assert lo <= placement.order <= hi
            widths.append(hi - lo)
            groups = len(scores[key].fused[fid].groups)
            computed = confidence.tier(placement.order, hi - lo, groups, TIERS)
            assert conf.tier == (confidence.FIXED_TIERS.get(key) or computed)
    assert max(widths) > 0  # the perturbations move something
    before = stageio.stage_path(ctx.paths, "confidence").read_bytes()
    confidence.run(ctx)
    assert stageio.stage_path(ctx.paths, "confidence").read_bytes() == before


def test_stage_rebuilds_rising_from_the_private_history(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Google's Rising history is in the private store (ruling of 2026-09-26): the
    rebuilt ranks read it back, and Google counts towards the fonts that rise."""
    from tff_catalog import jsonio

    ctx = ranked_build(tmp_path / "build", tmp_path / "store")
    with caplog.at_level(logging.WARNING):
        confidence.run(ctx)
    assert "rebuilt ranks differ" not in caplog.text
    rising = stageio.load_stage(ctx.paths, "scores")["rising"]
    assert any("google" in f.groups for f in rising.fused.values())
    assert "google" not in jsonio.load(ctx.paths.next_state / "smoothing.json")["rising"]


def test_stage_widens_and_warns_when_ranks_json_disagrees(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = ranked_build(tmp_path)
    ranks = stageio.load_stage(ctx.paths, "ranks")
    coding = ranks["coding"]
    first = min(coding, key=lambda f: coding[f].order)
    moved = max(p.order for p in coding.values()) + 5
    coding[first] = dataclasses.replace(coding[first], order=moved)
    stageio.dump_stage(ctx.paths, "ranks", ranks)
    with caplog.at_level(logging.WARNING):
        confidence.run(ctx)
    assert "coding: rebuilt ranks differ from ranks.json for 1 fonts" in caplog.text
    out = stageio.load_stage(ctx.paths, "confidence")
    assert out["coding"][first].range[1] == moved
