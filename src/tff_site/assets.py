"""Concatenate the JS and CSS parts into two scripts and one stylesheet, and hash asset names.

The parts rule (site/CONTRACT.md section 5, "JS parts"): the build concatenates the parts in
``site/js/`` in filename order into two ES modules, each with the parts its list names: the
page's, ``/assets/app.<h>.js`` (``JS_PARTS``), and the hopper's matcher,
``/assets/matcher.<h>.js`` (``MATCHER_PARTS``), which the list page loads only once a
visitor's list arrives (the owner's ruling of 2026-10-23, ``inventory_matcher_on_demand``).
Modules share no top-level names, so the matcher holds its own copy of the lower parts it
uses (``05-keys.js`` is in both lists), and hands its API to the page in one way: its last
line, ``MATCHER_START``, gives it to ``globalThis.tffMatcher``, which ``Data`` has defined as
a permanent setter. ``site/css/*.css`` is concatenated the same way into
``/assets/style.<h>.css``.

Each JS part declares exactly one top-level ``const``, named in its list (a part in both lists
has the same name in each). The lint refuses extra top-level declarations and statements (only
``Main.start();``, once, as the last line of ``90-main.js``, and ``MATCHER_START``, once, as
the last line of ``58-matcher.js``), ``import``/``export`` (dynamic ``import(`` too),
HTML-string APIs, ``eval``, ``Function(``, ``document.write``, browser storage APIs, request
and script APIs other than ``fetch`` (``OTHER_JS``), ``setAttribute('style'`` and absolute
URLs in ``fetch``; a script element anywhere but ``10-data.js`` (``SCRIPT_ELEMENT``: any
case and spacing, ``createElementNS`` and ``Core.el`` too), and there only once; the
hand-off's name, ``tffMatcher``, in code anywhere but Data's ``MATCHER_GLOBAL`` and
``MATCHER_START``; in code, a part's use of a const that only the other module holds
(modules share no top-level names, so it would be a ReferenceError); and in a part of the
matcher, any request, page, timer, clock or randomness API (``MATCHER_JS``). A file in
neither list is refused, and a part of both follows the rules of both. "In code" means
outside the lines that hold only a ``//`` comment, as the build ships it; every other rule
reads the comments too, to keep the lint dumb. The CSS lint refuses files outside ``CSS_PARTS``, ``@import``, ``@font-face`` and
``url()`` with a scheme or another host. Both report every problem at once (``AssetError``).
The interface font's ``@font-face`` rules come from ``font_faces``, which the build puts ahead
of the parts, so no part declares a font.

Every file under ``/assets/`` is named ``<stem>.<h>.<ext>``, where ``<h>`` is the first
``HASH_LEN`` hex digits of the sha256 of its bytes, so it can be cached as immutable.
"""

import hashlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

HASH_LEN = 10

