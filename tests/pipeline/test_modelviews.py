"""The measurement model's ranks (modelviews): each font's row, the engine's order and gate
on it, the quantile ranges (confidence), and the review's PIT flag. Every number here is
synthetic."""

import itertools
from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np
import pytest
from tests.helpers import ROOT

from tff_catalog import confidence, modelviews, review
from tff_catalog.config import load_config
from tff_catalog.engine import builders, measure
from tff_catalog.paths import Paths

CFG = load_config(Paths.for_root(ROOT)).ranking
MODEL = replace(CFG.model, views=("dev_apps",))
RANKING = replace(CFG, model=MODEL)


def synth(n: int = 400, seed: int = 3) -> tuple[measure.MixedFit, dict[str, dict[str, Any]]]:
    """Two sources on the same fonts: ecosyste.ms dependents (a negative binomial with a
    bulk) and npm (log-normal); font f-000's npm count is far off what its dependents say."""
    rng = np.random.default_rng(seed)
    eta = rng.standard_normal(n)
    ids = np.array([f"f-{i:03d}" for i in range(n)])
    deps = rng.negative_binomial(3.0, 3.0 / (3.0 + 2.0 + np.exp(1.0 + 1.2 * eta))).astype(float)
    npm = np.exp(8.0 + 1.5 * eta + 1.0 * rng.standard_normal(n))
    npm[0] = np.exp(8.0 + 1.5 * eta[0] + 6.0)  # an outlier: its sources conflict
    terms = (
        measure.CountTerm(builders.ECOSYSTEMS, ids, deps, np.ones(n)),
        measure.CountTerm(builders.NPM_FONTSOURCE, ids, npm, np.ones(n), dist="lognormal"),
    )
    fit = measure.fit_mixed(measure.Mixed(terms))
    group = {"ecosystems": "ecosystems", "npm_fontsource": "npm"}
    frames = {
        s: {str(f): {"state": "observed", "group": g, "value": 1.0} for f in ids}
        for s, g in group.items()
    }
    return fit, frames


@pytest.fixture(scope="module")
def model() -> tuple[measure.MixedFit, dict[str, modelviews.ModelEntry]]:
    fit, frames = synth()
    return fit, modelviews.entries(fit, frames, MODEL)


def test_every_font_gets_its_score_sd_quantiles_and_shares(model) -> None:
    fit, rows = model
    eap, sd = fit.scores()
    assert list(rows) == [str(f) for f in fit.model.ids]
    first = rows["f-001"]
    assert first.score == pytest.approx(eap[1])
    assert first.sd == pytest.approx(sd[1])
    assert first.low < first.score < first.high
    assert first.terms == 2
    assert set(first.z) == {"ecosystems", "npm_fontsource"}
    assert sum(first.info.values()) == pytest.approx(1.0)


def test_a_group_counts_only_above_its_bulk(model) -> None:
    fit, rows = model
    term = fit.model.counts[0]
    bulk = float(np.exp(fit.theta[0]))  # one package each, no exposure
    for row, fid in enumerate(fit.model.ids):
        above = term.y[row] > bulk
        assert ("ecosystems" in rows[str(fid)].groups) == above, fid
    assert all("npm" in r.groups for r in rows.values())  # no bulk worth the name in npm here


def test_the_outlier_is_flagged_for_review(model) -> None:
    _, rows = model
    assert "npm_fontsource" in rows["f-000"].pit or "ecosystems" in rows["f-000"].pit
    flagged = sum(1 for r in rows.values() if r.pit)
    assert flagged < 10  # a 0.1% tail, two terms, 400 fonts: the planted one and few others


def test_the_engine_orders_gates_and_bands_the_model_view(model) -> None:
    _, rows = model
    names = {fid: fid.upper() for fid in rows}
    scores = modelviews.survey_scores("dev_apps", rows, names, RANKING)
    places = scores.placements
    order = sorted(places, key=lambda f: places[f].order)
    assert [places[f].order for f in order] == list(range(1, len(rows) + 1))
    free = [f for f in order if not places[f].gate_held]
    assert all(rows[a].score >= rows[b].score for a, b in itertools.pairwise(free))
    for fid, p in places.items():
        held = len(rows[fid].groups) < CFG.engine.top100_min_groups
        if p.order <= CFG.display.exact_top or p.gate_held:
            assert p.gate_held == held, fid
        assert scores.fused[fid].observed == rows[fid].terms  # every term counts (q18a)
        assert scores.fused[fid].guard == ()  # no guard in the model views (q2)


