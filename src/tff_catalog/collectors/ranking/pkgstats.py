"""Ranking collector "pkgstats": Arch Linux package popularity by month (design-m1 §2.4).

**Source.** ``pkgstats.archlinux.de/api/packages``: for one month, every
package that at least 16 reporting systems had installed, with ``count`` (the
reports that month that include the package) and ``samples`` (the month's
largest ``count``, which the server uses as the number of reporting systems).
AUR packages are included. No data license is stated; aggregate counts are
facts (ruling T1: fixtures may carry trimmed real rows with a notice). The
server rounds ``popularity`` to two decimals, so it is ignored: the share is
``count / samples``, computed downstream.

**Fetch.** Only complete months (``complete_months``): a month counts once
``settle_days`` have passed since it ended, because the API lets shared caches
keep a month's answer until the next month starts, and then serve it stale for
up to a day. The API answers for the current, partial month too, so a month
is requested only once it has also settled by today's date (``clock``): a run
date in the future fails before any request, instead of storing a partial
month that later runs would copy for a year. The snapshot holds the last
``months`` complete months, one
extract each, ``month-YYYY-MM.csv.gz`` (``name,count,samples``, one row per
package, sorted by name). A month the previous snapshot already holds (same
extract format) is copied (git stores the bytes once); only the others are
requested, each as ``startMonth=endMonth=YYYYMM`` (one month at a time: a
multi-month request pools the months, which overweights recent ones) in pages
of ``page_size``. The API orders by ``count DESC, name ASC``, so paging is
stable. The pages are streamed to ``ctx.raw`` and never kept. A month is
checked before it is written:

- every page names the month asked for and the same ``total``, and together
  they hold exactly ``total`` rows, with no package twice;
- every row's ``count`` is at most its ``samples``, and all rows share one
  ``samples``;
- its row count and ``samples`` are at least ``min_share`` of the month
  before's, when that month is in the snapshot too (a broken or cut-off answer).

The manifest's ``window`` runs from the first day of the earliest month to the
last day of the latest, which is also its ``data_date``.

**Parse** (offline, pure). For every row of every month extract: an
``Observation`` keyed ``arch-pkg:<name>``, series ``YYYY-MM``, value the
``count``, unit ``installs``, the month's first and last day as its window, and
attrs ``month`` and ``samples`` (records.RESERVED_ATTRS). Every package is
emitted, fonts or not: the correction stage needs the installs of the packages
that pull fonts in, and mapping decides which keys are fonts.
"""

import csv
import io
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar
from urllib.parse import urlencode

from tff_catalog import clock
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchResult
from tff_catalog.records import Observation, Record, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "pkgstats"
HOST = "pkgstats.archlinux.de"
LIST_PATH = "/api/packages"
NAMESPACE = "arch-pkg"
UNIT = "installs"
HEADER = ("name", "count", "samples")
MAX_LIMIT = 10_000  # the API's largest page
MAX_OFFSET = 100_000  # the API refuses a larger offset
MAX_MONTHS = 24  # about 250 KB a month and growing: 6 MB, inside the store's 10 MB per snapshot

# Defaults of the Settings, which config/sources/pkgstats.toml spells out.
MONTHS = 12  # ranking.toml [sources.arch] months: the mean of 12 complete monthly shares
SETTLE_DAYS = 2  # stale-while-revalidate (1 day) plus the server's time zone, rounded up
PAGE_SIZE = MAX_LIMIT
MIN_SHARE = 0.5  # in 2025-26 a month fell at most 19% below the one before; half is broken

_EXTRACT_RE = re.compile(r"^month-([0-9]{4}-[0-9]{2})\.csv\.gz$")
_MONTH_RE = re.compile(r"^([0-9]{4})-([0-9]{2})$")


# --- months ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Month:
    """A calendar month; ``str()`` gives ``YYYY-MM``, the series name."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12 or not 1 <= self.year <= 9999:
            raise ValueError(f"not a month: {self.year}-{self.month}")

    @classmethod
    def of(cls, day: date) -> Month:
        return cls(day.year, day.month)

    @classmethod
    def parse(cls, text: str) -> Month:
        """``YYYY-MM``; ``ValueError`` otherwise."""
        m = _MONTH_RE.fullmatch(text)
        if m is None:
            raise ValueError(f"not a month (YYYY-MM): {text!r}")
        return cls(int(m[1]), int(m[2]))

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def api(self) -> int:
        """The API's form, ``YYYYMM`` as an integer."""
        return self.year * 100 + self.month

    def shift(self, n: int) -> Month:
        """The month ``n`` months later (earlier when negative)."""
        year, index = divmod(self.year * 12 + self.month - 1 + n, 12)
        return Month(year, index + 1)

    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    def last_day(self) -> date:
        return self.shift(1).first_day() - timedelta(days=1)

    @property
    def extract(self) -> str:
        """The month's extract name, ``month-YYYY-MM.csv.gz``."""
        return f"month-{self}.csv.gz"