# (file, top-level const) in concatenation order; must match site/CONTRACT.md section 5. The
# page's module, /assets/app.<h>.js, which every page loads.
JS_PARTS: tuple[tuple[str, str], ...] = (
    ("00-core.js", "Core"),
    ("05-keys.js", "Keys"),
    ("10-data.js", "Data"),
    ("15-state.js", "State"),
    ("20-view.js", "View"),
    ("25-render.js", "Render"),
    ("30-filters-ui.js", "FiltersUI"),
    ("35-announce.js", "Announce"),
    ("40-details.js", "Details"),
    ("45-specimens.js", "Specimens"),
    ("50-ext.js", "Ext"),
    ("90-main.js", "Main"),
)
# The hopper's matcher module, /assets/matcher.<h>.js: the parser and the matcher, loaded only
# once a visitor's list arrives (Data.loadMatcher; inventory_matcher_on_demand, 2026-10-23),
# with its own copy of Keys, which they use.
MATCHER_PARTS: tuple[tuple[str, str], ...] = (
    ("05-keys.js", "Keys"),
    ("52-owned-parse.js", "OwnedParse"),
    ("54-owned-match.js", "OwnedMatch"),
    ("58-matcher.js", "Matcher"),
)
CSS_PARTS: tuple[str, ...] = (
    "00-tokens.css",
    "10-base.css",
    "20-list.css",
    "25-filters.css",
    "30-details.css",
    "35-specimens.css",
    "40-pages.css",
    "45-blog.css",
)
# Substrings the JS lint refuses anywhere in a part (comments included, to keep the lint dumb).
FORBIDDEN_JS: tuple[str, ...] = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "eval(",
    "Function(",
    "document.write",
    "createContextualFragment",
    "setHTMLUnsafe",
    "parseHTMLUnsafe",
    "DOMParser",
    "srcdoc",
)
# Browser storage, refused the same way: nothing is stored (site/CONTRACT.md section 5). The
# built-file scan (tests/site/test_privacy.py) reads both modules for these and OTHER_JS's.
STORAGE_JS: tuple[str, ...] = (
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "document.cookie",
    "caches.",
    "serviceWorker",
    "cookieStore",
    "openDatabase",
    "navigator.storage",
    "getDirectory",
)
# Requests other than fetch(), which reads relative URLs from the DOM (section 5): WebRTC
# among them, which connect-src doesn't govern; scripts other than the matcher module's (a
# worker runs one); and style attributes, which the CSP blocks (style-src 'self'; set styles
# through CSSOM instead).
OTHER_JS: tuple[str, ...] = (
    "sendBeacon",
    "fetchLater",
    "XMLHttpRequest",
    "WebSocket",
    "WebTransport",
    "EventSource",
    "RTCPeerConnection",
    "Worker(",
    "importScripts",
    "setAttribute('style'",
    'setAttribute("style"',
)
# A script element, in any case and spacing, an HTML or SVG one (createElementNS) or through
# Core.el: only Data.loadMatcher adds one, for #list[data-matcher] (section 11). A tag held in
# a variable gets past it; the CSP's script-src 'self' still refuses any other site's script.
SCRIPT_ELEMENT = re.compile(
    r"(?:\bcreateElement(?:NS)?|(?<![\w$])el)\s*\(\s*(?:[^(),]*,\s*)?(['\"`])\s*script\s*\1",
    re.IGNORECASE,
)
DATA_PART = "10-data.js"
# Refused in a part of the matcher module, comments included: it is pure code, making no
# request and touching no page, timer, clock or randomness (tests/site/test_owned_js.py checks
# a wider list as the parts run). Its one global is the hand-off, MATCHER_START.
MATCHER_JS: tuple[str, ...] = (
    "fetch(",
    "document.",
    "window.",
    "navigator.",
    "location.",
    "createElement",
    "setTimeout(",
    "setInterval(",
    "Date.",
    "Date(",
    "Math.random",
)
# The one top-level statement besides the declarations in each module: the last line of its
# last part. The matcher's assigns its API, a frozen { OwnedParse, OwnedMatch }, to
# globalThis.tffMatcher, the permanent setter Data defined before it added the module's
# script; with no such setter, it defines the name itself, read-only and permanent, so Data
# refuses it (site/js/58-matcher.js).
MAIN_PART = "90-main.js"
MAIN_START = "Main.start();"
MATCHER_PART = "58-matcher.js"
MATCHER_GLOBAL = "tffMatcher"
MATCHER_START = f"Matcher.handOver('{MATCHER_GLOBAL}');"
_STARTS = {MAIN_PART: MAIN_START, MATCHER_PART: MATCHER_START}
# The only code lines that name the hand-off: Data's constant, which loadMatcher defines it
# by, and the matcher's last line, which hands over through it (section 5; no other part may
# touch it).
MATCHER_GLOBAL_LINES = {
    DATA_PART: f"  const MATCHER_GLOBAL = '{MATCHER_GLOBAL}';",
    MATCHER_PART: MATCHER_START,
}

_PAGE_CONSTS = dict(JS_PARTS)
_MATCHER_CONSTS = dict(MATCHER_PARTS)


def _free_names(names: list[str]) -> re.Pattern[str]:
    """A use of one of ``names`` as a name of its own: not a property (``api.OwnedParse``) and
    not part of a longer name (``MatchError``, ``loadMatcher``)."""
    return re.compile(rf"(?<![\w$.])({'|'.join(map(re.escape, names))})(?![\w$])")


