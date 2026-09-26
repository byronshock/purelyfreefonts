"""Git sources: sparse and blobless clones, deleted paths, file history. Owner: agent I1.

Collectors that read git repositories (google/fonts, fontsource, fontist) and
the alias miners use these helpers instead of calling git directly. Clones go
into the run's ``RawDir`` and are deleted after the run.

Everything runs the git CLI (``git`` on PATH) with the caller's environment,
so ``url.<base>.insteadOf`` rewrites and ``GIT_ALLOW_PROTOCOL`` apply (tests
serve fixture repositories that way). Prompts are off (``GIT_TERMINAL_PROMPT=0``):
a clone that would need credentials fails instead of hanging. Variables that
point git at another repository (``GIT_DIR``, ``GIT_WORK_TREE``,
``GIT_INDEX_FILE`` and the like, set inside git hooks) are dropped, and git
never looks above the directory it is given for a repository, so these helpers
only ever touch the repository they are pointed at.

Pinning: every clone returns the commit sha it checked out, and ``ref`` may
be a branch, a tag or a full commit sha. A sha is fetched directly, which the
server must allow (GitHub does; a local test repository needs
``uploadpack.allowAnySHA1InWant``). Partial clones also need the server to
allow filters (``uploadpack.allowFilter``); otherwise git fetches every blob.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

GIT_TIMEOUT = 1800  # seconds; a blobless full-history clone of google/fonts is the slowest call
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_RECORD = "\x1e"  # starts each commit in log_deleted's output
# `git rev-parse --local-env-vars` minus the config ones (tests rewrite URLs through
# GIT_CONFIG_COUNT), plus two more that git sets for hooks. Under a hook these would
# send every command to the project's own repository.
_REPO_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


class GitError(RuntimeError):
    """A git command failed; the message carries the command and git's stderr."""


@dataclass(frozen=True, slots=True)
class DeletedPath:
    """A path removed by a commit (``git log --diff-filter=D``)."""

    commit: str
    day: date  # committer date, UTC
    path: str


def _env(cwd: Path | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _REPO_ENV}
    env |= {"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    if cwd is not None:
        # A plain directory must not resolve to a repository that happens to enclose it.
        env["GIT_CEILING_DIRECTORIES"] = str(Path(cwd).absolute().parent)
    return env


def _git(
    *args: str, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    """Run ``git args`` and return the completed process (stdout as bytes)."""
    cmd = ["git", "-c", "advice.detachedHead=false", *args]
    try:
        done = subprocess.run(
            cmd, cwd=cwd, env=_env(cwd), capture_output=True, timeout=GIT_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)}: timed out after {GIT_TIMEOUT} s") from exc
    if check and done.returncode != 0:
        stderr = done.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} (in {cwd or '.'}) failed: {stderr[-2000:]}")
    return done


def _out(*args: str, cwd: Path) -> str:
    return _git(*args, cwd=cwd).stdout.decode("utf-8").strip()


def _is_sha(ref: str | None) -> bool:
    return ref is not None and _SHA.match(ref) is not None


def _fresh(dest: Path) -> Path:
    """``dest`` as an absolute path to a directory that does not exist yet, or is empty."""
    dest = Path(dest).absolute()
    if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
        raise FileExistsError(f"{dest} exists and is not an empty directory")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def _plain(what: str, value: str | None) -> None:
    """Refuse a URL or ref git would read as an option."""
    if value is not None and (not value or value.startswith("-")):
        raise ValueError(f"bad {what} {value!r}")


def _clone_into(dest: Path, clone: Callable[[Path], str]) -> str:
    """Run ``clone(dest)`` on a fresh ``dest``; on failure leave ``dest`` as it was.

    So a caller can retry into the same path after a network error.
    """
    dest = _fresh(dest)
    existed = dest.exists()
    try:
        return clone(dest)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        if existed:
            dest.mkdir()
        raise


def sparse_clone(
    url: str,
    dest: Path,
    patterns: Sequence[str],
    *,
    ref: str | None = None,
    depth: int | None = 1,
) -> str:
    """Clone ``url`` into ``dest`` with only ``patterns`` checked out; return the commit sha.

    Uses ``--filter=blob:none``, a sparse checkout and non-cone patterns, so for
    example google/fonts' ``/ofl/*/METADATA.pb`` costs about 4 MB.

    ``ref`` (default: the remote's HEAD) is a branch, tag or commit sha;
    ``depth=None`` fetches full history. ``dest`` must not exist or be empty;
    a failed clone leaves it as it was. Only blobs matching ``patterns`` are
    downloaded, in one batch at checkout.
    """
    if not patterns:
        raise ValueError("sparse_clone needs at least one pattern")
    if depth is not None and depth < 1:
        raise ValueError("depth must be at least 1 (or None for full history)")
    _plain("url", url)
    _plain("ref", ref)

    def clone(dest: Path) -> str:
        _git("init", "-q", str(dest))
        _git("remote", "add", "origin", url, cwd=dest)
        # Mark the remote as the promisor of missing blobs, as a partial clone would.
        _git("config", "remote.origin.promisor", "true", cwd=dest)
        _git("config", "remote.origin.partialclonefilter", "blob:none", cwd=dest)
        # Non-cone patterns go in before the fetch, so the checkout pulls only matching blobs.
        _git("sparse-checkout", "set", "--no-cone", "--", *patterns, cwd=dest)
        fetch = ["fetch", "-q", "--no-tags", "--filter=blob:none"]
        if depth is not None:
            fetch.append(f"--depth={depth}")
        _git(*fetch, "origin", ref or "HEAD", cwd=dest)
        _git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest)
        return head_sha(dest)

    return _clone_into(dest, clone)


