"""The foundries collector: the hand list of foundry families (ruling M12).

The generic checks run in ``tests/collectors/test_contract.py`` on a fixture
with its own synthetic list, ``root/config/foundries.toml`` (``regen_fixture``),
which the contract test's fetch reads through ``FetchContext.paths``. These
tests pin the collector's own rules on a synthetic list and synthetic HTTP
answers, check that the real list parses, and make one real check of two
listed URLs, marked ``network``.
"""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from tests.collectors.foundries import regen_fixture
from tests.collectors.foundries.regen_fixture import DAY, run_fetch
from tests.helpers import ROOT, mockhttp, regen

from tff_catalog import clock, jsonio
from tff_catalog.collectors.base import FetchContext, load_settings
from tff_catalog.collectors.universe.foundries import (
    CHECKS_EXTRACT,
    COLLECTOR,
    LIST_EXTRACT,
    LIST_FILE,
    NOT_CHECKED_HOST,
    NOT_CHECKED_OFF,
    Settings,
    by_name,
    list_doc,
    list_notes,
    list_path,
    load_list,
    read_list,
)
from tff_catalog.config_model import ConfigError
from tff_catalog.fetch import Fetcher
from tff_catalog.paths import Paths
from tff_catalog.records import LicenseFact, Record, SourceKey, UniverseRecord
from tff_catalog.store import RawDir, Snapshot, Store

FIXTURE = regen.fixture_dir(COLLECTOR.name)
SETTINGS = load_settings(COLLECTOR, Paths.for_root(ROOT))
REBUILD = (
    "the fixture's root/config/foundries.toml changed: "
    "run uv run python -m tests.collectors.foundries.regen_fixture"
)
LOG = logging.getLogger("tests.foundries")

# A synthetic list: invented foundries and families, URLs on the checked hosts
# (answered by the mock only) and one on a host the collector never contacts.
SYNTH_LIST = """\
schema = 1

[foundries.aster-type]
name = "Aster Type"
url = "https://aster.synth.example"

[[foundries.aster-type.families]]
name = "Aster Sans"
url = "https://www.theleagueofmoveabletype.com/aster-sans"
license = "Open Font License"
repository = "https://github.com/aster-type/aster-sans"

[[foundries.aster-type.families]]
name = "Birch  Mono"
url = "https://gitlab.com/aster-type/birch-mono"
license = "SIL Open Font License 1.1"
repository = "https://gitlab.com/aster-type/birch-mono"

[[foundries.aster-type.families]]
name = "Cobalt Serif"
url = "https://velvetyne.fr/fonts/cobalt-serif"
license = "NOASSERTION"

[foundries.birch-foundry]
name = "Birch Foundry"
url = "https://birch.synth.example"

[[foundries.birch-foundry.families]]
name = "Aster Sans"
url = "https://open-foundry.com/fonts/aster-sans"
license = "SIL Open Font License v.1.1"
repository = "https://github.com/aster-type/aster-sans"

[[foundries.birch-foundry.families]]
name = "Dune Display"
url = "https://www.fontshare.com/fonts/dune-display"
license = ""
repository = "https://github.com/birch-foundry/dune-display"

[[foundries.birch-foundry.families]]
name = "Elm Script"
url = "https://open-foundry.com/fonts/elm-script"
license = "OFL"
"""
ASTER_PAGE = "https://www.theleagueofmoveabletype.com/aster-sans"
ASTER_REPO = "https://github.com/aster-type/aster-sans"
ASTER_SHOWCASE = "https://open-foundry.com/fonts/aster-sans"
BIRCH_REPO = "https://gitlab.com/aster-type/birch-mono"
COBALT_PAGE = "https://velvetyne.fr/fonts/cobalt-serif"
DUNE_PAGE = "https://www.fontshare.com/fonts/dune-display"
DUNE_REPO = "https://github.com/birch-foundry/dune-display"
ELM_PAGE = "https://open-foundry.com/fonts/elm-script"
# (method, url, status, extra headers); a GET answer carries a small page.
ANSWERS: list[tuple[str, str, int, dict[str, str]]] = [
    ("HEAD", ASTER_PAGE, 405, {}),  # refuses HEAD: one GET follows
    ("GET", ASTER_PAGE, 200, {}),
    ("HEAD", ASTER_REPO, 200, {}),
    ("HEAD", ASTER_SHOWCASE, 301, {"location": ASTER_SHOWCASE + "/"}),
    ("HEAD", ASTER_SHOWCASE + "/", 200, {}),
    ("HEAD", BIRCH_REPO, 404, {}),
    ("GET", BIRCH_REPO, 404, {}),
    ("HEAD", DUNE_PAGE, 403, {}),
    ("GET", DUNE_PAGE, 403, {}),
    ("HEAD", DUNE_REPO, 410, {}),
    ("GET", DUNE_REPO, 410, {}),
    ("HEAD", ELM_PAGE, 302, {"location": "https://elsewhere.synth.example/elm"}),
]


