"""The methodology, privacy and about pages: Markdown to HTML, and the methodology extract.

Markdown is rendered with ``MarkdownIt("commonmark", {"html": False}).enable("table")`` plus
``mdit_py_plugins.anchors.anchors_plugin`` for stable heading ids. ``html=False`` means page
text can never inject a ``<script>`` or ``style`` attribute past the CSP. Strikethrough is on
too, because the methodology uses it; an image from another site is refused, because the
pages load only their own files (``img-src 'self'``).

``/methodology/`` is generated from ``docs/ranking-methodology.md`` so the two can't drift.
It takes the numbered ``## N.`` sections in ``METHODOLOGY_SECTIONS``; a missing section fails
the build loudly. From them the page shows:

- ``plain_words``: all of section 1 without its heading, its quote unwrapped;
- ``desktop_views``: the prose of section 5's ``### Desktop: two views``, up to its table;
- ``confidence``: section 6's paragraph that starts ``**Confidence.**``, with its list;
- ``biases``: all of section 11 without its heading.

The page supplies its own headings, so the document's headings may change (M1 step 17 drops
"(draft for the public page)") without moving an anchor. Relative links in the extract point
to the file on GitHub. Everything else on the page comes from the catalog: the views, bands,
tiers, source credits (one ``#source-<id>`` anchor per source, which the details panel links
to), data license, run date, method version and stale sources.

``/privacy/`` and ``/about/`` are ``site/content/<name>.md``: YAML front matter with exactly
``title`` (the page's ``<h1>``) and ``description``, then Markdown that starts its headings at
``##``. ``{{ name }}`` in the Markdown stands for one of ``content_links()``, so addresses live
in code once; an unknown or malformed placeholder fails the build. The 404 page gets a
``page`` (not canonical) and ``heading`` too, so its title reads like every other page's.
``robots.txt`` and ``sitemap.xml`` need no context of their own: ``tff_site.build`` gives them
a plain ``page``.

A heading in page text gets an id, which must not be empty, repeated on the page, one of the
templates' own ids (``TEMPLATE_IDS``) or start with ``source-``, which is kept for the source
credits; otherwise the build fails, rather than shipping a page with a broken anchor.
"""

import posixpath
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token
from markupsafe import Markup
from mdit_py_plugins.anchors import anchors_plugin

from tff_site import data

REPO_ROOT = Path(__file__).resolve().parents[2]
METHODOLOGY_MD = REPO_ROOT / "docs" / "ranking-methodology.md"
CONTENT_DIR = REPO_ROOT / "site" / "content"

# §1 in plain words, §5 the rankings (the two desktop views), §6 tiers, §11 known biases.
METHODOLOGY_SECTIONS: tuple[int, ...] = (1, 5, 6, 11)
# The parts of sections 5 and 6 the page shows (see the module docstring).
DESKTOP_VIEWS_HEADING = "Desktop: two views"
CONFIDENCE_LEAD = "**Confidence.**"

# build.SITE_NAME; build imports this module, so it can't be imported from there.
SITE_NAME = "Truly Free Fonts"
# A content page's <title> is its <h1>, an en dash and the site name.
TITLE_SEPARATOR = " \N{EN DASH} "
BLOB_URL = f"{data.REPO_URL}/blob/main"
METHODOLOGY_URL = f"{BLOB_URL}/docs/ranking-methodology.md"

