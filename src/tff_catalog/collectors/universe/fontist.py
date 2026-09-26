"""Universe collector "fontist": Fontist formulas (design-m1 §2.4, scout-universe §6).

**Fetch.** A sparse, blobless, depth-1 clone of ``fontist/formulas`` (settings
``repository`` and ``ref``) into ``ctx.raw``, checking out only the formulas in
``folders`` under ``Formulas/``: the root and ``sil/`` (about 1 MB). ``google/``
repeats Google Fonts and ``macos/`` holds proprietary Apple downloads, so neither
is read. The clone goes through ``gitsrc``, not the fetcher, so it is logged as a
manifest note (repository, ref, commit sha) rather than a ``fetched`` entry.

The repository has no license (docs/sources.md), so the snapshot keeps facts
only, never formula text: no descriptions, copyright lines or license texts.
Extracts:

- ``formulas.jsonl.gz``: one row per formula, sorted by ``key`` (the file's path
  under ``Formulas/`` without ``.yml``: ``fira_code``, ``sil/andika_7.000``)::

      {"key", "name", "homepage", "repository", "license_url", "spdx_license",
       "open_license": bool, "requires_license_agreement": bool,
       "resources": [{"name", "urls": [...], "sha256": str | [str] | null,
                      "file_size": int | null}],
       "fonts": [{"name", "collection": str | null,
                  "styles": [{"family_name", "preferred_family_name", "type",
                              "full_name", "post_script_name", "font"}]}]}

  Values are as the formula writes them (scalars as strings), missing ones null;
  ``fonts`` includes the fonts of ``font_collections`` with the collection's file
  name. The two flags say whether the formula carries an ``open_license`` or a
  ``requires_license_agreement`` text; the texts themselves are not kept.
- ``source.json``: repository, ref, commit sha, folders, and the formulas skipped
  (unreadable YAML, or a ``schema_version`` other than the settings').

A fetch fails when no formula has the expected ``schema_version`` (the default
branch moved to a new schema), or when it keeps fewer than ``min_share`` of the
previous snapshot's formulas (a folder moved), so the stale policy takes over.

**Parse** (offline, pure). Per formula with at least one family name:

- one ``UniverseRecord`` per family, keyed ``fontist-formula:<key>``. The family
  is each style's ``preferred_family_name`` (name ID 16), else its
  ``family_name`` (name ID 1), so "Fira Code Retina" is a style of "Fira Code".
  A formula that ships several families gives several records with one key
  (the universe keeps such a key out of every family's keys). ``urls``: the
  homepage and repository, when they are http(s) URLs. ``files``: each
  resource's first https URL (else its first http one), its sha256 (null when
  the formula lists several) and size; an archive goes to every family with
  role "other", a direct font file to the families whose styles name it (every
  family when it is the formula's only resource), role "regular" or "italic"
  from those styles' types, else "other". For an https zip archive, each
  family also gets its styles' font files as zip member references
  (``fontfiles.member_url``; the member is found by file name), with no sha256
  or size (the formula's are the archive's). ``drop``: the code of the first
  ``drop_names`` pattern that matches the family (math, symbol and dingbat
  fonts are not text); else "proprietary" when the formula requires a license
  agreement and has no open license, if ``agreement_is_proprietary``.
- one ``LicenseFact``: ``raw`` and ``spdx`` are ``spdx_license`` (Fontist speaks
  SPDX, ``LicenseRef-*`` included); ``text_url`` is ``license_url``; ``rfn`` is
  true for an ``-RFN`` id, false for ``-no-RFN``; attrs ``open_license`` and
  ``requires_license_agreement`` carry the flags. A formula that names no
  license id gives ``raw`` "NOASSERTION", unless another formula that does
  names every one of its families (by ``keys.match_key``; in practice another
  version of the same SIL font, ``sil/busra_9.200`` and ``sil/busra_9.300``):
  the licenses stage ANDs one source's facts for a family, and NOASSERTION in
  an AND voids the other formula's license.
"""

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlsplit

