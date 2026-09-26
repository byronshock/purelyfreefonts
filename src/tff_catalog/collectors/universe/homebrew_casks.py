"""Universe collector "homebrew_casks": Homebrew's font casks (design-m1 §2.4).

**Source.** Three unauthenticated GETs, all BSD-2-Clause data (ruling T1):

- ``formulae.brew.sh/api/cask.json``: every cask of ``homebrew/cask``, about
  19 MB of JSON (2 MB gzipped on the wire, which the fetcher decodes). About
  2,600 of the 7,760 casks are fonts, tokens ``font-*``; there is no license
  field. ``Last-Modified`` says when the API was generated.
- ``raw.githubusercontent.com/Homebrew/homebrew-cask/main/cask_renames.json``:
  ``{old token: new token}`` (the API's own copy answers 404).
- ``formulae.brew.sh/api/cask_tap_migrations.json``: ``{token: tap}`` for
  casks that moved to another tap (no font cask on 2026-09-25).

**Fetch.** ``cask.json`` is streamed to ``ctx.raw`` and never kept. Three
extracts are written, each only the font part of its body:

- ``casks.jsonl.gz``: one row per cask whose token starts with
  ``token_prefix``, sorted by token, holding ``EXTRACT_FIELDS``: ``only_path``
  is ``url_specs.only_path`` (the folder a git URL installs), ``fonts`` the
  source paths of the cask's ``font`` artifacts in cask order. Nothing else
  (checksums of the Ruby source, platforms, analytics, caveats) is kept.
- ``renames.json``: the ``cask_renames.json`` entries whose old or new token
  starts with ``token_prefix``.
- ``tap_migrations.json``: the tap migrations of such tokens.

A body that is not a list of casks, holds no font cask, or holds fewer than
``min_share`` of the previous snapshot's font casks fails the fetch, so the
stale policy reuses the last good snapshot instead of letting casks vanish.
The manifest's ``data_date`` is ``cask.json``'s ``Last-Modified`` day (the run
date without one, and never after it), and its notes give the homebrew-cask
commit the API was built from. There is no conditional GET: the API is rebuilt
every few minutes (``max-age=600``), so a monthly run never gets a 304.

**Parse** (offline, pure): one ``UniverseRecord`` per extract row, keyed
``brew-cask:<token>``, status ``delisted`` when the cask is disabled (it no
longer installs), ``deprecated`` when deprecated, else ``live``:

- ``family``: the cask's first ``name`` with runs of white space collapsed.
  When it matches ``build_pattern`` ("CaskaydiaCove Nerd Font (Cascadia Code)",
  "JetBrainsMono Nerd Font families (JetBrains Mono)") Homebrew names the font
  it patched, the pattern's ``parent`` group. With ``parent_relation`` ``build``
  (the default) the family is that parent and the build's name (the pattern's
  ``family`` group) is asserted as a ``build`` name, as ``nerdfonts`` does:
  the universe folds the build into the parent and never takes the build's
  name for the family's display name. With ``related`` (for when the parent
  names are not family names) the family is the build's name and the parent
  is asserted as ``related``, which only informs. The drop code is taken from
  the build's name either way.
- ``names``, sorted: every old token (the cask's ``old_tokens``, and each
  ``renames.json`` entry whose chain of renames reaches this token as its
  first current font cask, as the ``homebrew`` alias miner resolves it) as
  ``rename``; the parent above; the cask's other ``name`` values
  (a CJK name, "SF Pro" next to "San Francisco Pro") as ``related``, which
  informs and never folds. Old tokens are cask tokens, not family names. A
  name whose match key is the family's, or an earlier name's, is left out.
- ``urls``: the ``homepage``; ``files``: the download, only when it is a
  font file itself (by extension, the google/fonts raw links), with the cask's
  sha256 unless ``no_check``, role ``variable`` for a ``[axes]`` file name,
  ``italic`` for an italic one, else ``regular``. For a zip download, each font
  path the cask installs (``fonts``) as a zip member reference
  (``fontfiles.member_url``, read by range), with no sha256 (the cask's is the
  archive's) and the role of the path's file name.
- ``drop``: the code of the first ``drop_names`` pattern matching the family
  name, else ``proprietary`` when the download host is in
  ``proprietary_hosts`` (Apple's SF and New York casks), else None.
- ``attrs``: ``tap`` (reserved), ``version``, ``url`` (the download; the
  name ``corrections`` reads for gate M5, which joins a GitHub repository's
  counts to the homebrew group when the cask downloads its release asset),
  ``only_path``, ``gf_dir`` (``<license folder>/<slug>`` when the cask
  installs from google/fonts, by raw link or by git URL plus ``only_path``),
  ``github_repo`` and ``release`` (``<owner>/<repo>`` and the tag when the
  download is a GitHub release asset), ``fonts`` (the number of font
  artifacts), and for deprecated or disabled casks ``status_date``,
  ``status_reason`` and ``replacement`` (the successor cask, when named).

Tap migrations are logged, not recorded: a migrated cask is gone from
``cask.json``, so it has no row to annotate.
"""

