"""Owned-font matching for the hopper: the match document, the parser and the matcher.

The hopper (Milestone 3, slice 1; owner rulings of 2026-10-21 to 2026-10-23,
``inventory_slice_1_defaults``, ``inventory_match_data_site_build`` and
``hopper_hackgen_related``) takes a visitor's list of installed fonts in the
browser and hides the catalog families they already have. This module is the
reference that the site's JavaScript port must agree with. A visitor's list
never leaves the browser, so this code reads real lists only in tests.

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

Rules that keep the port in step:

- Rules are ASCII-only, with no ``\\s``, ``\\d``, ``\\w`` or Unicode case folding.
  Case-insensitive rules fold ASCII letters only (Python ``re.ASCII |
  re.IGNORECASE``, JavaScript ``/i`` without ``u``), and names are NFKC-normalized
  first, so full-width letters still match. Python's ``\\Z`` is JavaScript's
  ``$`` without the m flag; line patterns use DOTALL (JavaScript ``s``), because
  a line may hold U+2028.
- No optional group holds a capture that the code reads: JavaScript gives
  ``undefined`` where Python gives ``""``.

Standard library only (``keys``, ``names`` and ``jsonio``), so ``tff_site`` can
import it.
"""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tff_catalog import jsonio
from tff_catalog.keys import (
    DASH_CODEPOINTS,
    JOINER_CODEPOINTS,
    WHITESPACE_CODEPOINTS,
    match_key,
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
    "style": sorted(STYLE_WORDS),  # a whole word, or every camel-case part (BoldItalic)
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
    postscript: bool = False  # a PostScript style part was stripped (ArchivoBlack-Regular)


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
    kind = _style_token(tokens[-1], rules)
    if kind is None:
        return None
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
    while (step := _strip(text, rules)) is not None:
        text, kind = step
        patched = patched or kind == "patched"
        postscript = postscript or kind == "postscript"
        out.append(Candidate(match_key(text), text, patched, postscript))
    return tuple(out)


def candidates(name: str, words: Mapping[str, Any] = WORDS) -> tuple[Candidate, ...]:
    """``name``'s ``match_key``, then one trailing unit stripped at a time, longest first.

    Units, tried in this order at the end of the name: a Nerd phrase ("Nerd Font",
    "Nerd Font Mono", "Nerd Font Propo", any case, separators optional) or token
    (NF, NFM, NFP after a separator, lower-case letter or digit); CN, NL or PL after
    a separator; Variable, Var or VF after a separator, or "Variable" right after a
    lower-case letter; in a name without spaces, a style part after the last hyphen
    (PostScript form); a style word, with the modifiers before it (Extra Light).
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
