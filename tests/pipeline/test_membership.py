"""Catalog membership with hysteresis (milestone-1 step 12, methodology §6, ruling M11).

- The catalog: the overall top 500 plus the top 100 of the project rank and
  both desktop views; a font enters at 450 or better and leaves after 2 runs
  below 550.
- Each top-100 list: enter at 90 or better, leave after 2 runs worse than 110.
- Counters advance once per merged run: the stage reads the committed state,
  so a rerun (or a second unmerged run) writes the same bytes.
"""

import dataclasses
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT

from tff_catalog import jsonio, membership, stageio
from tff_catalog.config_model import Config, RankingConfig, from_mapping, load_toml
from tff_catalog.engine.order import Placement
from tff_catalog.latin import LatinResult
from tff_catalog.license_l3 import L3Result
from tff_catalog.licenses import LicenseClass, Verdict
from tff_catalog.membership import Membership, MemberState
from tff_catalog.paths import Paths
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe

RANKING: RankingConfig = from_mapping(RankingConfig, load_toml(ROOT / "config" / "ranking.toml"))
CFG = RANKING.membership
OCT, NOV, DEC, JAN = date(2026, 10, 3), date(2026, 11, 3), date(2026, 12, 3), date(2027, 1, 3)
EMPTY = Membership({}, {})


def ids(n: int) -> list[str]:
    return [f"fam-{i:04d}" for i in range(1, n + 1)]


def orders(ranked: list[str]) -> dict[str, int]:
    """{id: order} for ids listed best first."""
    return {fid: i for i, fid in enumerate(ranked, 1)}


def moved(base: list[str], at: dict[str, int] | str, position: int = 0) -> list[str]:
    """``base`` with each font of ``at`` ({id: 1-based position}, or one id and
    ``position``) at exactly that position, the others keeping their order."""
    at = {at: position} if isinstance(at, str) else at
    slots: list[str | None] = [None] * len(base)
    for fid, pos in at.items():
        slots[pos - 1] = fid
    rest = iter(f for f in base if f not in at)
    return [fid if fid is not None else next(rest) for fid in slots]


def as_json(m: Membership) -> dict[str, Any]:
    """``m`` as ``state/membership.json`` holds it."""
    return json.loads(jsonio.canonical_str(stageio.encode(m)))


def only_overall(ranked: list[str]) -> dict[str, dict[str, int]]:
    return {"overall": orders(ranked)}


# --- the catalog ----------------------------------------------------------------------------------


def test_the_first_run_takes_the_overall_top_500() -> None:
    base = ids(700)
    m = membership.update(only_overall(base), EMPTY, CFG, OCT)
    assert m.members() == base[:500]
    assert m.catalog[base[0]] == MemberState(True, OCT, 0)
    assert base[500] not in m.catalog


def test_a_font_enters_at_450_or_better() -> None:
    base = ids(700)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    newcomer = base[600]
    at_451 = membership.update(only_overall(moved(base, newcomer, 451)), s0, CFG, NOV)
    assert newcomer not in at_451.catalog
    at_450 = membership.update(only_overall(moved(base, newcomer, 450)), s0, CFG, NOV)
    assert at_450.catalog[newcomer] == MemberState(True, NOV, 0)


def test_a_member_leaves_after_two_runs_below_550() -> None:
    base = ids(700)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    fid = base[10]
    at_550 = membership.update(only_overall(moved(base, fid, 550)), s0, CFG, NOV)
    assert at_550.catalog[fid] == MemberState(True, OCT, 0)  # 550 is not below 550
    s1 = membership.update(only_overall(moved(base, fid, 551)), s0, CFG, NOV)
    assert s1.catalog[fid] == MemberState(True, OCT, 1)
    s2 = membership.update(only_overall(moved(base, fid, 600)), s1, CFG, DEC)
    assert s2.catalog[fid] == MemberState(False, OCT, 2)
    assert fid not in s2.members()
    s3 = membership.update(only_overall(moved(base, fid, 600)), s2, CFG, JAN)
    assert fid not in s3.catalog  # a leaver is reported once, then dropped


def test_coming_back_inside_resets_the_counter() -> None:
    base = ids(700)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    fid = base[10]
    s1 = membership.update(only_overall(moved(base, fid, 560)), s0, CFG, NOV)
    s2 = membership.update(only_overall(moved(base, fid, 540)), s1, CFG, DEC)
    assert s2.catalog[fid] == MemberState(True, OCT, 0)
    s3 = membership.update(only_overall(moved(base, fid, 560)), s2, CFG, JAN)
    assert s3.catalog[fid] == MemberState(True, OCT, 1)


