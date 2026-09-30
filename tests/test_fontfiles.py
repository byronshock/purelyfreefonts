"""Font-file facts: fontTools reading, HTTP Range reads, the facts cache and the read log (M1 step 5b).

Offline tests build their fonts with fontTools' FontBuilder and serve them from
memory (``FakeServer``, which answers ``get`` and ``get_range`` as the project's
``Fetcher`` does). Tests marked ``network`` use the pinned real OFL fonts of
``tests/fixtures/specimen-fonts.toml``, cached in ``~/.cache/tff/fonts/<sha256>``.
"""

import hashlib
import io
import json
import tomllib
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import FIXTURES, ROOT

from tff_catalog import fontfiles, jsonio
from tff_catalog.fetch import FetchError, FetchResult
from tff_catalog.fontfiles import (
    CACHE_FILE,
    FACT_TABLES,
    FIRST_READ,
    FileRead,
    FontFacts,
    FontFactsMissing,
    FontFileCache,
    FontFileError,
    ReadLog,
    cache_path,
    cmap_ranges,
    facts_for,
    facts_from_bytes,
    facts_from_read,
    git_blob_sha1,
    is_font_url,
    plan_ranges,
    read_font,
    read_tables,
    record_reads,
    recorded_reads,
    sfnt_from_tables,
)
from tff_catalog.records import FontFileRef
from tff_catalog.store import SnapshotFrozen, Store

FETCHED_AT = datetime(2026, 9, 25, tzinfo=UTC)
LATIN = tuple(range(0x20, 0x7F))
FONT_CACHE = Path.home() / ".cache" / "tff" / "fonts"
SPECIMEN_FONTS = FIXTURES / "specimen-fonts.toml"


# --- building fonts -----------------------------------------------------------------------------


def build_font(
    *,
    family: str = "Synth Sans",
    style: str = "Regular",
    codepoints: Iterable[int] = LATIN,
    mono: bool = False,
    variable: bool = False,
    cff: bool = False,
    flavor: str | None = None,
    panose: tuple[int, int, int] = (2, 11, 3),  # bFamilyType, bSerifStyle, bProportion
    family_class: int = 0x0800,
    points: int = 4,  # points per outline: more makes a bigger glyf table
    license_url: str | None = "https://openfontlicense.org",
) -> bytes:
    """A small, valid font made with FontBuilder; the same arguments give the same bytes."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.tables.O_S_2f_2 import Panose

    cps = sorted(set(codepoints))
    names = [".notdef", *(f"uni{cp:04X}" for cp in cps)]
    fb = FontBuilder(1000, isTTF=not cff)
    fb.font["head"].created = fb.font["head"].modified = 3_000_000_000
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({cp: f"uni{cp:04X}" for cp in cps})
    widths = {n: 600 if mono else 400 + 37 * (i % 7) for i, n in enumerate(names)}
    if cff:
        charstrings = {}
        for i, n in enumerate(names):
            pen = T2CharStringPen(widths[n], None)
            _outline(pen, points, i)
            charstrings[n] = pen.getCharString()
        fb.setupCFF(f"{family.replace(' ', '')}-{style}", {"FullName": family}, charstrings, {})
    else:
        glyphs = {}
        for i, n in enumerate(names):
            pen = TTGlyphPen(None)
            _outline(pen, points, i)
            glyphs[n] = pen.glyph()
        fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({n: (widths[n], 50) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    name_strings = {
        "familyName": family,
        "styleName": style,
        "fullName": f"{family} {style}",
        "psName": f"{family.replace(' ', '')}-{style}",
        "version": "Version 1.000",
        "licenseDescription": "This Font Software is licensed under the SIL Open Font License, Version 1.1.",
    }
    if license_url:
        name_strings["licenseInfoURL"] = license_url
    fb.setupNameTable(name_strings)
    pan = Panose()
    pan.bFamilyType, pan.bSerifStyle, pan.bProportion = panose
    fb.setupOS2(
        sTypoAscender=800, usWinAscent=800, usWinDescent=200, panose=pan, sFamilyClass=family_class
    )
    fb.setupPost(isFixedPitch=1 if mono else 0)
    if variable:
        fb.setupFvar(axes=[("wght", 100, 400, 900, "Weight")], instances=[])
    fb.font.flavor = flavor
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


def _outline(pen: Any, points: int, seed: int) -> None:
    # Hash-derived coordinates: deterministic, yet too irregular for WOFF to squeeze away.
    noise = hashlib.shake_128(seed.to_bytes(4)).digest(4 * points)
    pen.moveTo((50, 0))
    for i in range(1, max(points, 3)):
        x, y = (
            int.from_bytes(noise[4 * i : 4 * i + 2]),
            int.from_bytes(noise[4 * i + 2 : 4 * i + 4]),
        )
        pen.lineTo((50 + x % 400, y % 700))
    pen.closePath()


def big_font(**kwargs: Any) -> bytes:
    """A font big enough (about 300 KB) that range reads beat a whole download."""
    return build_font(codepoints=(*LATIN, *range(0x4E00, 0x4E00 + 1500)), points=24, **kwargs)


def collection(*fonts: bytes) -> bytes:
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.ttCollection import TTCollection

    ttc = TTCollection()
    ttc.fonts = [TTFont(io.BytesIO(f)) for f in fonts]
    out = io.BytesIO()
    ttc.save(out)
    return out.getvalue()


def raw_tables(data: bytes, tags: Iterable[str]) -> dict[str, bytes]:
    """What fontTools' own reader gives for each table (decompressed)."""
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data), fontNumber=0 if data[:4] == b"ttcf" else -1)
    return {t: bytes(font.reader[t]) for t in tags if t in font.reader}


