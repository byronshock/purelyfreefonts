"""Stage "parse" (design-m1 §1.2 row 2). Owner: agent I2, with the collector framework (step 3).

For every enabled collector, pick the snapshot this run uses (today's, or the
latest complete one within the stale window), run ``parse()`` offline and write
``build/stage/records/<source>.jsonl`` in canonical order. Collectors with
``needs_baseline`` are also parsed on their baseline snapshots (named in
``state/run_history.json``) into ``<source>@<baseline-date>.jsonl``.
``build/stage/stale.json`` lists every source used stale, with its data date.

The details, all deterministic given the store, ``state/`` and the config:

- **The day.** Snapshots are chosen for ``--from-snapshots`` when given, else
  for the run date. A snapshot dated before that day is *stale*.
- **Which snapshot.** When ``run_history`` has an entry for the day naming a
  snapshot of the source, that one (so a replay of a merged run reads what it
  read). Otherwise the newest complete snapshot no older than
  ``store.stale_max_age(ranking.stale.max_months)``. Nothing inside that window:
  the source is *dropped* (no records file) and listed as such.
- **Parse failures** count as a failed source, as the methodology's outage rule
  says: the error is logged, and the next older snapshot in the window is
  tried, flagged stale with the reason. A snapshot the store cannot read (a
  damaged manifest) counts as missing, and a damaged extract as a parse failure.
- **Baselines** (``needs_baseline``): the oldest snapshot of the source that
  ``run_history`` names, older than the one used and at most
  ``BASELINE_MAX_AGE`` before it. That gives the methodology's GitHub
  differences: none on the first run, the earliest snapshot in months 2-11,
  the 12-month difference from month 12.
- **Outputs.** ``records/<source>.jsonl`` (``stageio`` rows of records),
  ``stale.json`` (``{source: StaleSource}``), ``snapshots.json``
  (``{source: SourceSnapshot}``: what each records file was parsed from, for
  refresh's run history and the catalog's source list), and the ``stale`` part
  of ``build/state/`` (``{source: {last_good, stale_runs}}``). ``load_stale``
  and ``load_snapshots`` read the two JSON files back.
- **``--only``** narrows the collectors (by collector name, or by an engine
  source that reads it) and leaves every other source's files as they are.
  A full run deletes records files of collectors it did not write.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from tff_catalog import stageio
from tff_catalog.collectors import discover
from tff_catalog.collectors.base import ParseContext, load_settings
from tff_catalog.records import NAMESPACES, Relation, sort_key
from tff_catalog.state import read_part, write_part
from tff_catalog.store import Snapshot, Store, stale_max_age

if TYPE_CHECKING:
    import logging
    from pathlib import Path

    from tff_catalog.collectors.base import Collector
    from tff_catalog.paths import Paths
    from tff_catalog.records import Record
    from tff_catalog.stages import StageContext

# 12 months, plus two weeks for drift in the day the monthly run happens.
BASELINE_MAX_AGE = timedelta(days=366 + 14)
STALE_FILE = stageio.STAGE_FILES["stale"].path  # under build/stage/
SNAPSHOTS_FILE = stageio.STAGE_FILES["snapshots"].path  # under build/stage/


@dataclass(frozen=True, slots=True)
class StaleSource:
    """One entry of ``build/stage/stale.json``: a source this run could not read fresh."""

    snapshot: date | None  # the snapshot used instead; None when the source is dropped
    stale_of: date  # the day it stands in for (the run's snapshot day)
    data_date: date | None  # the used snapshot's data date
    age_days: int | None  # stale_of - snapshot, in days
    stale_runs: int  # consecutive runs stale, this one included (state/stale.json)
    dropped: bool  # nothing usable inside the stale window: the source is left out
    reason: str


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """One entry of ``build/stage/snapshots.json``: where a records file comes from."""

    snapshot: date
    stale: bool
    data_date: date | None
    window: tuple[date, date] | None
    fetched_at: str | None  # the snapshot's newest request (ISO UTC)
    collector_version: int
    records: int
    baselines: tuple[date, ...]  # records/<source>@<date>.jsonl written this run


def _keys(r: Record) -> tuple[Any, ...]:
    return (r.subject, r.object) if isinstance(r, Relation) else (r.key,)


def parse_source(
    c: Collector, snap: Snapshot, settings: object, log: logging.Logger
) -> list[Record]:
    """Run ``c.parse`` on ``snap`` and return the records sorted by ``records.sort_key``.

    Raises ``TypeError`` for a record type outside ``c.emits`` and ``ValueError``
    for a namespace outside ``records.NAMESPACES``, for a record whose ``source``
    is not ``c.name``, and for a snapshot written by a newer collector version.
    """
    if snap.manifest.collector_version > c.version:
        raise ValueError(
            f"{c.name}: snapshot {snap.date} has extract version "
            f"{snap.manifest.collector_version}, newer than the collector's {c.version}"
        )
    ctx = ParseContext(snapshot=snap, settings=settings, log=log)
    out: list[Record] = []
    for r in c.parse(ctx):
        if type(r) not in c.emits:
            allowed = ", ".join(t.__name__ for t in c.emits)
            raise TypeError(f"{c.name}: emitted a {type(r).__name__}, not in emits ({allowed})")
        if r.source != c.name:
            raise ValueError(f"{c.name}: emitted a record of source {r.source!r}")
        for key in _keys(r):
            if key.ns not in NAMESPACES:
                raise ValueError(f"{c.name}: namespace {key.ns!r} is not in NAMESPACES")
        out.append(r)
    return sorted(out, key=sort_key)


def _day(ctx: StageContext) -> date:
    """The day snapshots are chosen for: ``--from-snapshots``, else the run date."""
    return ctx.options.from_snapshots or ctx.run_date


def _pointer(ctx: StageContext, name: str, day: date) -> date | None:
    """The snapshot of ``name`` that a merged run on ``day`` used, if run_history says."""
    for entry in ctx.state.run_history:
        if str(entry.get("run_date")) == day.isoformat():
            used = (entry.get("snapshots") or {}).get(name)
            return None if used is None else date.fromisoformat(str(used))
    return None


def _candidates(ctx: StageContext, store: Store, name: str) -> list[Snapshot]:
    """Snapshots to try for ``name``, best first."""
    day = _day(ctx)
    pinned = _pointer(ctx, name, day)
    if pinned is not None:
        snap = store.snapshot(name, pinned)
        if snap is not None:
            return [snap]
        ctx.log.warning(
            "%s: run_history names snapshot %s, which the store lacks or cannot read",
            name,
            pinned,
        )
    oldest = day - stale_max_age(ctx.config.ranking.stale.max_months)
    out = []
    for d in reversed(store.dates(name)):
        if oldest <= d <= day:
            snap = store.snapshot(name, d)
            if snap is not None:
                out.append(snap)
    return out


def _baseline_candidates(ctx: StageContext, store: Store, name: str, used: date) -> list[Snapshot]:
    """Baseline snapshots of ``name`` for a run reading its ``used`` snapshot, best first."""
    out = []
    for d in ctx.state.snapshot_dates(name):
        if used - BASELINE_MAX_AGE <= d < used:
            snap = store.snapshot(name, d)
            if snap is None:
                ctx.log.warning(
                    "%s: baseline %s from run_history is missing or unreadable", name, d
                )
            else:
                out.append(snap)
    return out


def baselines(ctx: StageContext, c: Collector) -> list[Snapshot]:
    """The earlier snapshots a lifetime-counter collector needs (run_history pointers).

    At most one: the oldest named snapshot within ``BASELINE_MAX_AGE`` before the
    snapshot this run would use (before any parse failure moves it). Empty for a
    collector without ``needs_baseline``, and on the first run.
    """
    if not c.needs_baseline:
        return []
    store = ctx.require_store()
    candidates = _candidates(ctx, store, c.name)
    if not candidates:
        return []
    return _baseline_candidates(ctx, store, c.name, candidates[0].date)[:1]


def _selected(ctx: StageContext, name: str) -> bool:
    """``--only`` lets collector ``name`` through by its own name or an engine source's."""
    if ctx.options.selects(name):
        return True
    sources = ctx.config.ranking.sources.all()
    return any(ctx.options.selects(s) for s, cfg in sources.items() if cfg.collector == name)


