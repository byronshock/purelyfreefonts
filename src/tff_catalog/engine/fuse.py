"""Fusion with shrinkage and the outlier guard (methodology §4, design-m1 §6). Owner: agent P7.

- ``guard_factors``: a term more than ``gap`` z from the median of all a font's
  terms (``basis = "median"``, the owner's ruling of 2026-09-26) or, as ruling M1
  first read, from the unweighted mean of its other terms (``"others_mean"``)
  gets ``factor`` weight (gap 1.5, at least 3 terms, x0.5). All terms are judged
  against the unguarded values at once.
- ``fuse``: the shrunk weighted mean S = (κ·W·μ0 + Σ w'z) / (κ·W + Σ w').

Sums use ``math.fsum`` over sources in sorted order, so the result does not
depend on the iteration order of the mappings passed in.
"""

import math
import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

type GuardBasis = Literal["median", "others_mean"]


@dataclass(frozen=True, slots=True)
class Fused:
    score: float  # S
    weight: float  # Σ w' over the terms used (after guard factors)
    terms: int  # observed + censored terms
    observed: int  # observed terms only
    groups: tuple[str, ...]  # distinct independence groups among observed terms, sorted
    guard: tuple[tuple[str, float], ...] = ()  # (source, factor) where the guard fired


def _check_finite(terms: Mapping[str, float], what: str) -> None:
    for source, z in terms.items():
        if not math.isfinite(z):
            raise ValueError(f"{what}: term {source!r} is {z!r}; terms are equated z values")


def guard_factors(
    terms: Mapping[str, float],
    gap: float = 1.5,
    min_terms: int = 3,
    factor: float = 0.5,
    basis: GuardBasis = "others_mean",
) -> dict[str, float]:
    """Per-source weight factors: ``factor`` for a term more than ``gap`` z from its
    basis, when there are at least ``min_terms`` terms. Applied simultaneously; 1.0
    everywhere else. The basis is the median of all the font's terms (``"median"``,
    the term itself included) or the unweighted mean of the others (``"others_mean"``).

    ``terms`` are one font's z values by source, observed and censored, and
    should hold only the terms that carry weight (a switched-off source is not
    one of the "others"). The comparison is strict: a gap of exactly ``gap``
    does not fire. Returns every source of ``terms``, keys sorted. Raises
    ``ValueError`` for a non-finite z.
    """
    _check_finite(terms, "guard_factors")
    sources = sorted(terms)
    factors = dict.fromkeys(sources, 1.0)
    if len(sources) < max(min_terms, 2):
        return factors
    if basis == "median":
        middle = statistics.median(terms[source] for source in sources)
        for source in sources:
            if abs(terms[source] - middle) > gap:
                factors[source] = factor
        return factors
    if basis != "others_mean":
        raise ValueError(f"guard_factors: unknown basis {basis!r}")
    for source in sources:
        others = math.fsum(terms[other] for other in sources if other != source)
        mean_of_others = others / (len(sources) - 1)
        if abs(terms[source] - mean_of_others) > gap:
            factors[source] = factor
    return factors


def fuse(
    terms: Mapping[str, float],
    w_eff: Mapping[str, float],
    w_total: float,
    kappa: float,
    mu0: float,
    guard: Mapping[str, float] | None = None,
    groups: Mapping[str, str] | None = None,
    observed: frozenset[str] = frozenset(),
) -> Fused:
    """S = (κ·W·μ0 + Σ w'z) / (κ·W + Σ w') over the observed and censored terms.

    ``w_total`` is W_g, the survey's effective weight total (constant per
    survey); ``w_eff`` already includes each term's ``corrections.Term.factor``
    (so w' = w_eff * factor * guard factor). ``groups`` maps source to the
    family's independence group (``Term.group``, per family under gate M5) and
    ``observed`` names the observed (not censored) terms.

    Further contract:

    - ``guard`` holds guard factors by source, from ``guard_factors`` for a
      survey, or reused from each survey for overall; None (or a missing
      source) means 1.0. ``Fused.guard`` lists the used terms whose factor is
      not 1.0, sorted by source.
    - A term whose w' is 0 (its source is missing from ``w_eff``, switched off
      by ``overlap_scale``, stale-dropped or disabled) contributes nothing: it
      is not counted in ``terms``, ``observed`` or ``groups``.
    - With ``groups`` None, each source is its own group.
    - The bounds min(μ0, min z) <= S <= max(μ0, max z) hold exactly (μ0 only
      when κ·W > 0). With no weighted terms and no prior, S = μ0.

    Raises ``ValueError`` for a negative or NaN weight, κ or W, a non-finite
    z or μ0, or an observed term that ``groups`` has no group for.
    """
    _check_finite(terms, "fuse")
    # `not x >= 0` also catches NaN, which would otherwise slip past both
    # comparisons below and silently drop a term or the prior.
    if not (kappa >= 0 and w_total >= 0 and math.isfinite(kappa * w_total)):
        raise ValueError(f"fuse: kappa and w_total must be finite and >= 0, got {kappa}, {w_total}")
    if not math.isfinite(mu0):
        raise ValueError(f"fuse: mu0 must be finite, got {mu0!r}")
    guard = guard or {}
    used: list[tuple[str, float, float]] = []  # (source, w', z)
    for source in sorted(terms):
        w = w_eff.get(source, 0.0) * guard.get(source, 1.0)
        if not w >= 0:
            raise ValueError(f"fuse: negative or NaN weight {w} for {source!r}")
        if w > 0:
            used.append((source, w, terms[source]))

    prior = kappa * w_total
    weight = math.fsum(w for _, w, _ in used)
    numerator = math.fsum([prior * mu0, *(w * z for _, w, z in used)])
    denominator = prior + weight
    score = numerator / denominator if denominator > 0 else mu0
    bounds = [z for _, _, z in used] + ([mu0] if prior > 0 or not used else [])
    # Each product is rounded before the exact sum, so S can stray an ulp past
    # the true weighted mean's bounds; clamping restores them exactly.
    score = min(max(score, min(bounds)), max(bounds))

    group_of = groups if groups is not None else {source: source for source, _, _ in used}
    seen = [source for source, _, _ in used if source in observed]
    missing = [source for source in seen if source not in group_of]
    if missing:
        raise ValueError(f"fuse: no independence group for observed terms {missing}")
    return Fused(
        score=score,
        weight=weight,
        terms=len(used),
        observed=len(seen),
        groups=tuple(sorted({group_of[source] for source in seen})),
        guard=tuple(
            (source, guard[source]) for source, _, _ in used if guard.get(source, 1.0) != 1.0
        ),
    )
