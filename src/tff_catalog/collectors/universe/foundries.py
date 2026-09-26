"""Universe collector "foundries": the hand list of foundry families (design-m1 §2.4).

**Source.** ``config/foundries.toml`` in this repository: the families of
foundries outside the big directories (League of Moveable Type, Velvetyne,
Collletttivo, Open Foundry, Roundo on Fontshare). Ruling M12 (a): Claude seeds
it once from the foundry sites and the owner reviews it with gate C; the sites
are never scraped. Being on the list only adds a candidate to the universe; it
is never popularity.

**Fetch.** The list (``Settings.list_file``, from the repository root, which is
``ctx.paths.root`` when the fetch context carries paths) is read
strictly into ``config_model.FoundriesConfig``, checked as ``tff-catalog
config`` checks it (and a blank family name fails), and kept as the extract
``foundries.json``, so a replay sees the list as it stood on the run date. The
extract has its own format (``FoundryList``, format 1 of ``version``), so old
snapshots stay readable when the config model changes. An empty list fails the
fetch, so the stale policy keeps the last good snapshot. Then every family
``url`` and ``repository`` (not the foundry home pages) is checked: a HEAD,
and one GET when the HEAD is not answered 2xx, because some servers refuse
HEAD. One worker per host, each at the fetcher's per-host pace. The answers go to
``checks.jsonl``, one row ``{url, status, final_url, error}`` per URL, sorted
by URL (the rows the links stage writes). A URL on a host outside ``hosts`` is
never requested; its row says so. Every URL not answered 2xx, every family
name that several foundries list, and every name one foundry lists twice, is a
manifest note for the owner.

**Parse** (offline, pure). One family key per family name: a name that
several foundries list is one key, as the universe would fold it anyway.

- A ``UniverseRecord`` keyed ``foundry-family:<name>`` (the name as written:
  alias rules read this namespace as family names), status ``live``, with the
  family page as its ``homepage`` URL and the ``repository`` URL. The page is
  left out when it is the repository itself, as the list does for Velvetyne
  and Collletttivo. A URL whose check answered one of ``gone_statuses`` is
  left out and counted in the ``gone_urls`` attr; the family stays live,
  because a site move is no withdrawal and the owner reviews the notes.
  ``foundry`` lists the foundry ids.
- A ``LicenseFact`` per distinct license string of the family, ``raw``
  exactly as the list gives it: the foundry's own wording, "NOASSERTION" when
  it states none (an empty string counts as that). L1
  (``config/license-aliases.toml``) normalises it.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlsplit

from tff_catalog import jsonio
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config import check_foundries
from tff_catalog.config_model import (
    SCHEMA_VERSION,
    ConfigError,
    FoundriesConfig,
    from_mapping,
    load_toml,
)
from tff_catalog.fetch import Fetcher, FetchError, FetchResult, HostNotAllowed
from tff_catalog.paths import find_root
from tff_catalog.records import LicenseFact, Record, SourceKey, UniverseRecord, attrs
from tff_catalog.store import Snapshot

NAME = "foundries"
LIST_FILE = "config/foundries.toml"
LIST_EXTRACT = "foundries.json"
CHECKS_EXTRACT = "checks.jsonl"
NAMESPACE = "foundry-family"
NOASSERTION = "NOASSERTION"  # the list's word for "the foundry states no license"
GONE_STATUSES = (404, 410)
MAX_WORKERS = 8  # hosts checked at once
ANY_STATUS = range(100, 600)  # the GET fallback keeps whatever answer it gets
NOT_CHECKED_HOST = "not checked: the host is not in the collector's hosts"
NOT_CHECKED_OFF = "not checked: check_urls is false"

# The hosts of the family pages and repositories in config/foundries.toml. A URL
# elsewhere stays unchecked (a manifest note) until its host is added here.
# velvetyne.fr and collletttivo.it are left out on purpose: their robots.txt
# shuts out AI agents, and the list links those foundries' repositories instead.
HOSTS = (
    "github.com",
    "gitlab.com",
    "open-foundry.com",
    "www.fontshare.com",
    "www.theleagueofmoveabletype.com",
)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/foundries.toml``."""

    list_file: str = LIST_FILE  # from the repository root; an absolute path is used as is
    check_urls: bool = True  # HEAD every family page and repository
    gone_statuses: tuple[int, ...] = GONE_STATUSES  # a URL answering one is left out of records

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        path = PurePosixPath(self.list_file)
        if path.suffix != ".toml" or ".." in path.parts:
            raise ConfigError(
                f"{where}.list_file: must name a .toml file without '..', got {self.list_file!r}"
            )
        for status in self.gone_statuses:
            if not 400 <= status <= 599:
                raise ConfigError(f"{where}.gone_statuses: {status} is not an HTTP error status")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- the list ---------------------------------------------------------------------------------


