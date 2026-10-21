"""The ranks the measurement model scores (owner rulings of 2026-10-01; docs/milestone-method.md
step 10). Owner: the lead.

``ranking.toml [model] views`` names them (q1_model_views: Projects and Developers & apps at
the switch); every other rank stays on the engine, two methods side by side (q15). Stage
"rank" builds each view's model from the run's stage (``modelinputs``), takes its fit from
the private store or fits it (``fitstore.obtain``, by the run's ``--write-fit`` or
``--fit-in-memory``), and gives every font with a term:

- its score, the posterior mean of its popularity, with its posterior sd and the posterior
  quantiles of ``[model] quantiles`` (q19: the likely range is the rank at those quantiles
  among everyone else's scores, which stage "confidence" works out);
- its independence groups with evidence, a group giving evidence when one of its terms sits
  above what bulk alone predicts: a count above the expected bulk, or, for a term with no
  bulk, a positive count or a place on the list (q14_two_groups); the two-group rule then
  holds a font out of the top 100 as before;
- each source's z, the posterior mean from that source's terms alone (q18b), and its share
  of the font's posterior expected information (q16), which export publishes for every
  source except Google and Fonts Over Time (null, as their parameters are private);
- the sources whose leave-one-term-out PIT lies in a tail of ``[model] pit_tail``: flagged
  for review, a font whose sources conflict, in place of the guard's down-weighting (q2).

They are ``ModelEntry`` rows in ``build/stage/model.json``, with each view's ``FitInfo`` in
``build/stage/fits.json``: where the fit came from, its sha256, the problems
``MixedFit.problems`` finds and the terms its monotonicity scan finds falling (validate's
hard checks). ``survey_scores`` turns the rows into the engine's ``Fused`` and
``Placement``, so the order, the two-group gate and the bands are the engine's own
(``order.place``). The order sorts on the score and then its sd, each rounded to ``DIGITS``
(scoring plan §8.4), the more precisely measured font first, then the name.

Both files stay in ``build/stage/``, never committed: the shares and per-source z of Google
and Fonts Over Time derive from their private values (T2, T4).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from tff_catalog.engine import builders
from tff_catalog.engine.fuse import Fused
from tff_catalog.engine.order import Placement, Scored

if TYPE_CHECKING:
    from tff_catalog.config_model import Model, RankingConfig
    from tff_catalog.engine.measure import Mixed, MixedFit
    from tff_catalog.stages import StageContext
    from tff_catalog.surveys import SurveyScores

# The views a model exists for, with their terms (engine.builders).
MODEL_VIEWS: Mapping[str, tuple[str, ...]] = {
    "project": builders.PROJECT_TERMS,
    "dev_apps": builders.DEV_APPS_TERMS,
}
# Each term's engine source (terms.json's key): Fonts Over Time's two terms are one source.
TERM_SOURCES: Mapping[str, str] = {
    builders.FOT_STARTUPS: "fot",
    builders.FOT_OTHERS: "fot",
    builders.ALMANAC: "almanac",
    builders.GOOGLE: "google",
    builders.NPM_FONTSOURCE: "npm_fontsource",
    builders.ECOSYSTEMS: "ecosystems",
    builders.JSDELIVR: "jsdelivr",
    builders.EXPO: "npm_expo",
}
FOT_SOURCE = "fot"  # surveys.FOT: Fonts Over Time keeps its rank-based z in export (q18b, T4)
# Sources whose information shares export leaves null (q16): their parameters are private.
PRIVATE_SHARES = frozenset({"google", FOT_SOURCE})
DIGITS = 6  # as export.DIGITS: the order compares scores and sds rounded to this


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """One font in one model view."""

    score: float  # the posterior mean of its popularity
    sd: float  # the posterior sd
    low: float  # the posterior quantile [model] quantiles[0]
    high: float  # the posterior quantile [model] quantiles[1]
    terms: int  # its terms
    groups: tuple[str, ...]  # independence groups with a term above bulk (q14), sorted
    z: dict[str, float]  # source -> posterior mean from that source's terms alone (q18b)
    info: dict[str, float]  # source -> share of its posterior expected information (q16)
    pit: tuple[str, ...]  # sources whose leave-one-term-out PIT is in a tail (q2), sorted


@dataclass(frozen=True, slots=True)
class FitInfo:
    """Where a view's fit came from, and what its checks found (names only, never values)."""

    fitted_on: date
    sha256: str
    source: Literal["store", "pending", "memory"]
    fonts: int
    problems: tuple[str, ...]  # MixedFit.problems()
    falls: tuple[str, ...]  # terms whose monotonicity scan finds a fall


