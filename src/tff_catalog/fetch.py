"""The shared HTTP fetcher and the "fetch" stage (milestone-1 step 3). Owner: agent I1.

Every network read goes through ``Fetcher`` (ruff bans ``urllib.request`` and
``requests`` elsewhere). It:

- sends ``USER_AGENT``, never a personal email;
- keeps a per-host minimum interval between request starts (default 1 a
  second; 0.0 means no wait), shared by every view of one fetcher;
- retries with exponential backoff and a seeded jitter on connection errors,
  broken bodies, 429, 5xx and GitHub's rate-limit 403s, honouring
  ``Retry-After`` (seconds or an HTTP date) and ``x-ratelimit-reset`` up to
  ``MAX_RETRY_WAIT``, and waiting ``GITHUB_SECONDARY_WAIT`` after a GitHub
  secondary limit that names no time;
- makes conditional GETs from a previous manifest entry (ETag,
  Last-Modified) and reports ``not_modified``;
- follows redirects itself, so every hop is checked, paced and charged, and
  lists them (``FetchResult.redirects``; ``to_records`` gives one manifest
  entry per URL reached);
- refuses any URL outside its scope (``HostNotAllowed``): plain http, or a host
  outside the set it was scoped to, so a collector reaches only its ``hosts``.
  An entry is an exact host name, or a pattern with ``*`` in its leftmost label
  only, next to literal text (``doc-*-sheets.googleusercontent.com``), for a
  service that redirects to numbered hosts; ``host_allowed`` says which match.
  A bare ``*`` label (a whole domain) is refused;
- charges named per-run ``Budget``s. Every request to ``api.github.com`` is
  charged to the ``github`` budget (default ``GITHUB_BUDGET``), and a call that
  names a budget charges it for each request to the call's own host;
- sends ``GITHUB_TOKEN`` (read from the environment at request time) only to
  ``api.github.com``, never to any other host;
- accepts an ``httpx`` transport, so tests use ``httpx.MockTransport``.

httpx is imported only when a ``Fetcher`` is built, so importing this module
(the CLI does, through ``stages``) stays light.

Stage "fetch" (``run``) runs each enabled collector's ``fetch()`` into a new
snapshot, skipping sources whose snapshot for the run date is already complete
unless ``--refetch``. A failed source is left to the stale policy: ``parse``
reuses its latest complete snapshot no older than ``[stale] max_months`` and
flags it (``build/stage/stale.json``); ``fetch_all`` reports which one.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tff_catalog import __version__, clock, jsonio

if TYPE_CHECKING:
    import httpx

    from tff_catalog.collectors.base import Collector
    from tff_catalog.stages import StageContext
    from tff_catalog.store import FetchRecord, RawDir, Snapshot, Store

USER_AGENT = f"trulyfreefonts-catalog/{__version__} (+https://github.com/byronshock/trulyfreefonts)"
DEFAULT_MIN_INTERVAL = 1.0  # seconds between requests to one host
# Hosts that need a slower pace than the default, unless a caller's min_interval says
# otherwise. npm's download API answered about one request in five with a 429 (and no
# Retry-After) at one request a second.
HOST_MIN_INTERVAL: dict[str, float] = {"api.npmjs.org": 1.5}
DEFAULT_RETRIES = 4
DEFAULT_TIMEOUT = 60.0
BACKOFF_BASE = 2.0  # seconds before the first retry; doubled for each later one
BACKOFF_CAP = 120.0
MAX_RETRY_WAIT = 600.0  # a server asking for a longer wait fails the request instead
MAX_REDIRECTS = 10
JITTER_SEED = 20260925  # fixed, so retry delays are reproducible
GITHUB_API_HOST = "api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_BUDGET = 900  # an Actions GITHUB_TOKEN allows 1,000 API requests an hour per repository
GITHUB_SECONDARY_WAIT = 60.0  # GitHub's advice after a secondary rate limit without a reset time
TOKEN_ENV = "GITHUB_TOKEN"
XSSI_PREFIX = b")]}'"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CROSS_HOST_DROP = frozenset({"authorization", "cookie"})

# Indirections so tests can drive a fake clock; pacing needs a monotonic clock, not dates.
_sleep = time.sleep
_monotonic = time.monotonic


class HostNotAllowed(RuntimeError):
    """A request went outside the fetcher's scope: a host it was not scoped to, or not https."""


class BudgetExceeded(RuntimeError):
    """A named per-run request budget ran out."""


