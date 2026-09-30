"""Ranking collector "github_releases": release downloads of main-channel repos (design-m1 §2.4).

**Source.** GitHub's lifetime ``download_count`` of every release asset in the
repositories ``config/sources/github_releases.toml`` lists: repos whose GitHub
releases are an official download of the font (ruling M2). The counts are open
data (ruling T1). They are lifetime totals, so the engine source ``github``
(``ranking.toml``, ``counting = "snapshot_delta"``) uses the growth between two
snapshots; ``needs_baseline`` makes the parse stage parse the baseline too.

**Fetch.** For each repo, in the order listed:

- ``api = "rest"`` (the default): ``GET /repos/<repo>/releases?per_page=&page=``,
  following pages while GitHub's ``Link`` header names a next one (or, without
  one, while pages come back full), up to ``max_pages``. Pages embed every
  asset, and can be megabytes, so each is streamed to ``ctx.raw`` and read from
  there; only the extract below is kept. A 404 marks the repo ``missing``.
- ``api = "graphql"`` (Iosevka: 400+ releases of about 450 assets each):
  ``repository.releases(first:, orderBy: CREATED_AT DESC)`` with the first
  ``asset_page`` assets of each release, then the remaining asset pages,
  several releases per query (``release(tagName:)`` aliases). A query asks
  for at most ``graphql_nodes`` assets, because GitHub cuts bigger ones short
  (``RESOURCE_LIMITS_EXCEEDED``); on that error it is asked again at half the
  size. Any other GraphQL error fails the fetch, so a partial answer is never kept.
- ``latest = N`` keeps only the newest N releases (Iosevka: 24, ruling M2);
  0 keeps every release, with no cap by date.

Drafts, assets not in state ``uploaded``, assets matching ``skip_assets``
(checksums, signatures, license and text files, sources) and assets outside a
repo's ``assets`` filter are left out, and a release that REST paging returns
twice (one published mid-fetch shifts the pages) is kept once. Every request goes in the
manifest. Every request to ``api.github.com`` is charged to the run's
``github`` budget (``fetch.Budget``). The fetch refuses to start when that
budget, or its own ``max_requests``, cannot cover the requests the previous
snapshot implies (``estimate_requests``, a lower bound; one per repo without
one), and it stops at ``max_requests``, so the other GitHub collectors keep
their share. A repo whose asset count falls under ``min_share`` of the
previous snapshot's (counted under today's filters) is noted (a broken answer,
or deleted releases); a total under that share fails the fetch, so the stale
policy reuses the last good snapshot.

**Extracts** (``version`` 1):

- ``assets.jsonl.gz``: one row per asset, sorted by repo, tag and asset:
  ``{"repo", "tag", "published_at", "prerelease", "asset", "created_at",
  "downloads"}`` (times ISO UTC). No author, uploader or release notes.
- ``repos.json``: per repo ``{"repo", "api", "latest", "status", "releases",
  "assets", "total_releases", "renamed_to"}``, sorted by repo.

The manifest's ``data_date`` is the UTC day of the last request: the counts
are live totals as of then.

**Parse** (offline, pure): one ``Observation`` per kept asset, series
``lifetime``, unit ``downloads``, key ``gh-asset:<repo>/<asset base>``, where
the asset base is the asset name without its version (``asset_base``:
``JetBrainsMono-2.304.zip`` -> ``JetBrainsMono.zip``), so one alias row maps
every release of an asset. ``start`` is the asset's creation day, ``end`` the
data date. Attrs: ``first_seen`` (the asset's creation day, the exposure of
``asset_created``), ``release`` (the tag), ``published_at``, ``prerelease``,
``repo`` and ``asset`` (the full name). ``skip_assets`` and each repo's
``assets`` filter are applied again, so a tightened filter also applies to
baseline snapshots.
"""

import functools
import os
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, ClassVar, Literal

from tff_catalog import clock
from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import TOKEN_ENV, BudgetExceeded, FetchError, FetchResult
from tff_catalog.records import Observation, Record, Scalar, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "github_releases"
API_HOST = "api.github.com"
NAMESPACE = "gh-asset"
SERIES = "lifetime"
UNIT = "downloads"
ASSETS = "assets.jsonl.gz"
REPOS = "repos.json"
ROW_FIELDS = ("asset", "created_at", "downloads", "prerelease", "published_at", "repo", "tag")

