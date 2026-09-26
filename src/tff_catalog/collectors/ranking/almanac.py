"""Ranking collector "almanac": the Web Almanac's Fonts sheet (design-m1 §2.4, group http_archive).

**Source.** The HTTP Archive Web Almanac publishes each chapter's query results
as a Google Sheet. The 2025 Fonts chapter's sheet ("Fonts 2025",
``1otdu4p_…``) comes from the July 2025 crawl (``date = '2025-07-01'`` in its
SQL). The chapter is "Licensed under Apache 2.0" (ruling T1: raw counts may be
published; fixtures may carry trimmed real rows with the notice). Two tabs are
read, both top-100 lists whose family names went through the Almanac's
``FAMILY_INNER`` regex (it folds Condensed, Narrow, Black and weight suffixes
into the parent, and every "Font Awesome" name into one):

- ``pages`` (gid 1668708562, "Which families are popular in CSS?",
  normalisation Pages): ``client,family,count,total,proportion``, the root
  pages whose CSS declares an ``@font-face`` for the family, top 100 per
  client. Gate M6 (a): this is the engine's term (``config/ranking.toml``
  series ``pages/mobile``).
- ``services`` (gid 1594814478, "Which families are used broken down by
  service?", normalisation "Requests (parsed only)"):
  ``client,service,family,count,total,proportion,rank``, font requests by the
  name table's family, top 100 per client and service. Gate M6 (a): it adds no
  term; the correction stage reads it only to flag parent merges.

Settings pin the edition, sheet id, crawl date and both tabs (gid, question,
normalisation and columns), so a new edition is one config change
(methodology §10).

**Fetch.** One CSV export per tab,
``docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>``. Google
answers with a 307 to a signed URL on a ``doc-*-sheets`` host (``EXPORT_HOST``,
a fetcher hosts pattern); the fetcher follows it and the manifest lists both URLs. The raw export goes to ``ctx.raw``. It opens
with ``Section``, ``Question`` and ``Normalization`` rows and a blank row, then
the header row, found as the first row whose first cell is the tab's first
configured column (``client``). Pivot
tables sit to the right in the same rows, so every row is cut at the header's
first empty cell. The question, normalisation and header must equal the
settings'; every row needs a client (and a service), and whole-number
``count`` and ``total`` (thousands commas allowed); each configured client
needs at least ``min_rows`` rows; and when the previous snapshot holds the same
sheet and tab, a tab with fewer than ``min_share`` of its rows fails the fetch
(a broken export), so the stale policy keeps the last good snapshot. The
extracts are the two trimmed CSVs, ``pages.csv`` and ``services.csv`` (header
plus data rows, cells as the sheet shows them), and ``sheet.json`` (edition,
sheet id, crawl date, each tab's gid, preamble and row count). The manifest's
``data_date`` and window are the crawl date. A note says whether each tab
changed since the previous snapshot of the same sheet.

**Parse** (offline, pure; dates come from ``sheet.json``, never from the
settings, so an old snapshot parses the same after an edition switch). One
``Observation`` per family name and list, keyed ``almanac-name:<family>``:

- pages: series ``pages/<client>``, unit ``pages``, value ``count``;
- services: series ``requests/<client>/<service>``, unit ``requests``,
  value ``count``;
- ``start`` = ``end`` = the crawl date; attrs ``total`` (the list's
  denominator, as given) and ``rank`` (1 + the rows of the same list with a
  larger count, empty names included; the services tab's own ``rank`` column
  breaks ties arbitrarily, so it is not used).

Rows with an empty family (names the regex emptied, or names under 3
characters) are skipped. Names that differ only in case or spacing
("Roboto"/"roboto", and the services tab's exact repeats) are one family on
largely the same pages, so they fold into one observation at the **largest**
count, never the sum (ruling M6; design-m1 §8), keyed by the spelling with that
count (ties: the first in code-point order), with attr ``folded_rows`` giving
how many rows were folded.
"""

import csv
import io
import logging
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar, Literal
from urllib.parse import urlencode

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchError, FetchResult, HostNotAllowed
from tff_catalog.records import Observation, Record, SourceKey, Unit, attrs
from tff_catalog.store import Snapshot

NAME = "almanac"
SHEETS_HOST = "docs.google.com"
# The export's 307 goes to a signed URL on a numbered host (doc-04-7g-sheets on
# 2026-09-25 and 2026-09-26), which may differ on another network, such as an Actions
# runner. A fetcher pattern admits any doc-*-sheets host there, and no other
# googleusercontent.com host (fetch.host_allowed).
EXPORT_HOST = "doc-*-sheets.googleusercontent.com"
NAMESPACE = "almanac-name"
SHEET = "sheet.json"
SHEET_SCHEMA = 1
HEADER_CELL = "client"  # the first cell of each 2025 tab's header row (trim_export's default)

