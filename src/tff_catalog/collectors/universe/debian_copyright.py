"""Universe collector "debian_copyright": license short names from Debian's DEP-5 files.

**Use** (design-m1 §2.4, docs/sources.md): one input to the license cross-check
(L2, milestone-1 step 6a). Facts only: the license identifiers, never the file
text; fixtures are synthetic (BRIEF item 12). It emits ``LicenseFact`` records
only, keyed ``deb-src:<source package>``; ``data/aliases.csv`` rows of relation
``package`` tie a source package to its family. It emits no ``UniverseRecord``,
because a Debian source package is not a family and would mint one.

**Fetch.**

1. ``Sources.xz`` of sid main (about 12 MB) goes to ``ctx.raw`` and is never
   kept. A source package is a font package when its own ``Section`` or the
   section of any binary in its ``Package-List`` is in ``sections``: many font
   sources (DejaVu, Hack, Fira Code, Cascadia, Inter) are filed under ``misc``
   while their binaries are ``fonts``. When sid lists several versions of a
   package, the highest (dpkg order) wins.
2. For each font package, one GET of
   ``metadata.ftp-master.debian.org/changelogs/<Directory minus pool/>/unstable_copyright``
   (1 a second, the fetcher's default pace). The request is conditional on the
   previous snapshot's validators; on a 304 the previous row's content is
   carried over. Rows carry over only from a snapshot of the same collector
   ``version``: a carried row is never read again, so a new reading of the
   files needs every file fetched once. A 404 gives a row with status 404 and
   no stanzas. A request that fails after the fetcher's retries carries the
   previous row over, or leaves the package out, with a manifest note; more
   than ``max_failures`` such failures fail the fetch, and so does a
   font-package list below ``min_share`` of the previous snapshot's, so the
   stale policy reuses the last good snapshot. On 2026-09-26: 475 font
   packages (239 by source section alone), 476 requests in 8 minutes, 14.9 MB
   on the wire (12 MB of it ``Sources.xz``); a later run's requests are mostly
   304s.

The one extract, ``copyright.jsonl.gz``, has one row per font package, sorted
by name (``row``): package, version, directory, the Sources ``Homepage``, the
copyright URL and HTTP status, whether the file is machine-readable (DEP-5:
the first paragraph has ``Format``), ``Upstream-Name``, the URLs of the header's
``Source`` field, the header's ``License`` short name, and every ``Files``
stanza as ``{"files": [patterns], "license": <first line of License>}``.
Stanzas for the packaging (every pattern under ``debian/``) are dropped here
already, and no copyright holder, contact, e-mail or license text is kept.

**Parse** (offline, pure). For each row with status 200 and a DEP-5 file: the
stanzas, minus packaging patterns and minus stanzas whose every pattern
matches a ``non_font_files`` glob (build scripts, documentation, AppStream
metadata: they do not describe the fonts, and L2 ANDs every fact of a source);
then one ``LicenseFact`` per distinct license string, in order of appearance:

- ``raw`` is the stanza's short name or expression exactly as written
  (``OFL-1.1-RFN``, ``bitstream-vera``, ``AGPL-3 with Font exception``,
  ``OFL-1.1 or GPL-3+``); ``spdx`` stays None, because DEP-5 short names are
  not SPDX (``license-aliases.toml`` normalises them);
- ``rfn`` is True for a ``-RFN`` name and False for a ``-no-RFN`` one, else None;
- ``attrs``: ``version`` (the Debian source version), ``files`` (the stanzas'
  patterns, space-separated, cut at ``FILES_ATTR_MAX`` characters) and
  ``upstream_name`` when the header gives one.
"""