# --- a fake server ------------------------------------------------------------------------------


def fetch_result(
    url: str, status: int, body: bytes, headers: Sequence[tuple[str, str]] = ()
) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        status=status,
        headers=tuple(headers),
        content=body,
        path=None,
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
        fetched_at=FETCHED_AT,
    )


@dataclass
class FakeServer:
    """In-memory files behind the two ``Fetcher`` calls fontfiles makes.

    ``ranges=False`` plays a server that ignores Range and sends the whole file;
    ``ranges=1`` one that honours only the first Range request. ``calls`` lists
    ``(url, start, end)``, with None bounds for a plain GET.
    """

    files: dict[str, bytes]
    ranges: bool | int = True
    calls: list[tuple[str, int | None, int | None]] = field(default_factory=list)

    def _body(self, url: str) -> bytes:
        if url not in self.files:
            raise FetchError(f"HTTP 404 for {url}")
        return self.files[url]

    def get(self, url: str, **kwargs: Any) -> FetchResult:
        self.calls.append((url, None, None))
        return fetch_result(url, 200, self._body(url))

    def get_range(
        self, url: str, start: int, end: int, *, budget: str | None = None
    ) -> FetchResult:
        if start < 0 or end < start:  # as Fetcher.get_range
            raise ValueError(f"bad byte range {start}-{end}")
        self.calls.append((url, start, end))
        data = self._body(url)
        honoured = self.ranges is True or len(self.calls) <= int(self.ranges)
        if not honoured:
            return fetch_result(url, 200, data)
        last = min(end, len(data) - 1)
        span = f"bytes {start}-{last}/{len(data)}"
        return fetch_result(url, 206, data[start : last + 1], [("content-range", span)])


URL = "https://fonts.example/synth/SynthSans-Regular.ttf"
URL2 = "https://fonts.example/synth/SynthMono-Regular.ttf"


# --- FontFacts ----------------------------------------------------------------------------------


def test_facts_from_bytes_reads_every_field() -> None:
    data = build_font(panose=(2, 11, 3), family_class=0x0801)
    ff = facts_from_bytes(data)
    assert ff.sha256 == hashlib.sha256(data).hexdigest()
    assert ff.git_blob == git_blob_sha1(data)
    assert ff.format == "ttf"
    assert ff.cmap == ((0x20, 0x7E),)
    assert ff.family_name == "Synth Sans"
    assert ff.full_name == "Synth Sans Regular"
    assert ff.postscript_name == "SynthSans-Regular"
    assert ff.version == "Version 1.000"
    assert ff.license_description is not None
    assert ff.license_description.startswith("This Font Software is licensed under the SIL")
    assert ff.license_url == "https://openfontlicense.org"
    assert ff.is_fixed_pitch is False
    assert (ff.panose_family, ff.panose_serif, ff.panose_proportion) == (2, 11, 3)
    assert ff.family_class == 0x0801
    assert ff.axes == ()


def test_monospace_and_variable_fonts() -> None:
    mono = facts_from_bytes(build_font(family="Synth Mono", mono=True, panose=(2, 11, 9)))
    assert mono.is_fixed_pitch is True
    assert mono.panose_proportion == 9
    var = facts_from_bytes(build_font(variable=True))
    assert var.axes == (("wght", 100.0, 400.0, 900.0),)


def test_typographic_family_name_wins() -> None:
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(build_font(style="Bold")))
    font["name"].setName("Synth Sans Family", 16, 3, 1, 0x409)
    out = io.BytesIO()
    font.save(out)
    assert facts_from_bytes(out.getvalue()).family_name == "Synth Sans Family"


@pytest.mark.parametrize(
    ("cff", "flavor", "expected"),
    [
        (False, None, "ttf"),
        (True, None, "otf"),
        (False, "woff", "woff"),
        (True, "woff", "woff"),
        (False, "woff2", "woff2"),
        (True, "woff2", "woff2"),
    ],
)
def test_every_container_gives_the_same_facts(cff: bool, flavor: str | None, expected: str) -> None:
    plain = facts_from_bytes(build_font(cff=cff, mono=True, panose=(2, 11, 9)))
    ff = facts_from_bytes(build_font(cff=cff, mono=True, panose=(2, 11, 9), flavor=flavor))
    assert ff.format == expected
    assert replace(ff, sha256=plain.sha256, git_blob=plain.git_blob, format=plain.format) == plain


def test_a_collection_gives_its_first_font() -> None:
    data = collection(build_font(family="First Sans"), build_font(family="Second Sans"))
    ff = facts_from_bytes(data)
    assert ff.family_name == "First Sans"
    assert ff.format == "ttf"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "not a font"),
        (b"<!DOCTYPE html><html></html>", "not a font"),
        (b"PK\x03\x04" + b"\0" * 40, "zip archive"),
        (b"\x00\x01\x00\x00\x00\x09" + b"\0" * 20, "cannot read"),
    ],
)
def test_non_fonts_raise(data: bytes, message: str) -> None:
    with pytest.raises(FontFileError, match=message):
        facts_from_bytes(data)


