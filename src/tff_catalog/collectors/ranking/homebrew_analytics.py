"""Ranking collector "homebrew_analytics": Homebrew cask installs, the ruler (design-m1 §2.4).

**Source.** ``https://formulae.brew.sh/api/analytics/cask-install/{30d,90d,365d}.json``:
static JSON (GitHub Pages behind Fastly), regenerated daily about 01:00 UTC,
0.7-1.8 MB each; no auth, no documented rate limit. The site is BSD-2-Clause
and states no licence for the data; ruling T1 lets the catalog publish the
counts. Envelope ``{category: "cask_install", total_items, start_date,
end_date, total_count, items: [{number, cask, count, percent}]}``, where
``count`` is a string with thousands commas ("144,631"). ``cask`` is a token
(``font-jetbrains-mono``), or ``<tap>/<token>`` for a legacy tap prefix
(``caskroom/cask/``, ``caskroom/fonts/``, ``homebrew/cask-fonts/``) or a
third-party tap (``<user>/<tap>/``).

**Add dates** (design-m1 contradiction C4, gap G3). Exposure needs each cask's
add date, but every older font cask was bulk-migrated into homebrew-cask on
2024-05-15 and a full clone is about 0.9 GB. Only casks added inside the
``dates_window`` (365 days) can be too new, so the fetch lists the
``Casks/font`` tree (GitHub ``git/trees/<commit>:Casks/font?recursive=1``) at
the last commit before the window start (*base*) and at the last commit of the
window's end day (*head*). A cask in *head* but not in *base* was added in the
window; its add date is the oldest commit touching its file since the window
start (``commits?path=…&since=…``, one call, plus the ``rel="last"`` page when
there are more than 100). A new token that ``cask_renames.json`` (at *head*)
names as the new name of a cask removed from *base* is a rename, not an
addition: it keeps the old cask's exposure. Dates carried by the previous
snapshot's ``adds.json`` (``ctx.previous``) for the same file are reused, so
a monthly run only looks up the casks added since. About 100 GitHub calls on a
first run, a handful after; ``max_lookups`` caps them, and the fetch fails
before the first lookup when the run's shared GitHub budget cannot cover them.

**Fetch** keeps only these extracts; the raw bodies go to ``ctx.raw``:

- ``cask-install-<window>.jsonl``: one row ``{cask, count}`` per analytics row
  whose token starts with ``token_prefix``, from any tap, ``cask`` as given
  and ``count`` an int (commas stripped), sorted;
- ``windows.json``: per window ``{start_date, end_date, total_count, total_items}``;
- ``adds.json``: ``{repo, branch, since, until, base: {commit, date, casks},
  head: {…}, added: [{cask, path, first_seen, commit, renamed_from}], removed}``.
  Commit author names and emails are never kept.

A window whose request fails, whose body is not the analytics envelope or has
no font row, or which has fewer font rows than ``min_share`` of the previous
snapshot's is broken. A broken ``dates_window`` (the ruler's 365 days) fails
the fetch, so the stale policy keeps the last good ruler; any other broken
window is left out of the snapshot with a note, so the 30-day file (known to
be unreliable, and read only by Rising) can never make the ruler stale. The
manifest's window and data date are those of ``dates_window``, which must be
the longest window: ``first_seen`` of an older cask means "before the window"
only for windows that start after the *base* commit.

**Parse** (offline, pure): one ``Observation`` per (window, token, tap), unit
``installs``, series the window name, over the window's ``start_date`` to
``end_date``. The key is ``brew-cask:<token>`` with the tap prefix stripped
and the token lower-cased, so a legacy-prefixed row sums into its cask
downstream; its ``tap`` attr says where it came from (``main_tap`` for a bare
token). Rows of other (third-party) taps are left out. Every row carries
``first_seen``: the add date of a cask added in the window, else the *base*
commit's day, which means "before the window" (casks removed during the window
and old tokens found in neither tree count as present before it too).
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

from tff_catalog.collectors.base import CollectorBase, FetchContext, Kind, ParseContext
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import FetchError, FetchResult
from tff_catalog.records import Observation, Record, SourceKey, attrs
from tff_catalog.store import Snapshot

NAME = "homebrew_analytics"
ANALYTICS_HOST = "formulae.brew.sh"
API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
BASE_URL = f"https://{ANALYTICS_HOST}/api/analytics/cask-install"
NAMESPACE = "brew-cask"
UNIT = "installs"
CATEGORY = "cask_install"  # the envelope's category field
WINDOWS_EXTRACT = "windows.json"
ADDS = "adds.json"
PAGE = 100  # GitHub's largest page: one call covers most casks' history
GITHUB_BUDGET = "github"  # the fetcher's per-run budget every api.github.com call is charged to
ANY_STATUS = range(100, 600)

# Defaults of the Settings, which config/sources/homebrew_analytics.toml spells out.
WINDOWS = ("30d", "90d", "365d")  # every window the analytics API serves
LEGACY_TAPS = ("caskroom/cask", "caskroom/fonts", "homebrew/cask-fonts")
# A window losing a quarter of its font casks at once is a broken response, not a trend.
MIN_SHARE = 0.75
# A first run looks up about 100 added casks; far more means the tree layout moved.
MAX_LOOKUPS = 300

_TAP = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_LINK = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')


def window_days(window: str) -> int:
    """The length of an analytics window name ("365d" -> 365)."""
    return int(window.removesuffix("d"))


@dataclass(frozen=True, slots=True)
class Settings(CollectorBase.Settings):
    """``config/sources/homebrew_analytics.toml``."""

    base_url: str = BASE_URL
    windows: tuple[str, ...] = WINDOWS
    dates_window: str = "365d"  # add dates cover this window; the manifest's window too
    token_prefix: str = "font-"
    main_tap: str = "homebrew/cask"
    legacy_taps: tuple[str, ...] = LEGACY_TAPS
    min_share: float = MIN_SHARE  # of the previous snapshot's font rows, per window
    repo: str = "Homebrew/homebrew-cask"
    branch: str = "main"
    font_dir: str = "Casks/font"
    renames_file: str = "cask_renames.json"
    max_lookups: int = MAX_LOOKUPS  # add-date lookups per run, or the fetch fails

    def __post_init__(self) -> None:
        where = f"sources/{NAME}.toml"
        parts = urlsplit(self.base_url)
        if (
            parts.scheme != "https"
            or parts.hostname != ANALYTICS_HOST
            or self.base_url.endswith("/")
        ):
            raise ConfigError(
                f"{where}.base_url: must be an https URL on {ANALYTICS_HOST} without a trailing "
                f"slash, got {self.base_url!r}"
            )
        if not self.windows or len(set(self.windows)) != len(self.windows):
            raise ConfigError(f"{where}.windows: name each window once")
        unknown = sorted(set(self.windows) - set(WINDOWS))
        if unknown:
            raise ConfigError(f"{where}.windows: {unknown} not among {list(WINDOWS)}")
        if self.dates_window not in self.windows:
            raise ConfigError(f"{where}.dates_window: {self.dates_window!r} is not in windows")
        # Older casks get the base commit's day, "before the window", only in windows no
        # longer than this one; in a longer window every cask would look recently added.
        longest = max(self.windows, key=window_days)
        if self.dates_window != longest:
            raise ConfigError(
                f"{where}.dates_window: must be the longest window ({longest!r}), "
                f"got {self.dates_window!r}"
            )
        if not self.token_prefix:
            raise ConfigError(f"{where}.token_prefix: must not be empty")
        for tap in (self.main_tap, *self.legacy_taps):
            if not _TAP.fullmatch(tap):
                raise ConfigError(f"{where}: {tap!r} is not a lower-case <user>/<tap> name")
        if self.main_tap in self.legacy_taps or len(set(self.legacy_taps)) != len(self.legacy_taps):
            raise ConfigError(f"{where}.legacy_taps: repeats a tap or names main_tap")
        if not 0.0 <= self.min_share <= 1.0:
            raise ConfigError(f"{where}.min_share: must be between 0 and 1")
        if not _REPO.fullmatch(self.repo):
            raise ConfigError(f"{where}.repo: {self.repo!r} is not <owner>/<name>")
        for key in ("branch", "font_dir", "renames_file"):
            value = getattr(self, key)
            if not value or value != value.strip("/") or ".." in value.split("/") or " " in value:
                raise ConfigError(f"{where}.{key}: {value!r} is not a relative git name")
        if self.max_lookups < 0:
            raise ConfigError(f"{where}.max_lookups: must not be negative")


def _settings(obj: object) -> Settings:
    if not isinstance(obj, Settings):
        raise TypeError(f"{NAME} needs its Settings, got {type(obj).__name__}")
    return obj


def rows_name(window: str) -> str:
    """The extract holding one window's font rows."""
    return f"cask-install-{window}.jsonl"


