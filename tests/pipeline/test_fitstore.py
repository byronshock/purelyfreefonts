"""The measurement model's fit in the private store (tff_catalog.fitstore): written once,
held, read back bit for bit, and never fitted silently for a date nobody keeps it for.
Every number here is synthetic."""

import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from tff_catalog import fitstore, jsonio
from tff_catalog.engine import measure

OCT, NOV, SEP = date(2026, 10, 17), date(2026, 11, 3), date(2026, 9, 26)
VERSION = "2026-10-19"


def make_model(dist: str = "negbin", seed: int = 1) -> measure.Mixed:
    rng = np.random.default_rng(seed)
    n = 400
    eta = rng.standard_normal(n)
    y = rng.negative_binomial(3.0, 3.0 / (3.0 + 20.0 + np.exp(2.0 + 1.5 * eta))).astype(float)
    if dist != "negbin":
        y = y + 1.0
    return measure.Mixed((measure.CountTerm("installs", np.arange(n), y, np.ones(n), dist=dist),))


MODEL = make_model()


class Fitter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, model: measure.Mixed) -> measure.MixedFit:
        self.calls += 1
        return measure.fit_mixed(model)


def obtain(store: Path | None, mode: str, run_date: date, merged=(), model=MODEL, fit=None):
    """``fitstore.obtain``; a pending fit (--write-fit) is staged and kept at once, as a
    passing run's refresh keeps it (``store_pending``)."""
    got = fitstore.obtain(
        mode=mode,
        store_root=store,
        view="project",
        run_date=run_date,
        merged_dates=merged,
        model=model,
        method_version=VERSION,
        fit=fit or Fitter(),
    )
    if got.source == "pending" and store is not None:
        stage = store / "stage"
        fitstore.stage_pending(stage, "project", got.data)
        fitstore.store_pending(stage, store, run_date)
    return got


def test_the_github_run_writes_the_fit_once_and_later_runs_read_it(tmp_path: Path) -> None:
    fitter = Fitter()
    first = obtain(tmp_path, "write", OCT, fit=fitter)
    path = fitstore.fit_path(tmp_path, "project", OCT)
    assert path == tmp_path / "_state" / "fit" / "project" / "2026-10-17.json"
    assert (first.source, first.fitted_on, fitter.calls) == ("pending", OCT, 1)
    doc = jsonio.load(path)
    assert doc["view"] == "project"
    assert doc["spec_sha256"] == fitstore.spec_sha256(MODEL, VERSION)
    # The job's own replay, and November's run, read it back: the same bits, no new fit.
    for day, mode in ((OCT, "read"), (NOV, "write"), (NOV, "read")):
        again = obtain(tmp_path, mode, day, fit=fitter)
        assert (again.source, again.fitted_on, again.sha256) == ("store", OCT, first.sha256)
        assert again.fit.theta.tobytes() == first.fit.theta.tobytes()
        assert again.fit.scores()[0].tobytes() == first.fit.scores()[0].tobytes()
    assert fitter.calls == 1
    assert sorted(p.name for p in path.parent.iterdir()) == ["2026-10-17.json"]


def test_a_run_uses_the_newest_fit_on_or_before_its_date(tmp_path: Path) -> None:
    obtain(tmp_path, "write", OCT)
    later = date(2027, 10, 3)
    obtain(tmp_path, "write", later, model=MODEL)  # no: OCT's is in use for that date
    assert fitstore.stored_dates(tmp_path, "project") == [OCT]
    doc = jsonio.load(fitstore.fit_path(tmp_path, "project", OCT))
    later_path = fitstore.fit_path(tmp_path, "project", later)
    fitstore.write_once(later_path, jsonio.canonical_bytes({**doc, "fitted_on": "2027-10-03"}))
    assert fitstore.in_use(tmp_path, "project", NOV) == OCT
    assert fitstore.in_use(tmp_path, "project", later) == later  # a re-estimate, once ruled
    assert obtain(tmp_path, "read", later).fitted_on == later
    assert fitstore.in_use(tmp_path, "project", SEP) is None
    # A file whose contents disagree with its name is refused.
    misnamed = fitstore.fit_path(tmp_path, "project", date(2027, 12, 1))
    fitstore.write_once(misnamed, jsonio.canonical_bytes(doc))
    with pytest.raises(fitstore.FitError, match="not fitted on 2027-12-01"):
        obtain(tmp_path, "read", date(2027, 12, 2))


def test_a_date_nobody_keeps_a_fit_for_stops_without_the_flags(tmp_path: Path) -> None:
    fitter = Fitter()
    with pytest.raises(fitstore.FitError, match="no stored fit on or before 2026-10-17"):
        obtain(tmp_path, "read", OCT, fit=fitter)
    with pytest.raises(fitstore.FitError, match="no stored fit"):
        obtain(None, "read", OCT, fit=fitter)
    with pytest.raises(fitstore.FitError, match="needs TFF_STORE"):
        obtain(None, "write", OCT, fit=fitter)
    assert fitter.calls == 0


