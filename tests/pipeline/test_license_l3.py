"""L3 license verification (milestone-1 step 6b, ``tff_catalog.license_l3``).

Every family, URL and hash here is synthetic (hosts are ``.example`` or an
invented ``example/fonts`` repository); the license texts are the canonical
ones in ``data/license-texts/`` with invented copyright lines around them.
The store, the fetcher and the font-file reader are small fakes of their
documented interfaces, so these tests need neither the network nor the
snapshot store. ``test_license_hash_change_requeues`` is the step's
"Done when" test. The two ``network`` tests read public files only: pinned
google/fonts license files, and the SPDX release the canonical texts come from.
"""

import hashlib
import io
import json
import logging
import time
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from tests.helpers import ROOT

from tff_catalog import jsonio, license_l3, reviews, stageio
from tff_catalog.config_model import (
    AllowedLicense,
    Config,
    LicensesConfig,
    RankingConfig,
    RulingLicense,
    from_mapping,
    load_toml,
)
from tff_catalog.engine.order import Placement
from tff_catalog.license_l3 import (
    Canon,
    Evidence,
    FetchedText,
    Inputs,
    L3Result,
    L3Ruling,
    TextRef,
    is_web_page,
)
from tff_catalog.licenses import LicenseClass, Verdict
from tff_catalog.membership import Membership, MemberState
from tff_catalog.paths import Paths
from tff_catalog.records import (
    FontFileRef,
    LicenseFact,
    Record,
    SourceKey,
    UniverseRecord,
    write_jsonl,
)
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State
from tff_catalog.store import FetchRecord
from tff_catalog.universe import Family, Universe

DAY = date(2026, 10, 3)
LAST_MONTH = date(2026, 9, 3)
TEXTS = ROOT / "data" / "license-texts"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
REPO = f"https://raw.githubusercontent.com/example/fonts/{COMMIT}"
OFL_NOTICE = (
    "Copyright 2021 The Example Sans Project Authors (https://git.example/example-sans)\n"
    "\n"
    "This Font Software is licensed under the SIL Open Font License, Version 1.1.\n"
    "This license is copied below, and is also available with a FAQ at:\n"
    "https://openfontlicense.org\n"
    "\n"
    "\n"
    "-----------------------------------------------------------\n"
    "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007\n"
    "-----------------------------------------------------------\n"
    "\n"
)
OFL_NAME_13 = (
    "This Font Software is licensed under the SIL Open Font License, Version 1.1. "
    "This license is available with a FAQ at: https://openfontlicense.org"
)


@pytest.fixture(scope="module")
def canon() -> Canon:
    return license_l3.load_canon(TEXTS)


def spdx_text(spdx: str) -> str:
    return (TEXTS / f"{spdx}.txt").read_text(encoding="utf-8")


def ofl_body() -> str:
    """The OFL-1.1 text from PREAMBLE on (the SPDX file starts with its title)."""
    text = spdx_text("OFL-1.1")
    return text[text.index("PREAMBLE") :]


def ofl(notice: str = OFL_NOTICE) -> str:
    return notice + ofl_body()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- fingerprints and the canonical texts ------------------------------------------------------


def test_canon_has_every_needed_license(canon: Canon) -> None:
    ids = set(canon.by_fingerprint.values())
    for spdx in (
        "OFL-1.1",
        "OFL-1.0",
        "Apache-2.0",
        "Ubuntu-font-1.0",
        "MIT",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "CC-BY-4.0",
        "Bitstream-Vera",
        "Unlicense",
    ):
        assert spdx in ids
    assert set(canon.exceptions) >= {"Font-exception-2.0"}


def test_every_canonical_text_matches_itself(canon: Canon) -> None:
    files = [(p.stem, p) for p in TEXTS.glob("*.txt")]
    files += [(p.parent.name, p) for p in TEXTS.glob("variants/*/*.txt")]
    assert len(files) >= 30
    for spdx, path in files:
        assert canon.match(path.read_text(encoding="utf-8")) == spdx, path


def test_every_canonical_text_passes_its_own_notice_check(canon: Canon) -> None:
    """The notices check (header and copyright lines) never fails a license's own text."""
    files = [(p.stem, p) for p in TEXTS.glob("*.txt")]
    files += [(p.parent.name, p) for p in TEXTS.glob("variants/*/*.txt")]
    for spdx, path in files:
        text = path.read_text(encoding="utf-8")
        assert canon.identify(text) == (spdx, frozenset()), path
        families = license_l3._expected_families([spdx])
        notices = license_l3.notices(text, spdx, canon)
        assert license_l3.statement_problems("it", notices, families) == [], path
        assert license_l3.long_notice_lines(text) == [], path


def test_duplicate_fingerprints_are_refused(tmp_path: Path) -> None:
    (tmp_path / "OFL-1.1.txt").write_text(spdx_text("OFL-1.1"))
    (tmp_path / "Other.txt").write_text(ofl())  # the same body under another id
    with pytest.raises(ValueError, match="same fingerprint"):
        license_l3.load_canon(tmp_path)


def test_a_text_without_a_body_start_is_refused(tmp_path: Path) -> None:
    (tmp_path / "Mystery.txt").write_text("Do what you like with these fonts.\n")
    with pytest.raises(ValueError, match="no body start"):
        license_l3.load_canon(tmp_path)


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(lambda t: t, id="plain"),
        pytest.param(lambda t: t.replace("\n", "\r\n"), id="crlf"),
        pytest.param(lambda t: "\ufeff" + t, id="bom"),
        pytest.param(
            lambda t: t.replace("Copyright 2021", "Copyright (c) 2019-2026, Jane Example,\n"),
            id="other-copyright",
        ),
        pytest.param(
            lambda t: t.replace(
                "(https://git.example/example-sans)\n",
                '(https://git.example/example-sans),\nwith Reserved Font Name "Example".\n',
            ),
            id="reserved-font-name",
        ),
        pytest.param(
            lambda t: t.replace("https://openfontlicense.org", "http://scripts.sil.org/OFL"),
            id="old-faq-url",
        ),
        pytest.param(lambda t: t.replace("License", "Licence"), id="british-spelling"),
        pytest.param(lambda t: t.replace("1)", "1."), id="numbering"),
        pytest.param(lambda t: t.replace(". ", ".\n"), id="rewrapped"),
        pytest.param(
            lambda t: "# SIL Open Font License\n\n" + t.replace("PREAMBLE", "## PREAMBLE"),
            id="markdown",
        ),
        pytest.param(lambda t: t[t.index("SIL OPEN FONT") :], id="no-notice"),
        pytest.param(lambda t: t[t.index("PREAMBLE") :], id="no-title"),
    ],
)
def test_every_ofl_text_has_the_same_fingerprint(variant: Any) -> None:
    canonical = license_l3.fingerprint(spdx_text("OFL-1.1"))
    assert license_l3.fingerprint(variant(ofl())) == canonical


def test_a_changed_word_changes_the_fingerprint(canon: Canon) -> None:
    edited = ofl().replace("as long as they are not sold by themselves", "as long as they are sold")
    assert canon.match(edited) is None
    closest = canon.closest(edited, ["OFL-1.1"])
    assert closest is not None
    assert closest[0] == "OFL-1.1"
    assert 0.98 < closest[1] < 1


def test_an_added_clause_changes_the_fingerprint(canon: Canon) -> None:
    assert canon.match(ofl() + "\nADDITIONAL TERMS\nThese fonts may not be sold.\n") is None


def test_variants_match_their_license(canon: Canon) -> None:
    apache = spdx_text("Apache-2.0")
    short = apache[: apache.index("END OF TERMS AND CONDITIONS")] + "END OF TERMS AND CONDITIONS\n"
    assert canon.match(short) == "Apache-2.0"
    mit = (
        "The MIT License (MIT)\n\nCopyright (c) 2024 Jane Example\n\n"
        + spdx_text("MIT").split("\n", 3)[3]
    )
    assert canon.match(mit) == "MIT"


def variant_text(spdx: str, name: str) -> str:
    return (TEXTS / "variants" / spdx / name).read_text(encoding="utf-8")


def test_the_apache_standard_notice_is_apache(canon: Canon) -> None:
    """The notice from the Apache-2.0 appendix ("How to apply the Apache License to your
    work") grants the license by reference; a font that ships only it is Apache-2.0."""
    notice = variant_text("Apache-2.0", "notice.txt")
    droid = notice.replace(
        "Copyright [yyyy] [name of copyright owner]", "Copyright 2009 Jane Example"
    )
    assert canon.identify(droid) == ("Apache-2.0", frozenset())
    assert (
        canon.match(droid.replace("See the License", "Personal use only. See the License")) is None
    )
    # The whole license text still starts at its definitions, not at the notice in its appendix.
    assert canon.match(spdx_text("Apache-2.0")) == "Apache-2.0"


def test_the_gust_font_license_grants_lppl(canon: Canon) -> None:
    """GUST's own text (version 1.0, and the 2006 preliminary one TeX Gyre ships) is one
    license, and it verifies LPPL-1.3c too (``GRANTS``)."""
    final = variant_text("LicenseRef-GUST-Font-License", "GUST-FONT-LICENSE-1.0.txt")
    early = variant_text("LicenseRef-GUST-Font-License", "tex-gyre-2.501-preliminary.txt")
    assert canon.match(final) == canon.match(early) == "LicenseRef-GUST-Font-License"
    assert license_l3.verified_ids("LicenseRef-GUST-Font-License") == {
        "LicenseRef-GUST-Font-License",
        "LPPL-1.3c",
    }
    allowed = ("LPPL-1.3c", "LicenseRef-GUST-Font-License")
    for expr in allowed:
        inputs = make_inputs(canon, verdicts={"example-sans": verdict(expr)}, allowed=allowed)
        result = check(inputs, text=final, facts=Facts(FONT_SHA, None, None, None))
        assert (result.level, result.matched, fatal(result)) == (
            "L3",
            "LicenseRef-GUST-Font-License",
            [],
        ), expr
    # A GUST text does not verify another license, and LPPL's own text does not verify GUST's.
    inputs = make_inputs(canon, verdicts={"example-sans": verdict("MIT")}, allowed=allowed)
    assert check(inputs, text=final).level == "failed"
    gust = verdict("LicenseRef-GUST-Font-License")
    inputs = make_inputs(canon, verdicts={"example-sans": gust}, allowed=allowed)
    result = check(inputs, text=spdx_text("LPPL-1.3c"), facts=Facts(FONT_SHA, None, None, None))
    assert fatal(result)[0].endswith("is LPPL-1.3c, not LicenseRef-GUST-Font-License")


def test_the_vera_release_copyright_file_is_vera(canon: Canon) -> None:
    """GNOME's ttf-bitstream-vera-1.10 COPYRIGHT.TXT: the license with its release notes
    above and Bitstream's FAQ below, as Nerd Fonts ships it."""
    text = variant_text("Bitstream-Vera", "gnome-1.10-COPYRIGHT.txt")
    assert canon.identify(text) == ("Bitstream-Vera", frozenset())
    assert canon.match(text.replace("Happy Font Hacking!", "Not for commercial use.")) is None


@pytest.mark.parametrize(
    ("line", "restricted"),
    [
        ("Digitized data copyright (c) 2012-2018 for FiraGO, Carrois Corporate GbR.", False),
        ("Digitized data copyright (c) 2012 Example Type. Free for personal use only.", True),
    ],
)
def test_a_digitized_data_line_is_a_copyright_line(
    canon: Canon, line: str, restricted: bool
) -> None:
    """The type industry's "Digitized data copyright ..." counts as a copyright line: left
    out of the fingerprint and the notice's word count, but still read for restrictions."""
    text = ofl(line + "\n" + OFL_NOTICE)
    assert license_l3.header_words(text) == license_l3.header_words(ofl())
    assert license_l3.dropped_lines(text).startswith("digitized data copyright")
    result = check(make_inputs(canon), text=text)
    assert (result.level == "failed") is restricted
    if restricted:
        assert fatal(result) == [
            f"a notice in license text {TEXT_URL} restricts use: 'personal use'"
        ]


def test_header_keeps_notices_and_normalise_drops_them() -> None:
    text = ofl()
    assert "copyright 2021 the example sans project authors" in license_l3.header(text)
    assert "openfontlicense org" in license_l3.header(text)
    assert license_l3.normalise(text).startswith("preamble the goals of the open font license")
    assert "copyright 2021" not in license_l3.header_words(text)


