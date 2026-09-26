"""The one-command refresh (milestone-1 step 18). Owner: agent P13.

``refresh(paths, run_date, from_snapshots)`` runs ``stages.pipeline()`` in
order and leaves ``build/`` ready for the refresh pull request. It never
writes ``state/``. The contract, step by step:

1. **Setup.** The snapshot store is required (``StoreNotConfigured``
   otherwise). Neither ``build/state/`` nor ``build/stage/`` (both emptied
   below) may be ``state/`` or overlap it. The config and ``state/`` are loaded
   and checked before anything is deleted or fetched.
2. **A clean slate.** A full run empties ``build/state/`` and ``build/stage/``,
   so no stage reads an earlier run's intermediates (stage "rank" reads
   ``stage/l3.json`` when present). A ``--only`` run keeps both, because parse
   then updates only the named sources' records and their ``stale`` entries.
   Committed outputs (``build/*.json``, ``build/*.md``, ``build/specimens/``)
   and ``build/cache/`` are left to the stages that own them.
3. **Stages.** Every pipeline stage runs in ``STAGES`` order; "fetch" is
   skipped in replay (``--from-snapshots D``). Only stages marked ``network``
   get the fetcher, and only in a live run, so a replay never reaches the
   network and other stages never can. When "verify" changes the set of
   families that failed L3 (``license_l3.exclusions``, the set "rank" leaves
   out) from the one "rank" read, "rank" and "membership" run once more, right
   after "verify". When stage "aliases" used rows that ``data/aliases.csv``
   lacks (``aliases.unapplied``: an auto-accepted Google rename, an owner's
   gate A ruling), refresh applies them to ``data/aliases.csv``
   (``aliases.apply_accepted``) and reruns every stage from "universe" to
   "aliases", at most ``MAX_ALIAS_APPLY`` times, before going on. Otherwise
   the universe would mint an id for the new name, which the append-only
   registry keeps for good. The refresh pull request carries the table.
4. **Hard failures.** ``validate.ValidationFailed`` from stage "validate"
   stops the run: any later stage is skipped (validate runs last, after
   "review", so ``review.md`` is written and scanned) and each failure becomes
   one line of ``RunResult.failures``. Any other exception propagates.
5. **Next state.** Refresh builds the whole ``NextState`` in memory: every
   part as this run's stages wrote it (``state.read_part``), else as committed,
   plus its own part, ``run_history``: the committed entries, less any for
   this run date, plus ``{run_date, merged_pr: null, code_commit,
   config_sha256, snapshots}``. ``snapshots`` names the snapshot parse used for
   each source (``stage/snapshots.json``) and each pseudo-source
   (``store.PSEUDO_SOURCES``) with a snapshot on the snapshot day. It is written
   to ``build/state/`` with ``state.write_next_state``, so the directory always
   holds every state file, then read back with ``load_state`` as a check. This
   happens after a hard failure too, but not after a crash.
6. **Run manifest.** A live run writes ``$TFF_STORE/_runs/<run_date>.json``
   (``run_manifest``) at the end, a crash included. A replay reproduces an
   earlier run, so it leaves the manifest of that run alone.

Everything in ``build/`` is a function of the store, ``state/``, ``config/``,
``data/`` and the code commit, so a clean clone replaying the same snapshots
produces identical output. Only ``RunResult.seconds`` and the run manifest carry
timings.
"""

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import METHOD_VERSION, __version__, stageio, stages, state
from tff_catalog.config import config_hash, load_config
from tff_catalog.store import PSEUDO_SOURCES, Store
from tff_catalog.validate import ValidationFailed

if TYPE_CHECKING:
    from tff_catalog.config_model import Config
    from tff_catalog.paths import Paths
    from tff_catalog.stages import RunOptions, StageContext

RERUN_AFTER_VERIFY = ("rank", "membership")
ALIAS_RERUN_FROM = "universe"  # the first stage that reads data/aliases.csv
MAX_ALIAS_APPLY = 2
RUN_MANIFEST_SCHEMA = 1
_LOG = logging.getLogger("tff_catalog.refresh")


