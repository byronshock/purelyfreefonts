"""The blog (Milestone 2 step 7b; M2-D12): ``tff_site.blog``, its templates and its checks.

**This module's site** (it overrides ``site_dir`` and ``site_url``, as test_privacy.py does):
``site_data`` built with ``--drafts`` from ``tests/fixtures/blog/``, whose one post,
``2026-09-26-sample-post.md``, is a synthetic draft with headings, links, a list, a quote,
code, a PNG and an SVG. It exists only here: the real ``site/content/blog/`` is never read.

- **Posts:** front matter (``title``, ``date``, ``description``; optional ``updated`` and
  ``draft``), file names and slugs, images and their alt text; every failure the checklist
  names, reported at once through ``BlogError`` and the command line.
- **Build output:** ``/blog/``, ``/blog/<slug>/``, ``/blog/feed.xml``,
  ``/assets/blog/<slug>.<h>.<ext>``, the sitemap, the nav link, no script; nothing at all
  until a post is published; drafts only with ``--drafts``; byte-identical builds whatever
  the files' times.
- **The feed:** an offline Atom check (``atom_problems``, RFC 4287's rules that a machine can
  check), itself tested against broken feeds.
- **Contract:** site/CONTRACT.md section 2's paths and section 3's template files and blog
  context match the build.
- **Browser** (Chromium and Firefox): step 6's axe run (test_a11y.py's helpers, light and dark,
  375 and 1280 px) and step 9's privacy sweep (test_privacy.py's ``Sweep``, with scripts on
  and off) on ``/blog/`` and the post, plus no script, the images load, and reflow at 320 px.
"""

import datetime
import functools
import hashlib
import itertools
import os
import re
import shutil
import struct
import subprocess
import threading
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import pytest
import yaml

from tff_site import assets, blog, budgets, build, pages, serve
from tff_site.cli import main

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "blog"
CONTRACT = (ROOT / "site" / "CONTRACT.md").read_text(encoding="utf-8")
TEMPLATES = ROOT / "site" / "templates"
DEPLOY_YML = ROOT / ".github" / "workflows" / "deploy.yml"
FAKE_COMMIT = "0" * 40
SLUG = "sample-post"
POST_PATH = f"/blog/{SLUG}/"
BLOG_PAGES = {"index": "/blog/", "post": POST_PATH}
SUFFIX = " \N{EN DASH} Truly Free Fonts"
HASHED = re.compile(r"^(?P<stem>.+)\.(?P<h>[0-9a-f]{10})\.(?P<ext>[a-z0-9]+)$")
ATOM = "{http://www.w3.org/2005/Atom}"
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
GOOD_POST = """\
---
title: A post
date: 2026-10-01
description: A post about fonts.
---

Some text.
"""


# ------------------------------------------------------------------------------ helpers


