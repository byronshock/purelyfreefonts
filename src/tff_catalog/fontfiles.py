"""Font-file facts read from the files themselves (milestone-1 steps 5 and 5b). Owner: agent P2b.

``read_tables`` fetches only the tables it needs with HTTP Range requests (the
table directory first), so a 1 MB font costs a few kilobytes. Results are
cached in ``$TFF_STORE/_cache/fontfacts.jsonl``, keyed by the file's sha256 and,
for google/fonts files, the git blob sha1, so later runs fetch only changed
files. Every read in a live run is also recorded in the ``font_facts``
pseudo-source, so a replay needs no network.

Contracts:

- **Reading.** ``read_tables`` reads the first ``FIRST_READ`` bytes (sfnt
  header and table directory), then the wanted tables in as few Range requests
  as possible (tables closer than ``RANGE_GAP`` share one request). It falls
  back to the whole file when the server ignores Range (a 200 answer), for
  WOFF2 (whose tables are compressed together), or when the tables would cover
  at least ``WHOLE_FILE_SHARE`` of the file anyway. TrueType, CFF, WOFF 1.0 and
  collections (the first font) are read by range.
- **Facts.** ``facts_from_bytes`` reads a whole file and hashes it (sha256 and
  git blob sha1). ``facts_for`` reads a file by range only when the reference
  already carries its sha256 (the Fontsource registry does); otherwise it
  downloads the whole file, because the sha256 is the cache key. A download
  whose sha256 differs from the reference's is an error, and so is a range read
  whose file size (``Content-Range``) differs from the reference's ``size``:
  the only check a range read allows that the file is the one the hash names.
- **Zip members.** A reference ``<archive>.zip#<member>`` (``member_url``) names
  one font inside a zip archive (Homebrew casks and Fontist formulas download
  archives). ``read_zip_member`` reads the archive's end and central directory
  by range, then only that member's bytes, so a large release archive costs a
  few requests. ``<member>`` is a path in the archive, percent-encoded; when no
  entry has that exact path, the one entry ending in ``/<member>``, else the
  one with the same file name, is taken. A member is always read whole and
  hashed, like a file without a sha256.
- **Cache.** ``FontFileCache`` is append-only JSON Lines. Rows are
  ``{"kind": "facts", <FontFacts.to_json()>}`` and ``{"kind": "url", "url",
  "sha256"}``; the url rows let references without a sha256 (commit-pinned
  google/fonts URLs) skip the download next month. The first row for a key
  wins, so an old replay reads the same facts a later run sees.
- **Pseudo-source.** A live stage records every file it used with
  ``record_reads`` as ``font_facts/<date>/<stage>.jsonl`` (``FileRead`` rows,
  failures included); ``recorded_reads`` gives a replay the same url-to-sha256
  answers, and the cache gives the facts. A row may also carry the file's
  ``size`` in bytes when the stage knows it (stage "verify" records it, because
  the file it read is the one the catalog publishes); rows written before that
  field have none, and read as ``size`` None.

fontTools is imported inside functions, so importing this module stays cheap.
"""

import hashlib
import io
import struct
import zipfile
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from tff_catalog import jsonio

if TYPE_CHECKING:
    from fontTools.ttLib import TTFont

    from tff_catalog.fetch import Fetcher, FetchResult
    from tff_catalog.records import FontFileRef
    from tff_catalog.store import Store

TABLES = ("head", "name", "cmap", "post", "OS/2", "fvar")
# 'maxp' carries the glyph count fontTools needs to decode 'cmap' without the outline tables.
FACT_TABLES = (*TABLES, "maxp")

CACHE_FILE = Path("_cache") / "fontfacts.jsonl"  # under $TFF_STORE
READS_SOURCE = "font_facts"  # store.PSEUDO_SOURCES
READS_VERSION = 1  # "collector_version" of the pseudo-source's snapshots

FIRST_READ = 16 * 1024  # the sfnt header, the table directory and usually head..OS/2
# Merge table ranges closer than this: at one request a second per host, a request
# costs far more than the bytes in between.
RANGE_GAP = 256 * 1024
WHOLE_FILE_SHARE = 0.5  # fetch the rest of the file when the ranges would cover this much
MAX_TABLES = 1024
FONT_EXTENSIONS = (".ttf", ".otf", ".woff", ".woff2", ".ttc", ".otc")

Format = Literal["ttf", "otf", "woff", "woff2"]
FORMATS = frozenset(get_args(Format))


class FontFileError(ValueError):
    """Not a font file fontTools can read, a truncated one, or one whose hash is not the expected one."""


