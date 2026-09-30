"""Build the static site from ``catalog-site.json`` into a directory.

The build is offline and deterministic: it makes no network request (tests run it under a
socket guard), reads no clock, and two builds of the same inputs are byte-identical.

Steps: validate the data (``tff_site.data``); concatenate and hash the JS and CSS parts
(``tff_site.assets``); write the list-index and details JSON; copy each ``preview`` SVG from
``specimens/`` next to the data file to ``/assets/specimens/<id>.<h>.svg``, checking its
sha256; copy each ``font_file`` from the font cache to ``/assets/fonts/<id>.<h>.<ext>``, and the
interface font (``UI_FONTS``, from ``site/static/fonts/``) to ``/assets/ui/<stem>.<h>.woff2``;
render the templates (``site/templates``, Jinja2 with autoescape and StrictUndefined) and the
pages (``tff_site.pages``); write ``robots.txt``, ``sitemap.xml``, the static files and
``version.txt``. Output layout: site/CONTRACT.md, "Build output".

Pages. The list page ``/`` is ``index.html.j2``, rendered here with the ``list`` context of
site/CONTRACT.md section 3. Every other page comes from ``pages.page_contexts(doc)``, keyed by
URL path, and its template and file follow from the path:

- ``/about/`` -> ``about.html.j2`` -> ``about/index.html``
- ``/404.html`` -> ``404.html.j2`` -> ``404.html``; ``/robots.txt`` -> ``robots.txt.j2``

``/404.html``, ``/robots.txt`` and ``/sitemap.xml`` are rendered with a plain ``page`` context
when ``page_contexts`` leaves them out. Every page gets ``site``, ``page``, ``assets`` and
``build`` (section 3), plus ``urls``: the absolute URLs of the canonical pages, sorted by
path, for the sitemap.

The blog (``tff_site.blog``) adds ``/blog/``, ``/blog/<slug>/`` and ``/blog/feed.xml``, whose
templates and files ``blog.page_files`` names, and the posts' images, once a post is
published (with ``drafts``, drafts count); ``site.blog`` then turns on the Blog link.

Static files: exactly ``STATIC_FILES`` are copied from ``site/static/`` (section 2); anything
else there (``_src/``, drafts) never ships.

A row's ``specimen`` ``width`` and ``height`` are the size the no-script ``<img>`` shows at:
``SPEC_BOX_PX`` high (``--spec-h``), with the SVG's aspect ratio. A row's ``nerd`` is true
for a font with a Nerd Font build (``links.nerd``), which shows the catalog's ``nerd`` marker
beside its name; the list context's ``nerd`` carries that wording for the rows and the legend.

The output is written to a sibling staging directory and swapped in at the end, so a failed
build leaves the previous site as it was. Only a directory that holds a previous build (a
``version.txt``), an empty one, or a new path is replaced.
"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import jsonio
from tff_site import assets, blog, data, fonts, pages

if TYPE_CHECKING:
    import jinja2

REPO_ROOT = Path(__file__).resolve().parents[2]
SITE_DIR = REPO_ROOT / "site"
DEFAULT_DATA = REPO_ROOT / "build" / "catalog-site.json"
DEFAULT_OUT = REPO_ROOT / "build" / "site"

SITE_NAME = "Truly Free Fonts"
BASE_URL = "https://trulyfreefonts.com"
# M2 step 8 sets this to the Stripe link in ops/DONATIONS.md, once its checks pass.
TIP_URL: str | None = None
# Page text for the list page and the fallback 404 page (owner approval: M2 step 7). Some
# fonts here need attribution, so the text claims "any personal or commercial use", as the
# list page's lead does, and never "no restrictions".
HOME_PAGE = {
    "path": "/",
    "title": "Truly Free Fonts: the most popular fonts free for personal and commercial use",
    "description": (
        "The most popular truly free fonts, ranked by how many people install and use them. "
        "Every font here is free for any personal or commercial use."
    ),
    "canonical": True,
}
NOT_FOUND_PAGE = {
    "path": "/404.html",
    "title": "Page not found",
    "description": "There is no page at this address on Truly Free Fonts.",
    "canonical": False,
}
# Row badges (site/CONTRACT.md section 3), in this order. Kept short (owner ruling of
# 2026-09-30, filters_layout): the category already says "Monospace", every font is
# redistributable (Rule 3), "Comes with" names operating systems and apps only, and "Pulled
# in by" is in the details panel.
BADGE_TEXT = {
    "variable": "Adjustable weight",  # owner ruling of 2026-09-30, variable_label
    "limited": "Limited accents",
    "attribution": "Credit required",
    "preinstalled": "Comes with {}",
    "new": "New",
}
UNRANKED_PREFIX = "Not ranked: "
# Copied from site/static/ to the site root (site/CONTRACT.md section 2).
STATIC_FILES = ("apple-touch-icon.png", "favicon.ico", "favicon.svg", "share.png", "wordmark.svg")
# The interface font (AUTHORITY.md, "Interface font"): (file in site/static/fonts/, style).
# Each is copied to /assets/ui/<stem>.<h>.woff2 and declared ahead of the CSS parts; the first
# is preloaded (site/CONTRACT.md section 6).
UI_FONT_FAMILY = "Arimo"
UI_FONTS = (("arimo.woff2", "normal"), ("arimo-italic.woff2", "italic"))
UI_FONT_WEIGHTS = "400 700"
# The deploy receiver accepts only these paths (site/CONTRACT.md section 2).
SAFE_PATH = re.compile(r"^([a-z0-9][a-z0-9._-]*/)*[a-z0-9][a-z0-9._-]*$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(-dirty)?$")
MAX_SVG_PX = 100_000  # a sanity bound on a specimen's size; also refuses inf and nan
# The specimen box height, --spec-h in site/css/00-tokens.css (site/CONTRACT.md section 6).
SPEC_BOX_PX = 48
VERSION_FILE = "version.txt"

_SVG_ROOT = re.compile(rb"<svg\b([^>]*)>", re.IGNORECASE)
_SVG_ATTR = re.compile(rb"([\w:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
# Scripts, event handlers, foreign HTML, and links anywhere but inside the file (href="#id").
_SVG_ACTIVE = re.compile(
    rb"<script|<foreignobject|\son[a-z]+\s*=|href\s*=\s*+(?![\"']#)", re.IGNORECASE
)
_PAGE_DIR = re.compile(r"^/([a-z0-9][a-z0-9-]*)/$")
_PAGE_FILE = re.compile(r"^/([a-z0-9][a-z0-9-]*\.[a-z0-9]+)$")


class BuildError(ValueError):
    """The build can't go on. ``errors`` holds one line per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s); first: {errors[0] if errors else '-'}")


