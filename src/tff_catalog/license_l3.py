"""Stage "verify": L3 license verification for catalog candidates (milestone-1 step 6b). Owner: agent P9.

For the overall top 700 and the top 150 of the project and both desktop views
(``ranking.toml`` ``membership.l3_overall_max`` and ``l3_extra_top``; the extra
ranks are ``membership.extra_ranks``), plus every current catalog member and
every family the last run failed (stage "rank" left those out, so no rank
would bring one back once it is fixed): fetch the upstream license text,
match its fingerprint against ``data/license-texts/<SPDX>.txt``, check
name-table IDs 13 and 14, and record text_url, sha256, checked_on,
font_version and ``font_file`` {url, sha256, size} (Milestone 2 builds previews
from that file; export publishes it only with its size, which comes from the
source or, when the source gives none, from the ``font_facts`` read, ``StoreFacts``).
Texts and font reads go through
the ``license_texts`` and ``font_facts`` pseudo-sources, so replay is offline.

A changed text hash puts the font back in the queue. A new exclusion makes
refresh rerun "rank" and "membership" once.

Writes ``build/stage/l3.json``, ``build/stage/queues/l3.json`` and
``build/state/license_hashes.json``.

Contracts:

- **Fingerprint.** ``fingerprint`` hashes a text's *body*: NFKC, casefolded,
  punctuation, URLs, list bullets and copyright lines dropped, British
  spellings folded (SPDX's equivalent words), and everything before the
  body's first words (``BODY_STARTS``: titles, copyright and Reserved Font Name
  statements, "This Font Software is licensed under ...") cut off. So every
  OFL-1.1 text of any family gives the same fingerprint, and a text with any
  other change to the license's own words gives another one.
- **Canonical texts** (``load_canon``): ``data/license-texts/<SPDX id>.txt``
  (the SPDX License List texts), other published forms of the same license in
  ``variants/<id>/*.txt`` (a ``LicenseRef-`` license the SPDX list lacks keeps
  all its texts there), and exception texts in ``exceptions/``. A text
  matches a license when its fingerprint equals one of that license's, also
  once the whole text of each exception it carries is taken out (``Canon.identify``:
  a COPYING file holding the GPL and the font exception). A notice that grants
  another license by reference verifies that one too (``GRANTS``: the GUST Font
  License grants LPPL-1.3c).
- **Research** (``config/license-texts.toml``, ``load_research``; owner ruling
  L3_32): per family, upstream texts and font files tried before the sources'
  own (``apply_research``; a text shipped only in a release archive is named
  like a font file in one, ``archive.zip#path``, ``archive_member``), and
  ``mentions``, license families its notices and
  name IDs may name besides its L2 expression (each the family of a license
  allowed on its own, ``check_mentions``); and researched texts, which verify the
  licenses listed for the sha256 of their bytes: texts that match no canonical
  text, or match one under a notice the word limits refuse.
  A researched text is read *whole* for restrictions and other licenses
  (``_check_researched``); only the notice word limits are waived for it.
- **Evidence** (``gather``): license texts come from ``LicenseFact.text_url``
  (with ``text_sha256`` when the collector hashed the text) and
  ``UniverseRecord.urls`` with role "license"; font files from
  ``UniverseRecord.files``. Both are ordered best first: research's own
  (``apply_research``), then for font files those ``config/font-files.toml``
  lists for a family no source gives a readable one (``apply_font_files``),
  pinned URLs, then the source order ``SOURCE_ORDER`` (the google/fonts
  folder first, as methodology §2 allows); within a source, records named
  like the family before the others and live ones before the rest (google/fonts
  keeps a renamed family's old folder, such as ``ofl/ekmukta`` beside
  ``ofl/mukta``: only the family's own is what Google serves), as stage
  "latin" orders them; then for font files the role, and of one role a file
  named Regular first (Homebrew's casks give every upright weight in an
  archive the role "regular"); then the URL.
- **A family passes (level "L3")** when a fetched text matches licenses that
  satisfy its L2 expression (``build/stage/licenses.json``; every matched id
  must be allowed in ``licenses.toml`` with gate LIC's license rulings applied,
  "X WITH E" counting as allowed when listed so or when X is, and a ``WITH``
  exception's text must appear in the text; when no id of the expression is
  allowed, L2 let the family in by an owner ruling on it and every id counts),
  the text's notices (``notices``: the lines before the license body less the
  license's own title, and every copyright and Reserved Font Name line, which
  the fingerprint leaves out wherever they stand; "Digitized data copyright"
  lines count as copyright lines) and name IDs 13 and 14 of the font file name
  no license outside that expression (or research's ``mentions``) and no use
  restriction (``RESTRICTIONS``; a name ID holding a whole license text is
  judged by the license it is and its notices), the lines before the body
  hold at most ``EXTRA_WORDS_MAX`` words besides copyright and Reserved Font
  Name lines, the license's own title and the OFL notice
  (``STANDARD_NOTICES``), no copyright or Reserved Font Name line holds more
  than ``NOTICE_LINE_WORDS_MAX`` (``long_notice_lines``: the fingerprint
  cannot see a clause appended to one), no text tried restricts use or has
  such a long notice (even when another one verifies), and one of the first
  ``MAX_FILES`` font files could be read. Empty name IDs are a note, not a
  failure.
- **Owner rulings** (gate L3, ``data/reviews/l3/``): question id
  ``L3-<family id>``; (a) exclude, (b) keep at an owner ruling, (c) research.
  Each answer must carry ``text_sha256``, the text it rules on (``NO_TEXT``,
  "none", when there is none); it stops applying when the text changes. (a)
  keeps the family out even if it passes, and after the text changes it stays
  out, queued for a new answer;
  (b) turns a failure into level "ruling"; (c) keeps it failed and queued.
- **Queue** (``Queue``, ``build/stage/queues/l3.json``): every family whose
  text hash differs from ``state/license_hashes.json`` (reason "changed",
  even when it passes again), and every failure not excluded by a ruling
  ("failed", or "research" after a (c) answer). ``needs_owner`` marks the
  items that carry a question; ``verify --check`` counts catalog members
  that are neither at L3 nor held by a (b) ruling.
- **State** (``license_hashes``): every checked family's
  ``{text_url, text_sha256, checked_on, font_version, font_file: {url, sha256}, level}``;
  families outside this run's scope keep their old entry. ``checked_on`` is
  the day the current text first reached its current level.
- **license_texts pseudo-source**: ``<store>/license_texts/<date>/texts.jsonl.gz``,
  one row per URL (``url, status, sha256, bytes, text, error``), failures
  included, sorted by URL; an archive member's row holds the member's bytes and
  sha256. A live run reuses an earlier snapshot's text when
  the collector's ``text_sha256`` or a pinned URL (a commit on GitHub or a
  GitLab host, a release asset, a package version) shows it unchanged,
  and fetches the rest. When today's snapshot exists (a replay, or a second
  ``verify`` the same day) it is the only source; ``--refetch`` rebuilds it.
  When no text could be fetched because of a network error, the family keeps
  last run's level, with a note, but only while a snapshot of the stale window
  before the run (``[stale] max_months``, as for a failed source) holds last
  level's text: a host that stays down fails the family after that.
- **Exclusions** (``exclusions``): the families whose level is "failed"; stage
  "rank" leaves them out, refresh reruns "rank" and "membership" when the set
  changes, and the next verify checks them again.
"""

import difflib
import hashlib
import re
import sys
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import unquote, urlsplit

from tff_catalog import jsonio, stageio
from tff_catalog.keys import match_key
from tff_catalog.records import FontFileRef, LicenseFact, SourceKey, UniverseRecord, from_json
from tff_catalog.reviews import Question

if TYPE_CHECKING:
    import logging

    from tff_catalog.fetch import Fetcher
    from tff_catalog.fontfiles import FontFacts
    from tff_catalog.licenses import Verdict
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext
    from tff_catalog.store import Store
    from tff_catalog.universe import Family, Universe

GATE = "L3"
STAGE = "verify"
PSEUDO_SOURCE = "license_texts"  # store.PSEUDO_SOURCES
SNAPSHOT_VERSION = 1
TEXTS_EXTRACT = "texts.jsonl.gz"
TEXTS_DIR = "license-texts"  # under data/
QUEUE_FILE = "l3.json"  # under build/stage/queues/
QUEUE_SCHEMA = 1
MAX_TEXTS = 3  # candidate texts tried per family
MAX_FILES = 3  # candidate font files tried per family, until one reads
MAX_TEXT_BYTES = 512 * 1024  # a larger "license" is not a license text
TEXT_STATUSES = (200, 403, 404, 410, 451)  # answers kept as facts, not retried
CHOICES = ("a", "b", "c")
OPTIONS = (
    "Exclude it from the catalog",
    "Keep it: the license qualifies (owner ruling)",
    "Research more",
)

# Where evidence comes from, best first; unknown sources follow, by name.
SOURCE_ORDER = (
    "google_repo",
    "fontsource",
    "foundries",
    "nerdfonts",
    "fontist",
    "homebrew_casks",
    "google_metadata",
    "debian_copyright",
)
_FILE_ROLES = ("regular", "variable", "other", "italic")
_FILE_FORMATS = (".ttf", ".otf", ".woff2", ".woff")
# The words of a file name, camel case split: "SNPro-Regular.otf" is SN, Pro, Regular, otf.
_NAME_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")

Level = Literal["L3", "failed", "ruling"]
Reason = Literal["changed", "failed", "research"]


@dataclass(frozen=True, slots=True)
class L3Result:
    family_id: str
    level: Literal["L3", "failed", "ruling"]
    checked_on: date
    text_url: str | None
    text_sha256: str | None
    matched: str | None  # SPDX id whose canonical text matched
    name_ids: tuple[str | None, str | None]  # name table IDs 13 and 14
    font_version: str | None
    font_file: FontFileRef | None
    problems: tuple[str, ...] = ()
    copyright: str | None = None  # name table ID 0 of the file read: the CC BY credit's holder


# --- fingerprints -------------------------------------------------------------------------------

# SPDX's equivalent words (license-list-XML equivalentwords.txt), folded to one spelling.
EQUIVALENT_WORDS = {
    "acknowledgement": "acknowledgment",
    "analogue": "analog",
    "analyse": "analyze",
    "artefact": "artifact",
    "authorisation": "authorization",
    "authorised": "authorized",
    "calibre": "caliber",
    "cancelled": "canceled",
    "capitalisations": "capitalizations",
    "catalogue": "catalog",
    "categorise": "categorize",
    "centre": "center",
    "emphasised": "emphasized",
    "favour": "favor",
    "favourite": "favorite",
    "fulfil": "fulfill",
    "fulfilment": "fulfillment",
    "initialise": "initialize",
    "judgement": "judgment",
    "labelling": "labeling",
    "labour": "labor",
    "licence": "license",
    "licences": "licenses",
    "licenced": "licensed",
    "licencing": "licensing",
    "maximise": "maximize",
    "merchantibility": "merchantability",
    "modelled": "modeled",
    "modelling": "modeling",
    "offence": "offense",
    "optimise": "optimize",
    "organisation": "organization",
    "organise": "organize",
    "practise": "practice",
    "programme": "program",
    "realise": "realize",
    "recognise": "recognize",
    "signalling": "signaling",
    "utilisation": "utilization",
    "whilst": "while",
    "wilful": "wilfull",
}
_EQUIVALENT_PAIRS = {
    ("sub", "license"): "sublicense",
    ("non", "commercial"): "noncommercial",
    ("per", "cent"): "percent",
    ("copyright", "owner"): "copyright holder",
}