def write_list(tmp: Path, text: str = SYNTH_LIST) -> Settings:
    """Write a list file under ``tmp``; return settings that read it."""
    path = tmp / "foundries.toml"
    path.write_text(text, encoding="utf-8")
    return Settings(list_file=str(path))


def write_answers(directory: Path, answers: list[tuple[str, str, int, dict[str, str]]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "page.html").write_text("<!doctype html><title>synthetic</title>\n")
    entries = []
    for method, url, status, headers in answers:
        entry: dict[str, Any] = {"method": method, "url": url, "status": status}
        entry["headers"] = {"content-type": "text/html", **headers}
        if method == "GET":
            entry["body"] = "page.html"
        entries.append(entry)
    mockhttp.write_index(directory, entries)
    return directory


def synth_fetch(tmp: Path, settings: Settings | None = None) -> tuple[Snapshot, mockhttp.MockHTTP]:
    settings = settings or write_list(tmp)
    return run_fetch(tmp, write_answers(tmp / "http", ANSWERS), settings)


def parse(snapshot: Snapshot, settings: object = SETTINGS) -> list[Record]:
    return regen.parse_records(COLLECTOR, snapshot, settings)


def families(recs: list[Record]) -> dict[str, UniverseRecord]:
    return {r.key.key: r for r in recs if isinstance(r, UniverseRecord)}


def licenses(recs: list[Record]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    out: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for r in recs:
        if isinstance(r, LicenseFact):
            out.setdefault(r.key.key, []).append((r.raw, dict(r.attrs)))
    return out


def checks(snapshot: Snapshot) -> dict[str, dict[str, Any]]:
    return {row["url"]: row for row in snapshot.iter_jsonl(CHECKS_EXTRACT)}


# --- settings and the list -------------------------------------------------------------------


def test_settings_file_names_the_list() -> None:
    assert isinstance(SETTINGS, Settings)
    assert (SETTINGS.list_file, SETTINGS.check_urls, SETTINGS.gone_statuses) == (
        LIST_FILE,
        True,
        (404, 410),
    )
    assert list_path(SETTINGS, ROOT) == ROOT / "config" / "foundries.toml"
    assert list_path(Settings(list_file="/elsewhere/list.toml"), ROOT) == Path(
        "/elsewhere/list.toml"
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"list_file": "../foundries.toml"}, "list_file"),
        ({"list_file": "config/foundries.json"}, "list_file"),
        ({"gone_statuses": (200,)}, "gone_statuses"),
    ],
    ids=["parent-directory", "not-toml", "success-status"],
)
def test_settings_reject_bad_values(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings(**changes)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('license = "OFL"\n', 'license = "OFL"\nlicence = "OFL"\n', "unknown key"),
        (f'url = "{ELM_PAGE}"', 'url = "http://open-foundry.com/fonts/elm-script"', "https"),
        ("schema = 1", "schema = 2", "schema 2"),
    ],
    ids=["unknown-key", "plain-http", "schema"],
)
def test_a_bad_list_fails_the_fetch(tmp_path: Path, old: str, new: str, message: str) -> None:
    settings = write_list(tmp_path, SYNTH_LIST.replace(old, new))
    with pytest.raises(ConfigError, match=message):
        synth_fetch(tmp_path, settings)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_an_empty_list_fails_the_fetch(tmp_path: Path) -> None:
    settings = write_list(
        tmp_path,
        'schema = 1\n\n[foundries.aster]\nname = "A"\nurl = "https://a.synth.example"\nfamilies = []\n',
    )
    with pytest.raises(ValueError, match="lists no family"):
        synth_fetch(tmp_path, settings)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