class Page(HTMLParser):
    """Tags with attributes, headings, and whether a <script> appears."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.headings: list[tuple[int, str]] = []
        self.text: list[str] = []
        self._heading: list[Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = [int(tag[1]), ""]

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        if self._heading and tag == f"h{self._heading[0]}":
            self.headings.append((self._heading[0], self._heading[1].strip()))
            self._heading = None

    def handle_data(self, text: str) -> None:
        self.text.append(text)
        if self._heading:
            self._heading[1] += text

    def attrs(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag]

    @property
    def ids(self) -> list[str]:
        return [a["id"] for _, a in self.tags if a.get("id")]

    @property
    def plain(self) -> str:
        return " ".join(" ".join(self.text).split())


def parse(html: str) -> Page:
    page = Page()
    page.feed(html)
    page.close()
    return page


def tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def page_file(site: Path, path: str) -> Path | None:
    target = site / path.lstrip("/")
    if path.endswith("/"):
        target /= "index.html"
    return target if target.is_file() else None


def run_build(data: Path, out: Path, blog_dir: Path, **kwargs: Any) -> build.BuildResult:
    kwargs.setdefault("commit", FAKE_COMMIT)
    kwargs.setdefault("font_files", False)
    return build.build(data, out, blog_dir=blog_dir, **kwargs)


def write_post(folder: Path, name: str, text: str = GOOD_POST) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def post_text(**fields: str | None) -> str:
    """A post's text: the good post's front matter with ``fields`` changed (None drops one)."""
    meta = {"title": "A post", "date": "2026-10-01", "description": "A post about fonts."}
    meta.update(fields)
    head = "".join(f"{k}: {v}\n" for k, v in meta.items() if v is not None)
    return f"---\n{head}---\n\nSome text.\n"


def png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + b"\x80" * (3 * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# --------------------------------------------------------------------------- the Atom check


def atom_problems(blob: bytes, *, self_url: str) -> list[str]:
    """The Atom feed rules (RFC 4287) that a machine can check, offline; empty when valid.

    Well-formed UTF-8 XML with a declaration; an ``atom:feed`` root with exactly one ``id``,
    ``title`` and ``updated``, an author, a ``self`` link to ``self_url`` and at most one
    ``alternate`` link per type; each entry with exactly one ``id`` (unique), ``title`` and
    ``updated``, an ``alternate`` link or content, ``published`` no later than ``updated``;
    RFC 3339 times, the feed's ``updated`` the latest entry's, entries newest first; text
    constructs of a known type; absolute IRIs; and ``type="html"`` content that is HTML with
    no script, style or relative URL.
    """
    problems: list[str] = []
    if not blob.startswith(b'<?xml version="1.0" encoding="utf-8"?>\n'):
        problems.append("no UTF-8 XML declaration first")
    try:
        blob.decode("utf-8")
        root = ET.fromstring(blob)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        return [*problems, f"not well-formed UTF-8 XML: {exc}"]
    if root.tag != f"{ATOM}feed":
        return [*problems, f"root is {root.tag}, not atom:feed"]

    def children(parent: ET.Element, name: str) -> list[ET.Element]:
        return parent.findall(f"{ATOM}{name}")

    def exactly_one(parent: ET.Element, name: str, where: str) -> ET.Element | None:
        found = children(parent, name)
        if len(found) != 1:
            problems.append(f"{where}: {len(found)} atom:{name}, want exactly 1")
        return found[0] if found else None

    def at_most_one(parent: ET.Element, names: tuple[str, ...], where: str) -> None:
        problems.extend(
            f"{where}: more than one atom:{name}"
            for name in names
            if len(children(parent, name)) > 1
        )

    def iri(value: str | None, where: str) -> None:
        parts = urlsplit(value or "")
        if not (parts.scheme == "https" and parts.netloc) and parts.scheme not in ("tag", "urn"):
            problems.append(f"{where}: {value!r} is not an absolute IRI")

    def text(element: ET.Element | None, where: str) -> None:
        if element is None:
            return
        kind = element.get("type", "text")
        if kind not in ("text", "html", "xhtml"):
            problems.append(f"{where}: unknown text type {kind!r}")
        if not "".join(element.itertext()).strip():
            problems.append(f"{where}: empty")
        if kind == "html":
            problems.extend(html_problems(element.text or "", where))

    def when(element: ET.Element | None, where: str) -> datetime.datetime | None:
        if element is None:
            return None
        value = (element.text or "").strip()
        if not RFC3339.fullmatch(value):
            problems.append(f"{where}: {value!r} is not an RFC 3339 time")
            return None
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))

    def links(parent: ET.Element, where: str) -> list[ET.Element]:
        found = children(parent, "link")
        seen: set[tuple[str, str]] = set()
        for link in found:
            iri(link.get("href"), f"{where} link")
            rel = link.get("rel", "alternate")
            if rel == "alternate":
                key = (link.get("type", ""), link.get("hreflang", ""))
                if key in seen:
                    problems.append(f"{where}: two alternate links of type {key[0]!r}")
                seen.add(key)
        return found

    feed_id = exactly_one(root, "id", "feed")
    if feed_id is not None:
        iri(feed_id.text, "feed id")
    text(exactly_one(root, "title", "feed"), "feed title")
    feed_updated = when(exactly_one(root, "updated", "feed"), "feed updated")
    at_most_one(root, ("subtitle", "rights", "icon", "logo", "generator"), "feed")
    for name in ("subtitle", "rights"):
        for element in children(root, name):
            text(element, f"feed {name}")
    feed_links = links(root, "feed")
    selves = [link for link in feed_links if link.get("rel") == "self"]
    if [(s.get("href"), s.get("type")) for s in selves] != [(self_url, "application/atom+xml")]:
        problems.append(f"feed: want one self link to {self_url} (application/atom+xml)")
    if not [link for link in feed_links if link.get("rel", "alternate") == "alternate"]:
        problems.append("feed: no alternate link to the blog")
    authors = children(root, "author")
    for author in authors:
        exactly_one(author, "name", "feed author")
        for uri in children(author, "uri"):
            iri(uri.text, "feed author uri")

    entries = children(root, "entry")
    if not entries:
        problems.append("feed: no entries")
    ids: set[str] = set()
    latest: datetime.datetime | None = None
    published_order: list[datetime.datetime] = []
    for n, entry in enumerate(entries, 1):
        where = f"entry {n}"
        entry_id = exactly_one(entry, "id", where)
        if entry_id is not None:
            iri(entry_id.text, f"{where} id")
            if entry_id.text in ids:
                problems.append(f"{where}: id {entry_id.text} repeated")
            ids.add(entry_id.text or "")
        text(exactly_one(entry, "title", where), f"{where} title")
        updated = when(exactly_one(entry, "updated", where), f"{where} updated")
        at_most_one(entry, ("published", "summary", "content", "rights"), where)
        published = [when(p, f"{where} published") for p in children(entry, "published")]
        if published and published[0] and updated and published[0] > updated:
            problems.append(f"{where}: published after updated")
        if published and published[0]:
            published_order.append(published[0])
        entry_links = links(entry, where)
        has_alternate = any(link.get("rel", "alternate") == "alternate" for link in entry_links)
        contents = children(entry, "content")
        if not has_alternate and not contents:
            problems.append(f"{where}: neither content nor an alternate link")
        for content in contents:
            if content.get("src") and not children(entry, "summary"):
                problems.append(f"{where}: content by reference needs a summary")
            text(content, f"{where} content")
        for summary in children(entry, "summary"):
            text(summary, f"{where} summary")
        if not authors and not children(entry, "author"):
            problems.append(f"{where}: no author, and the feed has none")
        if updated and (latest is None or updated > latest):
            latest = updated
    if feed_updated and latest and feed_updated != latest:
        problems.append(f"feed updated {feed_updated} is not the latest entry's {latest}")
    if published_order != sorted(published_order, reverse=True):
        problems.append("entries are not newest first")
    return problems


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def html_problems(html: str, where: str) -> list[str]:
    """Problems in a feed's HTML: scripts, styles, handlers, and relative URLs (feed readers
    show the text away from the site)."""
    parser = _Html()
    parser.feed(html)
    parser.close()
    problems = []
    for tag, attrs in parser.tags:
        if tag in ("script", "style", "iframe", "object", "embed", "form"):
            problems.append(f"{where}: <{tag}>")
        for name, value in attrs.items():
            if name.startswith("on") or name == "style":
                problems.append(f"{where}: <{tag} {name}>")
            if name in ("href", "src"):
                parts = urlsplit(value or "")
                if parts.scheme not in ("https", "mailto"):
                    problems.append(f"{where}: <{tag} {name}={value!r}> is not absolute")
    return problems


# ------------------------------------------------------------------ this module's site


@pytest.fixture(scope="module")
def site_dir(site_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """This module's site (overrides the session fixture): the sample post, with --drafts."""
    out = tmp_path_factory.mktemp("blog-site")
    run_build(site_data, out, FIXTURES, drafts=True)
    return out


