"""Stage "links" (milestone-1 step 14): one test group per policy rule, the gate K
overrides, the link check through the ``link_checks`` pseudo-source, and the stage.

Every record here is synthetic. The few real hosts belong to companies and font
sites, so the rules read naturally. The one network test checks the committed
overrides.
"""

import itertools
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import httpx
import pytest
from tests.helpers import ROOT

from tff_catalog import export, jsonio, links, records, reviews, stageio
from tff_catalog.config_model import Config, ConfigError
from tff_catalog.fetch import Fetcher
from tff_catalog.links import Link, Links, NoAcceptedLink, Target
from tff_catalog.membership import Membership, MemberState
from tff_catalog.paths import Paths
from tff_catalog.records import SourceKey, UniverseRecord
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State
from tff_catalog.store import Store
from tff_catalog.universe import Family, Universe

DAY = date(2026, 10, 3)
NS = {
    "google_metadata": "gf-family",
    "google_repo": "gf-dir",
    "homebrew_casks": "brew-cask",
    "fontsource": "fs-id",
    "fontist": "fontist-formula",
    "nerdfonts": "nerd-folder",
    "foundries": "foundry-family",
}


def rec(
    source: str,
    family: str,
    *urls: tuple[str, str],
    status: str = "live",
    key: str | None = None,
) -> UniverseRecord:
    return UniverseRecord(
        source=source,
        key=SourceKey(NS[source], key or family),
        family=family,
        status=status,  # type: ignore[arg-type]
        urls=tuple(urls),
    )


def fam(fid: str, name: str, recs: Iterable[UniverseRecord] = ()) -> Family:
    recs = list(recs)
    return Family(
        id=fid,
        family=name,
        keys=tuple(sorted({r.key for r in recs})),
        sources=tuple(sorted({r.source for r in recs})),
        first_seen=DAY,
        minted_from=name,
    )


def choose(
    *recs: UniverseRecord, fid: str = "f", overrides: dict[str, Links] | None = None
) -> Links:
    return links.choose(fam(fid, "F", recs), recs, overrides or {})


# --- rule: https only -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://designer.example/inter/",
        "https://github.com/owner/inter",
        "https://github.com/owner/inter/releases",
        "https://github.com/owner/inter/releases/tag/v4.1",
        "https://gitlab.com/group/project/-/releases",
        "https://fonts.google.com/specimen/Noto+Sans+JP",
        "https://adobe-fonts.github.io/source-sans/",
        "https://sourceforge.net/projects/dejavu/",
        "https://example.org/q.php?p=fonts/fontfamily/6",
        "https://apis.example.org/fonts/",
    ],
)
def test_policy_accepts_pages(url: str) -> None:
    assert links.policy_problems(url) == []


def test_policy_needs_https() -> None:
    assert links.policy_problems("http://designer.example/inter/") == ["not https"]
    assert links.policy_problems("not a url") == ["not a URL"]
    assert links.policy_problems("ftp://example.org/fonts/") == ["not https"]


@pytest.mark.parametrize(
    "url",
    [
        "https://exa mple.org/",
        "https://example.org:99999/",
        "https://example.org/fonts/\x00",
        "https:///fonts/",
    ],
)
def test_policy_needs_a_well_formed_url(url: str) -> None:
    assert links.policy_problems(url) == ["not a URL"]
    assert links.resolve(url) == "not a web URL"


# --- rule: never a release asset --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/inter/releases/download/v4.1/Inter-4.1.zip",
        "https://github.com/owner/inter/archive/refs/tags/v4.1.tar.gz",
        "https://github.com/owner/inter/archive/refs/heads/master",
        "https://github.com/owner/inter/raw/master/docs/font-files/Inter.ttc",
        "https://github.com/owner/inter/files/15438431/Inter-4.1-GoogleFonts.zip",
        "https://raw.githubusercontent.com/owner/inter/master/LICENSE.txt",
        "https://objects.githubusercontent.com/github-production-release-asset/1",
        "https://codeload.github.com/owner/inter/zip/refs/heads/master",
        "https://fonts.gstatic.com/s/inter/v13/abc.woff2",
        "https://download.jetbrains.com/fonts/JetBrainsMono-2.304.zip",
        "https://example.org/fonts/Example-Regular.TTF",
        "https://gitlab.com/group/project/-/archive/v1/project-v1.zip",
        "https://gitlab.com/group/project/-/releases/v1/downloads/fonts.tar.xz",
        "https://sourceforge.net/projects/example/files/latest/download",
        "https://sourceforge.net/projects/example/files/example/2.37/example-2.37.tar.bz2/download",
        "https://downloads.sourceforge.net/project/example/example-2.37",
        "https://drive.google.com/uc?export=download&id=abc123",
        "https://api.example.com/v2/fonts/download/example",
        "https://example.org/q.php?o=download&name=Example-Regular.ttf",
    ],
)
def test_policy_rejects_release_assets(url: str) -> None:
    assert "a release asset or file download" in links.policy_problems(url)


# --- rule: never /releases/latest -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/IBM/plex/releases/latest",
        "https://github.com/IBM/plex/releases/latest/",
        "https://github.com/IBM/plex/releases/latest/download/plex.zip",
        "https://gitlab.com/group/project/-/releases/permalink/latest",
    ],
)
def test_policy_rejects_releases_latest(url: str) -> None:
    assert "a /releases/latest link" in links.policy_problems(url)


# --- rule: never googlefontdirectory-hg -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/googlefonts/googlefontdirectory-hg",
        "https://github.com/googlefonts/googlefontdirectory-hg/tree/main/ofl/abel",
    ],
)
def test_policy_rejects_the_hg_mirror(url: str) -> None:
    assert "the googlefontdirectory-hg mirror" in links.policy_problems(url)