def test_a_real_zip_is_refused() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("SynthSans-Regular.ttf", build_font())
    with pytest.raises(FontFileError, match="zip archive"):
        facts_from_bytes(out.getvalue())


def test_a_wrong_git_blob_raises() -> None:
    with pytest.raises(FontFileError, match="git blob"):
        facts_from_bytes(build_font(), git_blob="0" * 40)


def test_git_blob_sha1_is_gits() -> None:
    assert git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_json_round_trip_is_exact() -> None:
    ff = facts_from_bytes(build_font(variable=True, codepoints=[0x41, 0x42, 0x43, 0x100, 0x2013]))
    assert FontFacts.from_json(json.loads(jsonio.canonical_bytes(ff.to_json()))) == ff


def test_from_json_is_strict_but_reads_rows_without_the_newer_fields() -> None:
    d = facts_from_bytes(build_font()).to_json()
    with pytest.raises(ValueError, match="unknown fields"):
        FontFacts.from_json({**d, "colour": "red"})
    with pytest.raises(ValueError, match="missing fields"):
        FontFacts.from_json({k: v for k, v in d.items() if k != "cmap"})
    with pytest.raises(ValueError, match="format"):
        FontFacts.from_json({**d, "format": "pfb"})
    newer = ("panose_family", "panose_serif", "family_class", "copyright")
    old = {k: v for k, v in d.items() if k not in newer}
    assert FontFacts.from_json(old).panose_family is None


def test_cmap_ranges_and_codepoints() -> None:
    assert cmap_ranges([5, 1, 2, 3, 9, 10, 2]) == ((1, 3), (5, 5), (9, 10))
    assert cmap_ranges([]) == ()
    ff = facts_from_bytes(build_font(codepoints=[0x41, 0x42, 0x44]))
    assert ff.cmap == ((0x41, 0x42), (0x44, 0x44))
    assert ff.codepoints() == frozenset({0x41, 0x42, 0x44})


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (
            "https://raw.githubusercontent.com/google/fonts/abc/ofl/inter/Inter%5Bopsz,wght%5D.ttf",
            True,
        ),
        ("https://cdn.example/x/Font-Regular.WOFF2", True),
        ("https://cdn.example/x/Font.otf?raw=1", True),
        ("https://github.com/o/r/releases/download/v1/Font.zip", False),
        ("https://example.com/fonts/font", False),
        ("http://cdn.example/x/Font.ttf", False),
    ],
)
def test_is_font_url(url: str, ok: bool) -> None:
    assert is_font_url(url) is ok


# --- range reads --------------------------------------------------------------------------------


def test_plan_ranges_merges_close_spans() -> None:
    spans = [(100, 200), (0, 50), (250, 300)]
    assert plan_ranges(spans, gap=50) == [(0, 300)]
    assert plan_ranges(spans, gap=49) == [(0, 50), (100, 200), (250, 300)]
    assert plan_ranges([(0, 10), (5, 8)], gap=0) == [(0, 10)]
    assert plan_ranges([], gap=10) == []


@pytest.mark.parametrize("cff", [False, True])
def test_read_tables_by_range_matches_fonttools(cff: bool) -> None:
    data = big_font(cff=cff, variable=not cff)
    assert len(data) > 4 * FIRST_READ
    server = FakeServer({URL: data})
    read = read_font(URL, FACT_TABLES, server)
    assert read.data is None
    assert read.tables == raw_tables(data, FACT_TABLES)
    assert read.format == ("otf" if cff else "ttf")
    assert 1 <= read.requests <= 3
    assert all(start is not None for _, start, _ in server.calls)
    assert read.received < len(data) / 2
    assert read_tables(URL, ("name", "fvar"), FakeServer({URL: data})) == raw_tables(
        data, ("name", "fvar")
    )


@pytest.mark.parametrize("cff", [False, True])
def test_facts_by_range_equal_facts_of_the_whole_file(cff: bool) -> None:
    # CFF fonts too: without the 'CFF ' table, fontTools names glyphs from 'post'/'maxp'.
    data = big_font(cff=cff, variable=not cff, mono=True, panose=(2, 11, 9))
    whole = facts_from_bytes(data)
    read = read_font(URL, FACT_TABLES, FakeServer({URL: data}))
    assert read.data is None
    assert read.size == len(data)
    assert facts_from_read(read, sha256=whole.sha256) == replace(whole, git_blob=None)


def test_woff_tables_are_read_by_range_and_decompressed() -> None:
    ttf = big_font()
    woff = big_font(flavor="woff")
    read = read_font(URL, FACT_TABLES, FakeServer({URL: woff}))
    assert read.format == "woff"
    assert read.data is None
    assert read.tables == raw_tables(ttf, FACT_TABLES)


def test_woff2_falls_back_to_the_whole_file() -> None:
    data = big_font(flavor="woff2")
    server = FakeServer({URL: data})  # a .ttf name: the header gives it away
    read = read_font(URL, FACT_TABLES, server)
    assert read.format == "woff2"
    assert read.data == data
    assert read.requests == 2  # the header, then the rest in one range
    assert read.tables == raw_tables(data, FACT_TABLES)
    woff2_url = URL.replace(".ttf", ".woff2")
    named = FakeServer({woff2_url: data})
    assert read_font(woff2_url, FACT_TABLES, named).requests == 1
    assert named.calls == [(woff2_url, None, None)]


