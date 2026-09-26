"""Stage "parse" (``tff_catalog.parse``): snapshots to records, the stale policy and baselines.

The collectors here are fakes (``Counts``), patched in for ``collectors.discover``;
the store is a fresh temporary one written through ``Store.writer``.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from tests.helpers import ROOT
from tests.helpers.treehash import treehash

from tff_catalog import jsonio, parse, stageio
from tff_catalog.collectors.base import Collector, CollectorBase, ParseContext
from tff_catalog.config_model import Config, RankingConfig, from_mapping, load_toml
from tff_catalog.paths import Paths, StoreNotConfigured
from tff_catalog.records import LicenseFact, Observation, Record, SourceKey, read_jsonl, sort_key
from tff_catalog.stages import RunOptions, StageContext, make_context
from tff_catalog.state import NextState, State, read_part, write_next_state
from tff_catalog.store import Store

DAY = date(2026, 10, 3)
RANKING = from_mapping(
    RankingConfig, load_toml(ROOT / "config" / "ranking.toml"), where="ranking.toml"
)
# Only the ranking part is read by this stage; the other files belong to other agents.
CONFIG = cast("Config", Config(RANKING, *([None] * 5), sources={}))  # type: ignore[arg-type]


class Counts(CollectorBase):
    """A fake ranking collector: ``counts.json`` is ``{cask: installs}``."""

    name = "fake_counts"
    kind = "ranking"
    group = "homebrew"
    hosts = ("counts.synth.example",)
    emits = (Observation,)

    @dataclass(frozen=True, slots=True)
    class Settings:
        enabled: bool = True

    def fetch(self, ctx: object) -> None:
        raise AssertionError("parse never fetches")

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        counts = ctx.snapshot.load_json("counts.json")
        if not isinstance(counts, dict):
            raise ValueError("counts.json: expected an object")
        day = ctx.snapshot.date
        for key, n in counts.items():  # unsorted on purpose: parse_source sorts
            yield Observation(
                source=self.name,
                series="365d",
                key=SourceKey("brew-cask", key),
                value=float(n),
                unit="installs",
                start=day - timedelta(days=365),
                end=day - timedelta(days=1),
            )


def collector(name: str, **attrs: object) -> Collector:
    """A ``Counts`` collector called ``name``, with some class attributes replaced."""
    return cast("Collector", type(f"C_{name}", (Counts,), {"name": name, **attrs})())


@dataclass
class World:
    """A repository root with collector settings, a store and a state directory."""

    tmp: Path
    monkeypatch: pytest.MonkeyPatch
    found: dict[str, Collector]

    @property
    def paths(self) -> Paths:
        return Paths.for_root(
            self.tmp / "root", store=self.tmp / "store", raw_root=self.tmp / "raw"
        )

    @property
    def store(self) -> Store:
        return Store(self.tmp / "store")

    def add(self, c: Collector, settings: str = "") -> Collector:
        self.found[c.name] = c
        path = self.paths.sources_config / f"{c.name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings)
        return c

    def snapshot(self, name: str, day: date, counts: object, version: int = 1) -> None:
        with self.store.writer(name, day, version) as w:
            w.write_json("counts.json", counts)
            w.set_data_date(day - timedelta(days=1))

    def state(self, **parts: Any) -> None:
        nxt = NextState.from_state(State())
        for name, value in parts.items():
            setattr(nxt, name, value)
        write_next_state(nxt, self.paths.state)

    def ctx(self, day: date = DAY, **options: Any) -> StageContext:
        return make_context(
            self.paths,
            CONFIG,
            day,
            RunOptions(**options),
            network=False,
            log=logging.getLogger("test.parse"),
        )

    def run(self, day: date = DAY, **options: Any) -> StageContext:
        ctx = self.ctx(day, **options)
        parse.run(ctx)
        return ctx

    def records(self, name: str) -> list[Record]:
        return read_jsonl(self.paths.records / f"{name}.jsonl")

    def record_files(self) -> list[str]:
        return sorted(p.name for p in self.paths.records.glob("*.jsonl"))


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    w = World(tmp_path, monkeypatch, {})
    monkeypatch.setattr(parse, "discover", lambda kind=None: dict(sorted(w.found.items())))
    return w


def entry(day: str, **snapshots: str) -> dict[str, Any]:
    return {
        "run_date": day,
        "merged_pr": 1,
        "code_commit": "abc",
        "config_sha256": "def",
        "snapshots": snapshots,
    }


# --- parse_source -------------------------------------------------------------------------------


def test_parse_source_sorts_and_checks_the_contract(world: World) -> None:
    c = world.add(collector("fake_counts"))
    world.snapshot("fake_counts", DAY, {"font-b": 2, "font-a": 1, "font-c": 3})
    snap = world.store.snapshot("fake_counts", DAY)
    assert snap is not None
    log = logging.getLogger("t")
    recs = parse.parse_source(c, snap, c.Settings(), log)
    assert recs == sorted(recs, key=sort_key)
    assert [r.key.key for r in recs] == ["font-a", "font-b", "font-c"]

    def emits(records: list[Record]) -> Collector:
        return collector("fake_counts", parse=lambda self, ctx: iter(records))

    fact = LicenseFact(source="fake_counts", key=SourceKey("brew-cask", "x"), raw="OFL")
    with pytest.raises(TypeError, match="LicenseFact, not in emits"):
        parse.parse_source(emits([fact]), snap, c.Settings(), log)
    bad_ns = Observation("fake_counts", "365d", SourceKey("cask", "x"), 1.0, "installs", DAY, DAY)
    with pytest.raises(ValueError, match="namespace 'cask'"):
        parse.parse_source(emits([bad_ns]), snap, c.Settings(), log)
    other = Observation("other", "365d", SourceKey("brew-cask", "x"), 1.0, "installs", DAY, DAY)
    with pytest.raises(ValueError, match="source 'other'"):
        parse.parse_source(emits([other]), snap, c.Settings(), log)
    world.snapshot("fake_counts", DAY + timedelta(days=1), {}, version=2)
    newer = world.store.snapshot("fake_counts", DAY + timedelta(days=1))
    with pytest.raises(ValueError, match="newer than the collector's 1"):
        parse.parse_source(c, newer, c.Settings(), log)  # type: ignore[arg-type]


# --- the stage ------------------------------------------------------------------------------------


def test_a_fresh_run_writes_records_and_nothing_stale(world: World) -> None:
    world.add(collector("fake_counts"))
    world.add(collector("more_counts"))
    world.snapshot("fake_counts", DAY, {"font-b": 20, "font-a": 10})
    world.snapshot("more_counts", DAY, {"font-z": 5})
    ctx = world.run()
    assert world.record_files() == ["fake_counts.jsonl", "more_counts.jsonl"]
    assert [r.value for r in world.records("fake_counts")] == [10.0, 20.0]
    assert parse.load_stale(ctx.paths) == {}
    snaps = parse.load_snapshots(ctx.paths)
    assert snaps["fake_counts"] == parse.SourceSnapshot(
        snapshot=DAY,
        stale=False,
        data_date=DAY - timedelta(days=1),
        window=None,
        fetched_at=None,
        collector_version=1,
        records=2,
        baselines=(),
    )
    assert read_part(ctx.paths, "stale") == {
        "fake_counts": {"last_good": "2026-10-03", "stale_runs": 0},
        "more_counts": {"last_good": "2026-10-03", "stale_runs": 0},
    }
    first = treehash(ctx.paths.build)
    world.run()
    assert treehash(ctx.paths.build) == first, "a second parse wrote different bytes"
    assert not (ctx.paths.state).exists(), "parse wrote state/"


def test_stale_and_dropped_sources(world: World) -> None:
    world.add(collector("fake_counts"))
    world.add(collector("gone_counts"))
    world.snapshot("fake_counts", date(2026, 9, 10), {"font-a": 1})
    world.snapshot("gone_counts", date(2026, 7, 1), {"font-a": 1})
    world.state(
        stale={
            "fake_counts": {"last_good": "2026-09-10", "stale_runs": 1},
            "retired_counts": {"last_good": "2026-01-01", "stale_runs": 0},
        }
    )
    jsonio.dump_jsonl([], world.paths.records / "gone_counts.jsonl")  # from an earlier run
    ctx = world.run()
    assert world.record_files() == ["fake_counts.jsonl"]
    stale = parse.load_stale(ctx.paths)
    assert stale["fake_counts"] == parse.StaleSource(
        snapshot=date(2026, 9, 10),
        stale_of=DAY,
        data_date=date(2026, 9, 9),
        age_days=23,
        stale_runs=2,
        dropped=False,
        reason="no snapshot for 2026-10-03",
    )
    assert stale["gone_counts"] == parse.StaleSource(
        snapshot=None,
        stale_of=DAY,
        data_date=None,
        age_days=None,
        stale_runs=1,
        dropped=True,
        reason="no complete snapshot within 2 months",
    )
    assert parse.load_snapshots(ctx.paths)["fake_counts"].stale is True
    assert "gone_counts" not in parse.load_snapshots(ctx.paths)
    assert read_part(ctx.paths, "stale") == {
        "fake_counts": {"last_good": "2026-09-10", "stale_runs": 2},
        "gone_counts": {"last_good": "2026-07-01", "stale_runs": 1},
    }


def test_disabled_collectors_are_skipped_and_their_files_removed(world: World) -> None:
    world.add(collector("fake_counts"), settings="enabled = false\n")
    world.snapshot("fake_counts", DAY, {"font-a": 1})
    jsonio.dump_jsonl([], world.paths.records / "fake_counts.jsonl")
    jsonio.dump_jsonl([], world.paths.records / "fake_counts@2026-01-01.jsonl")
    ctx = world.run()
    assert world.record_files() == []
    assert parse.load_stale(ctx.paths) == {}
    assert read_part(ctx.paths, "stale") == {}


def test_a_parse_failure_falls_back_to_an_older_snapshot(world: World) -> None:
    world.add(collector("fake_counts"))
    world.snapshot("fake_counts", date(2026, 9, 20), {"font-a": 1})
    world.snapshot("fake_counts", DAY, ["not", "an", "object"])
    ctx = world.run()
    assert [r.end for r in world.records("fake_counts")] == [date(2026, 9, 19)]
    stale = parse.load_stale(ctx.paths)["fake_counts"]
    assert stale.snapshot == date(2026, 9, 20)
    assert stale.reason == "parse of 2026-10-03 failed (ValueError)"


def test_a_source_that_never_parses_is_dropped(world: World) -> None:
    world.add(collector("fake_counts"))
    world.snapshot("fake_counts", DAY, [])
    world.state(stale={"fake_counts": {"last_good": "2026-09-03", "stale_runs": 0}})
    ctx = world.run()
    assert world.record_files() == []
    stale = parse.load_stale(ctx.paths)["fake_counts"]
    assert (stale.dropped, stale.reason) == (True, "parse of 2026-10-03 failed (ValueError)")
    assert read_part(ctx.paths, "stale")["fake_counts"] == {
        "last_good": "2026-09-03",
        "stale_runs": 1,
    }


def test_a_damaged_snapshot_never_stops_the_stage(world: World) -> None:
    """A broken manifest counts as no snapshot and a broken extract as a parse failure;
    either way the source falls back and the other sources are untouched."""
    world.add(collector("fake_counts"))
    world.add(collector("more_counts"))
    world.add(collector("pinned_counts"))
    for name in ("fake_counts", "more_counts", "pinned_counts"):
        world.snapshot(name, date(2026, 9, 20), {"font-a": 1})
        world.snapshot(name, DAY, {"font-a": 2})
    root = world.store.root
    (root / "fake_counts" / DAY.isoformat() / "manifest.json").write_text("{oops")
    (root / "more_counts" / DAY.isoformat() / "counts.json").write_text('{"font-a": 9}')
    (root / "pinned_counts" / DAY.isoformat() / "manifest.json").write_text("[]")
    world.state(run_history=[entry(DAY.isoformat(), pinned_counts=DAY.isoformat())])
    ctx = world.run()
    stale = parse.load_stale(ctx.paths)
    assert sorted(stale) == ["fake_counts", "more_counts", "pinned_counts"]
    assert {name: s.snapshot for name, s in stale.items()} == dict.fromkeys(
        stale, date(2026, 9, 20)
    )
    assert stale["fake_counts"].reason == "no snapshot for 2026-10-03"
    assert stale["more_counts"].reason == "parse of 2026-10-03 failed (SnapshotCorrupt)"
    assert [r.value for r in world.records("more_counts")] == [1.0]


def test_baselines_in_months_2_to_11_are_the_earliest_merged_snapshot(world: World) -> None:
    c = world.add(collector("gh_counts", needs_baseline=True))
    days = [date(2026, 5, 3), date(2026, 6, 3), date(2026, 7, 3), date(2026, 9, 3), DAY]
    for i, d in enumerate(days):
        world.snapshot("gh_counts", d, {"font-a": 100 * (i + 1)})
    # 2026-05-03 was never merged (say, a dry run): it is no baseline, however old
    world.state(run_history=[entry(d.isoformat(), gh_counts=d.isoformat()) for d in days[1:4]])
    ctx = world.run()
    assert [s.date for s in parse.baselines(ctx, c)] == [date(2026, 6, 3)]
    assert world.record_files() == ["gh_counts.jsonl", "gh_counts@2026-06-03.jsonl"]
    assert [r.value for r in world.records("gh_counts@2026-06-03")] == [200.0]


def test_baselines_come_from_run_history(world: World) -> None:
    c = world.add(collector("gh_counts", needs_baseline=True))
    days = [date(2025, 9, 3), date(2025, 10, 3), date(2025, 11, 3), date(2026, 9, 3), DAY]
    for i, d in enumerate(days):
        world.snapshot("gh_counts", d, {"font-a": 100 * (i + 1)})

    first = world.run()  # no history: the first run divides lifetime totals by days listed
    assert world.record_files() == ["gh_counts.jsonl"]
    assert parse.baselines(first, c) == []

    history = [entry(d.isoformat(), gh_counts=d.isoformat()) for d in days[:-1]]
    world.state(run_history=history)
    ctx = world.run()
    # 2025-09-03 is over 12 months (plus slack) before the snapshot used: the 12-month difference
    assert world.record_files() == ["gh_counts.jsonl", "gh_counts@2025-10-03.jsonl"]
    assert [r.value for r in world.records("gh_counts@2025-10-03")] == [200.0]
    assert [s.date for s in parse.baselines(ctx, c)] == [date(2025, 10, 3)]
    assert parse.load_snapshots(ctx.paths)["gh_counts"].baselines == (date(2025, 10, 3),)

    # a pointer the store lacks is skipped, with a warning, for the next one
    history[1] = entry("2025-10-03", gh_counts="2025-10-04")
    world.state(run_history=history)
    ctx = world.run()
    assert world.record_files() == ["gh_counts.jsonl", "gh_counts@2025-11-03.jsonl"]


def test_a_collector_without_baselines_gets_none(world: World) -> None:
    c = world.add(collector("fake_counts"))
    world.snapshot("fake_counts", date(2026, 9, 3), {"font-a": 1})
    world.snapshot("fake_counts", DAY, {"font-a": 2})
    world.state(run_history=[entry("2026-09-03", fake_counts="2026-09-03")])
    ctx = world.run()
    assert parse.baselines(ctx, c) == []
    assert world.record_files() == ["fake_counts.jsonl"]


def test_run_history_pins_the_snapshots_of_a_replayed_run(world: World) -> None:
    world.add(collector("fake_counts"))
    world.snapshot("fake_counts", date(2026, 10, 1), {"font-a": 1})
    world.snapshot("fake_counts", DAY, {"font-a": 2})  # added after that run was merged
    world.state(run_history=[entry(DAY.isoformat(), fake_counts="2026-10-01")])
    ctx = world.run(from_snapshots=DAY)
    assert [r.value for r in world.records("fake_counts")] == [1.0]
    assert parse.load_stale(ctx.paths)["fake_counts"].snapshot == date(2026, 10, 1)


def test_the_day_is_the_replayed_one(world: World) -> None:
    world.add(collector("fake_counts"))
    world.snapshot("fake_counts", date(2026, 9, 3), {"font-a": 1})
    world.snapshot("fake_counts", DAY, {"font-a": 2})
    ctx = world.run(day=date(2026, 12, 1), from_snapshots=date(2026, 9, 3))
    assert [r.value for r in world.records("fake_counts")] == [1.0]
    assert parse.load_stale(ctx.paths) == {}


def test_only_narrows_the_run_and_keeps_the_rest(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.add(collector("fake_counts"))
    world.add(collector("npm"))  # read by the engine sources npm_fontsource and npm_expo
    world.snapshot("fake_counts", DAY, {"font-a": 1})
    world.snapshot("npm", date(2026, 9, 3), {"font-a": 7})
    ctx = world.run()
    assert set(parse.load_stale(ctx.paths)) == {"npm"}
    kept = (ctx.paths.records / "fake_counts.jsonl").read_bytes()
    world.snapshot("npm", DAY, {"font-a": 8})
    with caplog.at_level(logging.WARNING, logger="test.parse"):
        world.run(only=("npm_fontsource", "nonsense"))
    assert "--only nonsense" in caplog.text
    assert (ctx.paths.records / "fake_counts.jsonl").read_bytes() == kept
    assert [r.value for r in world.records("npm")] == [8.0]
    assert parse.load_stale(ctx.paths) == {}
    assert set(parse.load_snapshots(ctx.paths)) == {"fake_counts", "npm"}
    assert read_part(ctx.paths, "stale") == {
        "fake_counts": {"last_good": "2026-10-03", "stale_runs": 0},
        "npm": {"last_good": "2026-10-03", "stale_runs": 0},
    }
    world.run(only=("nothing_matches",))
    assert world.record_files() == ["fake_counts.jsonl", "npm.jsonl"]


def test_parse_needs_the_store(world: World) -> None:
    world.add(collector("fake_counts"))
    ctx = make_context(
        Paths.for_root(world.tmp / "root"), CONFIG, DAY, network=False, log=logging.getLogger("t")
    )
    with pytest.raises(StoreNotConfigured):
        parse.run(ctx)


def test_stage_files_decode_strictly(world: World) -> None:
    world.add(collector("fake_counts"))
    world.snapshot("fake_counts", date(2026, 9, 3), {"font-a": 1})
    ctx = world.run()
    raw = jsonio.load(ctx.paths.stage / parse.STALE_FILE)
    raw["fake_counts"]["extra"] = 1
    jsonio.dump(raw, ctx.paths.stage / parse.STALE_FILE)
    with pytest.raises(stageio.StageFileError, match="unknown"):
        parse.load_stale(ctx.paths)