class FetchError(RuntimeError):
    """A request failed after every retry, or returned an unexpected status."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    """One completed request."""

    url: str  # the requested URL, query string included
    final_url: str  # after redirects
    status: int
    headers: tuple[tuple[str, str], ...]  # lower-cased names, in response order
    content: bytes  # empty when streamed to ``path`` or when not modified
    path: Path | None  # set when the body was streamed to a file
    sha256: str  # of the body ("" when not modified)
    size: int  # body length in bytes
    fetched_at: datetime  # aware UTC, from clock.utc_now()
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False
    redirects: tuple[str, ...] = ()  # every URL a redirect led to, in order; the last is final_url

    def header(self, name: str) -> str | None:
        """The first response header called ``name`` (any case), if present."""
        name = name.lower()
        return next((v for k, v in self.headers if k == name), None)

    def body(self) -> bytes:
        """The body: ``content``, or the file it was streamed to."""
        return self.path.read_bytes() if self.path is not None else self.content

    def text(self) -> str:
        """The body decoded as UTF-8."""
        return self.body().decode("utf-8")

    def json(self) -> Any:
        """The body parsed as JSON, after stripping an optional ``)]}'`` XSSI prefix."""
        data = self.body().lstrip()
        if data.startswith(XSSI_PREFIX):
            data = data[len(XSSI_PREFIX) :].lstrip(b",")
        return json.loads(data)

    def to_record(self, *, kept: bool = False) -> FetchRecord:
        """The manifest entry for this request (``store.FetchRecord``)."""
        from tff_catalog.store import FetchRecord

        return FetchRecord(
            url=self.url,
            status=self.status,
            fetched_at=clock.iso_utc(self.fetched_at),
            sha256=self.sha256,
            bytes=self.size,
            etag=self.etag,
            last_modified=self.last_modified,
            kept=kept,
        )

    def to_records(self, *, kept: bool = False) -> tuple[FetchRecord, ...]:
        """One manifest entry per URL this request reached: ``url``, then each redirect.

        A manifest must list every URL a collector contacted (the contract
        test checks), and a redirected request contacts several. Every entry
        carries the final response's status, hash and validators.
        """
        first = self.to_record(kept=kept)
        return (first, *(replace(first, url=hop) for hop in self.redirects))


@dataclass(slots=True)
class Budget:
    """A named per-run request budget, for example ``Budget("github", 900)``."""

    name: str
    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def spend(self, n: int = 1) -> None:
        """Charge ``n`` requests; raise ``BudgetExceeded`` past the limit."""
        if n < 0:
            raise ValueError(f"budget {self.name}: cannot spend {n}")
        if self.used + n > self.limit:
            raise BudgetExceeded(
                f"the {self.name!r} budget of {self.limit} requests is spent "
                f"({self.used} used, {n} more asked)"
            )
        self.used += n


def normalize_url(url: str) -> str:
    """``url`` with scheme and host lower-cased and query parameters sorted (for lookups)."""
    parts = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


def previous_record(snapshot: Snapshot | None, url: str) -> FetchRecord | None:
    """The last manifest entry of ``snapshot`` for ``url`` that carries a validator, if any.

    Pass it as ``Fetcher.get(url, previous=...)`` for a conditional GET; on a
    304, copy the unchanged extract with ``SnapshotWriter.copy_extract``.
    """
    if snapshot is None:
        return None
    wanted = normalize_url(url)
    found = [
        r
        for r in snapshot.manifest.fetched
        if normalize_url(r.url) == wanted and (r.etag or r.last_modified)
    ]
    return found[-1] if found else None


# --- the fetcher ----------------------------------------------------------------------------


@dataclass(slots=True)
class _Shared:
    """State every view of one fetcher shares: client, pacing and budgets."""

    client: httpx.Client
    intervals: dict[str, float]
    retries: int
    budgets: dict[str, Budget]
    log: logging.Logger
    last_start: dict[str, float] = field(default_factory=dict)
    host_locks: dict[str, threading.Lock] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    budget_lock: threading.Lock = field(default_factory=threading.Lock)
    warned_anonymous: bool = False

    def host_lock(self, host: str) -> threading.Lock:
        with self.lock:
            return self.host_locks.setdefault(host, threading.Lock())