def complete_months(run_date: date, months: int, settle_days: int) -> tuple[Month, ...]:
    """The last ``months`` complete months on ``run_date``, oldest first.

    A month is complete once ``settle_days`` whole days have passed since it
    ended: on 2026-10-03 with ``settle_days = 2``, September 2026 is the latest.
    """
    current = Month.of(run_date)
    latest = current.shift(-1)
    if run_date < settled_on(latest, settle_days):
        latest = latest.shift(-1)
    return tuple(latest.shift(-i) for i in reversed(range(months)))


def settled_on(month: Month, settle_days: int) -> date:
    """The first day on which ``month`` counts as complete."""
    return month.shift(1).first_day() + timedelta(days=settle_days)


def check_settled(month: Month, today: date, settle_days: int) -> None:
    """Raise ``ValueError`` unless ``month`` is complete by ``today`` (the clock's date).

    The API serves a month while it is still running, so this guards the
    stored data against a run date later than the real date.
    """
    ready = settled_on(month, settle_days)
    if today < ready:
        raise ValueError(
            f"{month} is not complete until {ready}, and today is {today}: "
            "is the run date in the future?"
        )


def month_extracts(snapshot: Snapshot) -> list[tuple[Month, str]]:
    """The snapshot's month extracts, oldest first; ``ValueError`` when it has none."""
    found = []
    for entry in snapshot.manifest.extracts:
        m = _EXTRACT_RE.fullmatch(entry.path)
        if m is not None:
            found.append((Month.parse(m[1]), entry.path))
    if not found:
        raise ValueError(f"{snapshot.source} {snapshot.date}: no month-YYYY-MM.csv.gz extracts")
    return sorted(found)


# --- settings ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/pkgstats.toml``."""

    months: int = MONTHS  # complete months the snapshot holds
    settle_days: int = SETTLE_DAYS  # days after a month ends before it counts as complete
    page_size: int = PAGE_SIZE  # rows per request, 1 to 10,000
    min_share: float = MIN_SHARE  # of the month before's rows and samples, or the fetch fails

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not 1 <= self.months <= MAX_MONTHS:
            raise ConfigError(f"{where}.months: must be between 1 and {MAX_MONTHS}")
        if not 0 <= self.settle_days <= 27:
            raise ConfigError(f"{where}.settle_days: must be between 0 and 27")
        if not 1 <= self.page_size <= MAX_LIMIT:
            raise ConfigError(f"{where}.page_size: must be between 1 and {MAX_LIMIT}")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- the API -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Row:
    """One package in one month."""

    name: str
    count: int
    samples: int


@dataclass(frozen=True, slots=True)
class Page:
    """One answer of the list endpoint."""

    total: int  # rows in the whole month
    rows: tuple[Row, ...]


def list_url(month: Month, limit: int, offset: int) -> str:
    """The list request for one month, one page."""
    query = urlencode(
        {"startMonth": month.api, "endMonth": month.api, "limit": limit, "offset": offset}
    )
    return f"https://{HOST}{LIST_PATH}?{query}"


def _count(value: object, what: str, *, least: int = 0) -> int:
    # type() rather than isinstance: true must not pass as a count.
    if type(value) is not int or value < least:
        raise ValueError(f"{what}: expected an integer >= {least}, got {value!r}")
    return value


def _row(item: object, month: Month, where: str) -> Row:
    if not isinstance(item, Mapping):
        raise ValueError(f"{where}: not an object")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where}: no package name")
    count = _count(item.get("count"), f"{where} ({name}).count")
    samples = _count(item.get("samples"), f"{where} ({name}).samples", least=1)
    if count > samples:
        raise ValueError(f"{where} ({name}): count {count} is above samples {samples}")
    for key in ("startMonth", "endMonth"):
        if item.get(key) != month.api:
            raise ValueError(f"{where} ({name}): {key} is {item.get(key)!r}, asked for {month.api}")
    return Row(name, count, samples)


