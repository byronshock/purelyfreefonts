"""The snapshot store (``tff_catalog.store``, design-m1 §4): one snapshot per source and date."""

import gzip
import hashlib
import json
import logging
import os
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from jsonschema import Draft202012Validator
from tests.helpers import ROOT, synth
from tests.helpers.treehash import treehash

from tff_catalog import cli, clock, collectors, fetch, jsonio, store
from tff_catalog.collectors.base import Collector, CollectorBase, FetchContext, ParseContext
from tff_catalog.config_model import Config, RankingConfig, from_mapping, load_toml
from tff_catalog.paths import Paths
from tff_catalog.records import Observation, Record, SourceKey, read_jsonl
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State, load_state
from tff_catalog.store import (
    MANIFEST_NAME,
    ExtractRecord,
    FetchRecord,
    Manifest,
    RawDir,
    Snapshot,
    SnapshotCorrupt,
    SnapshotExists,
    SnapshotFrozen,
    Store,
    stale_max_age,
)

SCHEMA = Draft202012Validator(
    json.loads((ROOT / "schemas" / "stage" / "manifest.schema.json").read_text(encoding="utf-8"))
)
DAY = date(2026, 10, 3)
SRC = "fake_counts"
URL = "https://counts.synth.example/counts.json"


def fetch_record(body: bytes = b"{}", *, kept: bool = True) -> FetchRecord:
    return FetchRecord(
        url=URL,
        status=200,
        fetched_at="2026-10-03T06:00:00Z",
        sha256=hashlib.sha256(body).hexdigest(),
        bytes=len(body),
        etag='"abc"',
        kept=kept,
    )


def fake_fetch(s: Store, day: date, counts: dict[str, int], **kwargs: object) -> Snapshot:
    """What a collector's fetch() does to the store, through ``Store.writer``."""
    body = jsonio.canonical_bytes(counts)
    with s.writer(SRC, day, 1, **kwargs) as w:  # type: ignore[arg-type]
        w.record_fetch(fetch_record(body))
        w.write_bytes("counts.json", body)
        w.set_data_date(day - timedelta(days=1))
    snap = s.snapshot(SRC, day)
    assert snap is not None
    return snap


def assert_schema_valid(directory: Path) -> dict:
    manifest = jsonio.load(directory / MANIFEST_NAME)
    errors = sorted(SCHEMA.iter_errors(manifest), key=str)
    assert not errors, errors[0].message
    return manifest


# --- manifest ---------------------------------------------------------------------------------


def full_manifest() -> Manifest:
    return Manifest(
        schema=1,
        source=SRC,
        date=DAY,
        collector_version=2,
        complete=True,
        data_date=date(2026, 10, 2),
        window=(date(2025, 10, 3), date(2026, 10, 2)),
        fetched=(fetch_record(), fetch_record(b"x", kept=False)),
        extracts=(
            ExtractRecord("a.json", "0" * 64, 2, 1),
            ExtractRecord("b/c.csv.gz", "1" * 64, 9),
        ),
        stale_of=date(2026, 11, 3),
        notes=("one", "two"),
    )