import logging
import re
from collections.abc import Iterable, Iterator, Mapping, Set
from dataclasses import dataclass
from datetime import UTC, date
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Literal
from urllib.parse import unquote, urlsplit

from tff_catalog import fontfiles
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.keys import match_key
from tff_catalog.records import (
    DROP_REASONS,
    FileRole,
    FontFileRef,
    Record,
    Scalar,
    SourceKey,
    Status,
    UniverseRecord,
    attrs,
)
from tff_catalog.store import Snapshot

NAME = "homebrew_casks"
API_HOST = "formulae.brew.sh"
RAW_HOST = "raw.githubusercontent.com"
CASK_URL = f"https://{API_HOST}/api/cask.json"
RENAMES_URL = f"https://{RAW_HOST}/Homebrew/homebrew-cask/main/cask_renames.json"
TAP_MIGRATIONS_URL = f"https://{API_HOST}/api/cask_tap_migrations.json"
RAW_CASKS = "cask.json"
CASKS_EXTRACT = "casks.jsonl.gz"
RENAMES_EXTRACT = "renames.json"
TAP_MIGRATIONS_EXTRACT = "tap_migrations.json"
NAMESPACE = "brew-cask"
FONT_EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2")

# The only fields of a cask that reach the store, in this order.
EXTRACT_FIELDS = (
    "token",
    "tap",
    "name",
    "homepage",
    "url",
    "only_path",
    "version",
    "sha256",
    "old_tokens",
    "deprecated",
    "deprecation_date",
    "deprecation_reason",
    "deprecation_replacement_cask",
    "disabled",
    "disable_date",
    "disable_reason",
    "disable_replacement_cask",
    "fonts",
)

# google/fonts download links: a raw file of one family folder, or the git
# repository with url_specs.only_path naming the folder.
_GF_RAW = re.compile(
    r"^https://(?:github\.com/google/fonts/(?:raw|blob)/[^/]+"
    r"|raw\.githubusercontent\.com/google/fonts/[^/]+)"
    r"/(?P<dir>(?:ofl|apache|ufl)/[^/]+)/"
)
_GF_GIT = re.compile(r"^https://github\.com/google/fonts(?:\.git)?/?$")
_GF_DIR = re.compile(r"^(?:ofl|apache|ufl)/[^/]+$")
_GH_ASSET = re.compile(
    r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/releases/download/(?P<tag>[^/]+)/[^/]+$"
)

