"""``tff-site fetch-fonts``: download the font files "Type your own text" serves.

For every font with ``preview_ok`` and a ``font_file``, it downloads ``font_file.url`` into
the cache as ``<cache>/<sha256>`` (the file name is the expected hash), checks the sha256 and
size, and skips files already cached. A mismatch is an error and nothing is kept. The files
are served unchanged (D3) and never committed. ``tff-site build`` reads only the cache.

Details:

- A cached file is re-hashed on every run; a damaged one is deleted and fetched again.
- Downloads go to a temporary file in the cache directory and are renamed into place only
  after the size and sha256 match, so an interrupted run never leaves a bad file behind.
- Only ``https://`` URLs are fetched, redirects included, and a body longer than
  ``font_file.size`` is cut off and refused.
- Fonts sharing one file (the same sha256) download it once; the others count as cached.
  A URL that failed is not tried again in the same run, but another URL for the same file is.
- Transport errors, HTTP 429 and 5xx are retried ``ATTEMPTS`` times; other statuses fail.
- A file larger than ``MAX_FONT_BYTES`` (the schema's limit) is refused before any request.

Fonts inside release archives. A ``font_file.url`` of the form ``<archive>.zip#<member>``
(the pipeline's reference to one font in a zip archive; the member path percent-encoded)
names a font that upstream publishes only inside an archive. The archive is downloaded
once per run into a temporary folder in the cache directory (several fonts may share one,
as the TeX Gyre families do), at most ``MAX_ARCHIVE_BYTES``, and deleted at the end. The
member is found as the pipeline found it: that path, else the one entry whose path ends in
``/<member>``, else the one entry of that file name. Its recorded size must be
``font_file.size`` before a byte is read, it is extracted with the stdlib ``zipfile`` (which
checks its CRC) and cut off past that size, and it is kept only when its size and sha256
match: the bytes served are the member's, unchanged (M2-D5). An archive that could not be
fetched is not tried again in the same run.
"""

import hashlib
import json
import os
import re
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import httpx

from tff_catalog import __version__

DEFAULT_CACHE = Path.home() / ".cache" / "tff" / "fonts"
USER_AGENT = f"trulyfreefonts-site/{__version__} (+https://github.com/byronshock/trulyfreefonts)"
ATTEMPTS = 3
RETRY_DELAY = 2.0  # seconds, times the attempt number
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_CHUNK = 1 << 16
MAX_FONT_BYTES = 20_000_000  # catalog-site.schema.json font_file.size maximum
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024  # a release archive larger than this is not fetched
ARCHIVE_SUFFIXES = (".zip",)


@dataclass(frozen=True, slots=True)
class FetchReport:
    """Font ids fetched now, already cached, and failed (with the reason)."""

    fetched: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


class FetchError(Exception):
    """One font file could not be fetched; the message says why."""


def cache_path(sha256: str, cache_dir: Path = DEFAULT_CACHE) -> Path:
    """Return where the file with this sha256 lives in the cache."""
    return cache_dir / sha256


def file_sha256(path: Path) -> str:
    """Return the hex sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _client() -> httpx.Client:
    """The HTTP client (a seam: tests swap in one with a mock transport)."""
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)


def _is_cached(path: Path, sha256: str, size: int) -> bool:
    """True when ``path`` holds the expected bytes; a wrong file is deleted."""
    try:
        if not path.is_file() or path.is_symlink():
            return False
        if path.stat().st_size == size and file_sha256(path) == sha256:
            return True
    except OSError:
        return False
    path.unlink(missing_ok=True)
    return False


def split_member(url: str) -> tuple[str, str] | None:
    """``(archive url, member path)`` for ``<archive>.zip#<member>``, else None."""
    archive, sep, fragment = url.partition("#")
    if not sep or not fragment:
        return None
    if not archive.split("?", 1)[0].lower().endswith(ARCHIVE_SUFFIXES):
        return None
    return archive, unquote(fragment)