# --- what a statement names or restricts --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "families"),
    [
        (OFL_NAME_13, {"OFL"}),
        ("https://scripts.sil.org/OFL", {"OFL"}),
        ("http://scripts.sil.org/cms/scripts/page.php?item_id=OFL_web", {"OFL"}),
        ("Licensed under the Apache License, Version 2.0", {"Apache"}),
        ("http://www.apache.org/licenses/LICENSE-2.0", {"Apache"}),
        ("GPL - General Public License AND OFL - Open Font License", {"GPL", "OFL"}),
        ("GNU Lesser General Public License", {"LGPL"}),
        ("https://www.gnu.org/copyleft/lesser.html", {"LGPL"}),
        ("Creative Commons Attribution-ShareAlike 4.0 International", {"CC-BY-SA"}),
        ("http://creativecommons.org/licenses/by/4.0/", {"CC-BY"}),
        ("This font is in the public domain.", {"Public-domain"}),
        ("UBUNTU FONT LICENCE Version 1.0", {"Ubuntu"}),
        ("https://example.example/our-license", set()),
        ("", set()),
    ],
)
def test_mentions(text: str, families: set[str]) -> None:
    assert license_l3.mentions(text) == families


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (OFL_NAME_13, ()),
        ("Free for personal use only.", ("personal use",)),
        ("Free of charge for all non-commercial purposes.", ("noncommercial",)),
        ("Either commercially or noncommercially.", ()),
        ("This typeface is the property of Example Type Inc.", ("property of",)),
        ("Use is governed by the EULA.", ("eula",)),
        ("Example Sans is shareware.", ("shareware",)),
        ("The fonts may not be redistributed.", ("may not be redistributed",)),
    ],
)
def test_restrictions(text: str, found: tuple[str, ...]) -> None:
    assert license_l3.restrictions(text) == found


@pytest.mark.parametrize(
    ("spdx", "family", "text"),
    [
        ("OFL-1.1", "OFL", "OFL-1.1"),
        ("OFL-1.1-RFN", "OFL", "OFL-1.1"),
        ("OFL-1.1-no-RFN", "OFL", "OFL-1.1"),
        ("UFL-1.0", "Ubuntu", "Ubuntu-font-1.0"),
        ("GPL-2.0-or-later", "GPL", "GPL-2.0-only"),
        ("GPL-2.0+", "GPL", "GPL-2.0-only"),
        ("LGPL-2.1-only", "LGPL", "LGPL-2.1-only"),
        ("CC-BY-SA-4.0", "CC-BY-SA", "CC-BY-SA-4.0"),
        ("CC-BY-4.0", "CC-BY", "CC-BY-4.0"),
        ("0BSD", "BSD", "0BSD"),
        ("LicenseRef-Example", "LicenseRef-Example", "LicenseRef-Example"),
        ("LicenseRef-GUST-Font-License", "LPPL", "LicenseRef-GUST-Font-License"),
    ],
)
def test_license_family_and_text_id(spdx: str, family: str, text: str) -> None:
    assert license_l3.license_family(spdx) == family
    assert license_l3.text_id(spdx) == text


# --- SPDX expressions --------------------------------------------------------------------------


def test_expression_ids() -> None:
    expr = "OFL-1.1 OR (GPL-2.0-or-later WITH Font-exception-2.0 AND MIT)"
    assert license_l3.expression_ids(expr) == ("OFL-1.1", "GPL-2.0-or-later", "MIT")
    assert license_l3.expression_ids("MIT or OFL-1.1") == ("MIT", "OFL-1.1")
    for bad in ("OFL-1.1 OR", "(MIT", "MIT OFL-1.1", "AND MIT"):
        with pytest.raises(ValueError, match=r"unexpected|unbalanced|trailing"):
            license_l3.parse_expression(bad)


def test_satisfied() -> None:
    allowed = frozenset({"OFL-1.1", "MIT", "Bitstream-Vera", "GPL-2.0-or-later"})
    none: frozenset[str] = frozenset()
    ofl_only = frozenset({"OFL-1.1"})
    assert license_l3.satisfied("OFL-1.1", ofl_only, allowed, none)
    assert license_l3.satisfied("OFL-1.1-RFN", ofl_only, allowed, none)
    assert license_l3.satisfied("MIT OR OFL-1.1", ofl_only, allowed, none)
    assert not license_l3.satisfied("Bitstream-Vera AND MIT", frozenset({"MIT"}), allowed, none)
    assert license_l3.satisfied(
        "Bitstream-Vera AND MIT", frozenset({"MIT", "Bitstream-Vera"}), allowed, none
    )
    # A text matching a license that is not allowed verifies nothing.
    assert not license_l3.satisfied("OFL-1.1", ofl_only, frozenset({"MIT"}), none)
    gpl = "GPL-2.0-or-later WITH Font-exception-2.0"
    matched = frozenset({"GPL-2.0-only"})
    assert not license_l3.satisfied(gpl, matched, allowed, none)
    assert license_l3.satisfied(gpl, matched, allowed, frozenset({"Font-exception-2.0"}))


def test_satisfied_reads_allowed_ids_with_exceptions() -> None:
    """licenses.toml lists the GNU licenses only with the font exception ("X WITH E")."""
    allowed = frozenset({"OFL-1.1", "MIT", "GPL-2.0-or-later WITH Font-exception-2.0"})
    gpl, fe = frozenset({"GPL-2.0-only"}), frozenset({"Font-exception-2.0"})
    none: frozenset[str] = frozenset()
    expr = "GPL-2.0-or-later WITH Font-exception-2.0"
    assert license_l3.satisfied(expr, gpl, allowed, fe)
    assert license_l3.satisfied("GPL-2.0+ WITH Font-exception-2.0", gpl, allowed, fe)
    assert not license_l3.satisfied(expr, gpl, allowed, none)  # the exception's text is missing
    assert not license_l3.satisfied("GPL-2.0-or-later", gpl, allowed, fe)  # bare GPL is excluded
    assert not license_l3.satisfied("GPL-2.0-only WITH Font-exception-2.0", gpl, allowed, fe)
    # An exception only adds permissions: "MIT WITH E" is allowed because MIT is.
    assert license_l3.satisfied("MIT WITH Font-exception-2.0", frozenset({"MIT"}), allowed, fe)


def test_every_allowed_license_with_a_canonical_text_can_verify(canon: Canon) -> None:
    """Each key of the real config/licenses.toml is verifiable by its own text."""
    cfg = from_mapping(
        LicensesConfig, load_toml(ROOT / "config" / "licenses.toml"), where="licenses.toml"
    )
    allowed = frozenset(cfg.allowed)
    have = set(canon.by_fingerprint.values())
    checked = 0
    for key in sorted(allowed):
        leaf = license_l3.parse_expression(key)
        assert isinstance(leaf, license_l3._Leaf), key
        tid = license_l3.text_id(leaf.id)
        if tid not in have:
            continue
        exceptions = frozenset({leaf.exception} if leaf.exception else ())
        assert license_l3.satisfied(key, frozenset({tid}), allowed, exceptions), key
        checked += 1
    assert checked >= 15


def test_family_allowed_trusts_an_owner_ruling_on_the_family() -> None:
    allowed = frozenset({"MIT", "OFL-1.1"})
    assert license_l3.family_allowed("MIT OR OFL-1.1", allowed) == allowed
    assert license_l3.family_allowed("GPL-2.0-only OR OFL-1.1", allowed) == allowed
    # L2 let "Bitstream-Vera AND MIT" in although Vera is not allowed: a gate LIC ruling.
    assert license_l3.family_allowed("Bitstream-Vera AND MIT", allowed) == allowed | {
        "Bitstream-Vera"
    }


def test_exceptions_in(canon: Canon) -> None:
    exception = (TEXTS / "exceptions" / "Font-exception-2.0.txt").read_text()
    gpl = spdx_text("GPL-2.0-only")
    assert canon.exceptions_in(gpl) == frozenset()
    assert canon.exceptions_in(gpl + "\n\n" + exception) == {"Font-exception-2.0"}


@pytest.mark.parametrize("order", ["exception-below", "exception-above"])
def test_gpl_with_the_font_exception_in_one_text(canon: Canon, order: str) -> None:
    gpl = spdx_text("GPL-2.0-only")
    exception = (TEXTS / "exceptions" / "Font-exception-2.0.txt").read_text()
    text = f"{gpl}\n\n{exception}" if order == "exception-below" else f"{exception}\n\n{gpl}"
    assert license_l3.fingerprint(text) != license_l3.fingerprint(gpl)
    assert canon.identify(text) == ("GPL-2.0-only", frozenset({"Font-exception-2.0"}))
    expr = "GPL-2.0-or-later WITH Font-exception-2.0"
    inputs = make_inputs(canon, verdicts={"example-sans": verdict(expr)}, allowed=("OFL-1.1", expr))
    facts = Facts(
        FONT_SHA,
        license_description="GNU General Public License v2 or later, with the font exception",
        license_url="https://www.gnu.org/licenses/old-licenses/gpl-2.0.html",
    )
    result = check(inputs, text=text, facts=facts)
    assert (result.level, result.matched, fatal(result)) == ("L3", "GPL-2.0-only", [])
    # The bare GPL text does not verify it: without the exception the GPL is excluded.
    assert check(inputs, text=gpl, facts=facts).level == "failed"


# --- URLs, evidence and scope ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "pinned"),
    [
        (f"{REPO}/ofl/examplesans/OFL.txt", True),
        ("https://raw.githubusercontent.com/example/fonts/main/ofl/examplesans/OFL.txt", False),
        (f"https://github.com/example/fonts/raw/{COMMIT}/fonts/ExampleSans.ttf", True),
        (f"https://github.com/example/fonts/blob/{COMMIT}/OFL.txt", True),
        ("https://github.com/example/fonts/releases/download/v2.1/ExampleSans.zip", True),
        ("https://github.com/example/fonts/releases/latest", False),
        ("https://cdn.jsdelivr.net/npm/@example/sans@5.0.3/files/regular.woff2", True),
        ("https://cdn.jsdelivr.net/npm/example-sans@5.0.3/files/regular.woff2", True),
        ("https://cdn.jsdelivr.net/npm/@example/sans/files/regular.woff2", False),
        (f"https://cdn.jsdelivr.net/gh/example/fonts@{COMMIT}/ExampleSans.ttf", True),
        ("https://cdn.jsdelivr.net/gh/example/fonts@main/ExampleSans.ttf", False),
        ("https://fonts.example/ExampleSans.ttf", False),
        (f"https://gitlab.com/example/fonts/-/raw/{COMMIT}/LICENSE.txt", True),
        (f"https://salsa.debian.org/fonts-team/fonts-example/-/raw/{COMMIT}/debian/LICENSE", True),
        ("https://gitlab.com/example/fonts/-/raw/main/LICENSE.txt", False),
        (f"https://gitlab.com/example/fonts/-/blob/{COMMIT}/LICENSE.txt", False),
    ],
)
def test_is_pinned(url: str, pinned: bool) -> None:
    assert license_l3.is_pinned(url) is pinned


def test_fetchable_url() -> None:
    assert license_l3.fetchable_url(f"https://github.com/example/fonts/blob/{COMMIT}/OFL.txt") == (
        f"{REPO}/OFL.txt"
    )
    assert license_l3.fetchable_url("http://fonts.example/LICENSE") == (
        "https://fonts.example/LICENSE"
    )


@pytest.mark.parametrize(
    ("url", "member"),
    [
        (
            "https://fonts.example/Example-1.0.zip#Example-1.0/OFL.txt",
            ("https://fonts.example/Example-1.0.zip", "Example-1.0/OFL.txt"),
        ),
        (
            "https://fonts.example/Example%201.0.zip#Example%201.0/Example%20license.txt",
            ("https://fonts.example/Example%201.0.zip", "Example 1.0/Example license.txt"),
        ),
        ("https://fonts.example/Example-1.0.zip", None),
        ("https://fonts.example/Example-1.0.zip#", None),
        ("https://fonts.example/OFL.txt#section", None),
        ("http://fonts.example/Example-1.0.zip#OFL.txt", None),
    ],
)
def test_archive_member(url: str, member: tuple[str, str] | None) -> None:
    assert license_l3.archive_member(url) == member


def fam_key(key: str, ns: str = "gf-dir") -> SourceKey:
    return SourceKey(ns, key)


