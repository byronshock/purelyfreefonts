"""Stage "facts" (M1 step 5b): category, monospace and formats for every family.

Records and universes here are synthetic; fonts are built with FontBuilder and
served from memory (``tests/test_fontfiles.py``). The ``network`` test reads the
pinned real fonts of ``tests/fixtures/specimen-fonts.toml`` by range.
"""

import hashlib
import json
import logging
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.test_fontfiles import (
    FakeServer,
    big_font,
    build_font,
    pinned_fonts,
    real_fetcher,
)

from tff_catalog import jsonio, stageio
from tff_catalog.config_model import Config
from tff_catalog.facts import (
    CATEGORIES,
    Facts,
    candidates,
    derive_facts,
    fact_bases,
    family_facts,
    normalise_category,
    run,
    settled_by_metadata,
    table_category,
    table_monospace,
    universe_records,
    with_hashes,
)
from tff_catalog.fontfiles import FileRead, FontFacts, facts_from_bytes, record_reads
from tff_catalog.paths import Paths
from tff_catalog.records import (
    FontFileRef,
    Observation,
    SourceKey,
    UniverseRecord,
    to_json,
    write_jsonl,
)
from tff_catalog.stages import RunOptions, StageContext
from tff_catalog.state import State
from tff_catalog.store import Store
from tff_catalog.universe import Family, Universe

RUN_DAY = date(2026, 10, 3)
LOG = logging.getLogger("test.facts")


# --- builders -----------------------------------------------------------------------------------

_NS = {
    "google_metadata": "gf-family",
    "google_repo": "gf-dir",
    "fontsource": "fs-id",
    "nerdfonts": "nerd-folder",
    "homebrew_casks": "brew-cask",
    "fontist": "fontist-formula",
    "foundries": "foundry-family",
}


def rec(source: str, key: str, family: str, **kw: Any) -> UniverseRecord:
    return UniverseRecord(source=source, key=SourceKey(_NS[source], key), family=family, **kw)


def ref(url: str, **kw: Any) -> FontFileRef:
    return FontFileRef(url=url, **kw)


def universe(
    families: dict[str, list[UniverseRecord]], dropped: dict[str, str] | None = None
) -> Universe:
    """A universe with one family per id, owning the keys of its records."""
    fams = {}
    for fid, recs in families.items():
        fams[fid] = Family(
            id=fid,
            family=recs[0].family if recs else fid,
            keys=tuple(sorted({r.key for r in recs})),
            sources=tuple(sorted({r.source for r in recs})),
            first_seen=date(2026, 9, 3),
            minted_from=recs[0].family if recs else fid,
            drop=(dropped or {}).get(fid),
        )
    return Universe(families=fams, unmapped=())


def all_records(families: dict[str, list[UniverseRecord]]) -> list[UniverseRecord]:
    return [r for recs in families.values() for r in recs]


def ff_of(data: bytes) -> FontFacts:
    return facts_from_bytes(data)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- vocabulary ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "source", "expected"),
    [
        ("Sans Serif", "google_metadata", "sans-serif"),
        ("SANS_SERIF", "google_repo", "sans-serif"),
        ("sans-serif", "fontsource", "sans-serif"),
        ("Serif", "google_metadata", "serif"),
        ("Slab Serif", "google_metadata", "serif"),
        ("DISPLAY", "google_repo", "display"),
        ("Handwriting", "google_metadata", "handwriting"),
        ("script", "fontsource", "handwriting"),
        ("Monospace", "google_metadata", "monospace"),
        ("MONOSPACE", "google_repo", "monospace"),
        ("mono", "nerdfonts", "monospace"),
        ("  Fixed-Width ", "fontist", "monospace"),
        ("other", "fontsource", None),
        ("Symbols", "google_metadata", None),
        ("", "fontsource", None),
    ],
)
def test_normalise_category(raw: str, source: str, expected: str | None) -> None:
    assert normalise_category(raw, source) == expected


