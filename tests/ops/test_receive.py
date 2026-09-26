"""The deploy receiver, ``ops/deploy/tff-receive``, against temporary directories (design-m2 §5).

tff-receive runs as a subprocess, as sshd starts it (``python3 -I``), with ``--root`` pointing
at a temporary ``/srv/trulyfreefonts`` and ``--log-file`` in place of syslog; the verb comes
from argv or from ``SSH_ORIGINAL_COMMAND``. Covered here:

- the full plan / upload / activate / rollback / hold cycle, hardlink dedupe, carry-over of the
  old release's assets, pruning to three releases and the incoming/ cleanup;
- every malicious tar in ``ops/deploy/badtars.py`` (the same bytes the SSH key tests send),
  manifest checks, the SSH command grammar, locking and the exit codes;
- ``ops/tests/deploy_loop.py`` watching real switches of a served ``current`` (no errors, no
  mixed pages), and catching a non-atomic deploy;
- the sshd drop-in, parsed by the local sshd when one is installed;
- ``ops/deploy.sh`` end to end through a fake ``ssh`` that runs the receiver locally;
- Python 3.13 syntax (the server's version), and a full cycle under any other interpreter
  found (``TFF_RECEIVE_PYTHON``, ``python3.13`` or ``python3.12`` on PATH).
"""

import ast
import contextlib
import fcntl
import functools
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType

import pytest

from tff_site import pack

ROOT = Path(__file__).resolve().parents[2]
RECEIVE = ROOT / "ops" / "deploy" / "tff-receive"
BADTARS = ROOT / "ops" / "deploy" / "badtars.py"
SSHD_CONF = ROOT / "ops" / "deploy" / "sshd-deploy.conf"
DEPLOY_SH = ROOT / "ops" / "deploy.sh"
LOOP = ROOT / "ops" / "tests" / "deploy_loop.py"


def _load(name: str, path: Path) -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ next to ops/ scripts
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = dont_write
    return module


receiver = _load("tff_receive_under_test", RECEIVE)
badtars = _load("tff_badtars_under_test", BADTARS)
deploy_loop = _load("tff_deploy_loop_under_test", LOOP)


def sha_of(label: str) -> str:
    return hashlib.sha1(label.encode()).hexdigest()  # any 40-hex name will do


A, B, C, D, E = (sha_of(x) for x in "abcde")


# --- a tiny built site -----------------------------------------------------------------------


def make_site(path: Path, commit: str, *, variant: str | None = None, shared: bytes = b"") -> Path:
    """Write a small site shaped like tff-site's output; ``variant`` changes its script."""
    variant = variant or commit
    files: dict[str, bytes] = {}

    def hashed(stem: str, ext: str, data: bytes) -> str:
        name = f"assets/{stem}.{hashlib.sha256(data).hexdigest()[:10]}.{ext}"
        files[name] = data
        return "/" + name

    css = hashed("style", "css", b"body{margin:0}\n")  # the same in every release
    js = hashed("app", "js", f"const Main = {{v: '{variant}'}};\n".encode())
    index = hashed("list", "json", json.dumps({"v": 1, "commit": commit}).encode())
    details = hashed("details", "json", json.dumps({"v": 1, "x": variant}).encode())
    font = hashed("fonts/sample-sans-01", "ttf", b"\x00\x01\x00\x00font" + shared)
    spec = hashed("specimens/sample-sans-01", "svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{css}"><script type="module" src="{js}"></script>'
        f'<link rel="preload" as="fetch" crossorigin href="{index}">'
        '<link rel="icon" href="/favicon.svg"></head><body>'
        f'<ol id="list" data-index="{index}" data-details="{details}" data-font="{font}"></ol>'
        f'<noscript><img src="{spec}" alt=""></noscript></body></html>\n'
    )
    files["index.html"] = html.encode()
    files["favicon.svg"] = b"<svg xmlns='http://www.w3.org/2000/svg'><title>i</title></svg>"
    files["version.txt"] = f"commit={commit}\nrun_date=2026-09-25\n".encode()
    for rel, data in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_bytes(data)
    return path


def tar_of(site: Path, sha: str, paths: list[str] | None) -> bytes:
    import io

    buf = io.BytesIO()
    pack.pack(site, buf, commit=sha, only=paths)
    return buf.getvalue()


def manifest_of(site: Path, sha: str) -> bytes:
    return json.dumps(pack.manifest(site, commit=sha)).encode()


# --- running the receiver --------------------------------------------------------------------


@dataclass
class Result:
    code: int
    out: str
    err: str


