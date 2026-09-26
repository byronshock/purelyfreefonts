"""Stage "rank": per-survey scores, the overall rank and the views (milestone-1 steps 11-12). Owner: agent P8.

For each rank key (``config_model.RANK_KEYS``): equate each source to the
ruler, weight it (``effective_weights``), fuse with shrinkage and the guard,
then order, gate and band. The overall rank reuses the ``desktop_chosen`` and
``project`` terms, weighted v_s = M_g·w'_s/W_g and shrunk once (Σ M_g = 1),
with each survey's guard factors reused. Extra views never feed overall.

Reads ``build/stage/terms.json`` and ``build/stage/ruler_counts.json`` (stage
"correct"): z_R comes from the ruler counts (``equate.build_ruler``), never
from the floored Homebrew terms. Independence groups come from each
``Term.group`` (gate M5 makes them per family). Writes ``build/stage/ruler.json``,
``build/stage/scores.json`` (with guard events) and ``build/stage/ranks.json``,
and its part of the next state, ``smoothing`` (``state.write_part``).

Which terms each rank key ranks on (``view_terms``):

- ``desktop_chosen``, ``desktop_installed`` and ``project``: their own entry in
  terms.json. Stage "correct" has already removed the Linux terms that abstain
  (D8), so this module never decides an abstention.
- ``coding`` and ``dev_apps``: their own entry if terms.json has one, else each
  source's terms from the survey view with the same abstention (``coding``
  abstains, so Arch comes from ``desktop_chosen``). ``coding`` keeps monospace
  families only (``build/stage/facts.json``), filtered before equating.
- ``overall``: the terms of its mix keys (``ranks.overall.mix``).
- ``rising``: log-ratio terms built here from ``terms.json["rising"]`` (each
  rising source's most recent month, per family, which stage "correct" is
  expected to write) and the rolling history in ``state/smoothing.json``.
  The 12-month share comes from the same source in the survey view with the
  same abstention as Rising (``desktop_chosen`` or ``project``), so both
  shares are of the same families. A source whose Rising terms equal its
  12-month terms carries no recent window and is left out, with a warning
  (``rising_shares``). So is a source whose ``publish_raw`` is false (Google):
  its monthly shares would be published in the smoothing state
  (``RISING_KEEPS_PRIVATE_SHARES``, owner question T).

Before any key ranks on them, Fonts Over Time's observed values are replaced
by its EWMA-smoothed z (``smooth``, methodology §6).

The rest of the other stage files it reads: ``universe.json`` (names, for the
tie order), ``facts.json`` (monospace), ``l3.json`` when present (families that
failed L3 are removed before the ruler is built, so they shift nothing), the
Fonts Over Time records (``records/<collector>.jsonl``, to count its weekly
snapshots for the phase-in) and this run's ``stale`` and ``first_seen`` state
parts.

``smoothing.json``, written here::

    {"month": "YYYY-MM",                       the run month that wrote it
     "fot_ewma": {id: z},                      Fonts Over Time's smoothed z (§6)
     "fot_ewma_base": {id: z},                 the previous month's, which this month smoothed from
     "fot_weeks": ["YYYY-Www", ...],           FOT weekly snapshots seen so far (phase-in)
     "rising": {source: {id: [share, ...]}}}   monthly shares, oldest first

A second merged run in the same month recomputes that month from the same
base (``month``), so smoothing never advances twice in one month.
"""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from statistics import fmean
from typing import TYPE_CHECKING, Any, Literal

from tff_catalog import records, stageio, state
from tff_catalog.config_model import RANK_KEYS, SurveyRank, WeightedView
from tff_catalog.corrections import Term
from tff_catalog.engine import equate, order
from tff_catalog.engine import fuse as fusion
from tff_catalog.engine.fuse import Fused
from tff_catalog.engine.order import Placement, Scored

if TYPE_CHECKING:
    from pathlib import Path

    from tff_catalog.config_model import RankingConfig, RisingView
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext

# {source: {family_id: Term}} for one view.
type Terms = Mapping[str, Mapping[str, Term]]

# Why a listed font has no place in a rank key (catalog-site ``ranks.<key>.unranked``).
Unranked = Literal["no_deliberate_evidence", "no_evidence", "too_new"]

INSTALLED = "desktop_installed"  # the one view where no source abstains (D8)
# Rising is a rank other than most installed, so its Linux sources abstain (§5,
# D8); stage "correct" writes terms.json["rising"] that way (view_sources).
RISING_ABSTAINS = True
# Rising keeps each source's monthly shares in state/smoothing.json, which is public.
# Rulings T2 and T4 publish a source whose publish_raw is false (Google) as ranks and
# z only, so such a source takes no part in Rising and none of its shares are kept.
# Owner question T (pending): true would keep them there, which T2 does not allow.
RISING_KEEPS_PRIVATE_SHARES = False
FOT = "fot"  # the engine source with a phase-in weight and EWMA smoothing (D11, §6)
_WEEK = re.compile(r"^\d{4}-W\d{2}$")
_USED = frozenset({"observed", "censored"})  # the evidence states that make a term
# The mean of equal shares can miss them by an ulp, so a flat font's log-ratio
# comes out near 1e-16; anything this close to 0 is rounding, not a rise.
_RISE_TOL = 1e-9


