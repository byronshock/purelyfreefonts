"""engine.crosscheck: coverage-aware RRF, the alternative ruler and the §9 move flags."""

import dataclasses
import math
import re
from collections.abc import Iterable, Mapping

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from tests.helpers import ROOT

from tff_catalog import surveys
from tff_catalog.config_model import RANK_KEYS, RankingConfig, from_mapping, load_toml
from tff_catalog.corrections import Term
from tff_catalog.engine import crosscheck, equate
from tff_catalog.engine.crosscheck import Move
from tff_catalog.engine.order import Placement
from tff_catalog.review import Flag
from tff_catalog.surveys import RankInputs, SurveyScores

CFG = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"), "ranking.toml")
GROUP = {name: src.group for name, src in CFG.sources.all().items()}


def obs(value: float, source: str = "homebrew") -> Term:
    return Term(value=value, state="observed", group=GROUP[source])


def cen(value: float | None = None, source: str = "homebrew") -> Term:
    return Term(value=value, state="censored", group=GROUP[source], reason="below_floor")


def not_covered(source: str = "homebrew") -> Term:
    return Term(value=None, state="not_covered", group=GROUP[source], reason="no_package")


def too_new(source: str = "homebrew") -> Term:
    return Term(value=None, state="too_new", group=GROUP[source])


# --- source ranks -------------------------------------------------------------


def test_source_ranks_midrank_ties_and_censored_at_the_bottom() -> None:
    terms = {
        "homebrew": {
            "a": obs(100.0),
            "b": obs(50.0),
            "c": obs(50.0),
            "d": cen(500.0),  # a censored value never outranks an observed one
            "e": cen(None),
            "f": not_covered(),
            "g": too_new(),
        },
        "arch": {"x": not_covered("arch")},  # ranks nothing: left out
    }
    assert crosscheck.source_ranks(terms) == {"homebrew": {"a": 1, "b": 2, "c": 2, "d": 4, "e": 4}}


@pytest.mark.parametrize("value", [None, math.nan, math.inf])
def test_source_ranks_needs_a_finite_observed_value(value: float | None) -> None:
    bad = Term(value=value, state="observed", group="homebrew")
    with pytest.raises(ValueError, match="homebrew/a"):
        crosscheck.source_ranks({"homebrew": {"a": bad}})


# --- rrf ----------------------------------------------------------------------


def test_rrf_matches_the_formula_by_hand() -> None:
    ranks = {"a": {"x": 1, "y": 2, "z": 3}, "b": {"x": 2, "y": 1}}
    weights = {"a": 1.0, "b": 0.5}
    prior = (1.0 / (60 + 2) + 0.5 / (60 + 1.5)) / 1.5  # a font in the middle of both
    base = 0.2 * 1.5  # κ·W
    expected = {
        "x": (base * prior + 1.0 / 61 + 0.5 / 62) / (base + 1.5),
        "y": (base * prior + 1.0 / 62 + 0.5 / 61) / (base + 1.5),
        "z": (base * prior + 1.0 / 63) / (base + 1.0),  # b does not cover z
    }
    got = crosscheck.rrf(ranks, weights, 60)
    assert got == pytest.approx(expected, rel=1e-12)
    assert list(got) == ["x", "y", "z"]
    assert got["x"] > got["y"]  # first place in the heavier source counts more


def test_rrf_does_not_count_a_missing_source_as_a_zero() -> None:
    # Methodology §3: Monaspace has no Debian package. It leads Homebrew and
    # Arch; JetBrains Mono is second in all three sources.
    fill = {f"f{i:03d}": i + 3 for i in range(200)}
    ranks = {
        "homebrew": {"monaspace": 1, "jetbrains": 2, **fill},
        "arch": {"monaspace": 1, "jetbrains": 2, **fill},
        "debian": {"other": 1, "jetbrains": 2, **fill},
    }
    weights = {"homebrew": 1.0, "arch": 0.75, "debian": 0.25}

    def classic(font: str) -> float:  # classic RRF: a missing entry scores 0
        return sum(w / (60 + ranks[s][font]) for s, w in weights.items() if font in ranks[s])

    assert classic("jetbrains") > classic("monaspace")
    got = crosscheck.rrf(ranks, weights, 60)
    assert got["monaspace"] > got["jetbrains"]
    plain = crosscheck.rrf(ranks, weights, 60, kappa=0.0)  # no prior: plain weighted means
    assert plain["monaspace"] == pytest.approx(1 / 61)
    assert plain["jetbrains"] == pytest.approx(1 / 62)


