"""Ranking collector "nerd_releases": Nerd Fonts release downloads (design-m1 §2.4).

**Source.** The GitHub releases of ``ryanoasis/nerd-fonts`` (40 on
2026-09-25; v0.1.0 to v0.7.0 have no assets). Each release has one ``.zip``
and one ``.tar.xz`` per patched family, named by the family's ``folderName``
with no version (``JetBrainsMono.zip``), plus ``FontPatcher.zip`` and
``SHA-256.txt``. Download counts are live lifetime totals, and old releases
keep accruing, so every release is read (methodology §5: growth between
snapshots across all releases). GitHub counts are open data under ruling T1;
the extract keeps no author or uploader.

**Fetch.** GitHub GraphQL (``GITHUB_TOKEN`` required), releases newest first:

1. ``releases`` in pages of ``releases_per_page``, each with its first
   ``assets_per_page`` assets (name, download count, creation time);
2. for a release with more assets, ``release(tagName:)`` pages through the rest.

GitHub refuses a query that touches too many assets at once ("Resource limits
for this query exceeded": 10 releases of 100 assets fail, 5 pass), so a
refused page is asked again at half the size, down to 1. Drafts are skipped.
A release whose assets do not add up to its ``totalCount``, or a release list
that does not, fails the fetch, and so do a releases page that brings no new
release (paging would never end) and a list of font assets smaller than
``min_share`` of the previous snapshot's, counted under today's settings (a
broken answer); the stale policy then reuses the last good snapshot.

The one extract, ``assets.jsonl.gz``, has a row per font asset: ``release``
(tag), ``published_at``, ``prerelease``, ``asset`` (file name), ``downloads``
and ``created_at`` (ISO UTC, as GitHub gives them), sorted by release and
asset. A font asset is a file ending in one of ``suffixes`` whose base (the
name without that suffix) is not in ``exclude``: that drops ``SHA-256.txt``,
``FontPatcher``, ``NerdFontsSymbolsOnly`` (the icon glyphs alone) and four
v2.0.0 archives named by style alone (``Regular.zip`` and so on), which no
family can be credited with. The manifest's ``data_date`` is the UTC day of
the last request.

**Parse** (offline, pure): one ``Observation`` per release and asset base,
series ``lifetime``, key ``nerd-folder:<base>`` (the folder name, as the
``nerdfonts`` universe collector keys it), value = downloads of the base's
archives summed (``.zip`` + ``.tar.xz``), unit ``downloads``, ``start`` = the
first archive's creation day, ``end`` = the data date. Attrs: ``release``,
``published_at``, ``prerelease`` and ``first_seen`` (the creation day again:
the exposure of engine source ``nerd``). Prereleases are kept and marked; the
engine skips them. Lifetime counters need a baseline, so ``needs_baseline``.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, ClassVar

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import GITHUB_API_HOST, FetchError, FetchResult
from tff_catalog.records import Observation, Record, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "nerd_releases"
NAMESPACE = "nerd-folder"
SERIES = "lifetime"
ASSETS = "assets.jsonl.gz"
RESOURCE_LIMITS = "Resource limits for this query exceeded"
ROW_FIELDS = ("asset", "created_at", "downloads", "prerelease", "published_at", "release")

# Defaults of the Settings, which config/sources/nerd_releases.toml spells out.
REPO = "ryanoasis/nerd-fonts"
RELEASES_PER_PAGE = 4  # 5 releases of 100 assets passed GitHub's resource limit, 10 failed
ASSETS_PER_PAGE = 100  # GraphQL's maximum page size
SUFFIXES = (".tar.xz", ".zip")
# The last four are stray v2.0.0 uploads named by style alone: no family to credit, and
# they sat under the engine's 10th-percentile floor, which is taken over every key.
EXCLUDE = ("FontPatcher", "NerdFontsSymbolsOnly", "Bold", "BoldItalic", "Italic", "Regular")
MIN_SHARE = 0.9  # Nerd Fonts never deletes releases; losing a tenth is a broken answer

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

_ASSET_FIELDS = (
    "totalCount pageInfo { hasNextPage endCursor } nodes { name downloadCount createdAt }"
)
RELEASES_QUERY = (
    "query($owner: String!, $name: String!, $first: Int!, $after: String, $assets: Int!) "
    "{ repository(owner: $owner, name: $name) "
    "{ releases(first: $first, after: $after, orderBy: {field: CREATED_AT, direction: DESC}) "
    "{ totalCount pageInfo { hasNextPage endCursor } "
    "nodes { tagName isDraft isPrerelease publishedAt "
    f"releaseAssets(first: $assets) {{ {_ASSET_FIELDS} }} }} }} }} }}"
)
ASSETS_QUERY = (
    "query($owner: String!, $name: String!, $tag: String!, $first: Int!, $after: String) "
    "{ repository(owner: $owner, name: $name) { release(tagName: $tag) "
    f"{{ releaseAssets(first: $first, after: $after) {{ {_ASSET_FIELDS} }} }} }} }}"
)


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/nerd_releases.toml``."""

    repo: str = REPO
    releases_per_page: int = RELEASES_PER_PAGE
    assets_per_page: int = ASSETS_PER_PAGE
    suffixes: tuple[str, ...] = SUFFIXES  # font archives; the base is the name without one
    exclude: tuple[str, ...] = EXCLUDE  # archive bases that are not text fonts
    min_share: float = MIN_SHARE  # of the previous snapshot's font assets, or the fetch fails

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        if not _REPO_RE.fullmatch(self.repo):
            raise ConfigError(f"{where}.repo: expected 'owner/name', got {self.repo!r}")
        for key in ("releases_per_page", "assets_per_page"):
            if not 1 <= getattr(self, key) <= 100:
                raise ConfigError(f"{where}.{key}: must be between 1 and 100 (GraphQL pages)")
        if not self.suffixes or any(not s.startswith(".") or len(s) < 2 for s in self.suffixes):
            raise ConfigError(f"{where}.suffixes: expected file suffixes such as '.zip'")
        if any(not name or "/" in name for name in self.exclude):
            raise ConfigError(f"{where}.exclude: expected asset base names such as 'FontPatcher'")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")

    @property
    def owner_name(self) -> tuple[str, str]:
        owner, name = self.repo.split("/")
        return owner, name


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