METHODOLOGY_PAGE = {
    "path": "/methodology/",
    "heading": "How we rank",
    "description": (
        "How Truly Free Fonts ranks fonts: the method in plain words, how to read ranks, "
        "bands and tiers, known biases, and credits for every data source."
    ),
}
# Page text for the 404 page (owner approval: M2 step 7); build.NOT_FOUND_PAGE is the fallback.
NOT_FOUND_PAGE = {
    "path": "/404.html",
    "heading": "Page not found",
    "description": "There is no page at this address on Truly Free Fonts.",
}
# URL path -> Markdown file in CONTENT_DIR.
CONTENT_PAGES = {"/about/": "about.md", "/privacy/": "privacy.md"}
FRONT_MATTER_KEYS = ("title", "description")
# Source credits are grouped by survey, in this order (the template names the groups).
SURVEYS = ("desktop", "project")
LICENSE_NAMES = {"CC-BY-SA-4.0": "CC BY-SA 4.0"}
# The ids the templates give elements; tests/site/test_pages.py keeps this list equal to them.
TEMPLATE_IDS = frozenset(
    {
        # base.html.j2
        "main",
        "feedback",
        "tip",
        # privacy.html.j2
        "tips",
        # methodology.html.j2
        "toc-h",
        "in-plain-words",
        "desktop-views",
        "reading-ranks",
        "ranks",
        "scores",
        "not-ranked",
        "evidence-states",
        "tiers",
        "biases",
        "sources",
        "sources-desktop",
        "sources-project",
        "licenses",
        "this-run",
    }
)
# #source-<id> is each source's credit (site/CONTRACT.md section 8).
SOURCE_ANCHOR = "source-"

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_NUMBERED = re.compile(r"^(\d+)\.(?:\s|$)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_QUOTE = re.compile(r"^ {0,3}> ?")
_TABLE_ROW = re.compile(r"^ {0,3}\|")
_BLANK_LINES = re.compile(r"\n[ \t]*\n")
# Anything between double braces, so a typo ({{ issues-url }}) fails instead of showing.
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}")
_PLACEHOLDER_NAME = re.compile(r"\s*([a-z_]+)\s*")
_BRACES = re.compile(r"\{\{|\}\}|\{%|%\}")
_HEADING_ID = re.compile(r'<h[1-6] id="([^"]*)"')


class PageError(ValueError):
    """A content page can't be built: a methodology part or a content file is missing or bad."""


def render_markdown(
    text: str,
    *,
    refuse_h1: bool = False,
    link: Callable[[str], str] | None = None,
    image: Callable[[str, str], Mapping[str, str]] | None = None,
) -> str:
    """Render Markdown to HTML with raw HTML disabled and anchored headings.

    Headings from ``##`` to ``####`` get GitHub-style ids (``## Check for yourself`` becomes
    ``id="check-for-yourself"``), unique within one call; ``#`` is left alone, since each page
    has its own ``<h1>``, or refused with ``refuse_h1``. Raises ``ValueError`` for an image on
    another site. ``link(href)`` returns each link's new ``href``; ``image(src, alt)`` returns
    attributes to set on each ``<img>`` (``src``, ``width`` …) and may raise ``ValueError``
    to refuse it (the blog's hook, ``tff_site.blog``).
    """
    return _render(text, link=link, refuse_h1=refuse_h1, image=image)


def extract_sections(
    markdown: str, numbers: tuple[int, ...] = METHODOLOGY_SECTIONS
) -> dict[int, str]:
    """Return each numbered ``## N.`` section's Markdown (heading included), by number.

    A section runs to the next heading of level 1 or 2; headings inside fenced code don't
    count. Raises ``KeyError`` naming the first missing section, and ``ValueError`` if a
    number heads two sections.
    """
    found: dict[int, list[str]] = {}
    current: list[str] | None = None
    for line, fenced in _scan(markdown):
        heading = None if fenced else _ATX.match(line)
        if heading and len(heading[1]) <= 2:
            current = None
            numbered = _NUMBERED.match(heading[2] or "") if len(heading[1]) == 2 else None
            if numbered:
                number = int(numbered[1])
                if number in found:
                    raise ValueError(f"'## {number}.' heads two sections")
                current = found[number] = []
        if current is not None:
            current.append(line)
    for number in numbers:
        if number not in found:
            raise KeyError(f"no '## {number}.' section")
    return {number: "\n".join(found[number]).strip() + "\n" for number in numbers}


def page_contexts(doc: dict) -> dict[str, dict]:
    """Return the template context of each content page, keyed by URL path (``/privacy/``)."""
    contexts = {"/methodology/": methodology_context(doc)}
    for path, name in CONTENT_PAGES.items():
        contexts[path] = content_context(path, CONTENT_DIR / name)
    contexts[NOT_FOUND_PAGE["path"]] = {
        "page": _page(NOT_FOUND_PAGE["path"], NOT_FOUND_PAGE, canonical=False),
        "heading": NOT_FOUND_PAGE["heading"],
    }
    return dict(sorted(contexts.items()))


