"""The measurement model's fitted parameters, kept in the private store.

The parameters of the Projects and Developers & apps models derive from Google Fonts and
Fonts Over Time values (rulings T2, T4), and with the published scores they would let anyone
work back to a font's raw count, so they never reach the repository, a log or an artifact in
the clear (scoring plan §8.3 and §8.7). They are fitted once and held for the year (owner
ruling q9_theta_cadence, 2026-10-01): every later run scores with what was stored, so a
replay gives the same bytes (§8.4). The first fit is the GitHub replay of the held run of
2026-10-17 (october_replay_is_the_switch, 2026-10-19). One file per view and fit date::

    $TFF_STORE/_state/fit/<view>/<YYYY-MM-DD>.json
    {"schema": 1, "view": ..., "fitted_on": "YYYY-MM-DD", "spec_sha256": ...,
     "fit": <measure.MixedFit.to_record()>}

- **In use at a date:** the newest file dated on or before it. Holding a fit means writing
  none in between; re-estimating means writing a newer one, which takes an owner ruling.
- **Written once:** a file is never replaced, and a different fit for its date is an error.
- **The spec:** ``spec_sha256`` hashes what the parameters mean (the terms, their families
  and baseline groups, the grid and the view's method version), so a fit is never read into
  a model it wasn't made for. A changed model needs a new method version and a new fit.
- **When none is stored** (``obtain``): ``--write-fit`` (the GitHub run of the switch, or of
  a refit the owner rules, when the workflow's ``write_fit`` input asks) fits it and uses it;
  refresh stores it (``store_pending``) only once the whole run has passed its checks, so a
  failed run keeps nothing, and then runs the pipeline again offline in read mode, so that
  every rank pass, the one before "verify" included, scores with the fit kept, as the job's
  own replay does (refresh.py). ``--fit-in-memory`` (a local dry run) fits and keeps nothing;
  and a date a merged run used (2026-09-26 has none) is fitted in memory, keeping nothing,
  whatever the flag. Any other run stops: it would otherwise publish scores from a fit
  nobody kept.
- **Staged:** stage "rank" stages the record of the fit each view scored with in
  ``build/stage/fit-records/<view>.json`` (``stage_record``): the same bytes in a run that
  made the fit and in its replay that reads it back. ``store_pending`` keeps a new one from
  there, and the what-if rerun of ``nolicense`` scores with it (mode ``"staged"``: it never
  fits, and never reads or writes the store).
- **Read back:** a fit is always used as read back from its JSON, even one made in memory,
  so a run and its replay score with the same bits.

Nothing here logs or formats a parameter: messages name the view, the date and the file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tff_catalog import jsonio

if TYPE_CHECKING:
    from tff_catalog.engine.measure import Mixed, MixedFit

FIT_DIR = ("_state", "fit")
FIT_SCHEMA = 1
# stages.RunOptions.fit: the command line sets the first three; "staged" is nolicense's.
Mode = Literal["read", "write", "memory", "staged"]
MODES: tuple[Mode, ...] = ("read", "write", "memory", "staged")
RECORDS_DIR = "fit-records"  # under build/stage: the record of each view's fit in use


class FitError(RuntimeError):
    """A stored fit is missing where one is needed, unreadable, or not this model's."""


@dataclass(frozen=True)
class Obtained:
    """The fit a run scores with, and where it came from."""

    fit: MixedFit
    fitted_on: date
    sha256: str  # of the record's bytes, ``data``
    source: Literal["store", "pending", "memory", "staged"]  # logged, never in build/
    data: bytes  # the record, as stored (or as it would be), for ``stage_record``


def merged_dates(run_history: Iterable[Mapping[str, Any]]) -> list[date]:
    """The dates of the merged runs in ``state/run_history.json``."""
    return [date.fromisoformat(str(e["run_date"])) for e in run_history if e.get("run_date")]