import lzma
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchError, FetchResult, previous_record
from tff_catalog.records import LicenseFact, Record, Scalar, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "debian_copyright"
ARCHIVE_HOST = "deb.debian.org"
METADATA_HOST = "metadata.ftp-master.debian.org"
SOURCES_URL = f"https://{ARCHIVE_HOST}/debian/dists/sid/main/source/Sources.xz"
METADATA_BASE = f"https://{METADATA_HOST}/changelogs/"
COPYRIGHT_NAME = "unstable_copyright"
POOL_PREFIX = "pool/"
EXTRACT = "copyright.jsonl.gz"
SOURCES_RAW = "Sources.xz"
NAMESPACE = "deb-src"
PACKAGING_DIR = "debian/"  # Files stanzas under debian/ cover the packaging (usually GPL-2+)
FILES_ATTR_MAX = 200  # characters of the ``files`` attr; the extract keeps every pattern
PROGRESS_EVERY = 100  # packages between progress log lines
FORMAT_FIELDS = ("format", "format-specification")  # DEP-5 1.0, and the drafts before it
_URL = re.compile(r"https?://[^\s<>\"'(),;]+")
_RFN = re.compile(r"-RFN\b", re.IGNORECASE)
_NO_RFN = re.compile(r"-no-RFN\b", re.IGNORECASE)
_FIELD = re.compile(r"^([^\s:#][^:]*):[ \t]*(.*)$")

# Defaults of the Settings, which config/sources/debian_copyright.toml spells out.
SECTIONS: tuple[str, ...] = ("fonts",)
MIN_SHARE = 0.9  # Debian gains and loses a few font packages a month, never a tenth
MAX_FAILURES = 10
# Claude's default (no gate): Files patterns that never hold fonts or font sources
# (AppStream metadata, build tooling, autotools, fontconfig rules, LaTeX support,
# web assets, documentation), from the 2026-09-26 snapshot.
NON_FONT_FILES: tuple[str, ...] = (
    *("appstream/*", "*.metainfo.xml", "*.appdata.xml"),
    *("node_modules/*", "scripts/*", "tools/*", "tests/*", "test/*"),
    *("*.py", "*.pl", "*.sh", "*.pe"),
    *("Makefile*", "configure*", "aclocal.m4", "m4/*", "*install-sh", "missing"),
    *("*.conf", "latex/*", "css/*", "less/*", "scss/*", "*.css", "*.js"),
    *("doc/*", "docs/*", "_docs/*", "documentation/*", "README*", "*.md", "OFL-FAQ.txt"),
)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/debian_copyright.toml``."""

    sources_url: str = SOURCES_URL
    metadata_base: str = METADATA_BASE
    copyright_name: str = COPYRIGHT_NAME
    sections: tuple[str, ...] = SECTIONS
    min_share: float = MIN_SHARE  # of the previous snapshot's font packages, or the fetch fails
    max_failures: int = MAX_FAILURES  # failed copyright requests before the fetch fails
    non_font_files: tuple[str, ...] = NON_FONT_FILES  # fnmatch globs over Files patterns

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        _check_url(self.sources_url, ARCHIVE_HOST, f"{where}.sources_url")
        _check_url(self.metadata_base, METADATA_HOST, f"{where}.metadata_base")
        if not self.metadata_base.endswith("/"):
            raise ConfigError(f"{where}.metadata_base: must end with '/'")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.copyright_name):
            raise ConfigError(f"{where}.copyright_name: must be a plain file name")
        if not self.sections or not all(s.strip() for s in self.sections):
            raise ConfigError(f"{where}.sections: must list at least one section")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")
        if self.max_failures < 0:
            raise ConfigError(f"{where}.max_failures: must not be negative")
        if not all(g.strip() for g in self.non_font_files):
            raise ConfigError(f"{where}.non_font_files: empty pattern")


def _check_url(url: str, host: str, where: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != host:
        raise ConfigError(f"{where}: must be an https URL on {host}, got {url!r}")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- deb822 paragraphs (Sources and DEP-5 share the syntax) ------------------------------------


def paragraphs(lines: Iterable[str]) -> Iterator[dict[str, str]]:
    """deb822 paragraphs as ``{lower-case field: value}``.

    A value is its first line, then each continuation line (one that starts
    with a space or tab) after a newline, stripped. Blank or whitespace-only
    lines separate paragraphs; ``#`` comment lines and lines that are neither
    a field nor a continuation are skipped. A repeated field keeps its first value.
    """
    fields: dict[str, list[str]] = {}
    last: str | None = None
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line.strip():
            if fields:
                yield _joined(fields)
            fields, last = {}, None
        elif line[0] in " \t":
            if last is not None:
                fields[last].append(line.strip())
        elif (m := _FIELD.match(line)) is not None:
            name = m.group(1).strip().lower()
            last = None if name in fields else name
            if last is not None:
                fields[last] = [m.group(2).strip()]
        else:
            last = None
    if fields:
        yield _joined(fields)


def _joined(fields: Mapping[str, list[str]]) -> dict[str, str]:
    return {k: "\n".join(v) for k, v in fields.items()}


def _first_line(value: str | None) -> str | None:
    """The first line of a field value, or None when it is empty."""
    if value is None:
        return None
    first = value.split("\n", 1)[0].strip()
    return first or None


# --- Sources.xz: the font source packages ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourcePackage:
    """A font source package as sid's ``Sources`` lists it."""

    name: str
    version: str
    directory: str  # "pool/main/f/fonts-inter"
    homepage: str | None = None


