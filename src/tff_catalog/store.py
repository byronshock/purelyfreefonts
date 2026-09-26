"""The snapshot store (design-m1 §4, decision D15). Owner: agent I2.

Layout under ``$TFF_STORE`` (a clone of the private data repository)::

    <source>/<YYYY-MM-DD>/manifest.json + extracts (*.json, *.jsonl.gz, *.csv.gz)
    license_texts/<date>/  font_facts/<date>/  link_checks/<date>/   pseudo-sources for replay
    _cache/fontfacts.jsonl      keyed by file sha256 (+ git blob sha1); append-only
    _runs/<YYYY-MM-DD>.json     run manifest: code commit, config hash, snapshot per source, stale flags, sizes
    _fixtures/<collector>/      real fixtures kept private (tests marked `store`)
    _seed/                      one-off copies (old library scratchpad, probes)

Rules:
- One snapshot per (source, date). ``SnapshotWriter`` builds ``<date>.tmp/``
  and renames it atomically; a complete snapshot is never rewritten except by
  ``--refetch``, which is refused for dates in ``state/run_history.json``.
- Extracts over ``GZIP_OVER_BYTES`` are gzipped (``mtime=0``).
- ``check()`` fails over the size limits below and warns past ``REPO_WARN_BYTES``.
- Big raw downloads go in a ``RawDir`` under ``$TFF_RAW/<run_id>/``, deleted
  when the run ends unless ``--keep-raw``.
- Only ``commit()`` (the refresh workflow, or ``tff-catalog store commit``)
  pushes, and never when ``TFF_STORE_PUSH=0``.

Manifest (``manifest.json``, ``schema: 1``; ``schemas/stage/manifest.schema.json``)::

    {"schema":1,"source":"homebrew_analytics","date":"2026-10-03","collector_version":1,
     "complete":true,"data_date":"2026-10-02","window":{"start":"2025-10-03","end":"2026-10-02"},
     "fetched":[{"url":…,"status":200,"fetched_at":"2026-10-03T06:17:22Z","sha256":…,
                 "bytes":1787534,"etag":…,"last_modified":…,"kept":false}],
     "extracts":[{"path":"cask-install-365d.json","sha256":…,"bytes":141233,"rows":2964}],
     "stale_of":null,"notes":[]}

An extract's ``sha256`` and ``bytes`` describe the file as stored (for a
``.gz`` extract, the gzipped bytes), which is what git keeps and what the size
limits count. Extracts are listed sorted by path; ``fetched`` keeps request order.
"""

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import clock, jsonio

if TYPE_CHECKING:
    from tff_catalog.paths import Paths

MANIFEST_SCHEMA = 1
MANIFEST_NAME = "manifest.json"
GZIP_OVER_BYTES = 256 * 1024
EXTRACT_MAX_BYTES = 8 * 1024 * 1024
SNAPSHOT_MAX_BYTES = 10 * 1024 * 1024
RUN_MAX_BYTES = 30 * 1024 * 1024
REPO_WARN_BYTES = 1024 * 1024 * 1024
GROWTH_FLAG = 0.20  # month-on-month growth flagged in review.md
PUSH_ENV = "TFF_STORE_PUSH"
PSEUDO_SOURCES = ("license_texts", "font_facts", "link_checks")

TMP_SUFFIX = ".tmp"  # <date>.tmp/: a snapshot being written
OLD_SUFFIX = ".old"  # <date>.old/: the snapshot a --refetch replaced, deleted after the swap
# The patterns of schemas/stage/manifest.schema.json, so the store never writes a manifest
# its schema rejects (tests check the two agree).
_SOURCE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_EXTRACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"^https://\S+$")
_FETCHED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")  # every run id carries its date
_LOG = logging.getLogger("tff_catalog.store")


def stale_max_age(max_months: int) -> timedelta:
    """How old a reused snapshot may be: ``max_months`` times 31 days.

    ``max_months`` is ``ranking.toml [stale] max_months``; callers (fetch, parse,
    refresh) pass ``cfg.ranking.stale.max_months``, so the config is the one place
    the window is set.
    """
    return timedelta(days=31 * max_months)


class SnapshotExists(RuntimeError):
    """A complete snapshot already exists for this (source, date)."""


class SnapshotFrozen(RuntimeError):
    """``--refetch`` on a date that a merged run used (listed in run_history)."""


class SnapshotCorrupt(ValueError):
    """An extract on disk does not match its manifest entry, or a manifest is malformed."""


# --- manifest -------------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _iso(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def _check_keys(d: object, keys: frozenset[str], where: str) -> dict[str, Any]:
    if not isinstance(d, dict):
        raise SnapshotCorrupt(f"{where}: expected an object, got {type(d).__name__}")
    unknown, missing = sorted(set(d) - keys), sorted(keys - set(d))
    if unknown or missing:
        raise SnapshotCorrupt(f"{where}: unknown keys {unknown}, missing keys {missing}")
    return d