# --- rule: never an aggregator ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "aggregator"),
    [
        ("https://www.dafont.com/some-font.font", "dafont.com"),
        ("https://fontsource.org/fonts/inter", "fontsource.org"),
        ("https://www.npmjs.com/package/@fontsource/inter", "npmjs.com"),
        ("https://formulae.brew.sh/cask/font-inter", "formulae.brew.sh"),
        ("https://aur.archlinux.org/packages/ttf-example", "aur.archlinux.org"),
        ("https://github.com/ryanoasis/nerd-fonts", "github.com/ryanoasis/nerd-fonts"),
        ("https://github.com/google/fonts/tree/main/ofl/inter", "github.com/google/fonts"),
        ("https://github.com/fontsource/font-files", "github.com/fontsource"),
        ("https://fontlot.com/1234/some-font/", "fontlot.com"),
        ("https://open-foundry.com/fonts/bagnard", "open-foundry.com"),  # link_rules (4)
        (
            "https://web.archive.org/web/20210314185159/https://designer.example/fonts/",
            "web.archive.org",
        ),
    ],
)
def test_policy_rejects_aggregators(url: str, aggregator: str) -> None:
    assert f"an aggregator ({aggregator})" in links.policy_problems(url)


# --- resolving a source's URL -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/inter",
        "https://www.github.com/owner/inter.git",
        "http://github.com/owner/inter/tree/master/docs",
        "https://github.com/owner/inter/releases/download/v4.1/Inter-4.1.zip",
        "https://github.com/owner/inter/releases/latest",
    ],
)
def test_forge_urls_resolve_to_the_releases_page(url: str) -> None:
    assert links.resolve(url) == Target(
        key="github.com/owner/inter",
        url="https://github.com/owner/inter/releases",
        kind="repository",
        root="https://github.com/owner/inter",
    )


def test_gitlab_and_codeberg_repositories() -> None:
    gitlab = links.resolve("https://gitlab.com/group/sub/project/-/tree/main/fonts")
    assert isinstance(gitlab, Target)
    assert gitlab.url == "https://gitlab.com/group/sub/project/-/releases"
    codeberg = links.resolve("https://codeberg.org/someone/somefont")
    assert isinstance(codeberg, Target)
    assert codeberg.url == "https://codeberg.org/someone/somefont/releases"


def test_pages_agree_up_to_scheme_www_and_trailing_slash() -> None:
    a = links.resolve("http://www.example.org/font/")
    b = links.resolve("https://example.org/font")
    assert isinstance(a, Target)
    assert isinstance(b, Target)
    assert a.key == b.key
    assert (a.url, a.kind) == ("https://www.example.org/font/", "homepage")


@pytest.mark.parametrize(
    ("url", "why"),
    [
        (
            "https://github.com/ryanoasis/nerd-fonts",
            "an aggregator (github.com/ryanoasis/nerd-fonts)",
        ),
        (
            "https://github.com/googlefonts/googlefontdirectory-hg",
            "the googlefontdirectory-hg mirror",
        ),
        ("https://fonts.google.com/specimen/Inter", "a Google Fonts page"),
        ("https://download.example.org/Example.zip", "a release asset or file download"),
        ("mailto:someone", "not a web URL"),
    ],
)
def test_resolve_rejects(url: str, why: str) -> None:
    assert links.resolve(url) == why


# --- rule: Google families link the specimen page ----------------------------------------------


def test_google_primary_is_the_specimen_page() -> None:
    got = choose(rec("google_metadata", "Noto Sans JP"))
    assert got == Links(
        Link("https://fonts.google.com/specimen/Noto+Sans+JP"), None, "google_specimen"
    )


def test_google_specimen_given_by_a_record_is_kept() -> None:
    url = "https://fonts.google.com/specimen/Caacupe+One"
    got = choose(rec("google_metadata", "Caacupé One", ("specimen", url), key="Caacupe One"))
    assert got.primary.url == url


def test_google_designer_prefers_the_minisite() -> None:
    got = choose(
        rec("google_metadata", "Inter"),
        rec(
            "google_repo",
            "Inter",
            ("repository", "https://www.github.com/owner/inter"),
            ("minisite", "https://designer.example/inter"),
            key="ofl/inter",
        ),
    )
    assert got.designer == Link("https://designer.example/inter")


def test_google_designer_is_the_repository_root_without_a_minisite() -> None:
    got = choose(
        rec("google_metadata", "Abel"),
        rec("google_repo", "Abel", ("repository", "https://www.github.com/Owner/Abel.git")),
    )
    assert got.designer == Link("https://github.com/Owner/Abel")
    assert got.basis == "google_specimen"


def test_a_repo_folder_the_live_list_lacks_has_no_specimen_page() -> None:
    """design-m1 C12: only Google's live list makes a Google family."""
    home = ("homepage", "https://example.org/split/")
    got = choose(
        rec("google_repo", "Split Display", ("repository", "https://github.com/o/split")),
        rec("homebrew_casks", "Split Display", home, key="font-split-display"),
        rec("fontist", "Split Display", home),
    )
    assert got.basis == "two_sources"
    assert got.primary == Link("https://example.org/split/")


@pytest.mark.parametrize(
    "repository",
    [
        "https://github.com/googlefonts/googlefontdirectory-hg",
        "https://github.com/google/fonts",
        "https://fontsource.org/fonts/abel",
    ],
)
def test_google_designer_is_never_the_hg_mirror_or_an_aggregator(repository: str) -> None:
    got = choose(
        rec("google_metadata", "Abel"), rec("google_repo", "Abel", ("repository", repository))
    )
    assert got == Links(Link("https://fonts.google.com/specimen/Abel"), None, "google_specimen")


def test_a_family_no_longer_live_on_google_follows_the_other_rules() -> None:
    home = ("homepage", "https://example.org/font/")
    got = choose(
        rec("google_metadata", "Gone", status="delisted"),
        rec("homebrew_casks", "Gone", home, key="font-gone"),
        rec("fontist", "Gone", home),
    )
    assert got.basis == "two_sources"
    assert got.primary.url == "https://example.org/font/"
    with pytest.raises(NoAcceptedLink):
        choose(rec("google_repo", "Queued", status="queued"))


# --- rule: other families need two sources that agree --------------------------------------------


def test_two_sources_agreeing_on_a_homepage() -> None:
    got = choose(
        rec(
            "homebrew_casks",
            "Inter",
            ("homepage", "https://designer.example/inter/"),
            key="font-inter",
        ),
        rec("fontist", "Inter", ("homepage", "http://www.designer.example/inter")),
    )
    assert got == Links(Link("https://designer.example/inter/"), None, "two_sources")


