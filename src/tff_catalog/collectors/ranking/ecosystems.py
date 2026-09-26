"""Ranking collector "ecosystems": dependent repositories per npm package (design-m1 §2.4).

**Source.** ``packages.ecosyste.ms`` bulk lookup (``POST /api/v1/packages/bulk_lookup``
with ``{"purls": [...]}``, at most ``MAX_PURLS`` a call; anonymous tier,
5,000 requests an hour). For each package it knows it answers one object whose
``dependent_repos_count`` counts the repositories that declare the package, so
CI re-downloads can't inflate it. Unknown packages are left out of the answer,
and the answer comes back in its own order, so results are keyed by purl. The
``downloads`` field (npm's last month) is ignored. The data is CC BY-SA 4.0,
credited to "ecosyste.ms" (ruling T1; docs/sources.md).

**Packages.** Every package the npm registry lists for each scope in
``Settings.scopes`` (``registry.npmjs.org/-/org/<scope>/package``, one request
per scope). Ruling M7 (gate M7 (rec)): ``@fontsource`` and
``@fontsource-variable`` only, never Expo's packages. Names outside the scope,
or not valid npm names, are dropped with a manifest note.

**Fetch.** The purls (``pkg:npm/%40scope/name``), sorted, in calls of
``batch_size``; each raw answer goes to ``ctx.raw`` (about 1 MB a call) and
only the three fields below are kept. An answer counts only for the purls of
its own call; any other package in it is ignored with a manifest note, and a
package answered twice keeps its later sync. The counts are current values, so
nothing is carried over from ``ctx.previous``, which serves only as a sanity
check: a run with fewer than ``min_share`` of the previous snapshot's packages,
or of its packages with a count, fails (a broken answer), as does a run where
ecosyste.ms knows none of the packages; the stale policy then reuses the last
good snapshot. The manifest's ``data_date`` is the run date.

**Extract.** ``dependents.csv``: header ``package,dependent_repos_count,last_synced_at``,
one row per listed package, sorted by package. Both values are empty for a
package ecosyste.ms does not know; ``last_synced_at`` is kept as the API wrote
it (an ISO timestamp).

**Parse** (offline, pure). One ``Observation`` per row: key ``npm:<package>``,
series ``dependent_repos``, unit ``dependents``, start and end the snapshot's
data date (a count as of that day, ranking.toml ``counting = "current"``), and
attr ``last_synced_at`` (records.RESERVED_ATTRS: the UTC day of the sync), which
the correction stage compares with ``stale_sync_days``. A package without a
count is still emitted, with value None: it is inside the source's frame but
has no usable value (censored, methodology §3).
"""

import csv
import io
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import ClassVar
from urllib.parse import quote, unquote

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchResult
from tff_catalog.records import Observation, Record, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "ecosystems"
API_HOST = "packages.ecosyste.ms"
NPM_HOST = "registry.npmjs.org"
BULK_URL = f"https://{API_HOST}/api/v1/packages/bulk_lookup"
MAX_PURLS = 100  # the API refuses more purls in one call (ruling M7)
NAMESPACE = "npm"
SERIES = "dependent_repos"  # ranking.toml [sources.ecosystems] series
UNIT = "dependents"
EXTRACT = "dependents.csv"
HEADER = ("package", "dependent_repos_count", "last_synced_at")
PURL_PREFIX = "pkg:npm/"

# Defaults of the Settings, which config/sources/ecosystems.toml spells out.
SCOPES = ("@fontsource", "@fontsource-variable")  # gate M7 (rec); (b) adds "@expo-google-fonts"
BATCH_SIZE = MAX_PURLS
MIN_SHARE = 0.5

_SCOPE_RE = re.compile(r"^@[a-z0-9~-][a-z0-9._~-]*$")
# validate-npm-package-name's rule for new scoped names.
_PACKAGE_RE = re.compile(r"^@[a-z0-9~-][a-z0-9._~-]*/[a-z0-9~-][a-z0-9._~-]*$")


