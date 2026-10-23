"""Writes the hopper's shared vectors and the fixtures' expectations (Milestone 3, slice 1).

``uv run python -m tests.helpers.owned_vectors`` rewrites, from ``tff_catalog.owned``:

- ``tests/vectors/owned-fonts.json``: the constants (``spec``); a synthetic names.json
  (``names``, ``catalog_ids``, ``run_date``) and its match document (``document``); parse,
  candidates and match cases with the results owned.py gives; and match files it must refuse
  (``malformed``). site/js's port must give the same results (PR 3).
- ``tests/vectors/owned-known-answers.json``: the known answers on the real
  ``build/names.json``. These are expectations written here by hand, not results.
- ``tests/fixtures/owned/expected.json``: each fixture's parse, as a dropped file.

Every input is synthetic. tests/test_owned.py checks each file against owned.py, so a change
of behaviour fails there until the files are rewritten and the diff reviewed. Drawn cases come
from a seeded numpy generator, so a rewrite draws the same inputs.

A parse input is spelled ``{"text"}``, ``{"base64"}`` (a dropped file), ``{"units"}`` (UTF-16
code units, for a text with a lone surrogate, which JSON text can't carry) or ``{"parts":
[[text, times], ...], "bytes"}`` (each text repeated and joined; how 10 MB fits in a line).
"""

import base64
import codecs
import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from tff_catalog import owned

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "tests" / "vectors" / "owned-fonts.json"
KNOWN = ROOT / "tests" / "vectors" / "owned-known-answers.json"
FIXTURES = ROOT / "tests" / "fixtures" / "owned"
EXPECTED = FIXTURES / "expected.json"
SEED = 20261023

_LONE = re.compile("[\ud800-\udfff]")
_PAIR = re.compile("[\ud800-\udbff][\udc00-\udfff]")


def spelled(data: bytes | str) -> dict[str, Any]:
    """A parse input as the vector file spells it; ValueError for a surrogate pair held as
    two code points, which JavaScript would read as one character."""
    if isinstance(data, bytes):
        return {"base64": base64.b64encode(data).decode("ascii")}
    if _PAIR.search(data):
        raise ValueError("a surrogate pair held as two code points has no JavaScript form")
    if _LONE.search(data):
        raw = data.encode("utf-16-le", "surrogatepass")
        return {"units": [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]}
    return {"text": data}


def data_of(spelling: dict[str, Any]) -> bytes | str:
    """The value a spelled parse input stands for (``spelled``, or ``parts``)."""
    if "base64" in spelling:
        return base64.b64decode(spelling["base64"])
    if "parts" in spelling:
        if any(_LONE.search(text) for text, _ in spelling["parts"]):
            raise ValueError("parts must hold no surrogate")
        text = "".join(text * times for text, times in spelling["parts"])
        return text.encode("utf-8") if spelling.get("bytes") else text
    if "units" in spelling:
        raw = b"".join(unit.to_bytes(2, "little") for unit in spelling["units"])
        return raw.decode("utf-16-le", "surrogatepass")
    return spelling["text"]


def entry_of(e: dict[str, Any]) -> owned.Entry:
    return owned.Entry(tuple(e["families"]), e["full"], e["postscript"], e["trusted"])


# ---------------------------------------------------------------- the synthetic names.json


def _family(
    fid: str,
    family: str,
    names: tuple[tuple[str, ...], ...] = (),
    *,
    in_catalog: bool = True,
    distinct: tuple[str, ...] = (),
) -> dict[str, Any]:
    rows = []
    for name, relation, *detail in names:
        row: dict[str, Any] = {"name": name, "relation": relation}
        if relation == "build":
            row["detail"] = detail[0]
        rows.append(row)
    return {
        "id": fid,
        "family": family,
        "in_catalog": in_catalog,
        "superfamily_id": None,
        "names": rows,
        "distinct_from": list(distinct),
    }


RUN_DATE = "2026-01-01"
# Foo Sans has every relation; Foo Sans Black is a family and Foo Sans's weight (amb);
# Foo Sans Hairline is known but not listed; Foo Sans Heavy is ineligible and a weight too.
NAMES = {
    "schema_version": "0.1.0-draft",
    "run": {"date": RUN_DATE, "method_version": RUN_DATE},
    "families": [
        _family(
            "bar-serif", "Bar Serif", (("Bar Serif Display", "sibling"), ("Bar Mono", "related"))
        ),
        _family(
            "foo-sans",
            "Foo Sans",
            (
                ("Foo Sans Pro", "rename"),
                ("FSans", "postscript"),
                ("Foo Sans Variable", "build", "variable"),
                ("Foo Sans Text", "build", ""),
                ("Foo Sans NF", "build", "nf"),
                ("Foo Family", "bundle"),
            ),
            distinct=("foo-sans-tight",),
        ),
        _family("foo-sans-black", "Foo Sans Black"),
        _family(
            "foo-sans-hairline",
            "Foo Sans Hairline",
            (("Foo Hairline", "rename"),),
            in_catalog=False,
        ),
        _family("foo-sans-tight", "Foo Sans Tight", distinct=("foo-sans",)),
    ],
    "ineligible": [
        {"name": "Foo Sans Heavy", "reason": "proprietary"},
        {"name": "Foo Sans JP", "reason": "cjk"},
    ],
}
CATALOG_IDS = ["bar-serif", "foo-sans", "foo-sans-black", "foo-sans-tight"]


def _e(
    families: tuple[str, ...] = (),
    full: str | None = None,
    ps: str | None = None,
    trusted: bool = False,
) -> owned.Entry:
    return owned.Entry(families, full, ps, trusted)


