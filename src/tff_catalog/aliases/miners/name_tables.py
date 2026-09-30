"""Alias miner "name_tables": the names upstream font files give themselves (milestone-1 step 7).

A font file names its family in its ``name`` table: ID 1 (the legacy family,
four styles at most), ID 16 (the typographic family, when ID 1 is not it) and
ID 6 (the PostScript name). Those names reach the name-keyed sources: the
Almanac reads ID 16, else ID 1; Fonts Over Time and owned-font matching
(milestone 3) see what sites and systems call a font. So an upstream name that is not its family's own needs an alias row:
"Inter Variable" and "Inter Display" are Inter.

**Evidence.** Upstream files pinned by hash or commit, in two kinds:

1. *Fontist formulas*: the newest ``fontist`` snapshot on or before the run
   date (extract ``formulas.jsonl.gz``, see ``collectors.universe.fontist``).
   Fontist lists every font file of a sha256-pinned upstream archive with its
   ID 1 (``family_name``), ID 16 (``preferred_family_name``) and ID 6
   (``post_script_name``). One formula is one piece of evidence. No request.
   Formulas behind a license agreement with no open license are skipped
   (``SKIP_PROPRIETARY``): the fontist collector drops them as proprietary.
   So are formulas none of whose resources gives a sha256 (not pinned).
2. *The universe's pinned font files*: ``build/stage/records/*.jsonl`` (stage
   "parse" first). Per ``UniverseRecord``, the font files whose URL is pinned (a
   sha256, or a 40-hex commit in the path), leaving out google/fonts builds
   (``SKIP_PREFIXES``: Google's onboarding checks make their names the Google
   family's) and Fontist's records (kind 1 reads all their files). Of those,
   the first ``regular`` and the first ``variable`` file by URL, or the first
   file when there is neither (``chosen_files``). Only the ``name`` table is
   read, by HTTP Range (``fontfiles.read_font``): about two requests a file, at
   one a second per host. The record's family and key anchor the file.

**Candidates.** Every alias is a ``font-name`` key (it holds in every name
namespace, ``aliases.NAME_NAMESPACES``), every candidate is ``auto`` false (the
owner reviews them all, milestone-1 step 7) and names are compared by
``match_key``. Only these fixed rules make a row; nothing is matched by prefix
or likeness, and no name is paired through googlefontdirectory-hg:

- *build*: a typographic name (ID 16, else ID 1) that is a base name followed
  by build words only (``build_detail``: ``BUILD_WORDS``, the abbreviations in
  ``CASED_WORDS`` as written, ``OPSZ_WORDS`` and point sizes such as "16pt").
  The base is another typographic name of the same formula (the one with
  fewest words), or the record's family for kind 2. Detail: the first of the
  words' details in ``DETAIL_ORDER``. Examples: "Inter Variable" (variable) and
  "Inter Display" (opsz) in the Inter formula, "JetBrains Mono NL" (nl),
  "Cascadia Code PL" (powerline), "Maple Mono NF CN" (cjk). "Inter Tight",
  "Noto Sans JP", "IBM Plex Mono" and "TeX Gyre Heros Cn" give nothing. When
  the longer name is a family of its own (a file set holding both "Playfair"
  and "Playfair Display"), the stage queues the row as "other-family".
- *build, legacy*: an ID 1 name, without its trailing style words
  (``names.strip_style`` and ``STYLE_ABBREVIATIONS``), that extends its file's
  ID 16 name by whole words: "Fira Code Retina" (ID 16 "Fira Code"), "Noto
  Sans Condensed" (from "Noto Sans Condensed Black"), "Martian Mono Cn" (from
  "Martian Mono Cn Lt"). "Inter Display SemiBold" gives nothing, and so do an
  ID 1 name that is the ID 16 name with style words anywhere ("Joongnajoche
  Light OTF") and one that does not begin with it ("NSimSun", ID 16 "SimSun":
  another font of the file). Detail (``legacy_detail``): the build detail when
  the name is the family's plus build words ("Aptos Display": opsz), else the
  ID 16 name's, else static.
- *postscript*: the family part of an ID 6 name "<family>-<style>" whose style
  parts ``names.strip_style`` removes ("3270" from "3270-Regular",
  "Newsreader16pt"; nothing from "ArialMT"), when it is none of the
  evidence's family names and the file's ID 1 name begins with its ID 16 name.
  ID 25 (a variable font's PostScript prefix) is not read: forks keep their
  parent's (Adwaita Sans, built from Inter, says "InterVariable"), and it names
  nothing ID 6 does not.

A name that one piece of evidence gives to two families ("FranklinGothic" in
a formula of Franklin Gothic Book and Franklin Gothic Demi) is left out.

The target is the root family: ``font-name:<base>`` for kind 1 (the formula's
name with no base of its own), the record's key for kind 2 (its family name as
a ``font-name`` key when that key is a Fontist formula). Per alias and target
one candidate is kept: build before postscript, then the higher detail, then
the smallest spelling and evidence. ``evidence`` is the pinned file:
``<resource url>#<font file>`` for kind 1 (the resource that is that file, else
the formula's first, its archive), the file's URL for kind 2.

**Left out** (logged): a kind-2 file whose typographic name is neither its
record's family nor that family plus build words ("Noto Sans Mono" in the Nerd
Fonts folder "Noto"), so none of its names is taken; a file that is not a
readable font, or whose size differs from the record's. Up to ``max_failures``
files may fail to read (network errors included); more make the miner fail,
so the stage keeps the committed ``data/alias-seeds/name_tables.csv`` rather
than a partial one. So does a missing Fontist snapshot or records directory.

The stage writes the candidates to ``data/alias-seeds/name_tables.csv``
(``aliases.write_seeds``); this miner never writes ``data/aliases.csv``.
"""

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlsplit

