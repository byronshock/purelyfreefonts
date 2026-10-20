"""The measurement model (engine/measure.py) recovers known parameters from simulated data,
and its checks find nothing in data simulated from the model.

Ported from the private workbench tff-stats (tests/test_counts.py and tests/test_checks.py),
less the tests of its censored-normal terms, which the port leaves out. Every number here is
synthetic.
"""

import json
from dataclasses import replace

import numpy as np
import pytest
from scipy import special, stats

from tff_catalog.engine import measure


def param(fit: measure.MixedFit, name: str) -> float:
    """A parameter on its natural scale ("npm: b[packages]" is exp of "npm: log b[...]")."""
    names = fit.model.param_names()
    if name in names:
        return float(fit.theta[names.index(name)])
    term, _, rest = name.partition(": ")
    return float(np.exp(fit.theta[names.index(f"{term}: log {rest}")]))


def test_nb_logpmf_matches_scipy():
    y, mean, k = np.array([0, 3, 40, 900]), 25.0, 2.5
    ref = stats.nbinom.logpmf(y, k, k / (k + mean))
    assert np.allclose(measure.nb_logpmf(y, mean, k), ref)


def test_nb_logpmf_keeps_precision_for_huge_counts():
    y, mean, k = np.array([3e9, 1e12]), np.array([2.5e9, 1.1e12]), 0.8
    ref = np.array([-22.966595399759629, -28.765116506496338])  # mpmath, 50 digits
    assert np.allclose(measure.nb_logpmf(y, mean, k), ref, rtol=1e-12)


def test_nb_interval_matches_scipy():
    lo, hi, mean, k = np.array([0, 5, 16]), np.array([15, 9, 40]), 12.0, 2.0
    p = k / (k + mean)
    ref = np.log(stats.nbinom.cdf(hi, k, p) - stats.nbinom.cdf(lo - 1, k, p))
    assert np.allclose(measure.nb_interval_logp(lo, hi, mean, k), ref)


def test_log_ndtr_diff_in_both_tails():
    hi, lo = np.array([-30.0, 0.5, 40.0, 9.0]), np.array([-31.0, -0.5, 39.0, 8.0])
    upper = stats.norm.logsf(lo) + np.log1p(-np.exp(stats.norm.logsf(hi) - stats.norm.logsf(lo)))
    lower = stats.norm.logcdf(hi) + np.log1p(-np.exp(stats.norm.logcdf(lo) - stats.norm.logcdf(hi)))
    ref = np.where(lo > 0, upper, lower)
    assert np.allclose(measure.log_ndtr_diff(hi, lo), ref, rtol=1e-9)


def test_lognormal_interval_matches_normal_cdf():
    """A log-normal term with [lo, hi]: the probability of the interval."""
    x = np.array([0.0])
    term = measure.CountTerm(
        "list",
        np.arange(2),
        np.array([0.0, 50.0]),
        np.ones(2),
        y_hi=np.array([100.0, 200.0]),
        dist="lognormal",
    )
    tc = np.array([np.log(1e-9), np.log(80.0), 0.0, np.log(0.5)])  # tiny baseline, mean 80
    ll = measure.count_loglik(term, tc, x)[:, 0]
    sd = 0.5
    ref0 = np.log(stats.norm.cdf((np.log(100) - np.log(80)) / sd))
    ref1 = np.log(
        stats.norm.cdf((np.log(200) - np.log(80)) / sd)
        - stats.norm.cdf((np.log(50) - np.log(80)) / sd)
    )
    assert np.allclose(ll, [ref0, ref1], atol=1e-6)


def test_lognormal_sum_is_finite_at_the_grid_edges():
    """No overflow to NaN where the popularity part is huge or the bulk is nil."""
    y = np.array([1.0, 1e3, 1e9])
    term = measure.CountTerm("t", np.arange(3), y, np.array([1.0, 0.0, 1.0]), dist="lognormal_sum")
    x, _ = measure.grid_nodes()
    tc = np.array([np.log(1e3), 5.0, np.log(20.0), np.log(1.0), np.log(0.2)])
    assert np.isfinite(measure.count_loglik(term, tc, x)).all()