# The consts only one module holds: a part of the other may not use them (section 5).
_PAGE_ONLY = _free_names([c for f, c in JS_PARTS if f not in _MATCHER_CONSTS])
_MATCHER_ONLY = _free_names([c for f, c in MATCHER_PARTS if f not in _PAGE_CONSTS])
_TOP_DECLARATION = re.compile(
    r"^(?:export\s+)?(const|let|var|class|function\*?|async\s+function)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
# A top-level line that only closes the declaration: `})();`, `});`, `})(Core);`, `}`. One
# optional semicolon at the end, so no second statement can hide behind it.
_CLOSER = re.compile(r"^[)\]}][)\]}()\w$.\s]*;?\s*(?://.*)?$")
_IMPORT_EXPORT = re.compile(r"^\s*(?:import|export)\b|\bimport\s*\(", re.MULTILINE)
_ABSOLUTE_FETCH = re.compile(r"fetch\(\s*['\"`](?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_BACKTICK = re.compile(r"(?<!\\)`")
_BLANK_RUN = re.compile(r"\n{3,}")
_CSS_FORBIDDEN = (
    (re.compile(r"@import\b", re.IGNORECASE), "@import (the site ships one stylesheet)"),
    (
        re.compile(r"@font-face\b", re.IGNORECASE),
        "@font-face (the build declares the interface font ahead of the parts)",
    ),
    (
        re.compile(r"url\(\s*['\"]?\s*(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE),
        "url() with a scheme or another host (the page loads only its own files)",
    ),
)


class AssetError(ValueError):
    """A JS or CSS part broke the parts rule. ``problems`` holds one line per violation."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s); first: {problems[0] if problems else '-'}")


@dataclass(frozen=True, slots=True)
class AssetManifest:
    """Logical name to hashed URL, for example ``{"app.js": "/assets/app.1a2b3c4d5e.js"}``."""

    urls: dict[str, str] = field(default_factory=dict)


def content_hash(data: bytes) -> str:
    """Return the asset hash of ``data``: the first ``HASH_LEN`` hex digits of its sha256."""
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


def hashed_name(name: str, data: bytes) -> str:
    """Return ``name`` with its content hash before the extension: ``app.js`` -> ``app.<h>.js``.

    Only the last path segment changes: ``specimens/inter.svg`` -> ``specimens/inter.<h>.svg``.
    """
    folder, slash, base = name.rpartition("/")
    stem, dot, ext = base.rpartition(".")
    hashed = (
        f"{stem}.{content_hash(data)}.{ext}" if dot and stem else f"{base}.{content_hash(data)}"
    )
    return f"{folder}{slash}{hashed}"


def lint_js_part(filename: str, source: str) -> list[str]:
    """Return parts-rule violations in one JS part; an empty list means it passes. A part of
    both modules (``JS_PARTS`` and ``MATCHER_PARTS``) is held to the rules of both."""
    in_page, in_matcher = filename in _PAGE_CONSTS, filename in _MATCHER_CONSTS
    if not (in_page or in_matcher):
        return [f"{filename}: not a JS part in site/CONTRACT.md section 5"]
    const = _PAGE_CONSTS.get(filename) or _MATCHER_CONSTS[filename]
    problems: list[str] = []
    declarations = _TOP_DECLARATION.findall(source)
    if declarations != [("const", const)]:
        found = ", ".join(f"{kind} {name}" for kind, name in declarations) or "none"
        problems.append(f"{filename}: top-level declarations ({found}); want one: const {const}")
    declaration = re.compile(rf"^const\s+{re.escape(const)}\s*=")
    start = _STARTS.get(filename)
    significant: list[tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        if not line.strip() or line[0].isspace():
            continue
        if line.startswith(("//", "/*", "*")):
            continue
        significant.append((lineno, line.rstrip()))
        if declaration.match(line) or _CLOSER.match(line):
            continue
        if start is not None and line.rstrip() == start:
            continue
        problems.append(f"{filename}:{lineno}: top-level statement: {line[:60]}")
    if start is not None:
        starts = [n for n, line in significant if line == start]
        if len(starts) != 1 or significant[-1][1] != start:
            problems.append(f"{filename}: {start} must be the last top-level line, once")
    problems += [
        f"{filename}:{_line_of(source, m.start())}: import or export"
        for m in _IMPORT_EXPORT.finditer(source)
    ]
    for needle in FORBIDDEN_JS:
        problems += [f"{filename}:{n}: forbidden: {needle}" for n in _lines_with(source, needle)]
    for needle in STORAGE_JS:
        problems += [f"{filename}:{n}: storage API: {needle}" for n in _lines_with(source, needle)]
    for needle in OTHER_JS:
        problems += [f"{filename}:{n}: forbidden: {needle}" for n in _lines_with(source, needle)]
    for match in _ABSOLUTE_FETCH.finditer(source):
        problems.append(f"{filename}:{_line_of(source, match.start())}: fetch of an absolute URL")
    scripts = [_line_of(source, m.start()) for m in SCRIPT_ELEMENT.finditer(source)]
    if filename != DATA_PART or len(scripts) > 1:
        problems += [
            f"{filename}:{n}: a script element (only {DATA_PART} adds one, once)" for n in scripts
        ]
    code = list(_code_lines(source))
    named = [n for n, line in code if MATCHER_GLOBAL in line]
    allowed = [n for n, line in code if line == MATCHER_GLOBAL_LINES.get(filename)]
    if named != allowed[:1]:
        problems += [
            f"{filename}:{n}: names {MATCHER_GLOBAL} (only {DATA_PART}'s MATCHER_GLOBAL and"
            f" {MATCHER_PART}'s last line do, once each)"
            for n in named
        ]
    others = [(_MATCHER_ONLY, "the matcher's")] if in_page else []
    others += [(_PAGE_ONLY, "the page's")] if in_matcher else []
    for pattern, module in others:
        problems += [
            f"{filename}:{n}: {m.group(1)} is a part of {module} module only (modules share no"
            " top-level names)"
            for n, line in code
            for m in pattern.finditer(line)
        ]
    if in_matcher:
        for needle in MATCHER_JS:
            problems += [
                f"{filename}:{n}: not in the matcher module (pure code): {needle}"
                for n in _lines_with(source, needle)
            ]
    return problems


def lint_css_part(filename: str, source: str) -> list[str]:
    """Return parts-rule violations in one CSS part; an empty list means it passes."""
    if filename not in CSS_PARTS:
        return [f"{filename}: not a CSS part in site/CONTRACT.md section 6"]
    code = _CSS_COMMENT.sub(lambda m: "\n" * m.group().count("\n"), source)
    return [
        f"{filename}:{_line_of(code, match.start())}: {what}"
        for pattern, what in _CSS_FORBIDDEN
        for match in pattern.finditer(code)
    ]


def concat_js(parts_dir: Path, parts: tuple[tuple[str, str], ...] = JS_PARTS) -> str:
    """Lint every ``parts_dir/*.js`` and concatenate those ``parts`` names (``JS_PARTS``, the
    page's module, or ``MATCHER_PARTS``, the matcher's) in filename order into one ES module,
    without whole-line comments (``strip_js_comments``)."""
    names = {name for name, _ in parts}
    return _concat(Path(parts_dir), "*.js", lint_js_part, strip_js_comments, names.__contains__)


def strip_js_comments(source: str) -> str:
    """``source`` without the lines that hold only a ``//`` comment, which ship no code but
    cost the page budget (M2 step 10). A line inside a template literal stays: a backtick
    that no backslash escapes opens or closes one. Comments after code stay too."""
    return "\n".join(line for _, line in _code_lines(source))


def _code_lines(source: str) -> Iterator[tuple[int, str]]:
    """Each line ``strip_js_comments`` keeps, with its line number in ``source``."""
    inside = False
    for lineno, line in enumerate(source.split("\n"), 1):
        if inside or not line.lstrip().startswith("//"):
            yield lineno, line
        if len(_BACKTICK.findall(line)) % 2:
            inside = not inside


def strip_css_comments(source: str) -> str:
    """``source`` without its ``/* … */`` comments and the lines they leave empty (M2 step
    10). The parts put no comment marker inside a string."""
    stripped = _CSS_COMMENT.sub("", source)
    lines = "\n".join(line.rstrip() for line in stripped.split("\n"))
    return _BLANK_RUN.sub("\n\n", lines).lstrip("\n")


def font_faces(family: str, faces: list[tuple[str, str]], weights: str) -> str:
    """One ``@font-face`` rule per ``(url, font-style)`` in ``faces``, all in ``family``.

    ``font-display: optional``: the browser uses the font only if it is there within about
    100 ms of first use (the page preloads it, so it usually is) and never swaps it in later,
    so no text moves. Otherwise that page view keeps the next font of ``--font-ui``, which
    has the same widths, and the next page finds the font in the cache. (Kerning still
    differs a little, so a swap could move a line break, and ``swap`` would shift text.)"""
    return "".join(
        "@font-face {\n"
        f'  font-family: "{family}";\n'
        f"  font-style: {style};\n"
        f"  font-weight: {weights};\n"
        "  font-display: optional;\n"
        f'  src: url("{url}") format("woff2");\n'
        "}\n\n"
        for url, style in faces
    )


def concat_css(parts_dir: Path) -> str:
    """Concatenate ``parts_dir/*.css`` in filename order into one stylesheet, without
    comments (``strip_css_comments``)."""
    return _concat(Path(parts_dir), "*.css", lint_css_part, strip_css_comments)


def write_hashed(out_dir: Path, name: str, data: bytes) -> str:
    """Write ``data`` as ``out_dir/assets/<hashed name>`` and return its URL path."""
    if name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError(f"asset name must be a plain relative path: {name!r}")
    rel = f"assets/{hashed_name(name, data)}"
    path = Path(out_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"/{rel}"


def _concat(
    parts_dir: Path,
    pattern: str,
    lint: Callable[[str, str], list[str]],
    strip: Callable[[str], str],
    keep: Callable[[str], bool] = lambda name: True,
) -> str:
    """Lint every file matching ``pattern`` and join those ``keep`` takes, in filename order."""
    paths = sorted(parts_dir.glob(pattern), key=lambda p: p.name)
    problems: list[str] = []
    texts: list[str] = []
    for path in paths:
        # utf-8-sig drops a byte-order mark, which would land mid-file once concatenated.
        source = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        problems += lint(path.name, source)
        if keep(path.name):
            texts.append(strip(source).strip("\n") + "\n")
    if not texts:
        problems.append(f"{parts_dir}: no {pattern} parts")
    if problems:
        raise AssetError(problems)
    return "\n".join(texts)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _lines_with(text: str, needle: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if needle in line]