def test_gather_finds_the_records_and_license_of_a_several_families_key() -> None:
    formula = SourceKey("fontist-formula", "opal")
    fams = {
        fid: Family(fid, name, (), ("fontist",), date(2026, 9, 3), fid, shared=((formula, name),))
        for fid, name in (("opal-sans", "Opal Sans"), ("opal-serif", "Opal Serif"))
    }
    universe = Universe(families=fams, unmapped=())
    member = "https://opal.example/Opal.zip#Opal"
    records: list[UniverseRecord | LicenseFact] = [
        UniverseRecord(
            source="fontist", key=formula, family=name, files=(FontFileRef(f"{member}{n}.ttf"),)
        )
        for n, name in (("Sans", "Opal Sans"), ("Serif", "Opal Serif"))
    ]
    records.append(
        LicenseFact(
            source="fontist", key=formula, raw="OFL-1.1", text_url="https://opal.example/OFL.txt"
        )
    )
    evidence = license_l3.gather(records, license_l3.key_index(universe), universe)
    assert [f.url for f in evidence["opal-sans"].files] == [f"{member}Sans.ttf"]
    assert [f.url for f in evidence["opal-serif"].files] == [f"{member}Serif.ttf"]
    assert evidence["opal-sans"].texts == evidence["opal-serif"].texts
    assert license_l3.gather(records, license_l3.key_index(universe)) == {}  # keys alone: none


def test_gather_orders_evidence_best_first() -> None:
    key, other = fam_key("examplesans"), fam_key("example-sans", "fs-id")
    records: list[UniverseRecord | LicenseFact] = [
        UniverseRecord(
            source="fontsource",
            key=other,
            family="Example Sans",
            urls=(("license", "https://fonts.example/LICENSE"),),
            files=(
                FontFileRef("https://cdn.jsdelivr.net/npm/example-sans@1.0.0/regular.woff2"),
                FontFileRef("https://fonts.example/ExampleSans.zip"),
            ),
        ),
        UniverseRecord(
            source="google_repo",
            key=key,
            family="Example Sans",
            files=(
                FontFileRef(f"{REPO}/ofl/examplesans/ExampleSans-Italic.ttf", role="italic"),
                FontFileRef(f"{REPO}/ofl/examplesans/ExampleSans%5Bwght%5D.ttf", role="variable"),
            ),
        ),
        LicenseFact(
            source="google_repo",
            key=key,
            raw="OFL",
            text_url=f"{REPO}/ofl/examplesans/OFL.txt",
            text_sha256="a" * 64,
        ),
        LicenseFact(source="fontsource", key=other, raw="OFL-1.1", spdx="OFL-1.1"),
        LicenseFact(source="nerdfonts", key=fam_key("Unmapped", "nerd-folder"), raw="MIT"),
    ]
    evidence = license_l3.gather(records, {key: "example-sans", other: "example-sans"})
    assert list(evidence) == ["example-sans"]
    ev = evidence["example-sans"]
    assert ev.texts == (
        TextRef(f"{REPO}/ofl/examplesans/OFL.txt", "a" * 64, "google_repo"),
        TextRef("https://fonts.example/LICENSE", None, "fontsource"),
    )
    assert [f.url.rsplit("/", 1)[1] for f in ev.files] == [
        "ExampleSans%5Bwght%5D.ttf",
        "ExampleSans-Italic.ttf",
        "regular.woff2",
    ]


def place(order: int) -> Placement:
    return Placement(order=order, rank=order if order <= 100 else None, band=None, gate_held=False)


def test_scope() -> None:
    ranks = {
        "overall": {"a": place(1), "b": place(700), "c": place(701)},
        "project": {"c": place(150), "d": place(151)},
        "desktop_chosen": {"e": place(3)},
        "coding": {"f": place(1)},
    }
    extra = ("project", "desktop_chosen", "desktop_installed")
    assert license_l3.scope(ranks, ["g"], extra, 700, 150) == ["a", "b", "c", "e", "g"]


# --- the check ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """The ``fontfiles.FontFacts`` fields L3 reads."""

    sha256: str
    version: str | None = "Version 1.001"
    license_description: str | None = OFL_NAME_13
    license_url: str | None = "https://openfontlicense.org"
    copyright: str | None = "Copyright 2026 The Synth Project Authors"


class Texts:
    """A ``TextSource`` over a dict of URL -> text (or HTTP status); ``lately`` holds the
    sha256 of the texts read in the stale window before the run."""

    def __init__(self, texts: dict[str, str | int], lately: Iterable[str] = ()) -> None:
        self.texts = texts
        self.lately = frozenset(lately)
        self.asked: list[str] = []

    def get(self, ref: TextRef) -> FetchedText:
        self.asked.append(ref.url)
        body = self.texts.get(ref.url, 404)
        if isinstance(body, int):
            return FetchedText(ref.url, body, error=f"HTTP {body}")
        return FetchedText(ref.url, 200, sha(body), body)

    def read_lately(self, sha256: str) -> bool:
        return sha256 in self.lately


class FontReader:
    """A ``FactsSource`` over a dict of URL -> facts."""

    def __init__(self, facts: dict[str, Facts]) -> None:
        self.facts = facts

    def get(self, ref: FontFileRef) -> tuple[Any, str | None]:
        found = self.facts.get(ref.url)
        return (found, None) if found else (None, "HTTP 404")


TEXT_URL = f"{REPO}/ofl/examplesans/OFL.txt"
FONT_URL = f"{REPO}/ofl/examplesans/ExampleSans%5Bwght%5D.ttf"
FONT_SHA = "f" * 64


def verdict(spdx: str | None = "OFL-1.1", status: str = "allowed") -> Verdict:
    lic = None if spdx is None else LicenseClass(spdx=spdx, status=status)  # type: ignore[arg-type]
    return Verdict(family_id="example-sans", spdx=spdx, license=lic, preview_ok=True, seen=())


def make_inputs(
    canon: Canon,
    *,
    verdicts: dict[str, Verdict] | None = None,
    evidence: dict[str, Evidence] | None = None,
    previous: dict[str, dict[str, Any]] | None = None,
    rulings: dict[str, L3Ruling] | None = None,
    allowed: Iterable[str] = ("OFL-1.1", "MIT", "Apache-2.0", "Bitstream-Vera"),
) -> Inputs:
    return Inputs(
        names={"example-sans": "Example Sans"},
        verdicts={"example-sans": verdict()} if verdicts is None else verdicts,
        evidence={
            "example-sans": Evidence(
                texts=(TextRef(TEXT_URL, source="google_repo"),),
                files=(FontFileRef(FONT_URL, role="variable"),),
            )
        }
        if evidence is None
        else evidence,
        previous=previous or {},
        rulings=rulings or {},
        canon=canon,
        allowed=frozenset(allowed),
    )


def check(
    inputs: Inputs,
    text: str | int = "",
    facts: Facts | None = None,
    extra: dict[str, str | int] | None = None,
    lately: Iterable[str] = (),
) -> L3Result:
    texts = Texts({TEXT_URL: text if text != "" else ofl(), **(extra or {})}, lately)
    reader = FontReader({FONT_URL: facts or Facts(FONT_SHA)})
    return license_l3.check_family("example-sans", inputs, texts, reader, DAY)


def fatal(result: L3Result) -> list[str]:
    return [p for p in result.problems if not p.startswith("note: ")]


def test_a_clean_family_reaches_l3(canon: Canon) -> None:
    result = check(make_inputs(canon))
    assert result == L3Result(
        family_id="example-sans",
        level="L3",
        checked_on=DAY,
        text_url=TEXT_URL,
        text_sha256=sha(ofl()),
        matched="OFL-1.1",
        name_ids=(OFL_NAME_13, "https://openfontlicense.org"),
        font_version="Version 1.001",
        font_file=FontFileRef(url=FONT_URL, sha256=FONT_SHA, role="variable"),
        problems=(),
        copyright="Copyright 2026 The Synth Project Authors",  # name ID 0, for CC BY credits
    )


def test_empty_name_ids_are_a_note(canon: Canon) -> None:
    result = check(make_inputs(canon), facts=Facts(FONT_SHA, None, None, None))
    assert result.level == "L3"
    assert result.problems == ("note: name IDs 13 and 14 are empty",)


@pytest.mark.parametrize(
    ("facts", "problem"),
    [
        (Facts(FONT_SHA, license_description="Licensed under the Apache License, Version 2.0"),
         "name ID 13 names Apache"),
        (Facts(FONT_SHA, license_url="https://www.gnu.org/licenses/gpl.html"), "name ID 14 names GPL"),
        (Facts(FONT_SHA, license_description="Free for personal use only."),
         "name ID 13 restricts use: 'personal use'"),
    ],
)  # fmt: skip
def test_a_contradicting_name_table_fails(canon: Canon, facts: Facts, problem: str) -> None:
    result = check(make_inputs(canon), facts=facts)
    assert result.level == "failed"
    assert fatal(result) == [problem]
    assert result.matched == "OFL-1.1"  # the text itself was fine


def test_a_name_table_may_name_every_branch_of_an_or(canon: Canon) -> None:
    inputs = make_inputs(canon, verdicts={"example-sans": verdict("OFL-1.1 OR GPL-2.0-or-later")})
    facts = Facts(FONT_SHA, license_description="GPL - General Public License AND OFL")
    assert check(inputs, facts=facts).level == "L3"


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (404, f"license text {TEXT_URL}: HTTP 404"),
        (
            ofl().replace("not sold by themselves", "sold"),
            f"license text {TEXT_URL} matches no known license (closest: OFL-1.1,",
        ),
        (spdx_text("MIT"), f"license text {TEXT_URL} is MIT, not OFL-1.1"),
        (
            "<!DOCTYPE html>\n<html><body>SIL Open Font License. Use is prohibited unless"
            " you agree to the terms of use.</body></html>",
            f"license text {TEXT_URL} is a web page, not a license text",
        ),
        (
            ofl(OFL_NOTICE.replace("Authors", "Authors. Free for personal use only")),
            f"a notice in license text {TEXT_URL} restricts use: 'personal use'",
        ),
        (
            ofl(OFL_NOTICE + "These fonts may also be used under the example terms " * 5 + "\n"),
            f"the notice above license text {TEXT_URL} has 50 words besides",
        ),
    ],
    ids=["missing", "edited", "other-license", "web-page", "restricting-notice", "long-notice"],
)
def test_a_bad_text_fails(canon: Canon, text: str | int, problem: str) -> None:
    result = check(make_inputs(canon), text=text)
    assert result.level == "failed"
    assert fatal(result)[0].startswith(problem)
    assert result.font_file is not None  # the file was still read and recorded


def test_a_web_page_says_so_and_nothing_more(canon: Canon) -> None:
    page = "<!doctype html><html><body>Use is prohibited. Terms of use.</body></html>"
    result = check(make_inputs(canon), text=page)
    assert fatal(result) == [f"license text {TEXT_URL} is a web page, not a license text"]


@pytest.mark.parametrize(
    ("text", "page"),
    [
        ("<!DOCTYPE html>\n<html lang=en>", True),
        ("  <!-- mirror -->\n<HTML>", True),
        ("<html>", True),
        (ofl(), False),
        ("Copyright <html> in a notice", False),
    ],
)
def test_is_web_page(text: str, page: bool) -> None:
    assert is_web_page(text) is page


def test_a_text_matching_a_license_not_allowed_fails(canon: Canon) -> None:
    """An OR verifies only through a branch the allowed list lets in."""
    expr = "GPL-2.0-or-later OR OFL-1.1"
    inputs = make_inputs(canon, verdicts={"example-sans": verdict(expr)}, allowed=("OFL-1.1",))
    result = check(inputs, text=spdx_text("GPL-2.0-only"))
    assert result.level == "failed"
    assert fatal(result) == [f"the license texts found do not verify {expr}"]
    assert check(inputs).level == "L3"  # the OFL text verifies it