@pytest.fixture(scope="module")
def site_url(site_dir: Path) -> Iterator[str]:
    """This module's site, served by ``tff-site serve`` with site.caddy's headers."""
    server = serve.make_server(site_dir, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, name="blog-site", daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def built(site_dir: Path) -> dict[str, Page]:
    return {
        name: parse((site_dir / path.lstrip("/") / "index.html").read_text(encoding="utf-8"))
        for name, path in BLOG_PAGES.items()
    }


# -------------------------------------------------------------------------------- posts


def test_the_sample_post_is_a_draft():
    assert blog.load(FIXTURES) == []
    (post,) = blog.load(FIXTURES, drafts=True)
    assert post.slug == SLUG
    assert post.path == POST_PATH
    assert post.draft is True
    assert post.date == datetime.date(2026, 9, 26)
    assert post.updated is None
    assert post.title == "Sample post: what a post can hold"
    assert post.source == "tests/fixtures/blog/2026-09-26-sample-post.md"


def test_a_missing_folder_means_no_blog(tmp_path):
    assert blog.load(tmp_path / "none") == []
    assert blog.nav([]) is None
    assert blog.page_contexts([]) == {}


def test_posts_are_newest_first_and_drafts_stay_out(tmp_path):
    folder = tmp_path / "blog"
    write_post(folder, "2026-10-01-first.md", post_text(title="First"))
    write_post(folder, "2026-11-02-second.md", post_text(title="Second", date="2026-11-02"))
    write_post(folder, "2026-11-02-also.md", post_text(title="Also", date="2026-11-02"))
    write_post(folder, "2026-12-01-draft.md", post_text(date="2026-12-01", draft="true"))
    write_post(folder, "2026-09-01-off.md", post_text(date="2026-09-01", draft="false"))
    assert [p.slug for p in blog.load(folder)] == ["also", "second", "first", "off"]
    assert [p.slug for p in blog.load(folder, drafts=True)] == [
        "draft",
        "also",
        "second",
        "first",
        "off",
    ]


def test_front_matter_is_read_strictly(tmp_path):
    folder = tmp_path / "blog"
    text = (
        '---\ntitle: "  A   post: with a colon "\ndate: "2026-10-01"\n'
        "description: >-\n  Folded\n  text.\nupdated: 2026-10-05\ndraft: false\n---\n\nBody.\n"
    )
    write_post(folder, "2026-10-01-a-post.md", "﻿" + text.replace("\n", "\r\n"))
    (post,) = blog.load(folder)
    assert (post.title, post.description) == ("A post: with a colon", "Folded text.")
    assert (post.date, post.updated, post.draft) == (
        datetime.date(2026, 10, 1),
        datetime.date(2026, 10, 5),
        False,
    )


BAD_POSTS = {
    "missing title": ("2026-10-01-a.md", post_text(title=None), "missing title"),
    "missing date": ("2026-10-01-a.md", post_text(date=None), "missing date"),
    "missing description": (
        "2026-10-01-a.md",
        post_text(description=None),
        "missing description",
    ),
    "unknown field": ("2026-10-01-a.md", post_text(author="Byron"), "unknown front matter"),
    "repeated field": (
        "2026-10-01-a.md",
        "---\ntitle: A\ntitle: B\ndate: 2026-10-01\ndescription: D.\n---\n\nText.\n",
        "repeated",
    ),
    "title not text": ("2026-10-01-a.md", post_text(title="No"), "title must be non-empty"),
    "empty description": ("2026-10-01-a.md", post_text(description='""'), "non-empty text"),
    "impossible date": ("2026-10-01-a.md", post_text(date="2026-02-30"), "impossible date"),
    "quoted impossible date": (
        "2026-10-01-a.md",
        post_text(date='"2026-02-30"'),
        "date must be a date",
    ),
    "date and time": (
        "2026-10-01-a.md",
        post_text(date="2026-10-01 10:00:00"),
        "date must be a date",
    ),
    "updated before date": (
        "2026-10-01-a.md",
        post_text(updated="2026-09-30"),
        "is before date",
    ),
    "draft not a boolean": ("2026-10-01-a.md", post_text(draft="maybe"), "draft must be"),
    "no front matter": ("2026-10-01-a.md", "Just text.\n", "must start with front matter"),
    "front matter not a mapping": (
        "2026-10-01-a.md",
        "---\n- a list\n---\n\nText.\n",
        "'field: value'",
    ),
    "bad YAML": ("2026-10-01-a.md", "---\ntitle: [unclosed\n---\n\nText.\n", "front matter:"),
    "no text": ("2026-10-01-a.md", post_text().replace("Some text.\n", ""), "no text"),
    "no date in the name": ("a-post.md", GOOD_POST, "<yyyy-mm-dd>-<slug>.md"),
    "impossible date in the name": ("2026-13-01-a.md", GOOD_POST, "<yyyy-mm-dd>-<slug>.md"),
    "name and date differ": ("2026-10-02-a.md", GOOD_POST, "isn't the front matter's date"),
    "uppercase slug": ("2026-10-01-A-Post.md", GOOD_POST, "path rule"),
    "underscore in the slug": ("2026-10-01-a_post.md", GOOD_POST, "path rule"),
    "dot in the slug": ("2026-10-01-feed.xml.md", GOOD_POST, "path rule"),
    "space in the slug": ("2026-10-01-a post.md", GOOD_POST, "path rule"),
    "hyphen first": ("2026-10-01--a.md", GOOD_POST, "path rule"),
    "empty slug": ("2026-10-01-.md", GOOD_POST, "<yyyy-mm-dd>-<slug>.md"),
    "an h1 heading": ("2026-10-01-a.md", post_text() + "\n# Title\n", "'# ' heading"),
    "a heading taking a page id": ("2026-10-01-a.md", post_text() + "\n## Main\n", "'main'"),
    "an empty heading id": ("2026-10-01-a.md", post_text() + "\n## !!!\n", "''"),
    "an image from another site": (
        "2026-10-01-a.md",
        post_text() + "\n![A chart](https://example.com/x.png)\n",
        "only their own files",
    ),
}


@pytest.mark.parametrize("case", BAD_POSTS)
def test_a_bad_post_fails_the_build(tmp_path, case):
    name, text, message = BAD_POSTS[case]
    write_post(tmp_path / "blog", name, text)
    with pytest.raises(blog.BlogError) as caught:
        blog.load(tmp_path / "blog")
    assert any(message in line for line in caught.value.errors), caught.value.errors
    assert all(line.startswith(str(tmp_path / "blog" / name)) for line in caught.value.errors)


def test_a_repeated_slug_fails(tmp_path):
    folder = tmp_path / "blog"
    write_post(folder, "2026-10-01-same.md")
    write_post(folder, "2026-11-01-same.md", post_text(date="2026-11-01"))
    with pytest.raises(blog.BlogError, match="slugs are unique"):
        blog.load(folder)


def test_a_broken_draft_fails_even_without_drafts(tmp_path):
    folder = tmp_path / "blog"
    write_post(folder, "2026-10-01-ok.md")
    write_post(folder, "2026-10-02-draft.md", post_text(date="2026-10-02", draft="true", x="1"))
    with pytest.raises(blog.BlogError, match="unknown front matter"):
        blog.load(folder)


def test_every_problem_is_reported_at_once(tmp_path):
    folder = tmp_path / "blog"
    write_post(folder, "2026-10-01-one.md", post_text(title=None))
    write_post(folder, "2026-10-01-Two.md", post_text(author="x"))
    with pytest.raises(blog.BlogError) as caught:
        blog.load(folder)
    text = "\n".join(caught.value.errors)
    assert "missing title" in text
    assert "path rule" in text
    assert "unknown front matter" in text


def test_other_files_are_left_alone(tmp_path):
    folder = tmp_path / "blog"
    write_post(folder, "2026-10-01-ok.md")
    write_post(folder, ".2026-10-01-ok.md.swp", "junk")
    write_post(folder, "notes.txt", "junk")
    (folder / "sub.md").mkdir()
    with pytest.raises(blog.BlogError, match="not a regular file"):
        blog.load(folder)
    (folder / "sub.md").rmdir()
    assert [p.slug for p in blog.load(folder)] == ["ok"]


# ------------------------------------------------------------------------------- images


def image_post(folder: Path, markdown: str, **files: bytes) -> Path:
    for name, blob in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(blob)
    return write_post(folder, "2026-10-01-pics.md", post_text() + f"\n{markdown}\n")


def test_images_are_hashed_assets_with_their_size(tmp_path):
    folder = tmp_path / "blog"
    image = png(40, 30)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 16"><path d="M0 0h1v1z"/></svg>'
    image_post(
        folder,
        "![A grey box](box.png)\n\n![A line](./line.svg)\n\nAgain: ![A grey box](box.png)",
        **{"box.png": image, "line.svg": svg},
    )
    (post,) = blog.load(folder)
    png_url = f"/assets/{assets.hashed_name('blog/pics.png', image)}"
    svg_url = f"/assets/{assets.hashed_name('blog/pics.svg', svg)}"
    assert (
        f'<img src="{png_url}" alt="A grey box" width="40" height="30" loading="lazy" />'
        in post.html
    )
    assert f'<img src="{svg_url}" alt="A line" width="64" height="16"' in post.html
    assert post.html.count(png_url) == 2
    assert post.images == (("blog/pics.png", image), ("blog/pics.svg", svg))
    assert f'src="{blog.BASE_URL}{png_url}"' in post.feed_html
    out = tmp_path / "out"
    blog.write_images(out, [post])
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.*")) == sorted(
        [png_url.lstrip("/"), svg_url.lstrip("/")]
    )


