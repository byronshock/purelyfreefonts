"""Alias miner "fontsource_legacy": Fontsource's retired ids (milestone-1 step 7). Owner: P4.2.

Fontsource retires an id when a family is renamed or merged, but the id's npm
packages (``@fontsource/<id>``, ``@fontsource-variable/<id>``) stay on npm and
keep their downloads, so the npm, ecosyste.ms and jsDelivr sources go on
reporting the old keys. Methodology §5 folds them ("source-sans-pro ->
source-sans-3"); this miner proposes the rows that do it. No auto rule covers
it, so the owner reviews every row (gate A).

**Inputs.**

- The latest complete ``fontsource`` snapshot on or before the run date, read
  from ``ctx.store`` (the collector's extract format, version <=
  ``KNOWN_VERSION``): ``replacements.json``, the registry's ``{old id: new
  id}``; ``registry.json``, the commit it was read at; and every id in
  ``families/*.jsonl.gz`` and ``api-missing.json``, the current ids. Without
  a snapshot, or with one more than ``MAX_SNAPSHOT_AGE`` older than the run
  date, the miner fails and the stage keeps the committed seeds: the npm
  listing is live, so an old registry would make every font added since look
  retired.
- The npm organisation listings ``registry.npmjs.org/-/org/<org>/package`` of
  ``SCOPES``: which packages exist.
- For each retired package that ``replacements.json`` does not cover, the
  package's own ``metadata.json``, read through jsDelivr
  (``cdn.jsdelivr.net/npm/<package>/metadata.json``). Fontsource wrote it into
  every package it published: the font's name is ``fontName`` in the 4.x
  packages and ``family`` in the 5.x ones (``NAME_FIELDS``), and ``type`` is
  its origin ("google", "league", "other", "icons") in both. Every package
  retired by 2026-09-26 ended at 4.x; one retired later ends at 5.x. The
  ``x-jsd-version`` header pins the evidence URL to the version read.

Requests go through ``fetch.Fetcher`` (the project's User-Agent, 1 request a
second per host): two listings, then one request per retired package (25 on
2026-09-26).

**Candidates** (``auto`` false, ``detail`` empty, sorted):

1. Per ``replacements.json`` entry ``old -> new``, with chains followed to
   their end (``a -> b``, ``b -> c`` gives ``a -> c``; a cycle is dropped):

   - ``fs-id:old -> fs-id:new``, ``rename``;
   - ``npm:@<scope>/old -> fs-id:new``, ``package``, for each scope whose
     listing has that package.

   Evidence: ``replacements.json`` at the registry commit.
2. Per retired package ``@<scope>/<id>`` (listed, with ``<id>`` neither a
   current id nor a ``replacements.json`` key) whose ``metadata.json`` names
   its font:

   - ``npm:@<scope>/<id> -> <ns>:<name>``, ``package``;
   - ``fs-id:<id> -> <ns>:<name>``, ``rename`` (the jsDelivr stats still
     list some retired ids);

   ``<ns>`` is ``gf-family`` when ``type`` is "google", else ``font-name``.
   Evidence: the pinned ``metadata.json`` URL. The target is the name the
   package gives, nothing more: which family carries that name now (Muli is
   Mulish) is for that name's own rows (google/fonts history) or the owner. A
   package without a readable ``metadata.json`` or a name in it is logged and
   gets no row.

Nothing is inferred from how an id looks: a retired id with neither a
replacement entry nor package metadata gets no row, and ids are never paired
by prefix. Deprecated registry ids without a replacement (``kantumruy``) are
current ids with their own universe keys, so they get no row either.
"""

import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from tff_catalog.aliases import AliasCandidate
from tff_catalog.fetch import Fetcher
from tff_catalog.records import SourceKey
from tff_catalog.store import Snapshot

if TYPE_CHECKING:
    import httpx

    from tff_catalog.aliases.miners import MineContext

