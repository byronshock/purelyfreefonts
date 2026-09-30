"""Universe collector "nerdfonts": the families Nerd Fonts patches (design-m1 §2.4).

**Source.** ``bin/scripts/lib/fonts.json`` in ``ryanoasis/nerd-fonts``: about
39 KB, ``{"fonts": [...]}`` with one object per patched family (72 on
2026-09-25). The repository's LICENSE puts every file outside a folder with its
own license under MIT, so the file is MIT data (ruling T1: fixtures may carry
trimmed real rows with the notice). Fields used: ``unpatchedName`` (the
original family), ``patchedName`` (the Nerd build's name, changed when the
original has a Reserved Font Name: IBM Plex Mono -> BlexMono), ``folderName``
(the release asset and folder, the key), ``caskName`` (Homebrew token
``font-<caskName>-nerd-font``), ``licenseId`` (the original font's license),
``RFN``, ``RFNException``, ``isMonospaced``, ``version`` and
``imagePreviewFontSource`` (an original font file under ``src/unpatched-fonts/``).
``repoRelease`` is ignored: it says whether patched fonts are committed to the
repository, not whether they are released.

**Fetch.** Pinned to a commit, in at most two requests:

1. ``api.github.com/repos/<repo>/commits?path=<path>&sha=<ref>&per_page=1``:
   the last commit that changed the file. It is a conditional GET with the
   previous snapshot's ETag (a 304 costs no rate limit); its body names people,
   so it is never kept.
2. ``raw.githubusercontent.com/<repo>/<commit>/<path>``: the file at that
   commit, checked (a list of font objects, each with a ``folderName`` and an
   ``unpatchedName``; at least ``min_share`` of the previous snapshot's
   entries) and kept byte for byte as the extract ``fonts.json``. When the
   previous snapshot pinned the same commit, or the commit request answers 304,
   its ``fonts.json`` is copied instead and this request is skipped. A previous
   snapshot whose ``pin.json`` does not read is not reused.

The second extract, ``pin.json``, names the repository, ref, path, commit and
commit time. The manifest's ``data_date`` is the commit's (UTC) day, never
after the run date.

3. ``api.github.com/repos/<repo>/git/trees/<commit>:<unpatched_dir>?recursive=1``:
   the files under ``src/unpatched-fonts/`` at the pinned commit, where every
   folder keeps the original font's license file next to its fonts
   (``Meslo/LICENSE.txt``, ``Hack/LICENSE.md``). The third extract,
   ``licenses.json``, keeps the paths whose names look like a license
   (``LICENSE_NAME``), never the texts; stage "verify" (L3) reads the texts,
   pinned to the commit, as the text a Nerd folder's original is shipped
   under. When the previous snapshot listed them for the same commit, its
   extract is copied instead. The list is an extra: a failed or truncated
   tree answer is noted and leaves the extract out, and the snapshot still
   stands (version 1 snapshots have none either).

**Parse** (offline, pure), for each folder of ``fonts.json`` (a folder listed
twice keeps its first entry, with a warning):

- a ``UniverseRecord`` keyed ``nerd-folder:<folderName>``, family
  ``unpatchedName``, ``is_monospace`` from ``isMonospaced``, the original font
  file as a commit-pinned ``FontFileRef`` (TrueType or OpenType only), the drop
  code ``Settings.drop`` gives the folder, and as ``names`` the ``build``
  aliases ``patchedName`` and ``folderName``, each only when its match key
  differs from the family's and from the one before (so "CaskaydiaCove" for
  Cascadia Code, but nothing for Hack). Attrs: ``cask_name``,
  ``patched_name`` and ``version``, as given.
- a ``LicenseFact`` with ``raw`` = ``licenseId``, ``spdx`` = the same string
  only when it is a well-formed SPDX expression (the file's
  ``OFL-1.1-no-RFN or LGPL-2.1-only`` is not: lower-case operator), ``rfn``
  from ``RFN`` and the ``RFNException`` URL as attr ``rfn_exception``, and as
  ``text_url`` the folder's license file at the pinned commit
  (``license_path``): from the preview file's own directory up to the
  folder, the first directory holding a license file; in it, a file named
  just LICENSE or LICENCE first, then OFL, then the rest, by path. FAQs,
  copyright notes and read-mes are not license texts.
"""

