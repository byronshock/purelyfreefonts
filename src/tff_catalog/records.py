"""The records every collector emits (design-m1 §2.1). Frozen contract.

A collector's ``parse()`` turns one snapshot into these records, offline and
deterministically. Nothing downstream reads a snapshot directly.

JSON form (``to_json``/``from_json``, one object per line in
``build/stage/records/<source>.jsonl``, schema ``schemas/stage/records.schema.json``):

- ``{"type": "<class name>", <every field>}``; every field is always written.
- Dates are ISO strings; tuples are lists; a ``SourceKey`` is ``{"ns", "key"}``;
  a ``FontFileRef`` is an object of its fields.
- ``attrs`` is a JSON object (keys sorted); in Python it is a sorted tuple of
  ``(key, value)`` pairs, so records stay hashable.
- ``Observation.value`` is always written as a float, so ``5066`` and
  ``5066.0`` give the same bytes.

``sort_key`` orders records canonically, so a sorted JSONL file is byte-stable.

``attrs`` names in ``RESERVED_ATTRS`` have one meaning and type in every
collector, because downstream stages read them: a collector that has the fact
must use that name, and must not use the name for anything else. Other attrs
names are free. In particular, a ranking collector read by an engine source
whose ``exposure`` is in ``EXPOSURE_ATTR_KINDS`` puts ``first_seen`` on every
Observation of that source's series (design-m1 gap G3); the contract test
checks it.
"""

from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args

from tff_catalog import jsonio

Scalar = str | int | float | bool
Attrs = tuple[tuple[str, Scalar], ...]  # sorted by key; hashable; JSON-stable


def attrs(**kw: Scalar) -> Attrs:
    """Build an ``Attrs`` tuple from keyword arguments, sorted by key."""
    return tuple(sorted(kw.items()))


NAMESPACES = frozenset(
    {
        "gf-family",
        "gf-dir",
        "fs-id",
        "npm",
        "brew-cask",
        "arch-pkg",
        "deb-pkg",
        "deb-src",
        "nerd-folder",
        "gh-asset",
        "fot-name",
        "almanac-name",
        "fontist-formula",
        "font-name",
        "foundry-family",
        "system",
    }
)
GROUPS = frozenset(
    {
        "google",
        "fot",
        "http_archive",
        "npm_registry",
        "jsdelivr",
        "homebrew",
        "arch",
        "debian",
        "github_counters",
        "nerd",  # its own group since the owner's ruling of 2026-09-29 (gate R, R1)
        "flutter",
    }
)  # independence groups, methodology §6; gate T3 dropped Chocolatey from v1

Unit = Literal[
    "installs",
    "share",
    "downloads",
    "views",
    "sites",
    "dependents",
    "hits",
    "rank",
    "pages",
    "requests",
]
Status = Literal["live", "queued", "deprecated", "delisted"]
FileRole = Literal["regular", "variable", "italic", "other"]
RelationKind = Literal["depends", "recommends", "optdepends", "provides", "group", "preinstalled"]
UrlRole = Literal["homepage", "repository", "minisite", "specimen", "license"]
DropReason = Literal[
    "icon", "emoji", "symbol", "barcode", "math", "music", "proprietary", "non-font"
]

AttrKind = Literal["str", "int", "bool", "date"]  # "date": an ISO string YYYY-MM-DD

# attrs names with one agreed meaning (name -> (kind, meaning)). Frozen contract.
RESERVED_ATTRS: dict[str, tuple[AttrKind, str]] = {
    "first_seen": (
        "date",
        "first day this key was available on the channel: the Homebrew cask's add date, "
        "the npm package's first non-zero download day, the GitHub asset's created_at "
        "(exposure, design-m1 gap G3)",
    ),
    "tap": ("str", "Homebrew tap of the cask; legacy prefixes kept, third-party taps excluded"),
    "samples": ("int", "pkgstats: systems that reported in the month (the share's denominator)"),
    "submissions": ("int", "Debian popcon: total submissions in the report"),
    "month": ("str", "YYYY-MM of a monthly value"),
    "release": ("str", "GitHub or Nerd Fonts: the release tag the asset belongs to"),
    "prerelease": ("bool", "GitHub: the release is marked as a prerelease"),
    "published_at": ("date", "GitHub: the day the release was published"),
    "method": ("str", "Fonts Over Time: how the site was measured (browser or static)"),
    "category": ("str", "Fonts Over Time: the site's category (the startup cap, D11)"),
    "last_synced_at": ("date", "ecosyste.ms: the day the package was last synced"),
    "distro": (
        "str",
        "the package system a distro record comes from, a config/site.toml [package_systems] "
        "id (arch, cachyos, debian); on a Relation, the system of that dependency edge",
    ),
    "asset": ("str", "GitHub or Nerd Fonts: the release asset's file name"),
    "font_like": (
        "bool",
        "a distro package that is a font package: Section fonts, a font package name, the "
        "nerd-fonts group, or provides ttf-font",
    ),
    "url": ("str", "Homebrew: the cask's download URL (gate M5 matches it to GitHub assets)"),
    "category_sites": (
        "int",
        "Fonts Over Time: the sites counted that week for the row's category and method "
        "(the denominator of the D11 startup cap)",
    ),
}
# Not reserved: "repo" means the pacman repository on arch_repos records and
# owner/name on GitHub Observations; readers tell them apart by source.
# Engine-source exposures that come from a collector's first_seen attr (config_model.Exposure).
EXPOSURE_ATTR_KINDS = frozenset({"add_date", "first_nonzero_day", "asset_created"})

