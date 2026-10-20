"""The measurement model: one latent popularity per font, measured by count terms.

The owner's rulings of 2026-09-30 and 2026-10-01 on the scoring method (AUTHORITY.md, "The
scoring method"; ``data/reviews/method/``) replace the fused z for the Projects and
Developers & apps ranks with this model. Ported from the private workbench tff-stats
(``src/tff_stats/counts.py`` and ``checks.py``), counts only: the model's terms are all
count terms, so the censored-normal terms of tff-stats' ``factor`` are left out. Not yet
wired into the pipeline (docs/milestone-method.md step 6).

A count term models a source's raw counts in its window directly, as a baseline plus
popularity, so a low count is weak evidence rather than a censored one:

    y_f ~ NegBin(mean = n_f * b + exp(alpha + lambda * eta_f), dispersion k)

- ``n_f`` is how many packages the font's count sums over, so the baseline ``b`` (the bulk
  every package gets, whatever its popularity) scales with them;
- ``exp(alpha + lambda * eta)`` is the part that comes from people choosing the font;
- the negative binomial allows for counts more variable than a Poisson.

Counts in the millions use a log-normal instead (``dist``): either one noise on the total
(``"lognormal"``), or a bulk and a popularity part with their own noise
(``"lognormal_sum"``), their sum matched to a log-normal by Fenton-Wilkinson.

All terms share one latent eta ~ N(0, 1) per font. eta is integrated out on one uniform grid
(the trapezoid rule, ``GRID_STEP`` over [-6, 7]), and a font's score is its posterior mean on
the same grid, so the scores are the integrals the fit maximised.

Pure functions and frozen data over numpy arrays: no I/O, no config and no clock. The fitted
parameters derive from Google Fonts and Fonts Over Time values (rulings T2, T4), so nothing
here formats or logs a parameter or a count: ``MixedFit.problems`` names parameters only,
and the checks report rank statistics, falls in eta and font counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
from scipy import optimize, special, stats

LOG2PI = float(np.log(2 * np.pi))
GRID_LO, GRID_HI, GRID_STEP = -6.0, 7.0, 0.05  # the prior outside holds 1e-9
LOG_B_MIN = float(np.log(1e-12))  # "no bulk": a baseline held here contributes nothing
TAU_MIN = float(np.log(1e-3))
# Every parameter is bounded, so that the likelihood stays finite over the whole box the
# optimizer searches: L-BFGS-B's first steps can be huge, and an unbounded alpha once took a
# fit to exp(alpha + lambda eta) = inf, then NaN, which it did not always recover from (which
# it did depended on the BLAS kernel). With |alpha| <= 50 and lambda <= 20, alpha + lambda x
# stays under 190 at the grid's edges, far from exp's overflow at 709, and a real count's alpha
# (the log of its popularity part's median at the average font) lies far inside. A log-normal
# term's noise sd (on the log scale) stays under 20, so that exp(sigma^2) is finite too.
ALPHA_LIMIT = 50.0
LOG_SIGMA_MAX = float(np.log(20.0))
DISTS = ("negbin", "lognormal", "lognormal_sum")
# The 5% point of 1/2 chi2_0 + 1/2 chi2_1: a test of b = 0, which lies on the boundary.
LR_BOUNDARY_5PCT = 2.706
# A fitted model's record (``MixedFit.to_record``); a change of its shape bumps it.
RECORD_FORMAT = 1


def grid_nodes(
    step: float = GRID_STEP, lo: float = GRID_LO, hi: float = GRID_HI
) -> tuple[np.ndarray, np.ndarray]:
    """A uniform grid and the log weights of the trapezoid rule against N(0, 1).

    For an integrand as smooth as these posteriors, the trapezoid rule's error falls like
    exp(-2 pi^2 s^2 / step^2) for a posterior sd s: 3e-9 at s = step, far less above."""
    x = np.arange(lo, hi + step / 2, step)
    w = np.full(len(x), step)
    w[0] = w[-1] = step / 2
    return x, np.log(w) - 0.5 * x**2 - 0.5 * LOG2PI


@dataclass(frozen=True, eq=False)
class CountTerm:
    """One source's counts for the fonts it has.

    ``n`` is the packages summed, per baseline group: shape (N,) for one baseline, or (N, K)
    for K groups that share the popularity curve but not the baseline. A young package's
    share of the window multiplies its entry of ``n``: it scales the expected bulk only."""

    name: str
    ids: np.ndarray  # fonts with this term, unique
    y: np.ndarray  # counts in the window (the low end when ``y_hi`` is given)
    n: np.ndarray  # packages summed, per baseline group (the baselines' multipliers)
    groups: tuple[str, ...] = ("packages",)
    # The count is only known to lie in [y, y_hi] (a source that hides small counts, such as
    # pkgstats below 16 systems a month); None means exact.
    y_hi: np.ndarray | None = None
    # A multiplier on the whole mean (pkgstats: the system-months sampled); None means 1.
    exposure: np.ndarray | None = None
    # "negbin" for counts; "lognormal" for counts so large that counting noise is nil: log y
    # ~ Normal(log mean, sigma), its noise parameter being log sigma instead of log k;
    # "lognormal_sum" for a bulk and a popularity part with their own noise (sigma for
    # popularity, tau for the bulk: one more parameter).
    dist: str = "negbin"

    def __post_init__(self) -> None:
        if self.dist not in DISTS:
            raise ValueError(f"CountTerm {self.name}: dist must be one of {DISTS}")
        if len(self.groups) != self.k:
            raise ValueError(f"CountTerm {self.name}: one group name per column of n")
        if len(np.unique(self.ids)) != len(self.ids):
            raise ValueError(f"CountTerm {self.name}: a font appears twice")

    @property
    def k(self) -> int:
        return 1 if self.n.ndim == 1 else int(self.n.shape[1])

    @property
    def n2d(self) -> np.ndarray:
        return self.n[:, None] if self.n.ndim == 1 else self.n

    @property
    def n_params(self) -> int:
        return self.k + 3 + (self.dist == "lognormal_sum")

    def param_names(self) -> list[str]:
        noise = "log k" if self.dist == "negbin" else "log sigma"
        out = [f"log b[{g}]" for g in self.groups] + ["alpha", "log lambda", noise]
        return out + (["log tau"] if self.dist == "lognormal_sum" else [])


def nb_logpmf(y: Any, mean: Any, k: Any) -> np.ndarray:
    """log NegBin(y | mean, dispersion k), elementwise with broadcasting. The binomial
    coefficient goes through betaln and y log(mean / (k + mean)) through log1p, which keep
    their precision for counts near 10^12 (a plain difference of logs loses 1e-4 there)."""
    return (
        -np.log(y + k)
        - special.betaln(k, y + 1)
        + k * (np.log(k) - np.log(k + mean))
        - y * np.log1p(k / mean)
    )


def nb_interval_logp(lo: Any, hi: Any, mean: Any, k: Any) -> np.ndarray:
    """log P(lo <= Y <= hi) for Y ~ NegBin(mean, dispersion k), elementwise."""
    p = k / (k + mean)
    # P(Y <= y) = I_p(k, y + 1), the regularized incomplete beta function
    upper = special.betainc(k, hi + 1, p)
    lower = np.where(lo > 0, special.betainc(k, np.maximum(lo, 1), p), 0.0)
    return np.log(np.clip(upper - lower, 1e-300, None))


def log_ndtr_diff(hi: Any, lo: Any) -> np.ndarray:
    """log(Phi(hi) - Phi(lo)) for hi >= lo, accurate in both tails."""
    flip = lo > 0  # in the upper tail, Phi(hi) - Phi(lo) = Phi(-lo) - Phi(-hi)
    a, b = np.where(flip, -lo, hi), np.where(flip, -hi, lo)
    la, lb = special.log_ndtr(a), special.log_ndtr(b)
    return la + np.log(np.clip(-np.expm1(np.minimum(lb - la, 0.0)), 1e-300, None))


def _lognormal_ll(term: CountTerm, m: np.ndarray, s: Any) -> np.ndarray:
    """log density of log y ~ N(m, s^2) (N x Q), or of the interval [y, y_hi]."""
    s = np.broadcast_to(s, m.shape)
    ly = np.log(np.maximum(term.y, 1e-300))[:, None]
    r = (ly - m) / s
    ll = -0.5 * r**2 - np.log(s) - 0.5 * LOG2PI - ly
    if term.y_hi is not None:
        # an interval [y, y_hi]: below a list's cut-off, or a zero read as "under 1"
        wide = term.y_hi > term.y
        if wide.any():
            mw, sw = m[wide], s[wide]
            hi = (np.log(term.y_hi[wide])[:, None] - mw) / sw
            lo_y = term.y[wide][:, None]
            lo = np.where(lo_y > 0, (np.log(np.maximum(lo_y, 1e-300)) - mw) / sw, -np.inf)
            ll[wide] = log_ndtr_diff(hi, lo)
    return ll


@dataclass(frozen=True, eq=False)
class Mixed:
    """The terms of one model, aligned on the union of their fonts (``ids``, sorted)."""

    counts: tuple[CountTerm, ...]
    ids: np.ndarray = field(init=False)
    rows: tuple[np.ndarray, ...] = field(init=False)  # each term's fonts' rows in ``ids``

    def __post_init__(self) -> None:
        counts = tuple(self.counts)
        names = [t.name for t in counts]
        if not counts or len(set(names)) != len(names):
            raise ValueError("Mixed: at least one term, each named once")
        ids = np.array(sorted(set().union(*(set(t.ids.tolist()) for t in counts))))
        object.__setattr__(self, "counts", counts)
        object.__setattr__(self, "ids", ids)
        object.__setattr__(self, "rows", tuple(np.searchsorted(ids, t.ids) for t in counts))

    @property
    def n_params(self) -> int:
        return sum(t.n_params for t in self.counts)

    def split(self, theta: np.ndarray) -> list[np.ndarray]:
        out, at = [], 0
        for term in self.counts:
            out.append(theta[at : at + term.n_params])
            at += term.n_params
        return out

    def offsets(self) -> list[int]:
        at, out = 0, []
        for term in self.counts:
            out.append(at)
            at += term.n_params
        return out

    def param_names(self) -> list[str]:
        return [f"{t.name}: {p}" for t in self.counts for p in t.param_names()]

    def bulk_index(self) -> list[int]:
        """Positions of every log b in theta."""
        return [
            at + j
            for term, at in zip(self.counts, self.offsets(), strict=True)
            for j in range(term.k)
        ]

    def tau_index(self) -> list[int]:
        """Positions of every log tau (the bulk's own noise) in theta."""
        return [
            at + term.k + 3
            for term, at in zip(self.counts, self.offsets(), strict=True)
            if term.dist == "lognormal_sum"
        ]

    def bulk_scale(self) -> list[float]:
        """For each log b (as ``bulk_index``), the largest multiplier it gets: n times the
        exposure, so that b times it is the most bulk any font's count can expect."""
        out: list[float] = []
        for term in self.counts:
            e = 1.0 if term.exposure is None else term.exposure[:, None]
            out += [float(v) for v in np.max(term.n2d * e, axis=0, initial=0.0)]
        return out

    def term_logliks(self, theta: np.ndarray, x: np.ndarray) -> list[np.ndarray]:
        """log p(term_j of each font | eta = x), one N_j x Q array per term."""
        return [
            count_loglik(term, tc, x)
            for term, tc in zip(self.counts, self.split(theta), strict=True)
        ]

    def grid_loglik(self, theta: np.ndarray, x: np.ndarray) -> np.ndarray:
        """log p(terms_f | eta = x_q) for every font, N x Q."""
        ll = np.zeros((len(self.ids), len(x)))
        for rows, term_ll in zip(self.rows, self.term_logliks(theta, x), strict=True):
            ll[rows] += term_ll
        return ll


def means(term: CountTerm, tc: np.ndarray, x: Any) -> tuple[np.ndarray, np.ndarray]:
    """The bulk (N x 1) and the popularity part (1 x Q, or N x Q with an exposure), each
    including the exposure."""
    bulk = (term.n2d @ np.exp(tc[: term.k]))[:, None]
    alpha, loglam = tc[term.k], tc[term.k + 1]
    pop = np.exp(alpha + np.exp(loglam) * np.asarray(x))[None, :]
    if term.exposure is not None:
        bulk, pop = bulk * term.exposure[:, None], pop * term.exposure[:, None]
    return bulk, pop


def count_loglik(term: CountTerm, tc: np.ndarray, x: Any) -> np.ndarray:
    """log p(y_f | eta = x_q) for the term's fonts, N x Q."""
    bulk, pop = means(term, tc, x)
    lognoise = tc[term.k + 2]
    if term.dist == "negbin":
        mean = bulk + pop
        k = np.exp(lognoise)
        ll = nb_logpmf(term.y[:, None], mean, k)
        if term.y_hi is not None:
            wide = term.y_hi > term.y
            if wide.any():
                ll[wide] = nb_interval_logp(
                    term.y[wide][:, None], term.y_hi[wide][:, None], mean[wide], k
                )
        return ll
    return _lognormal_ll(term, *log_moments(term, tc, x))


def log_moments(term: CountTerm, tc: np.ndarray, x: Any) -> tuple[np.ndarray, Any]:
    """For a log-normal term: the mean m and sd s of log y given eta = x."""
    bulk, pop = means(term, tc, x)
    lognoise = tc[term.k + 2]
    if term.dist == "lognormal":
        return np.log(bulk + pop), np.exp(lognoise)
    # lognormal_sum: bulk * e^(tau Z1) + popularity * e^(sigma Z2), the sum matched to a
    # log-normal by its mean and variance (Fenton-Wilkinson); b and e^(alpha + lambda eta)
    # are the medians of the two parts. In logs, so that neither part overflows at the
    # grid's edges: with the parts' means mB = B e^(tau^2/2) and mP = P e^(sigma^2/2), and
    # their shares w of M = mB + mP, V / M^2 = wB^2 (e^(tau^2) - 1) + wP^2 (e^(sigma^2) - 1).
    sig2, tau2 = np.exp(2 * lognoise), np.exp(2 * tc[term.k + 3])
    log_mb = np.log(np.maximum(bulk, 1e-300)) + tau2 / 2
    log_mp = np.log(np.maximum(pop, 1e-300)) + sig2 / 2
    log_m = np.logaddexp(log_mb, log_mp)
    wb, wp = np.exp(log_mb - log_m), np.exp(log_mp - log_m)
    s2 = np.log1p(wb**2 * np.expm1(tau2) + wp**2 * np.expm1(sig2))
    return log_m - s2 / 2, np.sqrt(s2)


def cdf(term: CountTerm, tc: np.ndarray, x: Any, v: Any) -> np.ndarray:
    """P(y <= v | eta = x) for each of the term's fonts (``v`` per font), N x Q."""
    v = np.asarray(v, float)[:, None]
    if term.dist == "negbin":
        bulk, pop = means(term, tc, x)
        k = np.exp(tc[term.k + 2])
        p = k / (k + bulk + pop)
        return np.where(v >= 0, special.betainc(k, np.maximum(v, 0) + 1, p), 0.0)
    m, s = log_moments(term, tc, x)
    return np.where(v > 0, special.ndtr((np.log(np.maximum(v, 1e-300)) - m) / s), 0.0)


def simulate(term: CountTerm, tc: np.ndarray, eta: np.ndarray, rng: Any) -> np.ndarray:
    """One draw of the term's counts for its fonts at their ``eta`` (exact sums, not the
    Fenton-Wilkinson match)."""
    bulk, pop = means(term, tc, np.zeros(1))
    bulk = bulk[:, 0]
    pop = pop[:, 0] * np.exp(np.exp(tc[term.k + 1]) * eta)
    noise = np.exp(tc[term.k + 2])
    if term.dist == "negbin":
        return rng.poisson(rng.gamma(noise, (bulk + pop) / noise)).astype(float)
    if term.dist == "lognormal":
        return (bulk + pop) * np.exp(noise * rng.standard_normal(len(pop)))
    tau = np.exp(tc[term.k + 3])
    return bulk * np.exp(tau * rng.standard_normal(len(pop))) + pop * np.exp(
        noise * rng.standard_normal(len(pop))
    )


@dataclass(frozen=True, eq=False)
class MixedFit:
    model: Mixed
    theta: np.ndarray
    loglik: float
    cov: np.ndarray  # zero rows and columns for held parameters
    converged: bool
    free: tuple[int, ...] = ()
    held: tuple[str, ...] = ()  # baselines that reached their lower bound, held there
    at_bound: tuple[str, ...] = ()  # free parameters left on a bound (a failed check)
    grid: tuple[np.ndarray, np.ndarray] = field(default_factory=grid_nodes)

    @property
    def se(self) -> np.ndarray:
        return np.sqrt(np.clip(np.diag(self.cov), 0, None))

    @property
    def cov_free(self) -> np.ndarray:
        f = list(self.free)
        return self.cov[np.ix_(f, f)]

    def problems(self) -> list[str]:
        """What validation must refuse: no convergence, a covariance that isn't positive
        definite over the free parameters, a free parameter left on a bound, or a posterior
        narrower than the grid can integrate. Names parameters, never values."""
        out = [] if self.converged else ["the fit did not converge"]
        c = self.cov_free
        if not np.isfinite(c).all():
            out.append("the covariance is not finite")
        elif len(c) and np.linalg.eigvalsh(c).min() <= 0:
            out.append("the covariance is not positive definite over the free parameters")
        out += [f"{p} is on a bound" for p in self.at_bound]
        x, _ = self.grid
        if self.scores()[1].min() < x[1] - x[0]:
            out.append("a posterior sd is below the grid's step")
        return out

    def draws(self, n: int, seed: int = 0) -> np.ndarray:
        """n draws of theta from N(theta-hat, cov) over the free parameters only (by
        Cholesky), held parameters at their values: n x len(theta)."""
        rng = np.random.default_rng(seed)
        free = list(self.free)
        chol = np.linalg.cholesky(self.cov_free)
        out = np.tile(self.theta, (n, 1))
        out[:, free] += rng.standard_normal((n, len(free))) @ chol.T
        return out

    def posterior(self, theta: np.ndarray | None = None) -> np.ndarray:
        """Each font's posterior weights on the grid, N x G (rows sum to 1)."""
        x, logw = self.grid
        a = self.model.grid_loglik(self.theta if theta is None else theta, x) + logw[None, :]
        return np.exp(a - special.logsumexp(a, axis=1)[:, None])

    def scores(self, theta: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Posterior mean (the score) and sd of eta for each of ``model.ids``, on the grid
        the fit used."""
        x, _ = self.grid
        pi = self.posterior(theta)
        eap = pi @ x
        return eap, np.sqrt(np.maximum(pi @ x**2 - eap**2, 0))

    def quantiles(self, qs: Any, theta: np.ndarray | None = None) -> np.ndarray:
        """Posterior quantiles of eta, N x len(qs), deterministic (q19): each node's weight
        spread evenly over its cell of the grid, the cumulative weight interpolated linearly
        between cell edges, so a posterior narrower than a step still gets quantiles inside
        its cell rather than at a node."""
        x, _ = self.grid
        h = x[1] - x[0]
        edges = np.concatenate([[x[0] - h / 2], x + h / 2])
        cum = np.concatenate(
            [np.zeros((len(self.model.ids), 1)), self.posterior(theta).cumsum(1)], 1
        )
        qs = np.asarray(qs, float)
        return np.array([np.interp(qs, row, edges) for row in cum])

    def term_scores(self) -> list[np.ndarray]:
        """For each term, the posterior mean of eta from that term alone, for its fonts
        (q18b: a model view's per-source z)."""
        x, logw = self.grid
        out = []
        for ll in self.model.term_logliks(self.theta, x):
            a = ll + logw[None, :]
            out.append(np.exp(a - special.logsumexp(a, axis=1)[:, None]) @ x)
        return out

    def info_shares(self) -> np.ndarray:
        """Each term's share of what the data say about a font's eta: the posterior mean of
        its observed information -d2/deta2 log p_j(y_fj | eta) (by second differences on
        the grid, with the term's own intervals), over the sum across the font's terms.
        N x terms, NaN where a font has no term (q16)."""
        x, _ = self.grid
        h = x[1] - x[0]
        pi = self.posterior()[:, 1:-1]
        out = np.full((len(self.model.ids), len(self.model.counts)), np.nan)
        for j, (rows, ll) in enumerate(
            zip(self.model.rows, self.model.term_logliks(self.theta, x), strict=True)
        ):
            d2 = (ll[:, 2:] - 2 * ll[:, 1:-1] + ll[:, :-2]) / h**2
            out[rows, j] = np.maximum((pi[rows] * -d2).sum(1), 0.0)
        return out / np.nansum(out, axis=1)[:, None]

    def rank_draws(
        self,
        ids: Any,
        draws: int = 200,
        seed: int = 0,
        parameters: bool = True,
        measurement: bool = True,
    ) -> np.ndarray:
        """Ranks of ``ids`` (1 = best) in joint posterior draws, draws x len(ids): theta
        from ``draws`` (free block only), then one eta per font from its posterior at that
        theta (or the posterior mean, without ``measurement``)."""
        x, _ = self.grid
        rng = np.random.default_rng(seed + 1)
        ids = np.asarray(list(ids))
        pos = np.searchsorted(self.model.ids, ids)
        if (pos >= len(self.model.ids)).any() or (self.model.ids[pos] != ids).any():
            raise ValueError("rank_draws: some ids have no terms in this fit")
        thetas = self.draws(draws, seed) if parameters else np.tile(self.theta, (draws, 1))
        out = np.empty((draws, len(pos)), dtype=int)
        for r in range(draws):
            post = self.posterior(thetas[r])[pos]
            if measurement:
                u = rng.random(len(pos))
                eta = x[np.minimum((post.cumsum(1) < u[:, None]).sum(1), len(x) - 1)]
                eta = eta + (rng.random(len(pos)) - 0.5) * (x[1] - x[0])
            else:
                eta = post @ x
            out[r] = (-eta).argsort().argsort() + 1
        return out

    def to_record(self) -> dict[str, Any]:
        """The fit as JSON-ready data: what a later run needs to score with it. Floats are
        Python floats, so ``json`` writes each as its shortest round-trip repr and
        ``from_record`` reads back the same bits."""
        if not np.array_equal(self.grid[0], grid_nodes()[0]):
            raise ValueError("fit record: only a fit on the standard grid is stored")
        return {
            "format": RECORD_FORMAT,
            "terms": [
                {"name": t.name, "dist": t.dist, "groups": list(t.groups)}
                for t in self.model.counts
            ],
            "param_names": self.model.param_names(),
            "theta": [float(v) for v in self.theta],
            "free": list(self.free),
            "cov_free": [[float(v) for v in row] for row in self.cov_free],
            "loglik": float(self.loglik),
            "converged": bool(self.converged),
            "held": list(self.held),
            "at_bound": list(self.at_bound),
            "grid": {"lo": GRID_LO, "hi": GRID_HI, "step": GRID_STEP},
        }

    @classmethod
    def from_record(cls, model: Mixed, record: dict[str, Any]) -> MixedFit:
        """A fit read back for ``model``, whose terms must be the record's, in its order."""
        if record.get("format") != RECORD_FORMAT:
            raise ValueError(f"fit record: format {record.get('format')!r}, not {RECORD_FORMAT}")
        terms = [{"name": t.name, "dist": t.dist, "groups": list(t.groups)} for t in model.counts]
        if record["terms"] != terms or record["param_names"] != model.param_names():
            raise ValueError("fit record: its terms are not the model's")
        theta = np.array(record["theta"], float)
        free = tuple(int(i) for i in record["free"])
        cov = np.zeros((len(theta), len(theta)))
        if free:
            cov[np.ix_(free, free)] = np.array(record["cov_free"], float)
        g = record["grid"]
        if (g["lo"], g["hi"], g["step"]) != (GRID_LO, GRID_HI, GRID_STEP):
            raise ValueError("fit record: fitted on another grid")
        return cls(
            model,
            theta,
            float(record["loglik"]),
            cov,
            bool(record["converged"]),
            free,
            tuple(record["held"]),
            tuple(record["at_bound"]),
        )


def _bounds(model: Mixed) -> list[tuple[float | None, float | None]]:
    bounds: list[tuple[float | None, float | None]] = []
    for term in model.counts:
        noise_max = float(np.log(1e3)) if term.dist == "negbin" else LOG_SIGMA_MAX
        bounds += [(LOG_B_MIN, float(np.log(1e15)))] * term.k + [
            (-ALPHA_LIMIT, ALPHA_LIMIT),
            (float(np.log(1e-2)), float(np.log(20))),
            (float(np.log(1e-2)), noise_max),
        ]
        if term.dist == "lognormal_sum":
            bounds.append((TAU_MIN, float(np.log(5.0))))
    return bounds


def _start(model: Mixed) -> np.ndarray:
    parts = []
    for term in model.counts:
        # on the rate scale when the term has an exposure, so the start fits the data
        rate = term.y / (term.exposure if term.exposure is not None else 1.0)
        base = [
            np.log(max(np.median(rate[term.n2d[:, j] > 0]) / 2, 1e-12))
            if (term.n2d[:, j] > 0).any()
            else LOG_B_MIN
            for j in range(term.k)
        ]
        extra = [np.log(0.3)] if term.dist == "lognormal_sum" else []
        parts.append(np.array([*base, np.log(max(np.median(rate), 1e-12)), 0.0, 0.0, *extra]))
    return np.concatenate(parts)


def _negll(model: Mixed, nodes: tuple[np.ndarray, np.ndarray]) -> Any:
    x, logw = nodes

    def negll(theta: np.ndarray) -> float:
        a = model.grid_loglik(theta, x) + logw[None, :]
        return float(-special.logsumexp(a, axis=1).sum())

    return negll


def _hessian_fd(f: Any, theta: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Hessian of scalar ``f`` at ``theta`` by central differences."""
    k = len(theta)
    h = np.empty((k, k))
    e = np.eye(k) * eps
    for i in range(k):
        for j in range(i, k):
            h[i, j] = h[j, i] = (
                f(theta + e[i] + e[j])
                - f(theta + e[i] - e[j])
                - f(theta - e[i] + e[j])
                + f(theta - e[i] - e[j])
            ) / (4 * eps**2)
    return h


def covariance(
    model: Mixed, theta: np.ndarray, fixed: Any, nodes: tuple[np.ndarray, np.ndarray]
) -> tuple[list[int], np.ndarray]:
    """The free parameters, and the inverse observed information over them (zero rows and
    columns for the held ones), by a finite-difference Hessian at ``theta``."""
    negll = _negll(model, nodes)
    free = [i for i in range(len(theta)) if i not in fixed]
    cov = np.zeros((len(theta), len(theta)))

    def sub(th_free: np.ndarray) -> float:
        th = np.array(theta, float)
        th[free] = th_free
        return negll(th)

    try:
        cov[np.ix_(free, free)] = np.linalg.inv(_hessian_fd(sub, np.asarray(theta)[free]))
    except np.linalg.LinAlgError:
        cov[:] = np.nan
    return free, cov


def hold_bulk_noise(fit: MixedFit) -> MixedFit:
    """A bulk whose own noise tau reaches its lower bound is the same for every package:
    hold tau there (the boundary estimate) and recompute the covariance over the rest. Then
    b acts as a threshold, the floor under the scaled counts: it is estimated at the rate of
    a minimum, and its Hessian interval understates its uncertainty."""
    model = fit.model
    names = model.param_names()
    low = [i for i in model.tau_index() if i in fit.free and fit.theta[i] <= TAU_MIN + 1e-6]
    if not low:
        return fit
    fixed = {i: fit.theta[i] for i in range(len(fit.theta)) if i not in fit.free}
    fixed.update({i: fit.theta[i] for i in low})
    free, cov = covariance(model, fit.theta, fixed, fit.grid)
    held = fit.held + tuple(f"{names[i]} (at its lower bound: no noise of its own)" for i in low)
    at_bound = tuple(a for a in fit.at_bound if a not in {names[i] for i in low})
    return replace(fit, cov=cov, free=tuple(free), held=held, at_bound=at_bound)


def fit_mixed(
    model: Mixed,
    start: np.ndarray | None = None,
    fixed: dict[int, float] | None = None,
    nodes: tuple[np.ndarray, np.ndarray] | None = None,
    hold_empty_bulk: bool = True,
) -> MixedFit:
    """Fit by marginal maximum likelihood on the trapezoid grid (``nodes``, default
    ``grid_nodes()``), with L-BFGS-B and finite-difference gradients. ``fixed`` holds
    parameters at given values, {index in theta: value}; their rows and columns of the
    covariance are zero.

    With ``hold_empty_bulk``, a baseline the data can't tell from zero is held at 1e-12
    (``held``), and the fit repeated, in two steps:

    1. a baseline that reaches its lower bound, or under which no font can expect even 0.01
       of a count, sits on (or wanders toward) the edge of the parameter space;
    2. a baseline whose standard error is undefined or above 2 on the log scale (a flat
       likelihood) is held if a likelihood-ratio test can't reject b = 0: twice the loss in
       log-likelihood below 2.706, the 5% point of 1/2 chi2_0 + 1/2 chi2_1 (b = 0 lies on the
       boundary). The Hessian's standard errors mean nothing for either."""
    nodes = grid_nodes() if nodes is None else nodes
    negll = _negll(model, nodes)
    bounds = _bounds(model)
    names = model.param_names()

    def minimize(start: np.ndarray, fixed: dict[int, float]) -> Any:
        start, b = np.array(start, float), list(bounds)
        for i, v in fixed.items():
            start[i] = v
            b[i] = (v, v)
        return optimize.minimize(
            negll, start, method="L-BFGS-B", bounds=b, options={"maxiter": 5000, "ftol": 1e-12}
        )

    fixed = dict(fixed or {})
    held: list[str] = []
    res = minimize(_start(model) if start is None else start, fixed)
    while hold_empty_bulk:
        low = [
            i
            for i, scale in zip(model.bulk_index(), model.bulk_scale(), strict=True)
            if i not in fixed and (res.x[i] <= LOG_B_MIN + 1e-6 or np.exp(res.x[i]) * scale < 0.01)
        ]
        if not low:
            break
        fixed.update(dict.fromkeys(low, LOG_B_MIN))
        held += [names[i] for i in low]
        res = minimize(res.x, fixed)
    free, cov = covariance(model, res.x, fixed, nodes)
    if hold_empty_bulk:
        var = np.diag(cov)
        flat = [
            i
            for i in model.bulk_index()
            if i in free and (not np.isfinite(var[i]) or var[i] <= 0 or var[i] > 4.0)
        ]
        changed = False
        for i in sorted(flat, key=lambda i: -var[i] if np.isfinite(var[i]) else -np.inf):
            trial = {**fixed, i: LOG_B_MIN}
            alt = minimize(res.x, trial)
            if 2 * (alt.fun - res.fun) < LR_BOUNDARY_5PCT:
                fixed, res, changed = trial, alt, True
                held.append(f"{names[i]} (likelihood-ratio test)")
        if changed:
            free, cov = covariance(model, res.x, fixed, nodes)
    at_bound = tuple(
        names[i]
        for i in free
        if any(lim is not None and abs(res.x[i] - lim) < 1e-6 for lim in bounds[i])
    )
    fit = MixedFit(
        model,
        res.x,
        -float(res.fun),
        cov,
        bool(res.success),
        tuple(free),
        tuple(held),
        at_bound,
        nodes,
    )
    return hold_bulk_noise(fit) if hold_empty_bulk else fit


def single_term_posterior(
    term_params: Any, y: Any, n: Any = 1, grid: np.ndarray | None = None, exposure: float = 1.0
) -> tuple[np.ndarray, np.ndarray]:
    """Posterior mean and sd of eta for a font whose only evidence is a negative-binomial
    term's ``y`` counts over ``n`` packages (a number, or packages per baseline group), for
    each y in ``y`` (array), out of ``exposure`` (pkgstats: system-months)."""
    grid = np.linspace(-5, 6, 1101) if grid is None else grid
    n = np.atleast_1d(np.asarray(n, float))
    logb, (alpha, loglam, logk) = term_params[: len(n)], term_params[len(n) :]
    mean = exposure * (n @ np.exp(logb) + np.exp(alpha + np.exp(loglam) * grid))
    ll = nb_logpmf(np.asarray(y, float)[:, None], mean[None, :], np.exp(logk)) - 0.5 * grid**2
    pi = np.exp(ll - special.logsumexp(ll, axis=1)[:, None])
    eap = pi @ grid
    return eap, np.sqrt(np.maximum(pi @ grid**2 - eap**2, 0))


# --- checks on a fitted model -------------------------------------------------------------


def loto_pit(fit: MixedFit, seed: int = 2) -> tuple[list[np.ndarray], list[dict[str, Any]]]:
    """Leave-one-term-out predictive calibration. For each term, each font's eta posterior
    from its other terms predicts the term's count; the randomized PIT u (uniform under a
    correct model; between F(lo-) and F(hi) for counts and intervals) is returned as a normal
    score per font, one array per term (aligned with the term's ``ids``), with a per-term
    summary (the KS p, the scores' sd, the share in each tail): rank statistics only."""
    x, logw = fit.grid
    rng = np.random.default_rng(seed)
    model = fit.model
    per = model.term_logliks(fit.theta, x)
    total = np.zeros((len(model.ids), len(x))) + logw[None, :]
    for rows, ll in zip(model.rows, per, strict=True):
        total[rows] += ll
    zs, summary = [], []
    for term, rows, ll, tc in zip(
        model.counts, model.rows, per, model.split(fit.theta), strict=True
    ):
        a = total[rows] - ll
        post = np.exp(a - special.logsumexp(a, axis=1)[:, None])
        lo, hi = term.y, term.y if term.y_hi is None else term.y_hi
        # a count is the event lo-1 < Y <= hi; a log-normal value is a point unless it is an
        # interval
        below = lo - 1 if term.dist == "negbin" else lo
        f_lo = (post * cdf(term, tc, x, below)).sum(1)
        f_hi = (post * cdf(term, tc, x, hi)).sum(1)
        spread = (term.dist == "negbin") | (hi > lo)
        u = np.where(spread, f_lo + rng.random(len(f_lo)) * (f_hi - f_lo), f_hi)
        u = np.clip(u, 1e-12, 1 - 1e-12)
        z = special.ndtri(u)
        zs.append(z)
        summary.append(
            {
                "term": term.name,
                "fonts": len(u),
                "ks_p": float(stats.kstest(u, "uniform").pvalue),
                "z_sd": float(z.std()),
                "low_tail": float((u < 0.05).mean()),
                "high_tail": float((u > 0.95).mean()),
            }
        )
    return zs, summary


def monotonicity_scan(fit: MixedFit, points: int = 2000) -> list[dict[str, Any]]:
    """Is a font's score monotone in each count? For each term, a font with that term alone
    (the term's median n and exposure) is scored across counts from a twentieth of the
    smallest observed to ten times the largest; any fall in eta-hat as the count rises
    breaks "a higher count never lowers a rank". One entry a term: the falls, the largest
    fall in eta-hat, and how many of the term's fonts have counts at or below the highest
    fall (no count itself: they can be Google's or Fonts Over Time's)."""
    x, logw = fit.grid
    model = fit.model
    out = []
    for term, tc in zip(model.counts, model.split(fit.theta), strict=True):
        pos = term.y[term.y > 0]
        ys = np.geomspace(pos.min() / 20, pos.max() * 10, points)
        n = np.median(term.n2d, axis=0)
        probe = CountTerm(
            "probe",
            np.arange(points),
            ys,
            np.tile(n, (points, 1)) if term.k > 1 else np.full(points, n[0]),
            groups=term.groups,
            exposure=None if term.exposure is None else np.full(points, term.exposure[0]),
            dist=term.dist,
        )
        ll = count_loglik(probe, tc, x) + logw[None, :]
        eap = np.exp(ll - special.logsumexp(ll, axis=1)[:, None]) @ x
        steps = np.diff(eap)
        falls = steps < -1e-9
        top = float(ys[1:][falls].max()) if falls.any() else None
        out.append(
            {
                "term": term.name,
                "dist": term.dist,
                "falls": int(falls.sum()),
                "largest_fall": float(-steps.min()) if falls.any() else 0.0,
                "fonts_that_low": int((term.y <= top).sum()) if top is not None else 0,
            }
        )
    return out
