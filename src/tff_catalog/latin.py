"""Stage "latin": the Latin gate (milestone-1 step 5, decision D4). Owner: agent P2a.

- Google families: rule A, the strict metadata test (primary script Latin or
  unset, and a ``latin`` subset), plus the dual-script families the owner
  approved (gate L, ``reviews.gate_dir(paths, "L")``).
- Other fonts: the glyph test against ``data/glyphsets/GF_Latin_{Kernel,Core}.nam``
  with the thresholds in ``ranking.toml [latin]`` (gate L1).
- Coverage is ``basic`` (the "limited accents" badge) or ``extended``.

Writes ``build/stage/latin.json`` ({id: LatinResult}) and
``build/review/latin-allowlist.md`` (the dual-script sheet, sorted by Google year
views; ``build/review/`` is gitignored, because ruling T2 keeps view counts
private).

**Google families** are the eligible families with a record from
``GOOGLE_SOURCES`` (the live list first: design-m1 C12 pins it as the source of
truth; a google/fonts repo folder only when the live list lacks the family and
the folder is queued for it; any other repo-only folder takes the glyph test).
They are judged on Google's metadata alone, never on their files, except a
family Google serves only with its ``menu`` subset (the Playwrite families and
Allkin: ``menu_only``), which takes the glyph test like any other font (gate L,
the owner's ruling of 2026-09-26):

- rule A passes: basis ``gf_metadata``;
- primary script CJK (``CJK_SCRIPTS``): out, reason ``cjk``;
- no ``latin`` subset: out, ``no_latin_subset``;
- any other primary script with a ``latin`` subset is a dual-script candidate:
  out (``dual_script``) until the owner includes it (basis ``owner_allowlist``).

Coverage is ``extended`` when the subsets include ``latin-ext``, else ``basic``.

**Other families** take the glyph test on one file: the first readable one of
the family's ``FontFileRef``s. The files ``config/font-files.toml`` lists for
the family come first (``fontfiles.load_font_files``: official files research
found for a family no source gives a readable one; an id the universe lacks
fails the stage), then the records' (``candidate_files``): files of the records
named like the family first (a build's file, Nerd or CJK, only when the family
has none of its own), then by role (regular, variable, other, italic), then refs
with a complete code point list, then refs with a sha256 (read by range, not
downloaded), then URL. Its code points come from the ref's ``unicode_range``
when the source inspected the whole file and listed it in full (the Fontsource
registry; no download, design-m1 C13), else from ``fontfiles.facts_for`` (HTTP
Range reads, cached by file hash in the store's facts cache). Only font files and
fonts inside zip archives are read (``fontfiles.is_readable_url``: no other
archives, no pages, no plain http), at most
``MAX_READS`` per family. A live run logs every read, failures included, in the
``font_facts`` pseudo-source (``font_facts/<date>/latin.jsonl``); a run without
a fetcher (replay) answers from that log and the facts cache and fetches
nothing. Measurements are also cached by file sha256 in
``build/cache/latin.json`` (not read in replay, so a replay repeats the live
run's file choices). A family with no readable file is out (``no_file``). The
thresholds are ``ranking.toml [latin]`` (gate L1; ``L1_REC`` holds the
recommended values it ships with):

- CJK: fewer than ``cjk_codepoints_below`` (1,000) code points in
  ``CJK_RANGES`` (Han, kana, Hangul, Bopomofo), else out (``cjk``); this is the
  "mainly CJK" rule (Sarasa, LXGW WenKai, Pretendard, D2Coding);
- Kernel: at most ``kernel_missing_max`` (2) of GF_Latin_Kernel's code points
  missing, else out (``kernel_missing``);
- Latin share: of the letters that belong to a script (general category L*,
  Unicode Script property not Common, Inherited or Unknown), the share whose
  script is Latin; at least ``latin_share_min`` (0.30), else out
  (``latin_share``). Inter measures 0.60, because it also has Greek and
  Cyrillic. Common letters (the mathematical alphanumerics, letterlike symbols)
  are symbols, not text: counting them would put Iosevka at 0.37;
- coverage is ``extended`` when at most ``core_missing_max`` (3) GF_Latin_Core
  code points are missing, of any kind (Inter lacks U+030B), else ``basic``.

The values in brackets are the owner's gate L1 ruling of 2026-09-26 (the relaxed
thresholds): at most 2 Kernel code points missing (Fantasque Sans Mono lacks the
trademark sign and U+2212 minus), a Latin share of 30% (DejaVu Sans measures 0.35)
and at most 3 Core code points missing for ``extended``.

**Owner rulings** (gate L): any table of ``data/reviews/latin/<date>.toml`` may
carry ``include = [family ids]`` and ``exclude = [family ids]`` next to its
``ruling`` and ``reason``, and a table named after a queue question
(``L-<family id>``) rules on that family with ``choice``: (a) include, (b)
exclude, (c) research (nothing yet). Files apply oldest first, tables in file
order, so a later ruling wins. ``include`` passes a family that failed (basis
``owner_allowlist``); ``exclude`` fails any family (``owner_excluded``). A Google
family without a ``latin`` subset cannot be included.

The owner's L2 rule of 2026-09-26 decides the dual-script candidates: include a
family with full Latin Extended (a ``latin-ext`` subset) unless it is a script
companion, a script version of a family already in the universe (Noto Sans
Arabic of Noto Sans, Hind Siliguri of Hind, Anek Telugu of Anek Latin); leave
out the companions and the families with basic Latin only. Claude applied it
family by family in ``data/reviews/latin/2026-09-26.toml`` (tables
``L2-included``, ``L2-companions`` and ``L2-basic-latin``).

**Queue** (``questions``, gate L's provider for ``reviews``): the stage writes
``build/stage/queues/latin.json``, one question per family still waiting for a
ruling: every dual-script candidate (the sheet's rows, views rank first), then
every Google family that passes rule A with a CJK subset and no primary script
(Single Day: mainly CJK?), with ``QUESTION_OPTIONS``. A dual-script question
carries the L2 rule's advice (``l2_advice``): keep out a family with basic
Latin only, include one with full Latin Extended whose name starts with no
other universe family's name, and no recommendation when one does, since the
extra words may name a script (a companion) or a style (Reem Kufi Fun). The CJK
questions carry no recommendation. ``questions`` drops a queued question once a
ruling covers its family, including an ``include`` or ``exclude`` list recorded
after the stage wrote the queue, so a list ruling stops the asking before the
next run. That can renumber the group questions ``reviews`` prints for gate L.

**CJK builds** (Maple Mono NF CN) are alias rows with relation ``build`` and
detail ``cjk``, matched as the universe matches them: a row in the record key's
own namespace, or a row in a name namespace (``aliases.NAME_NAMESPACES``) for
the record's family name. The universe folds them into their Latin parent,
where stage "correct" counts their installs at ``cjk_build_credit``; here their
files are never the parent's test file. A CJK build with no alias row yet is
its own family and fails as ``cjk``; the sheet lists such families whose name,
without the "CN" and Nerd suffixes, matches a family that passed, as alias
candidates for the lead. Nothing is folded here: matching never guesses.

The sheet's notes also list, for the lead, the rule-A count on the pinned live
list, Google families that pass rule A although they carry a CJK subset
(``CJK_SUBSETS``; the rule has no CJK test when the primary script is unset),
include rulings that could not take effect, and families with no file to test.
"""