class Receiver:
    """A temporary /srv/trulyfreefonts and a way to run tff-receive against it."""

    def __init__(self, root: Path, python: str = sys.executable) -> None:
        self.root = root
        self.python = python
        self.log = root / "receive.log"
        for name in ("prod", "staging"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def dir(self, env: str = "production") -> Path:
        return self.root / ("prod" if env == "production" else "staging")

    def current(self, env: str = "production") -> str | None:
        link = self.dir(env) / "current"
        return str(link.readlink()).removeprefix("releases/") if link.is_symlink() else None

    def releases(self, env: str = "production") -> list[str]:
        rel = self.dir(env) / "releases"
        return sorted(p.name for p in rel.iterdir() if p.is_dir()) if rel.is_dir() else []

    def run(
        self,
        *words: str,
        stdin: bytes = b"",
        env: str = "production",
        ssh: str | None = None,
        options: tuple[str, ...] = (),
        extra_env: dict[str, str] | None = None,
    ) -> Result:
        cmd = [self.python, "-I", str(RECEIVE), "--root", str(self.root)]
        cmd += ["--log-file", str(self.log), "--lock-wait", "0", *options, env]
        environ = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        if ssh is not None:
            environ["SSH_ORIGINAL_COMMAND"] = ssh
        else:
            cmd += words
        environ.update(extra_env or {})
        proc = subprocess.run(
            cmd, input=stdin, capture_output=True, env=environ, timeout=120, check=False
        )
        return Result(proc.returncode, proc.stdout.decode(), proc.stderr.decode())

    def ok(self, *words: str, **kwargs) -> Result:
        result = self.run(*words, **kwargs)
        assert result.code == 0, result
        return result

    def plan(self, site: Path, sha: str, env: str = "production") -> list[str]:
        result = self.ok("plan", sha, stdin=manifest_of(site, sha), env=env)
        return result.out.splitlines()

    def deploy(self, site: Path, sha: str, env: str = "production", activate: bool = True):
        need = self.plan(site, sha, env)
        self.ok("upload", sha, stdin=tar_of(site, sha, need), env=env)
        if activate:
            self.ok("activate", sha, env=env)
        return need


@pytest.fixture
def rx(tmp_path: Path) -> Receiver:
    return Receiver(tmp_path / "srv")


def _other_pythons() -> list[str]:
    seen = {os.path.realpath(sys.executable)}
    found: list[str] = []
    for candidate in (
        os.environ.get("TFF_RECEIVE_PYTHON"),
        shutil.which("python3.13"),
        shutil.which("python3.12"),
    ):
        if candidate and os.path.realpath(candidate) not in seen:
            seen.add(os.path.realpath(candidate))
            found.append(candidate)
    return found


# --- the full cycle --------------------------------------------------------------------------


@pytest.mark.parametrize("python", [sys.executable, *_other_pythons()], ids=os.path.basename)
def test_full_cycle(tmp_path: Path, python: str) -> None:
    rx = Receiver(tmp_path / "srv", python)
    site_a = make_site(tmp_path / "a", A)
    site_b = make_site(tmp_path / "b", B)

    need_a = rx.deploy(site_a, A)
    assert need_a == sorted(pack.manifest(site_a, commit=A)["files"])
    assert rx.current() == A
    status = rx.ok("status").out
    assert f"current: {A}" in status
    assert "commit=" + A in status

    need_b = rx.plan(site_b, B)
    unchanged = {"favicon.svg"} | {p for p in need_a if "/style." in p or "/fonts/" in p}
    assert unchanged.isdisjoint(need_b)
    assert {"index.html", "version.txt"} <= set(need_b)
    rx.ok("upload", B, stdin=tar_of(site_b, B, need_b))
    rel = rx.dir() / "releases"
    for path in unchanged:  # hardlinked, not copied
        assert (rel / A / path).stat().st_ino == (rel / B / path).stat().st_ino
    assert rx.current() == A  # upload alone never switches

    rx.ok("activate", B)
    assert rx.current() == B
    assert (rx.dir() / "current").is_symlink()
    old_js = next(p for p in need_a if p.startswith("assets/app."))
    assert (rel / B / old_js).is_file(), "the old script is carried over for open tabs"
    assert old_js not in json.loads((rel / f"{B}.manifest.json").read_text())["files"]

    rollback = rx.ok("rollback")
    assert rx.current() == A
    assert "rollback" in rollback.out
    history = (rx.dir() / "history.log").read_text()
    assert f"rollback {A} prev={B}" in history
    # A second rollback would go further back, but there is nothing older than A.
    again = rx.run("rollback")
    assert again.code == 3
    assert "no earlier release" in again.err
    rx.ok("rollback", B)
    assert rx.current() == B


def test_upload_and_plan_are_idempotent(rx: Receiver, tmp_path: Path) -> None:
    site = make_site(tmp_path / "a", A)
    rx.deploy(site, A)
    assert rx.plan(site, A) == []
    again = rx.ok("upload", A, stdin=tar_of(site, A, None))
    assert "already stored" in again.out
    assert "already current" in rx.ok("activate", A).out
    assert rx.releases() == [A]


def test_plan_refuses_another_manifest_for_an_existing_release(rx: Receiver, tmp_path: Path):
    rx.deploy(make_site(tmp_path / "a", A), A)
    other = make_site(tmp_path / "other", A, variant="changed")
    result = rx.run("plan", A, stdin=manifest_of(other, A))
    assert result.code == 3
    assert "different manifest" in result.err


def test_upload_without_plan_or_with_missing_files(rx: Receiver, tmp_path: Path) -> None:
    site = make_site(tmp_path / "a", A)
    result = rx.run("upload", A, stdin=tar_of(site, A, None))
    assert result.code == 3
    assert "run plan first" in result.err
    need = rx.plan(site, A)
    result = rx.run("upload", A, stdin=tar_of(site, A, need[1:]))
    assert result.code == 3
    assert "neither uploaded nor in a kept release" in result.err
    assert rx.releases() == []
    assert not list((rx.dir() / "incoming").glob("*.partial")), "partial upload left behind"
    rx.ok("upload", A, stdin=tar_of(site, A, need))  # the plan survives a refused upload


def test_version_txt_must_name_the_commit(rx: Receiver, tmp_path: Path) -> None:
    site = make_site(tmp_path / "a", B)  # version.txt says B
    need = rx.plan(site, A)
    result = rx.run("upload", A, stdin=tar_of(site, A, need))
    assert result.code == 3
    assert f"does not say commit={A}" in result.err
    assert rx.releases() == []


def test_empty_upload_reads_as_no_files(rx: Receiver, tmp_path: Path) -> None:
    site = make_site(tmp_path / "a", A)
    rx.deploy(site, A)
    # A release with the same files under a new commit: only version.txt differs.
    site_b = tmp_path / "b"
    shutil.copytree(site, site_b)
    (site_b / "version.txt").write_text((site_b / "version.txt").read_text().replace(A, B))
    need = rx.plan(site_b, B)
    assert need == ["version.txt"]
    rx.ok("upload", B, stdin=tar_of(site_b, B, need))
    (site_b / "version.txt").write_text(f"commit={C}\n")
    assert rx.plan(site_b, C) == ["version.txt"]
    for body in (b"", tar_of(site_b, C, [])):  # nothing at all, or an empty tar
        result = rx.run("upload", C, stdin=body)
        assert result.code == 3
        assert "neither uploaded" in result.err


# --- activation, hold, rollback, pruning -----------------------------------------------------


def test_hold_blocks_activate_until_unhold(rx: Receiver, tmp_path: Path) -> None:
    rx.deploy(make_site(tmp_path / "a", A), A)
    rx.deploy(make_site(tmp_path / "b", B), B)
    rx.ok("rollback", "--hold")
    assert rx.current() == A
    assert "hold: " + A in rx.ok("status").out
    rx.deploy(make_site(tmp_path / "c", C), C, activate=False)
    held = rx.run("activate", C)
    assert held.code == 4
    assert "unhold" in held.err
    assert rx.current() == A
    rx.ok("rollback", B)  # rollback still works while held
    assert rx.current() == B
    rx.ok("unhold")
    assert "hold: no" in rx.ok("status").out
    rx.ok("activate", C)
    assert rx.current() == C


def test_keeps_three_releases_never_current_or_previous(rx: Receiver, tmp_path: Path) -> None:
    for i, sha in enumerate((A, B, C, D, E)):
        rx.deploy(make_site(tmp_path / sha, sha, shared=bytes([i])), sha)
        assert len(rx.releases()) <= 3
    assert rx.releases() == sorted([C, D, E])
    rel = rx.dir() / "releases"
    assert not (rel / f"{A}.manifest.json").exists()
    rx.ok("rollback")
    assert rx.current() == D
    rx.ok("rollback")
    assert rx.current() == C, "two rollbacks go back two releases"
    status = rx.ok("status").out
    assert f"current: {C}" in status
    assert "previous: none" in status


def test_prune_keeps_fresh_uploads_and_clears_old_incoming(rx: Receiver, tmp_path: Path) -> None:
    rx.deploy(make_site(tmp_path / "e", E), E, activate=False)  # uploaded, never activated
    stale = rx.dir() / "incoming" / "stale.partial"
    stale.mkdir()
    fresh = rx.dir() / "incoming" / f"{A}.manifest.json.keep"
    fresh.write_text("{}")
    two_days_ago = time.time() - 2 * 86400
    os.utime(stale, (two_days_ago, two_days_ago))
    for sha in (A, B, C, D):
        rx.deploy(make_site(tmp_path / sha, sha), sha)
    assert E in rx.releases(), "a fresh upload survives pruning"
    assert not stale.exists()
    assert fresh.exists()
    manifest = rx.dir() / "releases" / f"{E}.manifest.json"
    os.utime(manifest, (two_days_ago, two_days_ago))
    rx.ok("rollback")  # rollback never prunes
    assert E in rx.releases()
    rx.ok("activate", D)
    assert E not in rx.releases()
    assert not manifest.exists()


def test_carry_over_is_one_generation(rx: Receiver, tmp_path: Path) -> None:
    need = {}
    for sha in (A, B, C):
        need[sha] = rx.deploy(make_site(tmp_path / sha, sha), sha)
    js = {sha: next(p for p in need[sha] if p.startswith("assets/app.")) for sha in need}
    rel = rx.dir() / "releases" / C
    assert (rel / js[B]).is_file()
    assert not (rel / js[A]).exists()


def test_rollback_target_skips_releases_left_by_a_rollback() -> None:
    def history(*lines: str) -> list:
        entries = []
        for i, line in enumerate(lines):
            verb, sha, *fields = line.split()
            entries.append(
                receiver.HistoryEntry(f"t{i:02d}", verb, sha, dict(f.split("=") for f in fields))
            )
        return entries

    kept = {A, B, C, D}
    h = history(f"activate {A}", f"activate {B}", f"activate {C}")
    assert receiver.rollback_target(h, C, kept) == B
    h += history(f"rollback {B} prev={C}")
    assert receiver.rollback_target(h, B, kept) == A
    h += history(f"activate {D} prev={B}")
    assert receiver.rollback_target(h, D, kept) == B
    h += history(f"activate {C} prev={D}")  # C re-activated on purpose: good again
    assert receiver.rollback_target(h, C, kept) == D
    assert receiver.rollback_target(h, C, {C}) is None
    assert receiver.rollback_target([], None, kept) is None


def test_disk_use_is_bounded(rx: Receiver, tmp_path: Path) -> None:
    """A misused key can't fill the disk with plans or with uploads it never activates."""
    rx.deploy(make_site(tmp_path / "a", A), A)
    extra = [sha_of(f"x{i}") for i in range(5)]
    for i, sha in enumerate(extra):
        rx.deploy(make_site(tmp_path / sha, sha, shared=bytes([i])), sha, activate=False)
        time.sleep(0.01)  # distinct manifest mtimes
    assert rx.releases() == sorted([A, *extra[-receiver.KEEP_UNACTIVATED :]])
    assert rx.current() == A
    for i in range(6):
        rx.plan(make_site(tmp_path / f"p{i}", sha_of(f"p{i}")), sha_of(f"p{i}"))
        time.sleep(0.01)
    plans = sorted(p.name for p in (rx.dir() / "incoming").glob("*.manifest.json"))
    assert plans == sorted(f"{sha_of(f'p{i}')}.manifest.json" for i in range(2, 6))
    assert len(plans) == receiver.KEEP_PLANS


def test_a_pruned_release_can_come_back(rx: Receiver, tmp_path: Path) -> None:
    """Going back to an old commit re-uploads it; its old activation doesn't get it pruned."""
    for sha in (A, B, C, D, E):
        rx.deploy(make_site(tmp_path / sha, sha), sha)
    assert A not in rx.releases()
    rx.deploy(make_site(tmp_path / A, A), A, activate=False)
    assert A in rx.releases(), "the re-uploaded release was pruned at once"
    rx.ok("activate", A)
    assert rx.current() == A


def test_crash_leftovers_never_block_a_deploy(rx: Receiver, tmp_path: Path) -> None:
    site_a, site_b = make_site(tmp_path / "a", A), make_site(tmp_path / "b", B)
    rx.deploy(site_a, A)
    rx.deploy(site_b, B, activate=False)
    rel = rx.dir() / "releases"
    # A crash between the rename and the manifest write, or mid-removal: a release
    # directory without its manifest. It isn't a release, and a new upload replaces it.
    (rel / f"{B}.manifest.json").unlink()
    (rel / B / "index.html").unlink()
    assert B not in rx.ok("status").out
    rx.deploy(site_b, B)
    assert rx.current() == B
    assert (rel / B / "index.html").is_file()
    # The other way round: a manifest whose release directory is gone.
    rx.ok("activate", A)
    shutil.rmtree(rel / B)
    assert rx.plan(site_b, B) != []
    rx.ok("upload", B, stdin=tar_of(site_b, B, rx.plan(site_b, B)))
    rx.ok("activate", B)
    # Never replace the live directory, even when its manifest is missing.
    (rel / f"{B}.manifest.json").unlink()
    rx.plan(site_b, B)
    live = rx.run("upload", B, stdin=tar_of(site_b, B, None))
    assert live.code == 5
    assert "is live" in live.err
    assert (rel / B / "index.html").is_file()


def test_modes_come_from_the_receiver_not_the_tar(rx: Receiver, tmp_path: Path) -> None:
    import io
    import tarfile

    site = make_site(tmp_path / "a", A)
    doc = pack.manifest(site, commit=A)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for i, path in enumerate(sorted(doc["files"])):
            data = (site / path).read_bytes()
            info = tarfile.TarInfo(path)
            info.size, info.mode = len(data), (0o600, 0o777, 0o400)[i % 3]
            tar.addfile(info, io.BytesIO(data))
    rx.ok("plan", A, stdin=manifest_of(site, A))
    rx.ok("upload", A, stdin=buf.getvalue())
    release = rx.dir() / "releases" / A
    assert release.stat().st_mode & 0o7777 == 0o755
    for path in release.rglob("*"):
        want = 0o755 if path.is_dir() else 0o644
        assert path.stat().st_mode & 0o7777 == want, path
    assert (rx.dir() / "incoming").stat().st_mode & 0o7777 == 0o700


def test_activate_checks_the_release_first(rx: Receiver, tmp_path: Path) -> None:
    rx.deploy(make_site(tmp_path / "a", A), A)
    rx.deploy(make_site(tmp_path / "b", B), B, activate=False)
    (rx.dir() / "releases" / B / "version.txt").unlink()
    result = rx.run("activate", B)
    assert result.code == 3
    assert "release incomplete: version.txt" in result.err
    assert rx.current() == A


def test_a_failed_tidy_after_the_switch_is_not_a_failed_activate(
    rx: Receiver, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rx.deploy(make_site(tmp_path / "a", A), A)
    rx.deploy(make_site(tmp_path / "b", B), B, activate=False)

    def broken(site, log):
        raise PermissionError("disk trouble")

    monkeypatch.setattr(receiver, "prune", broken)
    monkeypatch.chdir(tmp_path)  # main() changes directory
    old_umask = os.umask(0o022)
    try:
        code = receiver.main(
            ["--root", str(rx.root), "--log-file", str(rx.log), "production", "activate", B],
            environ={},
        )
    finally:
        os.umask(old_umask)
    assert code == 0
    assert rx.current() == B
    assert "tidying up failed" in rx.log.read_text()


def test_syslog(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeSyslog:
        LOG_PID, LOG_USER, LOG_INFO, LOG_ERR = 1, 8, 6, 3

        def openlog(self, ident, option, facility):
            calls.append(("open", ident, option, facility))

        def syslog(self, priority, message):
            calls.append(("log", priority, message))

    monkeypatch.setattr(receiver, "syslog", FakeSyslog())
    log = receiver.Log("staging", None)
    log("activate x")
    log("refused 'bash'", error=True)
    assert calls == [
        ("open", "tff-receive", 1, 8),
        ("log", 6, "staging: activate x"),
        ("log", 3, "staging: refused 'bash'"),
    ]


def test_environments_are_separate(rx: Receiver, tmp_path: Path) -> None:
    rx.deploy(make_site(tmp_path / "a", A), A, env="staging")
    assert rx.current("staging") == A
    assert rx.current("production") is None
    assert rx.releases("production") == []
    assert f"environment: staging\ncurrent: {A}" in rx.ok("status", env="staging").out


# --- malicious uploads -----------------------------------------------------------------------


FAKE = "0000000000000000000000000000000000000bad"


@pytest.mark.parametrize("case", badtars.cases(FAKE), ids=lambda c: c.name)
def test_malicious_tar_is_refused(rx: Receiver, tmp_path: Path, case) -> None:
    absolute = Path(f"/tmp/{badtars.MARKER}-absolute.html")
    existed = absolute.exists()
    rx.ok("plan", FAKE, stdin=json.dumps(badtars.manifest(FAKE)).encode())
    result = rx.run("upload", FAKE, stdin=case.data)
    assert result.code == 3, result
    assert case.expect in result.err, result.err
    assert rx.releases() == []
    assert not list((rx.dir() / "incoming").glob("*.partial"))
    assert not list(tmp_path.rglob(f"*{badtars.MARKER}*")), "a file escaped the upload"
    assert existed or not absolute.exists()
    assert "refused" in rx.log.read_text()
    # The plan still stands and a good upload of the same release works.
    rx.ok("upload", FAKE, stdin=badtars.good_tar(FAKE))
    assert rx.releases() == [FAKE]


@pytest.mark.parametrize("python", _other_pythons(), ids=os.path.basename)
def test_malicious_tars_under_other_pythons(tmp_path: Path, python: str) -> None:
    """SafeTarInfo leans on tarfile internals: check them on the server's Python too."""
    rx = Receiver(tmp_path / "srv", python)
    rx.ok("plan", FAKE, stdin=json.dumps(badtars.manifest(FAKE)).encode())
    wrong = []
    for case in badtars.cases(FAKE):
        result = rx.run("upload", FAKE, stdin=case.data)
        if result.code != 3 or case.expect not in result.err:
            wrong.append((case.name, result))
    assert wrong == []
    rx.ok("upload", FAKE, stdin=badtars.good_tar(FAKE))


def test_badtars_command_line(tmp_path: Path) -> None:
    out = tmp_path / "tars"
    proc = subprocess.run(
        [sys.executable, str(BADTARS), str(out), FAKE], capture_output=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    names = [line.split("\t")[0] for line in (out / "cases.tsv").read_text().splitlines()]
    assert names == [c.name for c in badtars.cases(FAKE)]
    assert all((out / f"{n}.tar").is_file() for n in names)
    assert json.loads((out / "manifest.json").read_text()) == badtars.manifest(FAKE)


@pytest.mark.parametrize(
    ("doc", "expect"),
    [
        (b"not json", "not valid JSON"),
        (b"[]", "exactly"),
        (json.dumps({"commit": B, "files": {"version.txt": {}}}).encode(), "is not"),
        (json.dumps({"commit": FAKE, "files": {}, "x": 1}).encode(), "exactly"),
        (json.dumps({"commit": FAKE, "files": {}}).encode(), "non-empty"),
        (
            b'{"commit": "%s", "commit": "%s", "files": {}}' % (FAKE.encode(), FAKE.encode()),
            "duplicate",
        ),
        (
            b'{"commit": "%s", "files": {"version.txt": {"sha256": NaN, "size": 1}}}'
            % FAKE.encode(),
            "NaN",
        ),
    ],
)
def test_bad_manifest_is_refused(rx: Receiver, doc: bytes, expect: str) -> None:
    result = rx.run("plan", FAKE, stdin=doc)
    assert result.code == 3, result
    assert expect in result.err, result


def _files(**entries) -> dict:
    ok = {"sha256": "0" * 64, "size": 1}
    files = {"version.txt": ok}
    for path, value in entries.items():
        files[path.replace("__", "/")] = value if value is not None else ok
    return files


@pytest.mark.parametrize(
    ("files", "expect"),
    [
        (
            {
                "../x.html": {"sha256": "0" * 64, "size": 1},
                "version.txt": {"sha256": "0" * 64, "size": 1},
            },
            "path not allowed",
        ),
        (_files(**{"x.php": None}), "extension not allowed"),
        (_files(**{"Readme.txt": None}), "path not allowed"),
        (_files(**{"noext": None}), "extension not allowed"),
        (_files(**{"a.html": {"sha256": "X" * 64, "size": 1}}), "bad sha256"),
        (_files(**{"a.html": {"sha256": "0" * 64, "size": -1}}), "bad size"),
        (_files(**{"a.html": {"sha256": "0" * 64, "size": True}}), "bad size"),
        (_files(**{"a.html": {"sha256": "0" * 64, "size": 20_000_001}}), "too large"),
        (_files(**{"a.html": {"sha256": "0" * 64}}), "exactly"),
        ({"index.html": {"sha256": "0" * 64, "size": 1}}, "no version.txt"),
        (_files(**{"a.txt": None, "a.txt__b.txt": None}), "both a file and a directory"),
    ],
)
def test_manifest_checks(files: dict, expect: str) -> None:
    doc = json.dumps({"commit": FAKE, "files": files}).encode()
    with pytest.raises(receiver.InvalidInput, match=expect):
        receiver.parse_manifest(doc, FAKE)


def test_manifest_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    files = {f"f{i}.txt": {"sha256": "0" * 64, "size": 10} for i in range(5)}
    files["version.txt"] = {"sha256": "0" * 64, "size": 10}
    doc = json.dumps({"commit": FAKE, "files": files}).encode()
    assert len(receiver.parse_manifest(doc, FAKE)["files"]) == 6
    monkeypatch.setattr(receiver, "MAX_FILES", 5)
    with pytest.raises(receiver.InvalidInput, match="more than 5 files"):
        receiver.parse_manifest(doc, FAKE)
    monkeypatch.setattr(receiver, "MAX_FILES", 100)
    monkeypatch.setattr(receiver, "MAX_TOTAL_BYTES", 59)
    with pytest.raises(receiver.InvalidInput, match="too large in total"):
        receiver.parse_manifest(doc, FAKE)
    monkeypatch.setattr(receiver, "MAX_MANIFEST_BYTES", 100)
    with pytest.raises(receiver.InvalidInput, match="larger than"):
        receiver.parse_manifest(doc, FAKE)


def test_oversized_manifest_on_stdin(rx: Receiver) -> None:
    result = rx.run("plan", FAKE, stdin=b" " * 2_000_001)
    assert result.code == 3
    assert "larger than" in result.err


# --- the command grammar and exit codes -----------------------------------------------------


def test_verbs_over_ssh(rx: Receiver, tmp_path: Path) -> None:
    site = make_site(tmp_path / "a", A)
    plan = rx.run(ssh=f"plan {A}", stdin=manifest_of(site, A))
    assert plan.code == 0
    assert plan.out.splitlines() == sorted(pack.manifest(site)["files"])
    rx.ok(ssh=f"upload {A}", stdin=tar_of(site, A, None))
    rx.ok(ssh=f"activate {A}")
    assert rx.current() == A
    assert f"current: {A}" in rx.ok(ssh="status").out
    assert "via ssh" in rx.log.read_text()


@pytest.mark.parametrize(
    "command",
    [
        "",
        "bash",
        "sh -c id",
        "cat /etc/passwd",
        "id",
        "status; id",
        "status && id",
        "status\nid",
        " status",
        "status ",
        "STATUS",
        "upload ../../etc",
        f"upload {A} ../../etc",
        f"upload {A.upper()}",
        f"plan {A[:39]}",
        f"rollback --hold {A}",
        "rollback --force",
        "activate",
        "internal-sftp",
        "/usr/lib/openssh/sftp-server",
        "scp -f /etc/passwd",
        "rsync --server --sender -vlogDtpre.iLsfxCIvu . /etc/passwd",
        "/usr/local/sbin/tff-receive staging status",
    ],
)
def test_refused_ssh_commands(rx: Receiver, command: str) -> None:
    result = rx.run(ssh=command)
    assert result.code == 2, result
    assert "usage:" in result.err
    assert "not an allowed command" in result.err
    assert rx.releases() == []
    assert "refused" in rx.log.read_text()


def test_usage_errors(rx: Receiver) -> None:
    no_command = rx.run()  # sshd with no command: argv is just the environment
    assert no_command.code == 2
    assert "no command given" in no_command.err
    both = subprocess.run(
        [
            *(sys.executable, "-I", str(RECEIVE), "--root", str(rx.root)),
            *("--log-file", str(rx.log), "production", "status"),
        ],
        env={"PATH": os.environ["PATH"], "SSH_ORIGINAL_COMMAND": "status"},
        capture_output=True,
        check=False,
    )
    assert both.returncode == 2
    assert b"unexpected arguments" in both.stderr
    assert rx.run("status", env="testing").code == 2
    assert rx.run("status", options=("--bogus", "x")).code == 2
    assert rx.run("plan", "a b").code == 2


def test_refuses_the_wrong_user(tmp_path: Path) -> None:
    """Without --root it runs only as deploy or deploy-staging, never as root or byron."""
    proc = subprocess.run(
        [
            sys.executable,
            "-I",
            str(RECEIVE),
            "--log-file",
            str(tmp_path / "log"),
            "production",
            "status",
        ],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 2
    assert b"production is deployed as deploy" in proc.stderr
    assert "refused 'production status'" in (tmp_path / "log").read_text()


def test_lock_and_internal_errors(rx: Receiver, tmp_path: Path) -> None:
    site = make_site(tmp_path / "a", A)
    lock = rx.dir() / ".lock"
    with lock.open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        busy = rx.run("plan", A, stdin=manifest_of(site, A))
        assert busy.code == 4
        assert "another tff-receive" in busy.err
        assert rx.run("status").code == 0, "status never waits for the lock"
    rx.deploy(site, A)

    broken = Receiver(tmp_path / "broken")
    shutil.rmtree(broken.dir())
    missing = broken.run("status")
    assert missing.code == 5
    assert "setup-server.sh" in missing.err
    (broken.dir()).mkdir()
    (broken.dir() / "current").mkdir()
    assert broken.run("status").code == 5


# --- the deploy loop watching real switches --------------------------------------------------


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        pass


@contextlib.contextmanager
def serve(directory: Path) -> Iterator[str]:
    """Serve ``directory`` (resolved per request, so a symlink switch shows at once)."""
    handler = functools.partial(_Quiet, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _loop_in_background(url: str, duration: float):
    loop = deploy_loop.Loop(url, extras=2)
    thread = threading.Thread(
        target=loop.run, kwargs={"interval": 0.01, "duration": duration, "workers": 8}
    )
    thread.start()
    return loop, thread


def test_deploy_loop_sees_no_errors_during_switches(rx: Receiver, tmp_path: Path) -> None:
    rx.deploy(make_site(tmp_path / "a", A), A)
    rx.deploy(make_site(tmp_path / "b", B), B, activate=False)
    with serve(rx.dir() / "current") as url:
        loop, thread = _loop_in_background(url, 3.0)
        time.sleep(0.4)
        for words in (("activate", B), ("rollback",), ("rollback", B), ("rollback", A)):
            rx.ok(*words)
            time.sleep(0.4)
        thread.join()
        loop.client.close()
    stats = loop.stats
    assert stats.errors == 0, stats.samples
    assert stats.mixed == 0, stats.samples
    assert stats.probes > 30
    assert set(stats.versions) == {A, B}
    assert len(stats.versions) >= 4
    assert f"errors=0 mixed=0 switches={stats.switches}" in stats.line()


def test_deploy_loop_catches_a_non_atomic_deploy(tmp_path: Path) -> None:
    """Copying files into the served directory, page first, shows up as errors."""
    live = make_site(tmp_path / "live", A)
    new = make_site(tmp_path / "new", B)
    with serve(live) as url:
        loop, thread = _loop_in_background(url, 2.0)
        time.sleep(0.3)
        shutil.copy2(new / "index.html", live / "index.html")
        time.sleep(0.6)
        shutil.copytree(new / "assets", live / "assets", dirs_exist_ok=True)
        (live / "version.txt").write_bytes((new / "version.txt").read_bytes())
        thread.join()
        loop.client.close()
    assert loop.stats.errors > 0
    assert any("HTTP 404" in s for s in loop.stats.samples)


def test_deploy_loop_single_probes(tmp_path: Path) -> None:
    site = make_site(tmp_path / "s", A)
    with serve(site) as url:
        loop = deploy_loop.Loop(url, extras=10)
        loop.probe()
        assert (loop.stats.errors, loop.stats.mixed, loop.stats.versions) == (0, 0, [A])
        assert loop.stats.requests == 9  # page, 4 core files, 2 extras, version.txt, page

        (site / "version.txt").write_text(f"commit={B}\n")  # server says B, page says A
        loop.probe()
        assert loop.stats.mixed == 1
        assert "version.txt is " + B in loop.stats.samples[-1]

        (site / "version.txt").write_text(f"commit={A}\n")
        js = next((site / "assets").glob("app.*.js"))
        js.write_bytes(b"tampered")
        loop.probe()
        assert loop.stats.mixed == 2
        assert "don't match" in loop.stats.samples[-1]

        js.unlink()
        loop.probe()
        assert loop.stats.errors == 1
        assert "HTTP 404" in loop.stats.samples[-1]
        loop.client.close()


def test_deploy_loop_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = make_site(tmp_path / "s", A)
    with serve(site) as url:
        code = deploy_loop.main([url, "--interval", "0.05", "--duration", "0.5"])
        assert code == 0
        assert "errors=0 mixed=0" in capsys.readouterr().out
        code = deploy_loop.main(
            [url, "--interval", "0.05", "--duration", "0.3", "--require-switch"]
        )
        assert code == 1


# --- the sshd drop-in ------------------------------------------------------------------------


def _sshd() -> str | None:
    for candidate in (shutil.which("sshd"), "/usr/sbin/sshd", "/usr/bin/sshd"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


def _sshd_dump(sshd: str, config: Path, key: Path, user: str) -> dict[str, list[str]]:
    proc = subprocess.run(
        [sshd, "-T", "-f", str(config), "-h", str(key), "-C", f"user={user},host=x,addr=192.0.2.1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        name, _, value = line.partition(" ")
        out.setdefault(name.lower(), []).append(value)  # OpenSSH 10.5 prints CamelCase
    return out


@pytest.mark.skipif(_sshd() is None or shutil.which("ssh-keygen") is None, reason="no sshd")
def test_sshd_dropin_confines_only_the_deploy_users(tmp_path: Path) -> None:
    sshd = _sshd()
    assert sshd is not None
    key = tmp_path / "hostkey"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    drop = tmp_path / "sshd_config.d"
    drop.mkdir()
    # Debian's layout: an Include at the top of sshd_config, then global settings that
    # must stay valid (and global) after the drop-in's Match blocks.
    main = (
        "KbdInteractiveAuthentication no\nUsePAM yes\nX11Forwarding yes\nPrintMotd no\n"
        "AcceptEnv LANG LC_*\nSubsystem sftp /usr/lib/openssh/sftp-server\n"
    )
    config = tmp_path / "sshd_config"
    config.write_text(main)
    baseline = subprocess.run(
        [sshd, "-t", "-f", str(config), "-h", str(key)], capture_output=True, check=False
    )
    if baseline.returncode != 0:
        pytest.skip(f"sshd -t fails here even without the drop-in: {baseline.stderr!r}")
    (drop / "00-hardening.conf").write_text(
        "PermitRootLogin no\nPasswordAuthentication no\nAllowUsers byron deploy deploy-staging\n"
    )
    (drop / "10-deploy.conf").write_text(SSHD_CONF.read_text())
    (drop / "50-cloud-init.conf").write_text("PasswordAuthentication no\n")
    config.write_text(f"Include {drop}/*.conf\n{main}")
    check = subprocess.run(
        [sshd, "-t", "-f", str(config), "-h", str(key)], capture_output=True, text=True, check=False
    )
    assert check.returncode == 0, check.stderr

    for user, env in (("deploy", "production"), ("deploy-staging", "staging")):
        dump = _sshd_dump(sshd, config, key, user)
        assert dump["forcecommand"] == [f"/usr/local/sbin/tff-receive {env}"]
        assert dump["authorizedkeysfile"] == [f"/etc/ssh/authorized_keys/{user}"]
        for name, value in {
            "permittty": "no",
            "disableforwarding": "yes",
            "allowtcpforwarding": "no",
            "allowagentforwarding": "no",
            "allowstreamlocalforwarding": "no",
            "x11forwarding": "no",
            "permittunnel": "no",
            "permituserrc": "no",
            "passwordauthentication": "no",
            "kbdinteractiveauthentication": "no",
            "authenticationmethods": "publickey",
            "maxsessions": "2",
        }.items():
            assert dump[name] == [value], (user, name, dump[name])
    byron = _sshd_dump(sshd, config, key, "byron")
    assert byron["forcecommand"] == ["none"]
    assert byron["x11forwarding"] == ["yes"], "a Match block leaked into the global settings"
    assert byron["permittty"] == ["yes"]
    assert "byron" in byron["allowusers"]


# --- deploy_key_tests.sh against a real (local, unprivileged) sshd --------------------------

KEY_TESTS = ROOT / "ops" / "deploy" / "deploy_key_tests.sh"
SFTP_SERVERS = (
    "/usr/lib/openssh/sftp-server",
    "/usr/lib/ssh/sftp-server",
    "/usr/libexec/openssh/sftp-server",
    "/usr/libexec/sftp-server",
)


def _key_test_tools() -> str | None:
    """Why the key tests can't run against a local sshd here, or None when they can."""
    if _sshd() is None:
        return "no sshd"
    if not any(os.access(p, os.X_OK) for p in SFTP_SERVERS):
        return "no sftp-server"
    for tool in ("ssh", "ssh-keygen", "sftp", "scp", "rsync", "curl", "bash", "timeout"):
        if shutil.which(tool) is None:
            return f"no {tool}"
    return None


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.slow
@pytest.mark.skipif(_key_test_tools() is not None, reason=str(_key_test_tools()))
@pytest.mark.parametrize("forwarding", ["confined", "local-forwarding-allowed"])
def test_key_tests_against_a_local_sshd(tmp_path: Path, forwarding: str) -> None:
    """The drop-in's settings on an sshd run as this user: every key test passes, and with
    local forwarding allowed the -L and -D checks (and only they) fail."""
    import getpass

    sshd = _sshd()
    assert sshd is not None
    sftp_server = next(p for p in SFTP_SERVERS if os.access(p, os.X_OK))
    for name in ("hostkey", "deploykey"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / name)],
            check=True,
        )
    root = tmp_path / "srv"
    (root / "prod").mkdir(parents=True)
    force = f"{sys.executable} -I {RECEIVE} --root {root} --log-file {tmp_path / 'rx.log'}"
    force += " production"
    options = "restrict" if forwarding == "confined" else "restrict,port-forwarding"
    pub = (tmp_path / "deploykey.pub").read_text().strip()
    (tmp_path / "authorized_keys").write_text(f'{options},command="{force}" {pub}\n')
    port = _free_port()
    dropin = SSHD_CONF.read_text().split("Match User deploy,deploy-staging\n", 1)[1]
    dropin = dropin.replace("Match all", "").strip()
    if forwarding != "confined":
        dropin = dropin.replace("DisableForwarding yes", "DisableForwarding no")
        dropin = dropin.replace("AllowTcpForwarding no", "AllowTcpForwarding local")
    user = getpass.getuser()
    (tmp_path / "sshd_config").write_text(
        f"Port {port}\nListenAddress 127.0.0.1\nHostKey {tmp_path / 'hostkey'}\n"
        f"PidFile {tmp_path / 'sshd.pid'}\nUsePAM no\nStrictModes no\n"
        f"AuthorizedKeysFile {tmp_path / 'authorized_keys'}\nAcceptEnv LANG LC_*\n"
        f"Subsystem sftp {sftp_server}\n"
        f"Match User {user}\n\tForceCommand {force}\n\t{dropin}\nMatch all\n"
    )
    (tmp_path / "known_hosts").write_text(
        f"[127.0.0.1]:{port} {(tmp_path / 'hostkey.pub').read_text().split()[0]} "
        f"{(tmp_path / 'hostkey.pub').read_text().split()[1]}\n"
    )
    log = (tmp_path / "sshd.log").open("wb")
    server = subprocess.Popen(
        [sshd, "-D", "-e", "-f", str(tmp_path / "sshd_config")], stdout=log, stderr=log
    )
    try:
        import socket

        deadline = time.monotonic() + 10
        while True:
            if server.poll() is not None:
                pytest.skip(
                    f"sshd won't run unprivileged here: {(tmp_path / 'sshd.log').read_text()}"
                )
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 1):
                break
            assert time.monotonic() < deadline, "sshd did not start"
            time.sleep(0.1)
        opts = f"-F /dev/null -o Port={port} -o User={user}"
        opts += f" -o UserKnownHostsFile={tmp_path / 'known_hosts'} -o StrictHostKeyChecking=yes"
        environ = {
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "LANG": "C.UTF-8",
            "TFF_ADMIN_HOST": "",
            "TFF_KEYTEST_SSH_OPTS": opts,
        }
        proc = subprocess.run(
            ["bash", str(KEY_TESTS), str(tmp_path / "deploykey"), "deploy", "127.0.0.1"],
            env=environ,
            capture_output=True,
            text=True,
            timeout=280,
            check=False,
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()
    out = proc.stdout
    failed = [line for line in out.splitlines() if line.startswith("FAIL")]
    if forwarding == "confined":
        assert proc.returncode == 0, out + proc.stderr
        assert out.rstrip().endswith("0 failed")
        assert out.count("PASS  malicious tar") == len(badtars.cases(FAKE))
    else:
        assert proc.returncode == 1, out
        assert failed == [
            "FAIL  ssh -L: tunnel refused (administratively prohibited)",
            "FAIL  ssh -D: SOCKS tunnel refused (administratively prohibited)",
        ], out


# --- ops/deploy.sh through a fake ssh --------------------------------------------------------

FAKE_SSH = """#!/bin/sh
# ssh [-o X]... HOST sudo -n -u USER /usr/local/sbin/tff-receive ENV ARGS...: run it locally.
while [ $# -gt 0 ]; do
  case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac
done
shift
[ "$1" = sudo ] && shift 4
[ "$1" = /usr/local/sbin/tff-receive ] || exit 97
shift
exec "$TFF_TEST_PYTHON" -I "$TFF_TEST_RECEIVE" --root "$TFF_TEST_ROOT" \\
  --log-file "$TFF_TEST_LOG" --lock-wait 0 "$@"
"""
FAKE_UV = """#!/bin/sh
# uv run [--group G]... CMD...: run CMD from the test's virtualenv.
[ "$1" = run ] || exit 98
shift
while [ $# -gt 0 ]; do
  case "$1" in --group) shift 2 ;; --*) shift ;; *) break ;; esac
done
exec "$@"
"""
FAKE_CURL = "#!/bin/sh\nexit 7\n"  # no request ever leaves the test


def _head_commit() -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


@pytest.mark.skipif(
    shutil.which("bash") is None or _head_commit() is None, reason="needs bash and git"
)
def test_deploy_sh_dir_rollback_status(rx: Receiver, tmp_path: Path) -> None:
    commit = _head_commit()
    assert commit is not None
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("uv", FAKE_UV), ("curl", FAKE_CURL)):
        (fake_bin / name).write_text(body)
        (fake_bin / name).chmod(0o755)
    environ = {
        "PATH": f"{fake_bin}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "TMPDIR": str(tmp_path),
        "LANG": "C.UTF-8",
        "TFF_TEST_PYTHON": sys.executable,
        "TFF_TEST_RECEIVE": str(RECEIVE),
        "TFF_TEST_ROOT": str(rx.root),
        "TFF_TEST_LOG": str(rx.log),
    }

    def deploy_sh(*args: str) -> Result:
        proc = subprocess.run(
            ["bash", str(DEPLOY_SH), *args],
            env=environ,
            capture_output=True,
            timeout=300,
            check=False,
        )
        return Result(proc.returncode, proc.stdout.decode(), proc.stderr.decode())

    rx.deploy(make_site(tmp_path / "old", A), A, env="staging")
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "index.html").write_text("<!doctype html><title>Coming soon</title>\n")
    (stub / "stub.css").write_text("main{text-align:center}\n")

    result = deploy_sh(
        "staging", "--dir", str(stub), "--commit", commit, "--no-live", "--any-commit"
    )
    assert result.code == 0, result.err
    assert rx.current("staging") == commit
    assert (
        rx.dir("staging") / "releases" / commit / "version.txt"
    ).read_text() == f"commit={commit}\n"
    assert "uploading 3 of 3 files" in result.err
    assert not list(tmp_path.glob("tff-deploy.*")), "the temporary directory was not removed"

    again = deploy_sh(
        "staging", "--dir", str(stub), "--commit", commit, "--no-live", "--any-commit"
    )
    assert again.code == 0
    assert "uploading 0 of 3 files" in again.err

    status = deploy_sh("status", "staging")
    assert status.code == 0
    assert f"current: {commit}" in status.out

    back = deploy_sh("rollback", "staging", "--hold")
    assert back.code == 0, back.err
    assert rx.current("staging") == A
    assert "live version.txt: unreachable" in back.err
    held = deploy_sh("staging", "--dir", str(stub), "--commit", commit, "--no-live", "--any-commit")
    assert held.code != 0
    assert "unhold" in held.err
    assert deploy_sh("unhold", "staging").code == 0
    assert deploy_sh("rollback", "staging", commit).code == 0
    assert rx.current("staging") == commit
    assert rx.current("production") is None

    bad = tmp_path / "bad"
    shutil.copytree(stub, bad)
    (bad / "Notes.TXT").write_text("x")
    refused = deploy_sh(
        "staging", "--dir", str(bad), "--commit", commit, "--no-live", "--any-commit"
    )
    assert refused.code != 0
    assert "Notes.TXT" in refused.err

    assert deploy_sh().code == 2
    assert deploy_sh("status").code == 2
    assert deploy_sh("rollback", "nowhere").code == 2


RECORDING_UV = """#!/bin/sh
# uv sync ... | uv run [--group G]... CMD ...: records each call; fakes tff-site's build steps
# and pytest, and runs the real tff-site pack.
echo "$PWD :: TFF_SITE_DIR=${TFF_SITE_DIR:-} TFF_SITE_DATA=${TFF_SITE_DATA:-} uv $*" >> "$TFF_TEST_CALLS"
case "$1" in sync) exit 0 ;; run) shift ;; *) exit 98 ;; esac
while [ $# -gt 0 ]; do
  case "$1" in --group) shift 2 ;; --*) shift ;; *) break ;; esac
done
if [ "$1" = pytest ]; then
  [ "$2" = tests/live ] && exit "${TFF_TEST_LIVE_EXIT:-0}"
  exit 0
fi
[ "$1" = tff-site ] || exit 99
shift
case "$1" in
  fetch-fonts|check) exit 0 ;;
  build)  # build --out DIR --commit SHA
    mkdir -p "$3/assets"
    printf '<!doctype html><title>t</title>\\n' > "$3/index.html"
    printf 'body{}\\n' > "$3/assets/style.0123456789.css"
    printf 'commit=%s\\n' "$5" > "$3/version.txt"
    exit 0 ;;
esac
exec "$TFF_TEST_PYTHON" -c 'import sys; from tff_site.cli import main; sys.exit(main(sys.argv[1:]))' "$@"
"""
LIVE_CURL = """#!/bin/sh
# curl ... URL: answers version.txt from the test's receiver root, so the switch shows.
for last; do :; done
case "$last" in
  https://staging.trulyfreefonts.com/version.txt) cat "$TFF_TEST_ROOT/staging/current/version.txt" ;;
  https://trulyfreefonts.com/version.txt) cat "$TFF_TEST_ROOT/prod/current/version.txt" ;;
  *) exit 7 ;;
esac
"""


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None, reason="git")
def test_deploy_sh_builds_in_a_temporary_worktree(rx: Receiver, tmp_path: Path) -> None:
    """The build path, in a throwaway repository (the real one is never touched)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("uv", RECORDING_UV), ("curl", LIVE_CURL)):
        (fake_bin / name).write_text(body)
        (fake_bin / name).chmod(0o755)
    calls = tmp_path / "calls.log"
    work = tmp_path / "tmp"
    work.mkdir()
    environ = {
        "PATH": f"{fake_bin}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(work),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "TFF_TEST_PYTHON": sys.executable,
        "TFF_TEST_RECEIVE": str(RECEIVE),
        "TFF_TEST_ROOT": str(rx.root),
        "TFF_TEST_LOG": str(rx.log),
        "TFF_TEST_CALLS": str(calls),
    }
    repo = tmp_path / "repo"
    (repo / "ops").mkdir(parents=True)
    shutil.copy2(DEPLOY_SH, repo / "ops" / "deploy.sh")

    def git(*args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], env=environ, capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    # main carries a real (non-synthetic) catalog; the commit off main has none.
    (repo / "build").mkdir()
    (repo / "build" / "catalog-site.json").write_text('{"synthetic": false}\n')
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    on_main = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", on_main)
    (repo / "README.md").write_text("two\n")
    git("rm", "-q", "build/catalog-site.json")
    git("add", "-A")
    git("commit", "-q", "-m", "two")
    off_main = git("rev-parse", "HEAD")

    def deploy_sh(*args: str, **extra: str) -> Result:
        calls.write_text("")
        proc = subprocess.run(
            ["bash", str(repo / "ops" / "deploy.sh"), *args],
            env={**environ, **extra},
            capture_output=True,
            timeout=300,
            check=False,
        )
        return Result(proc.returncode, proc.stdout.decode(), proc.stderr.decode())

    result = deploy_sh("staging")
    assert result.code == 0, result.err
    assert rx.current("staging") == off_main
    lines = calls.read_text().splitlines()
    src = {line.split(" :: ")[0] for line in lines}
    assert len(src) == 1, "every step runs in the one worktree"
    src_dir = src.pop()
    assert src_dir.endswith("/src")
    steps = [line.split(" uv ", 1)[1] for line in lines]
    site = steps[2].split("--out ")[1].split()[0]
    # No build/catalog-site.json in this repository: staging gets the sample, and the site
    # tests compare the page with that same file.
    sample = f"{src_dir}/tests/fixtures/catalog-site.sample.json"
    assert steps[:4] == [
        "sync --locked --group browser",
        f"run tff-site fetch-fonts --data {sample}",
        f"run tff-site build --out {site} --commit {off_main} --data {sample} --drafts",
        f"run tff-site check {site}",
    ]
    assert (
        f"TFF_SITE_DIR={site} TFF_SITE_DATA={sample} uv run --group browser pytest tests/site"
        in lines[4]
    )
    assert "--ignore=tests/site/test_perf.py --browser chromium --browser firefox" in lines[4]
    assert steps[-1] == (
        "run --group browser pytest tests/live -q --base-url https://staging.trulyfreefonts.com"
        f" --expect-commit {off_main} --browser chromium --browser firefox"
    )
    assert git("worktree", "list", "--porcelain").count("worktree ") == 1, "worktree left"
    assert list(work.iterdir()) == [], "temporary files left"

    fast = deploy_sh("staging", "--fast", "--commit", on_main)
    assert fast.code == 0, fast.err
    assert rx.current("staging") == on_main
    assert "pytest tests/site" not in calls.read_text()
    assert "pytest tests/live" in calls.read_text()

    refused = deploy_sh("production")
    assert refused.code == 1
    assert "not on origin/main" in refused.err
    assert rx.current("production") is None
    assert calls.read_text() == "", "nothing built for a refused commit"

    no_catalog = deploy_sh("production", "--fast", "--commit", off_main, "--any-commit")
    assert no_catalog.code == 1
    assert "production never gets the sample" in no_catalog.err
    assert rx.current("production") is None
    assert calls.read_text() == "", "nothing built without a real catalog"

    # Header phase A (no CSP on production) refuses a build deploy to production.
    git("switch", "-q", "-c", "phase-a", on_main)
    (repo / "ops" / "Caddyfile").write_text(
        "trulyfreefonts.com {\n\theader -Content-Security-Policy\n}\n"
    )
    git("add", "-A")
    git("commit", "-q", "-m", "phase a")
    phase_a = git("rev-parse", "HEAD")
    git("switch", "-q", "main")
    refused = deploy_sh("production", "--fast", "--commit", phase_a, "--any-commit")
    assert refused.code == 1
    assert "still in header phase A" in refused.err
    assert rx.current("production") is None

    live_fail = deploy_sh("production", "--fast", "--commit", on_main, TFF_TEST_LIVE_EXIT="1")
    assert live_fail.code == 1
    assert "live test failed on production" in live_fail.err
    assert "ops/deploy.sh rollback production" in live_fail.err
    assert rx.current("production") == on_main
    assert git("worktree", "list", "--porcelain").count("worktree ") == 1
    built = [line for line in calls.read_text().splitlines() if "tff-site build" in line]
    assert len(built) == 1
    assert "--drafts" not in built[0], "production never publishes draft posts (M2 step 7b)"
    assert built[0].endswith("/build/catalog-site.json"), "production builds the real catalog"


# --- the server's Python ---------------------------------------------------------------------


def test_receiver_is_python_3_13_syntax() -> None:
    """The server runs Python 3.13: no syntax from 3.14 (ruff format with target py314 would
    rewrite ``except (A, B):`` into the 3.14-only form, so the script names its tuples)."""
    for path in (RECEIVE, BADTARS):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 13))
    source = RECEIVE.read_text()
    assert source.startswith("#!/usr/bin/python3 -I\n")
    assert os.access(RECEIVE, os.X_OK)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
