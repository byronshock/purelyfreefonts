"""Run state carried between monthly runs (design-m1 §5). Owner: agent I2.

``state/`` on main is read-only input. A run writes the state it proposes to
``build/state/``; the refresh pull request copies that to ``state/``, so state
advances only when that PR is merged, and two unmerged runs from the same
inputs give identical output.

Every stage is its own command, so each state file has owning stages
(``STATE_OWNERS``) that write it with ``write_part``:

- the first owner starts from the committed file (``ctx.state``);
- a later owner in the same run starts from the earlier owner's file
  (``read_part``), for example ``membership`` adds catalog dates to the
  ``first_seen`` that ``correct`` wrote;
- every part is a pure function of its inputs, so a rerun writes the same bytes.

``refresh`` starts with an empty ``build/state/`` and ends with
``complete_next_state``, which copies every file no stage wrote this run from
``state/``, so ``build/state/`` always holds the whole state.

Files (all JSON, written with ``jsonio.dump``)::

    run_history.json    [{run_date, merged_pr, code_commit, config_sha256, snapshots: {source: date}}]  <= 24
    ids.json            {family_id: {family, minted_from, first_seen}}      append-only id registry
    first_seen.json     {family_id: {catalog: date|null, sources: {source: date}}}
    membership.json     {catalog: {id: {member, entered, runs_outside}}, top100: {rank_key: {id: {...}}}}
    license_hashes.json {id: {text_url, text_sha256, checked_on, font_version, font_file: {url, sha256}, level}}
    stale.json          {source: {last_good: date, stale_runs: int}}
    published_ranks.json {rank_key: {id: order}}
    smoothing.json      {month: "YYYY-MM", fot_ewma: {id: z}, fot_ewma_base: {id: z},
                         fot_weeks: [week], rising: {source: {id: [share_m-2, share_m-1, share_m]}}}
                        (month: the run month that wrote it; fot_ewma_base: the EWMA that
                        month started from, so a rerun in the same month redoes it once;
                        fot_weeks: the Fonts Over Time weeks seen, for its phase-in)

A missing file reads as empty, so the first run starts from nothing. Snapshot
baselines (GitHub and Nerd differences) are pointers:
``run_history[*].snapshots``. Owner rulings stay in ``data/reviews/``.
"""

import copy
import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import jsonio

if TYPE_CHECKING:
    from tff_catalog.paths import Paths

STATE_FILES: dict[str, str] = {
    "run_history": "run_history.json",
    "ids": "ids.json",
    "first_seen": "first_seen.json",
    "membership": "membership.json",
    "license_hashes": "license_hashes.json",
    "stale": "stale.json",
    "published_ranks": "published_ranks.json",
    "smoothing": "smoothing.json",
}
MAX_RUN_HISTORY = 24

# The stages that write each state file, in pipeline order. Frozen contract.
STATE_OWNERS: dict[str, tuple[str, ...]] = {
    "stale": ("parse",),
    "ids": ("universe",),
    "first_seen": ("correct", "membership"),  # sources' first days, then catalog entry dates
    "smoothing": ("rank",),
    "membership": ("membership",),
    "license_hashes": ("verify",),
    "published_ranks": ("export",),
    "run_history": ("refresh",),
}
_EMPTY: dict[str, Any] = {"run_history": []}


def read_part(paths: Paths, name: str) -> Any:
    """State file ``name`` as this run has it so far: ``build/state/`` if a stage wrote
    it, else the committed ``state/`` file, else empty."""
    filename = STATE_FILES[name]
    for directory in (paths.next_state, paths.state):
        if (directory / filename).is_file():
            return jsonio.load(directory / filename)
    return _EMPTY.get(name, {})


def write_part(paths: Paths, name: str, obj: object, *, stage: str) -> Path:
    """Write state file ``name`` into ``build/state/``; only its ``STATE_OWNERS`` may."""
    if name not in STATE_FILES:
        raise KeyError(f"unknown state file {name!r}; known: {', '.join(STATE_FILES)}")
    if stage not in STATE_OWNERS[name]:
        raise PermissionError(f"stage {stage!r} may not write state {name!r}")
    path = paths.next_state / STATE_FILES[name]
    jsonio.dump(obj, path)
    return path


def complete_next_state(paths: Paths) -> list[str]:
    """Copy each state file no stage wrote this run from ``state/`` into ``build/state/``.

    Returns the names copied. A file missing from both stays missing (it reads as empty).
    """
    copied = []
    for name, filename in STATE_FILES.items():
        source, target = paths.state / filename, paths.next_state / filename
        if not target.exists() and source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(name)
    return copied


