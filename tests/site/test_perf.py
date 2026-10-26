"""Performance budget (Milestone 2 step 10; design-m2 §6) and ``tff-site check``.

**Budgets without a browser.** ``tff_site.budgets.check`` (the ``tff-site check`` command):
the sample site and a 540-font site built from ``tests/fixtures/make_large_catalog.py`` pass
every size budget and the CSP lint, and each check fails on a site made to break it.

**The list page in Chromium** (browser tests; Chromium only, since the throttling is CDP):

- Load: slow 4G (Lighthouse's numbers: 562.5 ms latency, 1.6 Mbps down at 90%, 750 kbps up
  at 90%) and the CPU slowed 4x, a fresh context per run, five runs; the median LCP must be
  at most 2.5 s, CLS at most 0.1 and TBT at most 200 ms, at a phone and a desktop size. LCP,
  layout shifts (session windows, without recent input) and long tasks come from
  PerformanceObservers that an init script starts before the page's own script; TBT is the
  time past 50 ms of each long task after first contentful paint, counted until the page
  has been quiet for two seconds after load.
- Refilter: 20 rank and filter changes made as a visitor would, the same ones in the same
  order every time, under the same slowdown, each timed from its change event to the second
  animation frame after it; the list must then show what the count says. A set in which
  every change takes at most 200 ms passes. Otherwise two more sets are measured, and each
  change is judged by its median over the three: every median must be at most 200 ms. One
  stall of a shared runner can't move a median, a change that is really slow is slow in
  every set, and a page that is slower overall is slow at every change. Planted slowdowns
  show the rule at work in the browser: a stall planted in the first set alone is outvoted
  by the two sets after it, and a change made slow in every set fails, named. What the rule
  gives up, knowingly: a change that sits at the budget on a slow runner (over in about half
  its sets) fails a little more often than under the old rule of two sets, since a median
  over 200 ms is the page at its limit there; and a page that is slow on one change per
  visit, at a different change each time, passes, since no change's median moves.
- Specimens: at load, no specimen is requested for a row more than 1000 px below the screen.
- The hopper (Milestone 3 steps 4 and 5), each run in a fresh context (nothing compiled yet,
  as for a visitor's first list), the matcher module as the build writes it, with the match
  file the site build makes from the committed ``build/names.json`` and four realistic lists
  of 10,000 fonts, one with a name past ASCII on every line (``tests/fixtures/hopper_lists.py``).
  Under the same CPU slowdown, reading the match file, readying its table and matching must
  take under 200 ms (step 5), and parsing 5 MB of ``system_profiler -json`` cut short under a
  second (step 4). Without a slowdown, as Lighthouse's desktop setting runs (the hopper needs a
  computer), parsing and matching together must take under 200 ms. Each is the median of five
  runs, with one more set when it fails. These need no built site.
- Those budgets are for the machine they were set on, where Lighthouse's 4x slowdown stands
  for a mid-range phone. CI's runners are about three times slower (the refilter budget's CI
  failure of 2026-10-06), so 4x there is a phone three times slower again, and the list past
  ASCII, which takes the port's slower road on every line, took 223 ms slowed 12x here. So
  each hopper run is paired with a run of ``REFERENCE_JS``, a fixed workload that shares no
  code with the site, cold and at the same slowdown, and the budget is scaled by how much
  slower the machine runs it than ``REFERENCE_MS`` (the machine the budgets were set on), as
  Lighthouse's benchmark index adjusts its CPU multiplier. The budget is never scaled below
  step 5's, and a machine more than ``FACTOR_MAX`` times slower fails rather than stretch it
  further. Here the factor is 1, and the four lists take at most 74 ms of the 200 under the
  slowdown, so each keeps a margin of at least 2.7 on any machine the factor reads right.
- The list past ASCII is also held to the ASCII fc-list's time, the two run in turn: at most
  ``PAST_ASCII_MATCH_RATIO`` times its median under the slowdown (1.6 to 1.8 here, at 4x and
  12x) and ``PAST_ASCII_WAIT_RATIO`` times without one (1.5 to 1.6, at 1x and 3x). That is a
  guard against a regression on the road past ASCII alone, which made such lists 3 to 6
  times slower before 2026-10-23, and which the scaled budget would let pass up to 2.7 times;
  it is no budget of its own.

The site is ``TFF_PERF_SITE_DIR`` when that is set (CI's 540-font build, or a real
catalog's build), else a 540-font site this module builds from make_large_catalog. It is
never guessed from ``TFF_SITE_DIR``: the deploy tests set that to whatever build they check.
When ``TFF_CADDY=1`` and the perf site is the one Caddy serves (``TFF_SITE_DIR``), Caddy
serves it with compression; otherwise this module serves it gzip-compressed with the
production headers (``tff-site serve`` never compresses, and the budgets assume
compression).

**A deployed site** (by hand, for step 10's done-when): ``test_deployed_*`` run the load and
refilter budgets, with the same slowdown, against ``TFF_PERF_URL`` (an https URL), through
whatever is in front of it (Cloudflare, on the test site). They are marked ``network`` and skip
without ``TFF_PERF_URL``, which no CI job sets::

    TFF_PERF_URL=https://staging.purelyfreefonts.com uv run --group browser pytest \\
        tests/site/test_perf.py -k deployed --browser chromium -s
"""

import functools
import gzip
import json
import math
import os
import re
import statistics
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import unquote, urlsplit

import numpy as np
import pytest
from tests.fixtures import hopper_lists

from tff_catalog import owned
from tff_site import assets, budgets, cli, data, matchfile

ROOT = Path(__file__).resolve().parents[2]
FAKE_COMMIT = "0" * 40
CI_CADDY_URL = "http://127.0.0.1:8080"
LARGE_FONTS = 540
MIN_LARGE_FONTS = 500  # a site this module builds; an explicit one is measured at its own size
PERF_SITE_ENV = "TFF_PERF_SITE_DIR"
PERF_URL_ENV = "TFF_PERF_URL"
RUNS = 5
LCP_MAX_MS = 2500
CLS_MAX = 0.1
TBT_MAX_MS = 200
REFILTER_MAX_MS = 200
REFILTER_STEPS = 20
REFILTER_SETS = 3  # when the first set has a change over budget: that set and two more
FAR_BELOW_PX = 1000
QUIET_MS = 2000
LOAD_TIMEOUT_MS = 60_000
# Lighthouse's "Slow 4G" (mobileSlow4G): 150 ms RTT x 3.75, 1.6 Mbps x 0.9, 750 kbps x 0.9.
SLOW_4G = {
    "offline": False,
    "latency": 562.5,
    "downloadThroughput": 188743.68,
    "uploadThroughput": 86400,
}
CPU_SLOWDOWN = 4
VIEWPORTS = {
    "phone": {"viewport": {"width": 412, "height": 823}, "is_mobile": True, "has_touch": True},
    "desktop": {"viewport": {"width": 1280, "height": 900}},
}
ROW = re.compile(r'<li class="font[" ]')
COUNT = re.compile(r"Showing ([\d,]+) of")

# Run before any page script (outside the CSP): the observers that measure the load, and a
# counter for 'tff:list-ready'.
PERF_JS = """
(() => {
  const m = { lcp: null, lcpNode: '', shifts: [], longtasks: [], fcp: null, errors: [] };
  Object.defineProperty(window, '__tffPerf', { value: m });
  window.__tffReady = 0;
  document.addEventListener('tff:list-ready', () => { window.__tffReady += 1; });
  const name = (n) => !n ? '' : `${(n.tagName || n.nodeName).toLowerCase()}` +
    `${n.id ? '#' + n.id : ''}${n.classList && n.classList.length ? '.' + [...n.classList].join('.') : ''}`;
  const watch = (type, fn) => {
    try {
      new PerformanceObserver((list) => { for (const e of list.getEntries()) fn(e); })
        .observe({ type, buffered: true });
    } catch (error) {
      m.errors.push(`${type}: ${error}`);
    }
  };
  watch('largest-contentful-paint', (e) => { m.lcp = e.startTime; m.lcpNode = name(e.element); });
  watch('layout-shift', (e) => {
    if (!e.hadRecentInput) {
      m.shifts.push({ t: e.startTime, v: e.value, from: (e.sources || []).map((s) => name(s.node)) });
    }
  });
  watch('longtask', (e) => { m.longtasks.push([e.startTime, e.duration]); });
  watch('paint', (e) => { if (e.name === 'first-contentful-paint') m.fcp = e.startTime; });
})();
"""
STATE_JS = """
() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const m = window.__tffPerf;
  return {
    now: performance.now(),
    load: nav ? nav.loadEventEnd : 0,
    lastTask: Math.max(0, ...m.longtasks.map(([s, d]) => s + d)),
    ready: window.__tffReady,
  };
}
"""
# Times every change event from its creation to the second frame after it.
# A redraw starts with a control's change event, or with a click on a sort button over the
# list (sort_header, 2026-09-30), which fires no change event.
REFILTER_JS = """
(() => {
  window.__tffRefilter = [];
  const record = (target, t0) => requestAnimationFrame(() => requestAnimationFrame(() => {
    window.__tffRefilter.push({
      ms: performance.now() - t0,
      target,
      count: document.getElementById('count').textContent,
      rows: document.querySelectorAll('#list > li.font').length,
      hash: location.hash,
    });
  }));
  document.addEventListener('change', (e) => record(e.target.id, e.timeStamp), true);
  document.addEventListener('click', (e) => {
    const button = e.target.closest('[data-sort]');
    if (button) record(button.id, e.timeStamp);
  }, true);
})();
"""


# ------------------------------------------------------------------------ the large site


def _rows_in(site: Path) -> int:
    page = site / "index.html"
    return len(ROW.findall(page.read_text(encoding="utf-8"))) if page.is_file() else 0