def test_a_server_that_ignores_range_costs_one_request() -> None:
    data = big_font()
    server = FakeServer({URL: data}, ranges=False)
    read = read_font(URL, FACT_TABLES, server)
    assert read.requests == 1
    assert read.data == data
    assert read.tables == raw_tables(data, FACT_TABLES)


def test_a_server_that_honours_only_the_first_range_gives_the_whole_file() -> None:
    data = big_font()
    server = FakeServer({URL: data}, ranges=1)
    read = read_font(URL, FACT_TABLES, server)
    assert (read.requests, read.data, read.size) == (2, data, len(data))
    assert read.tables == raw_tables(data, FACT_TABLES)


def _with_table(data: bytes, tag: bytes, offset: int, length: int) -> bytes:
    """``data`` with table ``tag``'s directory entry pointed at ``offset`` and ``length``."""
    out = bytearray(data)
    for i in range(int.from_bytes(out[4:6])):
        at = 12 + 16 * i
        if out[at : at + 4] == tag:
            out[at + 8 : at + 16] = offset.to_bytes(4) + length.to_bytes(4)
    return bytes(out)


def test_an_empty_table_far_from_the_others_costs_no_request() -> None:
    # A zero-byte Range is invalid (Fetcher.get_range raises ValueError); an empty
    # table is simply empty, and fontTools then decides whether the font is usable.
    base = big_font()
    # 'post' emptied and moved past padding, further than RANGE_GAP from every other table.
    padded = _with_table(base + b"\0" * 600_000, b"post", len(base) + 590_000, 0)
    read = read_font(URL, FACT_TABLES, FakeServer({URL: padded}))
    assert read.data is None
    assert read.tables["post"] == b""
    others = {t: v for t, v in raw_tables(base, FACT_TABLES).items() if t != "post"}
    assert {t: v for t, v in read.tables.items() if t != "post"} == others


def test_a_small_file_comes_whole_in_one_request() -> None:
    data = build_font()
    assert len(data) < FIRST_READ
    read = read_font(URL, FACT_TABLES, FakeServer({URL: data}))
    assert (read.requests, read.data) == (1, data)


def test_mostly_wanted_bytes_fetch_the_rest_whole() -> None:
    data = build_font(codepoints=range(0x20, 0x2000))  # a big cmap and names, a tiny glyf
    assert len(data) > FIRST_READ
    read = read_font(URL, FACT_TABLES, FakeServer({URL: data}))
    assert read.data == data
    assert read.requests == 2


def test_a_collection_is_read_by_range() -> None:
    first = big_font(family="First Sans")
    data = collection(first, build_font(family="Second Sans"))
    read = read_font(URL, FACT_TABLES, FakeServer({URL: data}))
    assert read.data is None
    assert read.tables == raw_tables(data, FACT_TABLES)
    assert facts_from_read(read, sha256="0" * 64).family_name == "First Sans"


def test_missing_tables_are_left_out() -> None:
    tables = read_tables(URL, ("fvar", "name"), FakeServer({URL: big_font()}))
    assert set(tables) == {"name"}


def test_a_directory_pointing_past_the_end_raises() -> None:
    data = bytearray(big_font())
    count = int.from_bytes(data[4:6])
    for i in range(count):  # stretch the 'name' table far beyond the file
        at = 12 + 16 * i
        if data[at : at + 4] == b"name":
            data[at + 12 : at + 16] = (10**8).to_bytes(4)
    with pytest.raises(FontFileError, match="outside the file"):
        read_font(URL, FACT_TABLES, FakeServer({URL: bytes(data)}))


def test_sfnt_from_tables_round_trips() -> None:
    from fontTools.ttLib import TTFont

    data = big_font()
    tables = raw_tables(data, FACT_TABLES)
    font = TTFont(io.BytesIO(sfnt_from_tables(b"\x00\x01\x00\x00", tables)))
    assert {t: bytes(font.reader[t]) for t in tables} == tables


# --- the cache ----------------------------------------------------------------------------------


def test_cache_put_get_flush_reopen(tmp_path: Path) -> None:
    path = cache_path(tmp_path)
    assert path == tmp_path / CACHE_FILE
    a = facts_from_bytes(build_font(family="Alpha Sans"))
    b = facts_from_bytes(build_font(family="Beta Sans"))
    cache = FontFileCache(path)
    assert len(cache) == 0
    cache.flush()
    assert not path.exists()  # nothing pending, nothing written
    cache.put(b)
    cache.put(a)
    cache.put(a)  # known: ignored
    cache.put_url(URL, a.sha256)
    assert cache.pending == 3
    cache.flush()
    assert cache.pending == 0
    rows = jsonio.load_jsonl(path)
    assert [r["kind"] for r in rows] == ["facts", "facts", "url"]
    assert rows[0]["sha256"] < rows[1]["sha256"]
    again = FontFileCache(path)
    assert len(again) == 2
    assert again.get(sha256=a.sha256) == a
    assert again.get(git_blob=b.git_blob) == b
    assert again.get(sha256="f" * 64, git_blob=a.git_blob) == a
    assert again.get(sha256="f" * 64) is None
    assert again.get_url(URL) == a
    assert again.get_url(URL + "?x") is None