# The first words of each license body the canonical texts contain. What comes
# before them is a title or a notice (copyright, Reserved Font Names, "This
# Font Software is licensed under ...") and does not count in the fingerprint.
BODY_STARTS = (
    "preamble the goals of the open font license",  # OFL-1.0, OFL-1.1
    "preamble this license allows the licensed fonts",  # Ubuntu-font-1.0
    "definitions license shall mean the terms and conditions",  # Apache-2.0
    "permission is hereby granted",  # MIT, MIT-0, Bitstream-Vera
    "permission to use copy modify",  # ISC, 0BSD, Baekmuk
    "redistribution and use in source and binary forms",  # BSD-*
    "creative commons corporation is not a law firm",  # CC 3.0 and CC0
    "creative commons corporation creative commons is not a law firm",  # CC 4.0
    "this is free and unencumbered software",  # Unlicense
    "everyone is permitted to copy and distribute verbatim",  # GNU licenses, WTFPL, Arphic
    "everyone is allowed to distribute verbatim copies",  # LPPL
    "this software is provided as is without any express or implied warranty",  # Zlib
    "the licensor provides the licensed program",  # IPA
    "definitions 1 1 contributor means",  # MPL-2.0
    "you are hereby granted permission under all bitstream propriety rights",  # Bitstream-Charter
    "these fonts are free softwares",  # mplus
    "licensing agreement for the fonts with original name",  # ParaType-Free-Font-1.3
    "as a special exception",  # exceptions
    # Notices that grant a license by reference (GRANTS, variants/): the Apache-2.0
    # appendix's standard notice, and the GUST Font License (LPPL-1.3c plus a request).
    "licensed under the apache license version 2 0 the license you may not use this file "
    "except in compliance with the license",
    "this work may be distributed and or modified under the conditions of the latex project "
    "public license",
)

# Canonical texts that grant another license by reference: a text matching the key
# verifies these ids too. The GUST Font License says "This work may be distributed
# and/or modified under the conditions of the LaTeX Project Public License, either
# version 1.3c of this license or (at your option) any later version", and adds
# only a request that is "not legally required".
GRANTS: Mapping[str, tuple[str, ...]] = {"LicenseRef-GUST-Font-License": ("LPPL-1.3c",)}

_WORD = re.compile(r"[^\W_]+")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w.-]+\.(?:org|com|net|io)(?:/\S*)?")
# A copyright notice line: "Copyright 2020 ...", "Copyright (c) ...", "© 2023 ...", and
# the type industry's "Digitized data copyright (c) 2012-2018 ..." (FiraGO's OFL.txt).
_COPYRIGHT = re.compile(
    r"^\W*(?:portions\s+|digitized\s+data\s+)?"
    r"(?:copyright\b\W*(?:©|\(c\)|\d|by\b|\[|<|\{|yyyy)|©|\(c\)\s*\d)"
)
# A list bullet at the start of a line: "1.", "2)", "(a)", "iv.", "- ".
_BULLET = re.compile(r"^\s*[-*•>#]*\s*(?:\(?(?:\d{1,2}|[a-z]|[ivx]{1,4})[.)])(?=\s)")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text.removeprefix("\ufeff")).casefold()


def _lines(text: str) -> list[str]:
    return _fold(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _equivalents(words: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(words):
        pair = _EQUIVALENT_PAIRS.get((words[i], words[i + 1])) if i + 1 < len(words) else None
        if pair:
            out.extend(pair.split())
            i += 2
        else:
            out.append(EQUIVALENT_WORDS.get(words[i], words[i]))
            i += 1
    return out


def _is_rfn(words: list[str]) -> bool:
    """A Reserved Font Name statement (not the OFL's own definition of the term)."""
    if words[:3] == ["with", "reserved", "font"]:
        return True
    return words[:2] == ["reserved", "font"] and words[3:4] != ["refers"]


def _line_words(line: str) -> list[str] | None:
    """A folded line's words as the fingerprint counts them; None for a copyright or
    Reserved Font Name line, which it leaves out wherever it stands."""
    if _COPYRIGHT.match(line):
        return None
    words = _WORD.findall(_URL.sub(" ", _BULLET.sub(" ", line)).replace("&", " and "))
    return None if words and _is_rfn(words) else words


def _body_words(text: str) -> list[str]:
    """Every word counted by the fingerprint, before the body is cut out."""
    words: list[str] = []
    for line in _lines(text):
        words.extend(_line_words(line) or ())
    return _equivalents(words)


def dropped_lines(text: str) -> str:
    """The copyright and Reserved Font Name lines the fingerprint leaves out, anywhere in
    ``text``, as ``_flat`` words (URLs included): what they say is checked separately."""
    lines = [line for line in _lines(text) if _line_words(line) is None]
    return " ".join(_flat(line).strip() for line in lines)


def long_notice_lines(text: str, known: Iterable[str] = ()) -> list[list[str]]:
    """The copyright and Reserved Font Name lines of ``text`` holding more than
    ``NOTICE_LINE_WORDS_MAX`` words once URLs and the ``known`` phrases (titles,
    ``STANDARD_NOTICES``) are taken out; each as its remaining words."""
    phrases = tuple(known)
    out = []
    for line in _lines(text):
        if _line_words(line) is None:
            words = _without(_flat(_URL.sub(" ", line)).strip(), phrases).split()
            if len(words) > NOTICE_LINE_WORDS_MAX:
                out.append(words)
    return out


def _flat(text: str) -> str:
    """Every word of ``text``, URLs included, as " w1 w2 ... " (for phrase searches)."""
    words = _WORD.findall(_fold(text).replace("&", " and "))
    return " " + " ".join(_equivalents(words)) + " "


def _body_start(flat: str) -> int | None:
    """Character offset in ``flat`` of the earliest ``BODY_STARTS`` phrase."""
    found = [i for s in BODY_STARTS if (i := flat.find(f" {s} ")) >= 0]
    return min(found) if found else None


def normalise(text: str) -> str:
    """The license body that ``fingerprint`` hashes, as space-separated words."""
    flat = " " + " ".join(_body_words(text)) + " "
    start = _body_start(flat)
    return flat[start or 0 :].strip()


def header(text: str) -> str:
    """The words before the license body, URLs and notices included (empty without a body start)."""
    flat = _flat(text)
    start = _body_start(flat)
    return flat[:start].strip() if start else ""


def header_words(text: str) -> str:
    """The words before the license body that ``normalise`` drops, less copyright and
    Reserved Font Name lines and URLs: titles and any other statement."""
    flat = " " + " ".join(_body_words(text)) + " "
    start = _body_start(flat)
    return flat[:start].strip() if start else ""


def fingerprint(text: str) -> str:
    """A whitespace-, case- and punctuation-insensitive sha256 of a license text.

    Copyright lines and the Reserved Font Name clause are removed first, so
    every OFL-1.1 text of any family gives the same fingerprint.
    """
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


# --- what a text or a name table says -----------------------------------------------------------

# The OFL's own notice above its title, in the forms upstreams use. Like a
# license's title, it may stand above the body without counting as extra text.
STANDARD_NOTICES = (
    "this font software is licensed under the sil open font license version 1 1",
    "this license is copied below and is also available with a faq at",
    "this license is copied below and is also available with an faq at",
    "this license is available with a faq at",
    "all rights reserved",
)
# More words than this above a license body (besides copyright and Reserved Font
# Name lines, titles and STANDARD_NOTICES) could change the license: L3 fails.
# Real OFL headers keep at most about 20 (a wrapped trademark sentence).
EXTRA_WORDS_MAX = 30
# The fingerprint leaves every copyright and Reserved Font Name line out, so a
# clause appended to one (LXGW WenKai adds a 90-word "[ADDITIONAL PERMISSION]"
# to its RFN line) would pass unseen. More words than this on one such line,
# less URLs, titles and STANDARD_NOTICES, fail L3. Real lines keep at most 30
# (Adobe's copyright, Reserved Font Name and trademark sentence).
NOTICE_LINE_WORDS_MAX = 40

# Phrases (whole words, after ``_flat``) that restrict use; any of them outside
# a license body, or in name IDs 13 and 14, fails L3.
RESTRICTIONS = (
    "personal use",
    "noncommercial",
    "not for commercial",
    "no commercial use",
    "commercial use is not",
    "commercial use requires",
    "commercial license",
    "eula",
    "end user license",
    "terms of use",
    "property of",
    "you agree",
    "prohibited",
    "not permitted",
    "without derivatives",
    "no derivatives",
    "noderivatives",
    "trial version",
    "demo version",
    "purchase",
    "proprietary",
    "all rights reserved except",
    "shareware",
    "not for resale",
    "evaluation only",
    "may not be modified",
    "must not be modified",
    "may not be redistributed",
    "must not be redistributed",
    "may not be distributed",
    "may not be embedded",
)

# License families named in free text: (family, phrases). Checked in order, and
# a matched phrase is blanked out, so "lesser general public license" is LGPL only.
_MENTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "OFL",
        (
            "open font license",
            "ofl",
            "openfontlicense org",
            "sil org ofl",
            "item id ofl",
            "ofl web",
        ),
    ),
    ("Ubuntu", ("ubuntu font license",)),
    ("Apache", ("apache license", "apache org licenses")),
    ("AGPL", ("affero general public license", "agpl")),
    (
        "LGPL",
        (
            "lesser general public license",
            "library general public license",
            "lgpl",
            "copyleft lesser",
        ),
    ),
    ("GPL", ("general public license", "gpl", "gnu org licenses gpl", "licenses gpl html")),
    ("MIT", ("mit license", "licenses mit")),
    ("BSD", ("bsd",)),
    ("CC-BY-SA", ("attribution sharealike", "by sa")),
    ("CC-BY-ND", ("noderivatives", "by nd")),
    ("CC-BY-NC", ("attribution noncommercial", "by nc")),
    ("CC0", ("cc0", "publicdomain zero")),
    ("CC-BY", ("creative commons attribution", "licenses by")),
    ("Public-domain", ("public domain", "unlicense")),
    ("Bitstream-Vera", ("bitstream vera",)),
    ("IPA", ("ipa font license",)),
    ("LPPL", ("latex project public license", "lppl", "gust font license", "gust org pl")),
    ("MPL", ("mozilla public license",)),
    ("Arphic", ("arphic public license",)),
)


def mentions(text: str) -> frozenset[str]:
    """The license families ``text`` names (``_MENTIONS``)."""
    flat = _flat(text)
    found = set()
    for family, phrases in _MENTIONS:
        for phrase in phrases:
            if f" {phrase} " in flat:
                found.add(family)
                flat = flat.replace(f" {phrase} ", " # ")
    return frozenset(found)


def restrictions(text: str) -> tuple[str, ...]:
    """The ``RESTRICTIONS`` phrases in ``text``, in list order."""
    flat = _flat(text)
    return tuple(p for p in RESTRICTIONS if f" {p} " in flat)


def license_family(spdx: str) -> str:
    """The family a license id belongs to, as ``mentions`` names it (else the id itself)."""
    base = text_id(spdx)
    for prefix, family in (
        ("OFL-", "OFL"),
        ("Ubuntu-font", "Ubuntu"),
        ("Apache-", "Apache"),
        ("AGPL-", "AGPL"),
        ("LGPL-", "LGPL"),
        ("GPL-", "GPL"),
        ("MIT", "MIT"),
        ("BSD-", "BSD"),
        ("0BSD", "BSD"),
        ("CC-BY-SA-", "CC-BY-SA"),
        ("CC-BY-ND-", "CC-BY-ND"),
        ("CC-BY-NC", "CC-BY-NC"),
        ("CC0-", "CC0"),
        ("CC-BY-", "CC-BY"),
        ("Unlicense", "Public-domain"),
        ("Bitstream-Vera", "Bitstream-Vera"),
        ("IPA", "IPA"),
        ("LPPL-", "LPPL"),
        ("LicenseRef-GUST-Font-License", "LPPL"),  # the LPPL-1.3c with a request (GRANTS)
        ("MPL-", "MPL"),
        ("Arphic-", "Arphic"),
    ):
        if base.startswith(prefix):
            return family
    return base