import bisect
import dataclasses
import functools
import hashlib
import re
import tomllib
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import fontTools
from fontTools.unicodedata import script as unicode_script

from tff_catalog import fontfiles, jsonio, stageio
from tff_catalog.config_model import Latin
from tff_catalog.fontfiles import FileRead, FontFacts, FontFileCache, ReadLog
from tff_catalog.keys import match_key
from tff_catalog.names import ID_PATTERN, NERD_SUFFIX
from tff_catalog.paths import find_root
from tff_catalog.records import FontFileRef, Observation, SourceKey, UniverseRecord, from_json
from tff_catalog.reviews import Question, gate_dir, questions_from_file

if TYPE_CHECKING:
    import logging

    from tff_catalog.aliases import AliasRow
    from tff_catalog.config_model import GoogleSource
    from tff_catalog.fetch import Fetcher
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext
    from tff_catalog.universe import Family, Universe

Basis = Literal["gf_metadata", "owner_allowlist", "glyph_test"]


@dataclass(frozen=True, slots=True)
class LatinResult:
    latin: bool
    basis: Basis | None  # how it passed; None when it failed
    coverage: Literal["basic", "extended"] | None
    reason: str | None = None  # why it failed: one of REASONS ("cjk", "kernel_missing", ...)
    kernel_missing: tuple[int, ...] = ()
    core_missing: tuple[int, ...] = ()
    latin_share: float | None = None
    cjk_codepoints: int = 0


# Why a family failed (LatinResult.reason).
REASONS = (
    "no_latin_subset",  # Google: no latin subset
    "dual_script",  # Google: another primary script; waits for the owner's allowlist
    "cjk",  # mainly CJK: a CJK primary script, or too many CJK code points
    "kernel_missing",  # glyph test: GF_Latin_Kernel not covered
    "latin_share",  # glyph test: too few of the letters are Latin
    "no_file",  # no font file or code point list to test
    "owner_excluded",  # an owner ruling (gate L)
)

# gate L1: the values ranking.toml [latin] ships with, as the owner ruled on 2026-09-26 (the
# relaxed thresholds). The stage reads the config.
L1_REC = Latin(
    kernel_missing_max=2,
    core_missing_max=3,
    latin_share_min=0.30,
    cjk_codepoints_below=1000,
)
# Google's subset for the font menu (the family name only): a family with no other subset
# is judged by its files (gate L, owner ruling of 2026-09-26: menu_only_google).
MENU_SUBSET = "menu"

# Universe collectors whose records carry Google's metadata, in order of preference.
GOOGLE_SOURCES = ("google_metadata", "google_repo")
LATIN_SCRIPT = "Latn"
# ISO 15924 codes of the CJK scripts: such a primary script means "mainly CJK".
CJK_SCRIPTS = frozenset(
    {"Hani", "Hans", "Hant", "Hira", "Kana", "Hrkt", "Jpan", "Hang", "Kore", "Bopo"}
)
# Google subsets of CJK text: rule A passes such a family when its primary script is unset
# (on 2026-09-25 one did: Single Day, a Korean font), so the sheet notes it for the lead.
CJK_SUBSETS = frozenset(
    {"chinese-simplified", "chinese-traditional", "chinese-hongkong", "japanese", "korean"}
)

# CJK code points counted by the glyph test: ideographs, kana, Hangul and Bopomofo.
# CJK punctuation (U+3000-303F) and fullwidth forms are left out: many Latin fonts
# carry a few of them.
CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x2E80, 0x2FDF),  # CJK Radicals Supplement, Kangxi Radicals
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x3100, 0x312F),  # Bopomofo
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x31A0, 0x31BF),  # Bopomofo Extended
    (0x31F0, 0x31FF),  # Katakana Phonetic Extensions
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xA960, 0xA97F),  # Hangul Jamo Extended-A
    (0xAC00, 0xD7FF),  # Hangul Syllables, Hangul Jamo Extended-B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x3FFFF),  # Supplementary and Tertiary Ideographic Planes
)
_CJK_STARTS = tuple(first for first, _ in CJK_RANGES)
# Script codes of letters that belong to no one script: Common, Inherited, Unknown.
NO_SCRIPT = frozenset({"Zyyy", "Zinh", "Zzzz"})

# The vendored glyphsets: googlefonts/glyphsets (Apache-2.0) at a pinned commit;
# data/glyphsets/NOTICE says where they come from.
GLYPHSET_DIR = Path("glyphsets")  # under data/
KERNEL = "GF_Latin_Kernel.nam"
CORE = "GF_Latin_Core.nam"
GLYPHSET_COMMIT = "432104e3ecb119c44b3cc29d7209d7b54bf41017"
GLYPHSET_URL = (
    "https://raw.githubusercontent.com/googlefonts/glyphsets/{commit}"
    "/Lib/glyphsets/results/nam/{name}"
)
GLYPHSET_SHA256 = {
    KERNEL: "671a2ae5f835f7d0d9e82b3118bd6f946378821d8b1e50ba320208abbd114329",
    CORE: "26936a4812ff33ee62a2e8156b9976173755909be84138ca4794a33f123b7d18",
}

# The dual-script sheet: gate L2 asks in batches of about 8.
SHEET_ROWS = 32
BATCH_SIZE = 8
SHEET_PATH = Path("review") / "latin-allowlist.md"  # under build/
CACHE_PATH = Path("latin.json")  # under build/cache/
CACHE_VERSION = 1
# Without TFF_STORE the facts cache lives here, shared with stage "facts".
LOCAL_FONTFACTS = Path("fontfacts.jsonl")  # under build/cache/
STAGE = "latin"  # this stage's extract in the font_facts pseudo-source
MAX_READS = 3  # font files read per family before giving up, as stage "facts"
FLUSH_EVERY = 100  # new facts-cache rows kept in memory at most, so a crash loses little

# "Maple Mono NF CN": the CJK build suffix, stripped before the Nerd one (names.NERD_SUFFIX).
CJK_BUILD_SUFFIX = re.compile(r"[\s_-]+CN\Z")

