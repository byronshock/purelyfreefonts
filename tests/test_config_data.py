"""The hand-kept data in ``config/preinstalled.toml`` and ``config/foundries.toml``.

Milestone-1 step 2 (D8, gate C, ruling M12). ``test_config.py`` covers the
strict loader; these tests cover the lists themselves: the seeds the milestone
names are present, entries follow the rules in each file's header, D8's "only
Linux entries abstain" holds through the real consumer
(``corrections.abstentions``), and nothing personal slips in. They read only
these two files (plus ``site.toml`` labels and ``license-aliases.toml`` keys),
so a half-edited ranking file elsewhere does not fail them.

The ``network`` test checks that every URL still answers, with the project's
fetcher (User-Agent, one request a second per host), and never contacts the
hosts whose robots.txt opts out of AI agents.
"""

import re
import tomllib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from urllib.parse import urlsplit

import pytest
from tests.helpers import ROOT

from tff_catalog.config import check_foundries, check_preinstalled
from tff_catalog.config_model import (
    Corrections,
    FoundriesConfig,
    PreinstalledConfig,
    from_mapping,
    load_toml,
)

CONFIG = ROOT / "config"

# Ruling M12 names these five; "Roundo" is a family on Fontshare (see foundries.toml).
M12_FOUNDRIES = (
    "league-of-moveable-type",
    "velvetyne",
    "collletttivo",
    "open-foundry",
    "fontshare",
)

# Noto families that are Latin text families, not script-specific ones.
NOTO_LATIN = frozenset(
    {"Noto Sans", "Noto Sans Display", "Noto Sans Mono", "Noto Serif", "Noto Serif Display"}
)

# The one license string that is not a license: the foundry states none, and L1
# sends the family to the license review queue (Velvetyne's Lack).
NO_LICENSE = "NOASSERTION"

# Hosts whose robots.txt opts out of AI agents; nothing here may point into them
# except the foundry's own root URL, and the network test leaves them alone.
AI_OPT_OUT_HOSTS = frozenset({"velvetyne.fr", "www.velvetyne.fr", "www.collletttivo.it"})

# Forges whose repository URLs are exactly https://<host>/<owner>/<repo>.
FORGES = frozenset({"github.com", "gitlab.com", "codeberg.org"})


@cache
def preinstalled() -> PreinstalledConfig:
    return from_mapping(
        PreinstalledConfig, load_toml(CONFIG / "preinstalled.toml"), "preinstalled.toml"
    )


@cache
def foundries() -> FoundriesConfig:
    return from_mapping(FoundriesConfig, load_toml(CONFIG / "foundries.toml"), "foundries.toml")


def _clean_name(name: str) -> bool:
    return bool(name) and name == name.strip() and "  " not in name


# --- both files ----------------------------------------------------------------------------


def test_files_load_strictly_and_pass_their_checks() -> None:
    check_preinstalled(preinstalled())
    check_foundries(foundries())
    assert preinstalled().schema == 1
    assert foundries().schema == 1


@pytest.mark.parametrize("filename", ["preinstalled.toml", "foundries.toml"])
def test_no_personal_data_or_plain_http(filename: str) -> None:
    text = (CONFIG / filename).read_text(encoding="utf-8")
    assert "@" not in text, "no emails or handles"
    assert "http://" not in text, "every URL is https"


# --- preinstalled.toml ---------------------------------------------------------------------


def test_milestone_seeds_are_present() -> None:
    systems = preinstalled().systems
    by_os = defaultdict(set)
    for entry in systems.values():
        by_os[entry.os].update(entry.families)
    assert {"windows", "macos", "linux", "android"} <= set(by_os)
    assert {"Cascadia Code", "Cascadia Mono"} <= set(systems["windows-11"].families)
    assert {"Adwaita Sans", "Adwaita Mono"} <= set(systems["gnome"].families)
    assert systems["gnome"].os == "linux"
    assert "Roboto" in systems["android"].families
    assert systems["macos"].os == "macos"
    assert {"PT Sans", "PT Serif"} <= set(systems["macos"].families)
    for distro in ("debian", "ubuntu", "fedora-workstation", "kde-plasma"):
        assert systems[distro].os == "linux", distro


def test_endeavouros_carries_its_installer_fonts() -> None:
    """Gate C2 (rec): EndeavourOS has no font dependencies, so eos-base-group goes here."""
    entry = preinstalled().systems["endeavouros"]
    assert entry.os == "linux"
    assert "eos-base-group" in entry.source
    for family in ("Cantarell", "DejaVu Sans", "Liberation Sans", "Noto Sans", "Open Sans"):
        assert family in entry.families