def _sections(fields: Mapping[str, str]) -> set[str]:
    """The source's own section and every binary's (``Package-List``), without the area prefix."""
    found = {fields.get("section", "")}
    for line in fields.get("package-list", "").split("\n"):
        parts = line.split()
        if len(parts) >= 3:
            found.add(parts[2])
    return {s.rsplit("/", 1)[-1] for s in found if s}


def font_sources(lines: Iterable[str], sections: Iterable[str]) -> list[SourcePackage]:
    """The source packages of a ``Sources`` file with a section in ``sections``, sorted by name.

    A package listed at several versions is kept at its highest (dpkg order);
    paragraphs without a package name, version or pool directory are skipped.
    """
    wanted = set(sections)
    best: dict[str, SourcePackage] = {}
    for fields in paragraphs(lines):
        name = _first_line(fields.get("package"))
        version = _first_line(fields.get("version"))
        directory = _first_line(fields.get("directory"))
        if not (name and version and directory and directory.startswith(POOL_PREFIX)):
            continue
        if not _sections(fields) & wanted:
            continue
        pkg = SourcePackage(name, version, directory, _first_line(fields.get("homepage")))
        held = best.get(name)
        if held is None or version_compare(version, held.version) > 0:
            best[name] = pkg
    return [best[n] for n in sorted(best)]


def copyright_url(base: str, directory: str, name: str = COPYRIGHT_NAME) -> str:
    """``<base><directory minus pool/>/<name>``, the file's URL on the metadata host."""
    return f"{base}{directory.removeprefix(POOL_PREFIX).strip('/')}/{name}"


# --- dpkg version order ------------------------------------------------------------------------


def _order(ch: str) -> int:
    """dpkg's character weight: ``~`` first, then the end, then letters, then the rest."""
    if ch == "~":
        return -1
    if ch == "" or (ch.isascii() and ch.isdigit()):
        return 0
    if ch.isascii() and ch.isalpha():
        return ord(ch)
    return ord(ch) + 256


def _digit(s: str, i: int) -> bool:
    return i < len(s) and s[i].isascii() and s[i].isdigit()


def _verrevcmp(a: str, b: str) -> int:
    """dpkg's ``verrevcmp``: alternate non-digit runs (by ``_order``) and numbers."""
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not _digit(a, i)) or (j < len(b) and not _digit(b, j)):
            ac = _order(a[i] if i < len(a) else "")
            bc = _order(b[j] if j < len(b) else "")
            if ac != bc:
                return ac - bc
            i, j = i + 1, j + 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        first_diff = 0
        while _digit(a, i) and _digit(b, j):
            if not first_diff:
                first_diff = ord(a[i]) - ord(b[j])
            i, j = i + 1, j + 1
        if _digit(a, i):
            return 1
        if _digit(b, j):
            return -1
        if first_diff:
            return first_diff
    return 0


