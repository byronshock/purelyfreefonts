"""Rebuild ``tests/fixtures/collectors/fontist/snapshot/`` from the fixture's git remote.

Run from the repository root after editing a formula under the fixture's ``git/``::

    uv run python tests/collectors/fontist/regen_snapshot.py
    uv run python -m tests.helpers.regen fontist

It runs ``fetch()`` offline, exactly as the contract test does, and copies the
snapshot it writes; the second command rewrites ``expected.jsonl``.
"""

import logging
import os
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tests.helpers import mockhttp, regen  # noqa: E402

from tff_catalog import clock, jsonio  # noqa: E402
from tff_catalog.collectors.base import FetchContext, load_settings  # noqa: E402
from tff_catalog.collectors.universe.fontist import COLLECTOR  # noqa: E402
from tff_catalog.fetch import Fetcher  # noqa: E402
from tff_catalog.paths import Paths  # noqa: E402
from tff_catalog.store import MANIFEST_NAME, RawDir, Store  # noqa: E402

FIXTURE = regen.fixture_dir(COLLECTOR.name)
DEFAULT_DAY = date(2026, 10, 3)


def main() -> None:
    target = FIXTURE / "snapshot"
    manifest = target / MANIFEST_NAME
    day = date.fromisoformat(jsonio.load(manifest)["date"]) if manifest.is_file() else None
    day = day or DEFAULT_DAY
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        os.environ.update(mockhttp.git_remotes(FIXTURE / "git", tmp / "remotes", day))
        store = Store(tmp / "store")
        with (
            clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
            Fetcher(transport=mockhttp.MockHTTP(()).transport) as fetcher,
            store.writer(COLLECTOR.name, day, COLLECTOR.version) as out,
        ):
            COLLECTOR.fetch(
                FetchContext(
                    run_date=day,
                    fetcher=fetcher.scoped(COLLECTOR.hosts),
                    out=out,
                    raw=RawDir(tmp / "raw"),
                    previous=None,
                    settings=load_settings(COLLECTOR, Paths.for_root(ROOT)),
                    log=regen.LOG,
                )
            )
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(tmp / "store" / COLLECTOR.name / day.isoformat(), target)
    print(f"{target.relative_to(ROOT)}: rewritten for {day}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