@dataclass(frozen=True, slots=True)
class RankInputs:
    """Everything needed to (re)compute the ranks; confidence perturbs ``weights``.

    - ``terms`` has an entry per rank key except ``overall``, which reuses its
      mix keys' terms. ``terms["rising"]``, when present, holds per-source
      log-ratios (``rising_terms``), not counts.
    - ``weights[key]`` is ``effective_weights`` for that key. ``weights["overall"]``
      is informational (v_s): ``overall`` recomputes it from the mix keys'
      weights, so perturbing the survey weights is what moves overall, with
      M_g fixed. Rising has no weights.
    """

    terms: Mapping[str, Terms]  # {rank key: terms}; each Term carries its group and factor
    ruler: Mapping[str, float]  # z_R by family, from build/stage/ruler_counts.json
    weights: Mapping[str, Mapping[str, float]]  # {rank key: {source: weight}} (effective_weights)
    names: Mapping[str, str]  # family_id -> family name (tie order)


@dataclass(frozen=True, slots=True)
class SurveyScores:
    key: str  # rank key
    fused: Mapping[str, Fused]  # by family_id
    placements: Mapping[str, Placement]
    overlaps: Mapping[str, int]  # source -> |O_s|
    weights: Mapping[str, float]  # source -> w_eff


@dataclass(frozen=True, slots=True)
class _Part:
    """One survey's equated terms and guard factors; overall reuses both."""

    z: dict[str, dict[str, float]]  # family_id -> source -> z, weight-carrying terms only
    guard: dict[str, dict[str, float]]  # family_id -> source -> guard factor


# --- weights --------------------------------------------------------------------------------------


def nominal_weights(cfg: RankingConfig, key: str) -> dict[str, float]:
    """The configured weight w of each engine source in rank key ``key``.

    A survey view takes its survey's weights, a weighted view its own. Overall
    and rising have none of their own ({}).
    """
    spec = getattr(cfg.ranks, key)
    if isinstance(spec, SurveyRank):
        return dict(getattr(cfg.surveys, spec.survey).weights)
    if isinstance(spec, WeightedView):
        return dict(spec.weights)
    return {}


def effective_weights(
    cfg: RankingConfig,
    key: str,
    overlaps: Mapping[str, int],
    stale_dropped: frozenset[str],
    phase_in: Mapping[str, bool],
) -> dict[str, float]:
    """The weight of each engine source in rank key ``key``.

    First, for every source: w_eff = w · overlap_scale(|O_s|) · (0 if the source
    is disabled or stale-dropped), where w is the nominal weight
    (``surveys.<survey>.weights`` or ``ranks.<view>.weights``) and a phasing-in
    source (Fonts Over Time before ``phase_in_snapshots``) uses its
    ``phase_in_weight`` in place of w.

    Then, for the project survey only (``project``, and the project part of
    ``overall``), gate M9 (b): each group G in ``project_group_shares`` keeps
    its fixed share, split pro rata inside the group:
    w_s = share_G · w_eff_s / Σ_{t∈G} w_eff_t. A group whose effective weights
    are all 0 contributes nothing, so W_project is the sum of the other groups'
    shares. Desktop and the extra views use w_eff as it is.

    W_g, the total used for shrinkage, is the sum of the returned weights.

    Every source of the key is returned, 0.0 when it is switched off, keys
    sorted. ``overall`` returns v_s = M_g·w_s/W_g (``mix_weights``) over its
    mix keys, whose overlaps ``overlaps`` must hold too, so W = Σ M_g; rising
    returns {} (it is not a weighted fusion).
    """
    if key == "overall":
        mix = cfg.ranks.overall.mix
        per_key = {
            k: effective_weights(cfg, k, overlaps, stale_dropped, phase_in) for k in sorted(mix)
        }
        return mix_weights(mix, per_key)
    sources = cfg.sources.all()
    e = cfg.engine
    out: dict[str, float] = {}
    for name, w in sorted(nominal_weights(cfg, key).items()):
        src = sources[name]
        if not src.enabled or name in stale_dropped:
            out[name] = 0.0
            continue
        if phase_in.get(name, False):
            w = getattr(src, "phase_in_weight", w)
        out[name] = w * equate.overlap_scale(overlaps.get(name, 0), e.overlap_full, e.overlap_off)
    spec = getattr(cfg.ranks, key)
    if isinstance(spec, SurveyRank) and spec.survey == "project":
        out = _group_shares(out, cfg)
    return out


def _group_shares(w_eff: Mapping[str, float], cfg: RankingConfig) -> dict[str, float]:
    """Gate M9 (b): each project group's fixed share, split pro rata to w_eff inside it."""
    out: dict[str, float] = {}
    for _, group in sorted(cfg.project_group_shares.items()):
        members = [s for s in group.sources if s in w_eff]
        total = math.fsum(w_eff[s] for s in members)
        for s in members:
            out[s] = group.share * w_eff[s] / total if total > 0 else 0.0
    loose = sorted(set(w_eff) - set(out))
    if loose:
        # config.check_ranking rules this out; a bypassed config must not mix scales.
        raise ValueError(f"project sources in no project group: {', '.join(loose)}")
    return dict(sorted(out.items()))


def mix_weights(
    mix: Mapping[str, float], weights: Mapping[str, Mapping[str, float]]
) -> dict[str, float]:
    """The overall weights v_s = M_g·w_s/W_g (D12) from each mix key g's weights.

    A mix key whose weights sum to 0 gives its sources 0, so Σ v is the sum of
    M_g over the mix keys that carry weight. Each source may feed one mix key
    only (methodology §1); a second one raises ``ValueError``.
    """
    out: dict[str, float] = {}
    for key in sorted(mix):
        ws = weights.get(key, {})
        total = math.fsum(ws.values())
        for s in sorted(ws):
            if s in out:
                raise ValueError(f"engine source {s!r} feeds two keys of the overall mix")
            out[s] = mix[key] * ws[s] / total if total > 0 else 0.0
    return dict(sorted(out.items()))