def match_cases() -> list[tuple[str, tuple[owned.Entry, ...]]]:
    """The matcher on the synthetic table: (label, entries); each runs with patched on and off."""
    return [
        ("family", (_e(("Foo Sans",), trusted=True),)),
        ("rename with styles", (_e(full="Foo Sans Pro Bold Italic"),)),
        ("postscript alias", (_e(ps="FSans-Bold"),)),
        ("build", (_e(("Foo Sans Text",), trusted=True),)),
        ("listed patched build", (_e(("Foo Sans NF",), trusted=True),)),
        ("stripped Nerd phrase", (_e(("FooSans Nerd Font Mono",), trusted=True),)),
        ("NF CN", (_e(("Foo Sans NF CN",), trusted=True),)),
        ("variable build, joined", (_e(("FooSansVariable",), trusted=True),)),
        ("ambiguous, trusted", (_e(("Foo Sans Black",), trusted=True),)),
        ("ambiguous, untrusted", (_e(full="Foo Sans Black"),)),
        ("ambiguous, untrusted, styled", (_e(full="Foo Sans Black Italic"),)),
        ("ambiguous, untrusted, camel-case styles", (_e(full="Foo Sans BlackItalic"),)),
        ("ambiguous, untrusted, hyphenated styles", (_e(full="Foo Sans Black-Regular"),)),
        ("ambiguous, trusted, camel-case styles", (_e(("Foo Sans BlackItalic",), trusted=True),)),
        ("known not listed, hyphenated styles", (_e(full="Foo Sans Hairline-Regular"),)),
        ("ambiguous, PostScript", (_e(ps="FooSansBlack-Regular"),)),
        ("ambiguous, PostScript of a weight", (_e(ps="FooSans-Black"),)),
        ("ambiguous, PostScript with two hyphens", (_e(ps="Foo-Sans-Black-Bold"),)),
        ("ambiguous, PostScript without a hyphen", (_e(ps="FooSansBlack"),)),
        ("ambiguous, PostScript form in a list", (_e(("FooSansBlack-Bold",)),)),
        ("ambiguous patched, trusted", (_e(("Foo Sans Black NF",), trusted=True),)),
        ("ambiguous patched, untrusted", (_e(full="Foo Sans Black NF"),)),
        ("known not listed, ambiguous, trusted", (_e(("Foo Sans Hairline",), trusted=True),)),
        ("known not listed, ambiguous, untrusted", (_e(full="Foo Sans Hairline Italic"),)),
        ("known not listed", (_e(("Foo Hairline",), trusted=True),)),
        ("ineligible, ambiguous, trusted", (_e(("Foo Sans Heavy",), trusted=True),)),
        ("ineligible, ambiguous, untrusted", (_e(full="Foo Sans Heavy"),)),
        ("ineligible stops the chain", (_e(full="Foo Sans JP Bold"),)),
        ("sibling", (_e(("Bar Serif Display",), trusted=True),)),
        ("sibling stays sibling when patched", (_e(("Bar Serif Display NF",), trusted=True),)),
        ("related", (_e(full="Bar Mono Bold"),)),
        ("bundle", (_e(("Foo Family",), trusted=True),)),
        ("width-like word is no style", (_e(full="Foo Sans Tight Bold"),)),
        ("hyphenated PostScript family", (_e(ps="Foo-Sans-Tight-Bold"),)),
        (
            "Object.prototype keys",
            (
                _e(("Constructor",), trusted=True),
                _e(full="Constructor Bold"),
                _e(("__proto__",)),
                _e(("hasOwnProperty",)),
            ),
        ),
        ("second family", (_e(("Mystery Sans", "Foo Sans")),)),
        ("full name after unknown families", (_e(("Mystery Sans",), "Foo Sans Bold"),)),
        (
            "owned beats near; family beats rename",
            (_e(("Foo Family",)), _e(("Foo Sans Pro",)), _e(("Foo Sans",))),
        ),
        ("one word kept", (_e(("- Bold",)),)),
        (
            "prefixes never match",
            (_e(("Foo",), trusted=True), _e(("Foo Sans Ti",), trusted=True), _e(full="Foo Sansa")),
        ),
        ("no entries", ()),
    ]


# Names whose candidates are pinned (Unicode, separators, the edges of every rule).
CANDIDATE_NAMES = [
    "Maple Mono NF CN", "Inter-Tight-Bold", "InterTight-Bold", "ArchivoBlack-Regular",
    "SourceSansPro-BoldIt", "JetBrainsMono-BoldItalic", "Fira Sans Extra Light", "Extra Bold",
    "- Bold", "\u2013 Bold", "_ Bold", "- Extra Bold", "Foo Bold\u2122", "Foo Bold\u00c9",
    "Foo Bold\u0663", "Foo Var\u0131able", "Foo VAR\u0130ABLE", "Hack\x1fNF", "Hack\ufeffNF",
    "Fira Code\u00a0Nerd Font", "Roboto Condensed Bold", "Constructor Bold", "TeX Gyre Heros Cn",
    "Noto Sans JP", "Martian Mono Cn Lt", "Kinto Sans Med", "Selawik Semilight", "Foo Semi Lt",
    "IosevkaTermNerdFontPropo", "CONF", "Inter-Display", "Inter \uff22\uff4f\uff4c\uff44", "Inter\u2010Bold",
    "Cascadia Code PL", "JetBrains Mono NL", "FiraCode NFM", "Hack NF", "Iosevka9NF", "FooNF",
    "Inter Variable", "InterVariable", "Inter var", "Satoshi-VF", "Inter VF Bold", "Variable",
    "Fira Code Retina", "Source Sans 3 ExtraLight", "Playfair Display Bold", "Archivo Black Italic",
    "Bungee Hairline", "Syne Italic", "Gentium Book", "Lato Hairline", "HackGen", "HackGen NF",
    "DejaVu Sans Mono for Powerline", "Noto Sans CN", "Noto Sans CJK SC", "Sarasa UI J",
    "更紗ゴシック UI J", "\U0001d504\U0001d505 Sans", "\U0001d504 Bold", "Bold", "Regular",
    "Foo  Bold", "Foo\u3000Bold", "Foo\u200bBold", "Foo_Bold", "Foo-", "-", "", " ", "\u200d",
    "Foo Sans NF CN Bold", "Foo Sans Bold NF", "Foo Sans Nerd_Font_Propo", "Foo Sans nerdfont",
    "Foo Sans NERD FONT MONO", "Foo Sans-NF", "Foo Sans_PL", "Foo SansPL", "Foo Sans CN-",
    "Foo Sans It", "Foo-Sans-It", "Foo Sans BoldIt", "Foo-BoldIt", "Foo-Semi-Bold", "Foo-SemiBold",
    "Foo Ultra Black Oblique", "Foo Demi", "Foo Thin Italic", "\uff26\uff4f\uff4f\uff33\uff41\uff4e\uff53", "\uff26\uff4f\uff4f \uff33\uff41\uff4e\uff53 \uff2e\uff26",
    "Archivo BlackItalic", "Archivo Black-Regular", "Archivo Black-Italic", "Archivo Black Regular",
    "Bungee Hairline-Regular", "Gentium BookItalic", "Syne Italic-Bold", "Foo ExtraLightItalic",
    "Foo Extra_Light-Italic", "Foo Semi-Bold", "Foo SemiLight", "Foo BoldExtra", "Foo bolditalic",
    "Foo bold-italic", "Foo BOLDItalic", "Foo Extra BoldItalic", "Foo Sans BlackItalic NF",
    "Archivo-Black-Bold", "Archivo-Black", "ArchivoBlackNF-Regular", "Foo-Bold-NF", "A-B-C-Bold",
]  # fmt: skip


# ---------------------------------------------------------------- parse cases