@dataclass(frozen=True, slots=True)
class BuildResult:
    """What a build wrote. ``version`` holds the ``version.txt`` fields."""

    out_dir: Path
    files: int
    version: dict[str, str]


@dataclass(frozen=True, slots=True)
class Specimen:
    """A copied specimen: its hashed URL and its no-script ``<img>`` size in CSS pixels."""

    url: str
    width: int
    height: int


def build(
    data_path: Path = DEFAULT_DATA,
    out_dir: Path = DEFAULT_OUT,
    *,
    fonts_dir: Path = fonts.DEFAULT_CACHE,
    commit: str | None = None,
    allow_dirty: bool = False,
    font_files: bool = True,
    site_dir: Path = SITE_DIR,
    drafts: bool = False,
    blog_dir: Path | None = None,
) -> BuildResult:
    """Build the site into ``out_dir``, replacing its contents.

    ``commit`` defaults to ``git rev-parse HEAD`` of the repository; a dirty tree is refused
    unless ``allow_dirty``, which records ``<sha>-dirty``. With ``font_files=False`` no font
    file is copied and "Type your own text" is left out (for builds without the font cache).
    A missing or mismatching specimen or font file is an error, except a preview whose sha256
    is ``data.PLACEHOLDER_SHA256``, which is built as "Preview not available yet".
    Blog posts come from ``blog_dir`` (default ``site_dir/content/blog``); ``drafts``
    publishes posts marked ``draft: true`` too (the staging deploy).
    """
    data_path, out_dir, site_dir, fonts_dir = map(Path, (data_path, out_dir, site_dir, fonts_dir))
    doc, catalog_sha256 = _load_valid(data_path)
    posts = blog.load(
        site_dir / "content" / "blog" if blog_dir is None else blog_dir, drafts=drafts
    )
    if commit is None:
        commit = git_commit(REPO_ROOT, allow_dirty=allow_dirty)
    elif not COMMIT_PATTERN.fullmatch(commit):
        raise BuildError([f"commit {commit!r} is not 40 lowercase hex digits (optionally -dirty)"])
    target = _check_out_dir(out_dir, protected=(REPO_ROOT, site_dir, data_path.parent, fonts_dir))
    # A build without font files serves a catalog in which no font has one.
    site_doc = doc if font_files else {**doc, "fonts": [_no_file(f) for f in doc["fonts"]]}

    version = version_fields(commit, doc, catalog_sha256=catalog_sha256)
    stage = _staging_dir(target)
    try:
        _write_site(stage, doc, site_doc, data_path.parent, fonts_dir, version, site_dir, posts)
        files = _check_tree(stage)
        _swap(stage, target)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return BuildResult(out_dir=out_dir, files=files, version=version)