# Defaults of the Settings, which config/sources/github_releases.toml spells out.
PER_PAGE = 100  # GitHub's maximum page size
MAX_PAGES = 20  # REST pages per repo (2,000 releases)
ASSET_PAGE = 100  # GraphQL assets per release per page
# Asset nodes one GraphQL query may ask for. GitHub cut queries of 960 short on
# 2026-09-26 (RESOURCE_LIMITS_EXCEEDED, the rest of the answer null); 600 passed.
GRAPHQL_NODES = 500
MAX_ASSET_PAGES = 20  # GraphQL asset pages per release
MAX_REQUESTS = 300  # this collector's share of the run's github budget (fetch.GITHUB_BUDGET)
MIN_SHARE = 0.5  # of the previous snapshot's assets, or the fetch fails
# Release files that are not fonts: checksums, signatures, notes, previews,
# license files, FontForge and editor sources, source archives.
SKIP_ASSETS = (
    r"(?i)(\.(asc|sig|minisig|sha1|sha256|sha512|md5|txt|md|json|ya?ml|pdf|png|jpe?g|gif|svg"
    r"|html?|sfd|vim)$|sha\d*sums?|checksums?|^(licen[cs]e|copying|readme|notice)(\.\w+)?$"
    r"|^sources?\.(zip|7z|tar\.\w+)$)"
)

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LINK_NEXT = re.compile(r'<[^>]*>\s*;\s*rel="next"')
_EXTENSIONS = (
    ".tar.xz",
    ".tar.gz",
    ".tar.bz2",
    ".tar.zst",
    ".zip",
    ".7z",
    ".ttc",
    ".ttf",
    ".otf",
    ".woff2",
    ".woff",
    ".dmg",
    ".pkg",
    ".deb",
    ".rpm",
    ".exe",
    ".msi",
)
_SEPARATORS = "-_. "
# A dotted (or underscored) version with at least two parts, between separators.
_GENERIC_VERSION = re.compile(r"(?:^|[-_. ])[vV]?\d+(?:[._]\d+)+(?=$|[-_. ])")
# A trailing git commit hash (7-12 hex digits, with a digit and a letter): CI builds.
_COMMIT_HASH = re.compile(r"[-_](?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,12}$")
_TAG_VERSION = re.compile(r"\d+(?:[._]\d+)*")
_NOT_FOUND = "Could not resolve to a Repository"
_LIMITS = "Resource limits for this query exceeded"


