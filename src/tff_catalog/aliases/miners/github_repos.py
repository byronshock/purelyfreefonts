"""Alias miner "github_repos": GitHub release assets -> families (milestone-1 step 7).

**Source.** ``REPOS``, a hand table of GitHub repositories: the family (or the
families) each one publishes, whether its releases are a *main download
channel* of the font (methodology §5: only those repos count), and rules for
repos whose assets are not all one family. It was seeded on 2026-09-26 from the
old library's repo table (``build.py``, ``final_assemble.py``), the scouts
(scout-desktop §8) and ``config/sources/github_releases.toml``, and the owner
reviews it with gate A. A repo is a main channel when the font's own site or
README sends people to its releases; it is not when Google Fonts is the
official download, when it is a mirror, or when another collector counts it
(``ryanoasis/nerd-fonts``). ``googlefonts/googlefontdirectory-hg`` is never a
source of names.

**Keys.** The miner maps the ``gh-asset:<repo>/<asset base>`` keys of the
newest ``github_releases`` snapshot on or before the run date, made by that
collector's own ``parse`` with its settings, so they are exactly the keys stage
"map" looks up. Keys seen only in prereleases are left out, because the engine
reads no prereleases, except from the repos in ``ranking.toml``
``[sources.github] prerelease_repos`` (repos that publish only prereleases,
such as OpenDyslexic's: the owner's ruling of 2026-09-26), whose prerelease keys
count like any other.

**Rules.** For each key, the entry of its repo (compared ignoring case) decides:

- a repo that is not a main channel gives nothing (with a warning, since the
  collector should not fetch it);
- the first rule whose pattern matches the whole asset base (``re.fullmatch``,
  ignoring case) gives the target: one family (``package``, or ``build`` with a
  build detail), a bundle's families (one ``bundle`` candidate per member), or
  a skip with its reason;
- otherwise the repo's default. A repo that publishes one family has that
  family as its default; a repo that publishes several has none, so an asset
  no rule names is logged and left out. Nothing is guessed from a name prefix.

A fetched repo missing from the table falls back to the ``family`` hint in the
collector's config (a warning asks for a table entry).

**Candidates.** Alias ``gh-asset:<key>``; target ``font-name:<family>``, a
family name the stage resolves through the universe and the name rows (so a
later rename or merge of that name carries these rows with it); source
``github_repos``; evidence ``https://github.com/<repo>/releases``; never
``auto``, so the owner reviews every one. Keys that differ only in what
``match_key`` ignores (``Fira_Code.zip``, ``FiraCode.zip``) get one candidate,
the first spelling. The stage writes them to
``data/alias-seeds/github_repos.csv``; this miner never writes
``data/aliases.csv``.

**Failure.** Without a store, without a ``github_releases`` snapshot, or with a
malformed table, ``mine`` raises ``MinerError`` (a missing or malformed
``config/sources/github_releases.toml`` raises the config's ``ConfigError``),
and the stage keeps the committed seeds.

**Bundles.** A key covers every release of an asset, so a bundle names the
families of the newest release carrying it (checked by listing the zips).
"""

import importlib
import logging
import re
import tomllib
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from tff_catalog.aliases import BUILD_DETAILS, AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.collectors.base import Collector, ParseContext, load_settings
from tff_catalog.keys import match_key
from tff_catalog.records import Observation, SourceKey
from tff_catalog.store import Snapshot

NAME = "github_repos"
COLLECTOR = "github_releases"  # the collector whose keys this miner maps
ASSET_NS = "gh-asset"
TARGET_NS = "font-name"
TARGET_RELATIONS = frozenset({"package", "build", "bundle", "skip"})

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_LISTED_UNMAPPED = 8  # asset names a warning lists before "and N more"


class MinerError(RuntimeError):
    """The miner cannot run: no store, no snapshot, or a malformed table."""


