"""Ranking collector "fontsource_stats": jsDelivr hits per Fontsource package (design-m1 §2.4).

**Source.** ``https://api.fontsource.org/v1/stats``: one unauthenticated GET
of about 590 KB (75 KB compressed on the wire), a JSON object ``{<Fontsource
id>: {"static": {…}, "variable": {…}, "total": {…}}}`` of about 2,120 ids.
Each variant object holds ``npmDownloadMonthly``, ``npmDownloadTotal``,
``jsDelivrHitsMonthly`` and ``jsDelivrHitsTotal``. ``static`` is the npm
package ``@fontsource/<id>``; ``variable``, present for the 570 variable
fonts, is ``@fontsource-variable/<id>``; ``total`` is Fontsource's sum of the
two. Fontsource computes the monthly fields from npm's ``point/last-month``
and jsDelivr's ``period=month`` (30 days each), refreshed by a cron job and
cached for up to a day. The payload carries no date. The engine source
``jsdelivr`` (``config/ranking.toml``, weight 0.05 in the code group and the
monthly cross-check ruler) reads the jsDelivr hits. Ruling T1: Fontsource and
jsDelivr are open sources; the counts may be published, and a small trimmed
real fixture may be committed with a notice.

**Monthly fields only.** The lifetime ``*Total`` fields are wrong (Inter's
static npm total is below npm's own count for the last year alone), so they
are dropped, and so is the ``total`` variant, which would count each package
twice. The monthly jsDelivr hits run about 35% below what jsDelivr reports
directly; the engine uses ranks only, so a uniform scale error is harmless.

**Fetch.** The whole body goes to ``ctx.raw`` and is never kept. The one
extract, ``stats.jsonl.gz``, has one row ``{"id", "static": {<field>:
<count>}, "variable": {…}}`` per id, sorted by id, holding only the
``FIELDS`` that carry a usable count (a whole number, at least 0) and only the
variants of ``SCOPES``; a variant with no usable count is kept as ``{}``.
Rows whose id is empty or whose value is not an object, ids with neither
variant, and variant names Fontsource adds are skipped, and the manifest
notes each (the first ``NOTE_LIMIT`` names, then a count). A body that is not
the stats object, lists no id, has no package with a positive jsDelivr count
(the field was renamed, or Fontsource lost its jsDelivr figures), or has fewer
ids, or fewer packages with a positive jsDelivr count, than ``min_share`` of
the previous snapshot's (``ctx.previous``) fails the fetch, so the stale
policy reuses the last good snapshot rather than rank on a partial answer.
There is no conditional GET: the statistics change daily.

**Dates.** The monthly window ends ``lag_days`` before the moment the data
left Fontsource's server, which is the fetch time less the CDN's ``Age``
header (the fetch time when there is none), and spans ``window_days`` days
counting both ends. The manifest keeps it as ``window``, with ``data_date``
its end, so parse needs no clock.

**Parse** (offline, pure): for each extract row, each variant present and
each of ``FIELDS``, one ``Observation`` over the manifest's window: series
``hits_series`` (unit ``hits``) or ``downloads_series`` (unit
``downloads``). A variant whose field has no usable count gets ``value``
None (in the frame, no value). The key follows ``key_namespace``:

- ``"fs-id"`` (default): ``fs-id:<id>``, the key the ``fontsource`` universe
  collector gives the family, so every key resolves directly and the
  source's frame is every Fontsource family. The two packages of a font are
  two rows of one key with attr ``package`` (``@fontsource/<id>`` or
  ``@fontsource-variable/<id>``), which the pipeline sums per key, as it does
  Homebrew's tap rows.
- ``"npm"``: ``npm:<package>``, the key the ``npm`` and ``ecosystems``
  collectors use, with attr ``fs_id``. Such keys resolve only through alias
  rows in the ``npm`` namespace.

Retired Fontsource ids (``andada``, ``be-vietnam``, …) are emitted as given;
the alias table folds them into their successors, not this collector.
"""

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchResult
from tff_catalog.records import Attrs, Observation, Record, SourceKey, Unit, attrs
from tff_catalog.store import Snapshot

