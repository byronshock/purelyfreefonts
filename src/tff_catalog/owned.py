"""Owned-font matching for the hopper: the match document, the parser and the matcher.

The hopper (Milestone 3, slice 1; owner rulings of 2026-10-21 to 2026-10-23,
``inventory_slice_1_defaults``, ``inventory_match_data_site_build`` and
``hopper_hackgen_related``) takes a visitor's list of installed fonts in the
browser and hides the catalog families they already have. This module is the
reference that the site's JavaScript port must agree with
(``tests/vectors/owned-fonts.json``). A visitor's list never leaves the browser,
so this code reads real lists only in tests.

- ``WORDS``: the characters and words that ``candidates`` strips, as one
  JSON-ready object. The match document carries it, so the port derives its
  rules from the same data instead of copying regular expressions.
- ``candidates``: a name's ``match_key``, then one trailing style word or build
  suffix stripped at a time. Width words stay, at least one word is kept, and
  nothing matches by prefix.
- ``match_document``: the compact match file that the site build publishes
  from ``build/names.json`` and the catalog's ids, with the reviewed recodes
  (``hopper_hackgen_related``: HackGen is only related to Hack here).
- ``check_document``: the collision check (Milestone 3 step 3): names that
  stripping turns into another family's key (the ``amb`` set), and
  ``distinct_from`` pairs that must never meet.
- ``parse``: a dropped file or a paste, in any format of docs/owned-fonts.md, to
  entries, warnings and error codes. It never raises.
- ``Table`` and ``match``: entries to owned and near-match families. An old name
  counts as owned (M3-D3 a), a patched build counts as owned unless
  ``patched=False`` (M3-D4 a), and derived, related, sibling and bundle names are
  never owned (M3-D5 a).

Rules that keep the port in step (each has a vector):

- Lengths, limits and sniffing windows count code points, never UTF-16 units:
  a name of 200 astral characters is 400 units long in JavaScript and still
  within ``MAX_NAME``.
- Lone surrogates, which only pasted text and JSON escapes can hold, become
  U+FFFD with the warning ``decoding``; valid astral characters stay. In
  JavaScript that is ``/\\p{Cs}/gu`` or ``toWellFormed()``: ``[\\ud800-\\udfff]``
  without the u flag also matches both halves of every astral character.
- Rules are ASCII-only, with no ``\\s``, ``\\d``, ``\\w`` or Unicode case folding.
  Case-insensitive rules fold ASCII letters only (Python ``re.ASCII |
  re.IGNORECASE``, JavaScript ``/i`` without ``u``), and names are NFKC-normalized
  first, so full-width letters still match. Python's ``\\Z`` is JavaScript's
  ``$`` without the m flag; line patterns use DOTALL (JavaScript ``s``), because
  a line may hold U+2028.
- No optional group holds a capture that the code reads: JavaScript gives
  ``undefined`` where Python gives ``""``.
- Keys are looked up in maps (JavaScript ``Map`` or ``Object.hasOwn``):
  "Constructor" has the key "constructor", which a plain object lookup finds on
  ``Object.prototype``.
- Every JSON text in a visitor's list goes through one scanner (``_scan_json``),
  never ``json.loads`` or ``JSON.parse``, so duplicate keys, huge integers and
  deep nesting read alike on both sides.

Standard library only (``keys``, ``names`` and ``jsonio``), so ``tff_site`` can
import it.
"""

import hashlib
import itertools
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from tff_catalog import jsonio
from tff_catalog.keys import (
    DASH_CODEPOINTS,
    JOINER_CODEPOINTS,
    WHITESPACE_CODEPOINTS,
    expand_codepoints,
    match_key,
    search_key,
)
from tff_catalog.names import (
    MODIFIERS,
    PS_ABBREVIATIONS,
    STYLE_ABBREVIATIONS,
    STYLE_WORDS,
    VARIABLE_WORDS,
)

FORMAT = "tff-match"
VERSION = 1

# The strippable words and the characters around them, as the match document
# carries them. Code points are hex, as in tests/vectors/name-keys.json.
WORDS: dict[str, Any] = {
    "whitespace": list(WHITESPACE_CODEPOINTS),  # each run becomes one space
    "dashes": list(DASH_CODEPOINTS),  # read as "-"
    "joiners": list(JOINER_CODEPOINTS),  # removed
    "separators": ["0020", "002D", "005F"],  # before a build suffix
    # A whole word; in a spaced name also the last part of a token of parts (BlackItalic,
    # Black-Regular), one at a time; after a PostScript hyphen, every part at once.
    "style": sorted(STYLE_WORDS),
    "modifiers": sorted(MODIFIERS),  # only with a style word after them (Extra Light)
    "postscript": sorted(PS_ABBREVIATIONS),  # also style words after a PostScript hyphen
    "abbreviations": sorted(STYLE_ABBREVIATIONS),  # whole words only (Kinto Sans Med)
    "variable": sorted(VARIABLE_WORDS),  # after a separator, in any ASCII case
    "variable_joined": ["Variable"],  # as written, right after a lower-case letter
    # Any ASCII case, separators optional ("FiraCode Nerd Font", "IosevkaTermNerdFontPropo").
    "nerd": ["nerd font", "nerd font mono", "nerd font propo"],
    # As written, after a separator, a lower-case letter or a digit ("Hack NF", "FiraCodeNFM").
    "nerd_tokens": ["NF", "NFM", "NFP"],
    "cased": ["CN", "NL", "PL"],  # as written, after a separator
}
# Build details: the same font, or a patched build (M3-D4).
SAME_FONT_DETAILS = frozenset({"", "variable", "static", "opsz"})
PATCHED_DETAILS = frozenset({"nerd", "nf", "nfm", "nfp", "propo", "powerline", "nl", "cjk"})

_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9_-]+")
_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")
_LOWER_DIGIT = _LOWER | frozenset("0123456789")


# --- candidates ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """One key to look up: the text it came from, and what was stripped to reach it."""

    key: str
    text: str
    patched: bool = False  # a Nerd, CN, PL or NL suffix was stripped
    # The family part before a PostScript name's one hyphen (ArchivoBlack-Regular), a
    # trusted name; not set for a part that more hyphens leave in doubt (Archivo-Black-Bold).
    postscript: bool = False


@dataclass(frozen=True, slots=True)
class _Rules:
    table: Mapping[int, str | None]
    seps: str
    after_token: frozenset[str]
    style: frozenset[str]
    ps_style: frozenset[str]
    modifiers: frozenset[str]
    abbreviations: frozenset[str]
    variable: frozenset[str]
    variable_joined: tuple[str, ...]
    nerd: re.Pattern[str]
    nerd_tokens: tuple[str, ...]
    cased: tuple[str, ...]


# The parts of a WORDS-shaped object, each a list of non-empty printable ASCII strings.
_WORD_PARTS = (
    "whitespace", "dashes", "joiners", "separators", "style", "modifiers", "postscript",
    "abbreviations", "variable", "variable_joined", "nerd", "nerd_tokens", "cased",
)  # fmt: skip
_PRINTABLE = re.compile(r"[\x20-\x7e]+")
_HEX = re.compile(r"[0-9A-Fa-f]{1,6}")


def _word_list(words: Mapping[str, Any], name: str) -> list[str]:
    """``words[name]``, a list of non-empty printable ASCII strings; else ValueError.

    ASCII keeps both sides' case folding equal: JavaScript's /i without the u flag folds
    é and É, which Python's re.ASCII | re.IGNORECASE does not.
    """
    value = words.get(name)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and _PRINTABLE.fullmatch(item) for item in value
    ):
        raise ValueError(f"words.{name} must be a list of printable ASCII strings")
    return value


def _points(entries: list[str], name: str) -> list[int]:
    """The code points of hex entries ("0020", "2000-200A"); ValueError for a bad entry.

    Hex is 1 to 6 digits and nothing else (``int(h, 16)`` also takes "0x20" and "2_0"),
    a range runs upwards to at most U+10FFFF, and no entry names a surrogate.
    """
    out: list[int] = []
    for entry in entries:
        parts = entry.split("-")
        if len(parts) > 2 or not all(_HEX.fullmatch(part) for part in parts):
            raise ValueError(f"words.{name}: bad code point {entry!r}")
        first, last = int(parts[0], 16), int(parts[-1], 16)
        if last > 0x10FFFF or last < first or (first <= 0xDFFF and last >= 0xD800):
            raise ValueError(f"words.{name}: bad range {entry!r}")
        out.extend(range(first, last + 1))
    return out


