"""``tff-site serve`` (tff_site.serve) and the Milestone 3 hook (site/js/50-ext.js).

- **parse_headers:** the real ``ops/caddy/site.caddy`` and small snippets: quoting, comments,
  the ``>`` deferred prefix, ``-`` removals, and a loud failure on anything it can't mirror.
- **The preview server:** every response carries the production headers and no ``Server``;
  ``/assets/*`` is immutable and everything else revalidates, a miss included; ``/404.html``
  answers missing files with status 404; directories are never listed; nothing is compressed.
  ``tests/test_caddy_config.py`` checks the same responses against real Caddy.
- **The CLI:** ``tff-site serve DIR --port 0`` prints its address and stops cleanly on Ctrl-C.
- **The hook** (browser tests): ``50-ext.js`` with ``00-core.js`` and ``05-keys.js`` and test
  doubles for State, Data, Specimens and Main, served by the preview server, so it also runs
  under the site's CSP. Then the real hook on the built list page (every part, through
  ``site_url``): hide, dim, notes, actions, state, the index and the summary.
"""

import http.client
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tff_site import serve

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "site" / "js"
HEADERS = serve.parse_headers(serve.SITE_CADDY.read_text(encoding="utf-8"))
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache, no-transform"
JS_PATH = "/assets/app.0123456789.js"


# ------------------------------------------------------------------------ parse_headers


def test_the_real_snippet_gives_the_design_headers():
    assert HEADERS.security["Content-Security-Policy"] == CSP
    assert HEADERS.security["Permissions-Policy"].startswith("accelerometer=(), ")
    assert "local-fonts=()" in HEADERS.security["Permissions-Policy"]
    assert {k: HEADERS.security[k] for k in list(HEADERS.security)[2:]} == {
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Strict-Transport-Security": "max-age=31536000",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Frame-Options": "DENY",
    }
    assert HEADERS.remove == ("Server",)
    assert HEADERS.immutable == {"Cache-Control": IMMUTABLE}
    # site.caddy writes ">Cache-Control" so that encode still compresses HTML.
    assert HEADERS.revalidate == {"Cache-Control": REVALIDATE}


SNIPPET = r"""# A comment line.
(tff_security) {
	header {
		X-One "a \"quoted\" value"  # a trailing comment
		>X-Two two
		-Server
		defer
	}
}

(tff_site) {
	import tff_security # also a comment
	@immutable path /assets/*
	header @immutable Cache-Control "public, max-age=31536000, immutable"
	@revalidate not path /assets/*
	header @revalidate >Cache-Control "no-cache, no-transform"
	header X-Three "three#not-a-comment"
	encode zstd gzip
	file_server
	handle_errors 404 {
		header Cache-Control "not mirrored"
	}
}
"""


def test_a_snippet_with_quotes_comments_and_prefixes():
    headers = serve.parse_headers(SNIPPET)
    assert headers.security == {
        "X-One": 'a "quoted" value',
        "X-Two": "two",
        "X-Three": "three#not-a-comment",
    }
    assert headers.remove == ("Server",)
    assert headers.immutable == {"Cache-Control": IMMUTABLE}
    assert headers.revalidate == {"Cache-Control": REVALIDATE}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("(tff_site) {", "(other) {"), "no (tff_site) snippet"),
        (("@immutable path /assets/*", "@immutable path /static/*"), "expects @immutable"),
        (("header X-Three", "header @check X-Three"), "doesn't know the matcher @check"),
        (("header @revalidate >Cache-Control", "header @revalidate -Cache-Control"), "-Cache"),
        ((">X-Two two", ">X-Two two too"), "can't mirror header line"),
        (("\tfile_server\n", "\tfile_server {\n"), "not closed"),
        (("# A comment line.", "}"), "unmatched"),
    ],
)
def test_what_the_preview_cannot_mirror_fails_loudly(change, message):
    old, new = change
    assert old in SNIPPET
    with pytest.raises(ValueError, match=re.escape(message)):
        serve.parse_headers(SNIPPET.replace(old, new, 1))


# ------------------------------------------------------------------------ the preview server


