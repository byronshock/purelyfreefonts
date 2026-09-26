"""Ranking collector "fot": Fonts Over Time's weekly homepage crawl (design-m1 §2.4).

**Source.** The repository ``fcjr/fontsovertime`` publishes one gzipped JSON
Lines file per weekly crawl, ``data/snapshots/weekly/<YYYY-Www>.jsonl.gz``
(about 3 MB gzipped, 10,000 rows): one object per homepage with its
``domain``, ``category`` (startups, popular, indie, ...), ``crawled_at``,
``method`` (``browser`` or ``static``), ``status`` (``ok``, ``timeout``,
``blocked``, ...), ``body_font``, ``heading_font`` and, for the browser crawl,
``dominant``: the families that render the page's text with their shares.
Rows also carry the page's final URL, title and description. The repository
has no license file; ruling T4 lets us use it at the phase-in weight, with
credit, publishing ranks only, and with synthetic test fixtures.

**Fetch.** Pinned to one commit, in three requests plus one per new week:

1. ``api.github.com/repos/<repo>/git/ref/heads/<ref>``: the branch head, which
   pins every later request (files are mutable: an opt-out rewrites every
   published snapshot).
2. **Column check**: the first ``HEADER_BYTES`` of ``latest.csv`` at that
   commit (an HTTP Range request). Its header must equal
   ``Settings.csv_columns``, and every weekly row read below must carry
   ``Settings.row_fields`` (plus ``ok_fields`` when its status is ok, and
   ``dominant`` on an ok browser row). Any mismatch raises
   ``ColumnMismatch``: the fetch fails and the fetch stage's stale policy
   reuses the last good snapshot and flags the source stale (methodology §5,
   "Notes on the sources").
3. ``api.github.com/repos/<repo>/contents/<weekly_dir>?ref=<commit>``: the
   weekly files with their git blob shas. The newest ``keep_weeks`` are kept.
4. For each kept week: when the previous snapshot holds its extract for the
   same blob sha (and the same extract settings), the extract is copied over
   and nothing is downloaded. Otherwise ``raw.githubusercontent.com/<repo>/
   <commit>/<path>`` is downloaded into the run's raw directory, its size and
   git blob sha1 are checked against the listing, and it is extracted. A week
   whose blob changed upstream is extracted again, with a manifest note.

**Extracts.** Only what the ranking needs; never a domain, URL, title or
description (ruling T4):

- ``weeks/<YYYY-Www>.jsonl.gz``: one row per counted site, sorted by ``site``:
  ``{"site": sha256(domain)[:16], "category", "method", "body", "heading",
  "dominant": [[family, share], ...]}``. Counted sites are ok rows whose method
  is in ``Settings.methods``; a domain crawled twice (a browser timeout retried
  statically) counts once, by the first method in ``methods`` that measured it.
  ``dominant`` keeps the families at ``dominant_share`` or more, largest first.
  Font names are kept raw, exactly as FOT wrote them.
- ``index.json``: the pin (repository, ref, commit), the extract settings and,
  per week, the upstream path, blob sha, size, the commit it was fetched at,
  its row and site counts, status counts and crawl dates.

The manifest's window runs from the first to the last crawl day of the kept
weeks, and its ``data_date`` is the last crawl day.

**Parse** (offline, pure): for each week, each counted site's font names are
its ``body``, its ``heading`` and its ``dominant`` families at
``dominant_share`` or more. A name is cleaned (whitespace collapsed, quotes
dropped, a Next.js ``__Inter_d65c78`` unwrapped to ``Inter``) and dropped when
it is a fallback face (next/font's ``__Inter_Fallback_d65c78``, or a last word
"Fallback": ``Inter Fallback``, ``inter-fallback``) or a CSS generic or system
keyword (``Settings.generic_names``). Then, deterministically:

- **One spelling per name**: names with the same ``match_key`` ("Work Sans",
  "WorkSans", "work sans") are written in their most common spelling across
  the snapshot (ties: the smallest string), so each key's volume is whole.
- **A site counts once per family**: within one site, names that agree after
  dropping trailing style words (``names.strip_style``) and variable-font
  suffixes ("Inter", "Inter-Bold", "Inter Variable", "Inter var") count once,
  under the plain name when the site uses it, else under the one most sites
  use (ties: the smallest). So mapping can alias "Inter-Bold" or "Inter
  Variable" to Inter without counting a site twice. The price: a site using
  both Archivo and Archivo Black counts for Archivo only. A name used alone
  keeps its spelling ("Poppins-SemiBold"): mapping decides what it is, nothing
  here guesses.

Each (week, name, category, method) gives one ``Observation``: key
``fot-name:<name>``, series the week (``2026-W39``), value the number of sites,
unit ``sites``, the week's crawl days as ``start``/``end``, and attrs
``category``, ``method`` (records.RESERVED_ATTRS) and ``category_sites``, the
number of counted sites of that category and method in the week (the
denominator the D11 startup cap needs).
"""