def test_manifest_round_trips_and_matches_the_schema() -> None:
    m = full_manifest()
    data = m.to_json()
    assert not sorted(SCHEMA.iter_errors(data), key=str)
    assert Manifest.from_json(json.loads(jsonio.canonical_bytes(data))) == m
    bare = Manifest(1, SRC, DAY, 1, False, None, None, (), ())
    assert not sorted(SCHEMA.iter_errors(bare.to_json()), key=str)
    assert Manifest.from_json(bare.to_json()) == bare


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(extra=1), "unknown keys"),
        (lambda d: d.pop("notes"), "missing keys"),
        (lambda d: d.update(schema=2), "expected 1"),
        (lambda d: d.update(complete=1), "expected bool"),
        (lambda d: d.update(collector_version=True), "expected int"),
        (lambda d: d.update(date="2026-10-3"), "not a date"),
        (lambda d: d.update(window={"start": "2026-01-01"}), "missing keys"),
        (lambda d: d["fetched"][0].pop("kept"), "missing keys"),
        (lambda d: d["extracts"][0].update(path="../up"), "bad extract name"),
        (lambda d: d["extracts"][0].update(rows="3"), "expected int"),
    ],
)
def test_manifest_from_json_is_strict(change: object, message: str) -> None:
    data = full_manifest().to_json()
    change(data)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        Manifest.from_json(data)


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d.update(source="Fake-Counts"),
        lambda d: d.update(collector_version=0),
        lambda d: d["fetched"][0].update(url="http://counts.synth.example/x"),
        lambda d: d["fetched"][0].update(url="https://counts.synth.example/a b"),
        lambda d: d["fetched"][0].update(status=42),
        lambda d: d["fetched"][0].update(status=600),
        lambda d: d["fetched"][0].update(fetched_at="2026-10-03T06:00:00+00:00"),
        lambda d: d["fetched"][0].update(fetched_at="2026-10-03T06:00:00.5Z"),
        lambda d: d["fetched"][0].update(sha256="ABC"),
        lambda d: d["fetched"][0].update(bytes=-1),
        lambda d: d["extracts"][0].update(sha256=""),
        lambda d: d["extracts"][0].update(sha256="F" * 64),
        lambda d: d["extracts"][0].update(bytes=-1),
        lambda d: d["extracts"][0].update(rows=-1),
    ],
)
def test_manifest_checks_agree_with_the_schema(change: object) -> None:
    """Every value the JSON Schema refuses, ``Manifest.from_json`` refuses too."""
    data = full_manifest().to_json()
    change(data)  # type: ignore[operator]
    assert list(SCHEMA.iter_errors(data)), "the schema accepts this change; drop the case"
    with pytest.raises(SnapshotCorrupt):
        Manifest.from_json(data)


def test_a_not_modified_fetch_has_no_sha256() -> None:
    data = full_manifest().to_json()
    data["fetched"][0].update(status=304, sha256="", bytes=0)
    assert not list(SCHEMA.iter_errors(data))
    assert Manifest.from_json(data).fetched[0].sha256 == ""


def test_the_writer_refuses_what_the_schema_refuses(tmp_path: Path) -> None:
    w = Store(tmp_path / "store").writer(SRC, DAY, 1)
    bad = fetch_record()
    for record in (
        FetchRecord(**{**bad.to_json(), "url": "http://counts.synth.example/counts.json"}),
        FetchRecord(**{**bad.to_json(), "fetched_at": "2026-10-03 06:00:00"}),
    ):
        with pytest.raises(ValueError, match=f"{SRC} fetched"):
            w.record_fetch(record)
    with pytest.raises(TypeError, match="FetchRecord"):
        w.record_fetch(bad.to_json())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="rows must be a count"):
        w.write_bytes("counts.json", b"{}", rows=-1)
    w.record_fetch(bad)
    w.write_bytes("counts.json", b"{}", rows=0)
    snap = w.close()
    assert snap.manifest.fetched == (bad,)
    assert_schema_valid(snap.path)


def test_synthetic_store_reads_back(synth_store: Path) -> None:
    s = Store(synth_store)
    assert s.sources() == sorted(synth.SOURCES)
    for source in synth.SOURCES:
        for day in s.dates(source):
            snap = s.snapshot(source, day)
            assert snap is not None
            assert_schema_valid(snap.path)
            rows = [json.dumps(r, sort_keys=True) for r in snap.iter_jsonl(synth.EXTRACT)]
            expected = read_jsonl(snap.path / synth.EXTRACT)
            assert len(rows) == len(expected) == snap.manifest.extracts[0].rows


# --- writing ----------------------------------------------------------------------------------


class Counts(CollectorBase):
    """A fake collector whose fetch() GETs one JSON file and keeps it."""

    name = SRC
    group = "homebrew"
    hosts = ("counts.synth.example",)

    @dataclass(frozen=True, slots=True)
    class Settings:
        enabled: bool = True

    def fetch(self, ctx: FetchContext) -> None:
        result = ctx.fetcher.get(URL)
        ctx.out.record_fetch(result.to_record(kept=True))
        ctx.out.write_bytes("counts.json", result.content)
        ctx.raw.file("dump/all.json").write_bytes(result.content * 100)

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        for key, n in sorted(ctx.snapshot.load_json("counts.json").items()):
            day = ctx.snapshot.date
            yield Observation(SRC, "365d", SourceKey("brew-cask", key), n, "installs", day, day)


