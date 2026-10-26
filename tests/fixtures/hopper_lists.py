"""Lists of installed fonts as the hopper receives them, made from a names.json.

The names come from the file; everything else is made up here (``/Library/Fonts/Font1.ttf``,
"Écriture Fâmille", "示例黑体"), so the lists hold nothing from a real machine.
tests/site/test_owned_js.py checks the port against Python on them, and
tests/site/test_perf.py times them (Milestone 3 step 5: 10,000 names match in under 200 ms).
One list has a character past ASCII on every line, since the port finds an ASCII name's keys
by a faster road than any other name's.
"""

import json
from collections.abc import Mapping
from typing import Any

FONTS = 10_000
LABELS = ("registry", "fc-list", "system_profiler -json", "fc-list, past ASCII")
_STYLES = (
    "", " Bold", " Italic", " Bold Italic", " Light", " ExtraLight Italic", " SemiBold", " Black",
    " Medium", " Thin", " Condensed Bold", " SemiCondensed Light", " Regular",
)  # fmt: skip
_PATCHES = ("", "", "", "", " NF", " Nerd Font Mono", " Nerd Font Propo", " PL", " NL")
_UNKNOWN = tuple(
    f"{a} {b}"
    for a in ("Acme", "Brand", "Corp", "Demo", "Ego", "Faux", "Gamma", "Halo")
    for b in ("Sans", "Serif", "Mono", "Grotesk", "Display", "Text", "Slab", "Script")
)
# Made-up family names past ASCII, as a machine in another locale lists them: fontconfig puts
# a localized name before or after the family, and other names carry accents or start with one.
_LOCAL = (
    "示例黑体", "テスト明朝", "見本ゴシック", "예시고딕", "Παράδειγμα Sans", "Пример Гротеск",
    "Ünïcode Sans", "Écriture Fâmille", "Øre Serif", "Žluť Mono",
)  # fmt: skip
_ACCENTED = ("Négrita", "Itálico", "Fêtée", "Grâce", "Šikmé", "Ćirilica")


def lists(names: Mapping[str, Any], fonts: int = FONTS) -> dict[str, str]:
    """A Windows registry paste, fc-list's family lines, a system_profiler -json file, and
    fc-list's lines with a name past ASCII on each, of ``fonts`` fonts each, with style words,
    patched builds, other names and fonts the file doesn't know, keyed by ``LABELS``."""
    families = [f["family"] for f in names["families"]] + [r["name"] for r in names["ineligible"]]
    others = [
        n["name"] for f in names["families"] for n in f["names"] if n["relation"] != "postscript"
    ]
    pool = [*families, *others, *_UNKNOWN]

    def family(i: int) -> str:
        return pool[(i * 7919) % len(pool)]

    def name(i: int) -> str:
        return family(i) + _PATCHES[(i // 3) % len(_PATCHES)] + _STYLES[i % len(_STYLES)]

    registry = "\n".join(f"{name(i)} (TrueType)" for i in range(fonts))
    fc_list = "\n".join(f"{family(i)},{name(i)}" if i % 4 else name(i) for i in range(fonts))
    typefaces = [
        {"_name": f"Font{i}.ttf", "path": f"/Library/Fonts/Font{i}.ttf", "typefaces": [
            {"_name": name(i).replace(" ", "") + "-Regular", "family": family(i),
             "fullname": name(i), "style": "Regular"}]}
        for i in range(fonts)
    ]  # fmt: skip
    macos = json.dumps({"SPFontsDataType": typefaces}, indent=2)

    def past_ascii(i: int) -> str:
        local = _LOCAL[(i // 4) % len(_LOCAL)]
        style = _STYLES[i % len(_STYLES)]
        return (
            f"{local} {i % 300},{name(i)}",
            f"{name(i)},{local} {i % 300}",
            f"{local} {i % 997}{style}",
            f"{family(i)} {_ACCENTED[i % len(_ACCENTED)]}{style}",
        )[i % 4]

    other = "\n".join(past_ascii(i) for i in range(fonts))
    return dict(zip(LABELS, (registry, fc_list, macos, other), strict=True))


def system_profiler_cut(size: int = 5_000_000, fonts: int = 20_000) -> str:
    """``system_profiler -json`` of ``fonts`` made-up fonts, pasted and cut at ``size``
    characters, so the text ends inside a string."""
    typefaces = [
        {"_name": f"F{i}.ttf", "path": f"/Library/Fonts/F{i}.ttf", "typefaces": [
            {"_name": f"Fam{i}-Bold", "family": f"Fam {i}", "fullname": f"Fam {i} Bold",
             "style": "Bold", "copyright": "y" * 120}]}
        for i in range(fonts)
    ]  # fmt: skip
    return json.dumps({"SPFontsDataType": typefaces}, indent=2)[:size]
