"""Stage "facts": category, monospace and formats (milestone-1 step 5b). Owner: agent P2b.

Sources in order: Google metadata, Fontsource, then the font's own tables
(``fontfiles``), cached by file hash. Every family gets all three facts.
Writes ``build/stage/facts.json`` ({id: Facts}).

Rules (``derive_facts``), each fact decided by the first source that has it:

- **category**: the category of ``google_metadata``, then ``google_repo`` (the
  METADATA.pb category, whose last value counts), then ``fontsource`` (its
  category, else its first classification other than "display", which counts
  only alone, because the list is alphabetical; "other" says nothing), then any
  other universe source; then "monospace" when the family is monospaced (as
  Fontsource files non-Google coding fonts); then the font's OS/2 panose and
  IBM class (``table_category``); else "sans-serif" (basis "default", listed in
  the log for review).
- **is_monospace**: Google, then Fontsource, whose classifications are
  complete (a category without "monospace" means proportional), except the
  families ``GOOGLE_MONOSPACE`` lists, which Google files as proportional
  although they are not; then the font
  tables (``post.isFixedPitch`` or panose, ``table_monospace``); then other
  sources' explicit flags (Nerd Fonts ``isMonospaced``), except that a "yes"
  from them beats a "no" from the tables, because a coding font with a few
  wide glyphs leaves ``isFixedPitch`` off; else False. A family whose category
  is "monospace" is always monospaced (the category's source decides both).
- **formats**: ``variable`` from Google or Fontsource, then the files (an
  ``fvar`` table; unread, a file whose role is "variable" or whose name follows
  the google/fonts rule ``Family[axes].ttf``), then other sources; ``static``
  is true for every family Google or Fontsource serves (both serve static
  instances), and otherwise unless every file seen is variable. Never both false.

``basis`` names the deciding source ("google_metadata", "fontsource",
"font_file", "default", ...) when one decided all three facts, else
``category=<b>;is_monospace=<b>;formats=<b>``.

The stage reads one font file per family that the metadata leaves open (a
Regular with a known sha256 first), through ``fontfiles.facts_for`` and the
store's cache. A live run records its reads in the ``font_facts``
pseudo-source; a replay resolves the same files from that record and the cache.
"""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args

from tff_catalog import fontfiles, records, stageio
from tff_catalog.fontfiles import FileRead, FontFacts, FontFileCache, ReadLog
from tff_catalog.keys import match_key

if TYPE_CHECKING:
    import logging

    from tff_catalog.fetch import Fetcher
    from tff_catalog.records import FontFileRef, UniverseRecord
    from tff_catalog.stages import StageContext
    from tff_catalog.universe import Universe

# The catalog-site vocabulary (schemas/catalog-site.schema.json).
Category = Literal["sans-serif", "serif", "display", "handwriting", "monospace"]
CATEGORIES: tuple[Category, ...] = get_args(Category)

GOOGLE_SOURCES = ("google_metadata", "google_repo")
FONTSOURCE = "fontsource"
# Sources whose classification is complete: no "monospace" among it means proportional.
METADATA_SOURCES = (*GOOGLE_SOURCES, FONTSOURCE)
FONT_FILE = "font_file"
DEFAULT = "default"
DEFAULT_CATEGORY: Category = "sans-serif"
MAX_TRIES = 3  # font files tried per family before giving up
FLUSH_EVERY = 100  # new cache rows kept in memory at most, so a crash loses little

STAGE = "facts"
# Monospaced families that Google's metadata files as "Sans Serif" with no Monospace
# classification (2026-09-26), so Google's "not monospaced" would put them in the wrong
# Spacing filter and out of the Coding rank. Names in match_key form.
GOOGLE_MONOSPACE = frozenset(
    match_key(name)
    for name in ("Atkinson Hyperlegible Mono", "Cascadia Code", "Cascadia Mono", "SUSE Mono")
)
_ROLE_ORDER = {"regular": 0, "variable": 1, "italic": 2, "other": 3}


@dataclass(frozen=True, slots=True)
class Facts:
    category: Category
    is_monospace: bool
    variable: bool
    static: bool
    basis: str  # which source decided: "google_metadata", "fontsource", "font_file", ...


# --- categories ---------------------------------------------------------------------------------