BAD_IMAGES = {
    "no alt text": ("![](box.png)", {"box.png": png()}, "no alt text"),
    "blank alt text": ("![  ](box.png)", {"box.png": png()}, "no alt text"),
    "a missing file": ("![A box](gone.png)", {}, "No such file"),
    "a subfolder": ("![A box](pics/box.png)", {"pics/box.png": png()}, "beside its post"),
    "a site path": ("![A box](/favicon.svg)", {}, "beside its post"),
    "a parent folder": ("![A box](../box.png)", {}, "beside its post"),
    "an uppercase name": ("![A box](Box.png)", {"Box.png": png()}, "lowercase"),
    "a JPEG": ("![A box](box.jpg)", {"box.jpg": b"\xff\xd8\xff"}, ".png or .svg"),
    "a PNG that isn't one": ("![A box](box.png)", {"box.png": b"GIF89a" + bytes(30)}, "PNG"),
    "an SVG with a script": (
        "![A box](box.svg)",
        {"box.svg": b'<svg width="1" height="1"><script>x()</script></svg>'},
        "script",
    ),
    "an image from another site": ("![A box](//example.com/x.png)", {}, "own files"),
}


@pytest.mark.parametrize("case", BAD_IMAGES)
def test_a_bad_image_fails_the_build(tmp_path, case):
    markdown, files, message = BAD_IMAGES[case]
    image_post(tmp_path / "blog", markdown, **files)
    with pytest.raises(blog.BlogError, match=re.escape(message)):
        blog.load(tmp_path / "blog")


def test_render_markdown_hooks():
    seen = []

    def image(src: str, alt: str) -> dict[str, str]:
        seen.append((src, alt))
        return {"src": "/assets/x.png", "width": "2"}

    html = pages.render_markdown(
        "## Head\n\n![**Bold** alt](a.png) [link](/about/)\n",
        link=lambda href: "https://example.com" + href,
        image=image,
    )
    assert seen == [("a.png", "Bold alt")]
    assert '<img src="/assets/x.png" alt="Bold alt" width="2" />' in html
    assert '<a href="https://example.com/about/">' in html
    with pytest.raises(ValueError, match="'# ' heading"):
        pages.render_markdown("# Title\n", refuse_h1=True)
    assert "<h1>Title</h1>" in pages.render_markdown("# Title\n")


# ------------------------------------------------------------------------- build output


def test_until_a_post_is_published_there_is_no_blog(tmp_path, site_data):
    out = tmp_path / "out"
    run_build(site_data, out, FIXTURES)
    files = tree(out)
    assert not [name for name in files if name.startswith(("blog/", "assets/blog/"))]
    for name, blob in files.items():
        if name.endswith(".html"):
            html = blob.decode()
            assert 'href="/blog/' not in html, name
            assert "application/atom+xml" not in html, name
    assert "/blog/" not in files["sitemap.xml"].decode()


@pytest.mark.parametrize("drafts", [False, True], ids=["published", "with-drafts"])
def test_the_real_posts_build_as_marked_and_their_links_resolve(tmp_path, site_data, drafts):
    """Whatever ``site/content/blog/`` holds: a build without --drafts publishes no draft,
    and every link on a built blog page reaches a page that same build writes, so a
    published post can't link to a draft, nor to a heading id that isn't there."""
    posts = blog.load(blog.DEFAULT_DIR, drafts=True)
    out = tmp_path / "out"
    build.build(site_data, out, commit=FAKE_COMMIT, font_files=False, drafts=drafts)
    for post in posts:
        assert (page_file(out, post.path) is None) is (post.draft and not drafts), post.slug
    for path in (blog.BLOG_PATH, *(post.path for post in posts)):
        if target := page_file(out, path):
            page = parse(target.read_text(encoding="utf-8"))
            assert unresolved_links(out, path, page) == [], path