import csv
import gzip
import hashlib
import io
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchResult
from tff_catalog.keys import match_key
from tff_catalog.names import strip_style
from tff_catalog.records import Observation, Record, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "fot"
API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
NAMESPACE = "fot-name"
UNIT = "sites"
INDEX = "index.json"
INDEX_SCHEMA = 1
WEEKS_DIR = "weeks"  # extracts: weeks/<YYYY-Www>.jsonl.gz
HEADER_BYTES = 4096  # enough for latest.csv's header line
MAX_FILE_BYTES = 64 * 1024 * 1024  # a weekly file is about 3 MB gzipped; far more is not one
SITE_HEX = 16  # sha256(domain)[:16] (ruling T4)
DOMINANT = "dominant"  # the per-family text shares
SHARES_METHOD = "browser"  # only the browser crawl measures shares; static rows have none
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

# Defaults of the Settings, which config/sources/fot.toml spells out.
REPO = "fcjr/fontsovertime"
REF = "main"
WEEKLY_DIR = "data/snapshots/weekly"
LATEST_CSV = "data/exports/latest.csv"
CSV_COLUMNS = (
    "domain",
    "category",
    "body_font",
    "heading_font",
    "platform",
    "sources",
    "method",
    "observed",
)
ROW_FIELDS = ("domain", "category", "crawled_at", "method", "status")
OK_FIELDS = ("body_font", "heading_font")
KEEP_WEEKS = 6  # covers a month of weekly crawls with a week to spare
METHODS = ("browser", "static")  # ranking.toml [sources.fot] methods
DOMINANT_SHARE = 0.05  # ranking.toml [sources.fot] dominant_share
# CSS generic families and keywords, browsers' aliases for the system font, and "sans" and
# "system" (the owner's gate U ruling of 2026-09-26): names of no particular font.
GENERIC_NAMES = (
    "-apple-system",
    "-webkit-body",
    "-webkit-pictograph",
    "-webkit-standard",
    "blinkmacsystemfont",
    "cursive",
    "emoji",
    "fangsong",
    "fantasy",
    "inherit",
    "initial",
    "math",
    "monospace",
    "revert",
    "revert-layer",
    "sans",
    "sans-serif",
    "serif",
    "system",
    "system-ui",
    "ui-monospace",
    "ui-rounded",
    "ui-sans-serif",
    "ui-serif",
    "unset",
)

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")
_WEEK_FILE = re.compile(r"^(?P<week>\d{4}-W\d{2})\.jsonl\.gz$")
# next/font renames a family to "__Inter_d65c78" and adds "__Inter_Fallback_d65c78".
_NEXT_FONT = re.compile(r"^__(?P<name>.+?)(?P<fallback>_Fallback)?_[0-9a-f]{6,8}$")
# Metric-matched stand-ins that render a local system font until the web font
# loads ("Inter Fallback" from Turbopack's next/font, "inter-fallback" from
# Nuxt): no font of their own, like next/font's "_Fallback" names.
_FALLBACK = re.compile(r"[\s_-]fallback$", re.IGNORECASE)
# Variable-font builds named after their family ("Inter Variable", "Inter var",
# "InterVariable", "Satoshi-VF"): a site using one beside the plain name counts once.
_VARIABLE = re.compile(r"(?:[\s_-]+(?i:variable|var|vf)|(?<=[a-z])Variable)$")
_QUOTES = "'\""


