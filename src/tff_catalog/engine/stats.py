"""Rank statistics (design-m1 §6). Owner: agent P7.

- ``probit``: Φ⁻¹, from ``statistics.NormalDist``.
- ``midrank_p``: the mid-rank percentile (L + U) / 2n, with L and U as counts
  (values < x and values <= x), so the top font gets p < 1 and a finite z
  (contradiction C2). Censored values are ``-inf`` and form one tie block at the
  bottom.
- ``hazen_quantile``: the quantile at position h = n·p + 0.5. It inverts
  ``midrank_p`` exactly: for a pool of n values, the mid-rank p of the i-th
  value gives h = i, and a tie block's p lands inside the block.
"""

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from statistics import NormalDist

_STANDARD_NORMAL = NormalDist()

# h is (L + U + 1) / 2 in exact arithmetic when p is a mid-rank over a pool of
# the same size; float division and multiplication can miss that half-integer
# by a few ulps. Snapping within this distance makes the inversion exact, and
# moves any other h by less than 1e-9, far below 1 / (n + 1) for any real
# universe, so no genuine position is ever snapped.
_SNAP = 1e-9


def probit(p: float) -> float:
    """Φ⁻¹(p): ``statistics.NormalDist().inv_cdf(p)``, for 0 < p < 1.

    Raises ``ValueError`` outside the open interval (and for NaN).
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"probit needs 0 < p < 1, got {p!r}")
    return _STANDARD_NORMAL.inv_cdf(p)


def midrank_p(x: float, pool_sorted: Sequence[float], include_self: bool) -> float:
    """Mid-rank percentile (L + U) / 2n of ``x`` in an ascending pool.

    L and U are counts: values < x and values <= x (``bisect_left`` and
    ``bisect_right``). When ``include_self`` is false, x is counted in (+1 on U
    and on n). Censored and missing-in-frame values are ``-inf``, so they form
    one tie block at the bottom and get p = c/2n automatically.

    With ``include_self`` true, ``x`` must occur in the pool. The result always
    lies in (0, 1), and is 0.5 when every value ties, so ``probit`` of it is
    finite. Raises ``ValueError`` for NaN, or for a value missing from the pool
    when ``include_self`` is true.
    """
    if math.isnan(x):
        raise ValueError("midrank_p: x is NaN")
    lower = bisect_left(pool_sorted, x)
    upper = bisect_right(pool_sorted, x)
    n = len(pool_sorted)
    if include_self:
        if upper == lower:
            raise ValueError(f"midrank_p: {x!r} is not in the pool (include_self=True)")
    else:
        upper += 1
        n += 1
    return (lower + upper) / (2 * n)


def hazen_quantile(sorted_values: Sequence[float], p: float) -> float:
    """The Hazen p-quantile: position h = n·p + 0.5, linear interpolation, clamped to the ends.

    It exactly inverts mid-ranks, so a source identical to the ruler maps onto
    the ruler's own z values.

    ``sorted_values`` is ascending and non-empty; 0 <= p <= 1. h is 1-based:
    h <= 1 gives the smallest value and h >= n the largest. Non-decreasing in p
    and in every value. Raises ``ValueError`` for an empty sequence or p outside
    [0, 1].
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("hazen_quantile: no values")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"hazen_quantile needs 0 <= p <= 1, got {p!r}")
    h = n * p + 0.5
    twice = round(2 * h)
    if abs(2 * h - twice) <= _SNAP:
        h = twice / 2
    if h <= 1.0:
        return sorted_values[0]
    if h >= n:
        return sorted_values[-1]
    k = math.floor(h)
    low, high = sorted_values[k - 1], sorted_values[k]
    frac = h - k
    if frac == 0.0 or low == high:
        return low
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("hazen_quantile interpolates between finite values only")
    # Rounding in low + frac·(high - low) can overshoot high by an ulp; the
    # clamp keeps the map monotone across interpolation segments.
    return min(max(low + frac * (high - low), low), high)