def read_page(doc: object, month: Month, *, limit: int, offset: int) -> Page:
    """One page of the list endpoint for ``month``; ``ValueError`` on anything unexpected."""
    where = f"{month} offset {offset}"
    if not isinstance(doc, Mapping):
        raise ValueError(f"{where}: expected an object")
    items = doc.get("packagePopularities")
    if not isinstance(items, list):
        raise ValueError(f"{where}: no packagePopularities list")
    total = _count(doc.get("total"), f"{where}: total")
    if _count(doc.get("count"), f"{where}: count") != len(items):
        raise ValueError(f"{where}: count is {doc.get('count')}, the page has {len(items)} rows")
    if doc.get("offset") != offset or doc.get("limit") != limit:
        raise ValueError(
            f"{where}: answered offset {doc.get('offset')!r} and limit {doc.get('limit')!r}, "
            f"asked for {offset} and {limit}"
        )
    rows = tuple(_row(item, month, f"{where} row {i}") for i, item in enumerate(items))
    return Page(total, rows)


def check_month(rows: Sequence[Row], month: Month) -> None:
    """Raise ``ValueError`` unless ``rows`` are one month's list: unique names, one samples."""
    if not rows:
        raise ValueError(f"{month}: no packages")
    twice = sorted(n for n, k in Counter(r.name for r in rows).items() if k > 1)
    if twice:
        raise ValueError(f"{month}: packages listed twice: {', '.join(twice[:5])}")
    samples = {r.samples for r in rows}
    if len(samples) != 1:
        raise ValueError(f"{month}: rows disagree on samples: {sorted(samples)[:5]}")


@dataclass(frozen=True, slots=True)
class MonthStats:
    """What the shrink check compares between neighbouring months."""

    rows: int
    samples: int

    @classmethod
    def of(cls, rows: Sequence[Row]) -> MonthStats:
        return cls(len(rows), max((r.samples for r in rows), default=0))


def check_shrink(month: Month, stats: MonthStats, before: MonthStats | None, share: float) -> None:
    """Raise ``ValueError`` when the month has under ``share`` of the month before's
    rows or samples (a cut-off or broken answer)."""
    if before is None:
        return
    for what, now, then in (
        ("rows", stats.rows, before.rows),
        ("samples", stats.samples, before.samples),
    ):
        if now < share * then:
            raise ValueError(
                f"{month}: {now} {what}, down from {then} the month before "
                f"(below min_share = {share}); a broken answer?"
            )


# --- the extract -------------------------------------------------------------------------------


def month_csv(rows: Sequence[Row]) -> bytes:
    """The extract of one month: a header, then one row per package, sorted by name."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for r in sorted(rows):
        writer.writerow((r.name, r.count, r.samples))
    return buffer.getvalue().encode("utf-8")


def _int(text: str, what: str) -> int:
    if not text.isascii() or not text.isdigit():
        raise ValueError(f"{what}: not a count: {text!r}")
    return int(text)


def read_month_csv(data: bytes, what: str = "month") -> list[Row]:
    """The rows of a month extract, in file order; ``ValueError`` if it is malformed."""
    lines = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
    header = next(lines, None)
    if tuple(header or ()) != HEADER:
        raise ValueError(f"{what}: header is {header!r}, expected {','.join(HEADER)}")
    rows = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=2):
        if len(line) != len(HEADER):
            raise ValueError(f"{what} line {number}: expected {len(HEADER)} fields, got {line!r}")
        name, count, samples = line
        if not name or name in seen:
            raise ValueError(f"{what} line {number}: empty or repeated package name {name!r}")
        seen.add(name)
        row = Row(
            name, _int(count, f"{what} line {number}"), _int(samples, f"{what} line {number}")
        )
        if row.samples < 1 or row.count > row.samples:
            raise ValueError(f"{what} line {number}: count {row.count} of samples {row.samples}")
        rows.append(row)
    return rows


# --- the collector -----------------------------------------------------------------------------


def _record(ctx: FetchContext, result: FetchResult) -> None:
    for record in result.to_records(kept=False):
        ctx.out.record_fetch(record)


def _reusable(previous: Snapshot | None, version: int) -> Snapshot | None:
    """The previous snapshot when its month extracts have this version's format."""
    if previous is not None and previous.manifest.collector_version == version:
        return previous
    return None


def _describe(months: Sequence[Month]) -> str:
    """``1 month: 2026-08`` or ``11 months: 2025-09 to 2026-07``."""
    if len(months) == 1:
        return f"1 month: {months[0]}"
    return f"{len(months)} months: {months[0]} to {months[-1]}"