def test_an_owner_ruling_on_the_family_lets_its_licenses_verify(canon: Canon) -> None:
    """Hack: L2 let "Bitstream-Vera AND MIT" in by a gate LIC family ruling, though
    licenses.toml does not allow Bitstream-Vera; L3 still needs both texts."""
    vera, mit_url = "https://fonts.example/LICENSE-Vera.txt", "https://fonts.example/LICENSE"
    mit = "Copyright (c) 2024 Jane Example\n\n" + spdx_text("MIT").split("\n", 3)[3]
    inputs = make_inputs(
        canon,
        verdicts={"example-sans": verdict("Bitstream-Vera AND MIT")},
        evidence={
            "example-sans": Evidence(
                texts=(TextRef(mit_url), TextRef(vera)), files=(FontFileRef(FONT_URL),)
            )
        },
        allowed=("MIT", "OFL-1.1"),
    )
    facts = Facts(FONT_SHA, None, None, None)
    both = check(inputs, facts=facts, extra={mit_url: mit, vera: spdx_text("Bitstream-Vera")})
    assert (both.level, both.matched) == ("L3", "Bitstream-Vera AND MIT")
    assert check(inputs, facts=facts, extra={mit_url: mit}).level == "failed"


@pytest.mark.parametrize(
    ("line", "phrase"),
    [
        ("Copyright 2024 Example Type. This font is for personal use only.", "personal use"),
        (
            'with Reserved Font Name "Example". A commercial license is needed.',
            "commercial license",
        ),
    ],
    ids=["copyright-line", "reserved-font-name-line"],
)
def test_a_restriction_below_the_body_fails(canon: Canon, line: str, phrase: str) -> None:
    """The fingerprint leaves copyright and Reserved Font Name lines out wherever they
    stand, so what those lines say is read on its own."""
    text = f"{ofl()}\n{line}\n"
    assert canon.match(text) == "OFL-1.1"
    result = check(make_inputs(canon), text=text)
    assert result.level == "failed"
    assert fatal(result) == [f"a notice in license text {TEXT_URL} restricts use: {phrase!r}"]


# A clause appended to a Reserved Font Name line (synthetic, shaped like a real
# upstream's "[ADDITIONAL PERMISSION]"), with no phrase of RESTRICTIONS in it.
ADDED_TERMS = (
    'Copyright 2021 The Example Sans Project Authors, with Reserved Font Name "Example". '
    "[ADDITIONAL TERMS] The Reserved Font Name may also be used by Modified Versions that "
    "are converted to other formats for delivery on the web, provided those versions are "
    "offered only by the platforms the authors list on the project page and by no one else."
)
TRADEMARK_LINE = (
    "Copyright 2010-2024 Example Type (https://type.example/), with Reserved Font Name "
    "'Example'. All Rights Reserved. Example is a trademark of Example Type in the United "
    "States and/or other countries."
)


@pytest.mark.parametrize("where", ["above", "below"])
def test_a_clause_added_to_a_copyright_line_fails(canon: Canon, where: str) -> None:
    """The fingerprint leaves copyright and Reserved Font Name lines out, so a long one is
    read as a notice that may change the license."""
    first_line = OFL_NOTICE.split("\n", 1)[0]
    if where == "above":
        text = ofl(OFL_NOTICE.replace(first_line, ADDED_TERMS))
    else:
        text = f"{ofl()}\n{ADDED_TERMS}\n"
    assert canon.match(text) == "OFL-1.1"  # the fingerprint cannot see the clause
    assert license_l3.restrictions(text) == ()
    [words] = license_l3.long_notice_lines(text)
    assert len(words) > license_l3.NOTICE_LINE_WORDS_MAX
    result = check(make_inputs(canon), text=text)
    assert result.level == "failed"
    assert fatal(result) == [
        f"a copyright or Reserved Font Name line in license text {TEXT_URL} has "
        f"{len(words)} words: {' '.join(words[:12])} ..."
    ]
    # Long but ordinary lines pass: copyright, Reserved Font Name and trademark; and the
    # whole OFL notice on the copyright line, as one-paragraph Markdown files have it.
    notice, gap, title = OFL_NOTICE.partition("\n\n\n")
    one_line = " ".join(line for line in notice.split("\n") if line) + gap + title
    assert one_line.startswith(f"{first_line} This Font Software is licensed under")
    assert "\n" not in one_line.split("openfontlicense.org")[0]
    for usual in (ofl(OFL_NOTICE.replace(first_line, TRADEMARK_LINE)), ofl(one_line)):
        assert license_l3.long_notice_lines(usual) == []
        assert check(make_inputs(canon), text=usual).level == "L3"


@pytest.mark.parametrize(
    "notice",
    [
        pytest.param(OFL_NOTICE.replace(OFL_NOTICE.split("\n", 1)[0], ADDED_TERMS), id="long-line"),
        pytest.param(
            OFL_NOTICE + "These fonts may also be used under the example terms " * 5 + "\n",
            id="long-notice",
        ),
    ],
)
def test_a_long_notice_fails_even_when_another_text_verifies(canon: Canon, notice: str) -> None:
    """Like a use restriction, words the fingerprint cannot see block the family: a clean
    copy of the license elsewhere does not outweigh them."""
    first = "https://fonts.example/OFL.txt"
    ev = {
        "example-sans": Evidence(
            texts=(TextRef(first), TextRef(TEXT_URL)), files=(FontFileRef(FONT_URL),)
        )
    }
    result = check(make_inputs(canon, evidence=ev), extra={first: ofl(notice)})
    assert (result.level, result.matched, result.text_url) == ("failed", "OFL-1.1", TEXT_URL)
    [problem] = fatal(result)
    assert first in problem
    assert " words" in problem


def test_another_license_named_above_the_body_fails(canon: Canon) -> None:
    text = (
        "This Font Software is licensed under the SIL Open Font License, Version 1.1.\n\n"
        + spdx_text("Apache-2.0")
    )
    assert canon.match(text) == "Apache-2.0"
    inputs = make_inputs(canon, verdicts={"example-sans": verdict("Apache-2.0")})
    facts = Facts(
        FONT_SHA,
        license_description="Licensed under the Apache License, Version 2.0",
        license_url=None,
    )
    result = check(inputs, text=text, facts=facts)
    assert fatal(result) == [f"a notice in license text {TEXT_URL} names OFL"]


def test_a_restricting_text_fails_even_when_another_verifies(canon: Canon) -> None:
    readme = "https://fonts.example/README.txt"
    ev = {
        "example-sans": Evidence(
            texts=(TextRef(readme), TextRef(TEXT_URL)), files=(FontFileRef(FONT_URL),)
        )
    }
    result = check(
        make_inputs(canon, evidence=ev),
        extra={readme: "Example Sans is free for personal use only.\n"},
    )
    assert (result.level, result.matched, result.text_url) == ("failed", "OFL-1.1", TEXT_URL)
    assert fatal(result) == [f"a notice in license text {readme} restricts use: 'personal use'"]
    # An earlier text that merely fails (here: an unknown text) does not block a later one.
    fine = check(make_inputs(canon, evidence=ev), extra={readme: "Example Sans, a typeface.\n"})
    assert fine.level == "L3"


def test_a_whole_license_text_in_a_name_id(canon: Canon) -> None:
    """A name ID holding a whole license text is judged by the license it is."""
    apache = spdx_text("Apache-2.0")
    assert license_l3.restrictions(apache) == ("you agree",)  # its body alone reads as one
    inputs = make_inputs(canon, verdicts={"example-sans": verdict("Apache-2.0")})
    facts = Facts(
        FONT_SHA,
        license_description=apache,
        license_url="http://www.apache.org/licenses/LICENSE-2.0",
    )
    assert check(inputs, text=apache, facts=facts).level == "L3"
    gpl = Facts(FONT_SHA, license_description=spdx_text("GPL-2.0-only"))
    assert fatal(check(make_inputs(canon), facts=gpl)) == ["name ID 13 is the GPL-2.0-only text"]


def test_the_next_font_file_is_read_when_the_first_fails(canon: Canon) -> None:
    italic = f"{REPO}/ofl/examplesans/ExampleSans-Italic.ttf"
    files = (FontFileRef(FONT_URL), FontFileRef(italic, role="italic"))
    inputs = make_inputs(canon, evidence={"example-sans": Evidence((TextRef(TEXT_URL),), files)})
    reader = FontReader({italic: Facts(FONT_SHA)})
    result = license_l3.check_family("example-sans", inputs, Texts({TEXT_URL: ofl()}), reader, DAY)
    assert result.level == "L3"
    assert result.font_file == FontFileRef(url=italic, sha256=FONT_SHA, role="italic")
    assert result.problems == (f"note: font file {FONT_URL}: HTTP 404",)


@pytest.mark.parametrize(
    ("v", "problem"),
    [
        (None, "L2 settled no license"),
        (verdict(None), "L2 settled no license"),
        (verdict("OFL-1.1", "ruling"), "L2 license OFL-1.1 is not allowed (status ruling)"),
    ],
)
def test_l2_must_allow_the_license(canon: Canon, v: Verdict | None, problem: str) -> None:
    inputs = make_inputs(canon, verdicts={} if v is None else {"example-sans": v})
    texts = Texts({})
    result = license_l3.check_family(
        "example-sans", inputs, texts, FontReader({FONT_URL: Facts(FONT_SHA)}), DAY
    )
    assert result.level == "failed"
    assert fatal(result) == [problem]
    assert texts.asked == []  # nothing to verify, nothing fetched


def test_no_font_file_fails(canon: Canon) -> None:
    ev = {"example-sans": Evidence(texts=(TextRef(TEXT_URL),))}
    result = check(make_inputs(canon, evidence=ev))
    assert result.level == "failed"
    assert fatal(result) == ["no font file to check name IDs 13 and 14"]
    assert result.font_file is None


def test_an_unreadable_font_file_fails(canon: Canon) -> None:
    inputs = make_inputs(canon)
    result = license_l3.check_family(
        "example-sans", inputs, Texts({TEXT_URL: ofl()}), FontReader({}), DAY
    )
    assert fatal(result) == [f"font file {FONT_URL}: HTTP 404"]


def test_the_next_text_is_tried_when_the_first_fails(canon: Canon) -> None:
    second = "https://fonts.example/OFL.txt"
    ev = {
        "example-sans": Evidence(
            texts=(TextRef(TEXT_URL), TextRef(second)), files=(FontFileRef(FONT_URL),)
        )
    }
    result = check(make_inputs(canon, evidence=ev), text=404, extra={second: ofl()})
    assert result.level == "L3"
    assert result.text_url == second


def test_an_and_expression_needs_every_text(canon: Canon) -> None:
    vera = "https://fonts.example/LICENSE-Vera.txt"
    mit_url = "https://fonts.example/LICENSE-MIT.txt"
    mit = "Copyright (c) 2024 Jane Example\n\n" + spdx_text("MIT").split("\n", 3)[3]
    inputs = make_inputs(
        canon,
        verdicts={"example-sans": verdict("Bitstream-Vera AND MIT")},
        evidence={
            "example-sans": Evidence(
                texts=(TextRef(mit_url), TextRef(vera)), files=(FontFileRef(FONT_URL),)
            )
        },
    )
    facts = Facts(FONT_SHA, None, None, None)
    only_mit = check(inputs, facts=facts, extra={mit_url: mit})
    assert only_mit.level == "failed"
    both = check(inputs, facts=facts, extra={mit_url: mit, vera: spdx_text("Bitstream-Vera")})
    assert both.level == "L3"
    assert both.matched == "Bitstream-Vera AND MIT"


def previous_entry(result: L3Result, **changes: Any) -> dict[str, Any]:
    return license_l3.state_entry(result) | changes


# --- research (config/license-texts.toml) -----------------------------------------------------

HACK_URL = f"{REPO}/hack/LICENSE.md"
HACK = (
    "The work in the Example project is Copyright 2018 Example Authors and licensed under "
    "the MIT License\n\nThe work in the DejaVu project was committed to the public domain.\n\n"
    "Bitstream Vera Sans Mono Copyright 2003 Bitstream Inc. and licensed under the Bitstream "
    'Vera License with Reserved Font Names "Bitstream" and "Vera"\n\n### MIT License\n\n'
    + spdx_text("MIT").split("\n", 2)[2]
    + "\n### BITSTREAM VERA LICENSE\n\n"
    + spdx_text("Bitstream-Vera").split("\n", 2)[2]
)
HACK_13 = "The work in the DejaVu project was committed to the public domain. MIT License."


def research(
    *,
    texts: Iterable[license_l3.ResearchedText] = (),
    families: Iterable[license_l3.FamilyResearch] = (),
) -> license_l3.Research:
    return license_l3.Research(
        families={f.family: f for f in families}, texts={t.sha256: t for t in texts}
    )


