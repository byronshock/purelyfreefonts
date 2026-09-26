"""The specimen budget: half at or under 5 KB gzip -9, none over 30 KB, under 10 MB in all."""

import hashlib
import logging
from datetime import date
from pathlib import Path

import pytest

from tff_catalog.paths import Paths
from tff_catalog.specimens import MAX_FILE_BYTES, MAX_TOTAL_BYTES, SMALL_GZIP_BYTES, budget
from tff_catalog.stages import StageContext
from tff_catalog.state import State


def small_svg() -> bytes:
    return b'<svg xmlns="http://www.w3.org/2000/svg"><path d="m0 0h10v10h-10z"/></svg>\n'


def incompressible(size: int, seed: int = 0) -> bytes:
    """``size`` pseudo-random bytes: gzip can't shrink them."""
    return hashlib.shake_256(seed.to_bytes(4, "big")).digest(size)


def write(directory: Path, name: str, data: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)


def test_empty_or_missing_directory_passes(tmp_path: Path) -> None:
    for directory in (tmp_path / "missing", tmp_path):
        report = budget.check(directory)
        assert report == budget.BudgetReport(0, 1.0, (), 0)
        assert report.ok


def test_measures_share_over_limit_and_total(tmp_path: Path) -> None:
    write(tmp_path, "a.svg", small_svg())
    write(tmp_path, "b.svg", small_svg())
    write(tmp_path, "c.svg", incompressible(SMALL_GZIP_BYTES + 200))
    write(tmp_path, "d.svg", incompressible(MAX_FILE_BYTES + 1, seed=1))
    write(tmp_path, "notes.txt", incompressible(MAX_FILE_BYTES * 2))  # not a specimen
    report = budget.check(tmp_path)
    assert report.files == 4
    assert report.small_share == 0.5
    assert report.over_limit == ("d.svg",)
    assert report.total_bytes == 2 * len(small_svg()) + SMALL_GZIP_BYTES + 200 + MAX_FILE_BYTES + 1
    assert not report.ok


def test_exactly_at_the_limits_passes(tmp_path: Path) -> None:
    write(tmp_path, "a.svg", small_svg())
    write(tmp_path, "b.svg", incompressible(MAX_FILE_BYTES))
    report = budget.check(tmp_path)
    assert (report.small_share, report.over_limit) == (0.5, ())
    assert report.ok


@pytest.mark.parametrize(
    ("share", "over", "total", "ok"),
    [
        (0.5, (), MAX_TOTAL_BYTES - 1, True),
        (0.49, (), 10, False),
        (1.0, ("x.svg",), 10, False),
        (1.0, (), MAX_TOTAL_BYTES, False),  # "under 10 MB"
    ],
)
def test_ok(share: float, over: tuple[str, ...], total: int, ok: bool) -> None:
    assert budget.BudgetReport(3, share, over, total).ok is ok


def _ctx(root: Path) -> StageContext:
    return StageContext(
        paths=Paths.for_root(root),
        config=None,  # type: ignore[arg-type]
        state=State(),
        run_date=date(2026, 9, 25),
        store=None,
        fetcher=None,
        log=logging.getLogger("tests.specimens"),
    )


def test_cmd_check_exit_code_and_report(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    ctx = _ctx(tmp_path)
    write(ctx.paths.specimens, "a.svg", small_svg())
    assert budget.cmd_check(ctx) == 0
    assert capsys.readouterr().out.rstrip().endswith(": ok")
    write(ctx.paths.specimens, "big.svg", incompressible(MAX_FILE_BYTES + 1))
    assert budget.cmd_check(ctx) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "big.svg" in out


def test_never_laxer_than_the_site_check() -> None:
    """``tff-site check`` measures the same budget; what passes here must pass there."""
    from tff_site import budgets as site

    assert SMALL_GZIP_BYTES <= site.SPECIMEN_HALF_MAX
    assert MAX_FILE_BYTES <= site.SPECIMEN_MAX
    assert MAX_TOTAL_BYTES <= site.SPECIMENS_TOTAL_RAW_MAX
