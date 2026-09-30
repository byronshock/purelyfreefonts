"""Run state (``tff_catalog.state``, design-m1 §5): load, propose, write and merge."""

import dataclasses
import logging
from datetime import date
from pathlib import Path

import pytest
from tests.helpers import synth
from tests.helpers.treehash import treehash

from tff_catalog import jsonio, stages, state
from tff_catalog.paths import Paths
from tff_catalog.state import (
    MAX_RUN_HISTORY,
    STATE_FILES,
    NextState,
    State,
    apply_state,
    load_state,
    write_next_state,
)


def entry(day: str, **snapshots: str) -> dict:
    return {
        "run_date": day,
        "merged_pr": 7,
        "code_commit": "abc",
        "config_sha256": "def",
        "snapshots": snapshots,
    }


def test_dataclasses_cover_every_state_file() -> None:
    assert {f.name for f in dataclasses.fields(State)} == set(STATE_FILES)
    assert {f.name for f in dataclasses.fields(NextState)} == {"base", *STATE_FILES}


def test_missing_state_reads_as_empty(tmp_path: Path) -> None:
    assert load_state(tmp_path / "nowhere") == State()
    assert load_state(tmp_path) == State()


def test_load_state_matches_the_synthetic_reader(synth_state: Path) -> None:
    loaded = load_state(synth_state)
    assert loaded == synth.read_state(synth_state)
    assert isinstance(loaded.run_history, tuple)
    assert loaded.membership["catalog"]


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("run_history.json", "{}", "expected a JSON array"),
        ("ids.json", "[]", "expected a JSON object"),
        ("stale.json", "{oops", "not valid JSON"),
        ("run_history.json", '[{"merged_pr": 1}]', "run_date"),
        ("run_history.json", '[{"run_date": "soon"}]', r"run_history\[0\].run_date: not a date"),
        (
            "run_history.json",
            '[{"run_date": "2026-10-03"}, {"run_date": "2026-11-03"}, {"run_date": "2026-10-03"}]',
            "more than one entry for 2026-10-03",
        ),
    ],
)
def test_bad_state_files_name_themselves(
    tmp_path: Path, filename: str, content: str, message: str
) -> None:
    (tmp_path / filename).write_text(content)
    with pytest.raises(ValueError, match=message) as info:
        load_state(tmp_path)
    assert filename in str(info.value)


def test_next_state_is_a_deep_copy(synth_state: Path) -> None:
    base = load_state(synth_state)
    before = synth.read_state(synth_state)
    nxt = NextState.from_state(base)
    assert nxt.base is base
    assert isinstance(nxt.run_history, list)
    for name in STATE_FILES:
        assert list(getattr(nxt, name)) == list(getattr(base, name)), name
    fid = next(iter(nxt.membership["catalog"]))
    nxt.membership["catalog"][fid]["runs_outside"] += 5
    nxt.run_history.append(entry("2026-10-03"))
    nxt.run_history[0]["snapshots"]["x"] = "2026-01-01"
    nxt.ids.clear()
    assert base == before, "NextState changed the state it started from"


def test_write_next_state_is_complete_and_deterministic(tmp_path: Path) -> None:
    history = [entry(f"2025-{m:02d}-03") for m in range(1, 13)]
    history += [entry(f"2026-{m:02d}-03") for m in range(1, 13)]
    history += [entry("2024-12-03")]
    nxt = NextState.from_state(State())
    nxt.run_history = list(reversed(history))
    nxt.ids = {"inter": {"family": "Inter", "minted_from": "x", "first_seen": date(2026, 9, 3)}}
    nxt.stale = {"b": {"last_good": "2026-09-03", "stale_runs": 1}, "a": {"stale_runs": 0}}
    write_next_state(nxt, tmp_path / "a")
    write_next_state(nxt, tmp_path / "b")
    assert treehash(tmp_path / "a") == treehash(tmp_path / "b")
    assert sorted(p.name for p in (tmp_path / "a").iterdir()) == sorted(STATE_FILES.values())
    written = jsonio.load(tmp_path / "a" / "run_history.json")
    assert len(written) == MAX_RUN_HISTORY
    assert [h["run_date"] for h in written] == sorted(h["run_date"] for h in history)[1:]
    assert jsonio.load(tmp_path / "a" / "ids.json")["inter"]["first_seen"] == "2026-09-03"
    assert jsonio.load(tmp_path / "a" / "smoothing.json") == {}
    assert (tmp_path / "a" / "stale.json").read_text().index('"a"') < (
        tmp_path / "a" / "stale.json"
    ).read_text().index('"b"')
    again = load_state(tmp_path / "a")
    assert again.stale == nxt.stale
    assert list(again.run_history) == written