class ColumnMismatch(ValueError):
    """FOT's files no longer have the columns this collector reads: the source goes stale."""


def _relative_path(value: str) -> bool:
    parts = value.split("/")
    return bool(value) and all(p not in ("", ".", "..") for p in parts)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/fot.toml``."""

    repo: str = REPO
    ref: str = REF
    weekly_dir: str = WEEKLY_DIR
    latest_csv: str = LATEST_CSV
    csv_columns: tuple[str, ...] = CSV_COLUMNS  # latest.csv's header, exactly
    row_fields: tuple[str, ...] = ROW_FIELDS  # every weekly row has these
    ok_fields: tuple[str, ...] = OK_FIELDS  # and every ok row these
    keep_weeks: int = KEEP_WEEKS  # the newest weekly files kept in each snapshot
    methods: tuple[str, ...] = METHODS  # counted crawl methods, preferred first
    dominant_share: float = DOMINANT_SHARE  # a family also counts at this share of text
    generic_names: tuple[str, ...] = GENERIC_NAMES  # dropped; lower case, compared casefolded

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not _REPO_RE.fullmatch(self.repo):
            raise ConfigError(f"{where}.repo: expected 'owner/name', got {self.repo!r}")
        if not self.ref or any(c.isspace() for c in self.ref):
            raise ConfigError(f"{where}.ref: expected a branch name, got {self.ref!r}")
        for key in ("weekly_dir", "latest_csv"):
            if not _relative_path(getattr(self, key)):
                raise ConfigError(f"{where}.{key}: expected a relative path inside the repository")
        for key in ("csv_columns", "row_fields", "methods"):
            values = getattr(self, key)
            if not values or len(set(values)) != len(values) or not all(values):
                raise ConfigError(f"{where}.{key}: expected distinct, non-empty names")
        if missing := [f for f in ROW_FIELDS if f not in self.row_fields]:
            raise ConfigError(
                f"{where}.row_fields: must include {', '.join(missing)} (parse reads them)"
            )
        if self.keep_weeks < 1:
            raise ConfigError(f"{where}.keep_weeks: must be at least 1")
        if not 0.0 < self.dominant_share <= 1.0:
            raise ConfigError(f"{where}.dominant_share: must be above 0 and at most 1")
        if bad := [n for n in self.generic_names if n != n.casefold()]:
            raise ConfigError(f"{where}.generic_names: write them in lower case: {bad}")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- the upstream files -------------------------------------------------------------------------


def ref_url(settings: Settings) -> str:
    """The API request for the head commit of ``settings.ref``."""
    return f"https://{API_HOST}/repos/{settings.repo}/git/ref/heads/{quote(settings.ref)}"


def listing_url(settings: Settings, commit: str) -> str:
    """The API request listing the weekly files at ``commit``."""
    path = quote(settings.weekly_dir, safe="/")
    return f"https://{API_HOST}/repos/{settings.repo}/contents/{path}?ref={commit}"


def raw_url(settings: Settings, commit: str, path: str) -> str:
    """``path`` of the repository at ``commit``, percent-encoded."""
    return f"https://{RAW_HOST}/{settings.repo}/{commit}/{quote(path, safe='/')}"


def head_commit(doc: object, settings: Settings) -> str:
    """The commit sha of a ``git/ref`` answer; ``ValueError`` when there is none."""
    obj = doc.get("object") if isinstance(doc, dict) else None
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha) or obj.get("type") != "commit":
        raise ValueError(f"{settings.repo}: no commit at the head of {settings.ref!r}")
    return sha


@dataclass(frozen=True, slots=True, order=True)
class WeekFile:
    """One weekly file of the listing."""

    week: str  # "2026-W39"
    path: str  # in the repository
    blob: str  # git blob sha1
    size: int


def weekly_files(doc: object, settings: Settings) -> dict[str, WeekFile]:
    """The weekly files of a contents-API listing, by week.

    Other entries (a README, a folder) are skipped. ``ValueError`` when the
    answer is not a listing or holds no weekly file.
    """
    if not isinstance(doc, list):
        raise ValueError(f"{settings.repo}/{settings.weekly_dir}: not a folder listing")
    out: dict[str, WeekFile] = {}
    for entry in doc:
        if not isinstance(entry, dict) or entry.get("type") != "file":
            continue
        m = _WEEK_FILE.fullmatch(str(entry.get("name", "")))
        if m is None or not _WEEK_RE.fullmatch(m["week"]):
            continue
        blob, size, path = entry.get("sha"), entry.get("size"), entry.get("path")
        if not (isinstance(blob, str) and _SHA_RE.fullmatch(blob)):
            raise ValueError(f"{settings.repo}: {m['week']} has no blob sha")
        if type(size) is not int or size < 0 or not isinstance(path, str):
            raise ValueError(f"{settings.repo}: {m['week']} has no size or path")
        out[m["week"]] = WeekFile(m["week"], path, blob, size)
    if not out:
        raise ValueError(f"{settings.repo}/{settings.weekly_dir}: no weekly files")
    return out


def csv_header(data: bytes) -> tuple[str, ...]:
    """The column names in the first line of a CSV prefix (a BOM is ignored)."""
    line, newline, _ = data.partition(b"\n")
    if not newline and len(data) >= HEADER_BYTES:
        raise ColumnMismatch(f"latest.csv: no header line in its first {HEADER_BYTES} bytes")
    text = line.decode("utf-8-sig", errors="replace").rstrip("\r")
    return tuple(next(csv.reader(io.StringIO(text)), []))


def check_columns(found: Sequence[str], settings: Settings) -> None:
    """``ColumnMismatch`` unless latest.csv's header is exactly ``settings.csv_columns``."""
    if tuple(found) != settings.csv_columns:
        raise ColumnMismatch(
            f"latest.csv columns changed: expected {list(settings.csv_columns)}, got {list(found)}"
        )