def overlaps(terms: Terms, ruler: Mapping[str, float]) -> dict[str, int]:
    """|O_s| per source: its observed and censored families that the ruler carries."""
    return {
        s: sum(1 for f, t in terms[s].items() if t.state in _USED and f in ruler)
        for s in sorted(terms)
    }


def phase_in(cfg: RankingConfig, weeks: Iterable[str]) -> dict[str, bool]:
    """Whether Fonts Over Time still phases in: fewer than ``phase_in_snapshots`` weekly snapshots."""
    return {FOT: len(set(weeks)) < cfg.sources.fot.phase_in_snapshots}


def stale_dropped(cfg: RankingConfig, stale: Mapping[str, Mapping[str, Any]]) -> frozenset[str]:
    """Engine sources to drop: their collector (or the source itself) has been stale
    for more than ``stale.max_months`` runs (``state/stale.json`` as stage "parse" left it)."""
    dead = {
        name
        for name, entry in stale.items()
        if int(entry.get("stale_runs", 0)) > cfg.stale.max_months
    }
    return frozenset(
        name for name, src in cfg.sources.all().items() if name in dead or src.collector in dead
    )


# --- which terms each rank key uses ---------------------------------------------------------------


def view_terms(
    terms_all: Mapping[str, Terms],
    key: str,
    cfg: RankingConfig,
    monospace: frozenset[str] | None = None,
) -> dict[str, dict[str, Term]]:
    """The terms rank key ``key`` ranks on (module docstring), limited to its sources.

    ``monospace``, when given, restricts a ``monospace_only`` view to those
    families. ``overall`` merges its mix keys' terms; ``rising`` is built by
    ``rising_terms`` instead and gives {} here.
    """
    if key == "overall":
        merged: dict[str, dict[str, Term]] = {}
        for k in sorted(cfg.ranks.overall.mix):
            merged.update(view_terms(terms_all, k, cfg, monospace))
        return dict(sorted(merged.items()))
    spec = getattr(cfg.ranks, key)
    wanted = nominal_weights(cfg, key)
    if key in terms_all:
        base = terms_all[key]
    elif isinstance(spec, WeightedView):
        base = _assembled(terms_all, wanted, spec.abstain, cfg)
    else:
        base = {}
    out = {s: dict(sorted(base[s].items())) for s in sorted(base) if s in wanted}
    if isinstance(spec, WeightedView) and spec.monospace_only and monospace is not None:
        out = {s: {f: t for f, t in ts.items() if f in monospace} for s, ts in out.items()}
    return out


def _assembled(
    terms_all: Mapping[str, Terms], wanted: Iterable[str], abstain: bool, cfg: RankingConfig
) -> dict[str, Mapping[str, Term]]:
    """A weighted view's terms taken from the survey views with the same abstention."""
    sources = cfg.sources.all()
    out: dict[str, Mapping[str, Term]] = {}
    for s in sorted(wanted):
        key = survey_key(cfg, sources[s].survey, abstain)
        if key is not None and s in terms_all.get(key, {}):
            out[s] = terms_all[key][s]
    return out


def survey_key(cfg: RankingConfig, survey: str, abstain: bool) -> str | None:
    """The survey rank key of ``survey`` with that abstention, else any of that survey."""
    keys = [
        k
        for k in RANK_KEYS
        if isinstance(spec := getattr(cfg.ranks, k), SurveyRank) and spec.survey == survey
    ]
    exact = [k for k in keys if getattr(cfg.ranks, k).abstain == abstain]
    return (exact or keys or [None])[0]


def rank_inputs(
    terms_all: Mapping[str, Terms],
    ruler: Mapping[str, float],
    names: Mapping[str, str],
    cfg: RankingConfig,
    *,
    monospace: frozenset[str] = frozenset(),
    stale: frozenset[str] = frozenset(),
    phasing: Mapping[str, bool] | None = None,
    rising: Terms | None = None,
) -> RankInputs:
    """``RankInputs`` for every rank key: view terms, overlaps and effective weights.

    ``stale`` names the stale-dropped engine sources, ``phasing`` the
    phase-in state (``phase_in``) and ``rising`` the log-ratio terms
    (``rising_terms``); without it the Rising view is empty.
    """
    phasing = phasing if phasing is not None else {}
    keys = [k for k in RANK_KEYS if k not in ("overall", "rising")]
    terms: dict[str, Terms] = {k: view_terms(terms_all, k, cfg, monospace) for k in keys}
    weights: dict[str, Mapping[str, float]] = {
        k: effective_weights(cfg, k, overlaps(terms[k], ruler), stale, phasing) for k in keys
    }
    weights["overall"] = mix_weights(cfg.ranks.overall.mix, weights)
    if rising:
        terms["rising"] = rising
    ids = {f for ts in terms.values() for fam in ts.values() for f in fam} | set(ruler)
    return RankInputs(
        terms=terms,
        ruler=dict(sorted(ruler.items())),
        weights=weights,
        names={f: names.get(f, f) for f in sorted(ids)},
    )


# --- scoring --------------------------------------------------------------------------------------


