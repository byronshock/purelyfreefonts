"""``tff-site pack`` and ``tff-site fetch-fonts`` (Milestone 2 step 11, design-m2 §5).

- **pack**: the same site gives the same bytes whatever the timestamps and permissions; the
  manifest; ``--only``; the receiver's path, extension and size rules; and that pack's copy
  of those rules equals ``ops/deploy/tff-receive``'s and site/CONTRACT.md's.
- **fetch-fonts**: download, check and cache font files through an httpx mock transport
  (retries, redirects, size and sha256 mismatches, a damaged cache). One test marked
  ``network`` fetches a real pinned OFL file from the sample catalog.

The full server round trip (plan, upload, activate) is in tests/ops/test_receive.py.
"""

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import httpx
import pytest

from tff_catalog import jsonio
from tff_site import cli, fonts, pack

ROOT = Path(__file__).resolve().parents[2]
RECEIVE = ROOT / "ops" / "deploy" / "tff-receive"
CONTRACT = ROOT / "site" / "CONTRACT.md"
SAMPLE = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
COMMIT = "a" * 40


def _load_receiver():
    loader = importlib.machinery.SourceFileLoader("tff_receive_for_pack", str(RECEIVE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = dont_write
    return module


def write_site(path: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_bytes(data)
    return path


def small_site(path: Path, commit: str = COMMIT) -> Path:
    return write_site(
        path,
        {
            "index.html": b"<!doctype html><title>t</title>\n",
            "version.txt": f"commit={commit}\nrun_date=2026-09-25\n".encode(),
            "methodology/index.html": b"<!doctype html><title>m</title>\n",
            "assets/app.0123456789.js": b"const Main = {};\n",
            "assets/fonts/sample-sans-01.abcdefabcd.ttf": bytes(range(256)) * 4,
            "assets/specimens/sample-sans-01.0000000000.svg": b"<svg/>",
            "favicon.ico": b"\x00\x00\x01\x00",
            "robots.txt": b"User-agent: *\n",
            "sitemap.xml": b"<urlset/>\n",
        },
    )


def tar_bytes(site: Path, **kwargs) -> bytes:
    buf = io.BytesIO()
    pack.pack(site, buf, **kwargs)
    return buf.getvalue()


# --- manifest --------------------------------------------------------------------------------


def test_manifest_lists_every_file_with_hash_and_size(tmp_path: Path) -> None:
    site = small_site(tmp_path / "site")
    doc = pack.manifest(site)
    assert set(doc) == {"commit", "files"}
    assert doc["commit"] == COMMIT
    assert list(doc["files"]) == sorted(doc["files"])
    assert len(doc["files"]) == 9
    font = site / "assets/fonts/sample-sans-01.abcdefabcd.ttf"
    assert doc["files"]["assets/fonts/sample-sans-01.abcdefabcd.ttf"] == {
        "sha256": hashlib.sha256(font.read_bytes()).hexdigest(),
        "size": 1024,
    }
    assert pack.manifest(site, commit="b" * 40)["commit"] == "b" * 40


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        (lambda s: (s / "Index.html").write_text("x"), "Index.html: path not allowed"),
        (lambda s: (s / ".DS_Store").write_text("x"), ".DS_Store: path not allowed"),
        (lambda s: (s / "notes.md").write_text("x"), "notes.md: extension not allowed"),
        (lambda s: (s / "LICENSE").write_text("x"), "LICENSE: path not allowed"),
        (lambda s: (s / "a b.txt").write_text("x"), "a b.txt: path not allowed"),
        (lambda s: (s / "link.html").symlink_to(s / "index.html"), "link.html: symlink"),
        (lambda s: (s / "dirlink").symlink_to(s / "assets"), "dirlink: symlink"),
        (lambda s: os.mkfifo(s / "pipe.txt"), "pipe.txt: not a regular file"),
        (lambda s: (s / "version.txt").unlink(), "version.txt is missing"),
        (lambda s: (s / "version.txt").write_text("run_date=x\n"), "no commit= line"),
    ],
)
def test_manifest_refuses_what_the_receiver_would(tmp_path: Path, setup, problem: str) -> None:
    site = small_site(tmp_path / "site")
    setup(site)
    with pytest.raises(pack.PackError) as info:
        pack.manifest(site)
    assert any(problem in p for p in info.value.problems) or problem in str(info.value)


def test_manifest_size_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = small_site(tmp_path / "site")
    monkeypatch.setattr(pack, "MAX_FILE_BYTES", 1000)
    with pytest.raises(pack.PackError, match="over the 1000-byte limit"):
        pack.manifest(site)
    monkeypatch.setattr(pack, "MAX_FILE_BYTES", 20_000_000)
    monkeypatch.setattr(pack, "MAX_FILES", 8)
    with pytest.raises(pack.PackError, match="9 files, over the 8-file limit"):
        pack.manifest(site)
    monkeypatch.setattr(pack, "MAX_FILES", 20_000)
    monkeypatch.setattr(pack, "MAX_TOTAL_BYTES", 1100)
    with pytest.raises(pack.PackError, match="in total"):
        pack.manifest(site)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(pack.PackError, match="holds no files"):
        pack.manifest(empty, commit=COMMIT)
    with pytest.raises(pack.PackError, match="is not a directory"):
        pack.manifest(tmp_path / "missing")


# --- the tar ---------------------------------------------------------------------------------


def test_same_site_same_bytes(tmp_path: Path) -> None:
    one = small_site(tmp_path / "one")
    two = small_site(tmp_path / "two")
    for path in two.rglob("*"):  # other timestamps and permissions change nothing
        os.utime(path, (1_000_000_000, 1_000_000_000))
        if path.is_file():
            path.chmod(0o600)
    assert tar_bytes(one) == tar_bytes(two)
    assert tar_bytes(one) == tar_bytes(one)
    assert tar_bytes(one, only=["index.html"]) == tar_bytes(two, only=["index.html"])


def test_tar_members_are_plain_and_sorted(tmp_path: Path) -> None:
    site = small_site(tmp_path / "site")
    doc = pack.manifest(site)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes(site)), mode="r:") as tar:
        members = tar.getmembers()
        names = [m.name for m in members]
        assert names == sorted(names)
        dirs = [m.name for m in members if m.isdir()]
        assert dirs == ["assets", "assets/fonts", "assets/specimens", "methodology"]
        for m in members:
            assert m.isdir() or m.isreg()
            assert (m.mtime, m.uid, m.gid, m.uname, m.gname) == (0, 0, 0, "", "")
            assert m.mode == (0o755 if m.isdir() else 0o644)
            if m.isreg():
                data = tar.extractfile(m).read()
                assert hashlib.sha256(data).hexdigest() == doc["files"][m.name]["sha256"]
        assert sorted(n for n in names if n not in dirs) == sorted(doc["files"])