def test_the_draft_build_writes_the_blog(site_dir):
    files = tree(site_dir)
    blog_files = sorted(n for n in files if n.startswith(("blog/", "assets/blog/")))
    png_name = next(n for n in blog_files if n.endswith(".png"))
    svg_name = next(n for n in blog_files if n.endswith(".svg"))
    assert blog_files == sorted(
        ["blog/index.html", f"blog/{SLUG}/index.html", "blog/feed.xml", png_name, svg_name]
    )
    for name in (png_name, svg_name):
        match = HASHED.fullmatch(name.rsplit("/", 1)[1])
        assert match
        assert match["stem"] == SLUG
        assert match["h"] == hashlib.sha256(files[name]).hexdigest()[:10]
    assert files[png_name] == (FIXTURES / "sample-chart.png").read_bytes()
    assert files[svg_name] == (FIXTURES / "sample-diagram.svg").read_bytes()
    for name in files:
        assert build.SAFE_PATH.fullmatch(name), name


def test_every_page_links_the_blog_and_its_feed(site_dir):
    pages_built = sorted(site_dir.rglob("*.html"))
    assert len(pages_built) >= 7
    for path in pages_built:
        page = parse(path.read_text(encoding="utf-8"))
        nav = [a["href"] for a in page.attrs("a") if a.get("href") in {"/", "/blog/"}]
        assert "/blog/" in nav, path
        feeds = [
            a
            for a in page.attrs("link")
            if a.get("rel") == "alternate" and a.get("type") == "application/atom+xml"
        ]
        assert [a["href"] for a in feeds] == ["/blog/feed.xml"], path


def test_the_nav_puts_blog_after_how_we_rank(built):
    page = built["index"]
    hrefs = [a["href"] for a in page.attrs("a") if a.get("href", "").startswith("/")]
    start = hrefs.index("/methodology/")
    assert hrefs[start : start + 4] == ["/methodology/", "/blog/", "/about/", "/privacy/"]
    current = [a["href"] for a in page.attrs("a") if a.get("aria-current") == "page"]
    assert current == ["/blog/"]
    assert [a for a in built["post"].attrs("a") if a.get("aria-current")] == []


def test_the_sitemap_lists_the_blog_but_not_the_feed(site_dir):
    sitemap = (site_dir / "sitemap.xml").read_text(encoding="utf-8")
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert f"{build.BASE_URL}/blog/" in locs
    assert f"{build.BASE_URL}{POST_PATH}" in locs
    assert not [loc for loc in locs if loc.endswith(".xml")]
    assert locs == sorted(locs)


@pytest.mark.parametrize("name", BLOG_PAGES)
def test_blog_pages_follow_the_page_contract(site_dir, built, name):
    path = BLOG_PAGES[name]
    html = (site_dir / path.lstrip("/") / "index.html").read_text(encoding="utf-8")
    page = built[name]
    assert html.startswith('<!doctype html>\n<html lang="en">')
    h1 = [text for level, text in page.headings if level == 1]
    want = "Blog" if name == "index" else "Sample post: what a post can hold"
    assert h1 == [want]
    assert re.search(r"<title>([^<]+)</title>", html)[1] == want + SUFFIX
    tags = [t for t, _ in page.tags]
    for landmark in ("header", "nav", "main", "footer"):
        assert tags.count(landmark) == 1, landmark
    body = tags.index("body")
    assert page.tags[body + 1] == ("a", {"class": "skip-link", "href": "#main"})
    canonical = [a["href"] for a in page.attrs("link") if a.get("rel") == "canonical"]
    assert canonical == [build.BASE_URL + path]
    description = [a["content"] for a in page.attrs("meta") if a.get("name") == "description"]
    assert len(description) == 1
    assert description[0]
    levels = [level for level, _ in page.headings]
    assert all(b <= a + 1 for a, b in itertools.pairwise(levels)), levels


@pytest.mark.parametrize("name", BLOG_PAGES)
def test_blog_pages_load_no_script_and_pass_the_csp_lint(site_dir, built, name):
    path = BLOG_PAGES[name]
    html = (site_dir / path.lstrip("/") / "index.html").read_text(encoding="utf-8")
    assert built[name].attrs("script") == []
    assert "<script" not in html
    assert budgets.lint_html(path, html) == []