def test_rrf_prior_keeps_a_font_seen_by_one_light_source_off_the_top() -> None:
    fill = {f"f{i:03d}": i + 3 for i in range(200)}
    ranks = {
        "homebrew": {"lead": 1, "both": 2, **fill},
        "jsdelivr": {"solo": 1, "both": 2, **fill},
    }
    weights = {"homebrew": 1.0, "jsdelivr": 0.05}
    got = crosscheck.rrf(ranks, weights, 60)
    assert got["lead"] > got["both"] > got["solo"]
    plain = crosscheck.rrf(ranks, weights, 60, kappa=0.0)
    assert plain["solo"] > plain["both"]  # without the prior, one light vote wins


def test_rrf_counts_only_sources_with_weight() -> None:
    ranks = {"a": {"x": 1, "y": 2}, "off": {"z": 1, "x": 2}, "unweighted": {"w": 1}, "empty": {}}
    got = crosscheck.rrf(ranks, {"a": 1.0, "off": 0.0, "empty": 3.0})
    assert got == crosscheck.rrf({"a": ranks["a"]}, {"a": 1.0})
    assert set(got) == {"x", "y"}
    assert crosscheck.rrf({}, {}) == {}
    assert crosscheck.rrf(ranks, {}) == {}


def test_rrf_weighs_each_term_by_its_factor() -> None:
    # corrections.Term.factor (the Almanac parent merge) scales a term's weight, as in fusion.
    ranks = {"a": {"x": 1, "y": 2, "z": 3}, "b": {"x": 2, "y": 1, "z": 3}}
    weights = {"a": 1.0, "b": 1.0}
    factors = {"a": {"x": 0.5}, "b": {"z": 0.0}}
    prior = (1 / 62 + 1 / 62) / 2
    base = 0.2 * 2.0  # W keeps the source weights: factors never change the prior's pull
    got = crosscheck.rrf(ranks, weights, 60, factors=factors)
    assert got["x"] == pytest.approx((base * prior + 0.5 / 61 + 1 / 62) / (base + 1.5), rel=1e-12)
    assert got["y"] == pytest.approx((base * prior + 1 / 62 + 1 / 61) / (base + 2.0), rel=1e-12)
    # z's b term has no weight but keeps its place in b's ranks.
    assert got["z"] == pytest.approx((base * prior + 1 / 63) / (base + 1.0), rel=1e-12)
    none = crosscheck.rrf(ranks, weights, 60, factors={"a": {"x": 0.0}, "b": {"x": 0.0}})
    assert set(none) == {"y", "z"}  # no weighted term, no score
    assert crosscheck.rrf(ranks, weights, 60, factors={}) == crosscheck.rrf(ranks, weights, 60)


@pytest.mark.parametrize(
    ("ranks", "k", "kappa", "factors"),
    [
        ({"a": {"x": 0}}, 60, 0.2, None),
        ({"a": {"x": 1}}, -1, 0.2, None),
        ({"a": {"x": 1}}, 60, -0.1, None),
        ({"a": {"x": 1}}, 60, 0.2, {"a": {"x": -0.5}}),
    ],
)
def test_rrf_rejects_bad_input(
    ranks: dict[str, dict[str, int]],
    k: int,
    kappa: float,
    factors: dict[str, dict[str, float]] | None,
) -> None:
    with pytest.raises(ValueError, match="rrf"):
        crosscheck.rrf(ranks, {"a": 1.0}, k, kappa=kappa, factors=factors)


@st.composite
def rrf_cases(draw: st.DrawFn) -> tuple[dict[str, dict[str, int]], dict[str, float]]:
    fonts = [f"f{i}" for i in range(draw(st.integers(1, 12)))]
    ranks: dict[str, dict[str, int]] = {}
    weights: dict[str, float] = {}
    for i in range(draw(st.integers(1, 4))):
        covered = draw(st.permutations(fonts))[: draw(st.integers(0, len(fonts)))]
        ranks[f"s{i}"] = {f: r for r, f in enumerate(covered, start=1)}
        weights[f"s{i}"] = draw(st.sampled_from([0.0, 0.05, 0.25, 0.5, 1.0]))
    return ranks, weights