class _Retry(Exception):
    """One attempt failed in a way worth retrying."""

    def __init__(self, reason: str, wait: float = 0.0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.wait = wait  # what the server asked for, in seconds


# A hosts pattern: "*" in the leftmost label only, beside literal text, then a domain of
# at least two labels. Only "*" is special (fnmatch's "?" and "[" can't appear).
_HOST_PATTERN = re.compile(
    r"(?=[^.]*[a-z0-9])[a-z0-9-]*\*[a-z0-9*-]*(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?){2,}"
)


def _host_set(hosts: Iterable[str]) -> frozenset[str]:
    if isinstance(hosts, str):
        raise TypeError("hosts must be an iterable of host names, not one string")
    out = frozenset(h.lower() for h in hosts)
    for h in out:
        if "*" in h and not _HOST_PATTERN.fullmatch(h):
            raise ValueError(
                f"hosts pattern {h!r}: '*' may appear only in the leftmost label, "
                "beside literal text, followed by a domain of two or more labels"
            )
    return out


def host_allowed(host: str, hosts: Iterable[str]) -> bool:
    """Whether ``host`` is one of ``hosts``: equal to an exact entry, or matching a
    pattern entry's leftmost label (``*`` stands for any run of letters, digits and
    hyphens, never a dot) with the rest equal."""
    host = host.lower()
    label, _, rest = host.partition(".")
    for entry in hosts:
        if entry == host:
            return True
        if "*" in entry:
            first, _, domain = entry.partition(".")
            if domain == rest and fnmatchcase(label, first):
                return True
    return False


def _jitter(key: str) -> float:
    """A reproducible number in [0, 1) for ``key``, from ``JITTER_SEED``."""
    digest = hashlib.sha256(f"{JITTER_SEED}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def backoff_delay(url: str, attempt: int) -> float:
    """Seconds to wait before retry ``attempt + 1`` of ``url``: capped doubling, 50-100% jitter."""
    base = min(BACKOFF_CAP, BACKOFF_BASE * 2**attempt)
    return base * (0.5 + 0.5 * _jitter(f"{url}#{attempt}"))


def _seconds_until(moment: datetime) -> float:
    return max(0.0, (moment - clock.utc_now()).total_seconds())


def _retry_after(value: str | None) -> float | None:
    """``Retry-After`` in seconds: delta-seconds or an HTTP date; None if absent or unreadable."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return _seconds_until(moment)


def _retry_wait(status: int, headers: httpx.Headers) -> float | None:
    """None when a response is final; else the wait (seconds) the server asked for, or 0."""
    limited = headers.get("x-ratelimit-remaining") == "0"
    asked = _retry_after(headers.get("retry-after"))
    if not (status == 429 or status >= 500 or (status == 403 and (limited or asked is not None))):
        return None
    if asked is None and limited:
        reset = headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            asked = _seconds_until(datetime.fromtimestamp(int(reset), UTC)) + 1.0
    return asked or 0.0


def _github_secondary_limit(response: httpx.Response) -> bool:
    """A GitHub API 403 or 429 that says "rate limit" in its body (a secondary limit).

    GitHub may send one without ``Retry-After`` or ``x-ratelimit-*`` headers,
    and then asks clients to wait at least a minute before retrying.
    """
    import httpx

    if response.request.url.host != GITHUB_API_HOST or response.status_code not in (403, 429):
        return False
    try:
        return b"rate limit" in response.read()[:4096].lower()
    except httpx.TransportError, httpx.DecodingError:
        return False


class Fetcher:
    """Rate-limited, retrying HTTP client shared by every collector in a run.

    ``last`` is the most recent ``FetchResult`` of this view; ``graphql()``
    returns only the data, so its callers record ``fetcher.last.to_record()``.
    """

    def __init__(
        self,
        *,
        hosts: Iterable[str] | None = None,
        user_agent: str = USER_AGENT,
        min_interval: Mapping[str, float] | None = None,
        retries: int = DEFAULT_RETRIES,
        timeout: float = DEFAULT_TIMEOUT,
        budgets: Iterable[Budget] = (),
        transport: httpx.BaseTransport | None = None,
        log: logging.Logger | None = None,
    ) -> None:
        """Create a fetcher. ``hosts=None`` allows any host (use ``scoped`` per collector)."""
        import httpx

        if retries < 0:
            raise ValueError("retries must not be negative")
        intervals = {h.lower(): float(s) for h, s in (min_interval or {}).items()}
        if any(s < 0 for s in intervals.values()):
            raise ValueError("min_interval values must not be negative")
        named: dict[str, Budget] = {}
        for b in budgets:
            if b.name in named:
                raise ValueError(f"budget {b.name!r} given twice")
            named[b.name] = b
        named.setdefault("github", Budget("github", GITHUB_BUDGET))
        client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": user_agent},
            follow_redirects=False,
        )
        self._shared = _Shared(
            client=client,
            intervals=intervals,
            retries=retries,
            budgets=named,
            log=log or logging.getLogger("tff_catalog.fetch"),
        )
        self._hosts: frozenset[str] | None = None if hosts is None else _host_set(hosts)
        self._owner = True
        self.last: FetchResult | None = None

    @property
    def hosts(self) -> frozenset[str] | None:
        """The hosts this view may reach (None: any)."""
        return self._hosts

    def budget(self, name: str) -> Budget:
        """The shared budget called ``name`` (``KeyError`` if there is none)."""
        try:
            return self._shared.budgets[name]
        except KeyError:
            known = ", ".join(sorted(self._shared.budgets))
            raise KeyError(f"no budget named {name!r}; budgets: {known}") from None

    def scoped(self, hosts: Iterable[str]) -> Fetcher:
        """A view sharing this fetcher's client, rate state and budgets, limited to ``hosts``.

        A view of a view never widens: its hosts are the intersection, patterns
        compared by their text. Closing a view does nothing; the fetcher that
        created the client closes it.
        """
        wanted = _host_set(hosts)
        if self._hosts is not None:
            wanted &= self._hosts
        view = Fetcher.__new__(Fetcher)
        view._shared = self._shared
        view._hosts = wanted
        view._owner = False
        view.last = None
        return view

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        previous: FetchRecord | None = None,
        to: Path | None = None,
        budget: str | None = None,
        expect: Iterable[int] = (200,),
    ) -> FetchResult:
        """GET ``url``; conditional when ``previous`` is given; streamed to ``to`` when set.

        With ``previous`` carrying an ETag or Last-Modified, a 304 is accepted
        and returned with ``not_modified=True``, an empty body and the
        validators carried over. ``to`` is written atomically. Any status
        outside ``expect`` raises ``FetchError`` (after retries if retryable).
        """
        sent = dict(headers or {})
        wanted = set(expect)
        if previous is not None and (previous.etag or previous.last_modified):
            if previous.etag:
                sent.setdefault("If-None-Match", previous.etag)
            if previous.last_modified:
                sent.setdefault("If-Modified-Since", previous.last_modified)
            wanted.add(304)
        return self._request(
            "GET",
            url,
            params=params,
            headers=sent,
            to=to,
            budget=budget,
            expect=frozenset(wanted),
            previous=previous,
        )

    def get_range(
        self, url: str, start: int, end: int, *, budget: str | None = None
    ) -> FetchResult:
        """GET bytes ``start..end`` (inclusive) with an HTTP Range header (font tables).

        Status 206: ``content`` is that range. Status 200: the server ignored
        Range and ``content`` is the whole file (callers slice it themselves).
        """
        if start < 0 or end < start:
            raise ValueError(f"bad byte range {start}-{end}")
        headers = {"Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"}
        return self._request(
            "GET", url, headers=headers, budget=budget, expect=frozenset({200, 206})
        )

    def head(self, url: str, *, headers: Mapping[str, str] | None = None) -> FetchResult:
        """HEAD ``url``, following redirects (link checks, foundry URLs).

        Any final status is returned, 404 included; retryable ones (429, 5xx)
        are retried first. ``FetchError`` means no response arrived at all.
        """
        return self._request("HEAD", url, headers=dict(headers or {}), expect=None)

    def post_json(
        self,
        url: str,
        payload: object,
        *,
        headers: Mapping[str, str] | None = None,
        budget: str | None = None,
    ) -> FetchResult:
        """POST a JSON body (ecosyste.ms bulk lookup); any 2xx is accepted.

        The body is ``jsonio.canonical_bytes(payload)``. Retries resend it, so
        use this only for read-only lookups.
        """
        sent = {"Content-Type": "application/json", **(headers or {})}
        return self._request(
            "POST",
            url,
            headers=sent,
            content=jsonio.canonical_bytes(payload),
            budget=budget,
            expect=frozenset(range(200, 300)),
        )

    def graphql(self, query: str, variables: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Run a GitHub GraphQL query with ``GITHUB_TOKEN``; charges the ``github`` budget.

        POSTs ``{"query": query, "variables": variables or {}}`` to
        ``GITHUB_GRAPHQL_URL`` and returns the response's ``data`` object.
        GraphQL ``errors`` raise ``FetchError``. Record the request with
        ``fetcher.last.to_record()``.
        """
        if not os.environ.get(TOKEN_ENV):
            raise FetchError(f"GitHub GraphQL needs {TOKEN_ENV} in the environment")
        payload = {"query": query, "variables": dict(variables or {})}
        doc = self.post_json(GITHUB_GRAPHQL_URL, payload, budget="github").json()
        errors = doc.get("errors") if isinstance(doc, dict) else None
        if errors:
            messages = "; ".join(str(e.get("message", e)) for e in errors if isinstance(e, dict))
            raise FetchError(f"GitHub GraphQL errors: {messages or errors!r}")
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, dict):
            raise FetchError("GitHub GraphQL returned no data")
        return data

    def close(self) -> None:
        """Close the shared client (a no-op on a view from ``scoped``)."""
        if self._owner:
            self._shared.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- internals ---------------------------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        content: bytes | None = None,
        to: Path | None = None,
        budget: str | None = None,
        expect: frozenset[int] | None,
        previous: FetchRecord | None = None,
    ) -> FetchResult:
        """Send with retries; ``expect=None`` accepts any final status."""
        import httpx

        target = httpx.URL(url)
        if params:
            target = target.copy_merge_params(dict(params))
        requested = str(target)
        self._check(target)
        if budget is not None:
            self.budget(budget)  # fail fast on an unknown name
        shared = self._shared
        for attempt in range(shared.retries + 1):
            final = attempt == shared.retries
            try:
                result = self._attempt(
                    method, target, headers, content, to, budget, expect, previous, final
                )
            except _Retry as retry:
                if final:
                    raise FetchError(
                        f"{method} {requested}: {retry.reason} (gave up after {attempt + 1} tries)"
                    ) from retry
                if retry.wait > MAX_RETRY_WAIT:
                    raise FetchError(
                        f"{method} {requested}: {retry.reason}; the server asks for a "
                        f"{retry.wait:.0f} s wait, over the {MAX_RETRY_WAIT:.0f} s limit"
                    ) from retry
                delay = max(backoff_delay(requested, attempt), retry.wait)
                shared.log.warning(
                    "%s %s: %s; retry %d of %d in %.1f s",
                    method,
                    requested,
                    retry.reason,
                    attempt + 1,
                    shared.retries,
                    delay,
                )
                _sleep(delay)
                continue
            self.last = result
            return result
        raise AssertionError("unreachable")  # the loop always returns or raises

    def _attempt(
        self,
        method: str,
        target: httpx.URL,
        headers: Mapping[str, str] | None,
        content: bytes | None,
        to: Path | None,
        budget: str | None,
        expect: frozenset[int] | None,
        previous: FetchRecord | None,
        final: bool,
    ) -> FetchResult:
        import httpx

        # A body that breaks off or fails to decode is worth another try, like a dropped connection.
        broken = (httpx.TransportError, httpx.DecodingError)
        try:
            response, hops = self._send(method, target, headers, content, budget)
        except httpx.TransportError as exc:
            raise _Retry(f"{type(exc).__name__}: {exc}") from exc
        try:
            status = response.status_code
            wait = _retry_wait(status, response.headers)
            if not wait and _github_secondary_limit(response):
                wait = GITHUB_SECONDARY_WAIT
            if wait is not None and not final:
                raise _Retry(f"HTTP {status}", wait)
            if expect is not None and status not in expect:
                try:
                    snippet = response.read()[:200].decode("utf-8", "replace").strip()
                except broken:
                    snippet = ""
                raise FetchError(
                    f"{method} {target}: HTTP {status}, expected {sorted(expect)}"
                    + (f": {snippet}" if snippet else "")
                )
            try:
                return self._result(str(target), hops, response, to, previous)
            except broken as exc:
                raise _Retry(f"{type(exc).__name__} while reading: {exc}") from exc
        finally:
            response.close()

    def _send(
        self,
        method: str,
        target: httpx.URL,
        headers: Mapping[str, str] | None,
        content: bytes | None,
        budget: str | None,
    ) -> tuple[httpx.Response, tuple[httpx.URL, ...]]:
        """Send one request, following redirects hop by hop; the response is streamed.

        Returns the final response and every URL a redirect led to (empty without one).
        """
        shared = self._shared
        origin = target.host
        url = target
        hops: list[httpx.URL] = []
        for _ in range(MAX_REDIRECTS + 1):
            self._check(url)
            self._charge(url.host, budget if url.host == origin else None)
            self._pace(url.host)
            request = shared.client.build_request(
                method, url, headers=self._headers(url, headers, origin), content=content
            )
            response = shared.client.send(request, stream=True)
            shared.log.debug("%s %s -> %d", method, url, response.status_code)
            location = response.headers.get("location")
            if response.status_code not in _REDIRECTS or not location:
                return response, tuple(hops)
            response.close()
            url = url.join(location)
            hops.append(url)
            status = response.status_code
            # As browsers do: 303 becomes a GET, and so does a POST answered by 301 or 302.
            if (status == 303 and method != "HEAD") or (status in (301, 302) and method == "POST"):
                method, content = "GET", None
        raise FetchError(f"{target}: more than {MAX_REDIRECTS} redirects")

    def _check(self, url: httpx.URL) -> None:
        if url.scheme != "https":
            raise HostNotAllowed(f"{url}: only https URLs are fetched")
        if self._hosts is not None and not host_allowed(url.host, self._hosts):
            allowed = ", ".join(sorted(self._hosts)) or "none"
            raise HostNotAllowed(
                f"{url}: host {url.host!r} is not in this fetcher's hosts ({allowed})"
            )

    def _charge(self, host: str, budget: str | None) -> None:
        """Charge every budget this request counts against, all or none."""
        shared = self._shared
        names = {budget} if budget is not None else set()
        if host == GITHUB_API_HOST and "github" in shared.budgets:
            names.add("github")
        with shared.budget_lock:
            charged = [self.budget(n) for n in sorted(names)]
            for b in charged:
                if b.remaining < 1:
                    b.spend(1)  # raises BudgetExceeded with the full message
            for b in charged:
                b.spend(1)

    def _pace(self, host: str) -> None:
        """Wait until ``host``'s minimum interval since its last request start has passed."""
        shared = self._shared
        interval = shared.intervals.get(host)
        if interval is None:  # a min_interval key may be a hosts pattern
            matched = [v for k, v in shared.intervals.items() if host_allowed(host, (k,))]
            interval = matched[0] if matched else HOST_MIN_INTERVAL.get(host, DEFAULT_MIN_INTERVAL)
        with shared.host_lock(host):
            last = shared.last_start.get(host)
            if last is not None and interval > 0:
                wait = last + interval - _monotonic()
                if wait > 0:
                    _sleep(wait)
            shared.last_start[host] = _monotonic()

    def _headers(
        self, url: httpx.URL, headers: Mapping[str, str] | None, origin: str
    ) -> dict[str, str]:
        """Request headers for one hop: credentials never cross hosts; the token goes to GitHub only."""
        sent = dict(headers or {})
        if url.host != origin:
            sent = {k: v for k, v in sent.items() if k.lower() not in _CROSS_HOST_DROP}
        if url.host == GITHUB_API_HOST:
            lower = {k.lower() for k in sent}
            token = os.environ.get(TOKEN_ENV)
            if token and "authorization" not in lower:
                sent["Authorization"] = f"Bearer {token}"
            elif not token and not self._shared.warned_anonymous:
                self._shared.warned_anonymous = True
                self._shared.log.warning(
                    "%s is not set: GitHub API requests are anonymous (60 an hour)", TOKEN_ENV
                )
            if "accept" not in lower:
                sent["Accept"] = "application/vnd.github+json"
            if "x-github-api-version" not in lower:
                sent["X-GitHub-Api-Version"] = "2022-11-28"
        return sent

    def _result(
        self,
        requested: str,
        hops: tuple[httpx.URL, ...],
        response: httpx.Response,
        to: Path | None,
        previous: FetchRecord | None,
    ) -> FetchResult:
        fetched_at = clock.utc_now()
        headers = tuple(response.headers.multi_items())
        etag = response.headers.get("etag")
        modified = response.headers.get("last-modified")
        redirects = tuple(str(u) for u in hops)
        common = {
            "url": requested,
            "final_url": redirects[-1] if redirects else requested,
            "status": response.status_code,
            "headers": headers,
            "fetched_at": fetched_at,
            "redirects": redirects,
        }
        if response.status_code == 304:
            return FetchResult(
                **common,
                content=b"",
                path=None,
                sha256="",
                size=0,
                etag=etag or (previous.etag if previous else None),
                last_modified=modified or (previous.last_modified if previous else None),
                not_modified=True,
            )
        if to is not None:
            digest, size = _stream_to(response, to)
            return FetchResult(
                **common,
                content=b"",
                path=to,
                sha256=digest,
                size=size,
                etag=etag,
                last_modified=modified,
            )
        body = response.read()
        return FetchResult(
            **common,
            content=body,
            path=None,
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
            etag=etag,
            last_modified=modified,
        )


