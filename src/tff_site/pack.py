"""``tff-site pack``: a deterministic tar of a built site, and its manifest.

The manifest, ``site.manifest.json``, is ``{"commit": <sha40>, "files": {<path>: {"sha256",
"size"}}}`` with paths relative to the site root, sorted. The server's ``tff-receive plan``
reads it and answers with the paths it lacks; ``pack --only`` then sends just those.

The tar holds regular files and directories only, sorted by path, with mtime 0, uid/gid 0,
empty owner names and mode 0644 (0755 for directories), so the same site gives the same bytes.
Paths must match ``^([a-z0-9][a-z0-9._-]*/)*[a-z0-9][a-z0-9._-]*$`` (the receiver's rule).

The receiver's other limits (``ops/deploy/tff-receive``) are checked here too, so a site the
server would refuse fails at pack time on the build machine: the extension allowlist, at most
20 MB per file, 400 MB and 20,000 files in total. A test keeps the two copies equal.
"""

import hashlib
import os
import re
import stat
import tarfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO

from tff_catalog import jsonio

PATH_RE = re.compile(r"([a-z0-9][a-z0-9._-]*/)*[a-z0-9][a-z0-9._-]*")
EXTENSIONS = frozenset(
    {"html", "css", "js", "json", "svg", "png", "ico", "txt", "xml", "ttf", "otf", "woff2", "woff"}
)
MAX_FILE_BYTES = 20_000_000
MAX_TOTAL_BYTES = 400_000_000
MAX_FILES = 20_000
MANIFEST_NAME = "site.manifest.json"
_CHUNK = 1 << 20


class PackError(ValueError):
    """The site can't be packed; ``problems`` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        shown = "; ".join(problems[:10])
        more = f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""
        super().__init__(f"cannot pack: {shown}{more}")


def path_problem(path: str) -> str | None:
    """Why the receiver would refuse ``path`` (relative, ``/``-separated), or None."""
    if not PATH_RE.fullmatch(path):
        return "path not allowed (lower-case letters, digits, '.', '_', '-'; no leading dot)"
    name = path.rsplit("/", 1)[-1]
    if "." not in name or name.rpartition(".")[2] not in EXTENSIONS:
        return "extension not allowed"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _walk(site_dir: Path) -> tuple[dict[str, Path], list[str]]:
    """Return the regular files under ``site_dir`` (relative posix path -> path) and problems."""
    files: dict[str, Path] = {}
    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(site_dir):
        base = Path(dirpath)
        for name in sorted(dirnames):
            if (base / name).is_symlink():
                problems.append(f"{(base / name).relative_to(site_dir).as_posix()}: symlink")
                dirnames.remove(name)
        dirnames.sort()
        for name in sorted(filenames):
            path = base / name
            rel = path.relative_to(site_dir).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                problems.append(f"{rel}: symlink")
            elif not stat.S_ISREG(mode):
                problems.append(f"{rel}: not a regular file")
            else:
                files[rel] = path
    return files, problems


def version_commit(site_dir: Path) -> str:
    """Return the ``commit=`` value of ``site_dir/version.txt``."""
    try:
        text = (site_dir / "version.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PackError([f"{site_dir}/version.txt is missing"]) from None
    for line in text.splitlines():
        if line.startswith("commit="):
            value = line.removeprefix("commit=").strip()
            if value:
                return value
    raise PackError([f"{site_dir}/version.txt has no commit= line"])


def manifest(site_dir: Path, *, commit: str | None = None) -> dict[str, Any]:
    """Return the manifest of ``site_dir`` (``commit`` as in ``pack``)."""
    site_dir = Path(site_dir)
    if not site_dir.is_dir():
        raise PackError([f"{site_dir} is not a directory"])
    found, problems = _walk(site_dir)
    entries: dict[str, dict[str, Any]] = {}
    total = 0
    for rel in sorted(found):
        problem = path_problem(rel)
        if problem:
            problems.append(f"{rel}: {problem}")
            continue
        size = found[rel].stat().st_size
        if size > MAX_FILE_BYTES:
            problems.append(f"{rel}: {size} bytes, over the {MAX_FILE_BYTES}-byte limit")
        total += size
        entries[rel] = {"sha256": _sha256(found[rel]), "size": size}
    if len(entries) > MAX_FILES:
        problems.append(f"{len(entries)} files, over the {MAX_FILES}-file limit")
    if total > MAX_TOTAL_BYTES:
        problems.append(f"{total} bytes in total, over the {MAX_TOTAL_BYTES}-byte limit")
    if not entries:
        problems.append(f"{site_dir} holds no files")
    if problems:
        raise PackError(problems)
    return {"commit": commit or version_commit(site_dir), "files": entries}


class _HashingReader:
    """Reads a file for tarfile while hashing it, so a file changed since the manifest fails."""

    def __init__(self, fh: BinaryIO) -> None:
        self.fh = fh
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.fh.read(size)
        self.digest.update(data)
        return data


def _info(name: str, *, size: int = 0, directory: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = 0o755 if directory else 0o644
    info.size = 0 if directory else size
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def pack(
    site_dir: Path,
    out: BinaryIO,
    *,
    commit: str | None = None,
    only: Iterable[str] | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Write the tar of ``site_dir`` (or just the paths in ``only``) to ``out``.

    ``commit`` defaults to the ``commit=`` line of ``site_dir/version.txt``. Writes the
    manifest to ``manifest_path`` when given, and returns it.
    """
    site_dir = Path(site_dir)
    doc = manifest(site_dir, commit=commit)
    files = doc["files"]
    if only is None:
        paths = list(files)
    else:
        wanted = set(only)
        unknown = sorted(wanted - files.keys())
        if unknown:
            raise PackError([f"{p}: asked for with --only but not in the site" for p in unknown])
        paths = sorted(wanted)
    if manifest_path is not None:
        jsonio.atomic_write(Path(manifest_path), jsonio.canonical_bytes(doc) + b"\n")

    dirs = {"/".join(p.split("/")[:i]) for p in paths for i in range(1, p.count("/") + 1)}
    entries = sorted([(d, True) for d in dirs] + [(p, False) for p in paths])
    with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for name, is_dir in entries:
            if is_dir:
                tar.addfile(_info(name, directory=True))
                continue
            with (site_dir / name).open("rb") as fh:
                reader = _HashingReader(fh)
                tar.addfile(_info(name, size=files[name]["size"]), reader)
            if reader.digest.hexdigest() != files[name]["sha256"]:
                raise PackError([f"{name}: changed while packing"])
    return doc
