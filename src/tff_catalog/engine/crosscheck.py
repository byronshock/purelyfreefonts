"""Monthly cross-checks (decisions D1 and D5, design-m1 §6). Owner: agent P7b.

Two other ways to rank the same terms, each compared with the published
order. A font whose order moves by more than ``ranking.toml [crosscheck]
move_flag`` (30% of its published order) is flagged in review.md (methodology
§9). Neither check ever changes a rank.

- **Coverage-aware RRF** (D1, k = ``crosscheck.rrf_k`` = 60). Each source ranks
  the fonts it covers (``source_ranks``: observed and censored terms, the
  censored ones sharing the bottom tie block, as in equating). A font scores
  the weighted mean of 1/(k + rank) over only the sources that cover it,
  shrunk toward a prior (``rrf``). A source that does not cover a font costs it
  some pull toward the middle, never a zero as in classic RRF.
- **The Fontsource ruler** (D5, ``engine.alt_ruler`` = ``jsdelivr``). The
  published pipeline (``surveys.views`` and ``surveys.overall``) rerun with
  z_R built from that source's values instead of Homebrew's, and each
  source's weight rescaled for its overlap with the new ruler
  (``rerun_with_ruler``).

``flags`` runs both for every rank key but Rising, which is not built on the
ruler. Everything here is deterministic whatever the iteration order of the
mappings passed in.
"""

import dataclasses
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from itertools import groupby
from typing import TYPE_CHECKING, cast

from tff_catalog.config_model import RANK_KEYS, SurveyRank
from tff_catalog.engine import equate, order

if TYPE_CHECKING:
    from tff_catalog.config_model import RankingConfig
    from tff_catalog.corrections import Term
    from tff_catalog.review import Flag
    from tff_catalog.surveys import RankInputs, SurveyScores, Terms

_COVERED = frozenset({"observed", "censored"})
# Rising is built from shares, not on the ruler: neither check applies, and
# its terms (log-ratios) are never ruler values.
_UNCHECKED = frozenset({"rising"})


class NoRulerValues(ValueError):
    """The alternative ruler's source has no observed or censored value this month."""


@dataclass(frozen=True, slots=True)
class Move:
    id: str
    rank: int
    other: int
    change: float  # |rank - other| / rank


# --- coverage-aware RRF -------------------------------------------------------


def source_ranks(terms: Terms) -> dict[str, dict[str, int]]:
    """{source: {id: rank in source}} for one view's terms; rank 1 is the highest value.

    Only observed and censored terms rank; not-covered and too-new terms do
    not. Censored terms share one tie block at the bottom whatever their value,
    as in equating (``stats.midrank_p``). A tie block over positions lo..hi
    gets the mid-rank (lo + hi) // 2 (rounded down to stay an integer). A
    source that ranks nothing is left out. An observed term needs a finite
    value (``ValueError`` otherwise).
    """
    out: dict[str, dict[str, int]] = {}
    for source in sorted(terms):
        values: dict[str, float] = {}
        for fid, term in terms[source].items():
            if term.state == "observed":
                if term.value is None or not math.isfinite(term.value):
                    raise ValueError(f"{source}/{fid}: observed term without a finite value")
                values[fid] = term.value
            elif term.state == "censored":
                values[fid] = -math.inf
        if values:
            out[source] = _midranks(values)
    return out


def _midranks(values: Mapping[str, float]) -> dict[str, int]:
    ordered = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    out: dict[str, int] = {}
    before = 0
    for _, block in groupby(ordered, key=lambda kv: kv[1]):
        ids = [fid for fid, _ in block]
        rank = (2 * before + len(ids) + 1) // 2
        out.update(dict.fromkeys(ids, rank))
        before += len(ids)
    return out


