"""``tff-site check``: size budgets and a CSP lint of a built site.

Sizes are gzip level 9 (``gzip_size``), in decimal kilobytes:

- the list page's HTML + CSS + JS: at most ``PAGE_MAX``. That is ``index.html`` plus every
  stylesheet and script it references;
- the list index: at most ``LIST_INDEX_MAX``;
- the details payload: at most ``DETAILS_MAX`` (loaded lazily; shard it if over). Every
  ``assets/details*.json`` file is held to it, so shards pass one by one;
- specimens: at least half at most ``SPECIMEN_HALF_MAX``, none over ``SPECIMEN_MAX`` (the
  owner's site ruling of 2026-09-30, specimen_max_size), all together under
  ``SPECIMENS_TOTAL_RAW_MAX`` uncompressed. ``tff_catalog.specimens.budget`` measures the
  same way, so a site whose specimens passed the specimens stage passes here too.

The files come from the list page itself: ``<link rel="stylesheet" href>``,
``<script src>`` and ``#list``'s ``data-index`` and ``data-details`` (site/CONTRACT.md
sections 2 and 4). A reference that names no file in the site is a problem too.

The CSP lint fails on an inline ``<script>`` without ``src``, a ``<style>`` element, any
``style=`` or ``on*=`` attribute, a ``<form>``, and ``rel=prefetch``. Under the same policy
(``ops/caddy/site.caddy``) it also fails on a ``<base>`` element (``base-uri 'none'``), a
``javascript:`` URL, a frame, plugin or media element (``default-src 'none'`` leaves no
``frame-src``, ``object-src`` or ``media-src``), ``rel=prerender``, and a script,
stylesheet, image, font or other resource loaded from another site (every fetch directive
is ``'self'``), including a ``preconnect`` or ``dns-prefetch`` hint that would contact one.
It runs on every ``.html`` file.
"""

import gzip
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

KB = 1000
PAGE_MAX = 100 * KB
LIST_INDEX_MAX = 100 * KB
DETAILS_MAX = 150 * KB
SPECIMEN_HALF_MAX = 5 * KB
SPECIMEN_MAX = 16 * KB
SPECIMENS_TOTAL_RAW_MAX = 10_000 * KB

LIST_PAGE = "index.html"
SPECIMENS_DIR = "assets/specimens"
DETAILS_GLOB = "assets/details*.json"

# Attributes that make the browser fetch something as the page loads, by element. <a href>,
# <link rel=canonical> and <meta content> are left out: nothing is fetched until a click.
# Frames, plugins and media are left out: _BLOCKED_ELEMENTS fails them whatever they load.
_RESOURCE_ATTRS = {
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "input": ("src",),
    "image": ("href", "xlink:href", "src"),  # SVG's <image>; the HTML parser reads it as <img>
}
# Elements the policy blocks whatever their URL: default-src 'none' is their fallback.
_BLOCKED_ELEMENTS = {
    "iframe": "frame-src",
    "frame": "frame-src",
    "object": "object-src",
    "embed": "object-src",
    "video": "media-src",
    "audio": "media-src",
    "track": "media-src",
}
# <link rel> values that fetch their href, or contact its host (the connection hints).
_FETCHING_RELS = frozenset(
    {
        "stylesheet",
        "icon",
        "apple-touch-icon",
        "preload",
        "modulepreload",
        "manifest",
        "preconnect",
        "dns-prefetch",
    }
)
# <link rel> values that load pages the visitor never asked for.
_SPECULATIVE_RELS = frozenset({"prefetch", "prerender"})
# Attributes that take a URL a visitor or the browser may follow (for the javascript: check).
_URL_ATTRS = frozenset(
    {"href", "src", "action", "formaction", "data", "poster", "cite", "background", "xlink:href"}
)
# The URL parser drops these before reading a scheme, so "java\tscript:" is still a scheme.
_URL_NOISE = re.compile(r"[\x00-\x20]")
_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Problem:
    """One failed check: the file (relative to the site root) and what is wrong."""

    path: str
    message: str


