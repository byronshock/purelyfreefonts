"""Ranking collector "debian": popcon installs and font dependencies (design-m1 §2.4).

One collector reads both Debian files (design gap C6: popcon's ``main/fonts``
list is a subset of ``by_inst``, and fonts filed outside Section fonts are
missing from it). Group ``debian``; engine source ``[sources.debian]`` reads
series ``inst``.

**Sources.**

- ``deb.debian.org/debian/dists/<suite>/<component>/binary-<architecture>/Packages.xz``
  (trixie, main, amd64: 9.7 MB, 68,825 stanzas on 2026-09-25). The amd64 index
  already holds every ``Architecture: all`` package, so binary-all is never
  fetched: it would count them twice. The file changes only at point releases,
  so it is a conditional GET.
- ``popcon.debian.org/by_inst.gz`` (3.4 MB, 224,225 rows): installs reported by
  every popularity-contest submitter (all architectures; Ubuntu and Devuan
  clients too), regenerated daily around 14:00 UTC. Columns ``rank name inst
  vote old recent no-files (maintainer)``; the file ends with a ``-----`` line
  and a ``Total`` row. Only ``inst`` is used (fonts are not tracked by access
  time, so ``vote`` and ``old`` mean little).
- the first ``RESULTS_HEAD`` bytes of ``popcon.debian.org/all-popcon-results.gz``
  (a Range request), whose first line ``Submissions: N`` is the report's total.
  When it is unreadable the fetch goes on without it (a manifest note).

All three are open data (ruling T1): the popcon site's material is MIT (Expat) or
GPL-2.0-or-later (www.debian.org/license); Packages files carry no terms of
their own. Maintainer names and addresses are never kept.

**Selection.** A stanza is *font-like* when its Section (without an area
prefix: ``contrib/fonts`` is ``fonts``) is in ``Settings.sections`` or its
name matches ``Settings.name_pattern``. The font
names are the font-like packages, the virtual packages they provide, and any
name matching the pattern (a removed ``ttf-*`` still named as an alternative).
A stanza is kept when it is font-like (role ``font``) or when one of its
Depends, Pre-Depends, Recommends or Suggests groups names a font name (role
``dependent``: gnome-core, desktop-base, task-desktop). Only those groups
are kept, whole, as package names without version constraints or architecture
qualifiers. A package listed twice keeps its highest version (dpkg order).
Popcon rows are kept for every kept package and for any name matching the
pattern (fonts only in testing or sid, and removed ones marked ``(Not in sid)``).

**Extracts** (every key always present; rows sorted by name):

- ``packages.jsonl.gz``: per kept stanza ``{package, version, source, section,
  architecture, description, homepage, provides, depends, pre_depends,
  recommends, suggests, role}``; ``source`` is the source package name (the
  binary's own name when the stanza has none), ``description`` the one-line
  summary, each relation field a list of alternative groups.
- ``popcon.jsonl.gz``: per kept row ``{name, rank, inst, vote, old, recent,
  no_files, in_sid}``.
- ``meta.json``: the selection (``selection_revision``, suite, component,
  architecture, sections, name_pattern), ``packages`` {url, last_modified,
  listed, fonts, dependents} and ``popcon`` {url, last_modified, date, rows,
  submissions}. ``popcon.date`` is the UTC day of by_inst's Last-Modified
  (else the fetch day), and the manifest's ``data_date``.

When Packages.xz answers 304 and the previous snapshot used the same
selection (settings and ``SELECTION_REVISION``, so a change to the selection
code is never masked by an unchanged file), its ``packages.jsonl.gz`` is
copied. The raw files are opened by their magic bytes, not their names, in
case the compression was undone on the wire. Either extract shrinking below
``min_share`` of the previous snapshot's rows fails the fetch (a broken file),
and the stale policy reuses the last good snapshot.

**Parse** (offline, pure; reads only the snapshot):

- an ``Observation`` per popcon row: series ``inst``, unit ``installs``, key
  ``deb-pkg:<name>``, ``start`` = ``end`` = the report day. Attrs: ``role``
  (``font`` or ``dependent``), ``packaged`` (in the Packages extract),
  ``in_sid``, ``submissions`` when known, and for packaged rows ``section`` and
  ``deb_source``. A packaged font-like package with no popcon row gets value
  None (inside the frame, no usable value);
- a ``Relation`` per alternative of each kept group: subject the stanza's
  package, object the alternative, ``alt`` its position in the group; kind
  ``depends`` (Depends, Pre-Depends), ``recommends`` or ``optdepends``
  (Suggests, never installed automatically). Attrs ``field`` (the control
  field) and, for a group of two or more, ``group`` (``a | b | c``);
- a ``Relation`` ``provides`` from each font-like package to each virtual
  package it provides.
"""