_ROLE_ORDER = {"regular": 0, "variable": 1, "other": 2, "italic": 3}
_MEASURED = ("kernel_missing", "core_missing", "latin_share", "cjk_codepoints")


# --- Google: rule A -------------------------------------------------------------------------


def _script(rec: UniverseRecord) -> str:
    return (rec.primary_script or "").strip()


def rule_a(rec: UniverseRecord) -> bool:
    """Google's strict metadata test: primary script Latin or unset, and a ``latin`` subset."""
    return _script(rec) in ("", LATIN_SCRIPT) and "latin" in rec.subsets


def _gf_coverage(rec: UniverseRecord) -> Literal["basic", "extended"]:
    return "extended" if "latin-ext" in rec.subsets else "basic"


def menu_only(rec: UniverseRecord) -> bool:
    """Whether Google serves the family only with its ``menu`` subset (no text subsets)."""
    return not set(rec.subsets) - {MENU_SUBSET}


def google_result(rec: UniverseRecord) -> LatinResult:
    """The metadata verdict for a Google family, before owner rulings."""
    if rule_a(rec):
        return LatinResult(True, "gf_metadata", _gf_coverage(rec))
    if _script(rec) in CJK_SCRIPTS:
        return LatinResult(False, None, None, "cjk")
    if "latin" not in rec.subsets:
        return LatinResult(False, None, None, "no_latin_subset")
    return LatinResult(False, None, None, "dual_script")


# --- glyphsets and the glyph test ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Glyphsets:
    kernel: frozenset[int]
    core: frozenset[int]
    digest: str  # sha256 over both files: part of the measurement cache key


def load_glyphset(path: Path) -> frozenset[int]:
    """Code points of a GF glyphset ``.nam`` file.

    Each line is ``0xXXXX`` with an optional ``# NAME`` comment; blank lines and
    lines starting with ``#`` are skipped. Anything else raises ``ValueError``
    naming the line, and so does a file without a code point.
    """
    points: set[int] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        text = line.split("#", 1)[0].strip()
        if not text:
            continue
        if not re.fullmatch(r"0x[0-9A-Fa-f]{4,6}", text):
            raise ValueError(f"{path}:{number}: expected 0xXXXX, got {line!r}")
        points.add(int(text, 16))
    if not points:
        raise ValueError(f"{path}: no code points")
    return frozenset(points)


def load_glyphsets(directory: Path) -> Glyphsets:
    """Read ``GF_Latin_Kernel.nam`` and ``GF_Latin_Core.nam`` from ``directory``."""
    digest = hashlib.sha256()
    for name in (KERNEL, CORE):
        digest.update(name.encode() + b"\0" + (directory / name).read_bytes() + b"\0")
    return Glyphsets(
        kernel=load_glyphset(directory / KERNEL),
        core=load_glyphset(directory / CORE),
        digest=digest.hexdigest(),
    )


@functools.cache
def default_glyphsets() -> Glyphsets:
    """The glyphsets vendored in this checkout's ``data/glyphsets/``."""
    return load_glyphsets(find_root(Path(__file__).parent) / "data" / GLYPHSET_DIR)


@dataclass(frozen=True, slots=True)
class Measure:
    """What the glyph test measures in one cmap; ``verdict`` applies the thresholds."""

    kernel_missing: tuple[int, ...]
    core_missing: tuple[int, ...]
    latin_share: float  # rounded to 4 places
    cjk_codepoints: int


def is_cjk(cp: int) -> bool:
    """Whether ``cp`` is in ``CJK_RANGES``."""
    i = bisect.bisect_right(_CJK_STARTS, cp) - 1
    return i >= 0 and cp <= CJK_RANGES[i][1]


def measure(cmap: Iterable[int], gs: Glyphsets) -> Measure:
    """Measure ``cmap`` against the glyphsets (see the module docstring)."""
    cps = frozenset(cp for cp in cmap if 0 <= cp <= 0x10FFFF)
    letters = latin = cjk = 0
    for cp in cps:
        if is_cjk(cp):
            cjk += 1
        ch = chr(cp)
        if unicodedata.category(ch)[0] != "L":
            continue
        script = unicode_script(ch)
        if script in NO_SCRIPT:
            continue
        letters += 1
        if script == LATIN_SCRIPT:
            latin += 1
    return Measure(
        kernel_missing=tuple(sorted(gs.kernel - cps)),
        core_missing=tuple(sorted(gs.core - cps)),
        latin_share=round(latin / letters, 4) if letters else 0.0,
        cjk_codepoints=cjk,
    )


def core_covered(core_missing: Sequence[int], th: Latin) -> bool:
    """Core counts as covered when at most ``core_missing_max`` code points are missing."""
    return len(core_missing) <= th.core_missing_max


def verdict(m: Measure, th: Latin) -> LatinResult:
    """Apply the gate L1 thresholds to a measurement."""
    kept = {name: getattr(m, name) for name in _MEASURED}
    if m.cjk_codepoints >= th.cjk_codepoints_below:
        return LatinResult(False, None, None, "cjk", **kept)
    if len(m.kernel_missing) > th.kernel_missing_max:
        return LatinResult(False, None, None, "kernel_missing", **kept)
    if m.latin_share < th.latin_share_min:
        return LatinResult(False, None, None, "latin_share", **kept)
    coverage = "extended" if core_covered(m.core_missing, th) else "basic"
    return LatinResult(True, "glyph_test", coverage, **kept)


def glyph_test(
    cmap: set[int] | frozenset[int], th: Latin, *, glyphsets: Glyphsets | None = None
) -> LatinResult:
    """Test a font's code points against GF_Latin_Kernel and GF_Latin_Core.

    ``glyphsets`` defaults to the vendored files (``default_glyphsets``).
    """
    return verdict(measure(cmap, glyphsets or default_glyphsets()), th)


def parse_unicode_range(text: str | None) -> frozenset[int] | None:
    """Code points of a CSS-style ``unicode-range`` list (``"U+0020-007E, U+00A0, U+4??"``).

    Returns None for an empty, truncated (``...``) or malformed list, which is
    then not trusted as a font's cmap.
    """
    if not text or "..." in text or "\u2026" in text:
        return None
    points: set[int] = set()
    for part in text.split(","):
        m = re.fullmatch(r"\s*[Uu]\+([0-9A-Fa-f?]{1,6})(?:-([0-9A-Fa-f]{1,6}))?\s*", part)
        if m is None:
            return None
        first, last = m.group(1), m.group(2)
        if "?" in first:
            if last is not None or not re.fullmatch(r"[0-9A-Fa-f]*\?+", first):
                return None
            lo, hi = int(first.replace("?", "0"), 16), int(first.replace("?", "F"), 16)
        else:
            lo = int(first, 16)
            hi = int(last, 16) if last is not None else lo
        if hi < lo or hi > 0x10FFFF:
            return None
        points.update(range(lo, hi + 1))
    return frozenset(points)


