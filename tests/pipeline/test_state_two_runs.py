"""Unmerged runs never advance state (design-m1 §5).

State advances only when a refresh PR is merged. So, on a synthetic store and
state S0 (``tests/helpers/synth.py``):

1. a run never writes ``state/``;
2. two runs from the same inputs give byte-identical ``build/`` trees,
   ``build/state/`` included;
3. a skipped month: the November run from the unmerged S0 equals a lone
   November run, and its counters advance once from S0, not twice;
4. merging either of two identical October runs, then running November,
   gives the same output, which differs from November on S0.

Each test runs three ways:

- ``stub``: the stub pipeline in ``synth``, with its own state reader and merge;
- ``store+state``: the same stub pipeline, but reading the store through
  ``tff_catalog.store``, choosing snapshots and writing records and the
  ``stale`` state part with the real "parse" stage (``SynthCollector`` reads
  the synthetic extracts back), and loading, writing and merging state with
  ``tff_catalog.state`` (M1 step 3);
- ``real``: the real ``refresh`` with the real collectors, xfail until M1
  step 18 (no real collector reads the synthetic store yet). When it passes,
  strict xfail fails the suite: then drop the mark and point
  ``synth.make_store``'s data at the real collectors' formats.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from tests.helpers import ROOT, synth
from tests.helpers.treehash import tree_diff, treehash

from tff_catalog import jsonio, parse, refresh, stages, state
from tff_catalog.collectors.base import Collector, CollectorBase, ParseContext
from tff_catalog.config_model import Config, RankingConfig, from_mapping, load_toml
from tff_catalog.paths import Paths
from tff_catalog.records import (
    RECORD_TYPES,
    Record,
    from_json,
    read_jsonl,
)

OCT = date(2026, 10, 3)
NOV = date(2026, 11, 3)

RANKING = from_mapping(
    RankingConfig, load_toml(ROOT / "config" / "ranking.toml"), where="ranking.toml"
)
# "parse" reads only the ranking config; the other files belong to other steps.
CONFIG = cast("Config", Config(RANKING, *([None] * 5), sources={}))  # type: ignore[arg-type]


class SynthCollector(CollectorBase):
    """Reads a synthetic snapshot's ``records.jsonl`` back into records."""

    kind = "universe"
    hosts = ("synth.example",)
    emits = tuple(RECORD_TYPES.values())

    def fetch(self, ctx: object) -> None:
        raise AssertionError("replay never fetches")

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        for row in ctx.snapshot.iter_jsonl(synth.EXTRACT):
            yield from_json(row)


# synth_installs stands in for a lifetime counter, so baselines are exercised too.
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


def store_state_refresh(
    paths: Paths,
    run_date: date,
    from_snapshots: date | None = None,
    *,
    options: object = None,
) -> refresh.RunResult:
    """``synth.stub_refresh`` on the real store, "parse" stage and state module."""
    del options
    paths = paths.with_(config=paths.build.parent / "config")
    paths.sources_config.mkdir(parents=True, exist_ok=True)
    for name in SYNTH_COLLECTORS:
        (paths.sources_config / f"{name}.toml").write_text("enabled = true\n")
    ctx = stages.make_context(
        paths,
        CONFIG,
        run_date,
        stages.RunOptions(from_snapshots=from_snapshots),
        network=False,
    )
    stages.run_stage("parse", ctx)
    used = parse.load_snapshots(paths)
    inputs = synth.Inputs(
        records={name: read_jsonl(paths.records / f"{name}.jsonl") for name in used},
        snapshots={name: s.snapshot for name, s in used.items()},
        stale=tuple(sorted(parse.load_stale(paths))),
    )
    files, catalog = synth.advance(ctx.state, inputs, run_date)
    assert state.read_part(paths, "stale") == files["stale"], "parse's stale part differs"
    nxt = state.NextState.from_state(ctx.state)
    for name, value in files.items():
        setattr(nxt, name, value)
    state.write_next_state(nxt, paths.next_state)
    jsonio.dump(catalog, paths.build / "catalog.json")
    return refresh.RunResult(
        run_date=run_date,
        build=paths.build,
        stages=synth.STUB_STAGES,
        stale=inputs.stale,
        failures=(),
        seconds=0.0,
    )


