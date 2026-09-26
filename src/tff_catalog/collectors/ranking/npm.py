"""Ranking collector "npm": npm downloads of the web-font packages (design-m1 §2.4).

**Source.** The packages of the npm organisations in ``scopes`` (``@fontsource``,
``@fontsource-variable``, ``@expo-google-fonts``), listed by
``registry.npmjs.org/-/org/<org>/package``, and their daily downloads from
``api.npmjs.org/downloads/range/<start>:<end>/<package>``. npm computes each
day once, soon after UTC midnight. Scoped packages can't be asked for in bulk,
so it is one request per package, at the fetcher's 1 a second per host (npm's
crawler policy): about 4,700 requests. Even at that pace npm answered one
request in five to ten with a 429 on 2026-09-26, which the fetcher's retries
absorb, so a full run takes about 90 to 110 minutes. The counts are open data
(ruling T1: raw counts may be published).

**Fetch.**

1. ``downloads/point/last-day`` gives the last day npm has counted, ``end``;
   every range then asks for the same ``history_days`` days ending there, so a
   run that crosses the daily update still reads one window. ``end`` must lie
   between ``MAX_LAG_DAYS`` before the run date and the run date.
2. One org listing per scope. Packages in ``exclude`` (meta packages such as
   ``@expo-google-fonts/dev``) are dropped, and so is any name that is not a
   plain package name of the scope (noted): an org can be given access to
   packages outside its scope. ``max_packages`` > 0 keeps an evenly
   spaced sample of that many per scope, for local runs and tests (noted).
3. One range per package, in name order, streamed to ``ctx.raw``. The answer
   must cover exactly the window, day by day; npm cuts a range longer than 18
   months short without saying so, hence ``MAX_HISTORY_DAYS``. A 404 ("package
   not found": npm has no counts for it yet) is kept as a row without counts.
   A request that still fails after the fetcher's retries, or an answer that
   does not check out, is made once more after all the others; a package that
   fails twice is left out (noted). More than ``max_failures`` failures fail the
   fetch, at once in the first pass (the stale policy then reuses the last
   snapshot).

The design asked for ``point/last-year`` for every package plus a range for the
bigger ones. One range per package gives the same last-year total (checked
against the point API on real data), the monthly sums and the first non-zero
day for every package, in fewer requests.

**Extract** ``downloads.csv.gz``, one row per package, sorted by name:
``package,first_day,last_year,<YYYY-MM>...``, with a column for every calendar
month wholly inside the window (the part-months at its ends are left out).
``first_day`` is the first day with a download (empty when there is none);
``last_year`` sums the last 365 days, npm's ``last-year``. A package npm has no
counts for has only its name. The manifest's ``window`` is the history window
and its ``data_date`` is ``end``. Nothing is copied from ``ctx.previous``: every
package needs its request anyway.

**Parse** (offline, pure). For each row, keyed ``npm:<package>``, unit
``downloads``:

- series ``last-year`` (what ``ranking.toml``'s npm sources read): the
  last-year total over ``end - 364`` to ``end``; ``None`` when npm has no counts;
- series ``YYYY-MM``: each complete month (Rising, the backtest), except months
  that ended before the package's first download (it was not on the channel yet).

Every Observation carries ``first_seen`` (records.RESERVED_ATTRS), the first
day with a download: exposure for the engine (``first_nonzero_day``), which
reads a day on or before a window's start as "before the window". A package
with no download in the window gets ``end + 1``: it is too new to count.
Monthly rows also carry ``month``.
"""

import csv
import io
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchError, FetchResult
from tff_catalog.records import Observation, Record, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "npm"
REGISTRY_HOST = "registry.npmjs.org"
API_HOST = "api.npmjs.org"
LAST_DAY_URL = f"https://{API_HOST}/downloads/point/last-day"
NAMESPACE = "npm"
UNIT = "downloads"
SERIES = "last-year"  # ranking.toml [sources.npm_fontsource] and [sources.npm_expo] series
EXTRACT = "downloads.csv.gz"
HEADER = ("package", "first_day", "last_year")
YEAR_DAYS = 365  # npm's last-year: the last 365 counted days
# Non-bulk ranges cover at most 18 months; longer ones come back cut short. The
# shortest 18 calendar months have 546 days.
MAX_HISTORY_DAYS = 546
MAX_LAG_DAYS = 7  # npm's last counted day may lag the run date by this much at most