def _typed(value: Any, tp: type, where: str, *, optional: bool = False) -> Any:
    if value is None and optional:
        return None
    # type() rather than isinstance: true must not pass as an integer.
    if type(value) is not tp:
        raise SnapshotCorrupt(f"{where}: expected {tp.__name__}, got {value!r}")
    return value


def _date(value: object, where: str, *, optional: bool = False) -> date | None:
    if value is None and optional:
        return None
    text = _typed(value, str, where)
    if not _DATE_RE.fullmatch(text):
        raise SnapshotCorrupt(f"{where}: not a date (YYYY-MM-DD): {text!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise SnapshotCorrupt(f"{where}: {exc}") from exc


def _required_date(value: object, where: str) -> date:
    day = _date(value, where)
    if day is None:
        raise SnapshotCorrupt(f"{where}: missing date")
    return day


def _matching(value: Any, pattern: re.Pattern[str], where: str, *, empty: bool = False) -> str:
    text = _typed(value, str, where)
    if not (empty and text == "") and not pattern.fullmatch(text):
        raise SnapshotCorrupt(f"{where}: {text!r} does not match {pattern.pattern}")
    return text


def _count(value: Any, where: str, *, optional: bool = False, least: int = 0) -> Any:
    number = _typed(value, int, where, optional=optional)
    if number is not None and number < least:
        raise SnapshotCorrupt(f"{where}: {number} is below {least}")
    return number


@dataclass(frozen=True, slots=True)
class FetchRecord:
    """One entry of a manifest's ``fetched`` list."""

    url: str
    status: int
    fetched_at: str  # ISO UTC, "…Z"
    sha256: str
    bytes: int
    etag: str | None = None
    last_modified: str | None = None
    kept: bool = False  # the raw body is also an extract

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "fetched_at": self.fetched_at,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "kept": self.kept,
        }

    @classmethod
    def from_json(cls, d: object, where: str = "fetched") -> FetchRecord:
        """Parse strictly, with the schema's patterns (``sha256`` is empty on a 304)."""
        d = _check_keys(d, _FETCH_KEYS, where)
        status = _count(d["status"], f"{where}.status", least=100)
        if status > 599:
            raise SnapshotCorrupt(f"{where}.status: {status} is not an HTTP status")
        return cls(
            url=_matching(d["url"], _URL_RE, f"{where}.url"),
            status=status,
            fetched_at=_matching(d["fetched_at"], _FETCHED_AT_RE, f"{where}.fetched_at"),
            sha256=_matching(d["sha256"], _SHA256_RE, f"{where}.sha256", empty=True),
            bytes=_count(d["bytes"], f"{where}.bytes"),
            etag=_typed(d["etag"], str, f"{where}.etag", optional=True),
            last_modified=_typed(d["last_modified"], str, f"{where}.last_modified", optional=True),
            kept=_typed(d["kept"], bool, f"{where}.kept"),
        )


@dataclass(frozen=True, slots=True)
class ExtractRecord:
    """One entry of a manifest's ``extracts`` list."""

    path: str  # relative to the snapshot directory
    sha256: str
    bytes: int
    rows: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes, "rows": self.rows}

    @classmethod
    def from_json(cls, d: object, where: str = "extracts") -> ExtractRecord:
        d = _check_keys(d, _EXTRACT_KEYS, where)
        return cls(
            path=_extract_name(_typed(d["path"], str, f"{where}.path")),
            sha256=_matching(d["sha256"], _SHA256_RE, f"{where}.sha256"),
            bytes=_count(d["bytes"], f"{where}.bytes"),
            rows=_count(d["rows"], f"{where}.rows", optional=True),
        )


_FETCH_KEYS = frozenset(
    {"url", "status", "fetched_at", "sha256", "bytes", "etag", "last_modified", "kept"}
)
_EXTRACT_KEYS = frozenset({"path", "sha256", "bytes", "rows"})
_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "source",
        "date",
        "collector_version",
        "complete",
        "data_date",
        "window",
        "fetched",
        "extracts",
        "stale_of",
        "notes",
    }
)