def test_the_post_page_shows_its_text_license_and_draft_mark(built):
    page = built["post"]
    text = page.plain
    assert "This post's text and images are under the CC BY-SA 4.0 license" in text
    hrefs = [a.get("href") for a in page.attrs("a")]
    assert blog.LICENSE["url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert blog.LICENSE["url"] in hrefs
    assert f"{pages.BLOB_URL}/LICENSE-DATA" in hrefs
    assert "Draft: not published yet" in text
    assert "<b>tags</b> show as text" in text  # raw HTML is off: the tags are text
    assert [a["datetime"] for a in page.attrs("time")] == ["2026-09-26"]
    images = page.attrs("img")[1:]  # the first is the site mark
    assert [(i["alt"], i["width"], i["height"]) for i in images] == [
        ("A bar chart with three bars of rising height", "240", "120"),
        ("A diagram: one box, an arrow, and a second box", "320", "96"),
        ("A bar chart with three bars of rising height", "240", "120"),
    ]
    assert all(i["src"].startswith(f"/assets/blog/{SLUG}.") for i in images)


def test_the_index_lists_the_post(built):
    page = built["index"]
    assert [(level, text) for level, text in page.headings] == [
        (1, "Blog"),
        (2, "Sample post: what a post can hold"),
    ]
    assert POST_PATH in [a.get("href") for a in page.attrs("a")]
    assert "Draft" in page.plain
    assert "A synthetic draft for the blog's tests" in page.plain


def unresolved_links(site: Path, path: str, page: Page) -> list[str]:
    """The links on the page at URL ``path`` that reach no file of ``site``, or no id on it.
    On the list page ``/`` the fragment is the view (site/CONTRACT.md section 9), not an id."""
    problems = []
    for attrs in page.attrs("a"):
        href = attrs.get("href") or ""
        parts = urlsplit(urljoin(path, href))
        if parts.scheme in ("https", "mailto"):
            continue
        target = None if parts.scheme else page_file(site, parts.path)
        if target is None:
            problems.append(f"{href}: no such page on this site")
            continue
        view = parts.path == "/"
        if parts.fragment and not view and parts.fragment not in page_ids(target):
            problems.append(f"{href}: no id {parts.fragment!r} on {parts.path}")
    return problems


def page_ids(target: Path) -> list[str]:
    return parse(target.read_text(encoding="utf-8")).ids


def test_links_in_blog_pages_resolve(site_dir, built):
    for name, page in built.items():
        ids = set(page.ids)
        assert len(ids) == len(page.ids), f"{name}: repeated ids"
        assert not (ids - blog.TEMPLATE_IDS - heading_ids(page)), name
        assert unresolved_links(site_dir, BLOG_PAGES[name], page) == [], name


def test_the_link_check_catches_broken_links(site_dir):
    page = parse(
        '<a href="/nowhere/">a</a> <a href="/about/#nowhere">b</a> <a href="#nowhere">c</a>'
        ' <a href="../missing/">d</a> <a href="javascript:x()">e</a>'
        ' <a href="/#rank=coding">fine</a> <a href="../sample-post/#images">fine</a>'
    )
    assert [p.split(":", 1)[0] for p in unresolved_links(site_dir, POST_PATH, page)] == [
        "/nowhere/",
        "/about/#nowhere",
        "#nowhere",
        "../missing/",
        "javascript",
    ]


def heading_ids(page: Page) -> set[str]:
    return {a["id"] for t, a in page.tags if re.fullmatch(r"h[1-6]", t) and a.get("id")}


def test_blog_template_ids_are_the_listed_ones(built):
    """blog.TEMPLATE_IDS, which a post's headings may not take, is every id the templates
    write on blog pages (the tip's only once the tip link is on)."""
    found = set()
    for page in built.values():
        found |= set(page.ids) - heading_ids(page)
    expected = set(blog.TEMPLATE_IDS) - (set() if build.TIP_URL else {"tip"})
    assert found == expected


def test_builds_are_byte_identical_whatever_the_file_times(tmp_path, site_data):
    folder = tmp_path / "blog"
    shutil.copytree(FIXTURES, folder)
    one, two = tmp_path / "one", tmp_path / "two"
    run_build(site_data, one, folder, drafts=True)
    for path in folder.iterdir():
        os.utime(path, (1_000_000_000, 1_000_000_000))
    run_build(site_data, two, folder, drafts=True)
    assert tree(one) == tree(two)


def test_the_command_line_takes_drafts(tmp_path, site_data, monkeypatch, capsys):
    monkeypatch.setattr(build, "build", functools.partial(build.build, blog_dir=FIXTURES))
    argv = ["build", "--data", str(site_data), "--commit", FAKE_COMMIT, "--no-font-files"]
    assert main([*argv, "--out", str(tmp_path / "plain")]) == 0
    assert not (tmp_path / "plain" / "blog").exists()
    assert main([*argv, "--out", str(tmp_path / "drafts"), "--drafts"]) == 0
    assert (tmp_path / "drafts" / "blog" / SLUG / "index.html").is_file()
    capsys.readouterr()
    assert main(["build", "--help"]) == 0
    assert "--drafts" in capsys.readouterr().out


SERVER_LOG = re.compile(r"\S+ - - \[[^\]]*\] ")  # http.server's log_message format


def test_a_bad_post_fails_the_command_without_a_traceback(tmp_path, site_data, monkeypatch, capsys):
    folder = tmp_path / "blog"
    write_post(folder, "2026-10-01-a.md", post_text(title=None, author="x"))
    monkeypatch.setattr(build, "build", functools.partial(build.build, blog_dir=folder))
    argv = ["build", "--data", str(site_data), "--out", str(tmp_path / "out")]
    assert main([*argv, "--commit", FAKE_COMMIT, "--no-font-files"]) == 1
    # A test server running in another thread logs to the same captured stderr
    # ("127.0.0.1 - - [date] Request timed out: ..."), so its lines are left out.
    lines = capsys.readouterr().err.splitlines(keepends=True)
    err = "".join(line for line in lines if not SERVER_LOG.match(line))
    assert err.startswith("tff-site build: failed\n")
    assert "missing title" in err
    assert "unknown front matter field(s) author" in err
    assert "Traceback" not in err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(("environment", "drafts"), [("staging", True), ("production", False)])
def test_only_the_staging_deploy_builds_drafts(tmp_path, environment, drafts):
    """deploy.yml's build step, run as the workflow runs it, with a stand-in ``uv``: only the
    test site gets --drafts. (tests/ops/test_receive.py pins the same for ops/deploy.sh.)"""
    workflow = yaml.safe_load(DEPLOY_YML.read_text(encoding="utf-8"))
    assert workflow["defaults"]["run"]["shell"] == "bash"
    (script,) = [
        step["run"]
        for step in workflow["jobs"]["build"]["steps"]
        if "tff-site build" in step.get("run", "")
    ]
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$UV_CALLS"\n', encoding="utf-8")
    fake.chmod(0o755)
    calls = tmp_path / "calls"
    env = {
        "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
        "ENVIRONMENT": environment,
        "UV_CALLS": str(calls),
        "TFF_SITE_DATA": "catalog-site.json",
        "TFF_SITE_DIR": "site",
        "SHA": FAKE_COMMIT,
    }
    subprocess.run(["bash", "-eo", "pipefail", "-c", script], env=env, check=True, timeout=30)
    (call,) = calls.read_text(encoding="utf-8").splitlines()
    want = f"run --locked tff-site build --data catalog-site.json --out site --commit {FAKE_COMMIT}"
    assert call == (f"{want} --drafts" if drafts else want)


# ------------------------------------------------------------------------------- the feed


def test_the_feed_passes_the_atom_check(site_dir):
    blob = (site_dir / "blog" / "feed.xml").read_bytes()
    assert atom_problems(blob, self_url=f"{build.BASE_URL}/blog/feed.xml") == []
    root = ET.fromstring(blob)
    (entry,) = root.findall(f"{ATOM}entry")
    assert entry.findtext(f"{ATOM}id") == f"{build.BASE_URL}{POST_PATH}"
    assert entry.findtext(f"{ATOM}published") == "2026-09-26T00:00:00Z"
    assert root.findtext(f"{ATOM}updated") == "2026-09-26T00:00:00Z"
    assert "CC BY-SA 4.0" in root.findtext(f"{ATOM}rights")
    content = entry.findtext(f"{ATOM}content")
    assert f'src="{build.BASE_URL}/assets/blog/{SLUG}.' in content
    assert f'href="{build.BASE_URL}/methodology/#tiers"' in content


def test_the_feed_of_several_posts_passes_the_atom_check(tmp_path, site_data):
    folder = tmp_path / "blog"
    write_post(folder, "2026-10-01-first.md", post_text(title="First & <best>"))
    write_post(
        folder,
        "2026-11-02-second.md",
        post_text(title="Second", date="2026-11-02", updated="2026-12-24")
        + "\n[Up](../first/) [Here](#x) [Mail](mailto:a@b.example)\n",
    )
    out = tmp_path / "out"
    run_build(site_data, out, folder)
    blob = (out / "blog" / "feed.xml").read_bytes()
    assert atom_problems(blob, self_url=f"{build.BASE_URL}/blog/feed.xml") == []
    root = ET.fromstring(blob)
    assert root.findtext(f"{ATOM}updated") == "2026-12-24T00:00:00Z"
    titles = [e.findtext(f"{ATOM}title") for e in root.findall(f"{ATOM}entry")]
    assert titles == ["Second", "First & <best>"]
    content = root.find(f"{ATOM}entry").findtext(f"{ATOM}content")
    assert f'href="{build.BASE_URL}/blog/first/"' in content
    assert f'href="{build.BASE_URL}/blog/second/#x"' in content
    index = parse((out / "blog" / "index.html").read_text(encoding="utf-8"))
    assert [a["datetime"] for a in index.attrs("time")] == [
        "2026-11-02",
        "2026-12-24",
        "2026-10-01",
    ]
    assert "Draft" not in index.plain


GOOD_FEED = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    "<id>https://example.com/blog/</id><title>T</title>"
    '<link rel="self" type="application/atom+xml" href="https://example.com/blog/feed.xml"/>'
    '<link rel="alternate" type="text/html" href="https://example.com/blog/"/>'
    "<updated>2026-10-02T00:00:00Z</updated><author><name>A</name></author>"
    "<entry><id>https://example.com/blog/b/</id><title>B</title>"
    '<link rel="alternate" type="text/html" href="https://example.com/blog/b/"/>'
    "<published>2026-10-02T00:00:00Z</published><updated>2026-10-02T00:00:00Z</updated>"
    '<content type="html">&lt;p&gt;&lt;a href="https://example.com/"&gt;x&lt;/a&gt;&lt;/p&gt;</content>'
    "</entry>"
    "<entry><id>https://example.com/blog/a/</id><title>A</title>"
    '<link rel="alternate" type="text/html" href="https://example.com/blog/a/"/>'
    "<published>2026-10-01T00:00:00Z</published><updated>2026-10-01T00:00:00Z</updated>"
    "</entry></feed>"
)
SELF = "https://example.com/blog/feed.xml"
BROKEN_FEEDS = {
    "no declaration": (lambda f: f.split("\n", 1)[1], "declaration"),
    "not XML": (lambda f: f.replace("</feed>", ""), "well-formed"),
    "not Atom": (lambda f: f.replace("2005/Atom", "2005/Other"), "not atom:feed"),
    "no feed id": (lambda f: f.replace("<id>https://example.com/blog/</id>", ""), "0 atom:id"),
    "relative id": (lambda f: f.replace("<id>https://example.com/blog/</id>", "<id>/b</id>"), "IRI"),
    "no self link": (lambda f: f.replace('rel="self"', 'rel="related"'), "self link"),
    "bad time": (lambda f: f.replace("2026-10-02T00:00:00Z</updated><author", "2026-10-02</updated><author"), "RFC 3339"),
    "stale feed time": (lambda f: f.replace("2026-10-02T00:00:00Z</updated><author", "2026-10-01T00:00:00Z</updated><author"), "latest entry"),
    "repeated entry id": (lambda f: f.replace("/blog/a/</id>", "/blog/b/</id>"), "repeated"),
    "entry without a title": (lambda f: f.replace("<title>A</title>", ""), "0 atom:title"),
    "published after updated": (lambda f: f.replace("<published>2026-10-01", "<published>2026-10-05"), "published after"),
    "oldest first": (lambda f: f.replace("<published>2026-10-02", "<published>2026-09-30"), "newest first"),
    "relative link in content": (lambda f: f.replace('href="https://example.com/"', 'href="/about/"'), "not absolute"),
    "script in content": (lambda f: f.replace("&lt;p&gt;&lt;a", "&lt;script&gt;&lt;/script&gt;&lt;p&gt;&lt;a"), "<script>"),
    "no author": (lambda f: f.replace("<author><name>A</name></author>", ""), "no author"),
    "unknown text type": (lambda f: f.replace('<content type="html">', '<content type="markdown">'), "text type"),
    "two alternates": (lambda f: f.replace("<updated>2026-10-02T00:00:00Z</updated><author>", '<link rel="alternate" type="text/html" href="https://example.com/x/"/><updated>2026-10-02T00:00:00Z</updated><author>'), "two alternate"),
}  # fmt: skip