def _facts(**kw: Any) -> FontFacts:
    base = {
        "sha256": "0" * 64,
        "git_blob": None,
        "format": "ttf",
        "cmap": ((0x20, 0x7E),),
        "family_name": "X",
        "full_name": None,
        "postscript_name": None,
        "version": None,
        "license_description": None,
        "license_url": None,
        "is_fixed_pitch": False,
        "panose_proportion": 0,
        "panose_family": 0,
        "panose_serif": 0,
        "family_class": 0,
    }
    return FontFacts(**(base | kw))


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"panose_family": 2, "panose_serif": 11}, "sans-serif"),
        ({"panose_family": 2, "panose_serif": 15}, "sans-serif"),
        ({"panose_family": 2, "panose_serif": 5}, "serif"),
        ({"panose_family": 3}, "handwriting"),
        ({"panose_family": 4}, "display"),
        ({"family_class": 0x0801}, "sans-serif"),
        ({"family_class": 0x0502}, "serif"),
        ({"family_class": 0x0A00}, "handwriting"),
        ({"family_class": 0x0900}, "display"),
        ({"family_class": 0x0C00}, None),
        ({}, None),
        ({"panose_family": 2, "panose_serif": 1, "family_class": 0x0800}, "sans-serif"),
    ],
)
def test_table_category(kw: dict[str, int], expected: str | None) -> None:
    assert table_category(_facts(**kw)) == expected


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"is_fixed_pitch": True}, True),
        ({"panose_family": 2, "panose_proportion": 9}, True),
        ({"panose_family": 0, "panose_proportion": 9}, True),
        ({"panose_family": 3, "panose_proportion": 3}, True),
        ({"panose_family": 3, "panose_proportion": 9}, False),
        ({"panose_family": 2, "panose_proportion": 3}, False),
        ({"is_fixed_pitch": None, "panose_proportion": None}, None),
    ],
)
def test_table_monospace(kw: dict[str, Any], expected: bool | None) -> None:
    assert table_monospace(_facts(**kw)) is expected


# --- derive_facts -------------------------------------------------------------------------------

INTER_VF = "https://raw.githubusercontent.com/google/fonts/abc/ofl/inter/Inter%5Bopsz,wght%5D.ttf"


def test_google_metadata_decides_and_wins_over_fontsource() -> None:
    fams = {
        "inter": [
            rec("google_metadata", "Inter", "Inter", category="Sans Serif", variable=True),
            rec("fontsource", "inter", "Inter", category="serif", variable=False),
        ],
        "jetbrains-mono": [
            rec(
                "google_metadata",
                "JetBrains Mono",
                "JetBrains Mono",
                category="Monospace",
                classifications=("Monospace",),
                variable=True,
            )
        ],
        "noto-sans-mono": [
            rec(
                "google_metadata",
                "Noto Sans Mono",
                "Noto Sans Mono",
                category="Sans Serif",
                classifications=("Monospace",),
                variable=True,
            )
        ],
        "lobster": [
            rec("google_repo", "ofl/lobster", "Lobster", category="DISPLAY", variable=False)
        ],
    }
    out = derive_facts(universe(fams), all_records(fams), {})
    assert out["inter"] == Facts("sans-serif", False, True, True, "google_metadata")
    assert out["jetbrains-mono"] == Facts("monospace", True, True, True, "google_metadata")
    assert out["noto-sans-mono"] == Facts("sans-serif", True, True, True, "google_metadata")
    assert out["lobster"] == Facts("display", False, False, True, "google_repo")


def test_fontsource_decides_when_google_is_silent() -> None:
    fams = {
        "comic-mono": [
            rec(
                "fontsource",
                "comic-mono",
                "Comic Mono",
                category="monospace",
                classifications=("monospace",),
                variable=False,
            )
        ],
        "adwaita-sans": [
            rec("fontsource", "adwaita-sans", "Adwaita Sans", category="sans-serif", variable=True),
            rec("homebrew_casks", "font-adwaita", "Adwaita Sans", is_monospace=True),
        ],
    }
    out = derive_facts(universe(fams), all_records(fams), {})
    assert out["comic-mono"] == Facts("monospace", True, False, True, "fontsource")
    # Fontsource's classification is complete: another source's "monospace" does not override it.
    assert out["adwaita-sans"] == Facts("sans-serif", False, True, True, "fontsource")