def blobless_clone(url: str, dest: Path, *, ref: str | None = None, bare: bool = True) -> str:
    """Clone full history without blobs (``--filter=blob:none``); return the commit sha.

    ``ref`` (default: the remote's HEAD) is a branch, tag or commit sha; HEAD is
    left at it (detached for a sha), so ``log_deleted`` walks the history up
    to the pin. Blobs arrive on demand (``show``). ``bare=False`` also checks
    out ``ref``'s whole tree, which downloads every blob in it. ``dest`` must
    not exist or be empty; a failed clone leaves it as it was.
    """
    _plain("url", url)
    _plain("ref", ref)

    def clone(dest: Path) -> str:
        args = ["clone", "-q", "--filter=blob:none"]
        args.append("--bare" if bare else "--no-checkout")
        if ref is not None and not _is_sha(ref):
            args += ["--branch", ref]
        _git(*args, "--", url, str(dest))
        if ref is not None and _is_sha(ref):
            found = _git(
                "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=dest, check=False
            )
            if found.returncode != 0:
                raise GitError(f"commit {ref} is not in the history of {url}")
            commit = found.stdout.decode("ascii").strip()
            _git("update-ref", "--no-deref", "HEAD", commit, cwd=dest)
        if not bare:
            _git("checkout", "-q", "--detach", "HEAD", cwd=dest)
        return head_sha(dest)

    return _clone_into(dest, clone)


def log_deleted(
    repo: Path, paths: Sequence[str] = (), *, since: date | None = None
) -> list[DeletedPath]:
    """List paths deleted in ``repo``'s history, oldest first; ``--no-renames``.

    Walks from HEAD. ``paths`` are git pathspecs (for example ``ofl/*/METADATA.pb``);
    ``since`` keeps commits whose committer date (UTC) is on or after that
    day. Without rename detection a rename reads as a deletion plus an
    addition, and no blob is needed, so this works on a blobless clone.
    Deletions inside merge commits are listed from the commits that made them.
    """
    args = ["log", "-z", "--reverse", "--diff-filter=D", "--no-renames", "--name-only"]
    args.append(f"--format={_RECORD}%H %ct")
    if since is not None:
        start = datetime.combine(since, time(), tzinfo=UTC)
        args.append(f"--since=@{int(start.timestamp())}")
    out = _git(*args, "HEAD", "--", *paths, cwd=Path(repo)).stdout.decode(
        "utf-8", "surrogateescape"
    )
    found = []
    for chunk in out.split(_RECORD)[1:]:
        header, _, names = chunk.partition("\0")
        commit, _, stamp = header.partition(" ")
        day = datetime.fromtimestamp(int(stamp), UTC).date()
        if since is not None and day < since:
            continue
        found += [
            DeletedPath(commit, day, name)
            for name in sorted(n for n in names.lstrip("\n").split("\0") if n)
        ]
    return found


def show(repo: Path, rev: str, path: str) -> bytes:
    """Return the bytes of ``path`` at ``rev`` (``git show <rev>:<path>``).

    The bytes are exact (no text conversion); in a blobless clone the blob is
    fetched on demand. A path missing at ``rev`` raises ``FileNotFoundError``.
    """
    path = path.lstrip("/")
    blob = _blob_id(Path(repo), rev, path)
    if blob is None:
        raise FileNotFoundError(f"{repo}: no file {rev}:{path}")
    return _git("cat-file", "blob", blob, cwd=Path(repo)).stdout


def _blob_id(repo: Path, rev: str, path: str) -> str | None:
    """The blob id of ``path`` at ``rev``, or None when it is missing or not a file.

    ``ls-tree`` reads trees only, so a blob a partial clone has not downloaded
    yet still counts as present.
    """
    listing = _git(
        "--literal-pathspecs",
        "ls-tree",
        "-z",
        "--full-tree",
        rev,
        "--",
        path,
        cwd=repo,
        check=False,
    )
    entries = [e for e in listing.stdout.decode("utf-8", "surrogateescape").split("\0") if e]
    if listing.returncode != 0 or len(entries) != 1:
        return None
    meta, _, name = entries[0].partition("\t")
    fields = meta.split(" ")
    if len(fields) != 3 or fields[1] != "blob" or name != path:
        return None
    return fields[2]


def head_sha(repo: Path) -> str:
    """Return the commit sha checked out in ``repo``."""
    return _out("rev-parse", "--verify", "HEAD^{commit}", cwd=Path(repo))
