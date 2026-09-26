"""Ranking collector "arch_repos": font dependency edges in Arch and CachyOS (design-m1 §2.4).

**Source.** The pacman sync databases: Arch's ``core``, ``extra`` and
``multilib`` from ``geo.mirror.pkgbuild.com`` (gzip, about 9 MB together) and
CachyOS's own ``cachyos`` repository from ``mirror.cachyos.org`` (zstd, about
0.5 MB). Each is a compressed tar of ``<name>-<version>/desc`` files: a
``%FIELD%`` line, then one value per line, then a blank line. The compression
is read from the magic bytes (gzip, zstd, xz, bzip2 or none), never from the
name. pacman has no Recommends: ``DEPENDS`` are installed with the package,
``OPTDEPENDS`` (``pkg: reason``) never are, and ``GROUPS`` is the only sign of
a group install (``pacman -S nerd-fonts``). The records feed step 10's Linux
dependency abstentions (gate M8) for the Arch pkgstats source, whose counts
include CachyOS systems, and gate M4's ``nerd-fonts`` group floor; the
licenses feed L2 (step 6a). EndeavourOS is not read: its repository has no
font dependencies, and its preinstalled fonts are in ``config/preinstalled.toml``
(gate C2 (rec)). Ruling T1 lets the Arch rows be published and trimmed into
fixtures; CachyOS fixtures are synthetic.

**Fetch.** Each database is streamed into ``ctx.raw`` and never kept (no
conditional GET: ``extra`` changes every hour). A package is *font-like* when
its name matches ``font_pattern`` (and not ``not_fonts``), it is in one of
``font_groups``, or it provides a name matching ``font_provides`` (the virtual
``ttf-font``, ``ttf-font-nerd``). The font targets are the font-like names plus
every name a font-like package provides. The extracts keep, per repository,
``<repo>.jsonl.gz``, sorted by name:

- every font-like package in full: ``name``, ``base``, ``version``, ``url``,
  ``license``, ``groups``, ``provides``, ``replaces``, ``depends``,
  ``optdepends``, with ``font: true``;
- every other package whose ``DEPENDS`` or ``OPTDEPENDS`` name a font target,
  with only those entries and its name, base and version (``font: false``).

Signatures, checksums and ``PACKAGER`` (a person's name and email) never reach
the store. ``repos.json`` lists each repository's name, distribution, URL,
extract and counts; parse reads it, so a snapshot describes itself. A fetch
fails, and the stale policy steps in, when a database is unreadable or empty,
when a repository lists fewer than ``min_share`` of its packages in the
previous snapshot, or when the font-like packages number fewer than
``min_fonts`` or fewer than ``min_share`` of the previous snapshot's. The
manifest's ``data_date`` is the newest ``Last-Modified`` day (UTC) of the
databases, never after the run date.

**Parse** (offline, pure; the font-like choice made at fetch time is read from
the ``font`` flag). Keys are ``arch-pkg:<name>`` for every package, group and
virtual name, CachyOS included, since pkgstats counts both under one name.
Every record carries attrs ``repo`` and ``distro`` (``arch`` or ``cachyos``,
``config/site.toml [package_systems]``), which ``corrections`` reads to name
the system that pulls a font in. Per row:

- ``depends`` / ``optdepends`` Relations from the package to each font target
  it names (version constraints and reasons stripped; ``alt`` is always 0,
  pacman has no ``a | b``). A target that is only provided, not a font-like
  package (``ttf-font``, ``emoji-font``), gets attr ``virtual: true``: it
  cannot be credited to one font (design-m1 §8), and no package count exists
  for it, so it never makes a font abstain;
- for font-like rows, a ``provides`` Relation to each name it provides, a
  ``group`` Relation from each of its groups (subject ``arch-pkg:<group>``,
  the member as object), and one ``LicenseFact`` per ``LICENSE`` entry, raw as
  written (L2 ANDs several facts of one source); ``rfn`` is set only when the
  entry is an SPDX ``OFL-1.x-RFN`` or ``OFL-1.x-no-RFN`` id.

A package listed in several repositories (a CachyOS rebuild of an Arch
package) gives records for each, told apart by ``repo`` and ``distro``.
"""

