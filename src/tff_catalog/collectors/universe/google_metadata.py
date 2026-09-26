"""Universe collector "google_metadata": the live Google Fonts family list (design-m1 §2.4).

**Source.** ``https://fonts.google.com/metadata/fonts``, undocumented and
unauthenticated: one GET of about 2.7 MB (150 KB gzipped on the wire), JSON
``{axisRegistry, familyMetadataList, promotedScript}``, sometimes behind a
``)]}'`` XSSI prefix (absent on 2026-09-25, so stripping it is optional). The
live list is the universe's source of truth for Google families (design-m1
C12); the google/fonts repo (``google_repo``) adds METADATA.pb facts. Ruling
T2: the data stays in the private store, fixtures are synthetic, and only ranks
are ever published.

**Fetch.** The whole body goes to ``ctx.raw`` and is never kept. A body that
is not the family list, lists no family, or lists fewer than ``min_share`` times
the families of the previous snapshot (``ctx.previous``) fails the fetch, so
the stale policy reuses the last good snapshot instead of letting Google's
families vanish for a month. The one extract, ``families.jsonl.gz``, has one
row per family, sorted by name, holding only ``KEPT_FIELDS`` (no designers,
sizes, per-style metrics, trending or sort orders), with each axis cut to
``AXIS_FIELDS``. Rows without a family name are skipped and a repeated name
keeps its first row; the manifest notes both. The payload carries no as-of
date, so the manifest's ``data_date`` is the fetch date. There is no
conditional GET: the endpoint answers ``cache-control: no-store`` without
validators.

**Parse** (offline, pure), for each extract row:

- a ``UniverseRecord`` keyed ``gf-family:<family>``, status ``live``, with
  Google's ``category`` and ``classifications`` as given, ``subsets`` sorted
  without the ``menu`` picker subset (so a family Google serves only as
  ``menu``, such as the Playwrite school scripts, has no subset at all),
  ``primary_script`` (None when empty), ``display_name`` (only when it differs
  from the family), ``is_monospace`` (category or a classification
  "Monospace"; None when the row has neither), ``variable`` (any tagged axis;
  None when the row has no ``axes`` list), ``added`` (``dateAdded``),
  ``latin_languages`` (``*_Latn`` entries of ``languages``; None while Google
  leaves the list empty, as on 2026-09-25), the specimen page as its
  ``specimen`` URL, a ``drop`` code (``drop_code``) and a few descriptive attrs
  (``is_brand_font``, ``is_noto``, ``last_modified``, ``stroke``,
  ``primary_language``, ``color_formats``, ``axes``);
- an ``Observation`` of series ``popularity``, unit ``rank``: Google's
  popularity order (lower is more popular: Roboto is 2 on 2026-09-26; it
  tracks 7-day views with Spearman 0.998). ``config/ranking.toml`` names it as
  the Google source's fallback (``google_metadata:popularity``), read only when
  ``/metadata/stats`` fails. Both window ends are the snapshot date, since the
  rank is as of the fetch. A family without a usable rank gets ``value`` None:
  in the frame, no value.

Google's own tags are taken as they are, gaps included: on 2026-09-26 the
live list files Cascadia Code, Cascadia Mono, SUSE Mono and Atkinson
Hyperlegible Mono as "Sans Serif" with no "Monospace" classification.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar
from urllib.parse import quote_plus, urlsplit

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.records import (
    DROP_REASONS,
    Observation,
    Record,
    Scalar,
    SourceKey,
    UniverseRecord,
    attrs,
)
from tff_catalog.store import Snapshot

NAME = "google_metadata"
HOST = "fonts.google.com"
URL = f"https://{HOST}/metadata/fonts"
SPECIMEN_BASE = f"https://{HOST}/specimen/"  # as links.specimen_url builds it
EXTRACT = "families.jsonl.gz"
RAW_NAME = "metadata-fonts.json"
NAMESPACE = "gf-family"
SERIES = "popularity"
LIST_FIELD = "familyMetadataList"

# The only fields of a family row that reach the store; everything else is dropped.
KEPT_FIELDS = (
    "family",
    "displayName",
    "category",
    "stroke",
    "classifications",
    "subsets",
    "axes",
    "lastModified",
    "dateAdded",
    "popularity",
    "isNoto",
    "isBrandFont",
    "isOpenSource",
    "primaryScript",
    "primaryLanguage",
    "colorCapabilities",
    "languages",
)
AXIS_FIELDS = ("tag", "min", "max", "defaultValue")
MENU_SUBSET = "menu"  # Google's font-picker subset (the family name's own glyphs), not a script
MONOSPACE = "Monospace"
EMOJI_SUBSET = "emoji"
LATIN_LANGUAGE = "_Latn"  # languages are "<lang>_<Script>", e.g. "en_Latn"

# Defaults of the Settings, which config/sources/google_metadata.toml spells out.
# Google delists a handful of families a year, so losing a tenth at once is a broken response.
MIN_SHARE = 0.9
# Family-name patterns of non-text families (records.DROP_REASONS codes); first match wins.
DROP_NAMES: tuple[tuple[str, str], ...] = (
    ("icon", r"^Material (Icons|Symbols)\b"),
    ("barcode", r"\bBarcode\b"),
    ("math", r"\bMath\b"),
    ("music", r"\bMusic(al)?\b"),
    ("emoji", r"\bEmoji\b"),
)
# Google's classification of symbol, placeholder and pictograph fonts.
SYMBOL_CLASSIFICATIONS: tuple[str, ...] = ("Symbols",)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/google_metadata.toml``."""

    url: str = URL
    min_share: float = MIN_SHARE  # of the previous snapshot's families, or the fetch fails
    drop_names: tuple[tuple[str, str], ...] = DROP_NAMES
    symbol_classifications: tuple[str, ...] = SYMBOL_CLASSIFICATIONS

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        parts = urlsplit(self.url)
        if parts.scheme != "https" or parts.hostname != HOST:
            raise ConfigError(f"{where}.url: must be an https URL on {HOST}, got {self.url!r}")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")
        for code, pattern in self.drop_names:
            if code not in DROP_REASONS:
                raise ConfigError(
                    f"{where}.drop_names: {code!r} is not one of {sorted(DROP_REASONS)}"
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"{where}.drop_names: bad pattern {pattern!r}: {exc}") from exc


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- fetch: the extract ----------------------------------------------------------------------


