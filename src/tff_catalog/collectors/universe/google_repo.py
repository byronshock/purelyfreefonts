"""Universe collector "google_repo": the google/fonts repository (design-m1 §2.4).

**Source.** ``https://github.com/google/fonts`` (branch ``main``). Its
top-level license folders (``ofl/``, ``apache/``, ``ufl/``) hold one folder per
family with a ``METADATA.pb`` textproto, the license text and the font files;
the README says the folder gives the license of every file inside it. The
root's ``to_production.txt``, ``to_sandbox.txt`` and ``to_delist.txt`` list
pending pushes. The live family list (``google_metadata``) stays the source of
truth for what is on fonts.google.com (design-m1 C12); this collector adds the
repository's facts: license folder and text, repository and minisite links,
and commit-pinned font files. Everything here is public (ruling T1).

**Fetch.** Never the whole repository (3 GB of blobs):

1. ``gitsrc.sparse_clone`` of ``settings.ref`` at depth 1 into ``ctx.raw``,
   checking out only ``<folder>/*/METADATA.pb``, the license texts
   (``LICENSE_NAMES``: ``OFL.txt``, ``LICENSE.txt``, ``UFL.txt`` and the
   ``LICENCE.txt`` of the newer Ubuntu families) and ``/to_*.txt``: about
   5 MB. The clone is noted in the manifest (it is not an HTTP request).
2. GitHub's git data API, five requests: the commit (its date is the
   snapshot's ``data_date``), the root tree, then each license folder's tree
   with ``recursive=1``, streamed to ``ctx.raw``, for every font file's blob
   sha and size (a partial clone cannot tell sizes without the blobs). A
   truncated tree is noted and its missing files keep ``size`` None.
3. Each ``METADATA.pb`` is read with gfmetadata. A file using fields newer
   than gfmetadata (``position`` in 2026-09) is read again skipping them and
   flagged ``unknown_fields``; one that still fails is listed under
   ``unparsed`` in ``repo.json`` and noted.

A clone with fewer than ``min_share`` of the previous snapshot's families
fails the fetch, so the stale policy keeps the last good snapshot. Missing
push lists, a family folder found under two license folders and push-list
comments shaped like a heading gftools does not write (by line number) are
noted.

**Extracts** (collector version 1):

- ``repo.json``: ``repository``, ``ref``, ``commit``, ``committed_at``,
  ``folders``, ``pending`` (per ``to_*.txt`` list, its family entries as
  ``{section, dir}``: ``list_entries`` read at fetch time; the lists'
  comments, PR links and designer-profile paths are not kept), ``unparsed``
  and ``truncated`` (folders whose tree listing was cut short).
- ``families.jsonl.gz``: one row per family folder, sorted by ``dir``
  (``"ofl/inter"``): ``metadata`` (``METADATA_FIELDS`` only: no designers,
  copyright lines, sample texts or source file lists), ``unknown_fields``,
  ``license_file`` (``name``, ``sha256``, ``bytes`` and ``rfn``, or null) and
  ``files`` (every font file under the folder: ``path``, git ``blob`` sha1,
  ``size``).

**Parse** (offline, pure), for each row:

- a ``UniverseRecord`` keyed ``gf-dir:<folder name>`` (``"inter"``, as
  ``names.gf_dir_slug`` gives it; the license folder is attr ``folder``) with
  METADATA.pb's facts: ``category`` is the LAST ``category`` value;
  ``subsets`` sorted without ``menu``; ``display_name`` only when it differs;
  ``is_monospace`` when that category or any classification is MONOSPACE;
  ``variable`` when the family has axes or a ``[axes]`` file; ``added`` from
  ``date_added``; ``latin_languages`` counts ``*_Latn`` languages (None when
  none are listed); METADATA.pb ``aliases`` become ``rename`` names;
  ``drop`` as ``drop_code`` says. Status: ``deprecated`` for a ``_todelist``
  folder or one listed in ``to_delist.txt``, ``queued`` for one listed under a
  ``queued_sections`` heading of ``to_production.txt``/``to_sandbox.txt``
  (gftools' "# New"; only gftools' own headings start a section, any other
  comment is a note), else ``live``. URLs: ``repository`` (never the
  googlefontdirectory-hg mirror, which is flagged ``hg_mirror`` instead),
  ``minisite``, and the commit-pinned ``license`` text. ``files`` are the
  METADATA.pb fonts as
  ``raw.githubusercontent.com/<owner>/<repo>/<commit>/…`` URLs with the
  tree's size; roles: italic style ``italic``, an ``[axes]`` file
  ``variable``, weight 400 ``regular``, else ``other``.
- a ``LicenseFact`` with ``raw`` = METADATA.pb's ``license`` ("OFL",
  "APACHE2", "UFL"), the pinned license text URL, its sha256 and ``rfn``
  (the OFL header reserves a font name), attr ``folder``. When the value is
  missing or does not match the folder (``FOLDER_LICENSE``), a second fact
  gives the folder's own claim (``raw`` = the folder name).
"""

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import quote