def test_only_sends_the_listed_files_and_their_directories(tmp_path: Path) -> None:
    site = small_site(tmp_path / "site")
    only = ["assets/fonts/sample-sans-01.abcdefabcd.ttf", "version.txt"]
    manifest_path = tmp_path / "out" / "site.manifest.json"
    doc = pack.pack(site, io.BytesIO(), only=only, manifest_path=manifest_path)
    assert len(doc["files"]) == 9, "the manifest always describes the whole site"
    assert manifest_path.read_bytes() == jsonio.canonical_bytes(doc) + b"\n"
    with tarfile.open(fileobj=io.BytesIO(tar_bytes(site, only=only)), mode="r:") as tar:
        assert tar.getnames() == ["assets", "assets/fonts", *only]
    with tarfile.open(fileobj=io.BytesIO(tar_bytes(site, only=[])), mode="r:") as tar:
        assert tar.getnames() == []
    with pytest.raises(pack.PackError, match="not in the site"):
        tar_bytes(site, only=["nope.html"])


def test_a_file_changed_while_packing_fails(tmp_path: Path, monkeypatch) -> None:
    site = small_site(tmp_path / "site")
    real = pack.manifest

    def stale(site_dir: Path, *, commit: str | None = None) -> dict:
        doc = real(site_dir, commit=commit)
        doc["files"]["index.html"]["sha256"] = "0" * 64
        return doc

    monkeypatch.setattr(pack, "manifest", stale)
    with pytest.raises(pack.PackError, match=r"index\.html: changed while packing"):
        tar_bytes(site)


def test_pack_rules_equal_the_receivers_and_the_contract() -> None:
    receiver = _load_receiver()
    assert pack.PATH_RE.pattern == receiver.PATH_RE.pattern
    assert pack.EXTENSIONS == receiver.EXTENSIONS
    assert pack.MAX_FILE_BYTES == receiver.MAX_FILE_BYTES
    assert pack.MAX_TOTAL_BYTES == receiver.MAX_TOTAL_BYTES
    assert pack.MAX_FILES == receiver.MAX_FILES
    rule = f"`^{pack.PATH_RE.pattern}$`"
    assert rule in CONTRACT.read_text(), "site/CONTRACT.md states the receiver's path rule"
    assert rule in pack.__doc__


