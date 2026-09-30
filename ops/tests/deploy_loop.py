"""Probe a site during a deploy: every file must load, and no page may mix two releases.

    uv run python ops/tests/deploy_loop.py https://staging.trulyfreefonts.com \\
        --interval 0.1 --duration 120

Milestone 2 step 11's "Done when": run this while a deploy (or a rollback) happens; it must
end with ``errors=0 mixed=0``.

Every ``--interval`` seconds it starts one probe, in a thread pool so that slow responses
never stretch the interval:

1. GET the page with ``Accept: text/html``, as a browser sends it.
2. GET every same-origin file the page's script depends on: script, stylesheet, preloads,
   and the list and details JSON (``#list[data-index]``, ``[data-details]``); then
   ``--extras`` of its other files (specimen images, icons), taken in turn across probes.
3. GET ``/version.txt``, then the page again.

It counts:

- **errors**: a failed request, a status other than 200 (redirects included), or a page
  referencing no file it can check when an earlier page did.
- **mixed**: a hashed file (``name.<10 hex>.ext``, where the hex is the start of its sha256)
  whose bytes don't match its name; or a probe in which the page did not change but
  ``/version.txt`` names another commit than the page's list data (``commit`` in
  ``list.<h>.json``), so the server was serving two releases at once.
- **switches**: probes during which the page changed, meaning the deploy happened mid-probe.
  They are expected, and their version check is skipped.

The last line is ``probes=… requests=… errors=E mixed=M switches=… versions=…`` and the exit
status is 0 when E and M are both 0 (and, with ``--require-switch``, a switch was seen).
"""

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TextIO
from urllib.parse import urljoin, urlsplit

import httpx

HASHED_RE = re.compile(r"\.([0-9a-f]{10})\.[a-z0-9]+$")
USER_AGENT = "trulyfreefonts-deploy-loop/1 (+https://github.com/byronshock/trulyfreefonts)"
MAX_SAMPLES = 20


