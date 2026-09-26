"""Rebuild the github_releases contract fixture from its trimmed source data.

Usage, from the repository root::

    uv run python -m tests.collectors.github_releases.build_fixture

``tests/fixtures/collectors/github_releases/source.json`` holds trimmed real
releases (ruling T1) of a few repos. This serves them through ``FakeGitHub``
to the collector's ``fetch()``, with the settings in
``config/sources/github_releases.toml`` (every other listed repo answers with
no releases), exactly as the contract test runs it: clock frozen at 06:00 UTC
on the fixture's date, a fixture token, no previous snapshot. It then writes:

- ``http/``: every request and answer, for ``mockhttp``;
- ``snapshot/``: the snapshot the fetch wrote;
- ``expected.jsonl``: its parse (``tests.helpers.regen``).

Run it after changing the repo list or the fetch; review the diff.
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from tests.collectors.github_releases.fakegithub import FakeGitHub, Recorder, release
from tests.helpers import ROOT, regen

from tff_catalog import clock
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.ranking.github_releases import COLLECTOR
from tff_catalog.fetch import Budget, Fetcher
from tff_catalog.paths import Paths
from tff_catalog.store import RawDir, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
SOURCE = FIXTURE / "source.json"
TOKEN = "fixture-token"  # tests/collectors/test_contract.py FIXTURE_TOKEN
LOG = logging.getLogger("tests.github_releases.build_fixture")


def load_source(path: Path = SOURCE) -> tuple[date, dict[str, list[dict[str, Any]]]]:
    """The fixture's date and its releases as REST objects, per repo (newest first)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    repos = {
        repo: [
            release(
                r["tag"],
                [tuple(a) for a in r["assets"]],
                repo=repo,
                published=r["published_at"],
                prerelease=r["prerelease"],
            )
            for r in rels
        ]
        for repo, rels in doc["repos"].items()
    }
    return date.fromisoformat(doc["date"]), repos


def record(day: date, fake: FakeGitHub, work: Path) -> tuple[Recorder, Path]:
    """Run ``fetch()`` against ``fake``; return the recorder and the snapshot directory."""
    recorder = Recorder(fake)
    settings = load_settings(COLLECTOR, Paths.for_root(ROOT))
    store = Store(work / "store")
    with (
        clock.frozen(datetime.combine(day, time(6), tzinfo=UTC)),
        Fetcher(
            transport=recorder.transport,
            min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0),
            budgets=(Budget("github", 5000),),
            log=LOG,
        ) as fetcher,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(work / "raw"),
                previous=None,
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    return recorder, snap.path


def build(fixture: Path = FIXTURE) -> None:
    """Rewrite ``http/``, ``snapshot/`` and ``expected.jsonl`` under ``fixture``.

    ``fixture`` is a directory named after the collector (``regen`` finds it by name).
    """
    day, repos = load_source(fixture / "source.json")
    old = os.environ.get("GITHUB_TOKEN")  # GraphQL repos need one; never a real one here
    os.environ["GITHUB_TOKEN"] = TOKEN
    try:
        with tempfile.TemporaryDirectory() as tmp:
            recorder, snapshot = record(day, FakeGitHub(repos), Path(tmp))
            for part in ("http", "snapshot"):
                shutil.rmtree(fixture / part, ignore_errors=True)
            recorder.write(fixture / "http")
            shutil.copytree(snapshot, fixture / "snapshot")
    finally:
        if old is None:
            os.environ.pop("GITHUB_TOKEN", None)
        else:
            os.environ["GITHUB_TOKEN"] = old
    regen.regen(COLLECTOR.name, base=fixture.parent)


if __name__ == "__main__":
    build()
    print(f"rebuilt {FIXTURE.relative_to(ROOT)}")