A = "\U0001d504"  # one code point, two UTF-16 units
NAMES30 = [f"Name {c}{n}" for c in "ABC" for n in range(10)]
# 504 lines, just over CUT_OFF_LINES, sorted and starting at D.
SORTED_LIST = "\n".join(f"{c}{n:02d} Sans,{c}{n:02d}" for c in "defghijk" for n in range(63))
SYSTEM_PROFILER = {
    "SPFontsDataType": [
        {
            "_name": "Archivo-Black.ttf",
            "path": "/Library/Fonts/Archivo-Black.ttf",
            "typefaces": [
                {
                    "_name": "ArchivoBlack-Regular",
                    "family": "Archivo Black",
                    "fullname": "Archivo Black",
                    "style": "Regular",
                },
            ],
        },
        {
            "_name": "Inter.ttc",
            "path": "/Library/Fonts/Inter.ttc",
            "typefaces": [
                {"_name": "Inter-Regular", "family": "Inter", "fullname": "Inter Regular"},
                {"_name": "Inter-Bold", "family": "Inter", "fullname": "Inter Bold"},
            ],
        },
    ]
}
JS = json.dumps(SYSTEM_PROFILER, indent=2)
REGISTRY = "\r\n".join(
    ["Arial (TrueType)", "Cambria & Cambria Math (TrueType)", "Archivo Black (OpenType)",
     "Courier 10,12,15 (VGA res)", "Segoe UI  Emoji (TrueType)", ""]
)  # fmt: skip
GET_ITEM = (
    "\r\n    Hive: HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\r\n\r\n"
    "Name                           Property\r\n----                           --------\r\n"
    "Fonts                          Arial (TrueType)             : arial.ttf\r\n"
    "                               Segoe UI  Emoji (TrueType)   : seguiemj.ttf\r\n"
    "                               Odd, Name                    : odd.ttf\r\n"
)
GET_ITEMPROPERTY = (
    "Arial (TrueType)             : arial.ttf\r\nOdd Name                     : D:\\Fonts\\odd.ttf\r\n"
    "PSPath                       : Microsoft.PowerShell.Core\\Registry::HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts\r\n"
    "PSParentPath                 : Microsoft.PowerShell.Core\\Registry::HKEY_LOCAL_MACHINE\\Software\\Microsoft\\Windows NT\\CurrentVersion\r\n"
    "PSChildName                  : Fonts\r\nPSDrive                      : HKLM\r\n"
    "PSProvider                   : Microsoft.PowerShell.Core\\Registry\r\n"
)
PS_NOT_FOUND = (
    "Get-Itm : The term 'Get-Itm' is not recognized as the name of a cmdlet, function, script file, or operable\n"
    "program. Check the spelling of the name, or if a path was included, verify that the path is correct and try again.\n"
    "At line:1 char:1\n+ Get-Itm 'HKLM:...'\n+ ~~~~~~~\n"
    "    + CategoryInfo          : ObjectNotFound: (Get-Itm:String) [], CommandNotFoundException\n"
)
COMMANDS = [
    "fc-list : family | sort > ~/fonts.txt",
    "sudo fc-list : family",
    "fc-list:family|sort",
    "$ fc-list : family",
    "(New-Object System.Drawing.Text.InstalledFontCollection).Families | ForEach-Object Name | Set-Clipboard",
    "Get-Item 'HKLM:\\Software\\Microsoft\\Windows NT\\CurrentVersion\\Fonts' | ForEach-Object Property",
    "system_profiler SPFontsDataType | grep 'Family:' | sort -u | pbcopy",
]


def _hand_parse_cases() -> list[tuple[str, bytes | str]]:
    """The parser's written cases: every format, error and warning, and each limit's edges."""
    cut_end = JS[: JS.index('"Inter Bold"') + 5]
    utf16_lone = (
        codecs.BOM_UTF16_LE
        + "Foo ".encode("utf-16-le")
        + b"\x00\xd8"
        + "\r\nBar\r\n".encode("utf-16-le")
    )
    cases: list[tuple[str, bytes | str]] = [
        (
            "fc-list with commas, escapes and a hidden name",
            "Archivo,Archivo Black\nicomoon\\-feather\nSarasa UI J,更紗ゴシック UI J\nFoo\\,Bar,Baz\n.Hidden Sans\nDejaVu Sans\n",
        ),
        ("one stray ', ' stays one name", "\n".join([*NAMES30, "Foo, Bar", "Archivo Black"])),
        (
            "one fontconfig comma in 32 lines splits but trusts nothing",
            "\n".join([*NAMES30, "Foo,Bar", "Archivo Black"]),
        ),
        (
            "two fontconfig commas in 32 lines make it fc-list",
            "\n".join([*NAMES30, "Foo,Bar", "Baz,Qux"]),
        ),
        (
            "plain fc-list drops its paths",
            "/usr/share/fonts/a/Archivo-Black.ttf: Archivo,Archivo Black:style=Black,Regular\n/usr/share/fonts/x.ttf: DejaVu Sans:style=Book\n",
        ),
        (
            "system_profiler text",
            "      Family: Archivo\n      Family: .SF NS\n      Family: Inter\n      Location: /Library/Fonts/Inter.ttc\n",
        ),
        ("system_profiler -json", JS),
        ("system_profiler -json cut at the end", cut_end),
        ("system_profiler -json cut at the top", JS[JS.index('"fullname"') :]),
        ("a dropped broken JSON file still warns", cut_end.encode()),
        ("a JSON list of names", '["Arial", "Inter"]'),
        ("a JSON list cut short", '[\n  "Arial",\n  "Inter",\n  "Rob'),
        ("a JSON list with a lone surrogate escape", '["Foo\\ud800 Sans"]'),
        (
            "registry, UTF-16 with a byte-order mark",
            codecs.BOM_UTF16_LE + REGISTRY.encode("utf-16-le"),
        ),
        ("registry, UTF-16 without one", REGISTRY.encode("utf-16-le")),
        ("registry, UTF-16 big-endian", codecs.BOM_UTF16_BE + REGISTRY.encode("utf-16-be")),
        (
            "registry value names without a suffix stay whole",
            "Arial (TrueType)\nCourier 10,12,15\nMS Sans Serif 8,10,12,14,18,24\nSegoe UI (TrueType)\nSmall Fonts\nSymbol 8,10,12,14,18,24 (VGA res)\n",
        ),
        (
            "UTF-16 without a byte-order mark, CJK names",
            "更紗ゴシック UI J\r\n思源黑体\r\n".encode("utf-16-le"),
        ),
        ("Get-Item's table", GET_ITEM),
        ("Get-ItemProperty's list", GET_ITEMPROPERTY),
        ("GDI names, CRLF", "Arial\r\nCambria\r\nSegoe UI\r\n"),
        ("a one-column PowerShell table", "Name\n----\nArial\nInter\n"),
        ("a two-column PowerShell table header", "Name Property\n---- --------\nArial\n"),
        (
            "a prompt, then the list",
            "user@host ~ $ fc-list : family | sort\nArchivo\nFoo %\nBar #\n$\n",
        ),
        ("a bare PowerShell prompt", "PS C:\\x>\nArial\n"),
        ("fonts called Sudo", "Sudo\nSudo Mono\n"),
        ("not found (fish)", "fish: Unknown command: fc-lis\nfc-lis : family\n^~~~~^\n"),
        ("not found (PowerShell)", PS_NOT_FOUND),
        (
            "not found, with 20 names after it",
            "bash: fc-lis: command not found\n" + "\n".join(NAMES30[:20]),
        ),
        ("empty", "  \n\t\n\u200b"),
        ("html", "<!DOCTYPE html><html><body>x</body></html>"),
        ("a font file", b"OTTO\x00\x0a\x00\x80\x00\x03\x00\x20" + bytes(52)),
        ("a list starting with 'true'", b"trueType Sans\nArchivo\n"),
        ("a PNG", b"\x89PNG\r\n\x1a\n" + bytes(20)),
        ("a ZIP", b"PK\x03\x04" + bytes(30)),
        ("bad UTF-8", b"Archivo\n\xff Sans\n"),
        ("UTF-8 with a byte-order mark", b"\xef\xbb\xbfArchivo\nInter\n"),
        ("a sorted fc-list paste cut at the top", SORTED_LIST),
        ("the same list dropped as a file", SORTED_LIST.encode()),
        ("a sorted list that starts with A", "Adwaita Sans\n" + SORTED_LIST),
        ("a name of 200 astral characters is kept", A * 200),
        ("201 are too long", A * 201),
        ("a line of 2,000 code points is kept", (A * 199 + ",") * 10),
        ("2,001 are too long", (A * 199 + ",") * 10 + A),
        ("1 control in 100 code points is no binary", A * 99 + "\x01"),
        ("1 control in 99 is binary", A * 98 + "\x01"),
        ("a NUL is binary", "Archivo\x00\nInter\n"),
        ("a lone surrogate in a paste", "Foo\ud800 Sans\n"),
        ("a valid astral name", A + "\U0001d505 Sans\n"),
        ("a lone surrogate in UTF-16", utf16_lone),
        (
            "line breaks CR, LF and CRLF; U+2028 inside a line",
            "Archivo\rInter\r\nRoboto\nFoo\u2028Sans\n",
        ),
        ("tabs and odd spaces collapse", "Archivo\t Black\nInter\u3000Tight\n\u00a0Roboto\u00a0\n"),
        ("a dash rule alone", "-----\nArchivo\n"),
        ("Hive and PSPath lines alone", "Hive: x\nPSPath : y\n"),
        ("only prompts", "user@host ~ $\n$\n"),
    ]
    cases += [(f"the command: {cmd}", cmd + "\n") for cmd in COMMANDS]
    return cases