UNITS = frozenset(get_args(Unit))
STATUSES = frozenset(get_args(Status))
FILE_ROLES = frozenset(get_args(FileRole))
RELATION_KINDS = frozenset(get_args(RelationKind))
URL_ROLES = frozenset(get_args(UrlRole))
DROP_REASONS = frozenset(get_args(DropReason))


@dataclass(frozen=True, slots=True, order=True)
class SourceKey:
    """A key in one namespace: ``SourceKey("npm", "@fontsource/inter")``."""

    ns: str
    key: str


@dataclass(frozen=True, slots=True)
class Observation:
    """Ranking collectors: one row per source key, never per family."""

    source: str  # collector name
    series: str  # "365d" | "2026-08" | "lifetime" | "2026-W39" | "pages/desktop"
    key: SourceKey
    value: float | None  # None = inside the frame, no usable value
    unit: Unit
    start: date  # data window; exposure is counted from `end`
    end: date
    attrs: Attrs = ()  # e.g. tap, category, site, month, release


@dataclass(frozen=True, slots=True)
class FontFileRef:
    # commit- or version-pinned where possible; "<archive>.zip#<member path>" names one font
    # inside a zip archive (fontfiles.member_url), read by range; its sha256 is the member's
    url: str
    sha256: str | None = None
    size: int | None = None
    role: FileRole = "regular"
    codepoints: int | None = None  # when the source inspected the file (Fontsource registry)
    unicode_range: str | None = None
    # git blob sha1 when the source lists it (the google/fonts tree): a facts-cache
    # key that survives the monthly commit pin of ``url`` (design-m1 gap G8)
    git_blob: str | None = None


@dataclass(frozen=True, slots=True)
class UniverseRecord:
    source: str
    key: SourceKey
    family: str
    display_name: str | None = None
    category: str | None = None  # source vocabulary; facts.py normalises it
    classifications: tuple[str, ...] = ()
    primary_script: str | None = None
    subsets: tuple[str, ...] = ()
    latin_languages: int | None = None
    is_monospace: bool | None = None
    variable: bool | None = None
    added: date | None = None
    status: Status = "live"
    urls: tuple[tuple[str, str], ...] = ()  # (homepage|repository|minisite|specimen|license, url)
    files: tuple[FontFileRef, ...] = ()
    names: tuple[tuple[str, str], ...] = ()  # (alias, relation) that the source itself asserts
    drop: str | None = None  # icon|emoji|symbol|barcode|math|music|proprietary|non-font
    attrs: Attrs = ()


@dataclass(frozen=True, slots=True)
class LicenseFact:
    source: str
    key: SourceKey
    raw: str  # exactly as the source says it
    spdx: str | None = None  # only if the source itself speaks SPDX
    text_url: str | None = None
    text_sha256: str | None = None
    rfn: bool | None = None
    attrs: Attrs = ()


@dataclass(frozen=True, slots=True)
class Relation:
    """Dependency and preinstall edges (step 10)."""

    source: str
    subject: SourceKey  # the package or system that pulls the object in
    kind: RelationKind
    object: SourceKey
    alt: int = 0  # position inside an `a | b` alternative group
    attrs: Attrs = ()


Record = Observation | UniverseRecord | LicenseFact | Relation
RECORD_TYPES: dict[str, type] = {
    c.__name__: c for c in (Observation, UniverseRecord, LicenseFact, Relation)
}
_TYPE_ORDER = {name: i for i, name in enumerate(RECORD_TYPES)}

_LITERALS: dict[str, frozenset[str]] = {
    "unit": UNITS,
    "status": STATUSES,
    "role": FILE_ROLES,
    "kind": RELATION_KINDS,
}


# --- encoding ---------------------------------------------------------------


def _file_to_json(ref: FontFileRef) -> dict[str, Any]:
    return {f.name: getattr(ref, f.name) for f in fields(FontFileRef)}


def _encode(name: str, value: Any) -> Any:
    if name == "attrs":
        return dict(value)
    if name == "value":
        return None if value is None else float(value)
    if isinstance(value, SourceKey):
        return {"ns": value.ns, "key": value.key}
    if isinstance(value, date):
        return value.isoformat()
    if name == "files":
        return [_file_to_json(ref) for ref in value]
    if isinstance(value, tuple):
        return [list(v) if isinstance(v, tuple) else v for v in value]
    return value