TabName = Literal["pages", "services"]
TABS: tuple[TabName, ...] = ("pages", "services")
EXTRACTS: dict[TabName, str] = {"pages": "pages.csv", "services": "services.csv"}
UNITS: dict[TabName, Unit] = {"pages": "pages", "services": "requests"}
# Columns parse reads; the settings' `columns` must include them.
REQUIRED: dict[TabName, tuple[str, ...]] = {
    "pages": ("client", "family", "count", "total"),
    "services": ("client", "service", "family", "count", "total"),
}

# Defaults of the Settings, which config/sources/almanac.toml spells out (the 2025 edition).
EDITION = 2025
SHEET_ID = "1otdu4p_CCI70B4FVzw6k02frStsPMrQoFu7jUim_0Bg"
CRAWL_DATE = date(2025, 7, 1)
CLIENTS = ("desktop", "mobile")
MIN_ROWS = 10  # per client and tab: fewer is a broken export (the sheets list 100)
MIN_SHARE = 0.9  # of the previous snapshot's rows for the same sheet and tab

_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_COUNT_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)$")


@dataclass(frozen=True, slots=True)
class Tab:
    """One tab of the sheet, as ``[pages]`` or ``[services]`` in the settings."""

    gid: int
    question: str  # the tab's "Question" row, checked on fetch
    normalization: str  # the tab's "Normalization" row, checked on fetch
    columns: tuple[str, ...]  # the header row, cut at its first empty cell


PAGES_TAB = Tab(
    gid=1668708562,
    question="Which families are popular in CSS?",
    normalization="Pages",
    columns=("client", "family", "count", "total", "proportion"),
)
SERVICES_TAB = Tab(
    gid=1594814478,
    question="Which families are used broken down by service?",
    normalization="Requests (parsed only)",
    columns=("client", "service", "family", "count", "total", "proportion", "rank"),
)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/almanac.toml``: one edition of the sheet."""

    edition: int = EDITION
    sheet_id: str = SHEET_ID
    crawl_date: date = CRAWL_DATE  # the HTTP Archive crawl the sheet's SQL reads
    clients: tuple[str, ...] = CLIENTS  # each must have at least min_rows rows per tab
    min_rows: int = MIN_ROWS
    min_share: float = MIN_SHARE
    pages: Tab = PAGES_TAB
    services: Tab = SERVICES_TAB

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not _SHEET_ID_RE.fullmatch(self.sheet_id):
            raise ConfigError(
                f"{where}.sheet_id: expected a Google Sheets id, got {self.sheet_id!r}"
            )
        if self.crawl_date.year != self.edition:
            raise ConfigError(
                f"{where}.crawl_date: {self.crawl_date} is not in edition {self.edition}; "
                "switch the edition, sheet id, crawl date and tabs together"
            )
        if not self.clients or len(set(self.clients)) != len(self.clients):
            raise ConfigError(f"{where}.clients: expected distinct client names")
        for client in self.clients:
            if not _plain(client):
                raise ConfigError(f"{where}.clients: {client!r} must be non-empty, without '/'")
        if self.min_rows < 1:
            raise ConfigError(f"{where}.min_rows: must be at least 1")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")
        if self.pages.gid == self.services.gid:
            raise ConfigError(f"{where}: the pages and services tabs have the same gid")
        for name in TABS:
            _check_tab(self.tab(name), name, f"{where}.{name}")

    def tab(self, name: TabName) -> Tab:
        """The settings of tab ``name``."""
        return self.pages if name == "pages" else self.services


def _check_tab(tab: Tab, name: TabName, where: str) -> None:
    if tab.gid < 0:
        raise ConfigError(f"{where}.gid: must not be negative")
    if not tab.question.strip() or not tab.normalization.strip():
        raise ConfigError(f"{where}: question and normalization must not be empty")
    blank = any(not c or c != c.strip() for c in tab.columns)
    if blank or len(set(tab.columns)) != len(tab.columns):
        raise ConfigError(f"{where}.columns: expected distinct, non-empty names")
    missing = [c for c in REQUIRED[name] if c not in tab.columns]
    if missing:
        raise ConfigError(f"{where}.columns: lacks {', '.join(missing)}")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


def _plain(text: str) -> bool:
    """A client or service name usable inside a series: non-empty, no ``/``."""
    return bool(text) and "/" not in text and text == text.strip()


def export_url(sheet_id: str, gid: int) -> str:
    """The CSV export of one tab."""
    query = urlencode({"format": "csv", "gid": gid})
    return f"https://{SHEETS_HOST}/spreadsheets/d/{sheet_id}/export?{query}"


def to_count(text: str) -> int:
    """A whole number as the sheet shows it (``"2,758,095"``); ``ValueError`` otherwise."""
    cleaned = text.strip()
    if not _COUNT_RE.fullmatch(cleaned):
        raise ValueError(f"not a whole number: {text!r}")
    return int(cleaned.replace(",", ""))


# --- the export --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Export:
    """One tab's CSV export, trimmed: the preamble, the header and the data rows."""

    preamble: tuple[tuple[str, str], ...]  # ("Section", "Design"), ("Question", …), …
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def preamble_value(self, label: str) -> str | None:
        """The second cell of the (last) preamble row labelled ``label``, if there is one."""
        return dict(self.preamble).get(label)