def _compile(words: Any) -> _Rules:
    """The rules of a ``WORDS``-shaped object; ValueError when a part is missing or malformed.

    It refuses exactly what the port's ``OwnedMatch.table`` refuses (the ``malformed``
    vectors): a part that is no list of non-empty printable ASCII strings, a bad code point
    (``_points``), separators that are none or not each one printable ASCII code point, and
    Nerd phrases that are none or hold no word. An empty phrase or ``variable_joined`` entry
    would strip nothing forever. The separators and phrases make a pattern that ignores case:
    on printable ASCII, re.ASCII | re.IGNORECASE folds as JavaScript's /i without the u flag,
    which would also fold é with É, and ÿ with Ÿ.
    """
    if not isinstance(words, dict):
        raise ValueError("words must be an object")
    parts = {name: _word_list(words, name) for name in _WORD_PARTS}
    table: dict[int, str | None] = {}
    table.update(dict.fromkeys(_points(parts["whitespace"], "whitespace"), " "))
    table.update(dict.fromkeys(_points(parts["dashes"], "dashes"), "-"))
    table.update(dict.fromkeys(_points(parts["joiners"], "joiners")))
    seps = "".join(map(chr, _points(parts["separators"], "separators")))
    if any("-" in s for s in parts["separators"]) or not _PRINTABLE.fullmatch(seps):
        raise ValueError("words.separators must be single printable ASCII code points")
    sep = "[" + "".join(re.escape(s) for s in seps) + "]"
    phrases = [
        [word for word in phrase.split(" ") if word]
        for phrase in sorted(parts["nerd"], key=len, reverse=True)
    ]
    if not phrases or not all(phrases):
        raise ValueError("words.nerd must hold phrases")
    alternatives = "|".join((sep + "*").join(map(re.escape, phrase)) for phrase in phrases)
    style = frozenset(parts["style"])
    return _Rules(
        table=table,
        seps=seps,
        after_token=_LOWER_DIGIT | frozenset(seps),
        style=style,
        ps_style=style | frozenset(parts["postscript"]),
        modifiers=frozenset(parts["modifiers"]),
        abbreviations=frozenset(parts["abbreviations"]),
        variable=frozenset(parts["variable"]),
        variable_joined=tuple(parts["variable_joined"]),
        # ASCII-only case folding, as JavaScript's /i without the u flag on ASCII.
        nerd=re.compile(f"{sep}*(?:{alternatives})\\Z", re.ASCII | re.IGNORECASE),
        nerd_tokens=tuple(sorted(parts["nerd_tokens"], key=len, reverse=True)),
        cased=tuple(parts["cased"]),
    )


_DEFAULT_RULES = _compile(WORDS)


def _normalize(name: str, rules: _Rules) -> str:
    text = unicodedata.normalize("NFKC", name).translate(rules.table)
    return " ".join(part for part in text.split(" ") if part)


def _style_token(token: str, rules: _Rules, *, postscript: bool = False) -> str | None:
    """The kind of style word ``token`` is ("style" or "abbreviation"), or None.

    The rule of ``names.is_style_word`` restricted to ASCII letters, digits,
    hyphens and underscores: "Bold™" and "Bold٣" are no style words here.
    """
    if not token.isascii():
        return None
    words = rules.ps_style if postscript else rules.style
    flat = token.lower().replace("-", "").replace("_", "")
    if flat in words:
        return "style"
    if flat in rules.abbreviations:
        return "abbreviation"
    if not _TOKEN_CHARS.fullmatch(token):
        return None
    parts = [p.lower() for p in _CAMEL.findall(token)]
    if (
        parts
        and all(p in words or p in rules.modifiers for p in parts)
        and any(p in words for p in parts)
    ):
        return "style"
    return None


def _style_tail(token: str, rules: _Rules) -> tuple[str, str] | None:
    """The style unit that ends a spaced name's last ``token``: its kind, and what stays of it.

    A style word or abbreviation goes whole ("Bold", "Semi-Bold", "Med"). In a token of
    camel-case or hyphen- or underscore-joined parts (``_style_token``), only the last part
    goes, with the modifiers before it ("ExtraLight"), and the rest stays: "BlackItalic"
    leaves "Black" and "Black-Regular" leaves "Black", so "Archivo BlackItalic" reaches the
    ambiguous "Archivo Black" before "Archivo", as "Archivo Black Italic" does. A token that
    ends in a modifier ("BoldExtra") strips nothing, as "Bold Extra" doesn't.
    """
    kind = _style_token(token, rules)
    if kind is None:
        return None
    if kind == "abbreviation" or token.lower().replace("-", "").replace("_", "") in rules.style:
        return kind, ""
    parts = list(_CAMEL.finditer(token))
    n = len(parts) - 1
    if parts[n].group().lower() not in rules.style:
        return None
    while n > 0 and parts[n - 1].group().lower() in rules.modifiers:
        n -= 1
    return kind, token[: parts[n].start()].rstrip("-_")


def _strip(text: str, rules: _Rules) -> tuple[str, str] | None:
    """``text`` without one trailing unit, and what the unit was; None when none strips.

    Every branch keeps a head with a non-empty ``match_key``, so "- Bold" stops at "bold".
    """
    m = rules.nerd.search(text)
    if m is not None and match_key(text[: m.start()]):
        return text[: m.start()], "patched"
    for tail in (*rules.nerd_tokens, *rules.cased):
        if text.endswith(tail):
            head = text[: -len(tail)]
            after = rules.seps if tail in rules.cased else rules.after_token
            if head and head[-1] in after:
                head = head.rstrip(rules.seps)
                if match_key(head):
                    return head, "patched"
    cut = max(text.rfind(s) for s in rules.seps)
    if cut > 0:
        word = text[cut + 1 :]
        if word.isascii() and word.lower() in rules.variable:
            head = text[:cut].rstrip(rules.seps)
            if match_key(head):
                return head, "variable"
    for tail in rules.variable_joined:
        if text.endswith(tail) and len(text) > len(tail) and text[-len(tail) - 1] in _LOWER:
            return text[: -len(tail)], "variable"
    if " " not in text:
        # A PostScript name: only the part after the last hyphen can go, so
        # "Inter-Tight-Bold" reaches "Inter-Tight" and never "Inter".
        family, dash, style = text.rpartition("-")
        if dash and match_key(family) and _style_token(style, rules, postscript=True):
            return family, "postscript"
        return None
    tokens = text.split(" ")
    tail = _style_tail(tokens[-1], rules)
    if tail is None:
        return None
    kind, rest = tail
    if rest:
        return " ".join([*tokens[:-1], rest]), "style"
    tokens.pop()
    while kind == "style" and len(tokens) > 1 and tokens[-1].isascii():
        if tokens[-1].lower() not in rules.modifiers:
            break
        tokens.pop()
    head = " ".join(tokens)
    return (head, "style") if match_key(head) else None


def _chain(name: str, rules: _Rules) -> tuple[Candidate, ...]:
    key = match_key(name)
    if not key:
        return ()
    text = _normalize(name, rules)
    out = [Candidate(key, text)]
    patched = postscript = False
    ps_steps = 0
    while (step := _strip(text, rules)) is not None:
        text, kind = step
        patched = patched or kind == "patched"
        if kind == "postscript":
            # Only a family part that one hyphen set apart is certain (ArchivoBlack-Regular,
            # Milestone 3 step 5): in Archivo-Black-Bold either hyphen may end the family.
            ps_steps += 1
            postscript = postscript or (ps_steps == 1 and "-" not in text)
        out.append(Candidate(match_key(text), text, patched, postscript))
    return tuple(out)


