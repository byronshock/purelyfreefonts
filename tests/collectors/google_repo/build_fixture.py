"""Rebuild the google_repo fixture: recorded API answers, snapshot and golden file.

Run from the repository root after changing anything under
``tests/fixtures/collectors/google_repo/git/``::

    uv run python -m tests.collectors.google_repo.build_fixture

The files under ``git/github.com/google/fonts/`` become a local repository with
a stable commit (``mockhttp.git_remotes``, dated ``DAY``). This script answers
GitHub's git data API for that commit from the repository itself (the commit,
the root tree and each license folder's recursive tree, with real blob shas and
sizes) into ``http/``, runs ``fetch()`` offline into ``snapshot/``, and rewrites
``expected.jsonl``. Review the diff before committing it.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock, jsonio
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.universe.google_repo import API_HOST, COLLECTOR
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.store import RawDir, Snapshot, Store

DAY = date(2026, 10, 3)  # the snapshot date; it also dates the fixture commit
FIXTURE = regen.fixture_dir(COLLECTOR.name)
GIT = FIXTURE / "git"
HTTP = FIXTURE / "http"
REPO = "github.com/google/fonts"
API = f"https://{API_HOST}/repos/google/fonts/git"
FOLDERS = ("ofl", "apache", "ufl")
TOKEN = "fixture-token"  # GITHUB_TOKEN during fetch; never a real one
LOG = logging.getLogger("tests.google_repo")


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["git", "-C", str(repo), *args], env=env, check=True, capture_output=True, text=True
    )
    return done.stdout


def _entries(repo: Path, tree: str, *, recursive: bool) -> list[dict[str, Any]]:
    """``git ls-tree -l`` of ``tree`` as GitHub's tree API lists it (paths relative to it)."""
    args = ["ls-tree", "-z", "-l", *(["-r", "-t"] if recursive else []), tree]
    out = []
    for line in _git(repo, *args).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, kind, sha, size = meta.split()
        entry: dict[str, Any] = {"path": path, "mode": mode, "type": kind, "sha": sha}
        if kind == "blob":
            entry["size"] = int(size)
            entry["url"] = f"{API}/blobs/{sha}"
        else:
            entry["url"] = f"{API}/trees/{sha}"
        out.append(entry)
    return out


def _tree_doc(repo: Path, tree: str, *, recursive: bool) -> dict[str, Any]:
    return {
        "sha": tree,
        "url": f"{API}/trees/{tree}",
        "tree": _entries(repo, tree, recursive=recursive),
        "truncated": False,
    }


def api_answers(repo: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """``(url, body file name, JSON body)`` for each API request fetch() makes for ``repo``."""
    commit = _git(repo, "rev-parse", "HEAD").strip()
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    stamp = datetime.fromisoformat(_git(repo, "log", "-1", "--format=%cI").strip())
    when = clock.iso_utc(stamp)
    described = {
        "sha": commit,
        "url": f"{API}/commits/{commit}",
        "author": {"date": when},
        "committer": {"date": when},
        "tree": {"sha": tree, "url": f"{API}/trees/{tree}"},
        "message": f"fixture {REPO}",
        "parents": [],
    }
    root = _tree_doc(repo, tree, recursive=False)
    answers = [
        (f"{API}/commits/{commit}", "commit.json", described),
        (f"{API}/trees/{tree}", "tree-root.json", root),
    ]
    subtrees = {e["path"]: e["sha"] for e in root["tree"] if e["type"] == "tree"}
    for folder in FOLDERS:
        if folder not in subtrees:  # a test removed it; fetch() must fail on it
            continue
        sha = subtrees[folder]
        doc = _tree_doc(repo, sha, recursive=True)
        answers.append((f"{API}/trees/{sha}?recursive=1", f"tree-{folder}.json", doc))
    return answers


def write_http(repo: Path, directory: Path = HTTP) -> None:
    """Record the API answers for ``repo`` as a mockhttp fixture in ``directory``."""
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    index = []
    for url, name, doc in api_answers(repo):
        (directory / name).write_bytes(jsonio.pretty_bytes(doc))
        index.append({"url": url, "headers": {"content-type": "application/json"}, "body": name})
    mockhttp.write_index(directory, index)


@contextmanager
def environment(values: Mapping[str, str]) -> Iterator[None]:
    """Set environment variables for the ``with`` block (outside pytest's monkeypatch)."""
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run_fetch(
    tmp: Path, http: Path = HTTP, *, previous: Snapshot | None = None, settings: object = None
) -> Snapshot:
    """Run ``fetch()`` offline (git remotes and ``http``) into a fresh store under ``tmp``.

    The caller provides the git environment (``git_env``) and ``GITHUB_TOKEN``.
    """
    settings = settings or load_settings(COLLECTOR, Paths.for_root(ROOT))
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    with (
        clock.frozen(datetime.combine(DAY, time(6), tzinfo=UTC)),
        Fetcher(
            transport=mock.transport, min_interval=dict.fromkeys(COLLECTOR.hosts, 0.0), log=LOG
        ) as fetcher,
        store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=DAY,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp / "raw"),
                previous=previous,
                settings=settings,
                log=LOG,
            )
        )
    assert not mock.unmatched, mock.unmatched
    snap = store.snapshot(COLLECTOR.name, DAY)
    assert snap is not None
    return snap


def git_env(tmp: Path) -> dict[str, str]:
    """The environment that serves ``git/`` as github.com (plus the fixture token)."""
    env = mockhttp.git_remotes(GIT, tmp / "remotes", DAY)
    return env | {"GITHUB_TOKEN": TOKEN, "GIT_ALLOW_PROTOCOL": "file"}


def fixture_repo(tmp: Path) -> Path:
    """The local repository ``git_env(tmp)`` built for google/fonts."""
    return tmp / "remotes" / REPO


def main() -> int:
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        with environment(git_env(tmp)):
            write_http(fixture_repo(tmp))
            snap = run_fetch(tmp)
        target = FIXTURE / "snapshot"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(snap.path, target)
    regen.regen(COLLECTOR.name)
    print(f"rebuilt {FIXTURE.relative_to(ROOT)}: http/, snapshot/, expected.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