def test_cli_pack(tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    site = small_site(tmp_path / "site")
    out, manifest_path, only = tmp_path / "site.tar", tmp_path / "m.json", tmp_path / "need.txt"
    assert cli.main(["pack", str(site), "--out", str(out), "--manifest", str(manifest_path)]) == 0
    assert out.read_bytes() == tar_bytes(site)
    assert json.loads(manifest_path.read_text()) == pack.manifest(site)
    only.write_text("version.txt\n\nindex.html\n")
    assert cli.main(["pack", str(site), "--only", str(only)]) == 0
    assert capsysbinary.readouterr().out == tar_bytes(site, only=["index.html", "version.txt"])


# --- fetch-fonts -----------------------------------------------------------------------------

FONT = b"\x00\x01\x00\x00" + bytes(range(256)) * 16
OTHER = b"OTTO" + bytes(100)
HOP = b"wOF2" + bytes(64)
BAD = bytes(len(FONT))  # the size of FONT, other bytes


def _file(url: str, data: bytes, **override) -> dict:
    entry = {"url": url, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return {**entry, "format": "ttf", **override}


def _catalog(path: Path, fonts_: list[dict]) -> Path:
    path.write_text(json.dumps({"fonts": fonts_}))
    return path


class Upstream:
    """A mock upstream: ``routes`` maps URL to a list of responses served in turn."""

    def __init__(self, routes: dict[str, list[httpx.Response]]) -> None:
        self.routes = routes
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        assert request.headers["user-agent"].startswith("trulyfreefonts-site/")
        queue = self.routes.get(url)
        if not queue:
            return httpx.Response(404)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = httpx.MockTransport(self)
        monkeypatch.setattr(
            fonts,
            "_client",
            lambda: httpx.Client(
                transport=transport,
                headers={"User-Agent": fonts.USER_AGENT},
                follow_redirects=True,
            ),
        )
        monkeypatch.setattr(fonts, "RETRY_DELAY", 0.0)


def test_fetch_fonts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = "https://fonts.example/"
    upstream = Upstream(
        {
            up + "good.ttf": [httpx.Response(200, content=FONT)],
            up + "bad.ttf": [httpx.Response(200, content=BAD)],
            up + "flaky.ttf": [httpx.Response(503), httpx.Response(200, content=OTHER)],
            up + "big.ttf": [httpx.Response(200, content=FONT + b"extra")],
            up + "moved.ttf": [
                httpx.Response(302, headers={"location": "http://fonts.example/plain.ttf"})
            ],
            up + "hop.ttf": [httpx.Response(301, headers={"location": "/files/hop.ttf"})],
            up + "files/hop.ttf": [httpx.Response(200, content=HOP)],
            up + "loop.ttf": [httpx.Response(302, headers={"location": up + "loop.ttf"})],
        }
    )
    upstream.install(monkeypatch)
    data = _catalog(
        tmp_path / "catalog-site.json",
        [
            {"id": "f-good", "preview_ok": True, "font_file": _file(up + "good.ttf", FONT)},
            {"id": "f-same", "preview_ok": True, "font_file": _file(up + "good.ttf", FONT)},
            {"id": "f-bad", "preview_ok": True, "font_file": _file(up + "bad.ttf", FONT)},
            {"id": "f-flaky", "preview_ok": True, "font_file": _file(up + "flaky.ttf", OTHER)},
            {"id": "f-big", "preview_ok": True, "font_file": _file(up + "big.ttf", FONT + b"x")},
            {"id": "f-gone", "preview_ok": True, "font_file": _file(up + "gone.ttf", b"g")},
            {"id": "f-moved", "preview_ok": True, "font_file": _file(up + "moved.ttf", b"m")},
            {"id": "f-hop", "preview_ok": True, "font_file": _file(up + "hop.ttf", HOP)},
            {"id": "f-loop", "preview_ok": True, "font_file": _file(up + "loop.ttf", b"l")},
            {"id": "f-http", "preview_ok": True, "font_file": _file("http://x/a.ttf", b"h")},
            {
                "id": "f-sha",
                "preview_ok": True,
                "font_file": {**_file(up + "a", b"s"), "sha256": "../../x"},
            },
            {"id": "f-noprev", "preview_ok": False, "font_file": _file(up + "good.ttf", FONT)},
            {"id": "f-none", "preview_ok": True, "font_file": None},
        ],
    )
    cache = tmp_path / "cache"
    report = fonts.fetch_fonts(data, cache)
    assert report.fetched == ["f-flaky", "f-good", "f-hop"]
    assert report.cached == ["f-same"]
    assert set(report.failed) == {
        *("f-bad", "f-big", "f-gone", "f-moved", "f-http", "f-sha", "f-loop")
    }
    assert "sha256 mismatch" in report.failed["f-bad"]
    assert "larger than" in report.failed["f-big"]
    assert "HTTP 404" in report.failed["f-gone"]
    assert "non-https" in report.failed["f-moved"]
    assert "http://fonts.example/plain.ttf" not in upstream.requests, "plain http was requested"
    assert "more than 5 redirects" in report.failed["f-loop"]
    assert upstream.requests.count(up + "loop.ttf") == fonts.MAX_REDIRECTS + 1
    assert "not an https URL" in report.failed["f-http"]
    assert "bad sha256" in report.failed["f-sha"]
    assert sorted(p.name for p in cache.iterdir()) == sorted(
        hashlib.sha256(x).hexdigest() for x in (FONT, OTHER, HOP)
    )
    assert fonts.file_sha256(fonts.cache_path(hashlib.sha256(FONT).hexdigest(), cache)) == (
        hashlib.sha256(FONT).hexdigest()
    )
    assert upstream.requests.count(up + "flaky.ttf") == 2
    assert upstream.requests.count(up + "good.ttf") == 1
    assert not list(tmp_path.rglob("x")), "a bad sha256 must never become a path"

    upstream.requests.clear()
    again = fonts.fetch_fonts(data, cache)
    assert again.fetched == []
    # The cache is content-addressed: f-bad's file (FONT's sha256) is there now too.
    assert again.cached == ["f-bad", "f-flaky", "f-good", "f-hop", "f-same"]
    assert all("good.ttf" not in u and "flaky" not in u for u in upstream.requests)

    damaged = fonts.cache_path(hashlib.sha256(FONT).hexdigest(), cache)
    damaged.write_bytes(b"damaged")
    third = fonts.fetch_fonts(data, cache)
    assert third.fetched == ["f-good"]
    assert "sha256 mismatch" in third.failed["f-bad"]
    assert damaged.read_bytes() == FONT


def test_fetch_fonts_gives_up_after_retries(tmp_path: Path, monkeypatch) -> None:
    url = "https://fonts.example/down.ttf"
    upstream = Upstream({url: [httpx.Response(502)]})
    upstream.install(monkeypatch)
    data = _catalog(
        tmp_path / "c.json", [{"id": "f", "preview_ok": True, "font_file": _file(url, FONT)}]
    )
    report = fonts.fetch_fonts(data, tmp_path / "cache")
    assert report.failed == {"f": f"HTTP 502 from {url}"}
    assert upstream.requests == [url] * fonts.ATTEMPTS
    assert list((tmp_path / "cache").iterdir()) == []


def test_cli_fetch_fonts(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    url = "https://fonts.example/good.ttf"
    Upstream({url: [httpx.Response(200, content=FONT)]}).install(monkeypatch)
    good = _catalog(
        tmp_path / "good.json", [{"id": "f", "preview_ok": True, "font_file": _file(url, FONT)}]
    )
    args = ["fetch-fonts", "--data", str(good), "--cache", str(tmp_path / "cache")]
    assert cli.main(args) == 0
    assert "fetched 1, cached 0, failed 0" in capsys.readouterr().out
    bad = _catalog(
        tmp_path / "bad.json", [{"id": "g", "preview_ok": True, "font_file": _file(url, BAD)}]
    )
    args = ["fetch-fonts", "--data", str(bad), "--cache", str(tmp_path / "cache")]
    assert cli.main(args) == 1
    assert "g: sha256 mismatch" in capsys.readouterr().err


@pytest.mark.network
def test_fetch_a_real_pinned_font(tmp_path: Path) -> None:
    """Orbitron (38 KB, OFL) from the sample catalog, checked against its pinned sha256."""
    sample = json.loads(SAMPLE.read_text())
    one = [f for f in sample["fonts"] if f["id"] == "sample-display-10"]
    data = _catalog(tmp_path / "c.json", one)
    report = fonts.fetch_fonts(data, tmp_path / "cache")
    assert report.fetched == ["sample-display-10"], report.failed
    sha = one[0]["font_file"]["sha256"]
    assert fonts.file_sha256(fonts.cache_path(sha, tmp_path / "cache")) == sha
