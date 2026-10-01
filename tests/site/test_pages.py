"""The content pages (Milestone 2 step 7): ``tff_site.pages`` and the page templates.

- Markdown: raw HTML off, heading anchors, no image from another site.
- The methodology extract: the numbered sections and the parts the page shows, and a loud
  failure when one is missing, so ``/methodology/`` can't drift from the document.
- The content files: strict front matter and placeholders.
- The built site (conftest ``site_dir``): every page builds with its headings, landmarks and
  meta tags, anchors are stable and every internal link resolves, robots.txt and the sitemap
  follow M2-D8 (a).
- In a browser: the pages load only their own files and reflow at 320 px.
"""

import copy
import itertools
import re
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from tff_site import assets, build, data, pages

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = data.load(ROOT / "tests" / "fixtures" / "catalog-site.sample.json")
COMMIT = "0" * 40
SUFFIX = " \N{EN DASH} Truly Free Fonts"

# Page file -> (URL path, <h1>, the ids of its <h2> headings in order). Other pages, the
# details panel and people's bookmarks link to these, so they must never move.
PAGES = {
    "methodology/index.html": (
        "/methodology/",
        "How we rank",
        [
            "toc-h",
            "in-plain-words",
            "desktop-views",
            "reading-ranks",
            "tiers",
            "biases",
            "sources",
            "licenses",
            "this-run",
        ],
    ),
    "privacy/index.html": (
        "/privacy/",
        "Privacy",
        [
            "what-the-pages-do-and-dont-do",
            "check-for-yourself",
            "what-our-server-logs",
            "what-cloudflare-sees",
            "coming-next-your-font-list-stays-on-your-device",
        ],
    ),
    "about/index.html": (
        "/about/",
        "About",
        [
            "which-fonts-are-listed",
            "whats-next",
            "report-a-problem-or-get-in-touch",
            "open-data-and-code",
        ],
    ),
    "404.html": ("/404.html", "Page not found", []),
}
METHODOLOGY_H3 = ["ranks", "scores", "not-ranked", "evidence-states"]
ABOUT_H3 = ["1-free-for-any-use", "2-latin-script", "3-free-to-share",
            "4-no-itf-free-font-license"]  # fmt: skip