@pytest.fixture(scope="module")
def large_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The site to measure: ``TFF_PERF_SITE_DIR`` if set, else a 540-font site built here."""
    given = os.environ.get(PERF_SITE_ENV)
    if given:
        if _rows_in(Path(given)) == 0:
            pytest.fail(f"{PERF_SITE_ENV}={given} is not a built site with a list")
        return Path(given)
    from tests.fixtures import make_large_catalog

    from tff_site import build

    root = tmp_path_factory.mktemp("large")
    catalog = make_large_catalog.write(root / "data", fonts=LARGE_FONTS)
    build.build(catalog, root / "site", commit=FAKE_COMMIT, allow_dirty=True, font_files=False)
    return root / "site"


class _GzipHandler(BaseHTTPRequestHandler):
    """Static files, gzip-compressed like Caddy's ``encode``, with site.caddy's headers."""

    protocol_version = "HTTP/1.1"
    compressible: ClassVar[frozenset[str]] = frozenset(
        {".html", ".css", ".js", ".json", ".svg", ".txt", ".xml"}
    )

    def __init__(self, *args: Any, root: Path, headers: Any, cache: dict, **kwargs: Any) -> None:
        self.root, self.caddy, self.cache = root, headers, cache
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        self._serve(send_body=True)

    def do_HEAD(self) -> None:
        self._serve(send_body=False)

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _serve(self, *, send_body: bool) -> None:
        from tff_site import serve

        path = unquote(urlsplit(self.path).path)
        target = (self.root / path.lstrip("/")).resolve()
        if target.is_dir():
            target = target / "index.html"
        status = HTTPStatus.OK
        if self.root not in target.parents or not target.is_file():
            target, status = self.root / "404.html", HTTPStatus.NOT_FOUND
        gz = "gzip" in self.headers.get("Accept-Encoding", "") and (
            target.suffix in self.compressible
        )
        key = (target, gz)
        if key not in self.cache:
            blob = target.read_bytes()
            self.cache[key] = gzip.compress(blob, compresslevel=5, mtime=0) if gz else blob
        body = self.cache[key]
        self.send_response(status)
        self.send_header("Content-Type", serve.CONTENT_TYPES.get(target.suffix, "text/plain"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept-Encoding")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        for name, value in self.caddy.security.items():
            self.send_header(name, value)
        immutable = path.startswith("/assets/") and status == HTTPStatus.OK
        for name, value in (self.caddy.immutable if immutable else self.caddy.revalidate).items():
            self.send_header(name, value)
        self.end_headers()
        if send_body:
            self.wfile.write(body)


@pytest.fixture(scope="module")
def large_rows(large_site: Path) -> int:
    """How many fonts the measured site lists (at least MIN_LARGE_FONTS for one built here)."""
    return _rows_in(large_site)


@pytest.fixture(scope="module")
def large_url(large_site: Path) -> Iterator[str]:
    """Base URL of the served perf site, compressed: Caddy's when it serves this site."""
    served = os.environ.get("TFF_SITE_DIR")
    if (
        os.environ.get("TFF_CADDY") == "1"
        and served
        and Path(served).resolve() == large_site.resolve()
    ):
        yield CI_CADDY_URL
        return
    from tff_site import serve

    headers = serve.parse_headers(serve.SITE_CADDY.read_text(encoding="utf-8"))
    handler = partial(_GzipHandler, root=large_site.resolve(), headers=headers, cache={})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, name="perf-gzip", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ----------------------------------------------------------------------- measuring a load


@dataclass(frozen=True)
class Load:
    lcp: float
    cls: float
    tbt: float
    fcp: float
    lcp_node: str
    shifts: list[dict[str, Any]]
    longtasks: list[list[float]]


def cls_of(shifts: list[dict[str, Any]]) -> float:
    """The largest session window: shifts less than 1 s apart, at most 5 s long."""
    best = total = 0.0
    start = last = None
    for shift in sorted(shifts, key=lambda s: s["t"]):
        t = shift["t"]
        if start is None or t - last >= 1000 or t - start >= 5000:
            start, total = t, 0.0
        total += shift["v"]
        last = t
        best = max(best, total)
    return best


def tbt_of(longtasks: list[list[float]], fcp: float) -> float:
    """Total blocking time after FCP: each long task's time past its first 50 ms that falls
    after FCP. A task that spans FCP counts at least as much as in Lighthouse, which takes
    50 ms off the part after FCP instead."""
    total = 0.0
    for start, duration in longtasks:
        end = start + duration
        clipped_start = max(start, fcp)
        if end - clipped_start < 50:
            continue
        total += end - max(start + 50, clipped_start)
    return total


def _chromium(browser_name: str) -> None:
    if browser_name != "chromium":
        pytest.skip("CPU and network throttling use the Chrome DevTools Protocol")


def _throttled_page(browser: Any, url: str, init_js: str, **context_args: Any) -> tuple[Any, Any]:
    context = browser.new_context(base_url=url, **context_args)
    context.add_init_script(init_js)
    page = context.new_page()
    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
    cdp.send("Network.emulateNetworkConditions", SLOW_4G)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU_SLOWDOWN})
    return context, page


def _wait_quiet(page: Any, timeout_ms: int = LOAD_TIMEOUT_MS) -> None:
    """Wait for load, the list to go live, and QUIET_MS without a long task after both."""
    waited = 0
    while True:
        s = page.evaluate(STATE_JS)
        if (
            s["load"] > 0
            and s["ready"] >= 1
            and s["now"] - max(s["load"], s["lastTask"]) >= QUIET_MS
        ):
            return
        if waited > timeout_ms:
            raise AssertionError(f"the page never went quiet: {s}")
        page.wait_for_timeout(250)
        waited += 250


def measure_load(browser: Any, url: str, context_args: dict[str, Any], want_rows: int) -> Load:
    context, page = _throttled_page(browser, url, PERF_JS, **context_args)
    try:
        page.goto("/", wait_until="load", timeout=LOAD_TIMEOUT_MS)
        _wait_quiet(page)
        rows = page.locator("#list > li.font").count()
        assert rows >= want_rows, f"the served list has {rows} fonts, not the {want_rows} measured"
        m = page.evaluate("() => window.__tffPerf")
    finally:
        context.close()
    assert m["errors"] == [], m["errors"]
    assert m["fcp"] is not None, "no first-contentful-paint entry"
    assert m["lcp"] is not None, "no largest-contentful-paint entry"
    return Load(
        lcp=m["lcp"],
        cls=cls_of(m["shifts"]),
        tbt=tbt_of(m["longtasks"], m["fcp"]),
        fcp=m["fcp"],
        lcp_node=m["lcpNode"],
        shifts=m["shifts"],
        longtasks=m["longtasks"],
    )


# ----------------------------------------------------------------- the browser tests


def _load_verdict(loads: list[Load], size: str) -> tuple[dict[str, Any], list[str]]:
    """The medians of some loads, and what breaks the budget (nothing when it passes)."""
    lcp = statistics.median(load.lcp for load in loads)
    cls = statistics.median(load.cls for load in loads)
    tbt = statistics.median(load.tbt for load in loads)
    summary = {
        "size": size,
        "median": {"lcp_ms": round(lcp), "cls": round(cls, 4), "tbt_ms": round(tbt)},
        "runs": [
            {
                "lcp_ms": round(x.lcp),
                "fcp_ms": round(x.fcp),
                "cls": round(x.cls, 4),
                "tbt_ms": round(x.tbt),
                "lcp_node": x.lcp_node,
                "long_tasks": len(x.longtasks),
            }
            for x in loads
        ],
    }
    failures = []
    if lcp > LCP_MAX_MS:
        failures.append(f"median LCP {lcp:.0f} ms")
    if cls > CLS_MAX:
        worst_shifts = max(loads, key=lambda x: x.cls).shifts
        failures.append(f"median CLS {cls:.3f}; shifts of the worst run: {worst_shifts}")
    if tbt > TBT_MAX_MS:
        failures.append(f"median TBT {tbt:.0f} ms")
    return summary, failures


def _assert_load_budget(browser: Any, url: str, rows: int, size: str) -> None:
    summary, failures = {}, ["not measured"]
    # Shared CI runners are noisy: a failing median gets one more set of runs (design-m2 §9).
    for _attempt in range(2):
        loads = [measure_load(browser, url, VIEWPORTS[size], rows) for _ in range(RUNS)]
        summary, failures = _load_verdict(loads, size)
        print(json.dumps(summary))
        if not failures:
            break
    assert failures == [], f"{failures}: {summary}"


@pytest.mark.parametrize("size", VIEWPORTS)
def test_load_meets_the_web_vitals_budget(
    browser: Any, browser_name: str, large_url: str, large_rows: int, size: str
) -> None:
    _chromium(browser_name)
    _assert_load_budget(browser, large_url, large_rows, size)


def _refilter_steps(ranks: list[str]) -> list[tuple[str, str, str | None]]:
    """20 changes: every rank in turn, then each filter group, then a rank again. Each one
    changes the view, so each must redraw. The page opens on the default rank
    (``data.DEFAULT_VIEW``, Projects), so it comes last in the first round."""
    default = data.DEFAULT_VIEW
    others = [r for r in ranks if r != default]
    steps: list[tuple[str, str, str | None]] = [("select", "#f-rank", r) for r in others]
    steps.append(("select", "#f-rank", default))
    steps += [
        ("check", "#f-cat-serif", None),
        ("check", "#f-cat-sans-serif", None),
        ("check", "#f-cat-all", None),
        ("check", "#f-cat-monospace", None),
        ("check", "#f-cat-display", None),
        ("check", "#f-cat-all", None),
        ("check", "#f-var", None),
        ("uncheck", "#f-var", None),
        ("check", "#f-hide-limited", None),
        ("check", "#f-nerd", None),
        ("select", "#f-os", "windows"),
        ("click", "#sort-name", None),
        ("click", "#sort-rank", None),
    ]
    k = 0
    while len(steps) < REFILTER_STEPS:
        steps.append(("select", "#f-rank", others[k % len(others)] if others else default))
        k += 1
    return steps[:REFILTER_STEPS]


