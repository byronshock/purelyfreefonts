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
  prove the rule in the browser.
- Specimens: at load, no specimen is requested for a row more than 1000 px below the screen.

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

import gzip
import json
import math
import os
import re
import statistics
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

from tff_site import budgets, cli, data

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


def _assert_refilter_budget(
    browser: Any, url: str, rows: int, extra_js: Sequence[str] = ()
) -> tuple[list[tuple[str, str, str | None]], list[list[float]]]:
    """Measure the refilter budget and fail if it is broken; return the steps and each set's
    timings. ``extra_js`` is an extra init script for each set in turn (the planted tests'
    slowdowns); the real tests pass none."""
    # Shared CI runners are noisy and stall now and then (design-m2 §9). A first set with
    # every change within budget passes. If a change is over, two more sets are measured and
    # each change is judged by its median over the three: one stall can't move a median, but
    # a change that is really slow is slow in every set.
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
    labels = [_step_label(k, step) for k, step in enumerate(first, start=1)]
    passed, medians, failures = _refilter_verdict(sets, labels)
    if len(sets) > 1:
        print(json.dumps({"refilter_median_ms": [round(m) for m in medians]}))
    assert passed, (
        f"over the {REFILTER_MAX_MS} ms budget at the median of {len(sets)} sets: "
        + "; ".join(failures)
    )
    return first, sets


def test_rank_and_filter_changes_redraw_within_200_ms(
    browser: Any, browser_name: str, large_url: str, large_rows: int
) -> None:
    _chromium(browser_name)
    _assert_refilter_budget(browser, large_url, large_rows)


# Planted before the page's own script, after REFILTER_JS: a capture-phase listener that holds
# the main thread for STALL_MS on the change to PLANTED_STEP (made once per set), as a stall
# or a slow redraw would.
PLANTED_STEP = "#f-hide-limited"
STALL_MS = 300
STALL_JS = """
(() => {
  let held = false;
  document.addEventListener('change', (e) => {
    if (held || e.target.id !== 'PLANTED_ID') return;
    held = true;
    const t0 = performance.now();
    while (performance.now() - t0 < STALL_MS) { /* hold the main thread */ }
  }, true);
})();
""".replace("STALL_MS", str(STALL_MS)).replace("PLANTED_ID", PLANTED_STEP[1:])


def test_one_planted_stall_in_the_first_set_passes(
    browser: Any, browser_name: str, large_url: str, large_rows: int
) -> None:
    # The first set fails at the planted step, so two more are measured; that step's median
    # over the three is one of the two unplanted sets, within budget.
    _chromium(browser_name)
    steps, sets = _assert_refilter_budget(browser, large_url, large_rows, extra_js=[STALL_JS])
    k = [selector for _, selector, _ in steps].index(PLANTED_STEP)
    assert len(sets) == REFILTER_SETS
    assert sets[0][k] >= STALL_MS, sets[0]


def test_the_same_slow_step_in_every_set_fails(
    browser: Any, browser_name: str, large_url: str, large_rows: int
) -> None:
    _chromium(browser_name)
    with pytest.raises(AssertionError, match="budget at the median of 3 sets") as caught:
        _assert_refilter_budget(browser, large_url, large_rows, extra_js=[STALL_JS] * REFILTER_SETS)
    assert f"(check {PLANTED_STEP}): " in str(caught.value), caught.value


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


def _fake_site(root: Path, *, html_extra: str = "", sizes: dict[str, int] | None = None) -> Path:
    """A minimal built site: the list page and its four assets, with incompressible filler."""
    sizes = {"css": 1000, "js": 1000, "list": 1000, "details": 1000, "font": 1000, **(sizes or {})}
    files = {
        "assets/style.0123456789.css": sizes["css"],
        "assets/app.0123456789.js": sizes["js"],
        "assets/list.0123456789.json": sizes["list"],
        "assets/details.0123456789.json": sizes["details"],
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
        ' data-details="/assets/details.0123456789.json"></ol></main></body></html>\n',
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
    messages = [p.message for p in budgets.check(site)]
    assert any("app.0123456789.js" in m and "not in the site" in m for m in messages)
    assert any("list index" in m and "not in the site" in m for m in messages)
    (site / "index.html").unlink()
    assert [p.path for p in budgets.check(site)] == ["index.html"]


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
