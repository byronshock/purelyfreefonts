"""Stage "licenses" (milestone-1 step 6a): L1 normalisation, the D3 classes, the L2
cross-check and the gate LIC review queue.

Every fixture is synthetic (BRIEF item 12): invented families, keys and
records. The license strings are the spellings the scouts found in each source
(facts about how sources write licenses, not copied rows).
"""

import logging
import re
import tomllib
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.helpers import ROOT, synth

from tff_catalog import licenses as lic
from tff_catalog import reviews, stageio
from tff_catalog.aliases import COLUMNS
from tff_catalog.config import check_licenses
from tff_catalog.config_model import (
    AllowedLicense,
    ExcludedLicense,
    LicenseAliasesConfig,
    LicensesConfig,
    RulingLicense,
    SiteConfig,
    from_mapping,
    load_toml,
)
from tff_catalog.latin import LatinResult
from tff_catalog.names import ID_PATTERN
from tff_catalog.paths import Paths
from tff_catalog.records import LicenseFact, Observation, SourceKey, write_jsonl
from tff_catalog.reviews import Answer, Ruling
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.universe import Family, Universe

CONFIG = ROOT / "config"
REAL_CFG = from_mapping(LicensesConfig, load_toml(CONFIG / "licenses.toml"), "licenses.toml")
ALIASES = from_mapping(
    LicenseAliasesConfig, load_toml(CONFIG / "license-aliases.toml"), "license-aliases.toml"
)
TABLE = lic.AliasIndex(ALIASES.aliases)
SITE = from_mapping(SiteConfig, load_toml(CONFIG / "site.toml"), "site.toml")
SITE_CLASSES = {c.id for c in SITE.license_classes}
LISTED = {**REAL_CFG.allowed, **REAL_CFG.excluded, **REAL_CFG.ruling}
EXCEPTIONS = {k.split(" WITH ")[1] for k in LISTED if " WITH " in k}
QUESTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")  # schemas/review.schema.json

# A small config for the combination rules, independent of the real file's contents.
CFG = LicensesConfig(
    schema=1,
    allowed={
        "OFL-1.1": AllowedLicense("SIL Open Font License 1.1", "open-font", True, False),
        "MIT": AllowedLicense("MIT License", "permissive", True, False),
        "CC-BY-4.0": AllowedLicense("CC BY 4.0", "attribution", True, True),
        # Forbids redistribution: excluded since Rule 3 of 2026-09-30.
        "LicenseRef-Grant": AllowedLicense("A free-use grant", "freeware", False, False),
        "GPL-2.0-or-later WITH Font-exception-2.0": AllowedLicense(
            "GPL with the font exception", "open-font", True, False
        ),
    },
    excluded={
        "GPL-2.0-or-later": ExcludedLicense("copyleft without a font exception (D3)"),
        "CC-BY-SA-4.0": ExcludedLicense("copyleft without a font exception (D3)"),
    },
    ruling={"Bitstream-Vera": RulingLicense("Does the Vera license qualify?")},
)


def fact(source: str, raw: str, key: str = "fam", ns: str = "fs-id", **kw: object) -> LicenseFact:
    return LicenseFact(source=source, key=SourceKey(ns, key), raw=raw, **kw)  # type: ignore[arg-type]


# --- the config files -------------------------------------------------------------------------


def test_licenses_toml_is_strict_and_groups_are_site_filters() -> None:
    check_licenses(REAL_CFG)
    assert REAL_CFG.allowed
    assert REAL_CFG.excluded
    assert REAL_CFG.ruling
    for spdx, entry in REAL_CFG.allowed.items():
        assert entry.group in SITE_CLASSES, spdx
        assert entry.redistributable, f"{spdx}: a non-redistributable grant comes from a ruling"


@pytest.mark.parametrize(
    ("expr", "status", "group", "attribution"),
    [
        ("OFL-1.1", "allowed", "open-font", False),
        ("OFL-1.0", "allowed", "open-font", False),
        ("Apache-2.0", "allowed", "permissive", False),
        ("MIT", "allowed", "permissive", False),
        ("CC0-1.0", "allowed", "permissive", False),
        ("CC-BY-4.0", "allowed", "attribution", True),  # D3: qualifies with the badge
        ("CC-BY-3.0", "allowed", "attribution", True),
        ("GPL-2.0-or-later WITH Font-exception-2.0", "allowed", "open-font", False),
        ("LGPL-2.1-or-later WITH Font-exception-2.0", "allowed", "open-font", False),
        ("CC-BY-SA-4.0", "excluded", None, None),  # D3: copyleft without a font exception
        ("GPL-1.0-only", "excluded", None, None),
        ("CC-BY-ND-4.0", "excluded", None, None),  # D3: Rule 4 extended
        ("CC-BY-NC-4.0", "excluded", None, None),  # Rule 1
        ("GPL-3.0-only", "excluded", None, None),
        ("GPL-2.0-or-later", "excluded", None, None),
        ("LGPL-2.1-only", "excluded", None, None),
        ("AGPL-3.0-or-later", "excluded", None, None),
        ("AGPL-3.0-only WITH PS-or-PDF-font-exception-20170817", "excluded", None, None),  # URW
        ("LicenseRef-Microsoft-EULA", "excluded", None, None),  # MS Core Fonts EULA
        ("LicenseRef-ITF-FFL", "excluded", None, None),  # AUTHORITY Rule 4
        ("EUPL-1.2", "excluded", None, None),  # not listed
        ("MPL-2.0", "allowed", "open-font", False),  # owner ruling of 2026-09-26
        ("X11", "allowed", "permissive", False),
        ("BSL-1.0", "allowed", "permissive", False),
        ("Artistic-2.0", "allowed", "permissive", False),
        ("Artistic-1.0", "ruling", None, None),  # researched, back to the owner
        ("Bitstream-Vera", "ruling", None, None),  # gate LIC items
        ("Ubuntu-font-1.0", "ruling", None, None),
        ("WTFPL", "ruling", None, None),
        ("LPPL-1.3c", "ruling", None, None),
        ("LicenseRef-Monofur", "ruling", None, None),
        ("LicenseRef-VicFieger", "ruling", None, None),
    ],
)
def test_d3_classes(expr: str, status: str, group: str | None, attribution: bool | None) -> None:
    c = lic.classify(expr, REAL_CFG)
    assert (c.status, c.group, c.attribution_required) == (status, group, attribution)
    assert (c.status == "allowed") == (c.redistributable is True)
    if status != "allowed":
        assert c.reason