class Page(HTMLParser):
    """Tags with attributes, headings with their text, and the text of the whole page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.headings: list[tuple[int, str | None, str]] = []
        self.text: list[str] = []
        self._heading: list[Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if re.fullmatch(r"h[1-6]", tag):
            self._heading = [int(tag[1]), dict(attrs).get("id"), ""]

    def handle_endtag(self, tag: str) -> None:
        if self._heading and tag == f"h{self._heading[0]}":
            self.headings.append((self._heading[0], self._heading[1], self._heading[2].strip()))
            self._heading = None

    def handle_data(self, text: str) -> None:
        self.text.append(text)
        if self._heading:
            self._heading[2] += text

    @property
    def ids(self) -> list[str]:
        return [a["id"] for _, a in self.tags if a.get("id")]

    @property
    def plain(self) -> str:
        return " ".join(" ".join(self.text).split())

    def attrs(self, tag: str) -> list[dict[str, str | None]]:
        return [a for t, a in self.tags if t == tag]


def parse(html: str) -> Page:
    page = Page()
    page.feed(html)
    page.close()
    return page


def read(site_dir: Path, rel: str) -> str:
    return (site_dir / rel).read_text(encoding="utf-8")


def page_file(site_dir: Path, path: str) -> Path | None:
    """The built file a site-relative URL path serves, or None."""
    target = site_dir / path.lstrip("/")
    if path.endswith("/"):
        target /= "index.html"
    return target if target.is_file() else None


def methodology_text() -> str:
    return pages.METHODOLOGY_MD.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------- Markdown


def test_render_markdown_escapes_raw_html():
    html = pages.render_markdown(
        'Text <script>alert(1)</script> and <b style="color:red" onclick="x()">bold</b>\n\n'
        "<style>p{}</style>\n\n[link](javascript:alert(1))\n"
    )
    assert "<script" not in html
    assert "<style" not in html
    assert "<b " not in html
    assert "&lt;script&gt;" in html
    assert 'href="javascript' not in html


def test_render_markdown_anchors_headings_from_level_2():
    html = pages.render_markdown(
        "# Title\n\n## Check for yourself\n\n### What's next?\n\n#### Deep\n\n##### Deeper\n"
    )
    assert "<h1>Title</h1>" in html
    assert '<h2 id="check-for-yourself">Check for yourself</h2>' in html
    assert '<h3 id="whats-next">' in html
    assert '<h4 id="deep">' in html
    assert "<h5>Deeper</h5>" in html


def test_render_markdown_ids_are_unique_within_a_call():
    html = pages.render_markdown("## Notes\n\n## Notes\n")
    assert re.findall(r'id="([^"]+)"', html) == ["notes", "notes-1"]


def test_render_markdown_has_tables_and_strikethrough():
    html = pages.render_markdown("| a | b |\n|---|---|\n| 1 | ~~2~~ |\n")
    assert "<table>" in html
    assert "<s>2</s>" in html


@pytest.mark.parametrize(
    "src", ["https://example.com/x.png", "http://example.com/x.png", "//example.com/x.png",
            "data:image/png;base64,AAAA"],
)  # fmt: skip
def test_render_markdown_refuses_images_from_other_sites(src):
    with pytest.raises(ValueError, match="own files"):
        pages.render_markdown(f"![alt]({src})\n")


def test_render_markdown_keeps_local_images():
    assert '<img src="/a.png" alt="alt"' in pages.render_markdown("![alt](/a.png)\n")


# -------------------------------------------------------------- the methodology extract


def test_extract_sections_from_the_methodology():
    sections = pages.extract_sections(methodology_text())
    assert list(sections) == list(pages.METHODOLOGY_SECTIONS) == [1, 5, 6, 11]
    for number, text in sections.items():
        first, *rest = text.splitlines()
        assert first.startswith(f"## {number}. "), first
        assert not any(re.match(r"#{1,2} ", line) for line in rest), number
    assert "### Desktop: two views" in sections[5]
    assert "**Confidence.**" in sections[6]


SYNTHETIC = """\
# Title

## 1. One

Text one.

```md
## 2. Not a section, it is code
```

### 2. A subsection, still one

## Unnumbered

Not in any section.