def test_font_tables_decide_when_the_metadata_says_other() -> None:
    serif = ff_of(build_font(panose=(2, 5, 3)))
    url = "https://cdn.example/x/XSerif-Regular.ttf"
    fams = {
        "x-serif": [
            rec(
                "fontsource",
                "x-serif",
                "X Serif",
                category="other",
                classifications=("other",),
                variable=False,
                files=(ref(url, sha256=serif.sha256),),
            )
        ]
    }
    out = derive_facts(universe(fams), all_records(fams), {serif.sha256: serif})
    assert out["x-serif"] == Facts(
        "serif", False, False, True, "category=font_file;is_monospace=font_file;formats=fontsource"
    )


def test_a_yes_from_nerd_fonts_beats_a_no_from_the_tables() -> None:
    plain = ff_of(build_font())  # isFixedPitch off, panose proportion 3
    fams = {
        "wide-code": [
            rec(
                "nerdfonts",
                "WideCode",
                "Wide Code",
                is_monospace=True,
                files=(ref("https://cdn.example/WideCode-Regular.ttf", sha256=plain.sha256),),
            )
        ]
    }
    out = derive_facts(universe(fams), all_records(fams), {plain.sha256: plain})
    assert out["wide-code"] == Facts(
        "monospace",
        True,
        False,
        True,
        "category=nerdfonts;is_monospace=nerdfonts;formats=font_file",
    )


def test_tables_say_monospace_and_a_variable_only_family_is_not_static() -> None:
    var = ff_of(build_font(variable=True, mono=True, panose=(0, 0, 0), family_class=0))
    fams = {
        "solo-var": [
            rec(
                "homebrew_casks",
                "font-solo-var",
                "Solo Var",
                files=(ref("https://cdn.example/SoloVar.ttf", sha256=var.sha256),),
            )
        ]
    }
    out = derive_facts(universe(fams), all_records(fams), {var.sha256: var})
    assert out["solo-var"] == Facts("monospace", True, True, False, "font_file")


def test_nothing_known_gives_the_defaults_and_drops_are_skipped() -> None:
    fams = {
        "bare": [rec("foundries", "velvetyne/bare", "Bare")],
        "icons": [rec("fontsource", "icons", "Icons", category="other")],
    }
    stray = rec("fontist", "stray", "Stray", category="Serif")  # maps to no family
    out = derive_facts(universe(fams, dropped={"icons": "icon"}), [*all_records(fams), stray], {})
    assert out == {"bare": Facts("sans-serif", False, False, True, "default")}


def test_google_fonts_file_names_and_roles_say_variable() -> None:
    fams = {
        "inter": [
            rec(
                "google_repo",
                "ofl/inter",
                "Inter",
                category="SANS_SERIF",
                files=(ref(INTER_VF),),
            )
        ],
        "roled": [
            rec(
                "homebrew_casks",
                "font-roled",
                "Roled",
                files=(ref("https://cdn.example/Roled-VF.ttf", role="variable"),),
            )
        ],
    }
    out = derive_facts(universe(fams), all_records(fams), {})
    assert out["inter"] == Facts(
        "sans-serif",
        False,
        True,
        True,
        "category=google_repo;is_monospace=google_repo;formats=font_file",
    )
    assert (out["roled"].variable, out["roled"].static) == (True, False)


def test_fact_bases_unpacks_the_basis() -> None:
    assert fact_bases(Facts("serif", False, False, True, "fontsource")) == {
        "category": "fontsource",
        "is_monospace": "fontsource",
        "formats": "fontsource",
    }
    mixed = Facts("serif", False, False, True, "category=font_file;is_monospace=default;formats=x")
    assert fact_bases(mixed)["is_monospace"] == "default"


_SOURCES = ("google_metadata", "google_repo", "fontsource", "nerdfonts", "homebrew_casks")
_WORDS = (None, "Sans Serif", "SERIF", "monospace", "other", "Handwriting", "display", "Symbols")