def git_commit(repo_root: Path = REPO_ROOT, *, allow_dirty: bool = False) -> str:
    """Return the 40-hex HEAD commit, with ``-dirty`` appended when allowed and dirty."""
    sha = _git(repo_root, "rev-parse", "--verify", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise BuildError([f"git rev-parse HEAD gave {sha!r}, not a 40-hex commit"])
    if _git(repo_root, "status", "--porcelain", "--untracked-files=normal"):
        if not allow_dirty:
            raise BuildError(
                [f"{repo_root} has uncommitted changes; commit them or pass --allow-dirty"]
            )
        return f"{sha}-dirty"
    return sha


def version_txt(commit: str, doc: dict, *, catalog_sha256: str | None = None) -> str:
    """Return ``version.txt``: commit, run_date, method_version, catalog_sha256, schema.

    It never contains a build time, so rebuilding the same commit gives the same bytes.
    ``catalog_sha256`` is the sha256 of the ``catalog-site.json`` file, so
    ``sha256sum build/catalog-site.json`` matches the live site (design-m2 §5); the build
    passes the hash of the bytes it read. Without it, the hash of ``doc`` in the form
    Milestone 1's export writes (``jsonio.pretty_bytes``, as ``jsonio.dump``) is used.
    """
    return _version_text(version_fields(commit, doc, catalog_sha256=catalog_sha256))


def version_fields(
    commit: str, doc: Mapping[str, Any], *, catalog_sha256: str | None = None
) -> dict[str, str]:
    """Return the ``version.txt`` fields, in file order (see ``version_txt``)."""
    if catalog_sha256 is None:
        catalog_sha256 = hashlib.sha256(jsonio.pretty_bytes(doc)).hexdigest()
    elif not re.fullmatch(r"[0-9a-f]{64}", catalog_sha256):
        raise ValueError(f"catalog_sha256 {catalog_sha256!r} is not 64 hex digits")
    major = str(doc["schema_version"]).split(".", 1)[0]
    return {
        "commit": commit,
        "run_date": doc["run"]["date"],
        "method_version": doc["run"]["method_version"],
        "catalog_sha256": catalog_sha256,
        "schema": f"catalog-site/{major}",
    }


def site_context(*, blog_nav: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The ``site`` context every page gets (site/CONTRACT.md section 3). ``blog_nav`` is
    ``blog.nav(posts)``: None until a post is published."""
    return {
        "name": SITE_NAME,
        "base_url": BASE_URL,
        "repo_url": data.REPO_URL,
        "feedback": {
            "issues_url": f"{data.REPO_URL}/issues/new/choose",
            "email": data.FEEDBACK_EMAIL,
            "mailto": f"mailto:{data.FEEDBACK_EMAIL}?subject=trulyfreefonts.com",
        },
        "tip_url": TIP_URL,
        "blog": None if blog_nav is None else dict(blog_nav),
    }


def rank_labels(fonts_in_order: list[Mapping[str, Any]], key: str = data.DEFAULT_VIEW) -> list[str]:
    """Rank labels of an unfiltered view, as ``View.compute`` numbers it (M2-D2).

    A counter counts the exact top 100 only; fonts past it show their band, and unranked
    fonts "Not ranked: <reason>".
    """
    labels: list[str] = []
    count = 0
    for font in fonts_in_order:
        entry = font["ranks"][key]
        if entry["order"] is None:
            labels.append(UNRANKED_PREFIX + data.UNRANKED_LABELS[entry["unranked"]])
        elif entry["rank"] is not None:
            count += 1
            labels.append(str(count))
        else:
            labels.append(entry["band"])
    return labels


def svg_size(svg: bytes) -> tuple[int, int]:
    """Return a specimen's ``(width, height)`` in whole CSS pixels.

    It reads the root element's ``width`` and ``height`` (plain numbers or ``px``), else its
    ``viewBox``. A specimen with scripts, event handlers or links is refused.
    """
    if _SVG_ACTIVE.search(svg):
        raise ValueError("the SVG has a script, an event handler or a link")
    root = _SVG_ROOT.search(svg)
    if root is None:
        raise ValueError("no <svg> element")
    attrs = {m[0].lower(): m[1] or m[2] for m in _SVG_ATTR.findall(root.group(1))}
    try:
        width, height = _px(attrs[b"width"]), _px(attrs[b"height"])
    except KeyError, ValueError:
        box = attrs.get(b"viewbox", b"").replace(b",", b" ").split()
        if len(box) != 4:
            raise ValueError("no width and height, and no viewBox") from None
        width, height = float(box[2]), float(box[3])
    if not (0 < width < MAX_SVG_PX and 0 < height < MAX_SVG_PX):
        raise ValueError(f"size {width} x {height}")
    return max(1, round(width)), max(1, round(height))


# ---------------------------------------------------------------------------------- steps


def _load_valid(path: Path) -> tuple[dict[str, Any], str]:
    """Read, parse and validate the catalog; return it with the sha256 of the bytes read."""
    try:
        blob = path.read_bytes()
        doc = data.loads(blob)
    except (OSError, ValueError) as exc:  # ValueError: bad UTF-8 or JSON (``data.loads``)
        raise data.CatalogError([f"{path}: {exc}"]) from exc
    data.validate(doc)
    return doc, hashlib.sha256(blob).hexdigest()


def _version_text(fields: Mapping[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in fields.items())


def _no_file(font: Mapping[str, Any]) -> dict[str, Any]:
    return {**font, "font_file": None}


def _write_site(
    out: Path,
    doc: dict[str, Any],
    site_doc: dict[str, Any],
    data_dir: Path,
    fonts_dir: Path,
    version: Mapping[str, str],
    site_dir: Path,
    posts: list[blog.Post],
) -> None:
    commit = version["commit"]
    urls = assets.AssetManifest().urls
    urls["app.js"] = assets.write_hashed(out, "app.js", assets.concat_js(site_dir / "js").encode())
    ui_fonts = _copy_ui_fonts(out, site_dir / "static" / "fonts")
    faces = [(ui_fonts[name], style) for name, style in UI_FONTS]
    css = assets.font_faces(UI_FONT_FAMILY, faces, UI_FONT_WEIGHTS)
    css += assets.concat_css(site_dir / "css")
    urls["style.css"] = assets.write_hashed(out, "style.css", css.encode())

    specimens, errors = _copy_specimens(out, site_doc, data_dir)
    font_assets, font_errors = _copy_fonts(out, site_doc, fonts_dir)
    errors += font_errors + _copy_static(out, site_dir / "static")
    if errors:
        raise BuildError(errors)

    index = data.list_index(site_doc, commit=commit)
    urls["list.json"] = assets.write_hashed(out, "list.json", jsonio.canonical_bytes(index))
    payload = data.details(site_doc, font_assets=font_assets)
    urls["details.json"] = assets.write_hashed(out, "details.json", jsonio.canonical_bytes(payload))

    blog.write_images(out, posts)
    common = {
        "site": site_context(blog_nav=blog.nav(posts)),
        "assets": {
            "css": urls["style.css"],
            "js": urls["app.js"],
            "font": ui_fonts[UI_FONTS[0][0]],
        },
        "build": {"commit": commit, "run_date": doc["run"]["date"]},
    }
    list_context = _list_context(site_doc, specimens, urls)
    _render_pages(out, site_dir / "templates", doc, common, list_context, blog.page_contexts(posts))
    _write(out, VERSION_FILE, _version_text(version).encode())


def _list_context(
    doc: Mapping[str, Any], specimens: Mapping[str, Specimen], urls: Mapping[str, str]
) -> dict[str, Any]:
    views = [
        {k: v[k] for k in ("key", "label", "measures")} for v in doc["views"] if v["available"]
    ]
    if not views or views[0]["key"] != data.DEFAULT_VIEW:
        raise BuildError([f"the first available view must be {data.DEFAULT_VIEW!r} (M2-D1)"])
    nerd = doc["nerd"]
    return {
        "views": views,
        "categories": [{"value": k, "label": v} for k, v in data.CATEGORY_LABELS.items()],
        # "No credit required" shows only while some font needs credit (license_filter).
        "credit_filter": any(f["license"]["attribution_required"] for f in doc["fonts"]),
        "systems_os": [{"value": k, "label": v} for k, v in data.OS_LABELS.items()],
        # The legend shows its leading marker as the rows do: "<marker>" + "<after_marker>".
        "nerd": {
            "marker": nerd["marker"],
            "label": nerd["label"],
            "legend": nerd["legend"],
            "after_marker": nerd["legend"].removeprefix(nerd["marker"]),
        },
        "total": len(doc["fonts"]),
        "index_url": urls["list.json"],
        "details_url": urls["details.json"],
        "rows": _rows(doc, specimens),
    }


def _rows(doc: Mapping[str, Any], specimens: Mapping[str, Specimen]) -> list[dict[str, Any]]:
    systems = {s["id"]: (i, s["label"], s["os"]) for i, s in enumerate(doc["systems"])}
    ordered = data.server_order(doc)
    rows = []
    for font, label in zip(ordered, rank_labels(ordered), strict=True):
        spec = specimens.get(font["id"])
        primary = font["links"]["primary"]
        rows.append(
            {
                "id": font["id"],
                "family": font["family"],
                "label": label,
                # The owner's site ruling of 2026-09-26 (list_layout): an unranked row puts
                # its "Not ranked: <reason>" label on a line of its own (li.font.is-unranked).
                "unranked": label.startswith(UNRANKED_PREFIX),
                "category_label": data.CATEGORY_LABELS[data.site_category(font)],
                "license_name": font["license"]["name"],
                "badges": _badges(font, systems),
                "specimen": None
                if spec is None
                else {"url": spec.url, "width": spec.width, "height": spec.height},
                "fallback": None if spec else ("failed" if font["preview_ok"] else "license"),
                "download": {"url": primary["url"], "label": data.destination_name(primary)},
                # A Nerd Font build (TASK-2): the "NF" marker beside the name.
                "nerd": font["links"]["nerd"] is not None,
            }
        )
    return rows


def _badges(
    font: Mapping[str, Any], systems: Mapping[str, tuple[int, str, str]]
) -> list[dict[str, str]]:
    shown = {
        "variable": font["formats"]["variable"],
        "limited": font["latin"]["coverage"] == "basic",
        "attribution": font["license"]["attribution_required"],
        "new": "too_new" in font["flags"],
    }
    details = {"preinstalled": ", ".join(comes_with(font, systems))}
    badges = []
    for key, text in BADGE_TEXT.items():
        if key in details:
            if details[key]:
                badges.append({"key": key, "text": text.format(details[key])})
        elif shown[key]:
            badges.append({"key": key, "text": text})
    return badges


def comes_with(font: Mapping[str, Any], systems: Mapping[str, tuple[int, str, str]]) -> list[str]:
    """The row's "Comes with" names: each operating system that preinstalls the font once
    (Windows, macOS, Linux, Android: every Linux distribution is "Linux"), then each app
    (LibreOffice), in catalog order. The details panel lists every system."""
    kinds = {systems[item["system"]][2] for item in font["preinstalled_on"]}
    names = [label for os_id, label in data.OS_LABELS.items() if os_id in kinds]
    others = sorted(
        {item["system"] for item in font["preinstalled_on"]}
        - {k for k, v in systems.items() if v[2] in data.OS_LABELS},
        key=lambda s: systems[s][0],
    )
    return names + [systems[s][1] for s in others]


def _copy_specimens(
    out: Path, doc: Mapping[str, Any], data_dir: Path
) -> tuple[dict[str, Specimen], list[str]]:
    found: dict[str, Specimen] = {}
    errors: list[str] = []
    for font in sorted(doc["fonts"], key=lambda f: f["id"]):
        if not data.has_specimen(font):
            continue
        preview = font["preview"]
        src = data_dir / preview["path"]
        where = f"{font['id']}: specimen {src}"
        try:
            blob = src.read_bytes()
        except OSError as exc:
            errors.append(f"{where}: {exc.strerror or exc}")
            continue
        digest = hashlib.sha256(blob).hexdigest()
        if digest != preview["sha256"]:
            errors.append(f"{where}: sha256 is {digest}, the catalog says {preview['sha256']}")
            continue
        try:
            width, height = svg_size(blob)
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
            continue
        url = assets.write_hashed(out, f"specimens/{font['id']}.svg", blob)
        shown = max(1, round(width * SPEC_BOX_PX / height))
        found[font["id"]] = Specimen(url=url, width=shown, height=SPEC_BOX_PX)
    return found, errors


def _copy_fonts(
    out: Path, doc: Mapping[str, Any], fonts_dir: Path
) -> tuple[dict[str, str], list[str]]:
    found: dict[str, str] = {}
    errors: list[str] = []
    for font in sorted(doc["fonts"], key=lambda f: f["id"]):
        wanted = font["font_file"]
        if wanted is None:
            continue
        src = fonts.cache_path(wanted["sha256"], fonts_dir)
        where = f"{font['id']}: font file {src}"
        try:
            blob = src.read_bytes()
        except FileNotFoundError:
            errors.append(f"{where}: not cached; run tff-site fetch-fonts or pass --no-font-files")
            continue
        except OSError as exc:
            errors.append(f"{where}: {exc.strerror or exc}")
            continue
        digest = hashlib.sha256(blob).hexdigest()
        if len(blob) != wanted["size"] or digest != wanted["sha256"]:
            errors.append(
                f"{where}: {len(blob)} bytes with sha256 {digest}, "
                f"the catalog says {wanted['size']} bytes with {wanted['sha256']}"
            )
            continue
        found[font["id"]] = assets.write_hashed(out, f"fonts/{font['id']}.{wanted['format']}", blob)
    return found, errors


def _copy_ui_fonts(out: Path, fonts_dir: Path) -> dict[str, str]:
    """Copy ``UI_FONTS`` to ``/assets/ui/`` and return each file's URL by name."""
    missing = [name for name, _ in UI_FONTS if not (fonts_dir / name).is_file()]
    if missing:
        raise BuildError(
            [f"{fonts_dir}: missing {', '.join(missing)} (site/static/_src/make_ui_font.py)"]
        )
    return {
        name: assets.write_hashed(out, f"ui/{name}", (fonts_dir / name).read_bytes())
        for name, _ in UI_FONTS
    }


def _copy_static(out: Path, static_dir: Path) -> list[str]:
    missing = [name for name in STATIC_FILES if not (static_dir / name).is_file()]
    if missing:
        return [f"{static_dir}: missing {', '.join(missing)}"]
    for name in STATIC_FILES:
        _write(out, name, (static_dir / name).read_bytes())
    return []


def _render_pages(
    out: Path,
    templates: Path,
    doc: Mapping[str, Any],
    common: Mapping[str, Any],
    list_context: Mapping[str, Any],
    blog_contexts: Mapping[str, Mapping[str, Any]],
) -> None:
    contexts: dict[str, Mapping[str, Any]] = {"/": {"page": HOME_PAGE, "list": list_context}}
    for path, context in pages.page_contexts(dict(doc)).items():
        if path in contexts:
            raise BuildError([f"pages.page_contexts: {path} is the list page"])
        contexts[path] = context
    for path, context in blog_contexts.items():
        if path in contexts:
            raise BuildError([f"blog.page_contexts: {path} is another page's path"])
        contexts[path] = context
    contexts.setdefault(NOT_FOUND_PAGE["path"], {"page": NOT_FOUND_PAGE})
    for path in ("/robots.txt", "/sitemap.xml"):
        contexts.setdefault(path, {"page": _plain_page(path)})
    urls = [
        BASE_URL + path
        for path, context in sorted(contexts.items())
        if context.get("page", {}).get("canonical")
    ]
    env = _environment(templates)
    for path, context in sorted(contexts.items()):
        template, target = _page_files(path)
        text = env.get_template(template).render({**common, "urls": urls, **context})
        _write(out, target, text.encode("utf-8"))


def _plain_page(path: str) -> dict[str, Any]:
    return {"path": path, "title": SITE_NAME, "description": "", "canonical": False}


def _page_files(path: str) -> tuple[str, str]:
    """Return ``(template, output file)`` for a page's URL path (see the module docstring)."""
    if blog_files := blog.page_files(path):
        return blog_files
    if path == "/":
        return "index.html.j2", "index.html"
    if match := _PAGE_DIR.fullmatch(path):
        return f"{match[1]}.html.j2", f"{match[1]}/index.html"
    if match := _PAGE_FILE.fullmatch(path):
        return f"{match[1]}.j2", match[1]
    raise BuildError([f"pages.page_contexts: unsupported page path {path!r}"])


def _environment(templates: Path) -> jinja2.Environment:
    # Imported here so `tff-site --help` stays fast.
    import jinja2

    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(templates),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        auto_reload=False,
    )


# ------------------------------------------------------------------------ files and git


def _write(out: Path, rel: str, blob: bytes) -> None:
    path = out / rel
    if path.exists():
        raise BuildError([f"{rel} is written twice"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


def _px(value: bytes) -> float:
    return float(value.strip().removesuffix(b"px"))


def _check_out_dir(out_dir: Path, *, protected: tuple[Path, ...]) -> Path:
    target = out_dir.resolve()
    for keep in protected:
        kept = Path(keep).resolve()
        if target == kept or target in kept.parents:
            raise BuildError([f"refusing to replace {out_dir}: it holds {keep}"])
    if target.exists():
        if not target.is_dir():
            raise BuildError([f"{out_dir} is not a directory"])
        if any(target.iterdir()) and not (target / VERSION_FILE).is_file():
            raise BuildError(
                [f"refusing to replace {out_dir}: it isn't empty and holds no {VERSION_FILE}"]
            )
    return target


def _staging_dir(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", suffix=".tmp", dir=target.parent))
    stage.chmod(0o755)  # mkdtemp makes it 0700; the site is served as is
    return stage


def _check_tree(root: Path) -> int:
    count = 0
    bad = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            bad.append(f"{rel}: not a regular file or directory")
        elif not SAFE_PATH.fullmatch(rel):
            bad.append(f"{rel}: path breaks the deploy receiver's rule")
        elif path.is_file():
            count += 1
    if bad:
        raise BuildError(bad)
    return count


def _swap(stage: Path, target: Path) -> None:
    if not target.exists():
        stage.rename(target)
        return
    old = target.with_name(f".{target.name}-old-{os.getpid()}")
    shutil.rmtree(old, ignore_errors=True)
    target.rename(old)
    stage.rename(target)
    shutil.rmtree(old)


def _git(repo_root: Path, *args: str) -> str:
    # GIT_OPTIONAL_LOCKS=0: `git status` must not refresh the index, which other processes
    # may be using.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    except FileNotFoundError as exc:
        raise BuildError(["git is not installed; pass --commit"]) from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit status {proc.returncode}"
        raise BuildError([f"git {' '.join(args)}: {detail}; pass --commit"])
    return proc.stdout.strip()