def test_the_fetch_stage_keeps_one_snapshot_per_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 3 "Done when" through the real fetch stage: fetch twice, then --refetch."""
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json={"font-a": len(requests)})

    found = {SRC: cast("Collector", Counts())}
    monkeypatch.setattr(collectors, "discover", lambda kind=None: found)
    paths = Paths.for_root(tmp_path / "root", store=tmp_path / "store", raw_root=tmp_path / "raw")
    (paths.sources_config).mkdir(parents=True)
    (paths.sources_config / f"{SRC}.toml").write_text("")
    (tmp_path / "store").mkdir()
    ranking = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))
    config = cast("Config", Config(ranking, *([None] * 5), sources={}))  # type: ignore[arg-type]

    def run_fetch(**options: Any) -> None:
        with fetch.Fetcher(
            transport=httpx.MockTransport(respond), min_interval={"counts.synth.example": 0.0}
        ) as fetcher:
            ctx = StageContext(
                paths=paths,
                config=config,
                state=load_state(paths.state),
                run_date=DAY,
                store=Store(paths.store),
                fetcher=fetcher,
                log=logging.getLogger("test.fetch"),
                options=RunOptions(**options),
            )
            fetch.run(ctx)

    run_fetch()
    run_fetch()  # already complete: a no-op
    assert len(requests) == 1
    run_fetch(refetch=True)
    assert len(requests) == 2
    assert sorted(p.name for p in (tmp_path / "store" / SRC).iterdir()) == [DAY.isoformat()]
    snap = Store(tmp_path / "store").snapshot(SRC, DAY)
    assert snap is not None
    assert snap.load_json("counts.json") == {"font-a": 2}
    assert_schema_valid(snap.path)
    assert not any((tmp_path / "raw").rglob("*.json")), "big raw files outlived the run"
    assert Store(tmp_path / "store").check().ok


def test_writer_builds_a_tmp_dir_and_renames_it(tmp_path: Path) -> None:
    s = Store(tmp_path / "store")
    w = s.writer(SRC, DAY, 1)
    w.write_json("z.json", [1, 2, 3])
    w.write_jsonl("rows.jsonl.gz", ({"n": n} for n in range(5)))
    w.write_bytes("sub/raw.csv", b"a,b\n1,2\n", rows=1)
    w.record_fetch(fetch_record())
    w.set_window(date(2025, 10, 3), date(2026, 10, 2))
    w.note("partial list: page 3 failed")
    directory = tmp_path / "store" / SRC
    assert sorted(p.name for p in directory.iterdir()) == ["2026-10-03.tmp"]
    assert s.snapshot(SRC, DAY) is None
    snap = w.close()
    assert sorted(p.name for p in directory.iterdir()) == ["2026-10-03"]
    assert snap == s.snapshot(SRC, DAY)
    manifest = assert_schema_valid(snap.path)
    assert [e["path"] for e in manifest["extracts"]] == ["rows.jsonl.gz", "sub/raw.csv", "z.json"]
    assert [e["rows"] for e in manifest["extracts"]] == [5, 1, 3]
    assert manifest["window"] == {"start": "2025-10-03", "end": "2026-10-02"}
    assert manifest["notes"] == ["partial list: page 3 failed"]
    for e in manifest["extracts"]:
        data = (snap.path / e["path"]).read_bytes()
        assert (hashlib.sha256(data).hexdigest(), len(data)) == (e["sha256"], e["bytes"])
    assert snap.load_json("z.json") == [1, 2, 3]
    assert list(snap.iter_jsonl("rows.jsonl.gz")) == [{"n": n} for n in range(5)]
    assert snap.read_bytes("sub/raw.csv") == b"a,b\n1,2\n"
    assert snap.fetched_at == "2026-10-03T06:00:00Z"
    with pytest.raises(RuntimeError, match="closed"):
        w.write_json("late.json", {})


def test_fetching_one_source_twice_leaves_one_snapshot_per_date(tmp_path: Path) -> None:
    """Milestone 1 step 3 "Done when": a second fetch, and a --refetch, never duplicate."""
    s = Store(tmp_path / "store")
    fake_fetch(s, DAY, {"font-a": 1})
    with pytest.raises(SnapshotExists):
        fake_fetch(s, DAY, {"font-a": 2})
    assert s.snapshot(SRC, DAY).load_json("counts.json") == {"font-a": 1}  # type: ignore[union-attr]
    fake_fetch(s, DAY, {"font-a": 3}, refetch=True)
    fake_fetch(s, DAY + timedelta(days=1), {"font-a": 4})
    entries = sorted(p.name for p in (tmp_path / "store" / SRC).iterdir())
    assert entries == ["2026-10-03", "2026-10-04"]
    assert s.snapshot(SRC, DAY).load_json("counts.json") == {"font-a": 3}  # type: ignore[union-attr]
    assert s.dates(SRC) == [DAY, DAY + timedelta(days=1)]


def test_refetch_is_refused_for_dates_merged_runs_used(tmp_path: Path) -> None:
    s = Store(tmp_path / "store")
    fake_fetch(s, DAY, {"font-a": 1})
    state = State(
        run_history=(
            {"run_date": "2026-11-03", "snapshots": {SRC: DAY.isoformat()}},
            {"run_date": "2026-12-03", "snapshots": {}},
        )
    )
    frozen = state.frozen_dates(SRC)
    assert frozen == {DAY, date(2026, 11, 3), date(2026, 12, 3)}
    with pytest.raises(SnapshotFrozen):
        s.writer(SRC, DAY, 1, refetch=True, frozen=frozen)
    with pytest.raises(SnapshotExists):
        s.writer(SRC, DAY, 1, frozen=frozen)
    with pytest.raises(SnapshotFrozen, match="immutable"):
        s.writer(SRC, date(2026, 11, 3), 1, frozen=frozen)  # adding one would change a replay
    fake_fetch(s, date(2027, 1, 3), {"font-a": 2}, frozen=frozen)
    assert s.snapshot(SRC, DAY).load_json("counts.json") == {"font-a": 1}  # type: ignore[union-attr]


def test_a_failed_fetch_leaves_nothing(tmp_path: Path) -> None:
    s = Store(tmp_path / "store")

    def failing_fetch() -> None:
        with s.writer(SRC, DAY, 1) as w:
            w.write_bytes("counts.json", b"{}")
            raise OSError("network down")

    with pytest.raises(OSError, match="network down"):
        failing_fetch()
    assert list((tmp_path / "store" / SRC).iterdir()) == []
    fake_fetch(s, DAY, {"font-a": 1})


def test_a_crashed_writer_is_cleaned_up_by_the_next(tmp_path: Path) -> None:
    left = tmp_path / "store" / SRC / "2026-10-03.tmp"
    left.mkdir(parents=True)
    (left / "junk.json").write_text("{}")
    snap = fake_fetch(Store(tmp_path / "store"), DAY, {"font-a": 1})
    assert not left.exists()
    assert sorted(p.name for p in snap.path.iterdir()) == ["counts.json", MANIFEST_NAME]


def test_close_refuses_files_it_did_not_write(tmp_path: Path) -> None:
    w = Store(tmp_path / "store").writer(SRC, DAY, 1)
    w.write_bytes("counts.json", b"{}")
    (w.tmp / "sneaky.json").write_text("{}")
    with pytest.raises(RuntimeError, match=r"sneaky\.json"):
        w.close()
    assert not (tmp_path / "store" / SRC / "2026-10-03").exists()
    assert not w.tmp.exists()


def test_an_incomplete_snapshot_is_skipped_and_replaceable(tmp_path: Path) -> None:
    s = Store(tmp_path / "store")
    w = s.writer(SRC, DAY, 1)
    w.write_bytes("counts.json", b"{}")
    w.close(complete=False)
    assert s.snapshot(SRC, DAY) is None
    assert s.dates(SRC) == []
    assert s.dates(SRC, complete_only=False) == [DAY]
    assert s.latest(SRC, DAY) is None
    fake_fetch(s, DAY, {"font-a": 1})  # no --refetch needed
    assert s.dates(SRC) == [DAY]


def test_gzip_extracts_are_byte_stable(tmp_path: Path) -> None:
    data = b"name,count\n" + b"".join(b"font-%d,%d\n" % (i, i) for i in range(2000))
    shas = []
    for name in ("a", "b"):
        with Store(tmp_path / name).writer(SRC, DAY, 1) as w:
            entry = w.write_bytes("counts.csv.gz", data, rows=2000)
        shas.append(entry.sha256)
        stored = (tmp_path / name / SRC / "2026-10-03" / "counts.csv.gz").read_bytes()
        assert gzip.decompress(stored) == data
        assert len(stored) < len(data)
    assert shas[0] == shas[1]
    snap = Store(tmp_path / "a").snapshot(SRC, DAY)
    assert snap is not None
    assert snap.read_bytes("counts.csv.gz") == data


def test_copy_extract_carries_the_same_blob(tmp_path: Path) -> None:
    s = Store(tmp_path / "store")
    with s.writer(SRC, DAY, 1) as w:
        w.write_jsonl("month-2026-08.jsonl.gz", [{"n": 1}, {"n": 2}])
    previous = s.snapshot(SRC, DAY)
    assert previous is not None
    later = DAY + timedelta(days=31)
    with s.writer(SRC, later, 1) as w:
        copied = w.copy_extract(previous, "month-2026-08.jsonl.gz")
        w.write_jsonl("month-2026-09.jsonl.gz", [{"n": 3}])
    assert copied == previous.manifest.extracts[0]
    snap = s.snapshot(SRC, later)
    assert snap is not None
    assert list(snap.iter_jsonl("month-2026-08.jsonl.gz")) == [{"n": 1}, {"n": 2}]
    (previous.path / "month-2026-08.jsonl.gz").write_bytes(b"tampered")
    with s.writer(SRC, later + timedelta(days=1), 1) as w:
        with pytest.raises(SnapshotCorrupt):
            w.copy_extract(previous, "month-2026-08.jsonl.gz")
        with pytest.raises(FileNotFoundError):
            w.copy_extract(previous, "missing.json")
        w.write_json("ok.json", {})


def test_reads_check_the_manifest(tmp_path: Path) -> None:
    snap = fake_fetch(Store(tmp_path / "store"), DAY, {"font-a": 1})
    assert snap.has("counts.json")
    assert not snap.has("other.json")
    with pytest.raises(FileNotFoundError, match=r"no extract 'other\.json'"):
        snap.read_bytes("other.json")
    (snap.path / "counts.json").write_bytes(b'{"font-a":9}')
    with pytest.raises(SnapshotCorrupt, match="sha256"):
        snap.load_json("counts.json")


@pytest.mark.parametrize("name", ["../x.json", "/abs.json", "manifest.json", "", "a//b", "a/./b"])
def test_bad_extract_names_are_refused(tmp_path: Path, name: str) -> None:
    w = Store(tmp_path / "store").writer(SRC, DAY, 1)
    with pytest.raises(ValueError, match="bad extract name"):
        w.write_bytes(name, b"")
    w.abort()


def test_writer_checks_its_arguments(tmp_path: Path) -> None:
    s = Store(tmp_path / "store")
    with pytest.raises(ValueError, match="bad source"):
        s.writer("Bad-Name", DAY, 1)
    with pytest.raises(ValueError, match="collector_version"):
        s.writer(SRC, DAY, 0)
    w = s.writer(SRC, DAY, 1)
    with pytest.raises(ValueError, match="after its end"):
        w.set_window(DAY, DAY - timedelta(days=1))
    w.abort()


# --- reading ----------------------------------------------------------------------------------


def test_sources_and_dates_skip_everything_else(tmp_path: Path) -> None:
    s = Store(tmp_path)
    fake_fetch(s, DAY, {"font-a": 1})
    for name in ("_runs", "_seed", ".git", "Not_A_Source"):
        (tmp_path / name).mkdir()
    (tmp_path / "README.md").write_text("store\n")
    for name in ("2026-10-04.tmp", "2026-10-05.old", "notes", "2026-13-01"):
        (tmp_path / SRC / name).mkdir()
    assert s.sources() == [SRC]
    assert s.dates(SRC, complete_only=False) == [DAY]
    assert s.dates("missing") == []
    assert Store(tmp_path / "nowhere").sources() == []


def test_latest_finds_the_newest_snapshot_in_the_stale_window(tmp_path: Path) -> None:
    s = Store(tmp_path)
    for day in (date(2026, 8, 3), date(2026, 9, 3), date(2026, 10, 3)):
        fake_fetch(s, day, {"font-a": day.month})
    assert s.latest(SRC, date(2026, 10, 20)).date == date(2026, 10, 3)  # type: ignore[union-attr]
    assert s.latest(SRC, date(2026, 10, 2)).date == date(2026, 9, 3)  # type: ignore[union-attr]
    assert s.latest(SRC, date(2026, 8, 2)) is None
    window = stale_max_age(2)
    assert window == timedelta(days=62)
    edge = date(2026, 10, 3) + window
    assert s.latest(SRC, edge, max_age=window).date == date(2026, 10, 3)  # type: ignore[union-attr]
    assert s.latest(SRC, edge + timedelta(days=1), max_age=window) is None


def test_a_damaged_snapshot_counts_as_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A copied or broken manifest never stops a run: readers skip it, check fails on it,
    and the next fetch replaces it without --refetch."""
    s = Store(tmp_path)
    older = fake_fetch(s, DAY - timedelta(days=5), {"font-a": 1})
    snap = fake_fetch(s, DAY, {"font-a": 2})
    copied = tmp_path / SRC / "2026-10-04"  # a snapshot copied to the wrong date
    copied.mkdir()
    for p in snap.path.iterdir():
        (copied / p.name).write_bytes(p.read_bytes())
    (snap.path / MANIFEST_NAME).write_text("{not json")
    with caplog.at_level(logging.WARNING, logger="tff_catalog.store"):
        assert s.snapshot(SRC, DAY) is None
        assert s.snapshot(SRC, date(2026, 10, 4)) is None
        assert s.dates(SRC) == [older.date]
        assert s.latest(SRC, date(2026, 10, 4)) == older
    assert "manifest says fake_counts 2026-10-03, expected fake_counts 2026-10-04" in caplog.text
    failures = "\n".join(s.check().failures)
    assert "2026-10-03: unreadable manifest" in failures
    assert "2026-10-04: manifest says fake_counts/2026-10-03" in failures
    with pytest.raises(SnapshotCorrupt, match=r"manifest\.json"):
        store.read_manifest(snap.path)
    fake_fetch(s, DAY, {"font-a": 3})  # no --refetch needed
    assert s.snapshot(SRC, DAY).load_json("counts.json") == {"font-a": 3}  # type: ignore[union-attr]