def test_cache_is_append_only_and_first_row_wins(tmp_path: Path) -> None:
    path = tmp_path / "fontfacts.jsonl"
    a = facts_from_bytes(build_font(family="Alpha Sans"))
    cache = FontFileCache(path)
    cache.put(a)
    cache.put_url(URL, a.sha256)
    cache.flush()
    before = path.read_bytes()
    other = facts_from_bytes(build_font(family="Gamma Sans"))
    second = FontFileCache(path)
    second.put_url(URL, other.sha256)  # the url is known: the first answer stays
    second.put(other)
    second.put(replace(a, family_name="Changed"))  # the sha256 is known: ignored
    second.flush()
    after = path.read_bytes()
    assert after.startswith(before)
    assert len(after.splitlines()) == 3
    third = FontFileCache(path)
    assert third.get_url(URL) == a
    assert third.get(sha256=a.sha256) == a


def test_a_bad_cache_row_names_its_line(tmp_path: Path) -> None:
    path = tmp_path / "fontfacts.jsonl"
    path.write_text('{"kind":"url","sha256":"' + "a" * 64 + '","url":"u"}\n{"kind":"mystery"}\n')
    with pytest.raises(ValueError, match=r"fontfacts.jsonl:2: unknown row kind"):
        FontFileCache(path)


# --- facts_for ----------------------------------------------------------------------------------


def test_facts_for_reads_a_hashed_ref_by_range_once(tmp_path: Path) -> None:
    data = big_font(mono=True, panose=(2, 11, 9))
    sha = hashlib.sha256(data).hexdigest()
    ref = FontFileRef(url=URL, sha256=sha)
    server = FakeServer({URL: data})
    cache = FontFileCache(tmp_path / "c.jsonl")
    ff = facts_for(ref, server, cache)
    assert ff.sha256 == sha
    assert ff.git_blob is None  # never saw the whole file
    assert ff.is_fixed_pitch is True
    assert 2 <= len(server.calls) <= 3
    assert all(start is not None for _, start, _ in server.calls)  # by range, never whole
    made = len(server.calls)
    assert facts_for(ref, server, cache) == ff
    assert len(server.calls) == made  # the second call was a cache hit
    cache.flush()
    assert facts_for(ref, None, FontFileCache(tmp_path / "c.jsonl")) == ff  # replay


def test_facts_for_downloads_a_ref_without_hash_and_remembers_its_url(tmp_path: Path) -> None:
    data = big_font()
    ref = FontFileRef(url=URL)
    server = FakeServer({URL: data})
    cache = FontFileCache(tmp_path / "c.jsonl")
    ff = facts_for(ref, server, cache)
    assert server.calls == [(URL, None, None)]
    assert ff.sha256 == hashlib.sha256(data).hexdigest()
    assert ff.git_blob == git_blob_sha1(data)
    cache.flush()
    assert facts_for(ref, None, FontFileCache(tmp_path / "c.jsonl")) == ff


ZIP_URL = "https://github.example/synth/releases/download/v1/SynthSans-1.0.zip"


