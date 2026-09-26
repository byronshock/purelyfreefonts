"""Stage "membership": catalog membership with hysteresis (milestone-1 step 12). Owner: agent P8.

The catalog is the overall top ``catalog_size`` plus the top ``extra_top`` of
each of ``extra_ranks``. A font enters at ``enter`` or better and leaves after
``leave_runs`` runs worse than ``leave``; each top-100 list has its own
hysteresis (gate M11). Counters advance once per merged run, never twice.

The rules, per list (the catalog, and the top-100 list of every rank key):

- A member stays while its order is ``leave`` or better, and its
  ``runs_outside`` goes back to 0. Each run worse than ``leave`` (or unranked)
  adds 1; at ``leave_runs`` it leaves (``member`` false, kept one run so the
  review can say so, then dropped).
- A non-member enters at ``enter`` or better, with ``entered`` = the run date.
- A list with no members yet (the first run, or a rank key that has just
  appeared, such as Rising) takes the plain top ``catalog_size`` or
  ``extra_top`` instead of ``enter``.
- The catalog also keeps, and admits, every font that is a member of the
  top-100 list of an ``extra_ranks`` key, whatever its overall order.
- With ``top100.enabled`` false (gate M11 (b)), a top-100 list is simply this
  run's top ``extra_top``.

The stage starts from the committed ``state/membership.json`` (``ctx.state``),
never from ``build/state/``, so a rerun after stage "verify" (or a second
unmerged run) gives the same counters. A font that failed L3 leaves at once,
and so does one the gates now rule out (``corrections.eligible_families``: a
universe drop, the Latin gate or an excluded license): filter first, so an
ineligible font never stays in the catalog on its counters.
It also sets ``first_seen[id].catalog`` (the day a font first entered the
catalog) in the ``first_seen`` part that stage "correct" wrote.

Writes ``build/stage/membership.json`` and ``build/state/membership.json``.
"""

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from tff_catalog import jsonio, stageio, state
from tff_catalog.corrections import eligible_families

if TYPE_CHECKING:
    from tff_catalog.config_model import Membership as MembershipConfig
    from tff_catalog.stages import StageContext


@dataclass(frozen=True, slots=True)
class MemberState:
    member: bool
    entered: date | None
    runs_outside: int


@dataclass(frozen=True, slots=True)
class Membership:
    catalog: Mapping[str, MemberState]  # by family_id
    top100: Mapping[str, Mapping[str, MemberState]]  # {rank key: {family_id: state}}

    def members(self) -> list[str]:
        """The catalog's member ids, sorted."""
        return sorted(fid for fid, s in self.catalog.items() if s.member)


def _hysteresis(
    orders: Mapping[str, int],
    prev: Mapping[str, MemberState],
    enter: int,
    leave: int,
    leave_runs: int,
    first_size: int,
    run_date: date,
    keep: frozenset[str] = frozenset(),
) -> dict[str, MemberState]:
    """One list's next states (module docstring); ``keep`` always stays in or enters."""
    enter_at = enter if any(s.member for s in prev.values()) else first_size
    out: dict[str, MemberState] = {}
    for fid in sorted(set(orders) | set(prev) | keep):
        position = orders.get(fid, math.inf)
        p = prev.get(fid)
        if p is not None and p.member:
            if position <= leave or fid in keep:
                out[fid] = MemberState(True, p.entered, 0)
            else:
                runs = p.runs_outside + 1
                out[fid] = MemberState(runs < leave_runs, p.entered, runs)
        elif position <= enter_at or fid in keep:
            out[fid] = MemberState(True, run_date, 0)
    return out


def update(
    ranks: Mapping[str, Mapping[str, int]],
    prev: Membership,
    cfg: MembershipConfig,
    run_date: date,
) -> Membership:
    """Next membership from this run's orders ({rank key: {family_id: order}}).

    A pure function: the same ranks, previous state and run date always give
    the same result. Top-100 lists are kept for every rank key in ``ranks`` or
    in ``prev`` (a key that disappears empties out through the counters).
    """
    t = cfg.top100
    if t.enabled:
        enter, leave, runs = t.enter, t.leave, t.leave_runs
    else:  # gate M11 (b): no hysteresis, the plain top extra_top each run
        enter, leave, runs = cfg.extra_top, cfg.extra_top, 1
    top100 = {
        key: _hysteresis(
            ranks.get(key, {}),
            prev.top100.get(key, {}),
            enter,
            leave,
            runs,
            cfg.extra_top,
            run_date,
        )
        for key in sorted(set(ranks) | set(prev.top100))
    }
    extra = frozenset(
        fid for key in cfg.extra_ranks for fid, s in top100.get(key, {}).items() if s.member
    )
    catalog = _hysteresis(
        ranks.get("overall", {}),
        prev.catalog,
        cfg.enter,
        cfg.leave,
        cfg.leave_runs,
        cfg.catalog_size,
        run_date,
        keep=extra,
    )
    return Membership(catalog=catalog, top100=top100)