@dataclass(frozen=True, slots=True)
class Manifest:
    schema: int
    source: str
    date: date
    collector_version: int
    complete: bool
    data_date: date | None
    window: tuple[date, date] | None  # (start, end) of the data
    fetched: tuple[FetchRecord, ...]
    extracts: tuple[ExtractRecord, ...]
    stale_of: date | None = None  # set on a stale reuse: the date it stands in for
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """The JSON object ``manifest.schema.json`` describes (every key always present)."""
        window = None
        if self.window is not None:
            window = {"start": self.window[0].isoformat(), "end": self.window[1].isoformat()}
        return {
            "schema": self.schema,
            "source": self.source,
            "date": self.date.isoformat(),
            "collector_version": self.collector_version,
            "complete": self.complete,
            "data_date": _iso(self.data_date),
            "window": window,
            "fetched": [f.to_json() for f in self.fetched],
            "extracts": [e.to_json() for e in self.extracts],
            "stale_of": _iso(self.stale_of),
            "notes": list(self.notes),
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Manifest:
        """Parse a manifest strictly: every schema key is required, and unknown keys, wrong
        types, values outside the schema's patterns and ranges, and another ``schema`` raise
        ``SnapshotCorrupt`` (a ``ValueError``)."""
        d = _check_keys(d, _MANIFEST_KEYS, "manifest")
        if _typed(d["schema"], int, "manifest.schema") != MANIFEST_SCHEMA:
            raise SnapshotCorrupt(f"manifest.schema is {d['schema']}, expected {MANIFEST_SCHEMA}")
        window = None
        if d["window"] is not None:
            w = _check_keys(d["window"], frozenset({"start", "end"}), "manifest.window")
            window = (
                _required_date(w["start"], "manifest.window.start"),
                _required_date(w["end"], "manifest.window.end"),
            )
        fetched = _typed(d["fetched"], list, "manifest.fetched")
        extracts = _typed(d["extracts"], list, "manifest.extracts")
        notes = _typed(d["notes"], list, "manifest.notes")
        return cls(
            schema=d["schema"],
            source=_matching(d["source"], _SOURCE_RE, "manifest.source"),
            date=_required_date(d["date"], "manifest.date"),
            collector_version=_count(d["collector_version"], "manifest.collector_version", least=1),
            complete=_typed(d["complete"], bool, "manifest.complete"),
            data_date=_date(d["data_date"], "manifest.data_date", optional=True),
            window=window,
            fetched=tuple(
                FetchRecord.from_json(f, f"manifest.fetched[{i}]") for i, f in enumerate(fetched)
            ),
            extracts=tuple(
                ExtractRecord.from_json(e, f"manifest.extracts[{i}]")
                for i, e in enumerate(extracts)
            ),
            stale_of=_date(d["stale_of"], "manifest.stale_of", optional=True),
            notes=tuple(_typed(n, str, f"manifest.notes[{i}]") for i, n in enumerate(notes)),
        )

    def extract(self, name: str) -> ExtractRecord | None:
        """The entry for extract ``name``, if listed."""
        return next((e for e in self.extracts if e.path == name), None)

    @property
    def extract_bytes(self) -> int:
        """Stored size of every extract together."""
        return sum(e.bytes for e in self.extracts)


def read_manifest(directory: Path) -> Manifest:
    """``directory/manifest.json`` parsed strictly; ``SnapshotCorrupt`` names the file."""
    path = Path(directory) / MANIFEST_NAME
    try:
        return Manifest.from_json(jsonio.load(path))
    except (json.JSONDecodeError, UnicodeDecodeError, SnapshotCorrupt) as exc:
        raise SnapshotCorrupt(f"{path}: {exc}") from exc


# --- snapshots ------------------------------------------------------------------------------


def _extract_name(name: str) -> str:
    """Check an extract name: relative, POSIX, no ``..``, and not the manifest itself."""
    parts = name.split("/")
    if (
        not _EXTRACT_RE.fullmatch(name)
        or name == MANIFEST_NAME
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"bad extract name {name!r}: use a relative path like 'counts.json.gz'")
    return name


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A complete snapshot on disk. Read-only."""

    source: str
    date: date
    path: Path
    manifest: Manifest

    def has(self, name: str) -> bool:
        return self.manifest.extract(name) is not None

    def extract_path(self, name: str) -> Path:
        """Path of extract ``name``; raises ``FileNotFoundError`` if the manifest lacks it."""
        if not self.has(name):
            listed = ", ".join(e.path for e in self.manifest.extracts) or "none"
            raise FileNotFoundError(
                f"{self.source} {self.date}: no extract {name!r} (listed: {listed})"
            )
        return self.path / name

    def read_bytes(self, name: str) -> bytes:
        """Extract bytes, gunzipped when the name ends in ``.gz``; sha256-checked."""
        path = self.extract_path(name)
        data = path.read_bytes()
        entry = self.manifest.extract(name)
        assert entry is not None
        if len(data) != entry.bytes or _sha256(data) != entry.sha256:
            raise SnapshotCorrupt(f"{path}: contents do not match the manifest's sha256")
        return gzip.decompress(data) if name.endswith(".gz") else data

    def load_json(self, name: str) -> Any:
        return json.loads(self.read_bytes(name))

    def iter_jsonl(self, name: str) -> Iterator[Any]:
        for number, line in enumerate(self.read_bytes(name).splitlines(), start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{self.path / name}:{number}: {exc.msg}") from exc

    @property
    def fetched_at(self) -> str | None:
        """When the newest request of this snapshot was made (ISO UTC), if it made any."""
        return max((f.fetched_at for f in self.manifest.fetched), default=None)


def _placed_manifest(directory: Path) -> Manifest:
    """The manifest of snapshot directory ``<source>/<date>/``; ``SnapshotCorrupt`` when it
    is unreadable or names another source or date."""
    manifest = read_manifest(directory)
    source, day = directory.parent.name, directory.name
    if manifest.source != source or manifest.date.isoformat() != day:
        raise SnapshotCorrupt(
            f"{directory}: manifest says {manifest.source} {manifest.date}, expected {source} {day}"
        )
    return manifest


def _complete_manifest(directory: Path) -> Manifest | None:
    """``directory``'s manifest when it holds a complete snapshot, else None.

    A corrupt manifest counts as no snapshot, with a warning: readers fall back
    to an older snapshot (the stale policy), the next fetch replaces it without
    ``--refetch``, and ``Store.check`` fails on it.
    """
    if not (directory / MANIFEST_NAME).is_file():
        return None
    try:
        manifest = _placed_manifest(directory)
    except SnapshotCorrupt as exc:
        _LOG.warning("%s", exc)
        return None
    return manifest if manifest.complete else None


def _is_complete(directory: Path) -> bool:
    return _complete_manifest(directory) is not None


def _fsync_dir(directory: Path) -> None:
    """Make a rename in ``directory`` durable (best effort: not every OS allows it)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class SnapshotWriter:
    """Builds ``<store>/<source>/<date>.tmp/`` and publishes it atomically on ``close()``.

    Open one with ``Store.writer``, which applies the one-snapshot-per-date rules.
    Every extract goes through ``write_*`` or ``copy_extract`` so the manifest
    lists it; ``close`` refuses a snapshot holding files it does not list.
    """

    def __init__(
        self,
        root: Path,
        source: str,
        day: date,
        collector_version: int,
        *,
        replace: bool = False,
    ) -> None:
        if not _SOURCE_RE.fullmatch(source):
            raise ValueError(f"bad source name {source!r} (lower case, digits and _)")
        if type(collector_version) is not int or collector_version < 1:
            raise ValueError(f"collector_version must be a positive integer: {collector_version!r}")
        self.source = source
        self.date = day
        self.collector_version = collector_version
        self.final = Path(root) / source / day.isoformat()
        self.tmp = self.final.with_name(day.isoformat() + TMP_SUFFIX)
        self._replace = replace
        self._fetched: list[FetchRecord] = []
        self._extracts: dict[str, ExtractRecord] = {}
        self._window: tuple[date, date] | None = None
        self._data_date: date | None = None
        self._notes: list[str] = []
        self._closed = False
        if self.tmp.exists():  # left by a crashed run
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def _open(self) -> None:
        if self._closed:
            raise RuntimeError(f"the writer for {self.source} {self.date} is closed")

    def write_bytes(self, name: str, data: bytes, *, rows: int | None = None) -> ExtractRecord:
        """Write an extract (gzipped if ``name`` ends in ``.gz``); returns its manifest entry."""
        self._open()
        name = _extract_name(name)
        if rows is not None and (type(rows) is not int or rows < 0):
            raise ValueError(f"{self.source}: extract {name}: rows must be a count, got {rows!r}")
        stored = gzip.compress(data, compresslevel=9, mtime=0) if name.endswith(".gz") else data
        if not name.endswith(".gz") and len(data) > GZIP_OVER_BYTES:
            _LOG.warning(
                "%s: extract %s is %d bytes; name it %s.gz", self.source, name, len(data), name
            )
        if len(stored) > EXTRACT_MAX_BYTES:
            _LOG.warning(
                "%s: extract %s is over the %d-byte limit", self.source, name, EXTRACT_MAX_BYTES
            )
        jsonio.atomic_write(self.tmp / name, stored)
        entry = ExtractRecord(path=name, sha256=_sha256(stored), bytes=len(stored), rows=rows)
        self._extracts[name] = entry
        return entry

    def write_json(self, name: str, obj: object) -> ExtractRecord:
        """Write an extract as canonical pretty JSON (``jsonio``)."""
        rows = len(obj) if isinstance(obj, list) else None
        return self.write_bytes(name, jsonio.pretty_bytes(obj), rows=rows)

    def write_jsonl(self, name: str, rows: Iterable[object]) -> ExtractRecord:
        """Write an extract as JSON Lines; ``rows`` in the manifest is the line count."""
        lines = [jsonio.canonical_bytes(row) + b"\n" for row in rows]
        return self.write_bytes(name, b"".join(lines), rows=len(lines))

    def copy_extract(self, previous: Snapshot, name: str) -> ExtractRecord:
        """Carry an unchanged extract over from an earlier snapshot (git dedupes the blob)."""
        self._open()
        name = _extract_name(name)
        entry = previous.manifest.extract(name)
        if entry is None:
            raise FileNotFoundError(f"{previous.source} {previous.date}: no extract {name!r}")
        data = (previous.path / name).read_bytes()
        if len(data) != entry.bytes or _sha256(data) != entry.sha256:
            raise SnapshotCorrupt(f"{previous.path / name}: contents do not match its manifest")
        jsonio.atomic_write(self.tmp / name, data)
        self._extracts[name] = entry
        return entry

    def record_fetch(self, record: FetchRecord) -> None:
        """Add a request to the manifest's ``fetched`` list.

        Raises ``ValueError`` at once for an entry the manifest schema rejects
        (not https, a malformed time or sha256, a status outside 100-599).
        """
        self._open()
        if not isinstance(record, FetchRecord):
            raise TypeError(f"record_fetch takes a FetchRecord, not {type(record).__name__}")
        FetchRecord.from_json(record.to_json(), f"{self.source} fetched")
        self._fetched.append(record)

    def set_window(self, start: date, end: date) -> None:
        if start > end:
            raise ValueError(f"window start {start} is after its end {end}")
        self._window = (start, end)

    def set_data_date(self, day: date) -> None:
        self._data_date = day

    def note(self, text: str) -> None:
        self._notes.append(text)

    def manifest(self, *, complete: bool = True) -> Manifest:
        """The manifest as it stands (``close`` writes this)."""
        return Manifest(
            schema=MANIFEST_SCHEMA,
            source=self.source,
            date=self.date,
            collector_version=self.collector_version,
            complete=complete,
            data_date=self._data_date,
            window=self._window,
            fetched=tuple(self._fetched),
            extracts=tuple(self._extracts[k] for k in sorted(self._extracts)),
            notes=tuple(self._notes),
        )

    def close(self, *, complete: bool = True) -> Snapshot:
        """Write the manifest and rename ``<date>.tmp`` to ``<date>``.

        ``complete=False`` keeps a partial snapshot for inspection: readers skip it,
        and the next writer for the date replaces it without ``--refetch``.
        """
        self._open()
        try:
            self._check_files()
            manifest = self.manifest(complete=complete)
            data = manifest.to_json()
            Manifest.from_json(data)  # never publish a manifest the readers would refuse
            jsonio.dump(data, self.tmp / MANIFEST_NAME)
            self._publish()
        except BaseException:
            self.abort()
            raise
        self._closed = True
        return Snapshot(source=self.source, date=self.date, path=self.final, manifest=manifest)

    def _check_files(self) -> None:
        on_disk = {p.relative_to(self.tmp).as_posix() for p in self.tmp.rglob("*") if p.is_file()}
        stray = sorted(on_disk - set(self._extracts))
        if stray:
            raise RuntimeError(f"{self.source} {self.date}: files not written as extracts: {stray}")

    def _publish(self) -> None:
        old = self.final.with_name(self.date.isoformat() + OLD_SUFFIX)
        if old.exists():
            shutil.rmtree(old)
        if self.final.exists():
            if not self._replace and _is_complete(self.final):
                raise SnapshotExists(f"{self.final} appeared while this snapshot was written")
            self.final.rename(old)
        self.tmp.rename(self.final)
        _fsync_dir(self.final.parent)
        if old.exists():
            shutil.rmtree(old)

    def abort(self) -> None:
        """Delete the temporary directory."""
        self._closed = True
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def __enter__(self) -> SnapshotWriter:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, *rest: object) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


