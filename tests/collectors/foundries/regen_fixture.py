"""Rebuild the foundries contract fixture from its synthetic list.

The contract test runs ``fetch()`` with the real settings, and ``fetch()``
reads the list under ``FetchContext.paths``, which the contract test points at
the fixture's ``root/``. So the fixture carries its own small synthetic list,
``root/config/foundries.toml``, and edits to the real ``config/foundries.toml``
never touch it. ``http/`` answers a synthetic HEAD 200 for every family URL on
a checked host, ``snapshot/`` is what ``fetch()`` makes of that, and
``expected.jsonl`` is its parse. Run this after every edit of the fixture's
list, then review the diff::

    uv run python -m tests.collectors.foundries.regen_fixture

``run_fetch`` is shared with the collector's own tests.
"""

import logging
import shutil
import sys
import tempfile
from datetime import UTC, date, datetime, time
from pathlib import Path

from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.universe.foundries import (
    COLLECTOR,
    family_urls,
    host_of,
    list_path,
    load_list,
)
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
FIXTURE_ROOT = FIXTURE / "root"  # the repository the contract test's fetch() sees
DAY = date(2026, 10, 1)
LOG = logging.getLogger("tests.foundries")
HEADERS = {"content-type": "text/html; charset=utf-8"}


def run_fetch(
    tmp: Path,
    http: Path,
    settings: object,
    day: date = DAY,
    *,
    hosts: tuple[str, ...] = (),
    root: Path | None = None,
) -> tuple[Snapshot, mockhttp.MockHTTP]:
    """Run ``fetch()`` offline against ``http`` into a fresh store under ``tmp``.

    ``hosts`` (default: the collector's) scope the fetcher, as the fetch stage
    does. ``root`` is the repository ``fetch()`` reads a relative list from
    (``FetchContext.paths``); without it, ``paths.find_root()``.
    """
    mock = mockhttp.MockHTTP.from_dir(http)
    scope = hosts or COLLECTOR.hosts
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(transport=mock.transport, min_interval=dict.fromkeys(scope, 0.0), log=LOG) as f,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=f.scoped(scope),
                out=writer,
                raw=RawDir(tmp / "raw"),
                previous=None,
                settings=settings,
                log=LOG,
                paths=Paths.for_root(root) if root is not None else None,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None, "fetch() left no complete snapshot"
    return snap, mock


def checked_urls(root: Path = FIXTURE_ROOT) -> list[str]:
    """The family URLs of ``root``'s list that ``fetch()`` requests (host in ``hosts``).

    The settings are always the real ones (``config/sources/foundries.toml``).
    """
    settings = load_settings(COLLECTOR, Paths.for_root(ROOT))
    urls = family_urls(load_list(list_path(settings, root)))
    return [u for u in urls if host_of(u) in COLLECTOR.hosts]


def write_http(directory: Path, urls: list[str]) -> None:
    """A mockhttp index answering HEAD 200 (no body) for each URL."""
    shutil.rmtree(directory, ignore_errors=True)
    entries = [{"method": "HEAD", "url": u, "headers": HEADERS} for u in urls]
    mockhttp.write_index(directory, entries)


def build(fixture: Path = FIXTURE) -> Path:
    """Rewrite ``http/``, ``snapshot/`` and ``expected.jsonl`` of ``fixture`` from its
    ``root/config/foundries.toml``; return it."""
    paths = Paths.for_root(ROOT)
    root = fixture / "root"
    write_http(fixture / "http", checked_urls(root))
    with tempfile.TemporaryDirectory() as tmp:
        settings = load_settings(COLLECTOR, paths)
        snap, mock = run_fetch(Path(tmp), fixture / "http", settings, root=root)
        assert not mock.unmatched, mock.unmatched
        target = fixture / "snapshot"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(snap.path, target)
    regen.regen(COLLECTOR.name, paths=paths, base=fixture.parent)
    return fixture


def main() -> int:
    fixture = build()
    print(f"rebuilt {fixture.relative_to(ROOT)}; review the diff before committing it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