def test_two_sources_agreeing_on_a_repository_give_its_releases_page() -> None:
    got = choose(
        rec("homebrew_casks", "Fira Code", ("homepage", "https://github.com/owner/FiraCode")),
        rec("fontsource", "Fira Code", ("repository", "https://github.com/owner/firacode.git")),
    )
    # GitHub names are case-blind; the spelling is the higher-ranked source's (SOURCE_ORDER).
    assert got.primary == Link("https://github.com/owner/firacode/releases")
    assert links.policy_problems(got.primary.url) == []


def test_a_release_asset_names_the_releases_page_never_itself() -> None:
    got = choose(
        rec(
            "homebrew_casks",
            "Example",
            ("homepage", "https://github.com/owner/example/releases/download/v1/Example.zip"),
        ),
        rec("fontist", "Example", ("homepage", "https://github.com/owner/example/releases/latest")),
    )
    assert got.primary == Link("https://github.com/owner/example/releases")


def test_one_source_is_not_enough() -> None:
    with pytest.raises(NoAcceptedLink) as caught:
        choose(
            rec("homebrew_casks", "Solo", ("homepage", "https://solo.example/"), key="font-solo"),
            rec(
                "homebrew_casks", "Solo", ("homepage", "https://solo.example"), key="font-solo-alt"
            ),
        )
    [candidate] = caught.value.candidates
    assert (candidate.url, candidate.sources) == ("https://solo.example/", ("homebrew_casks",))


def test_the_owner_approved_foundry_list_is_enough_on_its_own() -> None:
    """Owner ruling of 2026-09-26 (link_rules (1)): no second source is needed."""
    got = choose(rec("foundries", "Amdal", ("homepage", "https://gitlab.com/velvetyne/amdal")))
    assert got == Links(Link("https://gitlab.com/velvetyne/amdal/-/releases"), None, "foundry_list")


def test_the_foundry_list_beats_a_lone_other_source_and_skips_open_foundry() -> None:
    """Open Foundry is a showcase (link_rules (4)): the foundry list's repository wins over
    a fork only one other source names."""
    got = choose(
        rec(
            "foundries",
            "Bagnard",
            ("homepage", "https://open-foundry.com/fonts/bagnard"),
            ("repository", "https://github.com/sebsan/bagnard"),
        ),
        rec("fontsource", "Bagnard", ("repository", "https://github.com/dconstruct/Bagnard")),
    )
    assert got == Links(Link("https://github.com/sebsan/bagnard/releases"), None, "foundry_list")


def test_two_sources_still_beat_the_foundry_list_alone() -> None:
    got = choose(
        rec("foundries", "Chunk", ("homepage", "https://foundry.example/chunk")),
        rec("homebrew_casks", "Chunk", ("repository", "https://github.com/o/chunk")),
        rec("fontsource", "Chunk", ("repository", "https://github.com/o/chunk")),
    )
    assert got.primary == Link("https://github.com/o/chunk/releases")
    assert got.designer == Link("https://foundry.example/chunk")
    assert got.basis == "two_sources"


def test_two_sources_agreeing_on_an_aggregator_are_ignored() -> None:
    nerd = ("homepage", "https://github.com/ryanoasis/nerd-fonts")
    with pytest.raises(NoAcceptedLink) as caught:
        choose(rec("homebrew_casks", "X Nerd Font", nerd), rec("nerdfonts", "X Nerd Font", nerd))
    assert caught.value.candidates == ()
    assert {r.why for r in caught.value.rejected} == {
        "an aggregator (github.com/ryanoasis/nerd-fonts)"
    }


def test_designer_is_the_best_agreed_link_of_the_other_kind() -> None:
    home = ("homepage", "https://www.jetbrains.com/lp/mono/")
    repo = ("repository", "https://github.com/JetBrains/JetBrainsMono")
    got = choose(
        rec("homebrew_casks", "JetBrains Mono", home, repo),
        rec("fontist", "JetBrains Mono", home),
        rec("fontsource", "JetBrains Mono", repo),
    )
    # A tie on sources: the homepage is primary.
    assert got.primary == Link("https://www.jetbrains.com/lp/mono/")
    assert got.designer == Link("https://github.com/JetBrains/JetBrainsMono/releases")
    got = choose(
        rec("homebrew_casks", "JetBrains Mono", home, repo),
        rec("fontist", "JetBrains Mono", home),
        rec("fontsource", "JetBrains Mono", repo),
        rec("nerdfonts", "JetBrains Mono", repo),
    )
    # The repository has more sources now.
    assert got.primary == Link("https://github.com/JetBrains/JetBrainsMono/releases")
    assert got.designer == Link("https://www.jetbrains.com/lp/mono/")


def test_choice_ignores_record_order() -> None:
    recs = [
        rec("homebrew_casks", "A", ("homepage", "https://a.example/"), key="font-a"),
        rec("fontist", "A", ("homepage", "https://a.example")),
        rec("fontsource", "A", ("repository", "https://github.com/o/a")),
        rec("nerdfonts", "A", ("homepage", "https://github.com/O/A")),
        rec("foundries", "A", ("homepage", "https://www.a.example/")),
    ]
    first = choose(*recs)
    assert all(choose(*order) == first for order in itertools.permutations(recs))
    assert first.primary == Link("https://www.a.example/")  # the foundry list's spelling
    assert first.designer == Link("https://github.com/o/a/releases")


# --- overrides and gate K ------------------------------------------------------------------------


def test_committed_overrides_cover_the_gate_k_families() -> None:
    overrides = links.load_overrides(Paths.for_root(ROOT))
    by_family = {o.family: o for o in overrides}
    assert {"inter", "jetbrains-mono", "source-sans-3", "source-serif-4", "source-code-pro"} <= set(
        by_family
    )
    assert {"ibm-plex-sans", "ibm-plex-serif", "ibm-plex-mono", "ibm-plex-sans-condensed"} <= set(
        by_family
    )
    assert {o.question for o in overrides} == {
        "K-inter",
        "K-jetbrains-mono",
        "K-ibm-plex",
        "K-adobe-source",
        # Researched on 2026-09-26, each under a new question; approved on 2026-09-28.
        "K-metropolis-page",
        "K-profont-page",
        "K-terminus-page",
    }
    # The archived mirror says why it is the official link (owner ruling of 2026-09-28).
    assert "2020" in by_family["metropolis"].primary_note
    assert "archived mirror" in by_family["metropolis"].primary_label
    for o in overrides:
        assert o.choice == "a"
        assert o.primary, o.family
        if not o.question.endswith("-page"):  # the four approved ones name both links
            assert o.designer, o.family
        for url in filter(None, (o.primary, o.designer)):
            assert links.policy_problems(url) == [], (o.family, url)