def test_negbin_is_finite_at_the_grid_edges():
    y = np.array([0.0, 7.0, 3e9])
    term = measure.CountTerm("t", np.arange(3), y, np.array([1.0, 0.0, 2.0]))
    x, _ = measure.grid_nodes()
    tc = np.array([np.log(25.0), 3.0, np.log(3.0), np.log(1e3)])  # the steepest slope
    assert np.isfinite(measure.count_loglik(term, tc, x)).all()


def test_every_parameter_is_bounded_and_the_likelihood_finite_at_every_corner():
    """The optimizer's box: no unbounded parameter, and every term's log-likelihood finite at
    each corner of its own box, at every grid node (an unbounded alpha once took a fit to NaN)."""
    import itertools

    x, _ = measure.grid_nodes()
    y = np.array([0.0, 1.0, 7.0, 1e3, 3e9])
    n2d = np.array([1.0, 0.0, 2.0, 1.0, 0.5])
    terms = (
        measure.CountTerm("nb", np.arange(5), y, n2d, y_hi=y * 2),
        measure.CountTerm("ln", np.arange(5), y, n2d, y_hi=y * 2, dist="lognormal"),
        measure.CountTerm("sum", np.arange(5), y, n2d, dist="lognormal_sum"),
        measure.CountTerm("fot", np.arange(5), y, np.zeros(5), exposure=np.full(5, 1e4)),
    )
    model = measure.Mixed(terms)
    bounds = measure._bounds(model)
    assert all(lo is not None and hi is not None for lo, hi in bounds)
    for term, (start, stop) in zip(
        terms, itertools.pairwise([*model.offsets(), len(bounds)]), strict=True
    ):
        box = bounds[start:stop]
        for corner in itertools.product(*box):
            ll = measure.count_loglik(term, np.array(corner), x)
            assert np.isfinite(ll).all(), (term.name, corner)


def test_grid_matches_a_dense_integral():
    """The trapezoid grid integrates sharp posteriors far from 0."""
    rng = np.random.default_rng(3)
    n = 400
    eta = rng.standard_normal(n) * 1.5
    mean = 20.0 + np.exp(3.0 + 3.0 * eta)
    y = rng.negative_binomial(10.0, 10.0 / (10.0 + mean)).astype(float)
    model = measure.Mixed((measure.CountTerm("brew", np.arange(n), y, np.ones(n)),))
    theta = np.array([np.log(20.0), 3.0, np.log(3.0), np.log(10.0)])

    def marginal(x, logw):
        return special.logsumexp(model.grid_loglik(theta, x) + logw[None, :], axis=1)

    dense = np.linspace(-8, 8, 16001)
    exact = marginal(dense, np.log(stats.norm.pdf(dense) * (dense[1] - dense[0])))
    assert np.abs(marginal(*measure.grid_nodes()) - exact).max() < 1e-6


def simulate_two_terms(n=3000, seed=0, b=25.0, alpha=3.0, lam=1.6, k=3.0):
    """A negative-binomial term with a baseline, and a second term on part of the fonts."""
    rng = np.random.default_rng(seed)
    eta = rng.standard_normal(n)
    mean = b + np.exp(alpha + lam * eta)
    y = rng.negative_binomial(k, k / (k + mean)).astype(float)
    ids = np.arange(n)
    seen = rng.random(n) < 0.4
    other = np.exp(4.0 + 1.2 * eta[seen] + 0.5 * rng.standard_normal(seen.sum()))
    terms = (
        measure.CountTerm("brew", ids, y, np.ones(n)),
        measure.CountTerm("other", ids[seen], other, np.zeros(seen.sum()), dist="lognormal"),
    )
    return measure.Mixed(terms), eta


@pytest.fixture(scope="module")
def two_terms():
    model, eta = simulate_two_terms()
    return measure.fit_mixed(model), eta