def exclude(m: Membership, excluded: frozenset[str]) -> Membership:
    """``m`` with ``excluded`` fonts out of every list at once (an L3 failure)."""

    def out(states: Mapping[str, MemberState]) -> dict[str, MemberState]:
        return {
            fid: MemberState(False, s.entered, s.runs_outside) if fid in excluded else s
            for fid, s in sorted(states.items())
            if s.member or fid not in excluded
        }

    return Membership(
        catalog=out(m.catalog), top100={k: out(v) for k, v in sorted(m.top100.items())}
    )


def load(data: Mapping[str, Any]) -> Membership:
    """A ``Membership`` from ``state/membership.json`` data; empty data is the empty state.

    Dates may be ISO strings (the file) or ``date`` objects (``stageio.encode`` output).
    """
    plain = json.loads(jsonio.canonical_str(data))
    return stageio.decode(
        Membership, {"catalog": plain.get("catalog", {}), "top100": plain.get("top100", {})}
    )


def first_seen(
    part: Mapping[str, Any], committed: Mapping[str, Any], m: Membership, run_date: date
) -> dict[str, Any]:
    """The ``first_seen`` part with each family's ``catalog`` date set.

    ``part`` is what stage "correct" wrote this run. The date is the committed
    one if there is one, else the run date for a font in the catalog now, else
    null; so a rerun of this stage writes the same file. On a rerun ``part`` is
    this stage's own earlier output, so an entry it added for a font that is no
    longer a member (nothing committed, no source dates) is dropped again.
    """
    out = {fid: dict(entry) for fid, entry in sorted(part.items())}
    for fid in m.members():
        out.setdefault(fid, {"catalog": None, "sources": {}})
    in_catalog = set(m.members())
    for fid, entry in out.items():
        before = (committed.get(fid) or {}).get("catalog")
        entry["catalog"] = before or (run_date.isoformat() if fid in in_catalog else None)
    return {
        fid: entry
        for fid, entry in out.items()
        if entry["catalog"] is not None or entry.get("sources") or fid in committed
    }


def listed(m: Membership) -> frozenset[str]:
    """Every family id in any list of ``m``, member or not."""
    return frozenset(m.catalog) | frozenset(f for states in m.top100.values() for f in states)


def _optional(ctx: StageContext, name: str) -> Any:
    path = stageio.stage_path(ctx.paths, name)
    return stageio.load_stage(ctx.paths, name) if path.is_file() else None


def _l3_failures(ctx: StageContext) -> frozenset[str]:
    results = _optional(ctx, "l3") or {}
    return frozenset(fid for fid, r in results.items() if r.level == "failed")


def _eligible(ctx: StageContext) -> frozenset[str] | None:
    """The families that pass the gates this run, as stage "correct" frames them;
    None when there is no ``universe.json`` to judge by."""
    universe = _optional(ctx, "universe")
    if universe is None:
        return None
    fams = eligible_families(universe, _optional(ctx, "latin"), _optional(ctx, "licenses"))
    return frozenset(fams)


def run(ctx: StageContext) -> None:
    """Stage "membership"."""
    from tff_catalog.engine.order import published_orders

    placements = stageio.load_stage(ctx.paths, "ranks")
    # The published orders: a font the gate holds out of a short exact top sits past it, so
    # it never takes a top-100 place without a rank (methodology §6).
    orders = published_orders(placements, ctx.config.ranking.display.exact_top)
    prev = load(ctx.state.membership)
    m = update(orders, prev, ctx.config.ranking.membership, ctx.run_date)
    excluded = _l3_failures(ctx)
    eligible = _eligible(ctx)
    if eligible is not None:
        ruled_out = listed(m) - eligible
        if ruled_out:
            ctx.log.info("left at once, no longer eligible: %s", ", ".join(sorted(ruled_out)))
        excluded |= ruled_out
    m = exclude(m, excluded)
    stageio.dump_stage(ctx.paths, "membership", m)
    state.write_part(ctx.paths, "membership", stageio.encode(m), stage="membership")
    seen = first_seen(
        state.read_part(ctx.paths, "first_seen"), ctx.state.first_seen, m, ctx.run_date
    )
    state.write_part(ctx.paths, "first_seen", seen, stage="membership")
    members = m.members()
    entered = sum(1 for fid in members if m.catalog[fid].entered == ctx.run_date)
    left = sum(1 for s in m.catalog.values() if not s.member)
    ctx.log.info("catalog: %d members, %d entered, %d left", len(members), entered, left)
