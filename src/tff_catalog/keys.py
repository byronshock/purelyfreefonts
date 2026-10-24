"""Name keys shared by the catalog, the site search and owned-font matching.

The shared vector file ``tests/vectors/name-keys.json`` is the source of truth.
This module and the site's JavaScript port must both reproduce every case in it.

- ``match_key(s)``: NFKC, casefold, NFKC, drop every code point in
  ``DROP_CODEPOINTS``, NFC. Accents are kept: ``"Łódź Sans" -> "łódźsans"``.
- ``search_key(s)``: ``match_key(s)``, NFD, drop general category Mn, NFC.
  Accents are stripped: ``"Łódź Sans" -> "łodzsans"``.

Both functions are idempotent and depend only on the standard library, so the
site build can import this module without the pipeline's dependencies.

JavaScript has no casefold: the port uses the vector file's
``spec.casefold_extra`` table for the code points where Python's casefold
differs from ``toLowerCase()`` (Cherokee, Greek iota subscripts, old Cyrillic
variants and a few more), and ``toLowerCase()`` for the rest, with sharp s folded
to "ss" and final sigma to medial sigma.
"""

import unicodedata

# Whitespace, hyphens and dashes, underscores and invisible joiners. Entries are
# hex code points or inclusive ranges. The four groups are public because the
# hopper (``owned``, Milestone 3) treats them differently before it takes keys:
# white space separates words, a dash is read as "-", joiners are removed.
WHITESPACE_CODEPOINTS: tuple[str, ...] = (
    "0009-000D",
    "0020",
    "0085",
    "00A0",
    "1680",
    "2000-200A",
    "2028",
    "2029",
    "202F",
    "205F",
    "3000",
)
DASH_CODEPOINTS: tuple[str, ...] = ("002D", "00AD", "2010-2014", "2212", "FE63", "FF0D")
UNDERSCORE_CODEPOINTS: tuple[str, ...] = ("005F",)
JOINER_CODEPOINTS: tuple[str, ...] = ("200B-200D", "2060", "FEFF")
# Their concatenation, in the same order as the vector file's spec.drop_codepoints;
# a test asserts the two are equal.
DROP_CODEPOINTS: tuple[str, ...] = (
    WHITESPACE_CODEPOINTS + DASH_CODEPOINTS + UNDERSCORE_CODEPOINTS + JOINER_CODEPOINTS
)


def expand_codepoints(entries: tuple[str, ...] | list[str]) -> frozenset[int]:
    """Return every code point named by hex entries such as ``"0020"`` or ``"2000-200A"``."""
    points: set[int] = set()
    for entry in entries:
        first, _, last = entry.partition("-")
        points.update(range(int(first, 16), int(last or first, 16) + 1))
    return frozenset(points)


_DROP_TABLE = dict.fromkeys(expand_codepoints(DROP_CODEPOINTS))


def match_key(s: str) -> str:
    """Return the exact-match key of a font name: case, width and spacing folded, accents kept."""
    folded = unicodedata.normalize("NFKC", s).casefold()
    dropped = unicodedata.normalize("NFKC", folded).translate(_DROP_TABLE)
    # Dropping a joiner or space can leave a base letter next to a combining mark;
    # NFC recomposes them so the key is idempotent.
    return unicodedata.normalize("NFC", dropped)


def search_key(s: str) -> str:
    """Return the search key of a font name: ``match_key`` with accents stripped."""
    decomposed = unicodedata.normalize("NFD", match_key(s))
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", stripped)