def test_count_term_recovers_its_parameters(two_terms):
    fit, eta = two_terms
    assert fit.converged
    assert fit.problems() == []
    assert param(fit, "brew: b[packages]") == pytest.approx(25.0, rel=0.25)
    assert param(fit, "brew: lambda") == pytest.approx(1.6, rel=0.15)
    assert param(fit, "brew: alpha") == pytest.approx(3.0, abs=0.3)
    # Scores are as good as the data allow: as close to eta as scores from the true count
    # parameters (near the baseline a count says little, so neither is close to 1).
    eap, _ = fit.scores()
    truth = fit.theta.copy()
    truth[:4] = [np.log(25.0), 3.0, np.log(1.6), np.log(3.0)]
    oracle, _ = fit.scores(truth)
    e = eta[fit.model.ids.astype(int)]
    assert np.corrcoef(eap, e)[0, 1] == pytest.approx(np.corrcoef(oracle, e)[0, 1], abs=0.01)


def test_the_fit_is_a_stationary_point(two_terms):
    """No analytic gradients: L-BFGS-B's finite differences must still land where the
    likelihood is flat in every free parameter (the gradient's fallback, docs/milestone-
    method.md step 6)."""
    fit, _ = two_terms
    negll = measure._negll(fit.model, fit.grid)
    eps = 1e-5
    for i in fit.free:
        e = np.zeros(len(fit.theta))
        e[i] = eps
        grad = (negll(fit.theta + e) - negll(fit.theta - e)) / (2 * eps)
        assert abs(grad) < 0.05, fit.model.param_names()[i]


def test_a_stored_fit_scores_bit_for_bit(two_terms):
    """The fitted parameters go to the private store as JSON and later runs score with what
    they read back (scoring plan §8.3, §8.4): the same bits, so the same scores."""
    fit, _ = two_terms
    text = json.dumps(fit.to_record(), sort_keys=True)
    back = measure.MixedFit.from_record(fit.model, json.loads(text))
    assert back.theta.tobytes() == fit.theta.tobytes()
    assert back.cov.tobytes() == fit.cov.tobytes()
    assert (back.free, back.held, back.at_bound) == (fit.free, fit.held, fit.at_bound)
    for a, b in zip(back.scores(), fit.scores(), strict=True):
        assert a.tobytes() == b.tobytes()
    assert json.dumps(back.to_record(), sort_keys=True) == text


def test_a_stored_fit_must_match_its_model(two_terms):
    fit, _ = two_terms
    record = fit.to_record()
    other = measure.Mixed(fit.model.counts[:1])
    with pytest.raises(ValueError, match="not the model's"):
        measure.MixedFit.from_record(other, record)
    with pytest.raises(ValueError, match="format"):
        measure.MixedFit.from_record(fit.model, {**record, "format": 0})
    grid = {**record["grid"], "step": 0.1}
    with pytest.raises(ValueError, match="another grid"):
        measure.MixedFit.from_record(fit.model, {**record, "grid": grid})
    coarse = replace(fit, grid=measure.grid_nodes(step=0.1))
    with pytest.raises(ValueError, match="standard grid"):
        coarse.to_record()


def test_quantiles_bracket_the_score(two_terms):
    """Deterministic posterior quantiles (q19): ordered, around the posterior mean."""
    fit, _ = two_terms
    q = fit.quantiles([0.05, 0.5, 0.95])
    eap, psd = fit.scores()
    assert (np.diff(q, axis=1) > 0).all()
    assert (q[:, 0] < eap).all()
    assert (eap < q[:, 2]).all()
    assert np.median(np.abs(q[:, 1] - eap) / psd) < 0.2
    assert fit.quantiles([0.5]).tobytes() == fit.quantiles([0.5]).tobytes()


def test_quantiles_of_a_posterior_narrower_than_a_step():
    """Each node's weight spreads over its cell, so even a posterior far narrower than the
    grid's step gets quantiles on either side of its mean."""
    ids = np.arange(3)
    term = measure.CountTerm("t", ids, np.array([10.0, 1e3, 1e5]), np.zeros(3), dist="lognormal")
    model = measure.Mixed((term,))
    theta = np.array([measure.LOG_B_MIN, 2.0, np.log(3.0), np.log(0.01)])  # sigma 0.01
    fit = measure.MixedFit(model, theta, 0.0, np.zeros((4, 4)), True, (1, 2, 3))
    eap, sd = fit.scores()
    assert (sd < measure.GRID_STEP).all()
    q = fit.quantiles([0.05, 0.95])
    assert (q[:, 0] < eap).all()
    assert (eap < q[:, 1]).all()
    assert (q[:, 1] - q[:, 0] < measure.GRID_STEP).all()


