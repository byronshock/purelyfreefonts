"""A reference survey pipeline for the engine's property tests (design-m1 §6).

It composes the engine exactly as a survey does, on plain synthetic data:

1. filter every input to the eligible families (before equating);
2. build the ruler from the ruler counts (no floor);
3. the ruler's own source term: counts below ``ruler_censor_below`` are censored;
4. equate every source (censored = ``-inf``) and scale its weight by overlap;
5. per family seen by any source: guard factors over its weighted terms,
   then ``fuse`` with W_g = Σ w_eff (a family with no weighted term gets
   S = μ0 and is not ranked);
6. keep ranked fonts, sort by ``sort_key``, gate and ``place``.

``surveys.score_survey`` owns the production version; this one stays small so
the tests can state the properties against it.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from tff_catalog.engine.equate import build_ruler, equate_source, overlap, overlap_scale
from tff_catalog.engine.fuse import Fused, fuse, guard_factors
from tff_catalog.engine.order import Placement, Scored, gate, place, ranked, sort_key

RULER = "homebrew"  # the ruler's own engine source


@dataclass(frozen=True)
class Source:
    group: str
    weight: float
    values: Mapping[str, float]  # observed values
    censored: frozenset[str] = frozenset()  # in the frame, below the floor


@dataclass(frozen=True)
class Universe:
    names: Mapping[str, str]  # family id -> name
    ruler: Mapping[str, float]  # ruler counts (casks with no analytics row = 0)
    sources: Mapping[str, Source]  # the non-ruler sources
    ruler_weight: float = 1.0
    ruler_censor_below: float = 2.0


@dataclass(frozen=True)
class Params:
    kappa: float = 0.2
    mu0: float = 0.0
    guard: tuple[float, int, float] | None = (1.5, 3, 0.5)  # (gap, min_terms, factor)
    overlap_full: int = 50
    overlap_off: int = 15
    min_groups: int = 2
    exact_top: int = 10
    bands: tuple[tuple[int, int], ...] = ((11, 20), (21, 30))
    open_band_from: int = 31


@dataclass(frozen=True)
class Result:
    ruler_z: dict[str, float]
    z: dict[str, dict[str, float]]  # source -> family -> z
    overlaps: dict[str, int]
    weights: dict[str, float]  # w_eff
    fused: dict[str, Fused]
    placements: dict[str, Placement] = field(default_factory=dict)

    def ranks_json(self) -> dict[str, Any]:
        """Scores and placements only (what a rank publishes)."""
        return {
            "fused": {
                f: {
                    "score": v.score,
                    "weight": v.weight,
                    "terms": v.terms,
                    "observed": v.observed,
                    "groups": list(v.groups),
                    "guard": [list(g) for g in v.guard],
                }
                for f, v in self.fused.items()
            },
            "placements": {
                f: [p.order, p.rank, p.band, p.gate_held] for f, p in self.placements.items()
            },
        }

    def to_json(self) -> dict[str, Any]:
        return self.ranks_json() | {
            "ruler_z": self.ruler_z,
            "z": self.z,
            "overlaps": self.overlaps,
            "weights": self.weights,
        }

    def order(self, family: str) -> int | None:
        placement = self.placements.get(family)
        return None if placement is None else placement.order


def only(universe: Universe, eligible: frozenset[str]) -> Universe:
    """The universe restricted to ``eligible`` families (the gates run before equating)."""
    return replace(
        universe,
        names={f: n for f, n in universe.names.items() if f in eligible},
        ruler={f: c for f, c in universe.ruler.items() if f in eligible},
        sources={
            s: replace(
                src,
                values={f: v for f, v in src.values.items() if f in eligible},
                censored=frozenset(f for f in src.censored if f in eligible),
            )
            for s, src in universe.sources.items()
        },
    )


def engine_sources(universe: Universe) -> dict[str, Source]:
    """Every engine source, the ruler's own term included."""
    ruler_source = Source(
        group=RULER,
        weight=universe.ruler_weight,
        values={f: c for f, c in universe.ruler.items() if c >= universe.ruler_censor_below},
        censored=frozenset(f for f, c in universe.ruler.items() if c < universe.ruler_censor_below),
    )
    return {RULER: ruler_source} | dict(universe.sources)


def run(universe: Universe, params: Params, eligible: frozenset[str] | None = None) -> Result:
    """Deliberately never sorts before calling the engine: every mapping reaches it
    in the universe's own iteration order, so the determinism property (a
    shuffled universe gives the same bytes) tests the engine, not this model."""
    if eligible is not None:
        universe = only(universe, eligible)
    ruler_z = build_ruler(universe.ruler)
    sources = engine_sources(universe)
    z: dict[str, dict[str, float]] = {}
    overlaps: dict[str, int] = {}
    weights: dict[str, float] = {}
    for name, src in sources.items():
        xs = dict(src.values) | dict.fromkeys(src.censored, -math.inf)
        z[name] = equate_source(xs, ruler_z)
        overlaps[name] = len(overlap(xs, ruler_z))
        weights[name] = src.weight * overlap_scale(
            overlaps[name], params.overlap_full, params.overlap_off
        )
    w_total = math.fsum(weights.values())
    groups = {name: src.group for name, src in sources.items()}

    members = dict.fromkeys(f for src in sources.values() for f in (*src.values, *src.censored))
    fused: dict[str, Fused] = {}
    for family in members:
        terms = {s: z[s][family] for s in z if family in z[s] and weights[s] > 0}
        observed = frozenset(s for s in terms if family in sources[s].values)
        guard = guard_factors(terms, *params.guard) if params.guard else None
        fused[family] = fuse(
            terms, weights, w_total, params.kappa, params.mu0, guard, groups, observed
        )

    scored = [
        Scored(f, universe.names[f], v.score, v.weight, ruler_z.get(f), v.observed, len(v.groups))
        for f, v in fused.items()
    ]
    ordered = sorted((s for s in scored if ranked(s)), key=sort_key)
    placements = place(
        ordered,
        {s.id: gate(s, params.min_groups) for s in ordered},
        params.exact_top,
        params.bands,
        params.open_band_from,
    )
    return Result(ruler_z, z, overlaps, weights, fused, placements)