def _split_version(v: str) -> tuple[int, str, str]:
    epoch, colon, rest = v.partition(":")
    if not colon:
        epoch, rest = "0", v
    upstream, dash, revision = rest.rpartition("-")
    if not dash:
        upstream, revision = rest, ""
    return (int(epoch) if epoch.isdigit() else 0), upstream, revision


def version_compare(a: str, b: str) -> int:
    """Negative, zero or positive as Debian version ``a`` sorts before, with or after ``b``."""
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return ea - eb
    return _verrevcmp(ua, ub) or _verrevcmp(ra, rb)


# --- DEP-5 copyright files ---------------------------------------------------------------------


def _packaging(pattern: str) -> bool:
    return pattern.removeprefix("./").startswith(PACKAGING_DIR)


def _patterns(value: str) -> list[str]:
    """The patterns of a ``Files`` field (whitespace-separated; stray commas dropped)."""
    out = []
    for word in value.split():
        pattern = word.strip(",")
        if pattern and pattern not in out:
            out.append(pattern)
    return out


def _source_urls(value: str | None) -> list[str]:
    """The URLs of a header ``Source`` field, in order; never anything else (no e-mail)."""
    found: list[str] = []
    for m in _URL.finditer(value or ""):
        url = m.group(0).rstrip(".:!?]")
        if "@" not in url and url not in found:
            found.append(url)
    return found


def read_copyright(text: str) -> dict[str, Any]:
    """The facts of one copyright file (the content fields of an extract row).

    ``dep5`` is False for a free-form file, which then has no stanzas. Stanzas
    are the ``Files`` paragraphs in file order, each ``{"files", "license"}``
    (``license`` None when the paragraph has no short name), a header that
    also carries ``Files`` (a malformed file) included; packaging-only
    stanzas (every pattern under ``debian/``) are left out, as are the
    packaging patterns of mixed stanzas.
    """
    paras = list(paragraphs(text.splitlines()))
    header = paras[0] if paras else {}
    if not any(f in header for f in FORMAT_FIELDS):
        return {
            "dep5": False,
            "upstream_name": None,
            "source_urls": [],
            "header_license": None,
            "stanzas": [],
        }
    stanzas = []
    for para in paras:
        if "files" not in para:
            continue  # a standalone License paragraph: the text of a short name
        files = [p for p in _patterns(para["files"]) if not _packaging(p)]
        if files:
            stanzas.append({"files": files, "license": _first_line(para.get("license"))})
    return {
        "dep5": True,
        "upstream_name": _first_line(header.get("upstream-name")),
        "source_urls": _source_urls(header.get("source")),
        "header_license": _first_line(header.get("license")),
        "stanzas": stanzas,
    }


# --- fetch: the extract ------------------------------------------------------------------------

CONTENT_FIELDS = ("dep5", "upstream_name", "source_urls", "header_license", "stanzas")


def row(pkg: SourcePackage, url: str, status: int, content: Mapping[str, Any]) -> dict[str, Any]:
    """One extract row: the package, its copyright URL and status, and the file's facts."""
    out: dict[str, Any] = {
        "package": pkg.name,
        "version": pkg.version,
        "directory": pkg.directory,
        "homepage": pkg.homepage,
        "url": url,
        "status": status,
    }
    for name in CONTENT_FIELDS:
        out[name] = content.get(name)
    return out


MISSING = read_copyright("")  # the content of a row whose file is missing (404)


def previous_rows(previous: Snapshot | None) -> dict[str, dict[str, Any]] | None:
    """An earlier snapshot's rows by package; None when there is no such extract."""
    if previous is None or not previous.has(EXTRACT):
        return None
    return {
        r["package"]: r
        for r in previous.iter_jsonl(EXTRACT)
        if isinstance(r, dict) and isinstance(r.get("package"), str)
    }