def test_term_scores_are_each_terms_own_posterior_mean(two_terms):
    """q18b: a term's z is the posterior mean of eta from that term alone; a font with one
    term has that as its score."""
    fit, _ = two_terms
    eap, _ = fit.scores()
    brew, other = fit.term_scores()
    rows_other = fit.model.rows[1]
    only_brew = np.setdiff1d(np.arange(len(fit.model.ids)), rows_other)
    assert np.allclose(brew[only_brew], eap[only_brew], atol=1e-12)
    assert len(other) == len(fit.model.counts[1].ids)
    assert not np.allclose(other, eap[rows_other])


def test_low_counts_carry_little_information():
    params = np.array([np.log(25.0), 3.0, np.log(1.6), np.log(3.0)])
    y = np.array([0, 20, 30, 60, 200, 2000, 20000])
    _, psd = measure.single_term_posterior(params, y)
    # near the baseline the posterior stays wide; far above it, it is narrow
    assert psd[1] > 0.6
    assert psd[-1] < 0.3
    # and the information grows smoothly with the count: no step
    assert np.all(np.diff(psd[1:]) < 0.05)


def test_two_baselines_share_one_curve():
    """Plain and Nerd packages: separate baselines, one popularity curve."""
    rng = np.random.default_rng(7)
    n = 4000
    eta = rng.standard_normal(n)
    nerd = rng.random(n) < 0.15
    packages = np.column_stack([np.ones(n), nerd.astype(float)])
    mean = packages @ np.array([20.0, 2000.0]) + np.exp(2.0 + 2.5 * eta)
    y = rng.negative_binomial(5.0, 5.0 / (5.0 + mean)).astype(float)
    term = measure.CountTerm("brew", np.arange(n), y, packages, groups=("plain", "nerd"))
    fit = measure.fit_mixed(measure.Mixed((term,)))
    assert param(fit, "brew: b[plain]") == pytest.approx(20.0, rel=0.25)
    assert param(fit, "brew: b[nerd]") == pytest.approx(2000.0, rel=0.25)
    assert param(fit, "brew: lambda") == pytest.approx(2.5, rel=0.1)


@pytest.mark.slow
def test_interval_censored_counts_with_exposure():
    """Counts hidden below a reporting threshold, over unequal exposures, still recover the
    curve when the hidden part is given as an interval."""
    rng = np.random.default_rng(11)
    n = 3000
    eta = rng.standard_normal(n)
    exposure = rng.uniform(1e4, 3e4, n)
    rate = 2e-4 + np.exp(-7.0 + 1.5 * eta)
    y = rng.negative_binomial(4.0, 4.0 / (4.0 + exposure * rate)).astype(float)
    hidden = y < 16
    lo = np.where(hidden, 0.0, y)
    hi = np.where(hidden, 15.0, y)
    term = measure.CountTerm("pkg", np.arange(n), lo, np.ones(n), y_hi=hi, exposure=exposure)
    fit = measure.fit_mixed(measure.Mixed((term,)))
    assert param(fit, "pkg: lambda") == pytest.approx(1.5, rel=0.15)
    assert param(fit, "pkg: alpha") == pytest.approx(-7.0, abs=0.4)


def test_lognormal_term_with_baseline():
    """Huge counts: a log-normal term with a baseline recovers the curve."""
    rng = np.random.default_rng(5)
    n = 3000
    eta = rng.standard_normal(n)
    # the baseline is comparable to a typical font's own counts, so it can be identified
    mean = 1e6 + np.exp(13.5 + 1.5 * eta)
    y = mean * np.exp(rng.standard_normal(n) * 0.3)
    term = measure.CountTerm("views", np.arange(n), y, np.ones(n), dist="lognormal")
    fit = measure.fit_mixed(measure.Mixed((term,)))
    assert fit.converged
    assert param(fit, "views: lambda") == pytest.approx(1.5, rel=0.1)
    assert param(fit, "views: b[packages]") == pytest.approx(1e6, rel=0.3)