@given(rrf_cases(), st.integers(0, 100), st.sampled_from([0.0, 0.2, 1.0]))
def test_rrf_stays_between_the_prior_and_its_terms_whatever_the_order(
    case: tuple[dict[str, dict[str, int]], dict[str, float]], k: int, kappa: float
) -> None:
    ranks, weights = case
    got = crosscheck.rrf(ranks, weights, k, kappa=kappa)
    counted = [s for s in ranks if ranks[s] and weights[s] > 0]
    assert set(got) == {f for s in counted for f in ranks[s]}
    if counted:
        total = sum(weights[s] for s in counted)
        prior = sum(weights[s] / (k + (len(ranks[s]) + 1) / 2) for s in counted) / total
    for font, score in got.items():
        bounds = [1 / (k + ranks[s][font]) for s in counted if font in ranks[s]]
        if kappa > 0:
            bounds.append(prior)
        assert min(bounds) - 1e-12 <= score <= max(bounds) + 1e-12
    shuffled = {s: dict(reversed(ranks[s].items())) for s in reversed(ranks)}
    again = crosscheck.rrf(shuffled, dict(reversed(weights.items())), k, kappa=kappa)
    assert again == got
    assert list(again) == list(got)


@given(rrf_cases(), st.data())
def test_rrf_better_rank_never_lowers_the_score(
    case: tuple[dict[str, dict[str, int]], dict[str, float]], data: st.DataObject
) -> None:
    ranks, weights = case
    candidates = [(s, f) for s in sorted(ranks) for f, r in sorted(ranks[s].items()) if r > 1]
    assume(candidates)
    source, font = data.draw(st.sampled_from(candidates))
    r = ranks[source][font]
    ahead = next(f for f, rf in ranks[source].items() if rf == r - 1)
    better = {**ranks, source: {**ranks[source], font: r - 1, ahead: r}}
    before = crosscheck.rrf(ranks, weights).get(font, 0.0)
    assert crosscheck.rrf(better, weights).get(font, 0.0) >= before


# --- moves --------------------------------------------------------------------


def test_moves_flags_only_more_than_the_threshold_sorted_by_rank() -> None:
    ranks = {"a": 10, "b": 10, "c": 20, "d": 1, "e": 5, "gone": 3}
    other = {"a": 13, "b": 14, "c": 12, "d": 2, "e": 5, "new": 1}
    assert crosscheck.moves(ranks, other) == [
        Move("d", 1, 2, 1.0),
        Move("b", 10, 14, 0.4),
        Move("c", 20, 12, 0.4),
    ]  # a moves exactly 30%: not more than the threshold
    assert crosscheck.moves(ranks, other, threshold=0.5) == [Move("d", 1, 2, 1.0)]
    assert crosscheck.moves(ranks, ranks) == []
    with pytest.raises(ValueError, match="ranks start at 1"):
        crosscheck.moves({"a": 0}, {"a": 1})


# --- inputs for the rank-level checks -----------------------------------------

DESKTOP = ("homebrew", "arch", "github", "nerd", "debian")
PROJECT = ("fot", "almanac", "google", "npm_fontsource", "ecosystems", "jsdelivr", "npm_expo")
FONTS = [f"f{i:02d}" for i in range(60)]
EXTRA = [f"j{i}" for i in range(10)]  # Fontsource-only families
# Each source's frame: the families it has a term for (observed or censored).
FRAMES = {
    "homebrew": FONTS,
    "arch": FONTS[:20],
    "github": FONTS[20:45],
    "nerd": FONTS[:10],  # 10 < overlap_off: switched off by the main ruler
    "debian": FONTS[40:],
    "fot": FONTS,
    "almanac": FONTS[:30],
    "google": FONTS,
    "npm_fontsource": FONTS,
    "ecosystems": FONTS[:40],
    "jsdelivr": FONTS[:30] + EXTRA,
    "npm_expo": FONTS[40:],
}


def _weights(cfg: RankingConfig) -> dict[str, dict[str, float]]:
    """Effective weights as surveys.effective_weights gives them for FRAMES and the Homebrew ruler."""
    ruler = set(FONTS)

    def scale(s: str) -> float:
        n = len(set(FRAMES[s]) & ruler)
        return equate.overlap_scale(n, cfg.engine.overlap_full, cfg.engine.overlap_off)

    desktop = {s: cfg.surveys.desktop.weights[s] * scale(s) for s in DESKTOP}
    eff = {s: cfg.surveys.project.weights[s] * scale(s) for s in PROJECT}
    project = {}
    for group in cfg.project_group_shares.values():
        members = [s for s in group.sources if s in eff]
        total = sum(eff[s] for s in members)
        project |= {s: group.share * eff[s] / total if total else 0.0 for s in members}
    return {"desktop_chosen": desktop, "desktop_installed": dict(desktop), "project": project}