from tff_catalog import gitsrc
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.records import (
    DROP_REASONS,
    Attrs,
    FileRole,
    FontFileRef,
    LicenseFact,
    Record,
    Scalar,
    SourceKey,
    Status,
    UniverseRecord,
    attrs,
)
from tff_catalog.store import Snapshot

if TYPE_CHECKING:
    from gfmetadata import FamilyProto

NAME = "google_repo"
API_HOST = "api.github.com"
GIT_HOST = "github.com"  # the git remote, cloned by gitsrc (not through the fetcher)
RAW_BASE = "https://raw.githubusercontent.com"
NAMESPACE = "gf-dir"
EXTRACT = "families.jsonl.gz"
REPO_EXTRACT = "repo.json"
METADATA = "METADATA.pb"
# The license text each folder's families carry, and METADATA.pb's value for that folder.
LICENSE_FILES = {"ofl": "OFL.txt", "apache": "LICENSE.txt", "ufl": "UFL.txt"}
# Every license text name read, in the order tried after the folder's own: the newer
# Ubuntu families (ufl/ubuntusans, ufl/ubuntusansmono) carry only LICENCE.txt.
LICENSE_NAMES = ("OFL.txt", "LICENSE.txt", "UFL.txt", "LICENCE.txt")
FOLDER_LICENSE = {"ofl": "OFL", "apache": "APACHE2", "ufl": "UFL"}
LIST_PATTERN = "to_*.txt"
DELIST_FILE = "to_delist.txt"
QUEUE_FILES = ("to_production.txt", "to_sandbox.txt")
# The headings gftools writes into the push lists ("# New"; gftools.push.trafficjam.PushCategory).
PUSH_SECTIONS = (
    "New",
    "Upgrade",
    "Designer profile",
    "Axis Registry",
    "Knowledge",
    "Metadata / Description / License",
    "Sample texts",
    "Other",
    "Blocked",
    "Deleted",
)
TODELIST_SUFFIX = "_todelist"
HG_MIRROR = "/googlefonts/googlefontdirectory-hg"  # never a designer link (milestone-1 step 14)
MENU_SUBSET = "menu"  # Google's font-picker subset, not a script
EMOJI_SUBSET = "emoji"
MONOSPACE = "MONOSPACE"
LATIN_LANGUAGE = "_Latn"  # languages are "<lang>_<Script>", e.g. "en_Latn"
OFL_BODY = re.compile(r"This\s+Font\s+Software\s+is\s+licensed\s+under\s+the\s+SIL\s+Open\s+Font")
RESERVED_NAME = re.compile(r"Reserved\s+Font\s+Names?", re.IGNORECASE)
_REPOSITORY = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
# A comment shaped like a section heading (one to four plain words): when gftools does
# not know it, the fetch notes its line so PUSH_SECTIONS can be updated.
_HEADING_LIKE = re.compile(r"^[A-Za-z]+(?: [A-Za-z]+){0,3}$")
_KNOWN_HEADINGS = frozenset(s.casefold() for s in PUSH_SECTIONS)
_LIST_ENTRY = re.compile(r"^([a-z]+)/([^/\s]+)")
_BLOB = re.compile(r"[0-9a-f]{40}")  # a git blob sha1 from the tree listing

# METADATA.pb fields kept in the extract; the rest (designers, copyright lines,
# sample texts, source file lists, registry overrides) never reach the store.
METADATA_FIELDS = (
    "name",
    "display_name",
    "license",
    "category",
    "classifications",
    "stroke",
    "date_added",
    "subsets",
    "languages",
    "primary_script",
    "primary_language",
    "is_noto",
    "minisite_url",
    "aliases",
    "axes",
    "fonts",
    "source",
)
FONT_FIELDS = ("name", "style", "weight", "filename", "post_script_name", "full_name")
SOURCE_FIELDS = ("repository_url", "branch", "commit", "archive_url")