def candidates(name: str, words: Mapping[str, Any] = WORDS) -> tuple[Candidate, ...]:
    """``name``'s ``match_key``, then one trailing unit stripped at a time, longest first.

    Units, tried in this order at the end of the name: a Nerd phrase ("Nerd Font",
    "Nerd Font Mono", "Nerd Font Propo", any case, separators optional) or token
    (NF, NFM, NFP after a separator, lower-case letter or digit); CN, NL or PL after
    a separator; Variable, Var or VF after a separator, or "Variable" right after a
    lower-case letter; in a name without spaces, a style part after the last hyphen
    (PostScript form); a style word, with the modifiers before it (Extra Light), or
    the last part of a token of style parts, with its modifiers ("Archivo
    BlackItalic" gives "Archivo Black", then "Archivo"; ``_style_tail``).
    Width words (Condensed, Narrow) are never stripped, and every key is non-empty.
    ``candidates("Maple Mono NF CN")`` gives the keys maplemononfcn, maplemononf
    and maplemono, the last two patched. An empty key gives ``()``. Other ``words``
    must be ``WORDS``-shaped (ValueError otherwise).
    """
    return _chain(name, _DEFAULT_RULES if words is WORDS else _compile(words))


# --- the match document ----------------------------------------------------------------------

_LETTERS = {
    "rename": "r",
    "postscript": "p",
    "bundle": "u",
    "package": "u",  # none yet; a near-match only until a ruling says otherwise
    "sibling": "s",
    "related": "x",
}
# One code per family per key: the strongest.
_STRENGTH = {letter: n for n, letter in enumerate(("", "p", "b", "r", "n", "s", "x", "u"))}
_OWNED_LETTERS = frozenset({"", "p", "b", "r", "n"})
# What a reviewed recode may make of a names.json alias: a near-match relation only
# (hopper_hackgen_related: HackGen, a build of Hack in names.json, is related here).
RECODE_RELATIONS = frozenset({"related", "sibling", "bundle"})


def _recodes(names_doc: Mapping[str, Any], recodes: Mapping[str, str] | None) -> dict[str, str]:
    """``recodes`` checked: each name an alias some family of ``names_doc`` lists, each
    relation one of ``RECODE_RELATIONS``; else ValueError, so a stale entry is caught."""
    out = dict(recodes or {})
    for name, relation in out.items():
        if not isinstance(name, str) or relation not in RECODE_RELATIONS:
            raise ValueError(
                f"recode {name!r}: {relation!r} is not one of {sorted(RECODE_RELATIONS)}"
            )
    listed = {row["name"] for fam in names_doc["families"] for row in fam["names"]}
    stale = sorted(set(out) - listed)
    if stale:
        raise ValueError(f"recodes name no alias of names.json: {stale}")
    return out


def _letter(item: Mapping[str, Any], recodes: Mapping[str, str]) -> str:
    relation = recodes.get(item["name"], item["relation"])
    if relation == "build":
        detail = item.get("detail")
        if detail in PATCHED_DETAILS:
            return "n"
        if detail in SAME_FONT_DETAILS:
            return "b"
        raise ValueError(f"names.json: build {item['name']!r} has an unknown detail {detail!r}")
    if relation not in _LETTERS:
        raise ValueError(f"names.json: {item['name']!r} has an unknown relation {relation!r}")
    return _LETTERS[relation]


@dataclass(slots=True)
class _Holders:
    ids: dict[str, str] = field(default_factory=dict)  # catalog id -> strongest letter
    others: dict[str, str] = field(default_factory=dict)  # the same, for families not listed
    reasons: set[str] = field(default_factory=set)  # ineligible names' reasons

    def families(self) -> set[str]:
        return set(self.ids) | set(self.others)

    def owns(self, fid: str) -> bool:
        """Whether a hit on this key could count ``fid`` as owned (not only near)."""
        return self.ids.get(fid, self.others.get(fid, "u")) in _OWNED_LETTERS


def _holders(
    names_doc: Mapping[str, Any], catalog: frozenset[str], recodes: Mapping[str, str]
) -> tuple[dict[str, _Holders], list[str]]:
    """Every known name's key with its holders, and the known names in document order."""
    out: dict[str, _Holders] = defaultdict(_Holders)
    seen: dict[str, None] = {}
    for fam in names_doc["families"]:
        fid = fam["id"]
        rows = [(fam["family"], ""), *((n["name"], _letter(n, recodes)) for n in fam["names"])]
        for name, letter in rows:
            key = match_key(name)
            if not key:
                continue
            seen.setdefault(name)
            held = out[key].ids if fid in catalog else out[key].others
            if fid not in held or _STRENGTH[letter] < _STRENGTH[held[fid]]:
                held[fid] = letter
    for row in names_doc["ineligible"]:
        key = match_key(row["name"])
        if key:
            seen.setdefault(row["name"])
            out[key].reasons.add(row["reason"])
    return dict(out), list(seen)


@dataclass(frozen=True, slots=True)
class Collision:
    """A known name whose stripped form is another family's key."""

    name: str
    key: str
    reached: str  # the first stripped key that is known
    families: tuple[str, ...]  # its families that the name's own key lacks
    catalog: tuple[str, ...]  # those in the catalog: the name's amb entry


@dataclass(frozen=True, slots=True)
class Meeting:
    """A name of ``family`` whose candidate chain reaches ``other``, which it is distinct from."""

    family: str
    other: str
    name: str
    key: str


@dataclass(frozen=True, slots=True)
class Check:
    """What ``check_document`` found. ``meetings`` must be empty; the rest is reported."""

    collisions: tuple[Collision, ...]
    amb: Mapping[str, tuple[str, ...]]  # key -> catalog ids its name may be a weight of
    meetings: tuple[Meeting, ...]
    shared: Mapping[str, tuple[str, ...]]  # keys that several catalog families own


def check_document(
    names_doc: Mapping[str, Any],
    catalog_ids: Iterable[str],
    *,
    recodes: Mapping[str, str] | None = None,
) -> Check:
    """The collision check of Milestone 3 step 3, on ``names.json`` and the catalog's ids.

    It reads names, not the match document's keys, because stripping needs the
    word boundaries that keys drop. For every known name, the first stripped
    candidate that is a known key decides, as in ``match``: when that key belongs
    to a family the name's own key lacks, it is a collision, and the catalog
    families among them make the name's key ambiguous ("Archivo Black" may be
    Archivo's Black weight). For every ``distinct_from`` pair, in both
    directions, no candidate of any name of one family may be a key at which the
    other holds a code that could count it as owned (Inter Tight never reaches
    Inter; a bundle name both share, such as "Libertinus", only gives near-matches).
    ``recodes`` as in ``match_document``.
    """
    catalog = frozenset(catalog_ids)
    holders, names = _holders(names_doc, catalog, _recodes(names_doc, recodes))
    collisions: list[Collision] = []
    amb: dict[str, set[str]] = defaultdict(set)
    for name in names:
        chain = candidates(name)
        own = holders[chain[0].key].families()
        for cand in chain[1:]:
            if cand.key in holders:
                new = holders[cand.key].families() - own
                if new:
                    cat = tuple(sorted(new & catalog))
                    collisions.append(
                        Collision(name, chain[0].key, cand.key, tuple(sorted(new)), cat)
                    )
                    amb[chain[0].key].update(cat)
                break
    by_id = {fam["id"]: fam for fam in names_doc["families"]}
    pairs = {(fam["id"], other) for fam in names_doc["families"] for other in fam["distinct_from"]}
    meetings: list[Meeting] = []
    for fid, other in sorted(pairs | {(b, a) for a, b in pairs}):
        fam = by_id.get(fid)
        if fam is None:
            continue
        for name in (fam["family"], *(n["name"] for n in fam["names"])):
            for cand in candidates(name):
                found = holders.get(cand.key)
                if found is not None and found.owns(other):
                    meetings.append(Meeting(fid, other, name, cand.key))
    shared = {
        key: tuple(sorted(fid for fid, letter in h.ids.items() if letter in _OWNED_LETTERS))
        for key, h in sorted(holders.items())
        if sum(letter in _OWNED_LETTERS for letter in h.ids.values()) > 1
    }
    return Check(
        tuple(collisions),
        {key: tuple(sorted(ids)) for key, ids in sorted(amb.items()) if ids},
        tuple(meetings),
        shared,
    )