@dataclass(frozen=True, slots=True)
class RawDir:
    """A run's scratch directory for big raw files (clones, full dumps)."""

    path: Path
    keep: bool = False  # --keep-raw

    def file(self, name: str) -> Path:
        """Return ``path/name``, creating parent directories."""
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or ".." in parts:
            raise ValueError(f"bad raw file name {name!r}: use a relative path")
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def cleanup(self) -> None:
        """Delete the directory unless ``keep``."""
        if not self.keep and self.path.exists():
            shutil.rmtree(self.path)


@dataclass(frozen=True, slots=True)
class StoreReport:
    """Result of ``Store.check()``: failures block, warnings go in review.md."""

    failures: tuple[str, ...]
    warnings: tuple[str, ...]
    sizes: tuple[tuple[str, int], ...]  # (source, bytes) of the newest snapshots

    @property
    def ok(self) -> bool:
        return not self.failures


def _parse_day(name: str) -> date | None:
    if not _DATE_RE.fullmatch(name):
        return None
    try:
        return date.fromisoformat(name)
    except ValueError:
        return None


def _tree_bytes(root: Path) -> int:
    total = 0
    for directory, _, files in root.walk():
        for name in files:
            path = directory / name
            if not path.is_symlink():
                total += path.stat().st_size
    return total