from tff_catalog.aliases import AliasCandidate
from tff_catalog.aliases.miners import MineContext
from tff_catalog.keys import match_key
from tff_catalog.names import strip_style
from tff_catalog.paths import StoreNotConfigured
from tff_catalog.records import FontFileRef, SourceKey, UniverseRecord

if TYPE_CHECKING:
    import httpx

    from tff_catalog.fetch import Fetcher
    from tff_catalog.store import Snapshot

NAME = "name_tables"
NAME_NS = "font-name"
AUTO = False  # milestone-1 step 7: only gf_history and nerd rows are auto-accepted
FONTIST = "fontist"  # the store source of kind 1 (collectors/universe/fontist.py)
FORMULAS_EXTRACT = "formulas.jsonl.gz"  # that collector's extract
FONTIST_NS = "fontist-formula"  # its records' keys; one key can hold several families
SKIP_SOURCES = frozenset({FONTIST})  # kind 1 reads every name Fontist recorded
SKIP_PROPRIETARY = True  # formulas behind a license agreement only (``is_proprietary``)
# google/fonts builds carry the Google family's names (fontbakery checks them on onboarding).
SKIP_PREFIXES = (
    "https://raw.githubusercontent.com/google/fonts/",
    "https://github.com/google/fonts/",
)
# Where GitHub sends release-asset and raw downloads; added to the fetcher's hosts.
REDIRECT_HOSTS = frozenset(
    {"objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)
FILE_ROLES = ("regular", "variable")  # the files read per record, first of each by URL
MAX_FAILURES = 5  # more failed reads make the miner fail (the committed seeds stay)
MIN_INTERVAL = 1.0  # seconds between requests to one host

# Words that make a name a build of its base, with the alias detail they give
# (aliases.BUILD_DETAILS). Multi-word entries match whole; compared casefolded.
BUILD_WORDS: Mapping[str, str] = {
    "variable": "variable",
    "var": "variable",
    "static": "static",
    "powerline": "powerline",
    "for powerline": "powerline",
    "nerd font": "nerd",
    "nerd font mono": "nfm",
    "nerd font propo": "propo",
    "ligature": "",
    "ligatures": "",
}
# Abbreviations, compared as written: "TeX Gyre Heros Cn" is condensed, not a CJK build.
CASED_WORDS: Mapping[str, str] = {
    "VF": "variable",
    "NL": "nl",
    "PL": "powerline",
    "NF": "nf",
    "NFM": "nfm",
    "NFP": "nfp",
    "CN": "cjk",
}
# Optical-size splits the parent covers (methodology §2, "Inter Display"), plus "<n>pt".
OPSZ_WORDS = frozenset(
    {"display", "text", "micro", "caption", "subhead", "deck", "headline", "poster", "banner"}
)
OPSZ = "opsz"
STATIC = "static"
# Style words in ID 1 names that ``names.strip_style`` keeps: abbreviations and
# spellings seen in real files ("Martian Mono Cn Lt", "Kinto Sans Med",
# "Selawik Semilight", "Montserrat Alternates ExLight"). Compared casefolded.
STYLE_ABBREVIATIONS = frozenset(
    {
        "th",
        "xlt",
        "lt",
        "rg",
        "med",
        "md",
        "sb",
        "smbd",
        "bd",
        "xbd",
        "blk",
        "hv",
        "it",
        "semilight",
        "exlight",
        "exbold",
    }
)
_POINT_SIZE = re.compile(r"\d+(?:\.\d+)?pt")
# When a name carries several build words, the first of these present is the detail.
DETAIL_ORDER = (
    "cjk",
    "nerd",
    "nfm",
    "nfp",
    "propo",
    "nf",
    "powerline",
    "nl",
    OPSZ,
    "variable",
    STATIC,
    "",
)
_RELATION_ORDER = ("build", "postscript")
_WORD_SPLIT = re.compile(r"[\s_-]+")
_COMMIT = re.compile(r"/[0-9a-f]{40}(?:/|$)")
_MAX_PHRASE = max(len(p.split()) for p in BUILD_WORDS)


# --- names --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FontNames:
    """The names one font file's ``name`` table gives (None when it lacks one)."""

    family: str | None  # ID 1
    typographic: str | None = None  # ID 16
    postscript: str | None = None  # ID 6

    @property
    def typo(self) -> str | None:
        """The typographic family: ID 16, else ID 1."""
        return self.typographic or self.family


@dataclass(frozen=True, slots=True)
class NamedFile:
    """One font file's names and the evidence that shows them."""

    names: FontNames
    evidence: str


def clean(value: object) -> str | None:
    """A name with NULs dropped and white space collapsed; None when empty or not a string."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("\x00", "").split())
    return text or None


def _words(name: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split(name) if w]


def _extends(words: Sequence[str], stem: Sequence[str]) -> bool:
    """Whether ``words`` begin with every word of a non-empty ``stem`` (by ``match_key``)."""
    head = [match_key(w) for w in words[: len(stem)]]
    return bool(stem) and head == [match_key(w) for w in stem]


def _word_detail(words: Sequence[str]) -> list[str] | None:
    """The details of ``words`` read as build words left to right; None when one is not."""
    out: list[str] = []
    i = 0
    folded = [w.casefold() for w in words]
    while i < len(folded):
        for n in range(min(_MAX_PHRASE, len(folded) - i), 0, -1):
            phrase = " ".join(folded[i : i + n])
            if phrase in BUILD_WORDS:
                out.append(BUILD_WORDS[phrase])
                i += n
                break
        else:
            if words[i] in CASED_WORDS:
                out.append(CASED_WORDS[words[i]])
            elif folded[i] in OPSZ_WORDS or _POINT_SIZE.fullmatch(folded[i]):
                out.append(OPSZ)
            else:
                return None
            i += 1
    return out


def build_detail(name: str, base: str) -> str | None:
    """The alias detail when ``name`` is ``base`` followed by build words only, else None.

    Words split at spaces, hyphens and underscores and compare by ``match_key``:
    ``build_detail("Inter Display", "Inter") == "opsz"``,
    ``build_detail("Maple Mono NF CN", "Maple Mono") == "cjk"``,
    ``build_detail("Inter Tight", "Inter") is None``.
    """
    words, stem = _words(name), _words(base)
    if len(words) <= len(stem) or not _extends(words, stem):
        return None
    details = _word_detail(words[len(stem) :])
    if details is None:
        return None
    return min(details, key=DETAIL_ORDER.index)


def _is_style_word(word: str) -> bool:
    return word.casefold() in STYLE_ABBREVIATIONS or strip_style(f"x {word}") == "x"


def strip_styles(name: str) -> str:
    """``names.strip_style`` that also drops trailing ``STYLE_ABBREVIATIONS``, keeping one word.

    "Martian Mono Cn Lt" -> "Martian Mono Cn"; "Inter Bold Italic" -> "Inter".
    """
    text = " ".join(name.split())
    while True:
        words = strip_style(text).split(" ")
        if len(words) > 1 and words[-1].casefold() in STYLE_ABBREVIATIONS:
            words.pop()
        shorter = " ".join(words)
        if shorter == text:
            return text
        text = shorter


def _names_agree(names: FontNames) -> bool:
    """Whether the ID 1 name is the ID 16 name or begins with its words (or one is missing).

    "TeXGyreHerosCn" is "TeX Gyre Heros Cn" (by ``match_key``); "Martian Mono Cn
    Lt" begins with "Martian Mono"; "NSimSun" does not begin with "SimSun".
    """
    legacy, typo = names.family, names.typographic
    if legacy is None or typo is None or match_key(legacy) == match_key(typo):
        return True
    return _extends(_words(legacy), _words(typo))


def legacy_alias(names: FontNames) -> str | None:
    """The ID 1 name without its style words, when it extends the ID 16 name; else None.

    "Fira Code Retina" (ID 16 "Fira Code") stays; "Inter Display SemiBold" (ID
    16 "Inter Display") gives None; "Noto Sans Condensed Black" gives "Noto
    Sans Condensed"; "Martian Mono Cn Lt" gives "Martian Mono Cn". A name that
    is the ID 16 name with style words anywhere ("Joongnajoche Light OTF", ID
    16 "Joongnajoche OTF") gives None, and so does one that does not begin with
    the ID 16 name: "NSimSun" (ID 16 "SimSun") is another font of the file, and
    a fork can keep its parent's ID 1. Hyphens and underscores count as spaces.
    """
    legacy, typo = names.family, names.typographic
    if legacy is None or typo is None or match_key(legacy) == match_key(typo):
        return None
    if not _names_agree(names):
        return None
    words = _words(legacy)
    if match_key(" ".join(w for w in words if not _is_style_word(w))) == match_key(typo):
        return None
    unstyled = strip_styles(" ".join(words))
    return None if match_key(unstyled) == match_key(typo) else unstyled


def legacy_detail(legacy: str, base: str, typo_detail: str | None) -> str:
    """The detail of a legacy (ID 1) alias of the family ``base``.

    Its build detail when it is ``base`` plus build words ("Aptos Display" of
    Aptos: opsz); else its typographic name's build detail ("Maple Mono NF
    Condensed" of ID 16 "Maple Mono NF": nf), else static. The first of those
    in ``DETAIL_ORDER`` wins, so a ligature build's static cut is static.
    """
    found = [d for d in (build_detail(legacy, base), typo_detail) if d is not None]
    return min([*found, STATIC], key=DETAIL_ORDER.index)


def _postscript_family(ps: str) -> str:
    """``ps`` without its "-<style>" parts: "Aptos-Narrow-Bold-Italic" -> "Aptos-Narrow"."""
    while (shorter := strip_style(ps)) != ps:
        ps = shorter
    return ps


def postscript_aliases(names: FontNames, known: set[str]) -> list[str]:
    """PostScript family names of a file that are none of ``known`` (match keys).

    From ID 6 only when it is "<family>-<style>" with style parts that
    ``names.strip_style`` removes, so the family part is certain ("3270" from
    "3270-Regular"; nothing from "ArialMT" or "DMCAsansserif-100"), and never
    when the whole name is a known family ("Arial-Black" is Arial Black). Not
    from a file whose ID 1 name does not begin with its ID 16 name either: its
    names disagree ("NSimSun" in SimSun's collection), so none is certain.
    """
    ps = names.postscript
    if ps is None or match_key(ps) in known or not _names_agree(names):
        return []
    family = _postscript_family(ps)
    if family == ps or not match_key(family) or match_key(family) in known:
        return []
    return [family]


# --- candidates ---------------------------------------------------------------------------------


def _candidate(
    alias: str, target: SourceKey, relation: str, detail: str, evidence: str
) -> AliasCandidate:
    return AliasCandidate(
        alias=SourceKey(NAME_NS, alias),
        target=target,
        relation=relation,
        detail=detail,
        source=NAME,
        evidence=evidence,
        auto=AUTO,
    )


def _file_rows(
    named: NamedFile,
    target: SourceKey,
    base: str,
    known: set[str],
    build: tuple[str, str] | None,
) -> list[AliasCandidate]:
    """The rows of one file whose typographic name belongs to ``target``'s family.

    ``base``: the target family's name; ``known``: match keys of the evidence's
    family names, which never become legacy or postscript rows; ``build``:
    (typographic name, detail) when that name is a build of the target.
    """
    out = []
    if build is not None:
        out.append(_candidate(build[0], target, "build", build[1], named.evidence))
    legacy = legacy_alias(named.names)
    if legacy is not None and match_key(legacy) not in known:
        detail = legacy_detail(legacy, base, None if build is None else build[1])
        out.append(_candidate(legacy, target, "build", detail, named.evidence))
    seen = known | ({match_key(legacy)} if legacy is not None else set())
    out.extend(
        _candidate(alias, target, "postscript", "", named.evidence)
        for alias in postscript_aliases(named.names, seen)
    )
    return out


def _one_target(cands: list[AliasCandidate], notes: list[str] | None) -> list[AliasCandidate]:
    """Drop aliases that one piece of evidence gives to several targets ("FranklinGothic")."""
    targets: dict[str, set[SourceKey]] = defaultdict(set)
    for c in cands:
        targets[match_key(c.alias.key)].add(c.target)
    out = []
    for c in cands:
        found = targets[match_key(c.alias.key)]
        if len(found) == 1:
            out.append(c)
            continue
        note = (
            f"{c.alias.key!r}: one file set names it for {', '.join(sorted(t.key for t in found))}"
        )
        if notes is not None and note not in notes:
            notes.append(note)
    return out


def _roots(names: Iterable[str]) -> dict[str, tuple[str, str | None]]:
    """Each typographic name -> (its root, its build detail), within one formula.

    The root is the name with fewest words of which it is a build; a name with
    none is its own root, detail None.
    """
    by_key = {match_key(n): n for n in sorted(set(names), key=lambda n: (match_key(n), n))}
    out: dict[str, tuple[str, str | None]] = {}
    for key, name in by_key.items():
        bases = [
            (len(_words(b)), b, d)
            for k, b in by_key.items()
            if k != key and (d := build_detail(name, b)) is not None
        ]
        if bases:
            _, base, detail = min(bases)
            out[key] = (base, detail)
        else:
            out[key] = (name, None)
    return out


def formula_candidates(
    files: Sequence[NamedFile], notes: list[str] | None = None
) -> list[AliasCandidate]:
    """The candidates of one piece of kind-1 evidence (a Fontist formula's files)."""
    typos = [f.names.typo for f in files if f.names.typo is not None]
    roots = _roots(typos)
    known = set(roots)
    out: list[AliasCandidate] = []
    for named in files:
        typo = named.names.typo
        if typo is None:
            continue
        root, detail = roots[match_key(typo)]
        build = None if detail is None else (typo, detail)
        out.extend(_file_rows(named, SourceKey(NAME_NS, root), root, known, build))
    return _one_target(out, notes)


def record_target(rec: UniverseRecord) -> SourceKey:
    """The key a record's aliases target: its own, or its family name for a Fontist key."""
    if rec.key.ns == FONTIST_NS:
        return SourceKey(NAME_NS, rec.family)
    return rec.key


def record_candidates(
    rec: UniverseRecord, files: Sequence[NamedFile], notes: list[str] | None = None
) -> list[AliasCandidate]:
    """The candidates of one piece of kind-2 evidence: a record and its files' names.

    A file whose typographic name is neither the record's family nor that
    family plus build words is left out (a note says so).
    """
    family = rec.family
    target = record_target(rec)
    out: list[AliasCandidate] = []
    for named in files:
        typo = named.names.typo
        if typo is None:
            continue
        build = None
        if match_key(typo) != match_key(family):
            detail = build_detail(typo, family)
            if detail is None:
                if notes is not None:
                    notes.append(
                        f"{rec.key.ns}:{rec.key.key} ({family}): {named.evidence} is {typo!r}"
                    )
                continue
            build = (typo, detail)
        known = {match_key(family), match_key(typo)}
        out.extend(_file_rows(named, target, family, known, build))
    return _one_target(out, notes)


def _rank(c: AliasCandidate) -> tuple[Any, ...]:
    return (
        _RELATION_ORDER.index(c.relation),
        DETAIL_ORDER.index(c.detail),
        c.alias.key,
        c.evidence,
    )


def best(cands: Iterable[AliasCandidate]) -> list[AliasCandidate]:
    """One candidate per (alias match key, target), sorted (module docstring)."""
    kept: dict[tuple[str, SourceKey], AliasCandidate] = {}
    for c in cands:
        if match_key(c.alias.key) == match_key(c.target.key) and c.target.ns == NAME_NS:
            continue  # a family's own name
        slot = (match_key(c.alias.key), c.target)
        if slot not in kept or _rank(c) < _rank(kept[slot]):
            kept[slot] = c
    return sorted(kept.values())


# --- kind 1: Fontist formulas -------------------------------------------------------------------


def _first_url(urls: object) -> str | None:
    if not isinstance(urls, list):
        return None
    texts = [u for u in (clean(x) for x in urls) if u is not None]
    return next((u for u in texts if u.startswith("https://")), texts[0] if texts else None)


def _resources(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    found = row.get("resources")
    return [r for r in found if isinstance(r, Mapping)] if isinstance(found, list) else []


def _resource_url(resources: Sequence[Mapping[str, Any]], file: str | None) -> str | None:
    """The URL (https first) of the resource named ``file``, else of the first resource."""
    wanted = file.casefold() if file else None
    named = [r for r in resources if wanted and (clean(r.get("name")) or "").casefold() == wanted]
    chosen = named or resources[:1]
    return _first_url(chosen[0].get("urls")) if chosen else None


def formula_files(row: Mapping[str, Any]) -> list[NamedFile]:
    """Every style of one ``formulas.jsonl.gz`` row as a named file.

    The evidence is the URL (https first) of the resource that is the style's
    font file, else of the formula's first resource (the archive), with the
    style's font file (or collection) as the fragment.
    """
    resources = _resources(row)
    out = []
    for font in row.get("fonts") or ():
        if not isinstance(font, dict):
            continue
        for style in font.get("styles") or ():
            if not isinstance(style, dict):
                continue
            names = FontNames(
                family=clean(style.get("family_name")),
                typographic=clean(style.get("preferred_family_name")),
                postscript=clean(style.get("post_script_name")),
            )
            file = clean(style.get("font")) or clean(font.get("collection"))
            base = _resource_url(resources, file) or f"fontist:{row.get('key')}"
            out.append(NamedFile(names, f"{base}#{file}" if file else base))
    return out


def is_pinned_formula(row: Mapping[str, Any]) -> bool:
    """A formula whose download is pinned: one of its resources gives a sha256."""
    return any(r.get("sha256") for r in _resources(row))


def is_proprietary(row: Mapping[str, Any]) -> bool:
    """A formula that needs a license agreement and has no open license.

    The fontist collector drops such families as proprietary (its setting
    ``agreement_is_proprietary``), so their names would only load the review.
    """
    return row.get("requires_license_agreement") is True and row.get("open_license") is not True


def fontist_candidates(
    rows: Iterable[Mapping[str, Any]], notes: list[str] | None = None
) -> list[AliasCandidate]:
    """Kind-1 candidates of every pinned formula row but the proprietary ones (unsorted)."""
    out: list[AliasCandidate] = []
    for row in rows:
        if not isinstance(row, Mapping) or (SKIP_PROPRIETARY and is_proprietary(row)):
            continue
        if not is_pinned_formula(row):
            if notes is not None:
                notes.append(f"fontist formula {row.get('key')!r}: no sha256, not pinned")
            continue
        out.extend(formula_candidates(formula_files(row), notes))
    return out


def latest_fontist(ctx: MineContext) -> Snapshot:
    """The newest complete ``fontist`` snapshot on or before the run date.

    Raises ``StoreNotConfigured`` without a store and ``FileNotFoundError``
    without a snapshot; the stage then keeps the committed seeds.
    """
    if ctx.store is None:
        ctx.paths.require_store()  # raises with the fix-it message
        raise StoreNotConfigured("this mine context has no snapshot store")
    snap = ctx.store.latest(FONTIST, ctx.run_date)
    if snap is None:
        raise FileNotFoundError(
            f"no {FONTIST} snapshot on or before {ctx.run_date}: "
            f"run `tff-catalog fetch --only {FONTIST}` first"
        )
    return snap


def formula_rows(snap: Snapshot) -> Iterator[Mapping[str, Any]]:
    """The formula objects of a ``fontist`` snapshot's ``formulas.jsonl.gz``."""
    for row in snap.iter_jsonl(FORMULAS_EXTRACT):
        if isinstance(row, dict):
            yield row


# --- kind 2: the universe's pinned font files ---------------------------------------------------


def is_pinned(ref: FontFileRef) -> bool:
    """A font file named by content: it has a sha256, or its URL path holds a commit sha."""
    from tff_catalog.fontfiles import is_font_url

    if not is_font_url(ref.url):
        return False
    return ref.sha256 is not None or _COMMIT.search(urlsplit(ref.url).path) is not None


def chosen_files(rec: UniverseRecord) -> list[FontFileRef]:
    """The files read for a record: the first of each ``FILE_ROLES`` by URL, else the first."""
    if rec.source in SKIP_SOURCES:
        return []
    files = sorted(
        (f for f in rec.files if is_pinned(f) and not f.url.startswith(SKIP_PREFIXES)),
        key=lambda f: f.url,
    )
    picked = [
        next(f for f in files if f.role == role)
        for role in FILE_ROLES
        if any(f.role == role for f in files)
    ]
    return picked or files[:1]


def read_records(records_dir: Path) -> list[UniverseRecord]:
    """The universe records of ``build/stage/records/`` that have files to read, sorted.

    Raises ``FileNotFoundError`` when the directory holds no universe record
    (stage "parse" has not run), so the stage keeps the committed seeds.
    """
    from tff_catalog.facts import universe_records
    from tff_catalog.records import sort_key

    recs = universe_records(records_dir) if Path(records_dir).is_dir() else []
    if not recs:
        raise FileNotFoundError(
            f"{records_dir} holds no universe records: run `tff-catalog parse` first"
        )
    return sorted((r for r in recs if chosen_files(r)), key=sort_key)


def names_from_table(data: bytes) -> FontNames:
    """The names of a raw ``name`` table (fontTools; English names preferred)."""
    from fontTools.ttLib import newTable

    table = newTable("name")
    table.decompile(data, None)

    def get(number: int) -> str | None:
        return clean(table.getDebugName(number))

    return FontNames(family=get(1), typographic=get(16), postscript=get(6))


def read_names(ref: FontFileRef, fetcher: Fetcher) -> FontNames:
    """The names of one font file, read by range.

    Raises ``fontfiles.FontFileError`` when the file is no readable font, has no
    ``name`` table, or its size differs from the reference's.
    """
    from tff_catalog.fontfiles import FontFileError, read_font

    read = read_font(ref.url, ("name",), fetcher)
    if ref.size is not None and read.size is not None and read.size != ref.size:
        raise FontFileError(f"{ref.url}: {read.size} bytes, not the expected {ref.size}")
    data = read.tables.get("name")
    if data is None:
        raise FontFileError(f"{ref.url}: no name table")
    try:
        return names_from_table(data)
    except Exception as exc:  # fontTools raises many types on bad input
        raise FontFileError(f"{ref.url}: cannot decode the name table: {exc}") from exc


@dataclass(slots=True)
class Reads:
    """What kind 2 read: names by file URL, and the files that failed (URL -> error)."""

    names: dict[str, FontNames] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def read_all(
    recs: Sequence[UniverseRecord],
    reader: Callable[[FontFileRef], FontNames],
    *,
    max_failures: int = MAX_FAILURES,
    log: logging.Logger | None = None,
) -> Reads:
    """Names of every chosen file, each URL read once.

    A file that fails is logged and left out; more than ``max_failures``
    failures raise ``RuntimeError``.
    """
    out = Reads()
    for rec in recs:
        for ref in chosen_files(rec):
            if ref.url in out.names or ref.url in out.failed:
                continue
            try:
                out.names[ref.url] = reader(ref)
            except Exception as exc:  # any one file: a bad font, a 404, a timeout
                out.failed[ref.url] = f"{type(exc).__name__}: {exc}"
                if log is not None:
                    log.warning("%s: cannot read %s: %s", NAME, ref.url, exc)
                if len(out.failed) > max_failures:
                    raise RuntimeError(
                        f"{NAME}: more than {max_failures} font files failed to read; "
                        "the stage keeps the committed seeds"
                    ) from exc
    return out


def file_candidates(
    recs: Iterable[UniverseRecord], names: Mapping[str, FontNames], notes: list[str] | None = None
) -> list[AliasCandidate]:
    """Kind-2 candidates of every record from the names read (unsorted)."""
    out: list[AliasCandidate] = []
    for rec in recs:
        files = [NamedFile(names[f.url], f.url) for f in chosen_files(rec) if f.url in names]
        out.extend(record_candidates(rec, files, notes))
    return out


def fetch_hosts(recs: Iterable[UniverseRecord]) -> frozenset[str]:
    """The hosts of every chosen file, plus GitHub's download hosts."""
    hosts = {urlsplit(f.url).hostname or "" for r in recs for f in chosen_files(r)}
    return frozenset((hosts - {""}) | REDIRECT_HOSTS)


# --- the miner ----------------------------------------------------------------------------------


class NameTablesMiner:
    """Upstream name-table names (build, static and PostScript) -> their families.

    ``transport`` (an ``httpx`` transport) and ``min_interval`` are for tests;
    the default fetcher is live, at ``MIN_INTERVAL`` seconds per host.
    """

    name: ClassVar[str] = NAME

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        min_interval: float = MIN_INTERVAL,
        max_failures: int = MAX_FAILURES,
    ) -> None:
        self.transport = transport
        self.min_interval = min_interval
        self.max_failures = max_failures

    def _fetcher(self, hosts: frozenset[str]) -> Fetcher:
        from tff_catalog.fetch import Fetcher

        return Fetcher(
            hosts=hosts,
            min_interval=dict.fromkeys(hosts, self.min_interval),
            transport=self.transport,
        )

    def mine(self, ctx: MineContext) -> list[AliasCandidate]:
        """Candidates from the Fontist formulas and the universe's pinned files (module docstring)."""
        snap = latest_fontist(ctx)
        formulas = list(formula_rows(snap))
        notes: list[str] = []
        kind1 = fontist_candidates(formulas, notes)
        recs = read_records(ctx.paths.records)
        hosts = fetch_hosts(recs)
        with self._fetcher(hosts) as fetcher:
            reads = read_all(
                recs,
                lambda ref: read_names(ref, fetcher),
                max_failures=self.max_failures,
                log=ctx.log,
            )
        kind2 = file_candidates(recs, reads.names, notes)
        for note in notes:
            ctx.log.info("%s: left out %s", NAME, note)
        found = best([*kind1, *kind2])
        ctx.log.info(
            "%s: %d candidates (%d formulas of %s %s; %d files of %d records read, %d failed, "
            "%d left out)",
            NAME,
            len(found),
            len(formulas),
            FONTIST,
            snap.date,
            len(reads.names),
            len(recs),
            len(reads.failed),
            len(notes),
        )
        return found


MINER = NameTablesMiner()