def gzip_size(data: bytes) -> int:
    """Return the size of ``data`` compressed with gzip level 9 (fixed mtime)."""
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def check(site_dir: Path) -> list[Problem]:
    """Run every budget and the CSP lint on a built site; an empty list means it passes."""
    site = Path(site_dir)
    if not site.is_dir():
        return [Problem(".", f"{site}: no built site here (run tff-site build first)")]
    problems: list[Problem] = []
    details = {path.resolve() for path in site.glob(DETAILS_GLOB)}
    page = site / LIST_PAGE
    if page.is_file():
        refs = _page_refs(page.read_text(encoding="utf-8", errors="replace"))
        problems += _check_page(site, page, refs)
        index, found = _payload(site, refs.index, "list index", "data-index")
        problems += found
        if index is not None:
            problems += _check_size(site, index, "list index", LIST_INDEX_MAX)
        payload, found = _payload(site, refs.details, "details payload", "data-details")
        problems += found
        if payload is not None:
            details.add(payload)
    else:
        problems.append(Problem(LIST_PAGE, "missing: the list page"))
    for path in sorted(details):
        problems += _check_size(site, path, "details payload", DETAILS_MAX, hint=" (shard it)")
    problems += _check_specimens(site)
    for path in sorted(site.rglob("*.html")):
        rel = path.relative_to(site).as_posix()
        problems += lint_html(rel, path.read_text(encoding="utf-8", errors="replace"))
    return problems


def lint_html(path: str, html: str) -> list[Problem]:
    """Return CSP-lint problems in one HTML file."""
    parser = _Lint(path)
    parser.feed(html)
    parser.close()
    return parser.problems


# ---- budgets ----------------------------------------------------------------------------


@dataclass
class _Refs:
    """What the list page loads: its stylesheets and scripts, and the two JSON payloads."""

    styles: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    index: str | None = None
    details: str | None = None


class _RefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs = _Refs()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {name: value or "" for name, value in attrs}
        if tag == "link" and "stylesheet" in _rel_tokens(a.get("rel", "")) and a.get("href"):
            self.refs.styles.append(a["href"])
        elif tag == "script" and a.get("src"):
            self.refs.scripts.append(a["src"])
        elif a.get("id") == "list":
            self.refs.index = a.get("data-index") or None
            self.refs.details = a.get("data-details") or None

    handle_startendtag = handle_starttag


def _page_refs(html: str) -> _Refs:
    parser = _RefParser()
    parser.feed(html)
    parser.close()
    return parser.refs


def _local_file(site: Path, url: str) -> Path | None:
    """The file a same-site URL path names, or None (another site, or outside the root)."""
    if not url.startswith("/") or url.startswith("//"):
        return None
    rel = url.split("#", 1)[0].split("?", 1)[0].lstrip("/")
    target = (site / rel).resolve()
    root = site.resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _check_page(site: Path, page: Path, refs: _Refs) -> list[Problem]:
    problems: list[Problem] = []
    html = gzip_size(page.read_bytes())
    parts = {"HTML": html, "CSS": 0, "JS": 0}
    for kind, urls in (("CSS", refs.styles), ("JS", refs.scripts)):
        for url in urls:
            target = _local_file(site, url)
            if target is None or not target.is_file():
                problems.append(Problem(LIST_PAGE, f"{url}: referenced, but not in the site"))
                continue
            parts[kind] += gzip_size(target.read_bytes())
    total = sum(parts.values())
    if total > PAGE_MAX:
        split = ", ".join(f"{kind} {_kb(size)}" for kind, size in parts.items())
        problems.append(
            Problem(
                LIST_PAGE,
                f"the list page's HTML + CSS + JS is {_kb(total)} gzip -9 ({split}), "
                f"over the {_kb(PAGE_MAX)} budget",
            )
        )
    return problems


def _payload(
    site: Path, url: str | None, what: str, attribute: str
) -> tuple[Path | None, list[Problem]]:
    """The file ``#list[<attribute>]`` names, or None and the problem."""
    if url is None:
        return None, [Problem(LIST_PAGE, f"#list has no {attribute}: can't find the {what}")]
    target = _local_file(site, url)
    if target is None or not target.is_file():
        return None, [Problem(LIST_PAGE, f"{url}: the {what} is referenced, but not in the site")]
    return target, []


def _check_size(site: Path, path: Path, what: str, limit: int, hint: str = "") -> list[Problem]:
    size = gzip_size(path.read_bytes())
    if size <= limit:
        return []
    rel = path.resolve().relative_to(site.resolve()).as_posix()
    return [Problem(rel, f"the {what} is {_kb(size)} gzip -9, over the {_kb(limit)} budget{hint}")]