PUBLIC_DOMAIN = license_l3.FamilyResearch(
    "example-sans", "Example", "test", mentions=("Public-domain",)
)


def hack_inputs(canon: Canon, **known: Any) -> Inputs:
    """Hack-like inputs: its LICENSE.md researched, public-domain notes allowed."""
    expr = "Bitstream-Vera AND MIT"
    entry = license_l3.ResearchedText(sha(HACK), ("MIT", "Bitstream-Vera"), HACK_URL, "test")
    known.setdefault("families", [PUBLIC_DOMAIN])
    return replace(
        make_inputs(
            canon,
            verdicts={"example-sans": verdict(expr)},
            evidence={
                "example-sans": Evidence(
                    texts=(TextRef(HACK_URL),), files=(FontFileRef(FONT_URL, role="variable"),)
                )
            },
        ),
        research=research(texts=[entry], **known),
    )


def check_hack(inputs: Inputs, text: str = HACK, facts: Facts | None = None) -> L3Result:
    texts = Texts({HACK_URL: text})
    reader = FontReader({FONT_URL: facts or Facts(FONT_SHA, None, None, None)})
    return license_l3.check_family("example-sans", inputs, texts, reader, DAY)


def test_a_researched_text_verifies_its_licenses(canon: Canon) -> None:
    assert canon.match(HACK) is None  # two licenses and three notices: no canonical text
    assert check_hack(make_inputs(canon)).level == "failed"
    result = check_hack(hack_inputs(canon))
    assert (result.level, result.matched, fatal(result)) == ("L3", "Bitstream-Vera AND MIT", [])
    assert result.problems[0] == (
        f"note: license text {HACK_URL} is a researched text (config/license-texts.toml): "
        "Bitstream-Vera AND MIT"
    )


@pytest.mark.parametrize(
    ("edit", "problem"),
    [
        (lambda t: t + "\nOne more line.\n", "matches no known license"),
        (lambda t: t + "\n", "matches no known license"),
    ],
    ids=["added-line", "added-newline"],
)
def test_a_researched_text_holds_only_for_its_exact_bytes(
    canon: Canon, edit: Any, problem: str
) -> None:
    result = check_hack(hack_inputs(canon), text=edit(HACK))
    assert result.level == "failed"
    assert problem in fatal(result)[0]


def test_a_researched_text_is_read_whole_for_restrictions_and_licenses(canon: Canon) -> None:
    """No license body is set aside: a restriction or another license anywhere fails it,
    even inside what research took for a license body."""
    for text, problem in (
        (HACK.replace("to deal in the Software", "to deal, for personal use only, in the Software"),
         "restricts use: 'personal use'"),
        (HACK.replace("### MIT License", "### MIT License (or the GNU General Public License)"),
         "names GPL"),
    ):  # fmt: skip
        assert text != HACK
        entry = license_l3.ResearchedText(sha(text), ("MIT", "Bitstream-Vera"), HACK_URL, "test")
        inputs = replace(
            hack_inputs(canon), research=research(texts=[entry], families=[PUBLIC_DOMAIN])
        )
        result = check_hack(inputs, text=text)
        assert result.level == "failed"
        assert any(p.endswith(problem) for p in fatal(result)), fatal(result)


def test_a_researched_text_must_hold_the_l2_license(canon: Canon) -> None:
    entry = license_l3.ResearchedText(sha(HACK), ("MIT",), HACK_URL, "test")
    inputs = replace(hack_inputs(canon), research=research(texts=[entry], families=[PUBLIC_DOMAIN]))
    result = check_hack(inputs)
    assert result.level == "failed"
    assert fatal(result) == ["the license texts found do not verify Bitstream-Vera AND MIT"]
    inputs = replace(inputs, verdicts={"example-sans": verdict("Apache-2.0")})
    assert f"license text {HACK_URL} is MIT, not Apache-2.0" in fatal(check_hack(inputs))


# A notice that lists the Reserved Font Names on lines of their own, as Twilio Sans Mono's does:
# more words above the body than EXTRA_WORDS_MAX.
NAMES_NOTICE = OFL_NOTICE.replace(
    "\n\nThis Font Software",
    "\nwith Reserved Font Names set forth below\n"
    + "".join(
        f"Example Sans {style} Italic\n"
        for style in ("Thin", "Light", "Book", "Regular", "Medium", "Bold", "Heavy", "Black")
    )
    + "\nThis Font Software",
)


def test_a_researched_text_may_match_a_canonical_text_under_a_long_notice(canon: Canon) -> None:
    """Research of its exact bytes lets a canonical body under a notice longer than the word
    limits verify; the whole text is still read for restrictions and other licenses."""
    text = ofl(NAMES_NOTICE)
    assert canon.match(text) == "OFL-1.1"
    assert " words besides copyright" in fatal(check(make_inputs(canon), text=text))[0]
    entry = license_l3.ResearchedText(sha(text), ("OFL-1.1",), TEXT_URL, "test")
    inputs = replace(make_inputs(canon), research=research(texts=[entry]))
    result = check(inputs, text=text)
    assert (result.level, result.matched, fatal(result)) == ("L3", "OFL-1.1", [])
    assert result.problems[0].startswith(f"note: license text {TEXT_URL} is a researched text")
    limited = ofl(NAMES_NOTICE.replace("Thin Italic", "Thin Italic, for personal use"))
    entry = license_l3.ResearchedText(sha(limited), ("OFL-1.1",), TEXT_URL, "test")
    result = check(replace(inputs, research=research(texts=[entry])), text=limited)
    assert result.level == "failed"
    assert any(p.endswith("restricts use: 'personal use'") for p in fatal(result)), fatal(result)


def test_a_mentions_allowance_lets_a_name_id_name_that_license_only(canon: Canon) -> None:
    facts = Facts(FONT_SHA, license_description=HACK_13, license_url=None)
    assert fatal(check_hack(hack_inputs(canon, families=[]), facts=facts)) == [
        f"license text {HACK_URL} names Public-domain",
        "name ID 13 names Public-domain",
    ]
    inputs = hack_inputs(canon)
    assert check_hack(inputs, facts=facts).level == "L3"
    # The allowance names a license; it never lets a restriction or another license through.
    for said, problem in (
        (HACK_13 + " Free for personal use only.", "name ID 13 restricts use: 'personal use'"),
        (HACK_13 + " Or the GPL.", "name ID 13 names GPL"),
    ):
        facts = Facts(FONT_SHA, license_description=said, license_url=None)
        assert fatal(check_hack(inputs, facts=facts)) == [problem]


def test_a_mentions_allowance_must_name_an_allowed_license() -> None:
    from tff_catalog.config_model import ConfigError

    allowed = ("OFL-1.1", "Unlicense", "GPL-2.0-or-later WITH Font-exception-2.0")
    for ok in ("Public-domain", "CC0", "OFL"):
        fam = license_l3.FamilyResearch("example-sans", "Example", "test", mentions=(ok,))
        license_l3.check_mentions(research(families=[fam]), allowed)
    for bad in ("GPL", "CC-BY-SA", "Apache"):  # plain GPL is not the GPL with the font exception
        fam = license_l3.FamilyResearch("example-sans", "Example", "test", mentions=(bad,))
        with pytest.raises(ConfigError, match="not the family of an allowed license"):
            license_l3.check_mentions(research(families=[fam]), allowed)


def test_researched_texts_and_files_are_tried_first(canon: Canon) -> None:
    upstream_text = f"{REPO}/upstream/OFL.txt"
    upstream_file = f"{REPO}/upstream/ExampleSans-Regular.ttf"
    fam = license_l3.FamilyResearch(
        "example-sans", "Example", "test", texts=(upstream_text,), files=(upstream_file,)
    )
    old = make_inputs(canon).evidence
    evidence = license_l3.apply_research(old, research(families=[fam]))
    assert [t.url for t in evidence["example-sans"].texts] == [upstream_text, TEXT_URL]
    assert evidence["example-sans"].texts[0].source == "research"
    assert [f.url for f in evidence["example-sans"].files] == [upstream_file, FONT_URL]
    # The sources' web page is never reached once the upstream text verifies.
    inputs = replace(make_inputs(canon), evidence=evidence)
    texts = Texts({upstream_text: ofl(), TEXT_URL: "<!doctype html><html>OFL</html>"})
    reader = FontReader({upstream_file: Facts(FONT_SHA)})
    result = license_l3.check_family("example-sans", inputs, texts, reader, DAY)
    assert (result.level, result.text_url, texts.asked) == ("L3", upstream_text, [upstream_text])
    assert result.font_file is not None
    assert result.font_file.url == upstream_file


def write_research(tmp_path: Path, body: str) -> Paths:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "license-texts.toml").write_text("schema = 1\n" + body)
    return Paths.for_root(tmp_path)


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\nnote = "x"\n', "unknown key"),
        ('[[family]]\nfamily = "A b"\nname = "A"\nreason = "r"\n', "is not a family id"),
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\n' * 2, "listed twice"),
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\ntexts = ["http://x.example/OFL"]\n',
         "is not an https URL"),
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\nfiles = ["https://x.example/OFL.txt"]\n',
         "names no font file"),
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\ntexts = ["https://x.example/A-1.zip"]\n',
         "names no file in a zip archive"),
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\ntexts = ["https://x.example/OFL#a"]\n',
         "names no file in a zip archive"),
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\nmentions = ["Nice"]\n',
         "is no license family"),
        ('[[text]]\nsha256 = "abc"\nlicenses = ["MIT"]\nurl = "https://x.example/L"\nreason = "r"\n',
         "is not a sha256"),
        (f'[[text]]\nsha256 = "{"a" * 64}"\nlicenses = ["MIT OR X"]\nurl = "https://x.example/L"\n'
         'reason = "r"\n', "is not one license id"),
        (f'[[text]]\nsha256 = "{"a" * 64}"\nlicenses = []\nurl = "https://x.example/L"\n'
         'reason = "r"\n', "empty"),
    ],
    ids=["unknown-key", "bad-id", "twice", "http", "not-a-font", "bare-archive", "fragment",
         "mention", "sha", "expression", "no-licenses"],
)  # fmt: skip
def test_load_research_is_strict(tmp_path: Path, body: str, error: str) -> None:
    from tff_catalog.config_model import ConfigError

    with pytest.raises(ConfigError, match=error):
        license_l3.load_research(write_research(tmp_path, body))


def test_load_research_without_a_file(tmp_path: Path) -> None:
    assert license_l3.load_research(Paths.for_root(tmp_path)) == license_l3.Research()


# Upstreams with no repository to pin: GUST publishes its license only on its own site, and
# these release archives hold the only copy of theirs.
UNPINNED_UPSTREAMS = (
    "https://www.gust.org.pl/",
    "https://www.evertype.com/fonts/nko/ConakryFont.zip#",
    "https://practicaltypography.com/fonts/Charter%20210112.zip#",
    "https://software.sil.org/downloads/r/ezra/EzraSIL-2.51.zip#",
    "https://software.sil.org/downloads/r/scheherazade/Scheherazade-2.100.zip#",
    "https://software.sil.org/downloads/r/sophianubian/SophiaNubian-1.0.zip#",
)


def test_the_real_research_file_loads_and_allows_only_allowed_licenses() -> None:
    """config/license-texts.toml: strict, every allowance is an allowed license's family,
    every researched text verifies only allowed licenses, and texts are pinned where the
    upstream has commits (``UNPINNED_UPSTREAMS`` has none)."""
    found = license_l3.load_research(Paths.for_root(ROOT))
    cfg = from_mapping(
        LicensesConfig, load_toml(ROOT / "config" / "licenses.toml"), where="licenses.toml"
    )
    allowed = frozenset(cfg.allowed) | frozenset(cfg.ruling)  # gate LIC ruled these in
    license_l3.check_mentions(found, allowed)
    pairs = license_l3.allowed_pairs(allowed)
    for entry in found.texts.values():
        for spdx in entry.licenses:
            assert license_l3.leaf_allowed(license_l3._Leaf(spdx), pairs), spdx
    for fam in found.families.values():
        for url in fam.texts:
            assert license_l3.is_pinned(url) or url.startswith(UNPINNED_UPSTREAMS), url
    assert len(found.families) >= 20
    assert len(found.texts) >= 5


