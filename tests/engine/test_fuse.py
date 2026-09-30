"""engine.fuse: the outlier guard and the shrunk weighted mean."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tff_catalog.engine.fuse import Fused, fuse, guard_factors

SOURCES = ["arch", "debian", "fot", "github", "homebrew", "npm"]


def test_guard_needs_min_terms():
    assert guard_factors({"a": 0.0, "b": 9.0}) == {"a": 1.0, "b": 1.0}
    assert guard_factors({"a": 0.0, "b": 0.4, "c": 2.5}) == {"a": 1.0, "b": 1.0, "c": 0.5}
    assert guard_factors({"a": 0.0, "b": 0.4, "c": 2.5}, min_terms=4) == dict.fromkeys("abc", 1.0)


def test_one_extreme_term_can_halve_every_weight():
    """The others' mean includes the outlier: with n terms, one term c z away
    from n - 1 agreeing terms also pushes each of them c/(n-1) from its others'
    mean, so past 1.5·(n-1) the literal rule halves every weight."""
    assert guard_factors({"a": 0.0, "b": 0.0, "c": 9.0}) == dict.fromkeys("abc", 0.5)
    assert guard_factors({"a": 0.0, "b": 0.0, "c": 0.0, "d": 9.0}) == dict.fromkeys("abcd", 0.5)
    assert guard_factors({"a": 0.0, "b": 0.0, "c": 0.0, "d": 4.0}) == {
        "a": 1.0,
        "b": 1.0,
        "c": 1.0,
        "d": 0.5,
    }


def test_guard_gap_is_strict():
    assert guard_factors({"a": 0.0, "b": 0.0, "c": 1.5}) == dict.fromkeys("abc", 1.0)
    assert guard_factors({"a": 0.0, "b": 0.0, "c": 1.5}, gap=1.49)["c"] == 0.5


def test_guard_is_simultaneous_and_unweighted():
    """Each term is judged against the others' unguarded values, all at once."""
    factors = guard_factors({"a": 0.0, "b": 4.0, "c": 1.0})
    # a vs mean(4, 1) = 2.5: gap 2.5; b vs mean(0, 1) = 0.5: gap 3.5; c vs 2.0: gap 1.0
    assert factors == {"a": 0.5, "b": 0.5, "c": 1.0}
    # Here a one-at-a-time guard would differ: once a and b were judged (and,
    # say, pulled to their others' means 2.0 and 3.0), c would sit exactly 1.5
    # from 2.5 and escape. Judged at once, all three gaps (2, 2, 4) exceed 1.5.
    assert guard_factors({"a": 0.0, "b": 0.0, "c": 4.0}) == dict.fromkeys("abc", 0.5)


def test_guard_custom_factor_and_keys_sorted():
    factors = guard_factors({"z": 0.0, "y": 0.0, "x": 5.0}, factor=0.25)
    assert list(factors) == ["x", "y", "z"]
    assert factors["x"] == 0.25


def test_guard_rejects_unequated_terms():
    with pytest.raises(ValueError, match="equated z"):
        guard_factors({"a": -math.inf, "b": 0.0, "c": 0.0})