@dataclass(frozen=True, slots=True)
class RunResult:
    run_date: date
    build: Path
    stages: tuple[str, ...]  # stages run, in order (a rerun appears twice)
    stale: tuple[str, ...]  # sources used stale
    failures: tuple[str, ...]  # hard-check failures
    seconds: float

    @property
    def ok(self) -> bool:
        return not self.failures


def refresh(
    paths: Paths,
    run_date: date,
    from_snapshots: date | None = None,
    *,
    options: RunOptions | None = None,
) -> RunResult:
    """Run the whole pipeline for ``run_date`` (module docstring).

    ``from_snapshots`` (or ``options.from_snapshots``; a different date in each
    raises ``ValueError``) replays that day's snapshots with no network.
    """
    options = _options(options, from_snapshots)
    started = time.perf_counter()  # a duration for the log and the run manifest, never for build/
    paths.require_store()
    _check_layout(paths)
    config = load_config(paths)
    # Loads and checks state/ too, so a bad state file stops the run before build/ is emptied.
    ctx = stages.make_context(paths, config, run_date, options, network=not options.replay)
    run = _Run(ctx)
    try:
        if not options.only:
            _reset_build(paths)
        run.pipeline()
        run.finish_state(config)
    except BaseException as exc:
        if not options.replay:
            _write_manifest_quietly(run, config, time.perf_counter() - started, exc)
        raise
    finally:
        if ctx.fetcher is not None:
            ctx.fetcher.close()
    seconds = time.perf_counter() - started
    if not options.replay:
        write_run_manifest(run.ctx, run_manifest(run, config, seconds))
    result = RunResult(
        run_date=run_date,
        build=paths.build,
        stages=tuple(run.ran),
        stale=run.stale_sources(),
        failures=run.failures,
        seconds=seconds,
    )
    _log_summary(result)
    return result


# --- setup ----------------------------------------------------------------------------------


def _options(options: RunOptions | None, from_snapshots: date | None) -> RunOptions:
    options = options or stages.RunOptions()
    if from_snapshots is None:
        return options
    if options.from_snapshots not in (None, from_snapshots):
        raise ValueError(
            f"from_snapshots={from_snapshots} but options.from_snapshots={options.from_snapshots}"
        )
    return replace(options, from_snapshots=from_snapshots)


def _check_layout(paths: Paths) -> None:
    """Refuse a layout where emptying ``build/state/`` or ``build/stage/`` could touch ``state/``."""
    committed = paths.state.resolve()
    for label, directory in (("build/state", paths.next_state), ("build/stage", paths.stage)):
        ours = directory.resolve()
        if ours.is_relative_to(committed) or committed.is_relative_to(ours):
            raise ValueError(
                f"{label} ({directory}) overlaps state/ ({paths.state}); "
                "refresh never writes state/"
            )


def _reset_build(paths: Paths) -> None:
    """Empty ``build/state/`` and ``build/stage/``."""
    for directory in (paths.next_state, paths.stage):
        if directory.is_symlink() or directory.is_file():
            directory.unlink()
        elif directory.exists():
            shutil.rmtree(directory)
    paths.next_state.mkdir(parents=True)


# --- the run --------------------------------------------------------------------------------