def test_a_blank_family_name_fails_the_fetch(tmp_path: Path) -> None:
    """A blank name could not be keyed; failing beats dropping the entry silently."""
    settings = write_list(tmp_path, SYNTH_LIST.replace('name = "Elm Script"', 'name = "  "'))
    with pytest.raises(ConfigError, match=r"foundries\.birch-foundry\.families\[2\]: blank name"):
        synth_fetch(tmp_path, settings)
    assert Store(tmp_path / "store").snapshot(COLLECTOR.name, DAY) is None


# --- fetch ------------------------------------------------------------------------------------


def test_fetch_keeps_the_list_as_loaded(tmp_path: Path) -> None:
    settings = write_list(tmp_path)
    snap, _ = synth_fetch(tmp_path, settings)
    doc = snap.load_json(LIST_EXTRACT)
    assert doc == json.loads(jsonio.canonical_bytes(list_doc(load_list(list_path(settings)))))
    assert list(doc["foundries"]) == ["aster-type", "birch-foundry"]
    assert doc["foundries"]["aster-type"]["families"][1]["name"] == "Birch  Mono"  # as written
    extracts = {e.path: e.rows for e in snap.manifest.extracts}
    assert extracts == {LIST_EXTRACT: 6, CHECKS_EXTRACT: 8}
    assert snap.manifest.data_date == DAY


def test_fetch_checks_every_family_url_once(tmp_path: Path) -> None:
    snap, mock = synth_fetch(tmp_path)
    rows = checks(snap)
    assert list(rows) == sorted(rows)
    got = {url: (row["status"], row["final_url"]) for url, row in rows.items()}
    assert got == {
        ASTER_REPO: (200, ASTER_REPO),  # listed twice, checked once
        DUNE_REPO: (410, DUNE_REPO),
        BIRCH_REPO: (404, BIRCH_REPO),  # page and repository are one URL
        ASTER_SHOWCASE: (200, ASTER_SHOWCASE + "/"),  # followed a redirect
        ELM_PAGE: (None, None),
        COBALT_PAGE: (None, None),
        DUNE_PAGE: (403, DUNE_PAGE),
        ASTER_PAGE: (200, ASTER_PAGE),  # HEAD 405, then GET 200
    }
    assert "HostNotAllowed" in rows[ELM_PAGE]["error"]
    assert rows[COBALT_PAGE]["error"] == NOT_CHECKED_HOST
    assert all(rows[u]["error"] is None for u in rows if u not in (ELM_PAGE, COBALT_PAGE))
    # The foundry home pages and hosts outside `hosts` are never contacted.
    assert mock.hosts() <= set(COLLECTOR.hosts)
    assert not mock.unmatched


def test_manifest_lists_every_answered_request(tmp_path: Path) -> None:
    snap, mock = synth_fetch(tmp_path)
    listed = [(f.url, f.status) for f in snap.manifest.fetched]
    assert sorted(listed) == sorted(
        [
            (ASTER_PAGE, 200),  # HEAD 405 is recorded with its own status
            (ASTER_PAGE, 405),
            (ASTER_REPO, 200),
            (ASTER_SHOWCASE, 200),
            (ASTER_SHOWCASE + "/", 200),  # the redirect hop
            (BIRCH_REPO, 404),
            (BIRCH_REPO, 404),
            (DUNE_PAGE, 403),
            (DUNE_PAGE, 403),
            (DUNE_REPO, 410),
            (DUNE_REPO, 410),
        ]
    )
    # The request redirected off the collector's hosts got no answer to record.
    requested = {mockhttp.normalize_url(u) for u in mock.urls()}
    assert requested - {mockhttp.normalize_url(u) for u, _ in listed} == {ELM_PAGE}


def test_fetch_notes_what_the_owner_should_look_at(tmp_path: Path) -> None:
    snap, _ = synth_fetch(tmp_path)
    notes = snap.manifest.notes
    assert notes[0] == "listed by several foundries: Aster Sans (aster-type, birch-foundry)"
    assert list(notes[1:]) == [  # by URL
        f"gone (HTTP 410): {DUNE_REPO}",
        f"gone (HTTP 404): {BIRCH_REPO}",
        f"no answer: {ELM_PAGE} ({checks(snap)[ELM_PAGE]['error']})",
        f"not checked (host not in hosts): {COBALT_PAGE}",
        f"HTTP 403: {DUNE_PAGE}",
    ]


