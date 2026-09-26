"""The "specimens" stage: fetch and check each font file, render, flag, cache, prune."""

import hashlib
import logging
import shutil
import subprocess
from datetime import date
from pathlib import Path

import httpx
import pytest
from tests.specimens import fontmaker

from tff_catalog import jsonio, stageio
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.specimens import MAX_FILE_BYTES, RENDERER_VERSION, stage
from tff_catalog.specimens.stage import Preview
from tff_catalog.stages import StageContext
from tff_catalog.state import State

URL = "https://fonts.example/{name}.ttf"


class FakeFetcher:
    """Serves font bytes by URL, as ``Fetcher.get(url, to=path)`` streams a body to a file."""

    def __init__(self, files: dict[str, bytes], *, fail: set[str] | None = None) -> None:
        self.files = files
        self.fail = fail or set()
        self.calls: list[str] = []

    def get(self, url: str, *, to: Path | None = None, **_: object) -> None:
        self.calls.append(url)
        if url in self.fail:
            raise RuntimeError(f"503 from {url}")  # stands in for fetch.FetchError
        assert to is not None
        to.write_bytes(self.files[url])


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def font_row(font_id: str, data: bytes | None, *, family: str = "Test Sans", **over: object):
    ref = None
    if data is not None:
        ref = {"url": URL.format(name=font_id), "sha256": sha(data), "size": len(data)}
        ref["format"] = "ttf"
    row = {"id": font_id, "family": family, "preview_ok": True, "font_file": ref}
    row.update(over)
    return row


@pytest.fixture(scope="module")
def fonts() -> dict[str, bytes]:
    chars = fontmaker.sample_chars("Test Sans") + fontmaker.sample_chars("Noisy")
    return {
        "good": fontmaker.make_font(chars),
        "basic": fontmaker.make_font(fontmaker.basic_chars()),
        "greek": fontmaker.make_font("ΑΒΓΔαβγδ"),
        "noisy": fontmaker.make_font(chars, noisy=240),
    }


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    paths = Paths.for_root(tmp_path / "repo")
    return StageContext(
        paths=paths,
        config=None,  # type: ignore[arg-type]  # the stage reads no config
        state=State(),
        run_date=date(2026, 9, 25),
        store=None,
        fetcher=None,
        log=logging.getLogger("tests.specimens"),
    )


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fonts"
    monkeypatch.setattr(stage, "FONT_CACHE", path)  # never the real ~/.cache
    return path


def run_with(ctx: StageContext, rows: list[dict], fetcher: object | None) -> dict:
    jsonio.dump({"fonts": rows}, ctx.paths.build / "catalog.json")
    stage.run(StageContext(**{**_fields(ctx), "fetcher": fetcher}))
    return stageio.load_stage(ctx.paths, "previews")


def index_path(ctx: StageContext) -> Path:
    """The render index, committed beside the SVGs."""
    return ctx.paths.specimens / stage.INDEX_FILE


def svgs(ctx: StageContext) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(ctx.paths.specimens.glob("*.svg"))}


def _fields(ctx: StageContext) -> dict:
    return {name: getattr(ctx, name) for name in StageContext.__dataclass_fields__}


def fetcher_for(rows: list[dict], fonts: dict[str, bytes], **kw) -> FakeFetcher:
    files = {}
    for row in rows:
        if row["font_file"]:
            files[row["font_file"]["url"]] = fonts.get(row["id"], fonts["good"])
    return FakeFetcher(files, **kw)


# --- cache_key and fetch_font ------------------------------------------------------------------


def test_cache_key_is_a_sha256_that_moves_with_every_input() -> None:
    base = ("a" * 64, "Name\nsample\nbasic", 1, "uharfbuzz 0.56.2; HarfBuzz 14.5.0")
    key = stage.cache_key(*base)
    assert len(key) == 64
    assert int(key, 16) >= 0
    assert stage.cache_key(*base) == key
    variants = [
        ("b" * 64, *base[1:]),
        (base[0], "Name\nother\nbasic", *base[2:]),
        (*base[:2], 2, base[3]),
        (*base[:3], "uharfbuzz 0.57.0; HarfBuzz 14.6.0"),
    ]
    assert len({key, *(stage.cache_key(*v) for v in variants)}) == 5