## 2. Two
Text two.
# Level one ends it
Outside.
"""


def test_extract_sections_follows_the_heading_rules():
    sections = pages.extract_sections(SYNTHETIC, (1, 2))
    assert sections[1] == (
        "## 1. One\n\nText one.\n\n```md\n## 2. Not a section, it is code\n```\n\n"
        "### 2. A subsection, still one\n"
    )
    assert sections[2] == "## 2. Two\nText two.\n"


def test_extract_sections_names_the_missing_section():
    with pytest.raises(KeyError, match=r"'## 3\.'"):
        pages.extract_sections(SYNTHETIC, (1, 3, 4))


def test_extract_sections_refuses_a_repeated_number():
    with pytest.raises(ValueError, match="two sections"):
        pages.extract_sections("## 1. A\n\n## 1. B\n", (1,))


def test_methodology_parts_from_the_document():
    parts = pages.methodology_parts(methodology_text())
    assert list(parts) == ["plain_words", "desktop_views", "confidence", "biases"]
    assert parts["plain_words"].startswith("Each month we collect public counts")
    assert not any(line.startswith(">") for line in parts["plain_words"].splitlines())
    assert "**Most chosen**" in parts["desktop_views"]
    assert "**Most installed**" in parts["desktop_views"]
    assert not any(line.startswith("|") for line in parts["desktop_views"].splitlines())
    assert parts["confidence"].startswith("**Confidence.**")
    assert "Tier C" in parts["confidence"]
    assert "**Outages.**" not in parts["confidence"]
    assert parts["biases"].startswith("- **Developer skew.**")


BREAKS = {
    "no section 11": (r"^## 11\. .*$", "## Eleven", "'## 11.'"),
    "no section 5": (r"^## 5\. .*$", "## The rankings", "'## 5.'"),
    "renamed subsection": (r"^### Desktop: two views$", "### Desktop", "Desktop: two views"),
    "no confidence paragraph": (r"^\*\*Confidence\.\*\*", "Confidence:", "Confidence"),
    "empty section 11": (r"(^## 11\. .*$)[\s\S]*", r"\1\n", "§11 is empty"),
    "empty section 1": (r"(^## 1\. .*$)[\s\S]*?(?=^## 2\. )", r"\1\n\n", "§1 is empty"),
}


@pytest.mark.parametrize("name", BREAKS)
def test_methodology_fails_loudly_when_a_part_is_missing(name):
    pattern, replacement, message = BREAKS[name]
    broken, count = re.subn(pattern, replacement, methodology_text(), flags=re.MULTILINE)
    assert count, "the break no longer applies to the document"
    with pytest.raises(pages.PageError, match=re.escape(message)):
        pages.methodology_parts(broken)


def test_a_missing_methodology_fails_loudly(tmp_path):
    with pytest.raises(pages.PageError, match=r"gone\.md"):
        pages.methodology_context(SAMPLE, tmp_path / "gone.md")


def test_a_broken_methodology_fails_the_build(tmp_path, monkeypatch):
    broken = tmp_path / "ranking-methodology.md"
    broken.write_text(re.sub(r"^## 6\. ", "## Six. ", methodology_text(), flags=re.M))
    monkeypatch.setattr(pages, "METHODOLOGY_MD", broken)
    sample = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
    with pytest.raises(pages.PageError, match=r"ranking-methodology\.md: no '## 6\.' section"):
        build.build(sample, tmp_path / "out", commit=COMMIT, font_files=False)
    assert not (tmp_path / "out").exists()


def test_methodology_links_point_to_github(tmp_path):
    text = methodology_text().replace(
        "Absent is not unpopular.",
        "Absent is not unpopular ([why](../AUTHORITY.md#ranking), [step](milestone-1.md#step-3), "
        "[below](#11-known-biases-for-the-public-page), [site](https://example.org/)).",
    )
    source = tmp_path / "ranking-methodology.md"
    source.write_text(text, encoding="utf-8")
    html = pages.methodology_context(SAMPLE, source)["method"]["plain_words"]
    blob = f"{data.REPO_URL}/blob/main"
    assert f'href="{blob}/AUTHORITY.md#ranking"' in html
    assert f'href="{blob}/docs/milestone-1.md#step-3"' in html
    assert f'href="{pages.METHODOLOGY_URL}#11-known-biases-for-the-public-page"' in html
    assert 'href="https://example.org/"' in html
    source.write_text(text.replace("../AUTHORITY.md", "../../etc/passwd"), encoding="utf-8")
    with pytest.raises(pages.PageError, match="outside the repository"):
        pages.methodology_context(SAMPLE, source)


def test_methodology_context_comes_from_the_catalog():
    doc = copy.deepcopy(SAMPLE)
    for source in doc["sources"]:
        source["stale"] = source["id"] == "homebrew"
    method = pages.methodology_context(doc)["method"]
    assert method["run_date"] == doc["run"]["date"]
    assert method["method_version"] == doc["run"]["method_version"]
    assert [s["id"] for s in method["stale"]] == ["homebrew"]
    assert [s["key"] for s in method["surveys"]] == ["desktop", "project"]
    listed = [s["id"] for survey in method["surveys"] for s in survey["sources"]]
    assert sorted(listed) == sorted(s["id"] for s in doc["sources"])
    assert method["bands"] == [b["label"] for b in doc["bands"]]
    assert [v["key"] for v in method["views"]] == [
        "project",
        "desktop_chosen",
        "desktop_installed",
        "coding",
        "dev_apps",
        "rising",
    ]  # the catalog's order, less the retired Overall
    assert [t["tier"] for t in method["tiers"]] == ["A", "B", "C"]
    assert method["data_license"]["name"] == "CC BY-SA 4.0"
    assert all(s["measures"].endswith(".") for s in listed_sources(method))


def listed_sources(method: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for survey in method["surveys"] for s in survey["sources"]]


def test_every_source_gets_a_credit_or_the_build_fails():
    """A source outside the credited surveys would lose its #source-<id> anchor."""
    doc = copy.deepcopy(SAMPLE)
    doc["sources"][0]["survey"] = "print"
    with pytest.raises(pages.PageError, match="print"):
        pages.methodology_context(doc)