def _trim(row: Mapping[str, Any]) -> dict[str, Any]:
    """A family row cut to ``KEPT_FIELDS``, each axis to ``AXIS_FIELDS``."""
    out = {k: row[k] for k in KEPT_FIELDS if k in row}
    if isinstance(out.get("axes"), list):
        out["axes"] = [
            {k: a[k] for k in AXIS_FIELDS if k in a} if isinstance(a, dict) else a
            for a in out["axes"]
        ]
    return out


def extract_rows(doc: object) -> tuple[list[dict[str, Any]], list[str]]:
    """The extract rows of a ``/metadata/fonts`` body, sorted by family, and manifest notes.

    Raises ``ValueError`` when ``doc`` is not the family list or names no family.
    """
    listed = doc.get(LIST_FIELD) if isinstance(doc, dict) else None
    if not isinstance(listed, list):
        raise ValueError(f"{URL}: no {LIST_FIELD} list; not the Google Fonts family list")
    rows: dict[str, dict[str, Any]] = {}
    unnamed, repeated = 0, []
    for item in listed:
        family = _text(item.get("family")) if isinstance(item, dict) else None
        if family is None:
            unnamed += 1
        elif family in rows:
            repeated.append(family)
        else:
            rows[family] = _trim(item)
    if not rows:
        raise ValueError(f"{URL}: the family list names no family")
    notes = []
    if unnamed:
        notes.append(f"rows without a family name skipped: {unnamed}")
    if repeated:
        notes.append(f"repeated families kept once: {', '.join(sorted(set(repeated)))}")
    return [rows[name] for name in sorted(rows)], notes


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


# --- parse: records --------------------------------------------------------------------------


def _text(value: object) -> str | None:
    """A non-empty string as given, else None."""
    return value if isinstance(value, str) and value.strip() else None


def _texts(value: object) -> tuple[str, ...]:
    """The non-empty strings of a list, first occurrence kept, in order."""
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(v for v in value if _text(v) is not None))