def _newest_mtime(root: Path) -> float:
    newest = root.lstat().st_mtime
    for directory, dirs, files in root.walk():
        for name in (*dirs, *files):
            newest = max(newest, (directory / name).lstat().st_mtime)
    return newest


@dataclass(frozen=True, slots=True)
class _Found:
    """A snapshot directory as ``check`` sees it."""

    source: str
    date: date
    path: Path
    manifest: Manifest | None  # None when missing or unreadable


@dataclass(frozen=True, slots=True)
class Store:
    root: Path

    @classmethod
    def from_paths(cls, paths: Paths) -> Store:
        """The store at ``paths.require_store()``."""
        return cls(paths.require_store())

    def sources(self) -> list[str]:
        """Source directories, sorted (names starting with ``_`` excluded)."""
        if not self.root.is_dir():
            return []
        return sorted(
            p.name
            for p in self.root.iterdir()
            if p.is_dir() and not p.is_symlink() and _SOURCE_RE.fullmatch(p.name)
        )

    def dates(self, source: str, *, complete_only: bool = True) -> list[date]:
        """Snapshot dates of ``source``, oldest first.

        With ``complete_only`` (the default), only snapshots whose manifest says
        complete; otherwise every ``<date>/`` directory, finished or not.
        """
        directory = self.root / source
        if not directory.is_dir():
            return []
        out = []
        for path in directory.iterdir():
            day = _parse_day(path.name)
            if day is None or not path.is_dir():
                continue
            if not complete_only or _is_complete(path):
                out.append(day)
        return sorted(out)

    def snapshot(self, source: str, day: date) -> Snapshot | None:
        """The complete snapshot for exactly ``day``, if any.

        A snapshot whose manifest is unreadable, or names another source or date,
        counts as missing (logged), so one damaged directory never stops a run;
        ``check`` fails on it.
        """
        directory = self.root / source / day.isoformat()
        manifest = _complete_manifest(directory)
        if manifest is None:
            return None
        return Snapshot(source=source, date=day, path=directory, manifest=manifest)

    def latest(
        self, source: str, on_or_before: date, *, max_age: timedelta | None = None
    ) -> Snapshot | None:
        """The newest complete snapshot not after ``on_or_before`` (and not older than ``max_age``).

        The stale policy passes ``max_age=stale_max_age(cfg.ranking.stale.max_months)``.
        Both ends are inclusive: a snapshot exactly ``max_age`` old is still used.
        """
        oldest = None if max_age is None else on_or_before - max_age
        for day in reversed(self.dates(source)):
            if day > on_or_before:
                continue
            if oldest is not None and day < oldest:
                return None
            return self.snapshot(source, day)
        return None

    def writer(
        self,
        source: str,
        day: date,
        collector_version: int,
        *,
        refetch: bool = False,
        frozen: frozenset[date] = frozenset(),
    ) -> SnapshotWriter:
        """Open a writer; raises ``SnapshotExists`` or ``SnapshotFrozen`` as the rules say.

        - A complete snapshot for ``day`` exists: ``SnapshotExists`` (fetch treats
          it as done), unless ``refetch``, which replaces it on ``close``.
        - ``day`` is in ``frozen`` (the dates merged runs used, ``State.frozen_dates``):
          ``SnapshotFrozen`` for ``refetch``, and also when the date has no complete
          snapshot, because adding one would change what a replay of that run reads.
        """
        existing = self.root / source / day.isoformat()
        complete = existing.is_dir() and _is_complete(existing)
        if day in frozen and (refetch or not complete):
            raise SnapshotFrozen(
                f"{source} {day}: a merged run used this date (state/run_history.json), "
                "so its snapshot is immutable"
            )
        if complete and not refetch:
            raise SnapshotExists(f"{source} {day}: complete snapshot exists (use --refetch)")
        return SnapshotWriter(self.root, source, day, collector_version, replace=refetch)

    def raw_dir(self, paths: Paths, run_id: str, *, keep: bool = False) -> RawDir:
        """The run's ``RawDir`` under ``$TFF_RAW/<run_id>/`` (created).

        ``run_id`` must hold the run's date (``fetch-2026-10-03``), which ``gc``
        uses to tell run directories from anything else.
        """
        if not run_id or "/" in run_id or run_id in (".", "..") or not _RUN_DIR_RE.search(run_id):
            raise ValueError(f"bad run id {run_id!r}: use a name holding the run date")
        raw = RawDir(paths.raw_dir(run_id), keep=keep)
        raw.path.mkdir(parents=True, exist_ok=True)
        return raw

    def write_run(self, day: date, run: dict[str, Any]) -> Path:
        """Write ``_runs/<day>.json``."""
        path = self.root / "_runs" / f"{day.isoformat()}.json"
        jsonio.dump(run, path)
        return path

    # --- check --------------------------------------------------------------------------

    def _found(self) -> Iterator[_Found]:
        for source in self.sources():
            for day in self.dates(source, complete_only=False):
                path = self.root / source / day.isoformat()
                try:
                    manifest = read_manifest(path) if (path / MANIFEST_NAME).is_file() else None
                except SnapshotCorrupt:
                    manifest = None
                yield _Found(source, day, path, manifest)

    def _leftovers(self) -> list[Path]:
        """``<date>.tmp`` and ``<date>.old`` directories a crashed run left behind."""
        out = []
        for source in self.sources():
            for path in (self.root / source).iterdir():
                stem = path.name.removesuffix(TMP_SUFFIX).removesuffix(OLD_SUFFIX)
                if path.is_dir() and stem != path.name and _parse_day(stem) is not None:
                    out.append(path)
        return sorted(out)

    def check(self, *, run_date: date | None = None) -> StoreReport:
        """Size limits per extract, snapshot and run; repository size; growth.

        Failures: an extract over ``EXTRACT_MAX_BYTES``, a snapshot over
        ``SNAPSHOT_MAX_BYTES``, more than ``RUN_MAX_BYTES`` of new extract bytes
        in the run (extracts whose sha256 an earlier snapshot already holds cost
        nothing, as git stores them once), and any snapshot whose files do not
        match its manifest. Warnings: a repository over ``REPO_WARN_BYTES``, a
        snapshot more than ``GROWTH_FLAG`` bigger than the source's previous one,
        incomplete snapshots, leftovers and big uncompressed extracts.
        ``run_date`` defaults to the newest snapshot date in the store.
        """
        failures: list[str] = []
        warnings: list[str] = []
        found = list(self._found())
        for snap in found:
            failures += _integrity(snap)
            warnings += _snapshot_warnings(snap)
        for path in self._leftovers():
            warnings.append(f"{path.relative_to(self.root)}: left by an interrupted write")
        good = [s for s in found if s.manifest is not None and s.manifest.complete]
        if run_date is None and good:
            run_date = max(s.date for s in good)
        sizes: list[tuple[str, int]] = []
        if run_date is not None:
            failures += _run_size(good, run_date)
            for source in sorted({s.source for s in good}):
                history = [s for s in good if s.source == source and s.date <= run_date]
                if not history:
                    continue
                sizes.append((source, _stored(history[-1])))
                if len(history) >= 2 and history[-1].date == run_date:
                    warnings += _growth(history[-2], history[-1])
        repo = _tree_bytes(self.root) if self.root.is_dir() else 0
        if repo > REPO_WARN_BYTES:
            warnings.append(f"the store is {repo} bytes, over the {REPO_WARN_BYTES}-byte warning")
        return StoreReport(tuple(failures), tuple(warnings), tuple(sizes))

    # --- maintenance --------------------------------------------------------------------

    def gc(self, raw_root: Path, older_than: timedelta) -> list[Path]:
        """Delete raw run directories older than ``older_than``; return what was removed.

        A run directory is a child of ``raw_root`` whose name holds its run's date
        (``fetch-2026-10-03``, ``2026-10-03``): every run id does, and anything
        else under a mistyped ``TFF_RAW`` is left alone. A directory's age is that
        of its newest file. Leftover ``<date>.tmp`` and ``<date>.old`` directories
        in the store older than ``older_than`` go too.
        """
        raw_root = Path(raw_root).expanduser()
        resolved = raw_root.resolve()
        if resolved in (Path("/"), Path.home().resolve()) or self.root.resolve().is_relative_to(
            resolved
        ):
            raise ValueError(f"refusing to garbage-collect {raw_root}: not a raw download root")
        cutoff = clock.utc_now().timestamp() - older_than.total_seconds()
        candidates = []
        if raw_root.is_dir():
            candidates += sorted(
                p
                for p in raw_root.iterdir()
                if p.is_dir() and not p.is_symlink() and _RUN_DIR_RE.search(p.name)
            )
        candidates += self._leftovers()
        removed = []
        for path in candidates:
            if _newest_mtime(path) < cutoff:
                shutil.rmtree(path)
                removed.append(path)
        return removed

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def commit(self, message: str) -> str | None:
        """``git add -A``, commit, ``pull --rebase`` and push (unless TFF_STORE_PUSH=0).

        Returns the new commit sha, or None when nothing changed. Refuses while a
        ``<date>.tmp`` or ``<date>.old`` directory exists, so a half-written
        snapshot never reaches the data repository.
        """
        leftovers = self._leftovers()
        if leftovers:
            names = [p.relative_to(self.root).as_posix() for p in leftovers]
            raise RuntimeError(f"not committing: interrupted writes left {names}")
        if not self._git("status", "--porcelain").strip():
            return None
        self._git("add", "-A")
        self._git("commit", "--quiet", "-m", message)
        if os.environ.get(PUSH_ENV, "1").strip() != "0":
            self._git("pull", "--rebase", "--quiet")
            self._git("push", "--quiet")
        return self._git("rev-parse", "HEAD").strip()


