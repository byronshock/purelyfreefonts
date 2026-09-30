"""Tests for ``tff_catalog.gitsrc`` against a local bare repository (file:// only).

The remote has three commits with fixed dates:

- c1 (2024-01-01): four METADATA-style files, a font and a license text;
- c2 (2024-02-01 23:30 -05:00, so 2024-02-02 UTC): deletes ``ofl/b/METADATA.pb``
  and renames ``ofl/a/OFL.txt`` to ``LICENSE.txt``; tagged ``v1`` (annotated);
- c3 (2024-03-01): edits ``ofl/a/METADATA.pb`` and deletes ``apache/c/METADATA.pb``.

Branch ``feature`` points at c1.
"""

import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from tff_catalog import gitsrc
from tff_catalog.gitsrc import DeletedPath, GitError

BINARY = b"\x00\x01\x00\x00fake font\r\n\xff"
_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "tff test",
    "GIT_AUTHOR_EMAIL": "test@trulyfreefonts.invalid",
    "GIT_COMMITTER_NAME": "tff test",
    "GIT_COMMITTER_EMAIL": "test@trulyfreefonts.invalid",
}


@dataclass(frozen=True)
class Remote:
    url: str
    path: Path
    c1: str
    c2: str
    c3: str


def git(*args: str, cwd: Path, when: str | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | _ENV
    env["GIT_ALLOW_PROTOCOL"] = "file"
    if when:
        env |= {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, check=True)
    return done.stdout.decode().strip()


def write(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)


@pytest.fixture(scope="module")
def remote(tmp_path_factory: pytest.TempPathFactory) -> Remote:
    base = tmp_path_factory.mktemp("git")
    work = base / "work"
    work.mkdir()
    git("init", "-q", "-b", "main", cwd=work)
    write(
        work,
        {
            "ofl/a/METADATA.pb": b'name: "A"\n',
            "ofl/b/METADATA.pb": b'name: "B"\n',
            "ofl/a/OFL.txt": b"license\n",
            "ofl/a/A.ttf": BINARY,
            "apache/c/METADATA.pb": b'name: "C"\n',
            "README.md": b"readme\n",
        },
    )
    git("add", "-A", cwd=work)
    git("commit", "-qm", "one", cwd=work, when="2024-01-01T12:00:00+00:00")
    c1 = git("rev-parse", "HEAD", cwd=work)
    git("branch", "feature", cwd=work)
    git("rm", "-q", "ofl/b/METADATA.pb", cwd=work)
    git("mv", "ofl/a/OFL.txt", "ofl/a/LICENSE.txt", cwd=work)
    git("commit", "-qm", "two", cwd=work, when="2024-02-01T23:30:00-05:00")
    c2 = git("rev-parse", "HEAD", cwd=work)
    git("tag", "-a", "v1", "-m", "v1", cwd=work, when="2024-02-02T00:00:00+00:00")
    write(work, {"ofl/a/METADATA.pb": b'name: "A2"\n'})
    git("rm", "-q", "apache/c/METADATA.pb", cwd=work)
    git("commit", "-qam", "three", cwd=work, when="2024-03-01T12:00:00+00:00")
    c3 = git("rev-parse", "HEAD", cwd=work)
    bare = base / "remote.git"
    git("clone", "-q", "--bare", str(work), str(bare), cwd=base)
    git("config", "uploadpack.allowFilter", "true", cwd=bare)
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=bare)
    return Remote(bare.as_uri(), bare, c1, c2, c3)