def fit_path(store_root: Path, view: str, day: date) -> Path:
    """``$TFF_STORE/_state/fit/<view>/<day>.json``."""
    return store_root.joinpath(*FIT_DIR, view, f"{day.isoformat()}.json")


def spec(model: Mixed, method_version: str) -> dict[str, Any]:
    """What a fit's parameters mean: its ``spec_sha256`` is the canonical hash of this."""
    from tff_catalog.engine import measure

    return {
        "terms": [{"name": t.name, "dist": t.dist, "groups": list(t.groups)} for t in model.counts],
        "param_names": model.param_names(),
        "grid": {"lo": measure.GRID_LO, "hi": measure.GRID_HI, "step": measure.GRID_STEP},
        "record_format": measure.RECORD_FORMAT,
        "method_version": method_version,
    }


def spec_sha256(model: Mixed, method_version: str) -> str:
    return hashlib.sha256(jsonio.canonical_bytes(spec(model, method_version))).hexdigest()


def stored_dates(store_root: Path, view: str) -> list[date]:
    """The fit dates the store holds for ``view``, oldest first."""
    folder = store_root.joinpath(*FIT_DIR, view)
    if not folder.is_dir():
        return []
    days = []
    for path in folder.glob("*.json"):
        try:
            days.append(date.fromisoformat(path.stem))
        except ValueError as exc:
            raise FitError(f"{path}: not a fit file (its name must be a date)") from exc
    return sorted(days)


def in_use(store_root: Path, view: str, run_date: date) -> date | None:
    """The date of the fit a run on ``run_date`` uses: the newest on or before it."""
    days = [d for d in stored_dates(store_root, view) if d <= run_date]
    return days[-1] if days else None


def document(
    view: str, fitted_on: date, model: Mixed, method_version: str, fit: MixedFit
) -> dict[str, Any]:
    return {
        "schema": FIT_SCHEMA,
        "view": view,
        "fitted_on": fitted_on.isoformat(),
        "spec_sha256": spec_sha256(model, method_version),
        "fit": fit.to_record(),
    }