class Pkgstats(CollectorBase):
    """Arch Linux pkgstats: installs per package and complete month."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (HOST,)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "arch"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Copy the complete months the previous snapshot holds; fetch the others.

        ``ValueError`` before any request when a month to fetch has not settled
        by today's date (a future run date); after a request, when an answer
        fails the checks in the module docstring.
        """
        settings = _settings(ctx.settings)
        months = complete_months(ctx.run_date, settings.months, settings.settle_days)
        previous = _reusable(ctx.previous, self.version)
        held = {m for m in months if previous is not None and previous.has(m.extract)}
        wanted = [m for m in months if m not in held]
        if wanted:  # months are oldest first, so the newest one wanted decides
            check_settled(wanted[-1], clock.utc_today(), settings.settle_days)
        stats: dict[Month, MonthStats] = {}
        copied: list[Month] = []
        fetched: list[Month] = []
        for i, month in enumerate(months):
            if previous is not None and month in held:
                ctx.out.copy_extract(previous, month.extract)
                copied.append(month)
                continue
            rows = self._fetch_month(ctx, month, settings)
            stats[month] = MonthStats.of(rows)
            before = self._stats(months[i - 1], stats, previous) if i > 0 else None
            check_shrink(month, stats[month], before, settings.min_share)
            ctx.out.write_bytes(month.extract, month_csv(rows), rows=len(rows))
            fetched.append(month)
        if copied and previous is not None:
            ctx.out.note(f"copied from the {previous.date} snapshot: {_describe(copied)}")
        if fetched:
            ctx.out.note(f"fetched {_describe(fetched)}")
        ctx.out.set_window(months[0].first_day(), months[-1].last_day())
        ctx.out.set_data_date(months[-1].last_day())
        ctx.log.info(
            "%s: %s; %d fetched, %d copied", self.name, _describe(months), len(fetched), len(copied)
        )

    def _fetch_month(self, ctx: FetchContext, month: Month, settings: Settings) -> list[Row]:
        """Every page of one month, checked (module docstring)."""
        rows: list[Row] = []
        total: int | None = None
        offset = 0
        while total is None or offset < total:
            url = list_url(month, settings.page_size, offset)
            result = ctx.fetcher.get(url, to=ctx.raw.file(f"{month}/offset-{offset:06d}.json"))
            _record(ctx, result)
            page = read_page(result.json(), month, limit=settings.page_size, offset=offset)
            if total is None:
                total = page.total
                last = max(total - 1, 0) // settings.page_size * settings.page_size
                if last > MAX_OFFSET:  # fail now, not after the pages the API does serve
                    raise ValueError(
                        f"{month}: {total} packages need offset {last}, "
                        f"past the API's limit of {MAX_OFFSET}"
                    )
            elif page.total != total:
                raise ValueError(f"{month}: total went from {total} to {page.total} while paging")
            expected = min(settings.page_size, total - offset)
            if len(page.rows) != expected:
                raise ValueError(
                    f"{month} offset {offset}: {len(page.rows)} rows, expected {expected} of {total}"
                )
            rows.extend(page.rows)
            offset += settings.page_size
        check_month(rows, month)
        ctx.log.info(
            "%s: %s, %d packages, %d samples", self.name, month, len(rows), rows[0].samples
        )
        return rows

    @staticmethod
    def _stats(
        month: Month, stats: Mapping[Month, MonthStats], previous: Snapshot | None
    ) -> MonthStats | None:
        """The stats of a month fetched this run, or read from the snapshot it was copied from."""
        if month in stats:
            return stats[month]
        if previous is not None and previous.has(month.extract):
            return MonthStats.of(read_month_csv(previous.read_bytes(month.extract), month.extract))
        return None

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One Observation per package and month (module docstring)."""
        for month, name in month_extracts(ctx.snapshot):
            rows = read_month_csv(ctx.snapshot.read_bytes(name), name)
            if not rows:
                ctx.log.warning("%s: %s lists no packages", self.name, name)
            series, start, end = str(month), month.first_day(), month.last_day()
            for row in rows:
                yield Observation(
                    source=NAME,
                    series=series,
                    key=SourceKey(NAMESPACE, row.name),
                    value=float(row.count),
                    unit=UNIT,
                    start=start,
                    end=end,
                    attrs=attrs(month=series, samples=row.samples),
                )


COLLECTOR = Pkgstats()