def trusted_range(ref: FontFileRef) -> frozenset[int] | None:
    """The ref's ``unicode_range`` as a cmap, when it is complete and matches ``codepoints``."""
    points = parse_unicode_range(ref.unicode_range)
    if points is None or (ref.codepoints is not None and ref.codepoints != len(points)):
        return None
    return points


# --- owner rulings -----------------------------------------------------------------------------


def _id_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{where}: expected a list of family ids")
    bad = [v for v in value if not ID_PATTERN.match(v)]
    if bad:
        raise ValueError(f"{where}: bad family ids {bad}")
    return value


def load_allowlist(paths: Paths) -> dict[str, bool]:
    """The owner's Latin rulings: {family id: True (include) or False (exclude)}.

    Reads ``include`` and ``exclude`` from every table of
    ``data/reviews/latin/*.toml``, and the ``choice`` of each ``L-<family id>``
    table (a include, b exclude, anything else nothing), oldest file first,
    tables in file order; a later ruling overrides an earlier one. An id in both
    lists of one table raises ``ValueError``.
    """
    out: dict[str, bool] = {}
    directory = gate_dir(paths, "L")
    for path in sorted(directory.glob("*.toml")) if directory.is_dir() else ():
        with path.open("rb") as fh:
            doc = tomllib.load(fh)
        for qid, table in doc.items():
            if not isinstance(table, dict):
                continue
            where = f"{path.name} [{qid}]"
            fid = qid.removeprefix(QUESTION_PREFIX)
            if qid.startswith(QUESTION_PREFIX) and ID_PATTERN.match(fid):
                choice = table.get("choice")
                if choice in ("a", "b"):
                    out[fid] = choice == "a"
            include = _id_list(table.get("include", []), f"{where} include")
            exclude = _id_list(table.get("exclude", []), f"{where} exclude")
            both = sorted(set(include) & set(exclude))
            if both:
                raise ValueError(f"{where}: {both} both included and excluded")
            out.update(dict.fromkeys(include, True))
            out.update(dict.fromkeys(exclude, False))
    return out


def apply_ruling(
    res: LatinResult, include: bool, google: UniverseRecord | None, th: Latin
) -> LatinResult:
    """An owner ruling over a family's metadata or glyph verdict."""
    kept = {name: getattr(res, name) for name in _MEASURED}
    if not include:
        return LatinResult(False, None, None, "owner_excluded", **kept)
    if res.latin:
        return res
    if google is not None:
        if "latin" not in google.subsets:
            return res  # nothing Latin to include
        return LatinResult(True, "owner_allowlist", _gf_coverage(google), **kept)
    measured = res.latin_share is not None
    coverage = "extended" if measured and core_covered(res.core_missing, th) else "basic"
    return LatinResult(True, "owner_allowlist", coverage, **kept)


# --- gate L questions --------------------------------------------------------------------------

QUEUE_FILE = "latin.json"  # under build/stage/queues/
QUESTION_PREFIX = "L-"  # a queue question's id: "L-<family id>"
QUESTION_OPTIONS = ("Include it in the catalog", "Keep it out", "Research more")
INCLUDE, KEEP_OUT = 0, 1  # indexes into QUESTION_OPTIONS


def base_families(name: str, family_names: Mapping[str, str]) -> list[str]:
    """Universe families whose name could be ``name``'s base family (the L2 rule's test).

    ``family_names`` maps ``match_key`` of each universe family name to that
    name. A base is a shorter run of ``name``'s leading words, also with
    "Latin" added (Anek Telugu of Anek Latin) or with ``name``'s last word kept
    (Baloo Bhai 2 of Baloo 2). Shortest first; never ``name`` itself.
    """
    words = name.split()
    out: list[str] = []
    for i in range(1, len(words)):
        lead = words[:i]
        tries = [lead, [*lead, "Latin"]]
        if len(words) - i > 1:
            tries.append([*lead, words[-1]])
        for t in tries:
            found = family_names.get(match_key(" ".join(t)))
            if found is not None and match_key(found) != match_key(name) and found not in out:
                out.append(found)
    return out


def l2_advice(
    name: str, latin_ext: bool, family_names: Mapping[str, str]
) -> tuple[int | None, str]:
    """The owner's L2 rule (2026-09-26) on one dual-script candidate: (option, why).

    Basic Latin only: keep it out. Full Latin Extended and no possible base
    family (``base_families``): include it. Otherwise no option: whether the
    extra words name a script or a style is a judgment.
    """
    if not latin_ext:
        return KEEP_OUT, "it has basic Latin only, so the owner's L2 rule keeps it out"
    bases = base_families(name, family_names)
    if not bases:
        return INCLUDE, (
            "it has full Latin Extended and no base family in the universe, so the owner's L2 "
            "rule includes it"
        )
    return None, (
        f"{bases[0]} is in the universe: the owner's L2 rule keeps it out if it is a script "
        "version of that family, and includes it if the extra words name a style"
    )


def queue_questions(
    u: Universe, candidates: Sequence[Candidate], cjk_text: Sequence[str]
) -> list[Question]:
    """Gate L's open rows (module docstring): dual-script candidates, then CJK-subset passes."""
    family_names = {match_key(f.family): f.family for f in u.families.values()}
    out = []
    for c in candidates:
        if c.ruled is not None:
            continue
        fam = u.families.get(c.id)
        name = fam.family if fam else c.id
        rank = f"Google year-views rank {c.views_rank}" if c.views_rank else "no Google views rank"
        langs = "" if c.latin_languages is None else f", {c.latin_languages} Latin languages"
        accents = "latin-ext" if c.latin_ext else "basic Latin only"
        advice, why = l2_advice(name, c.latin_ext, family_names)
        out.append(
            Question(
                "L",
                QUESTION_PREFIX + c.id,
                f"{name} (`{c.id}`): Google's primary script is {c.primary_script}, with a latin "
                f"subset ({accents}{langs}); {rank}. Rule A leaves it out; {why}. Include it?",
                QUESTION_OPTIONS,
                advice,
            )
        )
    for fid in cjk_text:
        fam = u.families.get(fid)
        out.append(
            Question(
                "L",
                QUESTION_PREFIX + fid,
                f"{fam.family if fam else fid} (`{fid}`) passes rule A, but Google lists a CJK "
                "subset and no primary script: it may be mainly CJK. Keep it in the catalog?",
                QUESTION_OPTIONS,
            )
        )
    return out