def text_id(spdx: str) -> str:
    """The id whose canonical text ``spdx`` uses.

    The OFL's RFN variants share its text, as do the "-only", "-or-later" and
    "+" forms of the GNU licenses; the Ubuntu Font Licence has several spellings.
    """
    for rfn in ("-no-RFN", "-RFN"):
        if spdx.startswith("OFL-") and spdx.endswith(rfn):
            return spdx.removesuffix(rfn)
    if spdx in ("UFL-1.0", "LicenseRef-UbuntuFont", "LicenseRef-Ubuntu-Font-1.0"):
        return "Ubuntu-font-1.0"
    if gnu := _GNU_ID.match(spdx):
        return f"{gnu.group(1)}-only"
    return spdx


_GNU_ID = re.compile(r"^((?:A|L)?GPL-\d\.\d)(?:-only|-or-later|\+)?$")


def verified_ids(spdx: str) -> frozenset[str]:
    """The text ids a text matching ``spdx`` verifies: its own, and those it grants (``GRANTS``)."""
    return frozenset(text_id(i) for i in (spdx, *GRANTS.get(spdx, ())))


def _expected_families(ids: Iterable[str]) -> frozenset[str]:
    families = {license_family(i) for i in ids}
    if families & {"CC0", "Public-domain"}:
        families |= {"CC0", "Public-domain"}
    return frozenset(families)


def statement_problems(where: str, text: str, allowed_families: frozenset[str]) -> list[str]:
    """Fatal problems of a license statement (name ID 13 or 14, or a text's notices)."""
    problems = [f"{where} names {m}" for m in sorted(mentions(text) - allowed_families)]
    problems += [f"{where} restricts use: {p!r}" for p in restrictions(text)]
    return problems


def notices(text: str, matched: str | None, canon: Canon) -> str:
    """What a license text says besides the license: for a text matching ``matched``, the
    words before its body less that license's own title (``Canon.headers``), plus every
    copyright and Reserved Font Name line (``dropped_lines``); otherwise the whole text."""
    if matched is None:
        return _flat(text).strip()
    return f"{_without(header(text), canon.headers.get(matched, ()))} {dropped_lines(text)}".strip()


# --- SPDX expressions ---------------------------------------------------------------------------

_SPDX_TOKEN = re.compile(r"\(|\)|[A-Za-z0-9.+:-]+")


@dataclass(frozen=True, slots=True)
class _Leaf:
    id: str
    exception: str | None = None


@dataclass(frozen=True, slots=True)
class _Op:
    op: Literal["AND", "OR"]
    parts: tuple[_Leaf | _Op, ...]


def parse_expression(expr: str) -> _Leaf | _Op:
    """Parse an SPDX expression (WITH binds tighter than AND, AND than OR); ``ValueError`` if bad."""
    tokens = _SPDX_TOKEN.findall(expr)
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def take() -> str:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError(f"unexpected end of {expr!r}")
        pos += 1
        return tokens[pos - 1]

    def atom() -> _Leaf | _Op:
        tok = take()
        if tok == "(":
            node = either()
            if take() != ")":
                raise ValueError(f"unbalanced parentheses in {expr!r}")
            return node
        if tok.upper() in ("AND", "OR", "WITH", ")"):
            raise ValueError(f"unexpected {tok!r} in {expr!r}")
        if (peek() or "").upper() == "WITH":
            take()
            return _Leaf(tok, take())
        return _Leaf(tok)

    def chain(op: Literal["AND", "OR"], sub: Any) -> _Leaf | _Op:
        parts = [sub()]
        while (peek() or "").upper() == op:
            take()
            parts.append(sub())
        return parts[0] if len(parts) == 1 else _Op(op, tuple(parts))

    def both() -> _Leaf | _Op:
        return chain("AND", atom)

    def either() -> _Leaf | _Op:
        return chain("OR", both)

    node = either()
    if pos != len(tokens):
        raise ValueError(f"trailing {tokens[pos]!r} in {expr!r}")
    return node


def expression_ids(expr: str) -> tuple[str, ...]:
    """The license ids (not exceptions) of an expression, in order, without repeats."""

    return tuple(dict.fromkeys(leaf.id for leaf in _leaves(parse_expression(expr))))


def _allow_id(spdx: str) -> str:
    """``spdx`` as the allowed list compares it: the OFL's RFN forms and the Ubuntu
    Font Licence's spellings as one id, a GNU "+" or bare version as its SPDX 3 form."""
    if gnu := _GNU_ID.match(spdx):
        later = spdx.endswith(("+", "-or-later"))
        return f"{gnu.group(1)}-{'or-later' if later else 'only'}"
    return text_id(spdx)


def _leaves(node: _Leaf | _Op) -> Iterator[_Leaf]:
    if isinstance(node, _Leaf):
        yield node
    else:
        for part in node.parts:
            yield from _leaves(part)


def allowed_pairs(allowed: Iterable[str]) -> frozenset[tuple[str, str | None]]:
    """The allowed ids (``licenses.toml`` keys such as "OFL-1.1" or
    "GPL-2.0-only WITH Font-exception-2.0") as (``_allow_id``, exception) pairs."""
    pairs: set[tuple[str, str | None]] = set()
    for key in allowed:
        try:
            node = parse_expression(key)
        except ValueError:
            continue
        if isinstance(node, _Leaf):
            pairs.add((_allow_id(node.id), node.exception))
    return frozenset(pairs)


def leaf_allowed(leaf: _Leaf, pairs: frozenset[tuple[str, str | None]]) -> bool:
    """Whether the allowed list lets ``leaf`` in: listed as it is, or (with an exception)
    its license listed alone, since an exception only adds permissions (as L2 reads it)."""
    base = _allow_id(leaf.id)
    return (base, leaf.exception) in pairs or (base, None) in pairs


def family_allowed(expr: str, allowed: frozenset[str]) -> frozenset[str]:
    """The ids a text may verify ``expr`` by: ``allowed``, or, when ``allowed`` does not
    let ``expr`` in (an AND with a part missing, an OR with no branch), every id of
    ``expr``: L2 let it in all the same, so the owner ruled on the family (gate LIC)."""
    pairs = allowed_pairs(allowed)

    def ok(node: _Leaf | _Op) -> bool:
        if isinstance(node, _Leaf):
            return leaf_allowed(node, pairs)
        results = [ok(p) for p in node.parts]
        return all(results) if node.op == "AND" else any(results)

    tree = parse_expression(expr)
    if ok(tree):
        return allowed
    leaves = (f"{x.id} WITH {x.exception}" if x.exception else x.id for x in _leaves(tree))
    return allowed | frozenset(leaves)


def satisfied(
    expr: str, matched: frozenset[str], allowed: frozenset[str], exceptions: frozenset[str]
) -> bool:
    """Whether texts matching ``matched`` (text ids) verify ``expr``.

    A leaf needs its text matched (by ``text_id``), its license allowed
    (``leaf_allowed``: "X WITH E" is allowed when listed so, or when X is) and
    its ``WITH`` exception in ``exceptions``; AND needs every part, OR any.
    """
    pairs = allowed_pairs(allowed)

    def ok(node: _Leaf | _Op) -> bool:
        if isinstance(node, _Leaf):
            return (
                text_id(node.id) in matched
                and leaf_allowed(node, pairs)
                and (node.exception is None or node.exception in exceptions)
            )
        results = [ok(p) for p in node.parts]
        return all(results) if node.op == "AND" else any(results)

    return ok(parse_expression(expr))


# --- canonical texts ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Canon:
    """The canonical texts (``load_canon``)."""

    by_fingerprint: Mapping[str, str]  # fingerprint -> SPDX id
    bodies: Mapping[str, str]  # SPDX id -> normalised body of its main text
    headers: Mapping[str, tuple[str, ...]]  # SPDX id -> its texts' own headers (titles), both forms
    exceptions: Mapping[str, str]  # SPDX exception id -> normalised text

    def match(self, text: str) -> str | None:
        """The SPDX id whose canonical text ``text`` matches, if any (``identify``)."""
        return self.identify(text)[0]

    def identify(self, text: str) -> tuple[str | None, frozenset[str]]:
        """The license ``text`` is, and the exceptions it carries.

        A text is its license when its fingerprint is one of that license's, or
        when it is once the whole text of each exception it carries is taken out
        (a COPYING file with the GPL and the font exception in one).
        """
        found = self.exceptions_in(text)
        spdx = self.by_fingerprint.get(fingerprint(text))
        if spdx is None and found:
            words = " " + " ".join(_body_words(text)) + " "
            for exception in sorted(found):
                words = words.replace(f" {self.exceptions[exception]} ", " ")
            start = _body_start(words)
            if start is not None:
                rest = words[start:].strip()
                spdx = self.by_fingerprint.get(hashlib.sha256(rest.encode("utf-8")).hexdigest())
        return spdx, found

    def exceptions_in(self, text: str) -> frozenset[str]:
        """The exceptions whose whole text appears in ``text``."""
        words = " " + " ".join(_body_words(text)) + " "
        return frozenset(e for e, body in self.exceptions.items() if f" {body} " in words)

    def closest(self, text: str, ids: Iterable[str]) -> tuple[str, float] | None:
        """The canonical body among ``ids`` most like ``text``'s, with the share of equal words."""
        words = normalise(text).split()
        best: tuple[str, float] | None = None
        for spdx in ids:
            body = self.bodies.get(text_id(spdx))
            if body is None:
                continue
            ratio = difflib.SequenceMatcher(None, body.split(), words, autojunk=False).ratio()
            if best is None or ratio > best[1]:
                best = (text_id(spdx), ratio)
        return best


def canon_dir(paths: Paths) -> Path:
    """``data/license-texts/``."""
    return paths.data / TEXTS_DIR


def load_canon(directory: Path) -> Canon:
    """Read the canonical texts; ``ValueError`` when two licenses share a fingerprint
    or a text has no body start (``BODY_STARTS``)."""
    by_fp: dict[str, str] = {}
    bodies: dict[str, str] = {}
    headers: dict[str, list[str]] = {}
    files = [(p.stem, p) for p in sorted(directory.glob("*.txt"))]
    files += [(p.parent.name, p) for p in sorted(directory.glob("variants/*/*.txt"))]
    for spdx, path in files:
        text = path.read_text(encoding="utf-8")
        flat = " " + " ".join(_body_words(text)) + " "
        if _body_start(flat) is None:
            raise ValueError(f"{path}: no body start (BODY_STARTS) in this text")
        fp = fingerprint(text)
        if by_fp.setdefault(fp, spdx) != spdx:
            raise ValueError(f"{path}: same fingerprint as {by_fp[fp]}")
        bodies.setdefault(spdx, normalise(text))
        headers.setdefault(spdx, []).extend((header(text), header_words(text)))
    exceptions = {
        p.stem: normalise(p.read_text("utf-8")) for p in directory.glob("exceptions/*.txt")
    }
    return Canon(
        by_fingerprint=by_fp,
        bodies=bodies,
        headers={k: tuple(sorted(set(v), key=len, reverse=True)) for k, v in headers.items()},
        exceptions=dict(sorted(exceptions.items())),
    )