def _catalog_ids(
    names_doc: Mapping[str, Any], catalog_ids: Iterable[str], run_date: str
) -> list[str]:
    ids = list(catalog_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("the catalog ids repeat an id")
    if names_doc.get("run", {}).get("date") != run_date:
        raise ValueError(
            f"names.json is of run {names_doc.get('run', {}).get('date')}, not {run_date}"
        )
    listed = {fam["id"] for fam in names_doc["families"] if fam["in_catalog"]}
    if set(ids) != listed:
        only_catalog = sorted(set(ids) - listed)[:5]
        only_names = sorted(listed - set(ids))[:5]
        raise ValueError(
            "the catalog ids are not names.json's in_catalog families: "
            f"only in the catalog {only_catalog}, only in names.json {only_names}"
        )
    return sorted(ids)


def match_document(
    names_doc: Mapping[str, Any],
    catalog_ids: Iterable[str],
    run_date: str,
    *,
    recodes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The hopper's match file, compact and canonical, from ``names.json`` and the catalog ids.

    ``{"format": "tff-match", "v": 1, "run_date", "names_sha256", "ids", "k",
    "amb", "reasons", "words"}``: ``ids`` are the catalog's family ids, sorted;
    ``k`` maps every known name's ``match_key`` (catalog or not, aliases and
    ineligible names too) to its codes, comma-joined: ``<i>`` the family ``ids[i]``
    itself, ``<i>r`` a rename, ``<i>p`` a PostScript name, ``<i>b`` a same-font
    build, ``<i>n`` a patched build, ``<i>u`` a bundle (or package), ``<i>s`` a
    sibling, ``<i>x`` a related family, ``-`` a known family not in the catalog,
    ``!<j>`` an ineligible name with ``reasons[j]``. ``amb`` maps ambiguous keys to
    the indices of the catalog families their name may be a weight of
    (``check_document``). ``names_sha256`` hashes the canonical form of
    ``names_doc``. The catalog ids must be exactly the ``in_catalog`` families.

    ``recodes`` maps alias names, as names.json spells them, to the relation the
    hopper gives them instead (``RECODE_RELATIONS``), in every family that lists
    them: a reviewed list beside names.json, which the site build reads
    (``hopper_hackgen_related``: ``{"HackGen": "related"}``, so HackGen is only a
    near-match of Hack while the alias row, and the ranking, stay). A name no
    family lists as an alias, or another relation, is a ValueError.
    """
    ids = _catalog_ids(names_doc, catalog_ids, run_date)
    index = {fid: n for n, fid in enumerate(ids)}
    holders, _ = _holders(names_doc, frozenset(ids), _recodes(names_doc, recodes))
    reasons = sorted({row["reason"] for row in names_doc["ineligible"]})
    rindex = {reason: n for n, reason in enumerate(reasons)}
    k: dict[str, str] = {}
    for key in sorted(holders):
        h = holders[key]
        codes = [f"{index[fid]}{h.ids[fid]}" for fid in sorted(h.ids, key=index.__getitem__)]
        codes += ["-"] * bool(h.others)
        codes += [f"!{rindex[r]}" for r in sorted(h.reasons, key=rindex.__getitem__)]
        k[key] = ",".join(codes)
    check = check_document(names_doc, ids, recodes=recodes)
    return {
        "format": FORMAT,
        "v": VERSION,
        "run_date": run_date,
        "names_sha256": hashlib.sha256(jsonio.canonical_bytes(names_doc)).hexdigest(),
        "ids": ids,
        "k": k,
        "amb": {key: [index[fid] for fid in fids] for key, fids in check.amb.items()},
        "reasons": reasons,
        "words": json.loads(json.dumps(WORDS)),
    }


# --- parsing -----------------------------------------------------------------------------------

# Limits and windows count code points (Unicode scalar values), except MAX_BYTES
# and the UTF-16 sniff, which count bytes of a dropped file.
MAX_BYTES = 10_000_000
MAX_CHARS = 10_000_000
MAX_LINE = 2_000
MAX_NAME = 200  # after white space is collapsed
SNIFF_BYTES = 4_096  # UTF-16 without a byte-order mark
SNIFF_HTML = 1_024
SNIFF_JSON = 4_096
FEW_NAMES = 20
CUT_OFF_LINES = 500  # a pasted list this long whose top looks missing
FC_LIST_ONE_IN = 20  # one plain line in 20 written by fontconfig makes the list fc-list's

FORMATS = (
    "macos-json", "macos-text", "fc-list", "fc-list-plain", "windows-registry", "json-list", "lines",
)  # fmt: skip
ERRORS = ("empty", "too_big", "binary", "font_file", "html", "command", "not_found", "no_names")
WARNINGS = ("few_names", "cut_off", "prompt_dropped", "decoding", "paths_dropped", "hidden_skipped")
# The commands of docs/owned-fonts.md and their parts; compared in ASCII lower case.
COMMAND_WORDS = frozenset(
    {
        "fc-list", "fc-match", "fc-query", "system_profiler", "osascript", "pbcopy", "wl-copy",
        "xclip", "get-item", "get-childitem", "get-itemproperty", "add-type", "set-clipboard",
        "foreach-object", "new-object", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    }
)  # fmt: skip
FONT_EXTENSIONS = frozenset(
    {"ttf", "otf", "ttc", "otc", "fon", "fnt", "pfm", "pfb", "woff", "woff2"}
)


@dataclass(frozen=True, slots=True)
class Entry:
    """One installed font as a list gives it: its family names, full name and PostScript name.

    ``trusted`` when the first family name is the font's real family (fc-list's
    first name, a macOS Family), so an ambiguous name there may count as owned.
    """

    families: tuple[str, ...] = ()
    full: str | None = None
    postscript: str | None = None
    trusted: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "families": list(self.families),
            "full": self.full,
            "postscript": self.postscript,
            "trusted": self.trusted,
        }


@dataclass(frozen=True, slots=True)
class Parsed:
    """What ``parse`` found: entries, the format, counts, warnings, or an error code."""

    entries: tuple[Entry, ...] = ()
    format: str | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "error": self.error,
            "warnings": list(self.warnings),
            "counts": dict(self.counts),
            "entries": [e.to_json() for e in self.entries],
        }


_FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf", b"wOFF", b"wOF2")
_BINARY_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"%PDF-", b"\x89PNG\r\n\x1a\n", b"\x1f\x8b")
_WS = expand_codepoints(WHITESPACE_CODEPOINTS)
_WS_TABLE = dict.fromkeys(_WS, " ")
_BLANK = _WS | expand_codepoints(JOINER_CODEPOINTS)
_BLANK_CHARS = "".join(map(chr, sorted(_BLANK)))
_VISIBLE = re.compile("[^" + "".join(re.escape(chr(c)) for c in sorted(_BLANK)) + "]")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_ASCII_ALNUM = frozenset("0123456789abcdefghijklmnopqrstuvwxyz")
_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")  # C0 controls but white space, and DEL
_SURROGATE = re.compile(r"[\ud800-\udfff]")  # in a str, only lone surrogates
_HTML = re.compile(r"<!doctype html|<html[ \t\r\n>]|<head[ \t\r\n>]|<body[ \t\r\n>]")
# JSON: the first visible character opens it, or (a paste cut at the top) most of
# the first lines are JSON members, strings or brackets.
_JSON_MEMBER = re.compile(r'"(?:[^"\\]|\\.)*"[ \t\r\n]*:', re.DOTALL)
_JSON_ARRAY = re.compile(r'\[[ \t\r\n]*"')
_JSON_LINE = re.compile(r'[ \t]*(?:"(?:[^"\\]|\\.)*"[ \t]*(?::.*|,?)|[\[\]{}],?)[ \t]*', re.DOTALL)
_JSON_TOKEN = re.compile(r'"(?:[^"\\\x00-\x1f]|\\.)*"|[{}\[\]:,]|[^ \t\r\n{}\[\]:,"]+|"', re.DOTALL)
_FAMILY_LINE = re.compile(r"[ \t]*Family:[ \t]*(?P<name>.*)", re.DOTALL)
_LOCATION_LINE = re.compile(r"[ \t]*Location:")
# The patterns below run on cleaned lines: single spaces, no tabs, no controls.
_DASH_RULE = re.compile(r"-{3,}(?: -{3,})*")
_PATH = re.compile(r"/|~|[A-Za-z]:\\")
_REGISTRY_SUFFIX = re.compile(
    r"\((?:TrueType|OpenType|Type 1|All res|VGA res|8514/a res|[0-9,]+ res)\)(?: ?:.*)?",
    re.ASCII | re.IGNORECASE | re.DOTALL,
)
_TABLE_KEY = re.compile(r"Fonts[ \t]{2,}")  # Get-Item's Name column, on the raw line
_PS_PROPERTY = re.compile(r"PS(?:Path|ParentPath|ChildName|Drive|Provider) ?:")
_NOT_FOUND = re.compile(
    r"command not found|is not recognized as|commandnotfoundexception|unknown command",
    re.ASCII | re.IGNORECASE,
)
# PowerShell's error record and fish's caret line, after a not-found line.
_ERROR_DETAIL = re.compile(r"\+ |At line:[0-9]|At char:[0-9]")
_CARETS = re.compile(r"\^[~^]*")
_PROMPT_SYMBOL = "[$#%>\u276f]"
# A prompt shape: PowerShell's "PS C:\...>", "[user@host dir]$", "user@host:~$",
# "user@host ~ %" or "~/dir $", then the rest of the line (maybe a command).
_PROMPT = re.compile(
    r"(?:PS(?: [^>]*)?>"
    rf"|\[[^\]]*\] ?{_PROMPT_SYMBOL}"
    rf"|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+(?:[: ][^ $#%>\u276f]*)? ?{_PROMPT_SYMBOL}"
    rf"|~[^ $#%>\u276f]* ?{_PROMPT_SYMBOL}"
    r") ?(?P<rest>.*)",
    re.DOTALL,
)
# A bare prompt symbol counts alone or before a command ("$ fc-list : family"),
# so names that start or end with $, #, % or > are kept.
_BARE_PROMPT = re.compile(rf"{_PROMPT_SYMBOL} (?P<rest>.+)", re.DOTALL)
_LONE_PROMPT = frozenset("$#%>\u276f")
# A command's first word, after "(" or "&" and an optional sudo or doas.
_COMMAND = re.compile(
    r"(?:[(&] ?)*(?:(?:sudo|doas) )?(?P<word>[^ :|>;&()]+)", re.ASCII | re.IGNORECASE
)


def _decode(raw: bytes) -> tuple[str, bool]:
    """The text of ``raw`` and whether a replacement character went in."""
    if raw.startswith(b"\xef\xbb\xbf"):
        codec, body = "utf-8", raw[3:]
    elif raw.startswith(b"\xff\xfe"):
        codec, body = "utf-16-le", raw[2:]
    elif raw.startswith(b"\xfe\xff"):
        codec, body = "utf-16-be", raw[2:]
    else:
        codec, body = _utf16_guess(raw[:SNIFF_BYTES]) or "utf-8", raw
    try:
        return body.decode(codec), False
    except UnicodeDecodeError:
        return body.decode(codec, errors="replace"), True


def _utf16_guess(sample: bytes) -> str | None:
    """UTF-16 without a byte-order mark: two or more NUL bytes, nine in ten on one side of the pairs.

    Line breaks alone give the pattern, so a list of CJK names is caught too.
    """
    even = sample[0::2].count(0)
    odd = sample[1::2].count(0)
    if even + odd < 2:
        return None
    if odd >= 9 * even:
        return "utf-16-le"
    if even >= 9 * odd:
        return "utf-16-be"
    return None


def _clean(text: str) -> str:
    """``text`` with controls dropped, lone surrogates replaced and white space collapsed.

    "" when nothing visible is left (white space and joiners only).
    """
    if text.isascii() and text.isprintable():
        return " ".join(text.split())  # the same result, faster
    text = _CONTROL.sub("", text)
    if _SURROGATE.search(text) is not None:
        text = _SURROGATE.sub("\ufffd", text)
    text = " ".join(part for part in text.translate(_WS_TABLE).split(" ") if part)
    return text if _VISIBLE.search(text) else ""


@dataclass(slots=True)
class _State:
    pasted: bool
    warnings: set[str] = field(default_factory=set)
    lines: int = 0
    hidden: int = 0
    too_long: int = 0
    dropped: int = 0

    def name(self, raw: str | None) -> str | None:
        """A kept name, or None for an empty, over-long or hidden one (counted)."""
        if raw is None:
            return None
        if not raw.isascii() and _SURROGATE.search(raw) is not None:
            self.warnings.add("decoding")  # a JSON escape held a lone surrogate
        text = _clean(raw)
        if not text:
            return None
        if len(text) > MAX_NAME:
            self.too_long += 1
            return None
        if text.startswith("."):
            self.hidden += 1
            return None
        return text

    def done(self, entries: list[tuple[str, Entry]], error: str | None = None) -> Parsed:
        unique = list(dict.fromkeys(entries))
        kinds = Counter(kind for kind, _ in unique)
        fmt = max(FORMATS, key=lambda f: (kinds[f], -FORMATS.index(f))) if unique else None
        if not unique and error is None:
            error = "no_names"
        if 0 < len(unique) < FEW_NAMES:
            self.warnings.add("few_names")
        if self.hidden:
            self.warnings.add("hidden_skipped")
        if (
            self.pasted
            and fmt in ("macos-text", "fc-list", "windows-registry", "lines")
            and _cut_off([e for kind, e in unique if kind == fmt])
        ):
            self.warnings.add("cut_off")
        counts = {
            "lines": self.lines,
            "entries": 0 if error else len(unique),
            "hidden": self.hidden,
            "too_long": self.too_long,
            "dropped": self.dropped,
        }
        return Parsed(
            entries=() if error else tuple(e for _, e in unique),
            format=None if error else fmt,
            counts=counts,
            warnings=tuple(w for w in WARNINGS if w in self.warnings),
            error=error,
        )


def _cut_off(entries: list[Entry]) -> bool:
    """A sorted paste of 500 or more names whose first name starts after C: its top is missing.

    Only names whose search key starts with an ASCII letter or digit count, so
    the comparison is the same in code points and in UTF-16 units. Sorted: at
    least nine in ten neighbouring pairs in order.
    """
    firsts: list[str] = []
    for entry in entries:
        first = search_key(entry.families[0] if entry.families else entry.full or "")[:1]
        if first in _ASCII_ALNUM:
            if not firsts and first < "d":
                return False  # the list starts before D, so its top is there
            firsts.append(first)
    if len(firsts) < CUT_OFF_LINES:
        return False
    in_order = sum(a <= b for a, b in itertools.pairwise(firsts))
    return 10 * in_order >= 9 * (len(firsts) - 1) and firsts[0] >= "d"


def parse(data: bytes | str) -> Parsed:
    """Entries from a dropped file (bytes) or a paste (text), in any format of docs/owned-fonts.md.

    It never raises: a list it can't read gives an error code (``ERRORS``) and no
    entries. In order:

    - bytes: over ``MAX_BYTES`` is ``too_big``; a font's magic number (with a NUL
      in the first 12 bytes) is ``font_file``; ZIP, PDF, PNG or gzip is
      ``binary``. Then UTF-8 or UTF-16 by byte-order mark, UTF-16 by NUL pattern
      (``_utf16_guess``), else UTF-8, all with replacement characters and the
      warning ``decoding``. Text: lone surrogates become U+FFFD (``decoding``).
    - over ``MAX_CHARS`` code points is ``too_big``; a NUL, or controls over 1%
      of the code points, is ``binary``; nothing visible is ``empty``; an HTML
      tag near the top is ``html``.
    - JSON (``_json_like``): ``system_profiler -json``'s typefaces (family,
      fullname, _name; trusted), or a JSON list of names (untrusted). A cut or
      broken document gives what it holds and the warning ``cut_off``.
    - "Family:" lines: ``system_profiler`` text (trusted).
    - otherwise line by line (``_parse_lines``), lines over ``MAX_LINE`` code
      points dropped (counted ``too_long``).

    A pasted sorted list of 500 or more names that starts after C warns
    ``cut_off`` (a terminal's scrollback cut its top); a dropped file never does,
    except for broken JSON, where the cut is certain rather than guessed.
    """
    state = _State(pasted=isinstance(data, str))
    if isinstance(data, bytes | bytearray):
        raw = bytes(data)
        if len(raw) > MAX_BYTES:
            return state.done([], "too_big")
        if raw[:4] in _FONT_MAGIC and 0 in raw[:12]:
            return state.done([], "font_file")
        if raw.startswith(_BINARY_MAGIC):
            return state.done([], "binary")
        text, replaced = _decode(raw)
    elif isinstance(data, str):
        if len(data) > MAX_CHARS:
            return state.done([], "too_big")
        replaced = _SURROGATE.search(data) is not None
        text = _SURROGATE.sub("\ufffd", data) if replaced else data
    else:
        return state.done([], "empty")
    if replaced:
        state.warnings.add("decoding")
    if len(text) > MAX_CHARS:
        return state.done([], "too_big")
    text = text.removeprefix("\ufeff")
    if "\x00" in text or len(_CONTROL.findall(text)) * 100 > len(text):
        return state.done([], "binary")
    if _VISIBLE.search(text) is None:
        return state.done([], "empty")
    body = text.lstrip(_BLANK_CHARS)
    if _HTML.search(body[:SNIFF_HTML].translate(_ASCII_LOWER)):
        return state.done([], "html")
    lines = _LINE_BREAK.split(text)
    state.lines = sum(1 for line in lines if _VISIBLE.search(line))
    if _json_like(body, lines):
        return _parse_json(text, state)
    if any(_FAMILY_LINE.fullmatch(line) for line in lines):
        return _parse_macos_text(lines, state)
    return _parse_lines(lines, state)


# --- JSON (system_profiler -json SPFontsDataType, or a list of names) ---


def _json_like(body: str, lines: list[str]) -> bool:
    if body[:1] in ("{", "[") and (
        _JSON_MEMBER.search(body[:SNIFF_JSON]) or _JSON_ARRAY.match(body)
    ):
        return True
    head = [line for line in lines[:200] if _VISIBLE.search(line)][:50]
    return len(head) >= 3 and 2 * sum(bool(_JSON_LINE.fullmatch(line)) for line in head) >= len(
        head
    )


def _json_string(token: str) -> str | None:
    """A whole JSON string token decoded, or None when its escapes are bad."""
    try:
        return json.loads(token)
    except ValueError:
        return None


def _json_tokens(text: str) -> list[str]:
    """``_JSON_TOKEN``'s tokens of ``text`` up to and with the first lone '"', where
    ``_scan_json`` stops: a string that the text or a control character ends before it closes.

    Found one at a time, so a run of escaped quotes after such a quote is read once;
    ``findall`` would rescan from every quote to the end (quadratic: 9 s for 80 KB).
    """
    tokens: list[str] = []
    for found in _JSON_TOKEN.finditer(text):
        tokens.append(found.group())
        if tokens[-1] == '"':
            break
    return tokens


def _json_values(text: str) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Every JSON object (its string members) and every listed string, in opening order, and
    whether the text was cut: ``_scan_json``'s, for valid JSON too.

    ``json.loads`` keeps only the last of duplicate keys, so a walk of its result loses the
    objects and lists inside the earlier ones, and when it raises (an integer of more than
    4,300 digits, nesting deeper than the interpreter allows) depends on the platform. The
    port uses the same scanner, so both sides read every text alike.
    """
    return _scan_json(text)


def _scan_json(text: str) -> tuple[list[dict[str, Any]], list[str], bool]:
    """The objects and listed strings of a JSON text, whole, cut or broken, in opening order.

    Members before the first "{" (a paste cut at the top) belong to an implicit
    object, which a stray "}" closes; strings there count as listed. A member
    whose value is no string removes the key, as a later duplicate would.
    """
    tokens = _json_tokens(text)
    root: dict[str, Any] = {}
    out: list[dict[str, Any]] = [root]
    strings: list[str] = []
    stack: list[dict[str, Any] | None] = [root]  # None marks an array
    cut = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "{":
            obj: dict[str, Any] = {}
            out.append(obj)
            stack.append(obj)
        elif token == "[":
            stack.append(None)
        elif token == "}":
            while len(stack) > 1 and stack[-1] is None:
                stack.pop()
            if len(stack) > 1:
                stack.pop()
            else:
                cut = True
                stack[0] = {}
                out.append(stack[0])
        elif token == "]":
            if len(stack) > 1 and stack[-1] is None:
                stack.pop()
        elif token == '"':
            cut = True  # an unclosed string: the text ends inside it
            break
        elif token[0] == '"' and i + 1 < len(tokens) and tokens[i + 1] == ":":
            top = stack[-1]
            key = _json_string(token)
            value = tokens[i + 2] if i + 2 < len(tokens) else None
            if top is not None and key is not None:
                if value is not None and len(value) > 1 and value[0] == '"':
                    decoded = _json_string(value)
                    if decoded is None:
                        top.pop(key, None)
                    else:
                        top[key] = decoded
                    i += 3
                    continue
                top.pop(key, None)
            i += 2
            continue
        elif token[0] == '"' and (stack[-1] is None or len(stack) == 1):
            if (decoded := _json_string(token)) is not None:
                strings.append(decoded)
        i += 1
    cut = cut or len(stack) > 1 or bool(root)
    return [obj for obj in out if obj], strings, cut


def _parse_json(text: str, state: _State) -> Parsed:
    objects, strings, cut = _json_values(text)
    if cut:
        state.warnings.add("cut_off")
    entries: list[tuple[str, Entry]] = []
    for obj in objects:
        if isinstance(obj.get("path"), str):
            state.warnings.add("paths_dropped")
        family = obj.get("family")
        if not isinstance(family, str):
            continue
        name = state.name(family)
        if name is None:
            continue
        full, ps = obj.get("fullname"), obj.get("_name")
        entry = Entry(
            families=(name,),
            full=state.name(full) if isinstance(full, str) else None,
            postscript=state.name(ps) if isinstance(ps, str) else None,
            trusted=True,
        )
        entries.append(("macos-json", entry))
    if not entries:
        # A list of names (ConvertTo-Json of a one-per-line command, for example).
        kept = (state.name(item) for item in strings)
        entries.extend(("json-list", Entry(families=(name,))) for name in kept if name is not None)
    return state.done(entries)


# --- system_profiler text, fc-list, the registry and one name per line ---


def _parse_macos_text(lines: list[str], state: _State) -> Parsed:
    entries: list[tuple[str, Entry]] = []
    for line in lines:
        if len(line) > MAX_LINE:
            state.too_long += 1
        elif m := _FAMILY_LINE.fullmatch(line):
            if (name := state.name(m["name"])) is not None:
                entries.append(("macos-text", Entry(families=(name,), trusted=True)))
        elif _LOCATION_LINE.match(line):
            state.warnings.add("paths_dropped")
        elif _VISIBLE.search(line):
            state.dropped += 1
    return state.done(entries)


def _split_fc(text: str) -> tuple[list[str], bool]:
    """``text`` split at fontconfig's commas and unescaped, and whether fontconfig wrote it.

    A comma splits unless a backslash escapes it or a space follows it
    (fontconfig never writes ", "). ``\\\\``, ``\\,``, ``\\:`` and ``\\-`` are
    fontconfig's escapes; any other backslash stays. Fontconfig wrote the text
    when it split or held an escape.
    """
    if "," not in text and "\\" not in text:
        return [text], False
    parts: list[str] = []
    current: list[str] = []
    written = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] in "\\,:-":
            current.append(text[i + 1])
            written = True
            i += 2
            continue
        if c == "," and (i + 1 == n or text[i + 1] != " "):
            parts.append("".join(current))
            current = []
            written = True
        else:
            current.append(c)
        i += 1
    parts.append("".join(current))
    return parts, written