# --- settings ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/ecosystems.toml``."""

    scopes: tuple[str, ...] = SCOPES  # npm scopes whose packages are looked up
    batch_size: int = BATCH_SIZE  # purls per bulk_lookup call, 1 to 100
    min_share: float = MIN_SHARE  # of the previous snapshot's packages and counts, or fail

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not self.scopes:
            raise ConfigError(f"{where}.scopes: name at least one npm scope")
        for scope in self.scopes:
            if not _SCOPE_RE.fullmatch(scope):
                raise ConfigError(
                    f"{where}.scopes: {scope!r} is not an npm scope like '@fontsource'"
                )
        if len(set(self.scopes)) != len(self.scopes):
            raise ConfigError(f"{where}.scopes: a scope is listed twice")
        if not 1 <= self.batch_size <= MAX_PURLS:
            raise ConfigError(f"{where}.batch_size: must be between 1 and {MAX_PURLS}")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


# --- package names and purls -------------------------------------------------------------------


def is_package(name: str) -> bool:
    """Whether ``name`` is a scoped npm package name (``@scope/name``)."""
    return bool(_PACKAGE_RE.fullmatch(name))


def org_url(scope: str) -> str:
    """The npm registry's package list of the organisation behind ``scope``."""
    return f"https://{NPM_HOST}/-/org/{scope.removeprefix('@')}/package"


def to_purl(package: str) -> str:
    """``@fontsource/inter`` -> ``pkg:npm/%40fontsource/inter``, the form ecosyste.ms answers with."""
    return PURL_PREFIX + quote(package, safe="/")


def purl_package(purl: str) -> str | None:
    """The npm package name of an npm purl, without version, qualifiers or subpath; else None.

    Accepts the scope's ``@`` encoded (``%40``, the purl spec) or not.
    """
    if not purl.startswith(PURL_PREFIX):
        return None
    rest = purl.removeprefix(PURL_PREFIX).split("#", 1)[0].split("?", 1)[0]
    at = rest.find("@", 1)  # a literal @ after the start separates the version
    if at != -1:
        rest = rest[:at]
    name = unquote(rest)
    return name or None


def read_org_listing(doc: object, scope: str) -> tuple[list[str], list[str]]:
    """``(packages in scope, sorted; other names, sorted)`` from an org listing.

    The listing is ``{"<package>": "<access>"}``; ``ValueError`` for any other shape.
    """
    if not isinstance(doc, Mapping) or not all(isinstance(k, str) for k in doc):
        raise ValueError(f"{org_url(scope)}: expected an object of package names")
    prefix = f"{scope}/"
    kept = sorted(n for n in doc if n.startswith(prefix) and is_package(n))
    dropped = sorted(set(doc) - set(kept))
    return kept, dropped