# ------------------------------------------------------------------------ methodology


def methodology_context(doc: Mapping[str, Any], source: Path | None = None) -> dict[str, Any]:
    """The ``/methodology/`` context: ``page``, ``heading`` and ``method`` (see the template)."""
    source = METHODOLOGY_MD if source is None else source
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise PageError(f"{_shown(source)}: {exc.strerror or exc}") from exc
    where = _shown(source)
    parts = methodology_parts(text, where=where)
    sources = [_source(s) for s in doc["sources"]]
    unknown = sorted({s["survey"] for s in sources} - set(SURVEYS))
    if unknown:
        raise PageError(f"sources in survey {unknown}: /methodology/ credits only {SURVEYS}")
    html = {name: _render(text, link=_doc_link) for name, text in parts.items()}
    _check_heading_ids(html.values(), where=where)
    spdx = doc["data_license"]["spdx"]
    return {
        "page": _page(METHODOLOGY_PAGE["path"], METHODOLOGY_PAGE),
        "heading": METHODOLOGY_PAGE["heading"],
        "method": {
            **{name: Markup(part) for name, part in html.items()},
            "full_text_url": METHODOLOGY_URL,
            "run_date": doc["run"]["date"],
            "method_version": doc["run"]["method_version"],
            "views": [
                {k: v[k] for k in ("key", "label", "measures", "available")} for v in doc["views"]
            ],
            "bands": [band["label"] for band in doc["bands"]],
            "tiers": [{"tier": t, "text": doc["tiers"][t]} for t in sorted(doc["tiers"])],
            "unranked": [{"key": k, "label": v} for k, v in data.UNRANKED_LABELS.items()],
            "states": [{"key": k, "label": v} for k, v in data.STATE_LABELS.items()],
            "surveys": [
                {"key": key, "sources": [s for s in sources if s["survey"] == key]}
                for key in SURVEYS
                if any(s["survey"] == key for s in sources)
            ],
            "stale": [s for s in sources if s["stale"]],
            "data_license": {
                "name": LICENSE_NAMES.get(spdx, spdx),
                "url": doc["data_license"]["url"],
                "provisional": doc["data_license"]["provisional"],
                "scope_url": f"{BLOB_URL}/LICENSE-DATA",
            },
            "code_license": {"name": "MIT License", "url": f"{BLOB_URL}/LICENSE"},
            "credits_url": f"{data.REPO_URL}#source-credits",
        },
    }


def methodology_parts(markdown: str, *, where: str = "the methodology") -> dict[str, str]:
    """Return the Markdown of each part the page shows (see the module docstring)."""
    try:
        sections = extract_sections(markdown)
    except (KeyError, ValueError) as exc:
        needed = ", ".join(f"'## {n}.'" for n in METHODOLOGY_SECTIONS)
        raise PageError(f"{where}: {_message(exc)}; /methodology/ needs {needed}") from exc
    return {
        "plain_words": _nonempty(_unquote(_body(sections[1])), where=f"{where} §1"),
        "desktop_views": _until_table(
            _subsection(sections[5], DESKTOP_VIEWS_HEADING, where=f"{where} §5"),
            where=f"{where} §5",
        ),
        "confidence": _lead_block(sections[6], CONFIDENCE_LEAD, where=f"{where} §6"),
        "biases": _nonempty(_body(sections[11]), where=f"{where} §11"),
    }


def _source(source: Mapping[str, Any]) -> dict[str, Any]:
    measures = source["measures"].rstrip()
    return {
        "id": source["id"],
        "name": source["name"],
        "survey": source["survey"],
        "measures": measures if measures.endswith(".") else f"{measures}.",
        "url": source["url"],
        "link_label": data.destination_name({"url": source["url"]}),
        "license": source["license"],
        "data_date": source["data_date"],
        "stale": source["stale"],
        "publish_rank": source["publish_rank"],
    }


def _body(section: str) -> str:
    """A section without its heading line."""
    return section.partition("\n")[2].strip() + "\n"