def _stream_to(response: httpx.Response, to: Path) -> tuple[str, int]:
    """Write the body to ``to`` atomically; return its sha256 and size."""
    to = Path(to)
    to.parent.mkdir(parents=True, exist_ok=True)
    part = to.with_name(f".{to.name}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        with part.open("wb") as fh:
            for chunk in response.iter_bytes():
                digest.update(chunk)
                fh.write(chunk)
                size += len(chunk)
        part.replace(to)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size


# --- the fetch stage ------------------------------------------------------------------------


Outcome = Literal["fetched", "kept", "frozen", "failed"]


@dataclass(frozen=True, slots=True)
class SourceFetch:
    """What the fetch stage did for one collector.

    Outcomes: ``fetched``; ``kept`` (the run date's snapshot was already
    complete); ``frozen`` (no snapshot for a date a merged run used, and adding
    one would change that run's replay, so nothing was fetched); ``failed``.
    """

    source: str
    outcome: Outcome
    snapshot: date | None  # the snapshot parse will use; None: the source drops out this run
    stale: bool = False  # ``snapshot`` is an older one standing in (stale policy)
    error: str | None = None


def select(ctx: StageContext, collectors: Mapping[str, Collector]) -> dict[str, Collector]:
    """The collectors ``--only`` names, directly or through an engine source's ``collector``."""
    only = ctx.options.only
    if not only:
        return dict(collectors)
    wanted: set[str] = set()
    engine: dict[str, Any] | None = None
    for name in only:
        if name in collectors:
            wanted.add(name)
            continue
        if engine is None:
            engine = ctx.config.ranking.sources.all()
        if name in engine and engine[name].collector in collectors:
            wanted.add(engine[name].collector)
        else:
            ctx.log.warning("--only %s: no collector or engine source of that name", name)
    return {n: c for n, c in collectors.items() if n in wanted}


def fetch_all(
    ctx: StageContext, collectors: Mapping[str, Collector] | None = None
) -> tuple[SourceFetch, ...]:
    """Snapshot every enabled, selected collector; one ``SourceFetch`` each, by name.

    ``collectors`` defaults to ``collectors.discover()``. Settings are all
    loaded before any request, so a config error stops the stage early
    (``ConfigError``). ``--refetch`` of a snapshot a merged run used raises
    ``store.SnapshotFrozen``, also before any request, so a refused refetch
    changes nothing. Any other failure of one collector is logged and
    reported, with the snapshot the stale policy falls back on.
    """
    from tff_catalog.collectors import discover
    from tff_catalog.collectors.base import load_settings
    from tff_catalog.store import stale_max_age

    store = ctx.require_store()
    fetcher = ctx.require_fetcher()
    chosen = select(ctx, discover() if collectors is None else collectors)
    settings = {name: load_settings(c, ctx.paths) for name, c in sorted(chosen.items())}
    # As in parse: Settings without an ``enabled`` switch mean always on.
    enabled = [name for name, s in settings.items() if getattr(s, "enabled", True)]
    for name in sorted(set(settings) - set(enabled)):
        ctx.log.info("%s: disabled in config/sources/%s.toml; not fetched", name, name)
    if ctx.options.refetch:
        _refuse_frozen_refetch(ctx, store, enabled)
    if not os.environ.get(TOKEN_ENV) and any(GITHUB_API_HOST in chosen[n].hosts for n in enabled):
        ctx.log.warning("%s is not set: GitHub API requests will be anonymous", TOKEN_ENV)
    max_age = stale_max_age(ctx.config.ranking.stale.max_months)
    raw_root = store.raw_dir(
        ctx.paths, f"fetch-{ctx.run_date.isoformat()}", keep=ctx.options.keep_raw
    )
    try:
        out = [
            _fetch_one(ctx, store, fetcher, chosen[name], settings[name], max_age, raw_root)
            for name in enabled
        ]
    finally:
        raw_root.cleanup()
    _log_summary(ctx.log, out)
    return tuple(out)


def _refuse_frozen_refetch(ctx: StageContext, store: Store, names: Iterable[str]) -> None:
    """``SnapshotFrozen`` if ``--refetch`` would replace a snapshot that a merged run used."""
    from tff_catalog.store import SnapshotFrozen

    day = ctx.run_date
    refused = [
        n for n in names if day in ctx.state.frozen_dates(n) and store.snapshot(n, day) is not None
    ]
    if refused:
        raise SnapshotFrozen(
            f"--refetch {day}: a merged run used the {', '.join(refused)} snapshot of that date "
            "(state/run_history.json), so it can't be replaced; nothing was fetched"
        )


def _fetch_one(
    ctx: StageContext,
    store: Store,
    fetcher: Fetcher,
    c: Collector,
    settings: object,
    max_age: timedelta,
    raw_root: RawDir,
) -> SourceFetch:
    from tff_catalog.collectors.base import FetchContext
    from tff_catalog.store import RawDir, SnapshotExists, SnapshotFrozen

    day, refetch, log = ctx.run_date, ctx.options.refetch, ctx.log.getChild(c.name)
    frozen = ctx.state.frozen_dates(c.name)
    exists = store.snapshot(c.name, day) is not None
    if exists and not refetch:
        log.info("%s: the %s snapshot is already complete (--refetch replaces it)", c.name, day)
        return SourceFetch(c.name, "kept", day)
    if day in frozen:
        if exists:
            raise SnapshotFrozen(
                f"{c.name} {day}: a merged run used this date (state/run_history.json), "
                "so its snapshot can't be refetched"
            )
        snapshot, stale = _fallback(store, c.name, day, max_age)
        log.warning("%s: %s belongs to a merged run and has no snapshot; not fetched", c.name, day)
        return SourceFetch(c.name, "frozen", snapshot, stale=stale)
    previous = store.latest(c.name, day - timedelta(days=1))
    raw = RawDir(raw_root.path / c.name, keep=raw_root.keep)
    shutil.rmtree(raw.path, ignore_errors=True)  # left by an earlier --keep-raw run
    raw.path.mkdir(parents=True)
    try:
        with store.writer(c.name, day, c.version, refetch=refetch, frozen=frozen) as out:
            c.fetch(
                FetchContext(
                    run_date=day,
                    fetcher=fetcher.scoped(c.hosts),
                    out=out,
                    raw=raw,
                    previous=previous,
                    settings=settings,
                    log=log,
                    paths=ctx.paths,
                )
            )
    except SnapshotFrozen:
        raise
    except SnapshotExists:
        log.info("%s: the %s snapshot appeared meanwhile; keeping it", c.name, day)
        return SourceFetch(c.name, "kept", day)
    except Exception as exc:
        return _failed(c.name, day, store, max_age, exc, log)
    finally:
        raw.cleanup()
    log.info("%s: fetched the %s snapshot", c.name, day)
    return SourceFetch(c.name, "fetched", day)


def _fallback(store: Store, name: str, day: date, max_age: timedelta) -> tuple[date | None, bool]:
    """The snapshot the stale policy uses for ``day``, and whether it is stale."""
    snap = store.latest(name, day, max_age=max_age)
    if snap is None:
        return None, False
    return snap.date, snap.date < day


def _failed(
    name: str, day: date, store: Store, max_age: timedelta, exc: Exception, log: logging.Logger
) -> SourceFetch:
    """Report a failed fetch and the snapshot the stale policy falls back on."""
    error = f"{type(exc).__name__}: {exc}"
    snapshot, stale = _fallback(store, name, day, max_age)
    if snapshot is None:
        log.error(
            "%s: fetch failed (%s); no complete snapshot within %d days, so it drops out this run",
            name,
            error,
            max_age.days,
            exc_info=exc,
        )
    else:
        log.warning(
            "%s: fetch failed (%s); parse will use the %s snapshot%s",
            name,
            error,
            snapshot,
            ", flagged stale" if stale else "",
            exc_info=exc,
        )
    return SourceFetch(name, "failed", snapshot, stale=stale, error=error)


def _log_summary(log: logging.Logger, results: list[SourceFetch]) -> None:
    counts = {
        k: sum(r.outcome == k for r in results) for k in ("fetched", "kept", "frozen", "failed")
    }
    stale = sorted(r.source for r in results if r.stale)
    dropped = sorted(r.source for r in results if r.snapshot is None)
    log.info(
        "fetch: %d fetched, %d already complete, %d frozen, %d failed (stale: %s; dropped: %s)",
        counts["fetched"],
        counts["kept"],
        counts["frozen"],
        counts["failed"],
        ", ".join(stale) or "none",
        ", ".join(dropped) or "none",
    )


def run(ctx: StageContext) -> None:
    """Stage "fetch": snapshot every enabled collector (``--only`` narrows the list).

    For each collector: skip when ``<store>/<name>/<run_date>/`` is complete
    (unless ``--refetch``, which is refused for dates in ``state/run_history.json``);
    otherwise open a ``SnapshotWriter``, build a ``FetchContext`` with a scoped
    fetcher, call ``fetch()``, and close the writer. A failing collector is
    logged and left to the stale policy; the stage fails only if every one fails.
    """
    results = fetch_all(ctx)
    if results and all(r.outcome == "failed" for r in results):
        details = "; ".join(f"{r.source}: {r.error}" for r in results)
        raise FetchError(f"every source failed to fetch: {details}")