import email.utils
import re
import tarfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.records import (
    LicenseFact,
    Record,
    Relation,
    RelationKind,
    Scalar,
    SourceKey,
    attrs,
    sort_key,
)
from tff_catalog.store import Snapshot

NAME = "arch_repos"
ARCH_HOST = "geo.mirror.pkgbuild.com"
CACHYOS_HOST = "mirror.cachyos.org"
HOSTS = (ARCH_HOST, CACHYOS_HOST)
NAMESPACE = "arch-pkg"
REPOS = "repos.json"  # the repositories a snapshot holds, and their extracts
EXTRACT_SUFFIX = ".jsonl.gz"
DISTROS = ("arch", "cachyos")  # config/site.toml [package_systems]

# Package fields that reach the extract. Everything else in a desc file
# (PACKAGER names a person; PGPSIG, checksums and sizes are noise) is dropped.
LIST_FIELDS = ("license", "groups", "provides", "replaces", "depends", "optdepends")
DEPENDENCY_FIELDS: tuple[RelationKind, ...] = ("depends", "optdepends")  # also Relation kinds
_DESC_FIELDS = {
    "name": "NAME",
    "base": "BASE",
    "version": "VERSION",
    "url": "URL",
    "license": "LICENSE",
    "groups": "GROUPS",
    "provides": "PROVIDES",
    "replaces": "REPLACES",
    "depends": "DEPENDS",
    "optdepends": "OPTDEPENDS",
}
_KEPT_DESC = frozenset(_DESC_FIELDS.values())
_DESC_FILES = ("desc", "depends")  # old databases split the dependency fields out
_FIELD_LINE = re.compile(r"^%([A-Z0-9]+)%$")
# A dependency entry: "name", "name>=1.2", "name=1:2.0-1" or "name: why" (OPTDEPENDS).
_DEP_NAME = re.compile(r"^[^\s<>=:]+")
_REPO_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_RFN = re.compile(r"^OFL-1\.[01]-(no-)?RFN$")

# Compressions a sync database comes in, by magic bytes (tarfile modes).
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x1f\x8b", "gz"),
    (b"\x28\xb5\x2f\xfd", "zst"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"BZh", "bz2"),
)
_TAR_MAGIC_AT = 257  # "ustar" in an uncompressed tar header


@dataclass(frozen=True, slots=True)
class Repo:
    """One sync database: ``[[repos]]`` in ``config/sources/arch_repos.toml``."""

    name: str  # pacman's repository name; the extract is <name>.jsonl.gz
    distro: str  # DISTROS: the package system its dependents belong to
    url: str


