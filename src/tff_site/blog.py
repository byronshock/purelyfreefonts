"""The blog (Milestone 2 step 7b; M2-D12): Markdown posts built with the rest of the site.

**Posts.** Each post is one file, ``site/content/blog/<yyyy-mm-dd>-<slug>.md``. It starts with
YAML front matter between ``---`` lines, then Markdown whose headings start at ``##``:

- ``title`` (the page's ``<h1>``), ``date`` (``2026-10-01``) and ``description`` (the meta
  description, the index's summary and the feed's summary) are required;
- ``updated`` (a date, not before ``date``) and ``draft`` (``true`` or ``false``) are optional.

The build fails (``BlogError``, every problem at once) on a missing, unknown or repeated field;
a value of the wrong kind; a file name that isn't ``<yyyy-mm-dd>-<slug>.md``, or whose date
isn't the front matter's ``date``; a slug that isn't a page directory under the deploy's path
rule (site/CONTRACT.md section 2: lowercase letters, digits and hyphens, starting with a letter
or digit); a slug two posts share; an image with no alt text; and anything
``pages.render_markdown`` refuses (raw HTML is off, and a ``# `` heading or an image from
another site fails). Every post is checked, drafts included, whether or not it is published.

**Dates** come only from the front matter, never from file times or the clock, so two builds
of the same files are byte-identical. Posts are shown newest first (by ``date``, then slug).

**Drafts.** A post with ``draft: true`` is published only by ``tff-site build --drafts``, which
the staging deploy uses; its pages say it is a draft. Until at least one post is published,
``load`` returns nothing, and the build writes no ``/blog/`` pages and no Blog link
(``nav`` is None).

**Images** sit beside their post, in ``site/content/blog/``, and the post names them by file
name alone: ``![What the chart shows](itf-count.png)``. PNG and SVG only (the deploy's
extension list); an SVG may hold no script, event handler or link (``build.svg_size``). Each
is published as ``/assets/blog/<slug>.<h>.<ext>``, where ``<slug>`` is the post's and ``<h>``
the content hash (``tff_site.assets``), so it is cached as immutable. The ``<img>`` gets the
image's ``width`` and ``height``, so the text doesn't move as it loads.

**Pages** (``page_contexts``; templates in site/CONTRACT.md section 3): ``/blog/``
(``blog.html.j2``), ``/blog/<slug>/`` (``blog-post.html.j2``) and the Atom feed
``/blog/feed.xml`` (``blog-feed.xml.j2``; RFC 4287). None of them loads a script. Each post
page gives its license, CC BY-SA 4.0 (M2-D12; LICENSE-DATA covers ``site/content/blog/``). The
feed's text uses absolute URLs, since feed readers show it away from the site.
"""

import datetime
import re
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from markupsafe import Markup

from tff_site import assets, pages

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = REPO_ROOT / "site" / "content" / "blog"
BASE_URL = "https://purelyfreefonts.com"  # build.BASE_URL; build imports this module

BLOG_PATH = "/blog/"
FEED_PATH = "/blog/feed.xml"
INDEX_TEMPLATE = "blog.html.j2"
POST_TEMPLATE = "blog-post.html.j2"
FEED_TEMPLATE = "blog-feed.xml.j2"

REQUIRED_FIELDS = ("title", "date", "description")
OPTIONAL_FIELDS = ("updated", "draft")
IMAGE_TYPES = ("png", "svg")
# Page text for /blog/ and the feed (owner approval: M2 step 7b).
INDEX_PAGE = {
    "heading": "Blog",
    "description": (
        "Notes from Purely Free Fonts: how the list is made, what changes, and why some "
        "popular fonts are left out."
    ),
}
FEED_TITLE = "Purely Free Fonts blog"
# M2-D12: post text and images are under the catalog data's license.
LICENSE = {
    "name": "CC BY-SA 4.0",
    "url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "scope_url": f"{pages.BLOB_URL}/LICENSE-DATA",
}
FEED_RIGHTS = f"Text and images: {LICENSE['name']} ({LICENSE['url']})"
# The ids base.html.j2 and the blog templates write; a heading in a post may not take one.
TEMPLATE_IDS = frozenset({"main", "feedback", "tip"})