def test_libreoffice_bundle_never_makes_linux_abstain() -> None:
    """Distributions' LibreOffice packages bundle no fonts; gate C1 (b) may drop the entry."""
    entry = preinstalled().systems.get("libreoffice")
    if entry is not None:
        assert entry.os == "app"  # an application's bundle: no "comes with Windows" bit either
        assert {"Caladea", "Carlito"} <= set(entry.families)


def test_only_linux_entries_make_linux_sources_abstain() -> None:
    """D8 through the real consumer, with every listed name resolved to its own family.

    Each name a Linux entry lists abstains in every Linux source, naming a Linux
    system; a name only Windows, macOS or Android entries list never abstains.
    """
    from tff_catalog.corrections import abstentions, pkg_id
    from tff_catalog.records import SourceKey

    systems = preinstalled().systems
    names = sorted({n for entry in systems.values() for n in entry.families})
    fid = {n: f"family-{i}" for i, n in enumerate(names)}
    families = {pkg_id(SourceKey("font-name", n)): fid[n] for n in names}
    cfg = Corrections(
        min_exposure_days=60,
        nerd_credit=1.0,
        cjk_build_credit=1.0,
        bundle_credit=0.5,
        bundle_mode="fixed",
        width_siblings="separate",
        dependency_abstain=0.5,
        dependency_review=0.35,
        dependency_rule="top",
        dependency_alternatives="first",
    )
    found = abstentions((), {"arch": {}, "debian": {}}, preinstalled(), cfg, families=families)

    linux = {n for entry in systems.values() if entry.os == "linux" for n in entry.families}
    other = set(names) - linux
    assert other, "the non-Linux entries list families no Linux entry lists"
    assert set(found) == {"arch", "debian"}
    for source, got in found.items():
        assert set(got) == {fid[n] for n in linux}, source
        for ab in got.values():
            assert ab.why == "preinstalled"
            assert systems[ab.system].os == "linux"
    for n in ("Cascadia Code", "PT Sans", "Roboto"):
        assert n in other, n
        assert all(fid[n] not in got for got in found.values()), n


@pytest.mark.parametrize("system", sorted(preinstalled().systems))
def test_families_are_clean_sorted_and_unique(system: str) -> None:
    families = preinstalled().systems[system].families
    assert families, "an entry needs at least one family"
    assert all(_clean_name(f) for f in families)
    assert list(families) == sorted(families, key=str.casefold)
    assert len({f.casefold() for f in families}) == len(families)


@pytest.mark.parametrize("system", sorted(preinstalled().systems))
def test_only_text_families_and_no_noto_script_families(system: str) -> None:
    for family in preinstalled().systems[system].families:
        assert not re.search(r"(?i)emoji|symbol|\bmath\b|\bCJK\b", family), family
        if re.match(r"Noto (Sans|Serif)\b", family):
            assert family in NOTO_LATIN, (
                f"{family}: script-specific Noto families fail the Latin gate"
            )


@pytest.mark.parametrize("system", sorted(preinstalled().systems))
def test_notes_and_labels_are_single_clean_lines(system: str) -> None:
    entry = preinstalled().systems[system]
    for text in (entry.label, entry.note):
        assert text == text.strip()
        assert "\n" not in text
        assert "  " not in text
    assert entry.label


def test_labels_agree_with_site_package_systems() -> None:
    site = tomllib.loads((CONFIG / "site.toml").read_text(encoding="utf-8"))
    for system, entry in site.get("package_systems", {}).items():
        other = preinstalled().systems.get(system)
        if other is not None:
            assert other.label == entry["label"], system


# --- foundries.toml ------------------------------------------------------------------------


def test_m12_foundries_are_present() -> None:
    found = foundries().foundries
    assert set(M12_FOUNDRIES) <= set(found)
    for foundry_id in M12_FOUNDRIES:
        assert found[foundry_id].families, foundry_id
    assert [f.name for f in found["fontshare"].families] == ["Roundo"]


@pytest.mark.parametrize("foundry_id", sorted(foundries().foundries))
def test_families_are_sorted_unique_and_complete(foundry_id: str) -> None:
    families = foundries().foundries[foundry_id].families
    names = [f.name for f in families]
    assert names == sorted(names, key=str.casefold)
    assert len({n.casefold() for n in names}) == len(names)
    for family in families:
        assert _clean_name(family.name)
        assert family.license, family.name
        assert family.license == family.license.strip(), family.name
        for url in (family.url, family.repository):
            if url:
                parts = urlsplit(url)
                assert parts.scheme == "https", url
                assert parts.netloc, url
                assert parts.path.strip("/"), f"{family.name}: {url} is a bare host"