def test_empty_bulk_is_held_and_draws_skip_it():
    """A term with no bulk drives log b to its bound: the fit holds it there, the covariance
    over the free block is positive definite, and draws leave it alone."""
    rng = np.random.default_rng(4)
    n = 2500
    eta = rng.standard_normal(n)
    mean = np.exp(0.5 + 1.5 * eta)
    y = rng.negative_binomial(1.0, 1.0 / (1.0 + mean)).astype(float)
    term = measure.CountTerm("dependents", np.arange(n), y, np.ones(n))
    fit = measure.fit_mixed(measure.Mixed((term,)))
    assert fit.held == ("dependents: log b[packages]",)
    assert fit.problems() == []
    d = fit.draws(500, seed=1)
    assert np.all(d[:, 0] == measure.LOG_B_MIN)
    emp = np.cov(d[:, list(fit.free)].T)
    assert np.allclose(emp, fit.cov_free, rtol=0.2, atol=1e-4)


@pytest.mark.slow
def test_lognormal_sum_recovers_bulk_and_popularity_noise():
    """Downloads = a tight bulk + a noisy popularity part: the sum model recovers both. Two
    more terms on the same fonts let the term's own noise be told from eta's spread."""
    rng = np.random.default_rng(6)
    n = 4000
    eta = rng.standard_normal(n)
    y = 1700.0 * np.exp(0.15 * rng.standard_normal(n)) + np.exp(
        7.0 + 2.5 * eta + 0.6 * rng.standard_normal(n)
    )
    ids = np.arange(n)
    others = [
        measure.CountTerm(
            name,
            ids,
            rng.negative_binomial(3.0, 3.0 / (3.0 + np.exp(1.0 + 1.2 * eta))).astype(float),
            np.zeros(n),
        )
        for name in ("c1", "c2")
    ]
    term = measure.CountTerm("npm", ids, y, np.ones(n), dist="lognormal_sum")
    fit = measure.fit_mixed(measure.Mixed((term, *others)))
    assert fit.problems() == []
    assert param(fit, "npm: b[packages]") == pytest.approx(1700.0, rel=0.1)
    assert param(fit, "npm: lambda") == pytest.approx(2.5, rel=0.1)
    assert param(fit, "npm: sigma") == pytest.approx(0.6, rel=0.2)
    assert param(fit, "npm: tau") == pytest.approx(0.15, rel=0.35)


def test_info_shares_split_one_fonts_information():
    rng = np.random.default_rng(8)
    n = 1500
    eta = rng.standard_normal(n)
    y1 = rng.negative_binomial(5.0, 5.0 / (5.0 + np.exp(2.0 + 1.0 * eta))).astype(float)
    y2 = np.exp(5.0 + 2.0 * eta + 0.5 * rng.standard_normal(n))
    only = np.arange(n) < 300  # these fonts have the first term only
    t1 = measure.CountTerm("a", np.arange(n), y1, np.zeros(n))
    t2 = measure.CountTerm("b", np.arange(n)[~only], y2[~only], np.zeros(n - 300), dist="lognormal")
    fit = measure.fit_mixed(measure.Mixed((t1, t2)))
    s = fit.info_shares()
    assert np.allclose(np.nansum(s, axis=1), 1.0)
    assert np.allclose(s[:300, 0], 1.0)
    assert np.isnan(s[:300, 1]).all()
    # the precise log-normal term carries most of what both say
    assert np.median(s[300:, 1]) > 0.7


def test_terms_and_models_refuse_bad_shapes():
    with pytest.raises(ValueError, match="dist"):
        measure.CountTerm("t", np.arange(2), np.ones(2), np.ones(2), dist="poisson")
    with pytest.raises(ValueError, match="group"):
        measure.CountTerm("t", np.arange(2), np.ones(2), np.ones((2, 2)))
    with pytest.raises(ValueError, match="twice"):
        measure.CountTerm("t", np.array([1, 1]), np.ones(2), np.ones(2))
    term = measure.CountTerm("t", np.arange(2), np.ones(2), np.ones(2))
    with pytest.raises(ValueError, match="each named once"):
        measure.Mixed((term, term))