def test_write_next_state_refuses_a_run_listed_twice(tmp_path: Path) -> None:
    nxt = NextState.from_state(State())
    nxt.run_history = [entry("2026-10-03"), entry("2026-11-03"), {"run_date": date(2026, 10, 3)}]
    with pytest.raises(ValueError, match="more than one entry for 2026-10-03"):
        write_next_state(nxt, tmp_path / "out")
    assert not (tmp_path / "out" / "run_history.json").exists()


def test_write_next_state_round_trips_the_synthetic_state(
    synth_state: Path, tmp_path: Path
) -> None:
    write_next_state(NextState.from_state(load_state(synth_state)), tmp_path / "copy")
    assert treehash(tmp_path / "copy") == treehash(synth_state)


def _proposed(tmp_path: Path, **files: object) -> Path:
    directory = tmp_path / "build-state"
    for name, value in files.items():
        jsonio.dump(value, directory / STATE_FILES[name])
    return directory


def test_apply_state_to_a_copy_leaves_state_alone(synth_state: Path, tmp_path: Path) -> None:
    before = treehash(synth_state)
    proposed = _proposed(tmp_path, stale={"x": {"last_good": "2026-10-03", "stale_runs": 0}})
    out = apply_state(synth_state, proposed, tmp_path / "merged")
    assert out == tmp_path / "merged"
    assert treehash(synth_state) == before
    merged = load_state(out)
    assert merged.stale == {"x": {"last_good": "2026-10-03", "stale_runs": 0}}
    assert merged.ids == load_state(synth_state).ids  # files not proposed keep their state/
    with pytest.raises(FileExistsError):
        apply_state(synth_state, proposed, tmp_path / "merged")


def test_apply_state_in_place_and_on_the_first_run(tmp_path: Path) -> None:
    proposed = _proposed(tmp_path, ids={"a": {"family": "A"}}, run_history=[entry("2026-10-03")])
    first = apply_state(tmp_path / "no-state-yet", proposed, tmp_path / "s1")
    assert load_state(first).ids == {"a": {"family": "A"}}
    in_place = tmp_path / "state"
    jsonio.dump({"old": {}}, in_place / "ids.json")
    assert apply_state(in_place, proposed) == in_place
    assert load_state(in_place).ids == {"a": {"family": "A"}}
    assert len(load_state(in_place).run_history) == 1


def test_apply_state_refuses_other_files(tmp_path: Path) -> None:
    proposed = _proposed(tmp_path, ids={})
    (proposed / "notes.txt").write_text("x")
    with pytest.raises(ValueError, match=r"notes\.txt"):
        apply_state(tmp_path / "state", proposed, tmp_path / "out")
    with pytest.raises(FileNotFoundError):
        apply_state(tmp_path / "state", tmp_path / "missing", tmp_path / "out2")


def test_snapshot_pointers_and_frozen_dates() -> None:
    s = State(
        run_history=(
            entry("2026-09-03", github_releases="2026-09-03", nerd_releases="2026-09-03"),
            entry("2026-10-03", github_releases="2026-10-03", nerd_releases="2026-09-03"),
            entry("2026-11-03", nerd_releases="2026-11-02"),
        )
    )
    assert s.snapshot_dates("github_releases") == (date(2026, 9, 3), date(2026, 10, 3))
    assert s.snapshot_dates("nerd_releases") == (date(2026, 9, 3), date(2026, 11, 2))
    assert s.snapshot_dates("npm") == ()
    runs = {date(2026, 9, 3), date(2026, 10, 3), date(2026, 11, 3)}
    assert s.frozen_dates("npm") == runs
    assert s.frozen_dates("nerd_releases") == runs | {date(2026, 11, 2)}
    with pytest.raises(ValueError, match="not a date"):
        State(run_history=(entry("2026-09-03", npm="soon"),)).snapshot_dates("npm")


def test_stage_contexts_load_the_committed_state(synth_state: Path, tmp_path: Path) -> None:
    paths = Paths.for_root(tmp_path, state=synth_state)
    ctx = stages.make_context(
        paths, None, date(2026, 10, 3), network=False, log=logging.getLogger("t")
    )  # type: ignore[arg-type]
    assert ctx.state == load_state(synth_state)
    assert ctx.store is None
    assert ctx.fetcher is None


def test_parts_and_whole_state_agree(synth_state: Path, tmp_path: Path) -> None:
    """``complete_next_state`` after one part equals ``write_next_state`` of that change."""
    paths = Paths.for_root(tmp_path, state=synth_state)
    stale = {"synth_installs": {"last_good": "2026-10-03", "stale_runs": 0}}
    state.write_part(paths, "stale", stale, stage="parse")
    state.complete_next_state(paths)
    nxt = NextState.from_state(load_state(synth_state))
    nxt.stale = stale
    write_next_state(nxt, tmp_path / "whole")
    assert treehash(paths.next_state) == treehash(tmp_path / "whole")