class FontFactsMissing(LookupError, ValueError):
    """No cached facts for a file, and no network to read it (replay).

    Also a ``ValueError``, so a caller that skips unreadable files (``FontFileError``)
    skips these the same way, as the live run skipped a file it failed to read.
    """


@dataclass(frozen=True, slots=True)
class FontFacts:
    sha256: str
    git_blob: str | None
    format: Literal["ttf", "otf", "woff", "woff2"]
    cmap: tuple[tuple[int, int], ...]  # code point ranges (first, last), sorted
    family_name: str | None  # name ID 16, else 1
    full_name: str | None  # name ID 4
    postscript_name: str | None  # name ID 6
    version: str | None  # name ID 5
    license_description: str | None  # name ID 13
    license_url: str | None  # name ID 14
    is_fixed_pitch: bool | None  # post.isFixedPitch
    panose_proportion: int | None  # OS/2 panose bProportion (9 = monospaced)
    axes: tuple[tuple[str, float, float, float], ...] = ()  # (tag, min, default, max)
    # Added in step 5b for the category fallback (facts.table_category); None in
    # cache rows written before them.
    panose_family: int | None = None  # OS/2 panose bFamilyType (2 text, 3 script, 4 decorative)
    panose_serif: int | None = None  # OS/2 panose bSerifStyle (2-10 serif, 11-15 sans)
    family_class: int | None = None  # OS/2 sFamilyClass (class << 8 | subclass; 8 = sans serif)
    # Added for CC BY credit lines (L3Result.copyright); None in older cache rows.
    copyright: str | None = None  # name ID 0

    def codepoints(self) -> frozenset[int]:
        """Every code point the best Unicode cmap maps."""
        return frozenset(cp for first, last in self.cmap for cp in range(first, last + 1))

    def to_json(self) -> dict[str, Any]:
        """Every field by name; tuples become lists."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in ("cmap", "axes"):
                value = [list(item) for item in value]
            out[f.name] = value
        return out

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> FontFacts:
        """Rebuild from ``to_json`` output. Unknown fields, missing required ones and bad values raise ``ValueError``."""
        if not isinstance(d, dict):
            raise ValueError(f"FontFacts: expected an object, got {type(d).__name__}")
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(d) - known)
        if unknown:
            raise ValueError(f"FontFacts: unknown fields {unknown}")
        required = [f.name for f in fields(cls) if f.name not in _OPTIONAL]
        missing = [n for n in required if n not in d]
        if missing:
            raise ValueError(f"FontFacts: missing fields {missing}")
        kwargs = dict(d)
        try:
            kwargs["cmap"] = tuple((int(a), int(b)) for a, b in d["cmap"])
            kwargs["axes"] = tuple(
                (str(t), float(lo), float(dflt), float(hi)) for t, lo, dflt, hi in d.get("axes", ())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"FontFacts: {exc}") from exc
        if kwargs["format"] not in FORMATS:
            raise ValueError(f"FontFacts: format {kwargs['format']!r} not in {sorted(FORMATS)}")
        if not isinstance(kwargs["sha256"], str) or len(kwargs["sha256"]) != 64:
            raise ValueError(f"FontFacts: bad sha256 {kwargs['sha256']!r}")
        return cls(**kwargs)


# Fields added after the first cache rows were written: an older row reads them as the default.
_OPTIONAL = frozenset({"axes", "panose_family", "panose_serif", "family_class", "copyright"})


def cmap_ranges(codepoints: Iterable[int]) -> tuple[tuple[int, int], ...]:
    """Sorted, merged ``(first, last)`` runs of ``codepoints``."""
    runs: list[list[int]] = []
    for cp in sorted(set(codepoints)):
        if runs and cp == runs[-1][1] + 1:
            runs[-1][1] = cp
        else:
            runs.append([cp, cp])
    return tuple((a, b) for a, b in runs)


def git_blob_sha1(data: bytes) -> str:
    """The id git gives ``data`` as a blob (what the GitHub tree API lists)."""
    header = b"blob %d\0" % len(data)
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def is_font_url(url: str) -> bool:
    """Whether ``url`` names a font file by its extension (not an archive or a page)."""
    path = url.split("#", 1)[0].split("?", 1)[0].lower()
    return url.startswith("https://") and path.endswith(FONT_EXTENSIONS)


ZIP_EXTENSIONS = (".zip",)
ZIP_TAIL = 64 * 1024  # the end record, and usually the whole central directory
ZIP_MEMBER_SLACK = 1024  # a local header's name and extra field, beyond the central directory's
MAX_MEMBER = 64 * 1024 * 1024  # a member larger than this (packed or not) is not read


def member_url(archive: str, member: str) -> str:
    """The reference to ``member`` of the zip archive at ``archive`` (module docstring)."""
    from urllib.parse import quote

    return f"{archive}#{quote(member, safe='/')}"


def split_member(url: str) -> tuple[str, str] | None:
    """``(archive url, member path)`` of a zip member reference, else None."""
    from urllib.parse import unquote

    archive, sep, fragment = url.partition("#")
    if not sep or not fragment or not archive.startswith("https://"):
        return None
    member = unquote(fragment)
    path = archive.split("?", 1)[0].lower()
    if not path.endswith(ZIP_EXTENSIONS) or not member.lower().endswith(FONT_EXTENSIONS):
        return None
    return archive, member


def is_readable_url(url: str) -> bool:
    """Whether ``facts_for`` can read ``url``: a font file or a font in a zip archive."""
    return is_font_url(url) or split_member(url) is not None


# --- the cache ----------------------------------------------------------------------------------


def cache_path(store_root: Path) -> Path:
    """``$TFF_STORE/_cache/fontfacts.jsonl``."""
    return Path(store_root) / CACHE_FILE


class FontFileCache:
    """The append-only facts cache at ``$TFF_STORE/_cache/fontfacts.jsonl``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._facts: dict[str, FontFacts] = {}  # sha256 -> facts
        self._blobs: dict[str, str] = {}  # git blob sha1 -> sha256
        self._urls: dict[str, str] = {}  # url -> sha256
        self._pending: list[dict[str, Any]] = []
        if self.path.is_file():
            for number, row in enumerate(jsonio.iter_jsonl(self.path), 1):
                try:
                    self._load_row(row)
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"{self.path}:{number}: {exc}") from exc

    def _load_row(self, row: dict[str, Any]) -> None:
        match row.get("kind"):
            case "facts":
                self._remember(FontFacts.from_json({k: v for k, v in row.items() if k != "kind"}))
            case "url":
                self._urls.setdefault(str(row["url"]), str(row["sha256"]))
            case other:
                raise ValueError(f"unknown row kind {other!r}")

    def _remember(self, facts: FontFacts) -> bool:
        if facts.sha256 in self._facts:
            return False  # first row wins
        self._facts[facts.sha256] = facts
        if facts.git_blob:
            self._blobs.setdefault(facts.git_blob, facts.sha256)
        return True

    def __len__(self) -> int:
        return len(self._facts)

    def get(self, *, sha256: str | None = None, git_blob: str | None = None) -> FontFacts | None:
        """Facts by file sha256, else by git blob sha1."""
        if sha256 is not None and sha256 in self._facts:
            return self._facts[sha256]
        if git_blob is not None and git_blob in self._blobs:
            return self._facts.get(self._blobs[git_blob])
        return None

    def get_url(self, url: str) -> FontFacts | None:
        """Facts of the file first read at ``url`` (for references without a sha256)."""
        sha = self._urls.get(url)
        return None if sha is None else self._facts.get(sha)

    def put(self, facts: FontFacts) -> None:
        """Add facts (kept in memory until ``flush``); a known sha256 is ignored."""
        if self._remember(facts):
            self._pending.append({"kind": "facts", **facts.to_json()})

    def put_url(self, url: str, sha256: str) -> None:
        """Remember which file ``url`` served; the first answer for a url is kept."""
        if url not in self._urls:
            self._urls[url] = sha256
            self._pending.append({"kind": "url", "sha256": sha256, "url": url})

    @property
    def pending(self) -> int:
        """Rows not yet flushed."""
        return len(self._pending)

    def flush(self) -> None:
        """Append new entries, sorted, in one atomic write."""
        if not self._pending:
            return
        rows = sorted(self._pending, key=_row_order)
        old = self.path.read_bytes() if self.path.is_file() else b""
        if old and not old.endswith(b"\n"):
            old += b"\n"
        new = b"".join(jsonio.canonical_bytes(row) + b"\n" for row in rows)
        jsonio.atomic_write(self.path, old + new)
        self._pending.clear()