@dataclass(frozen=True, slots=True)
class Repo:
    """One ``[[repos]]`` table: a repository whose releases are a main download channel."""

    repo: str  # "owner/name", as GitHub spells it; the key prefix
    family: str = ""  # the family it publishes: a hint for the github_repos alias miner
    api: Literal["rest", "graphql"] = "rest"
    latest: int = 0  # 0: every release; N: only the newest N (by creation)
    assets: str = ""  # keep only assets whose name matches (re.search); "" keeps all
    strip: tuple[str, ...] = ()  # extra regexes removed from asset names to make the key


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/github_releases.toml``."""

    per_page: int = PER_PAGE
    max_pages: int = MAX_PAGES
    asset_page: int = ASSET_PAGE
    graphql_nodes: int = GRAPHQL_NODES
    max_asset_pages: int = MAX_ASSET_PAGES
    max_requests: int = MAX_REQUESTS
    min_share: float = MIN_SHARE
    skip_assets: str = SKIP_ASSETS
    repos: tuple[Repo, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        for key, low, high in (
            ("per_page", 1, 100),
            ("asset_page", 1, 100),
            ("graphql_nodes", 1, 5000),
            ("max_pages", 1, 1000),
            ("max_asset_pages", 1, 1000),
            ("max_requests", 1, 5000),
        ):
            value = getattr(self, key)
            if not low <= value <= high:
                raise ConfigError(f"{where}.{key}: must be between {low} and {high}, got {value}")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")
        _check_regex(self.skip_assets, f"{where}.skip_assets")
        seen: set[str] = set()
        for i, repo in enumerate(self.repos):
            at = f"{where}.repos[{i}]"
            if not _REPO_RE.fullmatch(repo.repo):
                raise ConfigError(f"{at}.repo: expected 'owner/name', got {repo.repo!r}")
            if repo.repo.casefold() in seen:
                raise ConfigError(f"{at}.repo: {repo.repo} is listed twice")
            seen.add(repo.repo.casefold())
            if repo.latest < 0:
                raise ConfigError(f"{at}.latest: must be 0 (every release) or more")
            _check_regex(repo.assets, f"{at}.assets")
            for j, pattern in enumerate(repo.strip):
                _check_regex(pattern, f"{at}.strip[{j}]")
                if not pattern:
                    raise ConfigError(f"{at}.strip[{j}]: empty pattern")
        if len(self.repos) > self.max_requests:
            raise ConfigError(
                f"{where}.max_requests: {self.max_requests} cannot cover the "
                f"{len(self.repos)} repos (one request each at least)"
            )


def _check_regex(pattern: str, where: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ConfigError(f"{where}: not a regular expression: {exc}") from exc


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


@functools.cache
def _regex(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# --- keys --------------------------------------------------------------------------------------


def split_extension(name: str) -> tuple[str, str]:
    """``(stem, extension)`` for the archive and font extensions GitHub releases use."""
    lower = name.lower()
    for ext in _EXTENSIONS:
        if lower.endswith(ext) and len(name) > len(ext):
            return name[: -len(ext)], name[-len(ext) :]
    return name, ""


def tag_version(tag: str) -> str | None:
    """The version a tag names: its longest run of digits and ``.``/``_`` (first on a tie).

    ``v2.304`` -> ``2.304``; ``@ibm/plex-sans@1.1.0`` -> ``1.1.0``; ``latest`` -> None.
    """
    runs = _TAG_VERSION.findall(tag)
    return max(runs, key=len) if runs else None


def asset_base(name: str, tag: str, strip: Sequence[str] = ()) -> str:
    """The asset name without its version, so every release of an asset shares one key.

    In order: each ``strip`` regex is removed; then the tag's version
    (``tag_version``) where it stands between separators, with an optional
    ``v`` and the separator before it; then any other dotted version of two or
    more parts; then a trailing commit hash (``Inter-3f174fcef6.zip`` ->
    ``Inter.zip``). Leftover separators at either end of the stem go too. The
    extension is kept; a name that would be left empty is returned unchanged.
    ``JetBrainsMono-2.304.zip`` (tag ``v2.304``) -> ``JetBrainsMono.zip``;
    ``Fira_Code_v6.2.zip`` -> ``Fira_Code.zip``; ``Hack-v3.003-ttf.zip`` ->
    ``Hack-ttf.zip``; ``MapleMono-TTF.zip`` stays as it is.
    """
    stem, ext = split_extension(name)
    for pattern in strip:
        stem = _regex(pattern).sub("", stem)
    version = tag_version(tag)
    if version:
        own = rf"(?:^|[-_. ])[vV]?{re.escape(version)}(?=$|[-_. ])"
        stem = _regex(own).sub("", stem)
    stem = _GENERIC_VERSION.sub("", stem)
    stem = _COMMIT_HASH.sub("", stem).strip(_SEPARATORS)
    return f"{stem}{ext}" if stem else name


def asset_key(repo: str, name: str, tag: str, strip: Sequence[str] = ()) -> SourceKey:
    """``gh-asset:<repo>/<asset base>``."""
    return SourceKey(NAMESPACE, f"{repo}/{asset_base(name, tag, strip)}")


# --- extract rows ------------------------------------------------------------------------------


def _iso(value: object, what: str) -> str:
    """An ISO time with a zone, as ``2026-10-03T06:17:22Z``; ``ValueError`` otherwise."""
    if not isinstance(value, str):
        raise ValueError(f"{what}: expected an ISO time, got {value!r}")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"{what}: time {value!r} has no zone")
    return clock.iso_utc(moment)


def _day(iso: str) -> date:
    return datetime.fromisoformat(iso).astimezone(UTC).date()


@dataclass(frozen=True, slots=True)
class AssetRow:
    """One line of ``assets.jsonl.gz``."""

    repo: str
    tag: str
    asset: str
    published_at: str | None  # ISO UTC; None when GitHub gives none
    prerelease: bool
    created_at: str  # ISO UTC
    downloads: int

    @property
    def ident(self) -> tuple[str, str, str]:
        """``(repo, tag, asset)``: unique within a snapshot."""
        return (self.repo, self.tag, self.asset)

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.repo.casefold(), self.repo, self.tag, self.asset)

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "tag": self.tag,
            "published_at": self.published_at,
            "prerelease": self.prerelease,
            "asset": self.asset,
            "created_at": self.created_at,
            "downloads": self.downloads,
        }

    @classmethod
    def from_json(cls, d: object) -> AssetRow:
        """Read one row strictly; ``ValueError`` on anything else."""
        if not isinstance(d, dict) or tuple(sorted(d)) != ROW_FIELDS:
            raise ValueError(f"{ASSETS}: expected the fields {list(ROW_FIELDS)}, got {d!r}")
        for key in ("repo", "tag", "asset"):
            if not isinstance(d[key], str) or not d[key]:
                raise ValueError(f"{ASSETS}: {key} must be a non-empty string in {d!r}")
        if type(d["prerelease"]) is not bool:
            raise ValueError(f"{ASSETS}: prerelease must be true or false in {d!r}")
        if type(d["downloads"]) is not int or d["downloads"] < 0:
            raise ValueError(f"{ASSETS}: downloads must be a count in {d!r}")
        published = d["published_at"]
        return cls(
            repo=d["repo"],
            tag=d["tag"],
            asset=d["asset"],
            published_at=None if published is None else _iso(published, "published_at"),
            prerelease=d["prerelease"],
            created_at=_iso(d["created_at"], "created_at"),
            downloads=d["downloads"],
        )


def _asset_row(
    repo: str, tag: object, published: object, pre: object, name: object, made: object, n: object
) -> AssetRow:
    """A row from API values, checked (``ValueError`` names what is wrong)."""
    return AssetRow.from_json(
        {
            "repo": repo,
            "tag": tag,
            "published_at": published,
            "prerelease": pre,
            "asset": name,
            "created_at": made,
            "downloads": n,
        }
    )


def rest_rows(repo: str, release: object) -> list[AssetRow]:
    """The rows of one REST release object: none for a draft; uploaded assets only."""
    if not isinstance(release, dict):
        raise ValueError(f"{repo}: a release is not an object: {release!r}")
    if release.get("draft") is True:
        return []
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError(f"{repo} {release.get('tag_name')}: no assets list")
    out = []
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError(f"{repo} {release.get('tag_name')}: an asset is not an object")
        if asset.get("state", "uploaded") != "uploaded":
            continue
        out.append(
            _asset_row(
                repo,
                release.get("tag_name"),
                release.get("published_at"),
                release.get("prerelease"),
                asset.get("name"),
                asset.get("created_at"),
                asset.get("download_count"),
            )
        )
    return out


def graphql_rows(repo: str, release: Mapping[str, Any], nodes: Iterable[object]) -> list[AssetRow]:
    """The rows of one GraphQL release node and its asset nodes (drafts give none)."""
    if release.get("isDraft") is True:
        return []
    out = []
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError(f"{repo} {release.get('tagName')}: an asset is not an object")
        out.append(
            _asset_row(
                repo,
                release.get("tagName"),
                release.get("publishedAt"),
                release.get("isPrerelease"),
                node.get("name"),
                node.get("createdAt"),
                node.get("downloadCount"),
            )
        )
    return out


def read_rows(snapshot: Snapshot) -> list[AssetRow]:
    """Every row of a snapshot's ``assets.jsonl.gz``, checked; duplicates raise ``ValueError``."""
    rows = [AssetRow.from_json(d) for d in snapshot.iter_jsonl(ASSETS)]
    if len({r.ident for r in rows}) != len(rows):
        raise ValueError(f"{snapshot.path / ASSETS}: an asset of a release is listed twice")
    return rows