# --- the table --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    """What an asset maps to.

    ``package``/``build``: one family; ``bundle``: two or more, each credited
    (D2); ``skip``: no candidate, ``detail`` says why. A build's ``detail`` is
    an ``aliases.BUILD_DETAILS`` value; a bundle's is free text.
    """

    families: tuple[str, ...]
    relation: str = "package"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Rule:
    """Assets whose base (the key after ``<repo>/``) matches ``pattern`` whole, ignoring case."""

    pattern: str
    target: Target


@dataclass(frozen=True, slots=True)
class RepoEntry:
    """One repository: what its release assets are, and whether they count."""

    repo: str  # "owner/name" as GitHub spells it
    default: Target | None  # for assets no rule names; None when the repo has several families
    main_channel: bool = True
    rules: tuple[Rule, ...] = ()
    note: str = ""  # why, where it is not obvious


def package(family: str) -> Target:
    return Target((family,))


def build(family: str, detail: str) -> Target:
    return Target((family,), "build", detail)


def bundle(*families: str, detail: str = "") -> Target:
    return Target(families, "bundle", detail)


def skip(reason: str) -> Target:
    return Target((), "skip", reason)


def repo(
    name: str,
    default: str | Target | None,
    *rules: Rule,
    main: bool = True,
    note: str = "",
) -> RepoEntry:
    """A table entry; a plain string default is that one family as a package."""
    target = package(default) if isinstance(default, str) else default
    return RepoEntry(name, target, main, rules, note)


# Gate A (owner; default as the old library did): Iosevka's Term and Fixed spacings
# count to their variant. The universe also lists "Iosevka Term" and "Iosevka Term
# Slab" (Nerd Fonts originals), and the nerd miner maps Nerd's IosevkaTerm builds to
# them. To count a spacing to its own family, name it here by what follows "Iosevka"
# in the asset name, e.g. {"Term": "Iosevka Term", "TermSlab": "Iosevka Term Slab"}.
IOSEVKA_OWN_FAMILIES: Mapping[str, str] = {}


def _iosevka_rules(own: Mapping[str, str] = IOSEVKA_OWN_FAMILIES) -> tuple[Rule, ...]:
    """Iosevka's release assets: ``<packaging>-[SGr-|Unhinted-]Iosevka[Term|Fixed]<variant>.zip``.

    Packaging (PkgTTC, PkgTTF, PkgWebFont, SuperTTC), style groups and hinting
    are formats of one download. Term and Fixed are the variant's spacing
    options, so they count to the variant, except the names in ``own``, which
    count to their own family; each variant is the name Homebrew and
    Fontsource list it under.
    """
    variants = {
        "": "Iosevka",
        "Aile": "Iosevka Aile",
        "Etoile": "Iosevka Etoile",
        "Slab": "Iosevka Slab",
        "Curly": "Iosevka Curly",
        "CurlySlab": "Iosevka Curly Slab",
        **{f"SS{n:02d}": f"Iosevka SS{n:02d}" for n in range(1, 19)},
    }
    lead = r"(?:PkgTTC|PkgTTF|PkgWebFont|SuperTTC)-(?:SGr-)?(?:Unhinted-)?Iosevka"
    first = tuple(Rule(rf"{lead}{re.escape(v)}\.zip", package(name)) for v, name in own.items())
    rest = (Rule(rf"{lead}(?:Term|Fixed)?{v}\.zip", package(name)) for v, name in variants.items())
    return (*first, *rest)


# The families in OpenType.zip, TrueType.zip and Web.zip of v6.4.0, the newest release
# carrying them (checked 2026-09-26). Older releases held fewer, but a key covers every
# release of an asset, and new downloads go mostly to the newest.
_PLEX_ALL = (
    "IBM Plex Sans",
    "IBM Plex Sans Condensed",
    "IBM Plex Mono",
    "IBM Plex Serif",
    "IBM Plex Sans Arabic",
    "IBM Plex Sans Devanagari",
    "IBM Plex Sans Hebrew",
    "IBM Plex Sans JP",
    "IBM Plex Sans KR",
    "IBM Plex Sans TC",
    "IBM Plex Sans Thai",
    "IBM Plex Sans Thai Looped",
)


def _plex_rules() -> tuple[Rule, ...]:
    """IBM/plex: one zip per family (``ibm-plex-<family>.zip``, ``plex-<family>-variable.zip``),
    and the whole set in ``OpenType.zip``, ``TrueType.zip`` and ``Web.zip``."""
    one = {
        "sans": "IBM Plex Sans",
        "sans-condensed": "IBM Plex Sans Condensed",
        "mono": "IBM Plex Mono",
        "serif": "IBM Plex Serif",
        "math": "IBM Plex Math",
        **{
            s: f"IBM Plex Sans {n}"
            for s, n in (
                ("sans-arabic", "Arabic"),
                ("sans-devanagari", "Devanagari"),
                ("sans-hebrew", "Hebrew"),
                ("sans-thai", "Thai"),
                ("sans-thai-looped", "Thai Looped"),
                ("sans-jp", "JP"),
                ("sans-kr", "KR"),
                ("sans-sc", "SC"),
                ("sans-tc", "TC"),
            )
        },
    }
    rules = [Rule(r"(?:OpenType|TrueType|Web)\.zip", bundle(*_PLEX_ALL))]
    rules.append(Rule(r"ibm-plex-sans-var\.zip", package("IBM Plex Sans")))
    for slug, name in one.items():
        rules.append(Rule(rf"(?:ibm-plex-{slug}|plex-{slug}-variable)\.zip", package(name)))
    return tuple(rules)


_MONASPACE = (
    "Monaspace Neon",
    "Monaspace Argon",
    "Monaspace Xenon",
    "Monaspace Radon",
    "Monaspace Krypton",
)
# The families in Libertinus-6.10.zip and Libertinus-7.051.zip (checked 2026-09-26).
_LIBERTINUS = (
    "Libertinus Serif",
    "Libertinus Serif Display",
    "Libertinus Serif Initials",
    "Libertinus Sans",
    "Libertinus Mono",
    "Libertinus Math",
    "Libertinus Keyboard",
)
_DEJAVU = ("DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif")
_GOOGLE_FONTS = "Google Fonts is the official download"
_CJK = "CJK-primary: main channel, but not fetched (the Latin gate excludes it)"
_ICON = "icon or emoji font: main channel, but not fetched (not ranked)"

# Every repo config/sources/github_releases.toml lists, and the old library's other
# repos (main channels or not), sorted by repo ignoring case.
REPOS: tuple[RepoEntry, ...] = (
    repo("0xType/0xProto", "0xProto"),
    repo("13rac1/twemoji-color-font", "Twitter Color Emoji", note=_ICON),
    repo("ACh-K/Cubic-11", "Cubic 11", note=_CJK),
    repo("adobe-fonts/source-code-pro", "Source Code Pro"),
    repo("adobe-fonts/source-han-code-jp", "Source Han Code JP", note=_CJK),
    repo("adobe-fonts/source-han-mono", "Source Han Mono", note=_CJK),
    repo("adobe-fonts/source-han-sans", "Source Han Sans", note=_CJK),
    repo("adobe-fonts/source-han-serif", "Source Han Serif", note=_CJK),
    repo(
        "adobe-fonts/source-sans",
        "Source Sans 3",
        note="source-sans-pro.zip holds the releases under its old name, Source Sans Pro",
    ),
    repo(
        "adobe-fonts/source-serif",
        "Source Serif 4",
        note="source-serif-pro.zip holds the releases under its old name, Source Serif Pro",
    ),
    repo(
        "ahatem/IoskeleyMono",
        "Ioskeley Mono",
        Rule(r".*NerdFont.*", build("Ioskeley Mono", "nerd")),
        Rule(r".*[-_.]NL(?:[-_.].*)?", build("Ioskeley Mono", "nl")),
    ),
    repo(
        "alerque/libertinus",
        None,
        Rule(
            r"(?:libertinus|libertine)(?:-r\d+-g[0-9a-f]+)?\.(?:zip|tar\.xz|tar\.zst)",
            bundle(*_LIBERTINUS),
        ),
        note="every release zip holds the whole set (the first, v6.0, was still named libertine)",
    ),
    repo("andreberg/Meslo-Font", "Meslo LG"),
    repo("antijingoist/opendyslexic", "OpenDyslexic"),
    repo("arrowtype/recursive", "Recursive"),
    repo("arrowtype/shantell-sans", "Shantell Sans"),
    repo("atelier-anchor/smiley-sans", "Smiley Sans", note=_CJK),
    repo("be5invis/Iosevka", None, *_iosevka_rules()),
    repo("be5invis/Sarasa-Gothic", "Sarasa Gothic", note=_CJK),
    repo(
        "belluzj/fantasque-sans",
        "Fantasque Sans Mono",
        note="CosmicSansNeueMono.zip is the font's first name; NoLoopK and LargeLineHeight are its options",
    ),
    repo("blobject/agave", "Agave"),
    repo(
        "calcom/sans",
        "Cal Sans",
        Rule(r"CalSansFlex.*", skip("Cal Sans Flex (v2.003) is not a catalog family yet")),
    ),
    repo(
        "canonical/Ubuntu-Sans-fonts",
        "Ubuntu Sans",
        note="its first releases were named Ubuntu-fonts",
    ),
    repo(
        "canonical/Ubuntu-Sans-Mono-fonts",
        "Ubuntu Sans Mono",
        note="its first releases were named UbuntuMono-fonts; they are Ubuntu Sans Mono, not Ubuntu Mono",
    ),
    repo("cormullion/juliamono", "JuliaMono"),
    repo("crozynski/comicneue", "Comic Neue"),
    repo(
        "dejavu-fonts/dejavu-fonts",
        None,
        Rule(r"dejavu-fonts-ttf\.(?:zip|tar\.bz2)", bundle(*_DEJAVU, "DejaVu Math TeX Gyre")),
        Rule(r"dejavu-lgc-fonts-ttf\.(?:zip|tar\.bz2)", bundle(*_DEJAVU, detail="lgc")),
        Rule(r"dejavu-sans-ttf\.zip", package("DejaVu Sans")),
        Rule(r"dejavu-fonts\.(?:zip|tar\.bz2)", skip("the source archive, not fonts")),
    ),
    repo("eigilnikolajsen/commit-mono", "Commit Mono"),
    repo("evilmartians/mono", "Martian Mono"),
    repo("fcambus/spleen", "Spleen"),
    repo("FortAwesome/Font-Awesome", "Font Awesome", note=_ICON),
    repo("github/hubot-sans", "Hubot Sans"),
    repo("github/mona-sans", "Mona Sans"),
    repo(
        "githubnext/monaspace",
        None,
        Rule(r"monaspace(?:-webfont)?-nerdfonts\.zip", bundle(*_MONASPACE, detail="nerd")),
        Rule(
            r"monaspace(?:-static|-variable|-frozen|-webfont-static|-webfont-variable)?\.zip",
            bundle(*_MONASPACE),
        ),
        note="every zip holds all five families (D2 names the Monaspace cask as a bundle)",
    ),
    repo(
        "GNOME/cantarell-fonts", "Cantarell", main=False, note="a mirror; GNOME releases elsewhere"
    ),
    repo(
        "google/fonts",
        None,
        main=False,
        note="the Google Fonts library; Google's own counts cover it",
    ),
    repo(
        "googlefonts/atkinson-hyperlegible", "Atkinson Hyperlegible", main=False, note=_GOOGLE_FONTS
    ),
    repo(
        "googlefonts/googlefontdirectory-hg",
        None,
        main=False,
        note="an old Mercurial mirror; never a source of names (milestone-1 step 7)",
    ),
    repo("googlefonts/googlesans-code", "Google Sans Code"),
    repo("googlefonts/Inconsolata", "Inconsolata", main=False, note=_GOOGLE_FONTS),
    repo("googlefonts/opensans", "Open Sans", main=False, note=_GOOGLE_FONTS),
    repo("googlefonts/roboto-3-classic", "Roboto", main=False, note=_GOOGLE_FONTS),
    repo("googlefonts/RobotoMono", "Roboto Mono", main=False, note=_GOOGLE_FONTS),
    repo("googlefonts/spacemono", "Space Mono", main=False, note=_GOOGLE_FONTS),
    repo("hchargois/gohufont", "Gohu"),
    repo("hfg-gmuend/openmoji", "OpenMoji", note=_ICON),
    repo("i-tu/Hasklig", "Hasklig"),
    repo(
        "iaolo/iA-Fonts",
        None,
        note="no release files yet; add rules when they appear (iA Writer Mono, Duo and Quattro)",
    ),
    repo("IBM/plex", None, *_plex_rules()),
    repo(
        "IdreesInc/Monocraft",
        "Monocraft",
        Rule(r"Monocraft-nerd-fonts-patched\.(?:ttf|ttc|otf)", build("Monocraft", "nerd")),
        note="MinecraftMono.otf is the font's first name",
    ),
    repo("intel/intel-one-mono", "Intel One Mono"),
    repo("itfoundry/poppins", "Poppins", main=False, note=_GOOGLE_FONTS),
    repo("jenskutilek/sudo-font", "Sudo"),
    repo("JetBrains/JetBrainsMono", "JetBrains Mono"),
    repo("JulietaUla/Montserrat", "Montserrat", main=False, note=_GOOGLE_FONTS),
    repo("kaspernordkvist/uncut_sans", "Uncut Sans"),
    repo("larsenwork/monoid", "Monoid"),
    repo("lauridskern/open-runde", "Open Runde"),
    repo(
        "liberationfonts/liberation-fonts",
        None,
        note="no release files yet; add rules when they appear (Liberation Sans, Serif and Mono)",
    ),
    repo("lxgw/LxgwNeoXiHei", "LXGW NeoXiHei", note=_CJK),
    repo("lxgw/LxgwWenKai", "LXGW WenKai", note=_CJK),
    repo("madmalik/mononoki", "Mononoki"),
    repo(
        "marcologous/Open-Sauce-Fonts",
        None,
        note="no release files yet; add rules when they appear (Open Sauce Sans, One and Two)",
    ),
    repo(
        "microsoft/cascadia-code",
        None,
        Rule(r"CascadiaCode\.zip", bundle("Cascadia Code", "Cascadia Mono")),
        Rule(r"Cascadia\.ttf", package("Cascadia Code")),
        Rule(r"CascadiaPL\.ttf", build("Cascadia Code", "powerline")),
        Rule(r"CascadiaMono\.ttf", package("Cascadia Mono")),
        Rule(r"CascadiaMonoPL\.ttf", build("Cascadia Mono", "powerline")),
        Rule(
            r"CascadiaNext(?:JP|SC|TC)\.wght\.ttf", skip("Cascadia Next is a separate CJK family")
        ),
        note="CascadiaCode.zip holds Cascadia Code and Cascadia Mono since 2020; the 2019 releases shipped single files",
    ),
    repo("mishamyrt/Lilex", "Lilex"),
    repo(
        "mozilla/Fira",
        None,
        main=False,
        note="Google Fonts is the main download of Fira Sans; the releases carry no font files",
    ),
    repo("naver/d2codingfont", "D2Coding", note=_CJK),
    repo("oppiliappan/scientifica", "Scientifica"),
    repo("orioncactus/pretendard", "Pretendard", note=_CJK),
    repo("pcaro90/hermit", "Hermit"),
    repo("psb1558/Junicode-font", "Junicode"),
    repo("rbanffy/3270font", "IBM 3270"),
    repo(
        "reddit/redditsans",
        None,
        note="no release files yet; add rules when they appear (Reddit Sans, maybe Reddit Sans Condensed)",
    ),
    repo(
        "RedHatOfficial/Overpass",
        None,
        Rule(
            r"overpass-desktop-fonts\.zip|overpass-webfonts\.zip|overpass\.zip",
            bundle("Overpass", "Overpass Mono"),
        ),
        Rule(
            r"overpass-fonts-ttf(?:-2)?\.zip|overpass-fonts-web-fonts\.zip|overpass\.tar\.gz"
            r"|overpass-fonts-.*\.noarch\.rpm",
            package("Overpass"),
        ),
        Rule(r".*\.src\.rpm", skip("a source package, not fonts")),
        note="version 2 was Overpass alone; version 3 added Overpass Mono to the same zips",
    ),
    repo(
        "RedHatOfficial/RedHatFont",
        None,
        note="no release files yet; add rules when they appear (Red Hat Display, Text and Mono)",
    ),
    repo("rektdeckard/departure-mono", "Departure Mono"),
    repo("romeovs/creep", "Creep"),
    repo(
        "rsms/inter", "Inter", note="Inter-UI.zip holds the releases under its old name, Inter UI"
    ),
    repo("rubjo/victor-mono", "Victor Mono"),
    repo("ryanoasis/nerd-fonts", None, main=False, note="the nerd_releases collector counts it"),
    repo("shannpersand/comic-shanns", "Comic Shanns Mono"),
    repo("SolidZORO/zpix-pixel-font", "Zpix", note=_CJK),
    repo("source-foundry/Hack", "Hack"),
    repo(
        "stipub/stixfonts",
        None,
        note="no release files yet; add rules when they appear (STIX Two Text and STIX Two Math)",
    ),
    repo(
        "subframe7536/maple-font",
        "Maple Mono",
        Rule(r"(?:cn|jp|kr|tc)-base-.*|vfc\.zip", skip("CJK base fonts for the build script")),
        Rule(
            r"Maple\.(?:UI|hand)\..*|MapleX\..*|Mapple-.*",
            skip("other Maple families (Maple UI, Maple Hand, MapleX, Mapple)"),
        ),
        Rule(r".*[-_.]NFMono(?:[-_.].*)?", build("Maple Mono", "nfm")),
        Rule(r".*[-_.]NFPropo(?:[-_.].*)?", build("Maple Mono", "nfp")),
        Rule(r"(?:.*[-_.])?(?:NF|nerdfont)(?:[-_.].*)?", build("Maple Mono", "nf")),
        Rule(r".*[-_.](?:CN|JP|KR|TC|SC)(?:[-_.].*)?", build("Maple Mono", "cjk")),
        Rule(r"MapleMono(?:Normal)?NL[-_.].*", build("Maple Mono", "nl")),
        note=(
            "Normal, NR and SL are Maple Mono's feature presets, NL its no-ligature build, and "
            "Maple Code its first name; an NF build that is also CN gets both credits from its "
            "name (corrections)"
        ),
    ),
    repo("sunaku/tamzen-font", "Tamzen"),
    repo("TakWolf/ark-pixel-font", "Ark Pixel Font", note=_CJK),
    repo("TakWolf/fusion-pixel-font", "Fusion Pixel Font", note=_CJK),
    repo("the-moonwitch/Cozette", "Cozette"),
    repo("theleagueof/junction", "Junction"),
    repo("theleagueof/league-gothic", "League Gothic"),
    repo("theleagueof/league-mono", "League Mono"),
    repo("theleagueof/league-spartan", "League Spartan"),
    repo("theleagueof/ostrich-sans", "Ostrich Sans"),
    repo("tonsky/FiraCode", "Fira Code"),
    repo("TrionesType/zhuque", "Zhuque Fangsong", note=_CJK),
    repo("twilio/twilio-sans-mono", "Twilio Sans Mono"),
    repo("uswds/public-sans", "Public Sans"),
    repo(
        "vercel/geist-font",
        None,
        Rule(r"Geist(?:\.Sans)?\.zip", package("Geist")),
        Rule(r"Geist\.?Mono\.zip", package("Geist Mono")),
        Rule(r"geist-font(?:-alpha)?\.zip", bundle("Geist", "Geist Mono", "Geist Pixel")),
        note=(
            "from 1.4.2 one geist-font zip holds Geist and Geist Mono, and from v1.7.2 Geist Pixel "
            "too (checked 2026-09-26: 1.4.2, 1.6.0 and 1.7.0 lack it; v1.7.2 and 1.8.0 have it)"
        ),
    ),
    repo("welai/glow-sans", "Glow Sans", note=_CJK),
    repo("yuru7/HackGen", "HackGen", note=_CJK),
    repo("yuru7/PlemolJP", "PlemolJP", note=_CJK),
    repo("yuru7/udev-gothic", "UDEV Gothic", note=_CJK),
    repo(
        "zed-industries/zed-fonts",
        None,
        Rule(r"zed-mono\.zip", package("Zed Mono")),
        Rule(r"zed-sans\.zip", package("Zed Sans")),
        Rule(r"zed-app-fonts\.zip", bundle("Zed Mono", "Zed Sans")),
    ),
)


def _target_problems(t: Target, where: str) -> list[str]:
    out = []
    if t.relation not in TARGET_RELATIONS:
        return [f"{where}: relation {t.relation!r} not in {sorted(TARGET_RELATIONS)}"]
    if any(not f or f != f.strip() for f in t.families):
        out.append(f"{where}: a family name is empty or has outer spaces")
    wanted = {"package": 1, "build": 1, "skip": 0}.get(t.relation)
    if wanted is not None and len(t.families) != wanted:
        out.append(f"{where}: a {t.relation} names {wanted} families, not {len(t.families)}")
    if t.relation == "bundle" and len(set(t.families)) < 2:
        out.append(f"{where}: a bundle names two or more distinct families")
    if t.relation == "build" and t.detail not in BUILD_DETAILS:
        out.append(f"{where}: build detail {t.detail!r} not in {sorted(BUILD_DETAILS)}")
    if t.relation == "skip" and not t.detail:
        out.append(f"{where}: a skip gives its reason")
    return out


def check_table(table: Iterable[RepoEntry]) -> list[str]:
    """What is wrong with ``table``: repo names, duplicates, targets and patterns."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in table:
        where = entry.repo
        if not _REPO_RE.fullmatch(entry.repo):
            out.append(f"{where}: expected 'owner/name'")
        if entry.repo.casefold() in seen:
            out.append(f"{where}: listed twice")
        seen.add(entry.repo.casefold())
        if entry.default is not None:
            out.extend(_target_problems(entry.default, f"{where} default"))
        for i, rule in enumerate(entry.rules):
            at = f"{where} rule {i} ({rule.pattern})"
            try:
                re.compile(rule.pattern)
            except re.error as exc:
                out.append(f"{at}: not a regular expression: {exc}")
            out.extend(_target_problems(rule.target, at))
    return out