def test_excluded_reasons_name_their_rule() -> None:
    assert "Rule 4" in lic.classify("LicenseRef-ITF-FFL", REAL_CFG).reason
    assert "D3" in lic.classify("CC-BY-SA-4.0", REAL_CFG).reason
    assert "not listed" in lic.classify("EUPL-1.2", REAL_CFG).reason


def test_alias_values_are_canonical_and_listed() -> None:
    for key, value in ALIASES.aliases.items():
        assert lic.canonical(value) == value, key
        if value in EXCEPTIONS or value == lic.NOASSERTION:
            continue
        reason = lic.classify(value, REAL_CFG).reason or ""
        assert "not listed" not in reason, f"{key} = {value}: add it to licenses.toml"


def test_every_listed_license_is_recognised_by_l1() -> None:
    for spdx in LISTED:
        assert lic.normalize(spdx, "any_source", TABLE) == lic.canonical(spdx), spdx
        assert lic.normalize(spdx.lower(), "any_source", TABLE) == lic.canonical(spdx), spdx


def test_alias_keys_do_not_clash_ignoring_case() -> None:
    raw = tomllib.loads((CONFIG / "license-aliases.toml").read_text(encoding="utf-8"))["aliases"]
    by_fold: dict[str, set[str]] = {}
    for key, value in raw.items():
        by_fold.setdefault(key.casefold(), set()).add(value)
    assert {k: v for k, v in by_fold.items() if len(v) > 1} == {}


def test_every_alias_key_normalises_to_its_value_and_is_stable() -> None:
    for key, value in ALIASES.aliases.items():
        if key.endswith("*"):
            continue
        source, _, raw = key.rpartition(lic.SCOPE) if lic.SCOPE in key else ("any", "", key)
        got = lic.normalize(raw, source, TABLE)
        if "exception" in value.casefold() and " WITH " not in value:
            assert got is None, key  # an exception names no license on its own
            continue
        assert got == lic.canonical(value), key
        assert lic.normalize(got, source, TABLE) == got, key


def test_every_foundries_toml_license_is_readable() -> None:
    """config/foundries.toml states licenses in the foundries' own words (u/foundries)."""
    doc = tomllib.loads((CONFIG / "foundries.toml").read_text(encoding="utf-8"))
    raws = {f["license"] for foundry in doc["foundries"].values() for f in foundry["families"]}
    assert raws
    assert {r for r in raws if lic.normalize(r, "foundries", TABLE) is None} == set()


def test_question_ids_advice_and_watch_list_are_consistent() -> None:
    ids = [lic.license_question_id(s) for s in LISTED]
    assert len(set(ids)) == len(ids)
    assert all(QUESTION_ID.match(i) for i in ids)
    long_id = "a-family-name-that-goes-on-and-on-well-past-the-sixty-four-limit"
    qid = lic.family_question_id(long_id)
    assert QUESTION_ID.match(qid)
    assert qid == lic.family_question_id(long_id) != lic.family_question_id(long_id + "-2")
    assert lic.family_question_id("hack") == "LIC-hack"
    for spdx, (choice, group) in lic.RULING_ADVICE.items():
        assert spdx in REAL_CFG.ruling, spdx
        assert choice in lic.CHOICES
        assert group is None or group in SITE_CLASSES
    assert set(lic.DEFAULT_GROUPS.values()) <= SITE_CLASSES
    assert all(ID_PATTERN.match(f) for f in lic.WATCH)
    assert len(set(lic.SOURCE_ORDER)) == len(lic.SOURCE_ORDER)
    assert len(lic.OPTIONS) == len(lic.CHOICES) <= reviews.MAX_OPTIONS


# --- L1: normalize ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "raw", "expected"),
    [
        ("google_repo", "OFL", "OFL-1.1"),  # METADATA.pb license field and folder
        ("google_repo", "APACHE2", "Apache-2.0"),
        ("google_repo", "ufl", "Ubuntu-font-1.0"),
        ("fontsource", "mit", "MIT"),  # the API's lower-case id
        ("fontsource", "UFL-1.0", "Ubuntu-font-1.0"),
        ("fontsource", "Bitstream-Vera", "Bitstream-Vera"),
        ("nerdfonts", "OFL-1.1-RFN", "OFL-1.1"),
        ("nerdfonts", "OFL-1.1-no-RFN or LGPL-2.1-only", "LGPL-2.1-only OR OFL-1.1"),
        ("nerdfonts", "Bitstream-Vera AND MIT", "Bitstream-Vera AND MIT"),
        ("nerdfonts", "MIT OR OFL-1.1-no-RFN", "MIT OR OFL-1.1"),
        ("nerdfonts", "LicenseRef-UbuntuFont", "Ubuntu-font-1.0"),
        ("nerdfonts", "CC-BY-SA-4.0", "CC-BY-SA-4.0"),
        ("fontist", "LicenseRef-Microsoft-fontpack-19980728", "LicenseRef-Microsoft-EULA"),
        ("fontist", "LicenseRef-PublicDomain", "LicenseRef-PublicDomain"),
        ("fontist", "LPPL-1.3c", "LPPL-1.3c"),
        ("debian_copyright", "Expat", "MIT"),
        ("debian_copyright", "bitstream-vera", "Bitstream-Vera"),
        ("debian_copyright", "BitstreamVera", "Bitstream-Vera"),
        ("debian_copyright", "SIL-OFL-1.1", "OFL-1.1"),
        ("debian_copyright", "GPL-2+", "GPL-2.0-or-later"),
        (
            "debian_copyright",
            "GPL-2+ with Font exception",
            "GPL-2.0-or-later WITH Font-exception-2.0",
        ),
        (
            "debian_copyright",
            "AGPL-3 with Font exception",
            "AGPL-3.0-only WITH PS-or-PDF-font-exception-20170817",
        ),
        ("arch_repos", "custom:OFL", "OFL-1.1"),
        ("arch_repos", "LicenseRef-OFL", "OFL-1.1"),
        ("arch_repos", "OFL-1.0-RFN", "OFL-1.0"),
        ("arch_repos", "custom:Arphic_Public_License", "Arphic-1999"),
        ("arch_repos", "custom", "NOASSERTION"),
        ("foundries", "itf_ffl", "LicenseRef-ITF-FFL"),  # Fontshare's license_type
        ("foundries", "sil_ofl", "OFL-1.1"),
        ("synth", "SIL  Open Font License,\tVersion 1.1", "OFL-1.1"),  # whitespace runs
        ("synth", "  OFL-1.1  ", "OFL-1.1"),
    ],
)
def test_normalize_source_spellings(source: str, raw: str, expected: str) -> None:
    assert lic.normalize(raw, source, TABLE) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "custom:Fjord", "OFL-1.1 AND", "(MIT", "MIT)", "AND MIT", "OFL-1.1 WITH"]
)
def test_normalize_unknown_or_malformed_is_none(raw: str) -> None:
    assert lic.normalize(raw, "arch_repos", TABLE) is None