def rrf(
    ranks: Mapping[str, Mapping[str, int]],
    weights: Mapping[str, float],
    k: int = 60,
    *,
    kappa: float = 0.2,
    factors: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, float]:
    """Coverage-aware RRF: ``ranks`` is {source: {id: rank in source}}.

    R(f) = (κ·W·π + Σ_{s covers f} w'_s/(k + r_s(f))) / (κ·W + Σ_{s covers f} w'_s)

    - A source counts when it ranks at least one font and has a positive
      weight; W is the sum of those weights (W_g in fusion).
    - w'_s = w_s · ``factors[s][f]`` (default 1.0), the term's own weight
      multiplier (``corrections.Term.factor``, the Almanac parent merge), as
      in fusion. A term whose w' is 0 is left out; it still holds its place
      in the source's ranks, as in equating.
    - π, the prior, is the score of a font in the middle of every counted
      source: Σ w_s/(k + (n_s + 1)/2) / W, where n_s is the number of fonts s
      ranks. It plays the part of fusion's μ0 = 0, the median eligible font.
    - ``kappa`` is fusion's κ (``engine.kappa``), so a font covered by every
      source keeps the same share of its own signal as in fusion.

    Returns a score for every font with a term of positive w'. Ranks must be
    at least 1, and ``k``, ``kappa`` and every factor non-negative
    (``ValueError`` otherwise).
    """
    if k < 0 or kappa < 0:
        raise ValueError(f"rrf: need k >= 0 and kappa >= 0, got k={k}, kappa={kappa}")
    counted = sorted(s for s in ranks if ranks[s] and weights.get(s, 0.0) > 0)
    if not counted:
        return {}
    factors = factors or {}
    total = math.fsum(weights[s] for s in counted)
    prior = math.fsum(weights[s] / (k + (len(ranks[s]) + 1) / 2) for s in counted) / total
    parts: dict[str, list[tuple[float, float]]] = {}
    for s in counted:
        by_id = factors.get(s, {})
        for fid, r in ranks[s].items():
            if r < 1:
                raise ValueError(f"rrf: rank {r} for {s}/{fid}; ranks start at 1")
            factor = by_id.get(fid, 1.0)
            if factor < 0:
                raise ValueError(f"rrf: negative factor {factor} for {s}/{fid}")
            w = weights[s] * factor
            if w > 0:
                parts.setdefault(fid, []).append((w / (k + r), w))
    base = kappa * total
    return {
        fid: (base * prior + math.fsum(p[0] for p in parts[fid]))
        / (base + math.fsum(p[1] for p in parts[fid]))
        for fid in sorted(parts)
    }


def rrf_inputs(
    inputs: RankInputs, key: str, cfg: RankingConfig
) -> tuple[dict[str, Mapping[str, Term]], dict[str, float]]:
    """The terms and source weights that RRF ranks rank key ``key`` from.

    A survey or view uses its own terms and weights. ``overall`` uses the terms
    of each rank g in ``ranks.overall.mix``, source s weighted
    v_s = M_g·w_s/W_g (methodology §4), so W = Σ M_g as in fusion; its sources
    are named "g:s", so a source in two mixed ranks stays two sources.
    """
    if key != "overall":
        return dict(inputs.terms.get(key, {})), dict(inputs.weights.get(key, {}))
    terms: dict[str, Mapping[str, Term]] = {}
    weights: dict[str, float] = {}
    for g, share in sorted(cfg.ranks.overall.mix.items()):
        ws = {s: w for s, w in inputs.weights.get(g, {}).items() if w > 0}
        total = math.fsum(ws[s] for s in sorted(ws))
        for s in sorted(ws):
            terms[f"{g}:{s}"] = inputs.terms.get(g, {}).get(s, {})
            weights[f"{g}:{s}"] = share * ws[s] / total
    return terms, weights


def rrf_orders(
    inputs: RankInputs, key: str, cfg: RankingConfig, among: Iterable[str] | None = None
) -> dict[str, int]:
    """{id: order} for rank key ``key`` under coverage-aware RRF, placed like the published order.

    Scores come from ``rrf`` over ``rrf_inputs`` with ``crosscheck.rrf_k``,
    ``engine.kappa`` and each term's ``factor``. The fonts are then kept,
    ordered and placed by the engine's own rules (``order.ranked``,
    ``sort_key``, ``gate`` and ``place``), so the 2-group gate holds the same
    fonts out of the exact top and only the scoring differs. A term with no
    weight (its source's weight or its factor is 0) gives no observed term or
    group, as in fusion. ``among`` (usually the published order's ids) limits
    the fonts placed, so both orders count the same fonts.
    """
    terms, weights = rrf_inputs(inputs, key, cfg)
    factors = {
        s: {fid: t.factor for fid, t in by_id.items() if t.factor != 1.0}
        for s, by_id in terms.items()
    }
    scores = rrf(
        source_ranks(terms),
        weights,
        cfg.crosscheck.rrf_k,
        kappa=cfg.engine.kappa,
        factors=factors,
    )
    keep = set(scores) if among is None else set(scores).intersection(among)
    live = sorted(s for s in terms if weights.get(s, 0.0) > 0)
    scored = [_scored(fid, scores[fid], terms, weights, live, inputs) for fid in keep]
    scored = sorted(
        (f for f in scored if order.ranked(f, cfg.engine.min_observed_terms)), key=order.sort_key
    )
    passes = {f.id: order.gate(f, cfg.engine.top100_min_groups) for f in scored}
    d = cfg.display
    placed = order.place(scored, passes, d.exact_top, d.bands, d.open_band_from)
    return {fid: p.order for fid, p in placed.items()}


