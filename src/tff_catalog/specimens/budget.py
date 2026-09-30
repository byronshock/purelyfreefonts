"""The specimen budget (design-m2 §3). Owner: agent A5.

At least half the files at or under 5 KB gzip -9, none over 16 KB gzip -9 (those
are re-rendered with the name only and flagged ``specimen_name_only``; the owner's
site ruling of 2026-09-30, specimen_max_size), and under 10 MB in total.
Projected: about 4.5 MB raw, 1.5 MB gzip.

Sizes are decimal (1 KB = 1,000 bytes), as in ``tff_site.budgets``. The 5 KB
share and the 16 KB cap are measured gzip -9 (fixed mtime), about what a visitor
downloads; the 10 MB total is raw bytes. An empty directory passes.
"""

import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tff_catalog.specimens import MAX_FILE_GZIP_BYTES, MAX_TOTAL_BYTES, SMALL_GZIP_BYTES

if TYPE_CHECKING:
    from tff_catalog.stages import StageContext

MIN_SMALL_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class BudgetReport:
    files: int
    small_share: float  # share of files at or under SMALL_GZIP_BYTES gzipped
    over_limit: tuple[str, ...]  # file names over MAX_FILE_GZIP_BYTES gzipped
    total_bytes: int

    @property
    def ok(self) -> bool:
        return (
            self.small_share >= MIN_SMALL_SHARE
            and not self.over_limit
            and self.total_bytes < MAX_TOTAL_BYTES
        )

    def summary(self) -> str:
        """One line for people: every measure against its limit, then ok or FAILED."""
        return (
            f"specimens: {self.files} files; {self.small_share:.0%} at or under "
            f"{SMALL_GZIP_BYTES} B gzip -9 (need {MIN_SMALL_SHARE:.0%}); "
            f"{len(self.over_limit)} over {MAX_FILE_GZIP_BYTES} B gzip -9; "
            f"{self.total_bytes} B in total (limit {MAX_TOTAL_BYTES} B): "
            f"{'ok' if self.ok else 'FAILED'}"
        )


def gzip_size(data: bytes) -> int:
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def check(directory: Path) -> BudgetReport:
    """Measure every ``*.svg`` in ``directory``."""
    paths = sorted(directory.glob("*.svg")) if directory.is_dir() else []
    small = total = 0
    over: list[str] = []
    for path in paths:
        data = path.read_bytes()
        total += len(data)
        size = gzip_size(data)
        small += size <= SMALL_GZIP_BYTES
        if size > MAX_FILE_GZIP_BYTES:
            over.append(path.name)
    share = small / len(paths) if paths else 1.0
    return BudgetReport(len(paths), share, tuple(over), total)


def cmd_check(ctx: StageContext) -> int:
    """``tff-catalog specimens --check``: non-zero when the budget fails."""
    report = check(ctx.paths.specimens)
    print(report.summary())
    for name in report.over_limit:
        print(f"  over {MAX_FILE_GZIP_BYTES} B gzip -9: {name}")
    return 0 if report.ok else 1