@dataclass(slots=True)
class _Outputs:
    """What one parse run writes, collected before the files are."""

    stale: dict[str, StaleSource]
    snapshots: dict[str, SourceSnapshot]
    state: dict[str, dict[str, Any]]
    written: set[Path]


def _first_parse(
    ctx: StageContext, c: Collector, settings: object, candidates: list[Snapshot]
) -> tuple[tuple[Snapshot, list[Record]] | None, str]:
    """Parse the first candidate that parses: ``((snapshot, records) or None, failures)``.

    ``failures`` names the first failed snapshot, or is empty. Only the exception's
    type goes in it (the log has the message), so the output never holds a path.
    """
    failure = ""
    for snap in candidates:
        try:
            return (snap, parse_source(c, snap, settings, ctx.log.getChild(c.name))), failure
        except Exception as exc:
            ctx.log.exception("%s: parse of snapshot %s failed", c.name, snap.date)
            failure = failure or f"parse of {snap.date} failed ({type(exc).__name__})"
    return None, failure


def _parse_baseline(
    ctx: StageContext, store: Store, c: Collector, settings: object, used: date
) -> tuple[Snapshot, list[Record]] | None:
    for snap in _baseline_candidates(ctx, store, c.name, used):
        try:
            return snap, parse_source(c, snap, settings, ctx.log.getChild(c.name))
        except Exception:
            ctx.log.exception("%s: parse of baseline %s failed; trying the next", c.name, snap.date)
    return None