import gzip
import lzma
import re
import zlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, ClassVar, TextIO

from tff_catalog import jsonio
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchError, FetchResult, previous_record
from tff_catalog.records import Observation, Record, Relation, Scalar, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "debian"
DEB_HOST = "deb.debian.org"
POPCON_HOST = "popcon.debian.org"
NAMESPACE = "deb-pkg"
SERIES = "inst"  # config/ranking.toml [sources.debian] series
PACKAGES = "packages.jsonl.gz"
POPCON = "popcon.jsonl.gz"
META = "meta.json"
META_SCHEMA = 1
POPCON_URL = f"https://{POPCON_HOST}/by_inst.gz"
RESULTS_URL = f"https://{POPCON_HOST}/all-popcon-results.gz"
RESULTS_HEAD = 4096  # bytes of all-popcon-results.gz; its first line is "Submissions: N"
RAW_PACKAGES = "Packages.xz"
RAW_POPCON = "by_inst.gz"
NOT_IN_SID = "(Not in sid)"  # by_inst's maintainer column for a package removed from sid
FONT, DEPENDENT = "font", "dependent"  # extract and attrs ``role``
# Bump when select_packages keeps other rows or fields: a 304 must not reuse an older selection.
SELECTION_REVISION = 1
XZ_MAGIC = b"\xfd7zXZ\x00"
GZIP_MAGIC = b"\x1f\x8b"

# Control field -> (extract key, Relation kind). Pre-Depends is a stronger Depends.
FIELDS: tuple[tuple[str, str, str], ...] = (
    ("Depends", "depends", "depends"),
    ("Pre-Depends", "pre_depends", "depends"),
    ("Recommends", "recommends", "recommends"),
    ("Suggests", "suggests", "optdepends"),
)
POPCON_COLUMNS = ("#rank", "name", "inst", "vote", "old", "recent", "no-files")

# Defaults of the Settings, which config/sources/debian.toml spells out.
SUITE = "trixie"  # Debian 13, stable since 2025-08
COMPONENT = "main"  # the DFSG-free archive; contrib and non-free hold no catalog fonts
ARCHITECTURE = "amd64"  # 97% of popcon submitters; its index includes Architecture: all
SECTIONS = ("fonts",)
NAME_PATTERN = r"^(?:fonts|xfonts|ttf|otf|t1)-"  # Debian's font package prefixes, old and new
MIN_SHARE = 0.9