@st.composite
def _family(draw: st.DrawFn) -> tuple[list[UniverseRecord], dict[str, FontFacts]]:
    recs = []
    known: dict[str, FontFacts] = {}
    for i in range(draw(st.integers(0, 4))):
        source = draw(st.sampled_from(_SOURCES))
        files = []
        for j in range(draw(st.integers(0, 2))):
            sha = f"{i}{j}".rjust(64, "a")
            files.append(
                ref(
                    f"https://cdn.example/{i}-{j}.ttf",
                    sha256=sha,
                    role=draw(st.sampled_from(("regular", "variable", "italic", "other"))),
                )
            )
            if draw(st.booleans()):
                known[sha] = _facts(
                    sha256=sha,
                    is_fixed_pitch=draw(st.sampled_from((None, True, False))),
                    panose_proportion=draw(st.sampled_from((None, 0, 3, 9))),
                    panose_family=draw(st.sampled_from((None, 0, 2, 3, 4))),
                    panose_serif=draw(st.sampled_from((None, 0, 5, 11))),
                    axes=draw(st.sampled_from(((), (("wght", 100.0, 400.0, 900.0),)))),
                )
        recs.append(
            rec(
                source,
                f"k{i}",
                "Fam",
                category=draw(st.sampled_from(_WORDS)),
                classifications=tuple(
                    w for w in draw(st.lists(st.sampled_from(_WORDS), max_size=2)) if w
                ),
                is_monospace=draw(st.sampled_from((None, True, False))),
                variable=draw(st.sampled_from((None, True, False))),
                files=tuple(files),
            )
        )
    return recs, known


@given(_family())
def test_every_family_gets_valid_facts_whatever_the_order(
    case: tuple[list[UniverseRecord], dict[str, FontFacts]],
) -> None:
    recs, known = case
    f = family_facts(recs, known)
    assert f.category in CATEGORIES
    assert f.variable or f.static
    assert f.is_monospace or f.category != "monospace"  # the mono category and Spacing agree
    assert set(fact_bases(f)) == {"category", "is_monospace", "formats"}
    assert family_facts(list(reversed(recs)), known) == f


def test_a_monospace_category_makes_the_family_monospaced() -> None:
    fams = {
        "odd": [
            # Fontsource says nothing on category but "not monospaced"; another source's
            # category word says monospace. The mono category must not show as proportional.
            rec("fontsource", "odd", "Odd", category="other", is_monospace=False),
            rec("nerdfonts", "Odd", "Odd", category="Monospace"),
        ]
    }
    out = derive_facts(universe(fams), all_records(fams), {})
    assert (out["odd"].category, out["odd"].is_monospace) == ("monospace", True)
    assert fact_bases(out["odd"])["is_monospace"] == "nerdfonts"


def test_records_of_one_key_decide_the_same_whatever_their_order() -> None:
    a = rec("fontsource", "x", "X", category="serif")
    b = rec("fontsource", "x", "X", category="display")
    assert family_facts([a, b], {}) == family_facts([b, a], {})


# --- choosing files -----------------------------------------------------------------------------


def test_candidates_prefer_hashed_regular_files_and_skip_archives() -> None:
    recs = [
        rec(
            "homebrew_casks",
            "font-x",
            "X",
            files=(
                ref("https://github.com/o/x/releases/download/v1/X.zip"),
                ref("https://cdn.example/b/X-Italic.ttf", role="italic", sha256="c" * 64),
                ref("https://cdn.example/a/X-Regular.ttf"),
            ),
        ),
        rec(
            "fontsource",
            "x",
            "X",
            files=(
                ref("https://cdn.example/f/X-Regular.ttf", sha256="a" * 64),
                ref("https://cdn.example/f/X[wght].ttf", role="variable", sha256="b" * 64),
            ),
        ),
    ]
    assert [r.url for r in candidates(recs)] == [
        "https://cdn.example/f/X-Regular.ttf",
        "https://cdn.example/f/X[wght].ttf",
        "https://cdn.example/b/X-Italic.ttf",
        "https://cdn.example/a/X-Regular.ttf",
    ]


def test_settled_by_metadata() -> None:
    gf = rec("google_metadata", "X", "X", category="Serif")
    assert settled_by_metadata([gf])  # variable alone never sends us to the files
    assert not settled_by_metadata([rec("fontsource", "x", "X", category="other")])
    assert not settled_by_metadata([rec("nerdfonts", "X", "X", is_monospace=True, category="mono")])