def test_a_family_the_last_run_failed_is_checked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage "rank" leaves out the families the last verify failed, so no rank puts them
    in scope: verify checks them again all the same, and one fixed since comes back."""
    ctx = stage_context(tmp_path)
    write_stage_files(ctx.paths)
    texts = Texts({f"{REPO}/ofl/{f.replace('-', '')}/OFL.txt": ofl() for f in FAMILIES})
    gamma = f"{REPO}/ofl/gammamono/gamma-mono.ttf"
    facts = {f"{REPO}/ofl/{f.replace('-', '')}/{f}.ttf": Facts(sha(f)) for f in FAMILIES}
    facts[gamma] = Facts(sha("gamma-mono"), license_description="Licensed under the Apache License")

    @contextmanager
    def fake_sources(ctx: StageContext, *, persist: bool = True) -> Iterator[tuple[Any, Any]]:
        yield texts, FontReader(facts)

    monkeypatch.setattr(license_l3, "open_sources", fake_sources)
    monkeypatch.setattr(license_l3, "load_l3_rulings", lambda paths, log=None: {})
    license_l3.run(ctx)
    assert license_l3.exclusions(ctx.paths) == {"gamma-mono"}
    ranks = stageio.load_stage(ctx.paths, "ranks")
    overall = {fid: p for fid, p in ranks["overall"].items() if fid != "gamma-mono"}
    stageio.dump_stage(ctx.paths, "ranks", {"overall": overall})  # as rank leaves it out
    facts[gamma] = Facts(sha("gamma-mono"))  # upstream fixed its name table
    license_l3.run(ctx)
    assert stageio.load_stage(ctx.paths, "l3")["gamma-mono"].level == "L3"
    assert license_l3.exclusions(ctx.paths) == frozenset()


def test_checked_on_is_kept_while_nothing_changes(canon: Canon) -> None:
    first = check(make_inputs(canon))
    later = check(
        make_inputs(
            canon, previous={"example-sans": previous_entry(first, checked_on="2026-09-03")}
        )
    )
    assert later.checked_on == LAST_MONTH
    moved = previous_entry(first, checked_on="2026-09-03", text_sha256="0" * 64)
    assert check(make_inputs(canon, previous={"example-sans": moved})).checked_on == DAY


def test_license_hash_change_requeues(canon: Canon) -> None:
    """Done-when test of step 6b: a changed license hash puts the font back in the queue."""
    before = check(make_inputs(canon))
    state = {"example-sans": previous_entry(before, checked_on="2026-09-03")}
    same = make_inputs(canon, previous=state)
    unchanged = check(same)
    assert license_l3.build_queue({"example-sans": unchanged}, same).items == ()

    # The upstream text changes: a new year in its copyright line is enough.
    newer = ofl(OFL_NOTICE.replace("2021", "2021-2026"))
    after = check(same, text=newer)
    assert after.level == "L3"  # the text still matches the OFL
    assert after.checked_on == DAY
    queue = license_l3.build_queue({"example-sans": after}, same)
    [item] = queue.items
    assert (item.id, item.reason, item.needs_owner) == ("L3-example-sans", "changed", False)
    assert (item.previous_sha256, item.text_sha256) == (sha(ofl()), sha(newer))
    assert queue.counts["changed"] == 1

    # A text that changed into something else needs the owner.
    worse = check(same, text=ofl().replace("not sold by themselves", "sold"))
    [item] = license_l3.build_queue({"example-sans": worse}, same).items
    assert (item.reason, item.level, item.needs_owner) == ("changed", "failed", True)
    assert item.question is not None
    assert item.question.id == "L3-example-sans"
    assert len(item.question.options) == 3


def ruling(choice: str, text: str | None) -> dict[str, L3Ruling]:
    covered = license_l3.NO_TEXT if text is None else sha(text)
    return {"example-sans": L3Ruling("example-sans", LAST_MONTH, choice, covered)}  # type: ignore[arg-type]


BAD_TEXT = ofl().replace("not sold by themselves", "sold")


def test_a_keep_ruling_holds_only_for_its_text(canon: Canon) -> None:
    inputs = make_inputs(canon, rulings=ruling("b", BAD_TEXT))
    kept = check(inputs, text=BAD_TEXT)
    assert kept.level == "ruling"
    assert "owner ruling L3-example-sans (2026-09-03): keep" in kept.problems
    assert license_l3.build_queue({"example-sans": kept}, inputs).items == ()

    # The upstream text changes again: the ruling no longer covers it.
    other = BAD_TEXT.replace("PREAMBLE", "PREAMBLE\n\nFonts are nice.")
    state = {"example-sans": previous_entry(kept)}
    changed = check(replace(inputs, previous=state), text=other)
    assert changed.level == "failed"
    [item] = license_l3.build_queue(
        {"example-sans": changed}, replace(inputs, previous=state)
    ).items
    assert (item.reason, item.needs_owner) == ("changed", True)


def test_an_exclude_ruling_holds_even_for_a_clean_text(canon: Canon) -> None:
    inputs = make_inputs(canon, rulings=ruling("a", ofl()))
    result = check(inputs)
    assert result.level == "failed"
    assert "owner ruling L3-example-sans (2026-09-03): exclude" in result.problems
    assert license_l3.build_queue({"example-sans": result}, inputs).items == ()


def test_a_research_ruling_stays_in_the_queue(canon: Canon) -> None:
    inputs = make_inputs(canon, rulings=ruling("c", BAD_TEXT))
    result = check(inputs, text=BAD_TEXT)
    [item] = license_l3.build_queue({"example-sans": result}, inputs).items
    assert (item.reason, item.level, item.needs_owner) == ("research", "failed", True)


def test_an_exclusion_holds_after_the_text_changes_until_the_owner_rules_again(
    canon: Canon,
) -> None:
    inputs = make_inputs(canon, rulings=ruling("a", BAD_TEXT))
    excluded = check(inputs, text=BAD_TEXT)
    assert excluded.level == "failed"
    later = replace(inputs, previous={"example-sans": previous_entry(excluded)})
    fixed = check(later)  # upstream's text is a clean OFL now
    assert fixed.level == "failed"
    assert fatal(fixed) == [
        f"owner ruling L3-example-sans (2026-09-03) excluded it for license text "
        f"{sha(BAD_TEXT)}; the text changed, so the owner rules again"
    ]
    [item] = license_l3.build_queue({"example-sans": fixed}, later).items
    assert (item.reason, item.needs_owner) == ("changed", True)
    assert item.question is not None
    assert reviews.pins(item.question) == {"text_sha256": sha(ofl())}
    # A ruling on the new text decides it.
    assert check(replace(later, rulings=ruling("b", ofl()))).level == "L3"


def test_a_network_failure_keeps_last_months_level(canon: Canon) -> None:
    first = check(make_inputs(canon))
    assert first.text_sha256 is not None
    state = {"example-sans": previous_entry(first, checked_on="2026-09-03")}
    kept = check(make_inputs(canon, previous=state), text=503, lately={first.text_sha256})
    assert kept.level == "L3"
    assert (kept.text_sha256, kept.checked_on) == (first.text_sha256, LAST_MONTH)
    assert any("kept the level checked on 2026-09-03" in p for p in kept.problems)
    assert check(make_inputs(canon), text=503).level == "failed"  # nothing to keep


def test_a_network_failure_keeps_the_level_only_for_the_stale_window(canon: Canon) -> None:
    """Like a failed source (methodology "Outages"): once no snapshot of the stale window
    holds the verified text, a host that stays down fails the family."""
    first = check(make_inputs(canon))
    state = {"example-sans": previous_entry(first, checked_on="2025-01-03")}
    gone = check(make_inputs(canon, previous=state), text=503)
    assert gone.level == "failed"
    assert fatal(gone) == [
        f"license text {TEXT_URL}: HTTP 503",
        "the verified text was not read in the stale window ([stale] max_months), "
        "so its level is not kept",
    ]
    # A text that is really gone (404) never keeps the level, however recent.
    assert first.text_sha256 is not None
    lost = check(make_inputs(canon, previous=state), text=404, lately={first.text_sha256})
    assert fatal(lost) == [f"license text {TEXT_URL}: HTTP 404"]


def test_next_license_hashes_keeps_families_out_of_scope(canon: Canon) -> None:
    result = check(make_inputs(canon))
    old = {"zeta-mono": {"level": "L3", "text_sha256": "1" * 64}}
    out = license_l3.next_license_hashes(old, {"example-sans": result})
    assert list(out) == ["example-sans", "zeta-mono"]
    assert out["example-sans"] == {
        "checked_on": "2026-10-03",
        "font_file": {"sha256": FONT_SHA, "url": FONT_URL},
        "font_version": "Version 1.001",
        "level": "L3",
        "text_sha256": sha(ofl()),
        "text_url": TEXT_URL,
    }


# --- the store-backed text source ---------------------------------------------------------------


@dataclass
class FakeSnapshot:
    date: date
    rows: list[dict[str, Any]]

    def has(self, name: str) -> bool:
        return name == license_l3.TEXTS_EXTRACT

    def iter_jsonl(self, name: str) -> Iterator[dict[str, Any]]:
        yield from self.rows


@dataclass
class FakeWriter:
    store: FakeStore
    day: date
    refetch: bool
    data: bytes = b""
    fetches: list[Any] = field(default_factory=list)

    def write_bytes(self, name: str, data: bytes, *, rows: int | None = None) -> None:
        assert name == license_l3.TEXTS_EXTRACT
        self.data = data

    def record_fetch(self, record: Any) -> None:
        self.fetches.append(record)

    def set_data_date(self, day: date) -> None:
        pass

    def note(self, text: str) -> None:
        pass

    def __enter__(self) -> FakeWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        rows = [json.loads(line) for line in self.data.splitlines()]
        self.store.snapshots[self.day] = FakeSnapshot(self.day, rows)
        self.store.written.append(self)


@dataclass
class FakeStore:
    snapshots: dict[date, FakeSnapshot] = field(default_factory=dict)
    written: list[FakeWriter] = field(default_factory=list)

    def snapshot(self, source: str, day: date) -> FakeSnapshot | None:
        assert source == license_l3.PSEUDO_SOURCE
        return self.snapshots.get(day)

    def dates(self, source: str) -> list[date]:
        assert source == license_l3.PSEUDO_SOURCE
        return sorted(self.snapshots)

    def latest(self, source: str, on_or_before: date) -> FakeSnapshot | None:
        days = [d for d in self.snapshots if d <= on_or_before]
        return self.snapshots[max(days)] if days else None

    def writer(
        self, source: str, day: date, version: int, *, refetch: bool, frozen: Any
    ) -> FakeWriter:
        assert day not in frozen
        return FakeWriter(self, day, refetch)


@dataclass
class FakeResult:
    url: str
    status: int
    content: bytes
    record: Any = None  # a store.FetchRecord, for the real store
    redirects: tuple[str, ...] = ()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest() if self.status == 200 else ""

    @property
    def size(self) -> int:
        return len(self.content)

    def to_record(self) -> Any:
        return self.record or {"url": self.url, "status": self.status}

    def to_records(self) -> tuple[Any, ...]:
        first = self.to_record()
        hops = (
            replace(first, url=hop) if self.record else {**first, "url": hop}
            for hop in self.redirects
        )
        return (first, *hops)


class FakeFetcher:
    def __init__(self, bodies: dict[str, bytes | int]) -> None:
        self.bodies = bodies
        self.asked: list[str] = []

    def get(self, url: str, *, expect: Iterable[int] = (200,)) -> FakeResult:
        self.asked.append(url)
        body = self.bodies.get(url, 404)
        if isinstance(body, int):
            assert body in expect
            return FakeResult(url, body, b"")
        return FakeResult(url, 200, body)


def store_texts(store: FakeStore, fetcher: FakeFetcher | None, **kw: Any) -> license_l3.StoreTexts:
    return license_l3.StoreTexts(cast("Any", store), DAY, cast("Any", fetcher), persist=True, **kw)


def test_store_texts_fetches_saves_and_replays() -> None:
    other = "https://fonts.example/LICENSE"
    store = FakeStore()
    fetcher = FakeFetcher({TEXT_URL: ofl().encode(), other: 404})
    live = store_texts(store, fetcher)
    assert live.get(TextRef(TEXT_URL)).text == ofl()
    assert live.get(TextRef(other)).status == 404
    live.close()
    [written] = store.written
    assert [r["url"] for r in store.snapshots[DAY].rows] == [other, TEXT_URL]
    assert len(written.fetches) == 2

    # Replay (no fetcher) and a second run the same day read today's snapshot only.
    hints = ("", "; rerun verify with --refetch")
    for again, hint in zip(
        (store_texts(store, None), store_texts(store, FakeFetcher({}))), hints, strict=True
    ):
        assert again.get(TextRef(TEXT_URL)).sha256 == sha(ofl())
        assert again.get(TextRef(other)).status == 404
        missing = again.get(TextRef("https://fonts.example/NEW"))
        assert (missing.status, missing.error) == (
            0,
            "not in the license_texts snapshot of 2026-10-03" + hint,
        )
        again.close()
    assert len(store.written) == 1


def test_store_texts_reuses_known_texts() -> None:
    unpinned = "https://fonts.example/LICENSE"
    store = FakeStore()
    store.snapshots[LAST_MONTH] = FakeSnapshot(
        LAST_MONTH,
        [
            FetchedText(TEXT_URL, 200, sha(ofl()), ofl()).to_json(),
            FetchedText(unpinned, 200, sha(ofl()), ofl()).to_json(),
        ],
    )
    fetcher = FakeFetcher({unpinned: ofl().encode()})
    texts = store_texts(store, fetcher)
    moved = f"{REPO.replace(COMMIT, 'f' * 40)}/ofl/examplesans/OFL.txt"  # next month's commit
    assert texts.get(TextRef(TEXT_URL)).ok  # pinned: reused
    assert texts.get(TextRef(moved, sha256=sha(ofl()))).url == moved  # same hash: reused
    assert texts.get(TextRef(unpinned)).ok  # not pinned: fetched again
    assert fetcher.asked == [unpinned]
    assert (texts.fetched, texts.reused) == (1, 2)


class RecordingFetcher(FakeFetcher):
    """A fake fetcher whose results give ``store.FetchRecord``s, for the real store."""

    def __init__(
        self, bodies: dict[str, bytes | int], redirects: dict[str, tuple[str, ...]] | None = None
    ) -> None:
        super().__init__(bodies)
        self.redirects = redirects or {}

    def get(self, url: str, *, expect: Iterable[int] = (200,)) -> FakeResult:
        result = replace(super().get(url, expect=expect), redirects=self.redirects.get(url, ()))
        record = FetchRecord(
            url=url,
            status=result.status,
            fetched_at="2026-10-03T06:00:00Z",
            sha256=hashlib.sha256(result.content).hexdigest(),
            bytes=result.size,
        )
        return replace(result, record=record)


def test_store_texts_with_the_real_store(tmp_path: Path) -> None:
    """The pseudo-source round trip through ``tff_catalog.store``: save live, read in replay."""
    from tff_catalog.store import Store

    store = Store(tmp_path / "store")
    (tmp_path / "store").mkdir()
    fetcher = RecordingFetcher(
        {TEXT_URL: ofl().encode(), "https://fonts.example/GONE": 410},
        redirects={TEXT_URL: (f"{TEXT_URL}?moved",)},
    )
    live = license_l3.StoreTexts(store, DAY, cast("Any", fetcher), persist=True)
    live.get(TextRef(TEXT_URL))
    live.get(TextRef("https://fonts.example/GONE"))
    live.close()
    snap = store.snapshot(license_l3.PSEUDO_SOURCE, DAY)
    assert snap is not None
    # Every URL reached is listed, redirects included.
    assert [f.url for f in snap.manifest.fetched] == [
        TEXT_URL,
        f"{TEXT_URL}?moved",
        "https://fonts.example/GONE",
    ]
    replay = license_l3.StoreTexts(store, DAY, None, persist=True)
    assert replay.get(TextRef(TEXT_URL)) == FetchedText(TEXT_URL, 200, sha(ofl()), ofl())
    assert replay.get(TextRef("https://fonts.example/GONE")).status == 410


def test_store_texts_read_lately_looks_at_the_stale_window_before_the_run(
    tmp_path: Path,
) -> None:
    """Texts read in the window before the run date count; older ones, failed reads and
    the run's own snapshot do not (a replay reads that one, so it must not decide)."""
    from datetime import timedelta

    from tff_catalog.store import Store

    (tmp_path / "store").mkdir()
    store = Store(tmp_path / "store")
    bodies = {day: f"{ofl()}\n{day.isoformat()}\n" for day in (date(2026, 7, 3), LAST_MONTH, DAY)}
    for day, body in bodies.items():
        url = f"https://fonts.example/{day.isoformat()}/OFL.txt"
        gone = f"https://fonts.example/{day.isoformat()}/GONE"
        fetcher = RecordingFetcher({url: body.encode(), gone: 404})
        live = license_l3.StoreTexts(store, day, cast("Any", fetcher), persist=True)
        assert live.get(TextRef(url)).ok
        assert live.get(TextRef(gone)).status == 404
        live.close()
    for fetcher in (None, RecordingFetcher({})):  # replay, and a second run the same day
        texts = license_l3.StoreTexts(
            store, DAY, cast("Any", fetcher), persist=False, window=timedelta(days=62)
        )
        assert texts.read_lately(sha(bodies[LAST_MONTH]))
        assert not texts.read_lately(sha(bodies[date(2026, 7, 3)]))  # 92 days before
        assert not texts.read_lately(sha(bodies[DAY]))  # the run's own snapshot
        assert not texts.read_lately(sha(""))