# --- fetch -------------------------------------------------------------------------------------


@dataclass(slots=True)
class RepoResult:
    """What the fetch found for one repo (a row of ``repos.json``)."""

    repo: Repo
    status: Literal["ok", "missing"] = "ok"
    rows: list[AssetRow] = field(default_factory=list)
    releases: int = 0
    total_releases: int | None = None  # GraphQL's totalCount
    renamed_to: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "repo": self.repo.repo,
            "api": self.repo.api,
            "latest": self.repo.latest,
            "status": self.status,
            "releases": self.releases,
            "assets": len(self.rows),
            "total_releases": self.total_releases,
            "renamed_to": self.renamed_to,
        }


class _Run:
    """One fetch: records every request, counts them against ``max_requests``."""

    def __init__(self, ctx: FetchContext, settings: Settings) -> None:
        self.ctx = ctx
        self.settings = settings
        self.used = 0
        self.last_day: date | None = None

    def spend(self) -> None:
        if self.used >= self.settings.max_requests:
            raise BudgetExceeded(
                f"{NAME}: max_requests ({self.settings.max_requests}) reached; "
                "raise it in config/sources/github_releases.toml"
            )
        self.used += 1

    def record(self, result: FetchResult | None) -> None:
        if result is None:
            return
        for entry in result.to_records(kept=False):
            self.ctx.out.record_fetch(entry)
        day = result.fetched_at.astimezone(UTC).date()
        self.last_day = day if self.last_day is None else max(self.last_day, day)

    def keep(self, rows: Iterable[AssetRow], repo: Repo) -> list[AssetRow]:
        """``rows`` without skipped assets or repeats, first one kept.

        REST pages are offsets, so a release published between two page
        requests pushes the previous page's last release onto the next page.
        """
        out: dict[tuple[str, str, str], AssetRow] = {}
        for row in rows:
            if _kept(row, self.settings, repo):
                out.setdefault(row.ident, row)
        return list(out.values())