def _stored(snap: _Found) -> int:
    assert snap.manifest is not None
    return snap.manifest.extract_bytes


def _integrity(snap: _Found) -> list[str]:
    where = f"{snap.source}/{snap.date}"
    if snap.manifest is None:
        manifest_path = snap.path / MANIFEST_NAME
        if manifest_path.is_file():
            try:
                read_manifest(snap.path)
            except SnapshotCorrupt as exc:
                return [f"{where}: unreadable manifest: {exc}"]
        return [f"{where}: no manifest"]
    m = snap.manifest
    out = []
    if m.source != snap.source or m.date != snap.date:
        out.append(f"{where}: manifest says {m.source}/{m.date}")
    total = 0
    for e in m.extracts:
        path = snap.path / e.path
        if not path.is_file():
            out.append(f"{where}: extract {e.path} is missing")
            continue
        data = path.read_bytes()
        total += len(data)
        if len(data) != e.bytes or _sha256(data) != e.sha256:
            out.append(f"{where}: extract {e.path} does not match its sha256")
        if len(data) > EXTRACT_MAX_BYTES:
            out.append(
                f"{where}: extract {e.path} is {len(data)} bytes (limit {EXTRACT_MAX_BYTES})"
            )
    if total > SNAPSHOT_MAX_BYTES:
        out.append(f"{where}: snapshot is {total} bytes (limit {SNAPSHOT_MAX_BYTES})")
    listed = {e.path for e in m.extracts} | {MANIFEST_NAME}
    on_disk = {p.relative_to(snap.path).as_posix() for p in snap.path.rglob("*") if p.is_file()}
    stray = sorted(on_disk - listed)
    if stray:
        out.append(f"{where}: files the manifest does not list: {stray}")
    return out