def test_an_unranked_member_counts_as_outside() -> None:
    base = ids(700)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    fid = base[3]
    s1 = membership.update(only_overall([f for f in base if f != fid]), s0, CFG, NOV)
    assert s1.catalog[fid] == MemberState(True, OCT, 1)


def test_the_top_100_of_the_extra_ranks_joins_the_catalog() -> None:
    base = ids(700)
    assert CFG.extra_ranks == ("project", "desktop_chosen", "desktop_installed")
    for key in CFG.extra_ranks:
        fid = base[650]
        ranks = only_overall(base) | {key: orders(moved(base, fid, 7))}
        m = membership.update(ranks, EMPTY, CFG, OCT)
        assert m.catalog[fid] == MemberState(True, OCT, 0), key
    coding = only_overall(base) | {"coding": orders(moved(base, base[650], 1))}
    assert base[650] not in membership.update(coding, EMPTY, CFG, OCT).catalog


def test_a_top_100_member_keeps_its_catalog_place_whatever_its_overall_order() -> None:
    base = ids(700)
    fid = base[650]
    s0 = membership.update(
        only_overall(base) | {"project": orders(moved(base, fid, 7))}, EMPTY, CFG, OCT
    )
    s1 = membership.update(
        only_overall(base) | {"project": orders(moved(base, fid, 105))}, s0, CFG, NOV
    )
    assert s1.top100["project"][fid] == MemberState(True, OCT, 0)  # 105: inside the leave line
    assert s1.catalog[fid] == MemberState(True, OCT, 0)


# --- the top-100 lists (ruling M11) ---------------------------------------------------------------


def test_top_100_hysteresis_enter_at_90_leave_after_two_runs_worse_than_110() -> None:
    assert (CFG.top100.enabled, CFG.top100.enter, CFG.top100.leave, CFG.top100.leave_runs) == (
        True,
        90,
        110,
        2,
    )
    base = ids(300)
    s0 = membership.update({"coding": orders(base)}, EMPTY, CFG, OCT)
    assert sorted(f for f, s in s0.top100["coding"].items() if s.member) == base[:100]
    newcomer, member = base[200], base[5]
    s1 = membership.update(
        {"coding": orders(moved(base, {newcomer: 95, member: 110}))}, s0, CFG, NOV
    )
    assert newcomer not in s1.top100["coding"]  # 95 is not 90 or better
    assert s1.top100["coding"][member] == MemberState(True, OCT, 0)  # 110 is not worse than 110
    s2 = membership.update(
        {"coding": orders(moved(base, {newcomer: 90, member: 111}))}, s1, CFG, DEC
    )
    assert s2.top100["coding"][newcomer] == MemberState(True, DEC, 0)
    assert s2.top100["coding"][member] == MemberState(True, OCT, 1)
    s3 = membership.update({"coding": orders(moved(base, member, 150))}, s2, CFG, JAN)
    assert s3.top100["coding"][member] == MemberState(False, OCT, 2)


def test_without_hysteresis_a_top_100_list_is_this_runs_top_100() -> None:
    cfg = dataclasses.replace(CFG, top100=dataclasses.replace(CFG.top100, enabled=False))
    base = ids(300)
    s0 = membership.update({"project": orders(base)}, EMPTY, cfg, OCT)
    fid = base[5]
    s1 = membership.update({"project": orders(moved(base, fid, 101))}, s0, cfg, NOV)
    assert not s1.top100["project"][fid].member
    s2 = membership.update({"project": orders(moved(base, base[200], 100))}, s1, cfg, DEC)
    assert s2.top100["project"][base[200]].member


def test_a_rank_key_that_disappears_empties_out() -> None:
    base = ids(150)
    s0 = membership.update({"rising": orders(base)}, EMPTY, CFG, OCT)
    s1 = membership.update({}, s0, CFG, NOV)
    s2 = membership.update({}, s1, CFG, DEC)
    assert not any(s.member for s in s2.top100["rising"].values())
    assert membership.update({}, s2, CFG, JAN).top100["rising"] == {}


def test_a_new_rank_key_starts_from_its_plain_top_100() -> None:
    base = ids(300)
    s0 = membership.update({"project": orders(base)}, EMPTY, CFG, OCT)
    s1 = membership.update({"project": orders(base), "rising": orders(base)}, s0, CFG, NOV)
    assert sum(s.member for s in s1.top100["rising"].values()) == 100


# --- counters advance once ------------------------------------------------------------------------


def test_update_is_a_pure_function_of_its_inputs() -> None:
    base = ids(700)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    ranks = only_overall(moved(base, base[10], 600)) | {"project": orders(base[20:])}
    a = membership.update(ranks, s0, CFG, NOV)
    b = membership.update(ranks, s0, CFG, NOV)
    assert a == b
    assert a.catalog[base[10]].runs_outside == 1  # once, not twice


