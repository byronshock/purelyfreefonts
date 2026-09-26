"""Collector "fontsource": trimming, records, drop codes, and fetch against the fixture.

The generic checks (manifest, golden file, schema) are in tests/collectors/test_contract.py.
"""

import logging
import re
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import cast

import pytest
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock
from tff_catalog.collectors import get
from tff_catalog.collectors.base import FetchContext, ParseContext, load_settings
from tff_catalog.collectors.universe import fontsource as fs
from tff_catalog.config_model import ConfigError, from_mapping
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import FontFileRef, LicenseFact, SourceKey, UniverseRecord
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir("fontsource")
DAY = date(2026, 9, 25)
LOG = logging.getLogger("tests.fontsource")
COMMIT = "0" * 40


def settings() -> fs.Fontsource.Settings:
    return cast(fs.Fontsource.Settings, load_settings(fs.COLLECTOR, Paths.for_root(ROOT)))


def parsed(snapshot: Snapshot) -> dict[tuple[str, str], UniverseRecord | LicenseFact]:
    """Records of ``snapshot`` by (record type, fs-id)."""
    ctx = ParseContext(snapshot=snapshot, settings=settings(), log=LOG)
    out = {}
    for r in fs.COLLECTOR.parse(ctx):
        assert isinstance(r, UniverseRecord | LicenseFact)
        out[(type(r).__name__, r.key.key)] = r
    return out


def fixture_records() -> dict[tuple[str, str], UniverseRecord | LicenseFact]:
    return parsed(regen.load_snapshot(FIXTURE / "snapshot", "fontsource"))


def run_fetch(tmp: Path, monkeypatch: pytest.MonkeyPatch, http: Path) -> tuple[Snapshot, Path]:
    """``fetch()`` offline against the fixture's git repository and the responses in ``http``."""
    for key, value in mockhttp.git_remotes(FIXTURE / "git", tmp / "remotes", DAY).items():
        monkeypatch.setenv(key, value)
    mock = mockhttp.MockHTTP.from_dir(http)
    store = Store(tmp / "store")
    raw = RawDir(tmp / "raw")
    c = fs.COLLECTOR
    with (
        clock.frozen(datetime.combine(DAY, time(6), tzinfo=UTC)),
        Fetcher(transport=mock.transport, min_interval=dict.fromkeys(c.hosts, 0.0)) as fetcher,
        store.writer(c.name, DAY, c.version) as writer,
    ):
        ctx = FetchContext(DAY, fetcher.scoped(c.hosts), writer, raw, None, settings(), LOG)
        c.fetch(ctx)
    snap = store.snapshot(c.name, DAY)
    assert snap is not None
    return snap, raw.path / "fontsource-registry"


# --- trimming --------------------------------------------------------------------------------


def test_count_scripts_reads_the_second_part_of_each_tag() -> None:
    tags = ["aa_Latn", "ab_Cyrl", "zh_Hans_CN", "en_Latn_US", "xx", "yy_latn", "zz_Qaaa"]
    assert fs.count_scripts(tags) == {"Cyrl": 1, "Hans": 1, "Latn": 2, "Qaaa": 1}


def test_trim_family_keeps_facts_and_drops_names_and_display_text() -> None:
    data = {
        "family": "Aster Sans",
        "designer": "Someone",
        "sampleText": {"short": "Hi"},
        "previewSubset": "latin",
        "previewContext": {"fallbackFamilies": ["sans-serif"]},
        "languages": ["aa_Latn", "ab_Cyrl", "ace_Latn"],
        "tags": ["sans/humanist"],
        "license": {"id": "OFL-1.1", "attribution": "The Aster Project Authors", "url": "x"},
        "futureKey": 1,
        "sources": [
            {
                "path": "a/Aster.ttf",
                "inspection": {
                    "codepoints": 95,
                    "unicodeRange": "U+0020-007E",
                    "features": {"gsub": ["liga"]},
                    "colorTables": [],
                },
            }
        ],
    }
    kept = fs.trim_family(data, keep_ranges=True, extra_tags=["special-use/emoji"])
    assert set(kept) == {"family", "tags", "license", "futureKey", "sources", "languageCounts"}
    assert kept["languageCounts"] == {"Cyrl": 1, "Latn": 2}
    assert kept["tags"] == ["sans/humanist", "special-use/emoji"]
    assert kept["license"] == data["license"]
    assert kept["sources"][0]["inspection"] == {
        "codepoints": 95,
        "unicodeRange": "U+0020-007E",
        "colorTables": [],
    }
    dropped = fs.trim_family(data, keep_ranges=False, override={"languages": []})
    assert dropped["languageCounts"] == {}
    assert dropped["sources"][0]["inspection"] == {"codepoints": 95, "colorTables": []}
    assert "unicodeRange" in data["sources"][0]["inspection"]  # the input is not changed


