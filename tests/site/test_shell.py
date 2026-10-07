"""The page shell (A14): site/templates/base.html.j2, site/css/10-base.css and site/static/.

Milestone 2 steps 1, 6, 8 and 14, M2-D10, ops/DONATIONS.md step 11 and site/CONTRACT.md
sections 2-4 and 6. tests/test_site_contracts.py already checks the block names, lang, the
skip link's place, the landmarks, one h1, the canonical switch, the tip switch and the
absence of inline code; this file checks the rest of the shell.

- Template tests render base.html.j2 under a small child page, with the build's real
  ``site`` context, so they need no build.
- CSS and static-file tests read the files.
- Browser tests (Chromium and Firefox) use the built, served site from tests/site/conftest.py.
"""

import base64
import hashlib
import importlib.util
import re
import struct
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined

from tff_site import build

ROOT = build.REPO_ROOT
SITE = build.SITE_DIR
TEMPLATES = SITE / "templates"
BASE_CSS = SITE / "css" / "10-base.css"
STATIC = SITE / "static"
GENERATOR = STATIC / "_src" / "make_static.py"
DONATIONS = ROOT / "ops" / "DONATIONS.md"
PINNED_FONTS = ROOT / "tests" / "fixtures" / "specimen-fonts.toml"

TIP_URL = re.search(
    r"^\| Link \| `(https://buy\.stripe\.com/[^`]+)`", DONATIONS.read_text(encoding="utf-8"), re.M
)[1]
NAV = ["/", "/methodology/", "/about/", "/privacy/"]
VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)


# ------------------------------------------------------------------------------ helpers


class Node:
    """A parsed element: tag, attributes, children and the text directly inside it."""

    def __init__(self, tag: str, attrs: dict[str, str | None], parent: Node | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node] = []
        self.parts: list[str | Node] = []

    @property
    def text(self) -> str:
        return "".join(p if isinstance(p, str) else p.text for p in self.parts)

    def iter(self) -> list[Node]:
        out = [self]
        for child in self.children:
            out += child.iter()
        return out

    def find_all(self, tag: str, **attrs: str) -> list[Node]:
        return [
            n
            for n in self.iter()
            if n.tag == tag and all(n.attrs.get(k.rstrip("_")) == v for k, v in attrs.items())
        ]

    def find(self, tag: str, **attrs: str) -> Node:
        (node,) = self.find_all(tag, **attrs)
        return node

    def within(self, tag: str) -> bool:
        node = self.parent
        while node is not None:
            if node.tag == tag:
                return True
            node = node.parent
        return False


class Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = Node("#document", {}, None)
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        self.current.parts.append(node)
        if tag not in VOID:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        assert node is not None, f"stray </{tag}>"
        self.current = node.parent

    def handle_data(self, data: str) -> None:
        self.current.parts.append(data)


