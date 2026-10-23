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

Rules that keep the port in step:

- Rules are ASCII-only, with no ``\\s``, ``\\d``, ``\\w`` or Unicode case folding.
  Case-insensitive rules fold ASCII letters only (Python ``re.ASCII |
  re.IGNORECASE``, JavaScript ``/i`` without ``u``), and names are NFKC-normalized
  first, so full-width letters still match. Python's ``\\Z`` is JavaScript's
  ``$`` without the m flag; line patterns use DOTALL (JavaScript ``s``), because
  a line may hold U+2028.
- No optional group holds a capture that the code reads: JavaScript gives
  ``undefined`` where Python gives ``""``.

Standard library only (``keys`` and ``names``), so ``tff_site`` can import it.
"""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

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