def _row_order(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["kind"], row.get("sha256", ""), row.get("url", ""))


# --- reading tables -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Entry:
    """One table directory entry; WOFF tables are zlib-compressed when stored < orig."""

    tag: str
    offset: int
    stored: int  # bytes in the file
    orig: int  # bytes once decompressed

    @property
    def end(self) -> int:
        return self.offset + self.stored


@dataclass(slots=True)
class TableRead:
    """What ``read_font`` got: the tables, and the whole file when it was downloaded."""

    format: Format
    sfnt_version: bytes  # b"\\x00\\x01\\x00\\x00", b"OTTO" or b"true"
    tables: dict[str, bytes]
    data: bytes | None  # the whole file, when it was fetched in full
    requests: int
    received: int  # body bytes received
    size: int | None = None  # the file's size, when the server said (Content-Range) or sent it all


def sniff(head: bytes) -> str:
    """The container of a font file from its first four bytes: ttf, otf, woff, woff2 or ttc."""
    magic = head[:4]
    if magic in (b"\x00\x01\x00\x00", b"true"):
        return "ttf"
    if magic == b"OTTO":
        return "otf"
    if magic == b"wOFF":
        return "woff"
    if magic == b"wOF2":
        return "woff2"
    if magic == b"ttcf":
        return "ttc"
    if magic.startswith(b"PK"):
        raise FontFileError("a zip archive, not a font file")
    raise FontFileError(f"not a font file (starts with {magic!r})")