def synth_zip(members: Mapping[str, bytes], padding: int = 1_000_000) -> bytes:
    """A release archive: the members after a large incompressible file."""
    noise = b"".join(hashlib.sha256(i.to_bytes(4, "big")).digest() for i in range(padding // 32))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SynthSans-1.0/docs/manual.bin", noise, zipfile.ZIP_STORED)
        for name, data in members.items():
            zf.writestr(name, data)
    return out.getvalue()


def test_member_urls_round_trip_and_only_zip_fonts_are_members() -> None:
    url = fontfiles.member_url(ZIP_URL, "SynthSans-1.0/fonts/SynthSans[wght].ttf")
    assert url == ZIP_URL + "#SynthSans-1.0/fonts/SynthSans%5Bwght%5D.ttf"
    assert fontfiles.split_member(url) == (ZIP_URL, "SynthSans-1.0/fonts/SynthSans[wght].ttf")
    assert fontfiles.is_readable_url(url)
    assert not fontfiles.is_font_url(url)
    assert fontfiles.split_member(ZIP_URL) is None
    assert fontfiles.split_member(ZIP_URL + "#README.md") is None
    assert fontfiles.split_member("https://x.example/a.tar.xz#a.ttf") is None
    assert fontfiles.split_member("http://x.example/a.zip#a.ttf") is None


def test_facts_for_reads_one_zip_member_by_range(tmp_path: Path) -> None:
    font = big_font()
    archive = synth_zip({"SynthSans-1.0/fonts/ttf/SynthSans-Regular.ttf": font})
    server = FakeServer({ZIP_URL: archive})
    cache = FontFileCache(tmp_path / "c.jsonl")
    ref = FontFileRef(url=fontfiles.member_url(ZIP_URL, "fonts/ttf/SynthSans-Regular.ttf"))
    ff = facts_for(ref, server, cache)
    assert ff == facts_from_bytes(font)
    assert all(start is not None for _, start, _ in server.calls), "never the whole archive"
    fetched = sum(end - start + 1 for _, start, end in server.calls if start is not None)
    assert len(server.calls) <= 4
    assert fetched < len(archive) // 2
    cache.flush()
    assert facts_for(ref, None, FontFileCache(tmp_path / "c.jsonl")) == ff  # replay: by url


def test_facts_and_size_gives_the_size_of_a_file_it_read(tmp_path: Path) -> None:
    """The size of a file read whole, by range (the server's Content-Range) or out of a
    zip archive; None when the facts come from the cache."""
    whole, ranged = big_font(), big_font(mono=True, panose=(2, 11, 9))
    member = "SynthSans-1.0/fonts/ttf/SynthSans-Regular.ttf"
    server = FakeServer({URL: whole, URL2: ranged, ZIP_URL: synth_zip({member: whole})})
    cache = FontFileCache(tmp_path / "c.jsonl")
    hashed = FontFileRef(url=URL2, sha256=hashlib.sha256(ranged).hexdigest())
    zipped = FontFileRef(url=fontfiles.member_url(ZIP_URL, member))
    for ref, data in ((FontFileRef(url=URL), whole), (hashed, ranged), (zipped, whole)):
        facts, size = fontfiles.facts_and_size(ref, server, cache)
        assert (facts.sha256, size) == (hashlib.sha256(data).hexdigest(), len(data))
        assert fontfiles.facts_and_size(ref, server, cache) == (facts, None)  # cached
        assert facts_for(ref, server, cache) == facts


def test_a_zip_member_is_found_by_its_name_and_never_guessed(tmp_path: Path) -> None:
    font = big_font()
    archive = synth_zip({"a/SynthSans-Regular.ttf": font, "b/Other.ttf": font, "c/Other.ttf": font})
    server = FakeServer({ZIP_URL: archive}, ranges=False)  # a server that ignores Range
    by_name = FontFileRef(url=fontfiles.member_url(ZIP_URL, "SynthSans-Regular.ttf"))
    assert facts_for(by_name, server, FontFileCache(tmp_path / "a.jsonl")).sha256 == (
        hashlib.sha256(font).hexdigest()
    )
    two = FontFileRef(url=fontfiles.member_url(ZIP_URL, "Other.ttf"))
    with pytest.raises(FontFileError, match="2 archive entries"):
        facts_for(two, server, FontFileCache(tmp_path / "b.jsonl"))
    missing = FontFileRef(url=fontfiles.member_url(ZIP_URL, "Missing.ttf"))
    with pytest.raises(FontFileError, match="no archive entry"):
        facts_for(missing, server, FontFileCache(tmp_path / "c.jsonl"))
    wrong = FontFileRef(url=by_name.url, sha256="0" * 64)
    with pytest.raises(FontFileError, match="not the expected"):
        facts_for(wrong, server, FontFileCache(tmp_path / "d.jsonl"))


def test_facts_for_finds_a_ref_by_git_blob_after_the_url_moves(tmp_path: Path) -> None:
    """google/fonts urls are pinned to each month's commit; the blob sha1 is not."""
    data = big_font()
    blob = git_blob_sha1(data)
    server = FakeServer({URL: data})
    cache = FontFileCache(tmp_path / "c.jsonl")
    ff = facts_for(FontFileRef(url=URL, git_blob=blob), server, cache)
    assert ff.git_blob == blob
    cache.flush()
    moved = FontFileRef(url=URL + "?commit=next", git_blob=blob)
    assert facts_for(moved, None, FontFileCache(tmp_path / "c.jsonl")) == ff  # no request
    assert server.calls == [(URL, None, None)]
    with pytest.raises(FontFileError, match="not the expected"):
        facts_for(FontFileRef(url=URL, git_blob="0" * 40), server, FontFileCache(tmp_path / "d"))


def test_facts_for_in_replay_without_a_cached_row_raises(tmp_path: Path) -> None:
    with pytest.raises(FontFactsMissing, match="no network"):
        facts_for(FontFileRef(url=URL, sha256="a" * 64), None, FontFileCache(tmp_path / "c"))


def test_facts_for_refuses_a_file_with_another_hash(tmp_path: Path) -> None:
    ref = FontFileRef(url=URL, sha256="a" * 64)
    with pytest.raises(FontFileError, match="not the expected"):
        facts_for(ref, FakeServer({URL: build_font()}), FontFileCache(tmp_path / "c"))


def test_facts_for_checks_the_size_of_a_file_read_by_range(tmp_path: Path) -> None:
    data = big_font()
    sha = hashlib.sha256(data).hexdigest()
    ok = FontFileRef(url=URL, sha256=sha, size=len(data))
    assert facts_for(ok, FakeServer({URL: data}), FontFileCache(tmp_path / "a")).sha256 == sha
    # The url now serves another file: its facts must not be filed under the old sha256.
    stale = FontFileRef(url=URL, sha256=sha, size=len(data) + 1)
    cache = FontFileCache(tmp_path / "b")
    with pytest.raises(FontFileError, match="not the expected"):
        facts_for(stale, FakeServer({URL: data}), cache)
    assert cache.get(sha256=sha) is None


def test_facts_for_lets_fetch_errors_through(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="404"):
        facts_for(FontFileRef(url=URL), FakeServer({}), FontFileCache(tmp_path / "c"))


# --- the font_facts pseudo-source ---------------------------------------------------------------

DAY = date(2026, 10, 3)


def test_the_read_log_keeps_other_stages_and_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    ok = FileRead("https://a.example/A.ttf", "a" * 64)
    bad = FileRead("https://b.example/B.ttf", None, "HTTP 404")
    assert record_reads(store, DAY, "latin", [ok]) is True
    assert record_reads(store, DAY, "facts", [bad, ok]) is True
    assert record_reads(store, DAY, "facts", [ok, bad]) is False  # the same rows: no rewrite
    snap = store.snapshot("font_facts", DAY)
    assert snap is not None
    assert sorted(e.path for e in snap.manifest.extracts) == ["facts.jsonl", "latin.jsonl"]
    assert list(snap.iter_jsonl("facts.jsonl")) == [ok.to_json(), bad.to_json()]  # by url
    with pytest.raises(SnapshotFrozen):
        record_reads(store, DAY, "verify", [ok], frozen=frozenset({DAY}))


def test_recorded_reads_prefers_a_success_and_the_stage_asked_for(tmp_path: Path) -> None:
    store = Store(tmp_path / "store")
    url = "https://a.example/A.ttf"
    record_reads(store, DAY, "latin", [FileRead(url, "a" * 64)])
    record_reads(store, DAY, "facts", [FileRead(url, None, "timeout")])
    assert recorded_reads(store, DAY)[url].sha256 == "a" * 64
    assert recorded_reads(store, DAY, prefer="facts")[url].error == "timeout"
    assert recorded_reads(store, date(2026, 11, 3)) == {}


def test_file_read_json_is_strict() -> None:
    read = FileRead("https://a.example/A.ttf", "a" * 64)
    assert FileRead.from_json(read.to_json()) == read
    assert read.to_json() == {"error": None, "sha256": "a" * 64, "url": read.url}  # old form
    sized = FileRead("https://a.example/A.ttf", "a" * 64, size=1234)
    assert sized.to_json()["size"] == 1234
    assert FileRead.from_json(sized.to_json()) == sized
    with pytest.raises(ValueError, match="expected"):
        FileRead.from_json({"url": "x"})
    with pytest.raises(ValueError, match="expected"):
        FileRead.from_json({**sized.to_json(), "bytes": 1})
    for bad in (-1, "12", 1.5, True):
        with pytest.raises(ValueError, match="bad size"):
            FileRead.from_json({**read.to_json(), "size": bad})


def test_a_size_fills_in_the_same_answer_but_never_another() -> None:
    log = ReadLog()
    url = "https://a.example/A.ttf"
    log.add(FileRead(url, "a" * 64))
    log.add(FileRead(url, "b" * 64, size=9))  # another file: the first answer stands
    assert log.reads[url] == FileRead(url, "a" * 64)
    log.add(FileRead(url, "a" * 64, size=7))
    assert log.reads[url] == FileRead(url, "a" * 64, size=7)
    log.add(FileRead(url, None, "timeout"))
    assert log.reads[url].size == 7


# --- config/font-files.toml ---------------------------------------------------------------------

PINNED = "https://raw.githubusercontent.com/o/r/0123456789abcdef0123456789abcdef01234567"
RELEASE = "https://github.com/o/r/releases/download/v1.0/Fonts-1.0.zip"


def font_files(tmp_path: Path, body: str) -> Path:
    (tmp_path / fontfiles.FONT_FILES).write_text("schema = 1\n" + body, encoding="utf-8")
    return tmp_path


def entry(family: str = "a", *files: str, name: str = "A", reason: str = "r") -> str:
    listed = ", ".join(f'"{u}"' for u in (files or (f"{PINNED}/A-Regular.ttf",)))
    return (
        f'[[family]]\nfamily = "{family}"\nname = "{name}"\nfiles = [{listed}]\n'
        f'reason = "{reason}"\n'
    )


def test_load_font_files_gives_each_familys_files_in_order(tmp_path: Path) -> None:
    member = fontfiles.member_url(RELEASE, "Fonts-1.0/ttf/B-Regular.ttf")
    body = entry("b", member, f"{PINNED}/B-Italic.ttf") + entry("a")
    got = fontfiles.load_font_files(font_files(tmp_path, body))
    assert got == {
        "a": (FontFileRef(f"{PINNED}/A-Regular.ttf"),),
        "b": (FontFileRef(member), FontFileRef(f"{PINNED}/B-Italic.ttf")),
    }
    assert list(got) == ["a", "b"]


def test_load_font_files_without_a_file(tmp_path: Path) -> None:
    assert fontfiles.load_font_files(tmp_path) == {}


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (entry() + 'note = "x"\n', "unknown key"),
        ('[[family]]\nfamily = "a"\nname = "A"\nreason = "r"\n', "missing key"),
        (entry("A b"), "is not a family id"),
        (entry() + entry(), "listed twice"),
        (entry(name=" "), r"\.name: empty"),
        (entry(reason=""), r"\.reason: empty"),
        ('[[family]]\nfamily = "a"\nname = "A"\nfiles = []\nreason = "r"\n', r"\.files: empty"),
        (entry("a", "http://x.example/A.ttf"), "is not an https URL"),
        (entry("a", RELEASE), "names no font file or zip member"),
        (entry("a", f"{PINNED}/OFL.txt"), "names no font file or zip member"),
        (entry("a", "https://x.example/fonts/A-Regular.ttf"), "is not pinned"),
        (entry("a", "https://x.example/A.zip#A-Regular.ttf"), "is not pinned"),
        (entry("a", f"{PINNED}/A.ttf", f"{PINNED}/A.ttf"), r"files\[1\]: .* is listed twice"),
    ],
    ids=["unknown-key", "no-files-key", "bad-id", "family-twice", "no-name", "no-reason",
         "no-files", "http", "bare-archive", "not-a-font", "unpinned", "unpinned-archive",
         "file-twice"],
)  # fmt: skip
def test_load_font_files_is_strict(tmp_path: Path, body: str, error: str) -> None:
    from tff_catalog.config_model import ConfigError

    with pytest.raises(ConfigError, match=error):
        fontfiles.load_font_files(font_files(tmp_path, body))