def write_overrides(root: Path, *tables: str) -> Paths:
    """Write ``config/link-overrides.toml`` under ``root`` (no tables: none) and return its paths."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "link-overrides.toml").write_text("schema = 1\n\n" + "\n".join(tables))
    return Paths.for_root(root)


INTER = """[[override]]
family = "inter"
name = "Inter"
question = "K-inter"
primary = "https://designer.example/inter/"
designer = "https://github.com/owner/inter/releases"
reason = "The designer's release is fuller."
"""


def write_ruling(paths: Paths, day: str, question: str, choice: str) -> None:
    directory = paths.reviews / "links"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{day}.toml").write_text(
        f'[{question}]\nchoice = "{choice}"\nrecommended = true\nruling = "test"\nreason = "test"\n'
    )


def approved(paths: Paths) -> dict[str, Links]:
    return links.approved_overrides(links.load_overrides(paths), links.gate_choices(paths))


def test_an_override_waits_for_the_owner(tmp_path: Path) -> None:
    paths = write_overrides(tmp_path, INTER)
    inter = rec("google_metadata", "Inter")
    assert approved(paths) == {}
    assert choose(inter, fid="inter", overrides=approved(paths)).basis == "google_specimen"

    write_ruling(paths, "2026-10-01", "K-inter", "a")
    got = choose(inter, fid="inter", overrides=approved(paths))
    assert got == Links(
        Link("https://designer.example/inter/"),
        Link("https://github.com/owner/inter/releases"),
        "override",
    )

    write_ruling(paths, "2026-11-01", "K-inter", "b")  # a later ruling wins
    assert approved(paths) == {}


def test_an_override_note_goes_with_its_primary_link(tmp_path: Path) -> None:
    note = "The designer took the original down; this is an archived copy."
    paths = write_overrides(
        tmp_path,
        INTER.replace(
            'reason = "', f'primary_label = "Archive"\nprimary_note = "{note}"\nreason = "'
        ),
    )
    write_ruling(paths, "2026-10-01", "K-inter", "a")
    got = approved(paths)["inter"]
    assert got.primary == Link("https://designer.example/inter/", "Archive", note)
    assert got.designer == Link("https://github.com/owner/inter/releases")
    assert export.link(got.primary) == {
        "url": "https://designer.example/inter/",
        "label": "Archive",
        "note": note,
    }
    assert export.link(got.designer) == {"url": "https://github.com/owner/inter/releases"}


def test_an_override_applies_only_with_its_own_choice(tmp_path: Path) -> None:
    paths = write_overrides(
        tmp_path, INTER.replace('question = "K-inter"', 'question = "K-inter"\nchoice = "b"')
    )
    write_ruling(paths, "2026-10-01", "K-inter", "a")
    assert approved(paths) == {}
    write_ruling(paths, "2026-10-02", "K-inter", "b")
    assert set(approved(paths)) == {"inter"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            (
                'primary = "https://designer.example/inter/"',
                'primary = "https://github.com/owner/inter/releases/latest"',
            ),
            "releases/latest",
        ),
        (
            (
                'primary = "https://designer.example/inter/"',
                'primary = "https://github.com/owner/inter/releases/download/v4.1/Inter-4.1.zip"',
            ),
            "release asset",
        ),
        (
            (
                'primary = "https://designer.example/inter/"',
                'primary = "http://designer.example/inter/"',
            ),
            "not https",
        ),
        (
            (
                'designer = "https://github.com/owner/inter/releases"',
                'designer = "https://fontsource.org/fonts/inter"',
            ),
            "aggregator",
        ),
        (('question = "K-inter"', 'question = "K inter"'), "question id"),
        (('family = "inter"', 'family = "Inter"'), "family id"),
        (("reason = ", 'colour = "blue"\nreason = '), "unknown key"),
    ],
)
def test_overrides_are_loaded_strictly(
    tmp_path: Path, change: tuple[str, str], message: str
) -> None:
    paths = write_overrides(tmp_path, INTER.replace(*change))
    with pytest.raises(ConfigError, match=message):
        links.load_overrides(paths)


def test_a_family_is_overridden_once(tmp_path: Path) -> None:
    paths = write_overrides(tmp_path, INTER, INTER)
    with pytest.raises(ConfigError, match="listed twice"):
        links.load_overrides(paths)


def write_members(paths: Paths, *fids: str) -> None:
    member = MemberState(member=True, entered=DAY, runs_outside=0)
    stageio.dump_stage(
        paths, "membership", Membership(catalog=dict.fromkeys(fids, member), top100={})
    )


def write_queue(paths: Paths, **candidates: int) -> None:
    """A links queue: family id -> how many single-source candidates it has."""
    undecided = {
        fid: {
            "family": fid.title(),
            "candidates": [
                {
                    "kind": "homepage",
                    "sources": ["homebrew_casks"],
                    "url": f"https://{fid}{i}.example/",
                }
                for i in range(n)
            ],
            "rejected": [],
        }
        for fid, n in candidates.items()
    }
    jsonio.dump({"undecided": undecided}, paths.queues / links.QUEUE_FILE)


def test_questions_are_single_select_and_asked_only_for_catalog_families(tmp_path: Path) -> None:
    from tff_catalog import reviews

    paths = write_overrides(tmp_path, INTER)
    write_queue(paths, solo=5, bare=0, outside=2)
    # Before the catalog exists only the override questions are asked.
    assert [q.id for q in links.questions(paths)] == ["K-inter"]

    write_members(paths, "solo", "bare")
    asked = {q.id: q for q in reviews.check_questions("K", links.questions(paths), "links")}
    # "bare" has no candidate to pick from and "outside" is not in the catalog.
    assert set(asked) == {"K-inter", "K-solo"}
    for q in asked.values():
        assert q.recommended == 0
    assert "https://designer.example/inter/" in asked["K-inter"].text
    assert asked["K-solo"].options == (
        "https://solo0.example/ (from homebrew_casks)",
        "https://solo1.example/ (from homebrew_casks)",
        "https://solo2.example/ (from homebrew_casks)",
        links.RESEARCH_OPTION,
    )
    assert [q.id for q in reviews.questions(paths, "K")] == ["K-inter", "K-solo"]


def test_a_ruled_question_closes_but_stays_known(tmp_path: Path) -> None:
    from tff_catalog import reviews

    paths = write_overrides(tmp_path, INTER)
    write_queue(paths, solo=1)
    write_members(paths, "solo")
    write_ruling(paths, "2026-10-01", "K-inter", "a")
    # Still given to reviews, so a changed answer can be recorded against it ...
    assert [q.id for q in links.questions(paths)] == ["K-inter", "K-solo"]
    # ... but no longer asked.
    assert [q.id for q in reviews.questions(paths, "K")] == ["K-solo"]


def test_asking_for_research_keeps_the_question_open(tmp_path: Path) -> None:
    from tff_catalog import reviews

    paths = write_overrides(tmp_path)
    write_queue(paths, solo=2)
    write_members(paths, "solo")
    write_ruling(paths, "2026-10-01", "K-solo", "c")  # the research option
    assert [q.id for q in reviews.questions(paths, "K")] == ["K-solo"]
    write_ruling(paths, "2026-10-02", "K-solo", "b")  # a candidate
    assert reviews.questions(paths, "K") == []


def test_an_owner_answer_recorded_by_rulings_apply_approves_and_can_change(
    tmp_path: Path,
) -> None:
    from tff_catalog import reviews

    paths = write_overrides(tmp_path, INTER)
    answers = tmp_path / "answers.toml"
    for day, choice, want in (("2026-10-01", "a", {"inter"}), ("2026-10-02", "b", set())):
        answers.write_text(
            f'gate = "K"\nday = {day}\n\n[K-inter]\nchoice = "{choice}"\n'
            'reason = "The owner, in chat."\n'
        )
        applied = reviews.apply_answers(paths, answers)
        assert applied.warnings == ()  # a known question: ruling and recommended filled in
        assert set(approved(paths)) == want
    [(answer, _)] = reviews.latest_answers(paths, "K").values()
    assert (answer.recommended, answer.ruling) == (
        False,
        "Reject it: keep the link the policy picks",
    )


def test_rulings_are_read_strictly(tmp_path: Path) -> None:
    from tff_catalog.reviews import RulingError

    paths = write_overrides(tmp_path, INTER)
    directory = paths.reviews / "links"
    directory.mkdir(parents=True)
    (directory / "2026-10-01.toml").write_text('[K-inter]\nchoice = "a"\nrecommended = true\n')
    with pytest.raises(RulingError, match="reason"):
        links.gate_choices(paths)


def test_queue_question_ids_fit_the_rulings_schema() -> None:
    from tff_catalog.reviews import _ID

    assert links.queue_question("solo") == "K-solo"
    long_a, long_b = "a" * 70, "a" * 69 + "b"
    ids = {links.queue_question(long_a), links.queue_question(long_b)}
    assert len(ids) == 2
    for qid in ids:
        assert len(qid) <= links.QUESTION_MAX
        assert _ID.fullmatch(qid)
        assert qid.startswith("K-aaa")
    assert links.queue_question(long_a) == links.queue_question(long_a)


# --- the check and the link_checks pseudo-source ---------------------------------------------------


class Web:
    """A fake web: url -> (status, location); records every request."""

    def __init__(self, pages: dict[str, tuple[int, str | None]]) -> None:
        self.pages = pages
        self.requests: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        if url not in self.pages:
            raise httpx.ConnectError("unreachable", request=request)
        status, location = self.pages[url]
        if status == 405 and request.method == "GET":
            return httpx.Response(200, text="ok")
        headers = {"location": location} if location else {}
        return httpx.Response(status, headers=headers, text="page")

    def fetcher(self) -> Fetcher:
        hosts = {httpx.URL(u).host for u in self.pages} | {"unreachable.example"}
        return Fetcher(
            transport=httpx.MockTransport(self.handle),
            min_interval=dict.fromkeys(hosts, 0.0),
            retries=0,
        )


PAGES: dict[str, tuple[int, str | None]] = {
    "https://ok.example/": (200, None),
    "https://gone.example/": (404, None),
    "https://nohead.example/": (405, None),
    "https://moved.example/": (301, "https://github.com/o/r/releases/latest"),
    "https://github.com/o/r/releases/latest": (200, None),
}
LINKS = {
    "ok": Links(Link("https://ok.example/"), Link("https://nohead.example/"), "two_sources"),
    "gone": Links(Link("https://gone.example/"), None, "two_sources"),
    "moved": Links(Link("https://moved.example/"), None, "two_sources"),
    "down": Links(Link("https://unreachable.example/"), None, "two_sources"),
}


def context(
    paths: Paths,
    fetcher: Fetcher | None,
    *,
    options: RunOptions | None = None,
    state: State | None = None,
) -> StageContext:
    return StageContext(
        paths=paths,
        config=cast(Config, None),  # the stage reads no config
        state=state or State(),
        run_date=DAY,
        store=Store(paths.store) if paths.store is not None else None,
        fetcher=fetcher,
        log=logging.getLogger("test.links"),
        options=options or RunOptions(),
    )


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    (tmp_path / "store").mkdir()
    return Paths.for_root(tmp_path / "root", store=tmp_path / "store")


def verdicts(checks: dict[str, links.LinkCheck]) -> dict[str, tuple[bool, int]]:
    return {url: (c.ok, c.status) for url, c in checks.items()}


def test_check_answers_and_problems(paths: Paths) -> None:
    web = Web(PAGES)
    checks = links.check(LINKS, context(paths, web.fetcher()))
    assert verdicts(checks) == {
        "https://ok.example/": (True, 200),
        "https://nohead.example/": (True, 200),  # HEAD refused, GET answered
        "https://gone.example/": (False, 404),
        "https://moved.example/": (False, 200),  # redirects to /releases/latest
        "https://unreachable.example/": (False, 0),
    }
    assert checks["https://moved.example/"].problems == (
        "redirects to https://github.com/o/r/releases/latest, a /releases/latest link",
    )
    assert checks["https://unreachable.example/"].problems[0].startswith("no answer: FetchError")
    assert ("GET", "https://nohead.example/") in web.requests


def test_a_redirect_to_plain_http_fails(paths: Paths) -> None:
    web = Web({"https://downgrade.example/": (301, "http://downgrade.example/")})
    rows, fetched = links.check_urls(["https://downgrade.example/"], web.fetcher())
    check = links.to_check(rows["https://downgrade.example/"])
    assert (check.ok, check.status) == (False, 0)
    assert check.problems[0].startswith("no answer: HostNotAllowed")
    assert web.requests == [("HEAD", "https://downgrade.example/")]  # the http hop is never sent
    assert fetched == []


def test_a_url_the_client_refuses_is_a_failed_check_not_a_crash() -> None:
    too_long = "https://ok.example/" + "a" * 70_000
    web = Web(PAGES)
    rows, _ = links.check_urls([too_long, "https://ok.example/"], web.fetcher())
    assert rows[too_long].status == 0
    assert (rows[too_long].error or "").startswith("InvalidURL")
    assert rows["https://ok.example/"].status == 200


def test_checks_are_recorded_and_replayed_offline(paths: Paths) -> None:
    web = Web(PAGES)
    live = links.check(LINKS, context(paths, web.fetcher()))
    snap = Store(paths.require_store()).snapshot(links.CHECKS_SOURCE, DAY)
    assert snap is not None
    rows = list(snap.iter_jsonl(links.CHECKS_EXTRACT))
    assert [r["url"] for r in rows] == sorted(live)
    # Every URL a request reached is listed, redirects included; the unreachable one got none.
    assert {f.url for f in snap.manifest.fetched} == (
        set(live) - {"https://unreachable.example/"} | {"https://github.com/o/r/releases/latest"}
    )

    replayed = links.check(LINKS, context(paths, None, options=RunOptions(from_snapshots=DAY)))
    assert replayed == live

    extra = {**LINKS, "new": Links(Link("https://new.example/"), None, "two_sources")}
    replayed = links.check(extra, context(paths, None, options=RunOptions(from_snapshots=DAY)))
    assert replayed["https://new.example/"].problems == (
        "not checked: no answer recorded for this day",
    )


def test_a_second_run_on_the_day_reuses_the_200_answers(paths: Paths) -> None:
    web = Web(PAGES)
    first = links.check(LINKS, context(paths, web.fetcher()))
    web.requests.clear()
    assert links.check(LINKS, context(paths, web.fetcher())) == first
    # Only the links that did not answer 200 are asked again.
    assert {url for _, url in web.requests} == {
        "https://gone.example/",
        "https://unreachable.example/",
    }

    web.requests.clear()
    links.check(LINKS, context(paths, web.fetcher(), options=RunOptions(refetch=True)))
    assert {url for _, url in web.requests} >= set(first)


def test_a_fixed_link_passes_on_a_same_day_rerun(paths: Paths) -> None:
    gone = {"gone": LINKS["gone"]}
    links.check(gone, context(paths, Web(PAGES).fetcher()))
    fixed = Web({**PAGES, "https://gone.example/": (200, None)})
    assert links.check(gone, context(paths, fixed.fetcher()))["https://gone.example/"].ok
    # The day's record now holds the new answer, so a replay sees the fix too.
    replayed = links.check(gone, context(paths, None, options=RunOptions(from_snapshots=DAY)))
    assert replayed["https://gone.example/"].ok


def test_a_frozen_day_is_not_rewritten(paths: Paths, caplog: pytest.LogCaptureFixture) -> None:
    web = Web(PAGES)
    links.check({"ok": LINKS["ok"]}, context(paths, web.fetcher()))
    frozen = State(run_history=({"run_date": DAY.isoformat()},))
    got = links.check(LINKS, context(paths, web.fetcher(), state=frozen))
    assert got["https://gone.example/"].status == 404
    assert "could not record link checks" in caplog.text
    assert set(links.recorded_checks(Store(paths.require_store()), DAY)) == {
        "https://ok.example/",
        "https://nohead.example/",
    }


# --- the stage and links --check -------------------------------------------------------------------


def build_world(paths: Paths, *, members: Iterable[str] | None = None) -> None:
    """A universe of five families, their records, one approved override."""
    recs = [
        rec("google_metadata", "Inter"),
        rec("google_metadata", "Abel"),
        rec(
            "google_repo",
            "Abel",
            ("repository", "https://github.com/googlefonts/googlefontdirectory-hg"),
            key="ofl/abel",
        ),
        rec(
            "homebrew_casks", "Fira Code", ("homepage", "https://ok.example/"), key="font-fira-code"
        ),
        rec("fontist", "Fira Code", ("homepage", "https://ok.example")),
        rec("homebrew_casks", "Solo", ("homepage", "https://gone.example/"), key="font-solo"),
        rec("homebrew_casks", "Icons", ("homepage", "https://ok.example/"), key="font-icons"),
    ]
    by_family: dict[str, list[UniverseRecord]] = {}
    for r in recs:
        by_family.setdefault(r.family, []).append(r)
    families = {
        "inter": fam("inter", "Inter", by_family["Inter"]),
        "abel": fam("abel", "Abel", by_family["Abel"]),
        "fira-code": fam("fira-code", "Fira Code", by_family["Fira Code"]),
        "solo": fam("solo", "Solo", by_family["Solo"]),
        "icons": replace(fam("icons", "Icons", by_family["Icons"]), drop="icon"),
    }
    stageio.dump_stage(paths, "universe", Universe(families=families, unmapped=()))
    for source in {r.source for r in recs}:
        records.write_jsonl(
            [r for r in recs if r.source == source], paths.records / f"{source}.jsonl"
        )
    write_overrides(paths.root, INTER)
    write_ruling(paths, "2026-10-01", "K-inter", "a")
    if members is not None:
        member = MemberState(member=True, entered=DAY, runs_outside=0)
        stageio.dump_stage(
            paths, "membership", Membership(catalog=dict.fromkeys(members, member), top100={})
        )


WORLD_PAGES = {
    **PAGES,
    "https://designer.example/inter/": (200, None),
    "https://github.com/owner/inter/releases": (200, None),
    "https://fonts.google.com/specimen/Abel": (200, None),
}


def test_stage_writes_links_queue_and_checks(paths: Paths) -> None:
    build_world(paths)
    web = Web(WORLD_PAGES)
    links.run(context(paths, web.fetcher()))

    chosen = stageio.load_stage(paths, "links")
    assert chosen == {
        "abel": Links(Link("https://fonts.google.com/specimen/Abel"), None, "google_specimen"),
        "fira-code": Links(Link("https://ok.example/"), None, "two_sources"),
        "inter": Links(
            Link("https://designer.example/inter/"),
            Link("https://github.com/owner/inter/releases"),
            "override",
        ),
    }
    queue = jsonio.load(paths.queues / links.QUEUE_FILE)
    assert queue["undecided"] == {
        "solo": {
            "family": "Solo",
            "candidates": [
                {"kind": "homepage", "sources": ["homebrew_casks"], "url": "https://gone.example/"}
            ],
            "rejected": [],
        }
    }
    assert queue["overrides"] == {
        "pending": [],
        "picked": {},
        "rejected": [],
        "research": [],
        "unknown_family": [],
        "unusable": {},
    }
    assert queue["failed_checks"] == {}
    recorded = links.recorded_checks(Store(paths.require_store()), DAY)
    assert set(recorded) == {
        "https://fonts.google.com/specimen/Abel",
        "https://ok.example/",
        "https://designer.example/inter/",
        "https://github.com/owner/inter/releases",
    }


SOLO_PAGE = "https://gone.example/"
SOLO_PICK = f"{SOLO_PAGE} (from homebrew_casks)"  # the option, as links.questions words it


def answer_gate_k(paths: Paths, day: str, **choices: str) -> reviews.Applied:
    """Record the owner's gate K answers with ``rulings apply``: question id -> letter."""
    answers = paths.root / f"answers-{day}.toml"
    tables = "".join(
        f'\n[{qid.replace("_", "-")}]\nchoice = "{c}"\nreason = "The owner, in chat."\n'
        for qid, c in choices.items()
    )
    answers.write_text(f'gate = "K"\nday = {day}\n{tables}')
    return reviews.apply_answers(paths, answers)