def _snapshot_warnings(snap: _Found) -> list[str]:
    if snap.manifest is None:
        return []
    where = f"{snap.source}/{snap.date}"
    out = [
        f"{where}: extract {e.path} is {e.bytes} bytes uncompressed; gzip it"
        for e in snap.manifest.extracts
        if not e.path.endswith(".gz") and e.bytes > GZIP_OVER_BYTES
    ]
    if not snap.manifest.complete:
        out.insert(0, f"{where}: incomplete snapshot (a later fetch replaces it)")
    return out


def _run_size(good: list[_Found], run_date: date) -> list[str]:
    seen: set[str] = set()
    for snap in good:
        if snap.date < run_date:
            assert snap.manifest is not None
            seen.update(e.sha256 for e in snap.manifest.extracts)
    added = 0
    for snap in good:
        if snap.date != run_date:
            continue
        assert snap.manifest is not None
        for e in snap.manifest.extracts:
            if e.sha256 not in seen:
                seen.add(e.sha256)
                added += e.bytes
    if added > RUN_MAX_BYTES:
        return [f"run {run_date}: {added} new extract bytes (limit {RUN_MAX_BYTES})"]
    return []


def _growth(previous: _Found, latest: _Found) -> list[str]:
    before, after = _stored(previous), _stored(latest)
    if before > 0 and after > before * (1 + GROWTH_FLAG):
        grew = round(100 * (after - before) / before)
        return [
            f"{latest.source}: snapshot grew {grew}% since {previous.date} ({before} -> {after} bytes)"
        ]
    return []