def _frame(src_terms: Mapping[str, Term]) -> dict[str, float]:
    """x per family over the source's frame: observed values; censored ones are -inf."""
    xs: dict[str, float] = {}
    for fid, t in src_terms.items():
        if t.state == "observed":
            if t.value is None:
                raise ValueError(f"observed term for {fid!r} has no value")
            xs[fid] = float(t.value)
        elif t.state == "censored":
            xs[fid] = -math.inf
    return xs


def _part(
    terms: Terms, weights: Mapping[str, float], ruler: Mapping[str, float], cfg: RankingConfig
) -> _Part:
    """Equate every weighted source, then each family's guard factors (§4)."""
    z: dict[str, dict[str, float]] = {}
    for s in sorted(terms):
        w = weights.get(s, 0.0)
        if w <= 0:
            continue
        for fid, value in equate.equate_source(_frame(terms[s]), ruler).items():
            if w * terms[s][fid].factor > 0:  # a term with no weight is not one of the "others"
                z.setdefault(fid, {})[s] = value
    g = cfg.engine.guard
    guard = {fid: fusion.guard_factors(zf, g.gap, g.min_terms, g.factor) for fid, zf in z.items()}
    return _Part(dict(sorted(z.items())), dict(sorted(guard.items())))


def _fused(
    part: _Part, terms: Terms, weights: Mapping[str, float], cfg: RankingConfig
) -> dict[str, Fused]:
    """S per family, with W_g = Σ weights and the part's guard factors."""
    w_total = math.fsum(weights.values())
    out: dict[str, Fused] = {}
    for fid, zf in part.z.items():
        ts = {s: terms[s][fid] for s in zf}
        out[fid] = fusion.fuse(
            zf,
            {s: weights[s] * t.factor for s, t in ts.items()},
            w_total,
            cfg.engine.kappa,
            cfg.engine.mu0,
            part.guard.get(fid, {}),
            {s: t.group for s, t in ts.items()},
            frozenset(s for s, t in ts.items() if t.state == "observed"),
        )
    return out


def _place(
    fused: Mapping[str, Fused],
    names: Mapping[str, str],
    ruler: Mapping[str, float],
    cfg: RankingConfig,
) -> dict[str, Placement]:
    """Keep the ranked fonts, sort them deterministically, gate and band them."""
    scored = [
        Scored(
            fid, names.get(fid, fid), f.score, f.weight, ruler.get(fid), f.observed, len(f.groups)
        )
        for fid, f in sorted(fused.items())
    ]
    ranked = sorted(
        (s for s in scored if order.ranked(s, cfg.engine.min_observed_terms)), key=order.sort_key
    )
    gate = {s.id: order.gate(s, cfg.engine.top100_min_groups) for s in ranked}
    d = cfg.display
    return order.place(ranked, gate, d.exact_top, d.bands, d.open_band_from)


def _scores(key: str, inputs: RankInputs, cfg: RankingConfig) -> tuple[SurveyScores, _Part]:
    terms, weights = inputs.terms.get(key, {}), inputs.weights.get(key, {})
    part = _part(terms, weights, inputs.ruler, cfg)
    fused = _fused(part, terms, weights, cfg)
    scores = SurveyScores(
        key=key,
        fused=fused,
        placements=_place(fused, inputs.names, inputs.ruler, cfg),
        overlaps=overlaps(terms, inputs.ruler),
        weights=dict(sorted(weights.items())),
    )
    return scores, part


def score_survey(
    terms: Terms, weights: Mapping[str, float], ruler: Mapping[str, float], cfg: RankingConfig
) -> SurveyScores:
    """Equate, fuse, order and place one survey or view.

    W_g is the sum of ``weights``; a source with no weight contributes nothing
    (no term, no group). There are no names here, so ties that reach the name
    step fall to the family id, and ``key`` is left empty; ``views`` passes
    both.
    """
    inputs = RankInputs({"": terms}, ruler, {"": weights}, {})
    return _scores("", inputs, cfg)[0]


def overall(inputs: RankInputs, cfg: RankingConfig) -> SurveyScores:
    """The overall rank from the most-chosen desktop and project terms (D12).

    Every term of the mix keys (``ranks.overall.mix``) at v_s = M_g·w_s/W_g
    (``mix_weights`` over ``inputs.weights`` of the mix keys), shrunk once with
    W = Σ v = Σ M_g, and each term keeps the guard factor its own survey gave
    it: the guard is not rerun over the combined terms. A Linux source that
    abstains in ``desktop_chosen`` therefore abstains here too.
    """
    parts = {
        k: _part(inputs.terms.get(k, {}), inputs.weights.get(k, {}), inputs.ruler, cfg)
        for k in sorted(cfg.ranks.overall.mix)
    }
    return _overall(inputs, parts, cfg)


def _overall(inputs: RankInputs, parts: Mapping[str, _Part], cfg: RankingConfig) -> SurveyScores:
    mix = cfg.ranks.overall.mix
    v = mix_weights(mix, {k: inputs.weights.get(k, {}) for k in mix})
    terms: dict[str, Mapping[str, Term]] = {}
    z: dict[str, dict[str, float]] = {}
    guard: dict[str, dict[str, float]] = {}
    for k in sorted(mix):
        terms.update({s: t for s, t in inputs.terms.get(k, {}).items() if s in v})
        for fid, zf in parts[k].z.items():
            z.setdefault(fid, {}).update(zf)
        for fid, gf in parts[k].guard.items():
            guard.setdefault(fid, {}).update(gf)
    fused = _fused(_Part(dict(sorted(z.items())), guard), terms, v, cfg)
    return SurveyScores(
        key="overall",
        fused=fused,
        placements=_place(fused, inputs.names, inputs.ruler, cfg),
        overlaps=overlaps(terms, inputs.ruler),
        weights=v,
    )