def releases_url(repo: str) -> str:
    return f"https://{API_HOST}/repos/{repo}/releases"


def _has_next(result: FetchResult, count: int, per_page: int) -> bool:
    """Whether another page follows: GitHub's ``Link`` header says, or a full page without one."""
    link = result.header("link")
    if link is not None:
        return bool(_LINK_NEXT.search(link))
    return count >= per_page


def _renamed(repo: str, releases: Sequence[object]) -> str | None:
    """The repo's new name when its releases' ``html_url`` names another one."""
    for release in releases:
        url = release.get("html_url") if isinstance(release, dict) else None
        if isinstance(url, str) and url.startswith("https://github.com/"):
            found = "/".join(url.removeprefix("https://github.com/").split("/")[:2])
            return found if found.casefold() != repo.casefold() else None
    return None


def fetch_rest(run: _Run, repo: Repo) -> RepoResult:
    """Every release of ``repo`` (or its newest ``latest``) through the REST API."""
    ctx, s = run.ctx, run.settings
    per_page = min(s.per_page, repo.latest) if repo.latest else s.per_page
    releases: list[object] = []
    more = False
    for page in range(1, s.max_pages + 1):
        run.spend()
        raw = ctx.raw.file(f"{repo.repo.replace('/', '__')}/releases-{page:04d}.json")
        result = ctx.fetcher.get(
            releases_url(repo.repo),
            params={"per_page": per_page, "page": page},
            to=raw,
            expect=(200, 404),
        )
        run.record(result)
        if result.status == 404:
            if page == 1:
                return RepoResult(repo, status="missing")
            raise FetchError(f"{repo.repo}: page {page} of its releases is gone (404)")
        items = result.json()
        if not isinstance(items, list):
            raise ValueError(f"{repo.repo}: releases page {page} is not a list")
        releases += items
        more = _has_next(result, len(items), per_page)
        if repo.latest and len(releases) >= repo.latest:
            releases, more = releases[: repo.latest], False
        if not more:
            break
    if more:
        ctx.log.warning("%s: more than %d pages of releases; older ones left out", repo.repo, page)
        ctx.out.note(f"{repo.repo}: stopped at max_pages ({s.max_pages}); older releases left out")
    rows = [row for release in releases for row in rest_rows(repo.repo, release)]
    return RepoResult(
        repo,
        rows=run.keep(rows, repo),
        releases=len(
            {
                r.get("tag_name")
                for r in releases
                if isinstance(r, dict) and r.get("draft") is not True
            }
        ),
        renamed_to=_renamed(repo.repo, releases),
    )


_ASSET_FIELDS = "pageInfo { hasNextPage endCursor } nodes { name downloadCount createdAt }"

RELEASES_QUERY = f"""query($owner: String!, $name: String!, $first: Int!, $assets: Int!, $after: String) {{
  repository(owner: $owner, name: $name) {{
    nameWithOwner
    releases(first: $first, after: $after, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      totalCount
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        tagName
        publishedAt
        isPrerelease
        isDraft
        releaseAssets(first: $assets) {{ {_ASSET_FIELDS} }}
      }}
    }}
  }}
}}
"""


def assets_query(n: int) -> str:
    """The follow-up query: the next asset page of ``n`` releases, aliased ``r0``..``r<n-1>``."""
    params = "".join(f", $t{i}: String!, $c{i}: String!" for i in range(n))
    aliases = "".join(
        f"    r{i}: release(tagName: $t{i}) {{ releaseAssets(first: $n, after: $c{i}) "
        f"{{ {_ASSET_FIELDS} }} }}\n"
        for i in range(n)
    )
    return (
        f"query($owner: String!, $name: String!, $n: Int!{params}) {{\n"
        f"  repository(owner: $owner, name: $name) {{\n{aliases}  }}\n}}\n"
    )


def _graphql(run: _Run, query: str, variables: Mapping[str, object]) -> dict[str, Any]:
    """One GraphQL request, recorded even when GitHub answers with errors."""
    run.spend()
    fetcher = run.ctx.fetcher
    before = fetcher.last
    try:
        return fetcher.graphql(query, variables)
    finally:
        if fetcher.last is not before:
            run.record(fetcher.last)


def _too_big(exc: FetchError, size: int) -> bool:
    """Whether GitHub cut a query short for its size and a smaller one is possible."""
    return _LIMITS in str(exc) and size > 1