def test_api_missing_keeps_rows_the_registry_lacks_sorted() -> None:
    rows = [{"id": "b"}, {"id": "a"}, {"id": "inter"}, {"family": "no id"}, "junk", {"id": ""}]
    assert fs.api_missing(rows, {"inter"}) == [{"id": "a"}, {"id": "b"}]
    with pytest.raises(fs.RegistryError, match="expected a list"):
        fs.api_missing({"fonts": []}, set())


# --- file references, URLs and drop codes ----------------------------------------------------


@pytest.mark.parametrize(
    ("src", "role"),
    [
        ({"variant": {"style": "italic", "weight": 400}, "inspection": {"axes": [{}]}}, "italic"),
        ({"variant": {"style": "normal", "weight": 400}, "inspection": {"axes": [{}]}}, "variable"),
        ({"variant": {"style": "normal", "weight": 400}, "inspection": {"axes": []}}, "regular"),
        ({"variant": {"style": "normal", "weight": 700}, "inspection": {}}, "other"),
        ({"inspection": {"style": "normal", "weight": 400}}, "regular"),
        ({"inspection": {"style": "italic", "weight": 400}}, "italic"),
    ],
)
def test_file_role(src: dict, role: str) -> None:
    assert fs.file_role(src) == role


def test_file_ref_is_pinned_escaped_and_checked() -> None:
    prov = {"repository": "google/fonts", "revision": COMMIT, "type": "github"}
    src = {
        "path": "ofl/inter/Inter-Italic[opsz,wght].ttf",
        "sha256": "a" * 64,
        "size": 906596,
        "variant": {"style": "italic", "weight": 400},
        "inspection": {"codepoints": 2813, "unicodeRange": "U+0020-007E", "axes": [{}]},
    }
    assert fs.file_ref(src, prov) == FontFileRef(
        url=f"https://raw.githubusercontent.com/google/fonts/{COMMIT}"
        "/ofl/inter/Inter-Italic%5Bopsz,wght%5D.ttf",
        sha256="a" * 64,
        size=906596,
        role="italic",
        codepoints=2813,
        unicode_range="U+0020-007E",
    )
    spaced = fs.file_ref({"path": "sources/x/files/X Var.ttf", "sha256": "bad", "size": -1}, prov)
    assert spaced is not None
    assert spaced.url.endswith("/sources/x/files/X%20Var.ttf")
    assert (spaced.sha256, spaced.size, spaced.codepoints) == (None, None, None)
    assert fs.file_ref(src, {**prov, "type": "gitlab"}) is None
    assert fs.file_ref(src, {**prov, "repository": "not a slug"}) is None
    assert fs.file_ref({"sha256": "a" * 64}, prov) is None


def test_license_text_url_is_pinned_to_the_cloned_commit() -> None:
    meta = {"repository": "https://github.com/fontsource/fontsource.git", "commit": COMMIT}
    assert fs.license_text_url(meta, "google", "inter") == (
        f"https://raw.githubusercontent.com/fontsource/fontsource/{COMMIT}"
        "/registry/data/families/google/inter/license.txt"
    )
    assert fs.license_text_url({**meta, "commit": "main"}, "google", "inter") is None
    assert fs.license_text_url({**meta, "repository": "https://gitlab.com/a/b"}, "g", "i") is None


def test_drop_code_precedence() -> None:
    names = fs.compile_drop_names([("icon", r"^Material (Icons|Symbols)\b")])
    music = {
        "family": "Bravura",
        "classifications": ["symbols"],
        "tags": ["special-use/music-symbols"],
    }
    assert fs.drop_code("google-icons", {"family": "Material Icons"}, names) == "icon"
    assert fs.drop_code("fontsource", music, names) == "music"  # the tag beats "symbols"
    both = {"tags": ["special-use/music-symbols", "special-use/emoji"]}
    assert fs.drop_code("google", both) == "emoji"  # records.DropReason order
    assert fs.drop_code("fontsource", {"classifications": ["symbols"]}) == "symbol"
    assert fs.drop_code("google", {"family": "Material Symbols"}, names) == "icon"
    # Any "symbols" classification, as google_metadata and google_repo rule (Flow, YakuHan).
    assert fs.drop_code("google", {"classifications": ["display", "symbols"]}) == "symbol"
    assert fs.drop_code("google", {"classifications": ["display", "symbols"]}, (), ()) is None
    punctuation = {"classifications": ["sans-serif"], "tags": ["special-use/punctuation"]}
    assert fs.drop_code("fontsource", punctuation) == "symbol"
    # Redaction is a text family; its tag alone drops nothing.
    assert (
        fs.drop_code("google", {"family": "Redaction", "tags": ["special-use/redaction"]}) is None
    )
    assert fs.drop_code("google", {"classifications": "symbols", "tags": "x"}) is None  # not lists