# Defaults of the Settings, which config/sources/homebrew_casks.toml spells out.
TOKEN_PREFIX = "font-"
MIN_SHARE = 0.9
BUILD_PATTERN = r"^(?P<family>.+?\bNerd Font)(?: families)?\s*\((?P<parent>[^()]+)\)$"
DROP_NAMES: tuple[tuple[str, str], ...] = (
    (
        "icon",
        r"\bIcon(?:s|ic)?\b|^(?:Academicons|Codicon|Devicons)$|^Font Awesome\b"
        r"|^Material (?:Icons|Symbols)\b|^Symbols Nerd Font\b",
    ),
    ("emoji", r"\bEmoji\b"),
    ("barcode", r"\bBarcode\b"),
    ("math", r"\bMath\b"),
    ("music", r"\bMusic(?:al)?\b"),
    ("symbol", r"\bSymbols?\b|\b(?:Web|Wing|Yarn)dings\b|\bDingbats?\b"),
)
PROPRIETARY_HOSTS: tuple[str, ...] = ("devimages-cdn.apple.com",)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/homebrew_casks.toml``."""

    cask_url: str = CASK_URL
    renames_url: str = RENAMES_URL
    tap_migrations_url: str = TAP_MIGRATIONS_URL
    token_prefix: str = TOKEN_PREFIX
    min_share: float = MIN_SHARE
    build_pattern: str = BUILD_PATTERN
    parent_relation: Literal["build", "related"] = "build"
    drop_names: tuple[tuple[str, str], ...] = DROP_NAMES
    proprietary_hosts: tuple[str, ...] = PROPRIETARY_HOSTS

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        for field, host in (
            ("cask_url", API_HOST),
            ("renames_url", RAW_HOST),
            ("tap_migrations_url", API_HOST),
        ):
            url = getattr(self, field)
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname != host:
                raise ConfigError(f"{where}.{field}: must be an https URL on {host}, got {url!r}")
        if not self.token_prefix.strip():
            raise ConfigError(f"{where}.token_prefix: must not be empty")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")
        build = _compile(self.build_pattern, f"{where}.build_pattern")
        if not {"family", "parent"} <= set(build.groupindex):
            raise ConfigError(f"{where}.build_pattern: needs groups (?P<family>) and (?P<parent>)")
        for code, pattern in self.drop_names:
            if code not in DROP_REASONS:
                raise ConfigError(
                    f"{where}.drop_names: {code!r} is not one of {sorted(DROP_REASONS)}"
                )
            _compile(pattern, f"{where}.drop_names")
        for host in self.proprietary_hosts:
            if not host or host != host.strip().lower() or "/" in host:
                raise ConfigError(f"{where}.proprietary_hosts: {host!r} is not a bare host name")


def _compile(pattern: str, where: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"{where}: bad pattern {pattern!r}: {exc}") from exc


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- small readers -------------------------------------------------------------------------------


def _text(value: object) -> str | None:
    """A string with non-space content, stripped; else None."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _texts(value: object) -> list[str]:
    """The non-empty strings of a list, stripped, first occurrence kept, in order."""
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(t for v in value if (t := _text(v)) is not None))


def _flag(value: object) -> bool:
    return value is True


def _day(value: object) -> str | None:
    """An ISO date string (``YYYY-MM-DD``, the start of a longer stamp), else None."""
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


# --- fetch: the extracts -------------------------------------------------------------------------


def font_paths(artifacts: object) -> list[str]:
    """Source paths of a cask's ``font`` artifacts, in cask order, each once."""
    if not isinstance(artifacts, list):
        return []
    paths: list[str] = []
    for artifact in artifacts:
        if isinstance(artifact, dict) and isinstance(artifact.get("font"), list):
            first = artifact["font"][0] if artifact["font"] else None
            if (path := _text(first)) is not None:
                paths.append(path)
    return list(dict.fromkeys(paths))


