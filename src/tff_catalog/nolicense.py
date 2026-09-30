"""What-if orders for families with no license found (gate LIC, owner ruling of 2026-09-26).

A family for which no source states any license is excluded before ranking (D3,
"no license found"; methodology §1: filter first), so the ranks never show how
far it would get. The owner's ruling ``no_license_families`` keeps that
exclusion, but Claude researches any such family that would reach the overall
top ``RESEARCH_TOP`` (700).

``what_if`` finds those families. It copies this run's ``build/stage/`` and
``build/state/`` into a temporary build directory, marks the families' licenses
allowed there, and reruns stages "correct" and "rank" on the copy, with no
snapshot store (so stage "rank" keeps no private history) and a silent log.
Nothing in the real build changes. Each family's orders in that run are what it
would get if its license were found; the other fonts move a little too, which
the orders here ignore. Stage "review" lists the result in the review pack
(``no-license.md``); a license Claude finds goes to the owner as a gate LIC
family question (``LIC-<family id>``, with ``spdx``).

Candidates (``candidates``) are the licenses queue's ``no_license`` families
that pass the Latin gate and have no drop reason. The rerun takes about as long
as the two stages do in a normal run (15 seconds on the 2026-09-26 build).
"""

import dataclasses
import logging
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import stageio

if TYPE_CHECKING:
    from tff_catalog.engine.order import Placement
    from tff_catalog.stages import StageContext

# Owner ruling of 2026-09-26 (licenses, no_license_families): Claude researches a family with
# no license found that would reach this overall order or better.
RESEARCH_TOP = 700
# The orders the pack shows for each family: overall first, then its two halves (D12).
KEYS = ("overall", "desktop_chosen", "project", "coding")
# What the copy says about each family's license; never written outside the copy.
WHAT_IF_REASON = "what-if: no license found, counted as allowed to find its order"


@dataclass(frozen=True, slots=True)
class NoLicense:
    """A family with no license found and the orders it would get (None: not ranked)."""

    id: str
    orders: Mapping[str, int | None]  # by rank key (``KEYS``)

    @property
    def overall(self) -> int | None:
        return self.orders.get("overall")

    @property
    def to_research(self) -> bool:
        """Whether the ruling asks Claude to research it: overall ``RESEARCH_TOP`` or better."""
        return self.overall is not None and self.overall <= RESEARCH_TOP


def candidates(ctx: StageContext) -> list[str]:
    """The licenses queue's ``no_license`` families that pass the Latin gate, have no drop
    reason and are still excluded as "no license found" (an owner ruling on the family
    takes it off the list), sorted; [] when stage "licenses" has not written its queue."""
    from tff_catalog import licenses

    if not licenses.queue_path(ctx.paths).is_file():
        return []
    queued = licenses.load_queue(ctx.paths).no_license
    latin = _optional(ctx, "latin") or {}
    verdicts = _optional(ctx, "licenses") or {}
    universe = _optional(ctx, "universe")
    eligible = universe.eligible() if universe is not None else {}

    def unlicensed(fid: str) -> bool:
        lic = getattr(verdicts.get(fid), "license", None)
        return lic is not None and lic.reason == licenses.NO_LICENSE_REASON

    return sorted(
        f for f in queued if f in eligible and f in latin and latin[f].latin and unlicensed(f)
    )


def _optional(ctx: StageContext, name: str) -> Any:
    path = stageio.stage_path(ctx.paths, name)
    return stageio.load_stage(ctx.paths, name) if path.is_file() else None


def _silent() -> logging.Logger:
    """A logger outside the tree, so the rerun's warnings never reach the run's log."""
    log = logging.Logger("tff_catalog.nolicense.what_if")
    log.setLevel(logging.CRITICAL + 1)
    return log


def _allow(ctx: StageContext, fids: Iterable[str]) -> None:
    """Mark ``fids`` allowed in ``ctx``'s licenses.json (a copy's, never the real one)."""
    from tff_catalog.licenses import LicenseClass

    verdicts = dict(stageio.load_stage(ctx.paths, "licenses"))
    for fid in fids:
        v = verdicts[fid]
        lic = LicenseClass(
            v.spdx or "NOASSERTION",
            "allowed",
            group="open-font",
            redistributable=True,
            attribution_required=False,
            reason=WHAT_IF_REASON,
        )
        verdicts[fid] = dataclasses.replace(v, license=lic)
    stageio.dump_stage(ctx.paths, "licenses", verdicts)


def what_if(ctx: StageContext, fids: Iterable[str] | None = None) -> tuple[NoLicense, ...]:
    """The orders each family (default: ``candidates``) would get with its license
    allowed, from a rerun of stages "correct" and "rank" on a copy of the build."""
    from tff_catalog import corrections, surveys

    wanted = sorted(candidates(ctx) if fids is None else set(fids))
    if not wanted:
        return ()
    with tempfile.TemporaryDirectory(prefix="tff-nolicense-") as tmp:
        build = Path(tmp) / "build"
        shutil.copytree(ctx.paths.stage, build / "stage")
        if ctx.paths.next_state.is_dir():
            shutil.copytree(ctx.paths.next_state, build / "state")
        copy = dataclasses.replace(
            ctx, paths=ctx.paths.with_(build=build), store=None, fetcher=None, log=_silent()
        )
        _allow(copy, wanted)
        corrections.run(copy)
        surveys.run(copy)
        placements = stageio.load_stage(copy.paths, "ranks")
    return tuple(NoLicense(fid, {k: _order(placements, k, fid) for k in KEYS}) for fid in wanted)


def _order(placements: Mapping[str, Mapping[str, Placement]], key: str, fid: str) -> int | None:
    p = placements.get(key, {}).get(fid)
    return None if p is None else p.order