def _unquote(text: str) -> str:
    return "\n".join(_QUOTE.sub("", line) for line in text.splitlines()).strip() + "\n"


def _subsection(section: str, heading: str, *, where: str) -> str:
    """The lines under ``### <heading>``, up to the next heading of level 1 to 3."""
    lines: list[str] | None = None
    for line, fenced in _scan(section):
        match = None if fenced else _ATX.match(line)
        if match and len(match[1]) <= 3:
            if lines is not None:
                break
            if len(match[1]) == 3 and (match[2] or "").strip() == heading:
                lines = []
                continue
        if lines is not None:
            lines.append(line)
    if lines is None:
        raise PageError(f"{where}: no '### {heading}' subsection; /methodology/ shows it")
    return _nonempty("\n".join(lines), where=f"{where} '### {heading}'")


def _until_table(text: str, *, where: str) -> str:
    kept = []
    for line, fenced in _scan(text):
        if not fenced and _TABLE_ROW.match(line):
            break
        kept.append(line)
    return _nonempty("\n".join(kept), where=f"{where}, before its table")


def _lead_block(section: str, lead: str, *, where: str) -> str:
    """The paragraph starting with ``lead`` and the blocks after it, up to the next bold lead."""
    blocks = _BLANK_LINES.split(_body(section))
    for i, block in enumerate(blocks):
        if block.lstrip().startswith(lead):
            kept = [block]
            for later in blocks[i + 1 :]:
                if later.lstrip().startswith(("**", "#")):
                    break
                kept.append(later)
            return "\n\n".join(b.strip("\n") for b in kept).strip() + "\n"
    raise PageError(f"{where}: no paragraph starting '{lead}'; /methodology/ shows it")


def _nonempty(text: str, *, where: str) -> str:
    text = text.strip()
    if not text:
        raise PageError(f"{where} is empty; /methodology/ shows it")
    return text + "\n"


def _doc_link(href: str) -> str:
    """Point a link in docs/ranking-methodology.md at its target on GitHub."""
    if _SCHEME.match(href) or href.startswith("//"):
        return href
    if href.startswith("#"):
        return METHODOLOGY_URL + href
    path = posixpath.normpath(href.lstrip("/") if href.startswith("/") else f"docs/{href}")
    if path == ".." or path.startswith("../"):
        raise PageError(f"the methodology links outside the repository: {href!r}")
    return f"{BLOB_URL}/{path}"


# ---------------------------------------------------------------------- content pages


def content_links() -> dict[str, str]:
    """The addresses ``{{ name }}`` stands for in ``site/content/*.md``."""
    return {
        "repo_url": data.REPO_URL,
        "issues_url": f"{data.REPO_URL}/issues/new/choose",
        "report_url": data.REPORT_ISSUE_URL,
        "email": data.FEEDBACK_EMAIL,
        "mailto": f"mailto:{data.FEEDBACK_EMAIL}?subject=trulyfreefonts.com",
        "methodology_url": METHODOLOGY_URL,
        "caddyfile_url": f"{BLOB_URL}/ops/Caddyfile",
        "site_caddy_url": f"{BLOB_URL}/ops/caddy/site.caddy",
        "logrotate_url": f"{BLOB_URL}/ops/logrotate-caddy",
        "data_license_url": f"{BLOB_URL}/LICENSE-DATA",
        "code_license_url": f"{BLOB_URL}/LICENSE",
    }


def content_context(path: str, source: Path) -> dict[str, Any]:
    """The context of a Markdown content page: ``page``, ``heading`` and ``content``."""
    where = _shown(source)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise PageError(f"{where}: {exc.strerror or exc}") from exc
    meta, body = _front_matter(text, where=where)
    body = _fill(body, content_links(), where=where)
    try:
        html = _render(body, refuse_h1=True)
    except ValueError as exc:
        raise PageError(f"{where}: {exc}") from exc
    _check_heading_ids([html], where=where)
    return {
        "page": _page(path, meta),
        "heading": meta["title"],
        "content": Markup(html),
    }