def make_site(root: Path) -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "methodology").mkdir()
    (root / "empty").mkdir()
    (root / "index.html").write_text("<!doctype html><title>Home</title>" * 50, "utf-8")
    (root / "404.html").write_text("<!doctype html><title>Not found</title>", "utf-8")
    (root / "methodology" / "index.html").write_text("<!doctype html><p>How</p>", "utf-8")
    (root / JS_PATH.lstrip("/")).write_text("const Core = 1;\n" * 50, "utf-8")
    (root / "version.txt").write_text(f"commit={'0' * 40}\n", "utf-8")
    return root


def started(server: Any) -> Iterator[str]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def preview(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Path]]:
    site = make_site(tmp_path_factory.mktemp("preview"))
    for address in started(serve.make_server(site, port=0)):
        yield address, site


def get(address: str, path: str, method: str = "GET", **headers: str):
    conn = http.client.HTTPConnection(address, timeout=10)
    try:
        conn.request(method, path, headers=headers)
        response = conn.getresponse()
        return response.status, response.headers, response.read()
    finally:
        conn.close()


def assert_security(headers) -> None:
    for name, value in HEADERS.security.items():
        assert headers[name] == value, name
    assert "Server" not in headers


def test_pages_revalidate_carry_the_headers_and_are_never_compressed(preview):
    address, site = preview
    status, headers, body = get(address, "/", **{"Accept-Encoding": "gzip, zstd"})
    assert status == 200
    assert_security(headers)
    assert headers["Cache-Control"] == REVALIDATE
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "Content-Encoding" not in headers
    assert body == (site / "index.html").read_bytes()
    status, headers, _ = get(address, "/version.txt?x=1")
    assert (status, headers["Cache-Control"]) == (200, REVALIDATE)
    assert headers["Content-Type"] == "text/plain; charset=utf-8"


def test_route_handlers_read_bodies_unencoded():
    """The preview server never compresses (above), but CI's Caddy does (``encode zstd gzip``),
    and Firefox accepts zstd there, which Playwright's fetch can't decode. A route handler that
    reads a body through a plain ``route.fetch`` would pass here and in Chromium, and fail
    only in Firefox CI, stalling the tests after it; handlers use ``conftest.fetch_unencoded``."""
    here = Path(__file__).resolve().parent
    call = "route" + ".fetch("  # split, so this line isn't a match
    bare = [
        f"{path.name}:{number}"
        for path in sorted(here.glob("*.py"))
        if path.name != "conftest.py"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if call in line
    ]
    assert bare == []


def test_hashed_assets_are_immutable(preview):
    address, _ = preview
    status, headers, _ = get(address, JS_PATH)
    assert (status, headers["Cache-Control"]) == (200, IMMUTABLE)
    assert headers["Content-Type"] == "text/javascript; charset=utf-8"
    assert_security(headers)


@pytest.mark.parametrize("path", ["/missing", "/assets/missing.0123456789.js", "/empty/"])
def test_a_miss_is_the_404_page_and_is_never_cached(preview, path):
    address, site = preview
    status, headers, body = get(address, path)
    assert status == 404
    assert body == (site / "404.html").read_bytes()
    assert headers["Cache-Control"] == REVALIDATE
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert_security(headers)


def test_directories_redirect_to_their_slash_and_head_has_no_body(preview):
    address, _ = preview
    status, headers, _ = get(address, "/methodology")
    assert status in {301, 308}
    assert headers["Location"] == "/methodology/"
    assert_security(headers)
    status, headers, body = get(address, "/", method="HEAD")
    assert (status, body) == (200, b"")
    assert int(headers["Content-Length"]) > 0


def test_one_connection_serves_many_requests(preview):
    address, _ = preview
    conn = http.client.HTTPConnection(address, timeout=10)
    try:
        for path in ("/", JS_PATH, "/missing", "/"):
            conn.request("GET", path)
            response = conn.getresponse()
            response.read()
            assert response.status in {200, 404}
    finally:
        conn.close()


def test_a_broken_request_is_refused_with_the_headers(preview):
    address, _ = preview
    host, port = address.split(":")
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        # Four words in the request line: refused before the path is known.
        sock.sendall(b"GET / extra HTTP/1.1\r\nHost: x\r\n\r\n")
        reply = sock.makefile("rb").read().decode("latin-1")
    assert reply.startswith("HTTP/1.1 400")
    assert f"Content-Security-Policy: {CSP}" in reply
    assert "Server:" not in reply


