"""``tff_site.views``: the views' wording comes from config/site.toml (owner's site ruling of
2026-10-07, ``view_labels_short``), over the catalog's copies until the first live refresh.

Offline, no browser. The build's use of it is in test_build.py
(``test_the_page_shows_config_s_view_wording`` and the mismatch tests), and the list page's
rank select in test_list_markup.py.
"""

import copy
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tff_site import data, views

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "site.toml"
COMMITTED = ROOT / "build" / "catalog-site.json"
SAMPLE = data.load(ROOT / "tests" / "fixtures" / "catalog-site.sample.json")

# The ruling, word for word (view_labels_short, data/reviews/site/2026-10-07.toml).
RULED = {
    "overall": "Overall",
    "desktop_chosen": "Chosen (desktop)",
    "desktop_installed": "Installed (desktop)",
    "project": "Projects",
    "coding": "Coding",
    "dev_apps": "Developers & apps",
}


def test_config_is_where_the_reader_looks():
    assert views.CONFIG_PATH == CONFIG
    assert CONFIG.is_file()


def test_config_has_the_ruled_view_names():
    wording = views.load()
    assert list(wording) == list(data.RANK_KEYS)
    assert {k: v["label"] for k, v in wording.items() if k in RULED} == RULED
    assert wording["rising"]["label"] == "Rising (beta)"  # unchanged, and still unavailable
    assert all(set(v) == set(views.WORDING) for v in wording.values())
    raw = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["views"]
    assert [{"key": k, **v} for k, v in wording.items()] == raw


def test_the_ruling_matches_when_its_record_is_here():
    """data/reviews/site/2026-10-07.toml arrives with PLAN-REVIEWERS-2.md's rulings."""
    record = ROOT / "data" / "reviews" / "site" / "2026-10-07.toml"
    if record.is_file():
        ruling = tomllib.loads(record.read_text(encoding="utf-8"))["view_labels_short"]
        assert ruling["value"].split(" / ") == list(RULED.values())


def test_the_committed_catalog_differs_from_config_only_in_wording():
    """The catalog stays the record of what was published: the same views in the same order,
    with the same fields, as config would give it. Only the labels and measures lines may
    differ, until the first live refresh copies config's; then the site's override is a no-op
    (below)."""
    committed = data.load(COMMITTED)["views"]
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["views"]
    assert [v["key"] for v in committed] == [v["key"] for v in config]
    for given, published in zip(config, committed, strict=True):
        assert set(given) <= set(published), given["key"]
        rest = [k for k in given if k not in views.WORDING]
        assert {k: given[k] for k in rest} == {k: published[k] for k in rest}
    # What the site shows differs from the catalog in those two fields only.
    shown = views.apply({"views": committed}, views.load())["views"]
    for view, published in zip(shown, committed, strict=True):
        assert {k for k in view if view[k] != published[k]} <= set(views.WORDING)


def test_the_override_is_a_no_op_once_the_catalog_carries_config_s_wording():
    committed = data.load(COMMITTED)
    refreshed = copy.deepcopy(committed)
    wording = views.load()
    for view in refreshed["views"]:
        view.update(wording[view["key"]])
    assert views.apply(refreshed, wording) == refreshed
    assert views.apply(SAMPLE, wording) == SAMPLE  # the sample already matches config


def test_apply_takes_config_s_label_and_measures_by_key_and_keeps_the_rest():
    doc = copy.deepcopy(SAMPLE)
    before = copy.deepcopy(doc)
    doc["views"].reverse()  # the catalog's order is kept, whatever config's
    wording = {
        v["key"]: {"label": f"L {v['key']}", "measures": f"M {v['key']}."} for v in SAMPLE["views"]
    }
    shown = views.apply(doc, wording)
    assert [v["key"] for v in shown["views"]] == [v["key"] for v in doc["views"]]
    for view, was in zip(shown["views"], doc["views"], strict=True):
        assert view == {**was, "label": f"L {was['key']}", "measures": f"M {was['key']}."}
    assert shown["fonts"] is doc["fonts"]
    doc["views"].reverse()
    assert doc == before  # unchanged


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda w: w.update(trending={"label": "T", "measures": "T."}), "'trending', which the"),
        (lambda w: w.pop("rising"), "the catalog has the view 'rising', which config"),
    ],
)
def test_apply_refuses_views_that_differ(change, message):
    wording = views.load()
    change(wording)
    with pytest.raises(views.ConfigError) as caught:
        views.apply(SAMPLE, wording)
    assert len(caught.value.errors) == 1
    assert message in caught.value.errors[0]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("schema = 1\n", "no [[views]] entries"),
        ("[[views]]\nkey = 'overall'\nlabel = 'Overall'\n", "for measures"),
        ("[[views]]\nkey = 'overall'\nlabel = ' '\nmeasures = 'M.'\n", "for label"),
        (
            "[[views]]\nkey = 'overall'\nlabel = 'A'\nmeasures = 'M.'\n"
            "[[views]]\nkey = 'overall'\nlabel = 'B'\nmeasures = 'M.'\n",
            "key 'overall' listed twice",
        ),
        ("[[views]\n", "site.toml: "),
    ],
)
def test_load_refuses_a_bad_config(tmp_path, text, message):
    path = tmp_path / "site.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(views.ConfigError) as caught:
        views.load(path)
    assert any(message in line for line in caught.value.errors), caught.value.errors


def test_load_names_a_missing_file(tmp_path):
    with pytest.raises(views.ConfigError, match=r"site\.toml"):
        views.load(tmp_path / "site.toml")


def test_the_reader_keeps_to_light_imports():
    """The tff_site package docstring: no tff_catalog.config (and so no numpy)."""
    code = (
        "import sys, tff_site.views; "
        "print(sorted(m for m in sys.modules if m.startswith('tff_catalog') or m == 'numpy'))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout.replace("'", '"')) == []