_WORDS: dict[str, Category] = {
    "sans serif": "sans-serif",
    "sansserif": "sans-serif",
    "sans": "sans-serif",
    "serif": "serif",
    "slab serif": "serif",
    "slab": "serif",
    "display": "display",
    "decorative": "display",
    "handwriting": "handwriting",
    "handwritten": "handwriting",
    "script": "handwriting",
    "calligraphy": "handwriting",
    "monospace": "monospace",
    "monospaced": "monospace",
    "mono": "monospace",
    "fixed width": "monospace",
    "fixed pitch": "monospace",
}


def normalise_category(raw: str, source: str) -> Category | None:
    """Map a source's category word ("Sans Serif", "sans-serif", "mono") to ``Category``.

    Case, spaces, hyphens and underscores do not matter, so Google's
    "SANS_SERIF" and "Sans Serif" and Fontsource's "sans-serif" agree. Words
    that name no text category ("other", "symbols") give None. The sources'
    vocabularies do not clash today, so ``source`` changes nothing yet; it is
    here for the day one does.
    """
    words = " ".join(re.split(r"[\s_\-]+", raw.strip().casefold())).strip()
    return _WORDS.get(words)


def table_category(ff: FontFacts) -> Category | None:
    """A category from the font's own OS/2 panose and IBM class, when it declares one."""
    if ff.panose_family == 3:  # Latin Hand Written
        return "handwriting"
    if ff.panose_family == 4:  # Latin Decorative
        return "display"
    if ff.panose_family == 2 and ff.panose_serif is not None:
        if 11 <= ff.panose_serif <= 15:  # normal, obtuse, perpendicular sans; flared; rounded
            return "sans-serif"
        if 2 <= ff.panose_serif <= 10:  # cove ... triangle serifs
            return "serif"
    ibm_class = (ff.family_class or 0) >> 8
    if ibm_class in (1, 2, 3, 4, 5, 7):  # oldstyle, transitional, modern, clarendon, slab, freeform
        return "serif"
    return {8: "sans-serif", 9: "display", 10: "handwriting"}.get(ibm_class)


def table_monospace(ff: FontFacts) -> bool | None:
    """Monospaced by the tables: ``post.isFixedPitch``, or panose spacing; None when neither is there.

    Panose's fourth digit is proportion for Latin Text (9 = monospaced) and
    spacing for Latin Hand Written and Symbol (3 = monospaced).
    """
    if ff.is_fixed_pitch is None and ff.panose_proportion is None:
        return None
    if ff.is_fixed_pitch:
        return True
    if ff.panose_family in (3, 5):
        return ff.panose_proportion == 3
    return ff.panose_proportion == 9


# --- deriving -----------------------------------------------------------------------------------


def _source_rank(source: str) -> int:
    return METADATA_SOURCES.index(source) if source in METADATA_SOURCES else len(METADATA_SOURCES)


def _record_order(r: UniverseRecord) -> tuple[Any, ...]:
    # records.sort_key last: two records of one key still sort the same whatever the input order.
    return (_source_rank(r.source), r.source, r.status != "live", records.sort_key(r))


def _record_category(r: UniverseRecord) -> Category | None:
    if r.category and (cat := normalise_category(r.category, r.source)) is not None:
        return cat
    if r.source in GOOGLE_SOURCES:
        return None
    # Fontsource lists its classifications alphabetically, so "display" comes first in
    # ["display", "sans-serif"]: it counts only when the list names nothing else.
    cats = [c for w in r.classifications if w and (c := normalise_category(w, r.source))]
    specific = [c for c in cats if c != "display"]
    return (specific or cats or [None])[0]


def _record_monospace(r: UniverseRecord) -> bool | None:
    if r.source in GOOGLE_SOURCES and match_key(r.family) in GOOGLE_MONOSPACE:
        return True
    if r.is_monospace is not None:
        return r.is_monospace
    words = [w for w in (r.category, *r.classifications) if w]
    if any(normalise_category(w, r.source) == "monospace" for w in words):
        return True
    if r.source in METADATA_SOURCES and _record_category(r) is not None:
        return False
    return None


def _first[T](items: Iterable[tuple[T | None, str]]) -> tuple[T, str] | None:
    for value, basis in items:
        if value is not None:
            return value, basis
    return None


def _monospace(
    recs: list[UniverseRecord], files: list[tuple[FontFileRef, FontFacts | None]]
) -> tuple[bool, str]:
    listed = [r for r in recs if r.source in METADATA_SOURCES]
    found = _first((_record_monospace(r), r.source) for r in listed)
    if found is not None:
        return found
    table = _first((table_monospace(ff), FONT_FILE) for _, ff in files if ff is not None)
    others = [
        (v, r.source)
        for r in recs
        if r.source not in METADATA_SOURCES and (v := _record_monospace(r)) is not None
    ]
    if table is not None and table[0]:
        return table
    says_yes = next(((v, b) for v, b in others if v), None)
    if says_yes is not None:
        return says_yes
    if table is not None:
        return table
    return others[0] if others else (False, DEFAULT)