def test_an_owner_pick_links_the_family_from_the_ruling_itself(paths: Paths) -> None:
    build_world(paths, members=["inter", "abel", "fira-code", "solo"])
    pages = {**WORLD_PAGES, SOLO_PAGE: (200, None)}
    links.run(context(paths, Web(pages).fetcher()))
    assert [q.id for q in reviews.questions(paths, "K")] == ["K-solo"]

    applied = answer_gate_k(paths, "2026-10-04", K_solo="a")
    assert applied.warnings == ()  # a known question: its option text becomes the ruling
    answer, _day = reviews.latest_answers(paths, "K")["K-solo"]
    assert answer.ruling == SOLO_PICK

    links.run(context(paths, Web(pages).fetcher()))
    # No override entry is needed: the ruling names the link.
    assert stageio.load_stage(paths, "links")["solo"] == Links(
        Link(SOLO_PAGE), None, links.PICK_BASIS
    )
    queue = jsonio.load(paths.queues / links.QUEUE_FILE)
    assert queue["undecided"] == {}
    assert queue["overrides"]["picked"] == {"K-solo": SOLO_PAGE}
    assert queue["failed_checks"] == {}
    assert reviews.questions(paths, "K") == []
    assert SOLO_PAGE in links.recorded_checks(Store(paths.require_store()), DAY)