def git_blob_sha1(path: Path) -> str:
    """The git blob sha1 of a file (what the contents API calls ``sha``)."""
    digest = hashlib.sha1(usedforsecurity=False)
    digest.update(b"blob %d\0" % path.stat().st_size)
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


# --- one weekly file ----------------------------------------------------------------------------


def site_id(domain: str) -> str:
    """The site's id: ``sha256(domain)[:16]`` of the lower-cased domain (ruling T4)."""
    return hashlib.sha256(domain.strip().lower().encode("utf-8")).hexdigest()[:SITE_HEX]


def _crawl_day(value: object, where: str) -> date:
    if not isinstance(value, str):
        raise ColumnMismatch(f"{where}: crawled_at is not a time: {value!r}")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ColumnMismatch(f"{where}: crawled_at {value!r} is not an ISO time") from exc
    return (moment.astimezone(UTC) if moment.tzinfo else moment).date()


def check_row(row: object, settings: Settings, where: str) -> Mapping[str, Any]:
    """The row, if it has the fields and types this collector reads; else ``ColumnMismatch``."""
    if not isinstance(row, dict):
        raise ColumnMismatch(f"{where}: not a JSON object")
    wanted = list(settings.row_fields)
    if row.get("status") == "ok":
        wanted += settings.ok_fields
        if row.get("method") == SHARES_METHOD:
            wanted.append(DOMINANT)
    missing = [f for f in wanted if f not in row]
    if missing:
        raise ColumnMismatch(f"{where}: no {', '.join(missing)} (the weekly columns changed)")
    for key in ("domain", "category", "method", "status"):
        if key in wanted and not isinstance(row[key], str):
            raise ColumnMismatch(f"{where}: {key} is not a string")
    dominant = row.get(DOMINANT)
    if DOMINANT in wanted and not isinstance(dominant, list):
        raise ColumnMismatch(f"{where}: {DOMINANT} is not a list")
    for item in dominant if isinstance(dominant, list) else ():
        if not isinstance(item, dict) or "family" not in item or "share" not in item:
            raise ColumnMismatch(f"{where}: a {DOMINANT} entry lacks family or share")
    return row


def _name(value: object) -> str | None:
    """A raw font name as FOT wrote it, if it is a non-empty string."""
    return value if isinstance(value, str) and value.strip() else None


