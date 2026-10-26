"""``tff-site build``: determinism, hashing, payloads, version.txt, the CSP lint and no network.

Most tests build a small self-contained site (templates, parts and static files written by
the ``mini_site`` fixture) from a copy of the sample catalog, so they don't depend on the
other wave-1 parts. ``test_real_templates_*`` builds a copy of the real ``site/`` with the
``catalog`` fixture's specimens and font files, stubbing only step 7's pages while they are
missing. The tests named ``test_built_site_*`` check the real site: the ``site_dir`` fixture
(tests/site/conftest.py) builds ``site/`` from the sample, or reads ``TFF_SITE_DIR`` in CI.
All of them are offline.
"""

import copy
import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from tests.helpers.names_files import copy_names

from tff_catalog import jsonio, owned
from tff_catalog.keys import search_key
from tff_site import assets, build, data, fonts, matchfile, pages, trim, views
from tff_site.cli import main

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLE_PATH = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
SAMPLE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
NAMES_PATH = matchfile.default_names_path(SAMPLE_PATH)
NAMES = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
REVIEWED_PATH = matchfile.default_reviewed_path(NAMES_PATH)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
HASHED = re.compile(r"^(?P<stem>.+)\.(?P<h>[0-9a-f]{10})\.(?P<ext>[a-z0-9]+)$")
SPECIMEN_IDS = ("sample-sans-01", "sample-mono-02", "sample-display-25")
BANDS = [b["label"] for b in SAMPLE["bands"]]  # "101-250", "251-500", "501+" with en dashes
REAL_PAGE_CONTEXTS = pages.page_contexts  # before the autouse fixture below replaces it

# ------------------------------------------------------------------------------ fixtures

BASE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{% block title %}{{ page.title }}{% endblock %}</title>
<meta name="description" content="{% block description %}{{ page.description }}{% endblock %}">
{% if page.canonical %}
<link rel="canonical" href="{{ site.base_url }}{{ page.path }}">
{% endif %}
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{{ assets.css }}">
{% block scripts %}
<script type="module" src="{{ assets.js }}"></script>
{% endblock %}
{% block head %}{% endblock %}
</head>
<body>
<a class="skip-link" href="#main">Skip to main content</a>
<header class="site-header"><a class="site-name" href="/">{{ site.name }}</a></header>
<main id="main" tabindex="-1">
{% block main %}{% endblock %}
</main>
<footer class="site-footer">
<p><a href="{{ site.feedback.issues_url }}">Report it</a> or
<a href="{{ site.feedback.mailto }}">{{ site.feedback.email }}</a>.</p>
{% if site.tip_url %}<p class="tip" id="tip"><a href="{{ site.tip_url }}">Tip</a></p>{% endif %}
<p class="footer-meta">Data from {{ build.run_date }} ({{ build.commit }}).
<a href="{{ site.repo_url }}">Source</a></p>
</footer>
</body>
</html>
"""

# Uses every field of the list context in site/CONTRACT.md section 3, so StrictUndefined
# fails the build if one is missing.
INDEX = """\
{% extends "base.html.j2" %}
{% block head %}
<link rel="preload" as="fetch" crossorigin href="{{ list.index_url }}">
{% endblock %}
{% block main %}
<h1>Fonts</h1>
<search id="filters" hidden>
<select id="f-rank" name="rank">
{% for view in list.views %}
<option value="{{ view.key }}" data-measures="{{ view.measures }}">{{ view.label }}</option>
{% endfor %}
</select>
{% for c in list.categories %}
<input type="radio" id="f-cat-{{ c.value }}" name="cat" value="{{ c.value }}">{{ c.label }}
{% endfor %}
{% if list.credit_filter %}
<input type="checkbox" id="f-hide-attr" name="hide" value="attr">No credit required
{% endif %}
{% for s in list.systems_os %}
<input type="checkbox" id="f-hide-{{ s.value }}" name="hide" value="{{ s.value }}">{{ s.label }}
{% endfor %}
</search>
<p id="count" class="count">Showing {{ list.total }} of {{ list.total }} fonts</p>
<ol id="list" class="font-list" data-index="{{ list.index_url }}"
    data-details="{{ list.details_url }}" data-match="{{ list.match_url }}"
    data-matcher="{{ list.matcher_url }}" data-run-date="{{ build.run_date }}">
{% for row in list.rows %}
<li class="font" id="font-{{ row.id }}" data-id="{{ row.id }}">
<span class="rank">{{ row.label }}</span>
<h3 class="font-name">{{ row.family }}</h3>
{% if row.specimen %}
<span class="spec" aria-hidden="true" data-src="{{ row.specimen.url }}"></span>
<noscript><img class="spec-img" src="{{ row.specimen.url }}" alt=""
 width="{{ row.specimen.width }}" height="{{ row.specimen.height }}" loading="lazy"></noscript>
{% elif row.fallback == "license" %}
<p class="spec-fallback" data-fallback="license">No preview. See it on
<a href="{{ row.download.url }}">{{ row.download.label }}</a>.</p>
{% else %}
<p class="spec-fallback" data-fallback="{{ row.fallback }}">Preview not available yet.</p>
{% endif %}
<p class="font-meta"><span class="font-cat">{{ row.category_label }}</span>
<span class="font-lic">{{ row.license_name }}</span></p>
{% if row.badges %}
<ul class="badges">{% for b in row.badges %}<li class="badge" data-badge="{{ b.key }}">{{ b.text }}</li>{% endfor %}</ul>
{% endif %}
<a class="download" href="{{ row.download.url }}">{{ row.download.label }}</a>
</li>
{% endfor %}
</ol>
{% endblock %}
"""

TEMPLATES = {
    "base.html.j2": BASE,
    "index.html.j2": INDEX,
    "about.html.j2": (
        '{% extends "base.html.j2" %}{% block main %}<h1>{{ page.title }}</h1>'
        "<p>{{ body }}</p>{% endblock %}\n"
    ),
    "404.html.j2": (
        '{% extends "base.html.j2" %}{% block scripts %}{% endblock %}'
        "{% block main %}<h1>{{ page.title }}</h1>{% endblock %}\n"
    ),
    "robots.txt.j2": "User-agent: *\nAllow: /\nSitemap: {{ site.base_url }}/sitemap.xml\n",
    "sitemap.xml.j2": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "{% for url in urls %}<url><loc>{{ url }}</loc><lastmod>{{ build.run_date }}</lastmod></url>\n"
        "{% endfor %}</urlset>\n"
    ),
}
JS = {
    "00-core.js": """\
        // 00-core: fixture part.
        const Core = (() => {
          const $ = (selector) => document.querySelector(selector);
          return Object.freeze({ $ });
        })();
        """,
    "90-main.js": """\
        // 90-main: fixture part.
        const Main = (() => {
          const start = () => {
            Core.$('html').dataset.js = '';
          };
          return Object.freeze({ start });
        })();
        Main.start();
        """,
    "58-matcher.js": """\
        // 58-matcher: fixture part, the matcher module's hand-off.
        const Matcher = Object.freeze({ handOver: () => {} });
        Matcher.handOver('tffMatcher');
        """,
}
CSS = {
    "00-tokens.css": ":root {\n  --c-fg: #111;\n}\n",
    "10-base.css": "body {\n  color: var(--c-fg);\n}\n",
}
STATIC = {name: f"static file {name}\n".encode() for name in build.STATIC_FILES}
UI_FONTS = {name: f"font file {name}\n".encode() for name, _ in build.UI_FONTS}
ABOUT = {
    "page": {"path": "/about/", "title": "About", "description": "What it is.", "canonical": True},
    "body": "Fonts & more <b>",
}


@pytest.fixture(scope="module")
def mini_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small site/ directory: templates, one JS and CSS part each, and static files."""
    root = tmp_path_factory.mktemp("mini-site")
    for folder, files in (("templates", TEMPLATES), ("js", JS), ("css", CSS)):
        (root / folder).mkdir()
        for name, text in files.items():
            (root / folder / name).write_text(textwrap.dedent(text), encoding="utf-8")
    (root / "static").mkdir()
    for name, blob in STATIC.items():
        (root / "static" / name).write_bytes(blob)
    (root / "static" / "fonts").mkdir()
    for name, blob in UI_FONTS.items():
        (root / "static" / "fonts" / name).write_bytes(blob)
    return root