import json
import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, ClassVar
from urllib.parse import quote, urlencode

from tff_catalog import clock
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchError, FetchResult, previous_record
from tff_catalog.keys import match_key
from tff_catalog.records import (
    DROP_REASONS,
    FontFileRef,
    LicenseFact,
    Record,
    Scalar,
    SourceKey,
    UniverseRecord,
    attrs,
)
from tff_catalog.store import Snapshot

NAME = "nerdfonts"
API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
NAMESPACE = "nerd-folder"
FONTS = "fonts.json"  # the file itself, as fetched
PIN = "pin.json"  # the commit the file was fetched at
PIN_SCHEMA = 1
LICENSES = "licenses.json"  # license file paths under unpatched_dir, at the pinned commit
LICENSES_SCHEMA = 1
# A file name that looks like a license text (Nerd Fonts has LICENSE, LICENCE.txt,
# OFL.txt, COPYING-LICENSE, "SIL Open Font License.txt", "Tinos/Apache License.txt").
LICENSE_NAME = re.compile(r"licen[cs]e|copying|(?:^|[^a-z])ofl(?:[^a-z]|$)", re.IGNORECASE)
NOT_LICENSE_TEXT = re.compile(r"faq|copyright|readme", re.IGNORECASE)
BUILD = "build"  # the alias relation of a Nerd build's name (data/aliases.csv)
FONT_EXTENSIONS = (".ttf", ".otf")
API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

# Defaults of the Settings, which config/sources/nerdfonts.toml spells out.
REPO = "ryanoasis/nerd-fonts"
REF = "master"
PATH = "bin/scripts/lib/fonts.json"
UNPATCHED_DIR = "src/unpatched-fonts"  # where imagePreviewFontSource paths start
# Nerd Fonts rarely retires a font, so losing a tenth at once is a broken file.
MIN_SHARE = 0.9
# Folders that are not text families, as universe drop codes (records.DROP_REASONS).
DROP: dict[str, str] = {"NerdFontsSymbolsOnly": "icon"}  # the icon glyphs alone

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SPDX_TOKEN = re.compile(r"[()]|[^\s()]+")
_SPDX_ID = re.compile(
    r"(?:DocumentRef-[A-Za-z0-9.-]+:)?LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9][A-Za-z0-9.-]*\+?"
)
_SPDX_OPERATORS = frozenset({"AND", "OR", "WITH"})


