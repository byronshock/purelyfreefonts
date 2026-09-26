"""The one-command refresh (milestone-1 steps 18 and 19).

``refresh`` runs on the synthetic store and state S0 of ``tests/helpers/synth.py``
with the real stage registry and runner, the real "parse" stage (reading the
synthetic extracts back through ``SynthCollector``) and synthetic bodies for the
other stages (``SynthStages``), built on ``synth.advance``. So these tests pin
refresh's own contract whatever state the other stages are in:

- a replay gives byte-identical ``build/`` twice, in two directories or in one;
- a replay of a live run gives the live run's ``build/``;
- fetch runs first live and never in replay, and only network stages get the fetcher;
- "verify" changing the L3 exclusions reruns "rank" and "membership" once;
- ``build/state/`` holds the whole next state, the stub pipeline's byte for byte
  apart from ``run_history``, whose entry refresh writes;
- a hard failure stops the run and reaches the command line; a crash propagates;
- ``--only`` updates the previous build in place;
- ``state/`` is never written (nor emptied through an overlapping layout), a bad
  ``state/`` stops the run before ``build/`` is emptied, and a live run records
  ``_runs/<date>.json``.

The workflow ``refresh.yml`` (triggers, permissions, pins reused from ``ci.yml``,
the contexts each key may read, bash syntax) is checked here too, and its step
scripts run under ``bash -e`` with stand-ins for git, gh, uv, sudo and diff. So is
the VPS watchdog (``ops/refresh-watchdog/``).
When the real stages can run on a synthetic store, ``test_state_two_runs.py``'s
"real" pipeline covers the same ground end to end.
"""

import ast
import importlib.machinery
import importlib.util
import re
import shutil
import subprocess
import sys
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from tests.helpers import ROOT, synth
from tests.helpers.treehash import tree_diff, treehash

from tff_catalog import cli, jsonio, parse, refresh, stageio, stages, state
from tff_catalog.collectors.base import Collector, CollectorBase, ParseContext
from tff_catalog.config import config_hash, load_config
from tff_catalog.config_model import CONFIG_FILES
from tff_catalog.license_l3 import L3Result
from tff_catalog.paths import Paths
from tff_catalog.records import RECORD_TYPES, Record, from_json, read_jsonl
from tff_catalog.validate import Failure, ValidationFailed

S0_DAY, OCT, NOV = synth.DAYS
DEC = date(2026, 12, 3)
NETWORK_STAGES = frozenset(s.name for s in stages.STAGES if s.network)


# --- synthetic collectors and stages ------------------------------------------------------------


class SynthCollector(CollectorBase):
    """Reads a synthetic snapshot's ``records.jsonl`` back into records."""

    kind = "universe"
    hosts = ("synth.example",)
    emits = tuple(RECORD_TYPES.values())

    def fetch(self, ctx: object) -> None:
        raise AssertionError("the synthetic fetch stage writes the snapshots")

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        for row in ctx.snapshot.iter_jsonl(synth.EXTRACT):
            yield from_json(row)


SYNTH_COLLECTORS: dict[str, Collector] = {
    name: cast(
        "Collector",
        type(
            f"Synth_{name}",
            (SynthCollector,),
            {"name": name, "needs_baseline": name == synth.INSTALLS},
        )(),
    )
    for name in synth.SOURCES
}

# The state parts each synthetic stage writes: the owners in state.STATE_OWNERS.
PARTS: dict[str, tuple[str, ...]] = {
    "universe": ("ids",),
    "correct": ("first_seen",),
    "rank": ("smoothing",),
    "membership": ("membership", "first_seen"),
    "verify": ("license_hashes",),
    "export": ("published_ranks",),
}


@dataclass(frozen=True, slots=True)
class Call:
    stage: str
    fetcher: bool  # the stage's context had a fetcher


@dataclass
class SynthStages:
    """Stand-ins for every stage body but "parse", deterministic like the real ones must be.

    ``fail_l3``: "verify" fails the first catalog member. ``hard_failures``:
    "validate" raises them. ``crash_in``: that stage raises ``RuntimeError``.
    """

    fail_l3: bool = False
    hard_failures: tuple[Failure, ...] = ()
    crash_in: str | None = None
    calls: list[Call] = field(default_factory=list)
    rank_saw: list[frozenset[str]] = field(default_factory=list)

    def load(self, stage: stages.Stage) -> Callable[[stages.StageContext], None]:
        def run(ctx: stages.StageContext) -> None:
            self.calls.append(Call(stage.name, ctx.fetcher is not None))
            if stage.name == self.crash_in:
                raise RuntimeError(f"boom in {stage.name}")
            handler = getattr(self, "_" + stage.name.replace("-", "_"), None)
            if handler is None:
                self._generic(stage.name, ctx)
            else:
                handler(ctx)

        return run

    def names(self) -> list[str]:
        return [c.stage for c in self.calls]

    # --- stage bodies ---

    def _fetch(self, ctx: stages.StageContext) -> None:
        store = ctx.require_store()
        ctx.require_fetcher()
        for source in synth.SOURCES:
            if store.snapshot(source, ctx.run_date) is None:
                recs = synth.source_records(source, ctx.run_date)
                synth.write_snapshot(store.root, source, ctx.run_date, recs)

    def _parse(self, ctx: stages.StageContext) -> None:
        parse.run(ctx)

    def _generic(self, name: str, ctx: stages.StageContext) -> dict[str, Any]:
        files, catalog = synth.advance(ctx.state, _inputs(ctx), ctx.run_date)
        for part in PARTS.get(name, ()):
            state.write_part(ctx.paths, part, files[part], stage=name)
        marker = {"stage": name, "run_date": ctx.run_date, "families": len(catalog["families"])}
        if name == "rank":
            excluded = refresh.l3_excluded(ctx.paths)
            self.rank_saw.append(excluded)
            marker["excluded"] = sorted(excluded)
        jsonio.dump(marker, ctx.paths.stage / "synth" / f"{name}.json")
        if name == "facts" and ctx.fetcher is not None:
            # A live network stage records what it read in a pseudo-source.
            synth.write_snapshot(ctx.require_store().root, "font_facts", ctx.run_date, [])
        return files

    def _verify(self, ctx: stages.StageContext) -> None:
        files = self._generic("verify", ctx)
        members = sorted(f for f, m in files["membership"]["catalog"].items() if m["member"])
        failed = set(members[:1]) if self.fail_l3 else set()
        stageio.dump_stage(
            ctx.paths,
            "l3",
            {
                fid: L3Result(
                    family_id=fid,
                    level="failed" if fid in failed else "L3",
                    checked_on=ctx.run_date,
                    text_url=None,
                    text_sha256=None,
                    matched=None,
                    name_ids=(None, None),
                    font_version=None,
                    font_file=None,
                )
                for fid in members
            },
        )

    def _export(self, ctx: stages.StageContext) -> None:
        self._generic("export", ctx)
        _, catalog = synth.advance(ctx.state, _inputs(ctx), ctx.run_date)
        catalog["excluded"] = sorted(refresh.l3_excluded(ctx.paths))
        jsonio.dump(catalog, ctx.paths.build / "catalog.json")

    def _validate(self, ctx: stages.StageContext) -> None:
        if self.hard_failures:
            raise ValidationFailed(self.hard_failures)

    def _review(self, ctx: stages.StageContext) -> None:
        catalog = jsonio.load(ctx.paths.build / "catalog.json")
        lines = [f"# Review {ctx.run_date}", ""]
        lines += [f"- {f['order']}. {f['family']}" for f in catalog["families"]]
        jsonio.atomic_write(ctx.paths.build / "review.md", ("\n".join(lines) + "\n").encode())