def batches[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """``items`` in consecutive slices of at most ``size``."""
    if size < 1:
        raise ValueError(f"batch size must be positive, got {size}")
    for start in range(0, len(items), size):
        yield items[start : start + size]


# --- the bulk answer ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Row:
    """One package: its dependent repositories and when ecosyste.ms last synced it."""

    package: str
    count: int | None = None
    synced: str | None = None  # ISO timestamp as the API wrote it


_NEVER = datetime.min.replace(tzinfo=UTC)


def synced_moment(text: str) -> datetime:
    """An ISO timestamp as an aware UTC datetime; one without an offset is taken as UTC.

    ``ValueError`` if unreadable.
    """
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def synced_day(text: str) -> date:
    """The UTC day of an ISO timestamp (``2026-07-24T21:17:33.931Z`` -> 2026-07-24).

    A timestamp without an offset is taken as UTC. ``ValueError`` if unreadable.
    """
    return synced_moment(text).date()


def _count(value: object, where: str) -> int | None:
    if value is None:
        return None
    # type() rather than isinstance: true must not pass as a count.
    if type(value) is not int or value < 0:
        raise ValueError(f"{where}: dependent_repos_count is {value!r}, not a count")
    return value


def _synced(value: object, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{where}: last_synced_at is {value!r}, not a timestamp")
    try:
        synced_day(value)
    except ValueError as exc:
        raise ValueError(f"{where}: last_synced_at {value!r}: {exc}") from None
    return value


def _later(a: Row, b: Row) -> Row:
    """Of two answers for one package, the one synced last (then the larger count).

    The order is total on rows, so the choice never depends on the answer's order.
    """

    def rank(r: Row) -> tuple[datetime, int, str]:
        moment = synced_moment(r.synced) if r.synced else _NEVER
        return (moment, -1 if r.count is None else r.count, r.synced or "")

    return b if rank(b) > rank(a) else a


def read_bulk(doc: object, wanted: Iterable[str]) -> tuple[dict[str, Row], list[str]]:
    """``(package -> Row for the wanted packages it answers, other names it answered)``.

    A package is named by its ``purl`` (by ``name`` only when it has no purl); a
    purl that is not npm's is never wanted. ``ValueError`` when the answer is not
    a list of objects or a kept field has the wrong type.
    """
    if not isinstance(doc, list):
        raise ValueError(f"{BULK_URL}: expected a list of packages")
    wanted = set(wanted)
    found: dict[str, Row] = {}
    unexpected: set[str] = set()
    for i, item in enumerate(doc):
        where = f"bulk_lookup item {i}"
        if not isinstance(item, Mapping):
            raise ValueError(f"{where}: not an object")
        purl, name = item.get("purl"), item.get("name")
        if isinstance(purl, str):
            package = purl_package(purl) or purl
        elif isinstance(name, str):
            package = name
        else:
            raise ValueError(f"{where}: no purl or name")
        if package not in wanted:
            unexpected.add(package)
            continue
        where = f"{where} ({package})"
        row = Row(
            package,
            _count(item.get("dependent_repos_count"), where),
            _synced(item.get("last_synced_at"), where),
        )
        found[package] = _later(found[package], row) if package in found else row
    return found, sorted(unexpected)


# --- the extract -------------------------------------------------------------------------------


def dependents_csv(rows: Iterable[Row]) -> bytes:
    """The extract: a header, then one row per package, sorted by package."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(HEADER)
    for r in sorted(rows):
        writer.writerow((r.package, "" if r.count is None else r.count, r.synced or ""))
    return buffer.getvalue().encode("utf-8")


def read_dependents_csv(data: bytes, what: str = EXTRACT) -> list[Row]:
    """The rows of the extract, in file order; ``ValueError`` if it is malformed."""
    lines = csv.reader(io.StringIO(data.decode("utf-8"), newline=""))
    header = next(lines, None)
    if tuple(header or ()) != HEADER:
        raise ValueError(f"{what}: header is {header!r}, expected {','.join(HEADER)}")
    rows: list[Row] = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=2):
        where = f"{what} line {number}"
        if len(line) != len(HEADER):
            raise ValueError(f"{where}: expected {len(HEADER)} fields, got {line!r}")
        package, count, synced = line
        if not is_package(package) or package in seen:
            raise ValueError(f"{where}: bad or repeated package name {package!r}")
        seen.add(package)
        if count and not (count.isascii() and count.isdigit()):
            raise ValueError(f"{where}: not a count: {count!r}")
        rows.append(Row(package, int(count) if count else None, _synced(synced or None, where)))
    return rows


# --- the collector -----------------------------------------------------------------------------


def _record(ctx: FetchContext, result: FetchResult) -> None:
    for record in result.to_records(kept=False):
        ctx.out.record_fetch(record)


def _previous_rows(previous: Snapshot | None, version: int) -> list[Row] | None:
    """The previous snapshot's rows, when it has this version's extract."""
    if previous is None or previous.manifest.collector_version != version:
        return None
    if not previous.has(EXTRACT):
        return None
    return read_dependents_csv(previous.read_bytes(EXTRACT), f"{previous.date} {EXTRACT}")


def check_against(rows: Sequence[Row], before: Sequence[Row] | None, share: float) -> None:
    """Raise ``ValueError`` when ecosyste.ms knows none of the packages, or when the
    packages, or those with a count, fall below ``share`` of the previous snapshot's."""
    counted = sum(r.count is not None for r in rows)
    if rows and counted == 0:
        raise ValueError(f"ecosyste.ms gave a count for none of the {len(rows)} packages")
    if before is None:
        return
    then_counted = sum(r.count is not None for r in before)
    for what, now, then in (
        ("packages", len(rows), len(before)),
        ("counts", counted, then_counted),
    ):
        if now < share * then:
            raise ValueError(
                f"{now} {what}, down from {then} in the previous snapshot "
                f"(below min_share = {share}); a broken answer?"
            )


class Ecosystems(CollectorBase):
    """ecosyste.ms: repositories that declare each Fontsource npm package."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (API_HOST, NPM_HOST)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "npm_registry"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """List the scopes' packages on npm, then look them all up (module docstring)."""
        settings = _settings(ctx.settings)
        packages = self._list(ctx, settings.scopes)
        found: dict[str, Row] = {}
        unexpected: set[str] = set()
        calls = 0
        for calls, chunk in enumerate(batches(packages, settings.batch_size), start=1):
            result = ctx.fetcher.post_json(BULK_URL, {"purls": [to_purl(p) for p in chunk]})
            _record(ctx, result)
            ctx.raw.file(f"bulk-{calls:04d}.json").write_bytes(result.body())
            got, others = read_bulk(result.json(), chunk)
            for package, row in got.items():
                found[package] = _later(found[package], row) if package in found else row
            unexpected.update(others)
        rows = [found.get(p, Row(p)) for p in packages]
        check_against(rows, _previous_rows(ctx.previous, self.version), settings.min_share)
        ctx.out.write_bytes(EXTRACT, dependents_csv(rows), rows=len(rows))
        ctx.out.set_data_date(ctx.run_date)
        counted = sum(r.count is not None for r in rows)
        ctx.out.note(
            f"ecosyste.ms counted {counted} of {len(rows)} packages; bulk_lookup calls: {calls}"
        )
        if unexpected:
            ctx.out.note(
                f"answers for packages not asked about, ignored: {', '.join(sorted(unexpected))}"
            )
        days = sorted(synced_day(r.synced) for r in rows if r.synced)
        if days:
            ctx.out.note(f"last_synced_at from {days[0]} to {days[-1]}")
        ctx.log.info(
            "%s: %d packages, %d with a count, %d calls", self.name, len(rows), counted, calls
        )

    def _list(self, ctx: FetchContext, scopes: Sequence[str]) -> list[str]:
        """Every package npm lists in ``scopes``, sorted; ``ValueError`` if there are none."""
        packages: set[str] = set()
        for scope in scopes:
            result = ctx.fetcher.get(org_url(scope))
            _record(ctx, result)
            kept, dropped = read_org_listing(result.json(), scope)
            packages.update(kept)
            ctx.out.note(f"npm lists {len(kept)} packages in {scope}")
            if dropped:
                ctx.out.note(f"{scope}: names outside the scope, dropped: {', '.join(dropped)}")
        if not packages:
            raise ValueError(f"npm lists no packages in {', '.join(scopes)}")
        return sorted(packages)

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One Observation per package (module docstring)."""
        snapshot = ctx.snapshot
        day = snapshot.manifest.data_date or snapshot.date
        for row in read_dependents_csv(snapshot.read_bytes(EXTRACT)):
            extra = attrs(last_synced_at=synced_day(row.synced).isoformat()) if row.synced else ()
            yield Observation(
                source=NAME,
                series=SERIES,
                key=SourceKey(NAMESPACE, row.package),
                value=None if row.count is None else float(row.count),
                unit=UNIT,
                start=day,
                end=day,
                attrs=extra,
            )


COLLECTOR = Ecosystems()