def _before_unescaped(text: str, sep: str) -> str:
    """``text`` up to its first ``sep`` that no backslash escapes."""
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == sep:
            return text[:i]
        i += 1
    return text


def _is_command(line: str) -> bool:
    """Whether a cleaned line starts with a command of docs/owned-fonts.md ("sudo fc-list:family")."""
    m = _COMMAND.match(line)
    return m is not None and m["word"].translate(_ASCII_LOWER) in COMMAND_WORDS


def _registry_name(line: str, state: _State) -> str | None:
    """The value name of a registry line ("Arial (TrueType)", "Odd Name : odd.ttf"), or None.

    The suffix is found from the last "(" backwards, so a line costs linear time.
    """
    i = line.rfind("(")
    while i > 0:
        if _REGISTRY_SUFFIX.fullmatch(line, i):
            data = line[line.index(")", i) :]
            if "\\" in data or "/" in data:
                state.warnings.add("paths_dropped")
            return line[:i].rstrip(" ")
        i = line.rfind("(", 0, i)
    name, colon, data = line.partition(" : ")
    if colon and name and data.rpartition(".")[2].translate(_ASCII_LOWER) in FONT_EXTENSIONS:
        if "\\" in data or "/" in data:
            state.warnings.add("paths_dropped")
        return name
    return None