def _relative_path(value: str) -> bool:
    parts = value.split("/")
    return bool(value) and all(p not in ("", ".", "..") for p in parts)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/nerdfonts.toml``."""

    repo: str = REPO
    ref: str = REF
    path: str = PATH
    unpatched_dir: str = UNPATCHED_DIR
    min_share: float = MIN_SHARE  # of the previous snapshot's entries, or the fetch fails
    drop: dict[str, str] = field(default_factory=lambda: dict(DROP))  # folderName -> code

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not _REPO_RE.fullmatch(self.repo):
            raise ConfigError(f"{where}.repo: expected 'owner/name', got {self.repo!r}")
        if not self.ref or any(c.isspace() for c in self.ref):
            raise ConfigError(f"{where}.ref: expected a branch name, got {self.ref!r}")
        for key in ("path", "unpatched_dir"):
            if not _relative_path(getattr(self, key)):
                raise ConfigError(f"{where}.{key}: expected a relative path inside the repository")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")
        for folder, code in self.drop.items():
            if code not in DROP_REASONS:
                raise ConfigError(
                    f"{where}.drop.{folder}: {code!r} is not one of {sorted(DROP_REASONS)}"
                )


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


def cask_token(cask_name: str) -> str:
    """The Homebrew cask of a Nerd build: ``font-<caskName>-nerd-font`` (all 72 on 2026-09-25)."""
    return f"font-{cask_name}-nerd-font"


# --- the pin -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pin:
    """The commit ``fonts.json`` was fetched at (extract ``pin.json``)."""

    repo: str
    ref: str
    path: str
    commit: str  # 40 hex digits
    committed_at: str  # ISO UTC, "…Z"

    @property
    def day(self) -> date:
        """The commit's UTC day (the snapshot's ``data_date``)."""
        return _utc(self.committed_at).date()

    def same_file(self, other: Pin) -> bool:
        """Whether both pins name the same file at the same commit."""
        return (self.repo, self.path, self.commit) == (other.repo, other.path, other.commit)

    def raw_url(self, path: str) -> str:
        """``path`` of the repository at the pinned commit, percent-encoded."""
        return f"https://{RAW_HOST}/{self.repo}/{self.commit}/{quote(path, safe='/')}"

    def tree_url(self, path: str) -> str:
        """The API request for every file under ``path`` at the pinned commit."""
        return (
            f"https://{API_HOST}/repos/{self.repo}/git/trees/"
            f"{self.commit}:{quote(path, safe='/')}?recursive=1"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": PIN_SCHEMA,
            "repo": self.repo,
            "ref": self.ref,
            "path": self.path,
            "commit": self.commit,
            "committed_at": self.committed_at,
        }

    @classmethod
    def from_json(cls, doc: object) -> Pin:
        """Read ``pin.json``; ``ValueError`` on anything else."""
        if not isinstance(doc, dict) or doc.get("schema") != PIN_SCHEMA:
            raise ValueError(f"{PIN}: not a schema-{PIN_SCHEMA} pin: {doc!r}")
        values = {k: doc.get(k) for k in ("repo", "ref", "path", "commit", "committed_at")}
        if not all(isinstance(v, str) and v for v in values.values()):
            raise ValueError(f"{PIN}: missing fields in {doc!r}")
        if not _SHA_RE.fullmatch(values["commit"]):
            raise ValueError(f"{PIN}: bad commit {values['commit']!r}")
        try:
            iso = clock.iso_utc(_utc(values["committed_at"]))
        except ValueError:
            iso = None
        if iso != values["committed_at"]:
            raise ValueError(f"{PIN}: committed_at {values['committed_at']!r} is not ISO UTC")
        return cls(**values)


def _utc(text: str) -> datetime:
    """An ISO time with a zone, in UTC; ``ValueError`` otherwise."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"time {text!r} has no time zone")
    return moment.astimezone(UTC)


def commits_url(settings: Settings) -> str:
    """The API request for the last commit that changed ``settings.path`` on ``settings.ref``."""
    query = urlencode({"path": settings.path, "per_page": 1, "sha": settings.ref})
    return f"https://{API_HOST}/repos/{settings.repo}/commits?{query}"