from tff_catalog import fontfiles, gitsrc
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.keys import match_key
from tff_catalog.records import (
    DROP_REASONS,
    FileRole,
    FontFileRef,
    LicenseFact,
    Record,
    SourceKey,
    UniverseRecord,
    attrs,
)
from tff_catalog.store import Snapshot

NAME = "fontist"
GIT_HOST = "github.com"
NS = "fontist-formula"
FORMULAS_DIR = "Formulas"
FORMULAS_EXTRACT = "formulas.jsonl.gz"
SOURCE_EXTRACT = "source.json"
NOASSERTION = "NOASSERTION"  # SPDX: the formula names no license id
PROPRIETARY = "proprietary"
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2")
_URL = re.compile(r"^https?://\S+$")  # schemas/stage/records.schema.json $defs.url
_URL_MAX = 2048
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LICENSE_ID = re.compile(r"[A-Za-z0-9.+-]+")
_STYLE_FIELDS = (
    "family_name",
    "preferred_family_name",
    "type",
    "full_name",
    "post_script_name",
    "font",
)
_ROLE_BY_TYPE: dict[str, FileRole] = {"regular": "regular", "italic": "italic"}
MIN_SHARE = 0.9
# Non-text families by name, first match wins (the other universe collectors' codes).
DROP_NAMES: tuple[tuple[str, str], ...] = (
    ("icon", r"\bIcons?\b"),
    ("emoji", r"\bEmoji\b"),
    ("barcode", r"\bBarcode\b"),
    ("math", r"Math\b"),  # "NewComputerModernMath" too
    ("music", r"\bMusic(?:al)?\b"),
    ("symbol", r"(?i)(?:\b|_)symbols?\b|\b(?:dingbats|marlett|webdings|wingdings)\b|^D050000L$"),
)


class FormulaSchemaError(RuntimeError):
    """No formula in the clone has the expected ``schema_version``."""


@dataclass(frozen=True, slots=True)
class Settings:
    """``config/sources/fontist.toml``."""

    enabled: bool = True
    repository: str = "https://github.com/fontist/formulas"
    ref: str = "HEAD"  # a branch, tag or commit sha; HEAD is the remote's default branch
    schema_version: int = 5  # formulas with another schema_version are skipped
    folders: tuple[str, ...] = (".", "sil")  # under Formulas/; "." is Formulas/ itself
    min_share: float = MIN_SHARE  # of the previous snapshot's formulas, or the fetch fails
    drop_names: tuple[tuple[str, str], ...] = DROP_NAMES
    agreement_is_proprietary: bool = True  # drop code for requires_license_agreement

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        parts = urlsplit(self.repository)
        if parts.scheme != "https" or parts.hostname != GIT_HOST:
            raise ConfigError(f"{where}.repository: must be an https URL on {GIT_HOST}")
        if not self.folders:
            raise ConfigError(f"{where}.folders: must name at least one folder")
        for folder in self.folders:
            path = PurePosixPath(folder)
            if not folder.strip() or path.is_absolute() or ".." in path.parts:
                raise ConfigError(f"{where}.folders: {folder!r} is not a path under Formulas/")
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


# --- fetch: formula files to fact rows --------------------------------------------------------


def _text(value: object) -> str | None:
    """A YAML scalar as the formula writes it: strings as they are, numbers as text."""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _sha(value: object) -> str | list[str] | None:
    if isinstance(value, list):
        return [s for s in (_text(v) for v in value) if s is not None]
    return _text(value)


def _size(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(doc: Mapping[str, Any], name: str) -> bool:
    """The formula carries the license text ``name`` (``false`` or a blank text is none)."""
    value = doc.get(name)
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, False) and bool(value)


