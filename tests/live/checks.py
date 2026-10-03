"""The live site's checks that need no browser (Milestone 2 step 9; design-m2 §6, "Live only").

Plain functions over HTTP responses, so the live tests (``tests/live``) and an offline test of
a local build (``tests/site/test_privacy.py``) run the same code:

- ``crawl(client)`` fetches every kind of file the site serves: the sitemap's pages, a missing
  page (404), the stylesheet, the script, both JSON payloads, a specimen (or ``favicon.svg``),
  ``robots.txt``, ``sitemap.xml`` and ``version.txt``. Every request sends ``Accept: text/html``,
  because Cloudflare injects its beacon only into responses to requests that accept HTML
  (ops/SERVER.md section G).
- ``header_problems`` wants, on every response, site.caddy's security headers
  (``Content-Security-Policy`` and the rest; HSTS need only be present), site.caddy's
  ``Cache-Control`` for the route, no ``Set-Cookie``, ``NEL``, ``Report-To``,
  ``Reporting-Endpoints`` or ``Speculation-Rules``, no ``Link`` header that prefetches or
  preconnects, and ``X-Robots-Tag: noindex`` on the test site's pages only.
- ``html_problems`` wants no ``cloudflareinsights``, ``data-cf-beacon`` or ``/cdn-cgi/`` in any
  HTML, no script but the site's own module, and no ``<link>`` that prefetches or preconnects.
- ``robots_problems`` wants ``robots.txt`` byte-equal to the built file (Cloudflare's managed
  robots.txt would add lines); ``version_problems`` wants ``version.txt`` in the contract's
  format, naming the expected commit, and agreeing with the list index.
- ``cloudflare_location`` reads ``/cdn-cgi/trace``: Web Analytics skips visitors in the EU, EEA,
  UK and Switzerland, so a clean result from there proves nothing about the beacon.

A Cloudflare challenge page raises ``CloudflareChallenge``: a setting on Cloudflare's side
blocked the test client, which is its own kind of failure (design-m2 §9, item 14).
"""

import difflib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx

from tff_site.serve import CaddyHeaders

USER_AGENT = "purelyfreefonts-live-test/1 (+https://github.com/byronshock/purelyfreefonts)"
ACCEPT_HTML = "text/html"
NOT_FOUND_PATH = "/no-such-page-privacy-check/"
IMMUTABLE_PREFIX = "/assets/"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

# The kinds of response step 9 wants the CSP on; the crawl must find one of each.
CSP_KINDS = frozenset({"html", "js", "json", "svg", "404"})
TRACKING_HEADERS = ("set-cookie", "nel", "report-to", "reporting-endpoints", "speculation-rules")
PREFETCH_RELS = frozenset({"prefetch", "prerender", "dns-prefetch", "preconnect", "next"})
PREFETCH_LINK_HEADER = re.compile(r"rel\s*=\s*\"?[^\";,]*\b(" + "|".join(PREFETCH_RELS) + r")\b")
# Cloudflare's Web Analytics beacon, and anything else it injects (email obfuscation, Rocket
# Loader, challenge scripts) lives under /cdn-cgi/.
BEACON = re.compile(r"cloudflareinsights|data-cf-beacon|/cdn-cgi/", re.IGNORECASE)
SCRIPT_SRC = re.compile(r"^/assets/app\.[0-9a-f]{10}\.js$")
VERSION_FIELDS = ("commit", "run_date", "method_version", "catalog_sha256", "schema")
VERSION_PATTERNS = {
    "commit": re.compile(r"[0-9a-f]{40}"),
    "run_date": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "method_version": re.compile(r"\S+"),
    "catalog_sha256": re.compile(r"[0-9a-f]{64}"),
    "schema": re.compile(r"catalog-site/\d+"),
}
# Where Cloudflare's Web Analytics doesn't inject by default: the EU, the EEA, the UK and
# Switzerland (ops/SERVER.md section G).
NO_BEACON_LOCATIONS = frozenset(
    {
        *("AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE"),
        *("IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"),
        *("IS", "LI", "NO", "GB", "CH"),
    }
)