def _refilter_once(
    browser: Any, url: str, want_rows: int, extra_js: str = ""
) -> tuple[list[tuple[str, str, str | None]], list]:
    """Load the list under the slowdown, make the 20 changes, and return the steps and what
    REFILTER_JS recorded for each. ``extra_js`` runs after REFILTER_JS, before the page's
    own script: only the planted tests below use it."""
    init_js = PERF_JS + REFILTER_JS + extra_js
    context, page = _throttled_page(browser, url, init_js, **VIEWPORTS["desktop"])
    try:
        page.goto("/", wait_until="load", timeout=LOAD_TIMEOUT_MS)
        _wait_quiet(page)
        assert page.locator("#list > li.font").count() >= want_rows
        ranks = page.eval_on_selector_all("#f-rank option", "(os) => os.map((o) => o.value)")
        steps = _refilter_steps(ranks)
        for k, (action, selector, value) in enumerate(steps, start=1):
            if action == "select":
                page.select_option(selector, value)
            elif action == "check":
                page.check(selector)
            elif action == "click":
                page.click(selector)
            else:
                page.uncheck(selector)
            for _ in range(200):
                if page.evaluate("window.__tffRefilter.length") >= k:
                    break
                page.wait_for_timeout(10)
        return steps, page.evaluate("window.__tffRefilter")
    finally:
        context.close()


def _check_refilter_records(steps: list[tuple[str, str, str | None]], timings: list) -> None:
    """Each record belongs to its step, and the page had redrawn when the clock stopped: the
    view in the URL changed, and the list shows what the count says."""
    assert len(timings) == REFILTER_STEPS, timings
    hash_before = ""
    for (_, selector, _), t in zip(steps, timings, strict=True):
        assert t["target"] == selector.removeprefix("#"), (selector, t)
        assert t["hash"] != hash_before, f"the change to {selector} left the view as it was: {t}"
        hash_before = t["hash"]
        shown = COUNT.match(t["count"])
        assert shown, t
        assert int(shown.group(1).replace(",", "")) == t["rows"], f"count and list differ: {t}"
    assert timings[-1]["hash"].startswith("#rank="), timings[-1]


def _step_label(k: int, step: tuple[str, str, str | None]) -> str:
    """A step as a failure names it: ``step 9 (check #f-hide-limited)``."""
    action, selector, value = step
    return f"step {k} ({action} {selector}{f' {value}' if value is not None else ''})"


def _refilter_verdict(
    sets: Sequence[Sequence[float]], labels: Sequence[str] | None = None
) -> tuple[bool, list[float], list[str]]:
    """Judge sets of refilter timings (ms per step, the same steps in the same order in every
    set): whether they pass, each step's median over the sets, and a line for each step whose
    median is over REFILTER_MAX_MS, naming it (by ``labels`` if given) with its timings.

    A first set alone passes only if every step is within budget; three sets pass if every
    step's median is. Any other number of sets, sets of different lengths, or a timing that
    is not a finite number of at least 0 is a mistake in the caller, and raises ValueError.
    """
    if len(sets) not in (1, REFILTER_SETS):
        raise ValueError(f"{len(sets)} sets: judge a first set alone or {REFILTER_SETS} sets")
    steps = len(sets[0])
    if steps == 0 or any(len(s) != steps for s in sets):
        raise ValueError(f"sets of different lengths, or empty: {[len(s) for s in sets]}")
    if labels is not None and len(labels) != steps:
        raise ValueError(f"{len(labels)} labels for {steps} steps")
    for s in sets:
        for ms in s:
            if isinstance(ms, bool) or not isinstance(ms, int | float) or not math.isfinite(ms):
                raise ValueError(f"not a timing: {ms!r}")
            if ms < 0:
                raise ValueError(f"a negative timing: {ms!r}")
    medians = [float(statistics.median(column)) for column in zip(*sets, strict=True)]
    failures = [
        f"{labels[k] if labels else f'step {k + 1}'}: "
        f"{', '.join(f'{ms:.1f}' for ms in column)} ms, median {median:.1f} ms"
        for k, (column, median) in enumerate(zip(zip(*sets, strict=True), medians, strict=True))
        if median > REFILTER_MAX_MS
    ]
    return not failures, medians, failures


def _refilter_sets(measure: Callable[[int], list[float]]) -> list[list[float]]:
    """``measure(0)``, the first set, and if any step of it is over budget, two more sets
    (``measure(1)``, ``measure(2)``): the sets ``_refilter_verdict`` then judges."""
    sets = [measure(0)]
    if not _refilter_verdict(sets)[0]:
        sets += [measure(k) for k in range(1, REFILTER_SETS)]
    return sets


def _measure_refilter(
    browser: Any, url: str, rows: int, extra_js: Sequence[str] = ()
) -> tuple[list[tuple[str, str, str | None]], list[list[float]]]:
    """Measure the sets ``_refilter_sets`` asks for, check every set's records, print every
    set's timings, and return the steps and each set's timings (ms). ``extra_js`` is an extra
    init script for each set in turn (the planted tests' slowdowns); the real tests pass none."""
    first: list[tuple[str, str, str | None]] = []

    def measure(n: int) -> list[float]:
        steps, timings = _refilter_once(
            browser, url, rows, extra_js[n] if n < len(extra_js) else ""
        )
        _check_refilter_records(steps, timings)
        # Medians per step are fair only if every set makes the same changes.
        assert not first or steps == first, f"set {n + 1} made other changes: {steps}"
        first[:] = steps
        worst = max(timings, key=lambda t: t["ms"])
        print(
            json.dumps(
                {"set": n + 1, "refilter_ms": [round(t["ms"]) for t in timings], "worst": worst}
            )
        )
        return [t["ms"] for t in timings]

    sets = _refilter_sets(measure)
    return first, sets


def _assert_refilter_verdict(
    steps: list[tuple[str, str, str | None]], sets: list[list[float]]
) -> None:
    """Fail if the sets break the budget, naming each step over it by its number and change."""
    labels = [_step_label(k, step) for k, step in enumerate(steps, start=1)]
    passed, medians, failures = _refilter_verdict(sets, labels)
    if len(sets) > 1:
        print(json.dumps({"refilter_median_ms": [round(m) for m in medians]}))
    assert passed, (
        f"over the {REFILTER_MAX_MS} ms budget at the median of {len(sets)} sets: "
        + "; ".join(failures)
    )


def _assert_refilter_budget(
    browser: Any, url: str, rows: int, extra_js: Sequence[str] = ()
) -> None:
    """Measure the refilter budget and fail if it is broken."""
    # Shared CI runners are noisy and stall now and then (design-m2 §9). A first set with
    # every change within budget passes. If a change is over, two more sets are measured and
    # each change is judged by its median over the three: one stall can't move a median, but
    # a change that is really slow is slow in every set.
    _assert_refilter_verdict(*_measure_refilter(browser, url, rows, extra_js))


def test_rank_and_filter_changes_redraw_within_200_ms(
    browser: Any, browser_name: str, large_url: str, large_rows: int
) -> None:
    _chromium(browser_name)
    _assert_refilter_budget(browser, large_url, large_rows)


# Planted before the page's own script, after REFILTER_JS: a capture-phase listener that holds
# the main thread on the change to PLANTED_STEP (made once per set), as a stall or a slow
# redraw would. PLANTED_STALL_MS is far past any stall a runner has shown (406 ms at most),
# so a set it reached can't be mistaken for one it didn't.
PLANTED_STEP = "#f-hide-limited"
PLANTED_STALL_MS = 1500
PLANTED_SLOW_MS = 300


def _planted_js(ms: int) -> str:
    """An init script that holds the main thread for ``ms`` on the change to PLANTED_STEP."""
    return """
(() => {
  let held = false;
  document.addEventListener('change', (e) => {
    if (held || e.target.id !== 'PLANTED_ID') return;
    held = true;
    const t0 = performance.now();
    while (performance.now() - t0 < HOLD_MS) { /* hold the main thread */ }
  }, true);
})();
""".replace("HOLD_MS", str(ms)).replace("PLANTED_ID", PLANTED_STEP[1:])


def test_one_planted_stall_in_the_first_set_is_outvoted(
    browser: Any, browser_name: str, large_url: str, large_rows: int
) -> None:
    # The first set fails at the planted step, so two more are measured, and that step's
    # median is a timing from an unplanted set. Only that is asserted, not the budget: the
    # real test judges the budget, and a natural stall at the planted step in set 2 or 3
    # would fail it here.
    _chromium(browser_name)
    steps, sets = _measure_refilter(browser, large_url, large_rows, [_planted_js(PLANTED_STALL_MS)])
    k = [selector for _, selector, _ in steps].index(PLANTED_STEP)
    assert len(sets) == REFILTER_SETS, sets
    assert sets[0][k] >= PLANTED_STALL_MS, sets[0]
    assert max(sets[1][k], sets[2][k]) < PLANTED_STALL_MS, sets  # the plant stayed in set 1
    assert _refilter_verdict(sets)[1][k] == max(sets[1][k], sets[2][k])


def test_the_same_slow_step_in_every_set_fails(
    browser: Any, browser_name: str, large_url: str, large_rows: int
) -> None:
    _chromium(browser_name)
    planted = [_planted_js(PLANTED_SLOW_MS)] * REFILTER_SETS
    steps, sets = _measure_refilter(browser, large_url, large_rows, planted)
    k = [selector for _, selector, _ in steps].index(PLANTED_STEP)
    assert len(sets) == REFILTER_SETS, sets
    with pytest.raises(AssertionError, match="budget at the median of 3 sets") as caught:
        _assert_refilter_verdict(steps, sets)
    assert f"step {k + 1} (check {PLANTED_STEP}): " in str(caught.value), caught.value


# The rule without a browser. Sets of timings in ms: PR #96's two sets on CI (run
# 40281855328), each failed by one stall at a different step, and a third without a stall,
# the faster of the two at each step.
CI_SET_1 = [101, 86, 47, 79, 87, 54, 71, 70, 49, 35, 68, 406, 106, 116, 48, 37, 42, 38, 34, 34]
CI_SET_2 = [99, 82, 46, 74, 96, 53, 85, 75, 53, 34, 74, 70, 76, 69, 39, 30, 43, 38, 309, 30]
QUIET_SET = [min(a, b) for a, b in zip(CI_SET_1, CI_SET_2, strict=True)]