def test_a_dry_run_and_a_merged_date_fit_in_memory_and_keep_nothing(tmp_path: Path) -> None:
    dry = obtain(tmp_path, "memory", OCT)
    merged = obtain(tmp_path, "write", SEP, merged=[SEP])  # 2026-09-26 has no fit, ever
    plain = obtain(None, "read", SEP, merged=[SEP])
    assert (dry.source, merged.source, plain.source) == ("memory", "memory", "memory")
    assert (dry.data, merged.data, plain.data) == (None, None, None)
    assert not (tmp_path / "_state").exists()
    # In memory too, the fit is used as read back, so the dry run scores as the stored would.
    kept = obtain(tmp_path, "write", OCT)
    assert kept.sha256 == dry.sha256
    assert kept.fit.theta.tobytes() == dry.fit.theta.tobytes()


def test_a_stored_fit_is_never_replaced(tmp_path: Path) -> None:
    obtain(tmp_path, "write", OCT)
    path = fitstore.fit_path(tmp_path, "project", OCT)
    data = path.read_bytes()
    fitstore.write_once(path, data)  # the same bytes: fine
    with pytest.raises(fitstore.FitError, match="never replaced"):
        fitstore.write_once(path, data.replace(b'"view"', b'"view" ', 1))
    assert path.read_bytes() == data


def test_a_fit_is_read_only_into_the_model_it_was_made_for(tmp_path: Path) -> None:
    obtain(tmp_path, "write", OCT)
    other = make_model("lognormal")
    with pytest.raises(fitstore.FitError, match="another model of project"):
        obtain(tmp_path, "read", NOV, model=other)
    with pytest.raises(fitstore.FitError, match="another model"):
        fitstore.obtain(
            mode="read",
            store_root=tmp_path,
            view="project",
            run_date=NOV,
            merged_dates=(),
            model=MODEL,
            method_version="2027-01-01",
            fit=Fitter(),
        )
    path = fitstore.fit_path(tmp_path, "project", OCT)
    for broken, message in ((b"{", "not JSON"), (b'{"schema": 9}', "not a fit of project")):
        bad = tmp_path / "bad" / "_state" / "fit" / "project" / "2026-10-17.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(broken)
        with pytest.raises(fitstore.FitError, match=message):
            obtain(tmp_path / "bad", "read", NOV)
    assert path.is_file()
    (tmp_path / "_state" / "fit" / "project" / "latest.json").write_text("{}")
    with pytest.raises(fitstore.FitError, match="its name must be a date"):
        fitstore.stored_dates(tmp_path, "project")


def test_a_failed_fit_is_never_kept(tmp_path: Path) -> None:
    def failing(model: measure.Mixed) -> measure.MixedFit:
        fit = measure.fit_mixed(model)
        return measure.MixedFit(model, fit.theta, fit.loglik, fit.cov, False, fit.free)

    with pytest.raises(fitstore.FitError, match="the fit did not converge"):
        obtain(tmp_path, "write", OCT, fit=failing)
    assert not (tmp_path / "_state").exists()


def test_messages_name_files_and_dates_never_values(tmp_path: Path) -> None:
    first = obtain(tmp_path, "write", OCT)
    values = {f"{v:.3g}" for v in first.fit.theta} | {repr(float(v)) for v in first.fit.theta}
    messages = []
    for call in (
        lambda: obtain(tmp_path, "read", NOV, model=make_model("lognormal")),
        lambda: obtain(tmp_path / "none", "read", OCT),
    ):
        with pytest.raises(fitstore.FitError) as caught:
            call()
        messages.append(str(caught.value))
    for message in messages:
        numbers = set(re.findall(r"-?\d+\.\d+(?:e-?\d+)?", message))
        assert not numbers & values, message


def test_the_record_holds_the_spec_and_nothing_else(tmp_path: Path) -> None:
    obtain(tmp_path, "write", OCT)
    doc = json.loads(fitstore.fit_path(tmp_path, "project", OCT).read_bytes())
    assert set(doc) == {"schema", "view", "fitted_on", "spec_sha256", "fit"}
    assert doc["fitted_on"] == "2026-10-17"
    assert fitstore.spec(MODEL, VERSION)["terms"] == [
        {"name": "installs", "dist": "negbin", "groups": ["packages"]}
    ]


def test_a_pending_fit_is_kept_only_when_the_run_stores_it(tmp_path: Path) -> None:
    """--write-fit stages the fit; refresh keeps it only once the run has passed, and a rank
    rerun after "verify" replaces what the first pass staged."""
    store, stage = tmp_path / "store", tmp_path / "stage"
    first = fitstore.obtain(
        mode="write",
        store_root=store,
        view="project",
        run_date=OCT,
        merged_dates=(),
        model=MODEL,
        method_version=VERSION,
        fit=Fitter(),
    )
    assert first.source == "pending"
    assert not (store / "_state").exists()  # nothing kept by the stage itself
    fitstore.stage_pending(stage, "project", b"first pass")
    fitstore.stage_pending(stage, "project", first.data)  # the rerun's replaces it
    assert fitstore.store_pending(stage, store, OCT) == [fitstore.fit_path(store, "project", OCT)]
    assert fitstore.fit_path(store, "project", OCT).read_bytes() == first.data
    with pytest.raises(fitstore.FitError, match="not this run's fit"):
        fitstore.store_pending(stage, store, NOV)
    assert fitstore.store_pending(tmp_path / "no-stage", store, OCT) == []


def test_obtain_refuses_an_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fit mode"):
        obtain(tmp_path, "sometimes", OCT)