NAME = "fontsource_stats"
HOST = "api.fontsource.org"
URL = f"https://{HOST}/v1/stats"
EXTRACT = "stats.jsonl.gz"
RAW_NAME = "stats.json"

# The variants the stats name, and the npm scope of each one's package.
SCOPES: dict[str, str] = {"static": "@fontsource", "variable": "@fontsource-variable"}
# Fontsource's own sum of the variants: dropped, since the pipeline sums packages itself.
SUM_VARIANT = "total"
HITS_FIELD = "jsDelivrHitsMonthly"
DOWNLOADS_FIELD = "npmDownloadMonthly"
# The monthly fields kept, and their Observation unit. The lifetime *Total fields are wrong.
FIELDS: dict[str, Unit] = {HITS_FIELD: "hits", DOWNLOADS_FIELD: "downloads"}

KeyNamespace = Literal["fs-id", "npm"]

# Defaults of the Settings, which config/sources/fontsource_stats.toml spells out.
HITS_SERIES = "jsdelivr_monthly"  # ranking.toml [sources.jsdelivr] series
DOWNLOADS_SERIES = "npm_monthly"
WINDOW_DAYS = 30  # npm's last-month and jsDelivr's month are both 30 days
LAG_DAYS = 1  # npm and jsDelivr count up to the day before
# Fontsource retires a few ids a year and keeps their stats, so losing a tenth is a broken answer.
# Likewise for packages with jsDelivr hits: about 99% have some (2,658 of 2,690 on 2026-09-26).
MIN_SHARE = 0.9
NOTE_LIMIT = 20  # names listed per manifest note; a changed payload could skip thousands


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/fontsource_stats.toml``."""

    url: str = URL
    hits_series: str = HITS_SERIES  # Observation series of jsDelivrHitsMonthly
    downloads_series: str = DOWNLOADS_SERIES  # Observation series of npmDownloadMonthly
    key_namespace: KeyNamespace = "fs-id"
    window_days: int = WINDOW_DAYS
    lag_days: int = LAG_DAYS
    min_share: float = MIN_SHARE  # of the previous snapshot's ids and hit packages, or fail

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        parts = urlsplit(self.url)
        if parts.scheme != "https" or parts.hostname != HOST:
            raise ConfigError(f"{where}.url: must be an https URL on {HOST}, got {self.url!r}")
        for key in ("hits_series", "downloads_series"):
            value = getattr(self, key)
            if not value or value != value.strip():
                raise ConfigError(f"{where}.{key}: must be a non-empty name, got {value!r}")
        if self.hits_series == self.downloads_series:
            raise ConfigError(f"{where}: hits_series and downloads_series must differ")
        # The engine's per_month rate reads a 28-31 day window as exactly one month.
        if not 28 <= self.window_days <= 31:
            raise ConfigError(f"{where}.window_days: must be 28 to 31, got {self.window_days}")
        if not 0 <= self.lag_days <= 7:
            raise ConfigError(f"{where}.lag_days: must be 0 to 7, got {self.lag_days}")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")

    def series(self, field: str) -> str:
        """The Observation series of monthly ``field``."""
        return self.hits_series if field == HITS_FIELD else self.downloads_series


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- values and dates --------------------------------------------------------------------------


def count(value: object) -> int | None:
    """A usable monthly count: a whole number at least 0 (bools are not counts), else None."""
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def package(variant: str, font_id: str) -> str:
    """The npm package of one variant of a Fontsource id: ``@fontsource-variable/inter``."""
    return f"{SCOPES[variant]}/{font_id}"


def window(end: date, days: int) -> tuple[date, date]:
    """The (start, end) days of a ``days``-day window ending on ``end``, both counted."""
    return end - timedelta(days=days - 1), end


def age_seconds(result: FetchResult) -> int | None:
    """The response's ``Age`` header in seconds, when it is a whole number at least 0."""
    value = (result.header("age") or "").strip()
    return int(value) if value.isascii() and value.isdigit() else None