def test_excluded_fonts_leave_every_list_at_once() -> None:
    base = ids(300)
    m = membership.update(only_overall(base) | {"project": orders(base)}, EMPTY, CFG, OCT)
    out = membership.exclude(m, frozenset({base[0]}))
    assert out.catalog[base[0]] == MemberState(False, OCT, 0)
    assert not out.top100["project"][base[0]].member
    left = Membership({"x": MemberState(False, OCT, 2), "y": MemberState(True, OCT, 0)}, {})
    assert membership.exclude(left, frozenset({"x", "y"})).catalog == {
        "y": MemberState(False, OCT, 0)
    }


def test_state_round_trip() -> None:
    base = ids(200)
    m = membership.update(only_overall(base) | {"project": orders(base)}, EMPTY, CFG, OCT)
    assert membership.load(as_json(m)) == m
    assert membership.load(stageio.encode(m)) == m
    assert membership.load({}) == EMPTY


# --- the stage ------------------------------------------------------------------------------------


def _ctx(root: Path, committed: State, run_date: date) -> StageContext:
    # The membership stage reads ranking.toml only.
    config = Config(RANKING, None, None, None, None, None, {})  # type: ignore[arg-type]
    return StageContext(
        paths=Paths.for_root(root),
        config=config,
        state=committed,
        run_date=run_date,
        store=None,
        fetcher=None,
        log=logging.getLogger("test.membership"),
    )


def _write_ranks(paths: Paths, ranks: dict[str, list[str]]) -> None:
    stageio.dump_stage(
        paths,
        "ranks",
        {
            key: {
                fid: Placement(order=i, rank=i if i <= 100 else None, band=None, gate_held=False)
                for i, fid in enumerate(ranked, 1)
            }
            for key, ranked in ranks.items()
        },
    )


def _state_file(paths: Paths, name: str) -> Any:
    return jsonio.load(paths.next_state / f"{name}.json")


def test_stage_writes_membership_and_catalog_dates(tmp_path: Path) -> None:
    base = ids(600)
    committed = State(
        first_seen={base[0]: {"catalog": "2026-09-03", "sources": {"homebrew": "2025-01-01"}}}
    )
    ctx = _ctx(tmp_path, committed, OCT)
    _write_ranks(ctx.paths, {"overall": base, "project": base})
    corrected = {
        base[0]: {"catalog": "2026-09-03", "sources": {"homebrew": "2025-01-01"}},
        base[1]: {"catalog": None, "sources": {"npm": "2026-01-01"}},
        base[599]: {"catalog": None, "sources": {"npm": "2026-01-01"}},
    }
    jsonio.dump(corrected, ctx.paths.next_state / "first_seen.json")  # stage "correct"'s part
    membership.run(ctx)
    m = stageio.load_stage(ctx.paths, "membership")
    assert m.members() == base[:500]
    assert _state_file(ctx.paths, "membership") == as_json(m)
    seen = _state_file(ctx.paths, "first_seen")
    assert seen[base[0]]["catalog"] == "2026-09-03"  # the first entry date is kept
    assert seen[base[1]] == {"catalog": "2026-10-03", "sources": {"npm": "2026-01-01"}}
    assert seen[base[599]]["catalog"] is None
    assert seen[base[2]] == {"catalog": "2026-10-03", "sources": {}}


def test_a_rerun_in_the_same_run_advances_nothing(tmp_path: Path) -> None:
    """Stage "verify" can make refresh rerun rank and membership: counters must not move twice."""
    base = ids(700)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    committed = State(membership=as_json(s0))
    ctx = _ctx(tmp_path, committed, NOV)
    _write_ranks(ctx.paths, {"overall": moved(base, base[10], 600)})
    membership.run(ctx)
    first = (ctx.paths.next_state / "membership.json").read_bytes()
    membership.run(ctx)
    assert (ctx.paths.next_state / "membership.json").read_bytes() == first
    assert stageio.load_stage(ctx.paths, "membership").catalog[base[10]].runs_outside == 1


def test_the_rerun_after_verify_writes_what_one_pass_would(tmp_path: Path) -> None:
    """Refresh reruns membership after stage "verify"; the second pass reads its own
    first_seen output, and must end where a single pass with the L3 result ends."""
    base = ids(600)
    once, twice = _ctx(tmp_path / "once", State(), OCT), _ctx(tmp_path / "twice", State(), OCT)
    failed = L3Result(
        family_id=base[4],
        level="failed",
        checked_on=OCT,
        text_url=None,
        text_sha256=None,
        matched=None,
        name_ids=(None, None),
        font_version=None,
        font_file=None,
    )
    _write_ranks(twice.paths, {"overall": base})
    membership.run(twice)  # the first pass, before verify
    assert _state_file(twice.paths, "first_seen")[base[4]]["catalog"] == "2026-10-03"
    for ctx in (once, twice):
        _write_ranks(ctx.paths, {"overall": [f for f in base if f != base[4]]})
        stageio.dump_stage(ctx.paths, "l3", {base[4]: failed})
        membership.run(ctx)
    for name in ("membership", "first_seen"):
        assert _state_file(once.paths, name) == _state_file(twice.paths, name), name
    assert base[4] not in _state_file(twice.paths, "first_seen")