class _Run:
    """One pass over the pipeline: what ran, and the hard failures."""

    def __init__(self, ctx: StageContext) -> None:
        self.ctx = ctx
        self.commit = code_commit(ctx.paths.root)
        self.ran: list[str] = []
        self.failures: tuple[str, ...] = ()

    def pipeline(self) -> None:
        """Run the stages (module docstring, step 3); stop at a hard failure."""
        replay = self.ctx.options.replay
        excluded_for_rank: frozenset[str] | None = None
        for stage in stages.pipeline(replay=replay):
            if stage.name == "rank" and excluded_for_rank is None:
                excluded_for_rank = l3_excluded(self.ctx.paths)
            if not self._run(stage):
                return
            if stage.name == "aliases" and not self._apply_aliases():
                return
            if stage.name == "verify":
                excluded = l3_excluded(self.ctx.paths)
                if excluded != (excluded_for_rank or frozenset()):
                    self.ctx.log.info(
                        "L3 exclusions changed (%s); rerunning %s",
                        _change(excluded_for_rank or frozenset(), excluded),
                        " and ".join(RERUN_AFTER_VERIFY),
                    )
                    for name in RERUN_AFTER_VERIFY:
                        if not self._run(stages.get(name)):
                            return

    def _apply_aliases(self) -> bool:
        """Apply the rows stage "aliases" used but the table lacks, and rerun from
        "universe" (module docstring, step 3); False when a rerun stage failed."""
        from tff_catalog import aliases

        paths = self.ctx.paths
        names = [s.name for s in stages.pipeline(replay=self.ctx.options.replay)]
        span = names[names.index(ALIAS_RERUN_FROM) : names.index("aliases") + 1]
        for _ in range(MAX_ALIAS_APPLY):
            waiting = aliases.unapplied(paths)
            if not waiting:
                return True
            done = aliases.apply_accepted(self.ctx)
            self.ctx.log.info(
                "aliases: %d rows were not in %s; applied (%d added, %d replaced), rerunning %s",
                waiting,
                paths.aliases_csv.name,
                done.added,
                done.replaced,
                ", ".join(span),
            )
            for name in span:
                if not self._run(stages.get(name)):
                    return False
        if aliases.unapplied(paths):
            self.ctx.log.warning(
                "aliases: rows still unapplied after %d reruns; run `tff-catalog aliases "
                "--apply` and review them before merging",
                MAX_ALIAS_APPLY,
            )
        return True

    def _run(self, stage: stages.Stage) -> bool:
        """Run one stage; False when it reported hard failures."""
        fetcher = self.ctx.fetcher if stage.network else None
        try:
            stages.run_stage(stage.name, replace(self.ctx, fetcher=fetcher))
        except ValidationFailed as exc:
            self.ran.append(stage.name)
            self.failures = failure_lines(exc)
            later = [s.name for s in stages.pipeline(replay=self.ctx.options.replay)]
            skipped = later[later.index(stage.name) + 1 :]
            self.ctx.log.error(
                "stage %s: %d hard failures; skipped %s",
                stage.name,
                len(self.failures),
                ", ".join(skipped) or "nothing",
            )
            return False
        self.ran.append(stage.name)
        return True

    def finish_state(self, config: Config) -> None:
        """Write the whole next state to ``build/state/`` (module docstring, step 5)."""
        paths = self.ctx.paths
        nxt = state.NextState.from_state(self.ctx.state)
        for name in state.STATE_FILES:
            if name != "run_history":
                setattr(nxt, name, state.read_part(paths, name))
        nxt.run_history = next_run_history(self.ctx, self.entry(config))
        state.write_next_state(nxt, paths.next_state)
        state.load_state(paths.next_state)  # a stage that wrote a malformed part fails here

    def entry(self, config: Config) -> dict[str, Any]:
        """This run's ``run_history`` entry."""
        return {
            "run_date": self.ctx.run_date.isoformat(),
            "merged_pr": None,  # unknown until the pull request is merged
            "code_commit": self.commit,
            "config_sha256": config_hash(config),
            "snapshots": snapshots_used(self.ctx),
        }

    def stale_sources(self) -> tuple[str, ...]:
        """Sources this run read from an older snapshot (dropped ones are not "used")."""
        return tuple(sorted(s for s, info in _stale(self.ctx.paths).items() if not info.dropped))


def l3_excluded(paths: Paths) -> frozenset[str]:
    """Families that failed L3 in ``build/stage/l3.json``: what stage "rank" leaves out.

    Stage "verify"'s own definition (``license_l3.exclusions``), so refresh and
    "rank" never disagree on what changed.
    """
    from tff_catalog import license_l3

    return license_l3.exclusions(paths)


def _change(before: frozenset[str], after: frozenset[str]) -> str:
    added, removed = sorted(after - before), sorted(before - after)
    parts = [f"+{', +'.join(added)}" if added else "", f"-{', -'.join(removed)}" if removed else ""]
    return "; ".join(p for p in parts if p)


def failure_lines(exc: ValidationFailed) -> tuple[str, ...]:
    """One line per hard failure: each of ``exc.failures`` (``validate.Failure``) as text, or,
    when it lists none, the non-empty lines of the message."""
    listed = getattr(exc, "failures", None)
    if listed and not isinstance(listed, str):
        return tuple(str(f) for f in listed)
    lines = tuple(line.strip() for line in str(exc).splitlines() if line.strip())
    return lines or (type(exc).__name__,)