def questions(paths: Paths) -> list[Question]:
    """Gate L's questions from the last run's queue (for ``reviews.questions``), less the
    families a ruling covers now (``load_allowlist``), so an ``include`` or ``exclude`` list
    recorded after that run stops the asking at once."""
    queued = questions_from_file(paths.queues / QUEUE_FILE)
    ruled = load_allowlist(paths)
    return [q for q in queued if q.id.removeprefix(QUESTION_PREFIX) not in ruled]


# --- Google year views (ranks only: ruling T2) --------------------------------------------------


def competition_ranks(
    values: Mapping[SourceKey, float], *, ascending: bool
) -> dict[SourceKey, int]:
    """1 = best; tied values share a rank ("1224" ranking)."""
    order = sorted(values.items(), key=lambda kv: (kv[1] if ascending else -kv[1], kv[0]))
    ranks: dict[SourceKey, int] = {}
    previous: float | None = None
    rank = 0
    for position, (key, value) in enumerate(order, 1):
        if value != previous:
            rank, previous = position, value
        ranks[key] = rank
    return ranks


def family_ranks(
    obs: Iterable[Observation], index: Mapping[SourceKey, str], *, ascending: bool
) -> dict[str, int]:
    """Each family's best source rank among ``obs`` (values None are skipped)."""
    values = {o.key: o.value for o in obs if o.value is not None}
    out: dict[str, int] = {}
    for key, rank in competition_ranks(values, ascending=ascending).items():
        fid = index.get(key)
        if fid is not None and (fid not in out or rank < out[fid]):
            out[fid] = rank
    return out


def _observations(records_dir: Path, collector: str, series: str) -> list[Observation]:
    path = records_dir / f"{collector}.jsonl"
    if not path.is_file():
        return []
    out = []
    for d in jsonio.iter_jsonl(path):
        if d.get("type") == "Observation" and d.get("series") == series:
            rec = from_json(d)
            assert isinstance(rec, Observation)
            out.append(rec)
    return out


def views_ranks(
    records_dir: Path, source: GoogleSource, index: Mapping[SourceKey, str]
) -> dict[str, int]:
    """Families' Google year-views ranks, from the engine source's series or its fallback.

    The fallback (``"<collector>:<series>"``, Google's popularity order) is
    read only when the main series has no rows; a ``rank`` unit sorts ascending.
    """
    obs = _observations(records_dir, source.collector, source.series)
    if not obs and source.fallback:
        collector, _, series = source.fallback.partition(":")
        obs = _observations(records_dir, collector, series)
    ascending = bool(obs) and obs[0].unit == "rank"
    return family_ranks(obs, index, ascending=ascending)


# --- the dual-script sheet ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """A Google family that fails rule A only on its primary script."""

    id: str
    primary_script: str
    latin_ext: bool
    latin_languages: int | None
    views_rank: int | None
    ruled: bool | None  # None: waiting; True: included; False: excluded


def dual_script_candidates(
    google: Mapping[str, UniverseRecord],
    views_rank: Mapping[str, int],
    allowlist: Mapping[str, bool],
    latin_languages: Mapping[str, int] | None = None,
) -> list[Candidate]:
    """Every dual-script family, best Google views rank first (no rank last, then by id)."""
    langs = latin_languages or {}
    out = []
    for fid, rec in google.items():
        if google_result(rec).reason != "dual_script":
            continue
        out.append(
            Candidate(
                id=fid,
                primary_script=_script(rec),
                latin_ext="latin-ext" in rec.subsets,
                latin_languages=langs.get(fid, rec.latin_languages),
                views_rank=views_rank.get(fid),
                ruled=allowlist.get(fid),
            )
        )
    return sorted(out, key=lambda c: (c.views_rank is None, c.views_rank or 0, c.id))


_NONE = "\N{EN DASH}"  # an empty cell in the sheet


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def allowlist_sheet(
    u: Universe,
    *,
    candidates: Sequence[Candidate] = (),
    notes: Sequence[str] = (),
    rows: int = SHEET_ROWS,
) -> str:
    """The dual-script review sheet for the owner (Markdown), about 30 rows.

    Lists the first ``rows`` candidates still waiting for a ruling, in batches of
    ``BATCH_SIZE``, best Google year-views rank first. Only ranks are shown,
    never view counts (ruling T2). ``notes`` are appended as a list for the lead.
    """
    waiting = [c for c in candidates if c.ruled is None]
    shown = waiting[:rows]
    included = sum(1 for c in candidates if c.ruled)
    excluded = sum(1 for c in candidates if c.ruled is False)
    lines = [
        "# Latin gate: dual-script allowlist (gate L2)",
        "",
        "Google families whose primary script is not Latin but which have a `latin` subset.",
        "Rule A leaves them out; each one here joins the catalog only if you include it.",
        "Rows are sorted by the Google year-views rank (ranks only, ruling T2).",
        "",
        f"Candidates: {len(candidates)}. Already ruled: {included} included, {excluded} excluded. "
        + (
            f"Waiting: {len(waiting)}; the first {len(shown)} are below."
            if shown
            else "Nothing is waiting for a ruling."
        ),
    ]
    if shown:
        lines += [
            "",
            "Answer each batch with one choice: (a) include all, (b) exclude all, or (c) decide "
            "one by one. The answer is recorded in `data/reviews/latin/<date>.toml` as `include` "
            "and `exclude` lists of the ids below.",
        ]
    for start in range(0, len(shown), BATCH_SIZE):
        batch = shown[start : start + BATCH_SIZE]
        lines += [
            "",
            f"## Batch {start // BATCH_SIZE + 1}",
            "",
            "| Views rank | Family | id | Primary script | Latin languages | Accents |",
            "|---:|---|---|---|---:|---|",
        ]
        for c in batch:
            fam = u.families.get(c.id)
            name = fam.family if fam is not None else c.id
            rank = _NONE if c.views_rank is None else str(c.views_rank)
            langs = _NONE if c.latin_languages is None else str(c.latin_languages)
            accents = "latin-ext" if c.latin_ext else "basic only"
            lines.append(
                f"| {rank} | {_cell(name)} | `{c.id}` | {_cell(c.primary_script)} | {langs} "
                f"| {accents} |"
            )
    if notes:
        lines += ["", "## Notes for the lead", ""] + [f"- {_cell(n)}" for n in notes]
    return "\n".join(lines) + "\n"


# --- deciding every family -----------------------------------------------------------------------


CmapFor = Callable[[FontFileRef], frozenset[int] | None]


NAME_CLASS = "name"  # every aliases.NAME_NAMESPACES namespace: a name means one family in all


@functools.cache
def _name_namespaces() -> frozenset[str]:
    # Imported here: the aliases package is heavier than this stage needs at import time.
    from tff_catalog.aliases import NAME_NAMESPACES

    return NAME_NAMESPACES