def _terms(seed: int, frames: dict[str, list[str]]) -> dict[str, dict[str, dict[str, Term]]]:
    """terms.json's survey views for ``frames``: every 7th term censored, no abstentions."""
    rng = np.random.default_rng(seed)
    fonts = sorted({f for frame in frames.values() for f in frame})
    appeal = {f: float(v) for f, v in zip(fonts, rng.normal(size=len(fonts)), strict=True)}

    def terms_for(source: str) -> dict[str, Term]:
        out = {}
        for i, f in enumerate(frames[source]):
            value = round(float(np.exp(appeal[f] + 0.5 * rng.normal())) * 1000, 3)
            out[f] = cen(value, source) if i % 7 == 6 else obs(value, source)
        return out

    desktop = {s: terms_for(s) for s in DESKTOP}
    project = {s: terms_for(s) for s in PROJECT}
    return {"desktop_chosen": desktop, "desktop_installed": desktop, "project": project}


def _inputs(seed: int = 7) -> RankInputs:
    terms = _terms(seed, FRAMES)
    ruler = equate.build_ruler(crosscheck.ruler_values(terms, "homebrew"))
    names = {f: f"Font {f.upper()}" for f in FONTS + EXTRA}
    return RankInputs(terms=terms, ruler=ruler, weights=_weights(CFG), names=names)


GITHUB_ONLY = ["g0", "g1", "g2"]  # only GitHub releases carry them; they are not Homebrew casks


def _pipeline_inputs(seed: int = 7) -> RankInputs:
    """RankInputs for every rank key, built as stage "rank" builds them (``surveys.rank_inputs``)."""
    terms = _terms(seed, FRAMES | {"github": FRAMES["github"] + GITHUB_ONLY})
    ruler = equate.build_ruler(crosscheck.ruler_values(terms, "homebrew"))
    names = {f: f"Font {f.upper()}" for f in FONTS + EXTRA + GITHUB_ONLY}
    mono = frozenset(FONTS[::3] + GITHUB_ONLY)
    return surveys.rank_inputs(terms, ruler, names, CFG, monospace=mono)


# --- rrf_inputs and rrf_orders ------------------------------------------------


def test_rrf_inputs_for_overall_weigh_each_rank_by_its_mix_share() -> None:
    inputs = _inputs()
    terms, weights = crosscheck.rrf_inputs(inputs, "overall", CFG)
    desktop = inputs.weights["desktop_chosen"]
    w_desktop = sum(w for w in desktop.values() if w > 0)
    assert weights["desktop_chosen:homebrew"] == pytest.approx(0.5 * 1.0 / w_desktop)
    assert weights["project:google"] == pytest.approx(0.5 * inputs.weights["project"]["google"])
    assert "desktop_chosen:nerd" not in weights  # no weight, no source
    assert math.fsum(weights.values()) == pytest.approx(1.0)  # W = Σ M_g
    assert terms["project:google"] is inputs.terms["project"]["google"]
    assert set(terms) == set(weights)
    project = crosscheck.rrf_inputs(inputs, "project", CFG)
    assert project == (dict(inputs.terms["project"]), dict(inputs.weights["project"]))


def _small(exact_top: int) -> RankingConfig:
    display = dataclasses.replace(CFG.display, exact_top=exact_top, bands=(), open_band_from=10**6)
    return dataclasses.replace(CFG, display=display)


def test_rrf_orders_place_fonts_by_the_engines_rules() -> None:
    terms = {
        "homebrew": {
            "solo": obs(1000.0),
            "a": obs(900.0),
            "b": obs(800.0),
            "c": obs(700.0),
            "x": cen(10.0),
        },
        "arch": {"a": obs(0.5, "arch"), "b": obs(0.4, "arch"), "c": obs(0.3, "arch")},
        "nerd": {"solo": obs(99.0, "nerd")},  # no weight: no second group for solo
    }
    weights = {"homebrew": 1.0, "arch": 0.75, "nerd": 0.0}
    inputs = RankInputs(
        terms={"desktop_chosen": terms},
        ruler={},
        weights={"desktop_chosen": weights},
        names={f: f.upper() for f in ("solo", "a", "b", "c", "x")},
    )
    scores = crosscheck.rrf(crosscheck.source_ranks(terms), weights, 60)
    assert max(scores, key=scores.__getitem__) == "solo"
    # solo scores highest but fails the 2-group gate; x is only censored, so never ranked.
    got = crosscheck.rrf_orders(inputs, "desktop_chosen", _small(2))
    assert got == {"a": 1, "b": 2, "solo": 3, "c": 4}
    among = crosscheck.rrf_orders(inputs, "desktop_chosen", _small(2), among=["a", "c", "solo"])
    assert among == {"a": 1, "c": 2, "solo": 3}