def _inputs(ctx: stages.StageContext) -> synth.Inputs:
    """What "parse" left for this run, in the stub pipeline's terms."""
    used = parse.load_snapshots(ctx.paths)
    return synth.Inputs(
        records={name: read_jsonl(ctx.paths.records / f"{name}.jsonl") for name in used},
        snapshots={name: s.snapshot for name, s in used.items()},
        stale=tuple(sorted(parse.load_stale(ctx.paths))),
    )


# --- fixtures -------------------------------------------------------------------------------------


@pytest.fixture
def synth_stages(monkeypatch: pytest.MonkeyPatch) -> SynthStages:
    """Synthetic stage bodies behind the real registry; the real parse reads SYNTH_COLLECTORS."""
    chosen = SynthStages()
    monkeypatch.setattr(stages.Stage, "load", lambda stage: chosen.load(stage))
    monkeypatch.setattr(parse, "discover", lambda kind=None: dict(sorted(SYNTH_COLLECTORS.items())))
    return chosen


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """The real config files plus settings for the synthetic collectors."""
    directory = tmp_path / "config"
    (directory / "sources").mkdir(parents=True)
    for filename in CONFIG_FILES:
        shutil.copyfile(ROOT / "config" / filename, directory / filename)
    for name in SYNTH_COLLECTORS:
        (directory / "sources" / f"{name}.toml").write_text("enabled = true\n")
    return directory


@dataclass
class Runner:
    tmp: Path
    store: Path
    config: Path

    def paths(self, name: str, state_dir: Path) -> Paths:
        work = self.tmp / "runs" / name
        return Paths.for_root(
            ROOT, state=state_dir, store=self.store, build=work / "build", raw_root=work / "raw"
        ).with_(config=self.config)

    def run(
        self, name: str, state_dir: Path, day: date, *, replay: bool = True
    ) -> refresh.RunResult:
        paths = self.paths(name, state_dir)
        return refresh.refresh(paths, day, from_snapshots=day if replay else None)


@pytest.fixture
def runner(tmp_path: Path, synth_store: Path, config_dir: Path) -> Runner:
    return Runner(tmp_path, synth_store, config_dir)


def assert_same_tree(a: Path, b: Path) -> None:
    assert treehash(a) == treehash(b), f"trees differ at {tree_diff(a, b)}"


def expected_stages(*, replay: bool, rerun: bool = False) -> list[str]:
    names = [s.name for s in stages.pipeline(replay=replay)]
    if rerun:
        at = names.index("verify") + 1
        names[at:at] = list(refresh.RERUN_AFTER_VERIFY)
    return names


# --- replay determinism ---------------------------------------------------------------------------


def test_replay_twice_gives_identical_build(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, synth_store: Path
) -> None:
    synth_stages.fail_l3 = True  # the rerun after "verify" is part of what must be stable
    state_before, store_before = treehash(synth_state), treehash(synth_store)
    a = runner.run("a", synth_state, OCT)
    b = runner.run("b", synth_state, OCT)
    assert a.ok, a.failures
    assert b.ok, b.failures
    assert_same_tree(a.build, b.build)
    assert treehash(synth_state) == state_before, "refresh wrote to state/"
    assert treehash(synth_store) == store_before, "a replay changed the store"
    assert a.stages == b.stages == tuple(expected_stages(replay=True, rerun=True))
    assert sorted(p.name for p in (a.build / "state").iterdir()) == sorted(
        state.STATE_FILES.values()
    )


