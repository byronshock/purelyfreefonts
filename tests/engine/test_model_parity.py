"""The Project and Developers & apps models match the private reference (tff-stats).

The reference is the private workbench's own build of the ruled models on the 2026-09-26
snapshots, with its fitted parameters and scores (``tff-stats parity``). It holds Google
views and Fonts Over Time counts (rulings T2, T4), so it never enters the repository: point
``TFF_PARITY_DIR`` at it to run these tests, which are skipped otherwise. The port reads the
same stage the reference was built from, so its terms must be the reference's exactly, and
at the reference's parameters its likelihood and scores must agree to rounding.
``TFF_PARITY_REFIT=1`` also refits (about 20 minutes): the parameters within 0.02 standard
errors, the same held baselines, the same top 100.

Failure messages give counts and the largest differences, never a count or a parameter.
"""

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tff_catalog import modelinputs
from tff_catalog.engine import measure
from tff_catalog.paths import Paths

PARITY = os.environ.get("TFF_PARITY_DIR")
pytestmark = [
    pytest.mark.store,
    pytest.mark.skipif(not PARITY, reason="needs TFF_PARITY_DIR (the private parity reference)"),
]
VIEWS = ("project", "dev_apps")


@pytest.fixture(scope="module")
def reference() -> dict[str, Any]:
    root = Path(str(PARITY))
    meta = json.loads((root / "reference.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {"meta": meta}
    for view in VIEWS:
        out[view] = (
            json.loads((root / f"{view}.json").read_text(encoding="utf-8")),
            dict(np.load(root / f"{view}.npz")),
        )
    return out


@pytest.fixture(scope="module")
def ported(reference: dict[str, Any]) -> dict[str, tuple[measure.Mixed, dict[int, float]]]:
    meta = reference["meta"]
    paths = Paths.for_root(Path(meta["stage_root"]))
    return modelinputs.models(paths, date.fromisoformat(meta["date"]))


def _differs(name: str, ours: np.ndarray, theirs: np.ndarray) -> str | None:
    """None if equal; else what differs, without the values."""
    if ours.shape != theirs.shape:
        return f"{name}: shape {ours.shape} != {theirs.shape}"
    bad = ~np.isclose(ours, theirs, rtol=0, atol=0)
    if bad.any():
        rel = np.max(np.abs(ours[bad] - theirs[bad]) / np.maximum(np.abs(theirs[bad]), 1e-300))
        return f"{name}: {int(bad.sum())} of {bad.size} differ (largest relative {rel:.1e})"
    return None


@pytest.mark.parametrize("view", VIEWS)
def test_the_terms_are_the_reference_s(reference, ported, view):
    meta, arrays = reference[view]
    model, fixed = ported[view]
    assert [{"name": t.name, "dist": t.dist, "k": t.k} for t in model.counts] == meta["terms"]
    problems = []
    for j, term in enumerate(model.counts):
        ids = arrays[f"t{j}_ids"]
        if list(term.ids.astype(str)) != list(ids):
            ours, theirs = set(term.ids.astype(str)), set(ids)
            problems.append(
                f"{term.name}: {len(ours - theirs)} fonts extra, {len(theirs - ours)} missing"
            )
            continue
        y_hi = term.y if term.y_hi is None else term.y_hi
        exposure = np.ones(len(term.y)) if term.exposure is None else term.exposure
        for field, ours in (("y", term.y), ("n", term.n2d), ("y_hi", y_hi), ("exposure", exposure)):
            found = _differs(f"{term.name} {field}", ours, arrays[f"t{j}_{field}"])
            if found:
                problems.append(found)
    assert problems == []
    assert sorted(fixed) == sorted(
        at for at, t in zip(model.offsets(), model.counts, strict=True) if t.n2d.sum() == 0
    )


@pytest.mark.parametrize("view", VIEWS)
def test_at_the_reference_parameters_the_scores_agree(reference, ported, view):
    _, arrays = reference[view]
    model, _ = ported[view]
    assert list(model.ids.astype(str)) == list(arrays["ids"])
    theta = arrays["theta"]
    negll = measure._negll(model, measure.grid_nodes())
    d_loglik = abs(-negll(theta) - float(arrays["loglik"][0]))
    assert d_loglik <= 1e-6, f"log-likelihood differs by {d_loglik:.1e}"
    fit = measure.MixedFit(
        model, theta, 0.0, arrays["cov"], True, tuple(int(i) for i in arrays["free"])
    )
    eap, _ = fit.scores()
    d_eap = float(np.max(np.abs(eap - arrays["eap"])))
    assert d_eap <= 1e-10, f"posterior means differ by up to {d_eap:.1e}"


@pytest.mark.slow
@pytest.mark.timeout(3600)  # a full refit on the real terms: minutes, more under load
@pytest.mark.skipif(not os.environ.get("TFF_PARITY_REFIT"), reason="TFF_PARITY_REFIT=1 refits")
@pytest.mark.parametrize("view", VIEWS)
def test_a_refit_lands_on_the_reference(reference, ported, view):
    meta, arrays = reference[view]
    model, fixed = ported[view]
    fit = measure.fit_mixed(model, fixed=fixed)
    assert fit.problems() == []
    assert [h.replace("[packages]", "[casks]") for h in fit.held] == meta["held"]
    free = [int(i) for i in arrays["free"]]
    se = np.sqrt(np.diag(arrays["cov"]))[free]
    worst = float(np.max(np.abs(fit.theta[free] - arrays["theta"][free]) / se))
    assert worst <= 0.02, f"a parameter moved {worst:.3f} standard errors"
    eap, _ = fit.scores()
    top = set(np.argsort(-eap)[:100])
    assert top == set(np.argsort(-arrays["eap"])[:100]), "the top 100 differs"