def _day(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _rank(value: object) -> float | None:
    """A popularity rank: a positive integer (bools are not ranks)."""
    if type(value) is int and value > 0:
        return float(value)
    return None


def _axis_tags(row: Mapping[str, Any]) -> tuple[str, ...]:
    axes = row.get("axes")
    if not isinstance(axes, list):
        return ()
    return tuple(t for a in axes if isinstance(a, dict) and (t := _text(a.get("tag"))))


def _variable(row: Mapping[str, Any], tags: tuple[str, ...]) -> bool | None:
    """Whether the family has a (tagged) axis; None when the row has no ``axes`` list."""
    return bool(tags) if isinstance(row.get("axes"), list) else None


def _monospace(category: str | None, classifications: tuple[str, ...]) -> bool | None:
    """Google's spacing claim; None when the row has neither a category nor a classification.

    Downstream (``facts``) reads a Google False as proportional, so an empty
    row must not claim it.
    """
    if category is None and not classifications:
        return None
    return MONOSPACE in (category, *classifications)


def _latin_languages(row: Mapping[str, Any]) -> int | None:
    languages = _texts(row.get("languages"))
    if not languages:
        return None
    return sum(lang.endswith(LATIN_LANGUAGE) for lang in languages)


def drop_code(row: Mapping[str, Any], settings: Settings) -> str | None:
    """The universe drop code Google's own metadata supports, or None for a text family.

    In order: the first ``drop_names`` pattern matching the family name; "emoji"
    when emoji is the only subset besides ``menu``; "symbol" for a
    ``symbol_classifications`` classification; "proprietary" when Google says
    the family is not open source.
    """
    family = _text(row.get("family")) or ""
    for code, pattern in settings.drop_names:
        if re.search(pattern, family):
            return code
    subsets = set(_texts(row.get("subsets"))) - {MENU_SUBSET}
    if subsets == {EMOJI_SUBSET}:
        return "emoji"
    if set(_texts(row.get("classifications"))) & set(settings.symbol_classifications):
        return "symbol"
    if row.get("isOpenSource") is False:
        return "proprietary"
    return None


def _attrs(row: Mapping[str, Any], tags: tuple[str, ...]) -> tuple[tuple[str, Scalar], ...]:
    found: dict[str, Scalar] = {}
    for name, field in (("is_brand_font", "isBrandFont"), ("is_noto", "isNoto")):
        if type(row.get(field)) is bool:
            found[name] = row[field]
    modified = _day(row.get("lastModified"))
    if modified is not None:
        found["last_modified"] = modified.isoformat()
    for name, field in (("stroke", "stroke"), ("primary_language", "primaryLanguage")):
        if (text := _text(row.get(field))) is not None:
            found[name] = text
    colors = _texts(row.get("colorCapabilities"))
    if colors:
        found["color_formats"] = ",".join(colors)
    if tags:
        found["axes"] = ",".join(tags)
    return attrs(**found)


def universe_record(row: Mapping[str, Any], settings: Settings) -> UniverseRecord:
    """The universe record of one extract row (whose ``family`` is a non-empty string)."""
    family = row["family"]
    category = _text(row.get("category"))
    classifications = _texts(row.get("classifications"))
    display = _text(row.get("displayName"))
    tags = _axis_tags(row)
    return UniverseRecord(
        source=NAME,
        key=SourceKey(NAMESPACE, family),
        family=family,
        display_name=display if display != family else None,
        category=category,
        classifications=classifications,
        primary_script=_text(row.get("primaryScript")),
        subsets=tuple(sorted(set(_texts(row.get("subsets"))) - {MENU_SUBSET})),
        latin_languages=_latin_languages(row),
        is_monospace=_monospace(category, classifications),
        variable=_variable(row, tags),
        added=_day(row.get("dateAdded")),
        status="live",
        urls=(("specimen", SPECIMEN_BASE + quote_plus(family)),),
        drop=drop_code(row, settings),
        attrs=_attrs(row, tags),
    )


def popularity(row: Mapping[str, Any], day: date) -> Observation:
    """Google's popularity rank of one extract row, as of the snapshot date ``day``."""
    return Observation(
        source=NAME,
        series=SERIES,
        key=SourceKey(NAMESPACE, row["family"]),
        value=_rank(row.get("popularity")),
        unit="rank",
        start=day,
        end=day,
    )


class GoogleMetadata(CollectorBase):
    """``fonts.google.com/metadata/fonts``: Google families plus the popularity fallback."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (HOST,)
    emits: ClassVar[tuple[type, ...]] = (UniverseRecord, Observation)
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """One GET into ``ctx.raw``; write the trimmed ``families.jsonl.gz`` extract."""
        settings = _settings(ctx.settings)
        result = ctx.fetcher.get(settings.url, to=ctx.raw.file(RAW_NAME))
        for record in result.to_records():
            ctx.out.record_fetch(record)
        rows, notes = extract_rows(result.json())
        check_shrink(len(rows), previous_families(ctx.previous), settings.min_share)
        for note in notes:
            ctx.log.warning("%s: %s", self.name, note)
            ctx.out.note(note)
        ctx.out.write_jsonl(EXTRACT, rows)
        ctx.out.set_data_date(ctx.run_date)
        ctx.log.info("%s: %d families (%d bytes fetched)", self.name, len(rows), result.size)

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """A universe record and a popularity observation per family of the extract."""
        settings = _settings(ctx.settings)
        day = ctx.snapshot.date
        seen: set[str] = set()
        for row in ctx.snapshot.iter_jsonl(EXTRACT):
            family = _text(row.get("family")) if isinstance(row, dict) else None
            if family is None or family in seen:
                continue
            seen.add(family)
            yield universe_record(row, settings)
            yield popularity(row, day)


COLLECTOR = GoogleMetadata()