# Defaults of the Settings, which config/sources/google_repo.toml spells out.
REPOSITORY = "https://github.com/google/fonts"
MIN_SHARE = 0.9  # Google adds and delists a few dozen families a year
# The same non-text rules as google_metadata, in the repository's vocabulary.
DROP_NAMES: tuple[tuple[str, str], ...] = (
    ("icon", r"^Material (Icons|Symbols)\b"),
    ("barcode", r"\bBarcode\b"),
    ("math", r"\bMath\b"),
    ("music", r"\bMusic(al)?\b"),
    ("emoji", r"\bEmoji\b"),
)
SYMBOL_CLASSIFICATIONS: tuple[str, ...] = ("SYMBOLS",)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/google_repo.toml``."""

    repository: str = REPOSITORY  # https://github.com/<owner>/<repo>
    ref: str = "main"  # branch, tag or commit sha to clone
    folders: tuple[str, ...] = ("ofl", "apache", "ufl")  # license folders (keys of LICENSE_FILES)
    font_extensions: tuple[str, ...] = (".ttf", ".otf")
    queued_sections: tuple[str, ...] = ("new",)  # to_production/to_sandbox headings, casefolded
    min_share: float = MIN_SHARE  # of the previous snapshot's families, or the fetch fails
    drop_names: tuple[tuple[str, str], ...] = DROP_NAMES
    symbol_classifications: tuple[str, ...] = SYMBOL_CLASSIFICATIONS

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not _REPOSITORY.fullmatch(self.repository) or self.repository.endswith(".git"):
            raise ConfigError(
                f"{where}.repository: must be https://github.com/<owner>/<repo>, "
                f"got {self.repository!r}"
            )
        if not self.ref or self.ref.startswith("-") or any(c.isspace() for c in self.ref):
            raise ConfigError(f"{where}.ref: not a branch, tag or commit: {self.ref!r}")
        unknown = [f for f in self.folders if f not in LICENSE_FILES]
        if not self.folders or unknown or len(set(self.folders)) != len(self.folders):
            raise ConfigError(
                f"{where}.folders: distinct names out of {sorted(LICENSE_FILES)}, got "
                f"{list(self.folders)}"
            )
        if not self.font_extensions or any(
            not e.startswith(".") or e != e.lower() for e in self.font_extensions
        ):
            raise ConfigError(f"{where}.font_extensions: lower-case suffixes like '.ttf'")
        if any(s != s.casefold() or not is_heading(s) for s in self.queued_sections):
            raise ConfigError(
                f"{where}.queued_sections: casefolded gftools headings out of "
                f"{sorted(_KNOWN_HEADINGS)}, got {list(self.queued_sections)}"
            )
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

    @property
    def owner_repo(self) -> str:
        """``"google/fonts"``."""
        match = _REPOSITORY.fullmatch(self.repository)
        assert match is not None  # checked in __post_init__
        return f"{match[1]}/{match[2]}"


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- fetch: METADATA.pb, license texts and lists --------------------------------------------


def sparse_patterns(settings: Settings) -> list[str]:
    """The non-cone sparse-checkout patterns: METADATA.pb, license texts, the pending lists."""
    names = (METADATA, *LICENSE_NAMES)
    return [f"/{folder}/*/{name}" for folder in settings.folders for name in names] + [
        f"/{LIST_PATTERN}"
    ]


def _optional(message: Any, name: str) -> Any:
    return getattr(message, name) if message.HasField(name) else None


def _axis_value(value: float) -> float:
    """An axis bound as written: METADATA.pb stores 32-bit floats (0.1 reads 0.10000000149)."""
    return float(f"{value:.7g}")


def metadata_json(family: FamilyProto) -> dict[str, Any]:
    """The kept ``METADATA_FIELDS`` of a parsed METADATA.pb; absent scalars are None."""
    source = None
    if family.HasField("source"):
        source = {name: _optional(family.source, name) for name in SOURCE_FIELDS}
    out: dict[str, Any] = {
        name: _optional(family, name)
        for name in (
            "name",
            "display_name",
            "license",
            "stroke",
            "date_added",
            "primary_script",
            "primary_language",
            "is_noto",
            "minisite_url",
        )
    }
    for name in ("category", "classifications", "subsets", "languages", "aliases"):
        out[name] = list(getattr(family, name))
    out["axes"] = [
        {"tag": a.tag, "min": _axis_value(a.min_value), "max": _axis_value(a.max_value)}
        for a in family.axes
    ]
    out["fonts"] = [{name: _optional(f, name) for name in FONT_FIELDS} for f in family.fonts]
    out["source"] = source
    return {name: out[name] for name in METADATA_FIELDS}


def read_metadata(text: str) -> tuple[dict[str, Any], bool]:
    """Parse METADATA.pb text; return the kept fields and whether unknown fields were skipped.

    Raises ``ValueError`` when the text does not parse even with unknown fields skipped.
    """
    from gfmetadata import FamilyProto, text_format

    family = FamilyProto()
    try:
        text_format.Parse(text, family)
        return metadata_json(family), False
    except text_format.ParseError:
        family = FamilyProto()
    try:
        # Fields newer than gfmetadata (FontProto.position appeared in 2026) are skipped.
        text_format.Parse(text, family, allow_unknown_field=True)
    except text_format.ParseError as exc:
        raise ValueError(str(exc)) from exc
    return metadata_json(family), True


def reserved_font_name(text: str) -> bool | None:
    """Whether an OFL text's header (before the license body) reserves a font name.

    The OFL body itself defines "Reserved Font Name", so only the copyright
    lines above it count. None when the text has no OFL body.
    """
    body = OFL_BODY.search(text)
    if body is None:
        return None
    return RESERVED_NAME.search(text[: body.start()]) is not None


def license_file(family_dir: Path, folder: str) -> dict[str, Any] | None:
    """The family's license text: name, sha256, bytes and rfn (None unless it is an OFL text).

    The folder's usual name is tried first, then ``LICENSE_NAMES`` in order.
    """
    usual = LICENSE_FILES[folder]
    for name in dict.fromkeys((usual, *LICENSE_NAMES)):
        path = family_dir / name
        if path.is_file():
            data = path.read_bytes()
            return {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "rfn": reserved_font_name(data.decode("utf-8", "replace")),
            }
    return None


def tree_files(
    doc: Mapping[str, Any], extensions: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    """Font files of one folder's recursive tree listing, by family folder name.

    ``doc`` is GitHub's ``git/trees/<sha>?recursive=1`` answer for a license
    folder, so paths are ``<family>/<file>``. Each file is ``{path, blob,
    size}`` with ``path`` relative to the family folder; lists are sorted.
    """
    suffixes = tuple(extensions)
    out: dict[str, list[dict[str, Any]]] = {}
    for entry in doc.get("tree") or ():
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        path = entry.get("path")
        if not isinstance(path, str) or "/" not in path:
            continue
        family, rest = path.split("/", 1)
        if rest.lower().endswith(suffixes):
            size = entry.get("size")
            out.setdefault(family, []).append(
                {
                    "path": rest,
                    "blob": entry.get("sha"),
                    "size": size if type(size) is int else None,
                }
            )
    return {k: sorted(v, key=lambda f: f["path"]) for k, v in sorted(out.items())}


@dataclass(slots=True)
class Clone:
    """What ``read_clone`` found in the sparse checkout."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    unparsed: list[dict[str, str]] = field(default_factory=list)
    pending: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # by list file
    orphans: list[str] = field(default_factory=list)  # license text but no METADATA.pb
    odd_headings: dict[str, list[int]] = field(default_factory=dict)  # by list file: line numbers