def parts_cases() -> list[tuple[str, list[list[Any]], bool]]:
    """Inputs of millions of characters, spelled as parts: the limits, long JSON strings,
    runs of escaped quotes, and duplicate keys holding objects or lists."""
    return [
        ("too big, pasted", [["a", owned.MAX_CHARS + 1]], False),
        ("too big, dropped", [["a", owned.MAX_BYTES + 1]], True),
        ("5,000,001 astral characters are within MAX_CHARS", [[A, 5_000_001]], False),
        ("10 MB of short lines, pasted", [["Inter Bold\n", 909_000]], False),
        ("10 MB of short lines, dropped", [["Inter Bold\n", 909_000]], True),
        (
            "lines with long space runs, pasted",
            [["Foo" + " " * 1900 + "Bar (TrueType\n", 5_000]],
            False,
        ),
        (
            "lines with long space runs, dropped",
            [["Foo" + " " * 1900 + "Bar (TrueType\n", 5_000]],
            True,
        ),
        ("Family: and 9,999,990 characters", [["Family: ", 1], ["A", 9_999_990]], False),
        ("a listed string near the limit", [['["', 1], ["A", 9_999_990], ['"]', 1]], False),
        ("a cut listed string", [['["', 1], ["A", 8_400_000]], False),
        (
            "a long member, then a stray x",
            [['{"family":"Archivo","copyright":"', 1], ["A", 8_400_000], ['"}x', 1]],
            False,
        ),
        ("a long string and a trailing comma", [['["', 1], ["A", 9_000_000], ['",]', 1]], False),
        (
            "a long first line, then JSON lines",
            [['"', 1], ["A", 9_000_000], ['",\n"Archivo",\n"Inter"\n', 1]],
            False,
        ),
        (
            "a long first line, then JSON lines, dropped",
            [['"', 1], ["A", 9_000_000], ['",\n"Archivo",\n"Inter"\n', 1]],
            True,
        ),
        ("80 KB of escaped quotes", [['["', 1], ['\\"', 40_000]], False),
        ("10 MB of escaped quotes", [['["', 1], ['\\"', 4_999_990]], False),
        ("10 MB of escaped quotes in a member", [['{"family":"', 1], ['\\"', 4_999_990]], False),
        ("10 MB of escaped quotes, dropped", [['["', 1], ['\\"', 4_999_990]], True),
        (
            "duplicate keys of lists, an integer key",
            [['{"0": 1, "x": ["Archivo"], "x": ["Inter"]}', 1]],
            False,
        ),
        (
            "duplicate integer keys of objects",
            [['{"0": {"family": "Archivo"}, "0": {"family": "Inter"}}', 1]],
            False,
        ),
        (
            "duplicate keys of objects, a 5,000-digit integer",
            [
                ['{"x": {"family": "Archivo"}, "x": {"family": "Inter"}, "n": 1', 1],
                ["0", 5000],
                ["}", 1],
            ],
            False,
        ),
        (
            "duplicate keys of lists, a 5,000-digit integer",
            [['{"x": ["Archivo"], "x": ["Inter"], "n": 1', 1], ["0", 5000], ["}", 1]],
            False,
        ),
        (
            "duplicate keys of objects, 120,000 deep",
            [
                ["[\n", 120_000],
                ['{"x": {"family": "Archivo"}, "x": {"family": "Inter"}}', 1],
                ["\n]", 120_000],
            ],
            False,
        ),
    ]