NAME = "fontsource_legacy"
SOURCE = "fontsource"  # the universe collector whose snapshot this miner reads
KNOWN_VERSION = 1  # the fontsource collector's extract format this reads
# The registry snapshot is compared with live npm listings; one refresh cycle of lag at most.
MAX_SNAPSHOT_AGE = timedelta(days=31)

# The fontsource collector's extracts (collectors/universe/fontsource.py, format 1).
# Kept here rather than imported, so a change there cannot break miner discovery;
# the tests check that they still agree.
REGISTRY_EXTRACT = "registry.json"
REPLACEMENTS_EXTRACT = "replacements.json"
API_EXTRACT = "api-missing.json"
FAMILIES_PREFIX = "families/"
FAMILIES_SUFFIX = ".jsonl.gz"
REPLACEMENTS_PATH = "registry/data/replacements.json"  # in the registry repository

FS_NS = "fs-id"
NPM_NS = "npm"
SCOPES = ("@fontsource", "@fontsource-variable")  # ranking.toml [sources.npm_fontsource]
REGISTRY_HOST = "registry.npmjs.org"
CDN_HOST = "cdn.jsdelivr.net"
HOSTS = (REGISTRY_HOST, CDN_HOST)
ORG_URL = "https://registry.npmjs.org/-/org/{org}/package"
METADATA_URL = "https://cdn.jsdelivr.net/npm/{package}/metadata.json"
PINNED_METADATA_URL = "https://cdn.jsdelivr.net/npm/{package}@{version}/metadata.json"
VERSION_HEADER = "x-jsd-version"
GOOGLE_TYPE = "google"  # metadata.json "type" of a Google Fonts family
# metadata.json's font name: "fontName" in the 4.x packages, "family" in the 5.x ones.
NAME_FIELDS = ("fontName", "family")

_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_PACKAGE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*)/([a-z0-9][a-z0-9._~-]*)$")
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]*$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class MinerError(RuntimeError):
    """The inputs are missing or not in the shape this miner reads."""


@dataclass(frozen=True, slots=True)
class Registry:
    """What the miner takes from one ``fontsource`` snapshot."""

    replacements: Mapping[str, str]  # old id -> new id, as the registry gives them
    current: frozenset[str]  # every id of the registry groups and the API-only rows
    evidence: str  # URL of replacements.json at the registry commit


def _is_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def split_package(package: str) -> tuple[str, str] | None:
    """``("@fontsource", "inter")`` for ``"@fontsource/inter"``; None for other names."""
    m = _PACKAGE.match(package)
    return (m.group(1), m.group(2)) if m else None


def replacements_url(meta: Mapping[str, Any]) -> str:
    """``replacements.json`` in the registry repository at the commit of ``registry.json``."""
    repo, commit, ref = meta.get("repository"), meta.get("commit"), meta.get("ref")
    if not isinstance(repo, str) or not repo.startswith("https://"):
        raise MinerError(f"{REGISTRY_EXTRACT}: no https repository ({repo!r})")
    pin = commit if isinstance(commit, str) and _COMMIT.match(commit) else ref
    if not isinstance(pin, str) or not pin:
        raise MinerError(f"{REGISTRY_EXTRACT}: neither a commit nor a ref")
    return f"{repo.removesuffix('.git').rstrip('/')}/blob/{pin}/{REPLACEMENTS_PATH}"


def _clean_replacements(raw: object, log: logging.Logger) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise MinerError(f"{REPLACEMENTS_EXTRACT} is not an object")
    out = {}
    for old, new in sorted(raw.items()):
        if _is_id(old) and _is_id(new) and old != new:
            out[old] = new
        else:
            log.warning("%s: skipped entry %r -> %r", REPLACEMENTS_EXTRACT, old, new)
    return out