# --- tff-catalog store {ls,check,commit,gc} ------------------------------------


def cmd_ls(paths: Paths, source: str | None = None) -> int:
    """List sources, or one source's snapshot dates with sizes and completeness."""
    store = Store.from_paths(paths)
    if source is None:
        for name in store.sources():
            days = store.dates(name)
            newest = days[-1].isoformat() if days else "-"
            print(f"{name:<24} {len(days):>4} snapshots  newest {newest}")
        return 0
    if source not in store.sources():
        print(f"no source {source!r} in {store.root}", file=sys.stderr)
        return 1
    for day in store.dates(source, complete_only=False):
        path = store.root / source / day.isoformat()
        try:
            m = read_manifest(path)
        except (FileNotFoundError, SnapshotCorrupt) as exc:
            print(f"{day}  unreadable  {exc}")
            continue
        state = "complete" if m.complete else "incomplete"
        extra = f"  stale_of {m.stale_of}" if m.stale_of else ""
        print(f"{day}  {state:<10} {m.extract_bytes:>10} bytes  {len(m.extracts)} extracts{extra}")
    return 0


def cmd_check(paths: Paths) -> int:
    """Run ``Store.check``; non-zero exit on any failure."""
    report = Store.from_paths(paths).check()
    for source, size in report.sizes:
        print(f"{source:<24} {size:>10} bytes")
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for failure in report.failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    print(f"store check: {len(report.failures)} failures, {len(report.warnings)} warnings")
    return 0 if report.ok else 1


def cmd_commit(paths: Paths, message: str | None = None) -> int:
    from tff_catalog.refresh import code_changes

    if code_changes(paths.root) is not None:
        print(
            "warning: the working tree has uncommitted changes to the code, config or data; "
            "the run manifests being committed name a commit that does not reproduce them "
            "(their code_dirty is true)",
            file=sys.stderr,
        )
    store = Store.from_paths(paths)
    sha = store.commit(message or f"snapshots {clock.utc_today().isoformat()}")
    print(sha or "nothing to commit")
    return 0


def cmd_gc(paths: Paths, raw_older_than: timedelta) -> int:
    removed = Store.from_paths(paths).gc(paths.raw_root, raw_older_than)
    for path in removed:
        print(f"removed {path}")
    print(f"store gc: removed {len(removed)} directories")
    return 0