def pin_from_commits(doc: object, settings: Settings) -> Pin:
    """The pin from a commits-API answer; ``ValueError`` when it names no usable commit."""
    first = doc[0] if isinstance(doc, list) and doc else None
    sha = first.get("sha") if isinstance(first, dict) else None
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise ValueError(f"{settings.repo}: no commit changes {settings.path} on {settings.ref}")
    try:
        moment = _utc(first["commit"]["committer"]["date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{settings.repo}@{sha}: no usable commit time") from exc
    return Pin(settings.repo, settings.ref, settings.path, sha, clock.iso_utc(moment))


# --- the file ----------------------------------------------------------------------------------


def _text(value: object) -> str | None:
    """A non-empty string as given, else None."""
    return value if isinstance(value, str) and value.strip() else None


def font_entries(doc: object) -> list[Mapping[str, Any]]:
    """The entries of a ``fonts.json`` document, in file order.

    Raises ``ValueError`` unless ``doc`` is ``{"fonts": [...]}`` with at least
    one entry and every entry an object with a non-empty ``folderName`` and
    ``unpatchedName``.
    """
    fonts = doc.get("fonts") if isinstance(doc, dict) else None
    if not isinstance(fonts, list) or not fonts:
        raise ValueError(f'{FONTS}: expected {{"fonts": [...]}} with at least one entry')
    for i, entry in enumerate(fonts):
        if not isinstance(entry, dict):
            raise ValueError(f"{FONTS}: entry {i} is not an object")
        for key in ("folderName", "unpatchedName"):
            if _text(entry.get(key)) is None:
                raise ValueError(f"{FONTS}: entry {i} has no {key}")
    return fonts


def previous_entries(previous: Snapshot | None) -> int | None:
    """The entry count of an earlier snapshot's ``fonts.json``, from its manifest."""
    entry = previous.manifest.extract(FONTS) if previous is not None else None
    return entry.rows if entry is not None else None


def check_shrink(entries: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when the list shrank below ``min_share`` of ``before`` entries."""
    if before is not None and entries < min_share * before:
        raise ValueError(
            f"{FONTS}: {entries} entries, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken file?"
        )


# --- the license files -------------------------------------------------------------------------


def license_files(doc: object) -> list[str]:
    """The license-like file paths of a git trees-API answer, sorted.

    Raises ``ValueError`` for an answer that is not a tree, or a truncated one
    (it could leave out any folder's license).
    """
    tree = doc.get("tree") if isinstance(doc, dict) else None
    if not isinstance(tree, list):
        raise ValueError("the trees API answer has no tree")
    if doc.get("truncated") is not False:
        raise ValueError("the trees API answer is truncated")
    paths = {
        item["path"]
        for item in tree
        if isinstance(item, dict)
        and item.get("type") == "blob"
        and isinstance(item.get("path"), str)
        and _relative_path(item["path"])
        and LICENSE_NAME.search(item["path"].rpartition("/")[2])
    }
    return sorted(paths)


def licenses_doc(pin: Pin, directory: str, files: list[str]) -> dict[str, Any]:
    """The ``licenses.json`` extract."""
    return {"schema": LICENSES_SCHEMA, "commit": pin.commit, "dir": directory, "files": files}


def read_licenses(doc: object, pin: Pin, directory: str) -> list[str] | None:
    """The paths of a ``licenses.json`` made at ``pin``'s commit for ``directory``, else None."""
    if not isinstance(doc, dict) or doc.get("schema") != LICENSES_SCHEMA:
        return None
    files = doc.get("files")
    if (doc.get("commit"), doc.get("dir")) != (pin.commit, directory) or not isinstance(
        files, list
    ):
        return None
    return [f for f in files if isinstance(f, str) and _relative_path(f)]


def _license_rank(path: str) -> tuple[int, str]:
    stem = path.rpartition("/")[2].rpartition(".")[0] or path.rpartition("/")[2]
    stem = stem.lower()
    if stem in ("license", "licence"):
        return (0, path)
    if stem.startswith("ofl") or "open font license" in stem:
        return (1, path)
    return (2, path)


def license_path(entry: Mapping[str, Any], files: list[str]) -> str | None:
    """The license file of a folder's original font, under ``unpatched_dir`` (module doc)."""
    folder = entry["folderName"]
    source = _text(entry.get("imagePreviewFontSource"))
    parts = source.split("/")[:-1] if source and _relative_path(source) else []
    if not parts or parts[0] != folder:
        parts = [folder]
    by_dir: dict[str, list[str]] = {}
    for path in files:
        name = path.rpartition("/")[2]
        if LICENSE_NAME.search(name) and not NOT_LICENSE_TEXT.search(name):
            by_dir.setdefault(path.rpartition("/")[0], []).append(path)
    while parts:
        found = by_dir.get("/".join(parts))
        if found:
            return min(found, key=_license_rank)
        parts.pop()
    return None


# --- parse ---------------------------------------------------------------------------------------


def is_spdx_expression(text: str) -> bool:
    """Whether ``text`` is a well-formed SPDX license expression.

    Checks the syntax only (ids or ``LicenseRef-`` ids joined by upper-case
    ``AND``, ``OR`` or ``WITH``, with balanced parentheses), not that each id is
    on the SPDX list.
    """
    tokens = _SPDX_TOKEN.findall(text)
    depth, want_id = 0, True
    for token in tokens:
        if want_id:
            if token == "(":
                depth += 1
            elif token not in _SPDX_OPERATORS and _SPDX_ID.fullmatch(token):
                want_id = False
            else:
                return False
        elif token == ")" and depth > 0:
            depth -= 1
        elif token in _SPDX_OPERATORS:
            want_id = True
        else:
            return False
    return bool(tokens) and not want_id and depth == 0


def build_names(family: str, *names: str | None) -> tuple[tuple[str, str], ...]:
    """``(name, "build")`` for each name whose match key is new (the family's counts as seen)."""
    seen = {match_key(family)}
    out = []
    for name in names:
        if name is None:
            continue
        key = match_key(name)
        if key and key not in seen:
            seen.add(key)
            out.append((name, BUILD))
    return tuple(out)


def font_file(entry: Mapping[str, Any], pin: Pin, settings: Settings) -> FontFileRef | None:
    """The original font file the entry's preview is made from, pinned to the commit."""
    source = _text(entry.get("imagePreviewFontSource"))
    if source is None or not _relative_path(source):
        return None
    if not source.lower().endswith(FONT_EXTENSIONS):
        return None  # the Symbols entry names a FontForge .sfd
    return FontFileRef(url=pin.raw_url(f"{settings.unpatched_dir}/{source}"), role="regular")


def _attrs(entry: Mapping[str, Any]) -> tuple[tuple[str, Scalar], ...]:
    found: dict[str, Scalar] = {}
    for name, key in (("cask_name", "caskName"), ("patched_name", "patchedName")):
        if (text := _text(entry.get(key))) is not None:
            found[name] = text
    if (version := _text(entry.get("version"))) is not None:
        found["version"] = version
    return attrs(**found)


def universe_record(entry: Mapping[str, Any], pin: Pin, settings: Settings) -> UniverseRecord:
    """The universe record of one ``fonts.json`` entry (checked by ``font_entries``)."""
    folder, family = entry["folderName"], entry["unpatchedName"]
    monospaced = entry.get("isMonospaced")
    ref = font_file(entry, pin, settings)
    return UniverseRecord(
        source=NAME,
        key=SourceKey(NAMESPACE, folder),
        family=family,
        is_monospace=monospaced if type(monospaced) is bool else None,
        status="live",
        files=(ref,) if ref is not None else (),
        names=build_names(family, _text(entry.get("patchedName")), folder),
        drop=settings.drop.get(folder),
        attrs=_attrs(entry),
    )


def license_fact(entry: Mapping[str, Any], text_url: str | None = None) -> LicenseFact | None:
    """The license the entry states for the original font, if it states one."""
    raw = _text(entry.get("licenseId"))
    if raw is None:
        return None
    rfn = entry.get("RFN")
    exception = _text(entry.get("RFNException"))
    return LicenseFact(
        source=NAME,
        key=SourceKey(NAMESPACE, entry["folderName"]),
        raw=raw,
        spdx=raw if is_spdx_expression(raw) else None,
        text_url=text_url,
        rfn=rfn if type(rfn) is bool else None,
        attrs=attrs(rfn_exception=exception) if exception is not None else (),
    )


# --- the collector -------------------------------------------------------------------------------


def _record(ctx: FetchContext, result: FetchResult, *, kept: bool) -> None:
    for record in result.to_records(kept=kept):
        ctx.out.record_fetch(record)


def _reusable(previous: Snapshot | None, log: logging.Logger) -> tuple[Snapshot, Pin] | None:
    """The previous snapshot and its pin when both extracts can be copied over.

    A snapshot without them, or whose ``pin.json`` does not read, is not reused:
    no validators are sent, so the file is downloaded again.
    """
    if previous is None or not (previous.has(PIN) and previous.has(FONTS)):
        return None
    try:
        return previous, Pin.from_json(previous.load_json(PIN))
    except ValueError as exc:  # also SnapshotCorrupt and JSONDecodeError
        log.warning("%s: not reusing the %s snapshot: %s", NAME, previous.date, exc)
        return None


class NerdFonts(CollectorBase):
    """Nerd Fonts ``fonts.json``: the patched families, their build names and licenses."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 2  # 2 adds licenses.json; version 1 snapshots parse without it
    hosts: ClassVar[tuple[str, ...]] = (API_HOST, RAW_HOST)
    emits: ClassVar[tuple[type, ...]] = (UniverseRecord, LicenseFact)
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Pin the file's last commit, then keep the file at that commit (see the module doc)."""
        settings = _settings(ctx.settings)
        reusable = _reusable(ctx.previous, ctx.log)
        previous, old_pin = reusable if reusable is not None else (None, None)
        url = commits_url(settings)
        answer = ctx.fetcher.get(url, headers=API_HEADERS, previous=previous_record(previous, url))
        _record(ctx, answer, kept=False)
        if answer.not_modified:
            if previous is None or old_pin is None:  # validators come only from `previous`
                raise FetchError(f"{url}: 304 without a previous snapshot")
            pin = old_pin
            ctx.out.copy_extract(previous, PIN)
            ctx.out.copy_extract(previous, FONTS)
            ctx.out.note(f"{settings.path} unchanged since {pin.commit[:12]} (304)")
        else:
            pin = pin_from_commits(answer.json(), settings)
            if previous is not None and old_pin is not None and old_pin.same_file(pin):
                ctx.out.copy_extract(previous, FONTS)
                ctx.out.note(f"{settings.path} unchanged since {pin.commit[:12]}")
            else:
                self._fetch_file(ctx, pin, settings)
            ctx.out.write_json(PIN, pin.to_json())
        self._license_list(ctx, pin, settings, previous)
        # A committer's clock, or a --date in the past, must not date the data after the run.
        ctx.out.set_data_date(min(pin.day, ctx.run_date))
        ctx.log.info("%s: %s at %s (%s)", self.name, pin.path, pin.commit[:12], pin.committed_at)

    def _license_list(
        self, ctx: FetchContext, pin: Pin, settings: Settings, previous: Snapshot | None
    ) -> None:
        """Write ``licenses.json``: copied when the previous snapshot has it for this commit."""
        directory = settings.unpatched_dir
        if previous is not None and previous.has(LICENSES):
            try:
                reusable = read_licenses(previous.load_json(LICENSES), pin, directory) is not None
            except ValueError:  # also SnapshotCorrupt and JSONDecodeError
                reusable = False
            if reusable:
                ctx.out.copy_extract(previous, LICENSES)
                return
        url = pin.tree_url(directory)
        try:
            answer = ctx.fetcher.get(url, headers=API_HEADERS)
            _record(ctx, answer, kept=False)
            files = license_files(answer.json())
        except (FetchError, ValueError) as exc:
            ctx.log.warning("%s: no license file list: %s", self.name, exc)
            ctx.out.note(f"no license file list at {pin.commit[:12]}: {exc}")
            return
        ctx.out.write_json(LICENSES, licenses_doc(pin, directory, files))
        ctx.log.info("%s: %d license files under %s", self.name, len(files), directory)

    def _fetch_file(self, ctx: FetchContext, pin: Pin, settings: Settings) -> None:
        result = ctx.fetcher.get(pin.raw_url(pin.path))
        _record(ctx, result, kept=True)
        data = result.body()
        try:
            doc = json.loads(data)
        except ValueError as exc:
            raise ValueError(f"{FONTS} at {pin.commit[:12]}: not JSON ({exc})") from exc
        entries = font_entries(doc)
        check_shrink(len(entries), previous_entries(ctx.previous), settings.min_share)
        ctx.out.write_bytes(FONTS, data, rows=len(entries))
        ctx.log.info("%s: %d entries (%d bytes)", self.name, len(entries), len(data))

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """A universe record and (when stated) a license fact per ``fonts.json`` folder.

        A folder listed twice keeps its first entry, with a warning, so each key
        has one record of each type.
        """
        settings = _settings(ctx.settings)
        pin = Pin.from_json(ctx.snapshot.load_json(PIN))
        files: list[str] = []
        if ctx.snapshot.has(LICENSES):
            listed = read_licenses(ctx.snapshot.load_json(LICENSES), pin, settings.unpatched_dir)
            if listed is None:
                ctx.log.warning(
                    "%s: %s is not for this pin; license texts left out", NAME, LICENSES
                )
            files = listed or []
        folders: set[str] = set()
        for entry in font_entries(ctx.snapshot.load_json(FONTS)):
            folder = entry["folderName"]
            if folder in folders:
                ctx.log.warning(
                    "%s: folder %s is listed again; the first entry is kept", NAME, folder
                )
                continue
            folders.add(folder)
            yield universe_record(entry, pin, settings)
            path = license_path(entry, files)
            text_url = pin.raw_url(f"{settings.unpatched_dir}/{path}") if path else None
            if (fact := license_fact(entry, text_url)) is not None:
                yield fact


COLLECTOR = NerdFonts()