def test_fonts_align_on_the_union_of_their_terms():
    a = measure.CountTerm("a", np.array(["inter", "roboto"]), np.ones(2), np.ones(2))
    b = measure.CountTerm("b", np.array(["lato", "roboto"]), np.ones(2), np.ones(2))
    model = measure.Mixed((a, b))
    assert model.ids.tolist() == ["inter", "lato", "roboto"]
    assert [r.tolist() for r in model.rows] == [[0, 2], [1, 2]]


# --- the checks ---------------------------------------------------------------------------


@pytest.mark.slow
def test_pit_finds_nothing_in_model_data():
    rng = np.random.default_rng(12)
    n = 2500
    eta = rng.standard_normal(n)
    ids = np.arange(n)
    nb = rng.negative_binomial(2.0, 2.0 / (2.0 + 20.0 + np.exp(2.0 + 1.5 * eta))).astype(float)
    traffic = 1500.0 * np.exp(0.2 * rng.standard_normal(n)) + np.exp(
        6.0 + 2.0 * eta + 0.7 * rng.standard_normal(n)
    )
    listed = np.exp(4.0 + 1.8 * eta + 0.8 * rng.standard_normal(n))
    cut = np.quantile(listed, 0.8)
    lo = np.where(listed >= cut, listed, 0.0)
    hi = np.where(listed >= cut, listed, cut)
    terms = (
        measure.CountTerm("installs", ids, nb, np.ones(n)),
        measure.CountTerm("downloads", ids, traffic, np.ones(n), dist="lognormal_sum"),
        measure.CountTerm("list", ids, lo, np.zeros(n), y_hi=hi, dist="lognormal"),
    )
    model = measure.Mixed(terms)
    fit = measure.fit_mixed(model, fixed={model.offsets()[2]: measure.LOG_B_MIN})
    zs, summary = measure.loto_pit(fit)
    assert [s["term"] for s in summary] == ["installs", "downloads", "list"]
    assert all(s["ks_p"] > 0.01 for s in summary), summary
    assert [len(z) for z in zs] == [n, n, n]


def test_monotone_families_never_fall():
    """A negative binomial and a one-noise log-normal score a higher count higher."""
    rng = np.random.default_rng(13)
    n = 2000
    eta = rng.standard_normal(n)
    ids = np.arange(n)
    nb = rng.negative_binomial(3.0, 3.0 / (3.0 + 20.0 + np.exp(2.0 + 1.5 * eta))).astype(float)
    ln = 1000.0 + np.exp(6.0 + 2.0 * eta + 0.6 * rng.standard_normal(n))
    terms = (
        measure.CountTerm("installs", ids, nb, np.ones(n)),
        measure.CountTerm("downloads", ids, ln, np.ones(n), dist="lognormal"),
    )
    fit = measure.fit_mixed(measure.Mixed(terms))
    assert [m["falls"] for m in measure.monotonicity_scan(fit)] == [0, 0]


def test_monotonicity_catches_a_family_that_falls():
    """Negative control: the bulk-plus-popularity (Fenton-Wilkinson) family with a tight
    bulk and a wide popularity noise scores a count far below the bulk above one at it."""
    n = 50
    ids = np.arange(n)
    y = np.geomspace(500.0, 1e6, n)
    term = measure.CountTerm("downloads", ids, y, np.ones(n), dist="lognormal_sum")
    model = measure.Mixed((term,))
    theta = np.array([np.log(2000.0), 6.0, np.log(2.0), np.log(1.2), np.log(0.01)])
    fit = measure.MixedFit(model, theta, 0.0, np.zeros((5, 5)), True, tuple(range(5)))
    (m,) = measure.monotonicity_scan(fit)
    assert m["falls"] > 0
    assert m["largest_fall"] > 0
    assert 0 < m["fonts_that_low"] < n
    assert set(m) == {"term", "dist", "falls", "largest_fall", "fonts_that_low"}  # no count
