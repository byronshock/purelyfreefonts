"""nolicense: what-if orders for families with no license found (owner ruling of 2026-09-26)."""

import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from tff_catalog import corrections, jsonio, licenses, nolicense, stageio, surveys
from tff_catalog.engine.order import Placement
from tff_catalog.latin import LatinResult
from tff_catalog.licenses import LicenseClass, Queue, Verdict
from tff_catalog.paths import Paths
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe

DAY = date(2026, 10, 1)
LOG = logging.getLogger("test.nolicense")
NO_LICENSE = LicenseClass("NOASSERTION", "excluded", reason=licenses.NO_LICENSE_REASON)
OFL = LicenseClass("OFL-1.1", "allowed", group="open-font", redistributable=True)


def verdict(fid: str, lic: LicenseClass, *, seen: bool = False) -> Verdict:
    facts = (("fontsource", lic.spdx),) if seen else ()
    return Verdict(fid, None if not seen else lic.spdx, lic, False, facts)


def family(fid: str, drop: str | None = None) -> Family:
    return Family(fid, fid.title(), (), (), DAY, fid, drop)


def build(root: Path) -> StageContext:
    """A build with five families: only "unlicensed" is a candidate."""
    paths = Paths.for_root(root)
    fids = ("unlicensed", "ruled", "not-latin", "dropped", "licensed")
    stageio.dump_stage(
        paths,
        "universe",
        Universe(
            families={f: family(f, "icon" if f == "dropped" else None) for f in fids},
            unmapped=(),
        ),
    )
    passes = LatinResult(True, "glyph_test", "extended")
    fails = LatinResult(False, None, None, "latin_share")
    stageio.dump_stage(paths, "latin", {f: fails if f == "not-latin" else passes for f in fids})
    ruled = LicenseClass("OFL-1.1", "allowed", reason="owner ruling LIC-ruled (2026-10-01): a")
    stageio.dump_stage(
        paths,
        "licenses",
        {
            "unlicensed": verdict("unlicensed", NO_LICENSE),
            "ruled": verdict("ruled", ruled),
            "not-latin": verdict("not-latin", NO_LICENSE),
            "dropped": verdict("dropped", NO_LICENSE),
            "licensed": verdict("licensed", OFL, seen=True),
        },
    )
    queue = Queue(1, (), {}, ("dropped", "not-latin", "ruled", "unlicensed"))
    stageio.dump(queue, licenses.queue_path(paths))
    jsonio.dump({"kept": True}, paths.next_state / "stale.json")
    return StageContext(
        paths=paths,
        config=SimpleNamespace(),  # type: ignore[arg-type]
        state=State(),
        run_date=DAY,
        store=None,
        fetcher=None,
        log=LOG,
    )


def test_candidates_are_unlicensed_latin_families_nobody_has_ruled_on(tmp_path: Path) -> None:
    ctx = build(tmp_path)
    assert nolicense.candidates(ctx) == ["unlicensed"]


def test_no_queue_means_no_candidates(tmp_path: Path) -> None:
    ctx = build(tmp_path)
    licenses.queue_path(ctx.paths).unlink()
    assert nolicense.candidates(ctx) == []
    assert nolicense.what_if(ctx) == ()


def test_what_if_reruns_on_a_copy_and_leaves_the_build_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = build(tmp_path)
    real = stageio.stage_path(ctx.paths, "licenses").read_bytes()
    seen: list[str] = []

    def fake_correct(copy: StageContext) -> None:
        assert copy.paths.build != ctx.paths.build
        assert copy.paths.config == ctx.paths.config  # config, data and state are read in place
        assert copy.store is None  # stage "rank" keeps no private history from a what-if
        assert jsonio.load(copy.paths.next_state / "stale.json") == {"kept": True}
        got = stageio.load_stage(copy.paths, "licenses")
        assert got["unlicensed"].license.status == "allowed"
        assert got["unlicensed"].license.reason == nolicense.WHAT_IF_REASON
        assert got["licensed"] == verdict("licensed", OFL, seen=True)
        seen.append("correct")

    def fake_rank(copy: StageContext) -> None:
        overall = {
            "licensed": Placement(1, 1, None, False),
            "unlicensed": Placement(650, None, "501+", False),
        }
        stageio.dump_stage(copy.paths, "ranks", {"overall": overall, "project": {}})
        seen.append("rank")

    monkeypatch.setattr(corrections, "run", fake_correct)
    monkeypatch.setattr(surveys, "run", fake_rank)
    [row] = nolicense.what_if(ctx)
    assert seen == ["correct", "rank"]
    assert row == nolicense.NoLicense(
        "unlicensed",
        {"overall": 650, "desktop_chosen": None, "project": None, "coding": None},
    )
    assert row.to_research
    assert stageio.stage_path(ctx.paths, "licenses").read_bytes() == real
    assert not stageio.stage_path(ctx.paths, "ranks").exists()


@pytest.mark.parametrize(
    ("order", "research"),
    [(1, True), (nolicense.RESEARCH_TOP, True), (nolicense.RESEARCH_TOP + 1, False), (None, False)],
)
def test_research_threshold_is_the_owners_700(order: int | None, research: bool) -> None:
    assert nolicense.RESEARCH_TOP == 700
    assert nolicense.NoLicense("x", {"overall": order}).to_research is research