@dataclass(frozen=True, slots=True)
class Pipeline:
    refresh: Callable[..., Any]
    apply_state: Callable[..., Path]
    load_state: Callable[[Path], state.State]
    collectors: dict[str, Collector] | None = None  # patched in for collectors.discover


PIPELINES = [
    pytest.param(Pipeline(synth.stub_refresh, synth.apply_state, synth.read_state), id="stub"),
    pytest.param(
        Pipeline(store_state_refresh, state.apply_state, state.load_state, SYNTH_COLLECTORS),
        id="store+state",
    ),
    pytest.param(
        Pipeline(refresh.refresh, state.apply_state, state.load_state),
        id="real",
        # Any exception: refresh itself exists now, but no real collector reads the
        # synthetic store until M1 step 18, so the pipeline stops at "universe".
        marks=pytest.mark.xfail(
            strict=True, reason="M1 step 18: real collectors over the synthetic store"
        ),
    ),
]


@pytest.fixture(params=PIPELINES)
def pipeline(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Pipeline:
    chosen: Pipeline = request.param
    if chosen.collectors is not None:
        found = dict(sorted(chosen.collectors.items()))
        monkeypatch.setattr(parse, "discover", lambda kind=None: found)
    return chosen


@dataclass
class Runner:
    pipeline: Pipeline
    tmp: Path
    store: Path

    def run(self, name: str, state_dir: Path, day: date) -> Path:
        """Replay ``day``'s snapshots with ``state_dir`` as state; return the build directory."""
        work = self.tmp / name
        paths = Paths.for_root(
            ROOT, state=state_dir, store=self.store, build=work / "build", raw_root=work / "raw"
        )
        result = self.pipeline.refresh(paths, day, from_snapshots=day)
        assert result.ok, result.failures
        assert result.build == paths.build
        return result.build

    def merge(self, name: str, state_dir: Path, build: Path) -> Path:
        """Simulate merging a run's refresh PR onto a copy of ``state_dir``."""
        return self.pipeline.apply_state(state_dir, build / "state", self.tmp / name)


@pytest.fixture
def runner(pipeline: Pipeline, tmp_path: Path, synth_store: Path) -> Runner:
    return Runner(pipeline, tmp_path / "runs", synth_store)


def assert_same_tree(a: Path, b: Path) -> None:
    assert treehash(a) == treehash(b), f"trees differ at {tree_diff(a, b)}"


def outside(pipeline: Pipeline, state_dir: Path) -> dict[str, int]:
    """Catalog ``runs_outside`` counters by family id."""
    catalog = pipeline.load_state(state_dir).membership.get("catalog", {})
    return {fid: entry["runs_outside"] for fid, entry in catalog.items()}


def test_run_never_mutates_state(runner: Runner, synth_state: Path, synth_store: Path) -> None:
    state_before, store_before = treehash(synth_state), treehash(synth_store, exclude=["_runs"])
    runner.run("a", synth_state, OCT)
    assert treehash(synth_state) == state_before, "a run wrote to state/"
    assert treehash(synth_store, exclude=["_runs"]) == store_before, "a replay changed snapshots"


def test_two_unmerged_runs_are_identical(runner: Runner, synth_state: Path) -> None:
    a = runner.run("a", synth_state, OCT)
    b = runner.run("b", synth_state, OCT)
    assert (a / "state").is_dir(), "a run must write build/state/"
    assert_same_tree(a, b)


def test_skipped_month_advances_counters_once(runner: Runner, synth_state: Path) -> None:
    runner.run("oct", synth_state, OCT)  # never merged
    n1 = runner.run("n1", synth_state, NOV)
    n2 = runner.run("n2", synth_state, NOV)
    assert_same_tree(n1, n2)
    before = outside(runner.pipeline, synth_state)
    after = outside(runner.pipeline, n1 / "state")
    steps = {fid: n - before.get(fid, 0) for fid, n in after.items()}
    assert all(step <= 1 for step in steps.values()), f"counters advanced twice: {steps}"
    assert any(step == 1 for step in steps.values()), "no counter advanced; the data is too tame"


def test_merging_either_run_gives_the_same_next_run(runner: Runner, synth_state: Path) -> None:
    a = runner.run("a", synth_state, OCT)
    b = runner.run("b", synth_state, OCT)
    merged_a = runner.merge("merged-a", synth_state, a)
    merged_b = runner.merge("merged-b", synth_state, b)
    assert_same_tree(merged_a, merged_b)
    next_a = runner.run("next-a", merged_a, NOV)
    next_b = runner.run("next-b", merged_b, NOV)
    assert_same_tree(next_a, next_b)
    unmerged = runner.run("next-s0", synth_state, NOV)
    assert treehash(next_a) != treehash(unmerged), "merging October changed nothing in November"


def test_treehash_sees_every_change(tmp_path: Path) -> None:
    tree = tmp_path / "t"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "a.json").write_text("{}\n")
    first = treehash(tree)
    assert treehash(tree) == first
    copy = tmp_path / "copy"
    (copy / "sub").mkdir(parents=True)
    (copy / "sub" / "a.json").write_text("{}\n")
    assert treehash(copy) == first, "the hash must not depend on the tree's location"
    (tree / "sub" / "a.json").write_text("{ }\n")
    assert treehash(tree) != first
    assert tree_diff(tree, copy) == ["sub/a.json"]
    (tree / "sub" / "a.json").write_text("{}\n")
    (tree / "empty").mkdir()
    assert tree_diff(tree, copy) == ["empty"]