def _scored(
    fid: str,
    score: float,
    terms: Mapping[str, Mapping[str, Term]],
    weights: Mapping[str, float],
    live: list[str],
    inputs: RankInputs,
) -> order.Scored:
    covering = [
        s
        for s in live
        if fid in terms[s]
        and terms[s][fid].state in _COVERED
        and weights[s] * terms[s][fid].factor > 0
    ]
    observed = [s for s in covering if terms[s][fid].state == "observed"]
    return order.Scored(
        id=fid,
        name=inputs.names.get(fid, fid),
        score=score,
        weight=math.fsum(weights[s] * terms[s][fid].factor for s in covering),
        ruler_z=inputs.ruler.get(fid),
        observed=len(observed),
        groups=len({terms[s][fid].group for s in observed}),
    )


# --- the alternative ruler ----------------------------------------------------


def ruler_values(terms: Mapping[str, Terms], source: str) -> dict[str, float]:
    """The alternative ruler's input: ``source``'s value for each family it covers.

    ``terms`` is {view: {source: {id: Term}}}. Observed and censored terms
    count with their value, a censored term without one counting 0 (as a cask
    with no analytics row does for Homebrew), so the tail keeps its order
    instead of tying at the floor. The views are merged, so an abstention in
    one view does not hide a family; if views disagree, the largest value wins.
    Rising's terms are log-ratios, not counts, and are never read. For a
    source with no floor, like jsDelivr, these are the raw values.
    """
    out: dict[str, float] = {}
    for view in sorted(set(terms) - _UNCHECKED):
        for fid, term in terms[view].get(source, {}).items():
            if term.state in _COVERED:
                value = 0.0 if term.value is None else term.value
                out[fid] = max(value, out.get(fid, value))
    return dict(sorted(out.items()))


def reweigh(
    inputs: RankInputs, ruler_z: Mapping[str, float], cfg: RankingConfig
) -> dict[str, dict[str, float]]:
    """Every rank key's weights, rescaled for each source's overlap with ``ruler_z``.

    The effective weights carry ``equate.overlap_scale(|O_s|)`` for the main
    ruler (``surveys.effective_weights``); this swaps it for the new ruler's:
    w' = w · scale(new) / scale(old), where O_s is the source's frame
    (observed and censored terms) in that key's own terms, intersected with
    the ruler's families. A source with weight 0 stays off, since a 0 does not
    say whether a small overlap, a stale drop or the config switched it off.

    - In a project survey key (``project``), each ``project_group_shares``
      group's total is then split again pro rata, so the groups keep their
      fixed shares (gate M9 (b)).
    - ``overall``, when ``inputs.weights`` has it, is rebuilt from its mix
      keys' new weights (``surveys.mix_weights``), as ``surveys.rank_inputs``
      builds it.

    Where no weighted source's overlap scale changes, the weights come back
    bit for bit, so a rerun with the main ruler reproduces the published ranks.
    """
    from tff_catalog import surveys  # imported here: surveys may import this module

    full, off = cfg.engine.overlap_full, cfg.engine.overlap_off
    old_ruler, new_ruler = set(inputs.ruler), set(ruler_z)
    out: dict[str, dict[str, float]] = {}
    for key in sorted(set(inputs.weights) - {"overall"}):
        before = inputs.weights[key]
        frames = _frames(inputs.terms.get(key, {}))
        ratios: dict[str, float] = {}
        for s in sorted(before):
            frame = frames.get(s, set())
            old = equate.overlap_scale(len(frame & old_ruler), full, off)
            new = equate.overlap_scale(len(frame & new_ruler), full, off)
            ratios[s] = new / old if old > 0 else 0.0
        after = {s: before[s] * ratios[s] for s in sorted(before)}
        if _group_shared(key, cfg):
            for group in cfg.project_group_shares.values():
                _resplit(after, before, ratios, group.sources)
        out[key] = after
    if "overall" in inputs.weights:
        out["overall"] = surveys.mix_weights(cfg.ranks.overall.mix, out)
    return dict(sorted(out.items()))