def split_cask(cask: str, main_tap: str) -> tuple[str, str]:
    """(tap, token) of an analytics ``cask`` field, lower-cased; a bare token is ``main_tap``'s."""
    tap, _, token = cask.strip().lower().rpartition("/")
    return tap or main_tap, token


def parse_count(value: object) -> int | None:
    """An install count: an int, or a string of digits with thousands commas; else None."""
    if type(value) is int:
        return value if value >= 0 else None
    if isinstance(value, str):
        digits = value.strip().replace(",", "")
        if digits.isascii() and digits.isdigit():
            return int(digits)
    return None


def _iso_day(value: object, where: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{where}: expected a date, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from exc


def _midnight(day: date) -> str:
    """``day`` at 00:00 UTC, as the GitHub API's ``since`` and ``until`` take it."""
    return f"{day.isoformat()}T00:00:00Z"


def _get(ctx: FetchContext, url: str, **kwargs: Any) -> FetchResult:
    """``ctx.fetcher.get`` that records every URL it reached in the manifest."""
    result = ctx.fetcher.get(url, **kwargs)
    for record in result.to_records():
        ctx.out.record_fetch(record)
    return result


# --- fetch: the analytics windows --------------------------------------------------------------


def extract_window(
    doc: object, settings: Settings, where: str
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """``(meta, rows, notes)`` of one analytics body: the window's dates and totals, its font
    rows (``{cask, count}``, sorted) and manifest notes.

    Raises ``ValueError`` when ``doc`` is not the cask-install envelope or holds no font row.
    """
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or doc.get("category") != CATEGORY or not isinstance(items, list):
        raise ValueError(f"{where}: not a {CATEGORY} analytics body")
    start = _iso_day(doc.get("start_date"), f"{where} start_date")
    end = _iso_day(doc.get("end_date"), f"{where} end_date")
    if start > end:
        raise ValueError(f"{where}: start_date {start} is after end_date {end}")
    rows, bad, others = [], 0, 0
    kept = {settings.main_tap, *settings.legacy_taps}
    for item in items:
        cask = item.get("cask") if isinstance(item, dict) else None
        if not isinstance(cask, str):
            bad += 1
            continue
        tap, token = split_cask(cask, settings.main_tap)
        if not token.startswith(settings.token_prefix):
            continue
        count = parse_count(item.get("count"))
        if count is None:
            bad += 1
            continue
        others += tap not in kept
        rows.append({"cask": cask, "count": count})
    if not rows:
        raise ValueError(f"{where}: no {settings.token_prefix}* row; a broken response?")
    rows.sort(key=lambda r: (r["cask"], r["count"]))
    meta = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_count": parse_count(doc.get("total_count")),
        "total_items": parse_count(doc.get("total_items")),
    }
    notes = [f"{where}: {len(rows)} font rows, {others} of them from third-party taps"]
    if bad:
        notes.append(f"{where}: {bad} rows without a cask name or a readable count skipped")
    return meta, rows, notes


def previous_rows(previous: Snapshot | None, window: str) -> int | None:
    """An earlier snapshot's font-row count for ``window``, from its manifest."""
    entry = previous.manifest.extract(rows_name(window)) if previous is not None else None
    return entry.rows if entry is not None else None


def check_shrink(where: str, rows: int, before: int | None, min_share: float) -> None:
    """Raise ``ValueError`` when a window's font rows fell below ``min_share`` of ``before``."""
    if before is not None and rows < min_share * before:
        raise ValueError(
            f"{where}: {rows} font rows, down from {before} in the previous snapshot "
            f"(below min_share = {min_share}); a broken response?"
        )


def fetch_window(ctx: FetchContext, settings: Settings, window: str) -> dict[str, Any]:
    """Fetch one window, write its rows extract; return its ``windows.json`` entry.

    Raises ``ValueError`` for a broken window (module docstring), after recording the
    request: a window left out of a complete snapshot is still listed in its manifest.
    """
    url = f"{settings.base_url}/{window}.json"
    # Any final status comes back (5xx after the fetcher's retries), so it can be recorded.
    result = _get(ctx, url, to=ctx.raw.file(f"cask-install-{window}.json"), expect=ANY_STATUS)
    if result.status != 200:
        raise ValueError(f"{url}: HTTP {result.status}")
    meta, rows, notes = extract_window(result.json(), settings, url)
    check_shrink(url, len(rows), previous_rows(ctx.previous, window), settings.min_share)
    for note in notes:
        ctx.out.note(note)
    ctx.out.write_jsonl(rows_name(window), rows)
    ctx.log.info("%s: %s: %d font rows (%d bytes fetched)", NAME, window, len(rows), result.size)
    return meta


def fetch_windows(ctx: FetchContext, settings: Settings) -> dict[str, dict[str, Any]]:
    """The ``windows.json`` entries of every window fetched intact.

    A broken ``dates_window`` raises (``FetchError`` or ``ValueError``); any other broken
    window is left out, with a manifest note, so it never makes the ruler stale.
    """
    out: dict[str, dict[str, Any]] = {}
    for window in settings.windows:
        try:
            out[window] = fetch_window(ctx, settings, window)
        except (FetchError, ValueError) as exc:
            if window == settings.dates_window:
                raise
            ctx.out.note(f"{window} window left out: {exc}")
            ctx.log.warning("%s: %s window left out: %s", NAME, window, exc)
    return out


# --- fetch: add dates ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class Commit:
    """A commit of the cask repository: its committer time (aware, UTC) and sha.

    Ordered by time, so the oldest of several commits on one day is the smallest.
    """

    moment: datetime
    sha: str

    @property
    def day(self) -> date:
        """The committer date (UTC)."""
        return self.moment.date()

    def to_json(self, casks: int) -> dict[str, Any]:
        return {"commit": self.sha, "date": self.day.isoformat(), "casks": casks}


def commit_of(item: object) -> Commit:
    """A ``Commit`` from one element of GitHub's commit list; ``ValueError`` if malformed."""
    sha = item.get("sha") if isinstance(item, dict) else None
    commit = item.get("commit") if isinstance(item, dict) else None
    committer = commit.get("committer") if isinstance(commit, dict) else None
    stamp = committer.get("date") if isinstance(committer, dict) else None
    if not isinstance(sha, str) or not _SHA.fullmatch(sha) or not isinstance(stamp, str):
        raise ValueError(f"not a GitHub commit: {str(item)[:120]}")
    moment = datetime.fromisoformat(stamp)
    if moment.tzinfo is None:
        raise ValueError(f"commit {sha}: date {stamp!r} has no time zone")
    return Commit(moment.astimezone(UTC), sha)


def _commit_list(doc: object, where: str) -> list[Commit]:
    if not isinstance(doc, list):
        raise ValueError(f"{where}: expected a list of commits")
    return [commit_of(item) for item in doc]


def link_target(header: str | None, rel: str) -> str | None:
    """The URL of a GitHub ``Link`` header entry with relation ``rel``, if any."""
    for url, rels in _LINK.findall(header or ""):
        if rel in rels.split():
            return url
    return None


def _commits_url(settings: Settings) -> str:
    return f"https://{API_HOST}/repos/{settings.repo}/commits"


def latest_commit(ctx: FetchContext, settings: Settings, until: str) -> Commit:
    """The newest commit on ``branch`` before ``until`` (an ISO UTC time)."""
    url = _commits_url(settings)
    params = {"sha": settings.branch, "until": until, "per_page": 1}
    commits = _commit_list(_get(ctx, url, params=params).json(), url)
    if not commits:
        raise ValueError(f"{settings.repo}: no commit on {settings.branch} before {until}")
    return commits[0]


def tree_casks(doc: object, settings: Settings, where: str) -> dict[str, str]:
    """Token -> repository path of every cask file in a ``git/trees`` listing of ``font_dir``."""
    tree = doc.get("tree") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or not isinstance(tree, list):
        raise ValueError(f"{where}: not a git tree listing")
    if doc.get("truncated") is not False:
        raise ValueError(f"{where}: the tree listing is truncated or unmarked")
    out: dict[str, str] = {}
    for entry in tree:
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        rel = entry.get("path")
        if not isinstance(rel, str) or not rel.endswith(".rb"):
            continue
        token = rel.rsplit("/", 1)[-1].removesuffix(".rb").lower()
        if token.startswith(settings.token_prefix):
            out.setdefault(token, f"{settings.font_dir}/{rel}")
    if not out:
        raise ValueError(f"{where}: no {settings.token_prefix}* cask files")
    return out


def font_tree(ctx: FetchContext, settings: Settings, commit: Commit, label: str) -> dict[str, str]:
    """The font casks at ``commit`` (``tree_casks``); the listing itself goes to ``ctx.raw``."""
    url = f"https://{API_HOST}/repos/{settings.repo}/git/trees/{commit.sha}:{settings.font_dir}"
    result = _get(ctx, url, params={"recursive": 1}, to=ctx.raw.file(f"tree-{label}.json"))
    return tree_casks(result.json(), settings, url)


def load_renames(ctx: FetchContext, settings: Settings, head: Commit) -> dict[str, str] | None:
    """``cask_renames.json`` at ``head`` (old token -> new token), or None when it is gone."""
    url = f"https://{RAW_HOST}/{settings.repo}/{head.sha}/{settings.renames_file}"
    result = _get(ctx, url, expect=(200, 404))
    if result.status == 404:
        return None
    doc = result.json()
    if not isinstance(doc, dict):
        raise ValueError(f"{url}: expected an object of old -> new tokens")
    return {
        old.lower(): new.lower()
        for old, new in sorted(doc.items())
        if isinstance(old, str) and isinstance(new, str)
    }


def renamed_from(renames: Mapping[str, str], gone: Iterable[str]) -> dict[str, str]:
    """New token -> the removed token it replaced, following rename chains (first old name wins)."""
    out: dict[str, str] = {}
    for old in sorted(gone):
        seen, new = {old}, renames.get(old)
        while new is not None and new not in seen:
            out.setdefault(new, old)
            seen.add(new)
            new = renames.get(new)
    return out


def first_commit(ctx: FetchContext, settings: Settings, path: str, since: str) -> Commit | None:
    """The oldest commit on ``branch`` touching ``path`` at or after ``since``, if any."""
    url = _commits_url(settings)
    params = {"sha": settings.branch, "path": path, "since": since, "per_page": PAGE}
    result = _get(ctx, url, params=params)
    last = link_target(result.header("link"), "last")
    if last is not None:
        result = _get(ctx, last)
    return min(_commit_list(result.json(), url), default=None)


def previous_adds(previous: Snapshot | None, since: date) -> dict[str, dict[str, Any]]:
    """Looked-up add dates of an earlier snapshot still inside the window, by token."""
    if previous is None or not previous.has(ADDS):
        return {}
    doc = previous.load_json(ADDS)
    added = doc.get("added") if isinstance(doc, dict) else None
    out = {}
    for row in added if isinstance(added, list) else ():
        if not isinstance(row, dict) or row.get("renamed_from") is not None:
            continue
        day, commit = _maybe_day(row.get("first_seen")), row.get("commit")
        if day is not None and day >= since and isinstance(commit, str) and _SHA.fullmatch(commit):
            out[str(row.get("cask"))] = row
    return out


def _maybe_day(value: object) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def check_budget(ctx: FetchContext, lookups: int) -> None:
    """Raise ``ValueError`` when the run's shared GitHub budget cannot cover ``lookups`` calls.

    Each lookup is one call, rarely two, so this is a lower bound. Failing before the
    first lookup leaves the rest of the budget to the collectors that fetch after this one,
    where running out halfway would spend it and still fail.
    """
    left = ctx.fetcher.budget(GITHUB_BUDGET).remaining
    if lookups > left:
        raise ValueError(
            f"{lookups} add-date lookups need more GitHub API calls than the {left} left "
            f"in this run's {GITHUB_BUDGET!r} budget"
        )


def _add_row(
    token: str, path: str, commit: Commit | None, renamed: str | None = None
) -> dict[str, Any]:
    return {
        "cask": token,
        "path": path,
        "first_seen": None if commit is None else commit.day.isoformat(),
        "commit": None if commit is None or renamed else commit.sha,
        "renamed_from": renamed,
    }


def date_adds(ctx: FetchContext, settings: Settings, start: date, end: date) -> dict[str, Any]:
    """The ``adds.json`` extract for the window ``start``..``end`` (module docstring)."""
    since, until = _midnight(start), _midnight(end + timedelta(days=1))
    base = latest_commit(ctx, settings, since)
    head = latest_commit(ctx, settings, until)
    before = font_tree(ctx, settings, base, "base")
    after = font_tree(ctx, settings, head, "head")
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    renames = load_renames(ctx, settings, head)
    if renames is None:
        ctx.out.note(f"{settings.renames_file} not found at {head.sha}; renames not detected")
    renamed = renamed_from(renames or {}, removed)
    known = previous_adds(ctx.previous, start)
    todo = {
        t
        for t in added
        if t not in renamed and (t not in known or known[t].get("path") != after[t])
    }
    if len(todo) > settings.max_lookups:
        raise ValueError(
            f"{settings.repo}: {len(todo)} casks added since {start} need an add date, over "
            f"max_lookups = {settings.max_lookups}; did the tree layout change?"
        )
    check_budget(ctx, len(todo))
    rows, missing = [], []
    for token in added:
        path = after[token]
        if token in renamed:
            rows.append(_add_row(token, path, base, renamed[token]))
        elif token in todo:
            commit = first_commit(ctx, settings, path, since)
            missing += [token] if commit is None else []
            rows.append(_add_row(token, path, commit))
        else:
            rows.append({k: known[token].get(k) for k in _add_row(token, path, None)})
    ctx.out.note(
        f"add dates: {len(before)} font casks at {base.sha[:12]} ({base.day}), {len(after)} at "
        f"{head.sha[:12]} ({head.day}); {len(added)} added ({len(todo)} looked up, "
        f"{len(renamed.keys() & set(added))} renames), {len(removed)} removed"
    )
    if missing:
        ctx.out.note(f"add dates: no commit found for {', '.join(missing)}; counted as older")
    ctx.log.info("%s: %d casks added since %s, %d looked up", NAME, len(added), start, len(todo))
    return {
        "repo": settings.repo,
        "branch": settings.branch,
        "since": since,
        "until": until,
        "base": base.to_json(len(before)),
        "head": head.to_json(len(after)),
        "added": rows,
        "removed": removed,
    }


# --- parse ---------------------------------------------------------------------------------------


def first_seen_dates(adds: object) -> tuple[date, dict[str, date]]:
    """``(default, dated)``: the base commit's day, and the add date of each cask added later."""
    if not isinstance(adds, dict) or not isinstance(adds.get("base"), dict):
        raise ValueError(f"{ADDS}: no base commit")
    default = _iso_day(adds["base"].get("date"), f"{ADDS} base.date")
    dated = {}
    added = adds.get("added")
    for row in added if isinstance(added, list) else ():
        day = row.get("first_seen") if isinstance(row, dict) else None
        if isinstance(day, str) and isinstance(row.get("cask"), str):
            dated[row["cask"]] = _iso_day(day, f"{ADDS} {row['cask']}")
    return default, dated


def window_counts(rows: Iterable[object], settings: Settings) -> dict[tuple[str, str], int]:
    """Summed counts by (token, tap) of the rows of the main and legacy taps."""
    kept = {settings.main_tap, *settings.legacy_taps}
    out: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        cask = row.get("cask") if isinstance(row, dict) else None
        count = parse_count(row.get("count")) if isinstance(row, dict) else None
        if not isinstance(cask, str) or count is None:
            continue
        tap, token = split_cask(cask, settings.main_tap)
        if tap in kept and token.startswith(settings.token_prefix):
            out[(token, tap)] += count
    return dict(sorted(out.items()))


class HomebrewAnalytics(CollectorBase):
    """``formulae.brew.sh`` cask installs over 30, 90 and 365 days, with cask add dates."""

    name: ClassVar[str] = NAME
    kind: ClassVar[Kind] = "ranking"
    version: ClassVar[int] = 1
    hosts: ClassVar[tuple[str, ...]] = (ANALYTICS_HOST, API_HOST, RAW_HOST)
    emits: ClassVar[tuple[type, ...]] = (Observation,)
    group: ClassVar[str | None] = "homebrew"
    needs_baseline: ClassVar[bool] = False
    Settings: ClassVar[type] = Settings

    def fetch(self, ctx: FetchContext) -> None:
        """The windows' font rows, their dates, and the add dates of ``dates_window``."""
        settings = _settings(ctx.settings)
        windows = fetch_windows(ctx, settings)
        ctx.out.write_json(WINDOWS_EXTRACT, windows)
        meta = windows[settings.dates_window]
        start, end = date.fromisoformat(meta["start_date"]), date.fromisoformat(meta["end_date"])
        ctx.out.set_window(start, end)
        ctx.out.set_data_date(end)
        ctx.out.write_json(ADDS, date_adds(ctx, settings, start, end))

    def parse(self, ctx: ParseContext) -> Iterator[Record]:
        """One installs observation per (window, cask token, tap), dated by ``first_seen``."""
        settings = _settings(ctx.settings)
        snap = ctx.snapshot
        windows = snap.load_json(WINDOWS_EXTRACT)
        default, dated = first_seen_dates(snap.load_json(ADDS))
        for window in settings.windows:
            meta = windows.get(window) if isinstance(windows, dict) else None
            if not isinstance(meta, dict) or not snap.has(rows_name(window)):
                ctx.log.warning("%s %s: no %s window", NAME, snap.date, window)
                continue
            start = _iso_day(meta.get("start_date"), f"{window} start_date")
            end = _iso_day(meta.get("end_date"), f"{window} end_date")
            for (token, tap), count in window_counts(
                snap.iter_jsonl(rows_name(window)), settings
            ).items():
                seen = dated.get(token, default)
                yield Observation(
                    source=NAME,
                    series=window,
                    key=SourceKey(NAMESPACE, token),
                    value=float(count),
                    unit=UNIT,
                    start=start,
                    end=end,
                    attrs=attrs(first_seen=seen.isoformat(), tap=tap),
                )


COLLECTOR = HomebrewAnalytics()