# Defaults of the Settings, which config/sources/npm.toml spells out.
SCOPES = ("@fontsource", "@fontsource-variable", "@expo-google-fonts")
EXCLUDE = ("@expo-google-fonts/dev",)  # a meta package that depends on every Expo font
HISTORY_DAYS = 540  # 17 complete months or more: 12-month windows for the backtest
MAX_PACKAGES = 0  # 0: every package
MAX_FAILURES = 10

_SCOPE_RE = re.compile(r"^@[a-z0-9][a-z0-9._~-]*$")
_PACKAGE_RE = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*)/[a-z0-9][a-z0-9._~-]*$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


# --- months and the window ---------------------------------------------------------------------


def month_label(day: date) -> str:
    """``YYYY-MM`` of ``day``: the series name of its month."""
    return f"{day.year:04d}-{day.month:02d}"


def month_bounds(label: str) -> tuple[date, date]:
    """The first and last day of month ``YYYY-MM``; ``ValueError`` otherwise."""
    m = _MONTH_RE.fullmatch(label)
    if m is None:
        raise ValueError(f"not a month (YYYY-MM): {label!r}")
    first = date(int(m[1]), int(m[2]), 1)
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return first, following - timedelta(days=1)


def complete_months(start: date, end: date) -> tuple[str, ...]:
    """The calendar months wholly inside ``start``..``end`` (inclusive), oldest first."""
    out = []
    day = start if start.day == 1 else month_bounds(month_label(start))[1] + timedelta(days=1)
    while True:
        first, last = month_bounds(month_label(day))
        if last > end:
            return tuple(out)
        out.append(month_label(first))
        day = last + timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Window:
    """The days every range of one snapshot covers, ``start`` to ``end`` inclusive."""

    start: date
    end: date  # the last day npm had counted when the fetch began

    def __post_init__(self) -> None:
        if self.days < YEAR_DAYS:
            raise ValueError(f"window {self.start} to {self.end} is under {YEAR_DAYS} days")

    @classmethod
    def ending(cls, end: date, days: int) -> Window:
        return cls(end - timedelta(days=days - 1), end)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def year_start(self) -> date:
        """First day of npm's ``last-year`` ending at ``end``."""
        return self.end - timedelta(days=YEAR_DAYS - 1)

    @property
    def unseen(self) -> date:
        """``first_seen`` of a package with no download in the window: the day after it."""
        return self.end + timedelta(days=1)

    @property
    def months(self) -> tuple[str, ...]:
        return complete_months(self.start, self.end)

    def range_url(self, package: str) -> str:
        return f"https://{API_HOST}/downloads/range/{self.start}:{self.end}/{package}"


def snapshot_window(snapshot: Snapshot) -> Window:
    """The window of a snapshot, from its manifest; ``ValueError`` when it has none."""
    window = snapshot.manifest.window
    if window is None:
        raise ValueError(f"{snapshot.source} {snapshot.date}: the manifest has no window")
    return Window(*window)


# --- settings ----------------------------------------------------------------------------------


def scope_of(package: str) -> str | None:
    """``@scope`` of a plain scoped package name, else None."""
    m = _PACKAGE_RE.fullmatch(package)
    return m[1] if m else None


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/npm.toml``."""

    scopes: tuple[str, ...] = SCOPES  # npm organisations, as @scope
    exclude: tuple[str, ...] = EXCLUDE  # packages never fetched
    history_days: int = HISTORY_DAYS  # days each range covers, ending at npm's last counted day
    max_packages: int = MAX_PACKAGES  # > 0: an evenly spaced sample of this many per scope
    max_failures: int = MAX_FAILURES  # packages that may fail before the fetch fails

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not self.scopes:
            raise ConfigError(f"{where}.scopes: name at least one scope")
        for scope in self.scopes:
            if not _SCOPE_RE.fullmatch(scope):
                raise ConfigError(f"{where}.scopes: {scope!r} is not an npm scope like '@name'")
        if len(set(self.scopes)) != len(self.scopes):
            raise ConfigError(f"{where}.scopes: a scope is listed twice")
        for package in self.exclude:
            if scope_of(package) is None:
                raise ConfigError(f"{where}.exclude: {package!r} is not a scoped package name")
        if not YEAR_DAYS <= self.history_days <= MAX_HISTORY_DAYS:
            raise ConfigError(
                f"{where}.history_days: must be between {YEAR_DAYS} and {MAX_HISTORY_DAYS}"
            )
        if self.max_packages < 0:
            raise ConfigError(f"{where}.max_packages: must be 0 (all) or more")
        if self.max_failures < 0:
            raise ConfigError(f"{where}.max_failures: must be 0 or more")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- the API answers ---------------------------------------------------------------------------


def org_url(scope: str) -> str:
    """The registry's package list of the organisation behind ``@scope``."""
    return f"https://{REGISTRY_HOST}/-/org/{scope.removeprefix('@')}/package"