def test_the_atom_check_passes_a_good_feed():
    assert atom_problems(GOOD_FEED.encode(), self_url=SELF) == []


@pytest.mark.parametrize("case", BROKEN_FEEDS)
def test_the_atom_check_catches(case):
    breaks, message = BROKEN_FEEDS[case]
    broken = breaks(GOOD_FEED)
    assert broken != GOOD_FEED, "the break changed nothing"
    problems = atom_problems(broken.encode(), self_url=SELF)
    assert any(message in p for p in problems), problems


# ----------------------------------------------------------------------------- contract


def contract_section(heading: str) -> str:
    start = CONTRACT.index(f"\n## {heading}")
    end = CONTRACT.find("\n## ", start + 1)
    return CONTRACT[start : end if end != -1 else None]


def table_rows(text: str, marker: str) -> list[list[str]]:
    """Cells of the first Markdown table after the line starting ``marker``, header dropped."""
    lines = text[text.index(marker) :].splitlines()
    rows = []
    for line in lines:
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if not set(cells[0]) <= {"-", "|"}:
            rows.append(cells)
    return rows[1:]


def path_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Section 2's table: each path, as a pattern (``<h>`` 10 hex digits, ``<id>`` and
    ``<slug>`` one path segment, ``<ext>`` an extension)."""
    holes = {
        "<h>": "[0-9a-f]{10}",
        "<id>": "[a-z0-9][a-z0-9-]*",
        "<slug>": "[a-z0-9][a-z0-9-]*",
        "<ext>": "[a-z0-9]+",
    }
    found = []
    for row in table_rows(contract_section("2. Build output"), "| Path |"):
        for path in re.findall(r"`([^`]+)`", row[0]):
            pattern = re.escape(path)
            for hole, regex in holes.items():
                pattern = pattern.replace(re.escape(hole), regex)
            found.append((path, re.compile(pattern)))
    return found


def test_section_2_names_every_file_of_a_blog_build(site_dir):
    patterns = path_patterns()
    for name in tree(site_dir):
        assert any(p.fullmatch(name) for _, p in patterns), f"{name} isn't in section 2"
    blog_rows = [(path, p) for path, p in patterns if "blog" in path]
    assert [path for path, _ in blog_rows] == [
        "blog/index.html",
        "blog/<slug>/index.html",
        "blog/feed.xml",
        "assets/blog/<slug>.<h>.<ext>",
    ]
    for path, pattern in blog_rows:
        assert any(pattern.fullmatch(name) for name in tree(site_dir)), path


def test_section_3_names_every_template():
    files_line = next(
        line
        for line in contract_section("3. Templates").splitlines()
        if line.startswith("**Files:**")
    )
    named = set(re.findall(r"`([^`]+\.j2)`", files_line))
    assert named == {p.name for p in TEMPLATES.glob("*.j2")}


def context_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in context_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in context_keys(v)}
    return set()


def test_section_3_blog_context_matches_the_build():
    posts = blog.load(FIXTURES, drafts=True)
    contexts = blog.page_contexts(posts)
    assert list(contexts) == ["/blog/", "/blog/feed.xml", POST_PATH]
    rows = {
        re.findall(r"`([^`]+)`", row[0])[0]: set(re.findall(r"`([a-z_]+)`", row[1]))
        for row in table_rows(contract_section("3. Templates"), "**Extra context for the blog")
    }
    assert set(rows) == {"/blog/", "/blog/<slug>/", "/blog/feed.xml"}
    documented = {
        "/blog/": rows["/blog/"],
        POST_PATH: rows["/blog/<slug>/"] | (rows["/blog/"] - {"heading", "posts"}),
        "/blog/feed.xml": rows["/blog/feed.xml"],
    }
    for path, context in contexts.items():
        keys = context_keys({k: v for k, v in context.items() if k != "page"})
        assert keys == documented[path], path
        page = context["page"]
        assert set(page) == {"path", "title", "description", "canonical"}
        assert page["path"] == path
        assert page["canonical"] is (path != blog.FEED_PATH)
    site_row = next(
        row
        for row in table_rows(contract_section("3. Templates"), "**Context every page gets")
        if row[0] == "`site`"
    )
    assert "`blog`" in site_row[1]
    assert set(build.site_context(blog_nav=blog.nav(posts))["blog"]) == {"url", "feed_url"}
    assert build.site_context()["blog"] is None
    assert blog.page_files(POST_PATH) == ("blog-post.html.j2", f"blog/{SLUG}/index.html")
    assert blog.page_files("/about/") is None


def test_license_data_covers_the_blog():
    text = (ROOT / "LICENSE-DATA").read_text(encoding="utf-8")
    covers = text.split("What this license covers", 1)[1].split("It does not cover", 1)[0]
    assert "site/content/blog/" in covers
    assert "text and images" in covers


# ------------------------------------------------------------------------------ browser


@pytest.mark.parametrize("viewport", ["375", "1280"])
@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("name", BLOG_PAGES)
def test_axe_blog_pages(guarded_context: Any, name: str, scheme: str, viewport: str) -> None:
    """Step 6's axe run (WCAG 2.2 AA tags, zero violations) on /blog/ and the post."""
    from tests.site.test_a11y import VIEWPORTS, assert_axe_clean, open_page

    path = BLOG_PAGES[name]
    guarded, page = open_page(
        guarded_context, path, color_scheme=scheme, viewport=VIEWPORTS[viewport]
    )
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    assert_axe_clean(page, f"{path} {scheme} {viewport}")
    guarded.assert_clean(page)


