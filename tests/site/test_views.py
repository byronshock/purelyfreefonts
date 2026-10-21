"""``tff_site.views``: the views' wording comes from config/site.toml (owner's site ruling of
2026-10-07, ``view_labels_short``), over the catalog's copies until the next refresh, and so do
their order and the retired Overall (``default_rank_project``, ``overall_retired``, 2026-09-30;
docs/milestone-method.md step 3).

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


def test_config_has_the_ruled_view_names_order_and_retired_view():
    wording = views.load()
    assert sorted(wording) == sorted(data.RANK_KEYS)
    assert next(iter(wording)) == data.DEFAULT_VIEW == "project"  # the list opens on it
    assert list(wording)[-1] == "overall"
    assert {k for k, v in wording.items() if v.get("retired")} == data.RETIRED_VIEWS
    assert {k: v["label"] for k, v in wording.items() if k in RULED} == RULED
    assert wording["rising"]["label"] == "Rising (beta)"  # unchanged, and still unavailable
    assert all(set(v) - {"retired"} == set(views.WORDING) for v in wording.values())
    raw = tomllib.loads(CONFIG.read_text(encoding="utf-8"))["views"]
    assert [{"key": k, **v} for k, v in wording.items()] == raw


def test_the_ruling_matches_when_its_record_is_here():
    """data/reviews/site/2026-10-07.toml arrives with PLAN-REVIEWERS-2.md's rulings."""
    record = ROOT / "data" / "reviews" / "site" / "2026-10-07.toml"
    if record.is_file():
        ruling = tomllib.loads(record.read_text(encoding="utf-8"))["view_labels_short"]
        assert ruling["value"].split(" / ") == list(RULED.values())


def test_the_committed_catalog_differs_from_config_only_in_wording_order_and_retirement():
    """The catalog stays the record of what was published: the same views, with the same
    fields, as config would give it. Only the labels, measures lines, the order and a retired
    view's ``available`` may differ, until the next refresh exports config's; then the site's
    override is a no-op (below)."""
    committed = {v["key"]: v for v in data.load(COMMITTED)["views"]}
    wording = views.load()
    assert sorted(committed) == sorted(wording)
    shown = views.apply({"views": list(committed.values())}, wording)["views"]
    assert [v["key"] for v in shown] == list(wording)
    for view in shown:
        published = committed[view["key"]]
        allowed = set(views.WORDING) | (
            {"available"} if data.RETIRED_VIEWS & {view["key"]} else set()
        )
        assert {k for k in view if view[k] != published[k]} <= allowed, view["key"]
        assert set(view) == set(published)
    assert not [v["key"] for v in shown if v["available"] and v["key"] in data.RETIRED_VIEWS]


def test_the_override_is_a_no_op_once_the_catalog_carries_config_s_views():
    committed = data.load(COMMITTED)
    refreshed = copy.deepcopy(committed)
    wording = views.load()
    by_key = {view["key"]: view for view in refreshed["views"]}
    refreshed["views"] = [by_key[key] for key in wording]
    for view in refreshed["views"]:
        view.update({f: wording[view["key"]][f] for f in views.WORDING})
        if wording[view["key"]].get("retired"):
            view["available"] = False
    assert views.apply(refreshed, wording) == refreshed
    assert views.apply(SAMPLE, wording) == SAMPLE  # the sample already matches config


def test_apply_takes_config_s_order_label_and_measures_by_key_and_keeps_the_rest():
    doc = copy.deepcopy(SAMPLE)
    for view in doc["views"]:
        view["available"] = True  # as the committed catalog lists Overall
    before = copy.deepcopy(doc)
    doc["views"].reverse()  # config's order wins, whatever the catalog's
    wording = {
        v["key"]: {"label": f"L {v['key']}", "measures": f"M {v['key']}."} for v in SAMPLE["views"]
    }
    wording["dev_apps"]["retired"] = True
    shown = views.apply(doc, wording)
    assert [v["key"] for v in shown["views"]] == [v["key"] for v in SAMPLE["views"]]
    was = {v["key"]: v for v in doc["views"]}
    for view in shown["views"]:
        key = view["key"]
        retired = {"available": False} if key == "dev_apps" else {}
        assert view == {**was[key], "label": f"L {key}", "measures": f"M {key}.", **retired}
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
        (
            "[[views]]\nkey = 'overall'\nlabel = 'Overall'\nmeasures = 'M.'\nretired = 'yes'\n",
            "retired must be true or false",
        ),
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
