"""The usability test plan, docs/usability-test.md (Milestone 2 step 12).

Offline. It checks that the plan
keeps what step 12 asks for and the owner's site rulings of 2026-09-25, and
that the names it tells testers and the owner to look for still match the
site: view labels from config/site.toml, filter labels from the list
templates, and badge texts from the build. A renamed control fails here, so
the script is updated with the site.
"""

import os
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("TFF_REPO_ROOT", Path(__file__).resolve().parents[1]))
DOC = ROOT / "docs" / "usability-test.md"


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


def sections(text: str, level: str) -> dict[str, str]:
    """Heading text -> body, for headings of exactly ``level`` ("##" or "###")."""
    parts = re.split(rf"^{level} (.+)$", text, flags=re.M)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def slug(heading: str) -> str:
    """GitHub's heading anchor."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def site_views() -> dict[str, str]:
    cfg = tomllib.loads((ROOT / "config" / "site.toml").read_text(encoding="utf-8"))
    return {v["key"]: v["label"] for v in cfg["views"]}


def test_has_every_part_step_12_asks_for(doc: str) -> None:
    heads = list(sections(doc, "##"))
    for part in (
        "Rounds",
        "Participants",
        "Sessions",
        "Consent script",
        "Tasks",
        "Measures",
        "Notes template",
        "Findings",
        "Invitation",
        "Tasks by email",
        "Results",
    ):
        assert part in heads, part


def test_nine_tasks_in_order(doc: str) -> None:
    tasks = sections(sections(doc, "##")["Tasks"], "###")
    numbers = [int(h.split(".", 1)[0]) for h in tasks]
    assert numbers == list(range(1, 10))
    for head, body in tasks.items():
        if not head.startswith("9."):
            for part in ("**Say:**", "**Done:**", "**Watch for:**"):
                assert part in body, (head, part)


def test_owner_rulings_are_applied(doc: str) -> None:
    lowered = doc.lower()
    assert "text only" not in lowered
    assert "monospace only" not in lowered
    tasks = {
        h.split(".", 1)[0]: b for h, b in sections(sections(doc, "##")["Tasks"], "###").items()
    }
    views = site_views()
    assert "Spacing" in tasks["1"]
    assert "Monospaced" in tasks["1"]
    assert "Spacing" in tasks["2"]
    assert "Proportional" in tasks["2"]
    assert '"Hide fonts that come with"' in tasks["4"]
    assert views["desktop_chosen"] in tasks["5"]
    assert views["desktop_installed"] in tasks["5"]


def test_names_match_the_site(doc: str) -> None:
    views = site_views()
    for key in ("overall", "desktop_chosen", "desktop_installed", "coding"):
        assert views[key] in doc, key
    for label in re.findall(r"\*(Desktop: [^*]+|[A-Z][a-z]+ fonts)\*", doc):
        assert label in views.values(), label

    templates = ROOT / "site" / "templates"
    filters = (templates / "_filters.html.j2").read_text(encoding="utf-8")
    for text in (
        "Hide fonts that come with",
        "Hide limited accents",
        "Redistributable fonts only",
        "Spacing",
        "Proportional",
        "Monospaced",
        ">Filters<",
    ):
        assert text.strip("<>") in doc, text
        assert text in filters, text
    assert "How we rank" in doc
    assert "How we rank" in (templates / "base.html.j2").read_text(encoding="utf-8")

    build = (ROOT / "src" / "tff_site" / "build.py").read_text(encoding="utf-8")
    for badge in ("Limited accents", "Not redistributable", "Comes with", "Pulled in by"):
        assert badge in doc, badge
        assert f'"{badge}' in build, badge


def test_measures_and_participants(doc: str) -> None:
    parts = sections(doc, "##")
    measures = parts["Measures"]
    for word in ("*unaided*", "*helped*", "*failed*", "1, very hard", "7, very easy", "Quotes"):
        assert word in measures, word
    for level in ("Blocker", "Major", "Minor"):
        assert f"- **{level}:**" in measures, level
    assert "No recording without consent" in measures
    assert "no analytics" in measures

    people = parts["Participants"]
    assert "5 new people per round" in people
    assert "at least 2" in people
    assert "phone" in people
    assert "keyboard only" in people
    assert "screen reader" in people
    assert "direct message" in people
    assert "M4-D2" in people


def test_raw_notes_stay_out_of_git(doc: str) -> None:
    assert "research/" in doc
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "research/" in ignored


def test_links_resolve(doc: str) -> None:
    anchors = {slug(h) for h in re.findall(r"^#{1,6} (.+)$", doc, flags=re.M)}
    for target in re.findall(r"\]\(([^)\s]+)\)", doc):
        path, _, fragment = target.partition("#")
        if path.startswith(("http://", "https://")):
            continue
        if path:
            assert (DOC.parent / path).is_file(), target
        else:
            assert fragment in anchors, target