def _category(
    recs: list[UniverseRecord],
    files: list[tuple[FontFileRef, FontFacts | None]],
    mono: tuple[bool, str],
) -> tuple[Category, str]:
    found = _first((_record_category(r), r.source) for r in recs)
    if found is not None:
        return found
    if mono[0]:
        return "monospace", mono[1]
    table = _first((table_category(ff), FONT_FILE) for _, ff in files if ff is not None)
    return table if table is not None else (DEFAULT_CATEGORY, DEFAULT)


def _file_kind(ref: FontFileRef, ff: FontFacts | None) -> bool | None:
    """True for a variable file, False for a static one, None when unknown.

    Without the file's facts, its role says so, or the google/fonts naming rule
    (a variable font is ``Family[axes].ttf``).
    """
    if ff is not None:
        return bool(ff.axes)
    name = ref.url.split("?", 1)[0].rsplit("/", 1)[-1]
    if ref.role == "variable" or re.search(r"(\[|%5B)[^/]*(\]|%5D)", name, re.IGNORECASE):
        return True
    return None


def _formats(
    recs: list[UniverseRecord], files: list[tuple[FontFileRef, FontFacts | None]]
) -> tuple[bool, bool, str]:
    listed = [r for r in recs if r.source in METADATA_SOURCES]
    kinds = [k for ref, ff in files if (k := _file_kind(ref, ff)) is not None]
    only_variable_files = bool(kinds) and all(kinds)
    static = bool(listed) or not only_variable_files
    found = _first((r.variable, r.source) for r in listed)
    if found is None and kinds:
        found = (any(kinds), FONT_FILE)
    if found is None:
        found = _first((r.variable, r.source) for r in recs if r.source not in METADATA_SOURCES)
    variable, basis = found if found is not None else (False, DEFAULT)
    if not variable and not static:  # a source says static-only, the files say variable-only
        static = True
    return variable, static, basis


def _basis(category: str, monospace: str, formats: str) -> str:
    if category == monospace == formats:
        return category
    return f"category={category};is_monospace={monospace};formats={formats}"


def family_facts(recs: Iterable[UniverseRecord], font_facts: Mapping[str, FontFacts]) -> Facts:
    """The facts of one family from its records and the facts of its files (by sha256)."""
    ordered = sorted(recs, key=_record_order)
    files = _files(ordered, font_facts)
    mono = _monospace(ordered, files)
    category, category_basis = _category(ordered, files, mono)
    if category == "monospace" and not mono[0]:
        # The "mono" category and the Spacing filter must agree (a source's
        # "monospace" category word is a monospace claim).
        mono = (True, category_basis)
    variable, static, formats_basis = _formats(ordered, files)
    return Facts(
        category=category,
        is_monospace=mono[0],
        variable=variable,
        static=static,
        basis=_basis(category_basis, mono[1], formats_basis),
    )


def _files(
    recs: list[UniverseRecord], font_facts: Mapping[str, FontFacts]
) -> list[tuple[FontFileRef, FontFacts | None]]:
    """Each distinct file of the family with its facts when known, best candidates first."""
    seen: dict[str, tuple[FontFileRef, FontFacts | None]] = {}
    for ref in candidates(recs, readable_only=False):
        if ref.url not in seen:
            seen[ref.url] = (ref, font_facts.get(ref.sha256) if ref.sha256 else None)
    return list(seen.values())


def eligible_ids(u: Universe) -> list[str]:
    """Ids of the families without a drop reason, sorted."""
    return sorted(fid for fid, fam in u.families.items() if fam.drop is None)


def group_records(u: Universe, recs: Iterable[UniverseRecord]) -> dict[str, list[UniverseRecord]]:
    """Records of every eligible family (by id); records whose key maps nowhere are left out.

    A record belongs to the family listing its key, else to ``Universe.by_key``'s answer,
    else (a several-families key) to the family its record was placed in.
    """
    owner = {key: fid for fid in eligible_ids(u) for key in u.families[fid].keys}
    grouped: dict[str, list[UniverseRecord]] = {fid: [] for fid in eligible_ids(u)}
    for r in recs:
        fid = owner.get(r.key)
        if fid is None and (fam := u.by_key(r.key)) is not None and fam.id in grouped:
            fid = fam.id
        if fid is None and (shared := u.record_owner(r.key, r.family)) in grouped:
            fid = shared
        if fid is not None:
            grouped[fid].append(r)
    return {fid: sorted(rs, key=_record_order) for fid, rs in grouped.items()}