def _download_once(client: httpx.Client, url: str, dest: Path, size: int) -> tuple[int, str]:
    # Redirects are followed here, not by httpx, so that a plain-http hop is refused before
    # any request goes to it.
    target = httpx.URL(url)
    for _ in range(MAX_REDIRECTS + 1):
        with client.stream("GET", target, follow_redirects=False) as response:
            if response.is_redirect:
                target = response.url.join(response.headers["location"])
                if target.scheme != "https":
                    raise FetchError(f"redirected to a non-https URL: {target}")
                continue
            if response.status_code != httpx.codes.OK:
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
            digest = hashlib.sha256()
            received = 0
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes(_CHUNK):
                    received += len(chunk)
                    if received > size:
                        raise FetchError(f"larger than the expected {size} bytes")
                    digest.update(chunk)
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            return received, digest.hexdigest()
    raise FetchError(f"more than {MAX_REDIRECTS} redirects from {url}")


def _fetch(client: httpx.Client, url: str, dest: Path, limit: int) -> tuple[int, str]:
    """Download ``url`` into ``dest`` (at most ``limit`` bytes), retrying as the module
    docstring says; return the bytes received and their sha256."""
    if not url.startswith("https://"):
        raise FetchError(f"not an https URL: {url!r}")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return _download_once(client, url, dest, limit)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if attempt == ATTEMPTS or not (status == 429 or status >= 500):
                raise FetchError(f"HTTP {status} from {url}") from None
        except httpx.TransportError as exc:
            if attempt == ATTEMPTS:
                raise FetchError(f"{type(exc).__name__} fetching {url}: {exc}") from None
        time.sleep(RETRY_DELAY * attempt)
    raise AssertionError("unreachable: the last attempt returns or raises")


def _keep(part: Path, dest: Path, received: int, digest: str, font_file: dict) -> None:
    """Move ``part`` into place as ``dest`` when its size and sha256 are the catalog's."""
    if received != font_file["size"]:
        raise FetchError(f"got {received} bytes, expected {font_file['size']}")
    if digest != font_file["sha256"]:
        raise FetchError(f"sha256 mismatch: got {digest}, expected {font_file['sha256']}")
    part.chmod(0o644)
    part.replace(dest)


def _download(client: httpx.Client, font_file: dict, dest: Path) -> None:
    """Fetch ``font_file`` into ``dest`` after checking its size and sha256."""
    url = font_file["url"]
    if not isinstance(url, str):
        raise FetchError(f"not an https URL: {url!r}")
    part = dest.with_name(f".{dest.name}.{os.getpid()}.part")
    try:
        received, digest = _fetch(client, url, part, font_file["size"])
        _keep(part, dest, received, digest, font_file)
    finally:
        part.unlink(missing_ok=True)


class _Archives:
    """The release archives one run downloads, each once, in a temporary folder."""

    def __init__(self, client: httpx.Client, folder: Path) -> None:
        self.client, self.folder = client, folder
        self.paths: dict[str, Path] = {}
        self.failed: dict[str, str] = {}

    def get(self, url: str) -> Path:
        """The downloaded archive at ``url``; ``FetchError`` when it can't be had."""
        if url in self.failed:
            raise FetchError(self.failed[url])
        if url not in self.paths:
            dest = self.folder / f"{hashlib.sha256(url.encode()).hexdigest()}.zip"
            try:
                _fetch(self.client, url, dest, MAX_ARCHIVE_BYTES)
            except (FetchError, OSError, httpx.HTTPError) as exc:
                dest.unlink(missing_ok=True)
                self.failed[url] = f"archive {url}: {exc}"
                raise FetchError(self.failed[url]) from None
            self.paths[url] = dest
        return self.paths[url]