def _set_with(slow: dict[int, float], base: float = 50.0) -> list[float]:
    """A set of REFILTER_STEPS timings of ``base`` ms, except the 1-based steps in ``slow``."""
    return [slow.get(k, base) for k in range(1, REFILTER_STEPS + 1)]


def test_refilter_verdict_one_stall_passes() -> None:
    # Either CI set alone fails, and names its stall; with a third set, neither stall moves
    # its step's median.
    assert _refilter_verdict([CI_SET_1]) == (
        False,
        [float(ms) for ms in CI_SET_1],
        ["step 12: 406.0 ms, median 406.0 ms"],
    )
    assert _refilter_verdict([CI_SET_2])[2] == ["step 19: 309.0 ms, median 309.0 ms"]
    passed, medians, failures = _refilter_verdict([CI_SET_1, CI_SET_2, QUIET_SET])
    assert (passed, failures) == (True, [])
    assert (medians[11], medians[18]) == (70, 34)
    # One stall anywhere among the three sets passes, at any step and in any set.
    for k in range(1, REFILTER_STEPS + 1):
        for n in range(REFILTER_SETS):
            sets = [_set_with({k: 5000} if m == n else {}) for m in range(REFILTER_SETS)]
            assert _refilter_verdict(sets)[0], (k, n)


def test_refilter_verdict_the_same_slow_step_in_two_or_three_sets_fails() -> None:
    labels = [f"step {k} (change {k})" for k in range(1, REFILTER_STEPS + 1)]
    two = [_set_with({7: 300}), _set_with({7: 250}), _set_with({})]
    assert _refilter_verdict(two, labels) == (
        False,
        _set_with({7: 250}),
        ["step 7 (change 7): 300.0, 250.0, 50.0 ms, median 250.0 ms"],
    )
    three = [_set_with({7: 300, 2: 900}), _set_with({7: 210}), _set_with({7: 205})]
    passed, medians, failures = _refilter_verdict(three, labels)
    assert (passed, medians[6], medians[1]) == (False, 210, 50)
    assert failures == ["step 7 (change 7): 300.0, 210.0, 205.0 ms, median 210.0 ms"]
    # Two stalls at different steps pass; at the same step they fail, as a slow change does.
    assert _refilter_verdict([_set_with({3: 400}), _set_with({4: 400}), _set_with({})])[0]


def test_refilter_verdict_everything_slower_fails_every_step() -> None:
    slow = [_set_with({}, base=201), _set_with({}, base=260), _set_with({}, base=230)]
    passed, medians, failures = _refilter_verdict(slow)
    assert not passed
    assert medians == [230.0] * REFILTER_STEPS
    assert len(failures) == REFILTER_STEPS
    assert failures[0] == "step 1: 201.0, 260.0, 230.0 ms, median 230.0 ms"
    # The budget itself passes: "within 200 ms" is at most 200.
    assert _refilter_verdict([_set_with({}, base=REFILTER_MAX_MS)])[0]
    assert _refilter_verdict([_set_with({5: REFILTER_MAX_MS + 0.1})])[2] == [
        "step 5: 200.1 ms, median 200.1 ms"
    ]


def test_refilter_sets_measures_more_only_after_a_slow_first_set() -> None:
    def measuring(results: list[list[float]]) -> tuple[Callable[[int], list[float]], list]:
        calls: list[int] = []

        def measure(n: int) -> list[float]:
            calls.append(n)
            return results[n]

        return measure, calls

    measure, calls = measuring([_set_with({})])
    assert _refilter_sets(measure) == [_set_with({})]
    assert calls == [0]  # a passing first set needs no more
    results = [_set_with({12: 406}), _set_with({}), _set_with({})]
    measure, calls = measuring(results)
    assert _refilter_sets(measure) == results
    assert calls == [0, 1, 2]
    assert _refilter_verdict(results)[0]


@pytest.mark.parametrize(
    ("sets", "labels", "words"),
    [
        ([], None, "0 sets"),
        ([_set_with({}), _set_with({})], None, "2 sets"),
        ([_set_with({})] * 4, None, "4 sets"),
        ([[]], None, "empty"),
        ([_set_with({}), _set_with({}), _set_with({})[:-1]], None, "different lengths"),
        ([_set_with({1: math.nan})], None, "not a timing"),
        ([_set_with({1: math.inf})], None, "not a timing"),
        ([_set_with({1: "50"})], None, "not a timing"),  # type: ignore[dict-item]
        ([_set_with({1: True})], None, "not a timing"),
        ([_set_with({1: -1})], None, "negative"),
        ([_set_with({})], ["step 1"], "1 labels for 20 steps"),
    ],
)
def test_refilter_verdict_refuses_malformed_sets(
    sets: list, labels: list | None, words: str
) -> None:
    with pytest.raises(ValueError, match=words):
        _refilter_verdict(sets, labels)


# The wiring around the rule, with a fake page. Five ranks, as the list page offers, so
# #f-hide-limited is step 14 here too.
FAKE_RANKS = [data.DEFAULT_VIEW, "r1", "r2", "r3", "r4"]


def _records(steps: list[tuple[str, str, str | None]], ms: Sequence[float]) -> list[dict]:
    """What REFILTER_JS records for ``steps`` on a page that redraws each one: a new view
    each time, and a list that shows what the count says."""
    return [
        {
            "ms": t,
            "target": selector[1:],
            "count": "Showing 9 of 540",
            "rows": 9,
            "hash": f"#rank=r{k}",
        }
        for k, ((_, selector, _), t) in enumerate(zip(steps, ms, strict=True))
    ]