_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
# A page directory under the deploy's path rule, like /about/ (build's page paths).
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IMAGE = re.compile(rf"^(?:\./)?([a-z0-9][a-z0-9._-]*\.({'|'.join(IMAGE_TYPES)}))$")
_POST_PATH = re.compile(r"^/blog/([a-z0-9][a-z0-9-]*)/$")
_HEADING_ID = re.compile(r'<h[1-6] id="([^"]*)"')
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class BlogError(ValueError):
    """A post can't be built. ``errors`` holds one line per problem."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s); first: {errors[0] if errors else '-'}")


@dataclass(frozen=True, slots=True)
class Post:
    """A checked, rendered post.

    ``html`` is its text for the page; ``feed_html`` the same with absolute URLs.
    ``images`` holds ``(asset name, bytes)`` for each image it shows, ready for
    ``assets.write_hashed`` (the name is ``blog/<slug>.<ext>``).
    """

    slug: str
    source: str
    title: str
    date: datetime.date
    description: str
    updated: datetime.date | None
    draft: bool
    html: str
    feed_html: str
    images: tuple[tuple[str, bytes], ...]

    @property
    def path(self) -> str:
        return f"{BLOG_PATH}{self.slug}/"


def load(blog_dir: Path = DEFAULT_DIR, *, drafts: bool = False) -> list[Post]:
    """Check and render every post in ``blog_dir``; return those this build publishes.

    Drafts are published only with ``drafts=True``. The result is newest first; it is empty
    when the directory is missing or holds no published post. Raises ``BlogError``.
    """
    blog_dir = Path(blog_dir)
    if not blog_dir.is_dir():
        return []
    errors: list[str] = []
    posts: list[Post] = []
    slugs: dict[str, str] = {}
    for path in sorted(blog_dir.iterdir()):
        if path.name.startswith(".") or path.suffix != ".md":
            continue
        where = _shown(path)
        if not path.is_file():
            errors.append(f"{where}: not a regular file")
            continue
        name = _NAME.fullmatch(path.name)
        slug = name[2] if name else ""
        if slug in slugs:
            errors.append(f"{where}: {slugs[slug]} has the slug {slug!r} too; slugs are unique")
        elif slug:
            slugs[slug] = where
        post, problems = _read(path, blog_dir, where)
        errors += problems
        if post is not None:
            posts.append(post)
    if errors:
        raise BlogError(errors)
    shown = [post for post in posts if drafts or not post.draft]
    return sorted(shown, key=lambda p: (-p.date.toordinal(), p.slug))


def nav(posts: Sequence[Post]) -> dict[str, str] | None:
    """The ``site.blog`` context: the blog's and the feed's paths, or None with no posts."""
    return {"url": BLOG_PATH, "feed_url": FEED_PATH} if posts else None


def write_images(out: Path, posts: Sequence[Post]) -> None:
    """Write each published post's images to ``out/assets/blog/``."""
    for post in posts:
        for name, blob in post.images:
            assets.write_hashed(out, name, blob)


def page_contexts(posts: Sequence[Post]) -> dict[str, dict[str, Any]]:
    """The context of each blog page, keyed by URL path; empty with no posts.

    ``/blog/`` gets ``page``, ``heading`` and ``posts``; ``/blog/<slug>/`` gets ``page``,
    ``heading`` and ``post``; ``/blog/feed.xml`` gets ``page`` and ``feed``
    (site/CONTRACT.md section 3).
    """
    if not posts:
        return {}
    contexts: dict[str, dict[str, Any]] = {
        BLOG_PATH: {
            "page": _page(BLOG_PATH, INDEX_PAGE["heading"], INDEX_PAGE["description"]),
            "heading": INDEX_PAGE["heading"],
            "posts": [_summary(post) for post in posts],
        },
        FEED_PATH: {
            "page": {
                "path": FEED_PATH,
                "title": FEED_TITLE,
                "description": INDEX_PAGE["description"],
                "canonical": False,
            },
            "feed": _feed(posts),
        },
    }
    for post in posts:
        contexts[post.path] = {
            "page": _page(post.path, post.title, post.description),
            "heading": post.title,
            "post": {
                **_summary(post),
                "content": Markup(post.html),
                "license": dict(LICENSE),
            },
        }
    return dict(sorted(contexts.items()))


def page_files(path: str) -> tuple[str, str] | None:
    """``(template, output file)`` for a blog page's URL path, or None for any other path."""
    if path == BLOG_PATH:
        return INDEX_TEMPLATE, "blog/index.html"
    if path == FEED_PATH:
        return FEED_TEMPLATE, "blog/feed.xml"
    if match := _POST_PATH.fullmatch(path):
        return POST_TEMPLATE, f"blog/{match[1]}/index.html"
    return None


