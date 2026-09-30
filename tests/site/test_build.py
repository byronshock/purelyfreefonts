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
import os
import re
import shutil
import statistics
import subprocess
import sys
import textwrap
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from tff_catalog import jsonio
from tff_catalog.keys import search_key
from tff_site import assets, build, data, fonts, pages
from tff_site.cli import main

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLE_PATH = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
SAMPLE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
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
    data-details="{{ list.details_url }}" data-run-date="{{ build.run_date }}">
{% for row in list.rows %}
<li class="font" id="font-{{ row.id }}" data-id="{{ row.id }}">
<span class="rank">{{ row.label }}</span>
<h3 class="font-name">{{ row.family }}</h3>
{% if row.specimen %}
<span class="spec" role="img" aria-label="{{ row.family }} sample" data-src="{{ row.specimen.url }}"></span>
<noscript><img class="spec-img" src="{{ row.specimen.url }}" alt="{{ row.family }} sample"
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
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="2800" height="492" viewBox="0 0 2800 492">'
        f'<path d="M0 0h{len(font_id)}v10z"/><!-- {font_id} --></svg>\n'
    ).encode()


def fake_font(font_id: str) -> bytes:
    return f"not really a font: {font_id}\n".encode() * 10


@pytest.fixture
def catalog(tmp_path: Path) -> tuple[Path, Path]:
    """A copy of the sample with three rendered specimens and cached (fake) font files.

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
    assert js == "\n".join(strip_js(textwrap.dedent(JS[n])).strip("\n") + "\n" for n in sorted(JS))
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
    js = next((out / "assets").glob("app.*.js")).read_text(encoding="utf-8")
    for needle in assets.FORBIDDEN_JS + assets.STORAGE_JS:
        assert needle not in js


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


def expected_labels(doc: dict) -> list[str]:
    """The unfiltered Overall view's rank cells as text, written out independently: each
    ranked font's score, 100·Φ(z) rounded, as screen readers hear it (owner rulings of
    2026-09-29 and 2026-09-30: score_display, score_curve, held_marker_style)."""
    labels = []
    for font in data.server_order(doc):
        entry = font["ranks"]["overall"]
        if entry["unranked"]:
            labels.append("Not ranked: " + data.UNRANKED_LABELS[entry["unranked"]])
            continue
        score = round(100 * statistics.NormalDist().cdf(entry["score"]))
        held = ", from one kind of source" if entry["gate_held"] else ""
        labels.append(f"Score {score} of 100{held}")
    return labels


def test_index_is_server_rendered_in_overall_order(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    rows = [a["data-id"] for t, a in page.tags if t == "li" and a.get("class") == "font"]
    index = load_json(out, next(a["data-index"] for t, a in page.tags if t == "ol"))
    assert rows == index["ids"]
    assert len(rows) == 40
    overall = {f["id"]: f["ranks"]["overall"] for f in SAMPLE["fonts"]}
    ranked = [i for i in rows if overall[i]["order"] is not None]
    # By score, best first: a font the two-source rule holds back takes its score's place.
    assert ranked == sorted(ranked, key=lambda i: (-overall[i]["score"], overall[i]["order"]))
    # The sample's held sample-mono-23 (order 101) scores above fonts in the top 100.
    assert ranked != sorted(ranked, key=lambda i: overall[i]["order"])
    assert rows[: len(ranked)] == ranked
    unranked = [f for f in SAMPLE["fonts"] if f["id"] in rows[len(ranked) :]]
    assert rows[len(ranked) :] == [
        f["id"] for f in sorted(unranked, key=lambda f: (f["family"].casefold(), f["id"]))
    ]
    assert page.ranks == expected_labels(SAMPLE)
    assert not set(BANDS) & set(page.ranks)  # no bands, and no numbers, in the list
    assert any(label.endswith(", from one kind of source") for label in page.ranks)
    assert page.ranks[-1] == "Not ranked: no evidence of deliberate installs"


def test_rows_carry_specimens_and_fallbacks(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    run_build(catalog, out, mini_site)
    page = parse((out / "index.html").read_text(encoding="utf-8"))
    specs = {a["data-src"] for t, a in page.tags if t == "span" and a.get("class") == "spec"}
    imgs = [a for t, a in page.tags if t == "img"]
    assert len(specs) == len(imgs) == len(SPECIMEN_IDS)
    for img in imgs:
        assert img["src"] in specs
        # the no-script image shows at the 48 px box height, keeping the SVG's aspect ratio
        assert (img["width"], img["height"]) == ("273", "48")
        assert asset(out, img["src"]).read_bytes().startswith(b"<svg")
    fallbacks = [a["data-fallback"] for t, a in page.tags if a.get("class") == "spec-fallback"]
    assert fallbacks.count("license") == sum(not f["preview_ok"] for f in SAMPLE["fonts"])
    assert len(fallbacks) == 40 - len(SPECIMEN_IDS)
    assert set(fallbacks) == {"license", "failed"}


def test_row_badges_and_download_names():
    rows = {r["id"]: r for r in build._rows(SAMPLE, {})}
    badges = {i: [(b["key"], b["text"]) for b in r["badges"]] for i, r in rows.items()}
    # Short badges (owner ruling of 2026-09-30, filters_layout): Linux distributions are
    # "Linux", apps follow the systems, and "Pulled in by" is in the details panel only.
    assert ("preinstalled", "Comes with Linux") in badges["sample-sans-08"]
    assert not any(k == "pulled" for b in badges.values() for k, _ in b)
    assert ("new", "New") in badges["sample-sans-29"]
    assert ("attribution", "Credit required") in badges["sample-hand-16"]
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
        3,
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
        assert len(col["top"]) == len(col["band"]) == len(col["why"]) == len(col["tier"]) == n
        for i, font_id in enumerate(index["ids"]):
            entry = fonts_[font_id]["ranks"].get(key)
            if entry is None:
                assert (col["tier"][i], col["top"][i], col["band"][i], col["why"][i]) == (
                    ".",
                    0,
                    -1,
                    -1,
                )
            elif entry["unranked"]:
                assert col["tier"][i] == "-"
                assert index["why_labels"][col["why"][i]] == data.UNRANKED_LABELS[entry["unranked"]]
            else:
                assert col["tier"][i] == entry["tier"]
                assert col["top"][i] == (entry["rank"] or 0)
                assert col["band"][i] == (
                    index["bands"].index(entry["band"]) if entry["band"] else -1
                )
        best = [
            (-(e := fonts_[index["ids"][i]]["ranks"][key])["score"], e["order"])
            for i in col["order"]
        ]
        assert best == sorted(best)  # by score, best first
        assert len(col["order"]) == sum(c in "ABC" for c in col["tier"])
        assert len(col["s"]) == len(col["held"]) == n
        assert col["note"] == data.VIEW_NOTES.get(key)
        for i, font_id in enumerate(index["ids"]):
            entry = fonts_[font_id]["ranks"].get(key)
            ranked = entry is not None and entry["order"] is not None
            score = round(100 * statistics.NormalDist().cdf(entry["score"])) if ranked else -1
            assert col["s"][i] == score
            assert col["held"][i] == ("1" if ranked and entry["gate_held"] else "0")
    coding = index["r"]["coding"]["tier"]
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
    for key in ("views", "bands", "tiers", "sources", "systems", "license_classes"):
        assert payload[key] == doc[key], key
    assert payload["v"] == 1
    assert payload["run_date"] == doc["run"]["date"]
    assert payload["state_labels"]["censored"] == "below the floor"
    assert payload["why_labels"] == data.UNRANKED_LABELS
    assert payload["report"] == {
        "issue_url": "https://github.com/byronshock/trulyfreefonts/issues/new?template=license.yml",
        "email": "admin@trulyfreefonts.com",
    }
    assert set(payload["fonts"]) == {f["id"] for f in doc["fonts"]}
    for font in doc["fonts"]:
        got = dict(payload["fonts"][font["id"]])
        type_own = got.pop("type_own")
        want = {k: v for k, v in font.items() if k not in ("preview", "font_file")}
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


# ------------------------------------------------------------------------- version.txt


def test_version_txt(tmp_path, catalog, mini_site):
    out = tmp_path / "site"
    result = run_build(catalog, out, mini_site)
    doc = data.load(catalog[0])
    text = (out / "version.txt").read_text(encoding="utf-8")
    file_sha = hashlib.sha256(catalog[0].read_bytes()).hexdigest()  # as `sha256sum` prints it
    assert text == (
        f"commit={COMMIT}\n"
        f"run_date={doc['run']['date']}\n"
        f"method_version={doc['run']['method_version']}\n"
        f"catalog_sha256={file_sha}\n"
        "schema=catalog-site/1\n"
    )
    assert result.version == dict(line.split("=", 1) for line in text.splitlines())
    assert build.version_txt(COMMIT, doc, catalog_sha256=file_sha) == text
    # the catalog fixture is written as Milestone 1 writes it (jsonio.dump), so the default
    # agrees with the file
    assert build.version_txt(COMMIT, doc) == text


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
    doc = copy.deepcopy(SAMPLE)
    want = hashlib.sha256(jsonio.pretty_bytes(doc)).hexdigest()
    assert f"catalog_sha256={want}\n" in build.version_txt(COMMIT, doc)
    doc["fonts"][0]["family"] = "Sample Sans One"
    assert build.version_txt(COMMIT, doc) != build.version_txt(COMMIT, SAMPLE)
    with pytest.raises(ValueError, match="64 hex"):
        build.version_txt(COMMIT, doc, catalog_sha256="abc")


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
        "https://trulyfreefonts.com/",
        "https://trulyfreefonts.com/about/",
        "https://trulyfreefonts.com/privacy/",
    ]
    assert (out / "robots.txt").read_text() == (
        "User-agent: *\nAllow: /\nSitemap: https://trulyfreefonts.com/sitemap.xml\n"
    )


@pytest.mark.parametrize("path", ["/", "/a/b/", "/Upper/", "about", "/.hidden"])
def test_unsupported_page_paths_fail(tmp_path, catalog, mini_site, monkeypatch, path):
    monkeypatch.setattr(pages, "page_contexts", lambda doc: {path: ABOUT})
    with pytest.raises(build.BuildError, match="page"):
        run_build(catalog, tmp_path / "out", mini_site)


def test_the_first_view_must_be_overall(tmp_path, catalog, mini_site):
    doc = data.load(catalog[0])
    doc["views"].insert(0, doc["views"].pop(3))
    catalog[0].write_bytes(jsonio.pretty_bytes(doc))
    with pytest.raises(build.BuildError, match="M2-D1"):
        run_build(catalog, tmp_path / "site", mini_site)


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
        "name": "Truly Free Fonts",
        "base_url": "https://trulyfreefonts.com",
        "repo_url": "https://github.com/byronshock/trulyfreefonts",
        "feedback": {
            "issues_url": "https://github.com/byronshock/trulyfreefonts/issues/new/choose",
            "email": "admin@trulyfreefonts.com",
            "mailto": "mailto:admin@trulyfreefonts.com?subject=trulyfreefonts.com",
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
    preload = next(a for a in preloads if a.get("as") == "fetch")
    assert (preload["href"], preload["as"]) == (ol["data-index"], "fetch")
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
    assert options[0] == "overall"
    assert selects["f-os"] == ["", "windows", "macos", "linux", "android"]
    assert "f-sort" not in selects  # sorting is buttons over the list (sort_header)
    sorts = [a for t, a in tags if t == "button" and a.get("data-sort")]
    assert [(a["id"], a["aria-pressed"]) for a in sorts] == [
        ("sort-rank", "true"),
        ("sort-name", "false"),
    ]
    # specimens: the script's span and the no-script image name the same hashed file
    spans = {a["aria-label"]: a["data-src"] for t, a in tags if a.get("class") == "spec"}
    imgs = [a for t, a in tags if t == "img" and a.get("class") == "spec-img"]
    assert len(spans) == len(imgs) == len(SPECIMEN_IDS)
    for img in imgs:
        assert spans[img["alt"]] == img["src"]
        assert (img["width"], img["height"]) == ("273", "48")
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
    assert list(version) == ["commit", "run_date", "method_version", "catalog_sha256", "schema"]
    assert build.COMMIT_PATTERN.fullmatch(version["commit"])
    file_sha = hashlib.sha256(site_data.read_bytes()).hexdigest()
    assert version == build.version_fields(version["commit"], doc, catalog_sha256=file_sha)
    for name in ("robots.txt", "sitemap.xml", *build.STATIC_FILES):
        assert name in files, name
    index = parse(files["index.html"].decode())
    ol = next(a for t, a in index.tags if t == "ol" and a.get("id") == "list")
    local = [ol["data-index"], ol["data-details"]]
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


def test_built_site_builds_twice_identically(tmp_path, site_data):
    for name in ("one", "two"):
        build.build(site_data, tmp_path / name, commit=COMMIT, font_files=False)
    assert tree(tmp_path / "one") == tree(tmp_path / "two")