def _front_matter(text: str, *, where: str) -> tuple[dict[str, str], str]:
    head, sep, body = text.removeprefix("﻿").partition("\n---\n")
    if not head.startswith("---\n") or not sep:
        raise PageError(f"{where}: must start with front matter between '---' lines")
    try:
        meta = yaml.safe_load(head.removeprefix("---\n"))
    except yaml.YAMLError as exc:
        raise PageError(f"{where}: front matter: {exc}") from exc
    if not isinstance(meta, dict) or sorted(meta) != sorted(FRONT_MATTER_KEYS):
        keys = sorted(meta) if isinstance(meta, dict) else meta
        raise PageError(f"{where}: front matter must have exactly {FRONT_MATTER_KEYS}, not {keys}")
    for key in FRONT_MATTER_KEYS:
        if not isinstance(meta[key], str) or not meta[key].strip():
            raise PageError(f"{where}: front matter {key!r} must be non-empty text")
    return {key: " ".join(meta[key].split()) for key in FRONT_MATTER_KEYS}, body


def _fill(text: str, values: Mapping[str, str], *, where: str) -> str:
    def value(match: re.Match[str]) -> str:
        name = _PLACEHOLDER_NAME.fullmatch(match[1])
        if not name or name[1] not in values:
            raise PageError(f"{where}: unknown placeholder {match[0]!r}")
        return values[name[1]]

    filled = _PLACEHOLDER.sub(value, text)
    if stray := _BRACES.search(_PLACEHOLDER.sub("", text)):
        raise PageError(f"{where}: stray {stray[0]!r}; placeholders are '{{{{ name }}}}'")
    return filled


# ---------------------------------------------------------------------------- helpers


def _page(path: str, meta: Mapping[str, str], *, canonical: bool = True) -> dict[str, Any]:
    title = meta.get("title") or meta["heading"]
    return {
        "path": path,
        "title": f"{title}{TITLE_SEPARATOR}{SITE_NAME}",
        "description": meta["description"],
        "canonical": canonical,
    }


def _check_heading_ids(parts: Iterable[str], *, where: str) -> None:
    """Refuse a heading id that is empty, repeated, a template's own or a source anchor."""
    seen: set[str] = set()
    for html in parts:
        for found in _HEADING_ID.findall(html):
            if (
                not found
                or found in seen
                or found in TEMPLATE_IDS
                or found.startswith(SOURCE_ANCHOR)
            ):
                raise PageError(
                    f"{where}: a heading's id {found!r} is empty, repeated, or taken by the "
                    "page itself; reword the heading"
                )
            seen.add(found)


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    anchors_plugin(md, min_level=2, max_level=4)
    return md


def _render(
    text: str,
    *,
    link: Callable[[str], str] | None = None,
    refuse_h1: bool = False,
    image: Callable[[str, str], Mapping[str, str]] | None = None,
) -> str:
    md = _markdown()
    env: dict[str, Any] = {}
    tokens = md.parse(text, env)
    for token in _walk(tokens):
        if token.type == "image":
            src = str(token.attrGet("src") or "")
            if _SCHEME.match(src) or src.startswith("//"):
                raise ValueError(f"image {src!r}: the pages load only their own files")
            if image is not None:
                alt = md.renderer.renderInlineAsText(token.children or [], md.options, env)
                for name, value in image(src, alt).items():
                    token.attrSet(name, value)
        elif token.type == "link_open" and link is not None:
            token.attrSet("href", link(str(token.attrGet("href") or "")))
        elif token.type == "heading_open" and token.tag == "h1" and refuse_h1:
            raise ValueError("a '# ' heading: the page's <h1> is its title; start at '##'")
    return md.renderer.render(tokens, md.options, env)


def _walk(tokens: list[Token]) -> Iterator[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _walk(token.children)


def _scan(text: str) -> Iterator[tuple[str, bool]]:
    """Yield each line with whether it sits inside fenced code (fence lines count as inside)."""
    fence = ""
    for line in text.splitlines():
        if fence:
            if re.match(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$", line):
                fence = ""
            yield line, True
        elif opened := _FENCE.match(line):
            fence = opened[1]
            yield line, True
        else:
            yield line, False


def _message(exc: Exception) -> str:
    return str(exc.args[0]) if exc.args else type(exc).__name__


def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)
