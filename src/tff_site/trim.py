"""Serve the committed specimens trimmed to the family's name (PLAN-REVIEWERS-1.md step 7a).

The owner's site rulings of 2026-10-05: a row's specimen shows only the family's name, larger
(``specimen_name_only_list``), and until ``build/specimens`` is redrawn names-only (step 7d,
which removes this module) the site build serves each committed two-line specimen cut down to
its name line (``specimen_trim_served``). Nothing in ``build/`` or the catalogs changes.

A specimen is one ``<path>`` of relative commands (``tff_catalog.specimens.render``): the
name's contours, then the sample line's. ``name_only`` cuts the path where the sample line
starts, at the largest backward jump in x between two contours' start points: the sample line
starts again near x = 0 after the name has run right. (A rule on height alone cuts 15 of the
500 files wrongly.) The renderer shifts the whole drawing right when ink reaches left of
x = 0; where the sample line set that shift, the name moves back to its own ink's left edge.
The view box becomes the name's: as wide as its ink (the file doesn't record the name's
advance), and ``NAME_BOX`` units tall below the top margin the file had, grown as the renderer
grows it to hold ink below that.

Only files whose sha256 ``PINS_FILE`` lists are trimmed: the 500 committed two-line specimens,
each checked once against ``render(name_only=True)`` from its font. Any other file is served as
it is, since a wrong cut would ship a plausible but wrong image, not an error. ``PINS_FILE``
is ``sha256sum``'s output in ``build/`` (``sha256sum specimens/*.svg``); add a file to it only
after checking its cut the same way.
"""

import functools
import itertools
import re
from dataclasses import dataclass
from pathlib import Path

PINS_FILE = Path(__file__).with_name("trim.sha256")
# render.py's line boxes at 256 units per em: the name's line is 1.2 em (307.2 units, framed
# as 308), and the two lines together 1.92 em (491.52, framed as 492).
NAME_BOX = 308
TWO_LINE_BOX = 492

_PIN = re.compile(r"([0-9a-f]{64})  specimens/[a-z0-9-]+\.svg")
# The renderer's output exactly: fixed attributes, one path of relative commands and integers.
_SVG = re.compile(
    rb'<svg xmlns="http://www\.w3\.org/2000/svg" width="(\d+)" height="(\d+)" '
    rb'viewBox="0 0 \1 \2"><path d="(m[mlhvqtcsz\d -]*)"/></svg>\n'
)
_TOKEN = re.compile(r"[mlhvqtcsz]|-?\d+")
_ARITY = {"m": 2, "l": 2, "h": 1, "v": 1, "q": 4, "t": 2, "c": 6, "s": 4}
_CURVE = {"q": "q", "t": "q", "c": "c", "s": "c"}  # the kind of curve each command draws

type Point = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Contour:
    """A contour's start point and the bounds of all its points, control points included (as
    ``RelPathPen.bounds`` counts them)."""

    start: Point
    left: int
    top: int
    right: int
    bottom: int


@functools.cache
def pinned() -> frozenset[str]:
    """The sha256 of every specimen ``name_only`` may trim (``PINS_FILE``)."""
    pins = set()
    for number, line in enumerate(PINS_FILE.read_text(encoding="ascii").splitlines(), 1):
        match = _PIN.fullmatch(line)
        if match is None:
            raise ValueError(f"{PINS_FILE.name} line {number}: not '<sha256>  specimens/<id>.svg'")
        pins.add(match[1])
    return frozenset(pins)


