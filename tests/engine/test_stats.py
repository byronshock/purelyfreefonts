"""engine.stats: probit, mid-rank percentiles (L and U as counts) and the Hazen quantile."""

import math
from statistics import NormalDist

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tff_catalog.engine.stats import hazen_quantile, midrank_p, probit

NEG_INF = -math.inf
pools = st.lists(st.integers(0, 12).map(float), min_size=1, max_size=60).map(sorted)


def test_probit_is_normal_inverse_cdf():
    assert probit(0.5) == 0.0
    assert probit(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert probit(0.1) == NormalDist().inv_cdf(0.1)
    assert probit(0.2) == pytest.approx(-probit(0.8), abs=1e-15)


@pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.5, math.nan])
def test_probit_rejects_p_outside_the_open_interval(p: float):
    with pytest.raises(ValueError, match="0 < p < 1"):
        probit(p)


def test_midrank_counts_not_positions():
    """(L + U) / 2n with L, U = counts < x and <= x: the top font stays below 1 (C2)."""
    pool = [1.0, 2.0, 3.0, 4.0]
    assert midrank_p(4.0, pool, include_self=True) == 7 / 8
    assert midrank_p(1.0, pool, include_self=True) == 1 / 8
    assert midrank_p(2.0, pool, include_self=True) == 3 / 8
    assert math.isfinite(probit(midrank_p(4.0, pool, include_self=True)))


def test_midrank_ties_share_the_block_middle():
    pool = [1.0, 2.0, 2.0, 2.0, 5.0]
    assert midrank_p(2.0, pool, include_self=True) == (1 + 4) / 10


def test_censored_block_sits_at_the_bottom():
    """c censored values (-inf) get p = c/2n automatically."""
    pool = [NEG_INF] * 3 + [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    assert midrank_p(NEG_INF, pool, include_self=True) == 3 / 20


def test_midrank_counts_self_in_when_outside_the_pool():
    pool = [1.0, 2.0, 3.0]
    # L = 1, U = 1 + 1, n = 3 + 1
    assert midrank_p(1.5, pool, include_self=False) == 3 / 8
    assert midrank_p(99.0, pool, include_self=False) == 7 / 8
    assert midrank_p(2.0, pool, include_self=False) == (1 + 3) / 8
    assert midrank_p(5.0, [], include_self=False) == 0.5


def test_midrank_rejects_missing_self_and_nan():
    with pytest.raises(ValueError, match="not in the pool"):
        midrank_p(1.5, [1.0, 2.0], include_self=True)
    with pytest.raises(ValueError, match="NaN"):
        midrank_p(math.nan, [1.0], include_self=False)


@given(pools, st.data())
def test_midrank_is_inside_the_open_interval(pool: list[float], data: st.DataObject):
    x = data.draw(st.sampled_from(pool))
    assert 0.0 < midrank_p(x, pool, include_self=True) < 1.0
    y = data.draw(st.integers(-1, 13).map(float))
    assert 0.0 < midrank_p(y, pool, include_self=False) < 1.0


def test_hazen_positions_and_interpolation():
    values = [10.0, 20.0, 30.0, 40.0]
    assert hazen_quantile(values, 1 / 8) == 10.0  # h = 1
    assert hazen_quantile(values, 3 / 8) == 20.0  # h = 2
    assert hazen_quantile(values, 0.5) == 25.0  # h = 2.5
    assert hazen_quantile(values, 0.0) == 10.0  # clamped low
    assert hazen_quantile(values, 1.0) == 40.0  # clamped high
    assert hazen_quantile([7.0], 0.3) == 7.0


def test_hazen_rejects_bad_input():
    with pytest.raises(ValueError, match="no values"):
        hazen_quantile([], 0.5)
    with pytest.raises(ValueError, match="0 <= p <= 1"):
        hazen_quantile([1.0], 1.2)


@given(st.lists(st.integers(-50, 50).map(lambda i: i / 7), min_size=1, max_size=200).map(sorted))
def test_hazen_exactly_inverts_midranks(values: list[float]):
    """Every value's own mid-rank maps back onto it exactly, ties included."""
    for value in values:
        assert hazen_quantile(values, midrank_p(value, values, include_self=True)) == value


@given(
    st.lists(st.floats(-5, 5), min_size=1, max_size=50).map(sorted),
    st.floats(0, 1),
    st.floats(0, 1),
)
def test_hazen_is_monotone_and_bounded(values: list[float], p: float, q: float):
    low, high = sorted((p, q))
    a, b = hazen_quantile(values, low), hazen_quantile(values, high)
    assert values[0] <= a <= b <= values[-1]