# --- evidence -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextRef:
    """A candidate license text for a family."""

    url: str
    sha256: str | None = None  # when the source hashed the text itself (google/fonts)
    source: str = ""


@dataclass(frozen=True, slots=True)
class Evidence:
    """A family's candidate license texts and font files, best first."""

    texts: tuple[TextRef, ...] = ()
    files: tuple[FontFileRef, ...] = ()


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^v?\d+(?:\.\d+)+(?:[-+][\w.]+)?$")


def is_pinned(url: str) -> bool:
    """Whether ``url`` names one fixed version of a file: a commit sha, a release tag,
    or an exact package version."""
    parts = urlsplit(url)
    host, segs = parts.netloc.lower(), [s for s in parts.path.split("/") if s]
    if host == "raw.githubusercontent.com":
        return len(segs) > 3 and bool(_COMMIT.match(segs[2]))
    if host == "github.com" and len(segs) > 4:
        if segs[2] in ("raw", "blob"):
            return bool(_COMMIT.match(segs[3]))
        return segs[2:4] == ["releases", "download"]
    if host == "cdn.jsdelivr.net" and len(segs) > 2 and segs[0] in ("gh", "npm"):
        # gh/<owner>/<repo>@<ref>/..., npm/<name>@<version>/..., npm/@<scope>/<name>@<version>/...
        spec = segs[1] if segs[0] == "npm" and not segs[1].startswith("@") else segs[2]
        ref = spec.rpartition("@")[2] if "@" in spec.lstrip("@") else ""
        return bool(_COMMIT.match(ref) or _VERSION.match(ref))
    if "-" in segs:
        # A GitLab host (gitlab.com, salsa.debian.org): <namespace>/<project>/-/raw/<commit>/<path>.
        i = segs.index("-")
        return (
            i >= 2
            and len(segs) > i + 3
            and segs[i + 1] == "raw"
            and bool(_COMMIT.match(segs[i + 2]))
        )
    return False


def fetchable_url(url: str) -> str:
    """The URL to fetch for ``url``: https, and a GitHub page's raw file."""
    if url.startswith("http://"):
        url = "https://" + url.removeprefix("http://")
    parts = urlsplit(url)
    segs = parts.path.split("/")
    if parts.netloc.lower() == "github.com" and len(segs) > 5 and segs[3] == "blob":
        return f"https://raw.githubusercontent.com/{segs[1]}/{segs[2]}/{'/'.join(segs[4:])}"
    return url


ARCHIVE_EXTENSIONS = (".zip",)


def archive_member(url: str) -> tuple[str, str] | None:
    """``(archive url, member path)`` when ``url`` names a file inside a zip archive, the
    way font file URLs do (``https://host/Font-1.0.zip#Font-1.0/OFL.txt``); else None."""
    from urllib.parse import unquote

    archive, sep, fragment = url.partition("#")
    if not sep or not fragment or not archive.startswith("https://"):
        return None
    if not urlsplit(archive).path.lower().endswith(ARCHIVE_EXTENSIONS):
        return None
    return archive, unquote(fragment)


class MemberError(ValueError):
    """A zip archive that holds no single readable text by the member's name."""

    def __init__(self, message: str, *, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


def read_member(data: bytes, member: str) -> bytes:
    """The bytes of ``member`` of the zip archive ``data``: the entry of that path, else
    the one entry whose path ends in it. ``MemberError`` when there is none (``missing``),
    several, the member is larger than ``MAX_TEXT_BYTES`` or ``data`` is no zip archive."""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            found = [i for i in infos if i.filename == member] or [
                i for i in infos if i.filename.endswith("/" + member.lstrip("/"))
            ]
            if not found:
                raise MemberError(f"no archive entry {member!r}", missing=True)
            if len(found) > 1:
                raise MemberError(f"{len(found)} archive entries match {member!r}")
            if found[0].file_size > MAX_TEXT_BYTES:
                raise MemberError(f"{found[0].file_size} bytes: too large for a license")
            return zf.read(found[0])
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError) as exc:
        raise MemberError(f"not a readable zip archive ({exc})") from exc


def _source_rank(source: str) -> tuple[int, str]:
    return (SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER), source)


def _named_regular(url: str) -> bool:
    """Whether the file's name (an archive member's own) has the word Regular."""
    name = unquote(url.rsplit("#", 1)[-1].rsplit("/", 1)[-1])
    return any(w.casefold() == "regular" for w in _NAME_WORDS.findall(name))


def _file_rank(ref: FontFileRef, source: str, other: tuple[bool, bool]) -> tuple[Any, ...]:
    path = urlsplit(ref.url).path.lower()
    fmt = next((i for i, ext in enumerate(_FILE_FORMATS) if path.endswith(ext)), len(_FILE_FORMATS))
    return (
        not is_pinned(ref.url),
        _source_rank(source),
        *other,
        _FILE_ROLES.index(ref.role),
        not _named_regular(ref.url),
        fmt,
        ref.url,
    )


def _is_font_file(url: str) -> bool:
    from tff_catalog.fontfiles import split_member

    return urlsplit(url).path.lower().endswith(_FILE_FORMATS) or split_member(url) is not None


def iter_records(records_dir: Path) -> Iterator[UniverseRecord | LicenseFact]:
    """Every UniverseRecord and LicenseFact in ``build/stage/records/`` (baselines skipped)."""
    if not records_dir.is_dir():
        raise FileNotFoundError(f"{records_dir} is missing: run `tff-catalog parse` first")
    for path in sorted(records_dir.glob("*.jsonl")):
        if "@" in path.name:
            continue
        for row in jsonio.iter_jsonl(path):
            if row.get("type") in ("UniverseRecord", "LicenseFact"):
                rec = from_json(row)
                if isinstance(rec, UniverseRecord | LicenseFact):
                    yield rec


def gather(
    records: Iterable[UniverseRecord | LicenseFact],
    family_of: Mapping[SourceKey, str],
    universe: Universe | None = None,
) -> dict[str, Evidence]:
    """Each family's candidate license texts and font files, best first (module docstring).

    With ``universe``, a record of a several-families key (``Family.shared``) is its
    family's, and that key's license facts are every sharing family's; and a record
    named otherwise than its family (a license fact takes its key's record's name)
    comes after the family's own ones of its source.
    """
    records = list(records)
    texts: dict[str, dict[str, tuple[tuple[Any, ...], TextRef]]] = {}
    files: dict[str, dict[str, tuple[tuple[Any, ...], FontFileRef]]] = {}
    named = {(r.source, r.key): r for r in records if isinstance(r, UniverseRecord)}

    def other(fid: str, rec: UniverseRecord | LicenseFact) -> tuple[bool, bool]:
        """(named otherwise than the family, not live) of the record, or its key's record."""
        r = rec if isinstance(rec, UniverseRecord) else named.get((rec.source, rec.key))
        if r is None:
            return (False, False)
        fam = universe.families.get(fid) if universe is not None else None
        return (
            fam is not None and match_key(r.family) != match_key(fam.family),
            r.status != "live",
        )

    def add_text(
        fid: str, url: str, sha: str | None, source: str, kind: int, off: tuple[bool, bool]
    ) -> None:
        url = fetchable_url(url)
        rank = (not is_pinned(url), _source_rank(source), *off, kind, sha is None, url)
        old = texts.setdefault(fid, {}).get(url)
        if old is None or rank < old[0]:
            texts[fid][url] = (rank, TextRef(url, sha, source))

    def owners(rec: UniverseRecord | LicenseFact) -> tuple[str, ...]:
        fid = family_of.get(rec.key)
        if fid is not None or universe is None:
            return (fid,) if fid is not None else ()
        if isinstance(rec, LicenseFact):
            return universe.sharing(rec.key)
        shared = universe.record_owner(rec.key, rec.family)
        return (shared,) if shared is not None else ()

    for rec in records:
        for fid in owners(rec):
            off = other(fid, rec)
            if isinstance(rec, LicenseFact):
                if rec.text_url:
                    add_text(fid, rec.text_url, rec.text_sha256, rec.source, 0, off)
                continue
            for role, url in rec.urls:
                if role == "license":
                    add_text(fid, url, None, rec.source, 1, off)
            for ref in rec.files:
                if _is_font_file(ref.url):
                    rank = _file_rank(ref, rec.source, off)
                    old = files.setdefault(fid, {}).get(ref.url)
                    if old is None or rank < old[0]:
                        files[fid][ref.url] = (rank, ref)
    out = {}
    for fid in sorted(set(texts) | set(files)):
        out[fid] = Evidence(
            texts=tuple(ref for _, ref in sorted(texts.get(fid, {}).values())),
            files=tuple(ref for _, ref in sorted(files.get(fid, {}).values())),
        )
    return out


def key_index(universe: Universe) -> dict[SourceKey, str]:
    """Every universe key of every family, to the family id."""
    return {key: fam.id for fam in universe.families.values() for key in fam.keys}


# --- research (config/license-texts.toml) -------------------------------------------------------

RESEARCH_FILE = "license-texts.toml"  # under config/
RESEARCH_SCHEMA = 1
RESEARCH_SOURCE = "research"  # TextRef.source of a researched text URL
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MENTION_FAMILIES = frozenset(family for family, _ in _MENTIONS)


@dataclass(frozen=True, slots=True)
class FamilyResearch:
    """One ``[[family]]`` table of ``config/license-texts.toml``."""

    family: str  # family id
    name: str  # display name, for people
    reason: str  # what the research found
    texts: tuple[str, ...] = ()  # upstream license texts, tried before the sources' ones
    files: tuple[str, ...] = ()  # upstream font files, read before the sources' ones
    # License families (as ``mentions`` names them) its texts' notices and name IDs 13
    # and 14 may name besides its L2 expression; each must be a family of an allowed
    # license, so an allowance never lets a restricting or excluded license through.
    mentions: tuple[str, ...] = ()
    # A page people can read the license on, for the site's License link when ``texts``
    # reads the license inside a release archive (a link there would download it).
    page: str = ""


@dataclass(frozen=True, slots=True)
class ResearchedText:
    """One ``[[text]]`` table: a license text, by the sha256 of its bytes, that holds
    ``licenses`` but matches no canonical text, or matches one under a notice longer
    than the word limits allow (research read it word for word)."""

    sha256: str
    licenses: tuple[str, ...]  # SPDX ids the text verifies
    url: str  # where it was read
    reason: str  # how it differs from the canonical texts, and why that changes nothing


@dataclass(frozen=True, slots=True)
class ResearchFile:
    schema: int
    family: tuple[FamilyResearch, ...] = ()
    text: tuple[ResearchedText, ...] = ()


@dataclass(frozen=True, slots=True)
class Research:
    """``config/license-texts.toml``, indexed (``load_research``)."""

    families: Mapping[str, FamilyResearch] = field(default_factory=dict)
    texts: Mapping[str, ResearchedText] = field(default_factory=dict)  # by sha256


def _check_url(url: str, where: str) -> None:
    from tff_catalog.config_model import ConfigError

    if not url.startswith("https://") or any(c.isspace() for c in url):
        raise ConfigError(f"{where}: {url!r} is not an https URL")