def _dominant(row: Mapping[str, Any], share: float) -> list[list[Any]]:
    """``[[family, share], ...]`` at ``share`` or more, largest first (then by name)."""
    out = []
    for item in row.get(DOMINANT) or ():
        family, value = _name(item.get("family")), item.get("share")
        if family is None or type(value) not in (int, float) or not math.isfinite(value):
            continue
        if value < share:
            continue
        out.append([family, round(float(value), 4)])
    return sorted(out, key=lambda p: (-p[1], p[0]))


def site_row(row: Mapping[str, Any], settings: Settings) -> dict[str, Any]:
    """The extract row of one counted site: no domain, URL, title or description."""
    return {
        "site": site_id(row["domain"]),
        "category": row["category"].strip(),
        "method": row["method"],
        "body": _name(row.get("body_font")),
        "heading": _name(row.get("heading_font")),
        DOMINANT: _dominant(row, settings.dominant_share),
    }


@dataclass(frozen=True, slots=True)
class WeekExtract:
    """What ``extract_week`` found in one weekly file."""

    rows: tuple[dict[str, Any], ...]  # extract rows, sorted by site
    lines: int  # upstream rows read
    status: dict[str, int]  # upstream rows by status
    crawled_from: date | None  # first and last crawl day of the counted sites
    crawled_to: date | None


def extract_week(lines: Iterable[bytes], settings: Settings, where: str) -> WeekExtract:
    """Check every row (``check_row``) and keep one row per counted site (module doc).

    The crawl days are those of the rows kept, not of a row a preferred
    method replaced.
    """
    best: dict[str, tuple[int, date, dict[str, Any]]] = {}
    status: Counter[str] = Counter()
    read = 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        at = f"{where}:{number}"
        try:
            doc = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{at}: not JSON ({exc})") from exc
        row = check_row(doc, settings, at)
        read += 1
        status[row["status"]] += 1
        day = _crawl_day(row["crawled_at"], at)
        if row["status"] != "ok" or row["method"] not in settings.methods:
            continue
        rank = settings.methods.index(row["method"])
        site = site_id(row["domain"])
        if site in best and best[site][0] <= rank:
            continue
        best[site] = (rank, day, site_row(row, settings))
    days = [day for _, day, _ in best.values()]
    return WeekExtract(
        rows=tuple(best[s][2] for s in sorted(best)),
        lines=read,
        status=dict(sorted(status.items())),
        crawled_from=min(days, default=None),
        crawled_to=max(days, default=None),
    )