def test_without_a_404_page_the_default_error_still_has_the_headers(tmp_path):
    site = make_site(tmp_path / "site")
    (site / "404.html").unlink()
    for address in started(serve.make_server(site, port=0)):
        status, headers, _ = get(address, "/missing")
        assert (status, headers["Cache-Control"]) == (404, REVALIDATE)
        assert_security(headers)


def test_another_snippet_can_be_served(tmp_path):
    snippet = tmp_path / "site.caddy"
    snippet.write_text(SNIPPET, encoding="utf-8")
    site = make_site(tmp_path / "site")
    for address in started(serve.make_server(site, port=0, snippet=snippet)):
        _, headers, _ = get(address, "/")
        assert headers["X-One"] == 'a "quoted" value'
        assert "Content-Security-Policy" not in headers


def test_a_missing_site_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="run tff-site build first"):
        serve.make_server(tmp_path / "nothing", port=0)


CLI = "from tff_site.cli import main; raise SystemExit(main())"


def test_the_command_prints_its_address_and_stops_on_ctrl_c(tmp_path):
    site = make_site(tmp_path / "site")
    process = subprocess.Popen(
        [sys.executable, "-c", CLI, "serve", str(site), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        line = process.stdout.readline()
        match = re.search(r"at http://127\.0\.0\.1:(\d+)/ with the headers of .*site\.caddy", line)
        assert match, line
        status, headers, _ = get(f"127.0.0.1:{match.group(1)}", "/")
        assert (status, headers["Content-Security-Policy"]) == (200, CSP)
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


def test_the_command_refuses_a_missing_site(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", CLI, "serve", str(tmp_path / "nothing"), "--port", "0"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1
    assert "no built site there (run tff-site build first)" in result.stderr


def test_the_command_says_when_the_port_is_taken(tmp_path):
    site = make_site(tmp_path / "site")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = str(taken.getsockname()[1])
        result = subprocess.run(
            [sys.executable, "-c", CLI, "serve", str(site), "--port", port],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert result.returncode == 1
    assert result.stderr.startswith("tff-site serve: "), result.stderr
    assert "Traceback" not in result.stderr


# ------------------------------------------------------------------------ the hook, alone

# Test doubles for the parts Ext uses. Their owners test the real ones; the last test below
# runs Ext with all of them on the built list.
DOUBLES = r"""
const State = (() => {
  const KEYS = Object.freeze(['rank', 'cat', 'lic', 'spacing', 'var', 'nerd', 'hide', 'redist',
    'q', 'sort', 'font']);
  let current = { rank: 'overall', cat: '', lic: [], spacing: '', var: false, nerd: false,
    hide: [], redist: false, q: '', sort: 'rank', font: '', internal: 'not a hash key' };
  const calls = [];
  const get = () => ({ ...current, lic: [...current.lic], hide: [...current.hide] });
  const set = (partial, options) => {
    calls.push([partial, options]);
    current = { ...current, ...partial };
    return true;
  };
  return { KEYS, calls, get, set };
})();
const Data = (() => {
  const index = { v: 1, n: 3, ids: ['a', 'b', 'c'], bits: new Int32Array([1, 2, 3]),
    r: { overall: { order: [0, 1, 2], top: [1, 2, 0], tier: 'AB-' } } };
  let loads = 0;
  return {
    index,
    get loads() { return loads; },
    loadIndex: () => { loads += 1; return Promise.resolve(index); },
  };
})();
const Specimens = (() => {
  let paused = false;
  return { pause() { paused = true; }, resume() { paused = false; }, get paused() { return paused; } };
})();
"""

# Stands in for Main: refresh() runs every filter over every font, as View does, then reports
# the change the way Main's onChange does.
HARNESS = r"""
const Harness = (() => {
  const log = { refreshes: 0, rows: null, errors: [], ready: [], violations: [] };
  let notify = null;
  const refresh = () => {
    log.refreshes += 1;
    const filters = Ext.filters();
    log.rows = Data.index.ids.map((id) => filters.map((f) => [f.id, f.classify(id), f.note(id)]));
    if (notify) notify({ state: State.get(), shown: 2, total: 3 });
  };
  const onChange = (fn) => { notify = fn; return () => { notify = null; }; };
  addEventListener('error', (event) => { log.errors.push(String(event.message)); });
  document.addEventListener('securitypolicyviolation', (e) => { log.violations.push(e.violatedDirective); });
  document.addEventListener('tff:list-ready', (event) => { log.ready.push(event.detail === globalThis.tff); });
  const host = { refresh, onChange, ready: Promise.resolve() };
  const first = Ext.start(host);
  window.__h = { log, Ext, State, Data, Specimens, Keys, first, again: () => Ext.start(host) };
  return log;
})();
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Hook</title>
<script type="module" src="/assets/app.js"></script></head>
<body><main id="main" tabindex="-1"><h1>Hook</h1>
<p id="ext-summary" class="ext-summary" hidden></p><ol id="list"></ol></main></body></html>
"""


@pytest.fixture(scope="module")
def hook_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    site = tmp_path_factory.mktemp("hook")
    (site / "assets").mkdir()
    parts = [(JS / name).read_text(encoding="utf-8") for name in ("00-core.js", "05-keys.js")]
    parts += [DOUBLES, (JS / "50-ext.js").read_text(encoding="utf-8"), HARNESS]
    (site / "assets" / "app.js").write_text("\n".join(parts), encoding="utf-8")
    (site / "index.html").write_text(PAGE, encoding="utf-8")
    for address in started(serve.make_server(site, port=0)):
        yield f"http://{address}"


@pytest.fixture
def hook(browser: Any, hook_url: str) -> Iterator[Any]:
    context = browser.new_context(base_url=hook_url)
    page = context.new_page()
    console: list[str] = []
    page.on("console", lambda message: console.append(message.text))
    page.goto("/")
    page.wait_for_function("() => globalThis.tff !== undefined")
    yield page
    assert not [text for text in console if re.search(r"content.security.policy", text, re.I)]
    assert page.evaluate("() => window.__h.log.violations") == []
    context.close()


def test_the_hook_is_published_once_and_frozen(hook):
    result = hook.evaluate(
        """async () => {
          const t = globalThis.tff;
          const again = await window.__h.again();
          return {
            ready: window.__h.log.ready,
            same: [again === t, (await window.__h.first) === t],
            frozen: [Object.isFrozen(t), Object.isFrozen(t.list), Object.isFrozen(t.list.specimens)],
            version: t.version,
            keys: t.keys === window.__h.Keys,
            list: Object.keys(t.list).sort(),
            ext: Object.keys(window.__h.Ext).sort(),
          };
        }"""
    )
    assert result == {
        "ready": [True],
        "same": [True, True],
        "frozen": [True, True, True],
        "version": 1,
        "keys": True,
        "list": sorted(
            [
                "addFilter",
                "removeFilter",
                "refresh",
                "getState",
                "setState",
                "index",
                "on",
                "setSummary",
                "specimens",
            ]
        ),
        "ext": ["filters", "start"],
    }


def test_add_filter_checks_its_arguments(hook):
    errors = hook.evaluate(
        """() => {
          const tries = [
            () => tff.list.addFilter('', { classify: () => 'show' }),
            () => tff.list.addFilter(7, { classify: () => 'show' }),
            () => tff.list.addFilter('x'),
            () => tff.list.addFilter('x', { classify: 'show' }),
            () => tff.list.addFilter('x', { classify: () => 'show', note: 'text' }),
          ];
          return tries.map((fn) => { try { fn(); return 'no error'; } catch (e) { return e.name; } });
        }"""
    )
    assert errors == ["TypeError"] * 5
    assert hook.evaluate("() => [window.__h.log.refreshes, window.__h.Ext.filters().length]") == [
        0,
        0,
    ]


def test_filters_run_in_the_order_added_and_readding_replaces_in_place(hook):
    result = hook.evaluate(
        """() => {
          const { addFilter } = tff.list;   // the methods work unbound
          addFilter('one', { classify: (id) => (id === 'a' ? 'hide' : 'show') });
          addFilter('two', { classify: (id) => (id === 'b' ? 'dim' : 'show'), affectsNumbering: 1 });
          addFilter('one', { classify: () => 'dim' });
          const filters = window.__h.Ext.filters();
          return {
            ids: filters.map((f) => f.id),
            numbering: filters.map((f) => f.affectsNumbering),
            frozen: Object.isFrozen(filters) && filters.every(Object.isFrozen),
            refreshes: window.__h.log.refreshes,
            rows: window.__h.log.rows.map((row) => row.map(([id, verdict]) => `${id}:${verdict}`)),
          };
        }"""
    )
    assert result == {
        "ids": ["one", "two"],
        "numbering": [False, True],
        "frozen": True,
        "refreshes": 3,
        "rows": [["one:dim", "two:show"], ["one:dim", "two:dim"], ["one:dim", "two:show"]],
    }


def test_a_filter_written_as_an_object_keeps_its_this(hook):
    result = hook.evaluate(
        """() => {
          const mine = {
            have: new Set(['b']),
            classify(id) { return this.have.has(id) ? 'hide' : 'show'; },
            note(id) { return this.have.has(id) ? null : `Not in ${this.have.size} font`; },
          };
          tff.list.addFilter('mine', mine);
          return { rows: window.__h.log.rows, errors: window.__h.log.errors };
        }"""
    )
    note = {"text": "Not in 1 font", "badge": None, "links": [], "actions": []}
    assert result == {
        "rows": [[["mine", "show", note]], [["mine", "hide", None]], [["mine", "show", note]]],
        "errors": [],
    }


def test_a_broken_filter_leaves_rows_shown_and_is_reported_once(hook):
    result = hook.evaluate(
        """() => {
          tff.list.addFilter('odd', { classify: (id) => (id === 'a' ? 'maybe' : 'show') });
          tff.list.addFilter('bad', {
            classify: () => { throw new Error('boom'); },
            note: () => { throw new Error('bang'); },
          });
          tff.list.refresh();
          return { rows: window.__h.log.rows, errors: window.__h.log.errors };
        }"""
    )
    assert result["rows"] == [[["odd", "show", None], ["bad", "show", None]]] * 3
    errors = result["errors"]
    assert len(errors) == 2, errors
    assert 'tff filter "odd": classify(): answered maybe' in errors[0]
    assert 'tff filter "bad": classify(): boom' in errors[1]


def test_notes_take_one_shape_and_unsafe_links_are_dropped(hook):
    notes = hook.evaluate(
        """() => {
          const links = [
            { label: 'Here', href: '/methodology/#source-x' },
            { label: 'Relative', href: 'about/' },
            { label: 'Secure', href: 'https://github.com/rsms/inter' },
            { label: 'Plain http', href: 'http://example.com/' },
            { label: 'Script', href: 'javascript:alert(1)' },
            { label: 'Hidden script', href: ' java\\tscript:alert(1)' },
            { label: 'Data', href: 'data:text/html,hi' },
            { label: 'Other host', href: '//example.com/x' },
            { label: 'Backslash', href: '/\\\\example.com/x' },
            { label: '', href: '/no-label/' },
            { label: 'No href' },
            null,
          ];
          const byId = {
            a: 'Installed',
            b: { badge: 'Yours', text: '  ', links, actions: [{ id: 'mark', label: 'Mark' },
              { id: '', label: 'No id' }, { id: 'x' }] },
            c: { text: '', links: [{ label: 'Bad', href: 'javascript:x' }] },
          };
          tff.list.addFilter('notes', { classify: () => 'show', note: (id) => byId[id] });
          const got = window.__h.log.rows.map((row) => row[0][2]);
          return { got, frozen: Object.isFrozen(got[1]) && Object.isFrozen(got[1].links) };
        }"""
    )
    assert notes["got"] == [
        {"text": "Installed", "badge": None, "links": [], "actions": []},
        {
            "text": None,
            "badge": "Yours",
            "links": [
                {"label": "Here", "href": "/methodology/#source-x"},
                {"label": "Relative", "href": "about/"},
                {"label": "Secure", "href": "https://github.com/rsms/inter"},
            ],
            "actions": [{"id": "mark", "label": "Mark"}],
        },
        None,
    ]
    assert notes["frozen"] is True


def test_remove_filter_refreshes_only_when_something_went(hook):
    result = hook.evaluate(
        """() => {
          tff.list.addFilter('one', { classify: () => 'hide' });
          const before = window.__h.log.refreshes;
          tff.list.removeFilter('nothing');
          const same = window.__h.log.refreshes;
          tff.list.removeFilter('one');
          return [before, same, window.__h.log.refreshes, window.__h.Ext.filters().length];
        }"""
    )
    assert result == [1, 1, 2, 0]


def test_get_state_is_a_copy_with_only_the_hash_keys(hook):
    result = hook.evaluate(
        """() => {
          const state = tff.list.getState();
          state.hide.push('changed');
          return { state, after: tff.list.getState().hide };
        }"""
    )
    assert result["state"] == {
        "rank": "overall",
        "cat": "",
        "var": False,
        "nerd": False,
        "hide": ["changed"],
        "q": "",
        "sort": "rank",
        "font": "",
    }
    assert result["after"] == []


def test_set_state_hands_a_copy_to_state(hook):
    result = hook.evaluate(
        """() => {
          const partial = { rank: 'project', hide: ['windows'], os: 'linux' };
          tff.list.setState(partial);
          tff.list.setState({ q: 'mono' }, { push: false });
          partial.hide.push('macos');
          let error = null;
          try { tff.list.setState('rank=coding'); } catch (e) { error = e.name; }
          return { calls: window.__h.State.calls, error };
        }"""
    )
    assert result == {
        "calls": [
            [{"rank": "project", "hide": ["windows"]}, {"push": True}],
            [{"q": "mono"}, {"push": False}],
        ],
        "error": "TypeError",
    }


def test_index_is_a_deep_frozen_copy_loaded_once(hook):
    result = hook.evaluate(
        """async () => {
          const [one, two] = await Promise.all([tff.list.index(), tff.list.index()]);
          const inner = window.__h.Data.index;
          return {
            same: one === two,
            loads: window.__h.Data.loads,
            frozen: [one, one.ids, one.bits, one.r, one.r.overall, one.r.overall.top]
              .every(Object.isFrozen),
            bits: one.bits,
            ids: one.ids,
            ownIsFree: !Object.isFrozen(inner) && !Object.isFrozen(inner.ids),
          };
        }"""
    )
    assert result == {
        "same": True,
        "loads": 1,
        "frozen": True,
        "bits": [1, 2, 3],
        "ids": ["a", "b", "c"],
        "ownIsFree": True,
    }


def test_change_listeners_get_a_frozen_copy_and_can_stop(hook):
    result = hook.evaluate(
        """() => {
          const seen = [];
          const off = tff.list.on('change', (change) => seen.push(change));
          tff.list.on('change', () => { throw new Error('listener bug'); });
          const last = [];
          tff.list.on('change', (change) => last.push(change.shown));
          tff.list.refresh();
          off();
          tff.list.refresh();
          const errors = [];
          for (const bad of [() => tff.list.on('changed', () => {}), () => tff.list.on('change')]) {
            try { bad(); } catch (e) { errors.push(e.name); }
          }
          const change = seen[0];
          return {
            count: seen.length,
            last,
            shape: [change.shown, change.total, Object.keys(change.state).length],
            frozen: Object.isFrozen(change) && Object.isFrozen(change.state)
              && Object.isFrozen(change.state.lic),
            internal: 'internal' in change.state,
            errors,
            reported: window.__h.log.errors.filter((m) => m.includes('listener bug')).length,
          };
        }"""
    )
    assert result == {
        "count": 1,
        "last": [2, 2],
        "shape": [2, 3, 11],  # section 9's eleven keys, "nerd" included (TASK-2)
        "frozen": True,
        "internal": False,
        "errors": ["TypeError", "TypeError"],
        "reported": 2,
    }


def test_set_summary_shows_and_hides_the_line(hook):
    result = hook.evaluate(
        """() => {
          const node = document.getElementById('ext-summary');
          const states = [];
          tff.list.setSummary('You have 41 of the top 100 in this view.');
          states.push([node.textContent, node.hidden]);
          tff.list.setSummary(null);
          states.push([node.textContent, node.hidden]);
          tff.list.setSummary(12);
          states.push([node.textContent, node.hidden]);
          tff.list.setSummary('');
          states.push([node.textContent, node.hidden]);
          return states;
        }"""
    )
    assert result == [
        ["You have 41 of the top 100 in this view.", False],
        ["", True],
        ["12", False],
        ["", True],
    ]


def test_specimens_pause_and_resume_through_the_hook(hook):
    result = hook.evaluate(
        """() => {
          const s = tff.list.specimens;
          const seen = [s.paused];
          s.pause();
          seen.push(s.paused, window.__h.Specimens.paused);
          s.resume();
          seen.push(s.paused);
          return seen;
        }"""
    )
    assert result == [False, True, True, False]


# ------------------------------------------------------------------------ the hook, on the list


def test_the_hook_drives_the_built_list(page):
    # Milestone 3 may start before or after the list: it gets exactly one tff:list-ready,
    # carrying the published hook.
    page.add_init_script(
        "document.addEventListener('tff:list-ready', (e) => {"
        " (window.__ready ||= []).push(e.detail === globalThis.tff && e.detail !== undefined); });"
    )
    page.goto("/")
    page.wait_for_function("() => globalThis.tff !== undefined")
    ready = page.evaluate(
        "() => new Promise((done) => setTimeout(() => done(window.__ready), 100))"
    )
    assert ready == [True]
    ids = page.eval_on_selector_all("#list > li.font", "rows => rows.map((r) => r.dataset.id)")
    hidden, dimmed, noted = ids[0], ids[1], ids[2]
    page.evaluate(
        """([hidden, dimmed, noted]) => {
          window.__changes = [];
          tff.list.on('change', (change) => window.__changes.push(change));
          tff.list.addFilter('t', {
            classify: (id) => (id === hidden ? 'hide' : id === dimmed ? 'dim' : 'show'),
            note: (id) => (id === noted ? {
              badge: 'Yours', text: 'Installed',
              links: [{ label: 'Why', href: '/methodology/' }, { label: 'Bad', href: 'javascript:x' }],
              actions: [{ id: 'mark', label: 'Mark' }],
            } : null),
          });
        }""",
        [hidden, dimmed, noted],
    )
    assert page.locator(f"#list > #font-{hidden}").count() == 0
    assert "is-dim" in page.locator(f"#font-{dimmed}").get_attribute("class")
    slot = page.locator(f'#font-{noted} .ext[data-filter="t"]')
    assert slot.locator(".ext-badge").inner_text() == "Yours"
    assert slot.locator(".ext-note").inner_text() == "Installed"
    assert slot.locator("a.ext-link").evaluate_all("links => links.map((a) => a.href)") == [
        page.url.split("#")[0].rstrip("/") + "/methodology/"
    ]
    action = page.evaluate(
        """(noted) => new Promise((resolve) => {
          document.addEventListener('tff:row-action', (e) => resolve(e.detail), { once: true });
          document.querySelector(`#font-${noted} button.ext-action[data-action="mark"]`).click();
        })""",
        noted,
    )
    assert action == {"filterId": "t", "fontId": noted, "actionId": "mark"}

    # setState is validated like the hash: an unknown category falls back to any, and a key
    # that isn't the list's own never reaches the state.
    page.evaluate("() => tff.list.setState({ sort: 'name', cat: 'no-such-category', os: 'x' })")
    page.wait_for_function("() => location.hash.includes('sort=name')")
    assert "cat=" not in page.evaluate("() => location.hash")
    change = page.evaluate("() => window.__changes.at(-1)")
    assert (change["state"]["sort"], change["state"]["cat"]) == ("name", "")
    state = page.evaluate("() => tff.list.getState()")
    assert (state["sort"], state["cat"], "os" in state) == ("name", "", False)
    paused = page.evaluate(
        """() => {
          const s = tff.list.specimens;
          const seen = [s.paused];
          s.pause();
          seen.push(s.paused);
          s.resume();
          return [...seen, s.paused];
        }"""
    )
    assert paused == [False, True, False]
    page.evaluate("() => tff.list.removeFilter('t')")
    assert page.locator(f"#list > #font-{hidden}").count() == 1
    assert page.locator(".ext").count() == 0

    index = page.evaluate(
        "async () => { const i = await tff.list.index(); return [Object.isFrozen(i.ids), i.ids]; }"
    )
    assert index[0] is True
    assert sorted(index[1]) == sorted(ids)
    page.evaluate("() => tff.list.setSummary('You have 3 of the top 100 in this view.')")
    assert page.locator("#ext-summary").inner_text() == "You have 3 of the top 100 in this view."