def extract_row(cask: Mapping[str, Any]) -> dict[str, Any]:
    """One cask cut to ``EXTRACT_FIELDS`` (the token must be a non-empty string)."""
    specs = cask.get("url_specs")
    only_path = _text(specs.get("only_path")) if isinstance(specs, dict) else None
    row: dict[str, Any] = {
        "token": _text(cask["token"]),
        "tap": _text(cask.get("tap")),
        "name": _texts(cask.get("name")),
        "homepage": _text(cask.get("homepage")),
        "url": _text(cask.get("url")),
        "only_path": only_path,
        "version": _text(cask.get("version")),
        "sha256": _text(cask.get("sha256")),
        "old_tokens": sorted(_texts(cask.get("old_tokens"))),
        "deprecated": _flag(cask.get("deprecated")),
        "deprecation_date": _day(cask.get("deprecation_date")),
        "deprecation_reason": _text(cask.get("deprecation_reason")),
        "deprecation_replacement_cask": _text(cask.get("deprecation_replacement_cask")),
        "disabled": _flag(cask.get("disabled")),
        "disable_date": _day(cask.get("disable_date")),
        "disable_reason": _text(cask.get("disable_reason")),
        "disable_replacement_cask": _text(cask.get("disable_replacement_cask")),
        "fonts": font_paths(cask.get("artifacts")),
    }
    return {k: row[k] for k in EXTRACT_FIELDS}


def extract_casks(doc: object, prefix: str) -> tuple[list[dict[str, Any]], list[str]]:
    """The font casks of a ``cask.json`` body as extract rows, sorted by token, and notes.

    Raises ``ValueError`` when ``doc`` is not a list of casks or holds no cask
    whose token starts with ``prefix``.
    """
    if not isinstance(doc, list):
        raise ValueError(f"cask.json: expected a list of casks, got {type(doc).__name__}")
    rows: dict[str, dict[str, Any]] = {}
    repeated: list[str] = []
    heads: set[str] = set()
    for cask in doc:
        token = _text(cask.get("token")) if isinstance(cask, dict) else None
        if token is None or not token.startswith(prefix):
            continue
        if token in rows:
            repeated.append(token)
            continue
        rows[token] = extract_row(cask)
        if (head := _text(cask.get("tap_git_head"))) is not None:
            heads.add(head)
    if not rows:
        raise ValueError(f"cask.json: no cask named {prefix}*; a broken response?")
    notes = []
    if repeated:
        notes.append(f"repeated tokens kept once: {', '.join(sorted(set(repeated)))}")
    if heads:
        notes.append(f"homebrew-cask commit {', '.join(sorted(heads))}")
    return [rows[t] for t in sorted(rows)], notes


def font_entries(doc: object, prefix: str, what: str) -> dict[str, str]:
    """The ``{key: value}`` string pairs of a JSON object where either side starts with ``prefix``.

    Serves ``cask_renames.json`` (old token -> new token) and
    ``cask_tap_migrations.json`` (token -> tap). Raises ``ValueError`` when
    ``doc`` is not an object.
    """
    if not isinstance(doc, dict):
        raise ValueError(f"{what}: expected a JSON object, got {type(doc).__name__}")
    out = {}
    for key, value in doc.items():
        old, new = _text(key), _text(value)
        if (
            old is not None
            and new is not None
            and (old.startswith(prefix) or new.startswith(prefix))
        ):
            out[old] = new
    return dict(sorted(out.items()))