def trim_export(text: str, first: str = HEADER_CELL) -> Export:
    """Find the header row (first cell ``first``) and cut every row at its first empty cell.

    ``fetch`` passes the tab's first configured column, so an edition whose
    header starts elsewhere is one config change (methodology §10). Rows
    before the header with a first cell are the preamble; rows after it that
    are empty once cut (pivot-table rows, blank lines) are dropped. Raises
    ``ValueError`` when there is no header row (an error page, say).
    """
    table = list(csv.reader(io.StringIO(text, newline="")))
    at = next((i for i, row in enumerate(table) if row and row[0].strip() == first), None)
    if at is None:
        raise ValueError(f"no header row (a row whose first cell is {first!r})")
    raw_header = [cell.strip() for cell in table[at]]
    width = raw_header.index("") if "" in raw_header else len(raw_header)
    preamble = tuple(
        (row[0].strip(), row[1].strip() if len(row) > 1 else "")
        for row in table[:at]
        if row and row[0].strip()
    )
    rows = []
    for row in table[at + 1 :]:
        cells = tuple(cell.strip() for cell in [*row[:width], *[""] * (width - len(row))])
        if any(cells):
            rows.append(cells)
    return Export(preamble=preamble, header=tuple(raw_header[:width]), rows=tuple(rows))


def check_export(export: Export, tab: Tab, name: TabName, settings: Settings) -> None:
    """Raise ``ValueError`` unless ``export`` is the tab the settings describe, well formed.

    Checks the question, the normalisation and the header, then every row: a
    client (and service) usable in a series, and whole-number count and total;
    then at least ``min_rows`` rows for each configured client.
    """
    where = f"{name} tab (gid {tab.gid})"
    for label, want in (("Question", tab.question), ("Normalization", tab.normalization)):
        got = export.preamble_value(label)
        if got != want:
            raise ValueError(f"{where}: {label} is {got!r}, expected {want!r}")
    if export.header != tab.columns:
        raise ValueError(
            f"{where}: columns {list(export.header)}, expected {list(tab.columns)}; "
            f"update [{name}] columns in config/sources/{NAME}.toml if the sheet changed"
        )
    index = {c: i for i, c in enumerate(export.header)}
    per_client: dict[str, int] = defaultdict(int)
    for n, row in enumerate(export.rows, start=1):
        for column in ("client", "service"):
            if column in index and not _plain(row[index[column]]):
                raise ValueError(f"{where}: row {n} has a bad {column} {row[index[column]]!r}")
        for column in ("count", "total"):
            try:
                to_count(row[index[column]])
            except ValueError as exc:
                raise ValueError(f"{where}: row {n}, {column}: {exc}") from None
        per_client[row[index["client"]]] += 1
    for client in settings.clients:
        if per_client[client] < settings.min_rows:
            raise ValueError(
                f"{where}: {per_client[client]} rows for client {client!r}, "
                f"fewer than min_rows = {settings.min_rows}"
            )