def table_index(table: Iterable[RepoEntry]) -> dict[str, RepoEntry]:
    """``repo.casefold()`` -> entry."""
    return {e.repo.casefold(): e for e in table}


# --- mining ---------------------------------------------------------------------------


def target_of(entry: RepoEntry, base: str) -> Target | None:
    """The target of the asset ``base`` of ``entry``'s repo: the first rule matching it
    whole (ignoring case), else the default; None when neither names it."""
    for rule in entry.rules:
        if re.fullmatch(rule.pattern, base, re.IGNORECASE):
            return rule.target
    return entry.default


def collector() -> Collector:
    """The ``github_releases`` collector. It is imported when the miner runs, not
    when miners are discovered, so a broken collector module fails this miner alone."""
    return importlib.import_module(f"tff_catalog.collectors.ranking.{COLLECTOR}").COLLECTOR


def asset_keys(
    snapshot: Snapshot,
    settings: object,
    log: logging.Logger,
    prerelease_repos: Collection[str] = (),
) -> dict[str, set[str]]:
    """Repo (as the collector spells it) -> its ``gh-asset`` keys in ``snapshot``.

    The keys come from the ``github_releases`` collector's own ``parse``, so
    they match the records exactly. A key seen only in prereleases is left out,
    unless its repo is in ``prerelease_repos`` (compared ignoring case).
    """
    parser = collector()
    counted = {r.casefold() for r in prerelease_repos}
    released: dict[str, set[str]] = defaultdict(set)
    prerelease: dict[str, set[str]] = defaultdict(set)
    for rec in parser.parse(ParseContext(snapshot=snapshot, settings=settings, log=log)):
        if not isinstance(rec, Observation) or rec.key.ns != ASSET_NS:
            continue
        found = dict(rec.attrs)
        name = str(found["repo"])
        pre = found.get("prerelease") is True and name.casefold() not in counted
        (prerelease if pre else released)[name].add(rec.key.key)
    dropped = sum(len(keys - released[name]) for name, keys in prerelease.items())
    if dropped:
        log.info("%s: %d keys seen only in prereleases left out", NAME, dropped)
    return {name: keys for name, keys in released.items() if keys}