def check_shrink(casks: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when ``casks`` is under ``min_share`` of the previous snapshot's."""
    if before is not None and casks < min_share * before:
        raise ValueError(
            f"cask.json: {casks} font casks, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken response?"
        )


def previous_casks(previous: Snapshot | None) -> int | None:
    """The row count of the previous snapshot's casks extract, if it has one."""
    if previous is None:
        return None
    entry = previous.manifest.extract(CASKS_EXTRACT)
    return None if entry is None else entry.rows


def modified_day(last_modified: str | None) -> date | None:
    """The UTC day of an HTTP ``Last-Modified`` value, or None when absent or unreadable."""
    if not last_modified:
        return None
    try:
        moment = parsedate_to_datetime(last_modified)
    except TypeError, ValueError:
        return None
    return (moment.astimezone(UTC) if moment.tzinfo else moment).date()


# --- parse: records ------------------------------------------------------------------------------


def rename_target(old: str, renames: Mapping[str, str], tokens: Set[str]) -> str | None:
    """The first token in ``tokens`` on ``old``'s chain of renames (``a -> b -> c``), or None
    when the chain ends elsewhere or loops first. Same rule as the ``homebrew`` alias miner,
    so a record names exactly the old tokens the miner sends to its key."""
    seen = {old}
    new = renames.get(old)
    while new is not None and new not in tokens:
        if new in seen:
            return None
        seen.add(new)
        new = renames.get(new)
    return new


def split_build(name: str, pattern: re.Pattern[str]) -> tuple[str, str | None]:
    """(family, parent) of a cask name: the parent a build pattern names, else None."""
    m = pattern.match(name)
    if m is None:
        return name, None
    family = " ".join(m.group("family").split())
    parent = " ".join(m.group("parent").split())
    if not family or not parent:
        return name, None
    return family, parent


def gf_dir(url: str | None, only_path: str | None) -> str | None:
    """``<license folder>/<slug>`` when the download is from google/fonts, else None."""
    if url is None:
        return None
    if (m := _GF_RAW.match(url)) is not None:
        return unquote(m.group("dir"))
    if _GF_GIT.match(url) and only_path is not None:
        folder = only_path.strip("/")
        return folder if _GF_DIR.match(folder) else None
    return None


def url_host(url: str | None) -> str | None:
    if url is None:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def is_font_file(url: str) -> bool:
    """Whether ``url`` downloads a font file itself (by extension, query ignored)."""
    try:
        path = unquote(urlsplit(url).path).lower()
    except ValueError:
        return False
    return path.endswith(FONT_EXTENSIONS)


def file_role(url: str) -> FileRole:
    """``variable`` for a ``[axes]`` file name, ``italic`` for an italic one, else ``regular``."""
    return name_role(unquote(urlsplit(url).path))


def name_role(path: str) -> FileRole:
    """``file_role`` of a file path."""
    file_name = path.rsplit("/", 1)[-1]
    if "[" in file_name:
        return "variable"
    if "italic" in file_name.lower():
        return "italic"
    return "regular"


def font_files(row: Mapping[str, Any]) -> tuple[FontFileRef, ...]:
    """The download as a font file, when it is one; for a zip download, each font the
    cask installs from it as a zip member reference (``fontfiles.member_url``)."""
    url = _text(row.get("url"))
    if url is None:
        return ()
    if is_zip(url):
        # The cask's sha256 is the archive's, not a member's: members carry none.
        refs = {
            fontfiles.member_url(url, path): FontFileRef(
                url=fontfiles.member_url(url, path), role=name_role(path)
            )
            for path in _texts(row.get("fonts"))
            if path.lower().endswith(FONT_EXTENSIONS) and not path.startswith(("/", "../"))
        }
        return tuple(refs[u] for u in sorted(refs))
    if not is_font_file(url):
        return ()
    sha = _text(row.get("sha256"))
    sha = sha.lower() if sha is not None and re.fullmatch(r"[0-9a-fA-F]{64}", sha) else None
    return (FontFileRef(url=url, sha256=sha, role=file_role(url)),)


def is_zip(url: str) -> bool:
    """Whether ``url`` downloads a zip archive over https (by extension, query ignored)."""
    try:
        path = unquote(urlsplit(url).path).lower()
    except ValueError:
        return False
    return url.startswith("https://") and path.endswith(fontfiles.ZIP_EXTENSIONS)


def status(row: Mapping[str, Any]) -> Status:
    """``delisted`` when disabled (it no longer installs), ``deprecated``, else ``live``."""
    if row.get("disabled") is True:
        return "delisted"
    if row.get("deprecated") is True:
        return "deprecated"
    return "live"


def drop_code(family: str, url: str | None, settings: Settings) -> str | None:
    """The first ``drop_names`` code matching ``family``; else ``proprietary`` by download host."""
    for code, pattern in settings.drop_names:
        if re.search(pattern, family):
            return code
    if url_host(url) in settings.proprietary_hosts:
        return "proprietary"
    return None


def _status_attrs(row: Mapping[str, Any]) -> dict[str, Scalar]:
    """Date, reason and successor of a disabled (else deprecated) cask."""
    for flag, prefix in (("disabled", "disable"), ("deprecated", "deprecation")):
        if row.get(flag) is True:
            found = {
                "status_date": _day(row.get(f"{prefix}_date")),
                "status_reason": _text(row.get(f"{prefix}_reason")),
                "replacement": _text(row.get(f"{prefix}_replacement_cask")),
            }
            return {k: v for k, v in found.items() if v is not None}
    return {}


def record_attrs(row: Mapping[str, Any]) -> tuple[tuple[str, Scalar], ...]:
    """The record's attrs (module docstring)."""
    url, only_path = _text(row.get("url")), _text(row.get("only_path"))
    given = {
        "tap": _text(row.get("tap")),
        "version": _text(row.get("version")),
        "url": url,
        "only_path": only_path,
        "gf_dir": gf_dir(url, only_path),
    }
    found: dict[str, Scalar] = {k: v for k, v in given.items() if v is not None}
    if url is not None and (m := _GH_ASSET.match(url)) is not None:
        found["github_repo"] = m.group("repo")
        found["release"] = unquote(m.group("tag"))
    fonts = row.get("fonts")
    found["fonts"] = len(fonts) if isinstance(fonts, list) else 0
    found |= _status_attrs(row)
    return attrs(**found)


def universe_record(
    row: Mapping[str, Any],
    old_tokens: Set[str],
    settings: Settings,
    build: re.Pattern[str],
    log: logging.Logger | None = None,
) -> UniverseRecord:
    """The universe record of one extract row (whose ``token`` is a non-empty string).

    ``old_tokens`` are the cask's old tokens (``old_names`` gives them), asserted
    as ``rename`` names; the row's own ``old_tokens`` field is not read again.
    """
    token = row["token"]
    names = _texts(row.get("name"))
    if names:
        primary = " ".join(names[0].split())
    else:
        primary = token.removeprefix(settings.token_prefix) or token
        if log is not None:
            log.warning("%s: cask %s has no name; family %r from its token", NAME, token, primary)
    built, parent = split_build(primary, build)
    family = built
    candidates = [(old, "rename") for old in sorted(old_tokens - {token})]
    if parent is not None and settings.parent_relation == "build":
        # A build's record names its parent family, as nerdfonts' records do; asserting the
        # parent as the build's "build" name instead made the universe display the build's name.
        family = parent
        candidates.append((built, "build"))
    elif parent is not None:
        candidates.append((parent, settings.parent_relation))
    candidates += [(" ".join(other.split()), "related") for other in names[1:]]
    url = _text(row.get("url"))
    homepage = _text(row.get("homepage"))
    return UniverseRecord(
        source=NAME,
        key=SourceKey(NAMESPACE, token),
        family=family,
        status=status(row),
        urls=(("homepage", homepage),) if homepage is not None else (),
        files=font_files(row),
        names=distinct_names(candidates, (family, primary)),
        drop=drop_code(built, url, settings),
        attrs=record_attrs(row),
    )


def distinct_names(
    candidates: Iterable[tuple[str, str]], own: Iterable[str]
) -> tuple[tuple[str, str], ...]:
    """``(name, relation)`` pairs, sorted, keeping the first of each match key and none
    whose match key is one of the ``own`` names' (the family's spellings)."""
    seen = {match_key(name) for name in own}
    kept = []
    for name, relation in candidates:
        key = match_key(name)
        if key and key not in seen:
            seen.add(key)
            kept.append((name, relation))
    return tuple(sorted(kept))


def renamed_to(renames: Mapping[str, str], tokens: Set[str]) -> dict[str, set[str]]:
    """For each token in ``tokens``, the old tokens whose rename chain reaches it first.

    An old token that is in ``tokens`` itself is skipped: a new cask took the
    name back, so the name is its own.
    """
    out: dict[str, set[str]] = {}
    for old in sorted(renames):
        if old in tokens:
            continue
        if (new := rename_target(old, renames, tokens)) is not None:
            out.setdefault(new, set()).add(old)
    return out


def old_names(
    rows: Mapping[str, Mapping[str, Any]], renames: Mapping[str, str]
) -> dict[str, set[str]]:
    """Each font cask's old tokens: its own ``old_tokens`` and the ``renames`` chains that
    reach it first, without tokens that are current casks (the ``homebrew`` miner's rule)."""
    out = renamed_to(renames, rows.keys())
    for token, row in rows.items():
        for old in _texts(row.get("old_tokens")):
            if old not in rows:
                out.setdefault(token, set()).add(old)
    return out


def _json_object(snapshot: Snapshot, name: str) -> dict[str, str]:
    if not snapshot.has(name):
        return {}
    doc = snapshot.load_json(name)
    if not isinstance(doc, dict):
        raise ValueError(f"{snapshot.path / name}: expected a JSON object")
    return {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, str)}