# ------------------------------------------------------------------------- contexts


def _page(path: str, title: str, description: str) -> dict[str, Any]:
    return {
        "path": path,
        "title": f"{title}{pages.TITLE_SEPARATOR}{pages.SITE_NAME}",
        "description": description,
        "canonical": True,
    }


def _summary(post: Post) -> dict[str, Any]:
    return {
        "url": post.path,
        "title": post.title,
        "date": post.date.isoformat(),
        "updated": post.updated.isoformat() if post.updated else None,
        "description": post.description,
        "draft": post.draft,
    }


def _feed(posts: Sequence[Post]) -> dict[str, Any]:
    entries = [
        {
            "id": BASE_URL + post.path,
            "url": BASE_URL + post.path,
            "title": post.title,
            "published": _timestamp(post.date),
            "updated": _timestamp(post.updated or post.date),
            "summary": post.description,
            "content": post.feed_html,  # plain text here: the template escapes it (type="html")
        }
        for post in posts
    ]
    return {
        "id": BASE_URL + BLOG_PATH,
        "title": FEED_TITLE,
        "subtitle": INDEX_PAGE["description"],
        "self_url": BASE_URL + FEED_PATH,
        "alternate_url": BASE_URL + BLOG_PATH,
        "updated": max(entry["updated"] for entry in entries),
        "rights": FEED_RIGHTS,
        "entries": entries,
    }


def _timestamp(day: datetime.date) -> str:
    """An RFC 3339 time for a front matter date: midnight UTC, the same in every build."""
    return f"{day.isoformat()}T00:00:00Z"


# --------------------------------------------------------------------------- reading