def _candidates_for(key: str, target: Target, repo_name: str) -> list[AliasCandidate]:
    evidence = f"https://github.com/{repo_name}/releases"
    return [
        AliasCandidate(
            alias=SourceKey(ASSET_NS, key),
            target=SourceKey(TARGET_NS, family),
            relation=target.relation,
            detail=target.detail,
            source=NAME,
            evidence=evidence,
            auto=False,
        )
        for family in target.families
    ]


def _entry_for(
    name: str, table: Mapping[str, RepoEntry], hints: Mapping[str, str], log: logging.Logger
) -> RepoEntry | None:
    entry = table.get(name.casefold())
    if entry is not None:
        return entry
    hint = hints.get(name.casefold(), "")
    if not hint:
        log.warning("%s: %s is not in the table and has no family hint; left out", NAME, name)
        return None
    log.warning("%s: %s is not in the table; using the config's family hint %r", NAME, name, hint)
    return repo(name, hint)


def _warn_unmapped(name: str, bases: list[str], log: logging.Logger) -> None:
    shown = ", ".join(bases[:_LISTED_UNMAPPED])
    more = f" and {len(bases) - _LISTED_UNMAPPED} more" if len(bases) > _LISTED_UNMAPPED else ""
    log.warning("%s: %s has no rule for %s%s; left out", NAME, name, shown, more)