def name_only(svg: bytes) -> bytes:
    """The two-line specimen ``svg`` cut down to its name line (see the module docstring).

    Raises ValueError for a file the renderer didn't write, or one with no second line.
    """
    match = _SVG.fullmatch(svg)
    if match is None:
        raise ValueError("not a specimen as tff_catalog.specimens.render writes it")
    file_height, d = int(match[2]), match[3].decode("ascii")
    contours = _contours(d)
    jumps = [b.start[0] - a.start[0] for a, b in itertools.pairwise(contours)]
    if not jumps or min(jumps) >= 0:
        raise ValueError("no second line to cut off")
    cut = jumps.index(min(jumps)) + 1
    name, sample = contours[:cut], contours[cut:]
    left = min(c.left for c in name)
    # The sample line's ink starts at x = 0 and the name's right of it: the renderer moved
    # both lines right for the sample line's sake (or the sample line just touches x = 0).
    # The file doesn't say how far, so the name's ink goes to x = 0. In 6 of the 500 pinned
    # files that is 2 to 13 units left of where render(name_only=True) puts it.
    shift = left if min(c.left for c in sample) == 0 < left else 0
    # The file is TWO_LINE_BOX tall below its top margin, unless ink reaches its bottom edge;
    # then the margin can't be read and is taken as none, which gives the renderer's height
    # in every pinned file.
    ink_bottom = max(c.bottom for c in contours)
    margin = file_height - TWO_LINE_BOX if ink_bottom < file_height else 0
    if margin < 0:
        raise ValueError(f"{file_height} units tall, less than two lines")
    width = max(c.right for c in name) - shift
    height = max(NAME_BOX + margin, max(c.bottom for c in name))
    # The first move is from (0, 0) and every later one from the contour before, so moving
    # the name changes the first move's x alone. "m" starts every contour and nothing else.
    pieces = d.split("m")[1 : cut + 1]
    x, y = contours[0].start
    sep = "" if y < 0 else " "  # the renderer's: no space before a minus sign
    if not pieces[0].startswith(f"{x}{sep}{y}"):
        raise ValueError("the first move isn't written as the renderer writes it")
    first = f"{x - shift}{sep}{y}{pieces[0].removeprefix(f'{x}{sep}{y}')}"
    path = "m" + "m".join([first, *pieces[1:]])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><path d="{path}"/></svg>\n'
    ).encode("ascii")


def _contours(d: str) -> list[_Contour]:
    """Every contour of the relative path data ``d``, in order; each must be closed."""
    if _TOKEN.sub("", d).strip(" "):
        raise ValueError("path data with something besides commands and integers")
    tokens = _TOKEN.findall(d)
    contours: list[_Contour] = []
    points: list[Point] = []  # the open contour's, start first
    cur: Point = (0, 0)
    ctrl: Point | None = None  # the last control point, and its curve's kind (q or c)
    kind = cmd = ""
    i = 0
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd == "z":
                if not points:
                    raise ValueError("z without a contour")
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                contours.append(_Contour(points[0], min(xs), min(ys), max(xs), max(ys)))
                cur, points, kind = points[0], [], ""
                continue
        elif cmd in ("", "z"):
            raise ValueError(f"a number without a command at token {i}")
        args = tokens[i : i + _ARITY[cmd]]
        if len(args) < _ARITY[cmd] or any(a.isalpha() for a in args):
            raise ValueError(f"{cmd} needs {_ARITY[cmd]} numbers at token {i}")
        i += len(args)
        n = [int(a) for a in args]
        rel = [(cur[0] + n[j], cur[1] + n[j + 1]) for j in range(0, len(n) - 1, 2)]
        if cmd == "m":
            if points:
                raise ValueError("a contour left open")
            cur = rel[0]
            points = [cur]
            cmd, kind = "l", ""  # pairs after "m" are line-tos
            continue
        if not points:
            raise ValueError(f"{cmd} before any m")
        match cmd:
            case "h":
                new = [(cur[0] + n[0], cur[1])]
            case "v":
                new = [(cur[0], cur[1] + n[0])]
            case "t" | "s":
                # The first control point reflects the last one if that ended the same kind
                # of curve, and is the current point otherwise.
                first = cur
                if ctrl is not None and kind == _CURVE[cmd]:
                    first = (2 * cur[0] - ctrl[0], 2 * cur[1] - ctrl[1])
                new = [first, *rel]
            case _:  # l, q, c
                new = rel
        ctrl, kind = (new[-2], _CURVE[cmd]) if cmd in _CURVE else (None, "")
        points += new
        cur = new[-1]
    if points:
        raise ValueError("a contour left open")
    return contours