def _fake_refilter_once(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stand in for ``_refilter_once``. Each set's extra init script names what its fake page
    does: "" redraws every change within budget, "slow" takes 250 ms at step 2 and 300 ms at
    step 14, "other steps" offers a rank fewer, and "bad records" shows a list the count
    doesn't match. Returns the scripts the sets were given, in order."""
    calls: list[str] = []

    def once(
        browser: Any, url: str, want_rows: int, extra_js: str = ""
    ) -> tuple[list[tuple[str, str, str | None]], list[dict]]:
        calls.append(extra_js)
        steps = _refilter_steps(FAKE_RANKS[:-1] if extra_js == "other steps" else FAKE_RANKS)
        records = _records(steps, _set_with({2: 250, 14: 300} if extra_js == "slow" else {}))
        if extra_js == "bad records":
            records[5]["rows"] += 1
        return steps, records

    monkeypatch.setitem(globals(), "_refilter_once", once)
    return calls


def test_the_budget_judges_the_sets_it_measured_naming_each_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_refilter_once(monkeypatch)
    _assert_refilter_budget(None, "", 0)
    _assert_refilter_budget(None, "", 0, ["slow"])  # outvoted by the two sets after it
    assert calls == ["", "slow", "", ""]
    calls.clear()
    with pytest.raises(AssertionError) as caught:
        _assert_refilter_budget(None, "", 0, ["slow"] * REFILTER_SETS)
    assert calls == ["slow"] * REFILTER_SETS
    assert (
        "over the 200 ms budget at the median of 3 sets: "
        "step 2 (select #f-rank r2): 250.0, 250.0, 250.0 ms, median 250.0 ms; "
        "step 14 (check #f-hide-limited): 300.0, 300.0, 300.0 ms, median 300.0 ms"
    ) in str(caught.value)


@pytest.mark.parametrize(
    ("n", "fault", "words"),
    [
        (0, "bad records", "count and list differ"),
        (1, "bad records", "count and list differ"),
        (2, "bad records", "count and list differ"),
        (1, "other steps", "set 2 made other changes"),
        (2, "other steps", "set 3 made other changes"),
    ],
)
def test_every_set_is_checked_and_makes_the_same_changes(
    monkeypatch: pytest.MonkeyPatch, n: int, fault: str, words: str
) -> None:
    # A median per step is fair only if every set made the same changes, and each set's
    # records must show a redraw, as the first set's do.
    calls = _fake_refilter_once(monkeypatch)
    planted = ["slow", "", ""]  # the first set fails, so sets 2 and 3 are measured
    planted[n] = fault
    with pytest.raises(AssertionError, match=words):
        _assert_refilter_budget(None, "", 0, planted)
    assert calls == planted[: n + 1]


# A fixed piece of work; the fastest of three runs, so the JIT has warmed up.
BUSY_JS = """
() => {
  let best = Infinity;
  for (let run = 0; run < 3; run += 1) {
    const t0 = performance.now();
    let x = 0;
    for (let i = 0; i < 3e6; i += 1) x = (x + Math.sqrt(i)) % 1e9;
    best = Math.min(best, performance.now() - t0);
    if (x < 0) return -1;
  }
  return best;
}
"""
# From a page task, as the site's own work would be: a long task, then a block pushed in
# above the list without any input, which moves everything below it.
PLANT_JS = """
() => new Promise((resolve) => setTimeout(() => {
  const t0 = performance.now();
  while (performance.now() - t0 < 150) { /* block the main thread */ }
  const block = document.createElement('div');
  block.style.height = '300px';
  document.getElementById('main').prepend(block);
  resolve();
}, 0))
"""


def test_the_measurements_see_the_slowdown_long_tasks_and_shifts(
    browser: Any, browser_name: str, large_url: str
) -> None:
    # The harness itself: a budget that passes because throttling was off, or because an
    # observer saw nothing, would prove nothing.
    _chromium(browser_name)
    context, page = _throttled_page(browser, large_url, PERF_JS, **VIEWPORTS["desktop"])
    plain = browser.new_context(base_url=large_url)
    try:
        page.goto("/", wait_until="load", timeout=LOAD_TIMEOUT_MS)
        _wait_quiet(page)
        m = page.evaluate("() => window.__tffPerf")
        # The page can't paint before its HTML has crossed one emulated round trip.
        assert m["fcp"] >= SLOW_4G["latency"], f"FCP {m['fcp']:.0f} ms: no network slowdown"
        fast_page = plain.new_page()
        fast_page.goto("/about/")
        fast, slow = fast_page.evaluate(BUSY_JS), page.evaluate(BUSY_JS)
        assert slow >= 2 * fast, f"the same work took {slow:.0f} ms slowed, {fast:.0f} ms not"
        tasks, shifts = len(m["longtasks"]), len(m["shifts"])
        page.evaluate(PLANT_JS)
        for _ in range(100):
            m = page.evaluate("() => window.__tffPerf")
            if len(m["longtasks"]) > tasks and len(m["shifts"]) > shifts:
                break
            page.wait_for_timeout(50)
    finally:
        plain.close()
        context.close()
    planted = m["longtasks"][tasks:]
    assert any(duration >= 140 for _, duration in planted), m["longtasks"]
    assert tbt_of(planted, m["fcp"]) >= 90
    assert cls_of(m["shifts"][shifts:]) > 0.05, m["shifts"]


def test_cls_and_tbt_arithmetic() -> None:
    # Session windows: shifts under 1 s apart join one window, capped at 5 s.
    shifts = [{"t": 0, "v": 0.05}, {"t": 900, "v": 0.05}, {"t": 2500, "v": 0.08}]
    assert cls_of(shifts) == pytest.approx(0.1)
    chain = [{"t": 900 * k, "v": 0.01} for k in range(10)]  # one gap-free run of 8.1 s
    assert cls_of(chain) == pytest.approx(0.06)
    assert cls_of([]) == 0
    # TBT: the part of each task past 50 ms, counting only what falls after FCP.
    assert tbt_of([[1000, 120], [2000, 40]], fcp=500) == pytest.approx(70)
    assert tbt_of([[400, 200]], fcp=500) == pytest.approx(100)
    assert tbt_of([[400, 120]], fcp=500) == 0


@pytest.mark.parametrize("size", VIEWPORTS)
def test_no_specimen_loads_far_below_the_screen(
    browser: Any, browser_name: str, large_url: str, size: str
) -> None:
    _chromium(browser_name)
    context = browser.new_context(base_url=large_url, **VIEWPORTS[size])
    context.add_init_script(PERF_JS)
    page = context.new_page()
    requested: list[str] = []
    page.on("request", lambda r: requested.append(r.url) if "/assets/specimens/" in r.url else None)
    try:
        page.goto("/", wait_until="load")
        _wait_quiet(page)
        page.wait_for_load_state("networkidle")
        ids = [re.sub(r"\.[0-9a-f]{10}\.svg$", "", url.rsplit("/", 1)[1]) for url in requested]
        below = page.evaluate(
            """(ids) => ids.map((id) => {
                const row = document.querySelector(`#list > li.font[data-id="${id}"]`);
                return [id, row ? row.getBoundingClientRect().top - innerHeight : null];
            })""",
            ids,
        )
        with_specimen = page.locator("#list span.spec[data-src]").count()
        scroll = page.evaluate("scrollY")
    finally:
        context.close()
    assert scroll == 0
    assert requested, "no specimen loaded at all"
    assert len(requested) < with_specimen, "every specimen loaded at once"
    far = [(i, round(d)) for i, d in below if d is None or d > FAR_BELOW_PX]
    assert far == [], f"specimens requested for rows far below the screen: {far[:10]}"


# ------------------------------------------------------------- the hopper (Milestone 3)

HOPPER_MS = 200  # Milestone 3 step 5: 10,000 names match in under 200 ms
PROFILER_MS = 1000  # step 4: 5 MB of system_profiler -json parses in under a second
# The machine's speed against the one the budgets were set on (the module docstring says why):
# REFERENCE_JS's median ms there, cold, by CPU slowdown, the sum it must return, and the most
# a budget may be scaled.
REFERENCE_MS = {1: 35.0, 4: 128.0}
REFERENCE_ROUNDS = 150_000
REFERENCE_CHECK = 2047583474
FACTOR_MAX = 5.0
# The list past ASCII against the ASCII fc-list, run for run: a regression guard.
PAST_ASCII = "fc-list, past ASCII"
ASCII_BASE = "fc-list"
PAST_ASCII_MATCH_RATIO = 2.5
PAST_ASCII_WAIT_RATIO = 2.2
# A fixed workload of the kind the matcher does (template strings, toLowerCase, a regex
# replace, charCodeAt and a Map), sharing no code with the site, so a slower site can't slow
# it; its sum proves every round ran.
REFERENCE_JS = """(rounds) => {
  const t0 = performance.now();
  const seen = new Map();
  let h = 0x811c9dc5;
  for (let i = 0; i < rounds; i++) {
    const name = `Sample ${(i * 7919) % 1009} Bold Italic ${i % 13}`;
    const key = name.toLowerCase().replace(/[ _-]+/g, '');
    for (let j = 0; j < key.length; j++) h = Math.imul(h ^ key.charCodeAt(j), 0x01000193);
    seen.set(key, (seen.get(key) || 0) + 1);
  }
  return { ms: performance.now() - t0, check: ((h >>> 0) + seen.size) >>> 0 };
}"""
# The matcher module as the build writes it (inventory_matcher_on_demand), inline; the timed
# code takes its API from globalThis.tffMatcher, which the module defines itself when no page
# has asked for it (58-matcher.js). Loading it is not timed.
HOPPER_PAGE = (
    "<!doctype html><html lang=en><meta charset=utf-8><title>hopper</title>"
    "<script type=module>\n{module}\n</script>"
)
# A visitor's list: parsing it (``parse_ms``), then reading the match file, readying its table
# and matching (``match_ms``, step 5's "match"); ``ms`` is both.
HOPPER_RUN_JS = """(arg) => {
  const { OwnedParse, OwnedMatch } = globalThis.tffMatcher;
  const t0 = performance.now();
  const parsed = OwnedParse.parse(arg.text);
  const t1 = performance.now();
  const result = OwnedMatch.match(parsed, OwnedMatch.table(JSON.parse(arg.doc)));
  const t2 = performance.now();
  return {
    parse_ms: t1 - t0, match_ms: t2 - t1, ms: t2 - t0,
    entries: parsed.entries.length, warnings: parsed.warnings, owned: result.owned,
  };
}"""


@functools.cache
def _hopper() -> dict[str, Any]:
    """The matcher module's page, the match file the site build makes from the committed
    build/names.json, as its text and its Python table, the four lists of 10,000 fonts, and
    the 5 MB paste."""
    module = assets.concat_js(ROOT / "site" / "js", assets.MATCHER_PARTS)
    assert "</script" not in module.lower()
    names_path = ROOT / "build" / "names.json"
    names = json.loads(names_path.read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "build" / "catalog-site.json").read_text(encoding="utf-8"))
    text = matchfile.match_file(catalog, names_path).blob.decode("utf-8")
    return {
        "page": HOPPER_PAGE.format(module=module),
        "doc": text,
        "table": owned.Table.from_document(json.loads(text)),
        "lists": hopper_lists.lists(names),
        "profiler": hopper_lists.system_profiler_cut(),
    }


def _hopper_run(browser: Any, text: str, slowdown: int) -> dict[str, Any]:
    """One run of HOPPER_RUN_JS on ``text``, in a context of its own with the CPU slowed
    ``slowdown`` times from the start."""
    context = browser.new_context()
    try:
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": slowdown})
        page.set_content(_hopper()["page"], wait_until="load")
        return page.evaluate(HOPPER_RUN_JS, {"doc": _hopper()["doc"], "text": text})
    finally:
        context.close()


def _reference_ms(browser: Any, slowdown: int) -> float:
    """One cold run of REFERENCE_JS, as _hopper_run runs a list: a context of its own, the CPU
    slowed ``slowdown`` times from the start."""
    context = browser.new_context()
    try:
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": slowdown})
        page.set_content("<!doctype html><title>reference</title>", wait_until="load")
        run = page.evaluate(REFERENCE_JS, REFERENCE_ROUNDS)
        assert run["check"] == REFERENCE_CHECK, run
        return float(run["ms"])
    finally:
        context.close()


def _factor(reference_ms: list[float], slowdown: int) -> float:
    """How much slower this machine ran REFERENCE_JS than the one the budgets were set on,
    never below 1 (_assert_hopper_budget fails one past FACTOR_MAX, rather than stretch a
    budget that far)."""
    return max(1.0, statistics.median(reference_ms) / REFERENCE_MS[slowdown])


def _checked_run(browser: Any, text: str, slowdown: int) -> dict[str, Any]:
    want, want_owned = _python(text)
    run = _hopper_run(browser, text, slowdown)
    assert (run["entries"], run["warnings"]) == (len(want["entries"]), want["warnings"])
    assert run["owned"] == want_owned
    return run


@functools.cache
def _python(text: str) -> tuple[dict[str, Any], list[Any]]:
    parsed = owned.parse(text)
    return parsed.to_json(), owned.match(parsed, _hopper()["table"]).to_json()["owned"]


def _assert_hopper_budget(
    browser: Any, text: str, field: str, budget_ms: float, slowdown: int
) -> None:
    """The median of ``field`` over RUNS cold runs is under ``budget_ms`` scaled by this
    machine's factor (``_factor``, from a reference run before each), at most FACTOR_MAX, and
    every run reads and matches ``text`` as Python does."""
    summary: dict[str, Any] = {}
    median, budget, factor = float("inf"), budget_ms, 1.0
    # Shared CI runners are noisy: a failing set, by its median or by a factor past FACTOR_MAX,
    # gets one more set of runs (design-m2 §9).
    for _attempt in range(2):
        pairs = [
            (_reference_ms(browser, slowdown), _checked_run(browser, text, slowdown))
            for _ in range(RUNS)
        ]
        runs = [run for _, run in pairs]
        factor = _factor([ms for ms, _ in pairs], slowdown)
        median = statistics.median(run[field] for run in runs)
        budget = budget_ms * factor
        summary = {
            "hopper": field,
            "cpu_slowdown": slowdown,
            "median_ms": round(median),
            "factor": round(factor, 2),
            "budget_ms": round(budget),
            "margin": round(budget / median, 2),
            "runs_ms": [round(run[field]) for run in runs],
            "parse_ms": [round(run["parse_ms"]) for run in runs],
            "match_ms": [round(run["match_ms"]) for run in runs],
            "reference_ms": [round(ms) for ms, _ in pairs],
        }
        print(json.dumps(summary))
        if factor <= FACTOR_MAX and median < budget:
            break
    assert factor <= FACTOR_MAX, (
        f"this machine ran the reference workload {factor:.1f} times slower than the one the"
        f" hopper's budgets were set on, past FACTOR_MAX ({FACTOR_MAX}): {summary}"
    )
    assert median < budget, summary


