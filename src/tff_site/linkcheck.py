"""``tff-site linkcheck``: check that license texts and download links answer HTTP 200.

It visits each chosen font's ``license.text_url``, ``links.primary`` and ``links.designer``,
at most ``rate`` requests a second per host, following redirects. This is the Milestone 2
step 4 check for the owner's ten fonts; Milestone 1 step 14 checks every link monthly.

How it behaves:

- Each distinct URL is fetched once, even when several fonts share it (one OFL text), and
  every font that names it gets the result.
- Requests are ``GET`` (some servers refuse ``HEAD``), and only the status line and headers
  are read. Redirects are followed by hand, up to ``MAX_REDIRECTS``, so every hop counts
  against its own host's pace. A redirect to anything but ``https://``, a loop, a network
  error or a timeout gives status 0.
- Pacing is per host name: one request, then ``1 / rate`` seconds before the next to that
  host. Requests run one at a time, and the next one is always the one whose host is free
  soonest, so different hosts interleave and the run takes about as long as the busiest
  host needs.
- An id that isn't in the catalog gives one result with field ``"id"`` and status 0.
"""

import math
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from tff_site import data

MAX_REDIRECTS = 10
TIMEOUT_S = 20.0
USER_AGENT = "tff-site-linkcheck/1 (+https://trulyfreefonts.com)"
REDIRECTS = frozenset({301, 302, 303, 307, 308})
FIELDS = ("license.text_url", "links.primary", "links.designer")

# Seams for tests: the HTTP client and the clock (monotonic, never the wall clock).
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


def _client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=False,
        timeout=TIMEOUT_S,
        headers={"User-Agent": USER_AGENT},
    )


@dataclass(frozen=True, slots=True)
class LinkResult:
    """One checked link: the font, which field, the URL and the final HTTP status (0: failed)."""

    font_id: str
    field: str
    url: str
    status: int


def linkcheck(
    data_path: Path, *, ids: Iterable[str] | None = None, rate: float = 1.0
) -> list[LinkResult]:
    """Check the links of the fonts in ``ids`` (default: every font) and return the results."""
    if not isinstance(rate, int | float) or not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"rate must be a positive number of requests per second, not {rate!r}")
    doc = data.load(Path(data_path))
    fonts = {font["id"]: font for font in doc["fonts"]}
    if isinstance(ids, str):  # one id, not its characters
        ids = [ids]
    # "a, b" on the command line: the spaces are not part of the ids.
    wanted = list(fonts) if ids is None else list(dict.fromkeys(i.strip() for i in ids))
    wanted = [i for i in wanted if i]
    rows: list[tuple[str, str, str]] = []
    for font_id in wanted:
        font = fonts.get(font_id)
        if font is None:
            rows.append((font_id, "id", ""))
            continue
        rows.extend((font_id, field, url) for field, url in font_links(font))
    urls = list(dict.fromkeys(url for _, field, url in rows if field != "id"))
    statuses = check_urls(urls, rate=rate)
    return [
        LinkResult(font_id, field, url, 0 if field == "id" else statuses[url])
        for font_id, field, url in rows
    ]


def font_links(font: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield ``(field, url)`` for a font's license text, official page and designer page."""
    yield FIELDS[0], font["license"]["text_url"]
    yield FIELDS[1], font["links"]["primary"]["url"]
    designer = font["links"].get("designer")
    if designer:
        yield FIELDS[2], designer["url"]


@dataclass(slots=True)
class _Job:
    start: str
    url: str
    order: int
    seen: set[str]


def check_urls(urls: Iterable[str], *, rate: float = 1.0) -> dict[str, int]:
    """Return the final status of each URL (0: failed), pacing each host at ``rate``/second."""
    interval = 1.0 / rate
    statuses: dict[str, int] = {}
    jobs: list[_Job] = []
    for order, url in enumerate(dict.fromkeys(urls)):
        if _https(url):
            jobs.append(_Job(start=url, url=url, order=order, seen={url}))
        else:
            statuses[url] = 0
    free_at: dict[str, float] = {}
    with _client() as client:
        while jobs:
            job = min(jobs, key=lambda j: (free_at.get(_host(j.url), -math.inf), j.order))
            wait = free_at.get(_host(job.url), -math.inf) - _monotonic()
            if wait > 0:
                _sleep(wait)
            free_at[_host(job.url)] = _monotonic() + interval
            status, location = _fetch(client, job.url)
            if status in REDIRECTS and location is not None:
                target = _follow(job, location)
                if target is not None:
                    job.url = target
                    job.seen.add(target)
                    continue
                status = 0
            statuses[job.start] = status
            jobs.remove(job)
    return statuses


def _fetch(client: httpx.Client, url: str) -> tuple[int, str | None]:
    """Return the status and ``Location`` of one GET, without reading the body."""
    try:
        with client.stream("GET", url) as response:
            return response.status_code, response.headers.get("location")
    except httpx.HTTPError, httpx.InvalidURL, ValueError:
        return 0, None


def _follow(job: _Job, location: str) -> str | None:
    """The next hop of a redirect, or None for a loop, too many hops or a non-https target."""
    try:
        target = str(httpx.URL(job.url).join(location))
    except httpx.InvalidURL, ValueError:
        return None
    if not _https(target) or target in job.seen or len(job.seen) > MAX_REDIRECTS:
        return None
    return target


def _https(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL, ValueError, TypeError:
        return False
    return parsed.scheme == "https" and bool(parsed.host)


def _host(url: str) -> str:
    return httpx.URL(url).host.lower()