def test_rrf_orders_give_a_term_without_weight_no_group() -> None:
    # b leads both sources, but its Arch term has factor 0: one weighted group, so
    # the 2-group gate holds it out of an exact top of 1, as fusion would.
    def inputs(factor: float) -> RankInputs:
        arch_b = dataclasses.replace(obs(0.5, "arch"), factor=factor)
        terms = {
            "homebrew": {"a": obs(900.0), "b": obs(1000.0)},
            "arch": {"a": obs(0.4, "arch"), "b": arch_b},
        }
        return RankInputs(
            terms={"desktop_chosen": terms},
            ruler={},
            weights={"desktop_chosen": {"homebrew": 1.0, "arch": 0.75}},
            names={"a": "A", "b": "B"},
        )

    assert crosscheck.rrf_orders(inputs(1.0), "desktop_chosen", _small(1)) == {"b": 1, "a": 2}
    assert crosscheck.rrf_orders(inputs(0.0), "desktop_chosen", _small(1)) == {"a": 1, "b": 2}


def test_rrf_orders_overall_counts_groups_across_both_surveys() -> None:
    inputs = RankInputs(
        terms={
            "desktop_chosen": {"homebrew": {"a": obs(10.0), "b": obs(5.0)}},
            "project": {"google": {"b": obs(7.0, "google"), "c": obs(3.0, "google")}},
        },
        ruler={},
        weights={"desktop_chosen": {"homebrew": 1.0}, "project": {"google": 0.55}},
        names={"a": "A", "b": "B", "c": "C"},
    )
    got = crosscheck.rrf_orders(inputs, "overall", _small(1))
    assert got["b"] == 1  # the only font with two groups
    assert set(got) == {"a", "b", "c"}


# --- the alternative ruler ----------------------------------------------------


def test_ruler_values_merge_views_and_keep_the_tail_ordered() -> None:
    terms = {
        "desktop_chosen": {"arch": {"a": obs(0.5, "arch")}},  # b abstains here
        "desktop_installed": {
            "arch": {
                "a": obs(0.5, "arch"),
                "b": obs(0.2, "arch"),
                "c": cen(0.001, "arch"),
                "d": cen(None, "arch"),
                "e": too_new("arch"),
                "f": not_covered("arch"),
            }
        },
        "coding": {"arch": {"a": obs(0.7, "arch")}},  # views disagree: the largest wins
        "project": {"jsdelivr": {"a": obs(9.0, "jsdelivr")}},
        # Rising's terms are log-ratios, not counts: never ruler values.
        "rising": {"arch": {"d": obs(1.2, "arch"), "z": obs(0.4, "arch")}},
    }
    got = crosscheck.ruler_values(terms, "arch")
    assert got == {"a": 0.7, "b": 0.2, "c": 0.001, "d": 0.0}
    assert list(got) == sorted(got)
    assert crosscheck.ruler_values(terms, "flutter") == {}


def test_reweigh_swaps_the_overlap_scale_and_keeps_group_shares() -> None:
    inputs = _inputs()
    assert crosscheck.reweigh(inputs, inputs.ruler, CFG) == inputs.weights  # same ruler: unchanged
    new = equate.build_ruler(crosscheck.ruler_values(inputs.terms, "jsdelivr"))
    assert set(new) == set(FONTS[:30] + EXTRA)
    got = crosscheck.reweigh(inputs, new, CFG)
    before = inputs.weights
    d = got["desktop_chosen"]
    assert d["homebrew"] == pytest.approx(1.0 * 30 / 50)  # overlap 60 -> 30
    assert d["arch"] == before["desktop_chosen"]["arch"]  # 20 -> 20
    assert d["github"] == 0.0  # 25 -> 10, under overlap_off: switched off
    assert d["debian"] == 0.0  # 20 -> 0
    assert d["nerd"] == 0.0  # switched off by the main ruler: stays off
    assert got["desktop_installed"] == d
    p, p0 = got["project"], before["project"]
    for group in CFG.project_group_shares.values():
        total = sum(p[s] for s in group.sources if s in p)
        was = sum(p0[s] for s in group.sources if s in p0)
        assert total == pytest.approx(was if group.sources != ("npm_expo", "flutter") else 0.0)
    # Inside "code", jsdelivr (overlap 30 -> 40) gains on npm_fontsource (60 -> 30).
    assert p["jsdelivr"] / p["npm_fontsource"] == pytest.approx(
        (p0["jsdelivr"] * 0.8 / 0.6) / (p0["npm_fontsource"] * 0.6)
    )