def test_equal_rounded_scores_order_by_sd_then_name() -> None:
    def entry(score: float, sd: float) -> modelviews.ModelEntry:
        z = {"ecosystems": score, "npm_fontsource": score}
        return modelviews.ModelEntry(score, sd, score - 1, score + 1, 2, ("a", "b"), z, {}, ())

    rows = {
        "b": entry(1.0000001, 0.2),
        "a": entry(1.0000004, 0.2),  # the same score to 6 decimals: then the name
        "c": entry(1.0, 0.1),  # more precise: first
        "d": entry(2.0, 0.5),
    }
    places = modelviews.survey_scores("dev_apps", rows, {f: f for f in rows}, RANKING).placements
    assert sorted(places, key=lambda f: places[f].order) == ["d", "c", "a", "b"]


def test_a_font_with_only_fonts_over_time_terms_is_not_ranked() -> None:
    """fot_only_needs_another_source (2026-10-20): a font is ranked only with a term from a
    source other than Fonts Over Time, an interval included; one with only Fonts Over Time's
    terms has neither a place nor a score."""

    def entry(score: float, *sources: str) -> modelviews.ModelEntry:
        z = dict.fromkeys(sources, score)
        return modelviews.ModelEntry(score, 0.2, score - 1, score + 1, 2, ("a", "b"), z, {}, ())

    rows = {
        "fot-only": entry(3.0, "fot"),
        "fot-and-almanac": entry(2.0, "fot", "almanac"),  # the Almanac's below-list interval
        "npm": entry(1.0, "npm_fontsource"),
    }
    assert [modelviews.rankable(e) for e in rows.values()] == [False, True, True]
    scores = modelviews.survey_scores("project", rows, {f: f for f in rows}, RANKING)
    assert set(scores.placements) == set(scores.fused) == {"fot-and-almanac", "npm"}
    assert scores.placements["fot-and-almanac"].order == 1


def test_a_quantile_range_is_the_rank_at_the_font_s_own_quantiles() -> None:
    scores = {"a": 3.0, "b": 2.0, "c": 1.0, "d": 0.0}
    quantiles = {"a": (2.5, 3.5), "b": (0.5, 2.6), "c": (0.9, 1.1), "d": (-1.0, 0.1)}
    assert confidence.quantile_range(scores, quantiles, "a") == (1, 1)
    assert confidence.quantile_range(scores, quantiles, "b") == (2, 3)  # below a, above c
    assert confidence.quantile_range(scores, quantiles, "c") == (3, 3)
    assert confidence.quantile_range(scores, quantiles, "d") == (4, 4)


def test_the_pit_flag_names_sources_inside_the_catalog_only() -> None:
    def entry(pit: tuple[str, ...]) -> modelviews.ModelEntry:
        return modelviews.ModelEntry(0.0, 0.1, -0.1, 0.1, 2, (), {}, {}, pit)

    models = {"project": {"a": entry(("google",)), "b": entry(()), "c": entry(("fot", "npm_expo"))}}
    orders = {"project": {"a": 3, "b": 1, "c": 900}}
    flags = review.pit_flags(models, orders, {"a": "Alpha"}, limit=500)
    assert [(f.kind, f.rank_key, f.family_id) for f in flags] == [("pit", "project", "a")]
    assert flags[0].message == "Alpha (3): `google`"


def test_the_config_names_only_views_with_a_model() -> None:
    from tff_catalog import config

    with pytest.raises(config.ConfigError, match="has no model"):
        config.check_ranking(replace(CFG, model=replace(CFG.model, views=("coding",))))
    config.check_ranking(RANKING)
    assert set(modelviews.MODEL_VIEWS) == {"project", "dev_apps"}
    assert modelviews.DIGITS == 6


def test_fit_info_round_trips_through_its_stage_file(tmp_path) -> None:
    from tff_catalog import stageio

    paths = Paths.for_root(tmp_path)
    info = modelviews.FitInfo(date(2026, 10, 17), "a" * 64, "pending", 1741, (), ("Expo",))
    stageio.dump_stage(paths, "fits", {"project": info})
    assert stageio.load_stage(paths, "fits") == {"project": info}
    row = modelviews.ModelEntry(1.0, 0.2, 0.7, 1.3, 3, ("g",), {"google": 0.9}, {"google": 1.0}, ())
    stageio.dump_stage(paths, "model", {"project": {"inter": row}})
    assert stageio.load_stage(paths, "model") == {"project": {"inter": row}}