def _assert_past_ascii_ratio(browser: Any, field: str, ratio: float, slowdown: int) -> None:
    """The list past ASCII's median ``field`` is at most ``ratio`` times the ASCII fc-list's,
    the two run in turn, cold, RUNS times each, so both meet the same machine and load. A
    regression guard: the budget of each is _assert_hopper_budget's."""
    lists = _hopper()["lists"]
    summary: dict[str, Any] = {}
    found = float("inf")
    for _attempt in range(2):
        pairs = [
            (_checked_run(browser, lists[ASCII_BASE], slowdown),
             _checked_run(browser, lists[PAST_ASCII], slowdown))
            for _ in range(RUNS)
        ]  # fmt: skip
        base = statistics.median(b[field] for b, _ in pairs)
        past = statistics.median(p[field] for _, p in pairs)
        found = past / base
        summary = {
            "hopper": f"{field}, past ASCII over ASCII",
            "cpu_slowdown": slowdown,
            "ratio": round(found, 2),
            "budget": ratio,
            "past_ascii_ms": [round(p[field]) for _, p in pairs],
            "ascii_ms": [round(b[field]) for b, _ in pairs],
        }
        print(json.dumps(summary))
        if found <= ratio:
            break
    assert found <= ratio, summary


@pytest.mark.parametrize("label", hopper_lists.LABELS)
def test_the_hopper_matches_10000_fonts_within_200_ms(
    browser: Any, browser_name: str, label: str
) -> None:
    """Step 5's budget, under the list page's CPU slowdown: reading the match file, readying
    its table and matching a parsed list of 10,000 fonts, the list past ASCII included (the
    budget scaled for a slower machine, as the module docstring says)."""
    _chromium(browser_name)
    text = _hopper()["lists"][label]
    assert owned.parse(text).counts["entries"] > 8000
    _assert_hopper_budget(browser, text, "match_ms", HOPPER_MS, CPU_SLOWDOWN)


@pytest.mark.parametrize("label", hopper_lists.LABELS)
def test_the_hopper_parses_and_matches_10000_fonts_within_200_ms_without_a_slowdown(
    browser: Any, browser_name: str, label: str
) -> None:
    """The whole of a visitor's wait, on a computer: the hopper needs one (step 6 tells phones
    so), and Lighthouse's desktop setting slows no CPU."""
    _chromium(browser_name)
    _assert_hopper_budget(browser, _hopper()["lists"][label], "ms", HOPPER_MS, 1)


def test_names_past_ascii_match_within_2_5_times_ascii_ones(
    browser: Any, browser_name: str
) -> None:
    """A regression guard on the road past ASCII, under the slowdown: the list past ASCII
    against the ASCII fc-list, run for run. Its budget is step 5's, above."""
    _chromium(browser_name)
    assert owned.parse(_hopper()["lists"][PAST_ASCII]).counts["entries"] > 8000
    _assert_past_ascii_ratio(browser, "match_ms", PAST_ASCII_MATCH_RATIO, CPU_SLOWDOWN)


def test_names_past_ascii_parse_and_match_within_2_2_times_ascii_ones_without_a_slowdown(
    browser: Any, browser_name: str
) -> None:
    """The same guard on the visitor's whole wait, without a slowdown."""
    _chromium(browser_name)
    _assert_past_ascii_ratio(browser, "ms", PAST_ASCII_WAIT_RATIO, 1)


@pytest.mark.parametrize(
    ("reference_ms", "slowdown", "factor"),
    [([30.0, 35.0, 99.0], 1, 1.0), ([120.0, 128.0, 140.0], 4, 1.0), ([60.0] * 3, 4, 1.0),
     ([256.0] * 5, 4, 2.0), ([90.0, 105.0, 400.0], 1, 3.0), ([640.0] * 3, 4, 5.0)],
)  # fmt: skip
def test_a_hopper_budget_scales_with_the_reference_run_only_up(
    reference_ms: list[float], slowdown: int, factor: float
) -> None:
    """The median reference run against the machine's the budgets were set on: a faster
    machine keeps step 5's budget, a slower one gets it scaled by how much slower it is."""
    assert _factor(reference_ms, slowdown) == pytest.approx(factor)