def _group_shared(key: str, cfg: RankingConfig) -> bool:
    """Whether ``surveys.effective_weights`` gives ``key`` the fixed project group shares."""
    spec = getattr(cfg.ranks, key, None)
    return isinstance(spec, SurveyRank) and spec.survey == "project"


def _frames(terms: Terms) -> dict[str, set[str]]:
    return {s: {f for f, t in by_id.items() if t.state in _COVERED} for s, by_id in terms.items()}


def _resplit(
    after: dict[str, float],
    before: Mapping[str, float],
    ratios: Mapping[str, float],
    sources: Iterable[str],
) -> None:
    members = sorted(s for s in sources if s in before)
    if all(ratios[s] == 1.0 for s in members if before[s] > 0):
        # Unchanged. Resplitting anyway would round-trip each weight through
        # share·w/Σw, which can move it by an ulp.
        return
    share = math.fsum(before[s] for s in members)
    raw = math.fsum(after[s] for s in members)
    for s in members:
        after[s] = share * after[s] / raw if raw > 0 else 0.0


def rerun_with_ruler(
    inputs: object, ruler: str, *, cfg: RankingConfig, key: str = "overall"
) -> dict[str, int]:
    """Re-rank ``inputs`` (``surveys.RankInputs``) with engine source ``ruler`` as the ruler.

    Returns {id: order} for rank key ``key``. Only the ruler changes: z_R is
    ``equate.build_ruler(ruler_values(inputs.terms, ruler))``, the weights are
    rescaled for the new overlaps (``reweigh``) and the ranks come from the
    published pipeline, so a rerun whose ruler values are those behind
    ``inputs.ruler`` gives back the published orders.
    """
    return rerun_orders(inputs, ruler, cfg, (key,))[key]


def rerun_orders(
    inputs: object, ruler: str, cfg: RankingConfig, keys: Iterable[str]
) -> dict[str, dict[str, int]]:
    """{rank key: {id: order}} for each of ``keys``, from one rerun (``rerun_with_ruler``).

    ``overall`` comes from ``surveys.overall``, every other key from
    ``surveys.views``. Raises ``NoRulerValues`` (a ``ValueError``) when
    ``ruler`` has no values, and ``ValueError`` when the pipeline gives no rank
    for a key.
    """
    from tff_catalog import surveys  # imported here: surveys may import this module

    rank_inputs = cast("RankInputs", inputs)
    wanted = sorted(set(keys))
    values = ruler_values(rank_inputs.terms, ruler)
    if not values:
        raise NoRulerValues(f"rerun_with_ruler: engine source {ruler!r} has no values")
    z = equate.build_ruler(values)
    rerun = dataclasses.replace(rank_inputs, ruler=z, weights=reweigh(rank_inputs, z, cfg))
    scores: dict[str, SurveyScores] = {}
    if "overall" in wanted:
        scores["overall"] = surveys.overall(rerun, cfg)
    if any(k != "overall" for k in wanted):
        scores.update(
            (k, v) for k, v in surveys.views(rerun, cfg).items() if k in wanted and k != "overall"
        )
    missing = [k for k in wanted if k not in scores]
    if missing:
        raise ValueError(f"rerun_with_ruler: no rank for {', '.join(missing)}")
    return {k: {fid: p.order for fid, p in scores[k].placements.items()} for k in wanted}


# --- moves and flags ----------------------------------------------------------