def views(inputs: RankInputs, cfg: RankingConfig) -> dict[str, SurveyScores]:
    """Every published rank key, including coding, dev_apps and rising.

    Each key ranks only on ``inputs.terms[key]`` with ``inputs.weights[key]``,
    except overall (``overall``, reusing the surveys' equating and guards) and
    rising (``rising_terms`` agreement). So an extra view's weights never reach
    overall. Keys in ``RANK_KEYS`` order; a key with no terms is empty.
    """
    out: dict[str, SurveyScores] = {}
    parts: dict[str, _Part] = {}
    for key in RANK_KEYS:
        if key not in ("overall", "rising"):
            out[key], parts[key] = _scores(key, inputs, cfg)
    for k in cfg.ranks.overall.mix:
        if k not in parts:
            parts[k] = _part(inputs.terms.get(k, {}), inputs.weights.get(k, {}), inputs.ruler, cfg)
    out["overall"] = _overall(inputs, parts, cfg)
    out["rising"] = _rising_scores(inputs, cfg)
    return {key: out[key] for key in RANK_KEYS}


# --- rising ---------------------------------------------------------------------------------------


def _rises(ratio: float) -> bool:
    """A log-ratio that is a rise, not 0 give or take rounding (``_RISE_TOL``)."""
    return ratio > _RISE_TOL


def _agreed(ratios: Iterable[float], need: int) -> float | None:
    """The rise at least ``need`` sources agree on: the need-th largest positive log-ratio."""
    ups = sorted((r for r in ratios if _rises(r)), reverse=True)
    return ups[need - 1] if len(ups) >= need else None


def rising_ratios(
    history: Mapping[str, Mapping[str, list[float]]],
    cfg: RisingView,
    baseline: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, dict[str, float]]:
    """{source: {id: log(recent share / 12-month share)}} for the fonts that qualify.

    ``history[source][id]`` holds the font's monthly shares of that source,
    oldest first, this month last. The recent share is the mean of the last
    ``smoothing_months``; the 12-month share is ``baseline[source][id]`` (the
    font's share of the source's 12-month window), or the mean of the whole
    history when ``baseline`` is None. A font needs ``min_history_months``
    months and a recent share of at least ``min_share``. Only
    ``cfg.sources`` count.
    """
    out: dict[str, dict[str, float]] = {}
    window = max(1, cfg.smoothing_months)
    for s in sorted(set(history) & set(cfg.sources)):
        base_s = None if baseline is None else baseline.get(s, {})
        ratios: dict[str, float] = {}
        for fid in sorted(history[s]):
            shares = [float(x) for x in history[s][fid]]
            if len(shares) < max(1, cfg.min_history_months):
                continue
            recent = fmean(shares[-window:])
            base = fmean(shares) if base_s is None else float(base_s.get(fid, 0.0))
            if recent >= cfg.min_share and recent > 0 and base > 0:
                ratios[fid] = math.log(recent / base)
        if ratios:
            out[s] = ratios
    return out


def rising(
    history: Mapping[str, Mapping[str, list[float]]],
    cfg: RisingView,
    *,
    baseline: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, float]:
    """Rising (beta): smoothed log-ratio of recent to 12-month share, 2 sources agreeing.

    Per source, ``rising_ratios``. A font rises when at least ``min_sources``
    sources show a positive log-ratio (above rounding noise, ``_RISE_TOL``);
    its score is the ``min_sources``-th
    largest of them, the rise that many sources agree on, so no single source
    (Google's spikes) sets it. Fonts that do not rise are left out.
    """
    by_family: dict[str, list[float]] = {}
    for ratios in rising_ratios(history, cfg, baseline).values():
        for fid, r in ratios.items():
            by_family.setdefault(fid, []).append(r)
    need = max(1, cfg.min_sources)
    out = {fid: _agreed(rs, need) for fid, rs in sorted(by_family.items())}
    return {fid: score for fid, score in out.items() if score is not None}


def rising_terms(
    history: Mapping[str, Mapping[str, list[float]]],
    cfg: RankingConfig,
    *,
    baseline: Mapping[str, Mapping[str, float]] | None = None,
    new: frozenset[str] = frozenset(),
    dropped: frozenset[str] = frozenset(),
) -> dict[str, dict[str, Term]]:
    """Rising's terms for ``RankInputs``: one observed Term per source and font, value =
    its log-ratio (``rising_ratios``). ``new`` fonts (under ``new_days``) go to "New"
    instead; disabled and ``dropped`` sources are left out."""
    sources = cfg.sources.all()
    return {
        s: {
            fid: Term(value=r, state="observed", group=sources[s].group)
            for fid, r in ratios.items()
            if fid not in new
        }
        for s, ratios in rising_ratios(history, cfg.ranks.rising, baseline).items()
        if sources[s].enabled and s not in dropped
    }