def test_a_failed_get_fallback_keeps_the_head_answer(tmp_path: Path) -> None:
    """HEAD 405, then the GET is redirected off the collector's hosts: the HEAD still counts."""
    page = "https://www.fontshare.com/fonts/fig-sans"
    text = (
        'schema = 1\n\n[foundries.fig]\nname = "Fig"\nurl = "https://fig.synth.example"\n\n'
        f'[[foundries.fig.families]]\nname = "Fig Sans"\nurl = "{page}"\nlicense = "OFL"\n'
    )
    answers = [
        ("HEAD", page, 405, {}),
        ("GET", page, 302, {"location": "https://elsewhere.synth.example/fig"}),
    ]
    snap, _ = run_fetch(
        tmp_path, write_answers(tmp_path / "http", answers), write_list(tmp_path, text)
    )
    row = checks(snap)[page]
    assert (row["status"], row["final_url"]) == (405, page)
    assert row["error"].startswith("HostNotAllowed: ")
    assert [(f.url, f.status) for f in snap.manifest.fetched] == [(page, 405)]
    assert snap.manifest.notes == (f"HTTP 405: {page}",)
    assert families(parse(snap))["Fig Sans"].urls == (("homepage", page),)  # 405 is not gone


def test_fetch_checks_only_the_fetchers_hosts(tmp_path: Path) -> None:
    """A fetcher scoped narrower than ``hosts`` leaves the other URLs unchecked, unrequested."""
    http = write_answers(tmp_path / "http", ANSWERS)
    snap, mock = run_fetch(tmp_path, http, write_list(tmp_path), hosts=("www.fontshare.com",))
    assert mock.hosts() == {"www.fontshare.com"}
    rows = checks(snap)
    assert rows[DUNE_PAGE]["status"] == 403
    unchecked = {url for url, row in rows.items() if row["error"] == NOT_CHECKED_HOST}
    assert unchecked == set(rows) - {DUNE_PAGE}
    assert all(rows[url]["status"] is None for url in unchecked)


def test_list_notes_name_every_repeat() -> None:
    lst = read_list(
        {
            "schema": 1,
            "foundries": {
                fid: {
                    "name": fid,
                    "url": f"https://{fid}.synth.example",
                    "families": [
                        {"name": name, "url": f"https://{fid}.synth.example/{i}", "license": "OFL"}
                        for i, name in enumerate(names)
                    ],
                }
                for fid, names in {
                    "aster": ["Aster Sans", "Aster  Sans", "Birch Mono"],
                    "birch": ["Aster Sans", "Cobalt Serif"],
                }.items()
            },
        }
    )
    assert list_notes(lst) == [
        "listed by several foundries: Aster Sans (aster, birch)",
        "listed more than once by one foundry: Aster Sans (aster)",
    ]
    assert [fid for fid, _ in by_name(lst)["Aster Sans"]] == ["aster", "aster", "birch"]


def test_fetch_without_checks_makes_no_request(tmp_path: Path) -> None:
    settings = Settings(list_file=write_list(tmp_path).list_file, check_urls=False)
    snap, mock = run_fetch(tmp_path, write_answers(tmp_path / "http", []), settings)
    assert mock.requests == []
    assert snap.manifest.fetched == ()
    assert {row["error"] for row in checks(snap).values()} == {NOT_CHECKED_OFF}
    assert snap.manifest.notes[-1] == "URL checks are off (check_urls = false)"
    fams = families(parse(snap))
    assert fams["Birch Mono"].urls == (("repository", BIRCH_REPO),)  # nothing is gone unchecked


# --- parse ------------------------------------------------------------------------------------


def test_parse_gives_one_family_per_name(tmp_path: Path) -> None:
    snap, _ = synth_fetch(tmp_path)
    fams = families(parse(snap))
    assert list(fams) == ["Aster Sans", "Birch Mono", "Cobalt Serif", "Dune Display", "Elm Script"]
    aster = fams["Aster Sans"]
    assert aster.key == SourceKey("foundry-family", "Aster Sans")
    assert (aster.family, aster.status, aster.drop, aster.display_name) == (
        "Aster Sans",
        "live",
        None,
        None,
    )
    assert aster.urls == (
        ("homepage", ASTER_SHOWCASE),
        ("homepage", ASTER_PAGE),
        ("repository", ASTER_REPO),
    )
    assert dict(aster.attrs) == {"foundry": "aster-type,birch-foundry"}