def _fake_sets(monkeypatch: pytest.MonkeyPatch, sets: list[tuple[float, float]]) -> list[int]:
    """_assert_hopper_budget's runs, faked, set by set: each ``(reference ms, list ms)`` is
    every run of that set, at CPU_SLOWDOWN. Returns the count of reference runs made."""
    made = [0]

    def reference(_browser: Any, slowdown: int) -> float:
        assert slowdown == CPU_SLOWDOWN
        made[0] += 1
        return sets[(made[0] - 1) // RUNS][0]

    def run(_browser: Any, _text: str, _slowdown: int) -> dict[str, Any]:
        ms = sets[(made[0] - 1) // RUNS][1]
        return {"match_ms": ms, "parse_ms": 0.0, "ms": ms}

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_reference_ms", reference)
    monkeypatch.setattr(module, "_checked_run", run)
    return made


SLOW = REFERENCE_MS[CPU_SLOWDOWN] * (FACTOR_MAX + 1)  # a reference run past FACTOR_MAX
NORMAL = REFERENCE_MS[CPU_SLOWDOWN]


@pytest.mark.parametrize(
    "sets",
    [[(NORMAL, 50.0)], [(NORMAL, 250.0), (NORMAL, 50.0)], [(SLOW, 50.0), (NORMAL, 50.0)]],
    ids=["first-set", "a-slow-median-then-a-good-one", "a-noisy-factor-then-a-good-one"],
)
def test_a_hopper_budget_that_fails_gets_one_more_set_of_runs(
    monkeypatch: pytest.MonkeyPatch, sets: list[tuple[float, float]]
) -> None:
    """As the module docstring says, a set that fails, by its median or by a factor past
    FACTOR_MAX (a noisy spell on a shared runner), gets one more set before the test fails."""
    made = _fake_sets(monkeypatch, sets)
    _assert_hopper_budget(None, "", "match_ms", HOPPER_MS, CPU_SLOWDOWN)
    assert made[0] == RUNS * len(sets)


@pytest.mark.parametrize(
    ("sets", "problem"),
    [([(SLOW, 50.0)] * 2, "past FACTOR_MAX"), ([(NORMAL, 50.0), (SLOW, 50.0)], None),
     ([(NORMAL, 250.0)] * 2, "'median_ms': 250"), ([(SLOW, 50.0), (NORMAL, 250.0)], "250")],
    ids=["slow-twice", "a-good-first-set-is-the-last", "over-budget-twice",
         "noisy-then-over-budget"],
)  # fmt: skip
def test_a_hopper_budget_fails_when_its_second_set_fails_too(
    monkeypatch: pytest.MonkeyPatch, sets: list[tuple[float, float]], problem: str | None
) -> None:
    """A machine too slow for the budgets fails past FACTOR_MAX rather than stretch them, and
    a median over its budget fails, each only once the second set fails as well; a first set
    that passes is the last."""
    made = _fake_sets(monkeypatch, sets)
    if problem is None:
        _assert_hopper_budget(None, "", "match_ms", HOPPER_MS, CPU_SLOWDOWN)
        assert made[0] == RUNS
        return
    with pytest.raises(AssertionError, match=problem):
        _assert_hopper_budget(None, "", "match_ms", HOPPER_MS, CPU_SLOWDOWN)
    assert made[0] == RUNS * 2


def test_the_reference_is_set_where_each_budget_runs() -> None:
    """A reference for each slowdown the hopper is timed at, the list page's and none, slower
    with the slowdown."""
    assert set(REFERENCE_MS) == {1, CPU_SLOWDOWN}
    assert REFERENCE_MS[1] * 2 < REFERENCE_MS[CPU_SLOWDOWN]
    assert FACTOR_MAX > 1


def test_the_hopper_parses_5_mb_of_system_profiler_json_within_a_second(
    browser: Any, browser_name: str
) -> None:
    """Step 4's budget, under the list page's CPU slowdown: a paste cut at 5 MB, so the text
    ends inside a string."""
    _chromium(browser_name)
    _assert_hopper_budget(browser, _hopper()["profiler"], "parse_ms", PROFILER_MS, CPU_SLOWDOWN)


# ------------------------------------------------------------ a deployed site (by hand)


@pytest.fixture(scope="module")
def deployed_url() -> str:
    """``TFF_PERF_URL`` without a trailing slash; the deployed-site tests skip without it."""
    url = os.environ.get(PERF_URL_ENV, "").rstrip("/")
    if not url:
        pytest.skip(f"{PERF_URL_ENV} is not set: no deployed site to measure")
    if not url.startswith("https://"):
        pytest.fail(f"{PERF_URL_ENV}={url} is not an https URL")
    return url


@pytest.fixture(scope="module")
def deployed_rows(browser: Any, deployed_url: str) -> int:
    """How many fonts the deployed list page's HTML lists."""
    context = browser.new_context()
    try:
        html = context.request.get(f"{deployed_url}/").text()
    finally:
        context.close()
    rows = len(ROW.findall(html))
    if rows < MIN_LARGE_FONTS:  # the budgets are for the full catalog, not a small sample
        pytest.fail(
            f"{PERF_URL_ENV}={deployed_url} lists {rows} fonts, fewer than {MIN_LARGE_FONTS}"
        )
    return rows


@pytest.mark.network
@pytest.mark.parametrize("size", VIEWPORTS)
def test_deployed_load_meets_the_web_vitals_budget(
    browser: Any, browser_name: str, deployed_url: str, deployed_rows: int, size: str
) -> None:
    _chromium(browser_name)
    _assert_load_budget(browser, deployed_url, deployed_rows, size)


@pytest.mark.network
def test_deployed_rank_and_filter_changes_redraw_within_200_ms(
    browser: Any, browser_name: str, deployed_url: str, deployed_rows: int
) -> None:
    _chromium(browser_name)
    _assert_refilter_budget(browser, deployed_url, deployed_rows)


# ------------------------------------------------------------ the budgets (no browser)


def test_large_site_meets_every_budget(large_site: Path) -> None:
    if not os.environ.get(PERF_SITE_ENV):
        assert _rows_in(large_site) >= MIN_LARGE_FONTS
    assert budgets.check(large_site) == []


def test_sample_site_meets_every_budget(site_dir: Path) -> None:
    assert budgets.check(site_dir) == []


def test_check_command_exit_codes(site_dir: Path, tmp_path: Path, capsys: Any) -> None:
    assert cli.main(["check", str(site_dir)]) == 0
    assert "all budgets and CSP checks pass" in capsys.readouterr().out
    assert cli.main(["check", str(tmp_path / "nothing")]) == 1
    assert "no built site" in capsys.readouterr().err


def _noise(size: int, seed: int = 7) -> bytes:
    """Bytes that gzip can't shrink, so a file's gzip size is about its raw size."""
    return np.random.default_rng(seed).integers(0, 256, size, dtype=np.uint8).tobytes()


MATCH = "assets/match.0123456789.json"
MATCHER = "assets/matcher.0123456789.js"


def _fake_site(root: Path, *, html_extra: str = "", sizes: dict[str, int] | None = None) -> Path:
    """A minimal built site: the list page and its six assets, with incompressible filler."""
    sizes = {
        "css": 1000,
        "js": 1000,
        "list": 1000,
        "details": 1000,
        "match": 1000,
        "matcher": 1000,
        "font": 1000,
        **(sizes or {}),
    }
    files = {
        "assets/style.0123456789.css": sizes["css"],
        "assets/app.0123456789.js": sizes["js"],
        "assets/list.0123456789.json": sizes["list"],
        "assets/details.0123456789.json": sizes["details"],
        MATCH: sizes["match"],
        MATCHER: sizes["matcher"],
        "assets/ui/arimo.0123456789.woff2": sizes["font"],
    }
    for k, (rel, size) in enumerate(files.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_noise(size, seed=k))
    (root / "index.html").write_text(
        "<!doctype html><html lang=en><head><meta charset=utf-8><title>t</title>"
        '<link rel="preload" href="/assets/ui/arimo.0123456789.woff2" as="font"'
        ' type="font/woff2" crossorigin>'
        '<link rel="stylesheet" href="/assets/style.0123456789.css">'
        '<script type="module" src="/assets/app.0123456789.js"></script>'
        '<link rel="preload" href="/assets/list.0123456789.json" as="fetch" crossorigin>'
        f"</head><body>{html_extra}<main id=main><ol id=list class=font-list"
        ' data-index="/assets/list.0123456789.json"'
        ' data-details="/assets/details.0123456789.json"'
        f' data-match="/{MATCH}" data-matcher="/{MATCHER}"></ol></main></body></html>\n',
        encoding="utf-8",
    )
    return root


def test_a_small_site_passes(tmp_path: Path) -> None:
    assert budgets.check(_fake_site(tmp_path)) == []


@pytest.mark.parametrize(
    ("sizes", "path", "words"),
    [
        ({"js": 101_000}, "index.html", "HTML + CSS + JS"),
        ({"css": 60_000, "js": 45_000}, "index.html", "HTML + CSS + JS"),
        ({"list": 101_000}, "assets/list.0123456789.json", "list index"),
        ({"details": 151_000}, "assets/details.0123456789.json", "details payload"),
        ({"match": 51_000}, MATCH, "match file"),
        ({"matcher": 26_000}, MATCHER, "matcher module"),
        ({"font": 31_000}, "assets/ui/arimo.0123456789.woff2", "interface font"),
    ],
)
def test_each_size_budget_fails_when_exceeded(
    tmp_path: Path, sizes: dict[str, int], path: str, words: str
) -> None:
    problems = budgets.check(_fake_site(tmp_path, sizes=sizes))
    assert [p.path for p in problems] == [path], problems
    assert words in problems[0].message
    assert "over the" in problems[0].message


def test_a_preloaded_font_must_be_in_the_site(tmp_path: Path) -> None:
    site = _fake_site(tmp_path)
    (site / "assets" / "ui" / "arimo.0123456789.woff2").unlink()
    problems = budgets.check(site)
    assert [(p.path, p.message) for p in problems] == [
        ("index.html", "/assets/ui/arimo.0123456789.woff2: preloaded, but not in the site")
    ]


def test_a_details_shard_is_held_to_the_budget(tmp_path: Path) -> None:
    site = _fake_site(tmp_path)
    (site / "assets" / "details-2.0123456789.json").write_bytes(_noise(151_000))
    problems = budgets.check(site)
    assert [p.path for p in problems] == ["assets/details-2.0123456789.json"]


def test_missing_assets_are_problems(tmp_path: Path) -> None:
    site = _fake_site(tmp_path)
    (site / "assets" / "app.0123456789.js").unlink()
    (site / "assets" / "list.0123456789.json").unlink()
    (site / MATCH).unlink()
    messages = [p.message for p in budgets.check(site)]
    assert any("app.0123456789.js" in m and "not in the site" in m for m in messages)
    assert any("list index" in m and "not in the site" in m for m in messages)
    assert f"/{MATCH}: the match file is referenced, but not in the site" in messages
    (site / "index.html").unlink()
    assert [p.path for p in budgets.check(site)] == ["index.html"]


@pytest.mark.parametrize(
    ("attribute", "path", "what"),
    [("data-match", MATCH, "match file"), ("data-matcher", MATCHER, "matcher module")],
)
def test_a_list_without_one_of_the_hopper_s_files_is_a_problem(
    tmp_path: Path, attribute: str, path: str, what: str
) -> None:
    site = _fake_site(tmp_path)
    page = site / "index.html"
    page.write_text(page.read_text().replace(f' {attribute}="/{path}"', ""), encoding="utf-8")
    assert [(p.path, p.message) for p in budgets.check(site)] == [
        ("index.html", f"#list has no {attribute}: can't find the {what}")
    ]
    _fake_site(tmp_path)
    (site / path).unlink()
    assert [(p.path, p.message) for p in budgets.check(site)] == [
        ("index.html", f"/{path}: the {what} is referenced, but not in the site")
    ]


def test_the_matcher_module_is_held_to_its_own_budget_outside_the_page_s() -> None:
    """inventory_matcher_on_demand: the parser and matcher left the page's script, so the page
    keeps its budget, and the module has one of its own, as the match file does."""
    assert budgets.PAGE_MAX == 100 * budgets.KB
    assert budgets.MATCHER_MAX == 25 * budgets.KB
    assert budgets.MATCH_MAX == 50 * budgets.KB


@pytest.mark.parametrize(
    "url", [f"https://x.test/{MATCH}", f"//x.test/{MATCH}", f"/../{MATCH}", f"{MATCH}"]
)
def test_a_match_file_off_this_site_is_a_problem(tmp_path: Path, url: str) -> None:
    """M3-D7 (a): the hopper's match file is a same-site asset, so ``data-match`` that names
    another site (or no site path) is flagged, though the site holds the file."""
    site = _fake_site(tmp_path)
    page = site / "index.html"
    page.write_text(page.read_text().replace(f'data-match="/{MATCH}"', f'data-match="{url}"'))
    assert [(p.path, p.message) for p in budgets.check(site)] == [
        ("index.html", f"{url}: the match file is referenced, but not in the site")
    ]


NAME = MATCH.rsplit("/", 1)[1]
# Where the match file may not be named: a case for each of budgets.TEXT_SUFFIXES.
MENTIONS = [
    pytest.param(
        "index.html",
        None,
        f"names the match file {NAME} 2 times; only the list page's #list[data-match] "
        "may, once (M3-D7 a)",
        id="preload-on-the-list-page",
    ),
    pytest.param(
        "about/index.html",
        f'<!doctype html><title>t</title><link rel="help" href="/{MATCH}">',
        None,
        id="link-on-another-page",
    ),
    pytest.param("assets/app.0123456789.js", f'fetch("/{MATCH}");', None, id="url-in-the-script"),
    pytest.param(
        "404.html",
        f'<!doctype html><title>t</title><a href="{MATCH}">x</a>',
        None,
        id="relative",
    ),
    pytest.param("sitemap.xml", f"<loc>https://x.test/{MATCH}</loc>", None, id="sitemap"),
    pytest.param("assets/details.0123456789.json", f'{{"next": "/{MATCH}"}}', None, id="payload"),
    pytest.param(
        "assets/style.0123456789.css",
        f"body{{background:url(/{MATCH})}}",
        None,
        id="stylesheet",
    ),
    pytest.param("robots.txt", f"User-agent: *\nAllow: /{MATCH}\n", None, id="text"),
    pytest.param(
        "favicon.svg",
        f'<svg xmlns="http://www.w3.org/2000/svg"><image href="/{MATCH}"/></svg>',
        None,
        id="svg",
    ),
]


MATCHER_NAME = MATCHER.rsplit("/", 1)[1]
ONLY_DATA_MATCHER = (
    f"names the matcher module {MATCHER_NAME}; only the list page's #list[data-matcher] may, "
    "once (M3-D7 a)"
)


# Where the matcher module may not be named: a case for each of budgets.TEXT_SUFFIXES too.
MATCHER_MENTIONS = [
    pytest.param(
        "index.html",
        f'<script type="module" src="/{MATCHER}"></script>',
        f"names the matcher module {MATCHER_NAME} 2 times; only the list page's "
        "#list[data-matcher] may, once (M3-D7 a)",
        id="script-on-the-list-page",
    ),
    pytest.param(
        "index.html",
        f'<link rel="modulepreload" href="/{MATCHER}">',
        None,
        id="modulepreload-on-the-list-page",
    ),
    pytest.param(
        "about/index.html",
        f'<!doctype html><title>t</title><script type="module" src="/{MATCHER}"></script>',
        ONLY_DATA_MATCHER,
        id="script-on-another-page",
    ),
    pytest.param(
        "assets/app.0123456789.js", f"import('/{MATCHER}');", ONLY_DATA_MATCHER, id="script"
    ),
    pytest.param(
        "assets/list.0123456789.json", f'{{"m": "/{MATCHER}"}}', ONLY_DATA_MATCHER, id="payload"
    ),
    pytest.param(
        "assets/style.0123456789.css",
        f"body{{background:url(/{MATCHER})}}",
        ONLY_DATA_MATCHER,
        id="stylesheet",
    ),
    pytest.param(
        "sitemap.xml", f"<loc>https://x.test/{MATCHER}</loc>", ONLY_DATA_MATCHER, id="sitemap"
    ),
    pytest.param("robots.txt", f"User-agent: *\nAllow: /{MATCHER}\n", ONLY_DATA_MATCHER, id="text"),
    pytest.param(
        "favicon.svg",
        f'<svg xmlns="http://www.w3.org/2000/svg"><script href="/{MATCHER}"/></svg>',
        ONLY_DATA_MATCHER,
        id="svg",
    ),
]


@pytest.mark.parametrize(("path", "text", "problem"), MATCHER_MENTIONS)
def test_only_the_list_s_data_matcher_may_name_the_matcher_module(
    tmp_path: Path, path: str, text: str, problem: str | None
) -> None:
    """inventory_matcher_on_demand: the matcher module loads only once a visitor's list
    arrives, so nothing but ``#list[data-matcher]`` names it: no script element or
    modulepreload, no other page, script, payload, stylesheet, text file, image or sitemap
    (each of ``budgets.TEXT_SUFFIXES`` has a case here)."""
    on_page = path == "index.html"
    site = _fake_site(tmp_path, html_extra=text if on_page else "")
    if not on_page:
        target = site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    want = problem or (
        f"names the matcher module {MATCHER_NAME} 2 times; only the list page's "
        "#list[data-matcher] may, once (M3-D7 a)"
    )
    problems = [(p.path, p.message) for p in budgets.check(site)]
    assert (path, want) in problems
    assert [p for p in problems if p[0] != path or "names the" not in p[1]] == (
        [] if not on_page or "modulepreload" in text else [p for p in problems if p[0] != path]
    )


@pytest.mark.parametrize("cases", [MENTIONS, MATCHER_MENTIONS], ids=["match", "matcher"])
def test_the_mention_cases_cover_every_text_suffix(cases: list[Any]) -> None:
    assert {Path(case.values[0]).suffix for case in cases} == budgets.TEXT_SUFFIXES


@pytest.mark.parametrize(("path", "text", "problem"), MENTIONS)
def test_only_the_list_s_data_match_may_name_the_match_file(
    tmp_path: Path, path: str, text: str | None, problem: str | None
) -> None:
    """M3-D7 (a): the match file loads only once a visitor's list arrives, so nothing but
    ``#list[data-match]`` names it: no preload, no other page, script, payload, stylesheet,
    text file, image or sitemap (each of ``budgets.TEXT_SUFFIXES`` has a case here)."""
    preload = f'<link rel="preload" as="fetch" crossorigin href="/{MATCH}">'
    site = _fake_site(tmp_path, html_extra=preload if text is None else "")
    if text is not None:
        target = site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    want = (
        problem
        or f"names the match file {NAME}; only the list page's #list[data-match] may, once (M3-D7 a)"
    )
    assert [(p.path, p.message) for p in budgets.check(site)] == [(path, want)]


def test_specimen_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    site = _fake_site(tmp_path)
    folder = site / "assets" / "specimens"
    folder.mkdir()
    small = b"<svg xmlns='http://www.w3.org/2000/svg'><path d='M0 0h1'/></svg>\n"
    for k in range(3):
        (folder / f"f{k}.0123456789.svg").write_bytes(small)
    assert budgets.check(site) == []

    for k in range(3, 7):  # 4 of 7 over 5 KB gzip: less than half are small
        (folder / f"f{k}.0123456789.svg").write_bytes(_noise(6_000, seed=k))
    problems = budgets.check(site)
    assert [p.path for p in problems] == ["assets/specimens"]
    assert "3 of 7" in problems[0].message

    # Over the cap gzipped (the owner's ruling of 2026-09-30); raw size alone never fails.
    (folder / "big.0123456789.svg").write_bytes(_noise(budgets.SPECIMEN_MAX + 1, seed=9))
    (folder / "raw.0123456789.svg").write_bytes(b" " * 60_000)
    paths = [p.path for p in budgets.check(site)]
    assert "assets/specimens/big.0123456789.svg" in paths
    assert "assets/specimens/raw.0123456789.svg" not in paths

    monkeypatch.setattr(budgets, "SPECIMENS_TOTAL_RAW_MAX", 20_000)
    assert any("uncompressed" in p.message for p in budgets.check(site))


def test_check_lints_every_html_file(tmp_path: Path) -> None:
    site = _fake_site(tmp_path)
    (site / "about").mkdir()
    (site / "about" / "index.html").write_text("<p style='color:red'>x</p>", encoding="utf-8")
    problems = budgets.check(site)
    assert [p.path for p in problems] == ["about/index.html"]
    assert problems[0].message.startswith("line 1: style attribute")


# ------------------------------------------------------------------------ the CSP lint


@pytest.mark.parametrize(
    ("html", "words"),
    [
        ("<script>alert(1)</script>", "inline <script>"),
        ("<script type='application/ld+json'>{}</script>", "inline <script>"),
        ("<script src=''></script>", "inline <script>"),
        ("<style>p{}</style>", "<style> element"),
        ("<p style='color:red'>x</p>", "style attribute"),
        ("<img src=/a.png alt='' onerror='x()'>", "onerror attribute"),
        ("<button type=button onclick=go()>Go</button>", "onclick attribute"),
        ("<form action=/x><input name=q></form>", "<form>"),
        ("<link rel=prefetch href=/next/>", 'rel="prefetch"'),
        ("<link rel='dns-prefetch prefetch' href=/x>", 'rel="prefetch"'),
        ("<base href=/>", "<base> element"),
        ("<a href='javascript:void(0)'>x</a>", "javascript: URL"),
        ("<a href=' java\tscript:x()'>x</a>", "javascript: URL"),
        ("<link rel=stylesheet href=https://cdn.example/x.css>", "not from this site"),
        ("<script type=module src=//cdn.example/x.js></script>", "not from this site"),
        ("<img src=https://example.com/a.png alt=''>", "not from this site"),
        ("<img srcset='/a.png 1x, https://example.com/b.png 2x' src=/a.png alt=''>", "not from"),
        ("<link rel=icon href=data:image/png;base64,AAAA>", "not from this site"),
        ("<link rel=prerender href=/about/>", 'rel="prerender"'),
        ("<link rel=preconnect href=https://fonts.gstatic.com>", "not from this site"),
        ("<link rel=dns-prefetch href=//cdn.example>", "not from this site"),
        ("<svg><image href=https://example.com/a.png /></svg>", "not from this site"),
        ("<iframe src=/about/ title=x></iframe>", "frame-src"),
        ("<iframe src=https://example.com/ title=x></iframe>", "frame-src"),
        ("<object data=/a.pdf></object>", "object-src"),
        ("<embed src=/a.swf>", "object-src"),
        ("<video src=/a.mp4 controls></video>", "media-src"),
        ("<audio src=/a.ogg controls></audio>", "media-src"),
    ],
)
def test_lint_flags(html: str, words: str) -> None:
    problems = budgets.lint_html("x.html", f"<!doctype html><title>t</title>\n{html}\n")
    assert len(problems) == 1, problems
    assert problems[0].path == "x.html"
    assert problems[0].message.startswith("line 2: ")
    assert words in problems[0].message


def test_lint_accepts_what_the_site_uses() -> None:
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<link rel="canonical" href="https://purelyfreefonts.com/">
<meta property="og:image" content="https://purelyfreefonts.com/share.png">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/assets/style.0123456789.css">
<script type="module" src="/assets/app.0123456789.js"></script>
<link rel="preload" href="/assets/list.0123456789.json" as="fetch" crossorigin>
</head><body><a class="skip-link" href="#main">Skip</a>
<search id="filters" hidden><input id="f-q" name="q" type="search"></search>
<noscript><img class="spec-img" src="/assets/specimens/a.0123456789.svg" alt=""
  width="10" height="10" loading="lazy"></noscript>
<a href="https://github.com/rsms/inter">GitHub: rsms/inter</a>
<a href="mailto:x@example.com">Email</a>
<svg viewBox="0 0 1 1" aria-hidden="true"><path d="M0 0"/></svg>
</body></html>
"""
    assert budgets.lint_html("index.html", html) == []


def test_specimen_limits_match_the_specimens_stage() -> None:
    # tff-catalog specimens --check and tff-site check must agree on the same files.
    from tff_catalog import specimens

    assert budgets.SPECIMEN_HALF_MAX == specimens.SMALL_GZIP_BYTES
    assert budgets.SPECIMEN_MAX == specimens.MAX_FILE_GZIP_BYTES
    assert budgets.SPECIMENS_TOTAL_RAW_MAX == specimens.MAX_TOTAL_BYTES


def test_gzip_size_is_deterministic() -> None:
    blob = b"purely free fonts " * 100
    assert budgets.gzip_size(blob) == budgets.gzip_size(blob)
    assert budgets.gzip_size(blob) < len(blob)
    assert budgets.gzip_size(blob) == len(gzip.compress(blob, compresslevel=9, mtime=0))