# --- raw files and run manifests ----------------------------------------------------------------


def test_raw_dir_is_deleted_unless_kept(tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path, raw_root=tmp_path / "raw")
    raw = Store(tmp_path / "store").raw_dir(paths, "2026-10-03-abc")
    assert raw.path == tmp_path / "raw" / "2026-10-03-abc"
    big = raw.file("clone/objects/pack.bin")
    big.write_bytes(b"x" * 100)
    for bad in ("../escape", "/etc/passwd"):
        with pytest.raises(ValueError, match="bad raw file"):
            raw.file(bad)
    RawDir(raw.path, keep=True).cleanup()
    assert big.exists()
    raw.cleanup()
    assert not raw.path.exists()
    raw.cleanup()  # twice is fine
    for bad in ("../x", "2026-10-03/x", "latest"):
        with pytest.raises(ValueError, match="bad run id"):
            Store(tmp_path / "store").raw_dir(paths, bad)


def test_write_run(tmp_path: Path) -> None:
    path = Store(tmp_path).write_run(DAY, {"code_commit": "abc", "snapshots": {SRC: "2026-10-03"}})
    assert path == tmp_path / "_runs" / "2026-10-03.json"
    assert jsonio.load(path)["snapshots"] == {SRC: "2026-10-03"}


# --- check ------------------------------------------------------------------------------------