# Headings added to the document before a line of §1 or §11: (line, heading) pairs, and the id
# the build must refuse.
HEADING_CLASHES = {
    "the same heading in two parts": (
        [("**Principles:**", "### Notes"), ("- **The ruler.**", "### Notes")],
        "'notes'",
    ),
    "a template anchor": ([("- **The ruler.**", "### Sources")], "'sources'"),
    "a source anchor": ([("- **The ruler.**", "### Source: Homebrew")], "'source-homebrew'"),
}


@pytest.mark.parametrize("name", HEADING_CLASHES)
def test_methodology_headings_cannot_break_the_page_anchors(tmp_path, name):
    added, taken = HEADING_CLASHES[name]
    text = methodology_text()
    for line, heading in added:
        assert text.count(f"\n{line}") == 1, line
        text = text.replace(f"\n{line}", f"\n{heading}\n\n{line}")
    source = tmp_path / "ranking-methodology.md"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(pages.PageError, match=re.escape(taken)):
        pages.methodology_context(SAMPLE, source)


# ------------------------------------------------------------------------ content files


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "page.md"
    path.write_text(text, encoding="utf-8")
    return path


GOOD = (
    "---\ntitle: A page\ndescription: What it is.\n---\n\nText with [a link]({{ issues_url }}).\n"
)


def test_content_page_context(tmp_path):
    context = pages.content_context("/x/", write(tmp_path, GOOD))
    assert context["page"] == {
        "path": "/x/",
        "title": "A page" + SUFFIX,
        "description": "What it is.",
        "canonical": True,
    }
    assert context["heading"] == "A page"
    assert f'<a href="{data.REPO_URL}/issues/new/choose">a link</a>' in context["content"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("No front matter.\n", "front matter"),
        ("---\ntitle: A\n---\nText\n", "exactly"),
        ("---\ntitle: A\ndescription: B\ndate: 2026-09-25\n---\nText\n", "exactly"),
        ("---\ntitle: ''\ndescription: B\n---\nText\n", "non-empty"),
        ("---\ntitle: [A]\ndescription: B\n---\nText\n", "non-empty"),
        ("---\ntitle: A\ndescription: B\n---\n# Second h1\n", "start at '##'"),
        ("---\ntitle: A\ndescription: B\n---\n[x]({{ nowhere }})\n", "unknown placeholder"),
        ("---\ntitle: A\ndescription: B\n---\n![x](https://example.com/x.png)\n", "own files"),
        ("---\ntitle: A: B\n---\n", "front matter"),
        # A mistyped placeholder fails rather than showing braces or a dead link.
        ("---\ntitle: A\ndescription: B\n---\n[x]({{ issues-url }})\n", "unknown placeholder"),
        ("---\ntitle: A\ndescription: B\n---\n[x]({{Issues_url}})\n", "unknown placeholder"),
        ("---\ntitle: A\ndescription: B\n---\nSee {{ issues_url\n}}.\n", "stray '{{'"),
        ("---\ntitle: A\ndescription: B\n---\n{% if x %}Hi{% endif %}\n", "stray '{%'"),
        # A heading may not take the page's own ids, a source anchor, or no id at all.
        ("---\ntitle: A\ndescription: B\n---\n## Main\n", "'main'"),
        ("---\ntitle: A\ndescription: B\n---\n## Tips\n", "'tips'"),
        ("---\ntitle: A\ndescription: B\n---\n## Feedback\n", "'feedback'"),
        ("---\ntitle: A\ndescription: B\n---\n## Source: GitHub\n", "'source-github'"),
        ("---\ntitle: A\ndescription: B\n---\n## ?!\n", "''"),
    ],
)
def test_content_page_refuses_bad_files(tmp_path, text, message):
    with pytest.raises(pages.PageError, match=re.escape(message)):
        pages.content_context("/x/", write(tmp_path, text))


def test_content_page_needs_its_file(tmp_path):
    with pytest.raises(pages.PageError, match=r"missing\.md"):
        pages.content_context("/x/", tmp_path / "missing.md")