def small_font(license_description: str = OFL_NAME_13) -> bytes:
    """A tiny valid TrueType font (fontTools' FontBuilder); the same arguments, the same bytes."""
    import io

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    fb = FontBuilder(1000, isTTF=True)
    fb.font["head"].created = fb.font["head"].modified = 3_000_000_000
    names = [".notdef", "A"]
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({0x41: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 0))
    pen.closePath()
    fb.setupGlyf({n: pen.glyph() for n in names})
    fb.setupHorizontalMetrics(dict.fromkeys(names, (500, 0)))
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable(
        {
            "familyName": "Example Sans",
            "styleName": "Regular",
            "version": "Version 1.001",
            "licenseDescription": license_description,
            "licenseInfoURL": "https://openfontlicense.org",
        }
    )
    fb.setupOS2()
    fb.setupPost()
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


class FontServer:
    """The one ``Fetcher`` call ``fontfiles.facts_for`` makes for a file without a sha256."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.asked: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResult:
        from tff_catalog.fetch import FetchError

        self.asked.append(url)
        if url not in self.files:
            raise FetchError(f"GET {url}: HTTP 404, expected [200]")
        return FakeResult(url, 200, self.files[url])


def test_store_facts_reads_live_and_replays_offline(tmp_path: Path) -> None:
    """The font_facts pseudo-source: a live run caches the facts and records its reads
    (failures too); a replay answers the same from the store, with no network."""
    from tff_catalog import fontfiles
    from tff_catalog.store import Store

    (tmp_path / "store").mkdir()
    store = Store(tmp_path / "store")
    font = small_font()
    gone = f"{REPO}/ofl/examplesans/Gone.ttf"
    server = FontServer({FONT_URL: font})
    live = license_l3.StoreFacts(store, DAY, cast("Any", server), persist=True)
    facts, error = live.get(FontFileRef(FONT_URL))
    assert error is None
    assert facts is not None
    assert (facts.sha256, facts.version) == (hashlib.sha256(font).hexdigest(), "Version 1.001")
    assert (facts.license_description, facts.license_url) == (
        OFL_NAME_13,
        "https://openfontlicense.org",
    )
    missing, why = live.get(FontFileRef(gone))
    assert missing is None
    assert why is not None
    assert "HTTP 404" in why
    live.close()
    reads = fontfiles.recorded_reads(store, DAY, prefer=license_l3.STAGE)
    assert sorted(reads) == sorted([gone, FONT_URL])
    assert reads[FONT_URL].sha256 == facts.sha256

    replay = license_l3.StoreFacts(store, DAY, None, persist=True)
    assert replay.get(FontFileRef(FONT_URL)) == (facts, None)
    assert replay.get(FontFileRef(gone)) == (None, why)
    never = replay.get(FontFileRef(f"{REPO}/ofl/examplesans/Never.ttf"))
    assert never == (None, "no cached facts and no network (replay)")
    assert server.asked == [FONT_URL, gone]  # the replay fetched nothing


def test_the_font_file_notes(canon: Canon) -> None:
    """An unpinned file, or one whose sha256 differs from the source's, is a note."""
    loose = "https://fonts.example/ExampleSans-Regular.ttf"
    ev = {
        "example-sans": Evidence(
            texts=(TextRef(TEXT_URL),), files=(FontFileRef(loose, sha256="0" * 64),)
        )
    }
    reader = FontReader({loose: Facts(FONT_SHA)})
    result = license_l3.check_family(
        "example-sans", make_inputs(canon, evidence=ev), Texts({TEXT_URL: ofl()}), reader, DAY
    )
    assert result.level == "L3"
    assert result.problems == (
        f"note: font file {loose} is not pinned to a commit or version",
        f"note: font file sha256 {FONT_SHA} differs from the source's {'0' * 64}",
    )


def test_store_texts_refuses_oversized_texts() -> None:
    fetcher = FakeFetcher({TEXT_URL: b"x" * (license_l3.MAX_TEXT_BYTES + 1)})
    row = store_texts(FakeStore(), fetcher).get(TextRef(TEXT_URL))
    assert (row.status, row.text, row.ok) == (200, None, False)


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_store_texts_reads_a_text_inside_a_release_archive() -> None:
    archive = "https://fonts.example/downloads/Example-1.0.zip"
    member = f"{archive}#Example-1.0/OFL.txt"
    files = {"Example-1.0/OFL.txt": ofl().encode(), "Example-1.0/ExampleSans.ttf": b"font"}
    fetcher = FakeFetcher({archive: zip_bytes(files)})
    store = FakeStore()
    live = store_texts(store, fetcher)
    row = live.get(TextRef(member))
    assert (row.status, row.sha256, row.text) == (200, sha(ofl()), ofl())  # the member's
    assert live.get(TextRef(f"{archive}#OFL.txt")).sha256 == sha(ofl())  # the one path ending in it
    missing = live.get(TextRef(f"{archive}#Example-1.0/LICENSE"))
    assert (missing.status, missing.ok) == (404, False)
    assert missing.error == f"{archive}: no archive entry 'Example-1.0/LICENSE'"
    assert fetcher.asked == [archive] * 3
    live.close()
    replay = store_texts(store, None)
    assert replay.get(TextRef(member)).text == ofl()


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"not a zip archive", "not a readable zip archive"),
        (zip_bytes({"a/OFL.txt": b"x", "b/OFL.txt": b"x"}), "2 archive entries match 'OFL.txt'"),
        (
            zip_bytes({"OFL.txt": b"x" * (license_l3.MAX_TEXT_BYTES + 1)}),
            "too large for a license",
        ),
    ],
    ids=["not-a-zip", "ambiguous", "too-large"],
)
def test_store_texts_refuses_a_bad_archive_member(body: bytes, error: str) -> None:
    archive = "https://fonts.example/Example-1.0.zip"
    row = store_texts(FakeStore(), FakeFetcher({archive: body})).get(TextRef(f"{archive}#OFL.txt"))
    assert (row.status, row.ok) == (200, False)
    assert error in (row.error or "")


def test_decode_text(canon: Canon) -> None:
    assert license_l3.decode_text("\ufeffLicence é".encode()) == "Licence é"
    assert license_l3.decode_text("Licence é".encode("latin-1")) == "Licence é"
    for codec in ("utf-16", "utf-16-be"):  # with a BOM, as Windows editors save it
        data = ("\ufeff" if codec == "utf-16-be" else "") + ofl()
        assert canon.match(license_l3.decode_text(data.encode(codec))) == "OFL-1.1"


# --- the stage ---------------------------------------------------------------------------------

RANKING = from_mapping(
    RankingConfig, load_toml(ROOT / "config" / "ranking.toml"), where="ranking.toml"
)


def config(allowed: Iterable[str] = ("OFL-1.1",), ruling: Iterable[str] = ()) -> Config:
    lic = LicensesConfig(
        schema=1,
        allowed={
            a: AllowedLicense(
                name=a, group="open", redistributable=True, attribution_required=False
            )
            for a in allowed
        },
        excluded={},
        ruling={r: RulingLicense(question=f"Does {r} qualify?") for r in ruling},
    )
    return cast("Config", Config(RANKING, lic, *([None] * 4), sources={}))  # type: ignore[arg-type]


FAMILIES = ("alpha-sans", "beta-serif", "gamma-mono")


