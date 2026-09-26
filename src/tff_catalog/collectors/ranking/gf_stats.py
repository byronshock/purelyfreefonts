"""Ranking collector "gf_stats": Google Fonts views per family (design-m1 §2.4).

**Source.** ``https://fonts.google.com/metadata/stats``, undocumented and
unauthenticated: one GET of about 1.6 MB (220 KB gzipped on the wire), a
``)]}'`` XSSI prefix and then a JSON array of about 1,950 rows ``{family,
designers[], totalViews, rate, viewsByDateRange: {7day, 30day, 90day, year:
{views, change}}, viewsByBrowser, viewsByOS}``. The engine source ``google``
(``config/ranking.toml``) reads series ``year``; Rising reads ``30day``
(``corrections.RISING_WINDOWS``). Ruling T2: the data stays in the private
store, fixtures are synthetic, and only ranks and rank-based z scores are ever
published (the engine source's ``publish_raw = false``), never view counts.
When this source fails, the engine falls back to ``google_metadata``'s
``popularity`` rank.

**Fetch.** The whole body goes to ``ctx.raw`` and is never kept. The one
extract, ``views.jsonl.gz``, has one row ``{"family", "views": {<window>:
<count>}}`` per family, sorted by name, holding only the windows of
``WINDOW_DAYS`` that carry a usable count (a whole number of views, at least
0). Designers (personal names), lifetime totals, change rates and the browser
and OS splits are dropped. Rows without a family name are skipped, a repeated
name keeps its first row, and windows Google adds that ``WINDOW_DAYS`` does
not know are ignored; the manifest notes each. A body that is not the stats
array, names no family, has a usable count for one of the configured
``series`` in no family or in fewer than ``min_coverage`` of them, or has
fewer than ``min_share`` times the families of the previous snapshot
(``ctx.previous``) fails the fetch, so the stale policy reuses the last good
snapshot. The coverage check matters because the engine falls back to
``popularity`` only when this source has no rows at all: a payload whose
``year`` counts went missing for most families would otherwise leave those
families in the frame with no value. The payload carries no as-of date, so
the manifest's ``data_date`` is the fetch date and its window is the longest
configured window up to it. There is no conditional GET: the endpoint answers
``cache-control: no-store`` without validators.

**Parse** (offline, pure): for each extract row and each configured series,
one ``Observation`` keyed ``gf-family:<family>`` (the key ``google_metadata``
uses, so exposure can read that record's ``added`` date), unit ``views``, over
the ``WINDOW_DAYS[series]`` days that end on the snapshot's data date. A
family whose row lacks the window gets ``value`` None: in the frame, no value.
Every family Google lists is emitted, including the Material Icons and
Material Symbols families that ``/metadata/fonts`` does not list; the alias
table marks such non-text keys ineligible, not this collector.
"""

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.records import Observation, Record, SourceKey
from tff_catalog.store import Snapshot

NAME = "gf_stats"
HOST = "fonts.google.com"
URL = f"https://{HOST}/metadata/stats"
EXTRACT = "views.jsonl.gz"
RAW_NAME = "metadata-stats.json"
NAMESPACE = "gf-family"
UNIT = "views"
RANGES_FIELD = "viewsByDateRange"
VIEWS_FIELD = "views"

# Google's date ranges and their length in days. A window ends on the data date and
# spans this many days, counting both ends, so "year" reads as exactly one year.
WINDOW_DAYS: dict[str, int] = {"year": 365, "90day": 90, "30day": 30, "7day": 7}

# Defaults of the Settings, which config/sources/gf_stats.toml spells out.
SERIES: tuple[str, ...] = ("year", "90day", "30day", "7day")
# Google delists only a handful of families a year, so losing a tenth at once is a broken response.
MIN_SHARE = 0.9
# On 2026-09-26 every one of 1,955 families had a count for all four windows, so a window
# missing from half of them means Google renamed or dropped it.
MIN_COVERAGE = 0.5


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/gf_stats.toml``."""

    url: str = URL
    series: tuple[str, ...] = SERIES  # Google's windows to emit, each one Observation series
    min_share: float = MIN_SHARE  # of the previous snapshot's families, or the fetch fails
    min_coverage: float = MIN_COVERAGE  # of the families with a count, per series, or it fails

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        parts = urlsplit(self.url)
        if parts.scheme != "https" or parts.hostname != HOST:
            raise ConfigError(f"{where}.url: must be an https URL on {HOST}, got {self.url!r}")
        if not self.series:
            raise ConfigError(f"{where}.series: name at least one window")
        unknown = [s for s in self.series if s not in WINDOW_DAYS]
        if unknown:
            raise ConfigError(
                f"{where}.series: {', '.join(map(repr, unknown))} not one of "
                f"{', '.join(map(repr, WINDOW_DAYS))}"
            )
        if len(set(self.series)) != len(self.series):
            raise ConfigError(f"{where}.series: has duplicates")
        for name in ("min_share", "min_coverage"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ConfigError(f"{where}.{name}: must be between 0 and 1")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- values ------------------------------------------------------------------------------------


def _text(value: object) -> str | None:
    """A non-empty string as given, else None."""
    return value if isinstance(value, str) and value.strip() else None


def view_count(value: object) -> int | None:
    """A usable view count: a whole number at least 0 (bools are not counts), else None."""
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def window(series: str, end: date) -> tuple[date, date]:
    """The (start, end) days of Google's ``series`` window ending on ``end``, both counted."""
    return end - timedelta(days=WINDOW_DAYS[series] - 1), end