def moves(
    ranks: Mapping[str, int], other: Mapping[str, int], threshold: float = 0.30
) -> list[Move]:
    """Fonts whose rank moves by more than ``threshold`` (relative), sorted by rank.

    change = |rank - other| / rank, strictly more than ``threshold`` to count.
    Only fonts in both mappings are compared. Ranks start at 1 (``ValueError``
    otherwise). Ties in rank sort by id.
    """
    out = []
    for fid in sorted(ranks.keys() & other.keys()):
        rank, other_rank = ranks[fid], other[fid]
        if rank < 1:
            raise ValueError(f"moves: rank {rank} for {fid!r}; ranks start at 1")
        change = abs(rank - other_rank) / rank
        if change > threshold:
            out.append(Move(fid, rank, other_rank, change))
    return sorted(out, key=lambda m: (m.rank, m.id))


def flags(
    inputs: object,
    orders: Mapping[str, Mapping[str, int]],
    cfg: RankingConfig,
    *,
    top: int | None = None,
) -> list[Flag]:
    """The §9 cross-check flags: fonts moving more than ``crosscheck.move_flag``.

    ``orders`` is the published {rank key: {id: order}}. For each key but
    Rising, the RRF order (``rrf_orders``, over the published fonts) and the
    ``engine.alt_ruler`` rerun (``rerun_orders``, run once for every key) are
    compared with it (``moves``). A published font with no place at all under
    a check (the rerun switched off every source it had observed terms from)
    is flagged too; when a check places no font of a key at all, one flag with
    no family id says so. With ``top``, only fonts at that published order or
    better are flagged.

    Each flag is kind "crosscheck" with its rank key and family id, and names
    orders only, never values (rulings T2, T4). Sorted by rank key
    (``RANK_KEYS`` order), then RRF before the ruler, then published order.
    The flags never block a run: when the alternative ruler has no values this
    month, one flag with no rank key, first, says the rerun did not run, and
    the RRF flags still come. An unknown rank key is a ``ValueError``.
    """
    from tff_catalog.review import Flag  # imported here: review may import this module

    unknown = sorted(set(orders) - set(RANK_KEYS))
    if unknown:
        raise ValueError(f"crosscheck.flags: unknown rank key(s) {', '.join(unknown)}")
    rank_inputs = cast("RankInputs", inputs)
    keys = [k for k in RANK_KEYS if k in orders and k not in _UNCHECKED]
    if not keys:
        return []
    ruler = cfg.engine.alt_ruler
    ruler_label = f"the {ruler} ruler"
    out: list[Flag] = []
    alt: dict[str, dict[str, int]] | None = None
    try:
        alt = rerun_orders(rank_inputs, ruler, cfg, keys)
    except NoRulerValues:
        out.append(Flag("crosscheck", f"The rerun with {ruler_label} did not run: no values"))
    rrf_label = f"coverage-aware RRF (k={cfg.crosscheck.rrf_k})"
    for key in keys:
        published = orders[key]
        checks = [(rrf_label, rrf_orders(rank_inputs, key, cfg, among=published))]
        if alt is not None:
            checks.append((ruler_label, alt[key]))
        if top is not None:
            published = {fid: o for fid, o in published.items() if o <= top}
        for label, other in checks:
            if published and not other:  # one flag, not one per font
                out.append(Flag("crosscheck", f"No font has a place under {label}", rank_key=key))
                continue
            for fid, message in _moved(published, other, cfg.crosscheck.move_flag, label):
                name = rank_inputs.names.get(fid, fid)
                out.append(Flag("crosscheck", f"{name} {message}", rank_key=key, family_id=fid))
    return out


def _moved(
    published: Mapping[str, int], other: Mapping[str, int], threshold: float, label: str
) -> list[tuple[str, str]]:
    """(id, message) for each move over ``threshold`` and each font ``other`` does not place."""
    found = [
        (m.rank, m.id, f"moves from {m.rank} to {m.other} ({_percent(m, threshold)}) under {label}")
        for m in moves(published, other, threshold)
    ]
    found += [
        (published[fid], fid, f"at {published[fid]} has no place under {label}")
        for fid in published.keys() - other.keys()
    ]
    return [(fid, message) for _, fid, message in sorted(found)]


def _percent(m: Move, threshold: float) -> str:
    """The move as a whole percent, or in tenths (rounded up) where a whole percent
    would print a flagged move at or under ``threshold`` (61/200 is "30.5%", not "30%")."""
    exact = Fraction(100 * abs(m.rank - m.other), m.rank)
    whole = round(exact)
    if whole > 100 * threshold:
        return f"{whole}%"
    return f"{math.ceil(10 * exact) / 10:.1f}%"
