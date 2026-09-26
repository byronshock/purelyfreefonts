"""Concatenate the JS and CSS parts into one script and one stylesheet, and hash asset names.

The parts rule (site/CONTRACT.md, "JS parts"): the build concatenates ``site/js/*.js`` in
filename order into one ES module, ``/assets/app.<h>.js``, and ``site/css/*.css`` the same
way into ``/assets/style.<h>.css``. Each JS part declares exactly one top-level ``const``,
named in ``JS_PARTS``. The lint refuses extra top-level declarations and statements (only
``Main.start();``, once, as the last line of ``90-main.js``), ``import``/``export`` (dynamic
``import(`` too), HTML-string APIs, ``eval``, ``Function(``, ``document.write``, browser
storage APIs, request APIs other than ``fetch`` (``OTHER_JS``), ``setAttribute('style'`` and
absolute URLs in ``fetch``; a file outside ``JS_PARTS`` is refused.
The CSS lint refuses files outside ``CSS_PARTS``, ``@import``, ``@font-face`` and ``url()``
with a scheme or another host. Both report every problem at once (``AssetError``).

Every file under ``/assets/`` is named ``<stem>.<h>.<ext>``, where ``<h>`` is the first
``HASH_LEN`` hex digits of the sha256 of its bytes, so it can be cached as immutable.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

HASH_LEN = 10

# (file, top-level const) in concatenation order; must match site/CONTRACT.md.
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
CSS_PARTS: tuple[str, ...] = (
    "00-tokens.css",
    "10-base.css",
    "20-list.css",
    "25-filters.css",
    "30-details.css",
    "35-specimens.css",
    "40-pages.css",
)
# Substrings the JS lint refuses anywhere in a part (comments included, to keep the lint dumb).
FORBIDDEN_JS: tuple[str, ...] = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "eval(",
    "Function(",
    "document.write",
)
# Browser storage, refused the same way: nothing is stored (site/CONTRACT.md section 5).
STORAGE_JS: tuple[str, ...] = (
    "localStorage",
    "sessionStorage",
    "indexedDB",
    "document.cookie",
    "caches.",
    "serviceWorker",
    "cookieStore",
)
# Requests other than fetch(), which reads relative URLs from the DOM (section 5), and style
# attributes, which the CSP blocks (style-src 'self'; set styles through CSSOM instead).
OTHER_JS: tuple[str, ...] = (
    "sendBeacon",
    "XMLHttpRequest",
    "WebSocket",
    "EventSource",
    "setAttribute('style'",
    'setAttribute("style"',
)
# The one top-level statement besides the declarations: the last line of the last part.
MAIN_PART = "90-main.js"
MAIN_START = "Main.start();"

_CONSTS = dict(JS_PARTS)
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
_CSS_FORBIDDEN = (
    (re.compile(r"@import\b", re.IGNORECASE), "@import (the site ships one stylesheet)"),
    (re.compile(r"@font-face\b", re.IGNORECASE), "@font-face (the interface uses system fonts)"),
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
    """Return parts-rule violations in one JS part; an empty list means it passes."""
    const = _CONSTS.get(filename)
    if const is None:
        return [f"{filename}: not a JS part in site/CONTRACT.md section 5"]
    problems: list[str] = []
    declarations = _TOP_DECLARATION.findall(source)
    if declarations != [("const", const)]:
        found = ", ".join(f"{kind} {name}" for kind, name in declarations) or "none"
        problems.append(f"{filename}: top-level declarations ({found}); want one: const {const}")
    declaration = re.compile(rf"^const\s+{re.escape(const)}\s*=")
    significant: list[tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        if not line.strip() or line[0].isspace():
            continue
        if line.startswith(("//", "/*", "*")):
            continue
        significant.append((lineno, line.rstrip()))
        if declaration.match(line) or _CLOSER.match(line):
            continue
        if filename == MAIN_PART and line.rstrip() == MAIN_START:
            continue
        problems.append(f"{filename}:{lineno}: top-level statement: {line[:60]}")
    if filename == MAIN_PART:
        starts = [n for n, line in significant if line == MAIN_START]
        if len(starts) != 1 or significant[-1][1] != MAIN_START:
            problems.append(f"{filename}: {MAIN_START} must be the last top-level line, once")
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


def concat_js(parts_dir: Path) -> str:
    """Lint and concatenate ``parts_dir/*.js`` in filename order into one ES module."""
    return _concat(Path(parts_dir), "*.js", lint_js_part)


def concat_css(parts_dir: Path) -> str:
    """Concatenate ``parts_dir/*.css`` in filename order into one stylesheet."""
    return _concat(Path(parts_dir), "*.css", lint_css_part)


def write_hashed(out_dir: Path, name: str, data: bytes) -> str:
    """Write ``data`` as ``out_dir/assets/<hashed name>`` and return its URL path."""
    if name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/")):
        raise ValueError(f"asset name must be a plain relative path: {name!r}")
    rel = f"assets/{hashed_name(name, data)}"
    path = Path(out_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"/{rel}"


def _concat(parts_dir: Path, pattern: str, lint: Callable[[str, str], list[str]]) -> str:
    paths = sorted(parts_dir.glob(pattern), key=lambda p: p.name)
    if not paths:
        raise AssetError([f"{parts_dir}: no {pattern} parts"])
    problems: list[str] = []
    texts: list[str] = []
    for path in paths:
        # utf-8-sig drops a byte-order mark, which would land mid-file once concatenated.
        source = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        problems += lint(path.name, source)
        texts.append(source.rstrip() + "\n")
    if problems:
        raise AssetError(problems)
    return "\n".join(texts)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _lines_with(text: str, needle: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if needle in line]