def test_parse_leaves_out_gone_urls_but_keeps_the_family(tmp_path: Path) -> None:
    snap, _ = synth_fetch(tmp_path)
    fams = families(parse(snap))
    birch = fams["Birch Mono"]  # whitespace collapsed; page == repository, and it is gone
    assert (birch.urls, dict(birch.attrs)) == ((), {"foundry": "aster-type", "gone_urls": 1})
    assert birch.status == "live"
    dune = fams["Dune Display"]  # the 403 page stays: refused is not gone
    assert dune.urls == (("homepage", DUNE_PAGE),)
    assert dict(dune.attrs)["gone_urls"] == 1
    # Unanswered and unchecked URLs stay too.
    assert fams["Elm Script"].urls == (("homepage", ELM_PAGE),)
    assert fams["Cobalt Serif"].urls == (("homepage", COBALT_PAGE),)


def test_parse_states_each_license_as_listed(tmp_path: Path) -> None:
    snap, _ = synth_fetch(tmp_path)
    assert licenses(parse(snap)) == {
        "Aster Sans": [
            ("Open Font License", {"foundry": "aster-type"}),
            ("SIL Open Font License v.1.1", {"foundry": "birch-foundry"}),
        ],
        "Birch Mono": [("SIL Open Font License 1.1", {"foundry": "aster-type"})],
        "Cobalt Serif": [("NOASSERTION", {"foundry": "aster-type"})],
        "Dune Display": [("NOASSERTION", {"foundry": "birch-foundry"})],  # an empty string
        "Elm Script": [("OFL", {"foundry": "birch-foundry"})],
    }
    facts = [r for r in parse(snap) if isinstance(r, LicenseFact)]
    assert {(f.spdx, f.text_url, f.rfn) for f in facts} == {(None, None, None)}


def test_parse_follows_the_gone_statuses_setting(tmp_path: Path) -> None:
    snap, _ = synth_fetch(tmp_path)
    fams = families(parse(snap, Settings(gone_statuses=(404, 410, 403))))
    assert fams["Dune Display"].urls == ()
    assert families(parse(snap, Settings(gone_statuses=())))["Birch Mono"].urls == (
        ("repository", BIRCH_REPO),
    )


def test_parse_reads_only_the_snapshot() -> None:
    snapshot = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
    elsewhere = Settings(list_file="/nonexistent/foundries.toml")
    assert parse(snapshot, elsewhere) == parse(snapshot)


def list_only_snapshot(tmp: Path, doc: dict[str, Any]) -> Snapshot:
    """A snapshot holding ``foundries.json`` and no ``checks.jsonl``."""
    store = Store(tmp / "store")
    with store.writer(COLLECTOR.name, DAY, COLLECTOR.version) as writer:
        writer.write_bytes(LIST_EXTRACT, jsonio.pretty_bytes(doc))
    snap = store.snapshot(COLLECTOR.name, DAY)
    assert snap is not None
    return snap


def test_parse_without_checks_keeps_every_url(tmp_path: Path) -> None:
    doc = list_doc(load_list(list_path(write_list(tmp_path))))
    fams = families(parse(list_only_snapshot(tmp_path, doc)))
    assert fams["Birch Mono"].urls == (("repository", BIRCH_REPO),)
    assert fams["Dune Display"].urls == (("homepage", DUNE_PAGE), ("repository", DUNE_REPO))
    assert all("gone_urls" not in dict(f.attrs) for f in fams.values())


def test_parse_reads_the_extract_strictly(tmp_path: Path) -> None:
    """The extract has its own format 1: an unknown key is a corrupt snapshot, not ignored."""
    doc = list_doc(load_list(list_path(write_list(tmp_path))))
    doc["foundries"]["aster-type"]["families"][0]["designer"] = "Someone"
    with pytest.raises(ConfigError, match="unknown key"):
        parse(list_only_snapshot(tmp_path, doc))