# Pools for drawn inputs.
_TEXT_PIECES = [
    "Archivo", "Inter Tight", "Noto Sans JP", "更紗ゴシック UI J", "思源黑体", A + " Sans", "Fira Code",
    "\uff26\uff49\uff52\uff41", "\r\n", "\n", "\r", " ", "\t", "\ufeff", "\u200b", "\u2028", ",", "\\,", "é", "Ω", ".Hidden",
]  # fmt: skip
_CODECS = ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "be-bom"]
_BYTE_UNITS = [
    b"A", b"\xc3\xa9", b"\xe2\x82\xac", b"\xf0\x9d\x94\x84", b"\xed\xa0\x80", b"\xc0\xaf",
    b"\xe0\x80\xaf", b"\xf4\x90\x80\x80", b"\x80", b"\xbf", b"\xc3", b"\xe2\x82", b"\xf0\x9d\x94",
    b"\xff", b"\xfe", b"\n", b"\x00", b"\x00\xd8", b"\x00\xdc", b"\x3d\xd8\x00\xde",
]  # fmt: skip
_LINE_PIECES = [
    "Foo", "Bar Sans", ",", "\\,", "\\\\", "\\-", "\\:", "\\x", ", ", ":style=Bold",
    "/usr/share/fonts/a.ttf: ", "C:\\Windows\\Fonts\\a.ttf: ", "~/x: ", ".Hidden", " (TrueType)",
    " (OpenType) : a.ttf", " : b.otf", "(8514/a res)", " & ", "user@host:~$ ", "PS C:\\x> ", "$ ",
    "% ", "# ", "\u276f ", "sudo ", "fc-list : family", "Get-Item", "Fonts  ", "----", " ----",
    "Name", "Property", "Hive:", "PSPath : x", "+ ", "^~~", "Sudo", "Mono",
    "(", ")", "  ", "\t", "\x01", "Sans", "Ünï", '"', '\\"', "[", "{", "}", ":",
]  # fmt: skip
_JSON_KEYS = ["family", "fullname", "_name", "path", "typefaces", "0", "x", "SPFontsDataType"]
_JSON_LEAVES = [
    None,
    True,
    False,
    0,
    12,
    -3.5,
    "",
    "Archivo",
    "Inter Bold",
    A,
    "a\\b",
    'q"t',
    ".SF NS",
]
_NAME_PIECES = [
    "Inter", "Fira", "Code", "Sans", "Mono", "Condensed", "Tight", "-", "_", "\u2013", " ", "Bold",
    "Italic", "Extra", "Semi", "Light", "NF", "NFM", "Nerd Font", "nerd font mono", "CN", "PL", "NL",
    "Variable", "VF", "var", "Regular", "BoldItalic", "It", "Lt", "Med", "x", "9", "É", "™",
    "\u200d", "\u00a0", "\uff22\uff4f\uff4c\uff44", A,
]  # fmt: skip
_SYN_NAMES = [
    "Foo Sans", "Foo Sans Pro", "FSans", "Foo Sans Text", "Foo Sans NF", "Foo Family", "Foo Sans Black",
    "Foo Sans Hairline", "Foo Hairline", "Foo Sans Heavy", "Foo Sans JP", "Bar Serif", "Bar Mono",
    "Bar Serif Display", "Foo Sans Tight", "FooSans", "Mystery", "Constructor",
]  # fmt: skip
_SUFFIXES = [
    "",
    " Bold",
    " Italic",
    " Black",
    " NF",
    " Nerd Font",
    "NF",
    " CN",
    " PL",
    " Variable",
    "Variable",
    " Condensed",
    "-Regular",
    "-BoldItalic",
    " Med",
]


def _pick(rng: np.random.Generator, pool: list[Any]) -> Any:
    return pool[int(rng.integers(len(pool)))]


def _json_value(rng: np.random.Generator, depth: int = 0) -> Any:
    kind = int(rng.integers(3)) if depth < 4 else 0
    if kind == 0:
        return _pick(rng, _JSON_LEAVES)
    if kind == 1:
        return [_json_value(rng, depth + 1) for _ in range(int(rng.integers(4)))]
    return {
        _pick(rng, _JSON_KEYS): _json_value(rng, depth + 1) for _ in range(int(rng.integers(4)))
    }


def _drawn_parse_cases(rng: np.random.Generator) -> list[tuple[str, bytes | str]]:
    """Damaged files in each encoding, byte soup, listings and JSON texts, cut or whole."""
    out: list[tuple[str, bytes | str]] = []
    for i in range(60):
        text = "".join(_pick(rng, _TEXT_PIECES) for _ in range(int(rng.integers(30))))
        codec = _pick(rng, _CODECS)
        raw = (
            codecs.BOM_UTF16_BE + text.encode("utf-16-be")
            if codec == "be-bom"
            else text.encode(codec)
        )
        damage = int(rng.integers(3))
        if damage == 1:
            at = int(rng.integers(len(raw) + 1))
            raw = (
                raw[:at] + bytes(rng.integers(0, 256, int(rng.integers(1, 4))).tolist()) + raw[at:]
            )
        elif damage == 2 and len(raw) > 3:
            raw = raw[: -int(rng.integers(1, 4))]
        out.append((f"drawn file {i}", raw))
    for i in range(30):
        raw = b"".join(_pick(rng, _BYTE_UNITS) for _ in range(int(rng.integers(40))))
        bom = (b"", codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)[int(rng.integers(3))]
        out.append((f"drawn bytes {i}", bom + raw))
    for i in range(70):
        lines = [
            "".join(_pick(rng, _LINE_PIECES) for _ in range(int(rng.integers(8))))
            for _ in range(int(rng.integers(1, 25)))
        ]
        if i % 10 == 9:
            lines.insert(int(rng.integers(len(lines) + 1)), "bash: fc-lis: command not found")
        text = "\n".join(lines)
        out.append((f"drawn listing {i}", text.encode() if i % 4 == 3 else text))
    for i in range(50):
        text = json.dumps(_json_value(rng), indent=2 if i % 2 else None, ensure_ascii=False)
        cut = int(rng.integers(3))
        at = int(rng.integers(60))
        if cut == 1:
            text = text[: max(1, len(text) - at)]
        elif cut == 2 and at < len(text):
            text = text[at:]
        out.append((f"drawn JSON {i}", text))
    return out


def _drawn_names(rng: np.random.Generator, n: int) -> list[str]:
    out = []
    for i in range(n):
        pieces = [_pick(rng, _NAME_PIECES) for _ in range(int(rng.integers(1, 7)))]
        out.append(("" if i % 2 else " ").join(pieces))
    return out


def _drawn_entries(rng: np.random.Generator, n: int) -> list[tuple[owned.Entry, ...]]:
    def name() -> str:
        return _pick(rng, _SYN_NAMES) + _pick(rng, _SUFFIXES) + _pick(rng, _SUFFIXES)

    out = []
    for _ in range(n):
        entries = []
        for _ in range(int(rng.integers(1, 6))):
            kind = int(rng.integers(4))
            if kind == 0:
                families = tuple(name() for _ in range(int(rng.integers(1, 4))))
                entries.append(_e(families, trusted=bool(rng.integers(2))))
            elif kind == 1:
                entries.append(_e(full=name()))
            elif kind == 2:
                entries.append(_e(ps=name().replace(" ", "")))
            else:
                entries.append(_e((name(),), name(), name().replace(" ", ""), True))
        out.append(tuple(entries))
    return out


# ---------------------------------------------------------------- malformed match files