class HomebrewCasks(CollectorBase):
    """``formulae.brew.sh/api/cask.json``: Homebrew's ``font-*`` casks, with their renames."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (API_HOST, RAW_HOST)
    emits: ClassVar[tuple[type, ...]] = (UniverseRecord,)
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Three GETs; write ``casks.jsonl.gz``, ``renames.json`` and ``tap_migrations.json``."""
        settings = _settings(ctx.settings)
        casks = ctx.fetcher.get(settings.cask_url, to=ctx.raw.file(RAW_CASKS))
        for record in casks.to_records():
            ctx.out.record_fetch(record)
        rows, notes = extract_casks(casks.json(), settings.token_prefix)
        check_shrink(len(rows), previous_casks(ctx.previous), settings.min_share)
        extracts = {}
        for name, url in (
            (RENAMES_EXTRACT, settings.renames_url),
            (TAP_MIGRATIONS_EXTRACT, settings.tap_migrations_url),
        ):
            result = ctx.fetcher.get(url)
            for record in result.to_records():
                ctx.out.record_fetch(record)
            extracts[name] = font_entries(result.json(), settings.token_prefix, url)
        for note in notes:
            ctx.out.note(note)
        ctx.out.write_jsonl(CASKS_EXTRACT, rows)
        for name, entries in extracts.items():
            ctx.out.write_json(name, entries)
        modified = modified_day(casks.last_modified)
        ctx.out.set_data_date(min(modified, ctx.run_date) if modified else ctx.run_date)
        ctx.log.info(
            "%s: %d font casks, %d renames, %d tap migrations (%d bytes of cask.json)",
            self.name,
            len(rows),
            len(extracts[RENAMES_EXTRACT]),
            len(extracts[TAP_MIGRATIONS_EXTRACT]),
            casks.size,
        )

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One universe record per font cask of the extract."""
        settings = _settings(ctx.settings)
        build = re.compile(settings.build_pattern)
        rows: dict[str, dict[str, Any]] = {}
        for row in ctx.snapshot.iter_jsonl(CASKS_EXTRACT):
            token = _text(row.get("token")) if isinstance(row, dict) else None
            if token is not None and token.startswith(settings.token_prefix):
                rows.setdefault(token, {**row, "token": token})
        old = old_names(rows, _json_object(ctx.snapshot, RENAMES_EXTRACT))
        migrated = sorted(
            t
            for t in _json_object(ctx.snapshot, TAP_MIGRATIONS_EXTRACT)
            if t.startswith(settings.token_prefix)
        )
        if migrated:
            ctx.log.info("%s: casks moved to other taps: %s", self.name, ", ".join(migrated))
        for token in sorted(rows):
            yield universe_record(rows[token], old.get(token, set()), settings, build, ctx.log)


COLLECTOR = HomebrewCasks()