@pytest.fixture(autouse=True)
def fake_pages(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Content pages for the mini site; the real-site tests keep ``pages.page_contexts``."""
    if not request.node.name.startswith("test_built_site_"):
        monkeypatch.setattr(pages, "page_contexts", lambda doc: {"/about/": ABOUT})


def svg(font_id: str) -> bytes:
    """A stand-in specimen. ``trim.PINS_FILE`` doesn't list it, so the build serves it as it
    is (step 7a trims only the pinned specimens; the trim couldn't read it anyway)."""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="2800" height="492" viewBox="0 0 2800 492">'
        f'<path d="M0 0h{len(font_id)}v10z"/><!-- {font_id} --></svg>\n'
    ).encode()


def fake_font(font_id: str) -> bytes:
    return f"not really a font: {font_id}\n".encode() * 10


@pytest.fixture
def catalog(tmp_path: Path) -> tuple[Path, Path]:
    """A copy of the sample with three rendered specimens and cached (fake) font files, and
    the sample's names file and reviewed list beside it (the hopper's match file).

    Every other preview is the placeholder, whatever the sample holds, so these tests don't
    depend on tests/fixtures/specimens/. Returns ``(data file, font cache)``.
    """
    doc = copy.deepcopy(SAMPLE)
    data_dir = tmp_path / "data"
    (data_dir / "specimens").mkdir(parents=True)
    cache = tmp_path / "fonts"
    cache.mkdir()
    for font in doc["fonts"]:
        if font["id"] in SPECIMEN_IDS:
            blob = svg(font["id"])
            (data_dir / font["preview"]["path"]).write_bytes(blob)
            font["preview"]["sha256"] = hashlib.sha256(blob).hexdigest()
        elif font["preview"]:
            font["preview"]["sha256"] = data.PLACEHOLDER_SHA256
        if font["font_file"]:
            blob = fake_font(font["id"])
            sha = hashlib.sha256(blob).hexdigest()
            font["font_file"].update(sha256=sha, size=len(blob))
            fonts.cache_path(sha, cache).write_bytes(blob)
    path = data_dir / "catalog-site.json"
    path.write_bytes(jsonio.pretty_bytes(doc))
    copy_names(SAMPLE_PATH, path)
    return path, cache


def run_build(
    catalog: tuple[Path, Path], out: Path, site: Path, **kwargs: Any
) -> build.BuildResult:
    path, cache = catalog
    kwargs.setdefault("commit", COMMIT)
    return build.build(path, out, fonts_dir=cache, site_dir=site, **kwargs)


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def asset(root: Path, url: str) -> Path:
    assert url.startswith("/assets/"), url
    return root / url.lstrip("/")


def load_json(root: Path, url: str) -> Any:
    return json.loads(asset(root, url).read_bytes())


class Tags(HTMLParser):
    """Start tags with their attributes, and the text inside each <script> and each rank cell
    (<span class="rank">, spans inside it included)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.script_text: list[str] = []
        self.ranks: list[str] = []
        self._in: str | None = None
        self._depth = 0  # open spans inside the rank cell

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag == "script":
            self._in = "script"
            self.script_text.append("")
        elif self._in == "rank" and tag == "span":
            self._depth += 1
        elif tag == "span" and "rank" in (dict(attrs).get("class") or "").split():
            self._in = "rank"
            self._depth = 1
            self.ranks.append("")

    def handle_endtag(self, tag: str) -> None:
        if self._in == "rank" and tag == "span":
            self._depth -= 1
            if self._depth:
                return
        self._in = None

    def handle_data(self, text: str) -> None:
        if self._in == "script":
            self.script_text[-1] += text
        elif self._in == "rank":
            self.ranks[-1] += text


def parse(html: str) -> Tags:
    parser = Tags()
    parser.feed(html)
    parser.close()
    return parser


RESOURCE_RELS = {"stylesheet", "icon", "apple-touch-icon", "preload", "modulepreload", "manifest"}


def csp_problems(html: str) -> list[str]:
    """The CSP lint (M2-D3, ops/caddy/site.caddy): no inline code, no form, own files only."""
    page = parse(html)
    problems = []
    for tag, attrs in page.tags:
        if tag in {"style", "form", "iframe", "object", "embed", "base"}:
            problems.append(f"<{tag}>")
        problems += [f"<{tag} {a}=>" for a in attrs if a == "style" or a.startswith("on")]
        rel = set((attrs.get("rel") or "").split())
        if "prefetch" in rel or "dns-prefetch" in rel or "preconnect" in rel:
            problems.append(f"<link rel={attrs.get('rel')}>")
        urls = []
        if tag == "script":
            if not attrs.get("src") or attrs.get("type") != "module":
                problems.append(f"<script {attrs}>")
            urls.append(attrs.get("src") or "")
        if tag == "link" and rel & RESOURCE_RELS:
            urls.append(attrs.get("href") or "")
        if tag in {"img", "source", "audio", "video", "track", "input"} and "src" in attrs:
            urls.append(attrs["src"] or "")
        problems += [f"<{tag}> loads {u!r}" for u in urls if not u.startswith("/") or u[:2] == "//"]
    problems += [f"inline script {text[:40]!r}" for text in page.script_text if text.strip()]
    return problems


# --------------------------------------------------------------------- determinism, layout


def test_two_builds_are_byte_identical(tmp_path, catalog, mini_site):
    first = run_build(catalog, tmp_path / "one", mini_site)
    second = run_build(catalog, tmp_path / "two", mini_site)
    assert first.files == second.files == len(tree(tmp_path / "one"))
    assert tree(tmp_path / "one") == tree(tmp_path / "two")
    # Rebuilding over a previous build replaces it with the same bytes.
    run_build(catalog, tmp_path / "one", mini_site)
    assert tree(tmp_path / "one") == tree(tmp_path / "two")
    assert first.version == second.version


SUBPROCESS_BUILD = textwrap.dedent(
    f"""
    import sys
    from pathlib import Path
    from tff_site import build, pages
    pages.page_contexts = lambda doc: {{"/about/": {ABOUT!r}}}
    data, out, cache, site, commit = sys.argv[1:]
    build.build(Path(data), Path(out), fonts_dir=Path(cache), site_dir=Path(site), commit=commit)
    """
)


def test_builds_in_other_processes_are_byte_identical(tmp_path, catalog, mini_site):
    """Different hash seeds, so no output depends on set or dict iteration order."""
    path, cache = catalog
    for seed in ("1", "2"):
        args = [str(path), str(tmp_path / seed), str(cache), str(mini_site), COMMIT]
        proc = subprocess.run(
            [sys.executable, "-c", SUBPROCESS_BUILD, *args],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert proc.returncode == 0, proc.stderr
    run_build(catalog, tmp_path / "here", mini_site)
    assert tree(tmp_path / "1") == tree(tmp_path / "2") == tree(tmp_path / "here")


def test_output_layout(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    files = set(tree(out))
    fixed = {"index.html", "404.html", "about/index.html", "robots.txt", "sitemap.xml"}
    assert fixed | {"version.txt"} | set(build.STATIC_FILES) <= files
    hashed = {f for f in files if f.startswith("assets/")}
    assert files - hashed == fixed | {"version.txt"} | set(build.STATIC_FILES)
    kinds = {re.sub(r"\.[0-9a-f]{10}\.", ".", f) for f in hashed}
    with_files = sorted(f["id"] for f in SAMPLE["fonts"] if f["font_file"])
    exts = {f["id"]: f["font_file"]["format"] for f in SAMPLE["fonts"] if f["font_file"]}
    assert kinds == (
        {"assets/app.js", "assets/style.css", "assets/list.json", "assets/details.json"}
        | {"assets/match.json", "assets/matcher.js"}
        | {f"assets/specimens/{i}.svg" for i in SPECIMEN_IDS}
        | {f"assets/fonts/{i}.{exts[i]}" for i in with_files}
        | {f"assets/ui/{name}" for name, _ in build.UI_FONTS}
    )
    for path in files:
        assert build.SAFE_PATH.fullmatch(path), path


def test_every_asset_name_is_its_content_hash(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    names = [p for p in (out / "assets").rglob("*") if p.is_file()]
    assert names
    for path in names:
        match = HASHED.fullmatch(path.name)
        assert match, path
        assert match["h"] == hashlib.sha256(path.read_bytes()).hexdigest()[: assets.HASH_LEN]


def test_a_changed_part_changes_only_its_asset_name(tmp_path, catalog, mini_site):
    site = tmp_path / "site-src"
    shutil.copytree(mini_site, site)
    run_build(catalog, tmp_path / "a", site)
    with (site / "css" / "10-base.css").open("a", encoding="utf-8") as fh:
        fh.write("p {\n  margin: 0;\n}\n")
    run_build(catalog, tmp_path / "b", site)
    a, b = tree(tmp_path / "a"), tree(tmp_path / "b")
    assert {k for k in a if k not in b} == {k for k in a if k.startswith("assets/style.")}
    assert {k for k in b if k not in a} == {k for k in b if k.startswith("assets/style.")}
    changed = sorted(k for k in a if k in b and a[k] != b[k])
    assert changed == ["404.html", "about/index.html", "index.html"]


def test_pages_reference_the_hashed_assets(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    index = parse((out / "index.html").read_text(encoding="utf-8"))
    css = next(a["href"] for t, a in index.tags if t == "link" and a.get("rel") == "stylesheet")
    js = next(a["src"] for t, a in index.tags if t == "script")
    ol = next(a for t, a in index.tags if t == "ol")
    preload = next(a for t, a in index.tags if t == "link" and a.get("rel") == "preload")
    assert HASHED.fullmatch(css.rsplit("/", 1)[1])
    for url in (css, js, ol["data-index"], ol["data-details"]):
        assert asset(out, url).is_file(), url
    assert preload["href"] == ol["data-index"]
    assert ol["data-run-date"] == SAMPLE["run"]["date"]
    # the 404 page shares the stylesheet and leaves the script out
    page_404 = parse((out / "404.html").read_text(encoding="utf-8"))
    assert ("link", {"rel": "stylesheet", "href": css}) in page_404.tags
    assert not [t for t, _ in page_404.tags if t == "script"]


def ui_font_url(out: Path, name: str) -> str:
    """The URL of the interface font file ``name`` in a built site."""
    stem, ext = name.rsplit(".", 1)
    (path,) = (out / "assets" / "ui").glob(f"{stem}.??????????.{ext}")
    return "/" + path.relative_to(out).as_posix()


def test_the_interface_font_is_copied_and_declared(tmp_path, catalog, mini_site):
    """AUTHORITY.md, "Interface font": each file of site/static/fonts/ is served unchanged
    from /assets/ui/ under its hash, and the stylesheet declares it."""
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    index = parse((out / "index.html").read_text(encoding="utf-8"))
    css = asset(out, next(a["href"] for t, a in index.tags if a.get("rel") == "stylesheet"))
    text = css.read_text()
    for name, style in build.UI_FONTS:
        url = ui_font_url(out, name)
        assert asset(out, url).read_bytes() == UI_FONTS[name]
        assert (
            f'  font-style: {style};\n  font-weight: 400 700;\n  font-display: optional;\n  src: url("{url}")'
            in text
        )
    assert text.count("@font-face") == len(build.UI_FONTS)


def test_every_page_preloads_the_upright_interface_font(tmp_path, catalog, real_site):
    """The real templates: one font preload per page, the upright, from the stylesheet's URL."""
    out = tmp_path / "out"
    run_build(catalog, out, real_site)
    upright = ui_font_url(out, build.UI_FONTS[0][0])
    pages = sorted(out.rglob("*.html"))
    assert len(pages) > 3
    for page in pages:
        tags = parse(page.read_text(encoding="utf-8")).tags
        fonts = [a for t, a in tags if t == "link" and a.get("as") == "font"]
        assert fonts == [
            {
                "rel": "preload",
                "href": upright,
                "as": "font",
                "type": "font/woff2",
                "crossorigin": None,
            }
        ], page


def test_a_missing_interface_font_fails_the_build(tmp_path, catalog, mini_site):
    site = tmp_path / "site-src"
    shutil.copytree(mini_site, site)
    (site / "static" / "fonts" / build.UI_FONTS[1][0]).unlink()
    with pytest.raises(build.BuildError) as caught:
        run_build(catalog, tmp_path / "out", site)
    assert caught.value.errors == [
        f"{site / 'static' / 'fonts'}: missing {build.UI_FONTS[1][0]}"
        " (site/static/_src/make_ui_font.py)"
    ]


def test_js_and_css_are_the_parts_in_filename_order(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    index = parse((out / "index.html").read_text(encoding="utf-8"))
    js = asset(out, next(a["src"] for t, a in index.tags if t == "script")).read_text()
    css = asset(out, next(a["href"] for t, a in index.tags if a.get("rel") == "stylesheet"))
    strip_js, strip_css = assets.strip_js_comments, assets.strip_css_comments

    def joined(module: tuple[tuple[str, str], ...]) -> str:
        names = sorted(n for n in JS if n in dict(module))
        return "\n".join(strip_js(textwrap.dedent(JS[n])).strip("\n") + "\n" for n in names)

    assert js == joined(assets.JS_PARTS)
    assert asset(out, match_url(out, "data-matcher")).read_text() == joined(assets.MATCHER_PARTS)
    # The interface font's @font-face rules come first, then the parts.
    faces = [(ui_font_url(out, name), style) for name, style in build.UI_FONTS]
    head = assets.font_faces(build.UI_FONT_FAMILY, faces, build.UI_FONT_WEIGHTS)
    parts = "\n".join(strip_css(CSS[n]).strip("\n") + "\n" for n in sorted(CSS))
    assert css.read_text() == head + parts
    assert js.rstrip().endswith("Main.start();")


def test_comments_are_not_shipped():
    """M2 step 10: whole-line // comments and /* */ comments cost the page budget and ship
    no code, so the build leaves them out; code, and a template literal's lines, stay."""
    js = (
        "// a part header\n"
        "const A = (() => {\n"
        "  // a note\n"
        "  const url = 'https://example.org/'; // kept: after code\n"
        "  const t = `line one\n"
        "  // inside a template literal: kept\n"
        "  `;\n"
        "  return Object.freeze({ url, t });\n"
        "})();\n"
    )
    assert assets.strip_js_comments(js) == (
        "const A = (() => {\n"
        "  const url = 'https://example.org/'; // kept: after code\n"
        "  const t = `line one\n"
        "  // inside a template literal: kept\n"
        "  `;\n"
        "  return Object.freeze({ url, t });\n"
        "})();\n"
    )
    css = "/* header */\n.a {\n  color: red; /* why */\n}\n\n\n/* two\n   lines */\n.b {\n  margin: 0;\n}\n"
    assert assets.strip_css_comments(css) == ".a {\n  color: red;\n}\n\n.b {\n  margin: 0;\n}\n"


# ---------------------------------------------------------------------- the CSP lint


def test_output_passes_the_csp_lint(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    pages_ = sorted(out.rglob("*.html"))
    assert len(pages_) == 3
    for page in pages_:
        assert csp_problems(page.read_text(encoding="utf-8")) == [], page
    modules = sorted((out / "assets").glob("*.js"))
    assert [m.name.split(".")[0] for m in modules] == ["app", "matcher"]
    for module in modules:
        js = module.read_text(encoding="utf-8")
        for needle in assets.FORBIDDEN_JS + assets.STORAGE_JS + assets.OTHER_JS:
            assert needle not in js, (module.name, needle)


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        '<script src="/a.js"></script>',  # not a module
        '<script type="module" src="https://cdn.example.com/a.js"></script>',
        "<style>p{}</style>",
        '<p style="color:red">x</p>',
        '<p onclick="x()">x</p>',
        "<form></form>",
        '<link rel="prefetch" href="/x">',
        '<link rel="stylesheet" href="//cdn.example.com/a.css">',
        '<img src="https://example.com/a.png" alt="">',
    ],
)
def test_the_csp_lint_catches(html):
    assert csp_problems(html) != []


def test_page_text_is_escaped(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    about = (out / "about" / "index.html").read_text(encoding="utf-8")
    assert "Fonts &amp; more &lt;b&gt;" in about
    assert "<b>" not in about


def test_a_missing_context_field_fails_the_build(tmp_path, catalog, mini_site):
    site = tmp_path / "site-src"
    shutil.copytree(mini_site, site)
    index = site / "templates" / "index.html.j2"
    index.write_text(index.read_text().replace("{{ row.family }}", "{{ row.nickname }}", 1))
    import jinja2

    with pytest.raises(jinja2.UndefinedError, match="nickname"):
        run_build(catalog, tmp_path / "out", site)
    assert not (tmp_path / "out").exists()


# -------------------------------------------------------------------- the list page


def linear_score(z: float) -> int:
    """The shown score, written out independently with Decimal (data.display_score)."""
    share = min(max(Decimal(repr(z)) / 3, Decimal(0)), Decimal(1))
    return max(1, math.ceil(share * 100))


@pytest.mark.parametrize(
    ("z", "shown"),
    [
        (None, -1),
        (-1.2, 1),  # below the low end: clipped, and never 0
        (0.0, 1),
        (0.03, 1),  # exactly the first step
        (0.030001, 2),
        (0.06, 2),
        (1.5, 50),
        (2.97, 99),
        (2.970001, 100),
        (3.0, 100),
        (4.2, 100),  # above the high end: clipped
    ],
)
def test_the_shown_score_is_linear_between_its_ends(z, shown):
    """q11_display_linear, q30_display_ends (2026-10-01): max(1, ⌈100·clip(z / 3, 0, 1)⌉),
    exact at its steps; score_display_linear_all_views (2026-10-19): in every view."""
    assert data.display_score(z) == shown
    if z is not None:
        assert data.display_score(z) == linear_score(z)


def expected_labels(doc: dict) -> list[str]:
    """The unfiltered default view's rank cells (Projects) as text, written out
    independently: each ranked font's score, max(1, ⌈100·clip(z / 3, 0, 1)⌉), as screen
    readers hear it (owner rulings of 2026-09-29, 2026-10-01 and 2026-10-19: score_display,
    q11_display_linear, q30_display_ends, score_display_linear_all_views,
    held_marker_style)."""
    labels = []
    for font in data.server_order(doc):
        entry = font["ranks"]["project"]
        if entry["unranked"]:
            labels.append("Not ranked: " + data.UNRANKED_LABELS[entry["unranked"]])
            continue
        score = linear_score(entry["score"])
        held = ", from one kind of source" if entry["gate_held"] else ""
        labels.append(f"Score {score} of 100{held}")
    return labels


def test_index_is_server_rendered_in_the_default_order(tmp_path, catalog, mini_site):
    """Projects (M2-D1, amended 2026-09-30: default_rank_project)."""
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    rows = [a["data-id"] for t, a in page.tags if t == "li" and a.get("class") == "font"]
    index = load_json(out, next(a["data-index"] for t, a in page.tags if t == "ol"))
    assert rows == index["ids"]
    assert len(rows) == 40
    project = {f["id"]: f["ranks"]["project"] for f in SAMPLE["fonts"]}
    ranked = [i for i in rows if project[i]["order"] is not None]
    # By score, best first: a font the two-source rule holds back takes its score's place.
    assert ranked == sorted(ranked, key=lambda i: (-project[i]["score"], project[i]["order"]))
    # The sample's held sample-mono-23 (order 101) scores above fonts in the top 100.
    assert project["sample-mono-23"]["gate_held"]
    assert ranked != sorted(ranked, key=lambda i: project[i]["order"])
    assert rows[: len(ranked)] == ranked
    unranked = [f for f in SAMPLE["fonts"] if f["id"] in rows[len(ranked) :]]
    assert rows[len(ranked) :] == [
        f["id"] for f in sorted(unranked, key=lambda f: (f["family"].casefold(), f["id"]))
    ]
    assert page.ranks == expected_labels(SAMPLE)
    assert not set(BANDS) & set(page.ranks)  # no bands, and no numbers, in the list
    assert any(label.endswith(", from one kind of source") for label in page.ranks)
    # The unranked fonts close the list, A to Z (no font lacks deliberate installs in projects).
    assert page.ranks[-1] == "Not ranked: no evidence in this rank"


def test_rows_carry_specimens_and_fallbacks(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    specs = {a["data-src"] for t, a in page.tags if t == "span" and a.get("class") == "spec"}
    imgs = [a for t, a in page.tags if t == "img"]
    assert len(specs) == len(imgs) == len(SPECIMEN_IDS)
    for img in imgs:
        assert img["src"] in specs
        # the no-script image shows at the 64 px box height, keeping the SVG's aspect ratio
        assert (img["width"], img["height"]) == ("364", "64")
        # a stand-in isn't pinned, so it is served byte for byte
        font_id = Path(img["src"]).name.split(".")[0]
        assert asset(out, img["src"]).read_bytes() == svg(font_id)
    fallbacks = [a["data-fallback"] for t, a in page.tags if a.get("class") == "spec-fallback"]
    assert fallbacks.count("license") == sum(not f["preview_ok"] for f in SAMPLE["fonts"])
    assert len(fallbacks) == 40 - len(SPECIMEN_IDS)
    assert set(fallbacks) == {"license", "failed"}


def test_the_no_script_box_is_the_css_box():
    """``SPEC_BOX_PX`` is ``--spec-h`` on wide screens, and phones have a 48 px box (the
    owner's site ruling of 2026-10-05, specimen_box_heights, the phones' part with step 7b)."""
    css = (ROOT / "site" / "css" / "00-tokens.css").read_text(encoding="utf-8")
    assert re.findall(r"--spec-h:\s*(\d+)px;", css) == [str(build.SPEC_BOX_PX), "48"]


def test_row_badges_and_download_names():
    rows = {r["id"]: r for r in build._rows(SAMPLE, {})}
    badges = {i: [(b["key"], b["text"]) for b in r["badges"]] for i, r in rows.items()}
    # Short badges (owner ruling of 2026-09-30, filters_layout): Linux distributions are
    # "Linux", apps follow the systems, and "Pulled in by" is in the details panel only.
    assert ("preinstalled", "Comes with Linux") in badges["sample-sans-08"]
    assert not any(k == "pulled" for b in badges.values() for k, _ in b)
    assert ("new", "New") in badges["sample-sans-29"]
    assert ("attribution", "Credit required") in badges["sample-hand-16"]
    # The owner's site ruling of 2026-10-05 (nerd_tag): a Nerd Font build is a tag, after
    # "Adjustable weight", on exactly the fonts with one.
    nerd = {f["id"] for f in SAMPLE["fonts"] if f["links"]["nerd"] is not None}
    assert nerd == {i for i, b in badges.items() if ("nerd", "Nerd Font available") in b}
    assert badges["sample-mono-02"][:2] == [
        ("variable", "Adjustable weight"),
        ("nerd", "Nerd Font available"),
    ]
    assert not any("nerd" in row for row in rows.values())  # the tag says it
    assert not any(k in ("monospace", "noredist") for b in badges.values() for k, _ in b)
    keys = [k for b in badges.values() for k, _ in b]
    for key in keys:
        assert key in build.BADGE_TEXT
    for row in badges.values():
        order = [list(build.BADGE_TEXT).index(k) for k, _ in row]
        assert order == sorted(order)
    assert rows["sample-sans-01"]["download"] == {
        "url": "https://github.com/rsms/inter",
        "label": "GitHub: rsms/inter",
    }
    assert rows["sample-display-10"]["download"]["label"] == "Google Fonts"
    assert rows["sample-serif-04"]["download"]["label"] == "example.com"
    assert rows["sample-sans-17"]["fallback"] == "license"
    assert rows["sample-display-18"]["fallback"] == "failed"
    assert rows["sample-sans-01"]["category_label"] == "Sans serif"
    # filed as sans-serif by the catalog, but monospaced: the site says Monospace
    assert rows["sample-mono-35"]["category_label"] == "Monospace"
    assert rows["sample-sans-01"]["license_name"] == "SIL Open Font License 1.1"


@pytest.mark.parametrize(
    ("link", "name"),
    [
        ({"url": "https://github.com/rsms/inter"}, "GitHub: rsms/inter"),
        ({"url": "https://github.com/rsms/inter.git"}, "GitHub: rsms/inter"),
        ({"url": "https://www.github.com/a/b/releases/tag/v1"}, "GitHub: a/b"),
        ({"url": "https://github.com/rsms"}, "github.com"),
        ({"url": "https://fonts.google.com/specimen/Inter"}, "Google Fonts"),
        ({"url": "https://www.example.org/fonts/x/"}, "example.org"),
        ({"url": "https://github.com/rsms/inter", "label": "Inter's site"}, "Inter's site"),
    ],
)
def test_destination_names(link, name):
    assert data.destination_name(link) == name


# ------------------------------------------------------------------------ the payloads


def test_list_index_format(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    index = load_json(out, next(a["data-index"] for t, a in page.tags if t == "ol"))
    doc = data.load(catalog[0])
    n = len(doc["fonts"])
    fonts_ = {f["id"]: f for f in doc["fonts"]}
    assert set(index) == set(LIST_KEYS)
    assert (index["v"], index["commit"], index["run_date"], index["n"]) == (
        4,
        COMMIT,
        "2026-09-25",
        n,
    )
    for key in ("ids", "cat", "bits", "keys", "by_name"):
        assert len(index[key]) == n, key
    assert index["cats"] == ["sans-serif", "serif", "display", "handwriting", "monospace"]
    assert index["views"] == doc["views"]
    assert index["bands"] == BANDS
    assert index["why_labels"] == list(data.UNRANKED_LABELS.values())
    assert sorted(index["by_name"]) == list(range(n))
    names = [
        (fonts_[index["ids"][i]]["family"].casefold(), index["ids"][i]) for i in index["by_name"]
    ]
    assert names == sorted(names)
    for i, font_id in enumerate(index["ids"]):
        font = fonts_[font_id]
        # the site category (owner ruling of 2026-09-30): every monospaced font is Monospace
        site_cat = "monospace" if font["is_monospace"] else font["category"]
        assert index["cats"][index["cat"][i]] == site_cat
        keys = index["keys"][i].split("|")
        assert keys == [search_key(font["family"])] + [
            search_key(a["name"]) for a in font["aliases"]
        ]
    available = [v["key"] for v in doc["views"] if v["available"]]
    assert sorted(index["r"]) == sorted(available)  # canonical JSON sorts keys
    for key, col in index["r"].items():
        assert len(col["top"]) == len(col["band"]) == len(col["why"]) == len(col["u"]) == n
        assert "tier" not in col  # no_tiers_or_ranges
        for i, font_id in enumerate(index["ids"]):
            entry = fonts_[font_id]["ranks"].get(key)
            if entry is None:
                assert (col["u"][i], col["top"][i], col["band"][i], col["why"][i]) == (
                    ".",
                    0,
                    -1,
                    -1,
                )
            elif entry["unranked"]:
                assert col["u"][i] == "-"
                assert index["why_labels"][col["why"][i]] == data.UNRANKED_LABELS[entry["unranked"]]
            else:
                assert col["u"][i] == "+"
                assert col["top"][i] == (entry["rank"] or 0)
                assert col["band"][i] == (
                    index["bands"].index(entry["band"]) if entry["band"] else -1
                )
        best = [
            (-(e := fonts_[index["ids"][i]]["ranks"][key])["score"], e["order"])
            for i in col["order"]
        ]
        assert best == sorted(best)  # by score, best first
        assert len(col["order"]) == col["u"].count("+")
        assert len(col["s"]) == len(col["held"]) == n
        assert col["note"] == data.VIEW_NOTES.get(key)
        for i, font_id in enumerate(index["ids"]):
            entry = fonts_[font_id]["ranks"].get(key)
            ranked = entry is not None and entry["order"] is not None
            score = linear_score(entry["score"]) if ranked else -1
            assert col["s"][i] == score
            assert col["held"][i] == ("1" if ranked and entry["gate_held"] else "0")
    coding = index["r"]["coding"]["u"]
    for i, font_id in enumerate(index["ids"]):
        assert (coding[i] != ".") == fonts_[font_id]["is_monospace"]


LIST_KEYS = (
    "v",
    "commit",
    "run_date",
    "n",
    "ids",
    "cats",
    "cat",
    "bits",
    "keys",
    "by_name",
    "views",
    "bands",
    "why_labels",
    "score_words",
    "r",
)


def bits_of(index: dict, font_id: str) -> int:
    return index["bits"][index["ids"].index(font_id)]


def test_list_index_bits():
    doc = copy.deepcopy(SAMPLE)
    for font in doc["fonts"]:
        if font["id"] == "sample-sans-01":
            font["preview"]["sha256"] = "a" * 64
        elif font["id"] == "sample-mono-02":
            font["preview"]["sha256"] = data.PLACEHOLDER_SHA256
    index = data.list_index(doc, commit=COMMIT)
    assert (
        bits_of(index, "sample-sans-01")
        == data.BIT_VARIABLE | data.BIT_SPECIMEN | data.BIT_TYPE_OWN
    )
    # a placeholder preview is no specimen; the font file still allows "Type your own text"
    assert bits_of(index, "sample-mono-02") & data.BIT_SPECIMEN == 0
    assert bits_of(index, "sample-mono-02") & data.BIT_MONOSPACE
    assert bits_of(index, "sample-hand-16") & data.BIT_ATTRIBUTION
    assert bits_of(index, "sample-display-10") & data.BIT_LIMITED
    assert bits_of(index, "sample-sans-29") & data.BIT_NEW
    assert bits_of(index, "sample-serif-30") & data.BIT_PULLED
    assert bits_of(index, "sample-serif-30") & sum(data.OS_BITS.values()) == 0
    assert bits_of(index, "sample-sans-08") & data.OS_BITS["linux"]
    assert bits_of(index, "sample-sans-06") & data.OS_BITS["android"]
    assert bits_of(index, "sample-mono-13") & data.OS_BITS["windows"]
    assert bits_of(index, "sample-sans-14") & data.OS_BITS["macos"]
    for font in doc["fonts"]:
        bits = bits_of(index, font["id"])
        assert bool(bits & data.BIT_MONOSPACE) == font["is_monospace"]
        assert bits & 16 == 0  # "not redistributable" until Rule 3 of 2026-09-30
        assert bool(bits & data.BIT_TYPE_OWN) == (font["font_file"] is not None)


def test_comes_with_names_each_system_once_then_apps():
    systems = {
        "libreoffice": (0, "LibreOffice", "app"),
        "ubuntu": (1, "Ubuntu", "linux"),
        "macos": (2, "macOS", "macos"),
        "fedora-workstation": (3, "Fedora Workstation", "linux"),
        "windows-11": (4, "Windows 11", "windows"),
    }
    font = {"preinstalled_on": [{"system": s} for s in systems]}
    assert build.comes_with(font, systems) == ["Windows", "macOS", "Linux", "LibreOffice"]
    assert build.comes_with({"preinstalled_on": []}, systems) == []


def test_an_apps_bundle_sets_no_os_bit():
    """A system with os "app" (LibreOffice's installers) tags a font but never sets a
    "comes with" bit, so hiding Windows fonts keeps it."""
    doc = copy.deepcopy(SAMPLE)
    doc["systems"].append({"id": "libreoffice", "label": "LibreOffice", "os": "app"})
    font = next(f for f in doc["fonts"] if f["id"] == "sample-serif-30")
    font["preinstalled_on"] = [{"system": "libreoffice"}]
    assert data.schema_errors(doc) == []
    assert data.semantic_errors(doc) == []
    index = data.list_index(doc, commit=COMMIT)
    assert bits_of(index, "sample-serif-30") & sum(data.OS_BITS.values()) == 0


def test_details_format(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    payload = load_json(out, next(a["data-details"] for t, a in page.tags if t == "ol"))
    doc = data.load(catalog[0])
    for key in ("views", "bands", "sources", "systems", "license_classes"):
        assert payload[key] == doc[key], key
    assert "tiers" not in payload  # no_tiers_or_ranges
    assert payload["v"] == 2
    assert payload["run_date"] == doc["run"]["date"]
    assert payload["state_labels"]["censored"] == "below the floor"
    assert payload["why_labels"] == data.UNRANKED_LABELS
    assert payload["report"] == {
        "issue_url": "https://github.com/byronshock/purelyfreefonts/issues/new?template=license.yml",
        "email": "admin@purelyfreefonts.com",
    }
    assert set(payload["fonts"]) == {f["id"] for f in doc["fonts"]}
    for font in doc["fonts"]:
        got = dict(payload["fonts"][font["id"]])
        type_own = got.pop("type_own")
        want = {k: v for k, v in font.items() if k not in ("preview", "font_file")}
        # Each rank's place, without its tier or likely range (no_tiers_or_ranges).
        want["ranks"] = {
            key: {k: v for k, v in rank.items() if k not in ("tier", "range")}
            for key, rank in font["ranks"].items()
        }
        assert got == want
        if font["font_file"]:
            assert type_own["size"] == font["font_file"]["size"]
            served = asset(out, type_own["url"])
            assert served.read_bytes() == fake_font(font["id"])
            assert served.name.startswith(f"{font['id']}.")
            assert served.suffix == f".{font['font_file']['format']}"
        else:
            assert type_own is None


def test_details_refuses_font_assets_for_fonts_without_a_file():
    with pytest.raises(ValueError, match="sample-sans-03"):
        data.details(SAMPLE, font_assets={"sample-sans-03": "/assets/fonts/x.0123456789.ttf"})


# ------------------------------------------------------------------------ the match file


def write_names(catalog: tuple[Path, Path], names: dict) -> Path:
    """Replace the ``catalog`` fixture's names file; return its path."""
    path = matchfile.default_names_path(catalog[0])
    path.write_bytes(jsonio.pretty_bytes(names))
    return path


def match_url(out: Path, attribute: str = "data-match") -> str:
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    return next(a for t, a in page.tags if t == "ol" and a.get("id") == "list")[attribute]


def test_the_match_file_is_derived_from_names_json_and_named_on_the_list(
    tmp_path, catalog, mini_site
):
    """The hopper's match data (the owner's ruling of 2026-10-22,
    ``inventory_match_data_site_build``): ``owned.match_document`` of the names file beside the
    catalog, with the reviewed list's recodes, as canonical JSON at a hashed URL that
    ``#list[data-match]`` names."""
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    url = match_url(out)
    assert re.fullmatch(r"/assets/match\.[0-9a-f]{10}\.json", url)
    blob = asset(out, url).read_bytes()
    ids = [f["id"] for f in SAMPLE["fonts"]]
    recodes = matchfile.load_reviewed(REVIEWED_PATH)[0].relations()
    want = owned.match_document(NAMES, ids, SAMPLE["run"]["date"], recodes=recodes)
    assert blob == jsonio.canonical_bytes(want)
    doc = json.loads(blob)
    assert (doc["format"], doc["v"], doc["run_date"]) == ("tff-match", 1, SAMPLE["run"]["date"])
    assert doc["ids"] == sorted(ids)
    assert doc["names_sha256"] == hashlib.sha256(jsonio.canonical_bytes(NAMES)).hexdigest()


def test_the_match_file_follows_names_json_and_moves_only_its_name_on_the_list(
    tmp_path, catalog, mini_site
):
    run_build(catalog, tmp_path / "a", mini_site)
    names = copy.deepcopy(NAMES)
    names["ineligible"].append({"name": "Sample Extra Icons", "reason": "icon"})
    write_names(catalog, names)
    run_build(catalog, tmp_path / "b", mini_site)
    a, b = tree(tmp_path / "a"), tree(tmp_path / "b")
    assert {k for k in a if k not in b} == {match_url(tmp_path / "a").lstrip("/")}
    assert sorted(k for k in a if k in b and a[k] != b[k]) == ["index.html", "version.txt"]


def test_a_names_file_of_another_run_fails_the_build_and_writes_nothing(
    tmp_path, catalog, mini_site
):
    """Each problem ``matchfile`` finds is one of the build's (test_matchfile.py has them all),
    found before any output is written."""
    names = copy.deepcopy(NAMES)
    names["run"]["date"] = "2026-09-26"
    names["families"] = [f for f in names["families"] if f["id"] != "sample-sans-40"]
    path = write_names(catalog, names)
    with pytest.raises(build.BuildError) as caught:
        run_build(catalog, tmp_path / "site", mini_site)
    assert caught.value.errors == [
        f"{path}: run.date is 2026-09-26, the catalog's is 2026-09-25",
        f"{path}: sample-sans-40: in the catalog, but not an in_catalog family",
    ]
    assert not (tmp_path / "site").exists()


def test_an_unreviewed_ambiguous_name_fails_the_build(tmp_path, catalog, mini_site):
    path = matchfile.default_names_path(catalog[0])
    reviewed = matchfile.default_reviewed_path(path)
    reviewed.unlink()
    with pytest.raises(build.BuildError) as caught:
        run_build(catalog, tmp_path / "site", mini_site)
    assert caught.value.errors == [
        f"{path}: 'Sample Sans 06 Black' may be a weight of sample-sans-06, its key "
        "'samplesans06black' owned by sample-sans-06-black: an ambiguous name nobody has "
        f"reviewed; once the owner has, list it in {reviewed}"
    ]


def test_the_command_line_takes_another_names_file(
    tmp_path, catalog, mini_site, monkeypatch, capsys
):
    """``--names F``: the reviewed list is then the one beside F."""
    monkeypatch.setattr(build, "build", functools.partial(build.build, site_dir=mini_site))
    path, cache = catalog
    other = tmp_path / "elsewhere" / "names-of-the-run.json"
    other.parent.mkdir()
    names = matchfile.default_names_path(path)
    names.rename(other)
    matchfile.default_reviewed_path(names).rename(matchfile.default_reviewed_path(other))
    out = tmp_path / "site"
    argv = ["build", "--data", str(path), "--out", str(out), "--fonts-dir", str(cache)]
    assert main([*argv, "--commit", COMMIT]) == 1
    assert f"{names}: not found" in capsys.readouterr().err
    assert main([*argv, "--commit", COMMIT, "--names", str(other)]) == 0
    assert asset(out, match_url(out)).is_file()
    version = (out / "version.txt").read_text(encoding="utf-8")
    assert f"names_sha256={hashlib.sha256(other.read_bytes()).hexdigest()}\n" in version


def test_a_names_file_nested_deeply_fails_the_command_line_in_a_line(
    tmp_path, catalog, mini_site, monkeypatch, capsys
):
    """A value 50,000 levels deep under a key the build doesn't read printed some 50,000
    lines of a ``RecursionError``; now it is one problem, and nothing is written."""
    monkeypatch.setattr(build, "build", functools.partial(build.build, site_dir=mini_site))
    path, cache = catalog
    names = matchfile.default_names_path(path)
    text = names.read_text(encoding="utf-8").rstrip()
    names.write_text(text[:-1] + ', "x": ' + "[" * 50_000 + "]" * 50_000 + "}", encoding="utf-8")
    out = tmp_path / "site"
    argv = ["build", "--data", str(path), "--out", str(out), "--fonts-dir", str(cache)]
    assert main([*argv, "--commit", COMMIT, "--names", str(names)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert f"{names}: nested too deeply to read" in err
    assert len(err.splitlines()) < 5
    assert not out.exists()


def test_the_names_file_s_folder_is_never_replaced(tmp_path, catalog, mini_site):
    other = tmp_path / "names-dir"
    other.mkdir()
    names = matchfile.default_names_path(catalog[0])
    shutil.copyfile(names, other / "names.json")
    shutil.copyfile(matchfile.default_reviewed_path(names), other / "names.reviewed.toml")
    with pytest.raises(build.BuildError, match="it holds"):
        run_build(catalog, other, mini_site, names_path=other / "names.json")


@pytest.mark.parametrize("attribute", ["data-match", "data-matcher"])
def test_no_page_preloads_or_names_the_hopper_s_files_but_the_list(
    tmp_path, catalog, real_site, attribute
):
    """M3-D7 (a), inventory_matcher_on_demand: the match file and the matcher module load only
    once a visitor's list arrives. Only ``#list``'s ``data-match`` and ``data-matcher`` name
    them: no preload, no script element, no other page, and neither script nor a payload."""
    out = tmp_path / "out"
    run_build(catalog, out, real_site)
    url = match_url(out, attribute)
    name = url.rsplit("/", 1)[1]
    for path, blob in tree(out).items():
        if path == url.lstrip("/"):
            continue
        assert blob.count(name.encode()) == (1 if path == "index.html" else 0), path
    tags = parse((out / "index.html").read_text(encoding="utf-8")).tags
    assert [a for t, a in tags if t in ("link", "script") and url in a.values()] == []


def test_the_matcher_is_a_module_of_its_own_beside_the_page_s(tmp_path, catalog, real_site):
    """inventory_matcher_on_demand: the parser and matcher leave the page's script for
    ``/assets/matcher.<h>.js``, the matcher parts joined, which the page names on
    ``#list[data-matcher]`` and loads with no script element of its own; Keys is in both."""
    out = tmp_path / "out"
    run_build(catalog, out, real_site)
    url = match_url(out, "data-matcher")
    assert re.fullmatch(r"/assets/matcher\.[0-9a-f]{10}\.js", url)
    matcher = asset(out, url).read_text(encoding="utf-8")
    assert matcher == assets.concat_js(real_site / "js", assets.MATCHER_PARTS)
    tags = parse((out / "index.html").read_text(encoding="utf-8")).tags
    (script,) = [a for t, a in tags if t == "script"]
    app = asset(out, script["src"]).read_text(encoding="utf-8")
    assert app == assets.concat_js(real_site / "js")
    tops = re.compile(r"^const (\w+) =", re.MULTILINE)
    assert tops.findall(matcher) == ["Keys", "OwnedParse", "OwnedMatch", "Matcher"]
    assert "OwnedParse" not in tops.findall(app)
    assert "Keys" in tops.findall(app)
    assert matcher.rstrip("\n").endswith(assets.MATCHER_START)
    assert app.rstrip("\n").endswith(assets.MAIN_START)


# ------------------------------------------------------------------------- version.txt


def _hashes(catalog: tuple[Path, Path]) -> dict[str, str]:
    """The sha256 of the names file and the reviewed list beside the ``catalog`` fixture."""
    names = matchfile.default_names_path(catalog[0])
    reviewed = matchfile.default_reviewed_path(names)
    return {
        "names_sha256": hashlib.sha256(names.read_bytes()).hexdigest(),
        "reviewed_sha256": hashlib.sha256(reviewed.read_bytes()).hexdigest(),
    }


def test_version_txt(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    result = run_build(catalog, out, mini_site)
    doc = data.load(catalog[0])
    text = (out / "version.txt").read_text(encoding="utf-8")
    file_sha = hashlib.sha256(catalog[0].read_bytes()).hexdigest()  # as `sha256sum` prints it
    hashes = _hashes(catalog)
    assert hashes["names_sha256"] == hashlib.sha256(NAMES_PATH.read_bytes()).hexdigest()
    assert hashes["reviewed_sha256"] == hashlib.sha256(REVIEWED_PATH.read_bytes()).hexdigest()
    assert text == (
        f"commit={COMMIT}\n"
        f"run_date={doc['run']['date']}\n"
        f"method_version={doc['run']['method_version']}\n"
        f"catalog_sha256={file_sha}\n"
        f"names_sha256={hashes['names_sha256']}\n"
        f"reviewed_sha256={hashes['reviewed_sha256']}\n"
        "schema=catalog-site/1\n"
    )
    assert result.version == dict(line.split("=", 1) for line in text.splitlines())
    assert build.version_txt(COMMIT, doc, catalog_sha256=file_sha, **hashes) == text
    # the catalog fixture is written as Milestone 1 writes it (jsonio.dump), so the default
    # agrees with the file
    assert build.version_txt(COMMIT, doc, **hashes) == text


def test_version_txt_names_the_file_bytes(tmp_path, catalog, mini_site):
    """catalog_sha256 is the file's sha256: another formatting of the same data is another file."""
    path, _ = catalog
    first = run_build(catalog, tmp_path / "a", mini_site)
    path.write_text(json.dumps(data.load(path), indent=4), encoding="utf-8")
    second = run_build(catalog, tmp_path / "b", mini_site)
    assert second.version["catalog_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert second.version["catalog_sha256"] != first.version["catalog_sha256"]
    assert {k: v for k, v in first.version.items() if k != "catalog_sha256"} == {
        k: v for k, v in second.version.items() if k != "catalog_sha256"
    }
    # without a file, version_txt hashes the data as Milestone 1 writes it, and follows it
    hashes = _hashes(catalog)
    doc = copy.deepcopy(SAMPLE)
    want = hashlib.sha256(jsonio.pretty_bytes(doc)).hexdigest()
    assert f"catalog_sha256={want}\n" in build.version_txt(COMMIT, doc, **hashes)
    doc["fonts"][0]["family"] = "Sample Sans One"
    assert build.version_txt(COMMIT, doc, **hashes) != build.version_txt(COMMIT, SAMPLE, **hashes)
    for name in ("catalog_sha256", "names_sha256", "reviewed_sha256"):
        with pytest.raises(ValueError, match=f"{name} 'abc' is not 64 hex"):
            build.version_txt(COMMIT, doc, **{**hashes, name: "abc"})
    with pytest.raises(ValueError, match="64 hex"):
        build.version_txt(COMMIT, doc, **{**hashes, "names_sha256": "A" * 64})


def test_version_txt_names_the_names_file_and_the_reviewed_list_by_their_bytes(
    tmp_path, catalog, mini_site
):
    """Another formatting of names.json changes ``names_sha256`` and nothing else: the match
    file's bytes, and so its URL, follow the data (its own ``names_sha256`` hashes the
    canonical form). A changed reviewed list changes ``reviewed_sha256``."""
    first = run_build(catalog, tmp_path / "a", mini_site)
    names = matchfile.default_names_path(catalog[0])
    names.write_text(json.dumps(NAMES, indent=4), encoding="utf-8")
    second = run_build(catalog, tmp_path / "b", mini_site)
    assert second.version["names_sha256"] == hashlib.sha256(names.read_bytes()).hexdigest()
    changed = {k for k in first.version if first.version[k] != second.version[k]}
    assert changed == {"names_sha256"}
    assert tree(tmp_path / "a")["index.html"] == tree(tmp_path / "b")["index.html"]
    reviewed = matchfile.default_reviewed_path(names)
    reviewed.write_text("# reviewed again\n" + reviewed.read_text(encoding="utf-8"))
    third = run_build(catalog, tmp_path / "c", mini_site)
    changed = {k for k in second.version if second.version[k] != third.version[k]}
    assert changed == {"reviewed_sha256"}


def test_version_txt_has_no_build_time(tmp_path, catalog, mini_site, monkeypatch):
    """A build tomorrow gives the same bytes: nothing reads the clock."""
    import datetime
    import time

    def no_clock(*args, **kwargs):
        raise AssertionError("the build read the clock")

    first = run_build(catalog, tmp_path / "a", mini_site)
    monkeypatch.setattr(time, "time", no_clock)
    monkeypatch.setattr(time, "time_ns", no_clock)
    monkeypatch.setattr(time, "localtime", no_clock)
    monkeypatch.setattr(time, "gmtime", no_clock)
    monkeypatch.setattr(time, "strftime", no_clock)

    class Frozen(datetime.datetime):
        now = classmethod(no_clock)
        utcnow = classmethod(no_clock)
        today = classmethod(no_clock)

    monkeypatch.setattr(datetime, "datetime", Frozen)
    second = run_build(catalog, tmp_path / "b", mini_site)
    assert first.version == second.version
    assert tree(tmp_path / "a") == tree(tmp_path / "b")


# ------------------------------------------------------------------------- no network


def test_the_build_makes_no_network_request(
    tmp_path, catalog, mini_site, no_network, network_attempts
):
    run_build(catalog, tmp_path / "site", mini_site)
    assert network_attempts == []


def test_the_build_runs_without_a_network_namespace(tmp_path, catalog, mini_site):
    """Stronger than the socket guard: no network interface at all (skips where unavailable)."""
    unshare = shutil.which("unshare")
    if unshare is None or subprocess.run([unshare, "-rn", "true"], check=False).returncode:
        pytest.skip("unprivileged network namespaces are not available here")
    path, cache = catalog
    args = [str(path), str(tmp_path / "ns"), str(cache), str(mini_site), COMMIT]
    proc = subprocess.run(
        [unshare, "-rn", sys.executable, "-c", SUBPROCESS_BUILD, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    run_build(catalog, tmp_path / "here", mini_site)
    assert tree(tmp_path / "ns") == tree(tmp_path / "here")


# ------------------------------------------------------------------ previews and fonts


def test_placeholder_previews_build_as_not_available_yet(tmp_path, mini_site):
    """The sample as committed: every preview is the placeholder until A5's specimens land."""
    out = tmp_path / "site"
    doc = copy.deepcopy(SAMPLE)
    for font in doc["fonts"]:
        if font["preview"]:
            font["preview"]["sha256"] = data.PLACEHOLDER_SHA256
    path = tmp_path / "catalog-site.json"
    path.write_bytes(jsonio.pretty_bytes(doc))
    copy_names(SAMPLE_PATH, path)
    build.build(path, out, commit=COMMIT, site_dir=mini_site, font_files=False)
    assert not (out / "assets" / "specimens").exists()
    assert not (out / "assets" / "fonts").exists()
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    assert not [a for t, a in page.tags if t == "img"]
    index = load_json(out, next(a["data-index"] for t, a in page.tags if t == "ol"))
    assert not any(b & (data.BIT_SPECIMEN | data.BIT_TYPE_OWN) for b in index["bits"])


def test_without_font_files_type_your_own_text_is_left_out(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    path, _ = catalog
    build.build(
        path, out, fonts_dir=tmp_path / "empty", commit=COMMIT, site_dir=mini_site, font_files=False
    )
    assert not (out / "assets" / "fonts").exists()
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    ol = next(a for t, a in page.tags if t == "ol")
    index = load_json(out, ol["data-index"])
    assert not any(b & data.BIT_TYPE_OWN for b in index["bits"])
    assert sum(bool(b & data.BIT_SPECIMEN) for b in index["bits"]) == len(SPECIMEN_IDS)
    payload = load_json(out, ol["data-details"])
    assert all(f["type_own"] is None for f in payload["fonts"].values())


def edit_catalog(path: Path, font_id: str, **changes: Any) -> None:
    doc = data.load(path)
    font = next(f for f in doc["fonts"] if f["id"] == font_id)
    for dotted, value in changes.items():
        *parents, last = dotted.split("__")
        target = font
        for key in parents:
            target = target[key]
        target[last] = value
    path.write_bytes(jsonio.pretty_bytes(doc))


def test_a_missing_specimen_fails(tmp_path, catalog, mini_site):
    (catalog[0].parent / "specimens" / "sample-mono-02.svg").unlink()
    with pytest.raises(build.BuildError) as caught:
        run_build(catalog, tmp_path / "site", mini_site)
    assert any("sample-mono-02: specimen" in e for e in caught.value.errors)
    assert not (tmp_path / "site").exists()


def test_a_mismatching_specimen_fails(tmp_path, catalog, mini_site):
    (catalog[0].parent / "specimens" / "sample-mono-02.svg").write_bytes(svg("changed"))
    with pytest.raises(build.BuildError, match=r"sample-mono-02: specimen .* sha256"):
        run_build(catalog, tmp_path / "site", mini_site)


def test_a_specimen_with_a_script_fails(tmp_path, catalog, mini_site):
    blob = b'<svg xmlns="http://www.w3.org/2000/svg" width="9" height="9"><script>x</script></svg>'
    (catalog[0].parent / "specimens" / "sample-mono-02.svg").write_bytes(blob)
    edit_catalog(catalog[0], "sample-mono-02", preview__sha256=hashlib.sha256(blob).hexdigest())
    with pytest.raises(build.BuildError, match="script"):
        run_build(catalog, tmp_path / "site", mini_site)


def test_only_a_pinned_specimen_is_served_cut_to_its_name(tmp_path, catalog, mini_site):
    """PLAN-REVIEWERS-1.md step 7a (the owner's site ruling of 2026-10-05,
    specimen_trim_served): a committed specimen whose sha256 ``trim.PINS_FILE`` lists is served
    cut down to its name line, moved left by its ``trim.SHIFTS`` entry, and its no-script image
    takes the cut file's aspect ratio. The same file is served as it is for a font flagged
    ``specimen_name_only``, and so is a file the trim could cut whose sha256 isn't pinned."""
    real = (ROOT / "build" / "specimens" / "reenie-beanie.svg").read_bytes()
    sha = hashlib.sha256(real).hexdigest()
    assert sha in trim.pinned()
    assert trim.SHIFTS[sha] > 0
    # one coordinate changed: still of the renderer's form, but never checked against a font
    other = re.sub(rb'd="m(\d+)', lambda m: b'd="m%d' % (int(m[1]) + 1), real, count=1)
    assert other != real
    assert hashlib.sha256(other).hexdigest() not in trim.pinned()
    assert trim.name_only(other) != other
    for font_id, blob in (
        ("sample-sans-01", real),
        ("sample-display-25", real),
        ("sample-mono-02", other),
    ):
        (catalog[0].parent / "specimens" / f"{font_id}.svg").write_bytes(blob)
        edit_catalog(catalog[0], font_id, preview__sha256=hashlib.sha256(blob).hexdigest())
    edit_catalog(catalog[0], "sample-display-25", flags=["specimen_name_only"])
    edit_catalog(catalog[0], "sample-mono-02", flags=[])
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    imgs = {Path(a["src"]).name.split(".")[0]: a for t, a in page.tags if t == "img"}
    cut = trim.name_only(real, trim.SHIFTS[sha])
    assert len(cut) < len(real)
    assert cut != trim.name_only(real)
    for font_id, served in (
        ("sample-sans-01", cut),
        ("sample-display-25", real),
        ("sample-mono-02", other),
    ):
        img = imgs[font_id]
        assert asset(out, img["src"]).read_bytes() == served, font_id
        width, height = build.svg_size(served)
        assert (img["width"], img["height"]) == (str(round(width * 64 / height)), "64")


def test_a_font_from_a_release_archive_is_served_like_any_other(tmp_path, catalog, mini_site):
    """A font_file that names a member of a zip archive (<archive>.zip#<member>) validates,
    and the build serves the member's bytes from the cache, named by the member's format."""
    victim = "sample-sans-05"
    url = "https://fonts.example/downloads/Sample%201.0.zip#Sample%201.0/OTF/Sample-Regular.otf"
    edit_catalog(catalog[0], victim, font_file__url=url, font_file__format="otf")
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    payload = load_json(out, next(a["data-details"] for t, a in page.tags if t == "ol"))
    type_own = payload["fonts"][victim]["type_own"]
    served = asset(out, type_own["url"])
    assert served.read_bytes() == fake_font(victim)
    assert served.name.startswith(f"{victim}.")
    assert served.suffix == ".otf"


def test_a_missing_font_file_fails(tmp_path, catalog, mini_site):
    cache = catalog[1]
    victim = next(f for f in SAMPLE["fonts"] if f["id"] == "sample-sans-05")
    sha = hashlib.sha256(fake_font(victim["id"])).hexdigest()
    fonts.cache_path(sha, cache).unlink()
    with pytest.raises(build.BuildError, match=r"sample-sans-05: font file .* fetch-fonts"):
        run_build(catalog, tmp_path / "site", mini_site)


def test_a_corrupt_font_file_fails(tmp_path, catalog, mini_site):
    cache = catalog[1]
    sha = hashlib.sha256(fake_font("sample-sans-05")).hexdigest()
    fonts.cache_path(sha, cache).write_bytes(b"corrupt")
    with pytest.raises(build.BuildError, match="sample-sans-05: font file"):
        run_build(catalog, tmp_path / "site", mini_site)


def test_every_problem_is_reported_at_once(tmp_path, catalog, mini_site):
    path, cache = catalog
    (path.parent / "specimens" / "sample-mono-02.svg").unlink()
    for child in cache.iterdir():
        child.unlink()
    with pytest.raises(build.BuildError) as caught:
        run_build(catalog, tmp_path / "site", mini_site)
    assert len(caught.value.errors) == 1 + sum(bool(f["font_file"]) for f in SAMPLE["fonts"])


@pytest.mark.parametrize(
    ("blob", "size"),
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="48"></svg>', (300, 48)),
        (b'<svg width="120.4px" height=\'40\' viewBox="0 0 1 1"/>', (120, 40)),
        (b'<svg viewBox="0 0 250.6 50" width="100%"/>', (251, 50)),
        (b'<?xml version="1.0"?>\n<svg\n viewBox="0,0,10,20"/>', (10, 20)),
        # links inside the file are fine (a glyph drawn once and reused)
        (b'<svg width="9" height="3"><path id="g" d="M0 0"/><use href="#g"/></svg>', (9, 3)),
        (b'<svg width="9" height="3"><use xlink:href = "#g"/></svg>', (9, 3)),
    ],
)
def test_svg_size(blob, size):
    assert build.svg_size(blob) == size


@pytest.mark.parametrize(
    "blob",
    [
        b"<svg/>",
        b"<html></html>",
        b'<svg width="1" height="1" onload="x()"/>',
        b'<svg width="1" height="1"><a href="https://example.com/"/></svg>',
        b'<svg width="1" height="1"><image xlink:href="/x.png"/></svg>',
        b'<svg width="1" height="1"><use href=#g /></svg>',
        b'<svg width="1" height="1"><foreignObject/></svg>',
        b'<svg width="0" height="1"/>',
        b'<svg width="inf" height="1"/>',
        b'<svg width="nan" height="1"/>',
    ],
)
def test_svg_size_refuses(blob):
    with pytest.raises(ValueError):  # noqa: PT011
        build.svg_size(blob)


# ------------------------------------------------------------------- inputs and outputs


def test_an_invalid_catalog_fails(tmp_path, catalog, mini_site):
    edit_catalog(catalog[0], "sample-sans-01", ranks={})
    with pytest.raises(data.CatalogError):
        run_build(catalog, tmp_path / "site", mini_site)


def test_a_file_that_isnt_json_fails(tmp_path, catalog, mini_site):
    catalog[0].write_text("{", encoding="utf-8")
    with pytest.raises(data.CatalogError, match=r"catalog-site\.json"):
        run_build(catalog, tmp_path / "site", mini_site)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ('{"schema_version": "1.0.0", "schema_version": "1.0.0"}', "repeated"),
        ('{"schema_version": NaN}', "NaN"),
        ('{"schema_version": -Infinity}', "Infinity"),
        ("\ufeff{}", "BOM"),
    ],
)
def test_ambiguous_json_is_refused(tmp_path, catalog, mini_site, text, problem):
    """A repeated key would let the schema check one value while the page shows another."""
    catalog[0].write_text(text, encoding="utf-8")
    with pytest.raises(data.CatalogError, match=problem):
        run_build(catalog, tmp_path / "site", mini_site)
    with pytest.raises(data.CatalogError, match=problem):
        data.validate_file(catalog[0])
    assert main(["validate", str(catalog[0])]) == 1


@pytest.mark.parametrize("commit", ["abc", "0" * 39, "A" * 40, "0" * 40 + "-clean"])
def test_a_malformed_commit_is_refused(tmp_path, catalog, mini_site, commit):
    with pytest.raises(build.BuildError, match="hex"):
        run_build(catalog, tmp_path / "site", mini_site, commit=commit)


def test_a_dirty_tree_is_refused_unless_allowed(tmp_path, catalog, mini_site, monkeypatch):
    answers = {"rev-parse": "f" * 40, "status": " M src/tff_site/build.py"}
    monkeypatch.setattr(build, "_git", lambda root, *args: answers[args[0]])
    with pytest.raises(build.BuildError, match="allow-dirty"):
        run_build(catalog, tmp_path / "site", mini_site, commit=None)
    result = run_build(catalog, tmp_path / "site", mini_site, commit=None, allow_dirty=True)
    assert result.version["commit"] == "f" * 40 + "-dirty"
    answers["status"] = ""
    result = run_build(catalog, tmp_path / "site", mini_site, commit=None)
    assert result.version["commit"] == "f" * 40


def test_git_commit_reads_the_repository():
    commit = build.git_commit(ROOT, allow_dirty=True)
    assert build.COMMIT_PATTERN.fullmatch(commit)


def test_git_commit_outside_a_repository_asks_for_commit(tmp_path):
    with pytest.raises(build.BuildError, match="--commit"):
        build.git_commit(tmp_path)


def test_a_directory_that_isnt_a_build_is_not_replaced(tmp_path, catalog, mini_site):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(build.BuildError, match=r"version\.txt"):
        run_build(catalog, out, mini_site)
    assert (out / "notes.txt").read_text(encoding="utf-8") == "keep me"


def test_a_file_is_not_replaced(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    out.write_text("keep me", encoding="utf-8")
    with pytest.raises(build.BuildError, match="not a directory"):
        run_build(catalog, out, mini_site)
    assert out.read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize("where", ["repo", "data", "site"])
def test_source_directories_are_never_replaced(tmp_path, catalog, mini_site, where):
    out = {"repo": ROOT, "data": catalog[0].parent, "site": mini_site}[where]
    with pytest.raises(build.BuildError, match="refusing"):
        run_build(catalog, out, mini_site)


def test_a_failed_build_keeps_the_previous_site(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    before = tree(out)
    (catalog[0].parent / "specimens" / "sample-mono-02.svg").unlink()
    with pytest.raises(build.BuildError):
        run_build(catalog, out, mini_site)
    assert tree(out) == before
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_missing_static_files_fail(tmp_path, catalog, mini_site):
    site = tmp_path / "site-src"
    shutil.copytree(mini_site, site)
    (site / "static" / "share.png").unlink()
    with pytest.raises(build.BuildError, match=r"missing share\.png"):
        run_build(catalog, tmp_path / "out", site)


def test_only_the_contract_static_files_ship(tmp_path, catalog, mini_site):
    site = tmp_path / "site-src"
    shutil.copytree(mini_site, site)
    (site / "static" / "notes.txt").write_text("draft", encoding="utf-8")
    (site / "static" / ".DS_Store").write_bytes(b"x")
    (site / "static" / "_src").mkdir()
    (site / "static" / "_src" / "share.svg").write_text("<svg/>", encoding="utf-8")
    out = tmp_path / "out"
    run_build(catalog, out, site)
    top = {p.name for p in out.iterdir() if p.is_file()}
    assert top & {"notes.txt", ".DS_Store"} == set()
    assert set(build.STATIC_FILES) <= top
    assert not (out / "_src").exists()
    for name in build.STATIC_FILES:
        assert (out / name).read_bytes() == STATIC[name]


def test_content_pages_follow_their_paths(tmp_path, catalog, mini_site, monkeypatch):
    site = tmp_path / "site-src"
    shutil.copytree(mini_site, site)
    (site / "templates" / "privacy.html.j2").write_text(TEMPLATES["about.html.j2"])
    privacy = {**ABOUT, "page": {**ABOUT["page"], "path": "/privacy/", "title": "Privacy"}}
    missing = {**ABOUT, "page": {**ABOUT["page"], "path": "/404.html", "canonical": False}}
    monkeypatch.setattr(
        pages,
        "page_contexts",
        lambda doc: {"/about/": ABOUT, "/privacy/": privacy, "/404.html": missing},
    )
    out = tmp_path / "out"
    run_build(catalog, out, site)
    assert "<h1>Privacy</h1>" in (out / "privacy" / "index.html").read_text()
    assert "<h1>About</h1>" in (out / "404.html").read_text()  # page_contexts wins
    sitemap = (out / "sitemap.xml").read_text()
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert locs == [
        "https://purelyfreefonts.com/",
        "https://purelyfreefonts.com/about/",
        "https://purelyfreefonts.com/privacy/",
    ]
    assert (out / "robots.txt").read_text() == (
        "User-agent: *\nAllow: /\nSitemap: https://purelyfreefonts.com/sitemap.xml\n"
    )


@pytest.mark.parametrize("path", ["/", "/a/b/", "/Upper/", "about", "/.hidden"])
def test_unsupported_page_paths_fail(tmp_path, catalog, mini_site, monkeypatch, path):
    monkeypatch.setattr(pages, "page_contexts", lambda doc: {path: ABOUT})
    with pytest.raises(build.BuildError, match="page"):
        run_build(catalog, tmp_path / "out", mini_site)


def test_the_first_view_must_be_projects(tmp_path, catalog, mini_site):
    """The order is config/site.toml's (tff_site.views), so config's first view is checked."""
    raw = tomllib.loads(views.CONFIG_PATH.read_text(encoding="utf-8"))
    assert raw["views"][0]["key"] == "project"
    raw["views"].insert(0, raw["views"].pop(3))  # Coding first
    config = tmp_path / "site.toml"
    config.write_text(
        "".join(
            "[[views]]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in view.items())
            for view in raw["views"]
        ),
        encoding="utf-8",
    )
    with pytest.raises(build.BuildError, match="M2-D1"):
        run_build(catalog, tmp_path / "site", mini_site, site_config=config)


OLD_WORDING = {  # the catalog's labels before the owner's site ruling of 2026-10-07
    "desktop_chosen": "Desktop: most chosen",
    "desktop_installed": "Desktop: most installed",
    "project": "Used in projects",
    "coding": "Coding fonts",
}


def test_the_page_shows_config_s_view_wording_order_and_retired_view(tmp_path, catalog, mini_site):
    """view_labels_short (2026-10-07): until the next refresh carries the new names into the
    catalog, the build shows config/site.toml's labels and measures lines, by key, in the rank
    select, the list index and the details payload; and since the scoring-method port, config's
    order and retired views (default_rank_project, overall_retired), so a catalog in the
    committed shape of 2026-09-26, Overall first and available, opens on Projects and never
    offers Overall. The catalog keeps its own copies, order and ``available``, and version.txt
    still hashes the file as read."""
    doc = data.load(catalog[0])
    by_key = {view["key"]: view for view in doc["views"]}
    doc["views"] = [by_key["overall"]] + [v for v in doc["views"] if v["key"] != "overall"]
    by_key["overall"]["available"] = True
    for view in doc["views"]:
        if view["key"] in OLD_WORDING:
            view["label"] = OLD_WORDING[view["key"]]
        view["measures"] = f"The catalog's line for {view['key']}."
    catalog[0].write_bytes(jsonio.pretty_bytes(doc))
    blob = catalog[0].read_bytes()
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    wording = views.load()
    shown = [
        {
            **by_key[key],
            "label": given["label"],
            "measures": given["measures"],
            **({"available": False} if given.get("retired") else {}),
        }
        for key, given in wording.items()
    ]
    available = [v for v in shown if v["available"]]
    assert "overall" not in [v["key"] for v in available]
    html = (out / "index.html").read_text(encoding="utf-8")
    options = re.findall(r'<option value="([^"]+)" data-measures="([^"]*)">([^<]*)</option>', html)
    assert [tuple(map(unescape, o)) for o in options] == [
        (v["key"], v["measures"], v["label"]) for v in available
    ]
    assert [o[2] for o in options[:4]] == [
        "Projects",
        "Chosen (desktop)",
        "Installed (desktop)",
        "Coding",
    ]
    page = parse(html)
    ol = next(a for t, a in page.tags if t == "ol")
    assert load_json(out, ol["data-index"])["views"] == shown
    assert load_json(out, ol["data-details"])["views"] == shown
    assert catalog[0].read_bytes() == blob  # the catalog file is left as it was
    assert data.load(catalog[0])["views"] == doc["views"]
    version = dict(line.split("=", 1) for line in (out / "version.txt").read_text().splitlines())
    assert version["catalog_sha256"] == hashlib.sha256(blob).hexdigest()


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            '[[views]]\nkey = "trending"\nlabel = "Trending"\nmeasures = "New."\n',
            "config/site.toml names the view 'trending', which the catalog lacks",
        ),
        (None, "the catalog has the view 'rising', which config/site.toml doesn't name"),
    ],
)
def test_the_build_fails_when_config_and_the_catalog_name_other_views(
    tmp_path, catalog, mini_site, edit, message
):
    """The keys must match exactly: a view in config that the catalog lacks, or the other way
    round, stops the build (view_labels_short)."""
    text = views.CONFIG_PATH.read_text(encoding="utf-8")
    if edit is None:
        start = text.index('[[views]]\nkey = "rising"')
        end = text.index("\n[", start + 1)
        text = text[:start] + text[end + 1 :]
    else:
        text = text.replace("[tiers]", f"{edit}\n[tiers]", 1)
    config = tmp_path / "site.toml"
    config.write_text(text, encoding="utf-8")
    with pytest.raises(build.BuildError) as caught:
        run_build(catalog, tmp_path / "site", mini_site, site_config=config)
    assert caught.value.errors == [message]
    assert not (tmp_path / "site").exists()


def test_the_build_names_a_missing_config(tmp_path, catalog, mini_site):
    with pytest.raises(build.BuildError, match=r"nowhere\.toml"):
        run_build(catalog, tmp_path / "site", mini_site, site_config=tmp_path / "nowhere.toml")


def test_the_command_line_builds(tmp_path, catalog, mini_site, monkeypatch, capsys):
    """``tff-site build`` passes its flags through (the CLI has no --site-dir, so patch it)."""
    monkeypatch.setattr(build, "build", functools.partial(build.build, site_dir=mini_site))
    path, cache = catalog
    out = tmp_path / "site"
    argv = ["build", "--data", str(path), "--out", str(out), "--fonts-dir", str(cache)]
    assert main([*argv, "--commit", COMMIT]) == 0
    files = tree(out)
    assert capsys.readouterr().out == f"built {len(files)} files into {out}\n"
    assert any(name.startswith("assets/fonts/") for name in files)
    assert main([*argv, "--commit", COMMIT, "--no-font-files"]) == 0
    assert not any(name.startswith("assets/fonts/") for name in tree(out))


def test_site_context_matches_the_contract():
    assert build.site_context() == {
        "name": "Purely Free Fonts",
        "base_url": "https://purelyfreefonts.com",
        "repo_url": "https://github.com/byronshock/purelyfreefonts",
        "feedback": {
            "issues_url": "https://github.com/byronshock/purelyfreefonts/issues/new/choose",
            "email": "admin@purelyfreefonts.com",
            "mailto": "mailto:admin@purelyfreefonts.com?subject=purelyfreefonts.com",
        },
        "tip_url": "https://buy.stripe.com/dRm00beFeaJ307651Qf7i00",
        "blog": None,
    }
    assert (
        list(data.CATEGORY_LABELS)
        == data.schema()["$defs"]["font"]["properties"]["category"]["enum"]
    )


# ---------------------------------------------------------------------- asset helpers


def test_hashing_helpers(tmp_path):
    blob = b"body{}"
    h = hashlib.sha256(blob).hexdigest()[:10]
    assert assets.content_hash(blob) == h
    assert assets.hashed_name("style.css", blob) == f"style.{h}.css"
    assert (
        assets.hashed_name("specimens/sample-sans-01.svg", blob)
        == f"specimens/sample-sans-01.{h}.svg"
    )
    assert assets.hashed_name("v1.0/LICENSE", blob) == f"v1.0/LICENSE.{h}"
    assert assets.write_hashed(tmp_path, "fonts/a.ttf", blob) == f"/assets/fonts/a.{h}.ttf"
    assert (tmp_path / "assets" / "fonts" / f"a.{h}.ttf").read_bytes() == blob
    for bad in ("/abs.js", "../up.js", "a//b.js", "./a.js"):
        with pytest.raises(ValueError, match="relative"):
            assets.write_hashed(tmp_path, bad, blob)


GOOD_PART = "// 20-view: test.\nconst View = (() => {\n  const x = 1;\n  return { x };\n})();\n"


@pytest.mark.parametrize(
    ("filename", "source", "problem"),
    [
        ("20-view.js", "const View = 1;\nconst Extra = 2;\n", "top-level declarations"),
        ("20-view.js", "const View = 1;\nfunction helper() {}\n", "top-level declarations"),
        ("20-view.js", "let View = 1;\n", "top-level declarations"),
        ("20-view.js", "const Other = 1;\n", "want one: const View"),
        ("20-view.js", "const View = 1;\ndocument.title = 'x';\n", "top-level statement"),
        ("20-view.js", "const View = 1;\nMain.start();\n", "top-level statement"),
        ("20-view.js", "import x from './x.js';\nconst View = 1;\n", "import or export"),
        ("20-view.js", "export const View = 1;\n", "import or export"),
        ("20-view.js", "const View = (() => {\n  import('./x.js');\n})();\n", "import or export"),
        ("20-view.js", "const View = (() => {\n  n.innerHTML = s;\n})();\n", "innerHTML"),
        ("20-view.js", "const View = (() => {\n  n.outerHTML = s;\n})();\n", "outerHTML"),
        (
            "20-view.js",
            "const View = (() => {\n  n.insertAdjacentHTML(p, s);\n})();\n",
            "insertAdjacentHTML",
        ),
        ("20-view.js", "const View = (() => {\n  eval(s);\n})();\n", "eval("),
        ("20-view.js", "const View = (() => {\n  new Function('x');\n})();\n", "Function("),
        ("20-view.js", "const View = (() => {\n  document.write(s);\n})();\n", "document.write"),
        ("20-view.js", "// never use innerHTML\nconst View = 1;\n", "innerHTML"),
        (
            "20-view.js",
            "const View = (() => {\n  localStorage.setItem('a', 1);\n})();\n",
            "storage",
        ),
        ("20-view.js", "const View = (() => {\n  document.cookie = 'a';\n})();\n", "storage"),
        # ... and the built-file scan's wider names (test_privacy.py), in either module: WebRTC,
        # which connect-src doesn't govern, WebTransport, fetchLater and the other stores
        *(
            (part, f"const {const} = (() => {{\n  {call};\n}})();\n", problem)
            for part, const in (("50-ext.js", "Ext"), ("54-owned-match.js", "OwnedMatch"))
            for call, problem in (
                ("new RTCPeerConnection()", "forbidden: RTCPeerConnection"),
                ("new webkitRTCPeerConnection()", "forbidden: RTCPeerConnection"),
                ("new WebTransport('/x')", "forbidden: WebTransport"),
                ("fetchLater('/x')", "forbidden: fetchLater"),
                ("openDatabase('x')", "storage API: openDatabase"),
                ("globalThis.navigator.storage.persist()", "storage API: navigator.storage"),
                ("s.getDirectory()", "storage API: getDirectory"),
            )
        ),
        (
            "20-view.js",
            "const View = (() => {\n  fetch('https://example.com/x');\n})();\n",
            "absolute",
        ),
        ("20-view.js", "const View = (() => {\n  fetch(`//example.com/x`);\n})();\n", "absolute"),
        ("99-extra.js", "const Extra = 1;\n", "not a JS part"),
        ("90-main.js", "const Main = 1;\n", "Main.start();"),
        ("90-main.js", "const Main = 1;\nMain.start();\nconst Late = 2;\n", "Main.start();"),
        ("90-main.js", "const Main = 1;\nMain.start();\nMain.start();\n", "once"),
        ("20-view.js", "const View = (() => {\n  return 1;\n})(); go();\n", "top-level statement"),
        ("20-view.js", "const View = (() => {\n  return 1;\n})() || go();\n", "top-level"),
        ("20-view.js", "const View = (() => {\n  navigator.sendBeacon(u);\n})();\n", "sendBeacon"),
        ("20-view.js", "const View = (() => {\n  new XMLHttpRequest();\n})();\n", "XMLHttp"),
        ("20-view.js", "const View = (() => {\n  new WebSocket(u);\n})();\n", "WebSocket"),
        (
            "20-view.js",
            "const View = (() => {\n  n.setAttribute('style', s);\n})();\n",
            "setAttribute('style'",
        ),
        # the matcher module (inventory_matcher_on_demand): its hand-off, last and once
        ("58-matcher.js", "const Matcher = 1;\n", assets.MATCHER_START),
        ("58-matcher.js", f"{assets.MATCHER_START}\nconst Matcher = 1;\n", "last top-level"),
        ("58-matcher.js", f"const Matcher = 1;\n{assets.MATCHER_START}\n" * 2, "top-level"),
        (
            "58-matcher.js",
            f"const Matcher = 1;\n{assets.MATCHER_START}\n{assets.MATCHER_START}\n",
            "once",
        ),
        ("58-matcher.js", "const Matcher = 1;\nMain.start();\n", "top-level statement"),
        ("90-main.js", f"const Main = 1;\n{assets.MATCHER_START}\nMain.start();\n", "top-level"),
        ("20-view.js", f"const View = 1;\n{assets.MATCHER_START}\n", "top-level statement"),
        ("58-matcher.js", "const Other = 1;\n", "want one: const Matcher"),
        # ... pure code: no request, page, timer, clock or randomness, Keys included
        ("52-owned-parse.js", "const OwnedParse = (() => {\n  fetch(u);\n})();\n", "fetch("),
        ("54-owned-match.js", "const OwnedMatch = (() => {\n  document.title;\n})();\n", "pure"),
        ("54-owned-match.js", "const OwnedMatch = (() => {\n  window.x;\n})();\n", "pure"),
        ("54-owned-match.js", "const OwnedMatch = (() => {\n  setTimeout(f);\n})();\n", "pure"),
        ("52-owned-parse.js", "const OwnedParse = (() => {\n  Date.now();\n})();\n", "pure"),
        ("52-owned-parse.js", "const OwnedParse = (() => {\n  Math.random();\n})();\n", "pure"),
        ("05-keys.js", "const Keys = (() => {\n  navigator.language;\n})();\n", "pure"),
        ("05-keys.js", "const Keys = (() => {\n  location.href;\n})();\n", "pure"),
        # ... each of MATCHER_JS's names on its own, where no other rule finds it, comments
        # included: an element (not a script) made off any document, a timer and a clock
        *(
            (part, source, f"not in the matcher module (pure code): {needle}")
            for part, const in (("52-owned-parse.js", "OwnedParse"), ("05-keys.js", "Keys"))
            for needle, call in (
                ("createElement", "doc.createElement('b')"),
                ("setInterval(", "setInterval(f, 1)"),
                ("Date(", "new Date()"),
            )
            for source in (
                f"const {const} = (() => {{\n  {call};\n}})();\n",
                f"// not {call}\nconst {const} = 1;\n",
            )
        ),
        # a script element: only Data.loadMatcher adds one, once
        (
            "20-view.js",
            "const View = (() => {\n  document.createElement('script');\n})();\n",
            "a script element",
        ),
        ("40-details.js", "const Details = (() => {\n  Core.el('script', {});\n})();\n", "script"),
        (
            "10-data.js",
            "const Data = (() => {\n  document.createElement('script');\n"
            '  document.createElement("script");\n})();\n',
            "once",
        ),
        # ... in any case and spacing, an HTML or SVG one, through Core.el, or as HTML text
        *(
            ("50-ext.js", f"const Ext = (() => {{\n  {call};\n}})();\n", "a script element")
            for call in (
                "document.createElement('SCRIPT')",
                "document.createElement( 'script')",
                "document.createElement(\n    `script`,\n  )",
                "document.createElementNS('http://www.w3.org/1999/xhtml','script')",
                "document.createElementNS(SVG, ' Script ')",
                "Core.el('Script')",
                'Core.el ("script", {})',
            )
        ),
        (
            "50-ext.js",
            "const Ext = (() => {\n  document.createRange().createContextualFragment(s);\n})();\n",
            "createContextualFragment",
        ),
        ("50-ext.js", "const Ext = (() => {\n  new DOMParser();\n})();\n", "DOMParser"),
        ("50-ext.js", "const Ext = (() => {\n  n.setHTMLUnsafe(s);\n})();\n", "setHTMLUnsafe"),
        ("50-ext.js", "const Ext = (() => {\n  frame.srcdoc = s;\n})();\n", "srcdoc"),
        ("50-ext.js", "const Ext = (() => {\n  new Worker('/w.js');\n})();\n", "Worker("),
        ("50-ext.js", "const Ext = (() => {\n  new SharedWorker(u);\n})();\n", "Worker("),
        # ... and a worker's own way to run another script, in either module, comments included
        *(
            (part, source, "forbidden: importScripts")
            for part, const in (("50-ext.js", "Ext"), ("54-owned-match.js", "OwnedMatch"))
            for source in (
                f"const {const} = (() => {{\n  importScripts('/w.js');\n}})();\n",
                f"// self.importScripts(u) runs a script\nconst {const} = 1;\n",
            )
        ),
        # the hand-off's name: only Data's constant and the matcher's last line
        ("50-ext.js", "const Ext = (() => {\n  globalThis.tffMatcher;\n})();\n", "tffMatcher"),
        (
            "50-ext.js",
            "const Ext = (() => {\n  Object.defineProperty(globalThis, 'tffMatcher', {});\n})();\n",
            "names tffMatcher",
        ),
        (
            "10-data.js",
            "const Data = (() => {\n  const MATCHER_GLOBAL = 'tffMatcher';\n"
            "  const other = 'tffMatcher';\n})();\n",
            "names tffMatcher",
        ),
        (
            "10-data.js",
            "const Data = (() => {\n  const MATCHER_GLOBAL = 'tffMatcher';\n"
            "  const MATCHER_GLOBAL = 'tffMatcher';\n})();\n",
            "once each",
        ),
        (
            "54-owned-match.js",
            "const OwnedMatch = (() => {\n  const x = globalThis['tffMatcher'];\n})();\n",
            "names tffMatcher",
        ),
        # modules share no top-level names: a const the other module alone holds
        (
            "54-owned-match.js",
            "const OwnedMatch = (() => {\n  const x = () => Core.$(1);\n})();\n",
            "Core is a part of the page's module only",
        ),
        (
            "52-owned-parse.js",
            "const OwnedParse = (() => {\n  const x = (s) => Data.loadIndex(s);\n})();\n",
            "Data is a part of the page's module only",
        ),
        (
            "58-matcher.js",
            f"const Matcher = Object.freeze({{ Main }});\n{assets.MATCHER_START}\n",
            "Main is a part of the page's module only",
        ),
        (
            "05-keys.js",
            "const Keys = (() => {\n  const f = () => State.get();\n})();\n",
            "State is a part of the page's module only",
        ),
        (
            "05-keys.js",
            "const Keys = (() => {\n  const f = (s) => OwnedParse.parse(s);\n})();\n",
            "OwnedParse is a part of the matcher's module only",
        ),
        (
            "40-details.js",
            "const Details = (() => {\n  const f = (s) => OwnedMatch.match(s);\n})();\n",
            "OwnedMatch is a part of the matcher's module only",
        ),
    ],
)
def test_the_js_lint_catches(filename, source, problem):
    problems = assets.lint_js_part(filename, source)
    assert any(problem in p for p in problems), problems


def test_the_js_lint_passes_good_parts():
    assert assets.lint_js_part("20-view.js", GOOD_PART) == []
    main = "const Main = (() => {\n  const start = () => {};\n  return { start };\n})();\nMain.start();\n"
    assert assets.lint_js_part("90-main.js", main) == []
    fetching = (
        "const Data = (() => {\n  const get = (url) => fetch(url);\n  return { get };\n})();\n"
    )
    assert assets.lint_js_part("10-data.js", fetching) == []
    loading = "const Data = (() => {\n  const s = document.createElement('script');\n})();\n"
    assert assets.lint_js_part("10-data.js", loading) == []
    handing = f"const Matcher = Object.freeze({{}});\n{assets.MATCHER_START}\n"
    assert assets.lint_js_part("58-matcher.js", handing) == []
    # a comment may name the hand-off and the other module's consts (not a script element); a
    # property, a longer name or a lowercase word is no use of a const
    commented = (
        "// Data.loadMatcher reads globalThis.tffMatcher, as Core.$ reads the page\n"
        "const OwnedMatch = (() => {\n  const ok = (api) => api.Core && api.Data;\n"
        "  const MatchError = 1;\n  const label = (s) => `${s} state`;\n})();\n"
    )
    assert assets.lint_js_part("54-owned-match.js", commented) == []
    reading = (
        "const Data = (() => {\n  const MATCHER_GLOBAL = 'tffMatcher';\n"
        "  const ok = (api) => typeof api.OwnedParse.parse === 'function';\n"
        "  const loadMatcher = () => document.createElement('script');\n})();\n"
    )
    assert assets.lint_js_part("10-data.js", reading) == []
    for closer in ("})(Core);", "}); // end of State", "}", "}).call(globalThis);"):
        source = f"const State = (() => {{\n  const a = 1;\n{closer}\n"
        assert assets.lint_js_part("15-state.js", source) == [], closer
    # the parts in site/js pass the build's lint as they stand
    for part in sorted((ROOT / "site" / "js").glob("*.js")):
        assert assets.lint_js_part(part.name, part.read_text(encoding="utf-8")) == [], part.name


@pytest.mark.parametrize(
    ("filename", "source", "problem"),
    [
        ("20-list.css", "@import url('/x.css');\n", "@import"),
        ("20-list.css", "@font-face { src: url('/f.woff2'); }\n", "@font-face"),
        ("20-list.css", "a { background: url('https://example.com/x.png'); }\n", "url()"),
        ("20-list.css", "a { background: url(//example.com/x.png); }\n", "url()"),
        ("20-list.css", 'a { mask: url("data:image/svg+xml,<svg/>"); }\n', "url()"),
        ("99-extra.css", "a {}\n", "not a CSS part"),
    ],
)
def test_the_css_lint_catches(filename, source, problem):
    problems = assets.lint_css_part(filename, source)
    assert any(problem in p for p in problems), problems


def test_the_css_lint_ignores_comments_and_local_urls():
    source = "/* no @import here; url(https://x) */\na { background: url('/assets/x.svg'); }\n"
    assert assets.lint_css_part("20-list.css", source) == []


def test_concatenation_reports_every_bad_part(tmp_path):
    (tmp_path / "20-view.js").write_text("const View = 1;\nconst Two = 2;\n")
    (tmp_path / "25-render.js").write_text("const Render = (() => {\n  n.innerHTML = '';\n})();\n")
    (tmp_path / "00-core.js").write_text("﻿const Core = 1;\r\n\r\n")
    with pytest.raises(assets.AssetError) as caught:
        assets.concat_js(tmp_path)
    assert sorted({p.split(":")[0] for p in caught.value.problems}) == [
        "20-view.js",
        "25-render.js",
    ]
    (tmp_path / "20-view.js").unlink()
    (tmp_path / "25-render.js").unlink()
    assert assets.concat_js(tmp_path) == "const Core = 1;\n"
    with pytest.raises(assets.AssetError, match="no"):
        assets.concat_css(tmp_path)


def test_each_module_takes_its_own_parts_and_every_part_is_linted(tmp_path):
    """The page's module and the matcher's (inventory_matcher_on_demand) come from one folder:
    each takes the parts its list names, 05-keys.js both, and the lint reads every file in it,
    whichever module is being made."""
    parts = {
        "00-core.js": "const Core = 1;\n",
        "05-keys.js": "// the name keys\nconst Keys = 2;\n",
        "58-matcher.js": f"const Matcher = Object.freeze({{}});\n{assets.MATCHER_START}\n",
        "90-main.js": "const Main = 3;\nMain.start();\n",
    }
    for name, text in parts.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert (
        assets.concat_js(tmp_path) == "const Core = 1;\n\nconst Keys = 2;\n\n" + parts["90-main.js"]
    )
    assert assets.concat_js(tmp_path, assets.MATCHER_PARTS) == (
        "const Keys = 2;\n\n" + parts["58-matcher.js"]
    )
    (tmp_path / "52-owned-parse.js").write_text(
        "const OwnedParse = (() => {\n  fetch('/x');\n})();\n", encoding="utf-8"
    )
    for module in (assets.JS_PARTS, assets.MATCHER_PARTS):
        with pytest.raises(assets.AssetError) as caught:
            assets.concat_js(tmp_path, module)
        assert caught.value.problems == [
            "52-owned-parse.js:2: not in the matcher module (pure code): fetch("
        ]
    (tmp_path / "52-owned-parse.js").unlink()
    (tmp_path / "05-keys.js").unlink()
    (tmp_path / "58-matcher.js").unlink()
    with pytest.raises(assets.AssetError, match="no"):
        assets.concat_js(tmp_path, assets.MATCHER_PARTS)


def test_the_two_modules_lists_agree_and_are_in_filename_order():
    """A part of both modules has one name; each list is in filename order, as the build joins
    them; each module ends with its one statement's part; and the matcher's parts use only
    lower-numbered parts of its own list (modules share no top-level names; the lint refuses a
    use of the other module's consts, test_the_js_lint_catches)."""
    page, matcher = dict(assets.JS_PARTS), dict(assets.MATCHER_PARTS)
    assert {name: page[name] for name in page.keys() & matcher.keys()} == {"05-keys.js": "Keys"}
    for parts in (assets.JS_PARTS, assets.MATCHER_PARTS):
        names = [name for name, _ in parts]
        assert names == sorted(names)
        assert len(set(names)) == len(names)
    assert assets.JS_PARTS[-1][0] == assets.MAIN_PART
    assert assets.MATCHER_PARTS[-1][0] == assets.MATCHER_PART
    consts = [const for _, const in assets.MATCHER_PARTS]
    for k, (name, const) in enumerate(assets.MATCHER_PARTS):
        code = assets.strip_js_comments((ROOT / "site" / "js" / name).read_text(encoding="utf-8"))
        used = {c for c in consts if c != const and re.search(rf"\b{c}\b", code)}
        assert used <= set(consts[:k]), (name, used)


# --------------------------------------------------------------------- the real site

# Stand-ins for Milestone 2 step 7's templates (A8), used only while they don't exist yet.
CONTENT_STUBS = {
    name: TEMPLATES[name] for name in ("404.html.j2", "robots.txt.j2", "sitemap.xml.j2")
}


@pytest.fixture
def real_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of site/ with step 7's missing pieces stubbed, and step 7's pages if it has them."""
    site = tmp_path / "real-site"
    shutil.copytree(ROOT / "site", site)
    try:
        REAL_PAGE_CONTEXTS(copy.deepcopy(SAMPLE))
    except NotImplementedError:
        monkeypatch.setattr(pages, "page_contexts", lambda doc: {})
        for name, text in CONTENT_STUBS.items():
            if not (site / "templates" / name).exists():
                (site / "templates" / name).write_text(text, encoding="utf-8")
    else:
        monkeypatch.setattr(pages, "page_contexts", REAL_PAGE_CONTEXTS)
    return site


def test_real_templates_render_the_list_contract(tmp_path, catalog, real_site):
    """site/templates with this build's context: CSP lint, rows, ranks, specimens, payloads.

    Unlike the ``test_built_site_*`` tests this uses the ``catalog`` fixture, so specimen rows
    and "Type your own text" are covered before the sample carries real specimen hashes.
    """
    out = tmp_path / "out"
    run_build(catalog, out, real_site)
    for page_file in sorted(out.rglob("*.html")):
        assert csp_problems(page_file.read_text(encoding="utf-8")) == [], page_file
    html = (out / "index.html").read_text(encoding="utf-8")
    page = parse(html)
    tags = page.tags
    assert html.startswith('<!doctype html>\n<html lang="en">')
    assert [t for t, _ in tags].count("h1") == 1
    assert next(a for t, a in tags if t == "a") == {"class": "skip-link", "href": "#main"}
    ol = next(a for t, a in tags if t == "ol" and a.get("id") == "list")
    index = load_json(out, ol["data-index"])
    details = load_json(out, ol["data-details"])
    preloads = [a for t, a in tags if t == "link" and a.get("rel") == "preload"]
    (preload,) = [a for a in preloads if a.get("as") == "fetch"]  # never the match file
    assert (preload["href"], preload["as"]) == (ol["data-index"], "fetch")
    assert load_json(out, ol["data-match"])["format"] == "tff-match"
    assert "crossorigin" in preload
    assert ol["data-run-date"] == SAMPLE["run"]["date"]
    lis = [a for t, a in tags if t == "li" and "font" in (a.get("class") or "").split()]
    rows = [a["data-id"] for a in lis]
    assert rows == index["ids"]
    assert page.ranks == expected_labels(SAMPLE)
    # The owner's site ruling of 2026-09-26 (list_layout): "Not ranked: <reason>" rows are
    # marked so the label takes a line of its own.
    unranked = ["is-unranked" in a["class"].split() for a in lis]
    assert unranked == [label.startswith("Not ranked: ") for label in page.ranks]
    assert any(unranked)
    # the options of each select: rank, the systems to hide, and the sort order
    selects: dict[str, list[str]] = {}
    for t, a in tags:
        if t == "select":
            current = selects.setdefault(a["id"], [])
        elif t == "option":
            current.append(a["value"])
    options = selects["f-rank"]
    assert options == [v["key"] for v in SAMPLE["views"] if v["available"]]
    assert options[0] == "project"
    assert "overall" not in options  # retired (owner ruling of 2026-09-30, overall_retired)
    assert selects["f-os"] == ["", "windows", "macos", "linux", "android"]
    assert "f-sort" not in selects  # sorting is buttons over the list (sort_header)
    sorts = [a for t, a in tags if t == "button" and a.get("data-sort")]
    assert [(a["id"], a["aria-pressed"]) for a in sorts] == [
        ("sort-rank", "true"),
        ("sort-name", "false"),
    ]
    # specimens: the script's span and the no-script image name the same hashed file, and
    # both are decorative, since the heading names the font (specimen_label_hidden)
    spans = [a for t, a in tags if a.get("class") == "spec"]
    imgs = [a for t, a in tags if t == "img" and a.get("class") == "spec-img"]
    assert len(spans) == len(imgs) == len(SPECIMEN_IDS)
    assert [a["data-src"] for a in spans] == [a["src"] for a in imgs]
    assert all(a.get("aria-hidden") == "true" and "role" not in a for a in spans)
    for img in imgs:
        assert img["alt"] == ""
        assert (img["width"], img["height"]) == ("364", "64")
        assert asset(out, img["src"]).is_file()
    # "Type your own text": every font file the details payload names is served
    with_files = [f for f in details["fonts"].values() if f["type_own"]]
    assert len(with_files) == sum(bool(f["font_file"]) for f in SAMPLE["fonts"])
    for font in with_files:
        assert asset(out, font["type_own"]["url"]).stat().st_size == font["type_own"]["size"]
    # the page loads only its own hashed files
    css = next(a["href"] for t, a in tags if t == "link" and a.get("rel") == "stylesheet")
    js = next(a["src"] for t, a in tags if t == "script")
    assert asset(out, css).is_file()
    assert asset(out, js).is_file()
    # no local path leaks into the page
    assert str(Path.home()) not in html
    assert str(tmp_path) not in html


def test_built_site_passes_the_csp_lint(site_dir):
    html_files = sorted(site_dir.rglob("*.html"))
    assert {p.relative_to(site_dir).as_posix() for p in html_files} >= {
        "index.html",
        "404.html",
        "methodology/index.html",
        "privacy/index.html",
        "about/index.html",
    }
    for page in html_files:
        assert csp_problems(page.read_text(encoding="utf-8")) == [], page


def test_built_site_follows_the_output_contract(site_dir, site_data):
    files = tree(site_dir)
    for path in files:
        assert build.SAFE_PATH.fullmatch(path), path
        if path.startswith("assets/"):
            match = HASHED.fullmatch(path.rsplit("/", 1)[1])
            assert match, path
            assert match["h"] == hashlib.sha256(files[path]).hexdigest()[:10], path
    doc = data.load(site_data)
    version = dict(line.split("=", 1) for line in files["version.txt"].decode().splitlines())
    assert list(version) == [
        "commit",
        "run_date",
        "method_version",
        "catalog_sha256",
        "names_sha256",
        "reviewed_sha256",
        "schema",
    ]
    assert build.COMMIT_PATTERN.fullmatch(version["commit"])
    file_sha = hashlib.sha256(site_data.read_bytes()).hexdigest()
    names_path = matchfile.default_names_path(site_data)
    made = matchfile.match_file(doc, names_path)
    assert version == build.version_fields(
        version["commit"],
        doc,
        catalog_sha256=file_sha,
        names_sha256=made.names_sha256,
        reviewed_sha256=made.reviewed_sha256,
    )
    for name in ("robots.txt", "sitemap.xml", *build.STATIC_FILES):
        assert name in files, name
    index = parse(files["index.html"].decode())
    ol = next(a for t, a in index.tags if t == "ol" and a.get("id") == "list")
    local = [ol["data-index"], ol["data-details"], ol["data-match"]]
    for tag, attrs in index.tags:
        rel = set((attrs.get("rel") or "").split())
        if tag == "link" and rel & RESOURCE_RELS:
            local.append(attrs["href"])
        elif tag in {"script", "img"}:
            local.append(attrs["src"])
        elif "spec" in (attrs.get("class") or "").split():
            local.append(attrs["data-src"])
    for url in local:
        assert url.lstrip("/") in files, url
    list_index = json.loads(files[ol["data-index"].lstrip("/")])
    rows = [
        a["data-id"]
        for t, a in index.tags
        if t == "li" and "font" in (a.get("class") or "").split()
    ]
    assert rows == list_index["ids"]
    # the hopper's match file is the one the site's names file gives (CONTRACT.md section 11)
    match = files[ol["data-match"].lstrip("/")]
    assert match == made.blob
    assert json.loads(match)["ids"] == sorted(list_index["ids"])
    assert json.loads(match)["run_date"] == doc["run"]["date"]


def test_built_site_builds_twice_identically(tmp_path, site_data):
    for name in ("one", "two"):
        build.build(site_data, tmp_path / name, commit=COMMIT, font_files=False)
    assert tree(tmp_path / "one") == tree(tmp_path / "two")