def test_a_pick_keeps_the_link_the_owner_saw_when_candidates_change(paths: Paths) -> None:
    build_world(paths, members=["inter", "abel", "fira-code", "solo"])
    links.run(context(paths, Web(WORLD_PAGES).fetcher()))
    answer_gate_k(paths, "2026-10-04", K_solo="a")
    # A later month: another source gives the family a page that sorts first, so
    # option (a) of a question asked now would be that page.
    brew = rec("homebrew_casks", "Solo", ("homepage", SOLO_PAGE), key="font-solo")
    extra = rec("fontsource", "Solo", ("homepage", "https://a-solo.example/"), key="solo")
    records.write_jsonl([extra], paths.records / "fontsource.jsonl")
    u = stageio.load_stage(paths, "universe")
    families = {**u.families, "solo": fam("solo", "Solo", [brew, extra])}
    stageio.dump_stage(paths, "universe", replace(u, families=families))
    assert links.candidates([brew, extra])[0][0].url == "https://a-solo.example/"

    links.run(context(paths, None, options=RunOptions(from_snapshots=DAY)))
    assert stageio.load_stage(paths, "links")["solo"].primary == Link(SOLO_PAGE)


@pytest.mark.parametrize(
    ("ruling", "values", "want"),
    [
        (SOLO_PICK, (), SOLO_PAGE),
        (
            "https://github.com/o/r/releases (from fontsource, homebrew_casks)",
            (),
            "https://github.com/o/r/releases",
        ),
        (
            "The owner took the designer's page.",
            (("url", "https://solo.example/"),),
            "https://solo.example/",
        ),
        ("Accept", (), None),
        (links.RESEARCH_OPTION, (), None),
    ],
)
def test_picked_url_reads_the_option_text_or_a_url_value(
    ruling: str, values: tuple[tuple[str, str], ...], want: str | None
) -> None:
    answer = reviews.Answer("K-solo", ruling, "test", choice="a", values=values)
    assert links.picked_url(answer) == want