def test_load_font_files_checks_the_schema(tmp_path: Path) -> None:
    from tff_catalog.config_model import ConfigError

    (tmp_path / fontfiles.FONT_FILES).write_text("schema = 2\n" + entry(), encoding="utf-8")
    with pytest.raises(ConfigError, match="schema 2, expected 1"):
        fontfiles.load_font_files(tmp_path)


def test_hand_files_come_first_and_only_once() -> None:
    a, b, c = (FontFileRef(f"https://x.example/{n}.ttf") for n in "ABC")
    assert fontfiles.with_hand_files([a, b], None) == [a, b]
    assert fontfiles.with_hand_files([a, b], [c]) == [c, a, b]
    assert fontfiles.with_hand_files([a, b], [replace(b, role="italic")]) == [
        replace(b, role="italic"),
        a,
    ]


def test_the_committed_font_files_table_loads() -> None:
    """config/font-files.toml: strict, pinned, and every file a font file or zip member."""
    got = fontfiles.load_font_files(ROOT / "config")
    assert {"go", "liberation-sans", "liberation-serif"} <= set(got)
    assert all(refs for refs in got.values())


# --- real fonts (network) -----------------------------------------------------------------------


def real_fetcher() -> Any:
    """The project's fetcher (User-Agent, one request a second per host, retries)."""
    from tff_catalog.fetch import Fetcher

    return Fetcher()