def test_replay_into_the_same_build_is_idempotent(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    first = runner.run("same", synth_state, OCT).build
    before = treehash(first)
    # Leftovers a stage must never read: an old L3 exclusion, a stray state file.
    stageio.dump_stage(
        runner.paths("same", synth_state),
        "l3",
        {
            "x": L3Result("x", "failed", OCT, None, None, None, (None, None), None, None),
        },
    )
    (first / "state" / "stray.json").write_text("{}\n")
    second = runner.run("same", synth_state, OCT).build
    assert treehash(second) == before, tree_diff(first, second)
    assert synth_stages.rank_saw == [frozenset(), frozenset()]


def test_replay_of_a_live_run_gives_its_build(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    live = runner.run("live", synth_state, DEC, replay=False)
    replayed = runner.run("replay", synth_state, DEC)
    assert live.ok
    assert replayed.ok
    assert_same_tree(live.build, replayed.build)
    assert live.stages == tuple(expected_stages(replay=False))
    assert replayed.stages == tuple(expected_stages(replay=True))


# --- stages, fetcher and the rerun -------------------------------------------------------------


def test_live_run_fetches_first_and_only_network_stages_get_the_fetcher(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    result = runner.run("live", synth_state, DEC, replay=False)
    assert synth_stages.names()[0] == "fetch" == result.stages[0]
    for call in synth_stages.calls:
        assert call.fetcher == (call.stage in NETWORK_STAGES), call
    assert result.stale == ()


def test_replay_never_fetches_and_gives_no_stage_a_fetcher(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    runner.run("replay", synth_state, OCT)
    assert "fetch" not in synth_stages.names()
    assert not any(call.fetcher for call in synth_stages.calls)


def test_new_l3_exclusions_rerun_rank_and_membership_once(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    synth_stages.fail_l3 = True
    result = runner.run("rerun", synth_state, OCT)
    assert result.stages == tuple(expected_stages(replay=True, rerun=True))
    first, second = synth_stages.rank_saw
    assert first == frozenset()
    assert len(second) == 1
    catalog = jsonio.load(result.build / "catalog.json")
    assert catalog["excluded"] == sorted(second)


def test_no_rerun_without_exclusions(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    result = runner.run("plain", synth_state, OCT)
    assert result.stages == tuple(expected_stages(replay=True))
    assert synth_stages.rank_saw == [frozenset()]


def test_unapplied_alias_rows_are_applied_and_the_stages_from_universe_rerun(
    runner: Runner,
    synth_stages: SynthStages,
    synth_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rename accepted this run must reach the universe before any id is minted for it."""
    from tff_catalog import aliases

    table: list[str] = []  # stands in for data/aliases.csv

    def synth_aliases(ctx: stages.StageContext) -> None:
        synth_stages._generic("aliases", ctx)
        waiting = 0 if table else 1  # the first pass uses a row the table lacks
        jsonio.dump({"counts": {"unapplied": waiting}}, ctx.paths.queues / aliases.QUEUE_FILE)

    def apply(ctx: stages.StageContext) -> aliases.Applied:
        table.append("row")
        jsonio.dump({"counts": {"unapplied": 0}}, ctx.paths.queues / aliases.QUEUE_FILE)
        return aliases.Applied(rows=1, added=1, replaced=0, queued=0)

    monkeypatch.setattr(
        SynthStages, "_aliases", lambda self, ctx: synth_aliases(ctx), raising=False
    )
    monkeypatch.setattr(aliases, "apply_accepted", apply)
    result = runner.run("aliases", synth_state, OCT)
    assert result.ok
    assert table == ["row"]
    names = expected_stages(replay=True)
    span = names[names.index("universe") : names.index("aliases") + 1]
    at = names.index("aliases") + 1
    assert result.stages == tuple(names[:at] + span + names[at:])


# --- next state ------------------------------------------------------------------------------------


def test_next_state_matches_the_stub_pipeline(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, tmp_path: Path
) -> None:
    """Every part as the stages wrote it; only run_history (refresh's own part) differs."""
    for day in (OCT, NOV):
        ours = runner.run(f"ours-{day}", synth_state, day).build
        stub_paths = Paths.for_root(
            ROOT, state=synth_state, store=runner.store, build=tmp_path / f"stub-{day}"
        )
        stub = synth.stub_refresh(stub_paths, day, from_snapshots=day).build
        assert tree_diff(ours / "state", stub / "state") == ["run_history.json"]
        ours_catalog = jsonio.load(ours / "catalog.json")
        assert ours_catalog.pop("excluded") == []
        assert ours_catalog == jsonio.load(stub / "catalog.json")


def test_run_history_entry(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, synth_store: Path
) -> None:
    result = runner.run("nov", synth_state, NOV)
    history = state.load_state(result.build / "state").run_history
    assert [h["run_date"] for h in history] == [S0_DAY.isoformat(), NOV.isoformat()]
    entry = history[-1]
    paths = runner.paths("nov", synth_state)
    assert entry == {
        "run_date": NOV.isoformat(),
        "merged_pr": None,
        "code_commit": refresh.code_commit(ROOT),
        "config_sha256": config_hash(load_config(paths)),
        "snapshots": {
            synth.INSTALLS: NOV.isoformat(),
            synth.PACKAGES: OCT.isoformat(),  # no November snapshot: stale
            synth.UNIVERSE: NOV.isoformat(),
        },
    }
    assert result.stale == (synth.PACKAGES,)
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    if head.returncode == 0:
        assert entry["code_commit"] == head.stdout.strip()


def test_merged_run_then_next_month(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, tmp_path: Path
) -> None:
    """Merging a run moves its entry into state; replaying the same day replaces it."""
    october = runner.run("oct", synth_state, OCT).build
    merged = state.apply_state(synth_state, october / "state", tmp_path / "merged")
    again = runner.run("oct-again", merged, OCT).build
    days = [h["run_date"] for h in state.load_state(again / "state").run_history]
    assert days == [S0_DAY.isoformat(), OCT.isoformat()]
    november = runner.run("nov", merged, NOV).build
    days = [h["run_date"] for h in state.load_state(november / "state").run_history]
    assert days == [S0_DAY.isoformat(), OCT.isoformat(), NOV.isoformat()]
    baseline = november / "stage" / "records" / f"{synth.INSTALLS}@{S0_DAY.isoformat()}.jsonl"
    assert baseline.is_file(), "parse did not find the baseline pointer in run_history"


# --- failures ----------------------------------------------------------------------------------------


def test_hard_failure_stops_the_run_and_fails_the_command(
    runner: Runner,
    synth_stages: SynthStages,
    synth_state: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    synth_stages.hard_failures = (
        Failure("schema", "catalog.json is invalid"),
        Failure("ineligible", "ranked", "synth-icons"),
    )
    result = runner.run("failed", synth_state, OCT)
    assert not result.ok
    assert result.failures == tuple(str(f) for f in synth_stages.hard_failures)
    assert result.stages[-1] == "validate"
    assert "review" in synth_stages.names()  # review runs first, so its report is scanned too
    assert len(list((result.build / "state").iterdir())) == len(state.STATE_FILES)

    paths = runner.paths("cli", synth_state)
    monkeypatch.setattr(Paths, "from_env", classmethod(lambda cls, root=None, env=None: paths))
    assert cli.main(["refresh", "--from-snapshots", OCT.isoformat()]) == 1
    err = capsys.readouterr().err
    assert "tff-catalog refresh: ineligible [synth-icons]: ranked" in err


def test_failure_lines() -> None:
    listed = ValidationFailed([Failure("schema", "bad field"), Failure("gate", "ranked", "inter")])
    assert refresh.failure_lines(listed) == ("schema: bad field", "gate [inter]: ranked")

    class Plain(ValidationFailed):
        def __init__(self, text: str) -> None:
            RuntimeError.__init__(self, text)

    assert refresh.failure_lines(Plain("one\n\n  two  \n")) == ("one", "two")
    assert refresh.failure_lines(Plain("")) == ("Plain",)


def test_live_crash_propagates_and_is_recorded(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, synth_store: Path
) -> None:
    synth_stages.crash_in = "map"
    with pytest.raises(RuntimeError, match="boom in map"):
        runner.run("crash", synth_state, DEC, replay=False)
    manifest = jsonio.load(synth_store / "_runs" / f"{DEC.isoformat()}.json")
    assert manifest["error"] == "RuntimeError: boom in map"
    assert manifest["stages"] == synth_stages.names()[:-1]


def test_replay_crash_writes_no_run_manifest(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, synth_store: Path
) -> None:
    synth_stages.crash_in = "map"
    with pytest.raises(RuntimeError):
        runner.run("crash", synth_state, OCT)
    assert not (synth_store / "_runs").exists()


def test_live_run_writes_the_run_manifest(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, synth_store: Path
) -> None:
    result = runner.run("live", synth_state, DEC, replay=False)
    manifest = jsonio.load(synth_store / "_runs" / f"{DEC.isoformat()}.json")
    snapshots = {source: DEC.isoformat() for source in (*synth.SOURCES, "font_facts")}
    assert manifest["snapshots"] == snapshots
    assert set(manifest["sizes"]) == set(snapshots)
    assert all(manifest["sizes"][source] > 0 for source in synth.SOURCES)
    assert manifest["stages"] == list(result.stages)
    assert manifest["failures"] == []
    assert manifest["error"] is None
    assert manifest["code_commit"] == refresh.code_commit(ROOT)
    history = state.load_state(result.build / "state").run_history
    assert history[-1]["snapshots"] == snapshots


# --- guards ---------------------------------------------------------------------------------------


def test_refuses_a_build_state_that_overlaps_state(
    runner: Runner, synth_stages: SynthStages, synth_store: Path, tmp_path: Path
) -> None:
    build = tmp_path / "b" / "build"
    precious = build / "state" / "ids.json"
    jsonio.dump({"keep": True}, precious)
    paths = Paths.for_root(ROOT, state=build / "state", store=synth_store, build=build)
    with pytest.raises(ValueError, match="never writes state/"):
        refresh.refresh(paths.with_(config=runner.config), OCT, from_snapshots=OCT)
    assert jsonio.load(precious) == {"keep": True}
    assert synth_stages.calls == []


def test_refuses_a_build_stage_that_overlaps_state(
    runner: Runner, synth_stages: SynthStages, synth_store: Path, tmp_path: Path
) -> None:
    """build/stage/ is emptied too, so state/ may not live under it either."""
    build = tmp_path / "b" / "build"
    precious = build / "stage" / "committed" / "ids.json"
    jsonio.dump({"keep": True}, precious)
    paths = Paths.for_root(ROOT, state=precious.parent, store=synth_store, build=build)
    with pytest.raises(ValueError, match=r"build/stage .* overlaps state/"):
        refresh.refresh(paths.with_(config=runner.config), OCT, from_snapshots=OCT)
    assert jsonio.load(precious) == {"keep": True}
    assert synth_stages.calls == []


def test_bad_state_stops_the_run_before_build_is_emptied(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, tmp_path: Path
) -> None:
    build = runner.run("kept", synth_state, OCT).build
    before = treehash(build)
    broken = tmp_path / "broken-state"
    shutil.copytree(synth_state, broken)
    (broken / "run_history.json").write_text("{}\n", encoding="utf-8")  # not an array
    synth_stages.calls.clear()
    with pytest.raises(ValueError, match=r"run_history\.json"):
        runner.run("kept", broken, OCT)
    assert treehash(build) == before, "the previous build was emptied before state/ was checked"
    assert synth_stages.calls == []


def test_live_hard_failure_is_recorded(
    runner: Runner, synth_stages: SynthStages, synth_state: Path, synth_store: Path
) -> None:
    synth_stages.hard_failures = (Failure("schema", "catalog.json is invalid"),)
    result = runner.run("failed-live", synth_state, DEC, replay=False)
    assert not result.ok
    manifest = jsonio.load(synth_store / "_runs" / f"{DEC.isoformat()}.json")
    assert manifest["failures"] == ["schema: catalog.json is invalid"]
    assert manifest["error"] is None
    assert manifest["stages"] == list(result.stages)
    assert manifest["stages"][-1] == "validate"


def test_l3_excluded_is_verifys_definition(runner: Runner, synth_state: Path) -> None:
    from tff_catalog import license_l3

    paths = runner.paths("l3", synth_state)
    assert refresh.l3_excluded(paths) == license_l3.exclusions(paths) == frozenset()
    stageio.dump_stage(
        paths,
        "l3",
        {
            fid: L3Result(fid, level, OCT, None, None, None, (None, None), None, None)
            for fid, level in (("a", "failed"), ("b", "L3"), ("c", "ruling"))
        },
    )
    assert refresh.l3_excluded(paths) == license_l3.exclusions(paths) == frozenset({"a"})


def test_conflicting_replay_dates(runner: Runner, synth_state: Path) -> None:
    options = stages.RunOptions(from_snapshots=OCT)
    with pytest.raises(ValueError, match="from_snapshots"):
        refresh.refresh(runner.paths("x", synth_state), OCT, NOV, options=options)


def test_options_replay_date_is_used(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    options = stages.RunOptions(from_snapshots=OCT)
    result = refresh.refresh(runner.paths("opt", synth_state), OCT, options=options)
    assert "fetch" not in result.stages


def test_only_run_updates_the_previous_build(
    runner: Runner, synth_stages: SynthStages, synth_state: Path
) -> None:
    """``--only`` keeps build/stage and build/state, so parse updates one source in place."""
    full = runner.run("only", synth_state, NOV).build
    before = treehash(full)
    options = stages.RunOptions(only=(synth.INSTALLS,), from_snapshots=NOV)
    result = refresh.refresh(runner.paths("only", synth_state), NOV, options=options)
    assert treehash(result.build) == before, tree_diff(full, result.build)
    assert result.stale == (synth.PACKAGES,), "the other sources' stale entries were kept"


def test_needs_the_store(runner: Runner, synth_state: Path) -> None:
    from tff_catalog.paths import StoreNotConfigured

    paths = runner.paths("nostore", synth_state).with_(store=None)
    with pytest.raises(StoreNotConfigured):
        refresh.refresh(paths, OCT, from_snapshots=OCT)


# --- the workflow -----------------------------------------------------------------------------------

WORKFLOWS = ROOT / ".github" / "workflows"
SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _workflow(name: str) -> dict[Any, Any]:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(doc: dict[Any, Any]) -> list[dict[str, Any]]:
    return [step for job in doc["jobs"].values() for step in job["steps"]]


def test_workflow_triggers_and_settings() -> None:
    doc = _workflow("refresh.yml")
    on = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare "on" as true
    assert on["schedule"] == [{"cron": "17 6 3 * *"}]
    assert "workflow_dispatch" in on
    assert doc["concurrency"]["group"]
    assert doc["concurrency"]["cancel-in-progress"] is False
    assert doc["permissions"] == {"contents": "read"}
    (job,) = doc["jobs"].values()
    assert job["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
        "issues": "write",
        "actions": "write",
    }
    steps = _steps(doc)
    uv = [s for s in steps if str(s.get("uses", "")).startswith("astral-sh/setup-uv@")]
    assert uv
    assert all(s["with"]["enable-cache"] is True for s in uv)
    (store,) = [s for s in steps if s.get("with", {}).get("path") == "_store"]
    assert store["with"]["repository"] == "byronshock/trulyfreefonts-data"
    assert store["with"]["ssh-key"] == "${{ secrets.DATA_STORE_KEY }}"
    assert job["env"]["TFF_STORE"] == "${{ github.workspace }}/_store"


def test_workflow_reuses_ci_pins() -> None:
    pins = {s["uses"] for s in _steps(_workflow("ci.yml")) if "uses" in s}
    used = {s["uses"] for s in _steps(_workflow("refresh.yml")) if "uses" in s}
    assert used
    assert all(SHA_PIN.fullmatch(u) for u in used), used
    assert used <= pins, f"pinned differently from ci.yml: {used - pins}"


def test_workflow_opens_the_pull_request_dispatches_ci_and_reports_failures() -> None:
    doc = _workflow("refresh.yml")
    (job,) = doc["jobs"].values()
    assert job["env"]["BRANCH"] == "refresh/monthly"
    runs = {s.get("name", ""): s for s in _steps(doc)}
    pr = runs["Open or update the refresh pull request"]
    assert pr["if"] == "${{ github.ref == 'refs/heads/main' }}"
    for needle in ("apply_state", "gh pr create", "gh pr edit", "gh workflow run ci.yml"):
        assert needle in pr["run"], needle
    issue = runs["Report the failure in an issue"]
    assert issue["if"] == "${{ failure() }}"
    assert "gh issue create" in issue["run"]
    replay = runs["Replay offline from a clean checkout"]
    assert "--from-snapshots" in replay["run"]
    assert "diff -r" in replay["run"]


def test_workflow_scripts_parse_and_take_no_expressions() -> None:
    """Every run: script is valid bash, and ${{ }} reaches scripts only through env."""
    bash = shutil.which("bash")
    for step in _steps(_workflow("refresh.yml")):
        script = step.get("run")
        if script is None:
            continue
        assert "${{" not in script, f"use env for expressions: {step.get('name', script[:40])}"
        if bash is not None:
            done = subprocess.run([bash, "-n"], input=script, capture_output=True, text=True)
            assert done.returncode == 0, (step.get("name"), done.stderr)


# The contexts GitHub allows in each key ("Context availability" in the Actions docs);
# anything else makes GitHub reject the whole file before the job starts.
KEY_CONTEXTS: dict[str, frozenset[str]] = {
    "concurrency": frozenset({"github", "inputs", "vars"}),
    "env": frozenset({"github", "secrets", "inputs", "vars"}),
    "jobs.env": frozenset({"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}),
    "jobs.if": frozenset({"github", "needs", "vars", "inputs"}),
    "jobs.runs-on": frozenset({"github", "needs", "strategy", "matrix", "vars", "inputs"}),
}
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
CONTEXT_NAME = re.compile(r"(?<![\w.])([A-Za-z_][\w-]*)\s*[.\[]")


def _contexts(value: object) -> set[str]:
    """The contexts that ``value``'s ``${{ }}`` expressions read (quoted strings ignored)."""
    found: set[str] = set()
    for expression in EXPRESSION.findall(str(value)):
        found |= set(CONTEXT_NAME.findall(re.sub(r"'[^']*'", "''", expression)))
    return found


def test_contexts_helper() -> None:
    assert _contexts("${{ runner.temp }}/raw") == {"runner"}
    assert _contexts("${{ github.ref == 'refs/heads/x.y' && inputs['a'] }}") == {"github", "inputs"}
    assert _contexts("plain") == set()


def test_workflow_keys_read_only_contexts_available_there() -> None:
    doc = _workflow("refresh.yml")
    places: list[tuple[str, object]] = [(key, doc.get(key)) for key in ("concurrency", "env")]
    for job in doc["jobs"].values():
        places += [(f"jobs.{key}", job.get(key)) for key in ("env", "if", "runs-on")]
    for key, value in places:
        extra = _contexts(value) - KEY_CONTEXTS[key]
        assert not extra, f"{key} reads {sorted(extra)}, not available there"


# --- the workflow's scripts, run with stand-ins for git, gh, uv, sudo and diff -----------------

STUBS: dict[str, str] = {
    # git diff exits 0 when there is no difference.
    "git": """case "$1 $2" in
  "diff --cached") exit "${STUB_STAGED_SAME:-1}" ;;
  "diff --quiet") exit "${STUB_WORKFLOWS_SAME:-0}" ;;
  "fetch --quiet") exit "${STUB_BRANCH_EXISTS:-1}" ;;
esac""",
    "gh": """case "$1 $2" in
  "pr list") printf '%s\\n' "${STUB_PR:-}" ;;
  "issue list") printf '%s\\n' "${STUB_ISSUE:-}" ;;
esac""",
    "sudo": 'exit "${STUB_SUDO:-1}"',
    "diff": 'exit "${STUB_DIFF:-0}"',
    "uv": "",
}


@dataclass(frozen=True)
class StepRun:
    code: int
    calls: list[str]  # "<command> <args>" of every stand-in called, in order
    out: str
    temp: Path  # $RUNNER_TEMP
    files: dict[str, str]  # summary, output, env: what the step appended

    def called(self, *prefixes: str) -> list[str]:
        return [c for c in self.calls if c.startswith(prefixes)]


def run_step(name: str, tmp: Path, **env: str) -> StepRun:
    """Run the named step's script as Actions does (``bash -e``), in a fake workspace."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    (script,) = [s["run"] for s in _steps(_workflow("refresh.yml")) if s.get("name") == name]
    stubs, workspace, temp = tmp / "bin", tmp / "workspace", tmp / "runner-temp"
    for directory in (
        stubs,
        workspace / ".git" / "info",
        workspace / "data" / "alias-seeds",
        temp / "replay" / "data" / "alias-seeds",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (workspace / "data" / "aliases.csv").write_text("alias\n", encoding="utf-8")
    (workspace / "data" / "alias-seeds" / "nerd.csv").write_text("alias\n", encoding="utf-8")
    for command, body in STUBS.items():
        stub = stubs / command
        stub.write_text(
            f'#!/bin/sh\nprintf "%s\\n" "{command} $*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8"
        )
        stub.chmod(0o755)
    files = {k: tmp / f"{k}.txt" for k in ("calls", "summary", "output", "env")}
    for file in files.values():
        file.touch()
    base = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(tmp),
        "LANG": "C.UTF-8",
        "STUB_LOG": str(files["calls"]),
        "GITHUB_STEP_SUMMARY": str(files["summary"]),
        "GITHUB_OUTPUT": str(files["output"]),
        "GITHUB_ENV": str(files["env"]),
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_REPOSITORY": "owner/repo",
        "RUNNER_TEMP": str(temp),
        "BRANCH": "refresh/monthly",
        "RUN_URL": "https://github.example/owner/repo/actions/runs/1",
        "RUN_DATE": OCT.isoformat(),
        "FROM_SNAPSHOTS": "",
    }
    done = subprocess.run(
        [bash, "-e", "-c", script],
        cwd=workspace,
        env=base | env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    return StepRun(
        done.returncode,
        files["calls"].read_text(encoding="utf-8").splitlines(),
        done.stdout + done.stderr,
        temp,
        {k: files[k].read_text(encoding="utf-8") for k in ("summary", "output", "env")},
    )


def test_step_prepare(tmp_path: Path) -> None:
    step = run_step("Prepare", tmp_path / "live")
    assert step.code == 0, step.out
    assert re.fullmatch(r"run_date=\d{4}-\d{2}-\d{2}\n", step.files["output"])
    assert step.files["env"] == f"TFF_RAW={step.temp}/raw\n"
    exclude = tmp_path / "live" / "workspace" / ".git" / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8") == "/_store/\n"
    assert step.called("git -C _store config user.name")

    step = run_step("Prepare", tmp_path / "replay", FROM_SNAPSHOTS="2026-09-03")
    assert step.files["output"] == "run_date=2026-09-03\n"

    step = run_step("Prepare", tmp_path / "bad", FROM_SNAPSHOTS="2026-9-3")
    assert step.code == 1
    assert "::error::from_snapshots must be YYYY-MM-DD" in step.out


def test_step_refresh(tmp_path: Path) -> None:
    step = run_step("Refresh", tmp_path / "live")
    assert step.code == 0, step.out
    assert step.calls == [f"uv run --locked tff-catalog -v refresh --date {OCT}"]
    step = run_step(
        "Refresh", tmp_path / "replay", RUN_DATE=str(S0_DAY), FROM_SNAPSHOTS=str(S0_DAY)
    )
    assert step.calls == [
        f"uv run --locked tff-catalog -v refresh --date {S0_DAY} --from-snapshots {S0_DAY}"
    ]


def test_step_replay(tmp_path: Path) -> None:
    name = "Replay offline from a clean checkout"
    replay = f"tff-catalog -v refresh --date {OCT} --from-snapshots {OCT}"

    step = run_step(name, tmp_path / "no-namespace")
    assert step.code == 0, step.out
    assert "::warning::could not cut the network" in step.out
    assert step.called("git worktree add --detach")
    assert step.called("uv run") == [f"uv run --locked --offline {replay}"]
    assert "Replay matched" in step.files["summary"]
    # The replay starts from the alias table and seeds the live run left.
    replay_data = step.temp / "replay" / "data"
    assert (replay_data / "aliases.csv").read_text(encoding="utf-8") == "alias\n"
    assert (replay_data / "alias-seeds" / "nerd.csv").is_file()

    step = run_step(name, tmp_path / "namespace", STUB_SUDO="0")
    assert step.code == 0, step.out
    assert "::warning::" not in step.out
    assert step.called("uv run") == []
    (inside,) = [c for c in step.called("sudo") if replay in c]
    assert inside.startswith("sudo -n --preserve-env unshare --net -- sudo -n --preserve-env -u ")

    step = run_step(name, tmp_path / "differs", STUB_DIFF="1")
    assert step.code == 1
    assert f"::error::the replay of {OCT} gave a different build/" in step.out
    assert "Replay matched" not in step.files["summary"]


def test_step_pull_request(tmp_path: Path) -> None:
    name = "Open or update the refresh pull request"
    head = "git push --force origin HEAD:refs/heads/refresh/monthly"
    dispatch = "gh workflow run ci.yml --repo owner/repo --ref refresh/monthly"

    step = run_step(name, tmp_path / "unchanged", STUB_STAGED_SAME="0")
    assert step.code == 0, step.out
    assert "Nothing changed against main" in step.files["summary"]
    assert step.called("git commit", "git push", "gh pr", "gh workflow") == []

    first = tmp_path / "first"
    listed = "none"
    if shutil.which("jq", path="/usr/bin:/bin") is not None:  # the step reads stale.json with jq
        stale = {"a_src": {"dropped": False}, "b_src": {"dropped": True}}
        jsonio.dump(stale, first / "workspace" / "build" / "stage" / "stale.json")
        listed = "a_src, b_src (dropped)"
    step = run_step(name, first)
    assert step.code == 0, step.out
    assert [c.split(" -")[0] for c in step.calls if not c.startswith("gh auth")] == [
        "git switch",
        "uv run",
        "git add",
        "git diff",
        "git",  # git -c user.name=... commit
        "git fetch",
        "git push",
        "gh pr list",
        "gh pr create",
        "gh workflow run ci.yml",
    ]
    assert "apply_state" in step.called("uv run")[0]
    assert step.called("git add") == [
        "git add --all -- state build data/aliases.csv data/alias-seeds"
    ]
    assert step.called("git push") == [head]
    assert step.called("gh workflow") == [dispatch]
    body = (step.temp / "pr-body.md").read_text(encoding="utf-8")
    assert f"The monthly catalog refresh of {OCT}" in body
    assert f"Stale or dropped sources: {listed}." in body

    step = run_step(name, tmp_path / "open", STUB_PR="12", STUB_BRANCH_EXISTS="0")
    assert step.code == 0, step.out
    (edit,) = step.called("gh pr edit")
    assert edit.startswith("gh pr edit 12 ")
    assert step.called("gh pr create") == []
    assert step.called("git push") == [head], "same workflows: the branch is updated in place"
    assert "Stale or dropped sources: none." in (step.temp / "pr-body.md").read_text()

    step = run_step(
        name, tmp_path / "workflows", STUB_PR="", STUB_BRANCH_EXISTS="0", STUB_WORKFLOWS_SAME="1"
    )
    assert step.code == 0, step.out
    assert step.called("git push") == ["git push origin --delete refresh/monthly", head]
    assert step.called("gh pr create")
    assert step.called("gh workflow") == [dispatch]


def test_step_failure_issue(tmp_path: Path) -> None:
    name = "Report the failure in an issue"
    step = run_step(name, tmp_path / "new")
    assert step.code == 0, step.out
    (create,) = step.called("gh issue create")
    assert "--title Monthly refresh failed" in create
    body = (step.temp / "issue-body.md").read_text(encoding="utf-8")
    assert f"The refresh of {OCT} failed: https://github.example/owner/repo/actions/runs/1" in body

    step = run_step(name, tmp_path / "open", STUB_ISSUE="5", RUN_DATE="")
    assert step.called("gh issue create") == []
    assert step.called("gh issue comment 5 ")
    assert "The refresh of this month failed" in (step.temp / "issue-body.md").read_text()


# --- the watchdog ----------------------------------------------------------------------------------

WATCHDOG = ROOT / "ops" / "refresh-watchdog" / "tff-refresh-watchdog"
NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def watchdog() -> Iterator[types.ModuleType]:
    """The watchdog script as a module (it has no .py name; the VPS runs it directly)."""
    loader = importlib.machinery.SourceFileLoader("tff_refresh_watchdog", str(WATCHDOG))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module  # dataclasses look their module up
    writes_bytecode, sys.dont_write_bytecode = (
        sys.dont_write_bytecode,
        True,
    )  # no __pycache__ in ops/
    try:
        loader.exec_module(module)
        yield module
    finally:
        sys.dont_write_bytecode = writes_bytecode
        del sys.modules[loader.name]


def _seen(watchdog: types.ModuleType, refresh_days: float, main_days: float, wf: str | None) -> Any:
    return watchdog.Seen(NOW - timedelta(days=refresh_days), NOW - timedelta(days=main_days), wf)


def test_watchdog_thresholds(watchdog: types.ModuleType) -> None:
    assert watchdog.parse_time("2026-12-20T12:00:00Z") == NOW
    assert watchdog.parse_time("2026-12-20T12:00") == NOW, "no zone means UTC"
    assert watchdog.parse_time("2026-12-20T13:00:00+01:00") == NOW
    assert watchdog.check(NOW, _seen(watchdog, 34.9, 49.9, "active")) == []
    (w,) = watchdog.check(NOW, _seen(watchdog, 35.5, 10, "active"))
    assert "refresh pull request" in w
    (w,) = watchdog.check(NOW, _seen(watchdog, 3, 50.5, "active"))
    assert "main" in w
    (w,) = watchdog.check(NOW, _seen(watchdog, 3, 3, "disabled_inactivity"))
    assert "disabled_inactivity" in w
    assert len(watchdog.check(NOW, watchdog.Seen(None, None, None))) == 3


@dataclass
class FakeGitHub:
    """Canned answers in place of GitHub's API; records the issues it opens."""

    repo: str
    token: str | None
    last: tuple[datetime | None, datetime | None, str | None] = (None, None, None)
    open_number: int | None = None
    opened: list[str] = field(default_factory=list)

    def last_refresh(self) -> datetime | None:
        return self.last[0]

    def last_main_commit(self) -> datetime | None:
        return self.last[1]

    def workflow_state(self) -> str | None:
        return self.last[2]

    def open_issue_number(self) -> int | None:
        return self.open_number

    def open_issue(self, body: str) -> int:
        self.opened.append(body)
        return 7


def _fake(
    last: tuple[datetime | None, datetime | None, str | None], open_number: int | None = None
) -> tuple[type, list[FakeGitHub]]:
    made: list[FakeGitHub] = []

    def make(repo: str, token: str | None) -> FakeGitHub:
        gh = FakeGitHub(repo, token, last, open_number)
        made.append(gh)
        return gh

    return cast("type", make), made


def test_watchdog_opens_one_issue(
    watchdog: types.ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = tmp_path / "token"
    token.write_text("github_pat_x\n")
    stale = (NOW - timedelta(days=40), NOW - timedelta(days=40), "active")
    fake, made = _fake(stale)
    argv = ["--now", "2026-12-20T12:00:00Z", "--token-file", str(token)]
    assert watchdog.main(argv, github=fake) == 1
    assert made[0].token == "github_pat_x"
    assert len(made[0].opened) == 1
    assert "gh workflow run refresh.yml" in made[0].opened[0]
    assert "opened issue #7" in capsys.readouterr().out

    fake, made = _fake(stale, open_number=3)
    assert watchdog.main(argv, github=fake) == 1
    assert made[0].opened == []
    assert "#3 is still open" in capsys.readouterr().out

    fake, made = _fake(stale)
    assert watchdog.main([*argv, "--dry-run"], github=fake) == 1
    assert made[0].opened == []


def test_watchdog_quiet_when_fine_and_journal_only_without_a_token(
    watchdog: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fine = (NOW - timedelta(days=17), NOW - timedelta(days=2), "active")
    fake, made = _fake(fine)
    assert watchdog.main(["--now", "2026-12-20T12:00:00Z"], github=fake) == 0
    assert "ok: last refresh pull request 17 days ago" in capsys.readouterr().out

    credentials = tmp_path / "creds"
    credentials.mkdir()
    (credentials / "github-token").write_text("none")  # the unit's SetCredential= fallback
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    fake, made = _fake((None, NOW, "active"))
    assert watchdog.main(["--now", "2026-12-20T12:00:00Z"], github=fake) == 1
    assert made[0].token is None
    assert made[0].opened == []
    assert "journal only" in capsys.readouterr().out


def test_watchdog_reads_githubs_answers(watchdog: types.ModuleType) -> None:
    """The API calls against canned answers in GitHub's shapes (no network)."""
    answers: dict[str, tuple[int, Any]] = {
        "/pulls?": (200, [{"created_at": "2026-11-03T06:30:00Z"}]),
        "/branches/refresh/monthly": (
            200,
            {"commit": {"commit": {"committer": {"date": "2026-12-03T06:40:00Z"}}}},
        ),
        "/commits/main": (200, {"commit": {"committer": {"date": "2026-12-10T09:00:00Z"}}}),
        "/actions/workflows/refresh.yml": (200, {"state": "disabled_inactivity"}),
        "/issues?": (
            200,
            [
                {"number": 4, "title": watchdog.ISSUE_TITLE, "pull_request": {}},
                {"number": 5, "title": "Something else"},
            ],
        ),
        "/issues": (201, {"number": 6}),
    }
    posted: list[tuple[str, Any]] = []

    class Canned(watchdog.GitHub):
        def request(self, method: str, path: str, body: object = None) -> tuple[int, Any]:
            if method == "POST":
                posted.append((path, body))
            for key, answer in answers.items():
                if key in path and (method == "POST") == (answer[0] == 201):
                    return answer
            return 404, None

    gh = Canned(watchdog.REPO, "token")
    assert gh.last_refresh() == datetime(2026, 12, 3, 6, 40, tzinfo=UTC)
    assert gh.last_main_commit() == datetime(2026, 12, 10, 9, 0, tzinfo=UTC)
    assert gh.workflow_state() == "disabled_inactivity"
    assert gh.open_issue_number() is None, "a pull request is not the watchdog's issue"
    assert gh.open_issue("body") == 6
    assert posted == [
        (f"/repos/{watchdog.REPO}/issues", {"title": watchdog.ISSUE_TITLE, "body": "body"})
    ]
    del answers["/branches/refresh/monthly"]
    assert gh.last_refresh() == datetime(2026, 11, 3, 6, 30, tzinfo=UTC)


def test_watchdog_unit_matches_the_script(watchdog: types.ModuleType) -> None:
    unit = (WATCHDOG.parent / "tff-refresh-watchdog.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/sbin/tff-refresh-watchdog\n" in unit
    assert f"LoadCredential={watchdog.CREDENTIAL}:" in unit
    assert f"SetCredential={watchdog.CREDENTIAL}:{watchdog.NO_TOKEN}\n" in unit
    timer = (WATCHDOG.parent / "tff-refresh-watchdog.timer").read_text(encoding="utf-8")
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer
    assert WATCHDOG.stat().st_mode & 0o111, "the script must be executable"
    assert (watchdog.REFRESH_MAX_DAYS, watchdog.MAIN_MAX_DAYS) == (35, 50)


def test_watchdog_runs_on_the_servers_python() -> None:
    """Debian 13 has Python 3.13: no newer syntax, and nothing outside the standard library."""
    source = WATCHDOG.read_text(encoding="utf-8")
    assert source.startswith("#!/usr/bin/python3 -I\n")
    tree = ast.parse(source, feature_version=(3, 13))
    imported = {
        name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
        )
    }
    assert imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names


@pytest.mark.network
def test_watchdog_reads_github(watchdog: types.ModuleType) -> None:
    """The real API calls, read-only; the run is dry and opens nothing."""
    gh = watchdog.GitHub(watchdog.REPO)
    main = gh.last_main_commit()
    assert main is not None
    assert main < datetime.now(UTC)
    assert gh.workflow_state() in (None, "active", "disabled_inactivity", "disabled_manually")
    gh.last_refresh()  # None until the first refresh pull request
    assert watchdog.main(["--dry-run"]) in (0, 1)