def _content_range(result: FetchResult) -> tuple[int | None, int | None]:
    """(first byte, total size) from ``Content-Range: bytes a-b/n``; None where unknown."""
    value = result.header("content-range")
    if not value or not value.startswith("bytes "):
        return None, None
    span, _, total = value[6:].partition("/")
    first = span.split("-", 1)[0]
    return (
        int(first) if first.strip().isdigit() else None,
        int(total) if total.strip().isdigit() else None,
    )


class _Buffer:
    """Byte ranges of one remote file, fetched on demand."""

    def __init__(self, url: str, fetcher: Fetcher) -> None:
        self.url = url
        self.fetcher = fetcher
        self.segments: dict[int, bytes] = {}
        self.size: int | None = None
        self.whole: bytes | None = None
        self.requests = 0
        self.received = 0

    def fetch(self, start: int, end: int) -> None:
        """Fetch bytes ``[start, end)``; a 200 answer is the whole file."""
        result = self.fetcher.get_range(self.url, start, end - 1)
        self.requests += 1
        self.received += len(result.content)
        if result.status == 200:
            self.whole = result.content
            self.size = len(result.content)
            return
        if result.status != 206:
            raise FontFileError(f"{self.url}: HTTP {result.status} for a range request")
        first, total = _content_range(result)
        if first is not None and first != start:
            raise FontFileError(f"{self.url}: asked for byte {start}, got byte {first}")
        if total is not None:
            self.size = total
        self.segments[start] = result.content
        if start == 0 and self.size is not None and len(result.content) >= self.size:
            self.whole = result.content[: self.size]

    def covered(self, start: int, end: int) -> bytes | None:
        if self.whole is not None:
            return self.whole[start:end] if end <= len(self.whole) else None
        for at, data in self.segments.items():
            if at <= start and end <= at + len(data):
                return data[start - at : end - at]
        return None

    def read(self, start: int, end: int) -> bytes:
        """Bytes ``[start, end)``, fetched when not held yet."""
        if start < 0 or end < start or (self.size is not None and end > self.size):
            raise FontFileError(f"{self.url}: bytes {start}-{end} lie outside the file")
        if start == end:  # an empty table: no request (a Range of zero bytes is invalid)
            return b""
        data = self.covered(start, end)
        if data is None:
            self.fetch(start, end)
            data = self.covered(start, end)
        if data is None or len(data) != end - start:
            raise FontFileError(f"{self.url}: truncated at bytes {start}-{end}")
        return data

    def held(self) -> int:
        return len(self.whole) if self.whole is not None else sum(map(len, self.segments.values()))

    def complete(self) -> bytes:
        """The whole file: the rest after the bytes held from the start, in one request."""
        if self.whole is not None:
            return self.whole
        prefix = self.segments.get(0, b"")
        for at in sorted(self.segments):  # extend over contiguous segments
            segment = self.segments[at]
            if 0 < at <= len(prefix) < at + len(segment):
                prefix = prefix[:at] + segment
        if self.size is None:
            result = self.fetcher.get(self.url)
            self.requests += 1
            self.received += len(result.content)
            self.whole = result.content
        elif len(prefix) >= self.size:
            self.whole = prefix[: self.size]
        else:
            self.whole = prefix + self.read(len(prefix), self.size)
        return self.whole


class _RangeFile(io.RawIOBase):
    """A seekable, read-only view of a remote file through ``_Buffer`` (for ``zipfile``)."""

    def __init__(self, buf: _Buffer, size: int) -> None:
        self.buf, self.size, self.pos = buf, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def readinto(self, b: Any) -> int:
        end = min(self.pos + len(b), self.size)
        if end <= self.pos:
            return 0
        data = self.buf.read(self.pos, end)
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