# Defaults of the Settings, which config/sources/arch_repos.toml spells out.
DEFAULT_REPOS = (
    Repo("core", "arch", f"https://{ARCH_HOST}/core/os/x86_64/core.db"),
    Repo("extra", "arch", f"https://{ARCH_HOST}/extra/os/x86_64/extra.db"),
    Repo("multilib", "arch", f"https://{ARCH_HOST}/multilib/os/x86_64/multilib.db"),
    Repo("cachyos", "cachyos", f"https://{CACHYOS_HOST}/repo/x86_64/cachyos/cachyos.db"),
)
# Font package names (ttf-*, otf-*, ttc-*, woff*-*, *-fonts, *-font, xorg-fonts-*), as in
# mapping.FONT_PACKAGE, plus font packages whose names do not say so.
FONT_PATTERN = r"(?:^|[-_.+])(?:x?fonts?|ttf|otf|ttc|woff2?)(?:[-_.+]|$)|^gsfonts$|^wqy-"
# Names the pattern catches that are libraries or tools, not fonts. Leaving them out
# keeps their many dependents (games on sdl2_ttf, browsers on woff2) out of the extract.
NOT_FONTS = (
    r"^(?:perl|python|haskell|lib32|ruby|lua|nodejs|rust|go)-|^sdl\d?_ttf$|^woff2$"
    r"|(?:^|-)font-(?:manager|viewer|util)$|^font-v$|^xorg-fonts-(?:encodings|alias-.+)$"
)
FONT_GROUPS = ("nerd-fonts",)
FONT_PROVIDES = r"^ttf-font"  # ttf-font, ttf-font-nerd, ttf-font-awesome
MIN_FONTS = 20  # font-like packages in all repositories (228 on 2026-09-25; the fixture has 29)
MIN_SHARE = 0.8  # of the previous snapshot's font-like packages, and of each repo's packages


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/arch_repos.toml``."""

    repos: tuple[Repo, ...] = DEFAULT_REPOS
    font_pattern: str = FONT_PATTERN
    not_fonts: str = NOT_FONTS
    font_groups: tuple[str, ...] = FONT_GROUPS
    font_provides: str = FONT_PROVIDES
    min_fonts: int = MIN_FONTS
    min_share: float = MIN_SHARE

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not self.repos:
            raise ConfigError(f"{where}.repos: list at least one repository")
        names = [r.name for r in self.repos]
        if len(set(names)) != len(names):
            raise ConfigError(f"{where}.repos: repository names repeat: {names}")
        for repo in self.repos:
            _check_repo(repo, f"{where}.repos.{repo.name}")
        for key in ("font_pattern", "not_fonts", "font_provides"):
            pattern = getattr(self, key)
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ConfigError(f"{where}.{key}: bad pattern {pattern!r}: {exc}") from exc
            # A pattern matching "" matches every name: all packages would be fonts (or none).
            if compiled.search("") and (pattern or key != "not_fonts"):
                raise ConfigError(f"{where}.{key}: {pattern!r} matches every package name")
        if self.min_fonts < 0:
            raise ConfigError(f"{where}.min_fonts: must not be negative")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")


def _check_repo(repo: Repo, where: str) -> None:
    if not _REPO_NAME.match(repo.name):
        raise ConfigError(f"{where}: name must be lower case letters, digits, - or _")
    if repo.distro not in DISTROS:
        raise ConfigError(f"{where}.distro: {repo.distro!r} is not one of {', '.join(DISTROS)}")
    parts = urlsplit(repo.url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        raise ConfigError(f"{where}.url: must be an https URL on {' or '.join(HOSTS)}")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- reading a sync database -------------------------------------------------------------------

type Package = dict[str, list[str]]  # desc field name (NAME, DEPENDS, ...) -> its lines


def compression(head: bytes) -> str:
    """The tarfile compression of a database from its first bytes ("" for a plain tar).

    Raises ``ValueError`` for anything else (an HTML error page, a truncated body).
    """
    for magic, mode in _MAGIC:
        if head.startswith(magic):
            return mode
    if head[_TAR_MAGIC_AT : _TAR_MAGIC_AT + 5] == b"ustar":
        return ""
    raise ValueError(f"not a pacman database (starts with {head[:8]!r})")


def parse_desc(text: str) -> Package:
    """The fields of one ``desc`` (or old-style ``depends``) file."""
    fields: Package = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if m := _FIELD_LINE.match(line):
            current = fields.setdefault(m.group(1), [])
        elif not line.strip():
            current = None
        elif current is not None:
            current.append(line.strip())
    return fields


def read_db(path: Path) -> dict[str, Package]:
    """Every package of the sync database at ``path``, by name, with only the fields kept.

    Raises ``ValueError`` when the file is not a readable pacman database or
    lists no package.
    """
    with path.open("rb") as fh:
        head = fh.read(512)
    mode = compression(head)
    by_dir: dict[str, Package] = {}
    try:
        with tarfile.open(path, f"r:{mode}" if mode else "r:") as tar:
            for member in tar:
                folder, _, leaf = member.name.rpartition("/")
                if not member.isfile() or leaf not in _DESC_FILES:
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", errors="replace")
                fields = parse_desc(text)
                by_dir.setdefault(folder, {}).update(
                    (k, v) for k, v in fields.items() if k in _KEPT_DESC
                )
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ValueError(f"{path.name}: unreadable pacman database: {exc}") from exc
    packages = {p["NAME"][0]: p for p in by_dir.values() if p.get("NAME")}
    if not packages:
        raise ValueError(f"{path.name}: the database lists no package")
    return packages


# --- choosing what to keep ---------------------------------------------------------------------


def dep_name(entry: str) -> str:
    """The package name of a DEPENDS, OPTDEPENDS or PROVIDES entry ("" if none)."""
    m = _DEP_NAME.match(entry.strip())
    return m.group(0) if m else ""


def is_font(pkg: Package, settings: Settings) -> bool:
    """Whether a package is font-like (module docstring); ``not_fonts`` overrides the rest."""
    name = pkg["NAME"][0]
    if settings.not_fonts and re.search(settings.not_fonts, name):
        return False
    if re.search(settings.font_pattern, name):
        return True
    if set(pkg.get("GROUPS", ())) & set(settings.font_groups):
        return True
    return any(re.search(settings.font_provides, dep_name(p)) for p in pkg.get("PROVIDES", ()))


def font_targets(fonts: Iterable[Package]) -> frozenset[str]:
    """The names a dependency can reach fonts by: font-like packages and what they provide."""
    names: set[str] = set()
    for pkg in fonts:
        names.add(pkg["NAME"][0])
        names.update(n for p in pkg.get("PROVIDES", ()) if (n := dep_name(p)))
    return frozenset(names)


def _first(pkg: Package, field: str) -> str | None:
    values = pkg.get(field)
    return values[0] if values else None


def font_row(pkg: Package) -> dict[str, Any]:
    """The extract row of a font-like package: every kept field, as the database has it."""
    row: dict[str, Any] = {
        k: _first(pkg, f) for k, f in _DESC_FIELDS.items() if k not in LIST_FIELDS
    }
    for k in LIST_FIELDS:
        row[k] = list(dict.fromkeys(pkg.get(_DESC_FIELDS[k], ())))
    row["font"] = True
    return row


def dependent_row(pkg: Package, targets: frozenset[str]) -> dict[str, Any] | None:
    """The extract row of a package that pulls a font target in, or None if it names none."""
    name = pkg["NAME"][0]
    kept = {
        k: [e for e in dict.fromkeys(pkg.get(_DESC_FIELDS[k], ())) if _pulls(e, name, targets)]
        for k in DEPENDENCY_FIELDS
    }
    if not any(kept.values()):
        return None
    row: dict[str, Any] = {k: None for k in _DESC_FIELDS if k not in LIST_FIELDS}
    row |= {"name": name, "base": _first(pkg, "BASE"), "version": _first(pkg, "VERSION")}
    row |= {k: [] for k in LIST_FIELDS} | kept
    row["font"] = False
    return row


def _pulls(entry: str, name: str, targets: frozenset[str]) -> bool:
    target = dep_name(entry)
    return target != name and target in targets


def select_rows(
    dbs: Mapping[str, Mapping[str, Package]], settings: Settings
) -> dict[str, list[dict[str, Any]]]:
    """{repo: extract rows sorted by name}: font-like packages and the packages pulling them in.

    Font targets are gathered over every repository first, since a CachyOS
    settings package depends on fonts that live in Arch's ``extra``.
    """
    fonts = {
        repo: {n for n, p in pkgs.items() if is_font(p, settings)} for repo, pkgs in dbs.items()
    }
    targets = font_targets(dbs[repo][n] for repo, names in fonts.items() for n in names)
    out: dict[str, list[dict[str, Any]]] = {}
    for repo, pkgs in dbs.items():
        rows = []
        for name in sorted(pkgs):
            if name in fonts[repo]:
                rows.append(font_row(pkgs[name]))
            elif (row := dependent_row(pkgs[name], targets)) is not None:
                rows.append(row)
        out[repo] = rows
    return out


def previous_fonts(previous: Snapshot | None) -> int | None:
    """The font-like package count of an earlier snapshot (None: none, or unreadable)."""
    if previous is None or not previous.has(REPOS):
        return None
    try:
        return sum(int(r["fonts"]) for r in previous.load_json(REPOS))
    except KeyError, TypeError, ValueError:  # an unreadable old snapshot skips the check
        return None


def check_counts(fonts: int, before: int | None, settings: Settings) -> None:
    """Raise ``ValueError`` when too few font-like packages came back to be a whole fetch."""
    if fonts < settings.min_fonts:
        raise ValueError(
            f"{NAME}: only {fonts} font-like packages (min_fonts = {settings.min_fonts}); "
            "a broken or truncated database?"
        )
    if before is not None and fonts < settings.min_share * before:
        raise ValueError(
            f"{NAME}: {fonts} font-like packages, down from {before} in the previous snapshot "
            f"(below min_share = {settings.min_share})"
        )


def previous_packages(previous: Snapshot | None) -> dict[str, int]:
    """{repository: package count} of an earlier snapshot (empty: none, or unreadable)."""
    if previous is None or not previous.has(REPOS):
        return {}
    try:
        return {str(r["name"]): int(r["packages"]) for r in previous.load_json(REPOS)}
    except KeyError, TypeError, ValueError:  # an unreadable old snapshot skips the check
        return {}


def check_packages(
    counts: Mapping[str, int], before: Mapping[str, int], settings: Settings
) -> None:
    """Raise ``ValueError`` when a repository lists under ``min_share`` of its earlier packages.

    The font count alone misses a small repository served half synced: CachyOS
    holds 6 of about 230 font-like packages but most of the settings packages.
    """
    for repo, n in counts.items():
        old = before.get(repo)
        if old is not None and n < settings.min_share * old:
            raise ValueError(
                f"{NAME}: {repo} lists {n} packages, down from {old} in the previous snapshot "
                f"(below min_share = {settings.min_share})"
            )


def last_modified_day(value: str | None) -> date | None:
    """The UTC day of an HTTP date such as ``Last-Modified`` (None: absent or unreadable)."""
    if not value:
        return None
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    # A "-0000" zone comes back naive; astimezone() would read it in the machine's zone.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


# --- records -------------------------------------------------------------------------------------


def _key(name: str) -> SourceKey:
    return SourceKey(NAMESPACE, name)


def _names(row: Mapping[str, Any], field: str) -> list[str]:
    values = row.get(field)
    return (
        [v for v in values if isinstance(v, str) and v.strip()] if isinstance(values, list) else []
    )


def row_records(
    row: Mapping[str, Any],
    repo: str,
    distro: str,
    fonts: frozenset[str],
    targets: frozenset[str],
) -> set[Record]:
    """The records of one extract row (a set: repeated entries give one record).

    ``fonts`` are the snapshot's font-like package names; ``targets`` add the
    names those packages provide (``font_targets``).
    """
    name = row["name"]
    where: dict[str, Scalar] = {"repo": repo, "distro": distro}
    out: set[Record] = set()
    for kind in DEPENDENCY_FIELDS:
        for entry in _names(row, kind):
            target = dep_name(entry)
            if target == name or target not in targets:
                continue
            extra: dict[str, Scalar] = {"virtual": True} if target not in fonts else {}
            out.add(Relation(NAME, _key(name), kind, _key(target), attrs=attrs(**where, **extra)))
    if row.get("font") is not True:
        return out
    for entry in _names(row, "provides"):
        target = dep_name(entry)
        if target and target != name:
            out.add(Relation(NAME, _key(name), "provides", _key(target), attrs=attrs(**where)))
    for group in _names(row, "groups"):
        out.add(Relation(NAME, _key(group), "group", _key(name), attrs=attrs(**where)))
    for raw in _names(row, "license"):
        out.add(LicenseFact(NAME, _key(name), raw, rfn=_rfn(raw), attrs=attrs(**where)))
    return out


def _rfn(raw: str) -> bool | None:
    m = _RFN.match(raw.strip())
    return None if m is None else m.group(1) is None


def _repos(snapshot: Snapshot) -> list[dict[str, Any]]:
    listed = snapshot.load_json(REPOS)
    if not isinstance(listed, list):
        raise ValueError(f"{snapshot.path / REPOS}: expected a list of repositories")
    return [r for r in listed if isinstance(r, dict)]


def _rows(snapshot: Snapshot, extract: str) -> list[dict[str, Any]]:
    return [
        r
        for r in snapshot.iter_jsonl(extract)
        if isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"]
    ]


def snapshot_records(snapshot: Snapshot, log: Callable[[str], None] | None = None) -> list[Record]:
    """Every record of a snapshot, deduplicated, in canonical order (``records.sort_key``)."""
    loaded = []
    for repo in _repos(snapshot):
        extract = repo.get("extract")
        if not isinstance(extract, str) or not snapshot.has(extract):
            if log is not None:
                log(f"{snapshot.source} {snapshot.date}: no extract for repository {repo!r}")
            continue
        loaded.append((str(repo["name"]), str(repo["distro"]), _rows(snapshot, extract)))
    font_rows = [row for _, _, rows in loaded for row in rows if row.get("font") is True]
    fonts = frozenset(row["name"] for row in font_rows)
    targets = fonts | {
        n for row in font_rows for p in _names(row, "provides") if (n := dep_name(p))
    }
    out: set[Record] = set()
    for repo, distro, rows in loaded:
        for row in rows:
            out |= row_records(row, repo, distro, fonts, targets)
    return sorted(out, key=sort_key)


class ArchRepos(CollectorBase):
    """Arch ``core``/``extra``/``multilib`` and CachyOS sync databases: font dependency edges."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = HOSTS
    emits: ClassVar[tuple[type, ...]] = (Relation, LicenseFact)
    group: ClassVar[str | None] = "arch"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Download every database into ``ctx.raw``; write ``repos.json`` and one extract each."""
        settings = _settings(ctx.settings)
        dbs: dict[str, dict[str, Package]] = {}
        modified: list[date] = []
        for repo in settings.repos:
            result = ctx.fetcher.get(repo.url, to=ctx.raw.file(f"{repo.name}.db"))
            for record in result.to_records():
                ctx.out.record_fetch(record)
            if result.path is None:  # the fetcher always streams when given ``to``
                raise ValueError(f"{repo.url}: the body was not saved")
            dbs[repo.name] = read_db(result.path)
            if (day := last_modified_day(result.last_modified)) is not None:
                modified.append(day)
            ctx.log.info(
                "%s: %s: %d packages, %d bytes", NAME, repo.name, len(dbs[repo.name]), result.size
            )
        check_packages(
            {name: len(pkgs) for name, pkgs in dbs.items()},
            previous_packages(ctx.previous),
            settings,
        )
        rows = select_rows(dbs, settings)
        fonts = sum(r["font"] for repo_rows in rows.values() for r in repo_rows)
        check_counts(fonts, previous_fonts(ctx.previous), settings)
        listed = []
        for repo in settings.repos:
            extract = repo.name + EXTRACT_SUFFIX
            ctx.out.write_jsonl(extract, rows[repo.name])
            n_fonts = sum(r["font"] for r in rows[repo.name])
            listed.append(
                {
                    "name": repo.name,
                    "distro": repo.distro,
                    "url": repo.url,
                    "extract": extract,
                    "packages": len(dbs[repo.name]),
                    "fonts": n_fonts,
                    "dependents": len(rows[repo.name]) - n_fonts,
                }
            )
        ctx.out.write_json(REPOS, listed)
        # A mirror's clock ahead of ours must not date the data after the run.
        ctx.out.set_data_date(min(max(modified, default=ctx.run_date), ctx.run_date))
        ctx.log.info(
            "%s: %d font-like packages, %d dependents",
            NAME,
            fonts,
            sum(r["dependents"] for r in listed),
        )

    def parse(self, ctx: ParseContext) -> list[Record]:
        """Relations and license facts of every repository in the snapshot (module docstring)."""
        _settings(ctx.settings)
        return snapshot_records(ctx.snapshot, ctx.log.warning)


COLLECTOR = ArchRepos()