def _families(state: _State, raw: list[str]) -> tuple[str, ...] | None:
    """The kept names of one font, or None when its first name is hidden or none is kept."""
    if raw and _clean(raw[0]).startswith("."):
        state.hidden += 1
        return None
    names = tuple(n for n in (state.name(r) for r in raw) if n is not None)
    return tuple(dict.fromkeys(names)) or None


def _parse_lines(lines: list[str], state: _State) -> Parsed:
    """fc-list (comma-joined or plain), the registry, or one name per line.

    Dropped lines: PowerShell table rules with the header above them, "Hive:",
    PSPath and its siblings, error records, prompts, commands and not-found
    errors. In a list with more registry lines than plain ones, the plain lines
    are value names without a suffix ("Courier 10,12,15", "Small Fonts"), kept
    whole. Otherwise a list is fc-list's (``trusted``) when at least one plain
    line in ``FC_LIST_ONE_IN`` split at a fontconfig comma or held an escape;
    commas split either way, so one stray "Foo,Bar" changes no other line's trust.
    """
    items: list[list[Any] | None] = []  # [kind, payload]; None once dropped
    previous: int | None = None  # the item of the previous visible line
    not_found = command = False
    for raw in lines:
        if len(raw) > MAX_LINE:
            state.too_long += 1
            previous = None
            continue
        stripped = raw.strip(_BLANK_CHARS)
        table = _TABLE_KEY.match(stripped)
        line = _clean(stripped[table.end() :] if table else stripped)
        if not line:
            continue
        here: int | None = None
        if _DASH_RULE.fullmatch(line):
            # A PowerShell table rule: the line above it, with as many words, is the header.
            item = items[previous] if previous is not None else None
            if item is not None and item[0] == "plain" and item[1].count(" ") == line.count(" "):
                items[previous] = None
                state.dropped += 1
            state.dropped += 1
        elif (
            line.startswith("Hive:")
            or _PS_PROPERTY.match(line)
            or _ERROR_DETAIL.match(line)
            or _CARETS.fullmatch(line)
            or "Check the spelling of the name" in line
        ):
            state.dropped += 1
        elif _NOT_FOUND.search(line):
            not_found = True
            state.dropped += 1
        elif (m := _PROMPT.fullmatch(line)) is not None or line in _LONE_PROMPT:
            state.warnings.add("prompt_dropped")
            command = command or (m is not None and _is_command(m["rest"]))
            state.dropped += 1
        elif (m := _BARE_PROMPT.fullmatch(line)) is not None and _is_command(m["rest"]):
            state.warnings.add("prompt_dropped")
            command = True
            state.dropped += 1
        elif _is_command(line):
            command = True
            state.dropped += 1
        elif _PATH.match(line):
            # Plain fc-list: "path: Family,Other:style=Bold"; the path goes at once.
            state.warnings.add("paths_dropped")
            _, colon, rest = line.partition(": ")
            if colon:
                here = len(items)
                items.append(["fc-list-plain", _split_fc(_before_unescaped(rest, ":"))[0]])
            else:
                state.dropped += 1
        elif (name := _registry_name(line, state)) is not None:
            here = len(items)
            items.append(["windows-registry", name.split(" & ")])
        else:
            here = len(items)
            items.append(["plain", line])
        previous = here
    kinds = Counter(item[0] for item in items if item is not None)
    written = plain = 0
    for item in items:
        if item is None or item[0] != "plain":
            continue
        if kinds["windows-registry"] > kinds["plain"]:
            item[0], item[1] = "windows-registry", item[1].split(" & ")
            continue
        item[1], by_fontconfig = _split_fc(item[1])
        plain += 1
        written += by_fontconfig
    fc_list = written > 0 and written * FC_LIST_ONE_IN >= plain
    entries: list[tuple[str, Entry]] = []
    for item in items:
        if item is None:
            continue
        kind, payload = item
        if kind == "plain":
            if names := _families(state, payload):
                entries.append(
                    ("fc-list" if fc_list else "lines", Entry(families=names, trusted=fc_list))
                )
        elif kind == "fc-list-plain":
            if names := _families(state, payload):
                entries.append(("fc-list-plain", Entry(families=names, trusted=True)))
        else:
            kept = (state.name(part) for part in payload)
            entries.extend(
                ("windows-registry", Entry(full=name)) for name in kept if name is not None
            )
    if not_found and len(entries) < FEW_NAMES:
        return state.done([], "not_found")
    if not entries and command:
        return state.done([], "command")
    return state.done(entries)


