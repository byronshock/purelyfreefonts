"""Stage "confidence": rank ranges and tiers (methodology §6, milestone-1 step 13). Owner: agent P10.

``rng = numpy.random.default_rng(ranking.toml [uncertainty].seed)``; per
survey, with sources sorted, ``d = rng.dirichlet(concentration · w_eff / W)``
and ``w* = d · W``: ``runs`` draws plus one leave-one-source-out run per
source; the 5-95% range uses ``numpy.quantile(..., method="inverted_cdf")``.
M_g is not perturbed.

How the weights move (``Layout``, built from ranking.toml by ``layout``):

- One Dirichlet per *draw* per run. The rank keys of one survey
  (``desktop_chosen`` and ``desktop_installed``; ``project``) share their
  survey's draw, so they move together. Each extra view (``coding``,
  ``dev_apps``) is its own draw. ``overall`` reuses the survey draws, and each
  survey's part keeps its total, which is M_g.
- A draw's Dirichlet is taken over the sources of its first rank key that
  belongs to it alone (``desktop_chosen``, ``project``, ``coding``...). Every
  rank key in the draw multiplies its weights by ``w*_s / w_s`` and is then
  scaled back to its own total, so the base key gets exactly ``d · W``.
- A leave-one-source-out run gives the source weight 0 and no terms in every
  rank key. Under gate M9 (b) the other sources of its project group take up
  its weight pro rata, as ``surveys.effective_weights`` does for a source that
  is switched off; elsewhere the total W shrinks.

Each run re-ranks every rank key with ``surveys.views``. Rising has no
weights, so only the leave-out runs move it; overall follows the survey
weights (``surveys.overall`` rebuilds v_s from them). A font that a run leaves
unranked counts as one place below the last font of the longer list, that
run's or the unperturbed one, so losing evidence never moves a font up (a run
that ranks nothing in a key would otherwise put every font first).

The stage rebuilds the inputs stage "rank" ranked (``load_inputs``) and checks
that they give the published orders. Each published range is widened, if
needed, to contain the font's published order, so a range always holds its
point rank. Tiers use each font's independence groups in that rank key
(``scores.json``).

Writes ``build/stage/confidence.json`` ({rank key: {id: Confidence}}).
"""

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from tff_catalog.config_model import RANK_KEYS, OverallRank, SurveyRank

if TYPE_CHECKING:
    from tff_catalog.config_model import RankingConfig, Tiers, Uncertainty
    from tff_catalog.corrections import Term
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext
    from tff_catalog.surveys import RankInputs, SurveyScores

Range = tuple[int, int]
Tier = Literal["A", "B", "C"]

# {rank key: {family_id: order}}: what a ranker returns for one set of inputs.
type Orders = Mapping[str, Mapping[str, int]]
# Recomputes the orders of every rank key from (perturbed) inputs.
type Ranker = Callable[[RankInputs], Orders]


@dataclass(frozen=True, slots=True)
class Confidence:
    """One ranked font's range and tier in one rank key."""

    range: Range  # (p5, p95) ranks
    tier: Tier


@dataclass(frozen=True, slots=True)
class Layout:
    """How weights move together in the perturbed runs (module docstring).

    - ``draws``: {rank key: {source: draw}}. Sources with the same draw share
      one Dirichlet per run, and within one rank key they keep their total.
    - ``shares``: {rank key: {source: project group}} (gate M9 (b)): a source
      left out hands its weight to the rest of its group in that rank key.
    """

    draws: Mapping[str, Mapping[str, str]]
    shares: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def independent(cls, weights: Mapping[str, Mapping[str, float]]) -> Layout:
        """Each rank key its own draw, no fixed groups (for callers without ranking.toml)."""
        return cls({key: dict.fromkeys(ws, f"key:{key}") for key, ws in weights.items()})