def _zip_entry(zf: zipfile.ZipFile, member: str) -> zipfile.ZipInfo:
    """The entry ``member`` names, found as ``tff_catalog.fontfiles`` finds it: that path,
    else the one path ending in ``/<member>``, else the one file of that name."""
    infos = [i for i in zf.infolist() if not i.is_dir()]
    for rule in (
        lambda i: i.filename == member,
        lambda i: i.filename.endswith("/" + member.lstrip("/")),
        lambda i: i.filename.rsplit("/", 1)[-1] == member.rsplit("/", 1)[-1],
    ):
        found = [i for i in infos if rule(i)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise FetchError(f"{len(found)} archive entries match {member!r}")
    raise FetchError(f"no archive entry {member!r}")


def _extract(archive: Path, member: str, dest: Path, size: int) -> tuple[int, str]:
    """Copy ``member`` of the zip at ``archive`` into ``dest``, at most ``size`` bytes; return
    the bytes written and their sha256. The entry must say ``size`` before any is read."""
    try:
        with zipfile.ZipFile(archive) as zf:
            info = _zip_entry(zf, member)
            if info.file_size != size:
                raise FetchError(f"archive entry {info.filename!r} is {info.file_size} bytes")
            digest = hashlib.sha256()
            received = 0
            with zf.open(info) as src, dest.open("wb") as fh:
                for chunk in iter(lambda: src.read(_CHUNK), b""):
                    received += len(chunk)
                    if received > size:
                        raise FetchError(f"larger than the expected {size} bytes")
                    digest.update(chunk)
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError) as exc:
        raise FetchError(f"not a readable zip archive: {exc}") from None
    return received, digest.hexdigest()


def _download_member(font_file: dict, dest: Path, archives: _Archives) -> None:
    """Extract ``font_file`` from its release archive into ``dest``, checking its size and
    sha256 (the module docstring)."""
    parts = split_member(font_file["url"])
    assert parts is not None
    archive_url, member = parts
    archive = archives.get(archive_url)
    part = dest.with_name(f".{dest.name}.{os.getpid()}.part")
    try:
        received, digest = _extract(archive, member, part, font_file["size"])
        _keep(part, dest, received, digest, font_file)
    finally:
        part.unlink(missing_ok=True)


def fetch_fonts(data_path: Path, cache_dir: Path = DEFAULT_CACHE) -> FetchReport:
    """Fill the cache with every ``font_file`` the catalog at ``data_path`` names."""
    doc = json.loads(Path(data_path).read_bytes())
    report = FetchReport()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    present: set[str] = set()  # sha256 values in the cache now
    failures: dict[tuple[str, str], str] = {}  # (sha256, url) -> why it failed
    wanted = [
        f for f in doc.get("fonts", []) if f.get("preview_ok") and f.get("font_file") is not None
    ]
    with _client() as client, tempfile.TemporaryDirectory(dir=cache_dir, prefix=".zip-") as tmp:
        archives = _Archives(client, Path(tmp))
        for font in sorted(wanted, key=lambda f: f["id"]):
            font_id, font_file = font["id"], font["font_file"]
            sha256, size = font_file.get("sha256"), font_file.get("size")
            if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
                report.failed[font_id] = f"bad sha256 {sha256!r}"
                continue
            if type(size) is not int or not 1 <= size <= MAX_FONT_BYTES:
                report.failed[font_id] = f"bad size {size!r}"
                continue
            key = (sha256, str(font_file.get("url")))
            if key in failures:
                report.failed[font_id] = failures[key]
                continue
            dest = cache_path(sha256, cache_dir)
            if sha256 in present or _is_cached(dest, sha256, size):
                report.cached.append(font_id)
                present.add(sha256)
                continue
            try:
                if split_member(str(font_file.get("url"))) is not None:
                    _download_member(font_file, dest, archives)
                else:
                    _download(client, font_file, dest)
            except (FetchError, OSError, httpx.HTTPError) as exc:
                report.failed[font_id] = failures[key] = str(exc)
                continue
            report.fetched.append(font_id)
            present.add(sha256)
    return report