def _ladder(source: str, n: int) -> dict[str, Term]:
    return {f: obs(float(100 - i), source) for i, f in enumerate(FONTS[:n])}


def test_reweigh_gives_the_weights_back_bit_for_bit_under_the_same_ruler() -> None:
    # jsdelivr has no terms, so its weight is 0 in the code group. Resplitting the
    # group anyway (share·w/Σw) moves npm_fontsource's weight by an ulp here.
    project = {s: _ladder(s, 60) for s in ("fot", "almanac", "google", "npm_expo")}
    project |= {"npm_fontsource": _ladder("npm_fontsource", 16)}
    project |= {"ecosystems": _ladder("ecosystems", 16)}
    desktop = {"homebrew": _ladder("homebrew", 60)}
    terms_all = {"desktop_chosen": desktop, "desktop_installed": desktop, "project": project}
    ruler = equate.build_ruler(crosscheck.ruler_values(terms_all, "homebrew"))
    inputs = surveys.rank_inputs(terms_all, ruler, {}, CFG)
    assert inputs.weights["project"]["jsdelivr"] == 0.0
    assert crosscheck.reweigh(inputs, inputs.ruler, CFG) == inputs.weights


def test_reweigh_rebuilds_overall_from_its_mix_keys() -> None:
    inputs = _pipeline_inputs()
    assert set(inputs.weights) > {"overall", "coding", "dev_apps"}
    assert crosscheck.reweigh(inputs, inputs.ruler, CFG) == inputs.weights
    new = equate.build_ruler(crosscheck.ruler_values(inputs.terms, "jsdelivr"))
    got = crosscheck.reweigh(inputs, new, CFG)
    assert got["overall"] == surveys.mix_weights(CFG.ranks.overall.mix, got)
    assert got["overall"] != inputs.weights["overall"]
    assert math.fsum(got["overall"].values()) == pytest.approx(1.0)  # W = Σ M_g
    # dev_apps is not a project survey key: no fixed group shares, only the overlap swap.
    dev = got["dev_apps"]
    assert dev["npm_fontsource"] == pytest.approx(
        inputs.weights["dev_apps"]["npm_fontsource"] * 0.6
    )


def _fake_scores(key: str, inputs: RankInputs) -> SurveyScores:
    """Orders by the ruler's z, so the result shows which ruler the pipeline saw."""
    ordered = sorted(inputs.ruler, key=lambda f: (-inputs.ruler[f], f))
    placements = {
        f: Placement(order=i, rank=None, band=None, gate_held=False)
        for i, f in enumerate(ordered, start=1)
    }
    return SurveyScores(key=key, fused={}, placements=placements, overlaps={}, weights={})


def test_rerun_swaps_only_the_ruler_and_the_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, list[RankInputs]] = {"overall": [], "views": []}

    def overall(inputs: RankInputs, cfg: RankingConfig) -> SurveyScores:
        seen["overall"].append(inputs)
        return _fake_scores("overall", inputs)

    def views(inputs: RankInputs, cfg: RankingConfig) -> dict[str, SurveyScores]:
        seen["views"].append(inputs)
        return {k: _fake_scores(k, inputs) for k in ("desktop_chosen", "project")}

    monkeypatch.setattr(surveys, "overall", overall)
    monkeypatch.setattr(surveys, "views", views)
    inputs = _inputs()
    got = crosscheck.rerun_with_ruler(inputs, "jsdelivr", cfg=CFG)
    (rerun,) = seen["overall"]
    assert seen["views"] == []
    assert rerun.ruler == equate.build_ruler(crosscheck.ruler_values(inputs.terms, "jsdelivr"))
    assert rerun.weights == crosscheck.reweigh(inputs, rerun.ruler, CFG)
    assert (rerun.terms, rerun.names) == (inputs.terms, inputs.names)
    assert got == {f: p.order for f, p in _fake_scores("overall", rerun).placements.items()}

    both = crosscheck.rerun_orders(inputs, "jsdelivr", CFG, ["project", "overall", "project"])
    assert list(both) == ["overall", "project"]
    assert len(seen["views"]) == 1
    with pytest.raises(ValueError, match="no rank for coding"):
        crosscheck.rerun_orders(inputs, "jsdelivr", CFG, ["coding"])
    with pytest.raises(ValueError, match="'flutter' has no values"):
        crosscheck.rerun_with_ruler(inputs, "flutter", cfg=CFG)


