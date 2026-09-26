"""Ordering, the evidence gate and bands (methodology §4 and §6, design-m1 §6). Owner: agent P7.

The caller keeps the fonts that are ``ranked`` at all, sorts them by
``sort_key`` and hands the result to ``place`` with each font's ``gate``
verdict. ``place`` fills the exact top with fonts that pass the gate; a font
that fails it but scored inside the top sits just after ``exact_top``, in its
original relative order, flagged ``gate_held`` (D14). Everything else keeps its
position, so ``order`` is always an exact integer 1..n.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Scored:
    id: str
    name: str
    score: float  # S
    weight: float  # Σ w'
    ruler_z: float | None
    observed: int
    groups: int  # distinct independence groups among observed terms


@dataclass(frozen=True, slots=True)
class Placement:
    order: int  # exact position, always an integer
    rank: int | None  # 1-100 inside the exact top, else None
    band: str | None  # "101-250", "251-500", "501+"
    gate_held: bool  # the 2-group gate kept it out of the top 100


def ranked(f: Scored, min_observed: int = 1) -> bool:
    """Ranked at all: at least ``min_observed`` observed terms."""
    return f.observed >= min_observed


def gate(f: Scored, groups: int = 2) -> bool:
    """The top-100 evidence gate: observed terms from at least ``groups`` groups."""
    return f.groups >= groups


def sort_key(f: Scored) -> tuple[float, float, float, str, str]:
    """(-S, -Σw', -z_R or +inf, casefold(name), id): deterministic ties.

    A font with no ruler z sorts after every font with one, at equal S and Σw'.
    Raises ``ValueError`` for a NaN score or weight, which would break sorting.
    """
    if math.isnan(f.score) or math.isnan(f.weight):
        raise ValueError(f"sort_key: NaN score or weight for {f.id!r}")
    ruler = math.inf if f.ruler_z is None else -f.ruler_z
    return (-f.score, -f.weight, ruler, f.name.casefold(), f.id)


def band_label(
    order: int, bands: Sequence[tuple[int, int]] = ((101, 250), (251, 500)), open_from: int = 501
) -> str | None:
    """The band containing ``order``: "lo-hi" for a closed band, "N+" from ``open_from``, else None."""
    for low, high in bands:
        if low <= order <= high:
            return f"{low}-{high}"
    if order >= open_from:
        return f"{open_from}+"
    return None


def place(
    ordered: Sequence[Scored],
    passes_gate: Mapping[str, bool],
    exact_top: int = 100,
    bands: Sequence[tuple[int, int]] = ((101, 250), (251, 500)),
    open_band_from: int = 501,
) -> dict[str, Placement]:
    """Assign orders, exact ranks and bands. Gate failures inside the top move to
    just after ``exact_top``, keeping their relative order.

    ``ordered`` is already sorted (``sort_key``) and holds only ranked fonts,
    with unique ids. A font missing from ``passes_gate`` fails the gate.

    - The exact top is filled with the first ``exact_top`` fonts that pass;
      those are the only fonts with a ``rank`` (= ``order``) and no ``band``.
    - The failing fonts passed over while filling it come next, in their
      relative order, then everything else in its original order. Orders run
      1..len(ordered).
    - ``gate_held`` marks a failing font that sorted inside the exact top, so
      the gate alone cost it an exact rank.
    - Every font outside the exact top gets the band of max(order,
      ``exact_top`` + 1): if too few fonts pass to fill the top (a view whose
      sources share one group), a failing font keeps a low order but is still
      banded, never ranked.

    Returns a dict with keys in order.
    """
    ids = [f.id for f in ordered]
    if len(set(ids)) != len(ids):
        raise ValueError("place: duplicate ids in ordered")
    top: list[Scored] = []
    passed_over: list[Scored] = []
    rest: list[Scored] = []
    for f in ordered:
        if len(top) >= exact_top:
            rest.append(f)
        elif passes_gate.get(f.id, False):
            top.append(f)
        else:
            passed_over.append(f)

    top_ids = {f.id for f in top}
    held_ids = {f.id for f in ordered[:exact_top] if not passes_gate.get(f.id, False)}
    placements: dict[str, Placement] = {}
    for order, f in enumerate(top + passed_over + rest, start=1):
        in_top = f.id in top_ids
        placements[f.id] = Placement(
            order=order,
            rank=order if in_top else None,
            band=None if in_top else band_label(max(order, exact_top + 1), bands, open_band_from),
            gate_held=f.id in held_ids,
        )
    return placements


def band_shift(placed: Mapping[str, Placement], exact_top: int) -> int:
    """How far a rank key's unranked placements move so the first sits just past the exact
    top: methodology §6 puts a font that fails the gate at ``exact_top`` + 1 or below, even
    when too few fonts pass to fill the top. 0 when the top is full, as in every survey view."""
    banded = [p.order for p in placed.values() if p.rank is None]
    return max(0, exact_top + 1 - min(banded)) if banded else 0


def published_orders(
    placements: Mapping[str, Mapping[str, Placement]], exact_top: int
) -> dict[str, dict[str, int]]:
    """{rank key: {font: order}} as the catalog publishes them (``band_shift`` applied): the
    orders stage "export" writes, stage "review" compares and stage "membership" enters and
    leaves the top-100 lists by, so no font holds a top-100 place without an exact rank."""
    out = {}
    for key, ps in sorted(placements.items()):
        shift = band_shift(ps, exact_top)
        out[key] = {f: p.order + (shift if p.rank is None else 0) for f, p in ps.items()}
    return out