def test_check_passes_a_clean_store(synth_store: Path) -> None:
    report = Store(synth_store).check()
    assert report.ok, report.failures
    assert report.warnings == ()
    assert [s for s, _ in report.sizes] == sorted(synth.SOURCES)
    assert all(size > 0 for _, size in report.sizes)


def test_check_enforces_the_size_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = Store(tmp_path)
    with s.writer(SRC, DAY, 1) as w:
        w.write_bytes("a.bin", b"a" * 600)
        w.write_bytes("b.bin", b"b" * 600)
    assert s.check().ok
    monkeypatch.setattr(store, "EXTRACT_MAX_BYTES", 500)
    monkeypatch.setattr(store, "SNAPSHOT_MAX_BYTES", 1000)
    monkeypatch.setattr(store, "RUN_MAX_BYTES", 1100)
    failures = s.check().failures
    assert any("extract a.bin is 600 bytes" in f for f in failures)
    assert any("snapshot is 1200 bytes" in f for f in failures)
    assert any("1200 new extract bytes" in f for f in failures)


def test_check_counts_only_new_blobs_against_the_run_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = Store(tmp_path)
    with s.writer(SRC, DAY, 1) as w:
        w.write_bytes("month-08.csv", b"8" * 900)
    later = DAY + timedelta(days=31)
    with s.writer(SRC, later, 1) as w:
        w.copy_extract(s.snapshot(SRC, DAY), "month-08.csv")  # type: ignore[arg-type]
        w.write_bytes("month-09.csv", b"9" * 300)
    monkeypatch.setattr(store, "RUN_MAX_BYTES", 1000)
    assert not any("new extract bytes" in f for f in s.check().failures)
    assert not any("new extract bytes" in f for f in s.check(run_date=later).failures)
    monkeypatch.setattr(store, "RUN_MAX_BYTES", 200)
    assert any("run 2026-11-03: 300 new" in f for f in s.check().failures)