class CloudflareChallenge(AssertionError):
    """Cloudflare answered with a challenge page instead of the site."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """One response: its kind (``html``, ``404``, ``js``, ``css``, ``json``, ``svg``, ``txt``,
    ``xml``), the path asked for, the status, the headers and the decoded body."""

    kind: str
    path: str
    status: int
    headers: httpx.Headers
    body: bytes

    @property
    def where(self) -> str:
        return f"{self.path} ({self.status})"


def client(base_url: str, **kwargs: Any) -> httpx.Client:
    """An HTTP client for the site at ``base_url`` that asks for HTML, like a browser."""
    headers = {"Accept": ACCEPT_HTML, "User-Agent": USER_AGENT}
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers=headers,
        follow_redirects=False,
        timeout=30.0,
        **kwargs,
    )


def get(http: httpx.Client, path: str, kind: str) -> Fetched:
    """Fetch ``path``; raise ``CloudflareChallenge`` on a challenge page."""
    response = http.get(path)
    body = response.content
    challenged = response.headers.get("cf-mitigated", "").lower() == "challenge" or (
        response.status_code in (403, 429, 503)
        and (b"challenge-platform" in body or b"Just a moment" in body)
    )
    if challenged:
        raise CloudflareChallenge(
            f"Cloudflare answered {path} with a challenge ({response.status_code}), not the "
            "site: a Cloudflare security setting blocked the test client"
        )
    return Fetched(kind, path, response.status_code, response.headers, body)


class _Tags(HTMLParser):
    """Every start tag of a document, as (name, attributes)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, {name: value or "" for name, value in attrs}))


def tags(body: bytes) -> list[tuple[str, dict[str, str]]]:
    """The start tags of an HTML body."""
    parser = _Tags()
    parser.feed(body.decode("utf-8", "replace"))
    parser.close()
    return parser.tags


def sitemap_paths(body: bytes) -> list[str]:
    """The paths of the sitemap's ``<loc>`` URLs, in order (they name the canonical host)."""
    root = ET.fromstring(body)
    return [urlsplit((loc.text or "").strip()).path or "/" for loc in root.iter(f"{SITEMAP_NS}loc")]


def crawl(http: httpx.Client) -> tuple[list[Fetched], list[str]]:
    """Fetch one of every kind of file the site serves; return them and what was missing."""
    problems: list[str] = []
    home = get(http, "/", "html")
    fetched = [home]
    found = tags(home.body)
    stylesheets = [
        a["href"] for t, a in found if t == "link" and "stylesheet" in a.get("rel", "").split()
    ]
    scripts = [a["src"] for t, a in found if t == "script" and a.get("src")]
    lists = [a for t, a in found if t == "ol" and a.get("id") == "list"]
    specimens = [a["data-src"] for t, a in found if t == "span" and a.get("data-src")]
    if not lists:
        problems.append("/ has no #list: this isn't the list page")
    wanted = [(path, "css") for path in stylesheets] + [(path, "js") for path in scripts]
    for ol in lists[:1]:
        wanted += [(ol.get("data-index", ""), "json"), (ol.get("data-details", ""), "json")]
    wanted += [(path, "svg") for path in specimens[:1]]
    wanted += [("/favicon.svg", "svg"), ("/robots.txt", "txt"), ("/version.txt", "txt")]
    sitemap = get(http, "/sitemap.xml", "xml")
    fetched.append(sitemap)
    pages: list[str] = []
    if sitemap.status == 200:
        pages = [path for path in sitemap_paths(sitemap.body) if path != "/"]
    else:
        problems.append(f"/sitemap.xml answered {sitemap.status}")
    wanted += [(path, "html") for path in pages] + [(NOT_FOUND_PATH, "404")]
    for path, kind in wanted:
        if not path.startswith("/") or path.startswith("//"):
            problems.append(f"/ names {path!r} ({kind}), which is not a path on this site")
            continue
        fetched.append(get(http, path, kind))
    missing = CSP_KINDS - {f.kind for f in fetched}
    if missing:
        problems.append(f"the crawl found no {', '.join(sorted(missing))} response")
    return fetched, problems


