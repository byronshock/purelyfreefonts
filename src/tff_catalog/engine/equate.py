"""The shared scale: the ruler and equating (methodology §3, design-m1 §6). Owner: agent P7.

- ``build_ruler``: z_R = Φ⁻¹(mid-rank percentile) over every ruler family.
- ``equate_source``: a source's values mapped onto the ruler's z values over
  the overlap O_s, by mid-rank percentile and the Hazen quantile. A source
  that sees only popular fonts therefore maps onto the popular end of the
  ruler, and a source identical to the ruler reproduces z_R exactly.
- ``overlap_scale``: the weight factor for a small overlap.

Results are dicts with sorted keys, so iteration order never depends on the
order of the mappings passed in.
"""

import math
from collections.abc import Mapping

from tff_catalog.engine.stats import hazen_quantile, midrank_p, probit


def _check_values(values: Mapping[str, float], what: str) -> None:
    for key, value in values.items():
        if math.isnan(value):
            raise ValueError(f"{what}: {key!r} is NaN")


def build_ruler(counts: Mapping[str, float]) -> dict[str, float]:
    """z_R per family from ruler counts after gates, alias sums and credits, with no floor.

    Casks with no analytics row count 0. z_R = probit(midrank_p(x, all counts)).

    Ties share one z. Every z is finite (mid-rank p is always inside (0, 1)).
    An empty ``counts`` gives an empty ruler. Raises ``ValueError`` for NaN.
    """
    _check_values(counts, "build_ruler")
    pool = sorted(counts.values())
    z_by_value: dict[float, float] = {}
    for value in pool:
        if value not in z_by_value:
            z_by_value[value] = probit(midrank_p(value, pool, include_self=True))
    return {family: z_by_value[counts[family]] for family in sorted(counts)}


def overlap(xs: Mapping[str, float], ruler_z: Mapping[str, float]) -> list[str]:
    """O_s: the families of ``xs`` (observed and censored) that the ruler carries, sorted."""
    return sorted(family for family in xs if family in ruler_z)


def equate_source(xs: Mapping[str, float], ruler_z: Mapping[str, float]) -> dict[str, float]:
    """Map a source's values onto the ruler's scale.

    O_s = the families in both ``xs`` (observed and censored; censored are
    ``-inf``) and ``ruler_z``. For each f in ``xs``: p = midrank_p(x_f, xs over
    O_s), z_s(f) = hazen_quantile(sorted(z_R over O_s), p).

    Ties share a mid-rank within a source (methodology §6), so equal values
    always get equal z, whether or not the family is on the ruler: a family
    outside the ruler whose value occurs in O_s joins that tie (a censored one
    joins the censored block). Only a value absent from O_s is counted into the
    pool for its own p (``include_self=False``, the design's "+1 and n+1 if x
    is not in the pool"), so it lands between its neighbours. z is
    non-decreasing in x. Every result lies within the range of z_R over O_s.
    The result is empty when O_s is empty: there is no scale to map onto, and
    ``overlap_scale`` switches such a source off anyway. Raises ``ValueError``
    for NaN values.
    """
    _check_values(xs, "equate_source")
    shared = overlap(xs, ruler_z)
    if not shared:
        return {}
    pool = sorted(xs[family] for family in shared)
    present = set(pool)
    scale = sorted(ruler_z[family] for family in shared)
    z_by_value: dict[float, float] = {}
    result: dict[str, float] = {}
    for family in sorted(xs):
        x = xs[family]
        if x not in z_by_value:
            z_by_value[x] = hazen_quantile(scale, midrank_p(x, pool, include_self=x in present))
        result[family] = z_by_value[x]
    return result


def overlap_scale(n: int, full: int = 50, off: int = 15) -> float:
    """Weight factor for a source overlapping the ruler on ``n`` families: 0 below ``off``, else min(1, n/full)."""
    if full <= 0:
        raise ValueError(f"overlap_scale: full must be positive, got {full}")
    if n < off:
        return 0.0
    return min(1.0, n / full)