def test_content_links_match_the_footer():
    links = pages.content_links()
    feedback = build.site_context()["feedback"]
    assert links["issues_url"] == feedback["issues_url"]
    assert links["email"] == feedback["email"]
    assert links["mailto"] == feedback["mailto"]
    assert links["repo_url"] == build.site_context()["repo_url"]
    for name, url in links.items():
        assert url.startswith(("https://", "mailto:")) or name == "email", name


def test_every_content_link_names_a_file_in_the_repository():
    blob = f"{data.REPO_URL}/blob/main/"
    for name, url in pages.content_links().items():
        if url.startswith(blob):
            assert (ROOT / url.removeprefix(blob).split("#")[0]).is_file(), name


def test_page_contexts_cover_the_content_pages():
    contexts = pages.page_contexts(copy.deepcopy(SAMPLE))
    assert list(contexts) == ["/404.html", "/about/", "/methodology/", "/privacy/"]
    headings = {path: h1 for path, h1, _ in PAGES.values()}
    for path, context in contexts.items():
        page = context["page"]
        assert page["path"] == path
        assert page["canonical"] is (path != "/404.html")
        assert page["title"] == context["heading"] + SUFFIX
        assert context["heading"] == headings[path]
        assert 50 <= len(page["description"]) <= 160, path


# ---------------------------------------------------------------------- the built site


@pytest.fixture(scope="module")
def built(site_dir: Path) -> dict[str, Page]:
    return {rel: parse(read(site_dir, rel)) for rel in PAGES}


def test_every_page_builds(site_dir):
    for rel in (*PAGES, "index.html", "robots.txt", "sitemap.xml"):
        assert (site_dir / rel).is_file(), rel


@pytest.mark.parametrize("rel", PAGES)
def test_pages_follow_the_page_contract(site_dir, built, rel):
    path, h1, _ = PAGES[rel]
    html = read(site_dir, rel)
    page = built[rel]
    assert html.startswith('<!doctype html>\n<html lang="en">')
    assert [(level, text) for level, _, text in page.headings if level == 1] == [(1, h1)]
    tags = [t for t, _ in page.tags]
    for landmark in ("header", "nav", "main", "footer"):
        assert landmark in tags, landmark
    body = tags.index("body")
    assert page.tags[body + 1] == ("a", {"class": "skip-link", "href": "#main"})
    assert page.attrs("main") == [{"id": "main", "tabindex": "-1"}]
    title = re.search(r"<title>([^<]+)</title>", html)
    assert title
    assert title[1] == h1 + SUFFIX
    description = [a for a in page.attrs("meta") if a.get("name") == "description"]
    assert len(description) == 1
    assert description[0]["content"]
    canonical = [a["href"] for a in page.attrs("link") if a.get("rel") == "canonical"]
    assert canonical == ([] if rel == "404.html" else [build.BASE_URL + path])
    current = [a["href"] for a in page.attrs("a") if a.get("aria-current") == "page"]
    assert current == ([] if rel == "404.html" else [path])


@pytest.mark.parametrize("rel", PAGES)
def test_pages_have_no_script_and_pass_the_csp_rules(site_dir, built, rel):
    page = built[rel]
    assert page.attrs("script") == []
    for tag, attrs in page.tags:
        assert tag not in {"style", "form", "iframe", "object", "embed", "base"}, tag
        assert not [a for a in attrs if a == "style" or a.startswith("on")], (tag, attrs)
        for name in ("src", "srcset"):
            if name in attrs:
                url = attrs[name] or ""
                assert url.startswith("/"), (tag, url)
                assert not url.startswith("//"), (tag, url)
        if tag == "link" and attrs.get("rel") != "canonical":
            assert (attrs.get("href") or "").startswith("/"), attrs


@pytest.mark.parametrize("rel", PAGES)
def test_headings_are_present_and_in_order(built, rel):
    _, _, h2_ids = PAGES[rel]
    if rel == "privacy/index.html" and build.TIP_URL:
        h2_ids = [*h2_ids, "tips"]
    page = built[rel]
    assert [hid for level, hid, _ in page.headings if level == 2] == h2_ids
    levels = [level for level, _, _ in page.headings]
    for before, after in itertools.pairwise(levels):
        assert after <= before + 1, levels
    assert all(text for _, _, text in page.headings)


