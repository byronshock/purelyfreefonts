"""Rebuild the fontsource_stats contract fixture from a saved ``/v1/stats`` body.

The fixture is trimmed real data (ruling T1): the ``IDS`` rows of a real
``https://api.fontsource.org/v1/stats`` answer, unchanged, served with the
CDN's ``Age`` header of that answer. ``snapshot/`` is what ``fetch()`` makes
of it on ``DAY`` (clock frozen at 06:00 UTC, as the contract test runs it),
and ``expected.jsonl`` is its parse. The real body is not in the repository;
pass the saved one, then review the diff::

    uv run python -m tests.collectors.fontsource_stats.build_fixture <stats.json>
    uv run python -m tests.helpers.regen fontsource_stats

``run_fetch`` is shared with the collector's own tests.
"""

import json
import logging
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime, time
from pathlib import Path

from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.fontsource_stats import COLLECTOR, HOST, URL
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
HTTP = FIXTURE / "http"
BODY = "stats.json"
DAY = date(2026, 9, 26)  # the answer was fetched 2026-09-26T01:56Z
AGE = "55155"  # its Age header: Fontsource's server made it 2026-09-25 about 10:37 UTC
LOG = logging.getLogger("tests.fontsource_stats")

# Kept ids: the largest packages, a static-only font, a legacy id and its successor,
# an icon font, two retired ids (one with 0 hits) and one with no hits or downloads.
IDS = (
    "42dot-sans",
    "abeezee",
    "andada",
    "fira-code",
    "geist-pixel",
    "inter",
    "jetbrains-mono",
    "league-gothic-condensed",
    "material-icons",
    "noto-sans-jp",
    "open-sans",
    "roboto",
    "source-sans-3",
    "source-sans-pro",
)


def write_http(directory: Path, body: bytes, headers: dict[str, str] | None = None) -> Path:
    """A one-response mockhttp fixture serving ``body`` at ``URL``."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / BODY).write_bytes(body)
    sent = {"content-type": "application/json"} | (headers or {})
    mockhttp.write_index(directory, [{"url": URL, "headers": sent, "body": BODY}])
    return directory


def run_fetch(
    tmp: Path,
    http: Path,
    settings: object,
    *,
    day: date = DAY,
    previous: Snapshot | None = None,
) -> Snapshot:
    """Run ``fetch()`` offline against ``http`` into a fresh store under ``tmp``."""
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(transport=mock.transport, min_interval={HOST: 0.0}, log=LOG) as fetcher,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp / "raw"),
                previous=previous,
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None, "fetch() left no complete snapshot"
    return snap


def trim(doc: dict[str, object]) -> bytes:
    """The ``IDS`` rows of a real answer, unchanged, in its compact form."""
    missing = [i for i in IDS if i not in doc]
    if missing:
        raise SystemExit(f"the answer lacks {missing}")
    return json.dumps({i: doc[i] for i in IDS}, separators=(",", ":")).encode()


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    doc = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    shutil.rmtree(HTTP, ignore_errors=True)
    write_http(HTTP, trim(doc), {"age": AGE, "cache-control": "public, max-age=300"})
    settings = load_settings(COLLECTOR, Paths.for_root(ROOT))
    with tempfile.TemporaryDirectory() as tmp:
        snap = run_fetch(Path(tmp), HTTP, settings)
        shutil.rmtree(FIXTURE / "snapshot", ignore_errors=True)
        shutil.copytree(snap.path, FIXTURE / "snapshot")
    print(f"wrote {HTTP.relative_to(ROOT)} and {(FIXTURE / 'snapshot').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