CHECKED = [k for k in RANK_KEYS if k != "rising"]


def _published(inputs: RankInputs) -> dict[str, dict[str, int]]:
    return {
        k: {f: p.order for f, p in s.placements.items()}
        for k, s in surveys.views(inputs, CFG).items()
    }


def test_rerun_with_the_main_rulers_values_gives_back_the_published_orders() -> None:
    inputs = _inputs()
    orders = {f: p.order for f, p in surveys.overall(inputs, CFG).placements.items()}
    assert crosscheck.rerun_with_ruler(inputs, "homebrew", cfg=CFG) == orders
    alt = crosscheck.rerun_with_ruler(inputs, "jsdelivr", cfg=CFG)
    assert alt != orders
    assert sorted(alt.values()) == list(range(1, len(alt) + 1))

    # Every checked rank key, on inputs built as stage "rank" builds them.
    inputs = _pipeline_inputs()
    published = _published(inputs)
    same = crosscheck.rerun_orders(inputs, "homebrew", CFG, CHECKED)
    assert same == {k: published[k] for k in CHECKED}
    assert all(same[k] for k in CHECKED)


# --- flags --------------------------------------------------------------------


def _reversed(inputs: RankInputs) -> RankInputs:
    """The same inputs with every mapping in reverse order."""

    def rev[V](m: Mapping[str, V]) -> dict[str, V]:
        return {k: m[k] for k in reversed(list(m))}

    return RankInputs(
        terms=rev({k: rev({s: rev(ts[s]) for s in ts}) for k, ts in inputs.terms.items()}),
        ruler=rev(inputs.ruler),
        weights=rev({k: rev(w) for k, w in inputs.weights.items()}),
        names=rev(inputs.names),
    )


def test_flags_on_the_real_pipeline() -> None:
    inputs = _pipeline_inputs()
    orders = _published(inputs)
    got = crosscheck.flags(inputs, orders, CFG)
    assert got == crosscheck.flags(_reversed(inputs), {k: orders[k] for k in reversed(orders)}, CFG)
    assert {f.kind for f in got} == {"crosscheck"}
    assert {f.rank_key for f in got} <= set(CHECKED)
    rrf_label, ruler_label = "coverage-aware RRF (k=60)", "the jsdelivr ruler"
    # Orders only, never a source's values (rulings T2, T4).
    shape = re.compile(
        r"^Font [A-Z0-9]+ (moves from \d+ to \d+ \(\d+(\.\d)?%\)|at \d+ has no place) "
        rf"under ({re.escape(rrf_label)}|{re.escape(ruler_label)})$"
    )
    assert all(shape.match(f.message) for f in got if f.family_id), [f.message for f in got]
    # The rerun switches off every source that overlaps the jsDelivr ruler on fewer
    # than 15 families (GitHub on 10, Debian on 0), so a font whose observed terms
    # all came from those has no place under it: the families only GitHub carries.
    new = equate.build_ruler(crosscheck.ruler_values(inputs.terms, "jsdelivr"))
    rerun = dataclasses.replace(inputs, ruler=new, weights=crosscheck.reweigh(inputs, new, CFG))

    def evidenced(key: str, fid: str) -> bool:
        terms, weights = crosscheck.rrf_inputs(rerun, key, CFG)
        return any(
            w > 0 and fid in terms[s] and terms[s][fid].state == "observed"
            for s, w in weights.items()
        )

    lost = {(f.rank_key, f.family_id) for f in got if "has no place" in f.message}
    assert lost == {
        (k, f) for k in CHECKED if k != "coding" for f in orders[k] if not evidenced(k, f)
    }
    assert {("desktop_chosen", "g0"), ("overall", "g1")} <= lost
    # Coding keeps too few Fontsource families for any source: one flag, not one per font.
    coding = [f for f in got if f.rank_key == "coding" and ruler_label in f.message]
    assert coding == [Flag("crosscheck", f"No font has a place under {ruler_label}", "coding")]
    # RRF places every published font, and each check keeps the published order's font set.
    assert not any("has no place" in f.message and rrf_label in f.message for f in got)
    for key in CHECKED:
        flagged = [
            orders[key][f.family_id] for f in got if f.rank_key == key and rrf_label in f.message
        ]
        assert flagged == sorted(flagged)
    top = crosscheck.flags(inputs, orders, CFG, top=20)
    assert top == [f for f in got if f.family_id is None or orders[f.rank_key][f.family_id] <= 20]

    # With the main ruler as the alternative, only RRF can disagree.
    same = dataclasses.replace(CFG, engine=dataclasses.replace(CFG.engine, alt_ruler="homebrew"))
    assert [f for f in crosscheck.flags(inputs, orders, same) if "ruler" in f.message] == []