def render(path: str = "/privacy/", *, canonical: bool = True, tip_url: str | None = None) -> str:
    child = (
        '{% extends "base.html.j2" %}{% block main %}<h1>{{ page.title }}</h1>'
        "<p>Body text.</p>{% endblock %}"
    )
    env = Environment(
        loader=ChoiceLoader([DictLoader({"child.html.j2": child}), FileSystemLoader(TEMPLATES)]),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    context = {
        "site": {**build.site_context(), "tip_url": tip_url},
        "page": {
            "path": path,
            "title": "A page title",
            "description": "A page description.",
            "canonical": canonical,
        },
        "assets": {
            "css": "/assets/style.0123456789.css",
            "js": "/assets/app.0123456789.js",
            "font": "/assets/ui/arimo.0123456789.woff2",
        },
        "build": {"commit": "0" * 40, "run_date": "2026-09-25"},
    }
    return env.get_template("child.html.j2").render(context)


def dom(html: str) -> Node:
    tree = Tree()
    tree.feed(html)
    tree.close()
    return tree.root


def meta(doc: Node, key: str) -> str:
    """The content of the one <meta> whose name or property is ``key``."""
    (node,) = [
        n for n in doc.find_all("meta") if key in (n.attrs.get("name"), n.attrs.get("property"))
    ]
    return node.attrs["content"] or ""


def elements(doc: Node) -> list[Node]:
    return [n for n in doc.iter() if n.tag != "#document"]


# ----------------------------------------------------------------------------- the head


def test_head_has_title_description_and_viewport():
    doc = dom(render())
    assert doc.find("title").text == "A page title"
    assert meta(doc, "description") == "A page description."
    assert meta(doc, "color-scheme") == "light dark"
    viewport = meta(doc, "viewport")
    assert "width=device-width" in viewport
    assert not re.search(r"user-scalable\s*=\s*(no|0)|maximum-scale", viewport)  # 1.4.4
    assert elements(doc)[2:3] == [doc.find("meta", charset="utf-8")]  # first in <head>


def test_share_tags_point_at_our_own_domain():
    doc = dom(render("/about/"))
    assert meta(doc, "og:title") == "A page title"
    assert meta(doc, "og:description") == "A page description."
    assert meta(doc, "og:url") == "https://purelyfreefonts.com/about/"
    assert meta(doc, "og:image") == "https://purelyfreefonts.com/share.png"
    assert (meta(doc, "og:image:width"), meta(doc, "og:image:height")) == ("1200", "630")
    assert meta(doc, "og:image:type") == "image/png"
    assert meta(doc, "og:image:alt").startswith("Purely Free Fonts")
    assert meta(doc, "twitter:card") == "summary_large_image"


def test_the_404_page_has_no_canonical_url():
    doc = dom(render("/404.html", canonical=False))
    assert not [n for n in doc.find_all("link") if n.attrs.get("rel") == "canonical"]
    assert not [n for n in doc.find_all("meta") if n.attrs.get("property") == "og:url"]


def test_icons_are_linked_and_shipped():
    doc = dom(render())
    icons = {
        (n.attrs["rel"], n.attrs["href"], n.attrs.get("type"), n.attrs.get("sizes"))
        for n in doc.find_all("link")
        if "icon" in (n.attrs.get("rel") or "")
    }
    assert icons == {
        ("icon", "/favicon.ico", None, "32x32"),
        ("icon", "/favicon.svg", "image/svg+xml", None),
        ("apple-touch-icon", "/apple-touch-icon.png", None, None),
    }
    shipped = {"/" + name for name in build.STATIC_FILES}
    assert {href for _, href, _, _ in icons} <= shipped
    assert "/share.png" in shipped


@pytest.mark.parametrize("tip_url", [None, TIP_URL])
def test_the_page_loads_only_its_own_files(tip_url):
    """Everything the shell makes the browser fetch is a root-relative path on this site;
    other sites are only ever plain links (M2 step 9)."""
    doc = dom(render(tip_url=tip_url))
    fetched = [n.attrs["src"] for n in doc.iter() if n.tag in {"script", "img"}]
    fetched += [
        n.attrs["href"]
        for n in doc.find_all("link")
        if n.attrs.get("rel") not in {"canonical"}  # canonical is never fetched
    ]
    assert fetched
    assert all(re.fullmatch(r"/(?!/)\S*", url) for url in fetched), fetched
    assert len([n for n in doc.find_all("link") if n.attrs.get("rel") == "stylesheet"]) == 1
    assert len(doc.find_all("script")) == 1
    assert doc.find("script").attrs == {"type": "module", "src": "/assets/app.0123456789.js"}
    for tag in ("iframe", "object", "embed", "form", "style", "base"):
        assert not doc.find_all(tag), tag
    for link in doc.find_all("a"):
        assert re.match(r"(/|#|https://|mailto:)", link.attrs["href"]), link.attrs


# --------------------------------------------------------------------------- the body


def test_body_is_skip_link_header_main_footer():
    doc = dom(render())
    body = doc.find("body")
    assert [(n.tag, n.attrs.get("class") or n.attrs.get("id")) for n in body.children] == [
        ("a", "skip-link"),
        ("header", "site-header"),
        ("main", "main"),
        ("footer", "site-footer"),
    ]
    assert body.children[0].attrs["href"] == "#main"
    assert body.children[0].text.strip()
    header = body.children[1]
    assert not header.find_all("h1"), "the page's one h1 belongs in main"
    nav = header.find("nav")
    assert nav.attrs.get("aria-label")
    assert nav.attrs.get("class") == "site-nav"
    name = header.find("a", class_="site-name")
    assert name.attrs["href"] == "/"
    # The wordmark alone, which names the link (owner ruling of 2026-09-29); the favicon is
    # the browser's icon only.
    assert name.text.strip() == ""
    (mark,) = name.find_all("img")
    assert mark.attrs["class"] == "site-name-mark"
    assert mark.attrs["src"] == "/wordmark.svg"
    assert mark.attrs["alt"] == "Purely Free Fonts"
    width, height = int(mark.attrs["width"]), int(mark.attrs["height"])
    assert (width, height) == (410, 59)
    assert abs(width / height - 9717 / 1400) < 0.01  # the wordmark's viewBox ratio
    assert not [i for i in header.find_all("img") if "favicon" in i.attrs.get("src", "")]


@pytest.mark.parametrize("path", [*NAV, "/404.html"])
def test_nav_marks_only_the_current_page(path):
    links = dom(render(path)).find("nav").find_all("a")
    assert [a.attrs["href"] for a in links] == NAV
    current = [a.attrs["href"] for a in links if a.attrs.get("aria-current") == "page"]
    assert current == ([path] if path in NAV else [])


def test_early_version_line_is_in_the_header_of_every_page():
    """Milestone 2 step 14: the page says it is an early version and what comes next, in the
    owner's wording of 2026-09-29 (data/reviews/site/2026-09-29.toml, site_status_line), on
    every page's header. Milestone 3 removes it."""
    for path in [*NAV, "/404.html"]:
        header = dom(render(path)).find("header")
        (status,) = [p for p in header.find_all("p") if p.attrs.get("class") == "site-status"]
        text = " ".join(status.text.split())
        assert text == "Early version. Coming next: free font inventory tools.", text
    # Every page, the blog's included, extends base.html.j2 and none replaces its header,
    # so pages added later keep the line too.
    for template in TEMPLATES.glob("*.j2"):
        if template.name != "base.html.j2":
            assert "block header" not in template.read_text(encoding="utf-8"), template.name


PRIVACY_LINE = (
    "No cookies, no tracking, and the page loads only its own files. Check the Network tab."
)


@pytest.mark.parametrize("tip_url", [None, TIP_URL])
def test_the_privacy_line_is_in_every_pages_footer(tip_url):
    """The owner's site ruling of 2026-10-07 (privacy_line_in_footer): the line that stood
    under the front page's lead is one short line in the footer of every page, after the
    feedback spot and the tip link and before the data date, and its link leads to where the
    Privacy page explains the check."""
    for path in [*NAV, "/404.html"]:
        footer = dom(render(path, tip_url=tip_url)).find("footer")
        classes = [n.attrs.get("class") for n in footer.children]
        assert classes == [
            "feedback",
            *(["tip"] if tip_url else []),
            "footer-privacy",
            "footer-meta",
        ]
        line = footer.find("p", class_="footer-privacy")
        assert " ".join(line.text.split()) == PRIVACY_LINE
        (link,) = line.find_all("a")
        assert link.attrs == {"href": "/privacy/#check-for-yourself"}
        assert link.text == "Check the Network tab"


def test_one_feedback_spot_first_in_the_footer_the_same_on_every_page():
    """M2-D10 and WCAG 3.2.6: GitHub issue forms and an email link with a subject, in one
    place on every page."""
    context = build.site_context()["feedback"]
    spots = set()
    for path in [*NAV, "/404.html"]:
        doc = dom(render(path, canonical=path != "/404.html"))
        footer = doc.find("footer")
        assert footer.children[0].tag == "div"
        assert footer.children[0].attrs == {"class": "feedback", "id": "feedback"}
        assert len(doc.find_all("div", id="feedback")) == 1
        hrefs = [a.attrs["href"] for a in footer.children[0].find_all("a")]
        spots.add((tuple(hrefs), footer.children[0].text))
    ((hrefs, _),) = spots
    assert hrefs == (context["issues_url"], context["mailto"])
    assert (
        context["issues_url"] == "https://github.com/byronshock/purelyfreefonts/issues/new/choose"
    )
    assert re.fullmatch(r"mailto:admin@purelyfreefonts\.com\?subject=[^&\s]+", context["mailto"])


def test_tip_link_is_one_plain_link_called_a_tip():
    """ops/DONATIONS.md step 11 and M2 step 8: one plain link to Stripe's hosted page, in the
    footer after the feedback spot, no Stripe script, called a tip and never a donation."""
    html = render(tip_url=TIP_URL)
    doc = dom(html)
    stripe = [n for n in doc.iter() if "stripe" in " ".join(str(v) for v in n.attrs.values())]
    assert [(n.tag, n.attrs) for n in stripe] == [("a", {"href": TIP_URL})]
    link = stripe[0]
    assert link.within("footer")
    assert "tip" in link.text.lower()
    assert "donat" not in html.lower()
    footer = doc.find("footer")
    order = [n.attrs.get("id") for n in footer.children]
    assert order.index("feedback") < order.index("tip")


def test_no_tip_link_until_the_build_sets_it():
    html = render(tip_url=None)
    assert "stripe" not in html.lower()
    assert 'id="tip"' not in html


def test_the_build_uses_the_live_stripe_link():
    """M2 step 8: the build's tip link is exactly the live one in DONATIONS.md."""
    assert TIP_URL.startswith("https://buy.stripe.com/")
    assert "test_" not in TIP_URL
    assert build.site_context()["tip_url"] == TIP_URL


# ---------------------------------------------------------------------------- the CSS

# Colour-bearing values that only the tokens may write (site/CONTRACT.md section 6).
COLOUR_VALUE = re.compile(
    r"#[0-9a-f]{3,8}\b"
    r"|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)\("
    r"|(?<![\w-])(?:transparent|white|black|red|green|blue|gray|grey|silver|yellow|orange"
    r"|canvas|canvastext|linktext|visitedtext|activetext|buttonface|buttontext|buttonborder"
    r"|field|fieldtext|highlight|highlighttext|graytext|mark|marktext|accentcolor)(?![\w-])",
    re.IGNORECASE,
)


def css_rules(source: str) -> list[tuple[str, str, dict[str, str]]]:
    """``(media, selector, declarations)`` for every rule, one level of @media deep."""
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    rules = []
    for media, body in re.findall(r"(@media[^{]+)\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}", code):
        for selector, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
            rules.append((media.strip(), " ".join(selector.split()), _decls(decls)))
    top = re.sub(r"@media[^{]+\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", code)
    for selector, decls in re.findall(r"([^{}]+)\{([^{}]*)\}", top):
        rules.append(("", " ".join(selector.split()), _decls(decls)))
    return rules


def _decls(block: str) -> dict[str, str]:
    pairs = (d.split(":", 1) for d in block.split(";") if ":" in d)
    return {k.strip(): " ".join(v.split()) for k, v in pairs}


RULES = css_rules(BASE_CSS.read_text(encoding="utf-8"))


def selectors(rule_selector: str) -> list[str]:
    return [s.strip() for s in rule_selector.split(",")]


def test_base_css_writes_no_colour_values():
    for media, selector, decls in RULES:
        for prop, value in decls.items():
            assert not COLOUR_VALUE.search(value), (media, selector, prop, value)
    assert not [r for r in RULES if "prefers-color-scheme" in r[0]], "themes live in the tokens"
    assert "data-theme" not in BASE_CSS.read_text(encoding="utf-8"), "no theme switch"


def test_base_css_uses_the_interface_font_and_the_user_font_size():
    (html,) = [d for m, s, d in RULES if not m and s == "html"]
    assert html["font-family"] == "var(--font-ui)"
    assert html["font-size"] == "100%"
    for _media, selector, decls in RULES:
        assert not re.search(r"\d(px|pt)\b", decls.get("font-size", "")), selector


def test_base_css_focus_ring_follows_the_contract():
    (ring,) = [d for m, s, d in RULES if not m and s == ":focus-visible"]
    assert ring == {"outline": "var(--focus-ring)", "outline-offset": "var(--focus-offset)"}
    removed = [s for m, s, d in RULES if d.get("outline") in {"none", "0"}]
    assert removed == ["main:focus"], "only the skip link's target drops the ring"


def test_base_css_has_nothing_that_can_cover_focus_or_scroll_sideways():
    for _media, selector, decls in RULES:
        assert decls.get("position") not in {"fixed", "sticky"}, selector  # 2.4.11
        for prop in ("overflow", "overflow-x", "overflow-y"):
            # A scrolling box with nothing focusable in it fails 2.1.1 (axe's
            # scrollable-region-focusable); .visually-hidden's `hidden` is the exception.
            assert decls.get(prop, "hidden").split()[0] in {"hidden", "visible", "clip"}, selector
    (pre,) = [d for m, s, d in RULES if not m and s == "pre"]
    assert pre["white-space"] == "pre-wrap"


def test_base_css_respects_reduced_motion_and_forced_colours():
    motion = [d for m, s, d in RULES if "prefers-reduced-motion: reduce" in m]
    assert motion
    assert motion[0]["transition-duration"].startswith("0.01ms")
    assert motion[0]["animation-duration"].startswith("0.01ms")
    assert [s for m, s, d in RULES if "forced-colors: active" in m]
    for _media, selector, decls in RULES:
        if "skip-link" in selector:
            assert not {"transition", "animation"} & set(decls), "never half-shown while focused"


def test_skip_link_is_off_screen_until_focused():
    (hidden,) = [d for m, s, d in RULES if not m and s == ".skip-link"]
    (shown,) = [d for m, s, d in RULES if not m and s == ".skip-link:focus"]
    assert hidden["position"] == "absolute"
    assert hidden["transform"].startswith("translateY(calc(-100%")
    assert shown == {"transform": "none"}


# ------------------------------------------------------------------------- the static files


def png_header(blob: bytes) -> tuple[int, int, int]:
    """(width, height, colour type) from a PNG's IHDR chunk."""
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    assert blob[12:16] == b"IHDR"
    width, height = struct.unpack(">II", blob[16:24])
    return width, height, blob[25]


def test_static_folder_holds_only_the_published_files():
    """The build copies every top-level file of site/static/, so nothing else lives there;
    the generators and their sources sit in _src/, a folder the build doesn't copy, and the
    interface font's files in fonts/, which the build serves from /assets/ui/."""
    top = {p.name for p in STATIC.iterdir() if p.is_file()}
    assert top == set(build.STATIC_FILES)
    assert {p.name for p in STATIC.iterdir() if p.is_dir()} == {"_src", "fonts"}
    assert {p.name for p in (STATIC / "fonts").iterdir()} == {n for n, _ in build.UI_FONTS}
    assert all(build.SAFE_PATH.fullmatch(name) for name in top)


def test_share_image_is_1200_by_630_and_opaque():
    blob = (STATIC / "share.png").read_bytes()
    assert png_header(blob) == (1200, 630, 2)  # colour type 2: RGB, no alpha
    assert len(blob) < 200_000


def test_apple_touch_icon_is_180_and_opaque():
    blob = (STATIC / "apple-touch-icon.png").read_bytes()
    assert png_header(blob) == (180, 180, 2)


def test_favicon_ico_holds_16_32_and_48_px_pngs():
    blob = (STATIC / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", blob[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        w, h, colours, _, planes, bits, size, offset = struct.unpack(
            "<BBBBHHII", blob[6 + 16 * i : 22 + 16 * i]
        )
        assert (colours, planes, bits) == (0, 1, 32)
        assert offset + size <= len(blob)
        assert png_header(blob[offset : offset + size])[:2] == (w, h)
        sizes.append(w)
    assert sizes == [16, 32, 48]


def test_favicon_svg_is_inert_outlines():
    """Paths and rectangles only: no script, style, link, text or foreign content, so it
    renders the same under the CSP, in any browser, with no font."""
    source = (STATIC / "favicon.svg").read_text(encoding="utf-8")
    assert len(source.encode()) < 4096
    root = ET.fromstring(source)
    ns = "{http://www.w3.org/2000/svg}"
    assert root.tag == f"{ns}svg"
    assert root.attrib["viewBox"] == "0 0 32 32"
    for el in root.iter():
        assert el.tag in {f"{ns}svg", f"{ns}rect", f"{ns}path"}, el.tag
        for name, value in el.attrib.items():
            assert not name.lower().startswith("on"), name
            assert "href" not in name, name
            assert name != "style", name
            assert "url(" not in value, (name, value)


def test_wordmark_svg_is_the_owners_inert_outlines():
    """The header's wordmark (AUTHORITY.md, "Headline font", owner rulings of 2026-10-06): the
    owner's drawing as site/static/_src/make_wordmark.py writes it from his pinned source, black
    outlines on a white rectangle in the 9717 x 1400 viewBox, with no script, style, link, text
    or foreign content, so it renders the same under the CSP, in any browser, with no font."""
    data = (STATIC / "wordmark.svg").read_bytes()
    assert hashlib.sha256(data).hexdigest() == (
        "721ec7c24a84d9b98309c741c41422515c47fd85fab61903653fb5752d7554fb"
    )
    source = data.decode("utf-8")
    assert len(data) < 8192
    root = ET.fromstring(source)
    ns = "{http://www.w3.org/2000/svg}"
    assert root.tag == f"{ns}svg"
    assert root.attrib["viewBox"] == "0 0 9717 1400"
    assert [r.attrib["fill"] for r in root.iter(f"{ns}rect")] == ["#ffffff"]
    assert [g.attrib["fill"] for g in root.iter(f"{ns}g")] == ["#000000"]
    assert not [p for p in root.iter(f"{ns}path") if "fill" in p.attrib]
    for el in root.iter():
        assert el.tag in {f"{ns}svg", f"{ns}title", f"{ns}rect", f"{ns}g", f"{ns}path"}, el.tag
        for name, value in el.attrib.items():
            assert not name.lower().startswith("on"), name
            assert "href" not in name, name
            assert name != "style", name
            assert "url(" not in value, (name, value)


def test_wordmark_svg_is_the_pinned_source_without_the_editors_data():
    """make_wordmark.py's output from the owner's pinned source (owner ruling of 2026-10-06):
    the same view box, title, plate and letter paths, with Inkscape's data left out."""
    spec = importlib.util.spec_from_file_location(
        "make_wordmark", STATIC / "_src" / "make_wordmark.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = module.SOURCE.read_bytes()
    assert hashlib.sha256(source).hexdigest() == module.SOURCE_SHA256
    assert (STATIC / "wordmark.svg").read_text(encoding="utf-8") == module.drawing_only(source)


@pytest.mark.parametrize(
    ("width", "want"), [(1280, 410), (768, 410), (760, 410), (759, 270), (700, 270), (375, 270)]
)
def test_the_wordmark_is_410_px_wide_from_760_px_and_270_below(browser, site_url, width, want):
    """Owner rulings of 2026-10-04 and 2026-10-06: 410 px wide where the navigation fits beside
    it (760 px and up) and 270 px below that (capitals about 38 and 25 px tall), in the header,
    which is white in both themes. test_the_header_keeps_one_row_at_every_width re-measures the
    760."""
    for scheme in ("light", "dark"):
        context = browser.new_context(
            base_url=site_url, viewport={"width": width, "height": 800}, color_scheme=scheme
        )
        try:
            page = context.new_page()
            page.goto("/missing")
            box = page.evaluate(
                """() => { const i = document.querySelector('.site-name-mark');
                  const r = i.getBoundingClientRect();
                  return [r.width, r.height, i.naturalWidth > 0,
                          getComputedStyle(document.querySelector('.site-header'))
                            .backgroundColor]; }"""
            )
            assert abs(box[0] - want) < 1, (scheme, box)
            assert abs(box[0] / box[1] - 9717 / 1400) < 0.02, (scheme, box)
            assert box[2], "the wordmark did not load"
            assert box[3] == "rgb(255, 255, 255)", (scheme, box)
        finally:
            context.close()


# The header, laid out: the wordmark's width, the nav's width and lines, whether the nav sits
# beside the wordmark, and the lines of the early-version line (0 when it isn't shown).
HEADER_JS = """() => {
  const box = (e) => e.getBoundingClientRect();
  const mid = (b) => (b.top + b.bottom) / 2;
  const mark = box(document.querySelector('.site-name-mark'));
  const links = [...document.querySelectorAll('.site-nav li')].map(box);
  const status = document.querySelector('.site-status');
  const lines = (e) => new Set([...e.getClientRects()].map((r) => Math.round(r.top))).size;
  return { height: Math.round(box(document.querySelector('.site-header')).height * 10) / 10,
           mark: mark.width, nav: links[links.length - 1].right - links[0].left,
           navLines: new Set(links.map((b) => Math.round(b.top))).size,
           beside: Math.abs(mid(links[0]) - mid(mark)) < 8,
           status: getComputedStyle(status).display === 'none' ? 0 : lines(status),
           scrollWidth: document.documentElement.scrollWidth };
}"""
# The one-row header's parts (10-base.css): the side gutters, the gap between the wordmark and
# the nav, the wide wordmark, and the breakpoint (47.5rem) from which the two share a row.
GUTTER, GAP, WIDE_MARK, BREAKPOINT, PHONE = 16, 24, 410, 760, 640
WIDTHS = sorted({*range(320, 1281, 4), PHONE - 1, PHONE, BREAKPOINT - 1, BREAKPOINT})


@pytest.mark.parametrize("path", [*NAV, "/missing"])
def test_the_header_keeps_one_row_at_every_width(browser, site_url, path):
    """#55's one-row rule (owner ruling of 2026-10-04, wordmark_breakpoint), on each page, whose
    own nav link is bold. From 760 px the 410 px wordmark and the nav share one row; below it
    the 270 px one does, down to about 610 px, where the nav takes its own line under the
    wordmark, still one line at 320 px. The early-version line takes one line of its own from
    40rem and isn't shown on phones (early_line_hidden_on_phones, 2026-10-06). Every 4 px from
    320 to 1280 px, so a second row is caught: the header has one height per layout. And the
    widest one-row header, 16 + 410 + 24 + the nav + 16, fits the breakpoint: a longer nav
    ("How it works", 2026-10-05, or a "Tip Jar") moves the 47.5rem rule and this test. So does
    the Blog link, which every build gets once a post is published (about 795 px); a draft
    that only staging shows (--drafts) isn't measured here."""
    context = browser.new_context(base_url=site_url, viewport={"width": 1280, "height": 800})
    try:
        page = context.new_page()
        page.goto(path)
        nav = page.evaluate(HEADER_JS)["nav"]
        need = GUTTER + WIDE_MARK + GAP + nav + GUTTER
        assert need <= BREAKPOINT, f"the one-row header needs {need:.1f} px: move the breakpoint"
        heights: dict[str, set[float]] = {"wide": set(), "middle": set(), "phone": set()}
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 800})
            got = page.evaluate(HEADER_JS)
            where = (width, got)
            assert got["navLines"] == 1, where
            assert got["scrollWidth"] <= width, where
            if width >= BREAKPOINT:
                layout, mark = "wide", WIDE_MARK
            else:
                layout, mark = ("middle" if width >= PHONE else "phone"), 270
            assert abs(got["mark"] - mark) < 1, where
            assert got["beside"] or layout == "phone", where
            assert got["status"] == (0 if layout == "phone" else 1), where
            heights[layout].add(got["height"])
        assert [len(heights["wide"]), len(heights["middle"])] == [1, 1], heights
        assert len(heights["phone"]) <= 2, heights  # the nav beside the wordmark, or under it
    finally:
        context.close()


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location("make_static", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fonts_cached() -> bool:
    from tff_site import fonts

    pinned = tomllib.loads(PINNED_FONTS.read_text(encoding="utf-8"))["font"]
    return all(fonts.cache_path(f["sha256"]).is_file() for f in pinned)


@pytest.mark.skipif(not _fonts_cached(), reason="the pinned fonts are not in the font cache")
def test_generator_reproduces_the_committed_svgs():
    """The SVGs are drawn from the pinned fonts' outlines; a re-run gives the same bytes."""
    gen = _generator()
    fonts_dir = gen.FONT_CACHE
    assert gen.favicon_svg(fonts_dir) == (STATIC / "favicon.svg").read_text(encoding="utf-8")
    src = STATIC / "_src"
    assert gen.touch_svg(fonts_dir) == (src / "apple-touch-icon.svg").read_text(encoding="utf-8")
    assert gen.share_svg(fonts_dir) == (src / "share.svg").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- in a browser

PIXELS = """async ([svg, png, w, h]) => {
  const load = (src) => new Promise((ok, bad) => {
    const img = new Image(); img.onload = () => ok(img); img.onerror = bad; img.src = src; });
  const pixels = async (src) => {
    const c = new OffscreenCanvas(w, h), x = c.getContext('2d');
    x.drawImage(await load(src), 0, 0, w, h);
    return x.getImageData(0, 0, w, h).data;
  };
  const a = await pixels(svg), b = await pixels(png);
  let sum = 0, far = 0;
  for (let i = 0; i < a.length; i++) { const d = Math.abs(a[i] - b[i]); sum += d; if (d > 32) far++; }
  return { mean: sum / a.length, far: far / a.length };
}"""


def _data_url(blob: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(blob).decode()}"


def test_committed_pngs_match_their_svg_sources(page):
    """Each PNG is its SVG source, rasterised (make_static.py): any drift is a stale image."""
    ico = (STATIC / "favicon.ico").read_bytes()
    favicon = (STATIC / "favicon.svg").read_bytes()
    pairs = [
        ((STATIC / "_src" / "share.svg").read_bytes(), (STATIC / "share.png").read_bytes()),
        (
            (STATIC / "_src" / "apple-touch-icon.svg").read_bytes(),
            (STATIC / "apple-touch-icon.png").read_bytes(),
        ),
    ]
    for i in range(3):
        _, _, _, _, _, _, size, offset = struct.unpack("<BBBBHHII", ico[6 + 16 * i : 22 + 16 * i])
        pairs.append((favicon, ico[offset : offset + size]))
    for svg, png in pairs:
        width, height, _ = png_header(png)
        diff = page.evaluate(
            PIXELS, [_data_url(svg, "image/svg+xml"), _data_url(png, "image/png"), width, height]
        )
        # Anti-aliasing differs a little between browsers, most at 16 px; a wrong or stale
        # image is far off (a mean near 100).
        assert diff["mean"] < 4, (width, height, diff)
        assert diff["far"] < 0.05, (width, height, diff)


FOCUSED = """() => {
  const a = document.activeElement;
  if (!a || a === document.body) return null;
  const r = a.getClientRects()[0] || a.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const s = getComputedStyle(a);
  return { tag: a.tagName, cls: a.className, id: a.id, text: (a.textContent || '').trim(),
           hit: !!hit && (hit === a || a.contains(hit)), top: r.top, left: r.left,
           right: r.right, bottom: r.bottom, outline: s.outlineStyle, width: s.outlineWidth,
           where: a.closest('header') ? 'header' : a.closest('main') ? 'main'
                : a.closest('footer') ? 'footer' : 'body' };
}"""
FRAME = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"


@pytest.mark.parametrize("path", ["/", "/privacy/", "/missing"])
@pytest.mark.parametrize("width", [375, 1280])
def test_skip_link_is_the_first_stop_and_moves_focus_to_main(guarded_context, path, width):
    guarded = guarded_context(viewport={"width": width, "height": 700})
    page = guarded.new_page()
    page.goto(path)
    page.keyboard.press("Tab")
    page.evaluate(FRAME)
    first = page.evaluate(FOCUSED)
    assert first["cls"] == "skip-link", first
    assert first["hit"], first
    assert first["top"] >= 0, first
    assert 0 <= first["left"] <= first["right"] <= width, first
    assert (first["outline"], first["width"]) == ("solid", "3px"), first
    page.keyboard.press("Enter")
    page.evaluate(FRAME)
    assert page.evaluate("() => document.activeElement.id") == "main"
    page.keyboard.press("Tab")
    page.evaluate(FRAME)
    after = page.evaluate(FOCUSED)
    assert after is not None
    assert after["where"] in {"main", "footer"}, after
    guarded.assert_clean(page)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_shell_tab_stops_show_the_ring_and_are_never_covered(guarded_context, site_dir, scheme):
    """2.4.7 and 2.4.11 for the header and footer: each stop has the 3 px ring and the
    element at its centre is the focused one. The nav has a Blog link once the site has a
    blog (M2 step 7b)."""
    blog = ["Blog"] if (site_dir / "blog" / "index.html").is_file() else []
    guarded = guarded_context(viewport={"width": 1280, "height": 700}, color_scheme=scheme)
    page = guarded.new_page()
    page.goto("/missing")  # the shortest page: few stops outside the shell
    seen = []
    for _ in range(40):
        page.keyboard.press("Tab")
        page.evaluate(FRAME)
        stop = page.evaluate(FOCUSED)
        if stop is None or (seen and stop == seen[0]):
            break
        seen.append(stop)
        assert stop["hit"], stop
        assert (stop["outline"], stop["width"]) == ("solid", "3px"), stop
    where = [s["where"] for s in seen]
    assert where[0] == "body", where
    assert "header" in where, where
    assert "footer" in where, where
    assert [s["text"] for s in seen if s["where"] == "header"][1:] == [
        "Fonts",
        "How it works",
        *blog,
        "About",
        "Privacy",
    ]


SPACING = (
    "* { line-height: 1.5 !important; letter-spacing: 0.12em !important;"
    " word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }"
)
OVERFLOW = """(width) => {
  const bad = [];
  for (const e of document.querySelectorAll('header, header *, footer, footer *')) {
    if (e.closest('.visually-hidden')) continue;
    const b = e.getBoundingClientRect(), s = getComputedStyle(e);
    if (b.right > width + 0.5 || b.left < -0.5) bad.push('outside ' + e.tagName + '.' + e.className);
    if (s.overflowX !== 'visible' && e.scrollWidth > e.clientWidth + 1) bad.push('clipped ' + e.tagName);
    if (s.overflowY !== 'visible' && e.scrollHeight > e.clientHeight + 1) bad.push('clipped ' + e.tagName);
  }
  const boxes = [...document.querySelectorAll('.site-name, .site-nav li, .site-status')]
    .map(e => e.getBoundingClientRect());
  boxes.forEach((a, i) => boxes.slice(i + 1).forEach((b) => {
    if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5
        && b.top < a.bottom - 0.5) bad.push('overlap');
  }));
  return { scrollWidth: document.documentElement.scrollWidth, bad };
}"""


@pytest.mark.parametrize("width", [320, 640])  # 640 px is 1280 px at 200% zoom (1.4.4)
@pytest.mark.parametrize("spacing", [False, True], ids=["plain", "text-spacing"])
def test_shell_reflows_without_clipping(browser, site_url, width, spacing):
    """1.4.10 and 1.4.12: no sideways scrolling, clipping or overlap in the header and footer.
    The text-spacing style needs a context that bypasses the CSP."""
    context = browser.new_context(
        base_url=site_url, viewport={"width": width, "height": 700}, bypass_csp=spacing
    )
    try:
        page = context.new_page()
        page.goto("/missing")
        if spacing:
            page.add_style_tag(content=SPACING)
        result = page.evaluate(OVERFLOW, width)
        assert result == {"scrollWidth": width, "bad": []}, result
    finally:
        context.close()


def test_shell_images_load_under_the_csp(guarded_context):
    guarded = guarded_context(viewport={"width": 1280, "height": 700})
    page = guarded.new_page()
    page.goto("/missing")
    assert page.evaluate(
        "() => { const i = document.querySelector('.site-name-mark');"
        " return i.complete && i.naturalWidth; }"
    )
    kinds = {
        "/wordmark.svg": "image/svg+xml",
        "/favicon.svg": "image/svg+xml",
        "/favicon.ico": "image/",
        "/apple-touch-icon.png": "image/png",
        "/share.png": "image/png",
    }
    for path, kind in kinds.items():
        response = page.request.get(path)
        assert response.status == 200, path
        assert response.headers["content-type"].startswith(kind), (path, response.headers)
    guarded.assert_clean(page)


def test_footer_sits_at_the_bottom_of_a_short_page(page):
    page.set_viewport_size({"width": 1280, "height": 1200})
    page.goto("/missing")
    bottom, height = page.evaluate(
        "() => [document.querySelector('footer').getBoundingClientRect().bottom, innerHeight]"
    )
    assert abs(bottom - height) <= 1