def derive_facts(
    u: Universe, recs: Iterable[UniverseRecord], font_facts: Mapping[str, FontFacts]
) -> dict[str, Facts]:
    """Facts for every eligible family; ``font_facts`` is keyed by file sha256."""
    return {fid: family_facts(rs, font_facts) for fid, rs in group_records(u, recs).items()}


# --- which files to read ------------------------------------------------------------------------


def settled_by_metadata(recs: Iterable[UniverseRecord]) -> bool:
    """Whether Google or Fontsource records settle the category and spacing, so no file need be read.

    ``variable`` alone never sends the stage to the files of a family Google or
    Fontsource lists: file roles and names answer it, and reading one file per
    Google family would download about a gigabyte.
    """
    listed = [r for r in recs if r.source in METADATA_SOURCES]
    return any(_record_category(r) is not None for r in listed) and any(
        _record_monospace(r) is not None for r in listed
    )


def candidates(recs: Iterable[UniverseRecord], *, readable_only: bool = True) -> list[FontFileRef]:
    """The family's font files, best first: known sha256, Regular, Google/Fontsource, then url.

    ``readable_only`` keeps only font files and zip members (not archives or pages).
    """
    refs: dict[str, tuple[tuple[bool, int, int, str], FontFileRef]] = {}
    for r in recs:
        for ref in r.files:
            if readable_only and not fontfiles.is_readable_url(ref.url):
                continue
            order = (
                ref.sha256 is None,
                _ROLE_ORDER.get(ref.role, len(_ROLE_ORDER)),
                _source_rank(r.source),
                ref.url,
            )
            if ref.url not in refs or order < refs[ref.url][0]:
                refs[ref.url] = (order, ref)
    return [ref for _, ref in sorted(refs.values(), key=lambda item: item[0])]


# --- the stage ----------------------------------------------------------------------------------


def universe_records(records_dir: Path) -> list[UniverseRecord]:
    """Every ``UniverseRecord`` in ``build/stage/records/*.jsonl`` (baseline files skipped)."""
    out: list[UniverseRecord] = []
    # A cheap pre-filter that skips parsing the (many) ranking rows; the parsed
    # type decides, so the JSON's spacing does not matter.
    marker = b"UniverseRecord"
    for path in sorted(Path(records_dir).glob("*.jsonl")):
        if "@" in path.name:  # <source>@<baseline>.jsonl: lifetime counters, never universe
            continue
        for line in path.read_bytes().splitlines():
            if marker in line:
                rec = records.from_json(json.loads(line))
                if isinstance(rec, records.UniverseRecord):
                    out.append(rec)
    return out


@dataclass(slots=True)
class _Resolver:
    """Finds the facts of font files: cache, the day's recorded reads in replay, else the network."""

    cache: FontFileCache
    fetcher: Fetcher | None  # None in replay
    recorded: Mapping[str, FileRead]  # replay: what the live run read that day
    log: logging.Logger
    reads: ReadLog = field(default_factory=ReadLog)  # live: every file used, for the store

    def facts(self, ref: FontFileRef) -> FontFacts | None:
        """The file's facts, or None (logged) when it cannot be had."""
        if self.fetcher is None:
            return self._replay(ref)
        from httpx import InvalidURL

        from tff_catalog.fetch import FetchError, HostNotAllowed

        earlier = self.reads.reads.get(ref.url)
        if earlier is not None and earlier.sha256 is None:
            return None  # failed already this run (another family shares the file)
        try:
            ff = fontfiles.facts_for(ref, self.fetcher, self.cache)
        # ValueError covers fontfiles.FontFileError; one bad url must not stop the stage.
        except (FetchError, HostNotAllowed, InvalidURL, OSError, ValueError) as exc:
            self.log.warning("font file %s: %s", ref.url, exc)
            self.reads.add(FileRead(ref.url, None, str(exc)[:300]))
            return None
        self.reads.add(FileRead(ref.url, ff.sha256))
        if self.cache.pending >= FLUSH_EVERY:
            self.cache.flush()
        return ff

    def _replay(self, ref: FontFileRef) -> FontFacts | None:
        read = self.recorded.get(ref.url)
        if read is None:
            return fontfiles.cached_facts(ref, self.cache)
        if read.sha256 is None:  # the live run's answer, failures included
            return None
        ff = self.cache.get(sha256=read.sha256)
        if ff is None:  # the store lacks rows the live run wrote: this replay will differ
            self.log.warning("replay: no cached facts for %s (sha256 %s)", ref.url, read.sha256)
        return ff