def entries(
    fit: MixedFit,
    frames: Mapping[str, Mapping[str, Mapping[str, object]]],
    cfg: Model,
) -> dict[str, ModelEntry]:
    """Every font of ``fit``'s model as a ``ModelEntry``. ``frames`` are the Project term
    entries by source and id (``engine.builders.frame``): each term's independence group,
    and whether the Web Almanac lists the font."""
    import numpy as np
    from scipy import special

    from tff_catalog.engine import measure

    model = fit.model
    eap, sd = fit.scores()
    quantiles = fit.quantiles(cfg.quantiles)
    x, logw = fit.grid
    term_ll = model.term_logliks(fit.theta, x)
    shares = fit.info_shares()
    pit = measure.pit_flags(fit, cfg.pit_tail)  # each term's fonts, by their own counts
    sources = sorted({TERM_SOURCES[t.name] for t in model.counts})
    # Per source: the summed log-likelihood of its terms, for each font that has one.
    by_source: dict[str, np.ndarray] = {s: np.zeros((len(model.ids), len(x))) for s in sources}
    has: dict[str, np.ndarray] = {s: np.zeros(len(model.ids), bool) for s in sources}
    count = np.zeros(len(model.ids), int)
    groups: list[set[str]] = [set() for _ in model.ids]
    flagged: list[set[str]] = [set() for _ in model.ids]
    for j, (term, tc, rows, ll) in enumerate(
        zip(model.counts, model.split(fit.theta), model.rows, term_ll, strict=True)
    ):
        source = TERM_SOURCES[term.name]
        by_source[source][rows] += ll
        has[source][rows] = True
        count[rows] += 1
        evidence = _above_bulk(term, tc, frames.get(source, {}))
        for i, row in enumerate(rows):
            fid = str(model.ids[row])
            if evidence[i]:
                groups[row].add(str(frames[source][fid]["group"]))
            if pit[j][i]:
                flagged[row].add(source)
    alone: dict[str, np.ndarray] = {}
    for s in sources:
        a = by_source[s] + logw[None, :]
        alone[s] = np.exp(a - special.logsumexp(a, axis=1)[:, None]) @ x
    out: dict[str, ModelEntry] = {}
    for row, fid in enumerate(model.ids):
        mine = [s for s in sources if has[s][row]]
        info: dict[str, float] = {}
        for j, term in enumerate(model.counts):
            share = shares[row, j]
            if not math.isnan(share):
                s = TERM_SOURCES[term.name]
                info[s] = info.get(s, 0.0) + float(share)
        out[str(fid)] = ModelEntry(
            score=float(eap[row]),
            sd=float(sd[row]),
            low=float(quantiles[row, 0]),
            high=float(quantiles[row, 1]),
            terms=int(count[row]),
            groups=tuple(sorted(groups[row])),
            z={s: float(alone[s][row]) for s in mine},
            info=dict(sorted(info.items())),
            pit=tuple(sorted(flagged[row])),
        )
    return out


def _above_bulk(term, tc, frame: Mapping[str, Mapping[str, object]]):  # type: ignore[no-untyped-def]
    """For each of ``term``'s fonts, whether its term sits above what bulk alone predicts
    (q14): a count above the expected bulk; with no bulk, a positive count, or for the Web
    Almanac a place on its list."""
    import numpy as np

    from tff_catalog.engine import measure

    if term.name == builders.ALMANAC:
        return np.array([frame[str(f)]["state"] == "observed" for f in term.ids])
    bulk, _ = measure.means(term, tc, np.zeros(1))
    if term.n2d.sum() == 0:
        return term.y > 0
    return term.y > bulk[:, 0]