def list_path(settings: Settings, root: Path | None = None) -> Path:
    """The hand list: ``list_file`` under ``root`` (default: ``paths.find_root()``)."""
    path = Path(settings.list_file)
    return path if path.is_absolute() else (root or find_root()) / path


@dataclass(frozen=True, slots=True)
class ListedFamily:
    """A family of the ``foundries.json`` extract (format 1)."""

    name: str  # as written in the list
    url: str
    license: str  # as written; "" or "NOASSERTION" when the foundry states none
    repository: str = ""


@dataclass(frozen=True, slots=True)
class ListedFoundry:
    """A foundry of the ``foundries.json`` extract (format 1)."""

    name: str
    url: str
    families: tuple[ListedFamily, ...]


@dataclass(frozen=True, slots=True)
class FoundryList:
    """The ``foundries.json`` extract, format 1: the list as ``fetch()`` loaded it.

    Its own dataclasses rather than ``config_model.FoundriesConfig``, so a
    snapshot stays readable (strictly) when the config file's model changes;
    a new extract format bumps the collector's ``version``.
    """

    schema: int
    foundries: dict[str, ListedFoundry]  # foundry id -> entry


def load_list(path: Path) -> FoundriesConfig:
    """The hand list, loaded strictly and checked like ``load_config`` does (``ConfigError``).

    A family whose name is blank fails too: parse could not key it.
    """
    cfg = from_mapping(FoundriesConfig, load_toml(path), where=path.name)
    if cfg.schema != SCHEMA_VERSION:
        raise ConfigError(f"{path.name}: schema {cfg.schema}, expected {SCHEMA_VERSION}")
    check_foundries(cfg)
    for foundry_id, foundry in cfg.foundries.items():
        for i, fam in enumerate(foundry.families):
            if not _name(fam.name):
                raise ConfigError(f"{path.name}: foundries.{foundry_id}.families[{i}]: blank name")
    return cfg


def list_doc(cfg: FoundriesConfig) -> dict[str, Any]:
    """The extract ``foundries.json`` (format 1, ``FoundryList``) of a loaded list."""
    return {
        "schema": cfg.schema,
        "foundries": {
            foundry_id: {
                "name": foundry.name,
                "url": foundry.url,
                "families": [
                    {
                        "name": fam.name,
                        "url": fam.url,
                        "license": fam.license,
                        "repository": fam.repository,
                    }
                    for fam in foundry.families
                ],
            }
            for foundry_id, foundry in sorted(cfg.foundries.items())
        },
    }


def read_list(doc: object, where: str = LIST_EXTRACT) -> FoundryList:
    """``FoundryList`` from a ``foundries.json`` document, strictly (``ConfigError``)."""
    if not isinstance(doc, Mapping):
        raise ConfigError(f"{where}: expected a JSON object, got {type(doc).__name__}")
    return from_mapping(FoundryList, doc, where=where)


def family_count(lst: FoundryList) -> int:
    return sum(len(foundry.families) for foundry in lst.foundries.values())


def url_roles(fam: ListedFamily) -> list[tuple[str, str]]:
    """``(role, url)`` of one list entry: the page (unless it is the repository), the repository."""
    out = []
    if fam.url and fam.url != fam.repository:
        out.append(("homepage", fam.url))
    if fam.repository:
        out.append(("repository", fam.repository))
    return out


def family_urls(lst: FoundryList) -> list[str]:
    """Every family page and repository URL of the list, sorted, each once."""
    found = {
        url
        for foundry in lst.foundries.values()
        for fam in foundry.families
        for _, url in url_roles(fam)
    }
    return sorted(found)