def test_owner_picks_leave_research_entries_and_bad_links_alone() -> None:
    def answer(ruling: str, **values: str) -> reviews.Answer:
        return reviews.Answer("x", ruling, "test", choice="a", values=tuple(values.items()))

    answers = {
        "K-picked": answer("https://picked.example/ (from homebrew_casks)"),
        "K-asked": answer(links.RESEARCH_OPTION),
        "K-researched": answer(links.RESEARCH_OPTION),
        "K-entry": answer("https://entry.example/ (from fontist)"),
        "K-asset": answer("x", url="https://github.com/o/r/releases/download/v1/F.zip"),
        "K-vague": answer("Accept"),
        "K-outside": answer("https://outside.example/ (from fontist)"),
    }
    entry = links.LinkOverride(
        family="entry", name="Entry", question="K-entry", primary="https://e.example/", reason="r"
    )
    researched = replace(entry, family="researched", question="K-researched-page")
    families = ["picked", "asked", "researched", "entry", "asset", "vague", "unasked"]
    got = links.owner_picks(families, answers, [entry, researched])
    assert got.links == {"picked": Links(Link("https://picked.example/"), None, links.PICK_BASIS)}
    assert got.picked == {"K-picked": "https://picked.example/"}
    assert got.research == ("asked",)  # "researched" already has its override entry
    assert set(got.unusable) == {"K-asset", "K-vague"}
    assert "release asset" in got.unusable["K-asset"]