def csv_bytes(header: Sequence[str], rows: Sequence[Sequence[str]]) -> bytes:
    """``header`` and ``rows`` as UTF-8 CSV with ``\\n`` line ends (the extract format)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


# --- sheet.json ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SheetInfo:
    """What ``sheet.json`` records about the edition a snapshot holds."""

    edition: int
    sheet_id: str
    crawl_date: date
    gids: tuple[tuple[str, int], ...]  # (tab, gid), in TABS order

    def gid(self, name: str) -> int | None:
        return dict(self.gids).get(name)

    @classmethod
    def from_json(cls, doc: object) -> SheetInfo:
        """Read ``sheet.json``; ``ValueError`` on anything else."""
        if not isinstance(doc, dict) or doc.get("schema") != SHEET_SCHEMA:
            raise ValueError(f"{SHEET}: not a schema-{SHEET_SCHEMA} sheet record")
        edition, sheet_id, tabs = doc.get("edition"), doc.get("sheet_id"), doc.get("tabs")
        if type(edition) is not int or not isinstance(sheet_id, str) or not sheet_id:
            raise ValueError(f"{SHEET}: bad edition or sheet_id")
        crawl = doc.get("crawl_date")
        try:
            crawl_date = date.fromisoformat(crawl) if isinstance(crawl, str) else None
        except ValueError:
            crawl_date = None
        if crawl_date is None:
            raise ValueError(f"{SHEET}: crawl_date {crawl!r} is not an ISO date")
        if not isinstance(tabs, dict):
            raise ValueError(f"{SHEET}: tabs must be an object")
        gids = []
        for name in TABS:
            tab = tabs.get(name)
            gid = tab.get("gid") if isinstance(tab, dict) else None
            if type(gid) is not int:
                raise ValueError(f"{SHEET}: tab {name} has no gid")
            gids.append((name, gid))
        return cls(edition, sheet_id, crawl_date, tuple(gids))


def sheet_doc(settings: Settings, exports: Mapping[TabName, Export]) -> dict[str, Any]:
    """The ``sheet.json`` extract: the edition, and each tab's gid, preamble and row count."""
    tabs: dict[str, Any] = {}
    for name in TABS:
        export, tab = exports[name], settings.tab(name)
        tabs[name] = {
            "gid": tab.gid,
            "preamble": dict(export.preamble),
            "rows": len(export.rows),
        }
    return {
        "schema": SHEET_SCHEMA,
        "edition": settings.edition,
        "sheet_id": settings.sheet_id,
        "crawl_date": settings.crawl_date.isoformat(),
        "tabs": tabs,
    }


def same_sheet(previous: Snapshot | None, settings: Settings, name: TabName) -> Snapshot | None:
    """``previous`` when it holds tab ``name`` of the same sheet, else None."""
    if previous is None or not previous.has(SHEET) or not previous.has(EXTRACTS[name]):
        return None
    try:
        info = SheetInfo.from_json(previous.load_json(SHEET))
    except ValueError:
        return None
    if info.sheet_id != settings.sheet_id or info.gid(name) != settings.tab(name).gid:
        return None
    return previous


def check_shrink(rows: int, name: TabName, previous: Snapshot | None, min_share: float) -> None:
    """Raise ``ValueError`` when a tab lost more than ``1 - min_share`` of the previous rows."""
    if previous is None:
        return
    entry = previous.manifest.extract(EXTRACTS[name])
    before = entry.rows if entry is not None else None
    if before is not None and rows < min_share * before:
        raise ValueError(
            f"{name} tab: {rows} rows, down from {before} in the {previous.date} snapshot "
            f"of the same sheet (below min_share = {min_share}); a broken export?"
        )


# --- parse ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Row:
    series: str
    family: str
    count: int
    total: int


def fold_key(name: str) -> str:
    """Names that differ only in case or spacing fold together (ruling M6: max, not sum)."""
    return " ".join(name.split()).casefold()


def read_table(snapshot: Snapshot, name: str) -> tuple[tuple[str, ...], list[list[str]]]:
    """The header and rows of a CSV extract."""
    text = snapshot.read_bytes(name).decode("utf-8")
    table = list(csv.reader(io.StringIO(text, newline="")))
    if not table:
        raise ValueError(f"{name}: empty")
    return tuple(table[0]), table[1:]


def _series(name: TabName, row: Sequence[str], index: Mapping[str, int]) -> str:
    client = row[index["client"]]
    if name == "pages":
        return f"pages/{client}"
    return f"requests/{client}/{row[index['service']]}"