def test_the_key_covers_the_family_and_both_lines_and_the_engine() -> None:
    texts = stage.sample_texts("Inter")
    assert texts.split("\n") == ["Inter", stage.SAMPLE, stage.BASIC_SAMPLE]
    assert "HarfBuzz" in stage.hb_version()


def test_fetch_font_downloads_once_then_reads_the_cache(tmp_path: Path) -> None:
    data = b"font bytes"
    fetcher = FakeFetcher({"u": data})
    path = stage.fetch_font("u", sha(data), fetcher, tmp_path)  # type: ignore[arg-type]
    assert path == tmp_path / sha(data)
    assert path.read_bytes() == data
    assert stage.fetch_font("u", sha(data), None, tmp_path) == path
    assert fetcher.calls == ["u"]
    assert [p.name for p in tmp_path.iterdir()] == [sha(data)]  # no partial file left


def test_fetch_font_hash_mismatch_keeps_nothing(tmp_path: Path) -> None:
    fetcher = FakeFetcher({"u": b"other bytes"})
    assert stage.fetch_font("u", sha(b"font"), fetcher, tmp_path) is None  # type: ignore[arg-type]
    assert list(tmp_path.iterdir()) == []


def test_fetch_font_replaces_a_damaged_cache_entry(tmp_path: Path) -> None:
    data = b"font bytes"
    (tmp_path / sha(data)).write_bytes(b"truncated")
    fetcher = FakeFetcher({"u": data})
    path = stage.fetch_font("u", sha(data), fetcher, tmp_path)  # type: ignore[arg-type]
    assert path is not None
    assert path.read_bytes() == data


def test_fetch_font_without_a_fetcher_or_cache_raises(tmp_path: Path) -> None:
    with pytest.raises(stage.FontUnavailable):
        stage.fetch_font("u", sha(b"x"), None, tmp_path)


def test_fetch_font_refuses_a_sha256_that_is_not_one(tmp_path: Path) -> None:
    fetcher = FakeFetcher({"u": b"x"})
    assert stage.fetch_font("u", "../../etc/passwd", fetcher, tmp_path) is None  # type: ignore[arg-type]
    assert fetcher.calls == []


# --- the stage -------------------------------------------------------------------------------