# --- run history ----------------------------------------------------------------------------


def next_run_history(ctx: StageContext, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The committed history less any entry for ``entry``'s date, plus ``entry``.

    ``state.write_next_state`` sorts it and keeps the newest ``MAX_RUN_HISTORY``.
    """
    day = entry["run_date"]
    return [e for e in ctx.state.run_history if str(e.get("run_date")) != day] + [entry]


def snapshots_used(ctx: StageContext) -> dict[str, str]:
    """``{source: snapshot date}`` this run read: parse's choices plus the pseudo-sources."""
    out = {name: s.snapshot.isoformat() for name, s in _snapshots(ctx.paths).items()}
    if ctx.store is not None:
        day = ctx.options.from_snapshots or ctx.run_date
        for name in PSEUDO_SOURCES:
            if ctx.store.snapshot(name, day) is not None:
                out[name] = day.isoformat()
    return dict(sorted(out.items()))


def code_commit(root: Path) -> str | None:
    """The checked-out commit of ``root`` (``git rev-parse HEAD``), or None outside git."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    sha = done.stdout.strip()
    return sha if done.returncode == 0 and sha else None


def _snapshots(paths: Paths) -> dict[str, Any]:
    from tff_catalog import parse

    try:
        return parse.load_snapshots(paths)
    except FileNotFoundError:
        return {}  # parse did not run


def _stale(paths: Paths) -> dict[str, Any]:
    from tff_catalog import parse

    try:
        return parse.load_stale(paths)
    except FileNotFoundError:
        return {}


# --- the run manifest -----------------------------------------------------------------------


def run_manifest(
    run: _Run, config: Config, seconds: float, error: BaseException | None = None
) -> dict[str, Any]:
    """``$TFF_STORE/_runs/<run_date>.json``: what a live run read and did (design-m1 §4)."""
    ctx = run.ctx
    snapshots = snapshots_used(ctx)
    return {
        "schema": RUN_MANIFEST_SCHEMA,
        "run_date": ctx.run_date.isoformat(),
        "version": __version__,
        "method_version": METHOD_VERSION,
        "code_commit": run.commit,
        "config_sha256": config_hash(config),
        "options": {"only": list(ctx.options.only), "refetch": ctx.options.refetch},
        "snapshots": snapshots,
        "sizes": _sizes(ctx.store, snapshots),
        "stale": stageio.encode(dict(sorted(_stale(ctx.paths).items()))),
        "stages": list(run.ran),
        "failures": list(run.failures),
        "error": None if error is None else f"{type(error).__name__}: {error}",
        "seconds": round(seconds, 1),
    }


def _sizes(store: Store | None, snapshots: dict[str, str]) -> dict[str, int]:
    """Stored extract bytes of each snapshot the run used."""
    out: dict[str, int] = {}
    if store is None:
        return out
    for name, day in snapshots.items():
        snap = store.snapshot(name, date.fromisoformat(day))
        if snap is not None:
            out[name] = snap.manifest.extract_bytes
    return out


def write_run_manifest(ctx: StageContext, manifest: dict[str, Any]) -> Path:
    """Write the run manifest into the store."""
    path = ctx.require_store().write_run(ctx.run_date, manifest)
    ctx.log.info("run manifest: %s", path)
    return path


def _write_manifest_quietly(
    run: _Run, config: Config, seconds: float, error: BaseException
) -> None:
    """Record a crashed live run; a second error here must not hide the first."""
    try:
        write_run_manifest(run.ctx, run_manifest(run, config, seconds, error))
    except Exception:
        _LOG.exception("could not write the run manifest of the failed run")


def _log_summary(result: RunResult) -> None:
    log: Callable[..., None] = _LOG.info if result.ok else _LOG.error
    log(
        "refresh %s: %d stages in %.1f s; stale: %s; hard failures: %d",
        result.run_date,
        len(result.stages),
        result.seconds,
        ", ".join(result.stale) or "none",
        len(result.failures),
    )