# --- the contract fixture and the real list -----------------------------------------------------


def test_contract_fixture_mirrors_its_list() -> None:
    snapshot = regen.load_snapshot(FIXTURE / "snapshot", COLLECTOR.name)
    listed = load_list(list_path(SETTINGS, regen_fixture.FIXTURE_ROOT))
    assert snapshot.load_json(LIST_EXTRACT) == json.loads(
        jsonio.canonical_bytes(list_doc(listed))
    ), REBUILD
    index = json.loads((FIXTURE / "http" / mockhttp.INDEX).read_text(encoding="utf-8"))
    assert sorted(e["url"] for e in index["responses"]) == regen_fixture.checked_urls(), REBUILD


def test_fetch_reads_the_list_under_the_contexts_paths(tmp_path: Path) -> None:
    """A relative ``list_file`` resolves under ``ctx.paths.root``, not the working tree."""
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "foundries.toml").write_text(SYNTH_LIST, encoding="utf-8")
    snap, _ = run_fetch(tmp_path, write_answers(tmp_path / "http", ANSWERS), SETTINGS, root=root)
    assert set(families(parse(snap))) == {
        "Aster Sans",
        "Birch Mono",
        "Cobalt Serif",
        "Dune Display",
        "Elm Script",
    }


def test_real_list_parses_to_one_family_per_name(tmp_path: Path) -> None:
    real = load_list(list_path(SETTINGS, ROOT))
    recs = parse(list_only_snapshot(tmp_path, list_doc(real)))
    names = {" ".join(f.name.split()) for fo in real.foundries.values() for f in fo.families}
    assert set(families(recs)) == names
    assert set(licenses(recs)) == names  # every family states something, NOASSERTION included


@pytest.mark.network
def test_real_family_urls_answer(tmp_path: Path) -> None:
    """HEAD one real family page and its repository from the list, at the project's pace."""
    real = load_list(list_path(SETTINGS, ROOT))
    fam = next(
        f
        for fo in real.foundries.values()
        for f in fo.families
        if f.repository and f.url != f.repository
    )
    text = (
        'schema = 1\n\n[foundries.pick]\nname = "Pick"\nurl = "https://pick.synth.example"\n\n'
        f'[[foundries.pick.families]]\nname = "{fam.name}"\nurl = "{fam.url}"\n'
        f'license = "{fam.license}"\nrepository = "{fam.repository}"\n'
    )
    settings = write_list(tmp_path, text)
    store = Store(tmp_path / "store")
    day = clock.utc_today()
    with (
        Fetcher(log=LOG) as fetcher,
        store.writer(COLLECTOR.name, day, COLLECTOR.version) as writer,
    ):
        COLLECTOR.fetch(
            FetchContext(
                run_date=day,
                fetcher=fetcher.scoped(COLLECTOR.hosts),
                out=writer,
                raw=RawDir(tmp_path / "raw"),
                previous=None,
                settings=settings,
                log=LOG,
            )
        )
    snap = store.snapshot(COLLECTOR.name, day)
    assert snap is not None
    rows = checks(snap)
    assert set(rows) == {fam.url, fam.repository}
    assert {row["status"] for row in rows.values()} == {200}, rows
    assert families(parse(snap))[fam.name].urls == (
        ("homepage", fam.url),
        ("repository", fam.repository),
    )


def test_run_fetch_freezes_the_clock(tmp_path: Path) -> None:
    snap, _ = synth_fetch(tmp_path)
    assert {f.fetched_at for f in snap.manifest.fetched} == {f"{DAY.isoformat()}T06:00:00Z"}


def test_a_family_s_font_files_become_regular_file_refs() -> None:
    # The list names one pinned Regular file per family, so the Latin gate and L3 can read
    # a foundry-only family (owner ruling C3 of 2026-09-26: the list must be able to add one).
    from tff_catalog.collectors.universe.foundries import ListedFamily, family_records
    from tff_catalog.records import FontFileRef

    url = "https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/fonts/Birch-Regular.otf"
    fam = ListedFamily("Birch Mono", "https://example.org/birch", "OFL", "", (url,))
    (record, _) = family_records("Birch Mono", [("birch-foundry", fam)], frozenset())
    assert record.files == (FontFileRef(url=url, role="regular"),)