def candidates(
    keys: Mapping[str, Iterable[str]],
    table: Mapping[str, RepoEntry],
    hints: Mapping[str, str],
    log: logging.Logger,
) -> list[AliasCandidate]:
    """Candidates for ``keys`` (repo -> ``gh-asset`` keys), sorted.

    ``table`` is ``table_index(REPOS)``; ``hints`` maps ``repo.casefold()`` to
    the collector config's ``family`` hint, used only for a repo the table lacks.
    """
    out: list[AliasCandidate] = []
    skipped = 0
    for name in sorted(keys, key=lambda n: (n.casefold(), n)):
        entry = _entry_for(name, table, hints, log)
        if entry is None:
            continue
        found = sorted(keys[name])
        if not entry.main_channel:
            log.warning(
                "%s: %s is not a main download channel (%s); its %d keys are left out",
                NAME,
                name,
                entry.note or "see the table",
                len(found),
            )
            continue
        unmapped = []
        for key in found:
            base = key.removeprefix(f"{name}/")
            target = target_of(entry, base) if base != key else None
            if target is None:
                unmapped.append(base)
            elif target.relation == "skip":
                skipped += 1
            else:
                out.extend(_candidates_for(key, target, name))
        if unmapped:
            _warn_unmapped(name, unmapped, log)
    found = _one_per_match_key(out)
    log.info("%s: %d candidates, %d assets skipped by rule", NAME, len(found), skipped)
    return found