def data_window(result: FetchResult, settings: Settings) -> tuple[date, date]:
    """The monthly window of a fetched body: it ends ``lag_days`` before the data left
    Fontsource's server (fetch time less the CDN's ``Age``)."""
    served: datetime = result.fetched_at - timedelta(seconds=age_seconds(result) or 0)
    return window(served.date() - timedelta(days=settings.lag_days), settings.window_days)


# --- fetch: the extract ------------------------------------------------------------------------


def _variant_counts(item: Mapping[str, Any]) -> dict[str, int]:
    """The usable monthly counts of one variant object."""
    out = {}
    for field in FIELDS:
        n = count(item.get(field))
        if n is not None:
            out[field] = n
    return out


def extract_rows(doc: object) -> tuple[list[dict[str, Any]], list[str]]:
    """The extract rows of a ``/v1/stats`` body, sorted by id, and manifest notes.

    Raises ``ValueError`` when ``doc`` is not the stats object or lists no id.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{URL}: not a JSON object; not the Fontsource stats")
    rows: list[dict[str, Any]] = []
    malformed: list[str] = []
    garbled: list[str] = []
    empty: list[str] = []
    unknown: set[str] = set()
    for font_id, stats in sorted(doc.items()):
        if not isinstance(font_id, str) or not font_id.strip() or not isinstance(stats, dict):
            malformed.append(repr(font_id))
            continue
        row: dict[str, Any] = {"id": font_id}
        for variant, item in stats.items():
            if variant not in SCOPES:
                if variant != SUM_VARIANT:
                    unknown.add(str(variant))
            elif isinstance(item, dict):
                row[variant] = _variant_counts(item)
            else:
                garbled.append(f"{font_id}/{variant}")
        if len(row) == 1:
            empty.append(font_id)
        else:
            rows.append(row)
    if not rows:
        raise ValueError(f"{URL}: the stats name no Fontsource id")
    notes = []
    if malformed:
        notes.append(f"entries without an id or a stats object skipped: {listed(malformed)}")
    if garbled:
        notes.append(f"variants that are not objects skipped: {listed(garbled)}")
    if empty:
        notes.append(f"ids with neither static nor variable stats skipped: {listed(empty)}")
    if unknown:
        notes.append(f"unknown variants ignored: {listed(sorted(unknown))}")
    return rows, notes


def listed(names: list[str], limit: int = NOTE_LIMIT) -> str:
    """``names`` for a manifest note: the first ``limit``, then how many more there are."""
    shown = ", ".join(names[:limit])
    return shown if len(names) <= limit else f"{shown} and {len(names) - limit} more"


def hit_packages(rows: Iterable[object]) -> int:
    """How many packages of extract ``rows`` have a positive jsDelivr count."""
    return sum(
        (count(counts.get(HITS_FIELD)) or 0) > 0
        for row in rows
        if isinstance(row, dict)
        for counts in (row.get(v) for v in SCOPES)
        if isinstance(counts, dict)
    )


def check_hits(rows: list[dict[str, Any]]) -> None:
    """Raise ``ValueError`` when no package has a positive jsDelivr count: Fontsource renamed
    the field or lost its jsDelivr figures, and an all-zero source would still count as
    evidence for every font."""
    if not hit_packages(rows):
        raise ValueError(f"{URL}: no package has a positive {HITS_FIELD}; renamed, or all lost?")


def previous_counts(previous: Snapshot | None) -> tuple[int | None, int | None]:
    """The ids (from the manifest) and hit packages (from the extract) of an earlier
    snapshot; None for what it lacks or cannot be read, so that check is skipped."""
    entry = previous.manifest.extract(EXTRACT) if previous is not None else None
    if previous is None or entry is None:
        return None, None
    try:
        hits = hit_packages(previous.iter_jsonl(EXTRACT))
    except OSError, ValueError:  # damaged: `store check` reports it; it must not block a fetch
        hits = None
    return entry.rows, hits


def check_shrink(what: str, now: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when ``now`` fell below ``min_share`` of ``before``."""
    if before is not None and now < min_share * before:
        raise ValueError(
            f"{URL}: {now} {what}, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken response?"
        )