@pytest.fixture(autouse=True)
def quiet_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own git config out of the clones."""
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)


def checked_out(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def missing_objects(repo: Path) -> int:
    """Objects the partial clone has not downloaded yet (without fetching them)."""
    out = git("rev-list", "--objects", "--all", "--missing=print", cwd=repo)
    return sum(line.startswith("?") for line in out.splitlines())


# --- sparse_clone -------------------------------------------------------------------------------


def test_sparse_clone_checks_out_only_the_patterns(remote: Remote, tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    sha = gitsrc.sparse_clone(remote.url, dest, ["/ofl/*/METADATA.pb"])
    assert sha == remote.c3
    assert checked_out(dest) == {"ofl/a/METADATA.pb"}
    assert (dest / "ofl/a/METADATA.pb").read_bytes() == b'name: "A2"\n'
    assert git("rev-list", "--count", "HEAD", cwd=dest) == "1"  # depth 1
    assert missing_objects(dest) > 0  # the font and other blobs were never downloaded


def test_sparse_clone_pins_a_commit(remote: Remote, tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    sha = gitsrc.sparse_clone(
        remote.url, dest, ["/ofl/*/METADATA.pb", "/ofl/*/OFL.txt"], ref=remote.c1
    )
    assert sha == remote.c1
    assert checked_out(dest) == {"ofl/a/METADATA.pb", "ofl/b/METADATA.pb", "ofl/a/OFL.txt"}
    assert gitsrc.head_sha(dest) == remote.c1


def test_sparse_clone_accepts_branches_and_annotated_tags(remote: Remote, tmp_path: Path) -> None:
    assert (
        gitsrc.sparse_clone(remote.url, tmp_path / "b", ["/README.md"], ref="feature") == remote.c1
    )
    assert gitsrc.sparse_clone(remote.url, tmp_path / "t", ["/README.md"], ref="v1") == remote.c2


def test_sparse_clone_full_history(remote: Remote, tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    gitsrc.sparse_clone(remote.url, dest, ["/README.md"], depth=None)
    assert git("rev-list", "--count", "HEAD", cwd=dest) == "3"
    assert checked_out(dest) == {"README.md"}


def test_sparse_clone_refuses_a_non_empty_destination(remote: Remote, tmp_path: Path) -> None:
    (tmp_path / "clone").mkdir()
    (tmp_path / "clone" / "x").write_text("x")
    with pytest.raises(FileExistsError):
        gitsrc.sparse_clone(remote.url, tmp_path / "clone", ["/README.md"])
    empty = tmp_path / "empty"
    empty.mkdir()
    assert gitsrc.sparse_clone(remote.url, empty, ["/README.md"]) == remote.c3


def test_sparse_clone_argument_checks(remote: Remote, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pattern"):
        gitsrc.sparse_clone(remote.url, tmp_path / "a", [])
    with pytest.raises(ValueError, match="depth"):
        gitsrc.sparse_clone(remote.url, tmp_path / "b", ["/x"], depth=0)


def test_a_failed_clone_leaves_the_destination_as_it_was(remote: Remote, tmp_path: Path) -> None:
    missing = (tmp_path / "nowhere.git").as_uri()
    fresh, empty = tmp_path / "fresh", tmp_path / "empty"
    empty.mkdir()
    for dest in (fresh, empty):
        with pytest.raises(GitError):
            gitsrc.sparse_clone(missing, dest, ["/README.md"])
        with pytest.raises(GitError):
            gitsrc.blobless_clone(remote.url, dest, ref="0" * 40)
    assert not fresh.exists()
    assert list(empty.iterdir()) == []
    # so a retry into the same path works
    assert gitsrc.sparse_clone(remote.url, fresh, ["/README.md"]) == remote.c3
    assert gitsrc.blobless_clone(remote.url, empty) == remote.c3


def test_option_like_urls_and_refs_are_refused(remote: Remote, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="url"):
        gitsrc.sparse_clone("--upload-pack=touch x", tmp_path / "a", ["/README.md"])
    with pytest.raises(ValueError, match="ref"):
        gitsrc.sparse_clone(remote.url, tmp_path / "b", ["/README.md"], ref="--all")
    with pytest.raises(ValueError, match="ref"):
        gitsrc.blobless_clone(remote.url, tmp_path / "c", ref="")
    assert not (tmp_path / "a").exists()


def test_git_variables_pointing_elsewhere_are_ignored(
    remote: Remote, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside a git hook, GIT_DIR and friends name the project's repository: leave it alone."""
    victim = tmp_path / "project"
    victim.mkdir()
    git("init", "-q", "-b", "main", cwd=victim)
    (victim / "f").write_text("f")
    git("add", "f", cwd=victim)
    git("commit", "-qm", "project", cwd=victim, when="2024-05-01T00:00:00+00:00")
    head, config = git("rev-parse", "HEAD", cwd=victim), (victim / ".git/config").read_bytes()
    monkeypatch.setenv("GIT_DIR", str(victim / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(victim))
    monkeypatch.setenv("GIT_INDEX_FILE", str(victim / ".git/index"))
    clone = tmp_path / "clone"
    assert gitsrc.sparse_clone(remote.url, clone, ["/README.md"]) == remote.c3
    assert checked_out(clone) == {"README.md"}
    bare = tmp_path / "bare.git"
    assert gitsrc.blobless_clone(remote.url, bare) == remote.c3
    assert gitsrc.show(bare, "HEAD", "README.md") == b"readme\n"
    assert len(gitsrc.log_deleted(bare)) == 3
    assert gitsrc.head_sha(clone) == remote.c3
    assert git("rev-parse", "HEAD", cwd=victim) == head
    assert (victim / ".git/config").read_bytes() == config
    assert git("status", "--porcelain", cwd=victim) == ""


def test_a_plain_directory_never_answers_for_an_enclosing_repository(
    remote: Remote, tmp_path: Path
) -> None:
    outer = tmp_path / "outer"
    gitsrc.sparse_clone(remote.url, outer, ["/README.md"])
    inner = outer / "raw" / "not-a-clone"
    inner.mkdir(parents=True)
    with pytest.raises(GitError):
        gitsrc.head_sha(inner)
    with pytest.raises(FileNotFoundError):
        gitsrc.show(inner, "HEAD", "README.md")


def test_clone_failures_raise_git_error(remote: Remote, tmp_path: Path) -> None:
    missing = (tmp_path / "nowhere.git").as_uri()
    with pytest.raises(GitError, match="failed"):
        gitsrc.sparse_clone(missing, tmp_path / "a", ["/x"])
    with pytest.raises(GitError):
        gitsrc.blobless_clone(missing, tmp_path / "b")
    with pytest.raises(GitError, match="not in the history"):
        gitsrc.blobless_clone(remote.url, tmp_path / "c", ref="0" * 40)


# --- blobless_clone -----------------------------------------------------------------------------


def test_blobless_clone_is_bare_and_has_no_blobs(remote: Remote, tmp_path: Path) -> None:
    dest = tmp_path / "repo.git"
    assert gitsrc.blobless_clone(remote.url, dest) == remote.c3
    assert git("rev-parse", "--is-bare-repository", cwd=dest) == "true"
    assert git("rev-list", "--count", "HEAD", cwd=dest) == "3"
    assert missing_objects(dest) > 0


def test_blobless_clone_pins_a_commit_or_branch(remote: Remote, tmp_path: Path) -> None:
    pinned = tmp_path / "pinned.git"
    assert gitsrc.blobless_clone(remote.url, pinned, ref=remote.c2) == remote.c2
    assert gitsrc.head_sha(pinned) == remote.c2
    assert [d.commit for d in gitsrc.log_deleted(pinned)] == [remote.c2, remote.c2]
    assert gitsrc.blobless_clone(remote.url, tmp_path / "f.git", ref="feature") == remote.c1


def test_blobless_clone_with_a_working_tree(remote: Remote, tmp_path: Path) -> None:
    dest = tmp_path / "work"
    assert gitsrc.blobless_clone(remote.url, dest, ref=remote.c1, bare=False) == remote.c1
    assert (dest / "ofl/a/A.ttf").read_bytes() == BINARY
    assert "ofl/b/METADATA.pb" in checked_out(dest)
    head = tmp_path / "head"
    assert gitsrc.blobless_clone(remote.url, head, bare=False) == remote.c3
    assert (head / "ofl/a/METADATA.pb").read_bytes() == b'name: "A2"\n'
    assert "ofl/b/METADATA.pb" not in checked_out(head)


# --- log_deleted, show, head_sha ----------------------------------------------------------------


@pytest.fixture(scope="module")
def history(remote: Remote, tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("history") / "repo.git"
    with pytest.MonkeyPatch.context() as mp:
        for key, value in _ENV.items():
            mp.setenv(key, value)
        gitsrc.blobless_clone(remote.url, dest)
    return dest


def test_log_deleted_lists_deletions_oldest_first(remote: Remote, history: Path) -> None:
    assert gitsrc.log_deleted(history) == [
        # committed 2024-02-01 23:30 at -05:00, which is 2024-02-02 in UTC
        DeletedPath(remote.c2, date(2024, 2, 2), "ofl/a/OFL.txt"),  # a rename, read as a deletion
        DeletedPath(remote.c2, date(2024, 2, 2), "ofl/b/METADATA.pb"),
        DeletedPath(remote.c3, date(2024, 3, 1), "apache/c/METADATA.pb"),
    ]


def test_log_deleted_filters_by_path_and_date(remote: Remote, history: Path) -> None:
    assert [d.path for d in gitsrc.log_deleted(history, ["*/METADATA.pb"])] == [
        "ofl/b/METADATA.pb",
        "apache/c/METADATA.pb",
    ]
    assert [d.path for d in gitsrc.log_deleted(history, ["apache"])] == ["apache/c/METADATA.pb"]
    assert [d.commit for d in gitsrc.log_deleted(history, since=date(2024, 2, 2))] == [
        remote.c2,
        remote.c2,
        remote.c3,
    ]
    assert [d.commit for d in gitsrc.log_deleted(history, since=date(2024, 2, 3))] == [remote.c3]
    assert gitsrc.log_deleted(history, since=date(2025, 1, 1)) == []


def test_show_returns_exact_bytes_at_a_revision(remote: Remote, history: Path) -> None:
    assert gitsrc.show(history, remote.c1, "ofl/a/A.ttf") == BINARY  # fetched on demand
    assert gitsrc.show(history, remote.c1, "/ofl/b/METADATA.pb") == b'name: "B"\n'
    assert gitsrc.show(history, f"{remote.c2}^", "ofl/b/METADATA.pb") == b'name: "B"\n'
    assert gitsrc.show(history, "HEAD", "ofl/a/METADATA.pb") == b'name: "A2"\n'


def test_show_missing_path_raises_file_not_found(remote: Remote, history: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"ofl/b/METADATA\.pb"):
        gitsrc.show(history, "HEAD", "ofl/b/METADATA.pb")
    with pytest.raises(FileNotFoundError):
        gitsrc.show(history, "no-such-rev", "README.md")
    with pytest.raises(FileNotFoundError):
        gitsrc.show(history, "HEAD", "ofl")  # a tree, not a file


def test_head_sha(remote: Remote, history: Path, tmp_path: Path) -> None:
    assert gitsrc.head_sha(history) == remote.c3
    with pytest.raises(GitError):
        gitsrc.head_sha(tmp_path)


# --- real network ---------------------------------------------------------------------------------


@pytest.mark.network
def test_real_sparse_clone_of_google_fonts(tmp_path: Path) -> None:
    dest = tmp_path / "fonts"
    sha = gitsrc.sparse_clone(
        "https://github.com/google/fonts", dest, ["/ofl/inter/METADATA.pb", "/ofl/inter/OFL.txt"]
    )
    assert len(sha) == 40
    assert checked_out(dest) == {"ofl/inter/METADATA.pb", "ofl/inter/OFL.txt"}
    assert b'name: "Inter"' in (dest / "ofl/inter/METADATA.pb").read_bytes()