def _ns_class(ns: str) -> str:
    return NAME_CLASS if ns in _name_namespaces() else ns


def cjk_build_keys(rows: Iterable[AliasRow]) -> frozenset[tuple[str, str]]:
    """(namespace class, match_key) of every alias row with relation ``build`` and detail ``cjk``.

    Name namespaces share the class ``NAME_CLASS``, as the universe reads them.
    """
    return frozenset(
        (_ns_class(r.ns), match_key(r.alias))
        for r in rows
        if r.relation == "build" and r.detail == "cjk"
    )


def is_cjk_build(r: UniverseRecord, cjk_keys: frozenset[tuple[str, str]]) -> bool:
    """Whether a CJK build row names ``r``'s key, or (in a name namespace) its family name."""
    by_key = (_ns_class(r.key.ns), match_key(r.key.key))
    by_name = (NAME_CLASS, match_key(r.family))
    return by_key in cjk_keys or by_name in cjk_keys


def family_records(
    fam: Family, records: Mapping[SourceKey, Sequence[UniverseRecord]]
) -> list[UniverseRecord]:
    """Every record of ``fam``'s universe keys, and its records of several-families keys."""
    own = [r for k in fam.keys for r in records.get(k, ())]
    return own + [r for k, name in fam.shared for r in records.get(k, ()) if r.family == name]


def google_record(recs: Iterable[UniverseRecord]) -> UniverseRecord | None:
    """The record whose metadata decides a Google family (``GOOGLE_SOURCES`` order, live first).

    The live list is the source of truth (design-m1 C12): a google/fonts repo folder
    makes a Google family only when it is queued for the live list. Other repo folders
    the live list lacks (the Big Shoulders split families, Batang, the Noto UI families)
    take the glyph test like any other font.
    """
    google = [
        r
        for r in recs
        if r.source == GOOGLE_SOURCES[0] or (r.source in GOOGLE_SOURCES and r.status == "queued")
    ]
    if not google:
        return None
    return min(google, key=lambda r: (GOOGLE_SOURCES.index(r.source), r.status != "live", r.key))


def candidate_files(
    recs: Iterable[UniverseRecord],
    cjk_keys: frozenset[tuple[str, str]] = frozenset(),
    *,
    family: str | None = None,
) -> list[FontFileRef]:
    """A family's font files in test order (module docstring), leaving out its CJK builds' files.

    ``family`` is the family's display name: files of records named like it come first.
    """
    own = match_key(family) if family is not None else None
    best: dict[FontFileRef, tuple[bool, int, bool, bool, str]] = {}
    for r in recs:
        if is_cjk_build(r, cjk_keys):
            continue
        foreign = own is not None and match_key(r.family) != own
        for f in r.files:
            order = (
                foreign,
                _ROLE_ORDER.get(f.role, len(_ROLE_ORDER)),
                trusted_range(f) is None,
                f.sha256 is None,
                f.url,
            )
            if f not in best or order < best[f]:
                best[f] = order
    return sorted(best, key=lambda f: (best[f], f.sha256 or ""))


def _measure_family(
    refs: Sequence[FontFileRef],
    cmap_for: CmapFor,
    gs: Glyphsets,
    cache: dict[str, Measure],
) -> Measure | None:
    reads = 0
    for ref in refs:
        if ref.sha256 and ref.sha256 in cache:
            return cache[ref.sha256]
        cmap = trusted_range(ref)
        if cmap is None:
            # Pages and whole archives are never fetched: a release zip can be hundreds of
            # MB, so only a zip member reference (read by range) gets into an archive.
            if reads >= MAX_READS or not fontfiles.is_readable_url(ref.url):
                continue
            reads += 1
            cmap = cmap_for(ref)
        if cmap is None:
            continue
        m = measure(cmap, gs)
        if ref.sha256:
            cache[ref.sha256] = m
        return m
    return None


def decide(
    u: Universe,
    records: Mapping[SourceKey, Sequence[UniverseRecord]],
    th: Latin,
    *,
    allowlist: Mapping[str, bool] | None = None,
    cjk_keys: frozenset[tuple[str, str]] = frozenset(),
    cmap_for: CmapFor = lambda ref: None,
    glyphsets: Glyphsets | None = None,
    cache: dict[str, Measure] | None = None,
    hand_files: Mapping[str, Sequence[FontFileRef]] | None = None,
) -> dict[str, LatinResult]:
    """The Latin verdict for every eligible family (module docstring), sorted by id.

    ``records`` maps each universe key to its records; ``cmap_for`` reads a
    file's code points (None when it cannot); ``cache`` maps file sha256 to a
    measurement and is filled as files are measured. ``hand_files`` holds the
    files of ``config/font-files.toml`` by family id, tried before the records'
    (not for a Google family judged on its metadata).
    """
    gs = glyphsets or default_glyphsets()
    rulings = allowlist or {}
    memo = cache if cache is not None else {}
    hand = hand_files or {}
    out: dict[str, LatinResult] = {}
    for fid in sorted(u.families):
        fam = u.families[fid]
        if fam.drop is not None:
            continue
        recs = family_records(fam, records)
        google = google_record(recs)
        if google is not None and menu_only(google):
            google = None  # judged by its files (MENU_SUBSET)
        if google is not None:
            res = google_result(google)
        else:
            refs = fontfiles.with_hand_files(
                candidate_files(recs, cjk_keys, family=fam.family), hand.get(fid)
            )
            m = _measure_family(refs, cmap_for, gs, memo)
            res = LatinResult(False, None, None, "no_file") if m is None else verdict(m, th)
        if fid in rulings:
            res = apply_ruling(res, rulings[fid], google, th)
        out[fid] = res
    return out


def cjk_build_suggestions(u: Universe, results: Mapping[str, LatinResult]) -> list[tuple[str, str]]:
    """(family id, parent id) for families out as ``cjk`` whose name looks like a CJK build.

    The name without ``CJK_BUILD_SUFFIX`` and then ``NERD_SUFFIX`` must have the
    same ``match_key`` as exactly one family that passed. Only a suggestion for
    an alias row: nothing is folded here.
    """
    passed: dict[str, list[str]] = {}
    for fid, res in results.items():
        if res.latin:
            passed.setdefault(match_key(u.families[fid].family), []).append(fid)
    out = []
    for fid, res in sorted(results.items()):
        name = u.families[fid].family
        if res.reason != "cjk" or not CJK_BUILD_SUFFIX.search(name):
            continue
        base = CJK_BUILD_SUFFIX.sub("", name)
        nerd = NERD_SUFFIX.search(base)
        if nerd is not None:
            base = base[: nerd.start()]
        parents = passed.get(match_key(base), [])
        if len(parents) == 1:
            out.append((fid, parents[0]))
    return out


# --- the stage ------------------------------------------------------------------------------------