# --- parse: records ----------------------------------------------------------------------------


def key_of(namespace: KeyNamespace, font_id: str, pkg: str) -> tuple[SourceKey, Attrs]:
    """The key of one package's rows and the attr naming the other identifier."""
    if namespace == "npm":
        return SourceKey("npm", pkg), attrs(fs_id=font_id)
    return SourceKey("fs-id", font_id), attrs(package=pkg)


def observations(
    row: Mapping[str, Any], settings: Settings, start: date, end: date
) -> Iterator[Observation]:
    """One Observation per variant present and monthly field, for an extract row whose ``id``
    is a non-empty string."""
    font_id = row["id"]
    for variant in SCOPES:
        counts = row.get(variant)
        if not isinstance(counts, dict):
            continue
        key, extra = key_of(settings.key_namespace, font_id, package(variant, font_id))
        for field, unit in FIELDS.items():
            n = count(counts.get(field))
            yield Observation(
                source=NAME,
                series=settings.series(field),
                key=key,
                value=None if n is None else float(n),
                unit=unit,
                start=start,
                end=end,
                attrs=extra,
            )


def snapshot_window(snapshot: Snapshot, settings: Settings) -> tuple[date, date]:
    """The manifest's window; for a manifest without one, the window the fetch would give
    a response of age 0 on the snapshot's date."""
    if snapshot.manifest.window is not None:
        return snapshot.manifest.window
    return window(snapshot.date - timedelta(days=settings.lag_days), settings.window_days)


class FontsourceStats(CollectorBase):
    """``api.fontsource.org/v1/stats``: monthly jsDelivr hits and npm downloads per package."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (HOST,)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "jsdelivr"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """One GET into ``ctx.raw``; write the trimmed ``stats.jsonl.gz`` extract."""
        settings = _settings(ctx.settings)
        result = ctx.fetcher.get(settings.url, to=ctx.raw.file(RAW_NAME))
        for record in result.to_records():
            ctx.out.record_fetch(record)
        rows, notes = extract_rows(result.json())
        check_hits(rows)
        ids_before, hits_before = previous_counts(ctx.previous)
        check_shrink("ids", len(rows), ids_before, settings.min_share)
        check_shrink(
            "packages with jsDelivr hits", hit_packages(rows), hits_before, settings.min_share
        )
        for note in notes:
            ctx.log.warning("%s: %s", self.name, note)
            ctx.out.note(note)
        ctx.out.write_jsonl(EXTRACT, rows)
        start, end = data_window(result, settings)
        ctx.out.set_window(start, end)
        ctx.out.set_data_date(end)
        ctx.log.info(
            "%s: %d ids, window %s to %s (%d bytes fetched)",
            self.name,
            len(rows),
            start,
            end,
            result.size,
        )

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """Monthly Observations per package of every extract row, over the manifest's window."""
        settings = _settings(ctx.settings)
        start, end = snapshot_window(ctx.snapshot, settings)
        seen: set[str] = set()
        for row in ctx.snapshot.iter_jsonl(EXTRACT):
            font_id = row.get("id") if isinstance(row, dict) else None
            if not isinstance(font_id, str) or not font_id.strip() or font_id in seen:
                continue
            seen.add(font_id)
            yield from observations(row, settings, start, end)


COLLECTOR = FontsourceStats()