def _one_per_match_key(cands: Iterable[AliasCandidate]) -> list[AliasCandidate]:
    """Sorted, keeping one spelling of keys the stage cannot tell apart.

    Stage "aliases" looks keys up by ``match_key``, so ``IBM-Plex-Mono.zip`` and
    ``ibm-plex-mono.zip`` (or ``Fira_Code.zip`` and ``FiraCode.zip``) are one key
    there; one row, the first spelling in sorted order, maps them all, and the
    owner reviews it once.
    """
    out: dict[tuple[str, ...], AliasCandidate] = {}
    for c in sorted(set(cands)):
        same = (c.alias.ns, match_key(c.alias.key), c.target.key, c.relation, c.detail)
        out.setdefault(same, c)
    return sorted(out.values())


def prerelease_repos(config_dir: Path) -> tuple[str, ...]:
    """``ranking.toml`` ``[sources.github] prerelease_repos``: the repos whose prereleases
    the engine counts (``()`` without the file). The config loader checks the file."""
    path = config_dir / "ranking.toml"
    if not path.is_file():
        return ()
    with path.open("rb") as fh:
        github = tomllib.load(fh).get("sources", {}).get("github", {})
    return tuple(str(r) for r in github.get("prerelease_repos", ()))


def family_hints(settings: object) -> dict[str, str]:
    """``repo.casefold()`` -> the ``family`` hint of each repo the collector's settings list."""
    out = {}
    for r in getattr(settings, "repos", ()):
        name, family = getattr(r, "repo", ""), getattr(r, "family", "")
        if name and family:
            out[name.casefold()] = family
    return out


class GithubRepos:
    """Maps GitHub release assets of main-channel repos to their families."""

    name: ClassVar[str] = NAME

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        """Candidates for the newest ``github_releases`` snapshot's keys (module docstring)."""
        problems = check_table(REPOS)
        if problems:
            raise MinerError(f"{NAME}: the repo table is malformed: {'; '.join(problems)}")
        if ctx.store is None:
            raise MinerError(f"{NAME}: needs the snapshot store (TFF_STORE)")
        snapshot = ctx.store.latest(COLLECTOR, ctx.run_date)
        if snapshot is None:
            raise MinerError(f"{NAME}: no {COLLECTOR} snapshot on or before {ctx.run_date}")
        settings = load_settings(collector(), ctx.paths)
        ctx.log.info("%s: reading %s %s", NAME, COLLECTOR, snapshot.date)
        keys = asset_keys(snapshot, settings, ctx.log, prerelease_repos(ctx.paths.config))
        return candidates(keys, table_index(REPOS), family_hints(settings), ctx.log)


MINER = GithubRepos()