_WORD = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
_PACKAGE = re.compile(r"\s*([a-z0-9][a-z0-9.+-]*)")
_POPCON_ROW = re.compile(r"^(\d+)\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(.*?))?\s*$")
_SUBMISSIONS = re.compile(r"^Submissions:\s*(\d+)\s*$")
_KEPT_FIELDS = frozenset(
    {"Package", "Version", "Source", "Section", "Architecture", "Description", "Homepage"}
    | {"Provides"}
    | {field for field, _, _ in FIELDS}
)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/debian.toml``."""

    suite: str = SUITE
    component: str = COMPONENT
    architecture: str = ARCHITECTURE
    sections: tuple[str, ...] = SECTIONS
    name_pattern: str = NAME_PATTERN
    min_share: float = MIN_SHARE  # of the previous snapshot's rows, or the fetch fails

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        for key in ("suite", "component", "architecture"):
            if not _WORD.fullmatch(getattr(self, key)):
                raise ConfigError(f"{where}.{key}: expected a lower-case archive name")
        if self.architecture == "all":
            raise ConfigError(
                f"{where}.architecture: binary-all is inside every architecture's index; "
                "name a real architecture (amd64)"
            )
        if not self.sections or not all(_WORD.fullmatch(s) for s in self.sections):
            raise ConfigError(f"{where}.sections: expected lower-case section names")
        try:
            re.compile(self.name_pattern)
        except re.error as exc:
            raise ConfigError(f"{where}.name_pattern: {exc}") from exc
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")

    @property
    def pattern(self) -> re.Pattern[str]:
        return re.compile(self.name_pattern)

    @property
    def packages_url(self) -> str:
        return (
            f"https://{DEB_HOST}/debian/dists/{self.suite}/{self.component}/"
            f"binary-{self.architecture}/Packages.xz"
        )

    def selection(self) -> dict[str, Any]:
        """What the Packages extract depends on (``meta.json``); a change forbids reusing it."""
        return {
            "selection_revision": SELECTION_REVISION,
            "suite": self.suite,
            "component": self.component,
            "architecture": self.architecture,
            "sections": sorted(self.sections),
            "name_pattern": self.name_pattern,
        }


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- dpkg versions ---------------------------------------------------------------------------


def _order(c: str) -> int:
    """dpkg's sort weight of one non-digit character: ``~`` first, then letters, then others."""
    if c == "~":
        return -1
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _compare_part(a: str, b: str) -> int:
    """dpkg's ``verrevcmp`` of an upstream version or revision: <0, 0 or >0."""
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) and not a[i].isdigit() else 0
            bc = _order(b[j]) if j < len(b) and not b[j].isdigit() else 0
            if ac != bc:
                return ac - bc
            i, j = i + 1, j + 1
        start_a, start_b = i, j
        while i < len(a) and a[i].isdigit():
            i += 1
        while j < len(b) and b[j].isdigit():
            j += 1
        na, nb = int(a[start_a:i] or 0), int(b[start_b:j] or 0)
        if na != nb:
            return -1 if na < nb else 1
    return 0


def _split_version(v: str) -> tuple[int, str, str]:
    epoch, sep, rest = v.partition(":")
    if not sep:
        epoch, rest = "0", v
    upstream, dash, revision = rest.rpartition("-")
    if not dash:
        upstream, revision = rest, ""
    return (int(epoch) if epoch.isdigit() else 0, upstream, revision)


def compare_versions(a: str, b: str) -> int:
    """Compare two Debian versions as ``dpkg --compare-versions`` does: <0, 0 or >0."""
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _compare_part(ua, ub) or _compare_part(ra, rb)


# --- Packages ----------------------------------------------------------------------------------


def iter_stanzas(lines: Iterable[str]) -> Iterator[dict[str, str]]:
    """The stanzas of a Packages file as ``{field: value}``, continuation lines joined by newlines.

    Raises ``ValueError`` on a line that is neither a field, a continuation nor blank.
    """
    fields: dict[str, str] = {}
    last: str | None = None
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        if not line.strip():
            if fields:
                yield fields
            fields, last = {}, None
        elif line[0] in " \t":
            if last is None:
                raise ValueError(f"Packages line {number}: continuation outside a field")
            fields[last] += "\n" + line.strip()
        else:
            name, sep, value = line.partition(":")
            if not sep or not name.strip():
                raise ValueError(f"Packages line {number}: not a field: {line[:80]!r}")
            last = name.strip()
            fields[last] = value.strip()
    if fields:
        yield fields


def relation_groups(value: str) -> list[list[str]]:
    """A relation field as alternative groups of package names, in order.

    ``"a (>= 1) | b:any, c"`` gives ``[["a", "b"], ["c"]]``: version constraints,
    architecture qualifiers and restrictions are dropped, a name repeated in a
    group is kept once, and alternatives that are not package names are skipped.
    """
    groups = []
    for text in value.replace("\n", " ").split(","):
        names: list[str] = []
        for alt in text.split("|"):
            m = _PACKAGE.match(alt)
            if m is not None and m.group(1) not in names:
                names.append(m.group(1))
        if names:
            groups.append(names)
    return groups


def _names(value: str) -> list[str]:
    """Package names of a Provides or Source field (version in parentheses dropped)."""
    return [g[0] for g in relation_groups(value)]


def _slim(stanza: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in stanza.items() if k in _KEPT_FIELDS}