# --- the index ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeekEntry:
    """One week of ``index.json``."""

    week: str
    path: str  # upstream
    blob: str  # upstream git blob sha1
    bytes: int  # upstream size
    commit: str  # the commit it was downloaded at
    extract: str  # weeks/<week>.jsonl.gz
    lines: int
    sites: int
    status: dict[str, int]
    crawled_from: date | None
    crawled_to: date | None

    def to_json(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "path": self.path,
            "blob": self.blob,
            "bytes": self.bytes,
            "commit": self.commit,
            "extract": self.extract,
            "lines": self.lines,
            "sites": self.sites,
            "status": dict(self.status),
            "crawled_from": None if self.crawled_from is None else self.crawled_from.isoformat(),
            "crawled_to": None if self.crawled_to is None else self.crawled_to.isoformat(),
        }

    @classmethod
    def from_json(cls, d: object) -> WeekEntry:
        if not isinstance(d, dict):
            raise ValueError(f"{INDEX}: a week is not an object: {d!r}")
        try:
            entry = cls(
                week=str(d["week"]),
                path=str(d["path"]),
                blob=str(d["blob"]),
                bytes=int(d["bytes"]),
                commit=str(d["commit"]),
                extract=str(d["extract"]),
                lines=int(d["lines"]),
                sites=int(d["sites"]),
                status={str(k): int(v) for k, v in dict(d["status"]).items()},
                crawled_from=_day(d["crawled_from"]),
                crawled_to=_day(d["crawled_to"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{INDEX}: bad week {d!r}: {exc}") from exc
        if not _WEEK_RE.fullmatch(entry.week) or entry.extract != week_extract(entry.week):
            raise ValueError(f"{INDEX}: bad week {entry.week!r} or extract {entry.extract!r}")
        return entry


def _day(value: object) -> date | None:
    return None if value is None else date.fromisoformat(str(value))


def week_extract(week: str) -> str:
    """The extract name of a week."""
    return f"{WEEKS_DIR}/{week}.jsonl.gz"


@dataclass(frozen=True, slots=True)
class Index:
    """``index.json``: the pin, the extract settings and the weeks kept."""

    repo: str
    ref: str
    commit: str
    methods: tuple[str, ...]
    dominant_share: float
    weeks: tuple[WeekEntry, ...]  # oldest first

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": INDEX_SCHEMA,
            "repo": self.repo,
            "ref": self.ref,
            "commit": self.commit,
            "methods": list(self.methods),
            "dominant_share": self.dominant_share,
            "weeks": [w.to_json() for w in self.weeks],
        }

    @classmethod
    def from_json(cls, doc: object) -> Index:
        """Read ``index.json``; ``ValueError`` on anything else."""
        if not isinstance(doc, dict) or doc.get("schema") != INDEX_SCHEMA:
            raise ValueError(f"{INDEX}: not a schema-{INDEX_SCHEMA} index")
        try:
            index = cls(
                repo=str(doc["repo"]),
                ref=str(doc["ref"]),
                commit=str(doc["commit"]),
                methods=tuple(str(m) for m in doc["methods"]),
                dominant_share=float(doc["dominant_share"]),
                weeks=tuple(WeekEntry.from_json(w) for w in doc["weeks"]),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{INDEX}: {exc}") from exc
        if not _SHA_RE.fullmatch(index.commit):
            raise ValueError(f"{INDEX}: bad commit {index.commit!r}")
        return index

    def same_extracts(self, settings: Settings) -> bool:
        """Whether weeks extracted under this index match what ``settings`` would extract."""
        return (
            self.repo == settings.repo
            and self.methods == settings.methods
            and self.dominant_share == settings.dominant_share
        )


def reusable_weeks(
    previous: Snapshot | None, settings: Settings, version: int
) -> dict[str, WeekEntry]:
    """The previous snapshot's weeks whose extracts can be copied as they are, by week."""
    if (
        previous is None
        or previous.manifest.collector_version != version
        or not previous.has(INDEX)
    ):
        return {}
    try:
        index = Index.from_json(previous.load_json(INDEX))
    except ValueError:
        return {}
    if not index.same_extracts(settings):
        return {}
    return {w.week: w for w in index.weeks if previous.has(w.extract)}


# --- parse ---------------------------------------------------------------------------------------


def clean_name(raw: object, generic: frozenset[str] = frozenset(GENERIC_NAMES)) -> str | None:
    """A font name ready to count, or None (empty, a fallback face, a name in ``generic``).

    Fallback faces are next/font's ``__Inter_Fallback_d65c78`` and any name
    whose last word is "Fallback" (``_FALLBACK``). ``generic`` holds lower-case
    names (``Settings.generic_names``).
    """
    if not isinstance(raw, str):
        return None
    text = " ".join(raw.split()).strip(_QUOTES).strip()
    m = _NEXT_FONT.fullmatch(text)
    if m is not None:
        if m["fallback"]:
            return None
        text = " ".join(m["name"].replace("_", " ").split())
    if not text or not match_key(text) or text.casefold() in generic or _FALLBACK.search(text):
        return None
    return text


def family_fold(name: str) -> str:
    """The key under which one site's names count once (``site_keys``).

    ``match_key`` of the name without trailing style words (``names.strip_style``)
    or variable-font suffixes (``_VARIABLE``): "Inter-Bold", "Inter Variable"
    and "InterVariable" all fold to "inter". Width words stay (Condensed).
    """
    text = strip_style(name)
    while (shorter := _VARIABLE.sub("", text)) != text and shorter:
        text = strip_style(shorter)
    return match_key(text)


def _sites(
    rows: Iterable[object], extract: str, share: float, generic: frozenset[str]
) -> list[Site]:
    """``site_of`` for each row of a weekly extract; an error names the row's position only."""
    out = []
    for n, row in enumerate(rows, start=1):
        try:
            out.append(site_of(row, share, generic))
        except ValueError as exc:
            raise ValueError(f"{extract}: row {n}: {exc}") from None
    return out


@dataclass(frozen=True, slots=True)
class Site:
    """One counted site of one week, as parse sees it."""

    category: str
    method: str
    names: frozenset[str]  # cleaned, as written


def site_of(row: object, share: float, generic: frozenset[str]) -> Site:
    """The names a site uses for body, headings or ``share`` of its text (``clean_name``).

    A malformed row raises ``ValueError`` naming only the missing keys: Fonts Over Time's
    rows stay private (ruling T4), and refresh's errors reach the public Actions log.
    """
    if not isinstance(row, dict):
        raise ValueError(f"weekly extract row is a {type(row).__name__}, not an object")
    missing = sorted({"category", "method"} - set(row))
    if missing:
        raise ValueError(f"weekly extract row lacks {' and '.join(missing)}")
    raw = [row.get("body"), row.get("heading")]
    raw += [f for f, s in row.get(DOMINANT) or () if s >= share]
    names = {n for n in (clean_name(r, generic) for r in raw) if n is not None}
    return Site(str(row["category"]), str(row["method"]), frozenset(names))


@dataclass(frozen=True, slots=True)
class Spelling:
    """How parse writes names, from every site of the snapshot (module doc).

    ``canonical`` maps a ``match_key`` to its most common spelling (ties: the
    smallest string); ``sites`` counts the sites using each canonical name in
    any spelling; ``fold`` maps a canonical name to its ``family_fold``.
    """

    canonical: Mapping[str, str]
    sites: Mapping[str, int]
    fold: Mapping[str, str]

    @classmethod
    def of(cls, sites: Iterable[Site]) -> Spelling:
        spelled: Counter[str] = Counter()
        keyed: Counter[str] = Counter()
        for site in sites:
            spelled.update(site.names)
            keyed.update({match_key(n) for n in site.names})
        canonical: dict[str, str] = {}
        for name in sorted(spelled):
            key = match_key(name)
            if key not in canonical or spelled[name] > spelled[canonical[key]]:
                canonical[key] = name
        return cls(
            canonical=canonical,
            sites={name: keyed[key] for key, name in canonical.items()},
            fold={name: family_fold(name) for name in canonical.values()},
        )

    def __call__(self, name: str) -> str:
        """The canonical spelling of ``name`` (a name of the sites it was made from)."""
        return self.canonical[match_key(name)]


def site_keys(names: Iterable[str], spelling: Spelling) -> frozenset[str]:
    """The names one site counts under: one spelling each, and one per family.

    Names with the same ``family_fold`` count once: under the plain name when
    the site uses it, else under the one most sites use (ties: the smallest).
    """
    groups: dict[str, set[str]] = defaultdict(set)
    for name in names:
        canonical = spelling(name)
        groups[spelling.fold[canonical]].add(canonical)
    out = set()
    for fold, group in groups.items():
        plain = [n for n in group if match_key(n) == fold]
        out.add(plain[0] if plain else min(group, key=lambda n: (-spelling.sites[n], n)))
    return frozenset(out)


def week_observations(
    entry: WeekEntry, sites: Sequence[Site], spelling: Spelling
) -> Iterator[Observation]:
    """One Observation per (name, category, method) of one week."""
    if entry.crawled_from is None or entry.crawled_to is None:
        return
    counts: Counter[tuple[str, str, str]] = Counter()
    totals: Counter[tuple[str, str]] = Counter()
    for site in sites:
        totals[site.category, site.method] += 1
        for name in site_keys(site.names, spelling):
            counts[name, site.category, site.method] += 1
    for (name, category, method), n in sorted(counts.items()):
        yield Observation(
            source=NAME,
            series=entry.week,
            key=SourceKey(NAMESPACE, name),
            value=float(n),
            unit=UNIT,
            start=entry.crawled_from,
            end=entry.crawled_to,
            attrs=attrs(category=category, method=method, category_sites=totals[category, method]),
        )


# --- the collector -------------------------------------------------------------------------------


def _record(ctx: FetchContext, result: FetchResult, *, kept: bool = False) -> None:
    for record in result.to_records(kept=kept):
        ctx.out.record_fetch(record)


class FontsOverTime(CollectorBase):
    """Fonts Over Time: which fonts about 10,000 homepages use, week by week."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (API_HOST, RAW_HOST)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "fot"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Pin the head commit, check the columns, then keep the newest weeks (module doc)."""
        settings = _settings(ctx.settings)
        answer = ctx.fetcher.get(ref_url(settings), headers=API_HEADERS)
        _record(ctx, answer)
        commit = head_commit(answer.json(), settings)
        header = ctx.fetcher.get_range(
            raw_url(settings, commit, settings.latest_csv), 0, HEADER_BYTES - 1
        )
        _record(ctx, header)
        check_columns(csv_header(header.body()), settings)
        listing = ctx.fetcher.get(listing_url(settings, commit), headers=API_HEADERS)
        _record(ctx, listing)
        files = weekly_files(listing.json(), settings)
        chosen = [files[w] for w in sorted(files)[-settings.keep_weeks :]]
        reuse = reusable_weeks(ctx.previous, settings, self.version)
        weeks = []
        for f in chosen:
            old = reuse.get(f.week)
            if old is not None and old.blob == f.blob and ctx.previous is not None:
                ctx.out.copy_extract(ctx.previous, old.extract)
                weeks.append(old)
                continue
            if old is not None:
                ctx.out.note(f"{f.week} changed upstream (blob {old.blob[:12]} -> {f.blob[:12]})")
            weeks.append(self._fetch_week(ctx, settings, commit, f))
        index = Index(
            settings.repo,
            settings.ref,
            commit,
            settings.methods,
            settings.dominant_share,
            tuple(weeks),
        )
        ctx.out.write_json(INDEX, index.to_json())
        days = [d for w in weeks for d in (w.crawled_from, w.crawled_to) if d is not None]
        if days:
            ctx.out.set_window(min(days), max(days))
            ctx.out.set_data_date(max(days))
        ctx.log.info(
            "%s: %d weeks at %s (%s), %d sites in the newest",
            self.name,
            len(weeks),
            commit[:12],
            ", ".join(w.week for w in weeks),
            weeks[-1].sites if weeks else 0,
        )

    def _fetch_week(
        self, ctx: FetchContext, settings: Settings, commit: str, f: WeekFile
    ) -> WeekEntry:
        if f.size > MAX_FILE_BYTES:
            raise ValueError(f"{f.path}: {f.size} bytes, over the {MAX_FILE_BYTES}-byte limit")
        target = ctx.raw.file(f"weekly/{f.week}.jsonl.gz")
        result = ctx.fetcher.get(raw_url(settings, commit, f.path), to=target)
        _record(ctx, result)
        if result.size != f.size or git_blob_sha1(target) != f.blob:
            raise ValueError(
                f"{f.path}: the download does not match blob {f.blob} at {commit[:12]}"
            )
        with gzip.open(target, "rb") as fh:
            found = extract_week(fh, settings, f.path)
        name = week_extract(f.week)
        ctx.out.write_jsonl(name, found.rows)
        ctx.log.info(
            "%s: %s: %d rows, %d counted sites", self.name, f.week, found.lines, len(found.rows)
        )
        return WeekEntry(
            week=f.week,
            path=f.path,
            blob=f.blob,
            bytes=f.size,
            commit=commit,
            extract=name,
            lines=found.lines,
            sites=len(found.rows),
            status=found.status,
            crawled_from=found.crawled_from,
            crawled_to=found.crawled_to,
        )

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One Observation per (week, name, category, method) (module doc)."""
        settings = _settings(ctx.settings)
        index = Index.from_json(ctx.snapshot.load_json(INDEX))
        if settings.dominant_share < index.dominant_share:
            ctx.log.warning(
                "%s: dominant_share %s is below the extracts' %s; smaller shares were not kept",
                self.name,
                settings.dominant_share,
                index.dominant_share,
            )
        share, generic = settings.dominant_share, frozenset(settings.generic_names)
        weeks = [
            (entry, _sites(ctx.snapshot.iter_jsonl(entry.extract), entry.extract, share, generic))
            for entry in sorted(index.weeks, key=lambda w: w.week)
        ]
        spelling = Spelling.of(s for _, sites in weeks for s in sites)
        for entry, sites in weeks:
            yield from week_observations(entry, sites, spelling)


COLLECTOR = FontsOverTime()
