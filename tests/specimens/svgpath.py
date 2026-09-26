"""A small SVG path-data reader for the tests: relative, compact paths back to absolute points.

It implements the SVG 1.1 grammar the specimens use (m l h v q t c s z, upper
and lower case, implicit repetition, implicit ``l`` after ``m``, numbers glued
by a minus sign) independently of the writer, so a round trip checks it.
"""

import re

type Point = tuple[int, int]
type Segment = tuple[str, tuple[Point, ...]]  # ("L"|"Q"|"C", points ending at the end point)
type Contour = tuple[Point, tuple[Segment, ...]]

_TOKEN = re.compile(r"[MmLlHhVvQqTtCcSsZz]|-?\d+")
_ARITY = {"m": 2, "l": 2, "h": 1, "v": 1, "q": 4, "t": 2, "c": 6, "s": 4, "z": 0}


def parse(d: str) -> list[Contour]:
    """Absolute contours of path data ``d`` (integers only)."""
    if _TOKEN.sub("", d).strip(" "):
        raise ValueError(f"unexpected characters in path data: {_TOKEN.sub('', d)!r}")
    tokens = _TOKEN.findall(d)
    contours: list[Contour] = []
    segs: list[Segment] = []
    cur: Point = (0, 0)
    start: Point | None = None
    prev_ctrl: Point | None = None
    prev_kind = ""
    cmd = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "zZ":
                if start is None:
                    raise ValueError("z without a contour")
                contours.append((start, tuple(segs)))
                cur, start, segs, prev_kind = start, None, [], ""
                continue
        elif not cmd or cmd in "zZ":
            raise ValueError(f"number without a command at token {i}")
        arity = _ARITY[cmd.lower()]
        nums = [int(t) for t in tokens[i : i + arity]]
        if len(nums) != arity or any(t.isalpha() for t in tokens[i : i + arity]):
            raise ValueError(f"command {cmd} needs {arity} numbers at token {i}")
        i += arity
        rel = cmd.islower()

        def pt(x: int, y: int, *, rel: bool = rel, base: Point = cur) -> Point:
            return (base[0] + x, base[1] + y) if rel else (x, y)

        match cmd.lower():
            case "m":
                if start is not None:
                    contours.append((start, tuple(segs)))
                cur = start = pt(*nums)
                segs, prev_kind = [], ""
                cmd = "l" if rel else "L"  # following pairs are line-tos
                continue
            case "l":
                end = pt(*nums)
                segs.append(("L", (end,)))
                prev_kind = "L"
            case "h":
                end = (cur[0] + nums[0], cur[1]) if rel else (nums[0], cur[1])
                segs.append(("L", (end,)))
                prev_kind = "L"
            case "v":
                end = (cur[0], cur[1] + nums[0]) if rel else (cur[0], nums[0])
                segs.append(("L", (end,)))
                prev_kind = "L"
            case "q":
                c, end = pt(nums[0], nums[1]), pt(nums[2], nums[3])
                segs.append(("Q", (c, end)))
                prev_ctrl, prev_kind = c, "Q"
            case "t":
                c = _reflect(prev_ctrl, cur) if prev_kind == "Q" and prev_ctrl else cur
                end = pt(*nums)
                segs.append(("Q", (c, end)))
                prev_ctrl, prev_kind = c, "Q"
            case "c":
                c1, c2, end = pt(nums[0], nums[1]), pt(nums[2], nums[3]), pt(nums[4], nums[5])
                segs.append(("C", (c1, c2, end)))
                prev_ctrl, prev_kind = c2, "C"
            case "s":
                c1 = _reflect(prev_ctrl, cur) if prev_kind == "C" and prev_ctrl else cur
                c2, end = pt(nums[0], nums[1]), pt(nums[2], nums[3])
                segs.append(("C", (c1, c2, end)))
                prev_ctrl, prev_kind = c2, "C"
        cur = end
    if start is not None:
        contours.append((start, tuple(segs)))
    return contours


def _reflect(c: Point, about: Point) -> Point:
    return 2 * about[0] - c[0], 2 * about[1] - c[1]


def points(contours: list[Contour]) -> list[Point]:
    """Every point, on- and off-curve."""
    return [p for start, segs in contours for p in (start, *(q for _, pts in segs for q in pts))]