def header_problems(
    fetched: Iterable[Fetched], expected: CaddyHeaders, *, staging: bool
) -> list[str]:
    """What is wrong with the responses' headers (see the module docstring)."""
    problems: list[str] = []
    for f in fetched:
        headers = f.headers
        want_status = 404 if f.kind == "404" else 200
        if f.status != want_status:
            problems.append(f"{f.where}: status {f.status}, not {want_status}")
        for name, value in expected.security.items():
            got = headers.get(name)
            if name.lower() == "strict-transport-security":
                if got is None:
                    problems.append(f"{f.where}: no {name}")
            elif got != value:
                problems.append(f"{f.where}: {name} is {got!r}, not site.caddy's {value!r}")
        route = expected.revalidate
        if f.path.startswith(IMMUTABLE_PREFIX) and f.status == 200:
            route = expected.immutable
        got = headers.get("cache-control")
        if got != route["Cache-Control"]:
            problems.append(f"{f.where}: Cache-Control is {got!r}, not {route['Cache-Control']!r}")
        problems += [f"{f.where}: sends {name}" for name in TRACKING_HEADERS if name in headers]
        link = headers.get("link", "")
        if PREFETCH_LINK_HEADER.search(link.lower()):
            problems.append(f"{f.where}: a Link header prefetches or preconnects: {link!r}")
        robots = headers.get("x-robots-tag")
        if staging and f.status == 200 and "noindex" not in (robots or "").lower():
            problems.append(f"{f.where}: the test site sends no X-Robots-Tag: noindex")
        if not staging and robots is not None:
            problems.append(f"{f.where}: X-Robots-Tag {robots!r} outside the test site")
    return problems


def html_problems(fetched: Iterable[Fetched]) -> list[str]:
    """What is wrong with the HTML: injected Cloudflare code, other scripts, prefetch links."""
    problems: list[str] = []
    for f in fetched:
        if f.kind not in ("html", "404"):
            continue
        text = f.body.decode("utf-8", "replace")
        problems += [f"{f.where}: contains {m.group(0)!r}" for m in BEACON.finditer(text)]
        for tag, attrs in tags(f.body):
            if tag == "script" and not SCRIPT_SRC.match(attrs.get("src", "")):
                problems.append(f"{f.where}: a script that isn't the site's: {attrs}")
            if tag == "link" and PREFETCH_RELS & set(attrs.get("rel", "").lower().split()):
                problems.append(f"{f.where}: a link that prefetches or preconnects: {attrs}")
    return problems


def robots_problems(served: Fetched, built: bytes) -> list[str]:
    """``robots.txt`` must be the built file, byte for byte."""
    if served.status != 200:
        return [f"/robots.txt answered {served.status}"]
    if served.body == built:
        return []
    diff = difflib.unified_diff(
        built.decode("utf-8", "replace").splitlines(),
        served.body.decode("utf-8", "replace").splitlines(),
        "built robots.txt",
        "served robots.txt",
        lineterm="",
    )
    return ["/robots.txt differs from the built file:\n" + "\n".join(diff)]


def parse_version(text: str) -> dict[str, str]:
    """``version.txt``'s ``key=value`` lines, in order."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        fields[key] = value if sep else ""
    return fields


def version_problems(
    text: str, *, expect_commit: str | None, index: Mapping[str, Any] | None = None
) -> list[str]:
    """``version.txt`` must have the contract's fields in order, name ``expect_commit`` (when
    given), and agree with the list index's ``commit`` and ``run_date`` (when given)."""
    fields = parse_version(text)
    problems: list[str] = []
    if tuple(fields) != VERSION_FIELDS:
        problems.append(f"version.txt has the fields {list(fields)}, not {list(VERSION_FIELDS)}")
    for key, pattern in VERSION_PATTERNS.items():
        if key in fields and not pattern.fullmatch(fields[key]):
            problems.append(f"version.txt: {key}={fields[key]!r} is malformed")
    if expect_commit is not None and fields.get("commit") != expect_commit:
        problems.append(f"version.txt: commit={fields.get('commit')!r}, not {expect_commit!r}")
    if index is not None:
        problems += [
            f"the list index says {key}={index.get(key)!r}, version.txt {fields.get(key)!r}"
            for key in ("commit", "run_date")
            if index.get(key) != fields.get(key)
        ]
    return problems


def cloudflare_location(http: httpx.Client) -> str | None:
    """The ``loc=`` of Cloudflare's ``/cdn-cgi/trace``, or None when the site isn't behind
    Cloudflare."""
    response = http.get("/cdn-cgi/trace")
    if response.status_code != 200:
        return None
    for line in response.text.splitlines():
        if line.startswith("loc="):
            return line.removeprefix("loc=").strip().upper()
    return None