def longest(series: tuple[str, ...]) -> str:
    """The longest window of ``series`` (the manifest's window)."""
    return max(series, key=lambda s: WINDOW_DAYS[s])


# --- fetch: the extract ------------------------------------------------------------------------


def _views(ranges: object, unknown: set[str]) -> dict[str, int]:
    """The usable counts of one row's ``viewsByDateRange``; adds unknown window names to ``unknown``."""
    if not isinstance(ranges, dict):
        return {}
    out = {}
    for name, item in ranges.items():
        if name not in WINDOW_DAYS:
            unknown.add(str(name))
            continue
        count = view_count(item.get(VIEWS_FIELD)) if isinstance(item, dict) else None
        if count is not None:
            out[name] = count
    return out


def extract_rows(doc: object) -> tuple[list[dict[str, Any]], list[str]]:
    """The extract rows of a ``/metadata/stats`` body, sorted by family, and manifest notes.

    Raises ``ValueError`` when ``doc`` is not the stats array or names no family.
    """
    if not isinstance(doc, list):
        raise ValueError(f"{URL}: not a JSON array; not the Google Fonts stats list")
    rows: dict[str, dict[str, Any]] = {}
    unnamed, repeated = 0, []
    unknown: set[str] = set()
    for item in doc:
        family = _text(item.get("family")) if isinstance(item, dict) else None
        if family is None:
            unnamed += 1
        elif family in rows:
            repeated.append(family)
        else:
            rows[family] = {"family": family, "views": _views(item.get(RANGES_FIELD), unknown)}
    if not rows:
        raise ValueError(f"{URL}: the stats list names no family")
    notes = []
    if unnamed:
        notes.append(f"rows without a family name skipped: {unnamed}")
    if repeated:
        notes.append(f"repeated families kept once: {', '.join(sorted(set(repeated)))}")
    if unknown:
        notes.append(f"unknown date ranges ignored: {', '.join(sorted(unknown))}")
    return [rows[name] for name in sorted(rows)], notes


def check_series(rows: list[dict[str, Any]], series: tuple[str, ...], min_coverage: float) -> None:
    """Raise ``ValueError`` when one of ``series`` has a count in no row, or in fewer than
    ``min_coverage`` of the rows (the payload changed)."""
    total = len(rows)
    thin = []
    for name in series:
        found = sum(name in row["views"] for row in rows)
        if found == 0 or found < min_coverage * total:
            thin.append(f"{name} ({found} of {total})")
    if thin:
        raise ValueError(
            f"{URL}: too few families have views for {', '.join(thin)} (min_coverage = "
            f"{min_coverage}); did Google rename or drop its date ranges?"
        )


def previous_families(previous: Snapshot | None) -> int | None:
    """The family count of an earlier snapshot, from its manifest (None: no snapshot)."""
    entry = previous.manifest.extract(EXTRACT) if previous is not None else None
    return entry.rows if entry is not None else None


def check_shrink(families: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when the list shrank below ``min_share`` of ``before`` families."""
    if before is not None and families < min_share * before:
        raise ValueError(
            f"{URL}: {families} families, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken response?"
        )


# --- parse: records ----------------------------------------------------------------------------


def observations(
    row: Mapping[str, Any], series: tuple[str, ...], end: date
) -> Iterator[Observation]:
    """One Observation per ``series`` for an extract row whose ``family`` is a non-empty string."""
    views = row.get("views")
    views = views if isinstance(views, dict) else {}
    for name in series:
        start, stop = window(name, end)
        count = view_count(views.get(name))
        yield Observation(
            source=NAME,
            series=name,
            key=SourceKey(NAMESPACE, row["family"]),
            value=None if count is None else float(count),
            unit=UNIT,
            start=start,
            end=stop,
        )


class GfStats(CollectorBase):
    """``fonts.google.com/metadata/stats``: Google Fonts views per family and window."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (HOST,)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "google"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """One GET into ``ctx.raw``; write the trimmed ``views.jsonl.gz`` extract."""
        settings = _settings(ctx.settings)
        result = ctx.fetcher.get(settings.url, to=ctx.raw.file(RAW_NAME))
        for record in result.to_records():
            ctx.out.record_fetch(record)
        rows, notes = extract_rows(result.json())
        check_series(rows, settings.series, settings.min_coverage)
        check_shrink(len(rows), previous_families(ctx.previous), settings.min_share)
        for note in notes:
            ctx.log.warning("%s: %s", self.name, note)
            ctx.out.note(note)
        ctx.out.write_jsonl(EXTRACT, rows)
        ctx.out.set_window(*window(longest(settings.series), ctx.run_date))
        ctx.out.set_data_date(ctx.run_date)
        ctx.log.info("%s: %d families (%d bytes fetched)", self.name, len(rows), result.size)

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One Observation per family of the extract and configured series."""
        settings = _settings(ctx.settings)
        end = ctx.snapshot.manifest.data_date or ctx.snapshot.date
        seen: set[str] = set()
        for row in ctx.snapshot.iter_jsonl(EXTRACT):
            family = _text(row.get("family")) if isinstance(row, dict) else None
            if family is None or family in seen:
                continue
            seen.add(family)
            yield from observations(row, settings.series, end)


COLLECTOR = GfStats()