def _zip_entry(zf: zipfile.ZipFile, member: str, url: str) -> zipfile.ZipInfo:
    """The entry ``member`` names: that path, else the one path ending in it, else the one
    file of that name (module docstring)."""
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
            raise FontFileError(f"{url}: {len(found)} archive entries match {member!r}")
    raise FontFileError(f"{url}: no archive entry {member!r}")


def read_zip_member(url: str, fetcher: Fetcher) -> bytes:
    """The bytes of the font a zip member reference names, read by range (module docstring)."""
    parts = split_member(url)
    if parts is None:
        raise ValueError(f"{url} is not a zip member reference")
    archive, member = parts
    buf = _Buffer(archive, fetcher)
    buf.fetch(0, 1)  # the archive's size (Content-Range), or all of it from a server without Range
    size = buf.size
    if size is None:
        raise FontFileError(f"{archive}: the server gave no size")
    if buf.whole is None and size > 1:
        tail = max(1, size - ZIP_TAIL)
        buf.fetch(tail, size)
    try:
        with zipfile.ZipFile(_RangeFile(buf, size)) as zf:
            info = _zip_entry(zf, member, url)
            if max(info.file_size, info.compress_size) > MAX_MEMBER:
                raise FontFileError(f"{url}: {info.file_size} bytes, over {MAX_MEMBER}")
            start = info.header_offset
            end = min(size, start + 30 + len(info.orig_filename) + info.compress_size)
            end = min(size, end + ZIP_MEMBER_SLACK)
            if buf.covered(start, end) is None:
                buf.fetch(start, end)
            return zf.read(info)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError) as exc:
        raise FontFileError(f"{url}: {exc}") from exc


def _directory(buf: _Buffer) -> tuple[str, bytes, list[_Entry]]:
    """(container, sfnt version, entries) of the file, or of the first font of a collection."""
    head = buf.read(0, 12)
    kind = sniff(head)
    if kind == "woff2":
        return kind, b"", []
    if kind == "woff":
        header = buf.read(0, 44)
        version = header[4:8]
        (count,) = struct.unpack(">H", header[12:14])
        _check_count(count)
        raw = buf.read(44, 44 + 20 * count)
        entries = [
            _Entry(tag.decode("latin-1"), offset, stored, orig)
            for tag, offset, stored, orig, _ in struct.iter_unpack(">4sLLLL", raw)
        ]
        return kind, version, entries
    base = 0
    if kind == "ttc":
        (fonts,) = struct.unpack(">L", head[8:12])
        if fonts < 1:
            raise FontFileError(f"{buf.url}: an empty font collection")
        (base,) = struct.unpack(">L", buf.read(12, 16))
        head = buf.read(base, base + 12)
        if sniff(head) not in ("ttf", "otf"):
            raise FontFileError(f"{buf.url}: the collection's first font is not an sfnt")
    version = head[:4]
    (count,) = struct.unpack(">H", head[4:6])
    _check_count(count)
    raw = buf.read(base + 12, base + 12 + 16 * count)
    entries = [
        _Entry(tag.decode("latin-1"), offset, length, length)
        for tag, _, offset, length in struct.iter_unpack(">4sLLL", raw)
    ]
    return kind, version, entries


def _check_count(count: int) -> None:
    if not 0 < count <= MAX_TABLES:
        raise FontFileError(f"implausible table count {count}")