def _page(conn: object, what: str) -> tuple[list[object], bool, str | None]:
    """``(nodes, has next page, end cursor)`` of a GraphQL connection."""
    if not isinstance(conn, dict) or not isinstance(conn.get("nodes"), list):
        raise ValueError(f"{what}: not a connection: {conn!r}")
    info = conn.get("pageInfo") or {}
    more = info.get("hasNextPage") is True
    cursor = info.get("endCursor")
    if more and not isinstance(cursor, str):
        raise ValueError(f"{what}: a next page without a cursor")
    return conn["nodes"], more, cursor if isinstance(cursor, str) else None


@dataclass(slots=True)
class _Release:
    """A GraphQL release node with its assets so far, and the cursor of the next page."""

    node: dict[str, Any]
    assets: list[object]
    cursor: str | None
    pages: int = 1


def releases_per_query(settings: Settings) -> int:
    """Releases one GraphQL query asks for: ``graphql_nodes`` asset nodes' worth, at least 1."""
    return max(1, min(settings.per_page, settings.graphql_nodes // settings.asset_page))


def fetch_graphql(run: _Run, repo: Repo) -> RepoResult:
    """Every release of ``repo`` (or its newest ``latest``) through the GraphQL API.

    Each query asks for ``releases_per_query`` releases; when GitHub answers
    that a query exceeded its resource limits, the same page is asked again at
    half the size. Every asset page after the first is fetched by ``_more_assets``.
    """
    s = run.settings
    owner, name = repo.repo.split("/")
    base = {"owner": owner, "name": name, "assets": s.asset_page}
    cap = repo.latest or s.max_pages * s.per_page
    size = releases_per_query(s)
    releases: list[_Release] = []
    total, found, after, more = None, None, None, True
    while more and len(releases) < cap:
        first = min(size, cap - len(releases))
        try:
            data = _graphql(run, RELEASES_QUERY, {**base, "first": first, "after": after})
        except FetchError as exc:
            if _NOT_FOUND in str(exc) and not releases:
                return RepoResult(repo, status="missing")
            if not _too_big(exc, first):
                raise
            size = first // 2
            run.ctx.log.warning(
                "%s: query too big for GitHub; %d releases a query", repo.repo, size
            )
            continue
        repository = data.get("repository")
        if not isinstance(repository, dict):
            raise ValueError(f"{repo.repo}: GraphQL returned no repository")
        found = repository.get("nameWithOwner")
        conn = repository.get("releases")
        nodes, more, after = _page(conn, f"{repo.repo} releases")
        total = conn.get("totalCount") if isinstance(conn.get("totalCount"), int) else None
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError(f"{repo.repo}: a release is not an object")
            assets, next_page, cursor = _page(node.get("releaseAssets"), f"{repo.repo} assets")
            releases.append(_Release(node, list(assets), cursor if next_page else None))
        more = more and bool(nodes)
    if more and not repo.latest:
        run.ctx.log.warning("%s: more than %d releases; older ones left out", repo.repo, cap)
        run.ctx.out.note(f"{repo.repo}: stopped at {cap} releases (max_pages x per_page)")
    _more_assets(run, repo, releases)
    rows = [row for r in releases for row in graphql_rows(repo.repo, r.node, r.assets)]
    renamed = found if isinstance(found, str) and found.casefold() != repo.repo.casefold() else None
    return RepoResult(
        repo,
        rows=run.keep(rows, repo),
        releases=sum(1 for r in releases if r.node.get("isDraft") is not True),
        total_releases=total,
        renamed_to=renamed,
    )


def _more_assets(run: _Run, repo: Repo, releases: list[_Release]) -> None:
    """Page the remaining assets of every release, ``releases_per_query`` releases a query.

    A release that is gone by the time its next page is asked for is dropped
    whole, never kept in part.
    """
    s = run.settings
    owner, name = repo.repo.split("/")
    size = releases_per_query(s)
    while pending := [r for r in releases if r.cursor is not None]:
        batch = pending[:size]
        variables: dict[str, object] = {"owner": owner, "name": name, "n": s.asset_page}
        for i, r in enumerate(batch):
            variables |= {f"t{i}": r.node.get("tagName"), f"c{i}": r.cursor}
        try:
            data = _graphql(run, assets_query(len(batch)), variables)
        except FetchError as exc:
            if not _too_big(exc, len(batch)):
                raise
            size = len(batch) // 2
            run.ctx.log.warning(
                "%s: query too big for GitHub; %d releases a query", repo.repo, size
            )
            continue
        repository = data.get("repository")
        if not isinstance(repository, dict):
            raise ValueError(f"{repo.repo}: GraphQL returned no repository")
        for i, r in enumerate(batch):
            found = repository.get(f"r{i}")
            if not isinstance(found, dict):
                run.ctx.log.warning("%s %s: release gone mid-fetch", repo.repo, r.node["tagName"])
                run.ctx.out.note(f"{repo.repo} {r.node['tagName']}: gone mid-fetch; left out")
                releases.remove(r)
                continue
            nodes, more, cursor = _page(found.get("releaseAssets"), f"{repo.repo} assets")
            r.assets += nodes
            r.pages += 1
            r.cursor = cursor if more and nodes else None
            if r.cursor is not None and r.pages >= s.max_asset_pages:
                raise FetchError(
                    f"{repo.repo} {r.node.get('tagName')}: more than {s.max_asset_pages} "
                    "asset pages; raise max_asset_pages"
                )


@dataclass(frozen=True, slots=True)
class Before:
    """What the previous snapshot says about one repo (``repos.json``)."""

    api: str
    latest: int
    releases: int
    assets: int  # kept under the filters of that day


def previous_repos(previous: Snapshot | None) -> dict[str, Before]:
    """The ``ok`` repos (case-folded name) of an earlier snapshot's ``repos.json``."""
    if previous is None or not previous.has(REPOS):
        return {}
    doc = previous.load_json(REPOS)
    out = {}
    for d in doc if isinstance(doc, list) else ():
        if not isinstance(d, dict) or d.get("status") != "ok" or not isinstance(d.get("repo"), str):
            continue
        counts = [d.get(k) for k in ("latest", "releases", "assets")]
        if all(type(n) is int and n >= 0 for n in counts):
            out[d["repo"].casefold()] = Before(str(d.get("api")), *counts)
    return out


def previous_assets(previous: Snapshot | None, settings: Settings) -> dict[str, int]:
    """Assets per repo (case-folded) of an earlier snapshot, under *today's* filters.

    Counting under today's ``skip_assets`` and ``assets`` rules means a
    tightened filter is not mistaken for a broken answer by ``check_shrink``.
    """
    if previous is None or not previous.has(ASSETS):
        return {}
    rules = {r.repo.casefold(): r for r in settings.repos}
    out: dict[str, int] = {}
    for row in read_rows(previous):
        name = row.repo.casefold()
        if _kept(row, settings, rules.get(name)):
            out[name] = out.get(name, 0) + 1
    return out


def _ceil(n: int, d: int) -> int:
    return -(-n // d)


def estimate_requests(settings: Settings, before: Mapping[str, Before]) -> int:
    """A lower bound on the requests a fetch makes, from the previous ``repos.json``.

    One per repo the previous snapshot does not describe with today's ``api``
    and ``latest``. Otherwise, REST: one page per ``per_page`` releases (up to
    ``max_pages``); GraphQL: one query per ``releases_per_query`` releases,
    plus the asset pages after each release's first (at least
    ceil(assets / asset_page) - releases of them), as many releases a query.
    """
    size = releases_per_query(settings)
    total = 0
    for repo in settings.repos:
        b = before.get(repo.repo.casefold())
        if b is None or (b.api, b.latest) != (repo.api, repo.latest):
            total += 1
        elif repo.api == "rest":
            per_page = min(settings.per_page, repo.latest) if repo.latest else settings.per_page
            total += min(settings.max_pages, max(1, _ceil(b.releases, per_page)))
        else:
            n = min(b.releases, repo.latest or settings.max_pages * settings.per_page)
            # the assets were counted over b.releases releases; with fewer, claim no more pages
            extra = max(0, _ceil(b.assets, settings.asset_page) - n) if n == b.releases else 0
            total += max(1, _ceil(n, size)) + _ceil(extra, size)
    return total


def check_shrink(
    results: Sequence[RepoResult], before: Mapping[str, int], min_share: float
) -> list[str]:
    """Notes for repos whose asset count fell under ``min_share`` of ``before``.

    Raises ``ValueError`` when the repos in both snapshots together fell under
    it: a broken answer, not a few deleted releases.
    """
    notes = []
    now_total = then_total = 0
    for r in results:
        then = before.get(r.repo.repo.casefold())
        if then is None or r.status != "ok":
            continue
        now_total += len(r.rows)
        then_total += then
        if len(r.rows) < min_share * then:
            notes.append(f"{r.repo.repo}: {len(r.rows)} assets, down from {then}")
    if then_total and now_total < min_share * then_total:
        raise ValueError(
            f"{NAME}: {now_total} assets, down from {then_total} in the previous snapshot "
            f"(below min_share = {min_share}); a broken answer?"
        )
    return notes


# --- parse -------------------------------------------------------------------------------------


def observation(row: AssetRow, end: date, repo: Repo | None) -> Observation:
    """The Observation of one asset row, counted up to ``end`` (the data date)."""
    made = _day(row.created_at)
    found: dict[str, Scalar] = {
        "asset": row.asset,
        "first_seen": made.isoformat(),
        "prerelease": row.prerelease,
        "release": row.tag,
        "repo": row.repo,
    }
    if row.published_at is not None:
        found["published_at"] = _day(row.published_at).isoformat()
    return Observation(
        source=NAME,
        series=SERIES,
        key=asset_key(row.repo, row.asset, row.tag, repo.strip if repo else ()),
        value=float(row.downloads),
        unit=UNIT,
        start=min(made, end),
        end=end,
        attrs=attrs(**found),
    )


def _kept(row: AssetRow, settings: Settings, repo: Repo | None) -> bool:
    if settings.skip_assets and _regex(settings.skip_assets).search(row.asset):
        return False
    return not (repo is not None and repo.assets and not _regex(repo.assets).search(row.asset))


# --- the collector -----------------------------------------------------------------------------


class GithubReleases(CollectorBase):
    """Lifetime download counts of release assets in main-channel repos (ruling M2)."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (API_HOST,)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "github_counters"
    needs_baseline: ClassVar[bool] = True
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Every listed repo's releases, then the two extracts (see the module docstring)."""
        settings = _settings(ctx.settings)
        repos_before, assets_before = self._previous(ctx, settings)
        self._preflight(ctx, settings, estimate_requests(settings, repos_before))
        run = _Run(ctx, settings)
        results = [
            fetch_graphql(run, repo) if repo.api == "graphql" else fetch_rest(run, repo)
            for repo in settings.repos
        ]
        for note in check_shrink(results, assets_before, settings.min_share):
            ctx.log.warning("%s: %s", self.name, note)
            ctx.out.note(note)
        for r in results:
            if r.status == "missing":
                ctx.log.warning("%s: %s not found", self.name, r.repo.repo)
                ctx.out.note(f"{r.repo.repo}: not found")
            elif r.renamed_to:
                ctx.out.note(f"{r.repo.repo}: now {r.renamed_to} (keys keep the listed name)")
        rows = sorted((row for r in results for row in r.rows), key=AssetRow.sort_key)
        ctx.out.write_jsonl(ASSETS, (row.to_json() for row in rows))
        summary = sorted((r.to_json() for r in results), key=lambda d: d["repo"].casefold())
        ctx.out.write_json(REPOS, summary)
        ctx.out.set_data_date(run.last_day or ctx.run_date)
        ctx.log.info(
            "%s: %d repos, %d assets, %d requests",
            self.name,
            len(results),
            len(rows),
            run.used,
        )

    def _previous(
        self, ctx: FetchContext, settings: Settings
    ) -> tuple[dict[str, Before], dict[str, int]]:
        """``previous_repos`` and ``previous_assets`` of ``ctx.previous``.

        An unreadable previous snapshot only costs the estimate and the shrink
        check (noted); it never blocks a fresh snapshot.
        """
        try:
            return previous_repos(ctx.previous), previous_assets(ctx.previous, settings)
        except (ValueError, OSError) as exc:
            ctx.log.warning("%s: previous snapshot unreadable (%s)", self.name, exc)
            ctx.out.note(f"previous snapshot unreadable, so not compared: {exc}")
            return {}, {}

    def _preflight(self, ctx: FetchContext, settings: Settings, need: int) -> None:
        """Refuse before any request when the run cannot finish.

        That is: GraphQL repos without a token, or fewer requests left in
        ``max_requests`` or the run's ``github`` budget than ``need``, the
        lower bound ``estimate_requests`` gives.
        """
        if any(r.api == "graphql" for r in settings.repos) and not os.environ.get(TOKEN_ENV):
            raise FetchError(f"{NAME}: GraphQL repos need {TOKEN_ENV} in the environment")
        if need > settings.max_requests:
            raise BudgetExceeded(
                f"{NAME}: max_requests ({settings.max_requests}) is under the {need} requests "
                "the previous snapshot implies; raise it in config/sources/github_releases.toml"
            )
        budget = ctx.fetcher.budget("github")
        if budget.remaining < need:
            raise BudgetExceeded(
                f"{NAME}: the github budget has {budget.remaining} requests left, "
                f"fewer than the {need} this fetch needs at least"
            )

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One Observation per kept asset of ``assets.jsonl.gz`` (see the module docstring)."""
        settings = _settings(ctx.settings)
        end = ctx.snapshot.manifest.data_date or ctx.snapshot.date
        rules = {r.repo.casefold(): r for r in settings.repos}
        for row in read_rows(ctx.snapshot):
            repo = rules.get(row.repo.casefold())
            if _kept(row, settings, repo):
                yield observation(row, end, repo)


COLLECTOR = GithubReleases()
