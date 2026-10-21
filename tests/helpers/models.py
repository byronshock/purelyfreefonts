"""Synthetic model views, for tests marked ``model_config`` that run stage "rank"'s real
model views (``modelviews.run``: the fit modes, ``fitstore``, the staged records) on a
small model in place of a run's Project terms. Every number here is synthetic."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from tff_catalog import modelinputs
from tff_catalog.engine import builders, measure
from tff_catalog.paths import Paths

FONTS = 160
GROUPS = {"ecosystems": "ecosystems", "npm_fontsource": "npm"}


def terms(drop: int = 0) -> tuple[measure.CountTerm, ...]:
    """Two terms on the same fonts, ecosyste.ms dependents and npm, less the first ``drop``
    fonts: a different model, so a different fit."""
    rng = np.random.default_rng(5)
    eta = rng.standard_normal(FONTS)
    ids = np.array([f"f-{i:03d}" for i in range(FONTS)])
    deps = rng.negative_binomial(3.0, 3.0 / (3.0 + 2.0 + np.exp(1.0 + 1.2 * eta))).astype(float)
    npm = np.exp(8.0 + 1.5 * eta + rng.standard_normal(FONTS))
    keep = slice(drop, None)
    n = FONTS - drop
    return (
        measure.CountTerm(builders.ECOSYSTEMS, ids[keep], deps[keep], np.ones(n)),
        measure.CountTerm(
            builders.NPM_FONTSOURCE, ids[keep], npm[keep], np.ones(n), dist="lognormal"
        ),
    )


def frames() -> dict[str, dict[str, dict[str, Any]]]:
    return {
        source: {
            f"f-{i:03d}": {"state": "observed", "group": group, "value": 1.0} for i in range(FONTS)
        }
        for source, group in GROUPS.items()
    }


def stub_model_inputs(
    monkeypatch: pytest.MonkeyPatch, drop: Callable[[Paths], int] = lambda paths: 0
) -> list[int]:
    """Give every model view the synthetic model, less ``drop(paths)`` fonts, and return the
    list that counts the fits made (one entry per ``measure.fit_mixed`` call)."""
    fitted: list[int] = []
    real_fit = measure.fit_mixed

    def project_data(paths: Paths, run_date: object, stale_dropped: object = ()) -> Any:
        return SimpleNamespace(frames=frames(), terms=terms(drop(paths)))

    def fit_mixed(model: measure.Mixed, *args: Any, **kwargs: Any) -> measure.MixedFit:
        fitted.append(len(model.ids))
        return real_fit(model)

    monkeypatch.setattr(modelinputs, "project_data", project_data)
    monkeypatch.setattr(builders, "project_terms", lambda data: list(data.terms))
    monkeypatch.setattr(builders, "model", lambda found, names: (measure.Mixed(tuple(found)), {}))
    monkeypatch.setattr(measure, "fit_mixed", fit_mixed)
    return fitted