def survey_scores(
    key: str,
    rows: Mapping[str, ModelEntry],
    names: Mapping[str, str],
    cfg: RankingConfig,
) -> SurveyScores:
    """A model view as the engine's ``SurveyScores``: one ``Fused`` per font (every term
    counts as observed, q18a; no guard, q2) and the engine's placements."""
    from tff_catalog import surveys
    from tff_catalog.engine import order

    fused = {
        fid: Fused(
            score=e.score,
            weight=_precision(e.sd),
            terms=e.terms,
            observed=e.terms,
            groups=e.groups,
        )
        for fid, e in sorted(rows.items())
    }
    scored = [
        Scored(
            fid,
            names.get(fid, fid),
            round(e.score, DIGITS),
            _precision(e.sd),
            None,
            e.terms,
            len(e.groups),
        )
        for fid, e in sorted(rows.items())
    ]
    ranked = sorted(
        (s for s in scored if order.ranked(s, cfg.engine.min_observed_terms)), key=order.sort_key
    )
    gate = {s.id: order.gate(s, cfg.engine.top100_min_groups) for s in ranked}
    d = cfg.display
    placements: dict[str, Placement] = order.place(
        ranked, gate, d.exact_top, d.bands, d.open_band_from
    )
    return surveys.SurveyScores(
        key=key, fused=fused, placements=placements, overlaps={}, weights={}
    )


def _precision(sd: float) -> float:
    """The order's second key, larger first: 1/s² of the sd rounded to ``DIGITS``, so two
    fonts with the same rounded sd tie, as §8.4 rounds s."""
    s = round(sd, DIGITS)
    return math.inf if s <= 0 else 1.0 / (s * s)


def _merged_dates(ctx: StageContext) -> list[date]:
    return [
        date.fromisoformat(str(e["run_date"])) for e in ctx.state.run_history if e.get("run_date")
    ]


def run(ctx: StageContext) -> tuple[dict[str, dict[str, ModelEntry]], dict[str, FitInfo]]:
    """Stage "rank"'s model views: their entries and fits, for ``[model] views``."""
    cfg = ctx.config.ranking
    if not cfg.model.views:
        return {}, {}
    from tff_catalog import METHOD_VERSIONS, fitstore, modelinputs
    from tff_catalog.engine import measure

    data = modelinputs.project_data(ctx.paths, ctx.run_date)
    terms = builders.project_terms(data)
    out: dict[str, dict[str, ModelEntry]] = {}
    fits: dict[str, FitInfo] = {}
    for key in cfg.model.views:
        model, fixed = builders.model(terms, MODEL_VIEWS[key])

        def fit(m: Mixed, fixed: dict[int, float] = fixed) -> MixedFit:
            return measure.fit_mixed(m, fixed=fixed)

        got = fitstore.obtain(
            mode=ctx.options.fit,  # type: ignore[arg-type]
            store_root=None if ctx.store is None else ctx.store.root,
            view=key,
            run_date=ctx.run_date,
            merged_dates=_merged_dates(ctx),
            model=model,
            method_version=METHOD_VERSIONS[key],
            fit=fit,
        )
        if got.data is not None:
            fitstore.stage_pending(ctx.paths.stage, key, got.data)
        out[key] = entries(got.fit, data.frames, cfg.model)
        falls = tuple(m["term"] for m in measure.monotonicity_scan(got.fit) if m["falls"])
        fits[key] = FitInfo(
            fitted_on=got.fitted_on,
            sha256=got.sha256,
            source=got.source,
            fonts=len(model.ids),
            problems=tuple(got.fit.problems()),
            falls=falls,
        )
        ctx.log.info(
            "%s: model on %d fonts, fit of %s (%s)", key, len(model.ids), got.fitted_on, got.source
        )
    return out, fits