def load_research(paths: Paths) -> Research:
    """``config/license-texts.toml``, checked strictly (no file: no research).

    Raises ``ConfigError`` for unknown or missing keys, a bad family id, a family
    or text listed twice, a URL that is not https, a text URL naming a zip archive
    but no file in it, a text inside an archive with no ``page`` (or a ``page`` that
    is an archive), a font file URL that names no font, an unknown license
    family in ``mentions``, a bad sha256 or an SPDX expression that does not parse.
    """
    from tff_catalog.config_model import ConfigError, from_mapping, load_toml
    from tff_catalog.names import ID_PATTERN

    path = paths.config / RESEARCH_FILE
    if not path.is_file():
        return Research()
    doc = from_mapping(ResearchFile, load_toml(path), where=RESEARCH_FILE)
    if doc.schema != RESEARCH_SCHEMA:
        raise ConfigError(f"{RESEARCH_FILE}: schema {doc.schema}, expected {RESEARCH_SCHEMA}")
    families: dict[str, FamilyResearch] = {}
    for i, fam in enumerate(doc.family):
        where = f"{RESEARCH_FILE}: family[{i}]"
        if not ID_PATTERN.fullmatch(fam.family):
            raise ConfigError(f"{where}.family: {fam.family!r} is not a family id")
        if fam.family in families:
            raise ConfigError(f"{where}.family: {fam.family!r} is listed twice")
        for n, url in enumerate(fam.texts):
            _check_url(url, f"{where}.texts[{n}]")
            archive = urlsplit(url).path.lower().endswith(ARCHIVE_EXTENSIONS)
            if ("#" in url or archive) and archive_member(url) is None:
                raise ConfigError(
                    f"{where}.texts[{n}]: {url} names no file in a zip archive "
                    "(archive.zip#path/in/archive)"
                )
        for n, url in enumerate(fam.files):
            _check_url(url, f"{where}.files[{n}]")
            if not _is_font_file(url):
                raise ConfigError(f"{where}.files[{n}]: {url} names no font file")
        for n, mention in enumerate(fam.mentions):
            if mention not in _MENTION_FAMILIES:
                raise ConfigError(f"{where}.mentions[{n}]: {mention!r} is no license family")
        if fam.page:
            _check_url(fam.page, f"{where}.page")
            if urlsplit(fam.page).path.lower().endswith(ARCHIVE_EXTENSIONS):
                raise ConfigError(f"{where}.page: {fam.page} is an archive, not a page")
        elif any(archive_member(url) for url in fam.texts):
            raise ConfigError(
                f"{where}.page: missing; its license text is inside a release archive, "
                "so the site needs a page to link instead"
            )
        families[fam.family] = fam
    texts: dict[str, ResearchedText] = {}
    for i, known in enumerate(doc.text):
        where = f"{RESEARCH_FILE}: text[{i}]"
        if not _SHA256.fullmatch(known.sha256):
            raise ConfigError(f"{where}.sha256: {known.sha256!r} is not a sha256")
        if known.sha256 in texts:
            raise ConfigError(f"{where}.sha256: {known.sha256} is listed twice")
        if not known.licenses:
            raise ConfigError(f"{where}.licenses: empty")
        for n, spdx in enumerate(known.licenses):
            try:
                node = parse_expression(spdx)
            except ValueError as exc:
                raise ConfigError(f"{where}.licenses[{n}]: {exc}") from exc
            if not isinstance(node, _Leaf) or node.exception is not None:
                raise ConfigError(f"{where}.licenses[{n}]: {spdx!r} is not one license id")
        _check_url(known.url, f"{where}.url")
        texts[known.sha256] = known
    return Research(families=dict(sorted(families.items())), texts=dict(sorted(texts.items())))


def check_mentions(research: Research, allowed: Iterable[str]) -> None:
    """``ConfigError`` unless every ``mentions`` allowance names the family of a license
    allowed on its own, without an exception (or a public-domain dedication, which an
    allowed CC0-1.0 or Unlicense stands for): an allowance never lets an excluded license
    through, and never plain GPL on the strength of the GPL with the font exception."""
    from tff_catalog.config_model import ConfigError

    families = _expected_families(i for i, exc in allowed_pairs(allowed) if exc is None)
    for fam in research.families.values():
        for mention in fam.mentions:
            if mention not in families:
                raise ConfigError(
                    f"{RESEARCH_FILE}: family {fam.family!r}: mentions {mention!r} is not "
                    "the family of an allowed license"
                )


def apply_font_files(
    evidence: Mapping[str, Evidence], hand: Mapping[str, Sequence[FontFileRef]]
) -> dict[str, Evidence]:
    """``evidence`` with each family's files from ``config/font-files.toml``
    (``fontfiles.load_font_files``) before the sources' ones; ``apply_research``,
    applied after this, puts research's own files before both."""
    from tff_catalog.fontfiles import with_hand_files

    out = dict(evidence)
    for fid, refs in hand.items():
        old = out.get(fid, Evidence())
        out[fid] = Evidence(texts=old.texts, files=tuple(with_hand_files(old.files, refs)))
    return dict(sorted(out.items()))


def apply_research(evidence: Mapping[str, Evidence], research: Research) -> dict[str, Evidence]:
    """``evidence`` with each researched family's texts and font files first, in the
    order ``config/license-texts.toml`` lists them."""
    out = dict(evidence)
    for fid, fam in research.families.items():
        old = out.get(fid, Evidence())
        texts = tuple(TextRef(fetchable_url(u), source=RESEARCH_SOURCE) for u in fam.texts)
        files = tuple(FontFileRef(u) for u in fam.files)
        seen_texts = {t.url for t in texts}
        out[fid] = Evidence(
            texts=texts + tuple(t for t in old.texts if t.url not in seen_texts),
            files=files + tuple(f for f in old.files if f.url not in fam.files),
        )
    return dict(sorted(out.items()))


# --- scope --------------------------------------------------------------------------------------


def scope(
    ranks: Mapping[str, Mapping[str, Any]],
    members: Iterable[str],
    extra_ranks: Sequence[str],
    overall_max: int,
    extra_top: int,
) -> list[str]:
    """The families L3 checks: overall order ``overall_max`` or better, the top
    ``extra_top`` of each extra rank, and every current catalog member; sorted.

    The limits are ``ranking.toml`` ``membership.l3_overall_max`` and ``l3_extra_top``.
    """
    ids = set(members)
    ids |= {fid for fid, p in ranks.get("overall", {}).items() if p.order <= overall_max}
    for key in extra_ranks:
        ids |= {fid for fid, p in ranks.get(key, {}).items() if p.order <= extra_top}
    return sorted(ids)


def catalog_members(membership: Any) -> list[str]:
    """Catalog members and top-100 list members of a ``membership.Membership``."""
    ids = {fid for fid, s in membership.catalog.items() if s.member}
    for lst in membership.top100.values():
        ids |= {fid for fid, s in lst.items() if s.member}
    return sorted(ids)


# --- rulings ------------------------------------------------------------------------------------


# A ruling's ``text_sha256`` when the family has no license text. Not "": a rulings
# file holds only non-empty values (schemas/review.schema.json), so reviews refuses
# to record a question that pins an empty one.
NO_TEXT = "none"


def pinned_sha(sha256: str | None) -> str:
    """The ``text_sha256`` a gate L3 question pins and a ruling carries for this text."""
    return sha256 or NO_TEXT


@dataclass(frozen=True, slots=True)
class L3Ruling:
    """The owner's latest gate L3 answer for one family."""

    family_id: str
    day: date
    choice: Literal["a", "b", "c"]
    text_sha256: str  # the text ruled on; NO_TEXT for none

    def covers(self, sha256: str | None) -> bool:
        return self.text_sha256 == pinned_sha(sha256)


def question_id(family_id: str) -> str:
    return f"{GATE}-{family_id}"


def load_l3_rulings(paths: Paths, log: logging.Logger | None = None) -> dict[str, L3Ruling]:
    """Gate L3 answers by family id; a later ruling overrides an earlier one.

    An answer without a ``text_sha256`` value, or with a choice outside a-c,
    is skipped with a warning: a ruling must name the text it covers.
    """
    from tff_catalog import reviews

    out: dict[str, L3Ruling] = {}
    for ruling in reviews.load_rulings(paths, GATE):
        for answer in ruling.answers:
            if not answer.id.startswith(f"{GATE}-"):
                continue
            sha = dict(answer.values).get("text_sha256")
            if answer.choice not in CHOICES or not isinstance(sha, str):
                if log is not None:
                    log.warning(
                        "gate L3 %s (%s): needs a choice a-c and text_sha256", answer.id, ruling.day
                    )
                continue
            fid = answer.id.removeprefix(f"{GATE}-")
            out[fid] = L3Ruling(fid, ruling.day, answer.choice, sha)  # type: ignore[arg-type]
    return out


