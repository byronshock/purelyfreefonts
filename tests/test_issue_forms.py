"""Issue forms and chooser (Milestone 2 step 12, M2-D10).

Offline: checks the rules GitHub
applies to issue forms that we rely on, plus our own: each form applies its
own label, the license form keeps the field ids the details panel prefills
(read from site/CONTRACT.md section 8, not restated here), the site's report
link names a form that exists, and the chooser has blank issues off and an
http(s) contact link naming the admin@ address (GitHub drops mailto: links).
"""

import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from tff_site import data

ROOT = Path(os.environ.get("TFF_REPO_ROOT", Path(__file__).resolve().parents[1]))
TEMPLATES = ROOT / ".github" / "ISSUE_TEMPLATE"
FORMS = {
    "license.yml": "license",
    "missing-font.yml": "missing-font",
    "usability.yml": "usability",
    "bug.yml": "bug",
}
# design-m2 "GitHub": the repository's labels. deploy-failure is the deploy workflow's own.
REPO_LABELS = {"deploy-failure", "license", "missing-font", "usability", "bug"}
ELEMENT_KEYS = {
    "markdown": {"value"},
    "input": {"label", "description", "placeholder", "value"},
    "textarea": {"label", "description", "placeholder", "value", "render"},
    "dropdown": {"label", "description", "multiple", "options", "default"},
    "checkboxes": {"label", "description", "options"},
}
# Query parameters prefill only these element types.
PREFILLABLE = {"input", "textarea"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
EMAIL = data.FEEDBACK_EMAIL


def load(name: str) -> dict:
    return yaml.safe_load((TEMPLATES / name).read_text(encoding="utf-8"))


def elements(form: dict) -> dict[str, dict]:
    return {el["id"]: el for el in form["body"] if el["type"] != "markdown"}


def test_only_known_files() -> None:
    names = {p.name for p in TEMPLATES.iterdir()}
    # M3 step 8 adds wrong-match.yml; add it here then.
    assert names == set(FORMS) | {"config.yml"}


def test_the_address_is_the_sites() -> None:
    assert EMAIL == "admin@purelyfreefonts.com"


@pytest.mark.parametrize(("name", "label"), FORMS.items())
def test_form_is_valid_and_applies_its_label(name: str, label: str) -> None:
    form = load(name)
    assert set(form) <= {"name", "description", "title", "labels", "assignees", "projects", "body"}
    assert form["name"].strip()
    assert form["description"].strip()
    assert form["labels"] == [label]
    assert label in REPO_LABELS

    body = form["body"]
    assert body[0]["type"] == "markdown", "the first element says reports are public"
    assert "Reports are public" in body[0]["attributes"]["value"]
    assert EMAIL in body[0]["attributes"]["value"]
    assert any(el["type"] != "markdown" for el in body)

    ids, labels = [], []
    for el in body:
        kind = el["type"]
        assert kind in ELEMENT_KEYS
        assert set(el) <= {"type", "id", "attributes", "validations"}
        assert set(el["attributes"]) <= ELEMENT_KEYS[kind]
        if kind == "markdown":
            assert el["attributes"]["value"].strip()
            assert "id" not in el
            assert "validations" not in el
            continue
        assert ID_RE.match(el["id"]), el["id"]
        ids.append(el["id"])
        labels.append(el["attributes"]["label"].strip())
        if kind == "dropdown":
            opts = el["attributes"]["options"]
            assert opts
            assert all(isinstance(o, str) and o.strip() for o in opts)
            assert len(set(opts)) == len(opts)
            assert "None" not in opts  # reserved by GitHub
        if kind == "checkboxes":
            opts = [o["label"] for o in el["attributes"]["options"]]
            assert opts
            assert len(set(opts)) == len(opts)
    assert all(labels)
    assert len(set(ids)) == len(ids)
    assert len(set(labels)) == len(labels)


def test_form_names_are_unique() -> None:
    names = [load(name)["name"] for name in FORMS]
    assert len(set(names)) == len(names)


def test_details_report_link_opens_the_license_form() -> None:
    parts = urlsplit(data.REPORT_ISSUE_URL)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{data.REPO_URL}/issues/new"
    (template,) = parse_qs(parts.query)["template"]
    assert template == "license.yml"
    assert (TEMPLATES / template).is_file()
    assert load(template)["name"] == "Wrong license or link"


def test_license_form_keeps_the_prefilled_ids() -> None:
    contract = (ROOT / "site" / "CONTRACT.md").read_text(encoding="utf-8")
    # Section 8: "... to which `Details` appends `&font_id=<id>&data_date=<run_date>` ..."
    appended = re.search(r"`Details` appends `([^`]+)`", contract)
    assert appended, "site/CONTRACT.md section 8 no longer names the appended parameters"
    params = re.findall(r"&([A-Za-z0-9_-]+)=", appended.group(1))
    assert params == ["font_id", "data_date"]
    fields = elements(load("license.yml"))
    for param in params:
        assert param in fields, f"license.yml has no field with id {param!r} to prefill"
        assert fields[param]["type"] in PREFILLABLE
        assert "value" not in fields[param]["attributes"]  # a default would hide a missed prefill


def test_usability_form_uses_the_test_plans_severities() -> None:
    severity = elements(load("usability.yml"))["severity"]
    levels = [re.search(r"\((\w+)\)$", o).group(1) for o in severity["attributes"]["options"]]
    assert levels == ["blocker", "major", "minor"]
    plan = (ROOT / "docs" / "usability-test.md").read_text(encoding="utf-8")
    for level in levels:
        assert f"- **{level.capitalize()}:**" in plan


def test_chooser_has_blank_issues_off_and_an_email_contact() -> None:
    config = load("config.yml")
    assert set(config) <= {"blank_issues_enabled", "contact_links"}
    assert config["blank_issues_enabled"] is False
    (link,) = config["contact_links"]
    assert set(link) == {"name", "url", "about"}
    url = urlsplit(link["url"])
    assert url.scheme == "https"  # a mailto: link never shows
    # The site's one feedback spot, div#feedback on every page (site/CONTRACT.md section 3).
    assert url.netloc == "purelyfreefonts.com"
    assert url.fragment == "feedback"
    assert EMAIL in link["about"]