def test_stage_renders_flags_and_records_previews(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    rows = [
        font_row("good", fonts["good"]),
        font_row("basic", fonts["basic"]),
        font_row("greek", fonts["greek"]),
        font_row("noisy", fonts["noisy"], family="Noisy"),
        font_row("mismatch", b"the bytes the catalog expects"),
        font_row("no-file", None),
        font_row("not-ok", fonts["good"], preview_ok=False),
    ]
    fetcher = fetcher_for(rows, fonts)
    fetcher.files[URL.format(name="mismatch")] = fonts["basic"]  # upstream changed under us
    previews = run_with(ctx, rows, fetcher)

    assert set(previews) == {"good", "basic", "greek", "noisy", "mismatch", "no-file"}
    for font_id in ("good", "basic"):
        p = previews[font_id]
        svg = ctx.paths.specimens / f"{font_id}.svg"
        assert p == Preview(f"specimens/{font_id}.svg", sha(svg.read_bytes()), ())
    assert previews["greek"] == Preview(
        None,
        None,
        ("specimen_failed",),
        "the font draws none of the sample texts, not even its name",
    )
    assert previews["mismatch"] == Preview(
        None, None, ("specimen_hash_mismatch",), "the font file does not match its sha256"
    )
    assert previews["no-file"] == Preview(
        None, None, ("specimen_failed",), "no font file to draw from"
    )
    noisy = previews["noisy"]
    assert noisy.flags == ("specimen_name_only",)
    assert noisy.path == "specimens/noisy.svg"
    assert len((ctx.paths.specimens / "noisy.svg").read_bytes()) <= MAX_FILE_BYTES
    assert sorted(p.name for p in ctx.paths.specimens.iterdir()) == [
        "basic.svg",
        "good.svg",
        "index.json",
        "noisy.svg",
    ]
    assert all(p.name == sha(p.read_bytes()) for p in cache_dir.iterdir())  # nothing bad kept


def test_the_noisy_font_really_is_over_budget_in_full(fonts: dict[str, bytes]) -> None:
    from tff_catalog.specimens.render import render

    full = render(fonts["noisy"], "Noisy", stage.SAMPLE, stage.BASIC_SAMPLE)
    name = render(fonts["noisy"], "Noisy", stage.SAMPLE, stage.BASIC_SAMPLE, name_only=True)
    assert full is not None
    assert name is not None
    assert len(full.svg) > MAX_FILE_BYTES >= len(name.svg)


def test_a_name_only_specimen_still_over_budget_fails(tmp_path: Path) -> None:
    chars = fontmaker.sample_chars("Noisy Family With A Long Name")
    font = fontmaker.make_font(chars, noisy=900)
    preview = stage.render_one("x", "Noisy Family With A Long Name", font, tmp_path)
    assert preview == Preview(
        None, None, ("specimen_failed",), "over 30 KB even with the name only"
    )
    assert list(tmp_path.iterdir()) == []


def test_unchanged_inputs_skip_rendering(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [font_row("good", fonts["good"]), font_row("greek", fonts["greek"])]
    first = run_with(ctx, rows, fetcher_for(rows, fonts))
    index = jsonio.load(index_path(ctx))
    assert set(index) == {"good", "greek"}

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("rendered again")

    monkeypatch.setattr(stage, "render_one", boom)
    fetcher = fetcher_for(rows, fonts)
    assert run_with(ctx, rows, fetcher) == first
    assert fetcher.calls == []  # the fonts came from the font cache


def test_a_changed_renderer_or_family_renders_again(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [font_row("good", fonts["good"])]
    first = run_with(ctx, rows, fetcher_for(rows, fonts))
    calls = []
    real = stage.render_one
    monkeypatch.setattr(stage, "render_one", lambda *a: calls.append(a[0]) or real(*a))
    run_with(ctx, rows, None)
    assert calls == []
    monkeypatch.setattr(stage, "RENDERER_VERSION", RENDERER_VERSION + 1)
    assert run_with(ctx, rows, None) == first  # drawn again, same bytes
    assert calls == ["good"]
    renamed = run_with(ctx, [font_row("good", fonts["good"], family="Sans Test")], None)
    assert calls == ["good", "good"]
    assert renamed["good"].sha256 != first["good"].sha256


def test_an_edited_svg_on_disk_is_rendered_again(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    rows = [font_row("good", fonts["good"])]
    first = run_with(ctx, rows, fetcher_for(rows, fonts))
    svg = ctx.paths.specimens / "good.svg"
    original = svg.read_bytes()
    svg.write_bytes(b"<svg/>")
    assert run_with(ctx, rows, None) == first
    assert svg.read_bytes() == original


def test_unavailable_font_keeps_a_same_input_specimen_else_fails(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    rows = [font_row("good", fonts["good"])]
    first = run_with(ctx, rows, fetcher_for(rows, fonts))
    (cache_dir / sha(fonts["good"])).unlink()
    down = fetcher_for(rows, fonts, fail={URL.format(name="good")})
    assert run_with(ctx, rows, down) == first  # kept: same inputs as the rendered one
    index_path(ctx).unlink()
    assert run_with(ctx, rows, down)["good"] == Preview(
        None, None, ("specimen_failed",), stage.UNAVAILABLE
    )
    assert not (ctx.paths.specimens / "good.svg").exists()


def test_a_hash_mismatch_is_not_cached(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    rows = [font_row("good", fonts["good"])]
    fetcher = fetcher_for(rows, fonts)
    fetcher.files[URL.format(name="good")] = b"not the font"
    assert run_with(ctx, rows, fetcher)["good"].flags == ("specimen_hash_mismatch",)
    assert run_with(ctx, rows, fetcher_for(rows, fonts))["good"].flags == ()


def test_a_render_crash_flags_that_font_only_and_is_not_cached(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes], monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [font_row("basic", fonts["basic"]), font_row("good", fonts["good"])]
    real = stage.render_one

    def crash_on_good(font_id: str, *a: object) -> Preview:
        if font_id == "good":
            raise IndexError("a malformed table")
        return real(font_id, *a)  # type: ignore[arg-type]

    monkeypatch.setattr(stage, "render_one", crash_on_good)
    previews = run_with(ctx, rows, fetcher_for(rows, fonts))
    assert previews["good"] == Preview(
        None, None, ("specimen_failed",), "the renderer raised an error on this font"
    )
    assert previews["basic"].path == "specimens/basic.svg"
    assert set(jsonio.load(index_path(ctx))) == {"basic"}


def test_stale_specimens_are_removed(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    ctx.paths.specimens.mkdir(parents=True)
    (ctx.paths.specimens / "gone.svg").write_bytes(b"<svg/>")
    (ctx.paths.specimens / "README").write_text("kept: not a specimen")
    rows = [font_row("good", fonts["good"])]
    run_with(ctx, rows, fetcher_for(rows, fonts))
    assert sorted(p.name for p in ctx.paths.specimens.iterdir()) == [
        "README",
        "good.svg",
        "index.json",
    ]


def test_output_bytes_are_stable_across_runs(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    rows = [font_row("good", fonts["good"]), font_row("basic", fonts["basic"])]
    run_with(ctx, rows, fetcher_for(rows, fonts))
    before = {p.name: p.read_bytes() for p in ctx.paths.specimens.iterdir()}
    stage_file = stageio.stage_path(ctx.paths, "previews").read_bytes()
    index_path(ctx).unlink()  # render everything again
    run_with(ctx, rows, None)
    assert {p.name: p.read_bytes() for p in ctx.paths.specimens.iterdir()} == before
    assert stageio.stage_path(ctx.paths, "previews").read_bytes() == stage_file


def test_the_stage_needs_the_exported_catalog(ctx: StageContext, cache_dir: Path) -> None:
    with pytest.raises(FileNotFoundError, match="export"):
        stage.run(ctx)


def test_a_font_id_that_cannot_name_a_file_is_refused(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    with pytest.raises(ValueError, match="font id"):
        run_with(ctx, [font_row("../evil", fonts["good"])], None)


# --- review additions: committed index, a fresh clone, the real fetcher ------------------------


def test_the_index_is_committed_with_the_svgs_not_in_a_gitignored_folder(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    rows = [font_row("good", fonts["good"])]
    run_with(ctx, rows, fetcher_for(rows, fonts))
    assert index_path(ctx).is_file()
    assert not (ctx.paths.cache / "specimens.json").exists()
    repo = Path(__file__).resolve().parents[2]
    for rel, want in (("build/specimens/index.json", 1), ("build/cache/specimens.json", 0)):
        try:
            done = subprocess.run(
                ["git", "check-ignore", "-q", "--no-index", rel], cwd=repo, check=False
            )
        except FileNotFoundError:
            pytest.skip("git is not installed")
        if done.returncode == 128:
            pytest.skip("not a git checkout")
        assert done.returncode == want, rel  # 0: ignored, 1: not ignored


def test_a_fresh_clone_replays_the_committed_specimens_without_the_font_files(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    """M1 step 18: replay in a fresh clone, network off, gives the committed build/ bytes."""
    rows = [
        font_row("good", fonts["good"]),
        font_row("greek", fonts["greek"]),
        font_row("noisy", fonts["noisy"], family="Noisy"),
    ]
    first = run_with(ctx, rows, fetcher_for(rows, fonts))
    committed = {**svgs(ctx), "index": index_path(ctx).read_bytes()}
    stage_file = stageio.stage_path(ctx.paths, "previews").read_bytes()
    # Another machine: no font cache; build/stage/ and build/cache/ are gitignored.
    for gone in (cache_dir, ctx.paths.stage, ctx.paths.cache):
        shutil.rmtree(gone, ignore_errors=True)
    assert run_with(ctx, rows, None) == first
    assert first["noisy"].flags == ("specimen_name_only",)  # the flag survives too
    assert {**svgs(ctx), "index": index_path(ctx).read_bytes()} == committed
    assert stageio.stage_path(ctx.paths, "previews").read_bytes() == stage_file
    # Changed inputs can't reuse it: without the file the font is flagged, its SVG removed.
    renamed = [font_row("good", fonts["good"], family="Sans Test"), *rows[1:]]
    again = run_with(ctx, renamed, None)
    assert again["good"] == Preview(None, None, ("specimen_failed",), stage.UNAVAILABLE)
    assert "good.svg" not in svgs(ctx)


@pytest.mark.parametrize(
    "edit",
    [
        lambda e: "not an entry",
        lambda e: [e],
        lambda e: {**e, "path": "specimens/../../elsewhere.svg"},
        lambda e: {**e, "path": "specimens/other.svg"},
        lambda e: {**e, "path": "specimens/./good.svg"},  # names the right file, wrongly
        lambda e: {**e, "flags": ["too_new"]},
        lambda e: {**e, "flags": ["specimen_hash_mismatch"]},
        lambda e: {**e, "path": None, "sha256": None, "flags": []},
        lambda e: {**e, "sha256": "0" * 64},
        lambda e: {**e, "extra": 1},
    ],
)
def test_a_hand_edited_index_entry_is_a_miss(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes], edit
) -> None:
    rows = [font_row("good", fonts["good"])]
    first = run_with(ctx, rows, fetcher_for(rows, fonts))
    index = jsonio.load(index_path(ctx))
    jsonio.dump({"good": edit(index["good"])}, index_path(ctx))
    assert run_with(ctx, rows, None) == first  # drawn again from the cached font
    assert jsonio.load(index_path(ctx)) == index
    (cache_dir / sha(fonts["good"])).unlink()
    jsonio.dump({"good": edit(index["good"])}, index_path(ctx))
    assert run_with(ctx, rows, None)["good"] == Preview(
        None, None, ("specimen_failed",), stage.UNAVAILABLE
    )


def test_an_unreadable_index_is_ignored(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes]
) -> None:
    rows = [font_row("good", fonts["good"])]
    first = run_with(ctx, rows, fetcher_for(rows, fonts))
    index_path(ctx).write_text("<<<<<<< HEAD\n", encoding="utf-8")  # a merge conflict
    assert run_with(ctx, rows, None) == first


def test_a_final_newline_does_not_pass_as_an_id_or_a_sha256(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes], tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="font id"):
        run_with(ctx, [font_row("good\n", fonts["good"])], None)
    fetcher = FakeFetcher({"u": b"x"})
    assert stage.fetch_font("u", sha(b"x") + "\n", fetcher, tmp_path) is None  # type: ignore[arg-type]
    assert fetcher.calls == []


def _mock_fetcher(files: dict[str, bytes], seen: list[str]) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = files.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    return Fetcher(
        transport=httpx.MockTransport(handler),
        retries=0,
        min_interval={"fonts.example": 0.0},
    )


def test_the_stage_with_the_real_m1_fetcher(
    ctx: StageContext, cache_dir: Path, fonts: dict[str, bytes], caplog: pytest.LogCaptureFixture
) -> None:
    """The real ``fetch.Fetcher`` (on a mock transport): its streaming and its errors."""
    rows = [
        font_row("good", fonts["good"]),
        font_row("gone", fonts["basic"]),  # 404 upstream
        font_row("plain", fonts["basic"]),  # an http:// URL: refused, never requested
    ]
    rows[2]["font_file"]["url"] = "http://fonts.example/plain.ttf"
    seen: list[str] = []
    fetcher = _mock_fetcher({URL.format(name="good"): fonts["good"]}, seen)
    with fetcher, caplog.at_level(logging.WARNING):
        previews = run_with(ctx, rows, fetcher)
    assert previews["good"].path == "specimens/good.svg"
    assert previews["gone"] == Preview(None, None, ("specimen_failed",), stage.UNAVAILABLE)
    assert previews["plain"] == Preview(None, None, ("specimen_failed",), stage.UNAVAILABLE)
    assert seen == [URL.format(name="gone"), URL.format(name="good")]  # sorted by id
    assert sorted(p.name for p in cache_dir.iterdir()) == [sha(fonts["good"])]  # no part files
    assert "gone: font unavailable" in caplog.text  # fetch.FetchError
    assert "plain: font unavailable" in caplog.text  # fetch.HostNotAllowed
    with _mock_fetcher({}, seen) as fetcher:
        seen.clear()
        assert run_with(ctx, rows[:1], fetcher) == {"good": previews["good"]}
    assert seen == []  # read from the font cache