def read_org(doc: object, scope: str) -> tuple[list[str], list[str]]:
    """``(packages, odd)`` of an org listing, each sorted: the plain package names of
    ``scope``, and every other name (skipped). An org may be given access to packages
    outside its scope, such as an unscoped legacy package, so those are skipped, not
    fatal. ``ValueError`` when the answer is not an object naming at least one
    package of ``scope``."""
    if not isinstance(doc, Mapping) or not doc:
        raise ValueError(f"{scope}: the org listing is not a non-empty object")
    good, odd = [], []
    for name in sorted(doc, key=str):
        (good if isinstance(name, str) and scope_of(name) == scope else odd).append(str(name))
    if not good:
        raise ValueError(f"{scope}: the org listing names no {scope} package: {odd[:5]!r}")
    return good, odd


def read_last_day(doc: object) -> date:
    """npm's last counted day, from ``point/last-day``."""
    if not isinstance(doc, Mapping):
        raise ValueError("last-day: expected an object")
    try:
        start, end = date.fromisoformat(doc["start"]), date.fromisoformat(doc["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"last-day: no start and end dates in {dict(doc)!r}") from exc
    if start != end:
        raise ValueError(f"last-day: covers {start} to {end}, not one day")
    return end


def check_last_day(end: date, run_date: date) -> None:
    """``ValueError`` unless ``end`` lies within ``MAX_LAG_DAYS`` before ``run_date``."""
    if end > run_date:
        raise ValueError(f"npm's last counted day {end} is after the run date {run_date}")
    if (run_date - end).days > MAX_LAG_DAYS:
        raise ValueError(
            f"npm's last counted day {end} is more than {MAX_LAG_DAYS} days before {run_date}"
        )


def _count(value: object, what: str) -> int:
    # type() rather than isinstance: true must not pass as a count.
    if type(value) is not int or value < 0:
        raise ValueError(f"{what}: expected a count, got {value!r}")
    return value


def read_range(doc: object, package: str, window: Window) -> list[int]:
    """The daily downloads of ``package``, oldest first, one per day of ``window``.

    ``ValueError`` unless the answer covers exactly the window, day by day.
    """
    if not isinstance(doc, Mapping):
        raise ValueError(f"{package}: expected an object")
    if doc.get("package") != package:
        raise ValueError(f"{package}: the answer is for {doc.get('package')!r}")
    got = (doc.get("start"), doc.get("end"))
    if got != (window.start.isoformat(), window.end.isoformat()):
        raise ValueError(
            f"{package}: the answer covers {got[0]} to {got[1]}, asked for "
            f"{window.start} to {window.end}"
        )
    days = doc.get("downloads")
    if not isinstance(days, list) or len(days) != window.days:
        size = len(days) if isinstance(days, list) else "no"
        raise ValueError(f"{package}: {size} days in the answer, expected {window.days}")
    out = []
    for i, item in enumerate(days):
        day = (window.start + timedelta(days=i)).isoformat()
        if not isinstance(item, Mapping) or item.get("day") != day:
            raise ValueError(f"{package}: day {i} of the answer is not {day}: {item!r}")
        out.append(_count(item.get("downloads"), f"{package} {day}"))
    return out


def not_found(doc: object) -> bool:
    """Whether a 404 answer is npm's "package … not found" (it has no counts yet)."""
    return isinstance(doc, Mapping) and "not found" in str(doc.get("error", ""))


# --- rows and the extract ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Row:
    """One package's line of the extract."""

    package: str
    first_day: date | None  # first day with a download; None: none in the window
    last_year: int | None  # None: npm has no counts for the package
    months: tuple[int, ...]  # one per complete month of the window; () when last_year is None


def summarize(package: str, daily: Sequence[int], window: Window) -> Row:
    """The row of a package from its daily downloads over ``window``."""
    if len(daily) != window.days:
        raise ValueError(f"{package}: {len(daily)} days, expected {window.days}")
    first = next((i for i, n in enumerate(daily) if n > 0), None)
    by_month: dict[str, int] = {}
    for i, n in enumerate(daily):
        label = month_label(window.start + timedelta(days=i))
        by_month[label] = by_month.get(label, 0) + n
    return Row(
        package=package,
        first_day=None if first is None else window.start + timedelta(days=first),
        last_year=sum(daily[-YEAR_DAYS:]),
        months=tuple(by_month[m] for m in window.months),
    )


def unknown(package: str) -> Row:
    """The row of a package npm has no counts for."""
    return Row(package=package, first_day=None, last_year=None, months=())


def downloads_csv(rows: Sequence[Row], window: Window) -> bytes:
    """The extract: a header with the window's complete months, then one row per package."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow((*HEADER, *window.months))
    for r in sorted(rows):
        first = "" if r.first_day is None else r.first_day.isoformat()
        total = "" if r.last_year is None else r.last_year
        months = r.months if r.last_year is not None else ("",) * len(window.months)
        writer.writerow((r.package, first, total, *months))
    return buffer.getvalue().encode("utf-8")


def _int(text: str, what: str) -> int:
    if not text.isascii() or not text.isdigit():
        raise ValueError(f"{what}: not a count: {text!r}")
    return int(text)


def _row(line: Sequence[str], window: Window, what: str) -> Row:
    package, first, total, *months = line
    if scope_of(package) is None:
        raise ValueError(f"{what}: not a scoped package name: {package!r}")
    if total == "":
        if first or any(months):
            raise ValueError(f"{what}: {package} has no last_year but other values")
        return unknown(package)
    first_day = None
    if first:
        first_day = date.fromisoformat(first)
        if not window.start <= first_day <= window.end:
            raise ValueError(f"{what}: first_day {first} is outside the window")
    row = Row(
        package=package,
        first_day=first_day,
        last_year=_int(total, what),
        months=tuple(_int(m, what) for m in months),
    )
    if first_day is None and (row.last_year or any(row.months)):
        raise ValueError(f"{what}: {package} has downloads but no first_day")
    return row


def read_downloads_csv(data: bytes, window: Window, what: str = EXTRACT) -> list[Row]:
    """The rows of the extract, in file order; ``ValueError`` if it is malformed or its
    month columns are not the window's complete months."""
    lines = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
    header = tuple(next(lines, None) or ())
    expected = (*HEADER, *window.months)
    if header != expected:
        raise ValueError(f"{what}: header is {header!r}, expected {expected!r}")
    rows, seen = [], set()
    for number, line in enumerate(lines, start=2):
        where = f"{what} line {number}"
        if len(line) != len(expected):
            raise ValueError(f"{where}: expected {len(expected)} fields, got {len(line)}")
        row = _row(line, window, where)
        if row.package in seen:
            raise ValueError(f"{where}: {row.package} is listed twice")
        seen.add(row.package)
        rows.append(row)
    return rows


def observations(row: Row, window: Window) -> Iterator[Observation]:
    """The Observations of one row (module docstring, **Parse**)."""
    key = SourceKey(NAMESPACE, row.package)
    seen = row.first_day or window.unseen
    yield Observation(
        source=NAME,
        series=SERIES,
        key=key,
        value=None if row.last_year is None else float(row.last_year),
        unit=UNIT,
        start=window.year_start,
        end=window.end,
        attrs=attrs(first_seen=seen.isoformat()),
    )
    if row.last_year is None:
        return
    for label, value in zip(window.months, row.months, strict=True):
        first, last = month_bounds(label)
        if last < seen:
            continue
        yield Observation(
            source=NAME,
            series=label,
            key=key,
            value=float(value),
            unit=UNIT,
            start=first,
            end=last,
            attrs=attrs(first_seen=seen.isoformat(), month=label),
        )


# --- the collector -----------------------------------------------------------------------------


def sample(names: Sequence[str], n: int) -> list[str]:
    """``n`` names evenly spaced over ``names``, the first included; all of them when ``n``
    is 0 or at least their number."""
    if n == 0 or n >= len(names):
        return list(names)
    return [names[i * len(names) // n] for i in range(n)]


def _record(ctx: FetchContext, result: FetchResult) -> None:
    for record in result.to_records(kept=False):
        ctx.out.record_fetch(record)


class Npm(CollectorBase):
    """npm: daily downloads of the Fontsource and Expo font packages."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (API_HOST, REGISTRY_HOST)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "npm_registry"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Every package's range over one window (module docstring, **Fetch**)."""
        settings = _settings(ctx.settings)
        result = ctx.fetcher.get(LAST_DAY_URL)
        _record(ctx, result)
        end = read_last_day(result.json())
        check_last_day(end, ctx.run_date)
        window = Window.ending(end, settings.history_days)
        packages = [p for scope in settings.scopes for p in self._listing(ctx, scope, settings)]
        rows, failed = self._ranges(ctx, packages, window, settings.max_failures)
        ctx.out.write_bytes(EXTRACT, downloads_csv(rows, window), rows=len(rows))
        ctx.out.set_window(window.start, window.end)
        ctx.out.set_data_date(window.end)
        missing = sorted(r.package for r in rows if r.last_year is None)
        if missing:
            ctx.out.note(f"npm has no counts for {len(missing)}: {', '.join(missing)}")
        if failed:
            ctx.out.note(f"left out after failed requests ({len(failed)}): {', '.join(failed)}")
        ctx.log.info(
            "%s: %d packages, %s to %s; %d without counts, %d failed",
            self.name,
            len(rows),
            window.start,
            window.end,
            len(missing),
            len(failed),
        )

    def _listing(self, ctx: FetchContext, scope: str, settings: Settings) -> list[str]:
        """The packages of one scope to fetch: listed, not excluded, sampled."""
        result = ctx.fetcher.get(org_url(scope))
        _record(ctx, result)
        listed, odd = read_org(result.json(), scope)
        if odd:
            ctx.out.note(f"{scope}: skipped names that are not {scope} packages: {', '.join(odd)}")
            ctx.log.warning("%s: %s: skipped odd names %s", self.name, scope, odd)
        kept = [p for p in listed if p not in settings.exclude]
        chosen = sample(kept, settings.max_packages)
        if len(chosen) < len(kept):
            ctx.out.note(f"{scope}: a sample of {len(chosen)} of {len(kept)} packages")
        ctx.log.info(
            "%s: %s lists %d packages, %d to fetch", self.name, scope, len(listed), len(chosen)
        )
        return chosen

    def _ranges(
        self, ctx: FetchContext, packages: Sequence[str], window: Window, max_failures: int
    ) -> tuple[list[Row], list[str]]:
        """One row per package that answered, and the packages that still failed.

        The packages that failed are asked once more after all the others: npm's
        429s and 5xx come in bursts, and a package left out loses its family's npm
        term for the month. More than ``max_failures`` failures in the first pass
        stop the fetch at once, so an outage costs a few requests, not thousands.
        """
        rows, failed = self._pass(ctx, packages, window, max_failures)
        if failed:
            ctx.log.info("%s: asking again for %d failed packages", self.name, len(failed))
            again, failed = self._pass(ctx, failed, window, max_failures)
            rows += again
        return rows, failed

    def _pass(
        self, ctx: FetchContext, packages: Sequence[str], window: Window, max_failures: int
    ) -> tuple[list[Row], list[str]]:
        """One request per package, in order: the rows, and the packages that failed."""
        rows: list[Row] = []
        failed: list[str] = []
        for i, package in enumerate(packages, start=1):
            try:
                rows.append(self._range(ctx, package, window))
            except (FetchError, ValueError) as exc:
                failed.append(package)
                ctx.log.warning("%s: %s left out: %s", self.name, package, exc)
                if len(failed) > max_failures:
                    raise FetchError(
                        f"{len(failed)} packages failed, over max_failures = {max_failures}: "
                        f"{', '.join(failed)}"
                    ) from exc
            if i % 500 == 0:
                ctx.log.info("%s: %d of %d packages", self.name, i, len(packages))
        return rows, failed

    def _range(self, ctx: FetchContext, package: str, window: Window) -> Row:
        result = ctx.fetcher.get(
            window.range_url(package),
            to=ctx.raw.file(f"range/{package}.json"),
            expect=(200, 404),
        )
        _record(ctx, result)
        doc = result.json()
        if result.status == 404:
            if not not_found(doc):
                raise ValueError(f"{package}: HTTP 404 without npm's 'not found': {doc!r}")
            return unknown(package)
        return summarize(package, read_range(doc, package, window), window)

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """The Observations of every row (module docstring, **Parse**)."""
        window = snapshot_window(ctx.snapshot)
        rows = read_downloads_csv(ctx.snapshot.read_bytes(EXTRACT), window)
        if not rows:
            ctx.log.warning("%s: %s lists no packages", self.name, EXTRACT)
        for row in rows:
            yield from observations(row, window)


COLLECTOR = Npm()