@pytest.mark.parametrize("foundry_id", sorted(foundries().foundries))
def test_forge_repositories_name_one_repository(foundry_id: str) -> None:
    """The links stage reads a forge URL as ``<owner>/<repo>``; an owner page is no repository."""
    for family in foundries().foundries[foundry_id].families:
        for url in (family.url, family.repository):
            parts = urlsplit(url)
            if parts.hostname in FORGES:
                segments = [s for s in parts.path.split("/") if s]
                assert len(segments) == 2, f"{family.name}: {url}"
                assert not parts.query, url
                assert not parts.fragment, url


def test_family_listed_by_two_foundries_names_one_repository() -> None:
    """Two foundries listing one family (Open Foundry's showcase) must not split its links."""
    repos: dict[str, set[str]] = defaultdict(set)
    for foundry in foundries().foundries.values():
        for family in foundry.families:
            if family.repository:
                repos[family.name.casefold()].add(family.repository.rstrip("/").casefold())
    assert {name: r for name, r in repos.items() if len(r) > 1} == {}


def test_family_urls_avoid_ai_opt_out_hosts() -> None:
    """velvetyne.fr and collletttivo.it opt out of AI agents: only the foundry root may be there."""
    for foundry_id, foundry in foundries().foundries.items():
        for family in foundry.families:
            for url in (family.url, family.repository):
                assert urlsplit(url).hostname not in AI_OPT_OUT_HOSTS, (foundry_id, url)
    assert not any(urlsplit(u).hostname in AI_OPT_OUT_HOSTS for u in _all_urls())


def test_license_strings_are_mapped_by_l1() -> None:
    """Every stated license is an L1 alias key (config/license-aliases.toml), or NOASSERTION."""
    aliases = tomllib.loads((CONFIG / "license-aliases.toml").read_text(encoding="utf-8"))
    known = {k.casefold() for k in aliases.get("aliases", {})}
    used = {f.license for foundry in foundries().foundries.values() for f in foundry.families}
    assert {s for s in used if s.casefold() not in known and s != NO_LICENSE} == set()


# --- network -------------------------------------------------------------------------------


def _all_urls() -> list[str]:
    urls = {entry.source for entry in preinstalled().systems.values()}
    for foundry in foundries().foundries.values():
        urls.add(foundry.url)
        for family in foundry.families:
            urls.add(family.url)
            if family.repository:
                urls.add(family.repository)
    return sorted(u for u in urls if urlsplit(u).hostname not in AI_OPT_OUT_HOSTS)


def _landing_problem(url: str, final_url: str) -> str | None:
    """Why a 2xx answer is still a dead link, if it is.

    GitLab sends a missing or private project to its sign-in page (302, then 200),
    and some sites send a missing page to their home page.
    """
    final = urlsplit(final_url)
    if re.search(r"(?i)sign[_-]?in|/login\b", final.path):
        return f"redirected to a sign-in page ({final_url})"
    if urlsplit(url).path.strip("/") and not final.path.strip("/"):
        return f"redirected to the home page ({final_url})"
    return None


def test_landing_problem_catches_sign_in_and_home_redirects() -> None:
    assert _landing_problem("https://gitlab.com/a/b", "https://gitlab.com/users/sign_in")
    assert _landing_problem("https://example.org/fonts/x", "https://example.org/")
    assert _landing_problem("https://github.com/a/B", "https://github.com/a/b") is None
    assert _landing_problem("https://example.org", "https://www.example.org/") is None


@pytest.mark.network
@pytest.mark.slow
def test_every_url_answers() -> None:
    """HEAD every URL (GET when HEAD is refused), one worker per host.

    Fontshare answers 200 for any ``/fonts/<slug>``, so its page proves nothing;
    Roundo's repository URL is the real check there.
    """
    from tff_catalog.fetch import Fetcher, FetchError

    by_host: dict[str, list[str]] = defaultdict(list)
    for url in _all_urls():
        by_host[urlsplit(url).hostname or ""].append(url)

    def check_host(fetcher: Fetcher, urls: list[str]) -> list[str]:
        failed = []
        for url in urls:
            try:
                result = fetcher.head(url)
                if result.status in (403, 405, 501):
                    result = fetcher.get(url, expect=range(200, 400))
            except FetchError as exc:
                failed.append(f"{url}: {exc}")
                continue
            if result.status >= 400:
                failed.append(f"{url}: HTTP {result.status}")
            elif problem := _landing_problem(url, result.final_url):
                failed.append(f"{url}: {problem}")
        return failed

    with Fetcher() as fetcher, ThreadPoolExecutor(max_workers=len(by_host)) as pool:
        results = pool.map(lambda urls: check_host(fetcher, urls), by_host.values())
        failed = [line for lines in results for line in lines]
    assert not failed, "\n".join(failed)