def _check_specimens(site: Path) -> list[Problem]:
    folder = site / SPECIMENS_DIR
    paths = sorted(folder.glob("*.svg")) if folder.is_dir() else []
    if not paths:
        return []
    problems: list[Problem] = []
    small = total = 0
    for path in paths:
        blob = path.read_bytes()
        total += len(blob)
        size = gzip_size(blob)
        if size <= SPECIMEN_HALF_MAX:
            small += 1
        if size > SPECIMEN_MAX:
            rel = path.relative_to(site).as_posix()
            problems.append(
                Problem(
                    rel,
                    f"specimen is {_kb(size)} gzip -9, over the {_kb(SPECIMEN_MAX)} cap "
                    "(render the family name only)",
                )
            )
    if small * 2 < len(paths):
        problems.append(
            Problem(
                SPECIMENS_DIR,
                f"{small} of {len(paths)} specimens are at most {_kb(SPECIMEN_HALF_MAX)} gzip -9; "
                "at least half must be",
            )
        )
    if total >= SPECIMENS_TOTAL_RAW_MAX:
        problems.append(
            Problem(
                SPECIMENS_DIR,
                f"specimens total {_kb(total)} uncompressed, over the "
                f"{_kb(SPECIMENS_TOTAL_RAW_MAX)} budget",
            )
        )
    return problems


def _kb(size: int) -> str:
    return f"{size / KB:.1f} KB"


# ---- CSP lint ---------------------------------------------------------------------------


def _rel_tokens(value: str) -> set[str]:
    return {token.lower() for token in value.split()}


def _is_other_site(url: str) -> bool:
    """True for an absolute or scheme-relative URL (anything but a path on this site)."""
    raw = _URL_NOISE.sub("", url)
    return raw.startswith(("//", "\\\\")) or bool(_SCHEME.match(raw))


def _is_javascript_url(url: str) -> bool:
    return _URL_NOISE.sub("", url).lower().startswith("javascript:")


def _srcset_urls(value: str) -> Iterable[str]:
    for candidate in value.split(","):
        words = candidate.split()
        if words:
            yield words[0]


class _Lint(HTMLParser):
    def __init__(self, path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.path = path
        self.problems: list[Problem] = []

    def _add(self, message: str) -> None:
        line = self.getpos()[0]
        self.problems.append(Problem(self.path, f"line {line}: {message}"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._check(tag, attrs)

    def _check(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        a = {name: value or "" for name, value in attrs}
        if tag == "script" and not a.get("src", "").strip():
            self._add("inline <script> (script-src 'self' allows only <script src>)")
        elif tag == "style":
            self._add("<style> element (style-src 'self' allows only stylesheet files)")
        elif tag == "form":
            self._add("<form> (form-action 'none'; use <search> and fieldsets)")
        elif tag == "base":
            self._add("<base> element (base-uri 'none')")
        elif tag in _BLOCKED_ELEMENTS:
            self._add(
                f"<{tag}> element ({_BLOCKED_ELEMENTS[tag]} falls back to default-src 'none')"
            )
        elif tag == "link":
            rels = _rel_tokens(a.get("rel", ""))
            for rel in sorted(rels & _SPECULATIVE_RELS):
                self._add(f'rel="{rel}" (a request the visitor never asked for)')
            if rels & _FETCHING_RELS and _is_other_site(a.get("href", "")):
                self._add(f'<link rel="{a.get("rel")}"> not from this site: {a.get("href")}')
        for name, value in attrs:
            if name == "style":
                self._add(f"style attribute on <{tag}> (style-src 'self' blocks inline styles)")
            elif name.startswith("on"):
                self._add(f"{name} attribute on <{tag}> (inline event handlers are blocked)")
            elif name in _URL_ATTRS and _is_javascript_url(value or ""):
                self._add(f"javascript: URL in <{tag} {name}>")
        for name in _RESOURCE_ATTRS.get(tag, ()):
            value = a.get(name, "")
            urls = _srcset_urls(value) if name == "srcset" else [value] if value else []
            for url in urls:
                if _is_other_site(url):
                    self._add(f"<{tag} {name}> not from this site: {url}")