def _name(text: str) -> str:
    return " ".join(text.split())


def by_name(lst: FoundryList) -> dict[str, list[tuple[str, ListedFamily]]]:
    """List entries grouped by family name (whitespace collapsed), with their foundry ids."""
    out: dict[str, list[tuple[str, ListedFamily]]] = defaultdict(list)
    for foundry_id, foundry in sorted(lst.foundries.items()):
        for fam in foundry.families:
            if name := _name(fam.name):
                out[name].append((foundry_id, fam))
    return dict(sorted(out.items()))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# --- fetch: the URL checks --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    """The answer to one URL: the last response's status and final URL, or why there is none."""

    url: str
    status: int | None = None
    final_url: str | None = None
    error: str | None = None
    results: tuple[FetchResult, ...] = ()  # every response, for the manifest

    def row(self) -> dict[str, Any]:
        """The ``checks.jsonl`` row."""
        return {
            "url": self.url,
            "status": self.status,
            "final_url": self.final_url,
            "error": self.error,
        }


def _ok(status: int | None) -> bool:
    return status is not None and 200 <= status < 300


def check_url(fetcher: Fetcher, url: str) -> Check:
    """HEAD ``url``, then one GET when the HEAD is not answered 2xx; never raises for a bad URL.

    A request that got no answer after the fetcher's retries (``FetchError``),
    or was redirected to a host outside the fetcher's scope (``HostNotAllowed``),
    gives a row with ``error`` set.
    """
    results: list[FetchResult] = []
    error = None
    try:
        results.append(fetcher.head(url))
        if not _ok(results[-1].status):
            results.append(fetcher.get(url, expect=ANY_STATUS))
    except (FetchError, HostNotAllowed) as exc:
        error = f"{type(exc).__name__}: {exc}"
    if not results:
        return Check(url, error=error)
    last = results[-1]
    return Check(url, last.status, last.final_url, error, tuple(results))


def check_all(fetcher: Fetcher, urls: Iterable[str], hosts: Iterable[str]) -> list[Check]:
    """Check each URL whose host is in ``hosts``, one worker per host; sorted by URL."""
    allowed = frozenset(hosts)
    groups: dict[str, list[str]] = defaultdict(list)
    done: list[Check] = []
    for url in sorted(set(urls)):
        host = host_of(url)
        if host in allowed:
            groups[host].append(url)
        else:
            done.append(Check(url, error=NOT_CHECKED_HOST))

    def run(group: list[str]) -> list[Check]:
        return [check_url(fetcher, url) for url in group]

    if groups:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(groups))) as pool:
            for checks in pool.map(run, [groups[h] for h in sorted(groups)]):
                done.extend(checks)
    return sorted(done, key=lambda c: c.url)


def list_notes(lst: FoundryList) -> list[str]:
    """Names several foundries list, and names one foundry lists more than once, by name."""
    out = []
    for name, listed in by_name(lst).items():
        count = Counter(fid for fid, _ in listed)
        if len(count) > 1:
            out.append(f"listed by several foundries: {name} ({', '.join(sorted(count))})")
        if twice := sorted(fid for fid, n in count.items() if n > 1):
            out.append(f"listed more than once by one foundry: {name} ({', '.join(twice)})")
    return out


def notes(lst: FoundryList, checks: Iterable[Check], settings: Settings) -> list[str]:
    """Manifest notes for the owner: ``list_notes``, then every URL not answered 2xx."""
    out = list_notes(lst)
    if not settings.check_urls:
        return [*out, "URL checks are off (check_urls = false)"]
    for c in checks:
        if c.error == NOT_CHECKED_HOST:
            out.append(f"not checked (host not in hosts): {c.url}")
        elif c.status is None:
            out.append(f"no answer: {c.url} ({c.error})")
        elif c.status in settings.gone_statuses:
            out.append(f"gone (HTTP {c.status}): {c.url}")
        elif not _ok(c.status):
            out.append(f"HTTP {c.status}: {c.url}")
    return out


# --- parse: records ---------------------------------------------------------------------------