@pytest.mark.parametrize(
    "raw",
    ["Font exception", "font-exception-2.0", "Font-exception-2.0 AND MIT", "MIT OR Font exception"],
)
def test_normalize_an_exception_on_its_own_is_unknown(raw: str) -> None:
    assert lic.normalize(raw, "debian_copyright", TABLE) is None
    assert lic.normalize("GPL-3+ with Font exception", "debian_copyright", TABLE) == (
        "GPL-3.0-or-later WITH Font-exception-2.0"
    )


def test_normalize_canonical_order_and_noassertion() -> None:
    assert lic.normalize("MIT AND Bitstream-Vera AND mit", "x", TABLE) == "Bitstream-Vera AND MIT"
    assert (
        lic.normalize("CC-BY-4.0 and (OFL or MIT)", "x", TABLE) == "(MIT OR OFL-1.1) AND CC-BY-4.0"
    )
    assert lic.normalize("MIT AND custom", "x", TABLE) == lic.NOASSERTION  # AND: unknown part
    assert lic.normalize("MIT OR custom", "x", TABLE) == "MIT"  # OR: the readable branch
    assert lic.normalize("NOASSERTION", "x", TABLE) == lic.NOASSERTION


def test_normalize_scoped_and_prefix_keys() -> None:
    table = {
        "Vendor": "Apache-2.0",
        "fontist::Vendor": "MIT",
        "LicenseRef-Acme-*": "LicenseRef-Grant",
        "LicenseRef-Acme-Pro-*": "CC-BY-SA-4.0",
        "MIT": "MIT",
    }
    assert lic.normalize("Vendor", "fontist", table) == "MIT"
    assert lic.normalize("vendor", "fontist", table) == "MIT"
    assert lic.normalize("Vendor", "arch_repos", table) == "Apache-2.0"
    assert lic.normalize("LicenseRef-Acme-2019", "x", table) == "LicenseRef-Grant"
    assert lic.normalize("LicenseRef-Acme-Pro-7", "x", table) == "CC-BY-SA-4.0"  # longest prefix
    assert (
        lic.normalize("Vendor OR LicenseRef-Acme-1", "x", table) == "Apache-2.0 OR LicenseRef-Grant"
    )
    # A prefix key names one license; it never swallows an expression that starts like it.
    assert lic.normalize("LicenseRef-Acme-1 and MIT", "x", table) == "LicenseRef-Grant AND MIT"
    with pytest.raises(ValueError, match=r"license-aliases\.toml"):
        lic.AliasIndex({"bad": "MIT AND"})


def test_synthetic_helper_licenses_normalise() -> None:
    for fam in synth.families():
        f = synth.license_fact(fam)
        assert lic.normalize(f.raw, f.source, TABLE) in {"OFL-1.1", "Apache-2.0"}


_IDS = sorted(i for i in LISTED if " " not in i)


@given(st.lists(st.sampled_from(_IDS), min_size=1, max_size=4), st.sampled_from(["AND", "OR"]))
def test_normalize_ignores_operand_order_and_is_idempotent(ids: list[str], op: str) -> None:
    forward = lic.normalize(f" {op} ".join(ids), "x", TABLE)
    backward = lic.normalize(f" {op.lower()} ".join(reversed(ids)), "x", TABLE)
    assert forward == backward
    assert forward is not None
    assert lic.normalize(forward, "x", TABLE) == forward
    lic.classify(forward, REAL_CFG)  # every canonical result classifies


# --- classify ---------------------------------------------------------------------------------


def test_classify_leaves() -> None:
    assert lic.classify("OFL-1.1", CFG) == lic.LicenseClass(
        "OFL-1.1", "allowed", "open-font", True, False
    )
    assert lic.classify("CC-BY-SA-4.0", CFG).status == "excluded"
    assert lic.classify("Bitstream-Vera", CFG).status == "ruling"
    for expr in ("Zlib", "NOASSERTION", "NONE"):
        c = lic.classify(expr, CFG)
        assert (c.status, c.redistributable) == ("excluded", None), expr


def test_classify_or_takes_the_best_branch() -> None:
    c = lic.classify("GPL-2.0-or-later OR OFL-1.1", CFG)
    assert (c.spdx, c.status, c.group) == ("OFL-1.1", "allowed", "open-font")
    assert lic.classify("Bitstream-Vera OR CC-BY-SA-4.0", CFG).status == "ruling"
    assert lic.classify("CC-BY-4.0 OR MIT", CFG).spdx == "MIT"  # no attribution needed
    assert lic.classify("LicenseRef-Grant OR CC-BY-4.0", CFG).spdx == "CC-BY-4.0"  # redistributable


def test_classify_ties_prefer_the_first_group_and_no_exception() -> None:
    assert tuple(c.id for c in SITE.license_classes) == lic.GROUP_ORDER
    unifont = "GPL-2.0-or-later WITH Font-exception-2.0 OR OFL-1.1"
    assert lic.classify(unifont, REAL_CFG).spdx == "OFL-1.1"
    assert lic.classify("MIT OR OFL-1.1", REAL_CFG).spdx == "OFL-1.1"
    assert lic.classify("Apache-2.0 AND OFL-1.1", REAL_CFG).group == "open-font"
    assert lic.classify("Apache-2.0 OR MIT", REAL_CFG).spdx == "Apache-2.0"  # then by id
    # Attribution still comes first.
    assert lic.classify("OFL-1.1 AND CC-BY-4.0", CFG).group == "attribution"