def universe_records(records_dir: Path) -> dict[SourceKey, list[UniverseRecord]]:
    """Every ``UniverseRecord`` in ``build/stage/records/*.jsonl`` (baselines skipped), by key."""
    out: dict[SourceKey, list[UniverseRecord]] = {}
    for path in sorted(records_dir.glob("*.jsonl")) if records_dir.is_dir() else ():
        if "@" in path.stem:
            continue
        for d in jsonio.iter_jsonl(path):
            if d.get("type") != "UniverseRecord":
                continue
            rec = from_json(d)
            assert isinstance(rec, UniverseRecord)
            out.setdefault(rec.key, []).append(rec)
    return out


def key_index(u: Universe) -> dict[SourceKey, str]:
    """Universe key -> family id, for every family."""
    return {k: fid for fid, fam in u.families.items() for k in fam.keys}


def load_cache(path: Path, gs: Glyphsets) -> dict[str, Measure]:
    """Measurements by file sha256; empty when the file is missing or was made differently."""
    if not path.is_file():
        return {}
    try:
        doc = jsonio.load(path)
        if doc.get("key") != _cache_key(gs):
            return {}
        return {sha: stageio.decode(Measure, m, f"$.{sha}") for sha, m in doc["measures"].items()}
    except ValueError, KeyError, TypeError, AttributeError:
        return {}  # a damaged cache is rebuilt


def dump_cache(path: Path, gs: Glyphsets, cache: Mapping[str, Measure]) -> None:
    jsonio.dump({"key": _cache_key(gs), "measures": stageio.encode(dict(cache))}, path)


def _cache_key(gs: Glyphsets) -> dict[str, Any]:
    # Categories come from unicodedata and scripts from fontTools' tables.
    return {
        "version": CACHE_VERSION,
        "glyphsets": gs.digest,
        "unicode": unicodedata.unidata_version,
        "fonttools": fontTools.version,
    }


@dataclass(slots=True)
class FileCmaps:
    """``cmap_for`` for the stage: a font file's code points through ``fontfiles``.

    With a fetcher (live): ``fontfiles.facts_for`` reads the file by range, or
    finds it in the facts cache; every read, failures included, goes to
    ``reads`` for the ``font_facts`` pseudo-source, and a url that failed once
    is not tried again this run; nor is any other member of a zip archive whose
    request failed (a dead host would cost each member its retries), unless the
    cache knows it. Without one (replay): ``recorded``, the day's
    logged reads, answers first (a logged failure fails again, although the
    cache may know the file by now), then the facts cache; nothing is fetched.
    """

    cache: FontFileCache
    fetcher: Fetcher | None
    log: logging.Logger
    recorded: Mapping[str, FileRead] = field(default_factory=dict)
    reads: ReadLog = field(default_factory=ReadLog)
    read: int = 0
    failed: int = 0
    dead: set[str] = field(default_factory=set)  # zip archives whose request failed this run

    def __call__(self, ref: FontFileRef) -> frozenset[int] | None:
        facts = self._facts(ref)
        if facts is None:
            self.failed += 1
            return None
        self.read += 1
        return facts.codepoints()

    def _facts(self, ref: FontFileRef) -> FontFacts | None:
        if self.fetcher is None:
            logged = self.recorded.get(ref.url)
            if logged is not None:
                return None if logged.sha256 is None else self.cache.get(sha256=logged.sha256)
            return fontfiles.cached_facts(ref, self.cache)
        from tff_catalog.fetch import FetchError, HostNotAllowed

        earlier = self.reads.reads.get(ref.url)
        if earlier is not None and earlier.sha256 is None:
            return None
        archive = (fontfiles.split_member(ref.url) or (None, None))[0]
        if archive in self.dead and fontfiles.cached_facts(ref, self.cache) is None:
            self.reads.add(FileRead(ref.url, None, f"{archive} failed earlier this run"))
            return None
        try:
            facts = fontfiles.facts_for(ref, self.fetcher, self.cache)
        except (FetchError, HostNotAllowed, OSError, ValueError) as exc:
            # ValueError covers fontfiles.FontFileError: not a font, truncated, wrong hash.
            self.log.warning("latin: cannot read %s: %s", ref.url, exc)
            self.reads.add(FileRead(ref.url, None, str(exc)[:300]))
            if archive is not None and isinstance(exc, FetchError | HostNotAllowed):
                self.dead.add(archive)
            return None
        self.reads.add(FileRead(ref.url, facts.sha256))
        if self.cache.pending >= FLUSH_EVERY:
            self.cache.flush()
        return facts


def _open_facts_cache(ctx: StageContext) -> FontFileCache:
    """The store's font facts cache, or ``build/cache/fontfacts.jsonl`` without TFF_STORE."""
    if ctx.store is not None:
        return FontFileCache(fontfiles.cache_path(ctx.store.root))
    ctx.log.warning("latin: TFF_STORE is not set: font facts are cached in build/cache only")
    return FontFileCache(ctx.paths.cache / LOCAL_FONTFACTS)


def _record_reads(ctx: StageContext, cmaps: FileCmaps) -> None:
    if ctx.fetcher is None or ctx.store is None or not cmaps.reads.reads:
        return
    try:
        fontfiles.record_reads(
            ctx.store,
            ctx.run_date,
            STAGE,
            cmaps.reads.reads.values(),
            frozen=ctx.state.frozen_dates(fontfiles.READS_SOURCE),
        )
    except Exception as exc:  # a missing log costs replay fidelity, not this run's verdicts
        ctx.log.warning("latin: could not record font reads in the store: %s", exc)


def _latin_languages(
    families: Iterable[str],
    u: Universe,
    records: Mapping[SourceKey, Sequence[UniverseRecord]],
) -> dict[str, int]:
    """A family's Latin-language count: the largest any of its records gives.

    The largest, not the first: Google's live list leaves ``languages`` empty
    (2026-09-25), so its count says less than the repo's or Fontsource's.
    """
    out = {}
    for fid in families:
        counts = [
            r.latin_languages
            for r in family_records(u.families[fid], records)
            if r.latin_languages is not None
        ]
        if counts:
            out[fid] = max(counts)
    return out


def _summary(results: Mapping[str, LatinResult]) -> list[str]:
    passed = Counter(f"{r.basis}/{r.coverage}" for r in results.values() if r.latin)
    failed = Counter(r.reason for r in results.values() if not r.latin)
    return [
        f"Passed {sum(passed.values())}: "
        + ", ".join(f"{k} {n}" for k, n in sorted(passed.items())),
        f"Out {sum(failed.values())}: " + ", ".join(f"{k} {n}" for k, n in sorted(failed.items())),
    ]