def test_universe_records_reads_only_universe_rows(tmp_path: Path) -> None:
    a = rec("fontsource", "x", "X", category="serif")
    obs = Observation(
        source="npm",
        series="365d",
        key=SourceKey("npm", "@fontsource/x"),
        value=5.0,
        unit="downloads",
        start=date(2025, 10, 3),
        end=date(2026, 10, 2),
    )
    write_jsonl([a], tmp_path / "fontsource.jsonl")
    write_jsonl([obs], tmp_path / "npm.jsonl")
    write_jsonl([replace(a, family="Baseline")], tmp_path / "fontsource@2026-09-03.jsonl")
    assert universe_records(tmp_path) == [a]
    # The pre-filter does not depend on the JSON's spacing.
    b = rec("fontsource", "y", "Y", category="serif")
    spaced = json.dumps(to_json(b), indent=None, separators=(", ", ": "))
    (tmp_path / "hand.jsonl").write_text(spaced + "\n")
    assert universe_records(tmp_path) == [a, b]


def test_with_hashes_fills_in_only_missing_hashes() -> None:
    ff = _facts(sha256="d" * 64)
    r = rec(
        "homebrew_casks",
        "font-x",
        "X",
        files=(ref("https://cdn.example/X.ttf"), ref("https://cdn.example/Y.ttf", sha256="e" * 64)),
    )
    (out,) = with_hashes([r], {"https://cdn.example/X.ttf": ff, "https://cdn.example/Y.ttf": ff})
    assert [f.sha256 for f in out.files] == ["d" * 64, "e" * 64]
    assert with_hashes([r], {}) == [r]


# --- the stage ----------------------------------------------------------------------------------

MONO_URL = "https://fonts.example/synth-mono/SynthMono-Regular.ttf"
VAR_URL = "https://fonts.example/synth-var/SynthVar%5Bwght%5D.ttf"
GONE_URL = "https://fonts.example/gone/Gone-Regular.ttf"
BACKUP_URL = "https://fonts.example/gone/Backup-Regular.otf"


def _fonts() -> dict[str, bytes]:
    return {
        MONO_URL: build_font(family="Synth Mono", mono=True, panose=(2, 11, 9)),
        VAR_URL: big_font(family="Synth Var", variable=True, panose=(2, 3, 3)),
        BACKUP_URL: build_font(family="Backup", cff=True, panose=(3, 0, 2), family_class=0),
    }


def _families(fonts: dict[str, bytes]) -> dict[str, list[UniverseRecord]]:
    return {
        "inter": [rec("google_metadata", "Inter", "Inter", category="Sans Serif", variable=True)],
        "synth-mono": [
            rec("homebrew_casks", "font-synth-mono", "Synth Mono", files=(ref(MONO_URL),))
        ],
        "synth-var": [
            rec(
                "fontsource",
                "synth-var",
                "Synth Var",
                category="other",
                files=(ref(VAR_URL, sha256=_sha(fonts[VAR_URL]), role="variable"),),
            )
        ],
        "backup": [
            rec(
                "homebrew_casks",
                "font-backup",
                "Backup",
                files=(ref(GONE_URL), ref(BACKUP_URL, role="italic")),
            )
        ],
        "zipped": [
            rec(
                "fontist",
                "zipped",
                "Zipped",
                files=(ref("https://github.com/o/z/releases/download/v1/Zipped.zip"),),
            )
        ],
    }


def _setup(root: Path) -> Paths:
    paths = Paths.for_root(root)
    fams = _families(_fonts())
    stageio.dump_stage(paths, "universe", universe(fams))
    by_source: dict[str, list[UniverseRecord]] = {}
    for r in all_records(fams):
        by_source.setdefault(r.source, []).append(r)
    for source, recs in by_source.items():
        write_jsonl(recs, paths.records / f"{source}.jsonl")
    return paths


def _ctx(paths: Paths, fetcher: Any, store: Any = None, replay: date | None = None) -> StageContext:
    return StageContext(
        paths=paths,
        config=cast("Config", None),  # the stage reads no config
        state=State(),
        run_date=RUN_DAY,
        store=store,
        fetcher=fetcher,
        log=LOG,
        options=RunOptions(from_snapshots=replay),
    )


