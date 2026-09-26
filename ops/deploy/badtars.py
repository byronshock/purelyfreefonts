"""Malicious and broken uploads that tff-receive must refuse (design-m2 §5, key tests).

Standard library only. ``tests/ops/test_receive.py`` runs every case against tff-receive in a
temporary directory; ``deploy_key_tests.sh`` sends the same bytes over SSH with a deploy key.

Each case is one tar for ``upload SHA`` after ``plan SHA`` was given ``manifest(SHA)``, plus a
piece of the error message that shows it was refused for the right reason (exit code 3).

    python3 ops/deploy/badtars.py OUT_DIR SHA

writes ``OUT_DIR/manifest.json``, ``OUT_DIR/<case>.tar`` for each case, ``OUT_DIR/good.tar``
(a valid upload of the same release) and ``OUT_DIR/cases.tsv`` (``case<TAB>expected``).
"""

import hashlib
import io
import json
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 20_000_000  # tff-receive's per-file limit
# Paths the escape cases try to write; after a run, none of them may exist anywhere.
MARKER = "tff-keytest"


@dataclass(frozen=True)
class Case:
    name: str
    data: bytes
    expect: str  # part of tff-receive's error message


def site_files(sha: str) -> dict[str, bytes]:
    """The files of the tiny release every case pretends to upload."""
    return {
        "index.html": b"<!doctype html><title>tff key test</title>\n",
        "version.txt": f"commit={sha}\n".encode(),
    }


def manifest(sha: str) -> dict:
    files = {
        path: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for path, data in sorted(site_files(sha).items())
    }
    return {"commit": sha, "files": files}


def _info(name: str, *, size: int = 0, kind: bytes = tarfile.REGTYPE, mode: int = 0o644):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = size
    info.mode = mode
    info.mtime = 0
    return info


def _tar(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def good_tar(sha: str) -> bytes:
    return _tar([(_info(p, size=len(d)), d) for p, d in sorted(site_files(sha).items())])


def cases(sha: str) -> list[Case]:
    index = site_files(sha)["index.html"]
    wrong = bytes(len(index))  # same size, different bytes

    def link(name: str, kind: bytes, target: str) -> bytes:
        info = _info(name, kind=kind)
        info.linkname = target
        return _tar([(info, None)])

    def device(kind: bytes) -> bytes:
        info = _info("index.html", kind=kind)
        info.devmajor, info.devminor = 1, 3
        return _tar([(info, None)])

    pax = _info("index.html", size=len(index))
    pax.pax_headers = {"path": f"../{MARKER}-pax.html"}

    # A header that claims more than the limit, with no data after it: the receiver must stop
    # at the header instead of reading 20 MB.
    oversize = _info("index.html", size=MAX_FILE_BYTES + 1).tobuf(tarfile.PAX_FORMAT)

    # Extended headers tarfile would read whole into memory: the receiver must stop at the
    # header (with no data after it, reading on would fail as "unreadable" instead).
    def extended_bomb(kind: bytes, fmt: int) -> bytes:
        return _info("././@LongLink", size=100_000_000, kind=kind).tobuf(fmt)

    gnu_sparse = _info("index.html", size=len(index), kind=tarfile.GNUTYPE_SPARSE)
    pax_sparse = _info("index.html", size=512 + len(index))
    pax_sparse.pax_headers = {
        "GNU.sparse.major": "1",
        "GNU.sparse.minor": "0",
        "GNU.sparse.name": "index.html",
        "GNU.sparse.realsize": str(len(index)),
    }
    sparse_map = f"1\n0\n{len(index)}\n".encode().ljust(512, b"\0") + index

    return [
        Case(
            "dotdot",
            _tar([(_info(f"../{MARKER}-dotdot.html", size=2), b"x\n")]),
            "path not allowed",
        ),
        Case(
            "dotdot-deep",
            _tar([(_info(f"assets/../../{MARKER}.html", size=2), b"x\n")]),
            "path not allowed",
        ),
        Case(
            "absolute",
            _tar([(_info(f"/tmp/{MARKER}-absolute.html", size=2), b"x\n")]),
            "path not allowed",
        ),
        Case("pax-path", _tar([(pax, index)]), "path not allowed"),
        Case("dotfile", _tar([(_info(f".{MARKER}.txt", size=2), b"x\n")]), "path not allowed"),
        Case(
            "uppercase", _tar([(_info("Index.html", size=len(index)), index)]), "path not allowed"
        ),
        Case(
            "extension", _tar([(_info(f"{MARKER}.php", size=2), b"x\n")]), "extension not allowed"
        ),
        Case("symlink", link("index.html", tarfile.SYMTYPE, "/etc/passwd"), "symlink"),
        Case("hardlink", link("index.html", tarfile.LNKTYPE, "/etc/passwd"), "hard link"),
        Case("chardev", device(tarfile.CHRTYPE), "device"),
        Case("blockdev", device(tarfile.BLKTYPE), "device"),
        Case("fifo", _tar([(_info("index.html", kind=tarfile.FIFOTYPE), None)]), "fifo"),
        Case(
            "setuid", _tar([(_info("index.html", size=len(index), mode=0o4755), index)]), "setuid"
        ),
        Case(
            "setgid-dir",
            _tar([(_info("assets", kind=tarfile.DIRTYPE, mode=0o2755), None)]),
            "setgid",
        ),
        Case("oversize", oversize, "too large"),
        Case("pax-bomb", extended_bomb(tarfile.XHDTYPE, tarfile.USTAR_FORMAT), "extended header"),
        Case(
            "longname-bomb",
            extended_bomb(tarfile.GNUTYPE_LONGNAME, tarfile.GNU_FORMAT),
            "extended header",
        ),
        Case("gnu-sparse", gnu_sparse.tobuf(tarfile.GNU_FORMAT), "sparse file"),
        Case("pax-sparse", _tar([(pax_sparse, sparse_map)]), "sparse file"),
        Case(
            "not-in-manifest",
            _tar([(_info(f"{MARKER}-extra.html", size=2), b"x\n")]),
            "not in the manifest",
        ),
        Case(
            "sha-mismatch", _tar([(_info("index.html", size=len(wrong)), wrong)]), "sha256 mismatch"
        ),
        Case("size-mismatch", _tar([(_info("index.html", size=3), b"ab\n")]), "manifest says"),
        Case("sent-twice", _tar([(_info("index.html", size=len(index)), index)] * 2), "sent twice"),
        Case("garbage", b"this is not a tar file\n" * 40, "unreadable"),
        Case("truncated", good_tar(sha)[:700], "unexpected end of data"),
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2 or len(argv[1]) != 40:
        print("usage: badtars.py OUT_DIR SHA40", file=sys.stderr)
        return 2
    out, sha = Path(argv[0]), argv[1]
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest(sha), sort_keys=True) + "\n")
    (out / "good.tar").write_bytes(good_tar(sha))
    lines = []
    for case in cases(sha):
        (out / f"{case.name}.tar").write_bytes(case.data)
        lines.append(f"{case.name}\t{case.expect}\n")
    (out / "cases.tsv").write_text("".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