def resolve_files(
    grouped: Mapping[str, list[UniverseRecord]], resolver: _Resolver
) -> dict[str, FontFacts]:
    """Read one file for each family the metadata leaves open; url -> facts."""
    found: dict[str, FontFacts] = {}
    for fid in sorted(grouped):
        recs = grouped[fid]
        if settled_by_metadata(recs):
            continue
        for ref in candidates(recs)[:MAX_TRIES]:
            ff = found.get(ref.url) or resolver.facts(ref)
            if ff is not None:
                found[ref.url] = ff
                break
    return found


def with_hashes(
    recs: Iterable[UniverseRecord], by_url: Mapping[str, FontFacts]
) -> list[UniverseRecord]:
    """Records whose file refs without a sha256 get the one their url turned out to have."""
    out = []
    for r in recs:
        files = tuple(
            replace(ref, sha256=by_url[ref.url].sha256)
            if ref.sha256 is None and ref.url in by_url
            else ref
            for ref in r.files
        )
        out.append(replace(r, files=files) if files != r.files else r)
    return out


def _open_cache(ctx: StageContext) -> FontFileCache:
    if ctx.store is not None:
        return FontFileCache(fontfiles.cache_path(ctx.store.root))
    ctx.log.warning("TFF_STORE is not set: font facts are cached in build/cache only")
    return FontFileCache(ctx.paths.cache / "fontfacts.jsonl")


def run(ctx: StageContext) -> None:
    """Stage "facts"."""
    u = stageio.load_stage(ctx.paths, "universe")
    grouped = group_records(u, universe_records(ctx.paths.records))
    cache = _open_cache(ctx)
    replay_day = ctx.options.from_snapshots
    recorded: dict[str, FileRead] = {}
    if ctx.fetcher is None and ctx.store is not None:
        recorded = fontfiles.recorded_reads(ctx.store, replay_day or ctx.run_date, prefer=STAGE)
    resolver = _Resolver(cache, ctx.fetcher, recorded, ctx.log)
    by_url = resolve_files(grouped, resolver)
    cache.flush()
    if ctx.fetcher is not None and ctx.store is not None and resolver.reads.reads:
        try:
            fontfiles.record_reads(
                ctx.store,
                ctx.run_date,
                STAGE,
                resolver.reads.reads.values(),
                frozen=ctx.state.frozen_dates(fontfiles.READS_SOURCE),
            )
        except Exception as exc:  # a missing record costs replay fidelity, not this run
            ctx.log.warning("could not record font reads in the store: %s", exc)
    font_facts = {ff.sha256: ff for ff in by_url.values()}
    out = {
        fid: family_facts(with_hashes(recs, by_url), font_facts) for fid, recs in grouped.items()
    }
    stageio.dump_stage(ctx.paths, "facts", out)
    _report(ctx, out, len(by_url), resolver)


def fact_bases(f: Facts) -> dict[str, str]:
    """The deciding source of each fact, unpacked from ``Facts.basis``."""
    if "=" not in f.basis:
        return dict.fromkeys(("category", "is_monospace", "formats"), f.basis)
    return {fact: basis for fact, _, basis in (p.partition("=") for p in f.basis.split(";"))}


def _report(ctx: StageContext, out: Mapping[str, Facts], files: int, resolver: _Resolver) -> None:
    bases: dict[str, Counter[str]] = defaultdict(Counter)
    for f in out.values():
        for fact, basis in fact_bases(f).items():
            bases[fact][basis] += 1
    failed = sum(1 for r in resolver.reads.reads.values() if r.sha256 is None)
    ctx.log.info("facts: %d families, %d font files used, %d reads failed", len(out), files, failed)
    for fact in sorted(bases):
        ctx.log.info("facts: %s decided by %s", fact, dict(sorted(bases[fact].items())))
    defaulted: dict[str, list[str]] = defaultdict(list)
    for fid, f in sorted(out.items()):
        if DEFAULT in f.basis:
            defaulted[f.basis].append(fid)
    for basis, ids in sorted(defaulted.items()):
        ctx.log.info("facts: %d families by %s: %s", len(ids), basis, ", ".join(ids[:50]))