EXPECTED = {
    "inter": Facts("sans-serif", False, True, True, "google_metadata"),
    "synth-mono": Facts("monospace", True, False, True, "font_file"),
    "synth-var": Facts("serif", False, True, True, "font_file"),
    "backup": Facts("handwriting", False, False, True, "font_file"),
    "zipped": Facts("sans-serif", False, False, True, "default"),
}


def test_the_stage_reads_what_the_metadata_leaves_open(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    server = FakeServer(_fonts())
    run(_ctx(paths, server))
    assert stageio.load_stage(paths, "facts") == EXPECTED
    urls = [url for url, _, _ in server.calls]
    assert set(urls) == {MONO_URL, VAR_URL, GONE_URL, BACKUP_URL}  # not Inter, not the zip
    assert (MONO_URL, None, None) in server.calls  # no sha256: downloaded whole
    assert all(start is not None for url, start, _ in server.calls if url == VAR_URL)
    assert (paths.cache / "fontfacts.jsonl").is_file()


def test_a_second_run_uses_the_cache_and_writes_the_same_bytes(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    run(_ctx(paths, FakeServer(_fonts())))
    first = stageio.stage_path(paths, "facts").read_bytes()
    again = FakeServer(_fonts())
    run(_ctx(paths, again))
    assert stageio.stage_path(paths, "facts").read_bytes() == first
    assert {url for url, _, _ in again.calls} == {GONE_URL}  # only the failure is retried


def test_a_replay_needs_no_network(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    run(_ctx(paths, FakeServer(_fonts())))
    first = stageio.stage_path(paths, "facts").read_bytes()
    run(_ctx(paths, None, replay=RUN_DAY))
    assert stageio.stage_path(paths, "facts").read_bytes() == first


def test_a_replay_without_cached_facts_falls_back_to_metadata(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    run(_ctx(paths, None, replay=RUN_DAY))
    out = stageio.load_stage(paths, "facts")
    assert out["inter"] == EXPECTED["inter"]
    assert out["synth-mono"] == Facts("sans-serif", False, False, True, "default")


def test_the_live_run_records_its_reads_and_a_replay_repeats_them(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    store = Store(tmp_path / "store")
    run(_ctx(paths, FakeServer(_fonts()), store=store))
    snap = store.snapshot("font_facts", RUN_DAY)
    assert snap is not None
    rows = {r["url"]: r for r in snap.iter_jsonl("facts.jsonl")}
    assert set(rows) == {MONO_URL, VAR_URL, GONE_URL, BACKUP_URL}
    assert rows[GONE_URL]["sha256"] is None
    assert "404" in rows[GONE_URL]["error"]
    assert (store.root / "_cache" / "fontfacts.jsonl").is_file()
    first = stageio.stage_path(paths, "facts").read_bytes()

    run(_ctx(paths, None, store=store, replay=RUN_DAY))
    assert stageio.stage_path(paths, "facts").read_bytes() == first

    # Had the mono file failed that day, the replay fails it too, although the cache knows it.
    record_reads(store, RUN_DAY, "facts", [FileRead(MONO_URL, None, "timeout")])
    run(_ctx(paths, None, store=store, replay=RUN_DAY))
    replayed = stageio.load_stage(paths, "facts")
    assert replayed["synth-mono"].basis == "default"
    assert replayed["synth-var"] == EXPECTED["synth-var"]


class _BrokenServer(FakeServer):
    """Raises what the real fetcher can raise besides FetchError: httpx.InvalidURL, ValueError."""

    def get(self, url: str, **kwargs: Any) -> Any:
        if url == MONO_URL:
            import httpx

            self.calls.append((url, None, None))
            raise httpx.InvalidURL(f"invalid url {url}")
        return super().get(url, **kwargs)

    def get_range(self, url: str, start: int, end: int, *, budget: str | None = None) -> Any:
        if url == VAR_URL:
            self.calls.append((url, start, end))
            raise ValueError("bad byte range")
        return super().get_range(url, start, end, budget=budget)


def test_one_bad_url_does_not_stop_the_stage(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    store = Store(tmp_path / "store")
    run(_ctx(paths, _BrokenServer(_fonts()), store=store))
    out = stageio.load_stage(paths, "facts")
    assert out["backup"] == EXPECTED["backup"]
    assert out["synth-mono"].basis == "default"
    assert fact_bases(out["synth-var"])["category"] == "default"
    snap = store.snapshot("font_facts", RUN_DAY)
    assert snap is not None
    rows = {r["url"]: r for r in snap.iter_jsonl("facts.jsonl")}
    assert rows[MONO_URL]["sha256"] is None
    assert "invalid url" in rows[MONO_URL]["error"]
    assert rows[VAR_URL]["sha256"] is None


def test_a_replay_warns_when_the_store_lacks_facts_the_live_run_had(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paths = _setup(tmp_path)
    store = Store(tmp_path / "store")
    record_reads(store, RUN_DAY, "facts", [FileRead(MONO_URL, "c" * 64)])  # no cache row
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        run(_ctx(paths, None, store=store, replay=RUN_DAY))
    assert stageio.load_stage(paths, "facts")["synth-mono"].basis == "default"
    assert any("no cached facts" in r.getMessage() for r in caplog.records)


def test_the_output_is_canonical_json(tmp_path: Path) -> None:
    paths = _setup(tmp_path)
    run(_ctx(paths, FakeServer(_fonts())))
    path = stageio.stage_path(paths, "facts")
    assert path.read_bytes() == jsonio.pretty_bytes(json.loads(path.read_bytes()))
    assert list(json.loads(path.read_bytes())) == sorted(EXPECTED)


# --- spot checks (milestone-1 step 5b) ----------------------------------------------------------


def test_spot_checks_from_google_metadata() -> None:
    fams = {
        "jetbrains-mono": [
            rec(
                "google_metadata",
                "JetBrains Mono",
                "JetBrains Mono",
                category="Monospace",
                variable=True,
            )
        ],
        "inter": [rec("google_metadata", "Inter", "Inter", category="Sans Serif", variable=True)],
    }
    out = derive_facts(universe(fams), all_records(fams), {})
    assert out["jetbrains-mono"].is_monospace is True
    assert (out["inter"].category, out["inter"].variable) == ("sans-serif", True)


@pytest.mark.network
def test_spot_checks_from_the_real_font_files(tmp_path: Path) -> None:
    """JetBrains Mono is monospace and Inter variable, read by range from the pinned files alone."""
    pinned = {f["key"]: f for f in pinned_fonts()}
    fams = {
        key: [
            rec(
                "homebrew_casks",
                f"font-{key}",
                key,
                files=(
                    ref(pinned[key]["url"], sha256=pinned[key]["sha256"], size=pinned[key]["size"]),
                ),
            )
        ]
        for key in ("inter", "jetbrains-mono")
    }
    paths = Paths.for_root(tmp_path)
    stageio.dump_stage(paths, "universe", universe(fams))
    write_jsonl(all_records(fams), paths.records / "homebrew_casks.jsonl")
    run(_ctx(paths, real_fetcher()))
    out = stageio.load_stage(paths, "facts")
    assert out["jetbrains-mono"].is_monospace is True
    assert out["jetbrains-mono"].category == "monospace"
    assert out["inter"].is_monospace is False
    assert (out["inter"].category, out["inter"].variable) == ("sans-serif", True)
    assert fact_bases(out["inter"])["formats"] == "font_file"


def test_fontsource_display_counts_only_when_nothing_else_is_listed() -> None:
    """Fontsource lists classifications alphabetically: ["display", "sans-serif"]."""
    both = rec("fontsource", "both", "Both", classifications=("display", "sans-serif"))
    alone = rec("fontsource", "alone", "Alone", classifications=("display",))
    hand = rec("fontsource", "hand", "Hand", classifications=("display", "handwriting"))
    assert family_facts([both], {}).category == "sans-serif"
    assert family_facts([alone], {}).category == "display"
    assert family_facts([hand], {}).category == "handwriting"


def test_google_families_filed_as_proportional_but_monospaced() -> None:
    cascadia = rec(
        "google_metadata",
        "Cascadia Code",
        "Cascadia Code",
        category="Sans Serif",
        is_monospace=False,
    )
    facts = family_facts([cascadia], {})
    assert (facts.category, facts.is_monospace) == ("sans-serif", True)
    inter = rec("google_metadata", "Inter", "Inter", category="Sans Serif", is_monospace=False)
    assert family_facts([inter], {}).is_monospace is False