def write_stage_files(paths: Paths) -> None:
    """Three synthetic families, all in scope: alpha and beta are clean, gamma's name
    table says Apache."""
    families = {}
    records: list[Record] = []
    for fid in FAMILIES:
        key = SourceKey("gf-dir", fid.replace("-", ""))
        families[fid] = Family(
            id=fid,
            family=fid.replace("-", " ").title(),
            keys=(key,),
            sources=("google_repo",),
            first_seen=LAST_MONTH,
            minted_from=fid,
        )
        files = (FontFileRef(f"{REPO}/ofl/{key.key}/{fid}.ttf"),)
        records.append(UniverseRecord(source="google_repo", key=key, family=fid, files=files))
        text_url = f"{REPO}/ofl/{key.key}/OFL.txt"
        records.append(LicenseFact(source="google_repo", key=key, raw="OFL", text_url=text_url))
    write_jsonl(records, paths.records / "google_repo.jsonl")
    stageio.dump_stage(paths, "universe", Universe(families=families, unmapped=()))
    stageio.dump_stage(
        paths, "licenses", {fid: replace(verdict(), family_id=fid) for fid in FAMILIES}
    )
    stageio.dump_stage(
        paths,
        "ranks",
        {"overall": {fid: place(i * 300 + 1) for i, fid in enumerate(FAMILIES)}},
    )
    member = MemberState(member=True, entered=LAST_MONTH, runs_outside=0)
    outside = MemberState(member=False, entered=None, runs_outside=0)
    stageio.dump_stage(
        paths,
        "membership",
        Membership(
            catalog={"alpha-sans": member, "beta-serif": member, "gamma-mono": outside},
            top100={},
        ),
    )


def stage_context(tmp_path: Path, previous: dict[str, Any] | None = None) -> StageContext:
    paths = Paths.for_root(tmp_path).with_(data=ROOT / "data")
    return StageContext(
        paths=paths,
        config=config(),
        state=State(license_hashes=previous or {}),
        run_date=DAY,
        store=None,
        fetcher=None,
        log=logging.getLogger("test_license_l3"),
        options=RunOptions(),
    )


@pytest.fixture
def stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StageContext:
    """A context whose sources are fakes: every text is the OFL; gamma's name table says Apache."""
    ctx = stage_context(tmp_path)
    write_stage_files(ctx.paths)
    texts = Texts({f"{REPO}/ofl/{f.replace('-', '')}/OFL.txt": ofl() for f in FAMILIES})
    facts = {f"{REPO}/ofl/{f.replace('-', '')}/{f}.ttf": Facts(sha(f)) for f in FAMILIES}
    facts[f"{REPO}/ofl/gammamono/gamma-mono.ttf"] = Facts(
        sha("gamma-mono"), license_description="Licensed under the Apache License, Version 2.0"
    )

    @contextmanager
    def fake_sources(ctx: StageContext, *, persist: bool = True) -> Iterator[tuple[Any, Any]]:
        yield texts, FontReader(facts)

    monkeypatch.setattr(license_l3, "open_sources", fake_sources)
    monkeypatch.setattr(license_l3, "load_l3_rulings", lambda paths, log=None: {})
    return ctx


def test_run_writes_results_queue_and_state(stage: StageContext) -> None:
    license_l3.run(stage)
    results = stageio.load_stage(stage.paths, "l3")
    assert {fid: r.level for fid, r in results.items()} == {
        "alpha-sans": "L3",
        "beta-serif": "L3",
        "gamma-mono": "failed",
    }
    queue = license_l3.load_queue(stage.paths)
    assert [(i.id, i.reason, i.needs_owner) for i in queue.items] == [
        ("L3-gamma-mono", "failed", True)
    ]
    assert queue.counts == {
        "L3": 2,
        "changed": 0,
        "checked": 3,
        "failed": 1,
        "needs_owner": 1,
        "ruling": 0,
    }
    [question] = license_l3.questions(stage.paths)
    assert question.gate == "L3"
    assert "name ID 13 names Apache" in question.text
    hashes = jsonio.load(stage.paths.next_state / "license_hashes.json")
    assert sorted(hashes) == list(FAMILIES)
    assert hashes["alpha-sans"]["font_file"] == {
        "sha256": sha("alpha-sans"),
        "url": f"{REPO}/ofl/alphasans/alpha-sans.ttf",
    }
    assert license_l3.exclusions(stage.paths) == {"gamma-mono"}


def test_run_is_deterministic(stage: StageContext) -> None:
    outputs = [
        stageio.stage_path(stage.paths, "l3"),
        license_l3.queue_path(stage.paths),
        stage.paths.next_state / "license_hashes.json",
    ]
    license_l3.run(stage)
    first = [p.read_bytes() for p in outputs]
    license_l3.run(stage)
    assert [p.read_bytes() for p in outputs] == first


def test_verify_checks_one_family(stage: StageContext) -> None:
    universe = stageio.load_stage(stage.paths, "universe")
    result = license_l3.verify(universe.families["beta-serif"], stage)
    assert (result.family_id, result.level, result.matched) == ("beta-serif", "L3", "OFL-1.1")


def test_cmd_check(stage: StageContext, capsys: pytest.CaptureFixture[str]) -> None:
    assert license_l3.cmd_check(stage) == 1  # nothing verified yet
    license_l3.run(stage)
    assert license_l3.cmd_check(stage) == 0  # gamma failed, but it is not in the catalog
    out = capsys.readouterr().out
    assert "2 catalog fonts (L3 2, ruling 0, failed 0, unchecked 0)" in out
    membership = stageio.load_stage(stage.paths, "membership")
    member = MemberState(member=True, entered=DAY, runs_outside=0)
    catalog = {**membership.catalog, "gamma-mono": member}
    stageio.dump_stage(stage.paths, "membership", replace(membership, catalog=catalog))
    assert license_l3.cmd_check(stage) == 1
    assert "gamma-mono: name ID 13 names Apache" in capsys.readouterr().out


def test_exclusions_before_verify_ran(tmp_path: Path) -> None:
    assert license_l3.exclusions(Paths.for_root(tmp_path)) == frozenset()


def test_load_inputs_applies_gate_lic_license_rulings(tmp_path: Path) -> None:
    """A license the owner allowed at gate LIC is allowed for L3 too
    (``licenses.effective_config``), as it was for L2."""
    from tff_catalog import licenses

    data = tmp_path / "data"
    (data / "reviews" / "licenses").mkdir(parents=True)
    (data / "license-texts").symlink_to(TEXTS, target_is_directory=True)
    ctx = replace(
        stage_context(tmp_path),
        paths=Paths.for_root(tmp_path).with_(data=data),
        config=config(ruling=("Ubuntu-font-1.0",)),
    )
    write_stage_files(ctx.paths)
    assert "Ubuntu-font-1.0" not in license_l3.load_inputs(ctx)[0].allowed
    qid = licenses.license_question_id("Ubuntu-font-1.0")
    (data / "reviews" / "licenses" / "2026-10-01.toml").write_text(
        "# Synthetic gate LIC ruling for a test.\n\n"
        f"[{qid}]\n"
        'choice = "a"\n'
        "recommended = true\n"
        'ruling = "The Ubuntu Font Licence qualifies and is redistributable."\n'
        'reason = "Test data."\n'
    )
    assert "Ubuntu-font-1.0" in license_l3.load_inputs(ctx)[0].allowed


def test_a_ruling_on_a_family_without_a_text_goes_through_reviews(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate L3 round trip through the real ``reviews``: a failure with no license text
    pins ``text_sha256 = "none"`` (a rulings file holds no empty value), the owner's (b)
    answer is recorded with it, and the next run keeps the family at level "ruling"."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "license-texts").symlink_to(TEXTS, target_is_directory=True)
    ctx = replace(stage_context(tmp_path), paths=Paths.for_root(tmp_path).with_(data=data))
    write_stage_files(ctx.paths)
    texts = Texts({f"{REPO}/ofl/{f.replace('-', '')}/OFL.txt": ofl() for f in FAMILIES[:2]})
    facts = {f"{REPO}/ofl/{f.replace('-', '')}/{f}.ttf": Facts(sha(f)) for f in FAMILIES}

    @contextmanager
    def fake_sources(ctx: StageContext, *, persist: bool = True) -> Iterator[tuple[Any, Any]]:
        yield texts, FontReader(facts)

    monkeypatch.setattr(license_l3, "open_sources", fake_sources)
    license_l3.run(ctx)
    [question] = reviews.questions(ctx.paths, "L3")
    assert question.id == "L3-gamma-mono"
    assert reviews.pins(question) == {"text_sha256": license_l3.NO_TEXT}
    answers = tmp_path / "answers.toml"
    answers.write_text(
        'gate = "L3"\nday = 2026-10-03\n[L3-gamma-mono]\nchoice = "b"\nreason = "Test data."\n'
    )
    assert reviews.cmd_apply_rulings(ctx.paths, answers) == 0
    assert license_l3.load_l3_rulings(ctx.paths) == {
        "gamma-mono": L3Ruling("gamma-mono", DAY, "b", license_l3.NO_TEXT)
    }
    license_l3.run(ctx)
    assert stageio.load_stage(ctx.paths, "l3")["gamma-mono"].level == "ruling"
    assert reviews.questions(ctx.paths, "L3") == []
    assert license_l3.load_queue(ctx.paths).items == ()


def test_open_sources_keeps_merged_run_dates_immutable(tmp_path: Path) -> None:
    """The texts snapshot of a date a merged run used is never written (``State.frozen_dates``)."""
    from tff_catalog.store import SnapshotFrozen, Store

    (tmp_path / "store").mkdir()
    ctx = replace(
        stage_context(tmp_path),
        store=Store(tmp_path / "store"),
        fetcher=cast("Any", RecordingFetcher({TEXT_URL: ofl().encode()})),
        state=State(run_history=({"run_date": DAY.isoformat(), "snapshots": {}},)),
    )
    with pytest.raises(SnapshotFrozen), license_l3.open_sources(ctx) as (texts, _):
        assert texts.get(TextRef(TEXT_URL)).ok


# --- real data ---------------------------------------------------------------------------------


@pytest.mark.network
def test_real_google_fonts_and_spdx_texts_match(canon: Canon) -> None:
    """A pinned google/fonts OFL.txt and Apache LICENSE.txt, and SPDX's own texts."""
    import httpx

    from tff_catalog.fetch import USER_AGENT

    commit = "23e54b51ddffbc7713c583748e3bd86f62b1fa4a"  # google/fonts main on 2026-09-24
    cases = {
        f"https://raw.githubusercontent.com/google/fonts/{commit}/ofl/inter/OFL.txt": "OFL-1.1",
        f"https://raw.githubusercontent.com/google/fonts/{commit}/ofl/lato/OFL.txt": "OFL-1.1",
        f"https://raw.githubusercontent.com/google/fonts/{commit}/apache/aclonica/LICENSE.txt": (
            "Apache-2.0"
        ),
        f"https://raw.githubusercontent.com/google/fonts/{commit}/ufl/ubuntu/UFL.txt": (
            "Ubuntu-font-1.0"
        ),
    }
    with httpx.Client(headers={"user-agent": USER_AGENT}, timeout=30) as client:
        for n, (url, spdx) in enumerate(cases.items()):
            time.sleep(1 if n else 0)  # one request a second to a host
            response = client.get(url)
            response.raise_for_status()
            text = license_l3.decode_text(response.content)
            assert canon.match(text) == spdx, url


@pytest.mark.network
def test_canonical_texts_are_the_spdx_release_texts() -> None:
    """data/license-texts/NOTICE: every <SPDX id>.txt and exception is SPDX's own file."""
    import httpx

    from tff_catalog.fetch import USER_AGENT

    commit = "31ba1a50e5397e00a304dbadc76531740e89ee48"  # spdx/license-list-data tag v3.29.0
    files = sorted(TEXTS.glob("*.txt")) + sorted(TEXTS.glob("exceptions/*.txt"))
    assert len(files) >= 35
    with httpx.Client(headers={"user-agent": USER_AGENT}, timeout=30) as client:
        for n, path in enumerate(files):
            time.sleep(1 if n else 0)  # one request a second to a host
            url = f"https://raw.githubusercontent.com/spdx/license-list-data/{commit}/text/{path.name}"
            response = client.get(url)
            response.raise_for_status()
            assert response.content == path.read_bytes(), path.name