def from_bytes(
    data: bytes, where: str, view: str, model: Mixed, method_version: str, fitted_on: date
) -> MixedFit:
    """The fit in a stored file's bytes, checked against ``view``, its date and the model's
    spec."""
    from tff_catalog.engine import measure

    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FitError(f"{where}: not JSON") from exc
    if not isinstance(doc, dict) or doc.get("schema") != FIT_SCHEMA or doc.get("view") != view:
        raise FitError(f"{where}: not a fit of {view} (schema {FIT_SCHEMA})")
    if doc.get("fitted_on") != fitted_on.isoformat():
        raise FitError(f"{where}: not fitted on {fitted_on.isoformat()}, as its name says")
    if doc.get("spec_sha256") != spec_sha256(model, method_version):
        raise FitError(
            f"{where}: fitted for another model of {view} (its spec differs); a new model needs "
            "a new method version and an owner ruling to refit (q9_theta_cadence)"
        )
    try:
        return measure.MixedFit.from_record(model, doc["fit"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FitError(f"{where}: its fit can't be read ({type(exc).__name__})") from exc


def write_once(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` unless the file is there; a different file is an error."""
    if path.is_file():
        if path.read_bytes() != data:
            raise FitError(f"{path}: a stored fit is never replaced, and this one differs")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _no_fit(view: str, run_date: date) -> str:
    return (
        f"{view}: no stored fit on or before {run_date.isoformat()}; only the switch or a "
        "refit by owner ruling makes one (--write-fit, the refresh workflow's write_fit "
        "input), and a local dry run can pass --fit-in-memory"
    )


def obtain(
    *,
    mode: Mode,
    store_root: Path | None,
    view: str,
    run_date: date,
    merged_dates: Iterable[date],
    model: Mixed,
    method_version: str,
    fit: Callable[[Mixed], MixedFit],
    stage_dir: Path | None = None,
) -> Obtained:
    """The fit a run on ``run_date`` scores ``view`` with (module docstring). ``stage_dir``
    is where mode ``"staged"`` reads the record back (``staged``)."""
    if mode not in MODES:
        raise ValueError(f"fit mode {mode!r}: one of {MODES}")
    if mode == "staged":
        if stage_dir is None:
            raise ValueError("fit mode 'staged' needs the stage directory")
        return staged(stage_dir, view, model, method_version)
    if store_root is not None:
        day = in_use(store_root, view, run_date)
        if day is not None:
            path = fit_path(store_root, view, day)
            data = path.read_bytes()
            fitted = from_bytes(data, str(path), view, model, method_version, day)
            return Obtained(fitted, day, hashlib.sha256(data).hexdigest(), "store", data)
    merged = run_date in set(merged_dates)
    if mode == "read" and not merged:
        raise FitError(_no_fit(view, run_date))
    if mode == "write" and not merged and store_root is None:
        raise FitError(f"{view}: --write-fit needs TFF_STORE, where the fit is kept")
    made = fit(model)
    problems = made.problems()  # names parameters, never values
    if problems:
        raise FitError(f"{view}: the fit of {run_date.isoformat()} fails: {'; '.join(problems)}")
    data = jsonio.canonical_bytes(document(view, run_date, model, method_version, made))
    source: Literal["pending", "memory"] = "pending" if mode == "write" and not merged else "memory"
    where = f"{view} fit of {run_date.isoformat()}"
    fitted = from_bytes(data, where, view, model, method_version, run_date)
    return Obtained(fitted, run_date, hashlib.sha256(data).hexdigest(), source, data)


def record_path(stage_dir: Path, view: str) -> Path:
    """``build/stage/fit-records/<view>.json``: the record of the fit ``view`` scored with."""
    return stage_dir / RECORDS_DIR / f"{view}.json"


def stage_record(stage_dir: Path, view: str, data: bytes) -> Path:
    """Stage the record of the fit ``view`` scored with; a rank rerun replaces it."""
    path = record_path(stage_dir, view)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def staged(stage_dir: Path, view: str, model: Mixed, method_version: str) -> Obtained:
    """The fit this build's stage "rank" scored ``view`` with, read back from its staged
    record and checked against the model's spec: never a new fit, never the store."""
    path = record_path(stage_dir, view)
    if not path.is_file():
        raise FitError(f"{path}: no staged fit of {view}; stage 'rank' stages one")
    data = path.read_bytes()
    try:
        day = date.fromisoformat(json.loads(data)["fitted_on"])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise FitError(f"{path}: not a fit of {view} (schema {FIT_SCHEMA})") from exc
    fitted = from_bytes(data, str(path), view, model, method_version, day)
    return Obtained(fitted, day, hashlib.sha256(data).hexdigest(), "staged", data)


def store_pending(
    stage_dir: Path,
    store_root: Path,
    run_date: date,
    views: Iterable[str],
    merged_dates: Iterable[date] = (),
) -> list[Path]:
    """Store, once each, the fits this run made to keep: the staged records of ``views``
    (``stage_record``) fitted on ``run_date``, unless that date is a merged run's, whose fit
    stays in memory. refresh calls it when a --write-fit run has passed. A record read from
    the store is left as it is, and a stored fit is never replaced (``write_once``). Returns
    the files it wrote."""
    if run_date in set(merged_dates):
        return []
    written = []
    for view in sorted(views):
        path = record_path(stage_dir, view)
        if not path.is_file():
            continue
        data = path.read_bytes()
        doc = json.loads(data)
        if not isinstance(doc, dict) or doc.get("view") != view:
            raise FitError(f"{path}: not a staged fit of {view}")
        if doc.get("fitted_on") != run_date.isoformat():
            continue  # an earlier fit the run read from the store
        target = fit_path(store_root, view, run_date)
        new = not target.is_file()
        write_once(target, data)
        if new:
            written.append(target)
    return written