def test_subheadings_are_present(built):
    method = [hid for level, hid, _ in built["methodology/index.html"].headings if level == 3]
    assert method[: len(METHODOLOGY_H3)] == METHODOLOGY_H3
    assert method[len(METHODOLOGY_H3) :] == ["sources-desktop", "sources-project"]
    about = [hid for level, hid, _ in built["about/index.html"].headings if level == 3]
    assert about == ABOUT_H3


def test_every_source_has_its_stable_anchor(built, site_data):
    doc = data.load(site_data)
    page = built["methodology/index.html"]
    items = {a["id"]: a for a in page.attrs("li") if (a.get("id") or "").startswith("source-")}
    assert sorted(items) == sorted(f"source-{s['id']}" for s in doc["sources"])
    names = [text for level, _, text in page.headings if level == 4]
    assert names == [
        s["name"] for key in pages.SURVEYS for s in doc["sources"] if s["survey"] == key
    ]


@pytest.mark.parametrize("rel", PAGES)
def test_ids_are_unique_and_in_page_links_resolve(site_dir, built, rel):
    page = built[rel]
    counts = Counter(page.ids)
    assert [i for i, n in counts.items() if n > 1] == []
    for attrs in page.attrs("a"):
        href = attrs.get("href") or ""
        if href.startswith("#"):
            assert href[1:] in counts, href


def markdown_ids(rel: str) -> set[str]:
    """The ids of the headings in a page's Markdown text, as tff_site.pages renders it."""
    contexts = pages.page_contexts(copy.deepcopy(SAMPLE))
    context = contexts[PAGES[rel][0]]
    parts = [context["content"]] if "content" in context else []
    if "method" in context:
        parts = [context["method"][n] for n in pages.methodology_parts(methodology_text())]
    return {i for part in parts for i in parse(str(part)).ids}


def test_template_ids_match_the_templates(built):
    """pages.TEMPLATE_IDS, which page text may not take, is every id the templates write."""
    found = set()
    for rel, page in built.items():
        ids = set(page.ids) - markdown_ids(rel)
        found |= {i for i in ids if not i.startswith(pages.SOURCE_ANCHOR)}
    expected = set(pages.TEMPLATE_IDS)
    if not build.TIP_URL:
        expected -= {"tip", "tips"}  # checked by test_privacy_page_mentions_tips_only_with_...
    assert found == expected


def test_the_credits_link_reaches_the_readme_section():
    """/methodology/ sends other credits to the README's "Source credits" (#source-credits)."""
    url = pages.methodology_context(SAMPLE)["method"]["credits_url"]
    assert url == f"{data.REPO_URL}#source-credits"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^## Source credits$", readme, re.M)


def test_links_between_pages_resolve(site_dir, built):
    for rel, page in built.items():
        for attrs in page.attrs("a"):
            href = attrs.get("href") or ""
            parts = urlsplit(href)
            if parts.scheme in {"https", "mailto"}:
                continue
            assert not parts.scheme, (rel, href)
            if not parts.path:
                continue
            target = page_file(site_dir, parts.path)
            assert target, (rel, href)
            if parts.fragment:
                assert parts.fragment in parse(target.read_text(encoding="utf-8")).ids, href


def test_methodology_shows_the_document(built):
    """The four parts appear exactly as the document renders them: the page can't drift."""
    html_parts = pages.methodology_context(SAMPLE)["method"]
    text = built["methodology/index.html"].plain
    for name in ("plain_words", "desktop_views", "confidence", "biases"):
        rendered = parse(str(html_parts[name])).plain
        assert rendered in text, name