def test_synthetic_inputs_are_deterministic(tmp_path: Path) -> None:
    a = synth.make_store(tmp_path / "a")
    b = synth.make_store(tmp_path / "b")
    assert_same_tree(a, b)
    assert_same_tree(synth.make_state(tmp_path / "sa", a), synth.make_state(tmp_path / "sb", b))
    other = synth.make_store(tmp_path / "c", seed=synth.SEED + 1)
    assert treehash(other) != treehash(a)
    assert synth.records() == synth.records()
    assert {type(r).__name__ for r in synth.records()} == {
        "Observation",
        "UniverseRecord",
        "LicenseFact",
        "Relation",
    }


def test_stub_marks_a_missing_snapshot_stale(synth_store: Path) -> None:
    inputs = synth.load_inputs(synth_store, NOV)
    assert inputs.stale == (synth.PACKAGES,)
    assert inputs.snapshots[synth.PACKAGES] == OCT
    assert synth.load_inputs(synth_store, OCT).stale == ()


def test_store_and_state_match_the_stub(
    tmp_path: Path, synth_store: Path, synth_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real store, parse stage and state module give the stub's state, byte for byte."""
    monkeypatch.setattr(parse, "discover", lambda kind=None: dict(sorted(SYNTH_COLLECTORS.items())))
    for day in (OCT, NOV):
        stub = Runner(PIPELINES[0].values[0], tmp_path / "stub", synth_store)
        real = Runner(PIPELINES[1].values[0], tmp_path / "real", synth_store)
        a = stub.run(f"a-{day}", synth_state, day)
        b = real.run(f"b-{day}", synth_state, day)
        assert_same_tree(a / "state", b / "state")
        assert (a / "catalog.json").read_bytes() == (b / "catalog.json").read_bytes()


def test_store_and_state_parse_baselines_from_run_history(
    tmp_path: Path, synth_store: Path, synth_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse, "discover", lambda kind=None: dict(sorted(SYNTH_COLLECTORS.items())))
    runner = Runner(PIPELINES[1].values[0], tmp_path / "runs", synth_store)
    build = runner.run("oct", synth_state, OCT)
    s0_day = synth.DAYS[0].isoformat()
    assert (build / "stage" / "records" / f"{synth.INSTALLS}@{s0_day}.jsonl").is_file()
    stale = parse.load_stale(Paths.for_root(ROOT, build=build))
    assert stale == {}
    nov = runner.run("nov", synth_state, NOV)
    stale = parse.load_stale(Paths.for_root(ROOT, build=nov))
    assert list(stale) == [synth.PACKAGES]
    assert (stale[synth.PACKAGES].snapshot, stale[synth.PACKAGES].stale_runs) == (OCT, 1)