def test_classify_and_needs_every_part() -> None:
    c = lic.classify("MIT AND CC-BY-4.0", CFG)
    assert (c.status, c.group, c.redistributable, c.attribution_required) == (
        "allowed",
        "attribution",
        True,
        True,
    )
    c = lic.classify("MIT AND LicenseRef-Grant", CFG)  # Rule 3: the grant forbids redistribution
    assert (c.status, c.group, c.redistributable) == ("excluded", None, None)
    assert "Rule 3" in c.reason
    c = lic.classify("MIT AND GPL-2.0-or-later", CFG)
    assert c.status == "excluded"
    assert "GPL-2.0-or-later" in c.reason
    c = lic.classify("Bitstream-Vera AND MIT", CFG)
    assert (c.spdx, c.status) == ("Bitstream-Vera AND MIT", "ruling")


def test_classify_with_exceptions() -> None:
    assert lic.classify("GPL-2.0-or-later WITH Font-exception-2.0", CFG).status == "allowed"
    c = lic.classify("MIT WITH Some-exception", CFG)  # an exception only adds permissions
    assert (c.spdx, c.status) == ("MIT WITH Some-exception", "allowed")
    c = lic.classify("GPL-2.0-or-later WITH Other-exception", CFG)
    assert c.status == "excluded"
    assert "Other-exception is not listed" in c.reason