def layout(cfg: RankingConfig, weights: Mapping[str, Mapping[str, float]]) -> Layout:
    """The ``Layout`` of ``weights`` ({rank key: {source: weight}}) under ranking.toml."""
    survey_of = {name: src.survey for name, src in cfg.sources.all().items()}
    group_of = {
        source: group
        for group, spec in sorted(cfg.project_group_shares.items())
        for source in spec.sources
    }
    draws: dict[str, dict[str, str]] = {}
    shares: dict[str, dict[str, str]] = {}
    for key in sorted(weights, key=_key_order):
        spec = getattr(cfg.ranks, key, None)
        if isinstance(spec, SurveyRank):
            draws[key] = dict.fromkeys(weights[key], f"survey:{spec.survey}")
            fixed = spec.survey == "project"
        elif isinstance(spec, OverallRank):
            draws[key] = {s: _survey_draw(survey_of, s, key) for s in weights[key]}
            fixed = True
        else:
            draws[key] = dict.fromkeys(weights[key], f"key:{key}")
            fixed = False
        if fixed:
            shares[key] = {
                s: group_of[s]
                for s in weights[key]
                if s in group_of and survey_of.get(s) == "project"
            }
    return Layout(draws, shares)


def _survey_draw(survey_of: Mapping[str, str], source: str, key: str) -> str:
    survey = survey_of.get(source)
    return f"survey:{survey}" if survey else f"key:{key}"


def _key_order(key: str) -> tuple[int, str]:
    return (RANK_KEYS.index(key) if key in RANK_KEYS else len(RANK_KEYS), key)


# --- perturbation -------------------------------------------------------------------------


def perturb(
    inputs: RankInputs,
    cfg: Uncertainty,
    *,
    ranker: Ranker,
    plan: Layout | None = None,
) -> dict[str, dict[str, Range]]:
    """{rank key: {family_id: (p5, p95)}} from the weight perturbations.

    ``ranker`` recomputes every rank key's orders from inputs (the stage uses
    ``surveys.views``); ``plan`` says how weights move together (default:
    ``Layout.independent``). Each range covers the font's order in the
    unperturbed ``ranker(inputs)``; a font a run leaves unranked counts below
    every font of that run and of the unperturbed one. Deterministic for a
    given seed, whatever the iteration order of the mappings in ``inputs``.
    """
    plan = plan or Layout.independent(inputs.weights)
    point = ranker(inputs)
    ids = {key: sorted(point[key]) for key in sorted(point)}
    samples: dict[str, list[np.ndarray]] = {key: [] for key in ids}
    rng = np.random.default_rng(cfg.seed)
    for _ in range(cfg.runs):
        weights = perturbed_weights(inputs.weights, plan, cfg.concentration, rng)
        _collect(samples, ids, ranker(dataclasses.replace(inputs, weights=weights)))
    if cfg.leave_one_out:
        for source in weighted_sources(inputs.weights):
            _collect(samples, ids, ranker(leave_out(inputs, source, plan)))
    lo_q, hi_q = cfg.quantiles
    ranges: dict[str, dict[str, Range]] = {}
    for key, fids in ids.items():
        orders = [point[key][fid] for fid in fids]
        if not samples[key]:
            ranges[key] = {fid: (o, o) for fid, o in zip(fids, orders, strict=True)}
            continue
        runs = np.stack(samples[key])  # runs x fonts
        lo, hi = np.quantile(runs, [lo_q, hi_q], axis=0, method="inverted_cdf")
        ranges[key] = {
            fid: contain((int(a), int(b)), o)
            for fid, a, b, o in zip(fids, lo, hi, orders, strict=True)
        }
    return ranges


def _collect(
    samples: dict[str, list[np.ndarray]], ids: Mapping[str, list[str]], run: Orders
) -> None:
    """Append one run's orders; a rank key the run lacks tells nothing and is skipped.

    ``ids[key]`` holds the fonts of the unperturbed run, so a font this run
    leaves unranked sits below both lists (module docstring).
    """
    for key, fids in ids.items():
        orders = run.get(key)
        if orders is None:
            continue
        unranked = max(len(orders), len(fids)) + 1
        samples[key].append(np.array([orders.get(fid, unranked) for fid in fids], dtype=np.int64))


def contain(rng: Range, order: int) -> Range:
    """``rng`` widened, if needed, to hold ``order``."""
    return (min(rng[0], order), max(rng[1], order))


def weighted_sources(weights: Mapping[str, Mapping[str, float]]) -> list[str]:
    """Sources with a positive weight in some rank key, sorted: the leave-one-out runs."""
    return sorted({s for ws in weights.values() for s, w in ws.items() if w > 0})