def test_check_finds_damaged_snapshots(tmp_path: Path) -> None:
    s = Store(tmp_path)
    good = fake_fetch(s, DAY, {"font-a": 1})
    (good.path / "counts.json").write_bytes(b"{}")
    (good.path / "stray.txt").write_text("x")
    second = fake_fetch(s, DAY + timedelta(days=1), {"font-a": 2})
    (second.path / "counts.json").unlink()
    (tmp_path / SRC / "2026-10-05").mkdir()
    broken = tmp_path / SRC / "2026-10-06"
    broken.mkdir()
    (broken / MANIFEST_NAME).write_text("{not json")
    failures = "\n".join(s.check().failures)
    assert "2026-10-03: extract counts.json does not match its sha256" in failures
    assert "2026-10-03: files the manifest does not list: ['stray.txt']" in failures
    assert "2026-10-04: extract counts.json is missing" in failures
    assert "2026-10-05: no manifest" in failures
    assert "2026-10-06: unreadable manifest" in failures
    assert cli_code(["store", "check"], tmp_path) == 1


def test_check_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = Store(tmp_path)
    with s.writer(SRC, DAY, 1) as w:
        w.write_bytes("counts.json", b"x" * 100)
    later = DAY + timedelta(days=31)
    with s.writer(SRC, later, 1) as w:
        w.write_bytes("counts.json", b"y" * 130)
    with s.writer("other_source", later, 1) as w:
        w.write_bytes("big.json", b"z" * (store.GZIP_OVER_BYTES + 1))
    w = s.writer("third_source", later, 1)
    w.write_bytes("part.json", b"{}")
    w.close(complete=False)
    (tmp_path / SRC / "2026-12-01.tmp").mkdir()
    monkeypatch.setattr(store, "REPO_WARN_BYTES", 1000)
    report = s.check()
    assert report.ok, report.failures
    text = "\n".join(report.warnings)
    assert f"{SRC}: snapshot grew 30% since 2026-10-03 (100 -> 130 bytes)" in text
    assert "big.json is 262145 bytes uncompressed" in text
    assert "third_source/2026-11-03: incomplete snapshot" in text
    assert f"{SRC}/2026-12-01.tmp: left by an interrupted write" in text
    assert "over the 1000-byte warning" in text
    assert dict(report.sizes) == {SRC: 130, "other_source": store.GZIP_OVER_BYTES + 1}