@pytest.mark.parametrize(
    ("expr", "match"),
    [
        ("", "empty"),
        ("MIT AND", "ends too early"),
        ("(MIT", "ends too early"),
        ("(MIT OR OFL-1.1))", "unexpected"),
        ("SIL Open Font License", "not an SPDX id"),
        ("A WITH B WITH C", "unexpected"),
    ],
)
def test_classify_rejects_malformed_expressions(expr: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        lic.classify(expr, CFG)


# --- L2: cross_check --------------------------------------------------------------------------


def test_cross_check_agreement() -> None:
    v = lic.cross_check(
        "aster-sans",
        [
            fact("fontsource", "OFL-1.1"),
            fact("google_repo", "OFL"),
            fact("nerdfonts", "OFL-1.1-RFN"),
        ],
        TABLE,
        CFG,
    )
    assert (v.spdx, v.license.status, v.preview_ok, v.queue) == ("OFL-1.1", "allowed", True, None)
    assert v.seen == (
        ("fontsource", "OFL-1.1"),
        ("google_repo", "OFL-1.1"),
        ("nerdfonts", "OFL-1.1"),
    )


def test_cross_check_disagreement_takes_the_first_source_and_queues() -> None:
    facts = [fact("arch_repos", "Apache"), fact("google_repo", "OFL")]  # a folder move
    v = lic.cross_check("birch-mono", facts, TABLE, REAL_CFG)
    assert (v.spdx, v.license.status) == ("OFL-1.1", "allowed")
    assert v.queue == "disagreement: google_repo OFL-1.1; arch_repos Apache-2.0"
    v = lic.cross_check(
        "x", [fact("debian_copyright", "CC-BY-SA-4.0"), fact("fontsource", "MIT")], TABLE, CFG
    )
    assert (v.spdx, v.license.status) == ("MIT", "allowed")
    assert v.queue.startswith("disagreement")


def test_cross_check_all_excluded_disagreement_needs_no_ruling() -> None:
    facts = [
        fact("debian_copyright", "AGPL-3 with Font exception"),
        fact("debian_copyright", "CC-BY-4.0"),
        fact("fontist", "AGPL-3.0-or-later"),
    ]  # URW Base 35: excluded under D3 whichever reading is right
    v = lic.cross_check("kestrel-sans", facts, TABLE, REAL_CFG)
    assert (v.license.status, v.preview_ok, v.queue) == ("excluded", False, None)
    assert v.spdx == "AGPL-3.0-or-later"


def test_cross_check_no_facts_is_excluded_without_queue() -> None:
    v = lic.cross_check("garnet-sans", [], TABLE, CFG)
    assert (v.spdx, v.license.status, v.license.reason) == (None, "excluded", lic.NO_LICENSE_REASON)
    assert (v.preview_ok, v.queue, v.seen) == (False, None, ())


def test_cross_check_noassertion_goes_to_the_owner() -> None:
    v = lic.cross_check("ember", [fact("arch_repos", "custom")], TABLE, CFG)
    assert (v.spdx, v.license.spdx, v.license.status) == (None, "NOASSERTION", "ruling")
    assert v.queue.startswith("noassertion:")


def test_cross_check_unreadable_strings_are_reported() -> None:
    v = lic.cross_check("fjord", [fact("arch_repos", "custom:Fjord")], TABLE, CFG)
    assert v.license.status == "ruling"
    assert v.queue == "unreadable: arch_repos 'custom:Fjord'"
    both = [fact("arch_repos", "custom:Fjord"), fact("fontsource", "MIT")]
    v = lic.cross_check("fjord", both, TABLE, CFG)
    assert (v.spdx, v.license.status) == ("MIT", "allowed")
    assert v.seen == (("arch_repos", None), ("fontsource", "MIT"))
    assert "unreadable" in v.queue


def test_cross_check_ands_several_facts_from_one_source() -> None:
    facts = [fact("debian_copyright", "BitstreamVera"), fact("debian_copyright", "Expat")]
    v = lic.cross_check("x", [*facts, fact("nerdfonts", "Bitstream-Vera AND MIT")], TABLE, CFG)
    assert v.spdx == "Bitstream-Vera AND MIT"
    assert v.queue == "license: Bitstream-Vera needs a ruling"


def test_cross_check_noassertion_in_one_source_poisons_its_and() -> None:
    both = [fact("arch_repos", "MIT"), fact("arch_repos", "custom")]  # licenses=(MIT custom)
    v = lic.cross_check("x", both, TABLE, CFG)
    assert (v.spdx, v.license.status) == (None, "ruling")  # like "MIT AND custom"
    assert v.queue.startswith("noassertion")
    assert lic.normalize("MIT AND custom", "arch_repos", TABLE) == lic.NOASSERTION
    v = lic.cross_check("x", [*both, fact("google_repo", "OFL")], TABLE, CFG)
    assert (v.spdx, v.queue) == ("OFL-1.1", None)  # Arch states nothing, so nothing disagrees


def test_cross_check_prefers_a_readable_spdx_field() -> None:
    v = lic.cross_check("x", [fact("fontsource", "Open Font", spdx="OFL-1.1-no-RFN")], TABLE, CFG)
    assert v.spdx == "OFL-1.1"
    v = lic.cross_check("x", [fact("fontsource", "Expat", spdx="Nonsense-9")], TABLE, CFG)
    assert v.spdx == "MIT"


def test_cross_check_watch_list() -> None:
    v = lic.cross_check("roboto-mono", [fact("google_repo", "OFL")], TABLE, CFG)
    assert v.license.status == "allowed"
    assert v.queue == f"watch: {lic.WATCH['roboto-mono']}"
    for variant, watched in (
        ("redaction-35", "redaction"),
        ("opendyslexic-mono", "opendyslexic"),
        ("unifont-upper", "unifont"),
        ("hack-2", "hack"),
    ):
        assert lic.watch_note(variant) == lic.WATCH[watched], variant
    for other in ("hackney", "roboto", "roboto-slab", "cascadia"):
        assert lic.watch_note(other) is None, other


# --- rulings on licenses ----------------------------------------------------------------------


def _answer(qid: str, choice: str, **values: object) -> Answer:
    return Answer(
        qid,
        ruling=f"ruled {choice}",
        reason="test",
        choice=choice,
        recommended=choice == "a",
        values=tuple(sorted(values.items())),  # type: ignore[arg-type]
    )


def test_apply_license_rulings() -> None:
    day = date(2026, 10, 4)
    qid = lic.license_question_id("Bitstream-Vera")
    cfg = lic.apply_license_rulings(CFG, {qid: (_answer(qid, "a"), day)})
    c = lic.classify("Bitstream-Vera", cfg)
    assert (c.status, c.redistributable, c.group) == ("allowed", True, "open-font")
    assert "Bitstream-Vera" not in cfg.ruling
    # (b), free to use but not redistributable, excludes since Rule 3 of 2026-09-30.
    cfg = lic.apply_license_rulings(CFG, {qid: (_answer(qid, "b"), day)})
    c = lic.classify("Bitstream-Vera", cfg)
    assert c.status == "excluded"
    assert "Rule 3" in c.reason
    assert "Bitstream-Vera" not in cfg.ruling
    cfg = lic.apply_license_rulings(CFG, {qid: (_answer(qid, "c"), day)})
    assert "owner ruling" in lic.classify("Bitstream-Vera", cfg).reason
    cfg = lic.apply_license_rulings(CFG, {qid: (_answer(qid, "d"), day)})
    assert cfg == CFG
    mit = lic.license_question_id("MIT")
    cfg = lic.apply_license_rulings(CFG, {mit: (_answer(mit, "a", group="freeware"), day)})
    assert (cfg.allowed["MIT"].name, cfg.allowed["MIT"].group) == ("MIT License", "freeware")
    cfg = lic.apply_license_rulings(CFG, {mit: (_answer(mit, "b"), day)})
    assert "MIT" in cfg.excluded
    assert "MIT" not in cfg.allowed
    with pytest.raises(lic.RulingError):
        lic.apply_license_rulings(CFG, {qid: (_answer(qid, "e"), day)})
    bad = _answer(qid, "a", attribution_required="yes")
    with pytest.raises(lic.RulingError, match="true or false"):
        lic.apply_license_rulings(CFG, {qid: (bad, day)})


# --- the stage --------------------------------------------------------------------------------

FAMILIES = {  # id: (name, keys, drop)
    "aster-sans": ("Aster Sans", [("gf-dir", "ofl/astersans"), ("fs-id", "aster-sans")], None),
    "birch-mono": ("Birch Mono", [("gf-dir", "ofl/birchmono")], None),
    "cobalt-serif": ("Cobalt Serif", [("fs-id", "cobalt-serif")], None),
    "dune-display": ("Dune Display", [("nerd-folder", "Dune")], None),
    "ember-grotesk": ("Ember Grotesk", [("brew-cask", "font-ember-grotesk")], None),
    "fjord-slab": ("Fjord Slab", [("brew-cask", "font-fjord-slab")], None),
    "garnet-sans": ("Garnet Sans", [("brew-cask", "font-garnet-sans")], None),
    "hack": ("Hack", [("nerd-folder", "Hack")], None),
    "heath-mono": ("Heath Mono", [("fontist-formula", "heath_mono")], None),
    "juniper-sans": (
        "Juniper Sans",
        [("fs-id", "juniper-sans"), ("fontist-formula", "juniper")],
        None,
    ),
    "kestrel-sans": ("Kestrel Sans", [("fontist-formula", "kestrel")], None),
    "synth-icons": ("Synth Icons", [("fs-id", "synth-icons")], "icon"),
}
LATIN_FAIL = {"juniper-sans"}
ALIAS_ROWS = [  # alias, ns, family_id, relation, detail
    ("ttf-birch-mono", "arch-pkg", "birch-mono", "package", ""),
    ("ttf-ember", "arch-pkg", "ember-grotesk", "package", ""),
    ("ttf-fjord", "arch-pkg", "fjord-slab", "package", ""),
    ("fonts-hack", "deb-src", "hack", "package", ""),
    ("fonts-kestrel", "deb-src", "kestrel-sans", "package", ""),
    ("ttf-aster-nerd", "arch-pkg", "aster-sans", "build", "nerd"),  # a build's own license
]
FACTS = {
    "google_repo": [
        fact("google_repo", "OFL", "ofl/astersans", "gf-dir"),
        fact("google_repo", "OFL", "ofl/birchmono", "gf-dir"),
    ],
    "fontsource": [
        fact("fontsource", "OFL-1.1", "aster-sans"),
        fact("fontsource", "Bitstream-Vera", "cobalt-serif"),
        fact("fontsource", "OFL-1.1", "juniper-sans"),
        fact("fontsource", "CC-BY-SA-4.0", "synth-icons"),
        fact("fontsource", "MIT", "not-in-universe"),
    ],
    "nerdfonts": [
        fact("nerdfonts", "Bitstream-Vera AND MIT", "Dune", "nerd-folder"),
        fact("nerdfonts", "Bitstream-Vera AND MIT", "Hack", "nerd-folder"),
    ],
    "fontist": [
        fact("fontist", "LicenseRef-Microsoft-fontpack-19980728", "heath_mono", "fontist-formula"),
        fact("fontist", "AGPL-3.0-or-later", "kestrel", "fontist-formula"),
        fact("fontist", "Apache-2.0", "juniper", "fontist-formula"),  # disagrees, but not Latin
    ],
    "debian_copyright": [
        fact("debian_copyright", "BitstreamVera", "fonts-hack", "deb-src"),
        fact("debian_copyright", "Expat", "fonts-hack", "deb-src"),
        fact("debian_copyright", "AGPL-3 with Font exception", "fonts-kestrel", "deb-src"),
    ],
    "arch_repos": [
        fact("arch_repos", "Apache", "ttf-birch-mono", "arch-pkg"),
        fact("arch_repos", "custom", "ttf-ember", "arch-pkg"),
        fact("arch_repos", "custom:Fjord", "ttf-fjord", "arch-pkg"),
        fact("arch_repos", "CC-BY-SA-4.0", "ttf-aster-nerd", "arch-pkg"),
    ],
}


def _write_tree(root: Path) -> Paths:
    paths = Paths.for_root(root)
    families = {
        fid: Family(
            id=fid,
            family=name,
            keys=tuple(sorted(SourceKey(ns, k) for ns, k in keys)),
            sources=("synth",),
            first_seen=date(2026, 9, 3),
            minted_from=name,
            drop=drop,
        )
        for fid, (name, keys, drop) in FAMILIES.items()
    }
    stageio.dump_stage(paths, "universe", Universe(families=families, unmapped=()))
    passed = LatinResult(latin=True, basis="glyph_test", coverage="extended")
    failed = LatinResult(latin=False, basis=None, coverage=None, reason="share")
    latin = {f: failed if f in LATIN_FAIL else passed for f in FAMILIES if f != "synth-icons"}
    stageio.dump_stage(paths, "latin", latin)
    share = Observation(
        source="arch_repos",
        series="2026-08",
        key=SourceKey("arch-pkg", "ttf-birch-mono"),
        value=0.01,
        unit="share",
        start=date(2026, 8, 1),
        end=date(2026, 8, 31),
    )
    for source, facts in FACTS.items():
        rows = [*facts, share] if source == "arch_repos" else facts
        write_jsonl(rows, paths.records / f"{source}.jsonl")
    baseline = fact("github_releases", "CC-BY-SA-4.0", "aster-sans")  # a baseline file: skipped
    write_jsonl([baseline], paths.records / "fontsource@2026-09-03.jsonl")
    lines = [",".join(COLUMNS)]
    lines += [
        f"{a},{ns},{fid},{rel},{det},synth,2026-09-03,test" for a, ns, fid, rel, det in ALIAS_ROWS
    ]
    paths.aliases_csv.parent.mkdir(parents=True, exist_ok=True)
    paths.aliases_csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return paths


@pytest.fixture
def stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A synthetic build tree, a stage context and a settable list of gate LIC rulings."""
    paths = _write_tree(tmp_path)
    config = SimpleNamespace(licenses=REAL_CFG, license_aliases=ALIASES, site=SITE)
    ctx = StageContext(
        paths, config, State(), date(2026, 10, 3), None, None, logging.getLogger("t")
    )  # type: ignore[arg-type]
    rulings: list[Ruling] = []

    def load_rulings(p: Paths, gate: str | None = None) -> list[Ruling]:
        assert (p, gate) == (paths, "LIC")
        return list(rulings)

    monkeypatch.setattr(reviews, "load_rulings", load_rulings)
    return SimpleNamespace(paths=paths, ctx=ctx, rulings=rulings)


def _verdicts(paths: Paths) -> dict[str, lic.Verdict]:
    return stageio.load_stage(paths, "licenses")


def test_stage_classifies_every_eligible_family(stage) -> None:
    lic.run(stage.ctx)
    v = _verdicts(stage.paths)
    assert sorted(v) == sorted(f for f, (_, _, drop) in FAMILIES.items() if drop is None)
    assert all(x.license is not None for x in v.values())  # step 6a: every candidate has a class
    got = {f: (x.spdx, x.license.status, x.preview_ok) for f, x in v.items()}
    assert got == {
        "aster-sans": ("OFL-1.1", "allowed", True),
        "birch-mono": ("OFL-1.1", "allowed", True),
        "cobalt-serif": ("Bitstream-Vera", "ruling", False),
        "dune-display": ("Bitstream-Vera AND MIT", "ruling", False),
        "ember-grotesk": (None, "ruling", False),
        "fjord-slab": (None, "ruling", False),
        "garnet-sans": (None, "excluded", False),
        "hack": ("Bitstream-Vera AND MIT", "ruling", False),
        "heath-mono": ("LicenseRef-Microsoft-EULA", "excluded", False),
        "juniper-sans": ("OFL-1.1", "allowed", True),
        "kestrel-sans": ("AGPL-3.0-or-later", "excluded", False),
    }
    # The build's CC-BY-SA and the baseline file never reach Aster Sans.
    assert v["aster-sans"].seen == (("fontsource", "OFL-1.1"), ("google_repo", "OFL-1.1"))
    assert v["birch-mono"].queue.startswith("disagreement")
    assert v["kestrel-sans"].queue is None


def test_stage_queue(stage, capsys: pytest.CaptureFixture[str]) -> None:
    lic.run(stage.ctx)
    queue = lic.load_queue(stage.paths)
    got = [(i.id, i.kind, i.families) for i in queue.items]
    alias = next(i for i in queue.items if i.kind == "alias")
    assert got == [
        ("LIC-birch-mono", "disagreement", ("birch-mono",)),
        ("LIC-ember-grotesk", "noassertion", ("ember-grotesk",)),
        ("LIC-hack", "watch", ("hack",)),
        ("LIC-spdx-bitstream-vera", "license", ("cobalt-serif", "dune-display", "hack")),
        (alias.id, "alias", ("fjord-slab",)),
    ]
    assert alias.subject == "arch_repos: custom:Fjord"
    assert alias.question is None
    assert "license-aliases.toml" in alias.detail
    rec = {q.id: q.recommended for q in lic.questions(stage.paths)}
    assert rec == {
        "LIC-birch-mono": 0,
        "LIC-ember-grotesk": 3,
        "LIC-hack": 0,
        "LIC-spdx-bitstream-vera": 0,
    }
    for q in lic.questions(stage.paths):
        assert (q.gate, q.options) == ("LIC", lic.OPTIONS)
        assert QUESTION_ID.match(q.id)
    assert queue.counts == {
        "candidates": 10,  # 11 eligible, one fails the Latin gate
        "allowed": 2,
        "excluded": 3,
        "ruling": 5,
        "no_license": 1,
        "unmapped_facts": 2,  # the build package and a key outside the universe
    }
    assert queue.no_license == ("garnet-sans",)
    assert lic.cmd_queue(stage.ctx, count=True) == 0
    assert capsys.readouterr().out == "5\n"
    assert lic.cmd_queue(stage.ctx) == 0
    text = capsys.readouterr().out
    assert "LIC-hack  [watch]  hack" in text
    assert "(a) Qualifies and redistributable (rec)" in text


def test_stage_is_deterministic(stage) -> None:
    files = (stageio.stage_path(stage.paths, "licenses"), lic.queue_path(stage.paths))
    lic.run(stage.ctx)
    first = [p.read_bytes() for p in files]
    lic.run(stage.ctx)
    assert [p.read_bytes() for p in files] == first


def test_stage_applies_owner_rulings(stage) -> None:
    lic.run(stage.ctx)
    before = len(lic.load_queue(stage.paths).items)
    stage.rulings.append(
        Ruling(
            "LIC",
            date(2026, 10, 4),
            (
                _answer("LIC-birch-mono", "a", spdx="OFL-1.1"),
                _answer("LIC-spdx-bitstream-vera", "a"),
                _answer("LIC-ember-grotesk", "c"),
                _answer("LIC-hack", "d"),
            ),
        )
    )
    lic.run(stage.ctx)
    v = _verdicts(stage.paths)
    assert (v["birch-mono"].license.status, v["birch-mono"].queue) == ("allowed", None)
    assert "owner ruling LIC-birch-mono (2026-10-04)" in v["birch-mono"].license.reason
    for fid in ("cobalt-serif", "dune-display"):
        c = v[fid].license
        assert (c.status, c.group, c.redistributable, v[fid].preview_ok) == (
            "allowed",
            "open-font",
            True,
            True,
        )
    assert v["ember-grotesk"].license.status == "excluded"
    assert v["hack"].license.status == "allowed"  # through the license ruling
    assert "research: owner ruling LIC-hack" in v["hack"].queue  # "d" keeps it queued
    queue = lic.load_queue(stage.paths)
    assert [i.id for i in queue.items if i.question] == ["LIC-hack"]
    assert len(queue.items) == before - 3
    # A later ruling overrides an earlier one.
    stage.rulings.append(
        Ruling(
            "LIC",
            date(2026, 10, 5),
            (_answer("LIC-ember-grotesk", "b", spdx="LicenseRef-Freeware"),),
        )
    )
    lic.run(stage.ctx)
    c = _verdicts(stage.paths)["ember-grotesk"].license
    assert c.status == "excluded"  # (b) excludes since 2026-09-30
    assert "Rule 3" in c.reason


@pytest.mark.parametrize(
    ("answer", "match"),
    [
        (_answer("LIC-ember-grotesk", "a"), "needs spdx"),  # the sources name no license
        (_answer("LIC-birch-mono", "a", spdx="MIT AND"), "spdx"),
        (_answer("LIC-birch-mono", "x"), "choice"),
        (_answer("LIC-birch-mono", "a", group="no-such-group"), "site.toml"),
        (_answer("LIC-birch-mono", "a", attribution_required=1), "true or false"),
        # Microsoft's EULA is excluded: letting the font in must say under what license.
        (_answer("LIC-heath-mono", "a"), "needs spdx"),
    ],
)
def test_stage_rejects_bad_rulings(stage, answer: Answer, match: str) -> None:
    stage.rulings.append(Ruling("LIC", date(2026, 10, 4), (answer,)))
    with pytest.raises(lic.RulingError, match=match):
        lic.run(stage.ctx)


def test_stage_needs_its_inputs(tmp_path: Path, stage, capsys: pytest.CaptureFixture[str]) -> None:
    bare = replace(stage.ctx, paths=Paths.for_root(tmp_path / "empty"))
    assert lic.cmd_queue(bare) == 1
    assert "run `tff-catalog licenses`" in capsys.readouterr().err
    with pytest.raises(FileNotFoundError, match="universe"):
        lic.run(bare)
    stageio.stage_path(stage.paths, "latin").unlink()  # before stage "latin": every family counts
    lic.run(stage.ctx)
    assert lic.load_queue(stage.paths).counts["candidates"] == 11


def _queued(paths: Paths) -> set[str]:
    return {f for item in lic.load_queue(paths).items for f in item.families}


def _flagged_candidates(paths: Paths) -> set[str]:
    return {
        f
        for f, v in _verdicts(paths).items()
        if v.queue is not None and f not in LATIN_FAIL and FAMILIES[f][2] is None
    }


def test_stage_every_flagged_candidate_is_queued(stage) -> None:
    """validate.check_license_queue: a candidate verdict with a reason is in the queue."""
    lic.run(stage.ctx)
    assert _flagged_candidates(stage.paths) <= _queued(stage.paths)
    stage.rulings.append(
        Ruling(
            "LIC",
            date(2026, 10, 4),
            (
                _answer("LIC-aster-sans", "d"),  # no other reason to ask about it
                _answer("LIC-spdx-bitstream-vera", "d"),
            ),
        )
    )
    lic.run(stage.ctx)
    assert _flagged_candidates(stage.paths) <= _queued(stage.paths)
    queue = lic.load_queue(stage.paths)
    research = next(i for i in queue.items if i.id == "LIC-aster-sans")
    assert (research.kind, research.question.recommended) == ("research", 0)
    vera = next(i for i in queue.items if i.id == "LIC-spdx-bitstream-vera")
    assert "research requested 2026-10-04" in vera.detail


def test_stage_family_ruling_with_spdx_lets_an_excluded_reading_in(stage) -> None:
    stage.rulings.append(
        Ruling(
            "LIC",
            date(2026, 10, 4),
            (_answer("LIC-heath-mono", "a", spdx="LicenseRef-Grant", group="freeware"),),
        )
    )
    lic.run(stage.ctx)
    v = _verdicts(stage.paths)["heath-mono"]
    assert (v.spdx, v.license.status, v.license.group, v.preview_ok) == (
        "LicenseRef-Grant",
        "allowed",
        "freeware",
        True,
    )


def test_stage_family_ruling_b_excludes_under_rule_3(stage) -> None:
    # Owner ruling of 2026-09-30 (redistributable_only): free to use but not
    # redistributable no longer qualifies.
    stage.rulings.append(
        Ruling("LIC", date(2026, 10, 4), (_answer("LIC-heath-mono", "b", spdx="LicenseRef-Grant"),))
    )
    lic.run(stage.ctx)
    v = _verdicts(stage.paths)["heath-mono"]
    assert (v.license.status, v.preview_ok) == ("excluded", False)
    assert "Rule 3" in v.license.reason


def test_stage_question_asks_for_spdx_when_no_license_is_named(stage) -> None:
    lic.run(stage.ctx)
    by_id = {q.id: q.text for q in lic.questions(stage.paths)}
    assert "must name the license it is under (spdx)" in by_id["LIC-ember-grotesk"]
    assert "spdx" not in by_id["LIC-birch-mono"]


def test_stage_logs_rulings_that_match_nothing(stage, caplog: pytest.LogCaptureFixture) -> None:
    stage.rulings.append(
        Ruling("LIC", date(2026, 10, 4), (_answer("LIC-no-such-family", "a", spdx="MIT"),))
    )
    with caplog.at_level(logging.WARNING):
        lic.run(stage.ctx)
    assert "LIC-no-such-family" in caplog.text


def test_facts_on_a_key_two_families_share_after_folding_reach_neither() -> None:
    def family(fid: str, key: str) -> Family:
        return Family(fid, fid, (SourceKey("fs-id", key),), ("synth",), date(2026, 9, 3), fid)

    universe = Universe(
        families={"lark": family("lark", "Lark"), "lark-2": family("lark-2", "lark")},
        unmapped=(),
    )
    facts = [
        fact("fontsource", "MIT", "Lark"),  # exact: lark
        fact("fontsource", "OFL-1.1", "LARK"),  # folds onto both: ambiguous
    ]
    checks, unmapped = lic.classify_all(universe, facts, [], TABLE, CFG, {})
    assert (checks["lark"].verdict.spdx, checks["lark-2"].verdict.spdx, unmapped) == (
        "MIT",
        None,
        1,
    )


def test_a_formula_license_reaches_every_family_the_formula_ships() -> None:
    """A several-families key is in no family's keys, but its license is theirs."""
    formula = SourceKey("fontist-formula", "opal")

    def family(fid: str, name: str) -> Family:
        return Family(fid, name, (), ("fontist",), date(2026, 9, 3), fid, shared=((formula, name),))

    universe = Universe(
        families={
            "opal-sans": family("opal-sans", "Opal Sans"),
            "opal-serif": family("opal-serif", "Opal Serif"),
        },
        unmapped=(),
    )
    facts = [LicenseFact(source="fontist", key=formula, raw="OFL-1.1", spdx="OFL-1.1")]
    checks, unmapped = lic.classify_all(universe, facts, [], TABLE, CFG, {})
    assert unmapped == 0
    assert checks["opal-sans"].verdict.spdx == checks["opal-serif"].verdict.spdx == "OFL-1.1"


def _write_rulings(paths: Paths, day: str, body: str) -> None:
    directory = reviews.gate_dir(paths, "LIC")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{day}.toml").write_text(body, encoding="utf-8")


def test_stage_reads_real_rulings_files_and_feeds_gate_questions(tmp_path: Path) -> None:
    """The real reviews module: schema-checked files in data/reviews/licenses/, and
    reviews.questions(paths, "LIC") asking exactly the open queue items."""
    paths = _write_tree(tmp_path)
    config = SimpleNamespace(licenses=REAL_CFG, license_aliases=ALIASES, site=SITE)
    ctx = StageContext(
        paths, config, State(), date(2026, 10, 3), None, None, logging.getLogger("t")
    )  # type: ignore[arg-type]
    lic.run(ctx)
    asked = [q.id for q in reviews.questions(paths, "LIC")]
    assert asked == [q.id for q in lic.questions(paths)]
    _write_rulings(
        paths,
        "2026-10-04",
        """# Owner rulings, gate LIC (test).

[LIC-birch-mono]
choice = "a"
recommended = true
ruling = "Qualifies and redistributable"
reason = "The google/fonts folder moved to ofl/."
spdx = "OFL-1.1"

[LIC-spdx-bitstream-vera]
choice = "a"
recommended = true
ruling = "Qualifies and redistributable"
reason = "Test."

[LIC-hack]
choice = "d"
recommended = false
ruling = "Research more"
reason = "Test."
""",
    )
    lic.run(ctx)
    v = _verdicts(paths)
    assert v["birch-mono"].license.status == "allowed"
    assert v["cobalt-serif"].license.status == "allowed"
    open_ids = [q.id for q in reviews.questions(paths, "LIC")]
    assert open_ids == [q.id for q in lic.questions(paths)]
    assert "LIC-hack" in open_ids  # (d) reopens it in both modules
    assert "LIC-birch-mono" not in open_ids
    assert "LIC-spdx-bitstream-vera" not in open_ids
    # L3 and export see the license the owner allowed.
    effective = lic.effective_config(paths, REAL_CFG)
    assert effective.allowed["Bitstream-Vera"].redistributable is True
    assert "Bitstream-Vera" not in effective.ruling