def _rising_scores(inputs: RankInputs, cfg: RankingConfig) -> SurveyScores:
    """Place the fonts that rise (``rising``'s agreement rule) on ``inputs.terms["rising"]``."""
    terms = inputs.terms.get("rising", {})
    need = max(1, cfg.ranks.rising.min_sources)
    by_family: dict[str, dict[str, Term]] = {}
    for s in sorted(terms):
        for fid, t in terms[s].items():
            if t.state == "observed" and t.value is not None:
                by_family.setdefault(fid, {})[s] = t
    fused: dict[str, Fused] = {}
    for fid, ts in sorted(by_family.items()):
        score = _agreed((t.value for t in ts.values() if t.value is not None), need)
        if score is None:
            continue
        agreeing = sorted(s for s, t in ts.items() if t.value is not None and _rises(t.value))
        fused[fid] = Fused(
            score=score,
            weight=float(len(agreeing)),
            terms=len(ts),
            observed=len(agreeing),
            groups=tuple(sorted({ts[s].group for s in agreeing})),
        )
    return SurveyScores(
        key="rising",
        fused=fused,
        placements=_place(fused, inputs.names, inputs.ruler, cfg),
        overlaps={},
        weights={},
    )


def shares(src_terms: Mapping[str, Term]) -> dict[str, float]:
    """Each observed family's share of the source's total (values of 0 or less count 0)."""
    values = {
        f: float(t.value)
        for f, t in src_terms.items()
        if t.state == "observed" and t.value is not None and t.value > 0
    }
    total = math.fsum(values.values())
    return {f: v / total for f, v in sorted(values.items())} if total > 0 else {}


def next_history(
    prev: Mapping[str, Mapping[str, list[float]]],
    current: Mapping[str, Mapping[str, float]],
    keep: int,
) -> dict[str, dict[str, list[float]]]:
    """Append this month's shares (``current``) to each source's history, keeping ``keep`` months.

    A source with no data this month keeps its history as it was. A font the
    source no longer shows gets 0.0; a font whose kept months are all 0 is
    dropped.
    """
    out: dict[str, dict[str, list[float]]] = {}
    for s in sorted(set(prev) | set(current)):
        old = prev.get(s, {})
        if s not in current:
            hist = {f: [float(x) for x in h] for f, h in sorted(old.items())}
        else:
            now = current[s]
            hist = {}
            for f in sorted(set(old) | set(now)):
                h = [*(float(x) for x in old.get(f, [])), float(now.get(f, 0.0))][-keep:]
                if any(x > 0 for x in h):
                    hist[f] = h
        if hist:
            out[s] = hist
    return out


# --- Fonts Over Time smoothing ----------------------------------------------------------------------


def smooth(
    terms: Mapping[str, Term], ruler: Mapping[str, float], prev: Mapping[str, float], lam: float
) -> tuple[dict[str, Term], dict[str, float]]:
    """FOT's EWMA (methodology §6): z = λ·z_now + (1 - λ)·z_prev for each observed family.

    z_now is the source equated to the ruler; a family with no z_prev keeps
    z_now. Returns the terms with each observed value replaced by its smoothed
    z (equating is rank-based, so the engine then ranks FOT by the smoothed z
    and maps it back onto the ruler), and the smoothed z to carry forward.
    Censored terms stay as they are.
    """
    z_now = equate.equate_source(_frame(terms), ruler)
    out = dict(sorted(terms.items()))
    smoothed: dict[str, float] = {}
    for fid, t in out.items():
        if t.state != "observed" or fid not in z_now:
            continue
        z = z_now[fid] if fid not in prev else lam * z_now[fid] + (1 - lam) * float(prev[fid])
        smoothed[fid] = z
        out[fid] = replace(t, value=z)
    return out, smoothed


def _smooth_everywhere(
    terms_all: Mapping[str, Terms],
    ruler: Mapping[str, float],
    prev: Mapping[str, float],
    cfg: RankingConfig,
) -> tuple[dict[str, Terms], dict[str, float]]:
    """``smooth`` FOT once, on its survey view, and use the smoothed values in every view."""
    fot = cfg.sources.fot
    key = survey_key(cfg, fot.survey, abstain=False)
    base = terms_all.get(key or "", {}).get(FOT)
    if not fot.enabled or not base:
        return dict(terms_all), {}
    _, z = smooth(base, ruler, prev, fot.ewma_lambda)
    out: dict[str, Terms] = {}
    for k, ts in terms_all.items():
        if FOT in ts:
            fot_terms = {
                f: replace(t, value=z[f]) if t.state == "observed" and f in z else t
                for f, t in ts[FOT].items()
            }
            ts = {**ts, FOT: fot_terms}
        out[k] = ts
    return out, z


# --- fonts listed without a rank ------------------------------------------------------------------


def unranked(
    terms_all: Mapping[str, Terms],
    scores: Mapping[str, SurveyScores],
    cfg: RankingConfig,
    members: Mapping[str, Iterable[str]],
) -> dict[str, dict[str, Unranked]]:
    """Why each listed font has no place in a rank key, for the site's "Not ranked" label.

    ``members`` is {rank key: the family ids listed in it} (the catalog, and
    only its monospace fonts for coding); ``terms_all`` is terms.json and
    ``scores`` is scores.json. For each listed font without a placement:

    - ``no_deliberate_evidence``: a source of the key (weight > 0) has an
      observed term for it in ``desktop_installed`` but none in the key: a
      Linux source abstained (D8), so the font is listed with its tag;
    - ``too_new``: otherwise, when one of its terms in the key is too new;
    - ``no_evidence``: everything else.

    Rising is skipped: a font that does not rise is not "unranked" there.
    """
    installed = terms_all.get(INSTALLED, {})
    out: dict[str, dict[str, Unranked]] = {}
    for key in sorted(members):
        if key == "rising":
            continue
        view = view_terms(terms_all, key, cfg)
        sc = scores.get(key)
        placed = sc.placements if sc is not None else {}
        live = sorted(s for s, w in (sc.weights if sc is not None else {}).items() if w > 0)
        reasons: dict[str, Unranked] = {}
        for fid in sorted(set(members[key])):
            if fid not in placed:
                reasons[fid] = _why(fid, view, installed, live)
        out[key] = reasons
    return out