def plan_ranges(spans: Iterable[tuple[int, int]], gap: int = RANGE_GAP) -> list[tuple[int, int]]:
    """Merge ``[start, end)`` spans whose gap is at most ``gap`` into fewer requests."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start - merged[-1][1] <= gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _format_of(kind: str, version: bytes) -> Format:
    if kind == "woff":
        return "woff"
    if kind == "woff2":
        return "woff2"
    return "otf" if version == b"OTTO" else "ttf"


def _open(data: bytes) -> TTFont:
    from fontTools.ttLib import TTFont

    number = 0 if data[:4] == b"ttcf" else -1
    return TTFont(io.BytesIO(data), lazy=True, fontNumber=number)


def _whole_read(data: bytes, tags: Sequence[str], requests: int, received: int) -> TableRead:
    kind = sniff(data)
    try:
        font = _open(data)
        reader = font.reader
        tables = {tag: bytes(reader[tag]) for tag in tags if tag in reader}
        version = font.sfntVersion.encode("latin-1")
    except FontFileError:
        raise
    except Exception as exc:  # fontTools raises many types on bad input
        raise FontFileError(f"fontTools cannot read the file: {exc}") from exc
    return TableRead(
        format=_format_of(kind, version),
        sfnt_version=version,
        tables=tables,
        data=data,
        requests=requests,
        received=received,
        size=len(data),
    )


def _decode(entry: _Entry, data: bytes) -> bytes:
    if entry.stored < entry.orig:
        try:
            data = zlib.decompress(data)
        except zlib.error as exc:
            raise FontFileError(f"table {entry.tag!r}: {exc}") from exc
    if len(data) != entry.orig:
        raise FontFileError(f"table {entry.tag!r}: {len(data)} bytes, expected {entry.orig}")
    return data


def read_font(url: str, tags: Sequence[str], fetcher: Fetcher, *, whole: bool = False) -> TableRead:
    """``read_tables`` with the details: format, request count, and the file when fetched whole.

    ``whole=True`` downloads the file in one GET (when the caller needs its hash);
    so does a url ending in ``.woff2``, whose tables cannot be read apart.
    """
    if whole or url.split("?", 1)[0].lower().endswith(".woff2"):
        result = fetcher.get(url)
        return _whole_read(result.content, tags, 1, len(result.content))
    buf = _Buffer(url, fetcher)
    buf.fetch(0, FIRST_READ)
    if buf.whole is not None:
        return _whole_read(buf.whole, tags, buf.requests, buf.received)
    kind, version, entries = _directory(buf)
    if kind == "woff2":
        return _whole_read(buf.complete(), tags, buf.requests, buf.received)
    wanted = [e for e in entries if e.tag in tags]
    for e in wanted:
        if buf.size is not None and e.end > buf.size:
            raise FontFileError(f"{url}: table {e.tag!r} lies outside the file")
    missing = [
        (e.offset, e.end) for e in wanted if e.stored and buf.covered(e.offset, e.end) is None
    ]
    plan = plan_ranges(missing)
    planned = sum(b - a for a, b in plan)
    if buf.size is not None and buf.held() + planned >= WHOLE_FILE_SHARE * buf.size:
        return _whole_read(buf.complete(), tags, buf.requests, buf.received)
    for start, end in plan:
        buf.read(start, end)
    if buf.whole is not None:  # the server answered a later range with the whole file
        return _whole_read(buf.whole, tags, buf.requests, buf.received)
    tables = {e.tag: _decode(e, buf.read(e.offset, e.end)) for e in wanted}
    return TableRead(
        format=_format_of(kind, version),
        sfnt_version=version,
        tables=tables,
        data=None,
        requests=buf.requests,
        received=buf.received,
        size=buf.size,
    )


def read_tables(url: str, tags: Sequence[str], fetcher: Fetcher) -> dict[str, bytes]:
    """Fetch the named sfnt tables of the font at ``url`` with HTTP Range requests.

    Falls back to one full GET when the server ignores Range or the file is
    WOFF2 (whose tables are compressed together). Tables the font lacks are
    left out; WOFF tables come back decompressed.
    """
    return read_font(url, tags, fetcher).tables


# --- facts --------------------------------------------------------------------------------------


def sfnt_from_tables(version: bytes, tables: Mapping[str, bytes]) -> bytes:
    """A minimal sfnt holding ``tables`` (checksums left zero; fontTools does not check them)."""
    tags = sorted(tables)
    count = len(tags)
    selector = max(count.bit_length() - 1, 0)
    search = (1 << selector) * 16
    out = [struct.pack(">4sHHHH", version, count, search, selector, count * 16 - search)]
    offset = 12 + 16 * count
    body = []
    for tag in tags:
        data = tables[tag]
        out.append(struct.pack(">4sLLL", tag.ljust(4).encode("latin-1"), 0, offset, len(data)))
        padded = data + b"\0" * (-len(data) % 4)
        body.append(padded)
        offset += len(padded)
    return b"".join(out + body)


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.replace("\x00", "").strip()
    return value or None


def _facts_from_font(font: TTFont, *, sha256: str, git_blob: str | None, fmt: Format) -> FontFacts:
    try:
        cmap = font.getBestCmap() if "cmap" in font else None
        name = font.get("name")
        post = font.get("post")
        os2 = font.get("OS/2")
        axes = (
            tuple(
                (str(a.axisTag), float(a.minValue), float(a.defaultValue), float(a.maxValue))
                for a in font["fvar"].axes
            )
            if "fvar" in font
            else ()
        )
    except Exception as exc:  # fontTools raises many types on bad input
        raise FontFileError(f"fontTools cannot decode the tables: {exc}") from exc

    def name_id(number: int) -> str | None:
        return _text(name.getDebugName(number)) if name is not None else None

    panose = getattr(os2, "panose", None)
    return FontFacts(
        sha256=sha256,
        git_blob=git_blob,
        format=fmt,
        cmap=cmap_ranges(cmap or ()),
        family_name=name_id(16) or name_id(1),
        full_name=name_id(4),
        postscript_name=name_id(6),
        version=name_id(5),
        license_description=name_id(13),
        license_url=name_id(14),
        is_fixed_pitch=None if post is None else bool(post.isFixedPitch),
        panose_proportion=None if panose is None else int(panose.bProportion),
        axes=axes,
        panose_family=None if panose is None else int(panose.bFamilyType),
        panose_serif=None if panose is None else int(panose.bSerifStyle),
        family_class=None if os2 is None else int(os2.sFamilyClass),
        copyright=name_id(0),
    )


def facts_from_bytes(data: bytes, *, git_blob: str | None = None) -> FontFacts:
    """Read facts from a whole font file with fontTools.

    The sha256 and git blob sha1 are computed from ``data``; a ``git_blob``
    given by the caller must match. A collection gives the facts of its first font.
    """
    blob = git_blob_sha1(data)
    if git_blob is not None and git_blob != blob:
        raise FontFileError(f"git blob {blob} is not the expected {git_blob}")
    kind = sniff(data)
    try:
        font = _open(data)
        version = font.sfntVersion.encode("latin-1")
    except Exception as exc:  # fontTools raises many types on bad input
        raise FontFileError(f"fontTools cannot read the file: {exc}") from exc
    return _facts_from_font(
        font, sha256=hashlib.sha256(data).hexdigest(), git_blob=blob, fmt=_format_of(kind, version)
    )


def facts_from_read(
    read: TableRead, *, sha256: str | None, git_blob: str | None = None
) -> FontFacts:
    """Facts from ``read_font``'s result; ``sha256`` is required unless the file came whole.

    A whole file must match ``git_blob`` too, when given (a range read cannot tell).
    """
    if read.data is not None:
        facts = facts_from_bytes(read.data, git_blob=git_blob)
        if sha256 is not None and facts.sha256 != sha256:
            raise FontFileError(f"sha256 {facts.sha256} is not the expected {sha256}")
        return facts
    if sha256 is None:
        raise ValueError("facts from table ranges need the file's sha256")
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(sfnt_from_tables(read.sfnt_version, read.tables)), lazy=True)
    return _facts_from_font(font, sha256=sha256, git_blob=None, fmt=read.format)


def cached_facts(ref: FontFileRef, cache: FontFileCache) -> FontFacts | None:
    """Facts for ``ref`` from the cache alone: by its sha256 or git blob sha1 when it
    has one (a commit-pinned google/fonts url changes monthly; the blob does not),
    else by url."""
    if ref.sha256 is not None or ref.git_blob is not None:
        return cache.get(sha256=ref.sha256, git_blob=ref.git_blob)
    return cache.get_url(ref.url)


def facts_for(ref: FontFileRef, fetcher: Fetcher | None, cache: FontFileCache) -> FontFacts:
    """Facts for one file: from the cache, else fetched (never in replay).

    A reference with a sha256 is read by range; one without is downloaded whole,
    because the sha256 is the cache key. New facts go into ``cache`` (call
    ``cache.flush()`` to keep them). Raises ``FontFactsMissing`` in replay,
    ``FontFileError`` for a file that is not a readable font, has the wrong
    hash, or (read by range) is not the ``size`` the reference gives, and the
    fetcher's errors for failed requests.
    """
    return facts_and_size(ref, fetcher, cache)[0]


def facts_and_size(
    ref: FontFileRef, fetcher: Fetcher | None, cache: FontFileCache
) -> tuple[FontFacts, int | None]:
    """``facts_for``, and the file's size in bytes when this call read the file (None when
    the facts came from the cache, or a range read got no size from the server)."""
    found = cached_facts(ref, cache)
    if found is not None:
        return found, None
    if fetcher is None:
        raise FontFactsMissing(f"no cached facts for {ref.url} and no network (replay)")
    if split_member(ref.url) is not None:
        data = read_zip_member(ref.url, fetcher)
        facts = facts_from_bytes(data, git_blob=ref.git_blob)
        if ref.sha256 is not None and facts.sha256 != ref.sha256:
            raise FontFileError(f"sha256 {facts.sha256} is not the expected {ref.sha256}")
        cache.put(facts)
        cache.put_url(ref.url, facts.sha256)
        return facts, len(data)
    read = read_font(ref.url, FACT_TABLES, fetcher, whole=ref.sha256 is None)
    if ref.sha256 is not None and None not in (ref.size, read.size) and ref.size != read.size:
        # Range-read facts are filed under the reference's sha256 unchecked; a
        # different size means the url now serves another file.
        raise FontFileError(f"{ref.url}: {read.size} bytes, not the expected {ref.size}")
    facts = facts_from_read(read, sha256=ref.sha256, git_blob=ref.git_blob)
    cache.put(facts)
    cache.put_url(ref.url, facts.sha256)
    return facts, len(read.data) if read.data is not None else read.size


# --- the font_facts pseudo-source ---------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class FileRead:
    """One font file a live stage used: which bytes the url gave, or why it failed."""

    url: str
    sha256: str | None  # None when the read failed
    error: str | None = None
    size: int | None = None  # the file's bytes, when the stage knows them (module docstring)

    def to_json(self) -> dict[str, Any]:
        """The row; ``size`` only when known, so rows without it keep their old form."""
        row: dict[str, Any] = {"error": self.error, "sha256": self.sha256, "url": self.url}
        if self.size is not None:
            row["size"] = self.size
        return row

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> FileRead:
        if set(d) - {"size"} != {"error", "sha256", "url"}:
            raise ValueError(
                f"FileRead: expected error, sha256, url and an optional size, got {sorted(d)}"
            )
        size = d.get("size")
        if size is not None and (type(size) is not int or size < 0):
            raise ValueError(f"FileRead: bad size {size!r}")
        return cls(url=str(d["url"]), sha256=d["sha256"], error=d["error"], size=size)


@dataclass(slots=True)
class ReadLog:
    """The reads one stage makes in a run, in the order made; ``record_reads`` saves them."""

    reads: dict[str, FileRead] = field(default_factory=dict)

    def add(self, read: FileRead) -> None:
        # A success is never replaced by a later failure of the same url, and a size
        # fills in one the same answer lacked.
        old = self.reads.get(read.url)
        if (
            old is None
            or old.sha256 is None
            or (old.size is None and read.size is not None and read.sha256 == old.sha256)
        ):
            self.reads[read.url] = read

    def rows(self) -> list[dict[str, Any]]:
        return [r.to_json() for r in sorted(self.reads.values())]


def record_reads(
    store: Store,
    day: date,
    stage: str,
    reads: Iterable[FileRead],
    *,
    frozen: frozenset[date] = frozenset(),
) -> bool:
    """Save a stage's reads as ``font_facts/<day>/<stage>.jsonl``, keeping other stages' files.

    The snapshot is rewritten (``refetch``) with the other extracts copied over,
    so latin, facts and verify can each add theirs on the same day. Returns
    False, writing nothing, when the file already holds exactly these rows.
    Raises ``store.SnapshotFrozen`` for a day a merged run used.
    """
    name = f"{stage}.jsonl"
    log = ReadLog()
    for read in reads:
        log.add(read)
    rows = log.rows()
    existing = store.snapshot(READS_SOURCE, day)
    if existing is not None and existing.has(name) and list(existing.iter_jsonl(name)) == rows:
        return False
    writer = store.writer(
        READS_SOURCE, day, READS_VERSION, refetch=existing is not None, frozen=frozen
    )
    with writer:
        if existing is not None:
            for extract in existing.manifest.extracts:
                if extract.path != name:
                    writer.copy_extract(existing, extract.path)
        writer.write_jsonl(name, rows)
        writer.note(f"font files read by stage {stage}")
    return True


def recorded_reads(store: Store, day: date, *, prefer: str | None = None) -> dict[str, FileRead]:
    """Every read recorded on ``day`` (url -> read).

    Across stages a success beats a failure; the rows of stage ``prefer`` beat
    every other stage's, so a replay repeats that stage's own answers.
    """
    snap = store.snapshot(READS_SOURCE, day)
    if snap is None:
        return {}
    log = ReadLog()
    own: list[FileRead] = []
    for extract in sorted(snap.manifest.extracts, key=lambda e: e.path):
        if extract.path.endswith(".jsonl"):
            rows = [FileRead.from_json(row) for row in snap.iter_jsonl(extract.path)]
            if extract.path == f"{prefer}.jsonl":
                own = rows
            for read in rows:
                log.add(read)
    return log.reads | {read.url: read for read in own}