def test_a_font_that_failed_l3_leaves_the_catalog_at_once(tmp_path: Path) -> None:
    base = ids(600)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    ctx = _ctx(tmp_path, State(membership=as_json(s0)), NOV)
    _write_ranks(ctx.paths, {"overall": [f for f in base if f != base[4]]})
    failed = L3Result(
        family_id=base[4],
        level="failed",
        checked_on=NOV,
        text_url=None,
        text_sha256=None,
        matched=None,
        name_ids=(None, None),
        font_version=None,
        font_file=None,
    )
    stageio.dump_stage(ctx.paths, "l3", {base[4]: failed})
    membership.run(ctx)
    m = stageio.load_stage(ctx.paths, "membership")
    assert not m.catalog[base[4]].member
    assert base[4] not in m.members()


def _universe(fids: list[str], drop: dict[str, str] | None = None) -> Universe:
    drop = drop or {}
    return Universe(
        families={
            fid: Family(
                id=fid,
                family=f"Synth {fid}",
                keys=(),
                sources=("synth",),
                first_seen=OCT,
                minted_from=f"Synth {fid}",
                drop=drop.get(fid),
            )
            for fid in fids
        },
        unmapped=(),
    )


def _verdict(fid: str, status: str) -> Verdict:
    return Verdict(
        family_id=fid,
        spdx="OFL-1.1",
        license=LicenseClass("OFL-1.1", status),  # type: ignore[arg-type]
        preview_ok=status == "allowed",
        seen=(),
    )


def test_a_font_the_gates_now_rule_out_leaves_every_list_at_once(tmp_path: Path) -> None:
    """Filter first: validate fails on an ineligible catalog font, so no counter keeps it."""
    base = ids(600)
    s0 = membership.update(only_overall(base) | {"project": orders(base)}, EMPTY, CFG, OCT)
    ctx = _ctx(tmp_path, State(membership=as_json(s0)), NOV)
    dropped, not_latin, excluded, ruling, gone = base[1], base[2], base[3], base[4], base[5]
    # Stage "correct" gives an ineligible font no term, so the ranks no longer hold it.
    _write_ranks(ctx.paths, {"overall": base[6:], "project": base[6:]})
    stageio.dump_stage(ctx.paths, "universe", _universe(base[:5] + base[6:], {dropped: "icon"}))
    stageio.dump_stage(
        ctx.paths,
        "latin",
        {fid: LatinResult(latin=fid != not_latin, basis=None, coverage=None) for fid in base},
    )
    stageio.dump_stage(
        ctx.paths,
        "licenses",
        {
            fid: _verdict(fid, {excluded: "excluded", ruling: "ruling"}.get(fid, "allowed"))
            for fid in base
        },
    )
    membership.run(ctx)
    m = stageio.load_stage(ctx.paths, "membership")
    # A license waiting for the owner's ruling is unverified, so it is filtered out like
    # the others until the ruling (methodology §1 and §9).
    for fid in (dropped, not_latin, excluded, ruling, gone):
        assert m.catalog[fid] == MemberState(False, OCT, 0), fid
        assert not m.top100["project"].get(fid, MemberState(False, None, 0)).member, fid
    # An eligible font the ranks no longer hold keeps its counters as usual (unranked, it is
    # one run outside the project top 100, which keeps it in the catalog).
    assert m.top100["project"][base[0]] == MemberState(True, OCT, 1)
    assert m.catalog[base[0]] == MemberState(True, OCT, 0)
    assert base[6] in m.members()


@pytest.mark.parametrize("run_date", [NOV, DEC])
def test_two_unmerged_runs_write_the_same_state(tmp_path: Path, run_date: date) -> None:
    base = ids(700)
    s0 = membership.update(only_overall(base), EMPTY, CFG, OCT)
    committed = State(membership=as_json(s0))
    out = []
    for name in ("a", "b"):
        ctx = _ctx(tmp_path / name, committed, run_date)
        _write_ranks(ctx.paths, {"overall": moved(base, base[10], 600)})
        membership.run(ctx)
        out.append((ctx.paths.next_state / "membership.json").read_bytes())
    assert out[0] == out[1]