def pinned_fonts() -> list[dict[str, Any]]:
    with SPECIMEN_FONTS.open("rb") as fh:
        return tomllib.load(fh)["font"]


def pinned_bytes(font: Mapping[str, Any], fetcher: Any) -> bytes:
    """The pinned file from ``~/.cache/tff/fonts/<sha256>``, downloaded and checked if missing."""
    path = FONT_CACHE / font["sha256"]
    if not path.is_file():
        data = fetcher.get(font["url"]).content
        assert hashlib.sha256(data).hexdigest() == font["sha256"]
        jsonio.atomic_write(path, data)
    data = path.read_bytes()
    assert (len(data), hashlib.sha256(data).hexdigest()) == (font["size"], font["sha256"])
    return data


@pytest.mark.network
def test_pinned_real_fonts_give_the_expected_facts() -> None:
    fetcher = real_fetcher()
    seen = {}
    for font in pinned_fonts():
        ff = facts_from_bytes(pinned_bytes(font, fetcher))
        seen[font["key"]] = ff
        assert ff.sha256 == font["sha256"]
        assert ff.format == font["format"]
        assert [a[0] for a in ff.axes] == font["axes"]
        assert ff.license_description, font["key"]
    assert seen["jetbrains-mono"].is_fixed_pitch is True
    assert seen["jetbrains-mono"].panose_proportion == 9
    assert seen["inter"].is_fixed_pitch is False
    assert seen["inter"].family_name == "Inter"
    assert 0x0151 in seen["inter"].codepoints()  # ő: extended Latin
    assert 0x0151 not in seen["orbitron"].codepoints()  # no Latin Extended-A


@pytest.mark.network
def test_range_reads_of_real_fonts_match_the_whole_files() -> None:
    fetcher = real_fetcher()
    for font in pinned_fonts():
        if font["key"] not in ("inter", "jetbrains-mono", "source-sans-3"):
            continue
        whole = facts_from_bytes(pinned_bytes(font, fetcher))
        read = read_font(font["url"], FACT_TABLES, fetcher)
        assert read.requests <= 3, font["key"]
        assert read.received < font["size"], font["key"]
        assert read.size == font["size"], font["key"]  # Content-Range gives the size
        assert facts_from_read(read, sha256=font["sha256"]) == replace(
            whole, git_blob=None if read.data is None else whole.git_blob
        )


@pytest.mark.network
def test_a_font_in_a_real_release_zip_is_read_by_range(tmp_path: Path) -> None:
    """Homebrew's font-hack downloads this 1.2 MB archive; one member costs a few requests."""
    archive = "https://github.com/source-foundry/Hack/releases/download/v3.003/Hack-v3.003-ttf.zip"
    ref = FontFileRef(url=fontfiles.member_url(archive, "ttf/Hack-Regular.ttf"))
    ff = facts_for(ref, real_fetcher(), FontFileCache(tmp_path / "c.jsonl"))
    assert ff.family_name == "Hack"
    assert ff.is_fixed_pitch is True
    assert 0x20AC in ff.codepoints()