def test_methodology_shows_the_run_and_the_credits(site_dir, built, site_data):
    doc = data.load(site_data)
    html = read(site_dir, "methodology/index.html")
    text = built["methodology/index.html"].plain
    assert f"Data from {doc['run']['date']}. Method version {doc['run']['method_version']}." in text
    ranks = re.search(r'id="ranks">(.*?)</dl>', html, re.S)
    assert ranks
    ranks_text = parse(ranks[1]).plain
    for view in doc["views"]:
        # A retired view (Overall, 2026-09-30) is left out; one not available yet is listed
        # as "Not shown yet."
        retired = view["key"] in data.RETIRED_VIEWS
        assert (view["measures"] in text) != retired, view["key"]
        assert (view["label"] in ranks_text) != retired, view["key"]
        pending = f"{view['measures']} Not shown yet."
        assert (pending in ranks_text) == (not retired and not view["available"]), view["key"]
    assert "Overall" not in ranks_text
    for band in doc["bands"]:
        assert band["label"] in text
    for tier, meaning in doc["tiers"].items():
        assert f"Tier {tier} {meaning}" in text
    for label in (*data.UNRANKED_LABELS.values(), *data.STATE_LABELS.values()):
        assert label in text.lower()
    for source in doc["sources"]:
        credit = re.search(rf'<li class="source" id="source-{source["id"]}">(.*?)</li>', html, re.S)
        assert credit, source["id"]
        credit_text = parse(credit[1]).plain
        for field in ("name", "license", "data_date"):
            assert source[field] in credit_text, (source["id"], field)
        assert f'href="{source["url"]}"' in credit[1]
        assert ("Stale" in credit_text) == source["stale"]
        assert ("isn't shown" in credit_text) == (not source["publish_rank"])
    run = re.search(r'id="this-run">(.*?)</section>', html, re.S)
    assert run
    stale = [s["name"] for s in doc["sources"] if s["stale"]]
    run_text = parse(run[1]).plain
    for name in stale:
        assert name in run_text
    if not stale:
        assert "None" in run_text
    licenses = re.search(r'id="licenses">(.*?)</section>', html, re.S)
    assert licenses
    assert f'<a href="{doc["data_license"]["url"]}">CC BY-SA 4.0</a>' in licenses[1]
    assert f'href="{data.REPO_URL}/blob/main/LICENSE">MIT License</a>' in licenses[1]
    assert html.count(f'href="{pages.METHODOLOGY_URL}"') >= 2


def test_the_sample_has_a_stale_source():
    """The stale-source line is covered by the sample, not only by a synthetic copy."""
    assert any(s["stale"] for s in SAMPLE["sources"])
    assert not all(s["publish_rank"] for s in SAMPLE["sources"])


def test_privacy_page_links_the_server_settings(built):
    hrefs = {a.get("href") for a in built["privacy/index.html"].attrs("a")}
    assert f"{data.REPO_URL}/blob/main/ops/Caddyfile" in hrefs
    assert f"{data.REPO_URL}/blob/main/ops/logrotate-caddy" in hrefs
    text = built["privacy/index.html"].plain
    for phrase in ("Firefox", "Chrome", "Safari", "Network", "14 days", "no-transform"):
        assert phrase in text, phrase


def test_privacy_page_mentions_tips_only_with_a_tip_link(tmp_path, monkeypatch):
    sample = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
    monkeypatch.setattr(build, "TIP_URL", "https://buy.stripe.com/test")
    build.build(sample, tmp_path / "tip", commit=COMMIT, font_files=False)
    with_tip = parse(read(tmp_path / "tip", "privacy/index.html"))
    assert [hid for level, hid, _ in with_tip.headings if level == 2][-1] == "tips"
    assert "Stripe" in with_tip.plain
    assert (
        {"tip", "tips"}
        <= set(with_tip.ids)
        <= set(pages.TEMPLATE_IDS) | markdown_ids("privacy/index.html")
    )


def test_not_found_page_falls_back_to_the_build_context(tmp_path, monkeypatch):
    """Without a 404 context from pages, the template shows build.NOT_FOUND_PAGE's title."""
    contexts = pages.page_contexts
    monkeypatch.setattr(
        pages,
        "page_contexts",
        lambda doc: {k: v for k, v in contexts(doc).items() if k != "/404.html"},
    )
    sample = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
    build.build(sample, tmp_path / "out", commit=COMMIT, font_files=False)
    page = parse(read(tmp_path / "out", "404.html"))
    assert [text for level, _, text in page.headings if level == 1] == [
        build.NOT_FOUND_PAGE["title"]
    ]