def test_drop_names_must_use_drop_reasons() -> None:
    with pytest.raises(ConfigError, match="iconz"):
        from_mapping(fs.Fontsource.Settings, {"drop_names": [["iconz", "x"]]})


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("drop_names", [["icon", "(unclosed"]], "bad pattern"),
        ("repository", "https://gitlab.com/fontsource/fontsource", "repository"),
        ("repository", "git@github.com:fontsource/fontsource.git", "repository"),
        ("ref", "--upload-pack=x", "ref"),
        ("ref", "main branch", "ref"),
        ("api_url", "http://api.fontsource.org/v1/fonts", "api_url"),
        ("full_ranges", ["Fontsource"], "full_ranges"),
    ],
)
def test_settings_are_checked_when_loaded(key: str, value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        from_mapping(fs.Fontsource.Settings, {key: value})


def test_the_settings_file_spells_out_the_defaults() -> None:
    assert settings() == fs.Fontsource.Settings()


# --- parse of the fixture snapshot -----------------------------------------------------------


def test_registry_wins_over_the_stale_api() -> None:
    recs = fixture_records()
    lic = recs[("LicenseFact", "dejavu-sans")]
    assert isinstance(lic, LicenseFact)
    assert (lic.raw, lic.spdx) == ("Bitstream-Vera", "Bitstream-Vera")  # the API says OFL-1.1
    assert lic.text_url is not None
    assert lic.text_url.endswith("/registry/data/families/fontsource/dejavu-sans/license.txt")
    dejavu = recs[("UniverseRecord", "dejavu-sans")]
    assert dict(dejavu.attrs) == {"fs_group": "fontsource", "origin": "registry"}
    assert dict(dejavu.urls) == {
        "license": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/"
        "0eda8a319c08835009849583cd090bb5b141ce25/LICENSE",
        "repository": "https://github.com/dejavu-fonts/dejavu-fonts",
    }


def test_registry_records_carry_the_facts_later_stages_read() -> None:
    recs = fixture_records()
    jb = recs[("UniverseRecord", "jetbrains-mono")]
    assert isinstance(jb, UniverseRecord)
    assert (jb.is_monospace, jb.variable, jb.added) == (True, True, date(2020, 11, 18))
    assert jb.latin_languages == 4
    assert [f.role for f in jb.files] == ["variable", "italic"]
    assert all(f.unicode_range is None and f.codepoints for f in jb.files)  # google group
    assert ("repository", "https://github.com/JetBrains/JetBrainsMono") in jb.urls
    neon = recs[("UniverseRecord", "monaspace-neon")]
    assert isinstance(neon, UniverseRecord)
    assert neon.files[0].url.endswith("/Monaspace%20Neon%20Var.ttf")
    assert neon.files[0].unicode_range == "U+0020-007E, U+00A0-0148, U+014A-017E, U+0188"
    flow = recs[("UniverseRecord", "flow-circular")]
    assert isinstance(flow, UniverseRecord)
    assert flow.latin_languages == 0  # family-overrides empties the languages


def test_deprecated_ids_name_their_replacement_but_assert_no_alias() -> None:
    recs = fixture_records()
    for fid, new in (("geist-sans", "geist"), ("source-sans-pro", "source-sans-3")):
        r = recs[("UniverseRecord", fid)]
        assert isinstance(r, UniverseRecord)
        assert r.status == "deprecated"
        assert dict(r.attrs)["replaced_by"] == new
        assert r.names == ()


def test_non_text_families_get_drop_codes() -> None:
    recs = fixture_records()
    drops = {k: r.drop for (t, k), r in recs.items() if t == "UniverseRecord" and r.drop}
    assert drops == {
        "bravura": "music",  # tag from family-tags.json
        "flow-circular": "symbol",  # ["display", "symbols"]
        "material-icons": "icon",  # the google-icons group
        "material-symbols": "icon",  # API-only, by drop_names
        "noto-color-emoji": "emoji",  # tag from family-tags.json
    }


def test_api_only_ids_come_from_the_api_rows() -> None:
    recs = fixture_records()
    api = {k for (t, k), r in recs.items() if dict(r.attrs).get("origin") == "api"}
    assert api == {"google-sans", "material-symbols"}
    gs = recs[("UniverseRecord", "google-sans")]
    assert isinstance(gs, UniverseRecord)
    assert (gs.category, gs.variable, gs.subsets, gs.files) == ("sans-serif", True, (), ())
    lic = recs[("LicenseFact", "google-sans")]
    assert isinstance(lic, LicenseFact)
    assert (lic.raw, lic.spdx, lic.text_url) == ("OFL-1.1", None, None)


def test_api_records_skip_rows_without_a_family() -> None:
    assert list(fs.api_records("fontsource", {"id": "x", "family": " "})) == []
    recs = list(fs.api_records("fontsource", {"id": "x", "family": "X", "category": "icons"}))
    assert [type(r).__name__ for r in recs] == ["UniverseRecord"]
    assert recs[0].drop == "icon"
    assert recs[0].key == SourceKey("fs-id", "x")


def _family(name: str, **extra: object) -> dict[str, object]:
    return {"family": name, "classifications": ["sans-serif"], "status": "active", **extra}


def _snapshot(tmp: Path, extracts: dict[str, object]) -> Snapshot:
    """A snapshot holding ``extracts`` (``.jsonl.gz``: a list of rows; else one JSON document)."""
    with Store(tmp / "store").writer("fontsource", DAY, 1) as writer:
        for name, doc in extracts.items():
            if name.endswith(".jsonl.gz"):
                writer.write_jsonl(name, cast(list, doc))
            else:
                writer.write_json(name, doc)
    snap = Store(tmp / "store").snapshot("fontsource", DAY)
    assert snap is not None
    return snap


def test_parse_keeps_the_first_group_of_an_id_and_skips_unnamed_families(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    meta = {
        "repository": "https://github.com/fontsource/fontsource",
        "ref": "main",
        "commit": COMMIT,
    }
    snap = _snapshot(
        tmp_path,
        {
            "registry.json": meta,
            "families/fontsource.jsonl.gz": [
                {"id": "aster", "family.json": _family("Aster", status="retired")},
                {"id": "nameless", "family.json": _family(" ")},
            ],
            "families/google.jsonl.gz": [{"id": "aster", "family.json": _family("Aster G")}],
            # No replacements.json, as a registry without legacy ids could give.
            "api-missing.json": [
                {"id": "aster", "family": "Aster API"},
                {"id": "nameless", "family": "Nameless", "license": "mit"},
            ],
        },
    )
    with caplog.at_level(logging.WARNING, logger=LOG.name):
        recs = parsed(snap)
    aster = recs[("UniverseRecord", "aster")]
    assert isinstance(aster, UniverseRecord)
    assert (aster.family, aster.status, dict(aster.attrs)["fs_group"]) == (
        "Aster",
        "live",
        "fontsource",
    )
    assert "replaced_by" not in dict(aster.attrs)
    # A registry row without a name is skipped, so the API's row for that id is used.
    nameless = recs[("UniverseRecord", "nameless")]
    assert dict(nameless.attrs)["origin"] == "api"
    assert recs[("LicenseFact", "nameless")].raw == "mit"
    assert set(recs) == {
        ("UniverseRecord", "aster"),
        ("UniverseRecord", "nameless"),
        ("LicenseFact", "nameless"),
    }
    text = caplog.text
    assert "aster is in two registry groups" in text
    assert "nameless has no family name" in text
    assert "aster has status 'retired'" in text


def test_parse_deprecates_an_api_only_id_that_replacements_json_retires(tmp_path: Path) -> None:
    snap = _snapshot(
        tmp_path,
        {
            "registry.json": {"repository": "https://github.com/fontsource/fontsource"},
            "families/google.jsonl.gz": [{"id": "new-sans", "family.json": _family("New Sans")}],
            "replacements.json": {"old-sans": "new-sans"},
            "api-missing.json": [{"id": "old-sans", "family": "Old Sans", "license": "OFL-1.1"}],
        },
    )
    old = parsed(snap)[("UniverseRecord", "old-sans")]
    assert isinstance(old, UniverseRecord)
    assert (old.status, dict(old.attrs)) == (
        "deprecated",
        {"origin": "api", "replaced_by": "new-sans"},
    )


@pytest.mark.parametrize(
    ("extracts", "message"),
    [
        ({"registry.json": [], "api-missing.json": []}, "registry.json: expected an object"),
        ({"registry.json": {}, "replacements.json": []}, "replacements.json: expected an object"),
        ({"registry.json": {}, "families/google.jsonl.gz": [["inter"]]}, "bad row"),
        ({"registry.json": {}, "families/google.jsonl.gz": [{"id": "inter"}]}, "bad row"),
        ({"registry.json": {}, "api-missing.json": {"fonts": []}}, "expected a list"),
    ],
)
def test_parse_refuses_extracts_it_cannot_read(
    tmp_path: Path, extracts: dict[str, object], message: str
) -> None:
    with pytest.raises(fs.RegistryError, match=message):
        parsed(_snapshot(tmp_path, extracts))


def test_read_registry_refuses_a_clone_without_families(tmp_path: Path) -> None:
    with pytest.raises(fs.RegistryError, match="no registry families"):
        fs.read_registry(tmp_path, ())
    odd = tmp_path / fs.FAMILIES_DIR / "Google Fonts" / "inter"
    odd.mkdir(parents=True)
    (odd / "family.json").write_text("{}")
    with pytest.raises(fs.RegistryError, match="unexpected registry group name"):
        fs.read_registry(tmp_path, ())


def test_urls_the_schema_would_refuse_are_left_out() -> None:
    prov = {"repository": "google/fonts", "revision": "main branch", "type": "github"}
    assert fs.file_ref({"path": "ofl/inter/Inter.ttf"}, prov) is None
    fam = {
        "project": {"repository": "https://github.com/a/b c"},
        "license": {"url": f"https://raw.githubusercontent.com/a/b/{COMMIT}/OFL .txt"},
    }
    assert fs._urls(fam) == ()
    fam = {"project": {"repository": "ftp://example.org/x"}}
    assert fs._urls(fam) == ()
    unpinned = {"license": {"url": "https://raw.githubusercontent.com/a/b/main/OFL.txt"}}
    assert fs._urls(unpinned) == ()


@pytest.mark.parametrize(
    ("url", "pinned"),
    [
        (f"https://raw.githubusercontent.com/a/b/{COMMIT}/OFL.txt", True),
        (f"https://raw.githubusercontent.com/a/b/{COMMIT}/fonts/LICENSE", True),
        (f"https://gitlab.example.org/a/b/-/raw/{COMMIT}/LICENSE", True),  # GitLab
        (f"https://forge.example.org/a/b/raw/commit/{COMMIT}/OFL.txt", True),  # Forgejo
        (f"https://github.com/a/b/raw/{COMMIT}/OFL.txt", True),
        (f"https://github.com/a/b/blob/{COMMIT}/OFL.txt", False),  # a web page
        ("https://gitlab.example.org/a/b/-/raw/main/LICENSE", False),  # a branch
        (f"https://gitlab.example.org/a/b/-/raw/{COMMIT}", False),  # no file after the sha
        (f"https://raw.githubusercontent.com/a/{COMMIT}/main/OFL.txt", False),
        (f"http://gitlab.example.org/a/b/-/raw/{COMMIT}/LICENSE", False),  # not https
        ("https://openfontlicense.org/open-font-license-official-text/", False),
        ("https://example.org/static/family.zip", False),
    ],
)
def test_pinned_raw_file(url: str, pinned: bool) -> None:
    assert fs.pinned_raw_file(url) is pinned


def test_a_license_file_pinned_on_another_forge_is_kept() -> None:
    # As the registry gives Adwaita (gitlab.gnome.org) and OpenDyslexic (a Forgejo host).
    lic = f"https://gitlab.example.org/team/family/-/raw/{COMMIT}/LICENSE"
    fam = {
        "project": {"repository": "https://gitlab.example.org/team/family"},
        "license": {"url": lic},
    }
    assert fs._urls(fam) == (
        ("license", lic),
        ("repository", "https://gitlab.example.org/team/family"),
    )


def test_api_ids_the_registry_retired_are_deprecated() -> None:
    row = {"id": "old-sans", "family": "Old Sans", "type": "other", "license": "OFL-1.1"}
    rec, lic = fs.api_records("fontsource", row, (), {"old-sans": "new-sans", "a": "b"})
    assert isinstance(rec, UniverseRecord)
    assert isinstance(lic, LicenseFact)
    assert (rec.status, dict(rec.attrs)["replaced_by"], rec.names) == ("deprecated", "new-sans", ())
    live, _ = fs.api_records("fontsource", row, (), {"other-id": "new-sans"})
    assert isinstance(live, UniverseRecord)
    assert live.status == "live"
    assert "replaced_by" not in dict(live.attrs)


# --- fetch against the fixture ---------------------------------------------------------------


def test_fetch_checks_out_only_family_json_and_the_small_registry_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap, checkout = run_fetch(tmp_path, monkeypatch, FIXTURE / "http")
    files = sorted(p.relative_to(checkout).as_posix() for p in checkout.rglob("*") if p.is_file())
    files = [f for f in files if not f.startswith(".git/")]
    families_dir = "registry/data/families/"
    aux = [f for f in files if not f.startswith(families_dir)]
    assert aux == [f"registry/data/{n}" for n in sorted(fs.AUX_FILES)]  # no languages.json
    checked_out = [f.removeprefix(families_dir) for f in files if f.startswith(families_dir)]
    assert len(checked_out) == 9  # no license.txt or description
    assert all(re.fullmatch(r"[^/]+/[^/]+/family\.json", f) for f in checked_out)
    families = b"".join(
        snap.read_bytes(e.path) for e in snap.manifest.extracts if e.path.startswith("families/")
    )
    for text in (b"Fixture Designer", b"sampleText", b"gsub", b'"languages"'):
        assert text not in families
    assert snap.manifest.notes[0].startswith(
        "git https://github.com/fontsource/fontsource main at "
    )
    (api,) = snap.manifest.fetched
    assert (api.url, api.status, api.kept) == ("https://api.fontsource.org/v1/fonts", 200, False)
    meta = snap.load_json("registry.json")
    assert meta["commit"] == snap.manifest.notes[0].rsplit(" ", 1)[1]
    assert [r["id"] for r in snap.load_json("api-missing.json")] == [
        "google-sans",
        "material-symbols",
    ]


def test_fetch_reproduces_the_committed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture snapshot is what fetch() writes, byte for byte, so the two cannot drift."""
    snap, _ = run_fetch(tmp_path, monkeypatch, FIXTURE / "http")
    committed = regen.load_snapshot(FIXTURE / "snapshot", "fontsource")
    assert snap.manifest.to_json() == committed.manifest.to_json()
    for entry in committed.manifest.extracts:
        assert snap.read_bytes(entry.path) == committed.read_bytes(entry.path), entry.path


def test_fetch_fails_on_an_api_answer_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = tmp_path / "http"
    http.mkdir()
    (http / "fonts.json").write_text('{"error": "not found"}')
    mockhttp.write_index(
        http, [{"url": "https://api.fontsource.org/v1/fonts", "body": "fonts.json"}]
    )
    with pytest.raises(fs.RegistryError, match="expected a list"):
        run_fetch(tmp_path, monkeypatch, http)
    assert Store(tmp_path / "store").snapshot("fontsource", DAY) is None


# --- the real sources --------------------------------------------------------------------------


def _points(text: str) -> int:
    total = 0
    for part in text.split(","):
        lo, _, hi = part.strip().removeprefix("U+").partition("-")
        total += int(hi or lo, 16) - int(lo, 16) + 1
    return total


@pytest.mark.network
def test_real_registry_and_api(tmp_path: Path) -> None:
    """One real fetch (a sparse clone and one API call) and a parse of it."""
    c = get("fontsource")
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    with Fetcher(log=LOG) as fetcher, store.writer(c.name, day, c.version) as writer:
        ctx = FetchContext(
            day, fetcher.scoped(c.hosts), writer, RawDir(tmp_path / "raw"), None, settings(), LOG
        )
        c.fetch(ctx)
    snap = store.snapshot(c.name, day)
    assert snap is not None
    assert snap.manifest.extract_bytes < 4 * 1024 * 1024
    recs = parsed(snap)
    universe = {k: r for (t, k), r in recs.items() if t == "UniverseRecord"}
    assert len(universe) > 2000
    inter = universe["inter"]
    assert isinstance(inter, UniverseRecord)
    assert inter.variable
    assert inter.files
    assert dict(inter.attrs)["fs_group"] == "google"
    assert recs[("LicenseFact", "dejavu-sans")].raw == "Bitstream-Vera"
    iosevka = universe["iosevka"]
    assert isinstance(iosevka, UniverseRecord)
    assert iosevka.is_monospace
    for ref in iosevka.files:
        assert ref.unicode_range
        assert ref.codepoints == _points(ref.unicode_range)