def _current_ids(snap: Snapshot) -> frozenset[str]:
    ids: set[str] = set()
    for entry in snap.manifest.extracts:
        if entry.path.startswith(FAMILIES_PREFIX) and entry.path.endswith(FAMILIES_SUFFIX):
            ids.update(
                row.get("id") for row in snap.iter_jsonl(entry.path) if isinstance(row, dict)
            )
    if snap.has(API_EXTRACT):
        rows = snap.load_json(API_EXTRACT)
        if isinstance(rows, list):
            ids.update(row.get("id") for row in rows if isinstance(row, dict))
    return frozenset(i for i in ids if _is_id(i))


def read_registry(snap: Snapshot, log: logging.Logger) -> Registry:
    """The replacements, current ids and evidence URL of a ``fontsource`` snapshot."""
    where = f"{snap.source} {snap.date}"
    if snap.manifest.collector_version > KNOWN_VERSION:
        raise MinerError(
            f"{where}: extract format {snap.manifest.collector_version} is newer than "
            f"{KNOWN_VERSION}, the one this miner reads"
        )
    for name in (REGISTRY_EXTRACT, REPLACEMENTS_EXTRACT):
        if not snap.has(name):
            raise MinerError(f"{where}: no {name}")
    current = _current_ids(snap)
    if not current:
        raise MinerError(f"{where}: no family ids")
    meta = snap.load_json(REGISTRY_EXTRACT)
    if not isinstance(meta, Mapping):
        raise MinerError(f"{where}: {REGISTRY_EXTRACT} is not an object")
    return Registry(
        replacements=_clean_replacements(snap.load_json(REPLACEMENTS_EXTRACT), log),
        current=current,
        evidence=replacements_url(meta),
    )


def final_targets(replacements: Mapping[str, str], log: logging.Logger) -> dict[str, str]:
    """Each old id's last replacement, following chains; ids on a cycle are dropped."""
    out = {}
    for old in sorted(replacements):
        seen, new = {old}, replacements[old]
        while new in replacements and new not in seen:
            seen.add(new)
            new = replacements[new]
        if new in seen:
            log.warning("%s: %s is on a replacement cycle; skipped", REPLACEMENTS_EXTRACT, old)
            continue
        out[old] = new
    return out


def _candidate(alias: SourceKey, target: SourceKey, relation: str, evidence: str) -> AliasCandidate:
    return AliasCandidate(alias, target, relation, "", NAME, evidence, False)


def replacement_candidates(
    reg: Registry, packages: frozenset[str], log: logging.Logger
) -> list[AliasCandidate]:
    """Part 1 of the module docstring: rows from ``replacements.json``."""
    out = []
    for old, new in final_targets(reg.replacements, log).items():
        if new not in reg.current:
            log.warning("%s: %s -> %s names no current id", REPLACEMENTS_EXTRACT, old, new)
        target = SourceKey(FS_NS, new)
        out.append(_candidate(SourceKey(FS_NS, old), target, "rename", reg.evidence))
        out.extend(
            _candidate(SourceKey(NPM_NS, f"{scope}/{old}"), target, "package", reg.evidence)
            for scope in SCOPES
            if f"{scope}/{old}" in packages
        )
    return out


def retired_packages(packages: Iterable[str], reg: Registry) -> list[str]:
    """Listed packages whose id is neither current nor a replacements.json key, sorted."""
    out = []
    for package in packages:
        parts = split_package(package)
        if parts is None or parts[0] not in SCOPES:
            continue
        pid = parts[1]
        if pid not in reg.current and pid not in reg.replacements:
            out.append(package)
    return sorted(out)


def package_candidates(
    package: str, metadata: Mapping[str, Any], evidence: str
) -> list[AliasCandidate]:
    """Part 2 of the module docstring: the rows of one retired package, [] without a name."""
    parts = split_package(package)
    name = next(
        (v for f in NAME_FIELDS if isinstance(v := metadata.get(f), str) and v.strip()), None
    )
    if parts is None or name is None:
        return []
    ns = "gf-family" if metadata.get("type") == GOOGLE_TYPE else "font-name"
    target = SourceKey(ns, " ".join(name.split()))
    return [
        _candidate(SourceKey(NPM_NS, package), target, "package", evidence),
        _candidate(SourceKey(FS_NS, parts[1]), target, "rename", evidence),
    ]