def carried_rows(
    previous: Snapshot | None, rows: Mapping[str, dict[str, Any]] | None, version: int
) -> Mapping[str, dict[str, Any]]:
    """The earlier rows a 304 or a failed request may carry over: only rows of this extract
    ``version``, because a carried row is never read again and would keep an old reading."""
    if previous is None or rows is None or previous.manifest.collector_version != version:
        return {}
    return rows


def check_shrink(packages: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when the font-package list shrank below ``min_share`` of ``before``."""
    if before is not None and packages < min_share * before:
        raise ValueError(
            f"{packages} font source packages in Sources, down from {before} in the "
            f"previous snapshot (below min_share = {min_share}); a broken index?"
        )


@dataclass(slots=True)
class _Run:
    """What one fetch has done so far."""

    ctx: FetchContext
    settings: Settings
    before: Mapping[str, dict[str, Any]]
    failures: list[str]
    carried: int = 0  # 304s
    missing: int = 0  # 404s

    def record(self, result: FetchResult) -> None:
        for entry in result.to_records():
            self.ctx.out.record_fetch(entry)

    def get(self, url: str, prev: dict[str, Any] | None) -> FetchResult:
        """GET ``url``, conditional only when a 304 could carry ``prev``'s content over."""
        validators = previous_record(self.ctx.previous, url) if _usable(prev) else None
        result = self.ctx.fetcher.get(url, previous=validators, expect=(200, 404))
        self.record(result)
        return result

    def package(self, pkg: SourcePackage) -> dict[str, Any] | None:
        """The package's row, or None when its request failed and no earlier row exists."""
        url = copyright_url(
            self.settings.metadata_base, pkg.directory, self.settings.copyright_name
        )
        prev = self.before.get(pkg.name)
        try:
            result = self.get(url, prev)
        except FetchError as exc:
            self.failures.append(f"{pkg.name}: {exc}")
            if len(self.failures) > self.settings.max_failures:
                raise FetchError(
                    f"{len(self.failures)} copyright requests failed (max_failures = "
                    f"{self.settings.max_failures}); last: {exc}"
                ) from exc
            kept = "the previous row is kept" if prev else "the package is left out"
            self.ctx.log.warning("%s: %s; %s", NAME, exc, kept)
            return prev
        if result.not_modified:  # only asked for with a usable previous row
            self.carried += 1
            assert prev is not None
            return row(pkg, url, 200, prev)
        if result.status == 404:
            self.missing += 1
            return row(pkg, url, 404, MISSING)
        return row(pkg, url, 200, read_copyright(result.body().decode("utf-8", "replace")))


def _usable(prev: dict[str, Any] | None) -> bool:
    """An earlier row whose content a 304 may carry over."""
    return prev is not None and prev.get("status") == 200


def _read_sources(path: Path, sections: tuple[str, ...]) -> list[SourcePackage]:
    with lzma.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        return font_sources(fh, sections)


# --- parse: records ----------------------------------------------------------------------------


def _rfn(raw: str) -> bool | None:
    if _NO_RFN.search(raw):
        return False
    if _RFN.search(raw):
        return True
    return None


def _clip(text: str, limit: int = FILES_ATTR_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def font_stanzas(stanzas: object, non_font: tuple[str, ...]) -> Iterator[tuple[str, list[str]]]:
    """``(license, patterns)`` of the stanzas that describe fonts, in file order.

    Packaging patterns are dropped; a stanza with no pattern left, no license,
    or only patterns matching a ``non_font`` glob is skipped.
    """
    if not isinstance(stanzas, list):
        return
    for st in stanzas:
        if not isinstance(st, dict):
            continue
        license_ = _text(st.get("license"))
        files = [f for f in st.get("files") or () if isinstance(f, str) and not _packaging(f)]
        if license_ is None or not files:
            continue
        if all(any(fnmatchcase(f, g) for g in non_font) for f in files):
            continue
        yield license_, files


def license_facts(r: Mapping[str, Any], settings: Settings) -> Iterator[LicenseFact]:
    """The license facts of one extract row (none unless status 200 and DEP-5)."""
    package, version = _text(r.get("package")), _text(r.get("version"))
    if package is None or version is None or r.get("status") != 200 or r.get("dep5") is not True:
        return
    grouped: dict[str, list[str]] = {}
    for license_, files in font_stanzas(r.get("stanzas"), settings.non_font_files):
        held = grouped.setdefault(license_, [])
        held.extend(f for f in files if f not in held)
    extra: dict[str, Scalar] = {}
    if (name := _text(r.get("upstream_name"))) is not None:
        extra["upstream_name"] = name
    for license_, files in grouped.items():
        yield LicenseFact(
            source=NAME,
            key=SourceKey(NAMESPACE, package),
            raw=license_,
            rfn=_rfn(license_),
            attrs=attrs(version=version, files=_clip(" ".join(files)), **extra),
        )


class DebianCopyright(CollectorBase):
    """Debian's ``debian/copyright`` files of font source packages, as license facts."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (ARCHIVE_HOST, METADATA_HOST)
    emits: ClassVar[tuple[type, ...]] = (LicenseFact,)
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """``Sources.xz`` into ``ctx.raw``; one copyright request per font package; the extract."""
        settings = _settings(ctx.settings)
        raw = ctx.raw.file(SOURCES_RAW)
        index = ctx.fetcher.get(settings.sources_url, to=raw)
        for entry in index.to_records():
            ctx.out.record_fetch(entry)
        packages = _read_sources(raw, settings.sections)
        if not packages:
            raise ValueError(f"{settings.sources_url}: no source package in {settings.sections}")
        before = previous_rows(ctx.previous)
        check_shrink(len(packages), None if before is None else len(before), settings.min_share)
        run = _Run(ctx, settings, carried_rows(ctx.previous, before, self.version), [])
        ctx.log.info("%s: %d font source packages", self.name, len(packages))
        rows = []
        for n, pkg in enumerate(packages, start=1):
            got = run.package(pkg)
            if got is not None:
                rows.append(got)
            if n % PROGRESS_EVERY == 0:
                ctx.log.info("%s: %d of %d copyright files", self.name, n, len(packages))
        rows.sort(key=lambda r: r["package"])
        self._notes(run, len(packages), rows)
        ctx.out.write_jsonl(EXTRACT, rows)
        ctx.out.set_data_date(ctx.run_date)

    def _notes(self, run: _Run, packages: int, rows: list[dict[str, Any]]) -> None:
        out = run.ctx.out
        free_form = sum(1 for r in rows if r["status"] == 200 and not r["dep5"])
        out.note(f"font source packages: {packages}; rows: {len(rows)}")
        if free_form:
            out.note(f"copyright files not in DEP-5 format (no facts): {free_form}")
        if run.carried:
            out.note(f"copyright files not modified since the previous snapshot: {run.carried}")
        if run.missing:
            out.note(f"copyright files missing (404): {run.missing}")
        for failure in run.failures:
            out.note(f"failed: {failure}")
        run.ctx.log.info(
            "%s: %d rows (%d not modified, %d missing, %d free-form, %d failed)",
            self.name,
            len(rows),
            run.carried,
            run.missing,
            free_form,
            len(run.failures),
        )

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One license fact per distinct short name of each package's font stanzas."""
        settings = _settings(ctx.settings)
        seen: set[str] = set()
        for r in ctx.snapshot.iter_jsonl(EXTRACT):
            package = _text(r.get("package")) if isinstance(r, dict) else None
            if package is None or package in seen:
                continue
            seen.add(package)
            yield from license_facts(r, settings)


COLLECTOR = DebianCopyright()