class _Refs(HTMLParser):
    """Collects the files a page references, split into core (every probe) and extra."""

    CORE_RELS = frozenset({"stylesheet", "preload", "modulepreload"})
    EXTRA_RELS = frozenset({"icon", "apple-touch-icon", "manifest", "mask-icon"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.core: list[str] = []
        self.extra: list[str] = []
        self.index: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v for k, v in attrs if v}
        if tag == "script" and "src" in a:
            self.core.append(a["src"])
        elif tag == "link" and "href" in a:
            rels = set(a.get("rel", "").lower().split())
            if rels & self.CORE_RELS:
                self.core.append(a["href"])
            elif rels & self.EXTRA_RELS:
                self.extra.append(a["href"])
        elif tag in ("img", "source") and "src" in a:
            self.extra.append(a["src"])
        if "data-index" in a:
            self.index = a["data-index"]
            self.core.append(a["data-index"])
        if "data-details" in a:
            self.core.append(a["data-details"])

    handle_startendtag = handle_starttag


@dataclass
class Stats:
    probes: int = 0
    requests: int = 0
    errors: int = 0
    mixed: int = 0
    switches: int = 0
    skipped: int = 0
    versions: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    def line(self) -> str:
        shown = ">".join(v[:12] for v in self.versions) or "-"
        return (
            f"probes={self.probes} requests={self.requests} errors={self.errors} "
            f"mixed={self.mixed} switches={self.switches} versions={shown}"
        )


def _same_origin(url: str, base: str) -> bool:
    a, b = urlsplit(url), urlsplit(base)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


class Loop:
    """The probe loop; ``run`` drives it, tests may call ``probe`` directly."""

    def __init__(self, base_url: str, *, extras: int = 4, client: httpx.Client | None = None):
        self.base = base_url.rstrip("/") + "/"
        self.extras = extras
        self.client = client or httpx.Client(
            timeout=10.0, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        )
        self.stats = Stats()
        self.lock = threading.Lock()
        self.turn = 0
        self.saw_files = False

    def _count(self, what: str, message: str) -> None:
        with self.lock:
            setattr(self.stats, what, getattr(self.stats, what) + 1)
            if len(self.stats.samples) < MAX_SAMPLES:
                self.stats.samples.append(f"{what}: {message}")

    def get(self, url: str, *, html: bool = False) -> httpx.Response | None:
        headers = {"Accept": "text/html,application/xhtml+xml"} if html else {}
        with self.lock:
            self.stats.requests += 1
        try:
            response = self.client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            self._count("errors", f"{url}: {type(exc).__name__}: {exc}")
            return None
        if response.status_code != 200:
            self._count("errors", f"{url}: HTTP {response.status_code}")
            return None
        return response

    def probe(self) -> None:
        with self.lock:
            self.stats.probes += 1
            turn = self.turn
            self.turn += 1
        first = self.get(self.base, html=True)
        if first is None:
            return
        refs = _Refs()
        refs.feed(first.text)
        core = list(dict.fromkeys(urljoin(self.base, r) for r in refs.core))
        extra = sorted({urljoin(self.base, r) for r in refs.extra} - set(core))
        core = [u for u in core if _same_origin(u, self.base)]
        extra = [u for u in extra if _same_origin(u, self.base)]
        if core:
            self.saw_files = True
        elif self.saw_files:
            self._count("errors", f"{self.base}: page references none of its files")
        if extra and self.extras:
            start = (turn * self.extras) % len(extra)
            extra = (extra + extra)[start : start + min(self.extras, len(extra))]
        else:
            extra = []
        index_url = urljoin(self.base, refs.index) if refs.index else None
        page_commit = None
        for url in core + extra:
            response = self.get(url)
            if response is None:
                continue
            m = HASHED_RE.search(urlsplit(url).path)
            if m and hashlib.sha256(response.content).hexdigest()[:10] != m.group(1):
                self._count("mixed", f"{url}: bytes don't match the hash in the name")
            if url == index_url:
                try:
                    page_commit = json.loads(response.content).get("commit")
                except ValueError, AttributeError:
                    self._count("errors", f"{url}: not a JSON object")
        version = self.get(urljoin(self.base, "version.txt"))
        served = None
        if version is not None:
            for line in version.text.splitlines():
                if line.startswith("commit="):
                    served = line.removeprefix("commit=").strip()
        second = self.get(self.base, html=True)
        if second is None:
            return
        with self.lock:
            if served and (not self.stats.versions or self.stats.versions[-1] != served):
                self.stats.versions.append(served)
        if second.content != first.content:
            with self.lock:
                self.stats.switches += 1
        elif page_commit and served and page_commit != served:
            self._count("mixed", f"page's list data is {page_commit}, version.txt is {served}")

    def run(
        self, *, interval: float, duration: float, workers: int = 16, log: TextIO | None = None
    ):
        start = time.monotonic()
        next_at = start
        next_report = start + 10.0
        inflight = threading.Semaphore(workers)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="probe") as pool:
            while (now := time.monotonic()) - start < duration:
                if now < next_at:
                    time.sleep(min(next_at - now, 0.05))
                    continue
                next_at += interval
                if not inflight.acquire(blocking=False):
                    self.stats.skipped += 1
                    continue
                future = pool.submit(self.probe)
                future.add_done_callback(lambda _f: inflight.release())
                if log is not None and now >= next_report:
                    next_report += 10.0
                    print(f"t={now - start:.0f}s {self.stats.line()}", file=log, flush=True)
        return self.stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", help="site base URL, e.g. https://staging.trulyfreefonts.com")
    parser.add_argument("--interval", type=float, default=0.1, help="seconds between probes")
    parser.add_argument("--duration", type=float, default=120.0, help="seconds to run")
    parser.add_argument("--extras", type=int, default=4, help="images and icons per probe")
    parser.add_argument("--workers", type=int, default=16, help="probes in flight at most")
    parser.add_argument(
        "--require-switch", action="store_true", help="fail unless the version changed"
    )
    ns = parser.parse_args(argv)
    if ns.interval <= 0 or ns.duration <= 0 or ns.workers < 1:
        parser.error("--interval, --duration and --workers must be positive")
    loop = Loop(ns.url, extras=ns.extras)
    with loop.client:
        stats = loop.run(
            interval=ns.interval, duration=ns.duration, workers=ns.workers, log=sys.stderr
        )
    for sample in stats.samples:
        print(f"  {sample}", file=sys.stderr)
    if stats.skipped:
        print(f"  {stats.skipped} probes skipped: all workers busy", file=sys.stderr)
    print(stats.line())
    if stats.errors or stats.mixed:
        return 1
    if ns.require_switch and len(set(stats.versions)) < 2:
        print("no switch seen: the version never changed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