def _as_date(value: object, where: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{where}: not a date (YYYY-MM-DD): {value!r}")


@dataclass(frozen=True, slots=True)
class State:
    """The committed state a run starts from. Never mutated."""

    run_history: tuple[dict[str, Any], ...] = ()
    ids: dict[str, dict[str, Any]] = field(default_factory=dict)
    first_seen: dict[str, dict[str, Any]] = field(default_factory=dict)
    membership: dict[str, Any] = field(default_factory=dict)
    license_hashes: dict[str, dict[str, Any]] = field(default_factory=dict)
    stale: dict[str, dict[str, Any]] = field(default_factory=dict)
    published_ranks: dict[str, dict[str, int]] = field(default_factory=dict)
    smoothing: dict[str, Any] = field(default_factory=dict)

    def snapshot_dates(self, source: str) -> tuple[date, ...]:
        """The dates of ``source``'s snapshots that merged runs used, sorted.

        These are the baseline pointers (``run_history[*].snapshots``): parse
        re-parses them for lifetime-counter collectors.
        """
        days = set()
        for i, entry in enumerate(self.run_history):
            day = (entry.get("snapshots") or {}).get(source)
            if day is not None:
                days.add(_as_date(day, f"run_history[{i}].snapshots.{source}"))
        return tuple(sorted(days))

    def frozen_dates(self, source: str) -> frozenset[date]:
        """The dates whose ``source`` snapshot is immutable, for ``Store.writer(frozen=...)``.

        Every merged run's date and every snapshot of ``source`` a merged run
        used: rewriting or adding one would change what a replay of that run reads.
        """
        runs = {
            _as_date(entry["run_date"], f"run_history[{i}].run_date")
            for i, entry in enumerate(self.run_history)
            if entry.get("run_date") is not None
        }
        return frozenset(runs | set(self.snapshot_dates(source)))


@dataclass(slots=True)
class NextState:
    """The whole proposed state in memory, for ``refresh`` and tests.

    Stages do not pass one around (each is its own command): they write their
    parts with ``write_part``. ``write_next_state`` writes a whole one at once.
    """

    base: State
    run_history: list[dict[str, Any]] = field(default_factory=list)
    ids: dict[str, dict[str, Any]] = field(default_factory=dict)
    first_seen: dict[str, dict[str, Any]] = field(default_factory=dict)
    membership: dict[str, Any] = field(default_factory=dict)
    license_hashes: dict[str, dict[str, Any]] = field(default_factory=dict)
    stale: dict[str, dict[str, Any]] = field(default_factory=dict)
    published_ranks: dict[str, dict[str, int]] = field(default_factory=dict)
    smoothing: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_state(cls, state: State) -> NextState:
        """Start from a deep copy of ``state``."""
        parts = {name: copy.deepcopy(getattr(state, name)) for name in STATE_FILES}
        parts["run_history"] = list(parts["run_history"])
        return cls(base=state, **parts)


def _history(entries: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """``run_history`` in date order, the newest ``MAX_RUN_HISTORY`` entries.

    Every entry needs a valid ``run_date``, and no two may share one.
    """
    days = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or "run_date" not in entry:
            raise ValueError(f"run_history[{i}]: expected an object with a run_date")
        days.append(_as_date(entry["run_date"], f"run_history[{i}].run_date"))
    if len(set(days)) != len(days):
        twice = sorted({d.isoformat() for d in days if days.count(d) > 1})
        raise ValueError(f"run_history: more than one entry for {', '.join(twice)}")
    ordered = [e for _, e in sorted(zip(days, entries, strict=True), key=lambda p: p[0])]
    return ordered[-MAX_RUN_HISTORY:]


def load_state(path: Path) -> State:
    """Read ``state/`` (or any directory of the same files); missing files read as empty.

    ``run_history.json`` must be a JSON array of entries with distinct, valid
    ``run_date`` values, and every other file an object; anything else raises
    ``ValueError`` naming the file.
    """
    parts: dict[str, Any] = {}
    for name, filename in STATE_FILES.items():
        file = Path(path) / filename
        if not file.is_file():
            continue
        try:
            value = jsonio.load(file)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{file}: not valid JSON: {exc}") from exc
        if name == "run_history":
            if not isinstance(value, list):
                raise ValueError(f"{file}: expected a JSON array")
            try:
                value = tuple(_history(value))
            except ValueError as exc:
                raise ValueError(f"{file}: {exc}") from exc
        elif not isinstance(value, dict):
            raise ValueError(f"{file}: expected a JSON object")
        parts[name] = value
    return State(**parts)


def write_next_state(next_state: NextState, path: Path) -> None:
    """Write every state file to ``path`` (normally ``build/state/``), deterministically.

    Each file is pretty canonical JSON (``jsonio.dump``, keys sorted);
    ``run_history`` is sorted by run date and cut to ``MAX_RUN_HISTORY`` entries;
    a missing or bad ``run_date``, or one listed twice, raises ``ValueError``
    before anything is written. Every file in ``STATE_FILES`` is written, empty
    parts included.
    """
    path = Path(path)
    history = _history(next_state.run_history)  # checked before any file is written
    for name, filename in STATE_FILES.items():
        value = history if name == "run_history" else getattr(next_state, name)
        jsonio.dump(value, path / filename)


def apply_state(state_dir: Path, next_state_dir: Path, out_dir: Path | None = None) -> Path:
    """Simulate merging a refresh PR: ``next_state_dir``'s files over ``state_dir``'s.

    Writes into ``out_dir`` (a copy) or, when it is None, into ``state_dir``
    itself; returns the directory written.

    Only the files in ``STATE_FILES`` are carried over; a state file missing from
    ``next_state_dir`` keeps its ``state_dir`` version. ``out_dir`` must not exist
    yet, so ``state_dir`` is never touched when it is given. Anything else in
    ``next_state_dir`` raises ``ValueError``: ``build/state/`` holds state files only.
    """
    state_dir, next_state_dir = Path(state_dir), Path(next_state_dir)
    if not next_state_dir.is_dir():
        raise FileNotFoundError(f"{next_state_dir}: no proposed state to merge")
    known = set(STATE_FILES.values())
    unknown = sorted(p.name for p in next_state_dir.iterdir() if p.name not in known)
    if unknown:
        raise ValueError(f"{next_state_dir}: not state files: {unknown}")
    target = state_dir
    if out_dir is not None:
        target = Path(out_dir)
        if target.exists():
            raise FileExistsError(f"{target}: already exists")
        if state_dir.is_dir():
            shutil.copytree(state_dir, target)
        else:
            target.mkdir(parents=True)
    else:
        target.mkdir(parents=True, exist_ok=True)
    for filename in sorted(known):
        source = next_state_dir / filename
        if source.is_file():
            jsonio.atomic_write(target / filename, source.read_bytes())
    return target