def read_checks(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    """``checks.jsonl`` rows by URL (none when the extract is absent)."""
    if not snapshot.has(CHECKS_EXTRACT):
        return {}
    return {
        row["url"]: row
        for row in snapshot.iter_jsonl(CHECKS_EXTRACT)
        if isinstance(row, dict) and isinstance(row.get("url"), str)
    }


def gone_urls(checks: Mapping[str, Mapping[str, Any]], statuses: Iterable[int]) -> frozenset[str]:
    """The URLs whose check answered one of ``statuses``."""
    wanted = frozenset(statuses)
    return frozenset(
        url
        for url, row in checks.items()
        if type(row.get("status")) is int and row["status"] in wanted
    )


def family_records(
    name: str, listed: list[tuple[str, ListedFamily]], gone: frozenset[str]
) -> list[Record]:
    """The universe record and license facts of one family name (see the module docstring)."""
    urls: set[tuple[str, str]] = set()
    lost: set[str] = set()
    for _, fam in listed:
        for role, url in url_roles(fam):
            if url in gone:
                lost.add(url)
            else:
                urls.add((role, url))
    key = SourceKey(NAMESPACE, name)
    found = {"foundry": ",".join(sorted({fid for fid, _ in listed}))}
    extra = {"gone_urls": len(lost)} if lost else {}
    out: list[Record] = [
        UniverseRecord(
            source=NAME,
            key=key,
            family=name,
            urls=tuple(sorted(urls)),
            attrs=attrs(**found, **extra),
        )
    ]
    stated: dict[str, set[str]] = defaultdict(set)
    for foundry_id, fam in listed:
        stated[fam.license.strip() or NOASSERTION].add(foundry_id)
    for raw, foundry_ids in sorted(stated.items()):
        out.append(
            LicenseFact(
                source=NAME,
                key=key,
                raw=raw,
                attrs=attrs(foundry=",".join(sorted(foundry_ids))),
            )
        )
    return out


class Foundries(CollectorBase):
    """``config/foundries.toml``: hand-listed foundry families, their URLs checked."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "universe"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = HOSTS
    emits: ClassVar[tuple[type, ...]] = (UniverseRecord, LicenseFact)
    group: ClassVar[str | None] = None
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Snapshot the list as ``foundries.json``; check its URLs into ``checks.jsonl``."""
        settings = _settings(ctx.settings)
        path = list_path(settings, ctx.paths.root if ctx.paths is not None else None)
        doc = list_doc(load_list(path))
        lst = read_list(doc)  # what parse will read back
        families = family_count(lst)
        if not families:
            raise ValueError(f"{path}: lists no family; is this the right file?")
        ctx.out.write_bytes(LIST_EXTRACT, jsonio.pretty_bytes(doc), rows=families)
        urls = family_urls(lst)
        if settings.check_urls:
            hosts = ctx.fetcher.hosts if ctx.fetcher.hosts is not None else self.hosts
            checks = check_all(ctx.fetcher, urls, hosts)
        else:
            checks = [Check(url, error=NOT_CHECKED_OFF) for url in urls]
        for check in checks:
            for result in check.results:
                for record in result.to_records():
                    ctx.out.record_fetch(record)
        ctx.out.write_jsonl(CHECKS_EXTRACT, (c.row() for c in checks))
        for note in notes(lst, checks, settings):
            ctx.log.warning("%s: %s", self.name, note)
            ctx.out.note(note)
        ctx.out.set_data_date(ctx.run_date)
        answered = sum(_ok(c.status) for c in checks)
        ctx.log.info(
            "%s: %d families from %d foundries; %d URLs, %d answered 2xx, %d requests",
            self.name,
            families,
            len(lst.foundries),
            len(checks),
            answered,
            sum(len(c.results) for c in checks),
        )

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """A universe record and its license facts per family name of the snapshotted list."""
        settings = _settings(ctx.settings)
        where = f"{ctx.snapshot.source} {ctx.snapshot.date} {LIST_EXTRACT}"
        lst = read_list(ctx.snapshot.load_json(LIST_EXTRACT), where=where)
        gone = gone_urls(read_checks(ctx.snapshot), settings.gone_statuses)
        for name, listed in by_name(lst).items():
            yield from family_records(name, listed, gone)


COLLECTOR = Foundries()
