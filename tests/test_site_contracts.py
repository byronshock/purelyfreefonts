"""Milestone 2 contracts: schemas, the sample catalog, site/CONTRACT.md, the parts rule and the
shared Caddy snippets. Offline except the one ``network`` test of the pinned font files."""

import copy
import hashlib
import json
import re
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined, nodes
from jsonschema import Draft202012Validator

from tff_catalog.keys import search_key
from tff_site import assets, data, fonts
from tff_site.cli import COMMANDS, main

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
SAMPLE_PATH = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
SPECIMEN_FONTS = ROOT / "tests" / "fixtures" / "specimen-fonts.toml"
SPECIMENS_DIR = SAMPLE_PATH.parent / "specimens"
VECTORS = ROOT / "tests" / "vectors" / "name-keys.json"
CONTRACT = (ROOT / "site" / "CONTRACT.md").read_text(encoding="utf-8")
JS_DIR = ROOT / "site" / "js"
TOKENS_CSS = ROOT / "site" / "css" / "00-tokens.css"
TEMPLATES = ROOT / "site" / "templates"
SITE_CADDY = ROOT / "ops" / "caddy" / "site.caddy"

SAMPLE = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
BAND_1, BAND_2 = (b["label"] for b in SAMPLE["bands"][:2])  # "101-250", "251-500" (en dash)
PINNED = tomllib.loads(SPECIMEN_FONTS.read_text(encoding="utf-8"))["font"]


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def table_after(text: str, marker: str) -> list[list[str]]:
    """Rows (cells without backticks) of the first Markdown table after the line ``marker``."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(marker))
    rows: list[list[str]] = []
    for line in lines[start:]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip().strip("`") for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if set(cells[0]) <= {"-"}:
            continue
        rows.append(cells)
    return rows[1:]  # drop the header row


def section(text: str, heading: str) -> str:
    """The text of the ``## <heading>`` section."""
    start = text.index(f"\n## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


# ---------------------------------------------------------------- schemas and the sample


@pytest.mark.parametrize("name", ["catalog-site.schema.json", "name-keys.schema.json"])
def test_schema_is_valid_2020_12(name):
    schema = load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_catalog_site_schema_version_matches_code():
    schema = load_schema("catalog-site.schema.json")
    assert schema["$id"].endswith(f"/{data.SCHEMA_VERSION}")
    assert schema["properties"]["schema_version"]["const"] == data.SCHEMA_VERSION
    assert schema["$defs"]["rank_key"]["enum"] == list(data.RANK_KEYS)
    assert not list(_keyword_uses(schema, "format")), "use pattern ^https:// instead of format"


def _keyword_uses(node: Any, keyword: str, under_properties: bool = False):
    """Yield every schema object that uses ``keyword`` (property names don't count)."""
    if isinstance(node, dict):
        if keyword in node and not under_properties:
            yield node
        for key, value in node.items():
            if key in ("properties", "$defs"):
                for child in value.values():
                    yield from _keyword_uses(child, keyword)
            elif isinstance(value, (dict, list)):
                yield from _keyword_uses(value, keyword)
    elif isinstance(node, list):
        for item in node:
            yield from _keyword_uses(item, keyword)


def test_sample_validates():
    assert data.schema_errors(SAMPLE) == []
    assert data.semantic_errors(SAMPLE) == []
    assert data.validate(SAMPLE) == data.Validated(version="1.0.0-draft", fonts=40)


def test_name_key_vectors_validate():
    doc = json.loads(VECTORS.read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(load_schema("name-keys.schema.json")).iter_errors(doc))
    assert [e.message for e in errors] == []


def test_sample_is_labelled_synthetic():
    assert SAMPLE["synthetic"] is True
    for font in SAMPLE["fonts"]:
        assert font["id"].startswith("sample-")
        assert font["family"].startswith("Sample ")
        assert all(a["name"].startswith(("Sample", "SMono")) for a in font["aliases"])


def test_sample_covers_the_step_2_cases():
    """Milestone 2 step 2's list, plus the edge cases the design adds."""
    fonts_ = SAMPLE["fonts"]
    entries = [e for f in fonts_ for e in f["ranks"].values()]
    system_os = {s["id"]: s["os"] for s in SAMPLE["systems"]}
    assert len(fonts_) == 40
    # ranks and bands, including 501+, and the edges of the exact top 100
    assert {e["band"] for e in entries} - {None} == {b["label"] for b in SAMPLE["bands"]}
    assert {100, 101, 250, 251, 500} <= {e["order"] for e in entries}
    assert {e["tier"] for e in entries} - {None} == {"A", "B", "C"}
    # every unranked reason; a font unranked even in Overall; gate held; too new
    assert {e["unranked"] for e in entries} - {None} == set(data.UNRANKED_LABELS)
    assert any(f["ranks"]["overall"]["unranked"] for f in fonts_)
    assert any(e["gate_held"] for e in entries)
    assert any("too_new" in f["flags"] for f in fonts_)
    # licenses: every font redistributable (Rule 3 of 2026-09-30), attribution required,
    # every class used
    assert all(f["license"]["redistributable"] for f in fonts_)
    assert any(f["license"]["attribution_required"] for f in fonts_)
    assert {f["license"]["class"] for f in fonts_} == {c["id"] for c in SAMPLE["license_classes"]}
    # limited accents; every category; each system's preinstalls; a package dependency
    assert any(f["latin"]["coverage"] == "basic" for f in fonts_)
    assert {f["category"] for f in fonts_} == {
        "sans-serif",
        "serif",
        "display",
        "handwriting",
        "monospace",
    }
    # a monospaced font the catalog files elsewhere: the site lists it under Monospace
    assert any(f["is_monospace"] and f["category"] != "monospace" for f in fonts_)
    pre = {p["system"] for f in fonts_ for p in f["preinstalled_on"]}
    pulled = {p["system"] for f in fonts_ for p in f["pulled_in_by"]}
    assert {system_os[s] for s in pre} == {"windows", "macos", "linux", "android"}
    assert pre | pulled == set(system_os)
    assert any(s["abstains_in"] for f in fonts_ if f["pulled_in_by"] for s in f["sources"].values())
    # step 3: unranked in most chosen, explained by a preinstalled_on tag, and by a pulled_in_by tag
    no_deliberate = [
        f for f in fonts_ if f["ranks"]["desktop_chosen"]["unranked"] == "no_deliberate_evidence"
    ]
    assert any(f["preinstalled_on"] for f in no_deliberate)
    assert any(f["pulled_in_by"] and not f["preinstalled_on"] for f in no_deliberate)
    # previews: none allowed by the license, render failed, name only
    assert any(not f["preview_ok"] for f in fonts_)
    assert {fl for f in fonts_ for fl in f["flags"]} >= data.SPECIMEN_FLAGS
    # a very long name
    assert max(len(f["family"]) for f in fonts_) >= 60
    # every source state and reason; a stale source; a source whose ranks aren't published
    states = [s for f in fonts_ for s in f["sources"].values()]
    assert {s["state"] for s in states} == set(data.STATE_LABELS)
    schema_reasons = load_schema("catalog-site.schema.json")["$defs"]["source_entry"]
    assert {s["reason"] for s in states} == set(schema_reasons["properties"]["reason"]["enum"])
    assert any(s["stale"] for s in SAMPLE["sources"])
    assert any(not s["publish_rank"] for s in SAMPLE["sources"])
    # Rising is listed but not available yet, and no font is ranked in it
    views = {v["key"]: v for v in SAMPLE["views"]}
    assert views["rising"]["available"] is False
    assert not any("rising" in f["ranks"] for f in fonts_)
    # Nerd Font builds (TASK-2): a monospace font or two with one, of both kinds, null elsewhere
    nerd = {f["id"]: f["links"]["nerd"] for f in fonts_ if f["links"]["nerd"] is not None}
    assert 1 <= len(nerd) < len(fonts_)
    assert all(f["is_monospace"] for f in fonts_ if f["id"] in nerd)
    folders = [n for n in nerd.values() if "/ryanoasis/nerd-fonts/tree/" in n["url"]]
    assert folders, "a Nerd Fonts project build"
    assert len(folders) < len(nerd), "a maker's own build"


def test_sample_has_a_font_found_only_by_alias():
    family_keys = [search_key(f["family"]) for f in SAMPLE["fonts"]]
    alias_only = [
        (f["id"], a["name"])
        for f in SAMPLE["fonts"]
        for a in f["aliases"]
        if a["relation"] == "rename" and not any(search_key(a["name"]) in k for k in family_keys)
    ]
    assert alias_only, "no alias that only the alias search can find"


def test_sample_names_exercise_the_keys():
    keys = {search_key(f["family"]) for f in SAMPLE["fonts"]}
    assert "samplełodzsans37" in keys  # ó and ź lose their accents; ł has no decomposition
    assert "samplestrasseserif27" in keys  # ß folds to ss


def test_sample_real_font_files_match_the_pinned_list():
    by_url = {p["url"]: p for p in PINNED}
    with_files = [f for f in SAMPLE["fonts"] if f["font_file"]]
    assert sorted(f["id"] for f in with_files) == sorted(p["sample_id"] for p in PINNED)
    for font in with_files:
        pinned = by_url[font["font_file"]["url"]]
        assert pinned["sample_id"] == font["id"]
        assert font["font_file"] == {k: pinned[k] for k in ("url", "sha256", "size", "format")}
        assert font["preview_ok"]
        assert font["preview"] is not None
        assert font["license"]["spdx"] == pinned["license"] == "OFL-1.1"
        assert font["license"]["text_url"] == pinned["license_url"]
        assert (font["latin"]["coverage"] == "basic") == (pinned["latin"] == "basic")


def test_pinned_font_list_shape():
    for font in PINNED:
        assert re.fullmatch(
            rf"https://raw\.githubusercontent\.com/{re.escape(font['repo'])}/"
            rf"{font['commit']}/[A-Za-z0-9%._/-]+",
            font["url"],
        )
        assert font["url"].endswith(
            font["path"].replace("[", "%5B").replace("]", "%5D").replace(",", "%2C")
        )
        assert re.fullmatch(r"[0-9a-f]{64}", font["sha256"])
        assert re.fullmatch(r"[0-9a-f]{40}", font["commit"])
        assert font["format"] == font["path"].rsplit(".", 1)[1].lower()
    assert len({p["sha256"] for p in PINNED}) == len(PINNED) == 5


def test_sample_previews_match_specimens_or_are_placeholders():
    """Until the specimen stage commits tests/fixtures/specimens/, previews are placeholders."""
    for font in SAMPLE["fonts"]:
        if font["preview"] is None:
            continue
        svg = SAMPLE_PATH.parent / font["preview"]["path"]
        if svg.is_file():
            assert font["preview"]["sha256"] == fonts.file_sha256(svg), font["id"]
        else:
            assert font["preview"]["sha256"] == data.PLACEHOLDER_SHA256, font["id"]


def _font(doc: dict, font_id: str) -> dict:
    return next(f for f in doc["fonts"] if f["id"] == font_id)


def _first(doc: dict, pred) -> Any:
    return next((f, k, e) for f in doc["fonts"] for k, e in f["ranks"].items() if pred(e))


def _source(doc: dict, pred) -> dict:
    return next(s for f in doc["fonts"] for s in f["sources"].values() if pred(s))


def _set(path: list, value: Any):
    def mutate(doc: dict) -> None:
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


SCHEMA_BREAKS = {
    "unknown top-level key": _set(["extra"], 1),
    "http url": _set(["fonts", 0, "links", "primary", "url"], "http://example.com/"),
    "rank over 100": _set(["fonts", 0, "ranks", "overall", "rank"], 101),
    "rank with a band": _set(["fonts", 0, "ranks", "overall", "band"], BAND_1),
    "unranked with an order": lambda d: _first(d, lambda e: e["unranked"])[2].update(order=5),
    "gate held inside the top 100": _set(["fonts", 0, "ranks", "overall", "gate_held"], True),
    "observed with a reason": _set(["fonts", 0, "sources", "homebrew", "reason"], "below_floor"),
    "censored with a rank": lambda d: _source(d, lambda s: s["state"] == "censored").update(
        rank_in_source=3
    ),
    "not covered but abstaining": lambda d: _source(
        d, lambda s: s["state"] == "not_covered"
    ).update(abstains_in=["overall"]),
    "font file without preview_ok": lambda d: _font(d, "sample-sans-17").update(
        font_file=copy.deepcopy(_font(d, "sample-sans-01")["font_file"])
    ),
    "preview of a non-redistributable font": _set(
        ["fonts", 0, "license", "redistributable"], False
    ),
    "attribution missing": lambda d: _font(d, "sample-hand-16")["license"].update(attribution=None),
    "coding rank for a proportional font": lambda d: d["fonts"][0]["ranks"].update(
        coding=copy.deepcopy(d["fonts"][0]["ranks"]["overall"])
    ),
    "short sha256": _set(["fonts", 0, "font_file", "sha256"], "abc123"),
    "unknown flag": lambda d: d["fonts"][0]["flags"].append("held_back"),
    "neither static nor variable": _set(
        ["fonts", 0, "formats"], {"variable": False, "static": False}
    ),
    "unknown rank key": lambda d: d["fonts"][0]["ranks"].update(
        weekly=copy.deepcopy(d["fonts"][0]["ranks"]["overall"])
    ),
    "family with a newline": _set(["fonts", 0, "family"], "Sample\nSans"),
    "family with a trailing newline": _set(["fonts", 0, "family"], "Sample Sans\n"),
    "id with a trailing newline": _set(["fonts", 0, "id"], "sample-sans-01\n"),
    "url with a trailing newline": _set(["data_license", "url"], "https://example.com/\n"),
    "provisional data license (ruling T5)": _set(["data_license", "provisional"], True),
    "a view with the old desktop flag": lambda d: d["views"][0].update(desktop=True),
    "links without nerd": lambda d: d["fonts"][0]["links"].pop("nerd"),
    "a nerd link without its label": lambda d: _font(d, "sample-mono-02")["links"]["nerd"].pop(
        "label"
    ),
    "a nerd link with a note": lambda d: _font(d, "sample-mono-02")["links"]["nerd"].update(
        note="x"
    ),
    "no nerd wording": lambda d: d.pop("nerd"),
    "nerd wording without its legend": lambda d: d["nerd"].pop("legend"),
}


@pytest.mark.parametrize("mutate", SCHEMA_BREAKS.values(), ids=SCHEMA_BREAKS.keys())
def test_schema_rejects(mutate):
    doc = copy.deepcopy(SAMPLE)
    mutate(doc)
    assert data.schema_errors(doc) != []


SEMANTIC_BREAKS = {
    "duplicate font id": (_set(["fonts", 1, "id"], "sample-sans-01"), "listed twice"),
    "unknown license class": (
        _set(["fonts", 0, "license", "class"], "gpl"),
        "isn't in license_classes",
    ),
    "unknown system": (
        _set(["fonts", 0, "preinstalled_on"], [{"system": "beos"}]),
        "unknown system",
    ),
    "dependency on a non-Linux system": (
        _set(["fonts", 0, "pulled_in_by"], [{"system": "macos", "package": "x"}]),
        "not a Linux system",
    ),
    "rank differs from order": (_set(["fonts", 0, "ranks", "overall", "order"], 2), "!= order"),
    "band that doesn't hold the order": (
        lambda d: _first(d, lambda e: e["band"] == BAND_1)[2].update(band=BAND_2),
        "doesn't hold order",
    ),
    "order used twice": (
        lambda d: d["fonts"][1]["ranks"]["overall"].update(rank=1, order=1),
        "used by 2 fonts",
    ),
    "rank shown for an unpublished source": (
        lambda d: next(
            f["sources"]["jsdelivr"]
            for f in d["fonts"]
            if f["sources"]["jsdelivr"]["state"] == "observed"
        ).update(rank_in_source=7),
        "unpublished source",
    ),
    "abstaining in most installed": (
        _set(["fonts", 0, "sources", "arch", "abstains_in"], ["desktop_installed"]),
        "abstains in desktop_installed",
    ),
    "project source abstaining": (
        _set(["fonts", 0, "sources", "fot", "abstains_in"], ["overall"]),
        "only desktop sources abstain",
    ),
    "missing source entry": (lambda d: d["fonts"][0]["sources"].pop("fot"), "missing ['fot']"),
    "preview path of another font": (
        _set(["fonts", 0, "preview", "path"], "specimens/sample-sans-03.svg"),
        "preview.path",
    ),
    "font missing from an available view": (
        lambda d: d["fonts"][0]["ranks"].pop("project"),
        "ranks.project missing",
    ),
    "monospace font missing from coding": (
        lambda d: _font(d, "sample-mono-02")["ranks"].pop("coding"),
        "ranks.coding missing",
    ),
    "gap between bands": (_set(["bands", 1, "from"], 260), "doesn't follow"),
    "closed last band": (_set(["bands", 2, "to"], 900), "open-ended"),
    "view missing": (lambda d: d["views"].pop(), "'rising' missing"),
    "failed specimen with a preview": (
        lambda d: d["fonts"][0]["flags"].append("specimen_failed"),
        "failed specimen",
    ),
    "too new without the flag": (
        lambda d: _font(d, "sample-sans-29").update(flags=[]),
        "too_new flag",
    ),
    "license link into a release archive": (
        _set(["fonts", 0, "license", "text_url"], "https://example.com/Font-1.0.zip#Font/OFL.txt"),
        "license.text_url: https://example.com/Font-1.0.zip#Font/OFL.txt is a download",
    ),
    "download link to a font file": (
        _set(["fonts", 0, "links", "primary"], {"url": "https://example.com/Font.woff2"}),
        "links.primary: https://example.com/Font.woff2 is a download",
    ),
}


@pytest.mark.parametrize(
    ("mutate", "message"), SEMANTIC_BREAKS.values(), ids=SEMANTIC_BREAKS.keys()
)
def test_semantic_checks_reject(mutate, message):
    doc = copy.deepcopy(SAMPLE)
    mutate(doc)
    assert data.schema_errors(doc) == []
    errors = data.semantic_errors(doc)
    assert any(message in e for e in errors), errors


SITE_RULINGS = tomllib.loads(
    (ROOT / "data" / "reviews" / "site" / "2026-09-25.toml").read_text(encoding="utf-8")
)


def test_sample_wording_matches_config_site_toml():
    """export-site copies its wording from config/site.toml; the sample must match it."""
    config = ROOT / "config"
    site = tomllib.loads((config / "site.toml").read_text(encoding="utf-8"))
    display = tomllib.loads((config / "ranking.toml").read_text(encoding="utf-8"))["display"]
    assert SAMPLE["data_license"] == site["data_license"]
    assert [{k: v[k] for k in ("key", "label", "measures")} for v in SAMPLE["views"]] == site[
        "views"
    ]
    assert SAMPLE["tiers"] == site["tiers"]
    assert SAMPLE["license_classes"] == site["license_classes"]
    assert SAMPLE["nerd"] == site["nerd"]
    for source in SAMPLE["sources"]:  # publish_rank stays synthetic: jsdelivr's is hidden here
        credit = site["sources"][source["id"]]
        assert {k: source[k] for k in ("name", "measures", "url", "license")} == {
            k: credit[k] for k in ("name", "measures", "url", "license")
        }
    for system in SAMPLE["systems"]:
        if system["id"] in site["package_systems"]:
            assert system["label"] == site["package_systems"][system["id"]]["label"]
            assert system["os"] == "linux"
    bands = [f"{lo}\u2013{hi}" for lo, hi in display["bands"]] + [f"{display['open_band_from']}+"]
    assert [b["label"] for b in SAMPLE["bands"]] == bands


def test_project_rank_label_follows_the_site_ruling():
    views = {v["key"]: v for v in SAMPLE["views"]}
    assert views["project"]["label"] == SITE_RULINGS["project_rank_label"]["value"]


RULINGS_0930 = tomllib.loads(
    (ROOT / "data" / "reviews" / "site" / "2026-09-30.toml").read_text(encoding="utf-8")
)


def test_filters_follow_the_site_rulings_of_2026_09_30():
    """monospace_category, license_filter and filters_layout: no Spacing, license-group or
    "Redistributable fonts only" filter; Monospace is a category; "No credit required" only
    while some font needs credit; one select of systems; Sort beside the count."""
    assert {"monospace_category", "license_filter", "filters_layout"} <= set(RULINGS_0930)
    filters = (TEMPLATES / "_filters.html.j2").read_text(encoding="utf-8")
    listing = (TEMPLATES / "_list.html.j2").read_text(encoding="utf-8")
    dom = section(CONTRACT, "4. DOM")
    for text in (filters, dom):
        for gone in ('name="spacing"', 'name="lic"', 'name="redist"', 'id="f-sort-rank"'):
            assert gone not in text, gone
    assert '<fieldset id="f-cat" class="pills">' in filters
    assert "{% if list.credit_filter %}" in filters
    assert 'name="hide" value="attr"> No credit required</label>' in filters
    assert '<select id="f-os" name="hide">' in filters
    # sort_header (the same day): buttons over the list replace the Sort select
    assert '<div id="list-sort" class="list-sort" role="group"' in listing
    assert 'id="f-sort"' not in listing
    assert 'id="f-sort"' not in dom
    assert 'id="sort-rank" data-sort="rank"' in dom
    assert 'id="sort-name" data-sort="name"' in dom
    assert "sort_header" in RULINGS_0930
    for gone in ('id="f-mono"', 'id="f-text"', 'name="mono"', 'name="text"', "desktop: true"):
        assert gone not in CONTRACT, gone
    assert (
        "desktop"
        not in load_schema("catalog-site.schema.json")["properties"]["views"]["items"]["properties"]
    )


NERD_RULINGS = tomllib.loads(
    (ROOT / "data" / "reviews" / "site" / "2026-09-29.toml").read_text(encoding="utf-8")
)


def test_the_nerd_marker_follows_the_site_rulings():
    """The owner's rulings of 2026-09-29 (TASK-2): the "NF" marker, the legend word for word,
    and a "Nerd Font available" filter, in the config, the template and the contract."""
    site = tomllib.loads((ROOT / "config" / "site.toml").read_text(encoding="utf-8"))["nerd"]
    assert site["legend"] == NERD_RULINGS["nerd_legend"]["ruling"]
    assert site["marker"] == "NF"
    assert f'"{site["marker"]}"' in NERD_RULINGS["nerd_marker"]["ruling"]
    assert site["label"] in site["legend"]
    label = re.search(r'"([^"]+)" option', NERD_RULINGS["nerd_filter"]["ruling"]).group(1)
    filters = (TEMPLATES / "_filters.html.j2").read_text(encoding="utf-8")
    assert f'id="f-nerd" name="nerd" value="1" aria-describedby="nf-legend"> {label}</label>' in (
        filters
    )
    dom = section(CONTRACT, "4. DOM")
    assert 'id="f-nerd" name="nerd"' in dom
    assert 'id="nf-legend"' in dom
    assert '<span class="nf-mark" role="img"' in dom
    assert "8192 a Nerd Font build" in section(CONTRACT, "7. List index JSON")


def test_hash_grammar_matches_the_hash_table():
    hash_section = section(CONTRACT, "9. URL hash")
    grammar = re.search(r"^key   = (.*)$", hash_section, re.MULTILINE).group(1)
    keys = re.findall(r'"(\w+)"', grammar)
    table = [row[0] for row in table_after(hash_section, "| Key | Value |")]
    assert keys == table
    assert keys.index("nerd") == keys.index("var") + 1  # "Nerd Font available", after var
    assert not {"mono", "text", "spacing", "lic", "redist"} & set(keys)
    assert "Extension keys" in hash_section  # Milestone 3's keys survive State (section 9)
    retired = re.search(r"^- \*\*Retired keys\*\*.*$", hash_section, re.MULTILINE).group(0)
    for key in ("spacing", "lic", "redist"):  # the owner rulings of 2026-09-30
        assert f"`{key}`" in retired, key


def test_pulled_in_by_reaches_the_details_panel():
    """Milestone 2 step 3: the pulled_in_by tag explains "no evidence of deliberate installs".
    Since 2026-09-30 (filters_layout) it is in the details panel, not a row badge."""
    assert "`pulled` (" not in section(CONTRACT, "3. Templates")
    assert '"Pulled in by"' in section(CONTRACT, "4. DOM")
    assert "'Pulled in by'" in (ROOT / "site" / "js" / "40-details.js").read_text(encoding="utf-8")
    assert "4096 pulled in by a package" in section(CONTRACT, "7. List index JSON")


# ---------------------------------------------------------------- the command line


def test_validate_command_accepts_the_sample(capsys):
    assert main(["validate", str(SAMPLE_PATH)]) == 0
    assert capsys.readouterr().out == "valid (1.0.0-draft), 40 fonts\n"


def test_validate_command_rejects_a_broken_file(tmp_path, capsys):
    doc = copy.deepcopy(SAMPLE)
    doc["fonts"][0]["ranks"]["overall"]["rank"] = 101
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    assert "$.fonts[0].ranks.overall" in capsys.readouterr().err


def test_validate_command_rejects_a_non_json_file(tmp_path, capsys):
    path = tmp_path / "not.json"
    path.write_text("{", encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    assert "invalid" in capsys.readouterr().err


def test_a_failed_build_prints_its_problems_not_a_traceback(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    assert main(["build", "--data", str(missing), "--out", str(tmp_path / "site")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("tff-site build: failed\n")
    assert str(missing) in err


def test_help_lists_every_command(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in COMMANDS:
        assert f"    {command} " in out


@pytest.mark.parametrize("command", COMMANDS)
def test_each_command_has_help(command, capsys):
    assert main([command, "--help"]) == 0
    assert capsys.readouterr().out.startswith(f"usage: tff-site {command}")


# ---------------------------------------------------------------- JS parts


def lint_part(filename: str, const: str, source: str) -> list[str]:
    """The parts rule (site/CONTRACT.md section 5): the build's own lint, so a part that
    passes this test also passes the build. ``const`` must be the table's name for it."""
    assert dict(assets.JS_PARTS).get(filename) == const, (filename, const)
    return assets.lint_js_part(filename, source)


JS_TABLE = table_after(section(CONTRACT, "5. JS parts"), "| File | Const |")


def test_js_parts_table_matches_the_build():
    assert [(row[0], row[1]) for row in JS_TABLE] == list(assets.JS_PARTS)


@pytest.mark.parametrize("row", JS_TABLE, ids=[row[0] for row in JS_TABLE])
def test_js_part_exists_or_is_pending(row):
    filename, _const, _owner, status, _what = row
    exists = (JS_DIR / filename).is_file()
    assert exists or status == "to be written in wave 1", filename
    assert (status == "written") == exists, f"{filename}: status says {status!r}"


def test_no_js_file_outside_the_table():
    assert {p.name for p in JS_DIR.glob("*.js")} <= {name for name, _ in assets.JS_PARTS}


@pytest.mark.parametrize(
    ("filename", "const"),
    [(n, c) for n, c in assets.JS_PARTS if (JS_DIR / n).is_file()],
)
def test_js_part_follows_the_rule(filename, const):
    assert lint_part(filename, const, (JS_DIR / filename).read_text(encoding="utf-8")) == []


# One example per refusal in site/CONTRACT.md sections 5 and 6.
BAD_JS = [
    ("00-core.js", "const Core = 1;\nconst Extra = 2;\n"),
    ("00-core.js", "const Core = 1;\nfunction helper() {}\n"),
    ("00-core.js", "const Core = 1;\ndocument.title = 'x';\n"),
    ("00-core.js", "import x from './x.js';\nconst Core = 1;\n"),
    ("00-core.js", "const Core = (() => {\n  import('./x.js');\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  node.innerHTML = s;\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  fetch('https://example.com/x');\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  localStorage.setItem('a', 1);\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  return 1;\n})(); alert(1);\n"),
    ("00-core.js", "const Core = (() => {\n  navigator.sendBeacon('/x', d);\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  new XMLHttpRequest();\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  new WebSocket('/x');\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  new EventSource('/x');\n})();\n"),
    ("00-core.js", "const Core = (() => {\n  n.setAttribute('style', s);\n})();\n"),
    ("90-main.js", "const Main = 1;\n"),
    ("90-main.js", "const Main = 1;\nMain.start();\nMain.start();\n"),
    ("90-main.js", "Main.start();\nconst Main = 1;\n"),
]
BAD_CSS = [
    "@import url(other.css);\n",
    "@font-face { font-family: X; src: url(/x.woff2); }\n",
    "a { background: url(https://example.com/x.png); }\n",
    "a { background: url(//example.com/x.png); }\n",
    "a { background: url(data:image/png;base64,AAAA); }\n",
]


@pytest.mark.parametrize(("filename", "source"), BAD_JS)
def test_the_parts_lint_catches(filename, source):
    assert lint_part(filename, dict(assets.JS_PARTS)[filename], source) != []


@pytest.mark.parametrize("name", assets.CSS_PARTS)
def test_css_part_follows_the_rule(name):
    path = ROOT / "site" / "css" / name
    if path.is_file():
        assert assets.lint_css_part(name, path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("source", BAD_CSS)
def test_the_css_lint_catches(source):
    assert assets.lint_css_part("10-base.css", source) != []


# ---------------------------------------------------------------- CSS parts and tokens


def css_rules(css: str, media: str = "") -> list[tuple[str, str, dict[str, str]]]:
    """(media, selector, declarations) for each rule, one level of @media deep."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    rules, i = [], 0
    while (open_ := css.find("{", i)) != -1:
        prelude = css[i:open_].strip()
        depth, j = 1, open_ + 1
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[open_ + 1 : j - 1]
        if prelude.startswith("@media"):
            rules += css_rules(body, prelude)
        else:
            pairs = (d.partition(":") for d in body.split(";"))
            decls = {k.strip(): v.strip() for k, _, v in pairs if k.strip()}
            rules.append((media, prelude, decls))
        i = j
    return rules


RULES = css_rules(TOKENS_CSS.read_text(encoding="utf-8"))
LIGHT = next(d for m, s, d in RULES if not m and s == ":root")
DARK = next(d for m, s, d in RULES if "prefers-color-scheme: dark" in m)
FORCED = next(d for m, s, d in RULES if "forced-colors: active" in m)
TOKEN_TABLE = [row[0] for row in table_after(CONTRACT, "**Tokens**")]


def test_css_parts_are_named_in_the_contract():
    for name in assets.CSS_PARTS:
        assert f"`{name}`" in CONTRACT, name


def test_tokens_file_only_touches_root():
    assert {s for _, s, _ in RULES} == {":root"}


def test_every_contract_token_is_defined_and_nothing_else():
    defined = {k for k in LIGHT if k.startswith("--")}
    assert set(TOKEN_TABLE) == defined


def test_colour_tokens_have_dark_and_forced_values():
    colours = {t for t in TOKEN_TABLE if t.startswith("--c-")}
    assert colours <= set(DARK) | {"--c-spec"}
    assert colours <= set(FORCED)
    assert set(DARK) <= colours
    assert set(FORCED) <= colours


def _luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


TEXT_ON = ["--c-bg", "--c-surface", "--c-badge-bg"]
CONTRAST_PAIRS = (
    [
        (fg, bg, 4.5)
        for fg in ("--c-fg", "--c-muted", "--c-link", "--c-link-visited")
        for bg in TEXT_ON
    ]
    + [(ui, bg, 3.0) for ui in ("--c-border", "--c-focus") for bg in TEXT_ON]
    + [
        ("--c-accent-fg", "--c-accent", 4.5),
        ("--c-badge-fg", "--c-badge-bg", 4.5),
        ("--c-warn-fg", "--c-warn-bg", 4.5),
    ]
    # The header, white in both themes (owner ruling of 2026-09-29).
    + [
        (fg, "--c-header-bg", 4.5)
        for fg in ("--c-header-fg", "--c-header-muted", "--c-header-link")
    ]
    + [("--c-header-focus", "--c-header-bg", 3.0), ("--c-header-fg", "--c-wordmark-bg", 4.5)]
)


def test_the_header_is_white_in_both_themes():
    for values in (LIGHT, {**LIGHT, **DARK}):
        assert values["--c-header-bg"] == values["--c-wordmark-bg"] == "#ffffff"
        assert all(values[f"--c-header-{k}"] == LIGHT[f"--c-{k}"] for k in ("fg", "muted", "link"))


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(("fg", "bg", "minimum"), CONTRAST_PAIRS)
def test_token_contrast(theme, fg, bg, minimum):
    values = LIGHT if theme == "light" else {**LIGHT, **DARK}
    assert _contrast(values[fg], values[bg]) >= minimum, (values[fg], values[bg])


def test_interface_uses_arimo_then_fonts_with_its_metrics():
    """AUTHORITY.md, "Interface font": Arimo, which the build declares ahead of the parts,
    then Liberation Sans and Arial, which share its metrics."""
    assert LIGHT["--font-ui"].startswith('"Arimo", "Liberation Sans", Arial, ')
    assert "@font-face" not in TOKENS_CSS.read_text(encoding="utf-8")


# ---------------------------------------------------------------- templates


def environment(extra: dict[str, str] | None = None) -> Environment:
    return Environment(
        loader=ChoiceLoader([DictLoader(extra or {}), FileSystemLoader(TEMPLATES)]),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


BLOCK_TABLE = [row[0] for row in table_after(CONTRACT, "**Blocks in `base.html.j2`:**")]


def test_base_template_blocks_match_the_contract():
    env = environment()
    source = (TEMPLATES / "base.html.j2").read_text(encoding="utf-8")
    blocks = [b.name for b in env.parse(source).find_all(nodes.Block)]
    assert sorted(blocks) == sorted(BLOCK_TABLE)
    assert len(blocks) == len(set(blocks))


CONTEXT = {
    "site": {
        "name": "Truly Free Fonts",
        "base_url": "https://trulyfreefonts.com",
        "repo_url": "https://github.com/byronshock/trulyfreefonts",
        "feedback": {
            "issues_url": "https://github.com/byronshock/trulyfreefonts/issues/new/choose",
            "email": "admin@trulyfreefonts.com",
            "mailto": "mailto:admin@trulyfreefonts.com?subject=trulyfreefonts.com",
        },
        "tip_url": None,
        "blog": None,
    },
    "page": {
        "path": "/privacy/",
        "title": "Privacy <test>",
        "description": "No cookies & no tracking.",
        "canonical": True,
    },
    "assets": {
        "css": "/assets/style.0123456789.css",
        "js": "/assets/app.0123456789.js",
        "font": "/assets/ui/arimo.0123456789.woff2",
    },
    "build": {"commit": "0" * 40, "run_date": "2026-09-25"},
}


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def render(context: dict) -> str:
    child = '{% extends "base.html.j2" %}{% block main %}<h1>Heading</h1>{% endblock %}'
    return environment({"child.html.j2": child}).get_template("child.html.j2").render(context)


def parse(html: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = Tags()
    parser.feed(html)
    return parser.tags


def test_base_template_renders_the_shell():
    html = render(CONTEXT)
    tags = parse(html)
    names = [t for t, _ in tags]
    assert tags[0] == ("html", {"lang": "en"})
    body = names.index("body")
    assert tags[body + 1] == ("a", {"class": "skip-link", "href": "#main"})
    assert ("main", {"id": "main", "tabindex": "-1"}) in tags
    assert names.count("h1") == 1
    assert {"header", "nav", "main", "footer"} <= set(names)
    assert ("div", {"class": "feedback", "id": "feedback"}) in tags
    assert ("link", {"rel": "canonical", "href": "https://trulyfreefonts.com/privacy/"}) in tags
    assert ("a", {"href": "/privacy/", "aria-current": "page"}) in tags
    assert "Privacy &lt;test&gt;" in html  # autoescaped
    assert 'id="tip"' not in html


def test_base_template_shows_the_tip_link_only_when_set():
    url = "https://buy.stripe.com/test_link"
    html = render({**CONTEXT, "site": {**CONTEXT["site"], "tip_url": url}})
    assert ("a", {"href": url}) in parse(html)
    assert 'id="tip"' in html


def test_base_template_has_no_inline_code():
    tags = parse(render(CONTEXT))
    for tag, attrs in tags:
        assert tag not in {"style", "form"}, tag
        assert "style" not in attrs, tag
        assert not [a for a in attrs if a.startswith("on")], tag
        if tag == "script":
            assert attrs.get("src"), attrs
            assert attrs.get("type") == "module", attrs


def test_canonical_link_is_left_out_when_asked():
    html = render({**CONTEXT, "page": {**CONTEXT["page"], "canonical": False}})
    assert 'rel="canonical"' not in html


# ---------------------------------------------------------------- Caddy


def test_site_caddy_csp_matches_milestone_2_step_9():
    caddy = SITE_CADDY.read_text(encoding="utf-8")
    milestone = (ROOT / "docs" / "milestone-2.md").read_text(encoding="utf-8")
    want = re.search(r"a CSP with no inline code: `([^`]+)`", milestone).group(1)
    got = re.search(r'Content-Security-Policy "([^"]+)"', caddy).group(1)
    assert got == want
    assert "local-fonts=()" in caddy
    assert 'Cache-Control "no-cache, no-transform"' in caddy
    # Deferred, or Caddy's encode skips every no-transform response and HTML goes uncompressed.
    assert re.search(
        r'^\theader @revalidate >Cache-Control "no-cache, no-transform"$', caddy, re.MULTILINE
    )
    assert 'Cache-Control "public, max-age=31536000, immutable"' in caddy
    assert re.search(r"^\(tff_security\) \{$", caddy, re.MULTILINE)
    assert re.search(r"^\(tff_site\) \{$", caddy, re.MULTILINE)


# ---------------------------------------------------------------- the pinned font files


@pytest.mark.network
@pytest.mark.parametrize("font", PINNED, ids=[p["key"] for p in PINNED])
def test_pinned_font_file_downloads_and_matches(font):
    """Fills ~/.cache/tff/fonts like `tff-site fetch-fonts` will; skips when offline."""
    import httpx

    path = fonts.cache_path(font["sha256"])
    if not path.is_file():
        try:
            response = httpx.get(font["url"], follow_redirects=True, timeout=60)
        except httpx.TransportError as exc:
            pytest.skip(f"offline and not cached: {exc}")
        response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest() == font["sha256"]
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(response.content)
        partial.replace(path)
    assert fonts.file_sha256(path) == font["sha256"]
    assert path.stat().st_size == font["size"]