# --- matching ----------------------------------------------------------------------------------

HOW = ("family", "postscript", "build", "rename", "patched")
WHY = ("ambiguous", "patched", "sibling", "related", "bundle")
OUTCOMES = ("owned", "near", "known", "ineligible", "unknown")
_HOW = {"": "family", "p": "postscript", "b": "build", "r": "rename", "n": "patched"}
_WHY = {"n": "patched", "s": "sibling", "x": "related", "u": "bundle"}
_SAME_FONT = frozenset({"", "p", "b", "r"})  # codes that name the font itself
_CODE_LETTERS = frozenset("pbrnusx")


@dataclass(frozen=True, slots=True)
class _Code:
    family: str | None = None  # a catalog id
    letter: str = ""
    known: bool = False  # "-": a known family that isn't listed
    reason: str | None = None  # "!j": an ineligible name


def _number(text: str, size: int) -> int:
    """A code's index: ASCII digits without a leading zero, below ``size``; else ValueError."""
    if not (text.isascii() and text.isdigit()) or (len(text) > 1 and text[0] == "0"):
        raise ValueError(f"bad index {text!r}")
    if int(text) >= size:
        raise ValueError(f"index {text} out of range (size {size})")
    return int(text)


def _parse_code(code: str, ids: tuple[str, ...], reasons: tuple[str, ...]) -> _Code:
    if code == "-":
        return _Code(known=True)
    if code[:1] == "!":
        return _Code(reason=reasons[_number(code[1:], len(reasons))])
    letter = code[-1] if code[-1:] in _CODE_LETTERS else ""
    return _Code(family=ids[_number(code[: len(code) - len(letter)], len(ids))], letter=letter)