def to_json(r: Record) -> dict[str, Any]:
    """Return the JSON object of a record: ``{"type": ..., <every field>}``."""
    out: dict[str, Any] = {"type": type(r).__name__}
    for f in fields(r):
        out[f.name] = _encode(f.name, getattr(r, f.name))
    return out


# --- decoding ---------------------------------------------------------------


def _key(v: Any) -> SourceKey:
    if not isinstance(v, dict) or set(v) != {"ns", "key"}:
        raise ValueError(f"bad SourceKey {v!r}")
    return SourceKey(str(v["ns"]), str(v["key"]))


def _date(v: Any) -> date | None:
    return None if v is None else date.fromisoformat(v)


def _pairs(v: Any) -> tuple[tuple[str, str], ...]:
    out = []
    for pair in v:
        if len(pair) != 2:
            raise ValueError(f"expected a pair, got {pair!r}")
        out.append((pair[0], pair[1]))
    return tuple(out)


def _file(v: Any) -> FontFileRef:
    names = {f.name for f in fields(FontFileRef)}
    unknown = set(v) - names
    if unknown:
        raise ValueError(f"unknown FontFileRef fields {sorted(unknown)}")
    ref = FontFileRef(**v)
    if ref.role not in FILE_ROLES:
        raise ValueError(f"bad FontFileRef role {ref.role!r}")
    return ref


def _attrs(v: Any) -> Attrs:
    if not isinstance(v, dict):
        raise ValueError(f"attrs must be an object, got {v!r}")
    return tuple(sorted(v.items()))


_DECODERS: dict[str, Any] = {
    "key": _key,
    "subject": _key,
    "object": _key,
    "start": _date,
    "end": _date,
    "added": _date,
    "value": lambda v: None if v is None else float(v),
    "classifications": tuple,
    "subsets": tuple,
    "urls": _pairs,
    "names": _pairs,
    "files": lambda v: tuple(_file(x) for x in v),
    "attrs": _attrs,
}


def from_json(d: dict[str, Any]) -> Record:
    """Rebuild a record from ``to_json`` output. Unknown fields and bad enums raise ``ValueError``."""
    try:
        cls = RECORD_TYPES[d["type"]]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unknown record type in {d!r}") from exc
    names = {f.name for f in fields(cls)}
    unknown = set(d) - names - {"type"}
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown fields {sorted(unknown)}")
    kwargs = {}
    for name in sorted(names & set(d)):
        decode = _DECODERS.get(name)
        try:
            kwargs[name] = decode(d[name]) if decode else d[name]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{cls.__name__}.{name}: {exc}") from exc
        allowed = _LITERALS.get(name)
        if allowed is not None and kwargs[name] not in allowed:
            raise ValueError(f"{cls.__name__}.{name}: {kwargs[name]!r} not in {sorted(allowed)}")
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"{cls.__name__}: {exc}") from exc


# --- ordering and files -----------------------------------------------------


def attr_problems(a: Attrs) -> list[str]:
    """Reserved attrs (``RESERVED_ATTRS``) with the wrong kind of value, one line each."""
    problems = []
    for name, value in a:
        if name not in RESERVED_ATTRS:
            continue
        kind = RESERVED_ATTRS[name][0]
        if kind == "bool":
            ok = type(value) is bool
        elif kind == "int":
            ok = type(value) is int
        elif kind == "str":
            ok = type(value) is str
        else:
            ok = type(value) is str and _is_iso_date(value)
        if not ok:
            problems.append(f"attrs.{name} must be {_KIND_WORDS[kind]}, got {value!r}")
    return problems


_KIND_WORDS = {
    "str": "a string",
    "int": "an integer",
    "bool": "true or false",
    "date": "an ISO date (YYYY-MM-DD)",
}


def _is_iso_date(text: str) -> bool:
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def _primary(r: Record) -> SourceKey:
    return r.subject if isinstance(r, Relation) else r.key


def sort_key(r: Record) -> tuple[int, str, str, str, str]:
    """Canonical order: type, source, primary key, then the canonical JSON as tie-break."""
    pk = _primary(r)
    return (
        _TYPE_ORDER[type(r).__name__],
        r.source,
        pk.ns,
        pk.key,
        jsonio.canonical_str(to_json(r)),
    )


def write_jsonl(records: list[Record] | tuple[Record, ...], path: Path) -> int:
    """Write records to JSON Lines in canonical order; return the row count."""
    return jsonio.dump_jsonl((to_json(r) for r in sorted(records, key=sort_key)), path)


def read_jsonl(path: Path) -> list[Record]:
    """Read records written by ``write_jsonl``."""
    return [from_json(d) for d in jsonio.iter_jsonl(path)]