# --- gc and commit --------------------------------------------------------------------------


def _age(path: Path, when: datetime) -> None:
    stamp = when.timestamp()
    for p in [path, *path.rglob("*")]:
        os.utime(p, (stamp, stamp), follow_symlinks=False)


def test_gc_removes_raw_dirs_older_than_the_limit(tmp_path: Path) -> None:
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    raw_root = tmp_path / "raw"
    old, fresh = raw_root / "fetch-2026-10-10", raw_root / "fetch-2026-10-18"
    touched, other = raw_root / "2026-10-09", raw_root / "photos"
    for d in (old, fresh, touched, other):
        (d / "clone").mkdir(parents=True)
        (d / "clone" / "pack").write_bytes(b"x")
    _age(old, now - timedelta(days=10))
    _age(fresh, now - timedelta(days=2))
    _age(touched, now - timedelta(days=10))
    _age(other, now - timedelta(days=100))  # not a run directory: never removed
    os.utime(touched / "clone" / "pack", ((now - timedelta(hours=1)).timestamp(),) * 2)
    s = Store(tmp_path / "store")
    leftover = tmp_path / "store" / SRC / "2026-10-01.tmp"
    leftover.mkdir(parents=True)
    _age(leftover, now - timedelta(days=9))
    with clock.frozen(now):
        removed = s.gc(raw_root, timedelta(days=7))
    assert removed == [old, leftover]
    assert fresh.exists()
    assert touched.exists()
    assert other.exists()
    with pytest.raises(ValueError, match="refusing"):
        s.gc(tmp_path, timedelta(days=7))
    assert s.gc(tmp_path / "no-raw-yet", timedelta(days=7)) == []


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Git with no user or system config, so signing or hooks never interfere."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@synth.example")