def test_about_page_states_the_rules(built):
    text = built["about/index.html"].plain
    for phrase in (
        "personal and commercial",
        "Latin",
        "pass the font files on",
        "ITF Free Font License",
        "Report a problem with this font",
    ):
        assert phrase in text, phrase


def test_not_found_page_uses_absolute_urls_only(built):
    page = built["404.html"]
    for tag, attrs in page.tags:
        for name in ("href", "src"):
            url = attrs.get(name)
            if url is not None and tag != "a":
                assert url.startswith("/"), (tag, url)
            elif url is not None:
                assert url.startswith(("/", "#", "https://", "mailto:")), url
    assert {"/", "/methodology/", "/about/"} <= {a.get("href") for a in page.attrs("a")}


def test_robots_txt_allows_indexing(site_dir):
    assert read(site_dir, "robots.txt") == (
        "# Truly Free Fonts: every page may be crawled and indexed.\n"
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "Sitemap: https://trulyfreefonts.com/sitemap.xml\n"
    )


def test_sitemap_lists_the_canonical_pages(site_dir, site_data):
    blob = (site_dir / "sitemap.xml").read_bytes()
    assert blob.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<urlset ')
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(blob)
    entries = {
        url.findtext("s:loc", namespaces=ns): url.findtext("s:lastmod", namespaces=ns)
        for url in root.findall("s:url", ns)
    }
    base = build.BASE_URL
    run_date = data.load(site_data)["run"]["date"]
    # The blog's pages, once a post is published or with --drafts, as on the test site
    # (M2 step 7b; tests/site/test_blog.py checks them).
    blog_pages = {
        f"{base}/{p.parent.relative_to(site_dir).as_posix()}/": None
        for p in (site_dir / "blog").rglob("index.html")
    }
    assert entries == {
        f"{base}/": run_date,
        f"{base}/about/": None,
        f"{base}/methodology/": run_date,
        f"{base}/privacy/": None,
        **blog_pages,
    }


def test_pages_css_follows_the_parts_rule():
    source = (ROOT / "site" / "css" / "40-pages.css").read_text(encoding="utf-8")
    assert assets.lint_css_part("40-pages.css", source) == []
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    # Colours come only from the tokens (site/CONTRACT.md section 6).
    assert not re.search(
        r"#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|oklch|oklab|lab|lch)\(", code, re.I
    )
    for prop, value in re.findall(r"([a-z-]*colou?r)\s*:\s*([^;]+);", code):
        assert "var(--c-" in value, (prop, value)
    for value in re.findall(r"border[a-z-]*\s*:\s*([^;]*\b(?:solid|dashed|dotted)\b[^;]*);", code):
        assert "var(--c-" in value, value


# ------------------------------------------------------------------------- in a browser

PAGE_PATHS = ["/methodology/", "/privacy/", "/about/", "/404.html"]


def test_pages_load_only_their_own_files(guarded_context):
    guarded = guarded_context()
    page = guarded.new_page()
    for path in PAGE_PATHS:
        page.goto(path)
        page.wait_for_load_state("load")
        guarded.assert_clean(page)
    assert guarded.requests
    assert all(url.startswith(guarded.origin) for url in guarded.requests)


def test_pages_reflow_at_320_px_without_sideways_scrolling(guarded_context):
    guarded = guarded_context(viewport={"width": 320, "height": 640})
    page = guarded.new_page()
    for path in PAGE_PATHS:
        page.goto(path)
        wide = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert wide <= 0, path


def test_a_source_anchor_scrolls_its_credit_into_view(guarded_context):
    guarded = guarded_context(viewport={"width": 1024, "height": 700})
    page = guarded.new_page()
    source = next(s for s in SAMPLE["sources"] if s["survey"] == "project")
    page.goto(f"/methodology/#source-{source['id']}")
    credit = page.locator(f"#source-{source['id']}")
    assert credit.evaluate("el => el.matches(':target')")
    box = credit.bounding_box()
    assert box
    assert 0 <= box["y"] < 700
    assert page.evaluate("() => window.scrollY") > 0