def _parse_one(ctx: StageContext, store: Store, c: Collector, out: _Outputs) -> None:
    settings = load_settings(c, ctx.paths)
    if not getattr(settings, "enabled", True):
        ctx.log.info("%s: disabled in config/sources/%s.toml", c.name, c.name)
        return
    day = _day(ctx)
    base = ctx.state.stale.get(c.name, {})
    runs_before = int(base.get("stale_runs", 0))
    candidates = _candidates(ctx, store, c.name)
    chosen, failure = _first_parse(ctx, c, settings, candidates)
    if chosen is None:
        last = None if candidates else store.latest(c.name, day)
        last_good = last.date.isoformat() if last is not None else base.get("last_good")
        out.state[c.name] = {"last_good": last_good, "stale_runs": runs_before + 1}
        months = ctx.config.ranking.stale.max_months
        out.stale[c.name] = StaleSource(
            snapshot=None,
            stale_of=day,
            data_date=None,
            age_days=None,
            stale_runs=runs_before + 1,
            dropped=True,
            reason=failure or f"no complete snapshot within {months} months",
        )
        ctx.log.warning("%s: dropped, no usable snapshot on or before %s", c.name, day)
        return
    snap, recs = chosen
    records_dir = ctx.paths.records
    path = records_dir / f"{c.name}.jsonl"
    stageio.dump_rows(recs, path)
    out.written.add(path)
    used_baselines: list[date] = []
    if c.needs_baseline:
        found = _parse_baseline(ctx, store, c, settings, snap.date)
        if found is not None:
            base_snap, base_recs = found
            base_path = records_dir / f"{c.name}@{base_snap.date.isoformat()}.jsonl"
            stageio.dump_rows(base_recs, base_path)
            out.written.add(base_path)
            used_baselines.append(base_snap.date)
    stale = snap.date < day
    runs = runs_before + 1 if stale else 0
    out.state[c.name] = {"last_good": snap.date.isoformat(), "stale_runs": runs}
    out.snapshots[c.name] = SourceSnapshot(
        snapshot=snap.date,
        stale=stale,
        data_date=snap.manifest.data_date,
        window=snap.manifest.window,
        fetched_at=snap.fetched_at,
        collector_version=snap.manifest.collector_version,
        records=len(recs),
        baselines=tuple(used_baselines),
    )
    if stale:
        out.stale[c.name] = StaleSource(
            snapshot=snap.date,
            stale_of=day,
            data_date=snap.manifest.data_date,
            age_days=(day - snap.date).days,
            stale_runs=runs,
            dropped=False,
            reason=failure or f"no snapshot for {day}",
        )
        ctx.log.warning("%s: stale, using the snapshot of %s", c.name, snap.date)


def _remove_records(records_dir: Path, name: str) -> None:
    for path in (records_dir / f"{name}.jsonl", *records_dir.glob(f"{name}@*.jsonl")):
        path.unlink(missing_ok=True)


def run(ctx: StageContext) -> None:
    """Stage "parse": write ``stage/records/*.jsonl`` and ``stage/stale.json``."""
    store = ctx.require_store()
    found = discover()
    _warn_unknown_only(ctx, found)
    collectors = [c for c in found.values() if _selected(ctx, c.name)]
    records_dir = ctx.paths.records
    records_dir.mkdir(parents=True, exist_ok=True)
    partial = bool(ctx.options.only)
    out = _Outputs(stale={}, snapshots={}, state={}, written=set())
    if partial:
        out.stale, out.snapshots = _previous_outputs(ctx.paths)
        out.state = dict(read_part(ctx.paths, "stale"))
    for c in collectors:
        _remove_records(records_dir, c.name)
        out.stale.pop(c.name, None)
        out.snapshots.pop(c.name, None)
        out.state.pop(c.name, None)
        _parse_one(ctx, store, c, out)
    if not partial:
        for path in records_dir.glob("*.jsonl"):
            if path not in out.written:
                path.unlink()
    stageio.dump_stage(ctx.paths, "stale", dict(sorted(out.stale.items())))
    stageio.dump_stage(ctx.paths, "snapshots", dict(sorted(out.snapshots.items())))
    write_part(ctx.paths, "stale", dict(sorted(out.state.items())), stage="parse")
    ctx.log.info("parsed %d sources (%d stale or dropped)", len(out.snapshots), len(out.stale))


def _warn_unknown_only(ctx: StageContext, found: dict[str, Collector]) -> None:
    known = set(found) | set(ctx.config.ranking.sources.all())
    for name in ctx.options.only:
        if name not in known:
            ctx.log.warning("--only %s: no collector or engine source has that name", name)


def _previous_outputs(paths: Paths) -> tuple[dict[str, StaleSource], dict[str, SourceSnapshot]]:
    """The stale and snapshot maps an earlier parse wrote, for an ``--only`` run to update."""
    try:
        stale = load_stale(paths)
    except FileNotFoundError, stageio.StageFileError:
        stale = {}
    try:
        snapshots = load_snapshots(paths)
    except FileNotFoundError, stageio.StageFileError:
        snapshots = {}
    return stale, snapshots


def load_stale(paths: Paths) -> dict[str, StaleSource]:
    """``build/stage/stale.json``: every source this run used stale or dropped."""
    return stageio.load_stage(paths, "stale")


def load_snapshots(paths: Paths) -> dict[str, SourceSnapshot]:
    """``build/stage/snapshots.json``: the snapshot each records file was parsed from."""
    return stageio.load_stage(paths, "snapshots")