def list_rows(name: TabName, header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[_Row]:
    """Every row of one tab as (series, family, count, total), empty names included."""
    index = {c: i for i, c in enumerate(header)}
    missing = [c for c in REQUIRED[name] if c not in index]
    if missing:
        raise ValueError(f"{EXTRACTS[name]}: lacks column(s) {', '.join(missing)}")
    out = []
    for n, row in enumerate(rows, start=2):
        if len(row) != len(header):
            raise ValueError(f"{EXTRACTS[name]}:{n}: {len(row)} cells, expected {len(header)}")
        for column in ("client", "service"):
            if column in index and not _plain(row[index[column]]):
                raise ValueError(f"{EXTRACTS[name]}:{n}: bad {column} {row[index[column]]!r}")
        out.append(
            _Row(
                series=_series(name, row, index),
                family=row[index["family"]].strip(),
                count=to_count(row[index["count"]]),
                total=to_count(row[index["total"]]),
            )
        )
    return out


def ranks(rows: Sequence[_Row]) -> list[int]:
    """Each row's rank in its list: 1 + the rows of the same series with a larger count."""
    counts: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        counts[r.series].append(r.count)
    return [1 + sum(c > r.count for c in counts[r.series]) for r in rows]


def observations(
    name: TabName,
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    day: date,
    log: logging.Logger | None = None,
) -> list[Observation]:
    """One observation per (list, folded family name) of one tab; see the module docstring."""
    parsed = list_rows(name, header, rows)
    groups: dict[tuple[str, str], list[tuple[_Row, int]]] = defaultdict(list)
    empty = 0
    for row, rank in zip(parsed, ranks(parsed), strict=True):
        if not row.family:
            empty += 1
            continue
        groups[(row.series, fold_key(row.family))].append((row, rank))
    if log is not None and empty:
        log.info("%s: %s: %d rows without a family name skipped", NAME, name, empty)
    out = []
    for members in groups.values():
        row, rank = min(members, key=lambda m: (-m[0].count, m[0].family))
        extra = {"folded_rows": len(members)} if len(members) > 1 else {}
        out.append(
            Observation(
                source=NAME,
                series=row.series,
                key=SourceKey(NAMESPACE, row.family),
                value=float(row.count),
                unit=UNITS[name],
                start=day,
                end=day,
                attrs=attrs(total=row.total, rank=rank, **extra),
            )
        )
    return out


# --- the collector -------------------------------------------------------------------------------


def _record(ctx: FetchContext, result: FetchResult, *, kept: bool) -> None:
    for record in result.to_records(kept=kept):
        ctx.out.record_fetch(record)


class Almanac(CollectorBase):
    """The Web Almanac Fonts sheet: pages per family (the term) and requests per service."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (SHEETS_HOST, EXPORT_HOST)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "http_archive"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Export both tabs, check and trim them, and keep them with ``sheet.json``."""
        settings = _settings(ctx.settings)
        exports = {name: self._fetch_tab(ctx, settings, name) for name in TABS}
        ctx.out.write_json(SHEET, sheet_doc(settings, exports))
        ctx.out.set_data_date(settings.crawl_date)
        ctx.out.set_window(settings.crawl_date, settings.crawl_date)
        ctx.log.info(
            "%s: edition %d, %s",
            self.name,
            settings.edition,
            ", ".join(f"{n} {len(e.rows)} rows" for n, e in exports.items()),
        )

    def _fetch_tab(self, ctx: FetchContext, settings: Settings, name: TabName) -> Export:
        tab = settings.tab(name)
        url = export_url(settings.sheet_id, tab.gid)
        try:
            result = ctx.fetcher.get(url, to=ctx.raw.file(f"{name}-{tab.gid}.csv"))
        except HostNotAllowed as exc:
            raise FetchError(
                f"{url}: {exc}. A redirect to a sign-in host (accounts.google.com) means the "
                "sheet is no longer public; one to a host outside "
                f"{EXPORT_HOST} means Google moved the export: update {__name__}.EXPORT_HOST"
            ) from exc
        _record(ctx, result, kept=False)
        try:
            export = trim_export(result.body().decode("utf-8-sig"), tab.columns[0])
        except ValueError as exc:  # an HTML error page, say
            kind = result.header("content-type") or "no content-type"
            raise ValueError(f"{name} tab (gid {tab.gid}, {kind}): {exc}") from None
        check_export(export, tab, name, settings)
        previous = same_sheet(ctx.previous, settings, name)
        check_shrink(len(export.rows), name, previous, settings.min_share)
        entry = ctx.out.write_bytes(
            EXTRACTS[name], csv_bytes(export.header, export.rows), rows=len(export.rows)
        )
        if previous is not None:
            before = previous.manifest.extract(EXTRACTS[name])
            same = before is not None and before.sha256 == entry.sha256
            ctx.out.note(
                f"{EXTRACTS[name]} {'unchanged' if same else 'changed'} since the "
                f"{previous.date} snapshot of the same sheet"
            )
        return export

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """Observations of both tabs, dated by the crawl ``sheet.json`` records."""
        info = SheetInfo.from_json(ctx.snapshot.load_json(SHEET))
        for name in TABS:
            header, rows = read_table(ctx.snapshot, EXTRACTS[name])
            yield from observations(name, header, rows, info.crawl_date, ctx.log)


COLLECTOR = Almanac()