def _with(doc: dict[str, Any], path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    out = json.loads(json.dumps(doc))
    target = out
    for key in path[:-1]:
        target = target[key]
    if value is _DROP:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return out


_DROP = object()


def _malformed(doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Match files ``Table.from_document`` refuses, each one change away from ``doc``."""
    words = doc["words"]
    amb_key = next(iter(doc["amb"]))
    cases: list[tuple[str, dict[str, Any]]] = [
        ("a wrong format", _with(doc, ("format",), "tff-matches")),
        ("version 2", _with(doc, ("v",), 2)),
        ("v true", _with(doc, ("v",), True)),
        ("v a string", _with(doc, ("v",), "1")),
        ("no v", _with(doc, ("v",), _DROP)),
        ("ids a string", _with(doc, ("ids",), "abc")),
        ("ids holding a number", _with(doc, ("ids",), [*doc["ids"], 5])),
        ("reasons a string", _with(doc, ("reasons",), "proprietary")),
        ("no reasons", _with(doc, ("reasons",), _DROP)),
        ("k a list", _with(doc, ("k",), [])),
        ("a k value a number", _with(doc, ("k", "x"), 5)),
        ("a code past the ids", _with(doc, ("k", "x"), "9")),
        ("a reason past the reasons", _with(doc, ("k", "x"), "!7")),
        ("a code with an unknown letter", _with(doc, ("k", "x"), "1q")),
        ("a code with a leading zero", _with(doc, ("k", "x"), "01")),
        ("an empty code", _with(doc, ("k", "x"), "1,,2")),
        ("an empty k value", _with(doc, ("k", "x"), "")),
        ("a letter without an index", _with(doc, ("k", "x"), "x")),
        ("a negative code", _with(doc, ("k", "x"), "-1")),
        ("amb a list", _with(doc, ("amb",), [])),
        ("an amb index past the ids", _with(doc, ("amb", amb_key), [9])),
        ("a negative amb index", _with(doc, ("amb", amb_key), [-1])),
        ("an amb index of 1.5", _with(doc, ("amb", amb_key), [1.5])),
        ("an amb index true", _with(doc, ("amb", amb_key), [True])),
        ("an amb key not in k", _with(doc, ("amb", "zzz"), [0])),
        ("an amb value an object", _with(doc, ("amb", amb_key), {})),
        ("no words", _with(doc, ("words",), _DROP)),
        ("words a list", _with(doc, ("words",), [])),
        ("no Nerd phrases part", _with(doc, ("words", "nerd"), _DROP)),
        ("no Nerd phrases", _with(doc, ("words", "nerd"), [])),
        ("a blank Nerd phrase", _with(doc, ("words", "nerd"), [*words["nerd"], " "])),
        (
            "an empty variable_joined entry",
            _with(doc, ("words", "variable_joined"), [*words["variable_joined"], ""]),
        ),
        ("a style word that is a number", _with(doc, ("words", "style"), [*words["style"], 5])),
        ("style é", _with(doc, ("words", "style"), [*words["style"], "é"])),
        (
            "a Nerd phrase with an em space",
            _with(doc, ("words", "nerd"), [*words["nerd"], "nerd\u2003font"]),
        ),
        ("no separators", _with(doc, ("words", "separators"), [])),
        ("a separator range", _with(doc, ("words", "separators"), ["0020-002D"])),
        ("joiner DC00", _with(doc, ("words", "joiners"), [*words["joiners"], "DC00"])),
        ("dash D7FF-D800", _with(doc, ("words", "dashes"), [*words["dashes"], "D7FF-D800"])),
        ("the format a list", _with(doc, ("format",), ["tff-match"])),
    ]
    blanks = ("0x20", "2_0", " 20", "D800", "DFFF-E000", "D800-DFFF", "110000", "0020-001F",
              "0020-0030-0040", "1234567", "")  # fmt: skip
    cases += [
        (f"whitespace {h!r}", _with(doc, ("words", "whitespace"), [*words["whitespace"], h]))
        for h in blanks
    ]
    separators = {
        "1F600": "an emoji", "00C9": "É", "00E9": "é", "00FF": "ÿ", "0178": "Ÿ", "0391": "Greek Alpha",
        "212A": "the Kelvin sign", "007F": "DEL", "001F": "a control", "00A0": "a no-break space",
        "10020": "beyond the BMP (fromCharCode would cut it to a space)",
    }  # fmt: skip
    cases += [
        (f"separator {h}, {what}", _with(doc, ("words", "separators"), [*words["separators"], h]))
        for h, what in separators.items()
    ]
    return cases


# ---------------------------------------------------------------- known answers


def _known(
    label: str,
    entry: owned.Entry,
    owned_how: dict[str, str] | None = None,
    not_owned: tuple[str, ...] = (),
    near: tuple[str, ...] = (),
    outcome: str | None = None,
    *,
    patched: bool = True,
    xfail: str | None = None,
) -> dict[str, Any]:
    out = {
        "label": label,
        "entry": entry.to_json(),
        "patched": patched,
        "owned": owned_how or {},
        "not_owned": sorted(not_owned),
        "near": sorted(near),
        "outcome": outcome,
    }
    if xfail:
        out["xfail"] = xfail
    return out


def known_answers() -> list[dict[str, Any]]:
    """Expectations on the real build/names.json (with the reviewed recodes, as the site build
    makes the match file): Milestone 3 steps 3 and 5, and the slice's checklist."""
    k, t = _known, True
    return [
        k(
            "Source Sans Pro is Source Sans 3, renamed",
            _e(("Source Sans Pro",), trusted=t),
            {"source-sans-3": "rename"},
            outcome="owned",
        ),
        k(
            "FiraCode NF is a patched Fira Code",
            _e(("FiraCode NF",), trusted=t),
            {"fira-code": "patched"},
            outcome="owned",
        ),
        k(
            "FiraCode Nerd Font is a patched Fira Code",
            _e(("FiraCode Nerd Font",), trusted=t),
            {"fira-code": "patched"},
            outcome="owned",
        ),
        k(
            "SauceCodePro Nerd Font is a patched Source Code Pro",
            _e(("SauceCodePro Nerd Font",), trusted=t),
            {"source-code-pro": "patched"},
            outcome="owned",
        ),
        k(
            "FiraCode NF is only near Fira Code with patched builds off",
            _e(("FiraCode NF",), trusted=t),
            {},
            ("fira-code",),
            ("fira-code",),
            "near",
            patched=False,
        ),
        k(
            "Roboto Slab is never Roboto",
            _e(("Roboto Slab",), trusted=t),
            {"roboto-slab": "family"},
            ("roboto",),
            outcome="owned",
        ),
        k(
            "Roboto Slab Bold in the registry is never Roboto",
            _e(full="Roboto Slab Bold"),
            {"roboto-slab": "family"},
            ("roboto",),
            outcome="owned",
        ),
        k(
            "Inter Tight is never Inter",
            _e(("Inter Tight",), trusted=t),
            {"inter-tight": "family"},
            ("inter",),
            outcome="owned",
        ),
        k(
            "Inter Tight Bold in the registry is never Inter",
            _e(full="Inter Tight Bold"),
            {"inter-tight": "family"},
            ("inter",),
            outcome="owned",
        ),
        k(
            "PostScript Inter-Tight-Bold is never Inter",
            _e(ps="Inter-Tight-Bold"),
            {"inter-tight": "family"},
            ("inter",),
            outcome="owned",
        ),
        k(
            "PostScript InterTight-Bold is never Inter",
            _e(ps="InterTight-Bold"),
            {"inter-tight": "family"},
            ("inter",),
            outcome="owned",
        ),
        k(
            "Noto Sans JP is never Noto Sans",
            _e(("Noto Sans JP",), trusted=t),
            {},
            ("noto-sans",),
            outcome="ineligible",
        ),
        k(
            "Noto Sans JP Bold in the registry is never Noto Sans",
            _e(full="Noto Sans JP Bold"),
            {},
            ("noto-sans",),
            outcome="ineligible",
        ),
        k(
            "Noto Sans SC is never Noto Sans",
            _e(("Noto Sans SC",), trusted=t),
            {},
            ("noto-sans",),
            outcome="ineligible",
        ),
        k(
            "Noto Sans CJK SC is never Noto Sans",
            _e(("Noto Sans CJK SC",), trusted=t),
            {},
            ("noto-sans",),
            outcome="ineligible",
        ),
        k(
            "Source Han Sans SC is ineligible",
            _e(("Source Han Sans SC",), trusted=t),
            outcome="ineligible",
        ),
        k(
            "Noto Sans CN (no such font) would be a CJK build of Noto Sans",
            _e(("Noto Sans CN",), trusted=t),
            {"noto-sans": "patched"},
            outcome="owned",
        ),
        k(
            "Maple Mono CN is a CJK build of Maple Mono",
            _e(("Maple Mono CN",), trusted=t),
            {"maple-mono": "patched"},
            outcome="owned",
        ),
        k(
            "Roboto Condensed is its own family",
            _e(("Roboto Condensed",), trusted=t),
            {"roboto-condensed": "family"},
            ("roboto",),
            outcome="owned",
        ),
        k(
            "Adwaita Sans is never Inter",
            _e(("Adwaita Sans",), trusted=t),
            {"adwaita-sans": "family"},
            ("inter",),
            outcome="owned",
        ),
        k(
            "Adwaita Sans Bold in the registry is never Inter",
            _e(full="Adwaita Sans Bold"),
            {"adwaita-sans": "family"},
            ("inter",),
            outcome="owned",
        ),
        k(
            "Gentium Book, trusted, owns no Gentium",
            _e(("Gentium Book",), trusted=t),
            {},
            ("gentium", "gentium-plus"),
        ),
        k(
            "Syne Italic in the registry is only near Syne",
            _e(full="Syne Italic"),
            {},
            ("syne",),
            ("syne",),
            "near",
        ),
        k(
            "Cascadia Code PL is a patched Cascadia Code",
            _e(("Cascadia Code PL",), trusted=t),
            {"cascadia-code": "patched"},
            outcome="owned",
        ),
        k(
            "Cascadia Code NF is a patched Cascadia Code",
            _e(("Cascadia Code NF",), trusted=t),
            {"cascadia-code": "patched"},
            outcome="owned",
        ),
        k(
            "Cascadia Code NF in the registry is a patched Cascadia Code",
            _e(full="Cascadia Code NF"),
            {"cascadia-code": "patched"},
            outcome="owned",
        ),
        k(
            "Maple Mono NF CN is a patched Maple Mono",
            _e(("Maple Mono NF CN",), trusted=t),
            {"maple-mono": "patched"},
            outcome="owned",
        ),
        k(
            "Inter Variable is Inter",
            _e(("Inter Variable",), trusted=t),
            {"inter": "build"},
            outcome="owned",
        ),
        k(
            "InterVariable is Inter",
            _e(("InterVariable",), trusted=t),
            {"inter": "build"},
            outcome="owned",
        ),
        k(
            "Inter Display is Inter",
            _e(("Inter Display",), trusted=t),
            {"inter": "build"},
            outcome="owned",
        ),
        k(
            "Fira Code Retina is Fira Code",
            _e(("Fira Code Retina",), trusted=t),
            {"fira-code": "build"},
            outcome="owned",
        ),
        k(
            "Source Sans 3 ExtraLight is Source Sans 3",
            _e(full="Source Sans 3 ExtraLight"),
            {"source-sans-3": "family"},
            outcome="owned",
        ),
        k(
            "Playfair Display Bold is Playfair Display, not Playfair",
            _e(full="Playfair Display Bold"),
            {"playfair-display": "family"},
            ("playfair",),
            outcome="owned",
        ),
        k(
            "JetBrains Mono NL is a patched JetBrains Mono",
            _e(("JetBrains Mono NL",), trusted=t),
            {"jetbrains-mono": "patched"},
            outcome="owned",
        ),
        k(
            "fc-list Archivo,Archivo Black owns Archivo, not Archivo Black",
            _e(("Archivo", "Archivo Black"), trusted=t),
            {"archivo": "family"},
            ("archivo-black",),
            outcome="owned",
        ),
        k(
            "fc-list Archivo Black first owns Archivo Black",
            _e(("Archivo Black",), trusted=t),
            {"archivo-black": "family"},
            ("archivo",),
            outcome="owned",
        ),
        k(
            "registry Archivo Black is near both",
            _e(full="Archivo Black"),
            {},
            ("archivo", "archivo-black"),
            ("archivo", "archivo-black"),
            "near",
        ),
        k(
            "registry Archivo Black Italic is near both",
            _e(full="Archivo Black Italic"),
            {},
            ("archivo", "archivo-black"),
            ("archivo", "archivo-black"),
            "near",
        ),
        k(
            "registry Archivo BlackItalic is near both",
            _e(full="Archivo BlackItalic"),
            {},
            ("archivo", "archivo-black"),
            ("archivo", "archivo-black"),
            "near",
        ),
        k(
            "registry Archivo Black-Regular is near both",
            _e(full="Archivo Black-Regular"),
            {},
            ("archivo", "archivo-black"),
            ("archivo", "archivo-black"),
            "near",
        ),
        k(
            "registry Archivo Black-Italic is near both",
            _e(full="Archivo Black-Italic"),
            {},
            ("archivo", "archivo-black"),
            ("archivo", "archivo-black"),
            "near",
        ),
        k(
            "fc-list Archivo BlackItalic first owns Archivo Black",
            _e(("Archivo BlackItalic",), trusted=t),
            {"archivo-black": "family"},
            ("archivo",),
            outcome="owned",
        ),
        k(
            "PostScript ArchivoBlack-Regular owns Archivo Black",
            _e(ps="ArchivoBlack-Regular"),
            {"archivo-black": "family"},
            ("archivo",),
            outcome="owned",
        ),
        k(
            "static Archivo's Black weight on macOS owns Archivo, not Archivo Black",
            _e(("Archivo",), "Archivo Black", "Archivo-Black", trusted=t),
            {"archivo": "family"},
            ("archivo-black",),
            outcome="owned",
        ),
        k(
            "PostScript Archivo-Black, static Archivo's Black, is near both",
            _e(ps="Archivo-Black"),
            {},
            ("archivo", "archivo-black"),
            ("archivo", "archivo-black"),
            "near",
        ),
        k(
            "one-per-line Archivo-Black-Bold is near both",
            _e(("Archivo-Black-Bold",)),
            {},
            ("archivo", "archivo-black"),
            ("archivo", "archivo-black"),
            "near",
        ),
        k(
            "one-per-line ArchivoBlack-Regular owns Archivo Black",
            _e(("ArchivoBlack-Regular",)),
            {"archivo-black": "family"},
            ("archivo",),
            outcome="owned",
        ),
        k(
            "Bungee Hairline, trusted, is only near Bungee",
            _e(("Bungee Hairline",), trusted=t),
            {},
            ("bungee",),
            ("bungee",),
            "near",
        ),
        k(
            "Bungee Hairline in the registry is only near Bungee",
            _e(full="Bungee Hairline"),
            {},
            ("bungee",),
            ("bungee",),
            "near",
        ),
        k(
            "Bungee Hairline-Regular in the registry is only near Bungee",
            _e(full="Bungee Hairline-Regular"),
            {},
            ("bungee",),
            ("bungee",),
            "near",
        ),
        k(
            "Gentium BookItalic in the registry owns no Gentium",
            _e(full="Gentium BookItalic"),
            {},
            ("gentium", "gentium-plus"),
            outcome="near",
        ),
        k(
            "Syne Italic-Bold in the registry is only near Syne",
            _e(full="Syne Italic-Bold"),
            {},
            ("syne",),
            ("syne",),
            "near",
        ),
        k(
            "Lato Hairline is a plain build of Lato",
            _e(("Lato Hairline",), trusted=t),
            {"lato": "build"},
            outcome="owned",
        ),
        k(
            "Constructor is unknown, not Object.prototype's",
            _e(("Constructor",), trusted=t),
            outcome="unknown",
        ),
        k(
            "HackGen is only near Hack (hopper_hackgen_related)",
            _e(("HackGen",), trusted=t),
            {},
            ("hack",),
            ("hack",),
            "near",
        ),
        k(
            "HackGen NF is only near Hack too",
            _e(("HackGen NF",), trusted=t),
            {},
            ("hack",),
            ("hack",),
            "near",
        ),
        k(
            "DejaVu Sans Mono for Powerline is a patched DejaVu Sans Mono",
            _e(("DejaVu Sans Mono for Powerline",), trusted=t),
            {"dejavu-sans-mono": "patched"},
            outcome="owned",
            xfail="names.json lists it as ineligible (latin), not as a Powerline build of "
            "DejaVu Sans Mono; it waits for names.json's reasons cleanup",
        ),
    ]


# ---------------------------------------------------------------- writing


def _canonical_line(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _lines(doc: dict[str, Any] | list[Any]) -> bytes:
    """``doc`` with one member per line, and one item per line in its lists, so a change of
    one case is a change of one line."""
    if isinstance(doc, list):
        body = ",\n".join("  " + _canonical_line(item) for item in doc)
        return ("[\n" + body + "\n]\n").encode("utf-8")
    parts = []
    for key, value in doc.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            items = ",\n".join("    " + _canonical_line(item) for item in value)
            parts.append(f"  {json.dumps(key)}: [\n{items}\n  ]")
        else:
            parts.append(f"  {json.dumps(key)}: {_canonical_line(value)}")
    return ("{\n" + ",\n".join(parts) + "\n}\n").encode("utf-8")


def spec() -> dict[str, Any]:
    """owned.py's constants, which the port must equal."""
    limits = (
        "MAX_BYTES",
        "MAX_CHARS",
        "MAX_LINE",
        "MAX_NAME",
        "SNIFF_BYTES",
        "SNIFF_HTML",
        "SNIFF_JSON",
        "FEW_NAMES",
        "CUT_OFF_LINES",
        "FC_LIST_ONE_IN",
    )
    return {
        "limits": {name.lower(): getattr(owned, name) for name in limits},
        "formats": list(owned.FORMATS),
        "errors": list(owned.ERRORS),
        "warnings": list(owned.WARNINGS),
        "command_words": sorted(owned.COMMAND_WORDS),
        "font_extensions": sorted(owned.FONT_EXTENSIONS),
        "words": owned.WORDS,
    }


def vectors() -> dict[str, Any]:
    """The whole of owned-fonts.json."""
    rng = np.random.default_rng(SEED)
    document = json.loads(json.dumps(owned.match_document(NAMES, CATALOG_IDS, RUN_DATE)))
    table = owned.Table.from_document(document)
    parse = [
        {"label": label, "input": spelled(data), "expect": owned.parse(data).to_json()}
        for label, data in _hand_parse_cases() + _drawn_parse_cases(rng)
    ]
    for label, parts, as_bytes in parts_cases():
        spelling = {"parts": parts, "bytes": as_bytes}
        parse.append(
            {"label": label, "input": spelling, "expect": owned.parse(data_of(spelling)).to_json()}
        )
    names = list(dict.fromkeys(CANDIDATE_NAMES + _SYN_NAMES + _drawn_names(rng, 300)))
    cands = [
        {
            "name": name,
            "expect": [
                {"key": c.key, "text": c.text, "patched": c.patched, "postscript": c.postscript}
                for c in table.candidates(name)
            ],
        }
        for name in names
    ]
    runs = match_cases()
    runs += [(f"drawn entries {i}", entries) for i, entries in enumerate(_drawn_entries(rng, 40))]
    match = [
        {
            "label": label + ("" if patched else ", patched off"),
            "entries": [e.to_json() for e in entries],
            "patched": patched,
            "expect": owned.match(entries, table, patched=patched).to_json(),
        }
        for label, entries in runs
        for patched in (True, False)
    ]
    malformed = [{"label": label, "document": doc} for label, doc in _malformed(document)]
    for group in (parse, match, malformed):
        labels = [c["label"] for c in group]
        assert len(set(labels)) == len(labels), "labels repeat"
    return {
        "format": "tff-owned-fonts",
        "version": 1,
        "spec": spec(),
        "names": NAMES,
        "catalog_ids": CATALOG_IDS,
        "run_date": RUN_DATE,
        "document": document,
        "parse": parse,
        "candidates": cands,
        "match": match,
        "malformed": malformed,
    }


def expected_fixtures() -> dict[str, Any]:
    """Each fixture's parse, as a dropped file."""
    return {
        path.name: owned.parse(path.read_bytes()).to_json()
        for path in sorted(FIXTURES.iterdir())
        if path.name != EXPECTED.name
    }


def main() -> None:
    VECTORS.write_bytes(_lines(vectors()))
    KNOWN.write_bytes(_lines(known_answers()))
    EXPECTED.write_bytes(_lines(expected_fixtures()))


if __name__ == "__main__":
    main()