def _is_number(value: Any) -> bool:
    """A JSON number: an int or a float, never a bool (``True == 1`` in Python)."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_integer(value: Any) -> bool:
    """A JSON number with an integral value: 0, 0.0 and 1e0 alike, as after ``JSON.parse``."""
    return _is_number(value) and (isinstance(value, int) or value.is_integer())


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


class Table:
    """A match document, ready to match against, with its rules compiled and its candidates memoized."""

    def __init__(
        self,
        ids: tuple[str, ...],
        k: Mapping[str, tuple[_Code, ...]],
        amb: Mapping[str, tuple[str, ...]],
        words: Mapping[str, Any],
    ) -> None:
        self.ids = ids
        self.k = k
        self.amb = amb
        self.words = words
        self._rules = _DEFAULT_RULES if words is WORDS else _compile(words)
        self._candidates: dict[str, tuple[Candidate, ...]] = {}

    @classmethod
    def from_document(cls, doc: Any) -> Table:
        """The table of a match document (``match_document``'s output, or its JSON).

        ValueError for anything malformed, exactly as the port's ``OwnedMatch.table``: not
        an object; a wrong format; ``v`` not the number 1; ``ids`` or ``reasons`` no list of
        strings; ``k`` or ``amb`` no object; a ``k`` value no string, or a bad code or index
        in it; an ``amb`` key missing from ``k``, or its value no list of integers below
        ``len(ids)``; bad words (``_compile``). Numbers are compared as JSON numbers, which
        is all ``JSON.parse`` leaves the port: 1 and 1.0 are the same, and ``true`` is no 1.
        """
        if not isinstance(doc, dict) or doc.get("format") != FORMAT:
            raise ValueError(f"not a {FORMAT} document")
        if not _is_number(doc.get("v")) or doc["v"] != VERSION:
            raise ValueError(f"not version {VERSION}")
        ids, reasons = doc.get("ids"), doc.get("reasons")
        if not _strings(ids) or not _strings(reasons):
            raise ValueError("ids and reasons must be lists of strings")
        ids, reasons = tuple(ids), tuple(reasons)
        k_doc, amb_doc = doc.get("k"), doc.get("amb")
        if not isinstance(k_doc, dict) or not isinstance(amb_doc, dict):
            raise ValueError("k and amb must be objects")
        k: dict[str, tuple[_Code, ...]] = {}
        for key, codes in k_doc.items():
            if not isinstance(key, str) or not isinstance(codes, str):
                raise ValueError(f"bad entry {key!r}: {codes!r}")
            k[key] = tuple(_parse_code(code, ids, reasons) for code in codes.split(","))
        amb: dict[str, tuple[str, ...]] = {}
        for key, indices in amb_doc.items():
            if key not in k:
                raise ValueError(f"amb key {key!r} is not in k")
            if not isinstance(indices, list) or not all(_is_integer(i) for i in indices):
                raise ValueError(f"amb {key!r}: indices must be integers")
            amb[key] = tuple(ids[_number(str(int(i)), len(ids))] for i in indices)
        return cls(ids, k, amb, doc.get("words"))

    def candidates(self, name: str) -> tuple[Candidate, ...]:
        """``candidates(name)`` under this document's words, memoized."""
        found = self._candidates.get(name)
        if found is None:
            found = self._candidates[name] = _chain(name, self._rules)
        return found


@dataclass(frozen=True, slots=True)
class Result:
    """Owned and near-match catalog families, and every entry's outcome counted."""

    owned: Mapping[str, Mapping[str, str]]  # id -> {"as": name, "how": HOW}
    near: Mapping[str, Mapping[str, str]]  # id -> {"as": name, "why": WHY}; never owned
    counts: Mapping[str, Any]  # entries and OUTCOMES, plus "reasons": {reason: n}

    def to_json(self) -> dict[str, Any]:
        return {"owned": dict(self.owned), "near": dict(self.near), "counts": dict(self.counts)}


def _tries(entry: Entry) -> Iterator[tuple[str, bool]]:
    """(name, trusted) in matching order.

    A PostScript name is no more trusted than any other: only its family part before its one
    hyphen is, through the candidate (``Candidate.postscript``), so "ArchivoBlack-Regular"
    can own Archivo Black and "Archivo-Black", static Archivo's Black weight, can't.
    """
    for n, family in enumerate(entry.families):
        yield family, entry.trusted and n == 0
    if entry.full:
        yield entry.full, False
    if entry.postscript:
        yield entry.postscript, False


def _hit(entry: Entry, table: Table) -> tuple[str, Candidate, tuple[_Code, ...], bool] | None:
    for name, trusted in _tries(entry):
        for cand in table.candidates(name):
            codes = table.k.get(cand.key)
            if codes is not None:
                return name, cand, codes, trusted or cand.postscript
    return None


def _keep(best: dict[str, tuple[int, int, str]], fid: str, rank: tuple[int, int, str]) -> None:
    if fid not in best or rank < best[fid]:
        best[fid] = rank


def match(parsed: Parsed | Iterable[Entry], table: Table, *, patched: bool = True) -> Result:
    """Which catalog families ``parsed``'s entries own, and which they only nearly match.

    Per entry, names are tried in order (first family, other families, full name,
    PostScript name), each with its candidates longest first; the first
    candidate that is a known key decides the entry, a known family that isn't
    listed and an ineligible name included.

    - Owned: the family, a PostScript name, a same-font build, a rename (M3-D3 a),
      and a patched build while ``patched`` (M3-D4 a). A family, rename,
      PostScript or build hit reached by stripping a Nerd, CN, PL or NL suffix is
      a patched build.
    - Near: a sibling, related or bundle name (M3-D5 a), with its own reason; a
      patched build with ``patched`` off ("patched"); and "ambiguous": on an
      untrusted entry, an ambiguous key's owned codes and the families its name
      may be a weight of; on any entry, those families when the key names no
      catalog family but a known one that isn't listed (Bungee Hairline).
    - Counted only: a known family that isn't listed, an ineligible name (by
      reason, also when its key is ambiguous and the entry trusted), unknown.

    An ambiguous key counts as owned only as a trusted name: fc-list's first
    name, a macOS family, or the family part before a PostScript name's one hyphen
    ("ArchivoBlack-Regular", in any list; not "Archivo-Black" or "Archivo-Black-Bold";
    Milestone 3 step 5). Owned beats near; per family the
    first ``HOW`` (or ``WHY``) in order wins, then the first entry.
    """
    entries = parsed.entries if isinstance(parsed, Parsed) else tuple(parsed)
    owned: dict[str, tuple[int, int, str]] = {}
    near: dict[str, tuple[int, int, str]] = {}
    counts = dict.fromkeys(OUTCOMES, 0)
    reasons: Counter[str] = Counter()
    for n, entry in enumerate(entries):
        hit = _hit(entry, table)
        if hit is None:
            counts["unknown"] += 1
            continue
        shown, cand, codes, trusted = hit
        amb = table.amb.get(cand.key, ())
        doubt = bool(amb) and not trusted
        got_owned: dict[str, str] = {}
        got_near: dict[str, str] = {}
        known = has_family = False
        why: set[str] = set()
        for code in codes:
            if code.family is None:
                known = known or code.known
                if code.reason is not None:
                    why.add(code.reason)
                continue
            has_family = True
            letter = "n" if cand.patched and code.letter in _SAME_FONT else code.letter
            if letter == "n" and not patched:
                got_near[code.family] = "patched"
            elif letter in _HOW:
                if doubt:
                    got_near[code.family] = "ambiguous"
                else:
                    got_owned[code.family] = _HOW[letter]
            else:
                got_near[code.family] = _WHY[letter]
        if amb and (doubt or (known and not has_family)):
            for fid in amb:
                if fid not in got_owned:
                    got_near[fid] = "ambiguous"  # first in WHY, so it wins over the code's own
        for fid, how in got_owned.items():
            _keep(owned, fid, (HOW.index(how), n, shown))
        for fid, reason in got_near.items():
            _keep(near, fid, (WHY.index(reason), n, shown))
        if got_owned:
            counts["owned"] += 1
        elif got_near:
            counts["near"] += 1
        elif known:
            counts["known"] += 1
        elif why:
            counts["ineligible"] += 1
            reasons.update(why)
        else:
            counts["unknown"] += 1
    return Result(
        owned={fid: {"as": owned[fid][2], "how": HOW[owned[fid][0]]} for fid in sorted(owned)},
        near={
            fid: {"as": near[fid][2], "why": WHY[near[fid][0]]}
            for fid in sorted(near)
            if fid not in owned
        },
        counts={"entries": len(entries), **counts, "reasons": dict(sorted(reasons.items()))},
    )