def run(ctx: StageContext) -> None:
    """Stage "latin"."""
    paths, th, log = ctx.paths, ctx.config.ranking.latin, ctx.log
    u: Universe = stageio.load_stage(paths, "universe")
    records = universe_records(paths.records)
    allowlist = load_allowlist(paths)
    unknown = sorted(set(allowlist) - set(u.families))
    if unknown:
        log.warning("latin: rulings name families not in the universe: %s", ", ".join(unknown))
    gs = load_glyphsets(paths.data / GLYPHSET_DIR)
    cjk_keys = cjk_build_keys(_alias_rows(paths))
    hand = hand_files(u, paths.config, log)
    replay = ctx.options.replay
    # A replay starts from no measurements, so its file choices are the live run's.
    cache = {} if replay else load_cache(paths.cache / CACHE_PATH, gs)
    recorded: Mapping[str, FileRead] = {}
    if ctx.fetcher is None and ctx.store is not None:
        day = ctx.options.from_snapshots or ctx.run_date
        recorded = fontfiles.recorded_reads(ctx.store, day, prefer=STAGE)
    cmaps = FileCmaps(_open_facts_cache(ctx), ctx.fetcher, log, recorded)
    try:
        results = decide(
            u,
            records,
            th,
            allowlist=allowlist,
            cjk_keys=cjk_keys,
            cmap_for=cmaps,
            glyphsets=gs,
            cache=cache,
            hand_files=hand,
        )
    finally:
        cmaps.cache.flush()
    _record_reads(ctx, cmaps)
    stageio.dump_stage(paths, "latin", results)
    if not replay:
        dump_cache(paths.cache / CACHE_PATH, gs, cache)

    google = _google_records(u, records)
    ignored = sorted(f for f in set(hand) & set(google) if not menu_only(google[f]))
    if ignored:
        log.warning(
            "latin: %s names Google families, which their metadata decides: %s",
            fontfiles.FONT_FILES,
            ", ".join(ignored),
        )
    ranks = views_ranks(paths.records, ctx.config.ranking.sources.google, key_index(u))
    candidates = dual_script_candidates(
        google, ranks, allowlist, _latin_languages(google, u, records)
    )
    notes = _notes(u, results, google, records, allowlist)
    sheet = allowlist_sheet(u, candidates=candidates, notes=notes)
    jsonio.atomic_write(paths.build / SHEET_PATH, sheet.encode("utf-8"))
    asked = queue_questions(u, candidates, _cjk_text(results, google, allowlist))
    jsonio.dump([dataclasses.asdict(q) for q in asked], paths.queues / QUEUE_FILE)
    for line in notes:
        log.info("latin: %s", line)
    log.info("latin: %d font files read, %d unreadable", cmaps.read, cmaps.failed)


def _alias_rows(paths: Paths) -> list[AliasRow]:
    from tff_catalog.aliases import load_aliases

    return load_aliases(paths.aliases_csv) if paths.aliases_csv.is_file() else []


def hand_files(
    u: Universe, config_dir: Path, log: logging.Logger | None = None
) -> dict[str, tuple[FontFileRef, ...]]:
    """``config/font-files.toml`` (``fontfiles.load_font_files``), checked against the universe.

    Raises ``config_model.ConfigError`` for a family id the universe lacks: a typo,
    or a family renamed or merged away. A dropped family's entry changes nothing and is
    logged. (Nor does a Google family's, unless Google serves only its menu subset: its
    metadata decides it; ``run`` logs those.)
    """
    from tff_catalog.config_model import ConfigError

    hand = fontfiles.load_font_files(config_dir)
    unknown = sorted(set(hand) - set(u.families))
    if unknown:
        raise ConfigError(
            f"{fontfiles.FONT_FILES}: families {', '.join(unknown)}: no family of this run's "
            "universe has that id (a typo, or a family renamed or merged away); fix or remove "
            "the entry"
        )
    dropped = sorted(fid for fid in hand if u.families[fid].drop is not None)
    if dropped and log is not None:
        log.warning(
            "latin: %s names families the universe drops: %s",
            fontfiles.FONT_FILES,
            ", ".join(dropped),
        )
    return hand


def _google_records(
    u: Universe, records: Mapping[SourceKey, Sequence[UniverseRecord]]
) -> dict[str, UniverseRecord]:
    out = {}
    for fid, fam in sorted(u.families.items()):
        if fam.drop is None:
            rec = google_record(family_records(fam, records))
            if rec is not None:
                out[fid] = rec
    return out


def _cjk_text(
    results: Mapping[str, LatinResult],
    google: Mapping[str, UniverseRecord],
    allowlist: Mapping[str, bool] | None = None,
) -> list[str]:
    """Google families rule A passes although they carry a CJK subset (and no ruling yet,
    when ``allowlist`` is given)."""
    return [
        fid
        for fid, rec in sorted(google.items())
        if results[fid].basis == "gf_metadata"
        and CJK_SUBSETS & set(rec.subsets)
        and (allowlist is None or fid not in allowlist)
    ]


def _names(u: Universe, fids: Iterable[str], limit: int = 40) -> str:
    names = [u.families[f].family for f in fids]
    more = f", and {len(names) - limit} more" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more


def _notes(
    u: Universe,
    results: Mapping[str, LatinResult],
    google: Mapping[str, UniverseRecord],
    records: Mapping[SourceKey, Sequence[UniverseRecord]],
    allowlist: Mapping[str, bool] | None = None,
) -> list[str]:
    live = [r for recs in records.values() for r in recs if r.source == GOOGLE_SOURCES[0]]
    notes = _summary(results)
    notes.append(
        f"Rule A: {sum(map(rule_a, live))} of the {len(live)} records on Google's live list "
        f"(the pinned snapshot); {sum(map(rule_a, google.values()))} of {len(google)} eligible "
        "Google families."
    )
    cjk_text = _cjk_text(results, google)
    if cjk_text:
        notes.append(
            f"Rule A passes {len(cjk_text)} with a CJK subset and no primary script (mainly CJK? "
            f"an exclude ruling takes them out): {_names(u, cjk_text)}"
        )
    stuck = sorted(fid for fid, include in (allowlist or {}).items() if include and fid in results)
    stuck = [fid for fid in stuck if not results[fid].latin]
    if stuck:
        notes.append(
            f"Included by a ruling but still out, having no latin subset: {_names(u, stuck)}"
        )
    no_file = [f for f, r in results.items() if r.reason == "no_file"]
    if no_file:
        notes.append(f"No file to test ({len(no_file)}): {_names(u, no_file)}")
    notes += [
        f"Possible CJK build: {u.families[b].family} (`{b}`) of `{p}`; add an alias row "
        "(relation build, detail cjk) if right."
        for b, p in cjk_build_suggestions(u, results)
    ]
    return notes