def test_fuse_formula():
    fused = fuse(
        {"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 0.5}, 3.0, 0.2, 0.0, observed=frozenset("ab")
    )
    assert fused.score == pytest.approx((1.0 + 1.0) / (0.6 + 1.5))
    assert fused == Fused(fused.score, 1.5, 2, 2, ("a", "b"), ())


def test_fuse_shrinks_toward_mu0():
    fused = fuse({"a": 2.0}, {"a": 1.0}, 1.0, 1.0, 1.0)
    assert fused.score == pytest.approx(1.5)
    assert fused.observed == 0  # nothing named observed: a censored term


def test_fuse_no_terms_is_the_prior():
    assert fuse({}, {}, 2.0, 0.2, 0.3).score == 0.3
    assert fuse({}, {}, 0.0, 0.0, 0.3).score == 0.3
    assert fuse({"a": 1.0}, {"a": 0.0}, 0.0, 0.0, -0.1) == Fused(-0.1, 0.0, 0, 0, (), ())


def test_zero_weight_terms_are_not_terms():
    """A source switched off by overlap_scale (w_eff = 0) contributes nothing, not even a group."""
    terms = {"a": 1.0, "b": 3.0, "c": -2.0}
    both = fuse(terms, {"a": 1.0, "b": 1.0, "c": 0.0}, 2.0, 0.2, 0.0, observed=frozenset(terms))
    alone = fuse(
        {"a": 1.0, "b": 3.0}, {"a": 1.0, "b": 1.0}, 2.0, 0.2, 0.0, observed=frozenset("ab")
    )
    assert both == alone
    missing = fuse(terms, {"a": 1.0, "b": 1.0}, 2.0, 0.2, 0.0, observed=frozenset(terms))
    assert missing == alone


def test_guard_factors_multiply_weights_and_are_reported():
    terms = {"a": 0.0, "b": 0.0, "c": 3.0}
    guard = guard_factors(terms)
    fused = fuse(terms, dict.fromkeys(terms, 1.0), 3.0, 0.2, 0.0, guard=guard)
    assert fused.weight == 2.5
    assert fused.score == pytest.approx(1.5 / (0.6 + 2.5))
    assert fused.guard == (("c", 0.5),)
    unguarded = fuse(terms, dict.fromkeys(terms, 1.0), 3.0, 0.2, 0.0)
    assert unguarded.guard == ()
    assert unguarded.weight == 3.0


def test_groups_count_observed_terms_only():
    terms = {"arch": 1.0, "debian": 0.5, "homebrew": 2.0, "github": 1.5}
    groups = {"arch": "arch", "debian": "debian", "homebrew": "homebrew", "github": "homebrew"}
    fused = fuse(
        terms,
        dict.fromkeys(terms, 1.0),
        4.0,
        0.2,
        0.0,
        groups=groups,
        observed=frozenset({"homebrew", "github", "arch"}),
    )
    assert fused.terms == 4
    assert fused.observed == 3
    assert fused.groups == ("arch", "homebrew")  # gate M5: github shares homebrew's group


def test_groups_default_to_sources_and_must_cover_observed():
    fused = fuse(
        {"a": 1.0, "b": 1.0}, {"a": 1.0, "b": 1.0}, 2.0, 0.2, 0.0, observed=frozenset("ab")
    )
    assert fused.groups == ("a", "b")
    with pytest.raises(ValueError, match="no independence group"):
        fuse({"a": 1.0}, {"a": 1.0}, 1.0, 0.2, 0.0, groups={}, observed=frozenset("a"))


def test_fuse_rejects_bad_input():
    with pytest.raises(ValueError, match="negative or NaN weight"):
        fuse({"a": 1.0}, {"a": -1.0}, 1.0, 0.2, 0.0)
    with pytest.raises(ValueError, match=">= 0"):
        fuse({"a": 1.0}, {"a": 1.0}, 1.0, -0.2, 0.0)
    with pytest.raises(ValueError, match="equated z"):
        fuse({"a": math.inf}, {"a": 1.0}, 1.0, 0.2, 0.0)


@pytest.mark.parametrize(
    ("w_eff", "w_total", "kappa", "mu0", "match"),
    [
        ({"a": math.nan}, 1.0, 0.2, 0.0, "NaN weight"),
        ({"a": 1.0}, math.nan, 0.2, 0.0, "w_total"),
        ({"a": 1.0}, math.inf, 0.2, 0.0, "w_total"),
        ({"a": 1.0}, 1.0, math.nan, 0.0, "kappa"),
        ({"a": 1.0}, 1.0, 0.2, math.nan, "mu0"),
    ],
)
def test_fuse_rejects_nan_rather_than_dropping_it(
    w_eff: dict[str, float], w_total: float, kappa: float, mu0: float, match: str
):
    """NaN fails every comparison, so without a check it would silently drop a term
    (a NaN weight) or the prior (a NaN W_g, which clamps S to the term's z)."""
    with pytest.raises(ValueError, match=match):
        fuse({"a": 1.0}, w_eff, w_total, kappa, mu0)


z_values = st.floats(-4, 4, allow_nan=False)
weights = st.floats(0, 2, allow_nan=False)


@st.composite
def fuse_inputs(draw: st.DrawFn) -> tuple[dict[str, float], dict[str, float], float, float, float]:
    sources = draw(st.lists(st.sampled_from(SOURCES), unique=True, max_size=6))
    terms = {s: draw(z_values) for s in sources}
    w_eff = {s: draw(weights) for s in sources}
    w_total = math.fsum(w_eff.values()) + draw(st.floats(0, 3))
    return terms, w_eff, w_total, draw(st.sampled_from([0.0, 0.2, 1.0])), draw(z_values)


@given(fuse_inputs(), st.booleans())
def test_shrinkage_bounds(inputs: tuple, guarded: bool):
    """min(μ0, min z) <= S <= max(μ0, max z), exactly."""
    terms, w_eff, w_total, kappa, mu0 = inputs
    guard = guard_factors(terms) if guarded else None
    fused = fuse(terms, w_eff, w_total, kappa, mu0, guard=guard)
    live = [terms[s] for s in terms if w_eff[s] > 0]
    assert min([mu0, *live]) <= fused.score <= max([mu0, *live])
    assert 0.0 <= fused.weight <= math.fsum(w_eff.values())


@given(fuse_inputs(), st.permutations(range(6)))
def test_fuse_ignores_mapping_order(inputs: tuple, perm: list[int]):
    terms, w_eff, w_total, kappa, mu0 = inputs
    order = [s for _, s in sorted(zip(perm, SOURCES, strict=True)) if s in terms]
    shuffled_terms = {s: terms[s] for s in order}
    shuffled_w = {s: w_eff[s] for s in reversed(order)}
    guard = guard_factors(terms)
    assert fuse(terms, w_eff, w_total, kappa, mu0, guard=guard) == fuse(
        shuffled_terms, shuffled_w, w_total, kappa, mu0, guard=guard_factors(shuffled_terms)
    )


@given(fuse_inputs(), st.data())
def test_score_is_monotone_in_each_term(inputs: tuple, data: st.DataObject):
    terms, w_eff, w_total, kappa, mu0 = inputs
    if not terms:
        return
    source = data.draw(st.sampled_from(sorted(terms)))
    raised = terms | {source: min(4.0, terms[source] + data.draw(st.floats(0, 3)))}
    assert (
        fuse(raised, w_eff, w_total, kappa, mu0).score
        >= fuse(terms, w_eff, w_total, kappa, mu0).score
    )