def asset_base(name: str, settings: Settings) -> str | None:
    """The font base of asset ``name`` (``JetBrainsMono.tar.xz`` -> ``JetBrainsMono``).

    None when the file is not a font archive (no suffix from ``suffixes``) or
    its base is in ``exclude``.
    """
    for suffix in sorted(settings.suffixes, key=len, reverse=True):
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name.removesuffix(suffix)
            return None if base in settings.exclude else base
    return None


# --- GraphQL requests -------------------------------------------------------------------------


def releases_variables(settings: Settings, first: int, after: str | None) -> dict[str, object]:
    """The variables of one ``RELEASES_QUERY`` page."""
    owner, name = settings.owner_name
    return {
        "owner": owner,
        "name": name,
        "first": first,
        "after": after,
        "assets": settings.assets_per_page,
    }


def assets_variables(
    settings: Settings, tag: str, first: int, after: str | None
) -> dict[str, object]:
    """The variables of one ``ASSETS_QUERY`` page (a release's later assets)."""
    owner, name = settings.owner_name
    return {"owner": owner, "name": name, "tag": tag, "first": first, "after": after}


def _record(ctx: FetchContext, result: FetchResult) -> None:
    for record in result.to_records(kept=False):
        ctx.out.record_fetch(record)


def graphql_page(
    ctx: FetchContext, query: str, variables: Mapping[str, object], first: int
) -> tuple[dict[str, Any], int]:
    """Run one page query of ``first`` items, halving that while GitHub refuses it as too big.

    Returns the data and the page size that passed. Every request is recorded
    in the manifest, refused ones too. Other errors propagate.
    """
    size = first
    while True:
        before = ctx.fetcher.last
        try:
            data = ctx.fetcher.graphql(query, {**variables, "first": size})
        except FetchError as exc:
            if ctx.fetcher.last is not None and ctx.fetcher.last is not before:
                _record(ctx, ctx.fetcher.last)  # the request was answered, with errors
            if RESOURCE_LIMITS not in str(exc) or size == 1:
                raise
            size = max(1, size // 2)
            ctx.log.warning("%s: GitHub refused a page as too big; retrying at %d", NAME, size)
            continue
        assert ctx.fetcher.last is not None
        _record(ctx, ctx.fetcher.last)
        return data, size


def _connection(value: object, where: str) -> dict[str, Any]:
    """A GraphQL connection (``totalCount``, ``pageInfo``, ``nodes``); ``FetchError`` otherwise."""
    if not isinstance(value, dict):
        raise FetchError(f"{where}: missing from the GraphQL answer")
    info = value.get("pageInfo")
    if (
        type(value.get("totalCount")) is not int
        or not isinstance(value.get("nodes"), list)
        or not isinstance(info, dict)
        or type(info.get("hasNextPage")) is not bool
    ):
        raise FetchError(f"{where}: not a GraphQL connection: {value!r:.200}")
    if info["hasNextPage"] and (not isinstance(info.get("endCursor"), str) or not value["nodes"]):
        # An empty page or a missing cursor would page forever.
        raise FetchError(f"{where}: a next page after an empty page or without a cursor")
    return value


def _repository(data: dict[str, Any], settings: Settings) -> dict[str, Any]:
    repo = data.get("repository")
    if not isinstance(repo, dict):
        raise FetchError(f"GitHub has no repository {settings.repo}")
    return repo


def fetch_releases(ctx: FetchContext, settings: Settings) -> list[dict[str, Any]]:
    """Every release node, newest first, each with all its assets (``releaseAssets.nodes``)."""
    releases: list[dict[str, Any]] = []
    seen: set[str] = set()
    first, after, total = settings.releases_per_page, None, None
    while True:
        variables = releases_variables(settings, first, after)
        data, first = graphql_page(ctx, RELEASES_QUERY, variables, first)
        conn = _connection(_repository(data, settings).get("releases"), f"{settings.repo} releases")
        total = conn["totalCount"]
        before = len(releases)
        for node in conn["nodes"]:
            tag = node.get("tagName") if isinstance(node, dict) else None
            if not isinstance(tag, str) or not tag:
                raise FetchError(f"{settings.repo}: a release without a tag: {node!r:.200}")
            if tag not in seen:
                seen.add(tag)
                releases.append(node)
        if not conn["pageInfo"]["hasNextPage"]:
            break
        if len(releases) == before or conn["pageInfo"]["endCursor"] == after:
            # Duplicates never grow the list, so this would page on until the GitHub budget ran out.
            raise FetchError(
                f"{settings.repo}: a releases page after {after!r} brought no new release"
            )
        after = conn["pageInfo"]["endCursor"]
        if len(releases) > total:
            raise FetchError(f"{settings.repo}: more releases than the {total} GitHub counts")
    if len(releases) != total:
        raise FetchError(f"{settings.repo}: got {len(releases)} releases, GitHub counts {total}")
    for node in releases:
        node["assets"] = release_assets(ctx, settings, node)
    return releases


def release_assets(
    ctx: FetchContext, settings: Settings, node: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """All asset nodes of a release node, paging past its first ``assets_per_page``."""
    tag = node["tagName"]
    where = f"{settings.repo} {tag} assets"
    conn = _connection(node.get("releaseAssets"), where)
    assets = list(conn["nodes"])
    first = settings.assets_per_page
    while conn["pageInfo"]["hasNextPage"]:
        variables = assets_variables(settings, tag, first, conn["pageInfo"]["endCursor"])
        data, first = graphql_page(ctx, ASSETS_QUERY, variables, first)
        release = _repository(data, settings).get("release")
        if not isinstance(release, dict):
            raise FetchError(f"{settings.repo}: release {tag} disappeared while paging")
        conn = _connection(release.get("releaseAssets"), where)
        assets += conn["nodes"]
        if len(assets) > conn["totalCount"]:
            break
    if len(assets) != conn["totalCount"]:
        raise FetchError(f"{where}: got {len(assets)}, GitHub counts {conn['totalCount']}")
    return assets


# --- the extract --------------------------------------------------------------------------------


def _iso_utc(value: object, where: str) -> str:
    """An ISO time with a zone, as given; ``ValueError`` otherwise."""
    if not isinstance(value, str):
        raise ValueError(f"{where}: expected an ISO time, got {value!r}")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None:
        raise ValueError(f"{where}: expected an ISO time with a zone, got {value!r}")
    return value


def asset_rows(releases: Sequence[Mapping[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    """The extract's rows: the font assets of every non-draft release, sorted.

    Raises ``ValueError`` on a malformed node or an asset listed twice.
    """
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for rel in releases:
        if rel.get("isDraft") is True:
            continue
        tag = rel["tagName"]
        published = _iso_utc(rel.get("publishedAt"), f"{tag} publishedAt")
        prerelease = rel.get("isPrerelease")
        if type(prerelease) is not bool:
            raise ValueError(f"{tag}: isPrerelease is {prerelease!r}")
        for asset in rel["assets"]:
            name = asset.get("name") if isinstance(asset, dict) else None
            if not isinstance(name, str) or not name:
                raise ValueError(f"{tag}: an asset without a name: {asset!r:.200}")
            if asset_base(name, settings) is None:
                continue
            downloads = asset.get("downloadCount")
            if type(downloads) is not int or downloads < 0:
                raise ValueError(f"{tag}/{name}: downloadCount is {downloads!r}")
            if (tag, name) in rows:
                raise ValueError(f"{tag}/{name}: listed twice")
            rows[(tag, name)] = {
                "release": tag,
                "published_at": published,
                "prerelease": prerelease,
                "asset": name,
                "downloads": downloads,
                "created_at": _iso_utc(asset.get("createdAt"), f"{tag}/{name} createdAt"),
            }
    return [rows[k] for k in sorted(rows)]


def previous_rows(previous: Snapshot | None, settings: Settings) -> int | None:
    """How many of an earlier snapshot's font assets the current ``settings`` still keep.

    Counted under today's ``suffixes`` and ``exclude``, so narrowing them never
    reads as a shrunken answer (which would fail every later fetch too, since
    a failed fetch leaves the same earlier snapshot as ``previous``).
    """
    if previous is None or not previous.has(ASSETS):
        return None
    return sum(
        1
        for row in previous.iter_jsonl(ASSETS)
        if isinstance(row, dict)
        and isinstance(row.get("asset"), str)
        and asset_base(row["asset"], settings) is not None
    )


def check_shrink(rows: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when the font assets shrank below ``min_share`` of ``before``."""
    if rows == 0:
        raise ValueError(f"{NAME}: no font assets in any release")
    if before is not None and rows < min_share * before:
        raise ValueError(
            f"{NAME}: {rows} font assets, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken answer?"
        )


# --- parse ---------------------------------------------------------------------------------------


def _day(text: str) -> date:
    return datetime.fromisoformat(text).astimezone(UTC).date()


def check_row(row: object, number: int) -> Mapping[str, Any]:
    """One extract row, type-checked; ``ValueError`` names the row otherwise."""
    where = f"{ASSETS} row {number}"
    if not isinstance(row, dict) or sorted(row) != sorted(ROW_FIELDS):
        raise ValueError(f"{where}: expected the fields {', '.join(ROW_FIELDS)}")
    for key in ("release", "asset"):
        if not isinstance(row[key], str) or not row[key]:
            raise ValueError(f"{where}: {key} must be a non-empty string")
    if type(row["downloads"]) is not int or row["downloads"] < 0:
        raise ValueError(f"{where}: downloads must be a count, got {row['downloads']!r}")
    if type(row["prerelease"]) is not bool:
        raise ValueError(f"{where}: prerelease must be true or false")
    for key in ("published_at", "created_at"):
        _iso_utc(row[key], f"{where} {key}")
    return row


@dataclass(slots=True)
class _Group:
    """The archives of one asset base in one release."""

    published: date
    prerelease: bool
    created: date
    downloads: int = 0


def observations(
    rows: Sequence[Mapping[str, Any]], end: date, settings: Settings
) -> list[Observation]:
    """One lifetime Observation per (release, asset base) of the checked rows."""
    groups: dict[tuple[str, str], _Group] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        base = asset_base(row["asset"], settings)
        if base is None:
            continue
        if (row["release"], row["asset"]) in seen:
            raise ValueError(f"{ASSETS}: {row['release']}/{row['asset']} is listed twice")
        seen.add((row["release"], row["asset"]))
        created = _day(row["created_at"])
        group = groups.setdefault(
            (row["release"], base),
            _Group(_day(row["published_at"]), row["prerelease"], created),
        )
        group.created = min(group.created, created)
        group.downloads += row["downloads"]
    out = []
    for (release, base), g in sorted(groups.items()):
        seen_on = min(g.created, end)
        out.append(
            Observation(
                source=NAME,
                series=SERIES,
                key=SourceKey(NAMESPACE, base),
                value=float(g.downloads),
                unit="downloads",
                start=seen_on,
                end=end,
                attrs=attrs(
                    first_seen=seen_on.isoformat(),
                    prerelease=g.prerelease,
                    published_at=g.published.isoformat(),
                    release=release,
                ),
            )
        )
    return out


# --- the collector -------------------------------------------------------------------------------


class NerdReleases(CollectorBase):
    """Nerd Fonts release downloads: lifetime totals per release and patched family."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (GITHUB_API_HOST,)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "github_counters"
    needs_baseline: ClassVar[bool] = True
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """Every release's font assets and their download counts (see the module doc)."""
        settings = _settings(ctx.settings)
        releases = fetch_releases(ctx, settings)
        rows = asset_rows(releases, settings)
        check_shrink(len(rows), previous_rows(ctx.previous, settings), settings.min_share)
        ctx.out.write_jsonl(ASSETS, rows)
        fetched = [f.fetched_at for f in ctx.out.manifest().fetched]
        ctx.out.set_data_date(date.fromisoformat(max(fetched)[:10]))
        with_assets = len({r["release"] for r in rows})
        assets = sum(len(r["assets"]) for r in releases)
        ctx.out.note(
            f"{len(releases)} releases, {with_assets} with font assets; "
            f"{len(rows)} of {assets} assets kept"
        )
        ctx.log.info("%s: %d releases, %d font assets", self.name, len(releases), len(rows))

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """A lifetime Observation per release and asset base (see the module doc)."""
        settings = _settings(ctx.settings)
        snap = ctx.snapshot
        end = snap.manifest.data_date or snap.date
        rows = [check_row(row, i) for i, row in enumerate(snap.iter_jsonl(ASSETS), start=1)]
        yield from observations(rows, end, settings)


COLLECTOR = NerdReleases()