def test_flags_do_not_block_when_the_alternative_ruler_has_no_values() -> None:
    inputs = _pipeline_inputs()
    orders = _published(inputs)
    no_ruler = dataclasses.replace(CFG, engine=dataclasses.replace(CFG.engine, alt_ruler="flutter"))
    got = crosscheck.flags(inputs, orders, no_ruler)
    assert got[0] == Flag("crosscheck", "The rerun with the flutter ruler did not run: no values")
    assert got[1:] == [f for f in crosscheck.flags(inputs, orders, CFG) if "RRF" in f.message]
    assert got[1:]


def test_flags_report_both_checks_per_rank_key(monkeypatch: pytest.MonkeyPatch) -> None:
    rrf = {"overall": {"a": 1, "b": 14, "c": 60}, "project": {"a": 2, "b": 3, "d": 4}}
    alt = {"overall": {"a": 3, "b": 10, "c": 40}, "project": {"a": 2, "b": 1}}  # d has no place
    calls: list[tuple[str, tuple[str, ...]]] = []

    def rrf_orders(
        inputs: RankInputs, key: str, cfg: RankingConfig, among: Iterable[str] = ()
    ) -> dict[str, int]:
        assert set(among) == set(orders[key])
        return rrf[key]

    def rerun_orders(
        inputs: RankInputs, ruler: str, cfg: RankingConfig, keys: Iterable[str]
    ) -> dict[str, dict[str, int]]:
        calls.append((ruler, tuple(keys)))
        return alt

    monkeypatch.setattr(crosscheck, "rrf_orders", rrf_orders)
    monkeypatch.setattr(crosscheck, "rerun_orders", rerun_orders)
    orders = {
        "project": {"a": 2, "b": 3, "d": 4},
        "rising": {"a": 1},
        "overall": {"a": 1, "b": 10, "c": 40},
    }
    inputs = RankInputs(terms={}, ruler={}, weights={}, names={"a": "Alpha", "b": "Beta"})
    rrf_label, ruler_label = "coverage-aware RRF (k=60)", "the jsdelivr ruler"
    assert crosscheck.flags(inputs, orders, CFG) == [
        Flag("crosscheck", f"Beta moves from 10 to 14 (40%) under {rrf_label}", "overall", "b"),
        Flag("crosscheck", f"c moves from 40 to 60 (50%) under {rrf_label}", "overall", "c"),
        Flag("crosscheck", f"Alpha moves from 1 to 3 (200%) under {ruler_label}", "overall", "a"),
        Flag("crosscheck", f"Beta moves from 3 to 1 (67%) under {ruler_label}", "project", "b"),
        Flag("crosscheck", f"d at 4 has no place under {ruler_label}", "project", "d"),
    ]
    assert calls == [("jsdelivr", ("overall", "project"))]
    top = crosscheck.flags(inputs, orders, CFG, top=10)
    assert [(f.rank_key, f.family_id) for f in top] == [
        ("overall", "b"),
        ("overall", "a"),
        ("project", "b"),
        ("project", "d"),
    ]
    assert [f.family_id for f in crosscheck.flags(inputs, orders, CFG, top=3)] == ["a", "b"]
    # A flagged move never prints as 30% or less.
    close = {"overall": {"a": 200, "b": 3000, "c": 3}}
    rrf["overall"] = {"a": 261, "b": 3901, "c": 4}
    alt["overall"] = close["overall"]
    percents = [
        re.findall(r"\(([\d.]+%)\)", f.message) for f in crosscheck.flags(inputs, close, CFG)
    ]
    assert percents == [["33%"], ["30.5%"], ["30.1%"]]
    calls.clear()
    assert crosscheck.flags(inputs, {"rising": {"a": 1}}, CFG) == []
    assert calls == []
    with pytest.raises(ValueError, match="unknown rank key"):
        crosscheck.flags(inputs, {"popular": {"a": 1}}, CFG)