def _resources(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    found = doc.get("resources")
    if not isinstance(found, Mapping):
        return []
    return [_resource(str(name), found[name]) for name in sorted(found, key=str)]


def _resource(name: str, spec: object) -> dict[str, Any]:
    spec = spec if isinstance(spec, Mapping) else {}
    urls = spec.get("urls")
    return {
        "name": name,
        "urls": [u for u in map(_text, urls if isinstance(urls, list) else ()) if u is not None],
        "sha256": _sha(spec.get("sha256")),
        "file_size": _size(spec.get("file_size")),
    }


def _styles(font: Mapping[str, Any]) -> list[dict[str, str | None]]:
    styles = font.get("styles")
    if not isinstance(styles, list):
        return []
    return [
        {field: _text(style.get(field)) for field in _STYLE_FIELDS}
        for style in styles
        if isinstance(style, Mapping)
    ]


def _font_rows(fonts: object, collection: str | None) -> list[dict[str, Any]]:
    if not isinstance(fonts, list):
        return []
    return [
        {"name": _text(font.get("name")), "collection": collection, "styles": _styles(font)}
        for font in fonts
        if isinstance(font, Mapping)
    ]


def _fonts(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _font_rows(doc.get("fonts"), None)
    collections = doc.get("font_collections")
    for coll in collections if isinstance(collections, list) else ():
        if isinstance(coll, Mapping):
            rows += _font_rows(coll.get("fonts"), _text(coll.get("filename")))
    return rows


def formula_row(key: str, doc: Mapping[str, Any]) -> dict[str, Any]:
    """The extract row of one formula: facts only (module docstring), no formula text."""
    return {
        "key": key,
        "name": _text(doc.get("name")),
        "homepage": _text(doc.get("homepage")),
        "repository": _text(doc.get("repository")),
        "license_url": _text(doc.get("license_url")),
        "spdx_license": _text(doc.get("spdx_license")),
        "open_license": _flag(doc, "open_license"),
        "requires_license_agreement": _flag(doc, "requires_license_agreement"),
        "resources": _resources(doc),
        "fonts": _fonts(doc),
    }


def formula_files(checkout: Path, folders: Sequence[str]) -> list[tuple[str, Path]]:
    """``(key, path)`` of every ``*.yml`` directly inside ``Formulas/<folder>``, sorted by key."""
    base = checkout / FORMULAS_DIR
    found: dict[str, Path] = {}
    for folder in folders:
        directory = base / folder
        if not directory.is_dir():
            continue
        for path in directory.glob("*.yml"):
            if path.is_file():
                key = path.relative_to(base).with_suffix("").as_posix()
                found[key] = path
    return sorted(found.items())


def sparse_patterns(folders: Sequence[str]) -> list[str]:
    """Non-cone sparse-checkout patterns: the formulas directly inside each folder."""
    return [f"/{PurePosixPath(FORMULAS_DIR, folder).as_posix()}/*.yml" for folder in folders]


def read_formulas(
    files: Iterable[tuple[str, Path]], schema_version: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fact rows of the readable formulas with ``schema_version``, and why others were skipped."""
    import yaml

    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for key, path in files:
        try:
            doc = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            skipped.append(f"{key}: unreadable YAML ({type(exc).__name__})")
            continue
        if not isinstance(doc, Mapping):
            skipped.append(f"{key}: not a mapping")
            continue
        version = doc.get("schema_version")
        if version != schema_version:
            skipped.append(f"{key}: schema_version {version!r}, expected {schema_version}")
            continue
        rows.append(formula_row(key, doc))
    return rows, skipped


def previous_formulas(previous: Snapshot | None) -> int | None:
    """The formula count of an earlier snapshot, from its manifest (None: no snapshot)."""
    entry = previous.manifest.extract(FORMULAS_EXTRACT) if previous is not None else None
    return entry.rows if entry is not None else None


def check_shrink(formulas: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when ``formulas`` is under ``min_share`` of ``before``."""
    if before is not None and formulas < min_share * before:
        raise ValueError(
            f"{NAME}: {formulas} formulas, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a moved folder or a broken clone?"
        )


# --- parse: fact rows to records --------------------------------------------------------------


def _url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _URL.match(value) and len(value) <= _URL_MAX else None


def _best_url(urls: Iterable[object]) -> str | None:
    """The first https URL, else the first http one (the formula's own order)."""
    valid = [u for u in (_url(v) for v in urls) if u is not None]
    return next((u for u in valid if u.startswith("https://")), valid[0] if valid else None)


def _sha256(value: object) -> str | None:
    """One sha256; null when the formula accepts several files (a list of hashes)."""
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if _SHA256.match(value) else None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value or None


def _styles_of(row: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """``(family, style)`` for every style with a family name, in formula order.

    A collection's styles name no file of their own, so they take the collection's.
    """
    out = []
    for font in row.get("fonts") or ():
        for style in font.get("styles") or ():
            name = _clean(style.get("preferred_family_name")) or _clean(style.get("family_name"))
            if name is None:
                continue
            if style.get("font") is None and font.get("collection") is not None:
                style = {**style, "font": font["collection"]}
            out.append((name, style))
    return out


def family_names(row: Mapping[str, Any]) -> list[str]:
    """The formula's families (preferred family name, else family name), first seen first."""
    return list(dict.fromkeys(name for name, _ in _styles_of(row)))


def _role(styles: Iterable[Mapping[str, Any]]) -> FileRole:
    kinds = {(_clean(s.get("type")) or "").casefold() for s in styles}
    for kind, role in _ROLE_BY_TYPE.items():
        if kind in kinds:
            return role
    return "other"


def _is_font_file(name: str) -> bool:
    return name.casefold().endswith(FONT_SUFFIXES)


ZIP_SUFFIXES = (".zip",)  # archives fontfiles reads members of (fontfiles.ZIP_EXTENSIONS)


def files_by_family(row: Mapping[str, Any]) -> dict[str, tuple[FontFileRef, ...]]:
    """Each family's resource refs, sorted by URL (module docstring for the rules)."""
    styles = _styles_of(row)
    families = list(dict.fromkeys(name for name, _ in styles))
    resources = row.get("resources") or ()
    found: dict[str, dict[str, FontFileRef]] = {name: {} for name in families}
    for res in resources:
        url = _best_url(res.get("urls") or ())
        if url is None:
            continue
        name = str(res.get("name") or "")
        size = res.get("file_size")
        base = {"url": url, "sha256": _sha256(res.get("sha256")), "size": _size(size)}
        if not _is_font_file(name):
            for fam in families:
                found[fam][url] = FontFileRef(**base, role="other")
            if url.startswith("https://") and urlsplit(url).path.lower().endswith(ZIP_SUFFIXES):
                # Each style's file inside the zip, found by name (fontfiles reads it by
                # range); the formula's sha256 and size are the archive's, not the member's.
                for fam, style in styles:
                    font = _clean(style.get("font"))
                    if font is not None and _is_font_file(font) and "/" not in font:
                        member = fontfiles.member_url(url, font)
                        role = _role(s for f, s in styles if f == fam and s.get("font") == font)
                        found[fam].setdefault(member, FontFileRef(url=member, role=role))
            continue
        wanted = name.casefold()
        named = [(f, s) for f, s in styles if (s.get("font") or "").casefold() == wanted]
        if not named and len(resources) == 1:
            named = styles
        if not named:
            for fam in families:
                found[fam][url] = FontFileRef(**base, role="other")
            continue
        for fam in dict.fromkeys(f for f, _ in named):
            role = _role(s for f, s in named if f == fam)
            found[fam][url] = FontFileRef(**base, role=role)
    return {fam: tuple(refs[u] for u in sorted(refs)) for fam, refs in found.items()}


def _rfn(spdx: str | None) -> bool | None:
    """True for an ``-RFN`` license id, false for ``-no-RFN``, else (or both) None."""
    if spdx is None:
        return None
    ids = _LICENSE_ID.findall(spdx)
    no_rfn = any(i.casefold().endswith("-no-rfn") for i in ids)
    rfn = any(i.casefold().endswith("-rfn") and not i.casefold().endswith("-no-rfn") for i in ids)
    if rfn == no_rfn:
        return None
    return rfn


def license_fact(source: str, row: Mapping[str, Any]) -> LicenseFact:
    """The formula's license statement (module docstring)."""
    spdx = _clean(row.get("spdx_license"))
    return LicenseFact(
        source=source,
        key=SourceKey(NS, row["key"]),
        raw=spdx or NOASSERTION,
        spdx=spdx,
        text_url=_url(row.get("license_url")),
        rfn=_rfn(spdx),
        attrs=attrs(
            open_license=bool(row.get("open_license")),
            requires_license_agreement=bool(row.get("requires_license_agreement")),
        ),
    )


def _urls(row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    found = ((role, _url(row.get(role))) for role in ("homepage", "repository"))
    return tuple((role, url) for role, url in found if url is not None)


def drop_code(family: str, proprietary: bool, settings: Settings) -> str | None:
    """The first ``drop_names`` code matching ``family``; else "proprietary" if ``proprietary``."""
    for code, pattern in settings.drop_names:
        if re.search(pattern, family):
            return code
    return PROPRIETARY if proprietary and settings.agreement_is_proprietary else None


def stated_families(rows: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """``match_key`` of every family named by a formula that names a license id."""
    return frozenset(
        match_key(family)
        for row in rows
        if _clean(row.get("spdx_license"))
        for family in family_names(row)
    )


def formula_records(
    source: str,
    row: Mapping[str, Any],
    settings: Settings,
    stated: frozenset[str] = frozenset(),
) -> list[Record]:
    """Every record of one formula; none when it names no family.

    ``stated`` is ``stated_families`` of the whole snapshot: a formula without a
    license id whose families are all in it gives no ``LicenseFact`` (module docstring).
    """
    families = family_names(row)
    if not families:
        return []
    key = SourceKey(NS, row["key"])
    proprietary = bool(row.get("requires_license_agreement")) and not row.get("open_license")
    urls = _urls(row)
    files = files_by_family(row)
    out: list[Record] = [
        UniverseRecord(
            source=source,
            key=key,
            family=family,
            urls=urls,
            files=files[family],
            drop=drop_code(family, proprietary, settings),
        )
        for family in families
    ]
    fact = license_fact(source, row)
    if fact.spdx is not None or not {match_key(f) for f in families} <= stated:
        out.append(fact)
    return out


# --- the collector ----------------------------------------------------------------------------


class Fontist(CollectorBase):
    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (GIT_HOST,)  # git only; no fetcher request
    emits: ClassVar[tuple[type, ...]] = (UniverseRecord, LicenseFact)
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Clone the formulas, keep their facts (``formulas.jsonl.gz``) and the commit."""
        s = ctx.settings
        assert isinstance(s, Settings)
        checkout = ctx.raw.file("formulas")
        patterns = sparse_patterns(s.folders)
        commit = gitsrc.sparse_clone(s.repository, checkout, patterns, ref=s.ref, depth=1)
        ctx.out.note(f"git sparse clone {s.repository} {s.ref} at {commit}: {' '.join(patterns)}")
        files = formula_files(checkout, s.folders)
        rows, skipped = read_formulas(files, s.schema_version)
        for reason in skipped:
            ctx.log.warning("fontist: skipped %s", reason)
        if not rows:
            raise FormulaSchemaError(
                f"{s.repository} {s.ref}: none of {len(files)} formulas has schema_version "
                f"{s.schema_version} ({len(skipped)} skipped)"
            )
        check_shrink(len(rows), previous_formulas(ctx.previous), s.min_share)
        if skipped:
            ctx.out.note(f"skipped {len(skipped)} of {len(files)} formulas (source.json)")
        ctx.out.write_jsonl(FORMULAS_EXTRACT, rows)
        ctx.out.write_json(
            SOURCE_EXTRACT,
            {
                "repository": s.repository,
                "ref": s.ref,
                "commit": commit,
                "folders": list(s.folders),
                "formulas": len(rows),
                "skipped": skipped,
            },
        )
        ctx.log.info("fontist: %d formulas at %s (%d skipped)", len(rows), commit, len(skipped))

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """Records of every formula in ``formulas.jsonl.gz`` (module docstring)."""
        s = ctx.settings
        assert isinstance(s, Settings)
        rows = list(ctx.snapshot.iter_jsonl(FORMULAS_EXTRACT))
        stated = stated_families(rows)
        empty = 0
        for row in rows:
            recs = formula_records(self.name, row, s, stated)
            empty += not recs
            yield from recs
        if empty:
            ctx.log.info("fontist: %d formulas name no family", empty)


COLLECTOR = Fontist()