def _read(path: Path, blog_dir: Path, where: str) -> tuple[Post | None, list[str]]:
    """Check one post file and render it; return the post (or None) and its problems."""
    errors: list[str] = []
    name = _NAME.fullmatch(path.name)
    file_date = _date(name[1]) if name else None
    slug = name[2] if name else ""
    if not name or file_date is None:
        errors.append(f"{where}: the file name must be <yyyy-mm-dd>-<slug>.md")
    elif not _SLUG.fullmatch(slug):
        errors.append(
            f"{where}: slug {slug!r} breaks the page path rule (site/CONTRACT.md section 2): "
            "use lowercase letters, digits and hyphens, starting with a letter or digit"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [*errors, f"{where}: {getattr(exc, 'strerror', None) or exc}"]
    meta, body, problems = _front_matter(text, where)
    errors += problems
    if meta is not None and file_date is not None and meta["date"] != file_date:
        errors.append(
            f"{where}: the file name's date {file_date} isn't the front matter's date "
            f"{meta['date']}; rename the file or fix the date"
        )
    if not body.strip():
        errors.append(f"{where}: the post has no text after its front matter")
    if errors or meta is None:
        return None, errors
    try:
        html, images = _render(body, slug=slug, blog_dir=blog_dir, base=None)
        feed_html, _ = _render(body, slug=slug, blog_dir=blog_dir, base=BASE_URL + f"/blog/{slug}/")
    except ValueError as exc:
        return None, [f"{where}: {exc}"]
    for found in _HEADING_ID.findall(html):
        if not found or found in TEMPLATE_IDS:
            errors.append(
                f"{where}: a heading's id {found!r} is empty or taken by the page itself; "
                "reword the heading"
            )
    if errors:
        return None, errors
    post = Post(
        slug=slug,
        source=where,
        title=meta["title"],
        date=meta["date"],
        description=meta["description"],
        updated=meta["updated"],
        draft=meta["draft"],
        html=html,
        feed_html=feed_html,
        images=tuple(images),
    )
    return post, []


def _front_matter(text: str, where: str) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Split and check the front matter; return ``(meta or None, body, problems)``."""
    text = text.removeprefix("﻿").replace("\r\n", "\n")
    head, sep, body = text.partition("\n---\n")
    if not head.startswith("---\n") or not sep:
        return None, "", [f"{where}: must start with front matter between '---' lines"]
    try:
        meta = yaml.load(head.removeprefix("---\n"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        return None, body, [f"{where}: front matter: {' '.join(str(exc).split())}"]
    except ValueError as exc:  # PyYAML builds the date 2026-02-30 and fails
        return None, body, [f"{where}: front matter has an impossible date ({exc})"]
    if not isinstance(meta, dict):
        return None, body, [f"{where}: front matter must be 'field: value' lines"]
    errors: list[str] = []
    allowed = (*REQUIRED_FIELDS, *OPTIONAL_FIELDS)
    if missing := [key for key in REQUIRED_FIELDS if key not in meta]:
        errors.append(f"{where}: front matter is missing {', '.join(missing)}")
    if unknown := sorted(str(key) for key in meta if key not in allowed):
        errors.append(
            f"{where}: unknown front matter field(s) {', '.join(unknown)}; "
            f"allowed: {', '.join(allowed)}"
        )
    checked: dict[str, Any] = {}
    for key in ("title", "description"):
        value = meta.get(key)
        if key in meta and (not isinstance(value, str) or not value.strip()):
            errors.append(f"{where}: {key} must be non-empty text (quote it if it is a number)")
        elif key in meta:
            checked[key] = " ".join(value.split())
    for key in ("date", "updated"):
        if key in meta:
            checked[key] = _date(meta[key])
            if checked[key] is None:
                errors.append(f"{where}: {key} must be a date like 2026-10-01, not {meta[key]!r}")
    checked.setdefault("updated", None)
    if checked["updated"] and checked.get("date") and checked["updated"] < checked["date"]:
        errors.append(f"{where}: updated {checked['updated']} is before date {checked['date']}")
    draft = meta.get("draft", False)
    if not isinstance(draft, bool):
        errors.append(f"{where}: draft must be true or false, not {draft!r}")
    checked["draft"] = draft is True
    return (None if errors else checked), body, errors


def _date(value: object) -> datetime.date | None:
    # YAML reads 2026-10-01 as a date and 2026-10-01 10:00 as a datetime, a date subclass.
    if isinstance(value, datetime.datetime):
        return None
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            return None
    return None


class _UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML's safe loader, except that a key repeated in one mapping is an error."""


def _unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"field {key!r} is repeated", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


# ------------------------------------------------------------------------- rendering


def _render(
    body: str, *, slug: str, blog_dir: Path, base: str | None
) -> tuple[str, list[tuple[str, bytes]]]:
    """Render a post's Markdown. With ``base`` (the post's absolute URL), links and images
    are made absolute, for the feed. Returns the HTML and each image's ``(asset name, bytes)``,
    once per distinct image."""
    images: dict[str, tuple[str, bytes]] = {}  # URL -> (asset name, bytes)

    def image(src: str, alt: str) -> dict[str, str]:
        name, blob, (width, height) = _image(src, alt, blog_dir)
        asset = f"blog/{slug}.{name.rpartition('.')[2]}"
        url = f"/assets/{assets.hashed_name(asset, blob)}"
        images.setdefault(url, (asset, blob))
        return {
            "src": url if base is None else BASE_URL + url,
            "width": str(width),
            "height": str(height),
            "loading": "lazy",
        }

    link: Callable[[str], str] | None = None if base is None else (lambda href: urljoin(base, href))
    html = pages.render_markdown(body, refuse_h1=True, link=link, image=image)
    return html, list(images.values())


def _image(src: str, alt: str, blog_dir: Path) -> tuple[str, bytes, tuple[int, int]]:
    """Check one image of a post; return its file name, bytes and size in pixels."""
    if not alt.strip():
        raise ValueError(f"image {src!r} has no alt text; write ![what it shows]({src})")
    match = _IMAGE.fullmatch(src)
    if not match:
        kinds = " or ".join(f".{kind}" for kind in IMAGE_TYPES)
        raise ValueError(
            f"image {src!r}: an image sits beside its post and is named by its file name "
            f"alone, in lowercase, ending {kinds}"
        )
    name, kind = match[1], match[2]
    path = blog_dir / name
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"image {src!r}: {exc.strerror or exc} ({_shown(path)})") from exc
    try:
        size = _png_size(blob) if kind == "png" else _svg_size(blob)
    except ValueError as exc:
        raise ValueError(f"image {src!r}: {exc}") from exc
    return name, blob, size


def _png_size(blob: bytes) -> tuple[int, int]:
    if len(blob) < 24 or not blob.startswith(_PNG_SIGNATURE) or blob[12:16] != b"IHDR":
        raise ValueError("not a PNG file")
    width, height = struct.unpack(">II", blob[16:24])
    if not (width and height):
        raise ValueError(f"size {width} x {height}")
    return width, height


def _svg_size(blob: bytes) -> tuple[int, int]:
    from tff_site import build  # build imports this module, so not at the top

    return build.svg_size(blob)


def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)