def _why(fid: str, view: Terms, installed: Terms, live: Iterable[str]) -> Unranked:
    for s in live:
        ref = installed.get(s, {}).get(fid)
        if ref is not None and ref.state == "observed" and fid not in view.get(s, {}):
            return "no_deliberate_evidence"
    if any(fid in ts and ts[fid].state == "too_new" for ts in view.values()):
        return "too_new"
    return "no_evidence"


# --- the stage ------------------------------------------------------------------------------------


def _l3_failures(paths: Paths) -> frozenset[str]:
    """Families that failed L3 (``build/stage/l3.json``, when stage "verify" has run)."""
    if not stageio.stage_path(paths, "l3").is_file():
        return frozenset()
    results = stageio.load_stage(paths, "l3")
    return frozenset(fid for fid, r in results.items() if r.level == "failed")


def _fot_weeks(path: Path) -> set[str]:
    """The weekly series ("YYYY-Www") in the FOT records of this run, if any."""
    if not path.is_file():
        return set()
    return {
        r.series
        for r in records.read_jsonl(path)
        if isinstance(r, records.Observation) and _WEEK.match(r.series)
    }


def _new_fonts(first_seen: Mapping[str, Any], run_date: date, days: int) -> frozenset[str]:
    """Fonts whose earliest channel date is under ``days`` before the run (Rising's "New")."""
    new = set()
    for fid, entry in first_seen.items():
        dates = [date.fromisoformat(d) for d in (entry.get("sources") or {}).values() if d]
        if dates and (run_date - min(dates)).days < days:
            new.add(fid)
    return frozenset(new)


def _without(terms_all: Mapping[str, Terms], excluded: frozenset[str]) -> dict[str, Terms]:
    return {
        k: {s: {f: t for f, t in fam.items() if f not in excluded} for s, fam in ts.items()}
        for k, ts in terms_all.items()
    }


@dataclass(frozen=True, slots=True)
class RisingShares:
    """This month's inputs to Rising, per rising source (``rising_shares``)."""

    recent: dict[str, dict[str, float]]  # {source: {id: share of the recent window}}
    baseline: dict[str, dict[str, float]]  # {source: {id: share of the 12-month window}}
    no_window: tuple[str, ...]  # sources whose Rising terms are their 12-month terms
    private: tuple[str, ...] = ()  # publish_raw false: left out (RISING_KEEPS_PRIVATE_SHARES)


def rising_shares(
    terms_all: Mapping[str, Terms], cfg: RankingConfig, dropped: frozenset[str]
) -> RisingShares:
    """Each enabled, not stale-dropped rising source's recent and 12-month shares.

    Recent: ``terms.json["rising"]``. 12-month: the same source in the survey
    view with the abstention of the Rising rank (``desktop_chosen`` for the
    desktop sources, ``project``), so the two shares are over the same
    families: a Linux source that abstains in one and not the other would
    make every one of its fonts look like it rises.

    A source whose Rising terms are exactly its 12-month terms has no recent
    window (stage "correct" wrote the 12-month values), so its log-ratios would
    measure the 12-month share's drift, not a rise: it is left out
    (``no_window``) and its history is not advanced.

    A source whose ``publish_raw`` is false is left out (``private``): its
    shares would go into the public smoothing state (``RISING_KEEPS_PRIVATE_SHARES``).

    Stage "confidence" rebuilds Rising and must take its baselines from this
    function too, or its Rising orders drift from the stage's.
    """
    recent_terms = terms_all.get("rising", {})
    sources = cfg.sources.all()
    recent: dict[str, dict[str, float]] = {}
    baseline: dict[str, dict[str, float]] = {}
    no_window: list[str] = []
    private: list[str] = []
    for s in sorted(cfg.ranks.rising.sources):
        if not sources[s].enabled or s in dropped or s not in recent_terms:
            continue
        if not sources[s].publish_raw and not RISING_KEEPS_PRIVATE_SHARES:
            private.append(s)
            continue
        key = survey_key(cfg, sources[s].survey, abstain=RISING_ABSTAINS)
        long_terms = terms_all.get(key or "", {}).get(s, {})
        if dict(recent_terms[s]) == dict(long_terms):
            no_window.append(s)
            continue
        recent[s] = shares(recent_terms[s])
        baseline[s] = shares(long_terms)
    return RisingShares(recent, baseline, tuple(no_window), tuple(private))


@dataclass(frozen=True, slots=True)
class _Smoothed:
    terms: dict[str, Terms]  # terms.json with FOT's observed values replaced by its smoothed z
    rising: dict[str, dict[str, Term]]  # rising_terms
    phasing: dict[str, bool]  # phase_in
    state: dict[str, Any]  # the smoothing.json part (module docstring)