def test_a_research_answer_waits_for_an_override_under_a_new_question(paths: Paths) -> None:
    build_world(paths, members=["inter", "abel", "fira-code", "solo"])
    links.run(context(paths, Web(WORLD_PAGES).fetcher()))
    answer_gate_k(paths, "2026-10-04", K_solo="b")  # the research option
    links.run(context(paths, Web(WORLD_PAGES).fetcher()))
    queue = jsonio.load(paths.queues / links.QUEUE_FILE)
    assert "solo" in queue["undecided"]
    assert queue["overrides"]["research"] == ["K-solo"]
    assert [q.id for q in reviews.questions(paths, "K")] == ["K-solo"]  # still open

    researched = (
        '[[override]]\nfamily = "solo"\nname = "Solo"\nquestion = "K-solo-page"\n'
        'primary = "https://solo.example/"\nreason = "Claude researched it."\n'
    )
    write_overrides(paths.root, INTER, researched)
    links.run(context(paths, Web(WORLD_PAGES).fetcher()))
    queue = jsonio.load(paths.queues / links.QUEUE_FILE)
    assert queue["overrides"]["research"] == []
    assert queue["overrides"]["pending"] == ["K-solo-page"]
    # The override's question replaces the family's own.
    assert [q.id for q in reviews.questions(paths, "K")] == ["K-solo-page"]


def test_an_override_entry_on_the_family_question_decides_instead_of_the_pick(
    paths: Paths,
) -> None:
    build_world(paths, members=["inter", "abel", "fira-code", "solo"])
    links.run(context(paths, Web(WORLD_PAGES).fetcher()))
    answer_gate_k(paths, "2026-10-04", K_solo="a")
    entry = (
        '[[override]]\nfamily = "solo"\nname = "Solo"\nquestion = "K-solo"\n'
        'primary = "https://designer.example/solo/"\nreason = "A fuller release."\n'
    )
    write_overrides(paths.root, INTER, entry)
    links.run(context(paths, Web(WORLD_PAGES).fetcher()))
    assert stageio.load_stage(paths, "links")["solo"] == Links(
        Link("https://designer.example/solo/"), None, "override"
    )
    assert jsonio.load(paths.queues / links.QUEUE_FILE)["overrides"]["picked"] == {}


def test_a_replay_warns_about_links_accepted_after_its_run(
    paths: Paths, caplog: pytest.LogCaptureFixture
) -> None:
    build_world(paths, members=["inter", "abel", "fira-code", "solo"])
    pages = {**WORLD_PAGES, SOLO_PAGE: (200, None)}
    links.run(context(paths, Web(pages).fetcher()))  # the run: solo is left undecided
    answer_gate_k(paths, "2026-10-04", K_solo="a")  # the owner rules afterwards

    replay = RunOptions(from_snapshots=DAY)
    links.run(context(paths, None, options=replay))
    queue = jsonio.load(paths.queues / links.QUEUE_FILE)
    assert queue["failed_checks"][SOLO_PAGE]["problems"] == [links.NOT_CHECKED]
    assert f"run `tff-catalog links --date {DAY.isoformat()}` live" in caplog.text

    # A live run for the run's date checks just that link and adds it to the day's record ...
    web = Web(pages)
    links.run(context(paths, web.fetcher()))
    assert {url for _, url in web.requests} == {SOLO_PAGE}
    # ... so the replay passes.
    caplog.clear()
    links.run(context(paths, None, options=replay))
    assert jsonio.load(paths.queues / links.QUEUE_FILE)["failed_checks"] == {}
    assert "no check recorded" not in caplog.text


def test_stage_output_is_deterministic(paths: Paths) -> None:
    build_world(paths)
    links.run(context(paths, Web(WORLD_PAGES).fetcher()))
    first = stageio.stage_path(paths, "links").read_bytes()
    links.run(context(paths, None, options=RunOptions(from_snapshots=DAY)))
    assert stageio.stage_path(paths, "links").read_bytes() == first


def test_links_check_passes_when_every_catalog_primary_answers(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    build_world(paths, members=["inter", "abel", "fira-code"])
    web = Web(WORLD_PAGES)
    links.run(context(paths, web.fetcher()))
    web.requests.clear()
    assert links.cmd_check(context(paths, web.fetcher())) == 0
    assert web.requests == []  # the day's recorded answers are reused
    assert "3 families, 0 failures" in capsys.readouterr().out


def test_links_check_fails_on_a_missing_or_broken_primary(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    build_world(paths, members=["inter", "abel", "fira-code", "solo"])
    pages = {**WORLD_PAGES, "https://ok.example/": (404, None)}
    links.run(context(paths, Web(pages).fetcher()))
    recorded = sorted(p.read_bytes() for p in (paths.store / links.CHECKS_SOURCE).rglob("*.*"))
    web = Web(pages)
    assert links.cmd_check(context(paths, web.fetcher())) == 1
    # It reads the day's recorded answers only: no request, and the snapshot is unchanged,
    # even for the URL whose recorded answer is not 200.
    assert web.requests == []
    after = sorted(p.read_bytes() for p in (paths.store / links.CHECKS_SOURCE).rglob("*.*"))
    assert after == recorded
    out = capsys.readouterr().out
    assert "FAIL solo: no accepted link (gate K)" in out
    assert "FAIL fira-code: primary https://ok.example/: HTTP 404" in out
    queue = jsonio.load(paths.queues / links.QUEUE_FILE)
    assert queue["failed_checks"]["https://ok.example/"]["used_by"] == ["fira-code:primary"]


def test_links_check_without_a_links_file(paths: Paths) -> None:
    assert links.cmd_check(context(paths, None)) == 1


# --- network -------------------------------------------------------------------------------------


@pytest.mark.network
def test_committed_override_links_answer_200() -> None:
    overrides = links.load_overrides(Paths.for_root(ROOT))
    urls = {u for o in overrides for u in (o.primary, o.designer) if u}
    urls.add(links.specimen_url("Inter"))
    with Fetcher() as fetcher:
        rows, fetched = links.check_urls(urls, fetcher)
    bad = {url: links.to_check(row) for url, row in rows.items() if not links.to_check(row).ok}
    assert bad == {}
    assert len(fetched) >= len(urls)