def perturbed_weights(
    weights: Mapping[str, Mapping[str, float]],
    plan: Layout,
    concentration: float,
    rng: np.random.Generator,
) -> dict[str, dict[str, float]]:
    """One run's weights: a Dirichlet per draw, applied to every rank key (module docstring)."""
    factors = _draw_factors(weights, plan, concentration, rng)
    out: dict[str, dict[str, float]] = {}
    for key in sorted(weights, key=_key_order):
        ws, draws = weights[key], plan.draws.get(key, {})
        new: dict[str, float] = {}
        for draw, block in sorted(_blocks(ws, draws, key).items()):
            total = sum(ws[s] for s in block)
            raw = {s: ws[s] * factors.get(draw, {}).get(s, 1.0) for s in block}
            raw_total = sum(raw.values())
            scale = total / raw_total if raw_total > 0 else 0.0
            new.update({s: raw[s] * scale for s in block})
        out[key] = new
    return out


def _blocks(ws: Mapping[str, float], draws: Mapping[str, str], key: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    for source in sorted(ws):
        blocks.setdefault(draws.get(source, f"key:{key}"), []).append(source)
    return blocks


def _draw_factors(
    weights: Mapping[str, Mapping[str, float]],
    plan: Layout,
    concentration: float,
    rng: np.random.Generator,
) -> dict[str, dict[str, float]]:
    """{draw: {source: w*_s / w_s}}, drawing the Dirichlets in sorted draw order."""
    factors: dict[str, dict[str, float]] = {}
    for draw, base in sorted(_draw_bases(weights, plan).items()):
        sources = sorted(s for s, w in base.items() if w > 0)
        if not sources:
            continue
        total = sum(base[s] for s in sources)
        alpha = np.array([concentration * base[s] / total for s in sources])
        d = rng.dirichlet(alpha)
        factors[draw] = {s: float(d[i]) * total / base[s] for i, s in enumerate(sources)}
    return factors


def _draw_bases(
    weights: Mapping[str, Mapping[str, float]], plan: Layout
) -> dict[str, dict[str, float]]:
    """Each draw's base weights: its first rank key that is wholly in it, else its first key."""
    bases: dict[str, dict[str, float]] = {}
    pure: set[str] = set()
    for key in sorted(weights, key=_key_order):
        blocks = _blocks(weights[key], plan.draws.get(key, {}), key)
        for draw, block in blocks.items():
            is_pure = len(blocks) == 1
            if draw in pure or (draw in bases and not is_pure):
                continue
            bases[draw] = {s: weights[key][s] for s in block}
            if is_pure:
                pure.add(draw)
    return bases


def leave_out(inputs: RankInputs, source: str, plan: Layout) -> RankInputs:
    """``inputs`` without ``source``: weight 0 and no terms in every rank key.

    The source stays as a key (an empty term map, weight 0.0), so code that
    looks every configured source up still finds it. In a rank key with fixed
    project groups (``plan.shares``), the rest of its group is scaled up to
    keep the group's total; a group left with no weight contributes nothing.
    """
    terms = {
        key: {s: ({} if s == source else by_id) for s, by_id in ts.items()}
        for key, ts in inputs.terms.items()
    }
    weights = {
        key: _without(ws, source, plan.shares.get(key, {})) for key, ws in inputs.weights.items()
    }
    return dataclasses.replace(inputs, terms=terms, weights=weights)


def _without(ws: Mapping[str, float], source: str, shares: Mapping[str, str]) -> dict[str, float]:
    out = {s: (0.0 if s == source else w) for s, w in sorted(ws.items())}
    group = shares.get(source)
    if group is None or source not in ws:
        return out
    members = [s for s in sorted(out) if shares.get(s) == group and out[s] > 0]
    rest = sum(out[s] for s in members)
    if rest > 0:
        factor = (rest + ws[source]) / rest
        out.update({s: out[s] * factor for s in members})
    return out


# --- tiers --------------------------------------------------------------------------------


def tier(rank: int, width: int, groups: int, cfg: Tiers) -> Tier:
    """A: groups >= a_min_groups and width <= max(a_min_width, a_rank_share·rank); B: width <= b_rank_share·rank; else C."""
    if groups >= cfg.a_min_groups and width <= max(cfg.a_min_width, cfg.a_rank_share * rank):
        return "A"
    if width <= cfg.b_rank_share * rank:
        return "B"
    return "C"


def tiers(
    ranges: Mapping[str, Mapping[str, Range]],
    orders: Mapping[str, Mapping[str, int]],
    groups: Mapping[str, int],
    cfg: Tiers,
) -> dict[str, dict[str, Tier]]:
    """Tiers for every ranked font of every rank key.

    ``orders`` names the ranked fonts ({rank key: {id: order}}); each needs a
    range in ``ranges`` (``KeyError`` otherwise). The width is p95 - p5.
    ``groups`` is each font's count of distinct independence groups among its
    observed terms (a missing font has 0); the stage calls this once per rank
    key with that key's counts.
    """
    out: dict[str, dict[str, Tier]] = {}
    for key in sorted(orders, key=_key_order):
        key_ranges = ranges.get(key, {})
        out[key] = {}
        for fid in sorted(orders[key]):
            if fid not in key_ranges:
                raise KeyError(f"{key}: no range for ranked font {fid!r}")
            lo, hi = key_ranges[fid]
            out[key][fid] = tier(orders[key][fid], hi - lo, groups.get(fid, 0), cfg)
    return out


# --- the stage ----------------------------------------------------------------------------


def surveys_ranker(cfg: RankingConfig) -> Ranker:
    """The stage's ranker: the orders of every rank key from ``surveys.views``."""
    from tff_catalog import surveys

    def rank(inputs: RankInputs) -> Orders:
        return {key: _orders(s) for key, s in surveys.views(inputs, cfg).items()}

    return rank


def _orders(scores: SurveyScores) -> dict[str, int]:
    return {fid: p.order for fid, p in scores.placements.items()}


def load_inputs(ctx: StageContext, scores: Mapping[str, SurveyScores]) -> RankInputs:
    """The inputs stage "rank" ranked, rebuilt as ``surveys.run`` builds them.

    - terms.json less the families that failed L3 (``l3.json``, when present);
    - Rising's terms from this run's share history (``smoothing`` ``rising``);
    - Fonts Over Time's observed values replaced by this run's smoothed z
      (``smoothing`` ``fot_ewma``), in every view;
    - every view's terms by ``surveys.rank_inputs`` (monospace from facts.json);
    - z_R from ruler.json and each rank key's weights from ``scores``
      (scores.json): exactly what stage "rank" used.

    ``smoothing`` is the part stage "rank" wrote to ``build/state/`` this run.
    This mirrors ``surveys.run``; the stage warns when the rebuilt ranks differ
    from ranks.json.
    """
    from tff_catalog import stageio, state, surveys

    paths, cfg = ctx.paths, ctx.config.ranking
    failed = _l3_failed(paths)
    terms_all = {
        key: {s: {f: t for f, t in by_id.items() if f not in failed} for s, by_id in ts.items()}
        for key, ts in stageio.load_stage(paths, "terms").items()
    }
    smoothing = state.read_part(paths, "smoothing")
    dropped = surveys.stale_dropped(cfg, state.read_part(paths, "stale"))
    rise = _rising_terms(ctx, terms_all, smoothing.get("rising", {}), dropped)
    terms_all = _smoothed_fot(terms_all, smoothing.get("fot_ewma", {}), cfg)
    universe = stageio.load_stage(paths, "universe")
    facts = stageio.load_stage(paths, "facts")
    inputs = surveys.rank_inputs(
        terms_all,
        stageio.load_stage(paths, "ruler"),
        {fid: fam.family for fid, fam in universe.families.items()},
        cfg,
        monospace=frozenset(fid for fid, f in facts.items() if f.is_monospace),
        stale=dropped,
        rising=rise,
    )
    return dataclasses.replace(inputs, weights={key: dict(s.weights) for key, s in scores.items()})


def _l3_failed(paths: Paths) -> frozenset[str]:
    from tff_catalog import stageio

    if not stageio.stage_path(paths, "l3").is_file():
        return frozenset()
    return frozenset(f for f, r in stageio.load_stage(paths, "l3").items() if r.level == "failed")


def _rising_terms(
    ctx: StageContext,
    terms_all: Mapping[str, Mapping[str, Mapping[str, Term]]],
    history: Mapping[str, Mapping[str, list[float]]],
    dropped: frozenset[str],
) -> dict[str, dict[str, Term]]:
    """Rising's terms as stage "rank" built them from the history it wrote."""
    from tff_catalog import state, surveys

    cfg = ctx.config.ranking
    # The same sources and 12-month baselines (Rising's abstention) as stage "rank".
    rs = surveys.rising_shares(terms_all, cfg, dropped)
    live, baseline = sorted(rs.recent), rs.baseline
    first_seen = state.read_part(ctx.paths, "first_seen")
    new = _new_fonts(first_seen, ctx.run_date, cfg.ranks.rising.new_days)
    return surveys.rising_terms(
        {s: history[s] for s in live if s in history},
        cfg,
        baseline=baseline,
        new=new,
        dropped=dropped,
    )


def _new_fonts(first_seen: Mapping[str, Any], run_date: date, days: int) -> frozenset[str]:
    """Fonts whose earliest channel date is under ``days`` before the run (Rising's "New")."""
    new = set()
    for fid, entry in first_seen.items():
        dates = [date.fromisoformat(d) for d in (entry.get("sources") or {}).values() if d]
        if dates and (run_date - min(dates)).days < days:
            new.add(fid)
    return frozenset(new)


def _smoothed_fot(
    terms_all: Mapping[str, Mapping[str, Mapping[str, Term]]],
    ewma: Mapping[str, float],
    cfg: RankingConfig,
) -> dict[str, dict[str, Mapping[str, Term]]]:
    """Every view's Fonts Over Time terms with observed values replaced by the smoothed z."""
    from tff_catalog.surveys import FOT

    if not cfg.sources.fot.enabled or not ewma:
        return {key: dict(ts) for key, ts in terms_all.items()}
    out: dict[str, dict[str, Mapping[str, Term]]] = {}
    for key, ts in terms_all.items():
        out[key] = dict(ts)
        if FOT in ts:
            out[key][FOT] = {
                f: dataclasses.replace(t, value=float(ewma[f]))
                if t.state == "observed" and f in ewma
                else t
                for f, t in ts[FOT].items()
            }
    return out


def run(ctx: StageContext) -> None:
    """Stage "confidence"."""
    from tff_catalog import stageio

    ranking = ctx.config.ranking
    scores = stageio.load_stage(ctx.paths, "scores")
    placements = stageio.load_stage(ctx.paths, "ranks")
    inputs = load_inputs(ctx, scores)
    ranker = surveys_ranker(ranking)
    published = {key: {fid: p.order for fid, p in ps.items()} for key, ps in placements.items()}
    _check_rebuilt(ctx, ranker(inputs), published)
    ranges = perturb(
        inputs, ranking.uncertainty, ranker=ranker, plan=layout(ranking, inputs.weights)
    )
    out: dict[str, dict[str, Confidence]] = {}
    for key in sorted(published, key=_key_order):
        orders = published[key]
        key_ranges = {
            fid: contain(ranges.get(key, {}).get(fid, (o, o)), o) for fid, o in orders.items()
        }
        fused = scores[key].fused if key in scores else {}
        groups = {fid: len(f.groups) for fid, f in fused.items()}
        key_tiers = tiers({key: key_ranges}, {key: orders}, groups, ranking.tiers)[key]
        out[key] = {fid: Confidence(key_ranges[fid], key_tiers[fid]) for fid in sorted(orders)}
        ctx.log.info("%s: %d fonts, tiers %s", key, len(orders), _tier_counts(key_tiers))
    stageio.dump_stage(ctx.paths, "confidence", out)


def _check_rebuilt(ctx: StageContext, rebuilt: Orders, published: Orders) -> None:
    """Warn for each rank key whose rebuilt orders differ from ranks.json.

    They should not: ``load_inputs`` mirrors stage "rank". Where they do, the
    ranges are still widened to hold the published orders.
    """
    for key in sorted(published, key=_key_order):
        mine, theirs = rebuilt.get(key, {}), published[key]
        differ = sum(1 for f in set(mine) | set(theirs) if mine.get(f) != theirs.get(f))
        if differ:
            ctx.log.warning(
                "%s: rebuilt ranks differ from ranks.json for %d fonts (%d published); "
                "ranges are widened to the published orders",
                key,
                differ,
                len(theirs),
            )


def _tier_counts(key_tiers: Mapping[str, Tier]) -> str:
    counts = dict.fromkeys(("A", "B", "C"), 0)
    for t in key_tiers.values():
        counts[t] += 1
    return " ".join(f"{t}={n}" for t, n in counts.items())