@pytest.mark.parametrize("scripts", [True, False], ids=["scripts", "no-scripts"])
def test_blog_pages_stay_on_this_site(guarded_context: Any, scripts: bool) -> None:
    """Step 9's privacy sweep on /blog/ and the post: no request elsewhere, no cookie, no
    storage, no CSP violation, the site's CSP on every response."""
    from tests.site.test_privacy import PHONE, WIDE, Sweep, expected_csp

    context = guarded_context(viewport=WIDE if scripts else PHONE, java_script_enabled=scripts)
    sweep = Sweep(context, csp=expected_csp(), scripts=scripts)
    sweep.run(list(BLOG_PAGES.values()))
    assert sweep.findings() == {}
    assert sweep.done["pages"] == len(BLOG_PAGES)
    requested = {urlsplit(r.url).path for r in sweep.requests}
    assert {p for p in requested if p.startswith("/assets/blog/")}, "no image was requested"
    assert not [p for p in requested if p.startswith("/assets/app.")], "a script was loaded"


def test_blog_images_load_and_no_script_runs(guarded_context: Any) -> None:
    guarded = guarded_context(viewport={"width": 1280, "height": 900})
    page = guarded.new_page()
    page.goto(POST_PATH)
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_load_state("networkidle")
    sizes = page.evaluate(
        "() => Array.from(document.querySelectorAll('.blog-post img'),"
        " (img) => [img.complete, img.naturalWidth > 0])"
    )
    assert sizes == [[True, True]] * 3
    assert page.evaluate("() => document.scripts.length") == 0
    assert page.evaluate("() => document.documentElement.hasAttribute('data-js')") is False
    guarded.assert_clean(page)


@pytest.mark.parametrize("name", BLOG_PAGES)
def test_blog_pages_reflow_at_320(guarded_context: Any, name: str) -> None:
    from tests.site.test_a11y import OVERFLOW_JS, open_page

    guarded, page = open_page(
        guarded_context, BLOG_PAGES[name], viewport={"width": 320, "height": 700}
    )
    found = page.evaluate(OVERFLOW_JS)
    assert found["scrollWidth"] <= 320, found
    assert found["out"] == [], found
    guarded.assert_clean(page)