@pytest.mark.usefixtures("git_env")
def test_commit_without_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "store"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    s = Store(root)
    monkeypatch.setenv(store.PUSH_ENV, "0")
    assert s.commit("nothing yet") is None
    fake_fetch(s, DAY, {"font-a": 1})
    sha = s.commit("snapshots 2026-10-03")
    assert sha == _git(root, "rev-parse", "HEAD")
    assert _git(root, "log", "-1", "--format=%s") == "snapshots 2026-10-03"
    assert s.commit("again") is None
    (root / SRC / "2026-10-04.tmp").mkdir()
    with pytest.raises(RuntimeError, match="interrupted writes"):
        s.commit("half-written")


@pytest.mark.usefixtures("git_env")
def test_commit_pulls_and_pushes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    (seed / "README.md").write_text("store\n")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "push", "-q", "origin", "main")
    root = tmp_path / "store"
    _git(tmp_path, "clone", "-q", str(remote), str(root))
    (seed / "_runs").mkdir()
    (seed / "_runs" / "other.json").write_text("{}\n")  # someone else pushed meanwhile
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "other")
    _git(seed, "push", "-q", "origin", "main")
    monkeypatch.delenv(store.PUSH_ENV, raising=False)
    s = Store(root)
    fake_fetch(s, DAY, {"font-a": 1})
    sha = s.commit("snapshots 2026-10-03")
    assert sha == _git(remote, "rev-parse", "main")
    assert (root / "_runs" / "other.json").is_file()


def cli_code(argv: list[str], store_root: Path, **env: str) -> int:
    mp = pytest.MonkeyPatch()
    try:
        mp.chdir(ROOT)
        mp.setenv("TFF_STORE", str(store_root))
        for key, value in env.items():
            mp.setenv(key, value)
        return cli.main(argv)
    finally:
        mp.undo()


def test_store_commands(
    synth_store: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_code(["store", "ls"], synth_store) == 0
    out = capsys.readouterr().out
    assert f"{synth.PACKAGES}" in out
    assert "3 snapshots  newest 2026-11-03" in out
    assert cli_code(["store", "ls", synth.PACKAGES], synth_store) == 0
    out = capsys.readouterr().out
    assert "2026-09-03  complete" in out
    assert "2026-11-03" not in out
    assert cli_code(["store", "ls", "nope"], synth_store) == 1
    before = treehash(synth_store)
    assert cli_code(["store", "check"], synth_store) == 0
    assert "store check: 0 failures, 0 warnings" in capsys.readouterr().out
    raw = tmp_path / "raw"
    (raw / "run-1").mkdir(parents=True)
    assert cli_code(["store", "gc"], synth_store, TFF_RAW=str(raw)) == 0
    assert "removed 0 directories" in capsys.readouterr().out
    assert treehash(synth_store) == before


@pytest.mark.usefixtures("git_env")
def test_store_commit_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "store"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    assert cli_code(["store", "commit"], root, TFF_STORE_PUSH="0") == 0
    assert capsys.readouterr().out.strip() == "nothing to commit"
    fake_fetch(Store(root), DAY, {"font-a": 1})
    with clock.frozen(datetime(2026, 10, 3, 7, tzinfo=UTC)):
        assert cli_code(["store", "commit"], root, TFF_STORE_PUSH="0") == 0
    assert _git(root, "log", "-1", "--format=%s") == "snapshots 2026-10-03"