def dedupe(stanzas: Iterable[Mapping[str, str]]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """One stanza per package: the highest version (dpkg order), ties broken by content.

    Returns ``({package: stanza}, the names listed more than once)``; stanzas
    without a Package field are skipped. Only the fields the extract uses are kept.
    """
    out: dict[str, dict[str, str]] = {}
    repeated: set[str] = set()
    for stanza in stanzas:
        name = stanza.get("Package", "").strip()
        if not name:
            continue
        slim = _slim(stanza)
        held = out.get(name)
        if held is None:
            out[name] = slim
            continue
        repeated.add(name)
        order = compare_versions(slim.get("Version", ""), held.get("Version", ""))
        if order > 0 or (order == 0 and jsonio.canonical_str(slim) > jsonio.canonical_str(held)):
            out[name] = slim
    return out, sorted(repeated)


def section_name(stanza: Mapping[str, str]) -> str:
    """The stanza's Section without its archive-area prefix (``contrib/fonts`` is ``fonts``)."""
    return stanza.get("Section", "").strip().rpartition("/")[2]


def _font_like(stanza: Mapping[str, str], settings: Settings) -> bool:
    return section_name(stanza) in settings.sections or bool(
        settings.pattern.search(stanza["Package"])
    )


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    return text.split("\n", 1)[0].strip() or None


def package_row(
    stanza: Mapping[str, str], groups: Mapping[str, list[list[str]]], role: str
) -> dict:
    """The extract row of one kept stanza (module docstring)."""
    name = stanza["Package"]
    source = _names(stanza.get("Source", ""))
    return {
        "package": name,
        "version": stanza.get("Version") or None,
        "source": source[0] if source else name,
        "section": stanza.get("Section") or None,
        "architecture": stanza.get("Architecture") or None,
        "description": _first_line(stanza.get("Description")),
        "homepage": stanza.get("Homepage") or None,
        "provides": sorted(set(_names(stanza.get("Provides", "")))),
        **{key: groups.get(key, []) for _, key, _ in FIELDS},
        "role": role,
    }


@dataclass(frozen=True, slots=True)
class Selection:
    """The kept Packages rows, and counts for ``meta.json`` and the log."""

    rows: list[dict]
    listed: int | None  # distinct packages in the file (None: not known)
    repeated: list[str]  # kept packages listed more than once

    @property
    def fonts(self) -> int:
        return sum(r["role"] == FONT for r in self.rows)

    @property
    def dependents(self) -> int:
        return sum(r["role"] == DEPENDENT for r in self.rows)


def select_packages(stanzas: Iterable[Mapping[str, str]], settings: Settings) -> Selection:
    """Font-like stanzas and their dependents, as extract rows sorted by package."""
    packages, repeated = dedupe(stanzas)
    pattern = settings.pattern
    fonts = {n for n, s in packages.items() if _font_like(s, settings)}
    font_names = set(fonts)
    for name in fonts:
        font_names.update(_names(packages[name].get("Provides", "")))

    def is_font(name: str) -> bool:
        return name in font_names or bool(pattern.search(name))

    rows = []
    for name in sorted(packages):
        stanza = packages[name]
        groups = {}
        for field, key, _ in FIELDS:
            kept = [
                g
                for g in relation_groups(stanza.get(field, ""))
                if any(is_font(n) and n != name for n in g)
            ]
            if kept:
                groups[key] = kept
        if name in fonts or groups:
            rows.append(package_row(stanza, groups, FONT if name in fonts else DEPENDENT))
    kept = {r["package"] for r in rows}
    return Selection(rows, len(packages), [n for n in repeated if n in kept])


def open_text(path: Path) -> TextIO:
    """``path`` as UTF-8 text, decompressed as its magic bytes say (xz, gzip or none).

    The name does not decide: a server or proxy that sends a ``.gz`` file with
    ``Content-Encoding: gzip`` gets it decoded on the wire, and the file on
    disk is then plain text.
    """
    with path.open("rb") as fh:
        head = fh.read(len(XZ_MAGIC))
    if head.startswith(XZ_MAGIC):
        return lzma.open(path, "rt", encoding="utf-8", errors="replace")
    if head.startswith(GZIP_MAGIC):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def read_packages(path: Path, settings: Settings) -> Selection:
    """Select from a Packages file on disk (xz-compressed, as fetched)."""
    with open_text(path) as fh:
        return select_packages(iter_stanzas(fh), settings)


# --- popcon ----------------------------------------------------------------------------------


def iter_by_inst(lines: Iterable[str]) -> Iterator[dict]:
    """The package rows of a popcon ``by_inst`` file, in file order.

    Stops at the ``-----`` line before the Total row. Raises ``ValueError`` on
    an unreadable row, and at the end when the ``#rank name inst vote old
    recent no-files`` header was missing (the columns changed: a broken source).
    """
    header = False
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        if line.startswith("#"):
            header = header or tuple(line.split()[: len(POPCON_COLUMNS)]) == POPCON_COLUMNS
            continue
        if line.startswith("---"):
            break
        if not line.strip():
            continue
        m = _POPCON_ROW.match(line)
        if m is None:
            raise ValueError(f"by_inst line {number}: unreadable row {line[:80]!r}")
        rank, name, inst, vote, old, recent, no_files, rest = m.groups()
        yield {
            "name": name,
            "rank": int(rank),
            "inst": int(inst),
            "vote": int(vote),
            "old": int(old),
            "recent": int(recent),
            "no_files": int(no_files),
            "in_sid": (rest or "").strip() != NOT_IN_SID,
        }
    if not header:
        raise ValueError(f"by_inst: no {' '.join(POPCON_COLUMNS)} header; the format changed?")


@dataclass(frozen=True, slots=True)
class PopconRows:
    rows: list[dict]  # kept rows, sorted by name
    total: int  # rows in the file


def select_popcon(
    rows: Iterable[Mapping[str, Any]], names: set[str], settings: Settings
) -> PopconRows:
    """Rows for ``names`` (the kept packages) and names matching the pattern, one per name."""
    pattern = settings.pattern
    kept: dict[str, dict] = {}
    total = 0
    for row in rows:
        total += 1
        name = row["name"]
        if name not in kept and (name in names or pattern.search(name)):
            kept[name] = dict(row)
    return PopconRows([kept[n] for n in sorted(kept)], total)


def read_popcon(path: Path, names: set[str], settings: Settings) -> PopconRows:
    """Select from a ``by_inst`` file on disk (gzipped, as fetched)."""
    with open_text(path) as fh:
        return select_popcon(iter_by_inst(fh), names, settings)


def submissions_from(head: bytes) -> int | None:
    """``N`` of the ``Submissions: N`` first line of all-popcon-results.gz, from its first bytes."""
    try:
        text = zlib.decompressobj(wbits=31).decompress(head, 65536)
    except zlib.error:
        return None
    for line in text.decode("utf-8", "replace").splitlines()[:5]:
        m = _SUBMISSIONS.match(line)
        if m is not None:
            return int(m.group(1))
    return None


def http_day(value: str | None) -> date | None:
    """The UTC day of an HTTP date (``Last-Modified``); None when absent or unreadable."""
    if not value:
        return None
    try:
        moment = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


# --- snapshot checks --------------------------------------------------------------------------


def previous_rows(previous: Snapshot | None, name: str) -> int | None:
    """The row count of an earlier snapshot's extract ``name``, from its manifest."""
    entry = previous.manifest.extract(name) if previous is not None else None
    return entry.rows if entry is not None else None


def check_shrink(what: str, rows: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when ``rows`` is empty or below ``min_share`` of ``before``."""
    if rows == 0:
        raise ValueError(f"{what}: no rows kept; a broken file?")
    if before is not None and rows < min_share * before:
        raise ValueError(
            f"{what}: {rows} rows, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken file?"
        )


def comparable(previous: Snapshot | None, settings: Settings) -> Snapshot | None:
    """The previous snapshot when it selected packages as ``settings`` do.

    Only such a snapshot may lend its Packages extract (on a 304) or its row
    counts (the shrink check); one with another selection (settings or
    ``SELECTION_REVISION``), a missing extract or an unreadable ``meta.json``
    counts as none.
    """
    if previous is None or not all(previous.has(n) for n in (META, PACKAGES, POPCON)):
        return None
    try:
        meta = load_meta(previous)
    except ValueError:
        return None
    if not isinstance(meta.get("packages"), dict):
        return None
    wanted = settings.selection()
    return previous if {k: meta.get(k) for k in wanted} == wanted else None


def packages_info(url: str, last_modified: str | None, sel: Selection) -> dict[str, Any]:
    """``meta.json``'s ``packages`` entry (the same keys whether fetched or copied on a 304)."""
    return {
        "url": url,
        "last_modified": last_modified,
        "listed": sel.listed,
        "fonts": sel.fonts,
        "dependents": sel.dependents,
    }


def load_meta(snapshot: Snapshot) -> dict[str, Any]:
    """``meta.json``, checked for its schema and the fields parse reads."""
    meta = snapshot.load_json(META)
    if not isinstance(meta, dict) or meta.get("schema") != META_SCHEMA:
        raise ValueError(f"{META}: not a schema-{META_SCHEMA} meta file")
    popcon = meta.get("popcon")
    if not isinstance(popcon, dict) or not isinstance(popcon.get("date"), str):
        raise ValueError(f"{META}: no popcon.date")
    return meta


# --- parse -----------------------------------------------------------------------------------


def _key(name: str) -> SourceKey:
    return SourceKey(NAMESPACE, name)


def relations(rows: Iterable[Mapping[str, Any]]) -> Iterator[Relation]:
    """Dependency edges (one per alternative) and the font packages' provides."""
    for row in rows:
        subject = _key(row["package"])
        for field, key, kind in FIELDS:
            for group in row[key]:
                extra: dict[str, Scalar] = {"group": " | ".join(group)} if len(group) > 1 else {}
                for alt, name in enumerate(group):
                    yield Relation(
                        source=NAME,
                        subject=subject,
                        kind=kind,
                        object=_key(name),
                        alt=alt,
                        attrs=attrs(field=field, **extra),
                    )
        if row["role"] == FONT:
            for virtual in row["provides"]:
                yield Relation(NAME, subject, "provides", _key(virtual))


def observations(
    rows: Sequence[Mapping[str, Any]], popcon: Iterable[Mapping[str, Any]], meta: Mapping[str, Any]
) -> Iterator[Observation]:
    """One ``inst`` observation per popcon row, and None for font packages popcon lacks."""
    day = date.fromisoformat(meta["popcon"]["date"])
    submissions = meta["popcon"].get("submissions")
    common: dict[str, Scalar] = {"submissions": submissions} if type(submissions) is int else {}
    packaged = {r["package"]: r for r in rows}
    seen = set()
    for row in popcon:
        name = row["name"]
        seen.add(name)
        yield _observation(name, float(row["inst"]), day, packaged.get(name), common, row["in_sid"])
    for name, row in packaged.items():
        if row["role"] == FONT and name not in seen:
            yield _observation(name, None, day, row, common, None)


def _observation(
    name: str,
    value: float | None,
    day: date,
    row: Mapping[str, Any] | None,
    common: Mapping[str, Scalar],
    in_sid: bool | None,
) -> Observation:
    found: dict[str, Scalar] = {**common, "packaged": row is not None}
    found["role"] = row["role"] if row is not None else FONT  # kept for the name pattern
    if in_sid is not None:
        found["in_sid"] = in_sid
    if row is not None:
        found["deb_source"] = row["source"]
        if row["section"]:
            found["section"] = row["section"]
    return Observation(
        source=NAME,
        series=SERIES,
        key=_key(name),
        value=value,
        unit="installs",
        start=day,
        end=day,
        attrs=attrs(**found),
    )


# --- the collector -----------------------------------------------------------------------------


def _record(ctx: FetchContext, result: FetchResult) -> None:
    for record in result.to_records(kept=False):
        ctx.out.record_fetch(record)


class Debian(CollectorBase):
    """Debian popcon installs of font packages and their dependents, with dependency edges."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (DEB_HOST, POPCON_HOST)
    emits: ClassVar[tuple[type, ...]] = (Observation, Relation)
    group: ClassVar[str | None] = "debian"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Packages.xz (conditional), by_inst.gz and the submission total (module docstring)."""
        settings = _settings(ctx.settings)
        previous = comparable(ctx.previous, settings)
        rows, packages = self._packages(ctx, settings, previous)
        popcon = self._popcon(ctx, settings, {r["package"] for r in rows}, previous)
        meta = {
            "schema": META_SCHEMA,
            **settings.selection(),
            "packages": packages,
            "popcon": popcon,
        }
        ctx.out.write_json(META, meta)
        ctx.out.set_data_date(date.fromisoformat(popcon["date"]))

    def _packages(
        self, ctx: FetchContext, settings: Settings, previous: Snapshot | None
    ) -> tuple[list[dict], dict[str, Any]]:
        """The kept Packages rows and ``meta.json``'s ``packages`` entry; writes the extract."""
        url = settings.packages_url
        result = ctx.fetcher.get(
            url, previous=previous_record(previous, url), to=ctx.raw.file(RAW_PACKAGES)
        )
        _record(ctx, result)
        if result.not_modified:
            if previous is None:  # the fetcher sends validators only from `previous`
                raise FetchError(f"{url}: 304 without a previous snapshot")
            listed = load_meta(previous)["packages"].get("listed")
            ctx.out.copy_extract(previous, PACKAGES)
            ctx.out.note(f"Packages.xz unchanged since {result.last_modified} (304)")
            copied = Selection(
                list(previous.iter_jsonl(PACKAGES)), listed if type(listed) is int else None, []
            )
            return copied.rows, packages_info(url, result.last_modified, copied)
        assert result.path is not None
        sel = read_packages(result.path, settings)
        check_shrink(PACKAGES, len(sel.rows), previous_rows(previous, PACKAGES), settings.min_share)
        if not sel.fonts:
            raise ValueError(f"{url}: no font-like packages; the wrong file?")
        ctx.out.write_jsonl(PACKAGES, sel.rows)
        if sel.repeated:
            ctx.out.note(f"listed twice, highest version kept: {', '.join(sel.repeated)}")
        ctx.log.info(
            "%s: %d packages, %d font-like, %d dependents (%d bytes)",
            self.name,
            sel.listed,
            sel.fonts,
            sel.dependents,
            result.size,
        )
        return sel.rows, packages_info(url, result.last_modified, sel)

    def _popcon(
        self, ctx: FetchContext, settings: Settings, names: set[str], previous: Snapshot | None
    ) -> dict[str, Any]:
        """``meta.json``'s ``popcon`` entry; writes the extract."""
        result = ctx.fetcher.get(POPCON_URL, to=ctx.raw.file(RAW_POPCON))
        _record(ctx, result)
        assert result.path is not None
        kept = read_popcon(result.path, names, settings)
        check_shrink(POPCON, len(kept.rows), previous_rows(previous, POPCON), settings.min_share)
        ctx.out.write_jsonl(POPCON, kept.rows)
        day = http_day(result.last_modified) or result.fetched_at.astimezone(UTC).date()
        submissions = self._submissions(ctx)
        ctx.log.info(
            "%s: popcon of %s, %d of %d rows kept, %s submissions",
            self.name,
            day,
            len(kept.rows),
            kept.total,
            "unknown" if submissions is None else submissions,
        )
        return {
            "url": POPCON_URL,
            "last_modified": result.last_modified,
            "date": day.isoformat(),
            "rows": kept.total,
            "submissions": submissions,
        }

    def _submissions(self, ctx: FetchContext) -> int | None:
        try:
            result = ctx.fetcher.get_range(RESULTS_URL, 0, RESULTS_HEAD - 1)
        except FetchError as exc:
            ctx.log.warning("%s: no submission total: %s", self.name, exc)
            ctx.out.note(f"submission total unknown: {exc}")
            return None
        _record(ctx, result)
        found = submissions_from(result.content[:RESULTS_HEAD])
        if found is None:
            ctx.out.note("submission total unknown: no 'Submissions:' line in all-popcon-results")
        return found

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """Observations and relations from the snapshot's extracts (module docstring)."""
        _settings(ctx.settings)
        meta = load_meta(ctx.snapshot)
        rows = list(ctx.snapshot.iter_jsonl(PACKAGES))
        popcon = list(ctx.snapshot.iter_jsonl(POPCON))
        seen: set[Record] = set()
        for record in (*relations(rows), *observations(rows, popcon, meta)):
            if record not in seen:
                seen.add(record)
                yield record


COLLECTOR = Debian()