def read_clone(
    root: Path, settings: Settings, files: Mapping[str, Mapping[str, list[dict[str, Any]]]]
) -> Clone:
    """Read the sparse checkout at ``root``; ``files`` is ``tree_files`` per license folder."""
    found = Clone()
    for path in sorted(root.glob(LIST_PATTERN)):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            found.pending[path.name] = pending_entries(text, settings.folders)
            if odd := unknown_headings(text):
                found.odd_headings[path.name] = odd
    for folder in settings.folders:
        base = root / folder
        family_dirs = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
        for family_dir in family_dirs:
            rel = f"{folder}/{family_dir.name}"
            meta_path = family_dir / METADATA
            if not meta_path.is_file():
                found.orphans.append(rel)
                continue
            try:
                meta, unknown = read_metadata(meta_path.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                found.unparsed.append({"dir": rel, "error": str(exc)[:300]})
                continue
            if not meta.get("name"):
                found.unparsed.append({"dir": rel, "error": "METADATA.pb names no family"})
                continue
            found.rows.append(
                {
                    "dir": rel,
                    "metadata": meta,
                    "unknown_fields": unknown,
                    "license_file": license_file(family_dir, folder),
                    "files": list(files.get(folder, {}).get(family_dir.name, [])),
                }
            )
    found.rows.sort(key=lambda row: row["dir"])
    return found


def unlisted_fonts(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """METADATA.pb font files the tree listing lacks, as ``<dir>/<file>``."""
    missing = []
    for row in rows:
        listed = {f["path"] for f in row["files"]}
        for font in row["metadata"]["fonts"]:
            name = font.get("filename")
            if name and name not in listed:
                missing.append(f"{row['dir']}/{name}")
    return missing


def shared_folder_names(rows: Iterable[Mapping[str, Any]]) -> list[str]:
    """Family folder names found under two license folders (a license move caught halfway).

    Both rows are kept and parse gives both the same ``gf-dir`` key, so the
    licenses stage sees the two claims side by side.
    """
    folders: dict[str, list[str]] = {}
    for row in rows:
        folder, name = row["dir"].split("/", 1)
        folders.setdefault(name, []).append(folder)
    return sorted(name for name, found in folders.items() if len(found) > 1)


def previous_families(previous: Snapshot | None) -> int | None:
    """The family count of an earlier snapshot, from its manifest (None: no snapshot)."""
    entry = previous.manifest.extract(EXTRACT) if previous is not None else None
    return entry.rows if entry is not None else None


def check_shrink(families: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` for no family, or fewer than ``min_share`` of ``before``."""
    if families == 0:
        raise ValueError(f"{NAME}: the clone holds no family folder with a {METADATA}")
    if before is not None and families < min_share * before:
        raise ValueError(
            f"{NAME}: {families} families, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken clone?"
        )


def _utc_date(stamp: str) -> date:
    """The UTC day of an ISO timestamp such as GitHub's ``2026-09-24T09:58:38Z``."""
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


# --- parse: the pending lists -------------------------------------------------------------


def is_heading(words: str) -> bool:
    """Whether a push-list comment's text is a section heading: one of gftools' own
    (``PUSH_SECTIONS``, any case), the only headings gftools writes and reads back."""
    return words.casefold() in _KNOWN_HEADINGS


def _comment(line: str) -> str | None:
    """The text of a ``# …`` comment line, else None."""
    stripped = line.strip()
    return stripped.lstrip("#").strip() if stripped.startswith("#") else None


def list_entries(text: str, folders: Iterable[str]) -> Iterator[tuple[str, str]]:
    """``(section, dir)`` for each family entry of a ``to_*.txt`` list, in order.

    A heading comment (``is_heading``: ``# New``, ``# Metadata / Description /
    License``) starts a section, named casefolded ("" before the first). Any
    other comment, such as a commented-out entry, a ``# Deleted: <path>`` line
    or a note people add by hand ("# Held back for now"), changes nothing, so a
    note inside "# New" never makes the new families after it look live. An
    entry is a path whose first part is a license folder; deeper paths name
    their family's folder, and ``# …`` after it is ignored.
    """
    wanted = set(folders)
    section = ""
    for line in text.splitlines():
        words = _comment(line)
        if words is not None:
            if is_heading(words):
                section = words.casefold()
            continue
        match = _LIST_ENTRY.match(line.split("#", 1)[0].strip())
        if match and match[1] in wanted:
            yield section, f"{match[1]}/{match[2]}"


def unknown_headings(text: str) -> list[int]:
    """Line numbers (from 1) of comments shaped like a heading that gftools does not write.

    Such a comment is read as a note (``list_entries``); the fetch lists the
    lines, not their text, so a section gftools adds later is noticed and
    added to ``PUSH_SECTIONS``.
    """
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if (words := _comment(line)) is not None
        and not is_heading(words)
        and _HEADING_LIKE.fullmatch(words)
    ]


def pending_entries(text: str, folders: Iterable[str]) -> list[dict[str, str]]:
    """A push list's family entries as the extract keeps them: ``{section, dir}``, first
    occurrence of each pair, in order. Comments, PR links and non-family paths are left out."""
    pairs = dict.fromkeys(list_entries(text, folders))
    return [{"section": section, "dir": rel} for section, rel in pairs]


def _pairs(entries: object) -> Iterator[tuple[str, str]]:
    """The ``(section, dir)`` pairs of an extract's ``pending`` list, skipping malformed ones."""
    for entry in entries if isinstance(entries, list) else ():
        if isinstance(entry, dict):
            section, rel = entry.get("section"), entry.get("dir")
            if isinstance(section, str) and isinstance(rel, str):
                yield section, rel


def list_statuses(
    pending: Mapping[str, object], queued_sections: Iterable[str]
) -> dict[str, Status]:
    """Family folder -> "deprecated" (``to_delist.txt``) or "queued" (a queued section of
    ``to_production.txt`` or ``to_sandbox.txt``); ``pending`` is ``pending_entries`` by list."""
    queued = set(queued_sections)
    out: dict[str, Status] = {}
    for name in QUEUE_FILES:
        for section, rel in _pairs(pending.get(name)):
            if section in queued:
                out[rel] = "queued"
    for _, rel in _pairs(pending.get(DELIST_FILE)):
        out[rel] = "deprecated"
    return out


# --- parse: records -----------------------------------------------------------------------


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


def pinned_url(owner_repo: str, commit: str, path: str) -> str:
    """``https://raw.githubusercontent.com/<owner>/<repo>/<commit>/<path>``, percent-encoded.

    Brackets become ``%5B``/``%5D``; commas stay, as GitHub serves them.
    """
    return f"{RAW_BASE}/{owner_repo}/{commit}/{quote(path, safe='/,')}"


def file_role(font: Mapping[str, Any]) -> FileRole:
    """``italic`` for the italic style, ``variable`` for an ``[axes]`` file,
    ``regular`` for weight 400, else ``other``."""
    if font.get("style") == "italic":
        return "italic"
    if "[" in (font.get("filename") or ""):
        return "variable"
    return "regular" if font.get("weight") == 400 else "other"


def font_files(row: Mapping[str, Any], owner_repo: str, commit: str) -> tuple[FontFileRef, ...]:
    """The METADATA.pb fonts of a row as commit-pinned references, in METADATA.pb order,
    each with the git blob sha1 of the tree listing (the font-facts cache key)."""
    files = {f["path"]: f for f in row.get("files") or ()}
    refs: dict[str, FontFileRef] = {}
    for font in row["metadata"].get("fonts") or ():
        name = _text(font.get("filename"))
        if name is None or name in refs:
            continue
        listed = files.get(name) or {}
        size, blob = listed.get("size"), listed.get("blob")
        refs[name] = FontFileRef(
            url=pinned_url(owner_repo, commit, f"{row['dir']}/{name}"),
            size=size if type(size) is int else None,
            role=file_role(font),
            git_blob=blob if isinstance(blob, str) and _BLOB.fullmatch(blob) else None,
        )
    return tuple(refs.values())


def is_hg_mirror(url: str) -> bool:
    """The googlefontdirectory-hg mirror, which must never be used as a designer link."""
    return HG_MIRROR in url.lower()


def family_urls(
    row: Mapping[str, Any], owner_repo: str, commit: str
) -> tuple[tuple[str, str], ...]:
    """``repository`` (not the hg mirror), ``minisite`` and the pinned ``license`` text."""
    meta = row["metadata"]
    urls: list[tuple[str, str]] = []
    repository = _text((meta.get("source") or {}).get("repository_url"))
    if repository is not None and not is_hg_mirror(repository):
        urls.append(("repository", repository.strip()))
    minisite = _text(meta.get("minisite_url"))
    if minisite is not None:
        urls.append(("minisite", minisite.strip()))
    lic = row.get("license_file")
    if lic:
        urls.append(("license", pinned_url(owner_repo, commit, f"{row['dir']}/{lic['name']}")))
    return tuple((role, url) for role, url in urls if re.match(r"^https?://\S+$", url))


def drop_code(meta: Mapping[str, Any], settings: Settings) -> str | None:
    """The universe drop code METADATA.pb supports, or None for a text family.

    In order: the first ``drop_names`` pattern matching the family name;
    "emoji" when emoji is the only subset besides ``menu``; "symbol" for a
    ``symbol_classifications`` classification.
    """
    family = _text(meta.get("name")) or ""
    for code, pattern in settings.drop_names:
        if re.search(pattern, family):
            return code
    if set(_texts(meta.get("subsets"))) - {MENU_SUBSET} == {EMOJI_SUBSET}:
        return "emoji"
    if set(_texts(meta.get("classifications"))) & set(settings.symbol_classifications):
        return "symbol"
    return None


def _latin_languages(meta: Mapping[str, Any]) -> int | None:
    languages = _texts(meta.get("languages"))
    if not languages:
        return None
    return sum(lang.endswith(LATIN_LANGUAGE) for lang in languages)


def _axis_tags(meta: Mapping[str, Any]) -> tuple[str, ...]:
    axes = meta.get("axes")
    if not isinstance(axes, list):
        return ()
    return tuple(t for a in axes if isinstance(a, dict) and (t := _text(a.get("tag"))))


def _record_attrs(row: Mapping[str, Any], tags: tuple[str, ...]) -> Attrs:
    meta = row["metadata"]
    found: dict[str, Scalar] = {"folder": row["dir"].split("/", 1)[0]}
    if type(meta.get("is_noto")) is bool:
        found["is_noto"] = meta["is_noto"]
    for name in ("stroke", "primary_language"):
        if (text := _text(meta.get(name))) is not None:
            found[name] = text
    if tags:
        found["axes"] = ",".join(tags)
    repository = _text((meta.get("source") or {}).get("repository_url"))
    if repository is not None and is_hg_mirror(repository):
        found["hg_mirror"] = True
    return attrs(**found)


def family_status(rel: str, statuses: Mapping[str, Status]) -> Status:
    """``deprecated`` for a ``_todelist`` folder, else the lists' status, else ``live``."""
    if rel.endswith(TODELIST_SUFFIX):
        return "deprecated"
    return statuses.get(rel, "live")


def universe_record(
    row: Mapping[str, Any],
    settings: Settings,
    *,
    owner_repo: str,
    commit: str,
    statuses: Mapping[str, Status],
) -> UniverseRecord:
    """The universe record of one extract row (whose metadata names a family)."""
    meta = row["metadata"]
    family = meta["name"]
    categories = _texts(meta.get("category"))
    classifications = _texts(meta.get("classifications"))
    category = categories[-1] if categories else None  # only the last value counts
    display = _text(meta.get("display_name"))
    tags = _axis_tags(meta)
    files = font_files(row, owner_repo, commit)
    return UniverseRecord(
        source=NAME,
        key=SourceKey(NAMESPACE, row["dir"].split("/", 1)[1]),
        family=family,
        display_name=display if display != family else None,
        category=category,
        classifications=classifications,
        primary_script=_text(meta.get("primary_script")),
        subsets=tuple(sorted(set(_texts(meta.get("subsets"))) - {MENU_SUBSET})),
        latin_languages=_latin_languages(meta),
        is_monospace=MONOSPACE in (category, *classifications),
        variable=bool(tags) or any("%5B" in f.url for f in files),  # a [axes] file name
        added=_day(meta.get("date_added")),
        status=family_status(row["dir"], statuses),
        urls=family_urls(row, owner_repo, commit),
        files=files,
        names=tuple((a, "rename") for a in _texts(meta.get("aliases")) if a != family),
        drop=drop_code(meta, settings),
        attrs=_record_attrs(row, tags),
    )


def license_facts(row: Mapping[str, Any], *, owner_repo: str, commit: str) -> list[LicenseFact]:
    """METADATA.pb's license claim, plus the folder's when the two differ (module docstring)."""
    folder, slug = row["dir"].split("/", 1)
    key = SourceKey(NAMESPACE, slug)
    lic = row.get("license_file") or None
    text = {
        "text_url": pinned_url(owner_repo, commit, f"{row['dir']}/{lic['name']}") if lic else None,
        "text_sha256": lic["sha256"] if lic else None,
        "rfn": lic.get("rfn") if lic else None,
    }
    claimed = _text(row["metadata"].get("license"))
    facts = []
    if claimed is not None:
        facts.append(LicenseFact(NAME, key, claimed, attrs=attrs(folder=folder), **text))
    if claimed != FOLDER_LICENSE.get(folder):
        facts.append(LicenseFact(NAME, key, folder, attrs=attrs(folder=folder), **text))
    return facts


class GoogleRepo(CollectorBase):
    """The google/fonts repository: METADATA.pb facts, license texts, pinned font files."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (API_HOST, GIT_HOST)
    emits: ClassVar[tuple[type, ...]] = (UniverseRecord, LicenseFact)
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Sparse clone plus five git data API requests; write ``repo.json`` and the families."""
        settings = _settings(ctx.settings)
        checkout = ctx.raw.path / "google-fonts"
        commit = gitsrc.sparse_clone(
            f"{settings.repository}.git", checkout, sparse_patterns(settings), ref=settings.ref
        )
        ctx.out.note(
            f"git: sparse depth-1 clone of {settings.repository} {settings.ref} at {commit}"
        )
        committed_at, files, truncated = self._trees(ctx, settings, commit)
        found = read_clone(checkout, settings, files)
        check_shrink(len(found.rows), previous_families(ctx.previous), settings.min_share)
        self._notes(ctx, found, truncated)
        ctx.out.write_json(
            REPO_EXTRACT,
            {
                "repository": settings.repository,
                "ref": settings.ref,
                "commit": commit,
                "committed_at": committed_at,
                "folders": list(settings.folders),
                "pending": found.pending,
                "unparsed": found.unparsed,
                "truncated": truncated,
            },
        )
        ctx.out.write_jsonl(EXTRACT, found.rows)
        ctx.out.set_data_date(_utc_date(committed_at))
        ctx.log.info(
            "%s: %d families at %s (%s); %d unparsed",
            self.name,
            len(found.rows),
            commit[:12],
            committed_at,
            len(found.unparsed),
        )

    def _trees(
        self, ctx: FetchContext, settings: Settings, commit: str
    ) -> tuple[str, dict[str, dict[str, list[dict[str, Any]]]], list[str]]:
        """The commit's date, ``tree_files`` per license folder, and the truncated folders."""
        api = f"https://{API_HOST}/repos/{settings.owner_repo}/git"
        described = self._get_json(ctx, f"{api}/commits/{commit}", "commit.json")
        root = self._get_json(ctx, f"{api}/trees/{described['tree']['sha']}", "tree-root.json")
        subtrees = {e["path"]: e["sha"] for e in root.get("tree") or () if e.get("type") == "tree"}
        files: dict[str, dict[str, list[dict[str, Any]]]] = {}
        truncated = []
        for folder in settings.folders:
            if folder not in subtrees:
                raise ValueError(f"{NAME}: {settings.repository} at {commit} has no {folder}/")
            doc = self._get_json(
                ctx, f"{api}/trees/{subtrees[folder]}", f"tree-{folder}.json", recursive=True
            )
            if doc.get("truncated"):
                truncated.append(folder)  # GitHub cuts listings past 100,000 entries or 7 MB
            files[folder] = tree_files(doc, settings.font_extensions)
        return str(described["committer"]["date"]), files, truncated

    @staticmethod
    def _get_json(ctx: FetchContext, url: str, raw_name: str, *, recursive: bool = False) -> Any:
        """GET a GitHub API document into ``ctx.raw``, record it, and return it parsed."""
        params = {"recursive": 1} if recursive else None
        result = ctx.fetcher.get(url, params=params, to=ctx.raw.file(raw_name))
        for record in result.to_records():
            ctx.out.record_fetch(record)
        doc = result.json()
        if not isinstance(doc, dict):
            raise ValueError(f"{url}: expected a JSON object")
        return doc

    def _notes(self, ctx: FetchContext, found: Clone, truncated: list[str]) -> None:
        notes = []
        if truncated:
            notes.append(f"tree listing truncated (sizes missing) for: {', '.join(truncated)}")
        if found.unparsed:
            dirs = ", ".join(u["dir"] for u in found.unparsed)
            notes.append(f"{METADATA} unreadable, family skipped: {dirs}")
        skipped = [r["dir"] for r in found.rows if r["unknown_fields"]]
        if skipped:
            notes.append(
                f"{METADATA} fields unknown to gfmetadata skipped in: {', '.join(skipped)}"
            )
        missing = unlisted_fonts(found.rows)
        if missing:
            shown = ", ".join(missing[:5]) + (", …" if len(missing) > 5 else "")
            notes.append(
                f"font files named in {METADATA} but not in the tree: {len(missing)} ({shown})"
            )
        if found.orphans:
            notes.append(f"folders without {METADATA} ignored: {len(found.orphans)}")
        absent = [name for name in (*QUEUE_FILES, DELIST_FILE) if name not in found.pending]
        if absent:
            notes.append(f"push lists missing, so no family is queued from them: {absent}")
        for name, lines in sorted(found.odd_headings.items()):
            notes.append(
                f"{name}: comments shaped like a heading gftools does not write, read as notes "
                f"(a new section? see PUSH_SECTIONS): lines {lines}"
            )
        shared = shared_folder_names(found.rows)
        if shared:
            notes.append(f"family folders in two license folders (one gf-dir key): {shared}")
        for note in notes:
            ctx.log.warning("%s: %s", self.name, note)
            ctx.out.note(note)

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """A universe record and one or two license facts per family folder of the extract."""
        settings = _settings(ctx.settings)
        repo = ctx.snapshot.load_json(REPO_EXTRACT)
        commit = repo["commit"]
        match = _REPOSITORY.fullmatch(repo["repository"])
        if match is None:
            raise ValueError(f"{ctx.snapshot.path}: bad repository {repo['repository']!r}")
        owner_repo = f"{match[1]}/{match[2]}"
        statuses = list_statuses(repo.get("pending") or {}, settings.queued_sections)
        seen: set[str] = set()
        for row in ctx.snapshot.iter_jsonl(EXTRACT):
            rel = row.get("dir") if isinstance(row, dict) else None
            meta = row.get("metadata") if isinstance(row, dict) else None
            if not isinstance(rel, str) or "/" not in rel or rel in seen:
                continue
            if not isinstance(meta, dict) or _text(meta.get("name")) is None:
                continue
            seen.add(rel)
            yield universe_record(
                row, settings, owner_repo=owner_repo, commit=commit, statuses=statuses
            )
            yield from license_facts(row, owner_repo=owner_repo, commit=commit)


COLLECTOR = GoogleRepo()