def _smoothing(
    ctx: StageContext,
    terms_all: Mapping[str, Terms],
    ruler: Mapping[str, float],
    dropped: frozenset[str],
) -> _Smoothed:
    """FOT's phase-in and EWMA and Rising's history, from the committed smoothing state.

    It never reads this run's own output, and a second run in the same month
    redoes that month instead of adding one, so smoothing advances once a month.
    """
    cfg, paths, prev = ctx.config.ranking, ctx.paths, ctx.state.smoothing
    month = f"{ctx.run_date:%Y-%m}"
    again = prev.get("month") == month
    r = cfg.ranks.rising
    rs = rising_shares(terms_all, cfg, dropped)
    recent, baseline = rs.recent, rs.baseline
    if rs.no_window:
        ctx.log.warning(
            "rising: no recent window in terms.json for %s (its values are the 12-month "
            "terms); left out of Rising",
            ", ".join(rs.no_window),
        )
    if rs.private:
        ctx.log.info(
            "rising: %s left out (publish_raw is false; the smoothing state is public)",
            ", ".join(rs.private),
        )
    private = {s for s, src in cfg.sources.all().items() if not src.publish_raw}
    old = {
        s: {f: h[:-1] if again and s in recent else h for f, h in fam.items()}
        for s, fam in prev.get("rising", {}).items()
        if RISING_KEEPS_PRIVATE_SHARES or s not in private
    }
    history = next_history(old, recent, max(r.smoothing_months, r.min_history_months))
    new = _new_fonts(state.read_part(paths, "first_seen"), ctx.run_date, r.new_days)
    rise = rising_terms(
        {s: history[s] for s in recent if s in history},
        cfg,
        baseline=baseline,
        new=new,
        dropped=dropped,
    )
    records_path = paths.records / f"{cfg.sources.fot.collector}.jsonl"
    weeks = set(prev.get("fot_weeks", [])) | _fot_weeks(records_path)
    base = prev.get("fot_ewma_base" if again else "fot_ewma", {})
    smoothed, ewma = _smooth_everywhere(terms_all, ruler, base, cfg)
    part = {
        "month": month,
        "fot_ewma": ewma,
        "fot_ewma_base": dict(sorted(base.items())),
        "fot_weeks": sorted(weeks),
        "rising": history,
    }
    return _Smoothed(smoothed, rise, phase_in(cfg, weeks), part)


def run(ctx: StageContext) -> None:
    """Stage "rank"."""
    cfg, paths = ctx.config.ranking, ctx.paths
    excluded = _l3_failures(paths)
    terms_all = _without(stageio.load_stage(paths, "terms"), excluded)
    counts = stageio.load_stage(paths, "ruler_counts")
    ruler = equate.build_ruler({f: v for f, v in counts.items() if f not in excluded})
    universe = stageio.load_stage(paths, "universe")
    names = {fid: fam.family for fid, fam in universe.families.items()}
    facts = stageio.load_stage(paths, "facts")
    monospace = frozenset(fid for fid, f in facts.items() if f.is_monospace)
    dropped = stale_dropped(cfg, state.read_part(paths, "stale"))
    smoothed = _smoothing(ctx, terms_all, ruler, dropped)
    inputs = rank_inputs(
        smoothed.terms,
        ruler,
        names,
        cfg,
        monospace=monospace,
        stale=dropped,
        phasing=smoothed.phasing,
        rising=smoothed.rising,
    )
    scores = views(inputs, cfg)
    stageio.dump_stage(paths, "ruler", inputs.ruler)
    stageio.dump_stage(paths, "scores", scores)
    stageio.dump_stage(paths, "ranks", {k: dict(s.placements) for k, s in scores.items()})
    state.write_part(paths, "smoothing", smoothed.state, stage="rank")
    _report(ctx, inputs, scores, terms_all, excluded, dropped)


def _report(
    ctx: StageContext,
    inputs: RankInputs,
    scores: Mapping[str, SurveyScores],
    terms_all: Mapping[str, Terms],
    excluded: frozenset[str],
    dropped: frozenset[str],
) -> None:
    """Log what the review needs: counts, guard events, overlaps and weights, and the
    fonts unranked in most chosen only because of abstentions, which must carry a tag."""
    log = ctx.log
    for key, sc in scores.items():
        guarded = sum(1 for f in sc.fused.values() if f.guard)
        log.info("%s: %d ranked, %d guard events", key, len(sc.placements), guarded)
    for key in ("desktop_chosen", "project"):
        for s, w in inputs.weights.get(key, {}).items():
            n = scores[key].overlaps.get(s, 0)
            log.info("%s %s: overlap %d, weight %.4f", key, s, n, w)
    if excluded:
        log.info("left out after L3: %s", ", ".join(sorted(excluded)))
    if dropped:
        log.warning("stale-dropped sources: %s", ", ".join(sorted(dropped)))
    listed = {"desktop_chosen": scores[INSTALLED].placements}
    why = unranked(terms_all, scores, ctx.config.ranking, listed)["desktop_chosen"]
    abstained = sorted(f for f, reason in why.items() if reason == "no_deliberate_evidence")
    log.info("unranked in desktop_chosen only because of abstentions: %d", len(abstained))
    if abstained and stageio.stage_path(ctx.paths, "tags").is_file():
        tags = stageio.load_stage(ctx.paths, "tags")
        untagged = [
            f
            for f in abstained
            if f not in tags or not (tags[f].preinstalled_on or tags[f].pulled_in_by)
        ]
        if untagged:
            log.warning(
                "abstaining fonts with no preinstalled_on/pulled_in_by tag: %s", ", ".join(untagged)
            )