# --- sources: texts and font files --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FetchedText:
    """One license text as fetched (or why it could not be)."""

    url: str
    status: int  # HTTP status; 0 when no answer came
    sha256: str | None = None  # of the bytes, when status is 200
    text: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.text is not None

    @property
    def transient(self) -> bool:
        """A network failure, not an answer about the file."""
        return self.status == 0 or self.status == 429 or self.status >= 500

    def to_json(self) -> dict[str, Any]:
        return {
            "bytes": None if self.text is None else len(self.text.encode("utf-8")),
            "error": self.error,
            "sha256": self.sha256,
            "status": self.status,
            "text": self.text,
            "url": self.url,
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> FetchedText:
        return cls(
            url=str(d["url"]),
            status=int(d["status"]),
            sha256=d.get("sha256"),
            text=d.get("text"),
            error=d.get("error"),
        )


class TextSource(Protocol):
    def get(self, ref: TextRef) -> FetchedText: ...

    def read_lately(self, sha256: str) -> bool:
        """Whether a text with this sha256 was read in the stale window before the run."""
        ...


class FactsSource(Protocol):
    def get(self, ref: FontFileRef) -> tuple[FontFacts | None, str | None]:
        """The file's facts, or None and why."""
        ...

    def size(self, url: str) -> int | None:
        """The size in bytes of the file ``get`` read at ``url``, when known."""
        ...


_WEB_PAGE = re.compile(r"\A\s*(?:<!--.*?-->\s*)*<(?:!doctype\s+html|html)[\s>]", re.I | re.S)


def is_web_page(text: str) -> bool:
    """Whether ``text`` is an HTML document rather than a plain license text."""
    return bool(_WEB_PAGE.match(text))


def decode_text(data: bytes) -> str:
    """License text bytes as text: UTF-16 with a BOM (Windows editors save OFL.txt so),
    else UTF-8 (a BOM dropped), else Latin-1."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def read_texts(snapshot: Any) -> dict[str, FetchedText]:
    """The rows of a ``license_texts`` snapshot, by URL."""
    if snapshot is None or not snapshot.has(TEXTS_EXTRACT):
        return {}
    rows = (FetchedText.from_json(row) for row in snapshot.iter_jsonl(TEXTS_EXTRACT))
    return {row.url: row for row in rows}


def _read_between(store: Store, first: date, before: date) -> frozenset[str]:
    """The sha256 of every text read (status 200) in a ``license_texts`` snapshot
    dated from ``first`` up to, but not including, ``before``."""
    shas: set[str] = set()
    for day in store.dates(PSEUDO_SOURCE):
        if first <= day < before:
            rows = read_texts(store.snapshot(PSEUDO_SOURCE, day)).values()
            shas |= {row.sha256 for row in rows if row.ok and row.sha256}
    return frozenset(shas)


class StoreTexts:
    """License texts through the ``license_texts`` pseudo-source (module docstring)."""

    def __init__(
        self,
        store: Store,
        run_date: date,
        fetcher: Fetcher | None,
        *,
        persist: bool,
        refetch: bool = False,
        frozen: frozenset[date] = frozenset(),
        window: timedelta = timedelta(days=62),
    ) -> None:
        today = store.snapshot(PSEUDO_SOURCE, run_date)
        self._lately = _read_between(store, run_date - window, run_date)
        fixed = today is not None and not refetch
        self._store, self._day, self._frozen_days = store, run_date, frozen
        self._fetcher = None if fixed else fetcher
        self._persist = persist and self._fetcher is not None
        self._refetch = today is not None
        self._hint = "; rerun verify with --refetch" if fixed and fetcher is not None else ""
        base = today if today is not None else store.latest(PSEUDO_SOURCE, run_date)
        self._base_day = base.date if base is not None else None
        self._known = read_texts(base)
        self._by_sha = {r.sha256: r for r in self._known.values() if r.ok and r.sha256}
        self._rows: dict[str, FetchedText] = {}
        self._records: list[Any] = []
        self.fetched = 0
        self.reused = 0

    def get(self, ref: TextRef) -> FetchedText:
        url = ref.url
        if url in self._rows:
            return self._rows[url]
        known = self._known.get(url)
        if self._fetcher is None:
            row = known or FetchedText(url, 0, error=self._missing())
        elif known is not None and known.ok and is_pinned(url):
            row, self.reused = known, self.reused + 1
        elif ref.sha256 is not None and ref.sha256 in self._by_sha:
            row, self.reused = replace(self._by_sha[ref.sha256], url=url), self.reused + 1
        else:
            row, self.fetched = self._fetch(url), self.fetched + 1
        self._rows[url] = row
        return row

    def read_lately(self, sha256: str) -> bool:
        """Whether a ``license_texts`` snapshot of the window before the run (never the
        run's own, so a replay answers as the live run did) holds this text, read."""
        return sha256 in self._lately

    def _missing(self) -> str:
        if self._base_day is None:
            return "no license_texts snapshot in the store"
        return f"not in the license_texts snapshot of {self._base_day.isoformat()}{self._hint}"

    def _fetch(self, url: str) -> FetchedText:
        from tff_catalog.fetch import FetchError, HostNotAllowed

        assert self._fetcher is not None
        member = archive_member(url)
        try:
            res = self._fetcher.get(member[0] if member else url, expect=TEXT_STATUSES)
        except (FetchError, HostNotAllowed) as exc:
            return FetchedText(url, 0, error=str(exc)[:300])
        self._records.extend(res.to_records())
        if res.status != 200:
            return FetchedText(url, res.status, error=f"HTTP {res.status}")
        if member is not None:
            # A text inside a release archive: the row is the member's, bytes and sha256.
            try:
                data = read_member(res.content, member[1])
            except MemberError as exc:
                return FetchedText(url, 404 if exc.missing else 200, error=f"{member[0]}: {exc}")
            return FetchedText(url, 200, hashlib.sha256(data).hexdigest(), decode_text(data))
        sha = res.sha256 or hashlib.sha256(res.content).hexdigest()
        if res.size > MAX_TEXT_BYTES:
            return FetchedText(url, 200, sha, error=f"{res.size} bytes: too large for a license")
        return FetchedText(url, 200, sha, decode_text(res.content))

    def close(self) -> None:
        """Save the texts used as today's snapshot (live runs only)."""
        if not self._persist or not self._rows:
            return
        rows = [self._rows[u].to_json() for u in sorted(self._rows)]
        data = b"".join(jsonio.canonical_bytes(r) + b"\n" for r in rows)
        writer = self._store.writer(
            PSEUDO_SOURCE,
            self._day,
            SNAPSHOT_VERSION,
            refetch=self._refetch,
            frozen=self._frozen_days,
        )
        with writer:
            writer.write_bytes(TEXTS_EXTRACT, data, rows=len(rows))
            for record in self._records:
                writer.record_fetch(record)
            writer.set_data_date(self._day)
            writer.note(f"license texts read by stage {STAGE}")


class StoreFacts:
    """Font-file facts through the facts cache and the ``font_facts`` pseudo-source.

    Each read also records the file's size when the source gave none (``FileRead.size``),
    because export publishes the file only with its size: a replay reads it from the
    ``font_facts`` snapshot. A live run takes it from the file it downloaded, else from
    the latest ``font_facts`` snapshot up to the run's day (same url and sha256), else
    from the specimens' font cache (``~/.cache/tff/fonts/<sha256>``), else it downloads
    the file again and checks its sha256.
    """

    def __init__(
        self,
        store: Store,
        run_date: date,
        fetcher: Fetcher | None,
        *,
        persist: bool,
        frozen: frozenset[date] = frozenset(),
        font_cache: Path | None = None,
    ) -> None:
        from tff_catalog import fontfiles

        self._ff = fontfiles
        self._store, self._day, self._frozen_days = store, run_date, frozen
        self._fetcher = fetcher
        self._persist = persist and fetcher is not None
        self._cache = fontfiles.FontFileCache(fontfiles.cache_path(store.root))
        self._recorded = (
            {} if fetcher is not None else fontfiles.recorded_reads(store, run_date, prefer=STAGE)
        )
        self._known = {} if fetcher is None else _known_sizes(store, run_date)
        if font_cache is None:
            from tff_catalog.specimens.stage import FONT_CACHE

            font_cache = FONT_CACHE
        self._font_cache = font_cache.expanduser()
        self._reads = fontfiles.ReadLog()
        self._sizes: dict[str, int] = {}
        self.size_problems: dict[str, str] = {}  # url -> why its size stayed unknown (live)

    def get(self, ref: FontFileRef) -> tuple[FontFacts | None, str | None]:
        ff = self._ff
        if self._fetcher is None:
            read = self._recorded.get(ref.url)
            if read is not None and read.sha256 is None:
                return None, read.error or "the live run could not read it"
            if read is not None:
                ref = replace(ref, sha256=read.sha256)
            found = ff.cached_facts(ref, self._cache)
            if found is None:
                return None, "no cached facts and no network (replay)"
            if read is not None and read.size is not None and read.sha256 == found.sha256:
                self._sizes[ref.url] = read.size
            return found, None
        from tff_catalog.fetch import FetchError, HostNotAllowed

        try:
            facts, size = ff.facts_and_size(ref, self._fetcher, self._cache)
        except (ff.FontFileError, ff.FontFactsMissing, FetchError, HostNotAllowed) as exc:
            self._reads.add(ff.FileRead(ref.url, None, str(exc)[:300]))
            return None, str(exc)[:300]
        if size is None and ref.size is None:
            size = self._size_of(ref.url, facts.sha256)
        if size is not None:
            self._sizes[ref.url] = size
        self._reads.add(ff.FileRead(ref.url, facts.sha256, size=size))
        return facts, None

    def size(self, url: str) -> int | None:
        return self._sizes.get(url)

    def _size_of(self, url: str, sha256: str) -> int | None:
        """The size of the file with ``sha256`` at ``url``, found without its facts read."""
        known = self._known.get((url, sha256))
        if known is not None:
            return known
        local = self._font_cache / sha256
        if local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest() == sha256:
            return local.stat().st_size
        from tff_catalog.fetch import FetchError, HostNotAllowed

        ff = self._ff
        assert self._fetcher is not None
        try:
            if ff.split_member(url) is not None:
                data = ff.read_zip_member(url, self._fetcher)
            else:
                data = self._fetcher.get(url).content
        except (ff.FontFileError, FetchError, HostNotAllowed) as exc:
            self.size_problems[url] = str(exc)[:300]
            return None
        if hashlib.sha256(data).hexdigest() != sha256:
            self.size_problems[url] = f"it now serves another file than sha256 {sha256}"
            return None
        return len(data)

    def close(self) -> None:
        """Save new facts in the cache and the reads as today's ``font_facts`` (live runs only)."""
        if not self._persist:
            return
        self._cache.flush()
        if self._reads.reads:
            self._ff.record_reads(
                self._store, self._day, STAGE, self._reads.reads.values(), frozen=self._frozen_days
            )


def _known_sizes(store: Store, run_date: date) -> dict[tuple[str, str], int]:
    """Every recorded size in the latest ``font_facts`` snapshot up to ``run_date``,
    by (url, sha256): a live run reuses them rather than download a file again."""
    from tff_catalog import fontfiles

    latest = store.latest(fontfiles.READS_SOURCE, run_date)
    if latest is None:
        return {}
    reads = fontfiles.recorded_reads(store, latest.date).values()
    return {(r.url, r.sha256): r.size for r in reads if r.sha256 and r.size is not None}


@contextmanager
def open_sources(
    ctx: StageContext, *, persist: bool = True
) -> Iterator[tuple[TextSource, FactsSource]]:
    """The store-backed text and facts sources for a run; saved on a clean exit when ``persist``."""
    from tff_catalog.fontfiles import READS_SOURCE
    from tff_catalog.store import stale_max_age

    store = ctx.require_store()
    texts = StoreTexts(
        store,
        ctx.run_date,
        ctx.fetcher,
        persist=persist,
        refetch=ctx.options.refetch,
        frozen=ctx.state.frozen_dates(PSEUDO_SOURCE),
        window=stale_max_age(ctx.config.ranking.stale.max_months),
    )
    facts = StoreFacts(
        store,
        ctx.run_date,
        ctx.fetcher,
        persist=persist,
        frozen=ctx.state.frozen_dates(READS_SOURCE),
    )
    yield texts, facts
    texts.close()
    facts.close()
    ctx.log.info("verify: %d license texts fetched, %d reused", texts.fetched, texts.reused)
    for url, why in sorted(facts.size_problems.items()):
        ctx.log.warning("verify: the size of %s is unknown, so export leaves it out: %s", url, why)


# --- the check ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything the check reads besides the network."""

    names: Mapping[str, str]  # family id -> family name
    verdicts: Mapping[str, Verdict]
    evidence: Mapping[str, Evidence]
    previous: Mapping[str, Mapping[str, Any]]  # state/license_hashes.json
    rulings: Mapping[str, L3Ruling]
    canon: Canon
    allowed: frozenset[str]  # licenses.toml allowed ids
    research: Research = field(default_factory=Research)  # config/license-texts.toml


@dataclass(slots=True)
class _TextCheck:
    fetched: FetchedText
    matched: str | None = None
    ids: frozenset[str] = frozenset()  # the text ids a clean match verifies
    exceptions: frozenset[str] = frozenset()
    problems: list[str] = field(default_factory=list)
    note: str | None = None
    # The problems that fail the family even when another text verifies it: a use
    # restriction, or added words the fingerprint cannot see (a long notice).
    blocking: list[str] = field(default_factory=list)


def _expected(verdict: Verdict | None) -> tuple[str | None, list[str]]:
    """The L2 expression to verify, or the reason there is none."""
    if verdict is None or verdict.spdx is None:
        return None, ["L2 settled no license"]
    if verdict.license is None or verdict.license.status != "allowed":
        status = verdict.license.status if verdict.license else "none"
        return None, [f"L2 license {verdict.spdx} is not allowed (status {status})"]
    try:
        expression_ids(verdict.spdx)
    except ValueError as exc:
        return None, [f"L2 expression: {exc}"]
    return verdict.spdx, []


def _check_text(
    fetched: FetchedText, expr: str, families: frozenset[str], inputs: Inputs
) -> _TextCheck:
    out = _TextCheck(fetched)
    if not fetched.ok:
        out.problems.append(f"license text {fetched.url}: {fetched.error or fetched.status}")
        return out
    text = fetched.text or ""
    known = inputs.research.texts.get(fetched.sha256 or "")
    if known is not None and not is_web_page(text):
        # Research read this exact text word for word, a canonical body under a notice
        # too long for the word limits included (Twilio Sans Mono's list of names).
        out.exceptions = inputs.canon.exceptions_in(text)
        return _check_researched(out, text, known, expr, families)
    out.matched, out.exceptions = inputs.canon.identify(text)
    ids = expression_ids(expr)
    where = f"a notice in license text {fetched.url}"
    if out.matched is None:
        if is_web_page(text):
            # A license's home page (scripts.sil.org/OFL, openfontlicense.org), as Fontist and
            # name ID 14 give it, is no upstream text; its site's words are not the font's terms.
            out.problems.append(f"license text {fetched.url} is a web page, not a license text")
            return out
        close = inputs.canon.closest(text, ids)
        near = f" (closest: {close[0]}, {close[1]:.1%} of words alike)" if close else ""
        out.problems.append(f"license text {fetched.url} matches no known license{near}")
        # An unknown text's own words may restrict use: the words above a license
        # body, or the whole text when it has none ("free for personal use").
        flat = _flat(text)
        start = _body_start(flat)
        scan = flat if start is None else flat[:start]
        out.blocking = [f"{where} restricts use: {p!r}" for p in restrictions(scan)]
        out.problems += out.blocking
        return out
    out.ids = verified_ids(out.matched)
    if not out.ids & {text_id(i) for i in ids}:
        out.problems.append(f"license text {fetched.url} is {out.matched}, not {expr}")
    # Restrictions and other licenses are looked for in every notice, the copyright
    # lines below the body included: the fingerprint leaves those lines out.
    said = statement_problems(where, notices(text, out.matched, inputs.canon), families)
    out.problems += said
    out.blocking = [p for p in said if " restricts use: " in p]
    known = (*inputs.canon.headers.get(out.matched, ()), *STANDARD_NOTICES)
    extra = _without(header_words(text), known).split()
    if len(extra) > EXTRA_WORDS_MAX:
        out.blocking.append(
            f"the notice above license text {fetched.url} has {len(extra)} words besides "
            f"copyright, Reserved Font Name and license title lines: {' '.join(extra[:12])} ..."
        )
    for words in long_notice_lines(text, known):
        out.blocking.append(
            f"a copyright or Reserved Font Name line in license text {fetched.url} has "
            f"{len(words)} words: {' '.join(words[:12])} ..."
        )
    out.problems += [p for p in out.blocking if p not in out.problems]
    return out


def _check_researched(
    out: _TextCheck, text: str, known: ResearchedText, expr: str, families: frozenset[str]
) -> _TextCheck:
    """A text ``config/license-texts.toml`` lists by its sha256: it verifies the licenses
    research found in it. Its *whole* text is read for restrictions and for other
    licenses (no license body is set aside), and only this exact text is spared the
    notice word limits, because research read every word of it."""
    url = out.fetched.url
    out.matched = " AND ".join(sorted(known.licenses))
    out.ids = frozenset(text_id(i) for i in known.licenses)
    out.note = f"license text {url} is a researched text (config/{RESEARCH_FILE}): {out.matched}"
    if not out.ids & {text_id(i) for i in expression_ids(expr)}:
        out.problems.append(f"license text {url} is {out.matched}, not {expr}")
    said = statement_problems(f"license text {url}", text, families)
    out.problems += said
    out.blocking = [p for p in said if " restricts use: " in p]
    return out


def _without(words: str, phrases: Iterable[str]) -> str:
    """``words`` (space-separated) with every whole-word occurrence of ``phrases`` removed."""
    padded = f" {words} "
    for phrase in phrases:
        if phrase:
            padded = padded.replace(f" {phrase} ", " ")
    return padded.strip()


def expected_families(expr: str, family_id: str, research: Research) -> frozenset[str]:
    """The license families a family's notices and name IDs may name: its L2
    expression's, plus the ``mentions`` research allowed for it."""
    fam = research.families.get(family_id)
    return _expected_families(expression_ids(expr)) | frozenset(fam.mentions if fam else ())


def _texts_verdict(
    family_id: str, expr: str, evidence: Evidence, texts: TextSource, inputs: Inputs
) -> tuple[list[_TextCheck], bool]:
    """Try candidate texts until the expression is verified; (checks, verified)."""
    families = expected_families(expr, family_id, inputs.research)
    allowed = family_allowed(expr, inputs.allowed)
    checks: list[_TextCheck] = []
    matched: set[str] = set()
    exceptions: set[str] = set()
    for ref in evidence.texts[:MAX_TEXTS]:
        check = _check_text(texts.get(ref), expr, families, inputs)
        checks.append(check)
        if check.matched and not check.problems:
            matched |= check.ids
            exceptions |= check.exceptions
            if satisfied(expr, frozenset(matched), allowed, frozenset(exceptions)):
                return checks, True
    return checks, False


def _primary(checks: Sequence[_TextCheck], verified: bool) -> _TextCheck | None:
    """The text a result reports: the first clean match, else the first fetched, else the first."""
    for want in (
        (lambda c: c.matched is not None and not c.problems) if verified else None,
        lambda c: c.fetched.ok,
        lambda c: True,
    ):
        if want is not None:
            for c in checks:
                if want(c):
                    return c
    return None


def name_id_problems(where: str, value: str, families: frozenset[str], canon: Canon) -> list[str]:
    """Fatal problems of name ID 13 or 14. A font that carries a whole license text there
    is judged like a license text: by the license it is and its notices, not by its body."""
    matched, _ = canon.identify(value)
    problems = []
    if matched is not None and license_family(matched) not in families:
        problems.append(f"{where} is the {matched} text")
    return problems + statement_problems(where, notices(value, matched, canon), families)


def _file_check(
    evidence: Evidence, facts: FactsSource, families: frozenset[str] | None, canon: Canon
) -> tuple[FontFileRef | None, FontFacts | None, list[str], list[str]]:
    """Read the first readable of the best font files: (ref used, facts, fatal problems, notes)."""
    if not evidence.files:
        return None, None, ["no font file to check name IDs 13 and 14"], []
    errors: list[str] = []
    for ref in evidence.files[:MAX_FILES]:
        found, error = facts.get(ref)
        if found is not None:
            break
        errors.append(f"font file {ref.url}: {error}")
    else:
        return evidence.files[0], None, errors, []
    fatal: list[str] = []
    notes: list[str] = list(errors)
    id13, id14 = found.license_description, found.license_url
    if families is not None:
        fatal += name_id_problems("name ID 13", id13 or "", families, canon)
        fatal += name_id_problems("name ID 14", id14 or "", families, canon)
    if not (id13 or id14):
        notes.append("name IDs 13 and 14 are empty")
    if not is_pinned(ref.url):
        notes.append(f"font file {ref.url} is not pinned to a commit or version")
    if ref.sha256 is not None and ref.sha256 != found.sha256:
        notes.append(f"font file sha256 {found.sha256} differs from the source's {ref.sha256}")
    return ref, found, fatal, notes


def check_family(
    family_id: str,
    inputs: Inputs,
    texts: TextSource,
    facts: FactsSource,
    run_date: date,
) -> L3Result:
    """Run L3 for one family (module docstring); never raises for bad upstream data."""
    evidence = inputs.evidence.get(family_id, Evidence())
    previous = inputs.previous.get(family_id)
    expr, fatal = _expected(inputs.verdicts.get(family_id))
    notes: list[str] = []
    checks: list[_TextCheck] = []
    verified = False
    if expr is not None:
        if not evidence.texts:
            fatal.append("no upstream license text found")
        else:
            checks, verified = _texts_verdict(family_id, expr, evidence, texts, inputs)
    primary = _primary(checks, verified)
    carried = False
    if expr is not None and not verified:
        down = primary.fetched if primary is not None and primary.fetched.transient else None
        if down is not None and _was_verified(previous) and _read_lately(previous, texts):
            carried = True
            notes.append(
                f"license text unavailable ({down.error or down.status}); "
                f"kept the level checked on {previous['checked_on']}"  # type: ignore[index]
            )
        else:
            fatal += [p for c in checks for p in c.problems]
            if down is not None and _was_verified(previous):
                fatal.append(
                    "the verified text was not read in the stale window ([stale] max_months), "
                    "so its level is not kept"
                )
            if checks and not fatal:
                fatal.append(f"the license texts found do not verify {expr}")
    elif verified:
        # A text that restricts use, or carries words the fingerprint cannot see, fails
        # the family even when another text verifies it.
        fatal += [p for c in checks for p in c.blocking]
        notes += [c.note for c in checks if c.note and c.matched and not c.problems]
    families = expected_families(expr, family_id, inputs.research) if expr is not None else None
    ref, found, file_fatal, file_notes = _file_check(evidence, facts, families, inputs.canon)
    fatal += file_fatal
    notes += file_notes
    text_url = primary.fetched.url if primary else None
    text_sha = primary.fetched.sha256 if primary else None
    if carried:
        text_url, text_sha = previous["text_url"], previous["text_sha256"]  # type: ignore[index]
    level, owner_notes = _level(fatal, inputs.rulings.get(family_id), text_sha, carried, previous)
    matched = " AND ".join(sorted({c.matched for c in checks if c.matched and not c.problems}))
    return L3Result(
        family_id=family_id,
        level=level,
        checked_on=_checked_on(previous, text_sha, level, run_date),
        text_url=text_url,
        text_sha256=text_sha,
        matched=matched or (primary.matched if primary else None),
        name_ids=(found.license_description, found.license_url) if found else (None, None),
        font_version=found.version if found else None,
        font_file=FontFileRef(
            url=ref.url,
            sha256=found.sha256,
            size=ref.size or facts.size(ref.url),
            role=ref.role,
            git_blob=ref.git_blob,
        )
        if ref is not None and found is not None
        else None,
        problems=tuple(dict.fromkeys(fatal + owner_notes)) + tuple(f"note: {n}" for n in notes),
        copyright=found.copyright if found else None,
    )


def _was_verified(previous: Mapping[str, Any] | None) -> bool:
    return previous is not None and previous.get("level") in ("L3", "ruling")


def _read_lately(previous: Mapping[str, Any] | None, texts: TextSource) -> bool:
    """Whether last level's text was read in the stale window (``TextSource.read_lately``),
    so a network failure may keep that level: for ``[stale] max_months``, as for a source."""
    sha = previous.get("text_sha256") if previous is not None else None
    return isinstance(sha, str) and texts.read_lately(sha)


def _level(
    fatal: Sequence[str],
    ruling: L3Ruling | None,
    text_sha: str | None,
    carried: bool,
    previous: Mapping[str, Any] | None,
) -> tuple[Level, list[str]]:
    """The level, and the ruling that decided it (as a problem line).

    An exclusion (a) always holds, and one made for an earlier text keeps the
    family out until the owner rules on the new one; otherwise a clean check
    is L3 whatever the ruling, and a failure is kept (b) or stays failed (c,
    or no ruling).
    """
    valid = ruling if ruling is not None and ruling.covers(text_sha) else None
    said = f"owner ruling {question_id(valid.family_id)} ({valid.day.isoformat()})" if valid else ""
    if valid is not None and valid.choice == "a":
        return "failed", [f"{said}: exclude"]
    if ruling is not None and valid is None and ruling.choice == "a":
        return "failed", [
            f"owner ruling {question_id(ruling.family_id)} ({ruling.day.isoformat()}) "
            f"excluded it for license text {ruling.text_sha256}; the text changed, "
            "so the owner rules again"
        ]
    if not fatal:
        if carried and previous is not None and previous.get("level") == "ruling":
            return "ruling", []
        return "L3", []
    if valid is not None and valid.choice == "b":
        return "ruling", [f"{said}: keep"]
    if valid is not None:
        return "failed", [f"{said}: research"]
    return "failed", []


def _checked_on(
    previous: Mapping[str, Any] | None, text_sha: str | None, level: str, run_date: date
) -> date:
    if (
        previous is not None
        and previous.get("text_sha256") == text_sha
        and previous.get("level") == level
        and isinstance(previous.get("checked_on"), str)
    ):
        return date.fromisoformat(previous["checked_on"])
    return run_date


# --- the queue and the state --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One entry of ``build/stage/queues/l3.json``."""

    id: str  # the question id, "L3-<family id>"
    family_id: str
    family: str
    reason: Literal["changed", "failed", "research"]
    level: Literal["L3", "failed", "ruling"]
    needs_owner: bool
    expected: str | None  # the L2 expression
    matched: str | None
    text_url: str | None
    text_sha256: str | None
    previous_sha256: str | None  # from state/license_hashes.json, for "changed"
    problems: tuple[str, ...]
    question: Question | None  # for the owner; None when nothing needs deciding


@dataclass(frozen=True, slots=True)
class Queue:
    schema: int
    items: tuple[QueueItem, ...]  # by id
    counts: dict[str, int]  # checked, L3, ruling, failed, changed, needs_owner


def _question(result: L3Result, name: str, expected: str | None) -> Question:
    fatal = [p for p in result.problems if not p.startswith("note: ")]
    text = (
        f"{name} ({result.family_id}) failed the L3 license check"
        f"{f' for {expected}' if expected else ''}: {'; '.join(fatal)}. "
        f"Text: {result.text_url or 'none'}. Record the answer with "
        f'text_sha256 = "{pinned_sha(result.text_sha256)}".'
    )
    return Question(gate=GATE, id=question_id(result.family_id), text=text, options=OPTIONS)


def queue_item(
    result: L3Result, inputs: Inputs, previous: Mapping[str, Any] | None
) -> QueueItem | None:
    """The queue entry for one result, or None (module docstring)."""
    ruling = inputs.rulings.get(result.family_id)
    valid = ruling if ruling is not None and ruling.covers(result.text_sha256) else None
    prev_sha = previous.get("text_sha256") if previous is not None else None
    changed = previous is not None and prev_sha != result.text_sha256
    if not changed and result.level != "failed":
        return None
    if not changed and valid is not None and valid.choice == "a":
        return None  # excluded by the owner; nothing to decide
    needs_owner = result.level == "failed" and not (valid is not None and valid.choice == "a")
    reason: Reason = "changed" if changed else "research" if valid is not None else "failed"
    verdict = inputs.verdicts.get(result.family_id)
    expected = verdict.spdx if verdict is not None else None
    name = inputs.names.get(result.family_id, result.family_id)
    return QueueItem(
        id=question_id(result.family_id),
        family_id=result.family_id,
        family=name,
        reason=reason,
        level=result.level,
        needs_owner=needs_owner,
        expected=expected,
        matched=result.matched,
        text_url=result.text_url,
        text_sha256=result.text_sha256,
        previous_sha256=prev_sha if changed else None,
        problems=result.problems,
        question=_question(result, name, expected) if needs_owner else None,
    )


def build_queue(results: Mapping[str, L3Result], inputs: Inputs) -> Queue:
    """The L3 queue for a run's results."""
    items = [
        item
        for fid in sorted(results)
        if (item := queue_item(results[fid], inputs, inputs.previous.get(fid))) is not None
    ]
    counts = {
        "checked": len(results),
        "L3": sum(r.level == "L3" for r in results.values()),
        "ruling": sum(r.level == "ruling" for r in results.values()),
        "failed": sum(r.level == "failed" for r in results.values()),
        "changed": sum(i.reason == "changed" for i in items),
        "needs_owner": sum(i.needs_owner for i in items),
    }
    return Queue(QUEUE_SCHEMA, tuple(items), counts)


def state_entry(result: L3Result) -> dict[str, Any]:
    """The ``license_hashes`` entry of one result."""
    ff = result.font_file
    return {
        "checked_on": result.checked_on.isoformat(),
        "font_file": None if ff is None else {"sha256": ff.sha256, "url": ff.url},
        "font_version": result.font_version,
        "level": result.level,
        "text_sha256": result.text_sha256,
        "text_url": result.text_url,
    }


def next_license_hashes(
    previous: Mapping[str, Mapping[str, Any]], results: Mapping[str, L3Result]
) -> dict[str, Any]:
    """``license_hashes`` for ``build/state/``: this run's results over the old entries."""
    out: dict[str, Any] = {fid: dict(entry) for fid, entry in previous.items()}
    out.update({fid: state_entry(r) for fid, r in results.items()})
    return dict(sorted(out.items()))


# --- the stage ----------------------------------------------------------------------------------


def _require(paths: Paths, name: str, stage: str) -> Any:
    path = stageio.stage_path(paths, name)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing: run `tff-catalog {stage}` first")
    return stageio.load_stage(paths, name)


def load_inputs(ctx: StageContext) -> tuple[Inputs, list[str]]:
    """Read the stage files, config, state and rulings; return the inputs and the scope.

    The allowed ids are ``licenses.toml``'s with gate LIC's license rulings applied
    (``licenses.effective_config``), as stage "licenses" used them.
    """
    from tff_catalog import licenses
    from tff_catalog.fontfiles import load_font_files

    paths = ctx.paths
    universe = _require(paths, "universe", "universe")
    verdicts = _require(paths, "licenses", "licenses")
    ranks = _require(paths, "ranks", "rank")
    membership = _require(paths, "membership", "membership")
    m = ctx.config.ranking.membership
    wanted = scope(
        ranks, catalog_members(membership), m.extra_ranks, m.l3_overall_max, m.l3_extra_top
    )
    # Stage "rank" left out the families the last verify failed, so no rank puts them in
    # scope: check them again, or one fixed since would never come back.
    wanted = sorted(set(wanted) | exclusions(paths))
    ids = [fid for fid in wanted if fid in universe.families]
    if len(ids) < len(wanted):
        unknown = sorted(set(wanted) - set(ids))
        ctx.log.warning(
            "verify: %d ids in scope are not in the universe: %s", len(unknown), unknown
        )
    research = load_research(paths)
    allowed = frozenset(licenses.effective_config(paths, ctx.config.licenses).allowed)
    check_mentions(research, allowed)
    unknown = sorted(set(research.families) - set(universe.families))
    if unknown:
        ctx.log.warning("verify: %s lists families not in the universe: %s", RESEARCH_FILE, unknown)
    evidence = gather(iter_records(paths.records), key_index(universe), universe)
    hand = load_font_files(paths.config)  # stage "latin" checked the ids
    inputs = Inputs(
        names={fid: universe.families[fid].family for fid in ids},
        verdicts=verdicts,
        evidence=apply_research(apply_font_files(evidence, hand), research),
        previous=ctx.state.license_hashes,
        rulings=load_l3_rulings(paths, ctx.log),
        canon=load_canon(canon_dir(paths)),
        allowed=allowed,
        research=research,
    )
    return inputs, ids


def verify_all(
    ids: Iterable[str], inputs: Inputs, texts: TextSource, facts: FactsSource, run_date: date
) -> dict[str, L3Result]:
    """``check_family`` for every id, in sorted order."""
    return {fid: check_family(fid, inputs, texts, facts, run_date) for fid in sorted(ids)}


def write_outputs(ctx: StageContext, results: Mapping[str, L3Result], inputs: Inputs) -> Queue:
    """Write ``l3.json``, the queue and ``build/state/license_hashes.json``."""
    from tff_catalog import state

    queue = build_queue(results, inputs)
    stageio.dump_stage(ctx.paths, "l3", dict(results))
    stageio.dump(queue, queue_path(ctx.paths))
    state.write_part(
        ctx.paths,
        "license_hashes",
        next_license_hashes(ctx.state.license_hashes, results),
        stage=STAGE,
    )
    return queue


_INPUTS: list[tuple[StageContext, Inputs]] = []  # verify()'s one-entry cache


def verify(fam: Family, ctx: StageContext) -> L3Result:
    """Run L3 for one family.

    Reads the same stage files as ``run`` (cached for the same context). Texts
    and font facts come from the store and, outside replay, the network, but
    nothing is saved: only ``run`` records the day's snapshot.
    """
    if not _INPUTS or _INPUTS[0][0] is not ctx:
        _INPUTS[:] = [(ctx, load_inputs(ctx)[0])]
    inputs = _INPUTS[0][1]
    if fam.id not in inputs.names:
        inputs = replace(inputs, names={**inputs.names, fam.id: fam.family})
    with open_sources(ctx, persist=False) as (texts, facts):
        return check_family(fam.id, inputs, texts, facts, ctx.run_date)


def run(ctx: StageContext) -> None:
    """Stage "verify"."""
    inputs, ids = load_inputs(ctx)
    with open_sources(ctx) as (texts, facts):
        results = verify_all(ids, inputs, texts, facts, ctx.run_date)
    queue = write_outputs(ctx, results, inputs)
    ctx.log.info(
        "verify: %s; queue: %d items",
        ", ".join(f"{k} {v}" for k, v in sorted(queue.counts.items())),
        len(queue.items),
    )


# --- reading the outputs ------------------------------------------------------------------------


def queue_path(paths: Paths) -> Path:
    """``build/stage/queues/l3.json``."""
    return paths.queues / QUEUE_FILE


def load_queue(paths: Paths) -> Queue:
    """Read the queue the last ``verify`` run wrote."""
    return stageio.load(queue_path(paths), Queue)


def questions(paths: Paths) -> list[Question]:
    """Gate L3's open owner questions, from the last run's queue (for ``reviews.questions``)."""
    return [i.question for i in load_queue(paths).items if i.question is not None]


def exclusions(paths: Paths) -> frozenset[str]:
    """The families the last ``verify`` run failed (none before it ran): stage "rank"
    leaves them out."""
    if not stageio.stage_path(paths, "l3").is_file():
        return frozenset()
    results = stageio.load_stage(paths, "l3")
    return frozenset(fid for fid, r in results.items() if r.level == "failed")


def check_catalog(
    members: Iterable[str], results: Mapping[str, L3Result]
) -> tuple[list[str], dict[str, int]]:
    """Catalog members neither at L3 nor held by a ruling; and the counts by level."""
    bad: list[str] = []
    counts = {"L3": 0, "ruling": 0, "failed": 0, "unchecked": 0}
    for fid in sorted(set(members)):
        result = results.get(fid)
        level = result.level if result is not None else "unchecked"
        counts[level] += 1
        if level not in ("L3", "ruling"):
            bad.append(fid)
    return bad, counts


def cmd_check(ctx: StageContext) -> int:
    """``verify --check``: non-zero unless every catalog font is L3 or has an owner ruling."""
    paths = ctx.paths
    try:
        membership = _require(paths, "membership", "membership")
        results = _require(paths, "l3", "verify")
    except FileNotFoundError as exc:
        print(f"tff-catalog verify --check: {exc}", file=sys.stderr)
        return 1
    members = [fid for fid, s in membership.catalog.items() if s.member]
    bad, counts = check_catalog(members, results)
    summary = ", ".join(f"{k} {v}" for k, v in counts.items())
    print(f"tff-catalog verify --check: {len(members)} catalog fonts ({summary})")
    for fid in bad:
        result = results.get(fid)
        why = "; ".join(result.problems) if result is not None else "not checked"
        print(f"  {fid}: {why}")
    return 1 if bad else 0