def fetch_packages(fetcher: Fetcher, scopes: Iterable[str] = SCOPES) -> frozenset[str]:
    """Every package the npm organisations of ``scopes`` list."""
    out: set[str] = set()
    for scope in scopes:
        url = ORG_URL.format(org=scope.removeprefix("@"))
        listing = fetcher.get(url).json()
        if not isinstance(listing, dict) or not listing:
            raise MinerError(f"{url}: expected a non-empty object of packages")
        out.update(p for p in listing if (parts := split_package(p)) and parts[0] == scope)
    return frozenset(out)


def fetch_metadata(
    fetcher: Fetcher, package: str, log: logging.Logger
) -> tuple[Mapping[str, Any] | None, str]:
    """A package's ``metadata.json`` (None when missing or unreadable) and its evidence URL."""
    url = METADATA_URL.format(package=package)
    result = fetcher.get(url, expect=(200, 404))
    version = result.header(VERSION_HEADER)
    pinned = (
        PINNED_METADATA_URL.format(package=package, version=version)
        if version and _VERSION.match(version)
        else url
    )
    if result.status == 404:
        log.warning("%s: no metadata.json; no row", package)
        return None, pinned
    try:
        doc = json.loads(result.body())
    except json.JSONDecodeError, UnicodeDecodeError:
        log.warning("%s: metadata.json is not JSON; no row", package)
        return None, pinned
    if not isinstance(doc, dict):
        log.warning("%s: metadata.json is not an object; no row", package)
        return None, pinned
    return doc, pinned


def latest_snapshot(ctx: MineContext) -> Snapshot:
    """The newest complete ``fontsource`` snapshot on or before the run date,
    at most ``MAX_SNAPSHOT_AGE`` old."""
    if ctx.store is None:
        raise MinerError(f"no snapshot store: {NAME} reads the {SOURCE} collector's snapshot")
    snap = ctx.store.latest(SOURCE, ctx.run_date)
    fetch_it = f"run `tff-catalog fetch --only {SOURCE}` first"
    if snap is None:
        raise MinerError(f"no complete {SOURCE} snapshot on or before {ctx.run_date}: {fetch_it}")
    if ctx.run_date - snap.date > MAX_SNAPSHOT_AGE:
        raise MinerError(
            f"the newest {SOURCE} snapshot ({snap.date}) is more than "
            f"{MAX_SNAPSHOT_AGE.days} days older than the run ({ctx.run_date}), so npm's "
            f"live listing would count every font added since as retired: {fetch_it}"
        )
    return snap


@dataclass(frozen=True, slots=True)
class FontsourceLegacy:
    """The miner. ``transport`` replaces the network in tests (an ``httpx.MockTransport``)."""

    name: ClassVar[str] = NAME
    transport: httpx.BaseTransport | None = None

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        """Propose the rows of the module docstring, sorted and without duplicates."""
        snap = latest_snapshot(ctx)
        reg = read_registry(snap, ctx.log)
        with Fetcher(hosts=HOSTS, transport=self.transport, log=ctx.log) as fetcher:
            packages = fetch_packages(fetcher)
            found = replacement_candidates(reg, packages, ctx.log)
            retired = retired_packages(packages, reg)
            named = 0
            for package in retired:
                metadata, evidence = fetch_metadata(fetcher, package, ctx.log)
                if metadata is None:
                    continue
                rows = package_candidates(package, metadata, evidence)
                if not rows:
                    ctx.log.warning("%s: metadata.json names no font; no row", package)
                named += bool(rows)
                found.extend(rows)
        out = sorted(set(found))
        ctx.log.info(
            "%s: %s %s, %d replacements, %d packages listed, %d retired (%d named); %d candidates",
            NAME,
            SOURCE,
            snap.date,
            len(reg.replacements),
            len(packages),
            len(retired),
            named,
            len(out),
        )
        return out


MINER = FontsourceLegacy()
