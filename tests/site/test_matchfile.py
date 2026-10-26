"""``tff_site.matchfile``: the hopper's match file, made at build time from ``names.json`` and
the catalog (the owner's ruling of 2026-10-22, ``inventory_match_data_site_build``), with the
reviewed list beside the names file (``hopper_hackgen_related``, 2026-10-23, for its recodes).

Offline, no browser. Each test writes a names file, and usually the reviewed list beside it,
into ``tmp_path``. The build's use of the module (the hashed file, ``#list[data-match]``,
``version.txt``, ``--names``) is in test_build.py.
"""

import copy
import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from tff_catalog import jsonio, owned
from tff_catalog.keys import match_key
from tff_site import budgets, data, matchfile

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PATH = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
SAMPLE = data.load(SAMPLE_PATH)
NAMES_PATH = ROOT / "tests" / "fixtures" / "names.sample.json"
NAMES = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
REVIEWED_PATH = ROOT / "tests" / "fixtures" / "names.sample.reviewed.toml"
REVIEWED = REVIEWED_PATH.read_text(encoding="utf-8")
SAMPLE_RECODES = {"Sample Mono 20 Gen": "related"}
COMMITTED_DATA = ROOT / "build" / "catalog-site.json"
# What a reviewed list must say for the sample's one ambiguous name, as a TOML table.
AMBIGUOUS_06 = (
    '[[ambiguous]]\nname = "Sample Sans 06 Black"\nfamilies = ["sample-sans-06"]\n'
    'owners = ["sample-sans-06-black"]\nruling = "synthetic"\n'
)
RECODE_20 = (
    '[[recode]]\nname = "Sample Mono 20 Gen"\nfamily = "sample-mono-20"\nrelation = "related"\n'
    'ruling = "synthetic"\n'
)
# How a collision problem that no edit to the reviewed list clears ends (ops/MONTHLY.md step 5).
UNLISTABLE = (
    "; no entry in the reviewed list clears this: the owner rules, and an alias row "
    "(Milestone 1's review) or a change to the build carries it out (ops/MONTHLY.md step 5)"
)


def recodable(family_id: str, *also: str) -> str:
    """How a collision problem that a recode of ``family_id``'s alias would end ends, with the
    recodes of ``also``, the family's names that would bypass it."""
    together = (
        f", together with a [[recode]] of each of its names that would bypass it "
        f"({', '.join(map(repr, also))})"
        if also
        else ""
    )
    return (
        f"; the owner rules: a [[recode]] making {family_id}'s alias at that key a near-match "
        f"clears this{together}, and otherwise an alias row (Milestone 1's review) or a change "
        "to the build carries it out (ops/MONTHLY.md step 5)"
    )


def recode_entry(name: str, family_id: str, relation: str) -> str:
    """A reviewed list's [[recode]] entry, citing the sample's "synthetic"."""
    return (
        RECODE_20.replace("Sample Mono 20 Gen", name)
        .replace("sample-mono-20", family_id)
        .replace('"related"', f'"{relation}"')
    )


def write(tmp_path: Path, names: Any = None, reviewed: str | None = REVIEWED) -> Path:
    """Write a names file (the sample's by default) and the reviewed list beside it, unless
    ``reviewed`` is None; return the names file's path."""
    path = tmp_path / "names.json"
    path.write_bytes(jsonio.pretty_bytes(NAMES if names is None else names))
    if reviewed is not None:
        (tmp_path / "names.reviewed.toml").write_text(reviewed, encoding="utf-8")
    return path


def errors_of(path: Path, doc: dict[str, Any] = SAMPLE) -> list[str]:
    with pytest.raises(matchfile.MatchFileError) as caught:
        matchfile.match_file(doc, path)
    return caught.value.errors


def family(names: dict[str, Any], font_id: str) -> dict[str, Any]:
    return next(f for f in names["families"] if f["id"] == font_id)


def names_with(mutate: Any) -> dict[str, Any]:
    names = copy.deepcopy(NAMES)
    mutate(names)
    return names


# ------------------------------------------------------------------------- the match file


def test_the_sample_gives_owned_s_match_document_with_its_recodes(tmp_path):
    path = write(tmp_path)
    made = matchfile.match_file(SAMPLE, path)
    ids = [f["id"] for f in SAMPLE["fonts"]]
    want = owned.match_document(NAMES, ids, SAMPLE["run"]["date"], recodes=SAMPLE_RECODES)
    assert made.blob == jsonio.canonical_bytes(want)
    assert made.names_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert made.reviewed_sha256 == hashlib.sha256(REVIEWED.encode()).hexdigest()
    doc = json.loads(made.blob)
    assert (doc["format"], doc["v"], doc["run_date"]) == ("tff-match", 1, SAMPLE["run"]["date"])
    assert doc["amb"] == {"samplesans06black": [doc["ids"].index("sample-sans-06")]}
    assert doc["k"]["samplemono20gen"] == f"{doc['ids'].index('sample-mono-20')}x"


def test_the_sample_s_files_are_found_beside_it(tmp_path):
    """The sample catalog builds with its own names and reviewed list, which these tests copy."""
    assert matchfile.default_names_path(SAMPLE_PATH) == NAMES_PATH
    assert matchfile.default_reviewed_path(NAMES_PATH) == REVIEWED_PATH
    in_place = matchfile.match_file(SAMPLE, NAMES_PATH)
    assert in_place == matchfile.match_file(SAMPLE, write(tmp_path))


def test_the_match_file_follows_the_names_data_not_its_formatting(tmp_path):
    """The match file's own ``names_sha256`` hashes the canonical form; the one for
    ``version.txt`` hashes the file's bytes, as ``catalog_sha256`` does."""
    first = matchfile.match_file(SAMPLE, write(tmp_path))
    path = tmp_path / "names.json"
    path.write_text(json.dumps(NAMES, indent=4), encoding="utf-8")
    second = matchfile.match_file(SAMPLE, path)
    assert second.blob == first.blob
    assert second.names_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert second.names_sha256 != first.names_sha256
    assert second.reviewed_sha256 == first.reviewed_sha256


def test_without_its_recode_the_alias_keeps_its_names_json_relation(tmp_path):
    made = matchfile.match_file(SAMPLE, write(tmp_path, reviewed=AMBIGUOUS_06))
    doc = json.loads(made.blob)
    assert doc["k"]["samplemono20gen"] == f"{doc['ids'].index('sample-mono-20')}b"
    assert made.reviewed_sha256 == hashlib.sha256(AMBIGUOUS_06.encode()).hexdigest()


@pytest.mark.parametrize(
    ("data_file", "names_file"),
    [
        ("build/catalog-site.json", "build/names.json"),
        ("tests/fixtures/catalog-site.sample.json", "tests/fixtures/names.sample.json"),
        ("large/catalog-site.json", "large/names.json"),
        ("large/data.json", "large/names.json"),
    ],
)
def test_the_names_file_is_the_catalog_s_neighbour_by_default(data_file, names_file):
    assert matchfile.default_names_path(Path(data_file)) == Path(names_file)


@pytest.mark.parametrize(
    ("names_file", "reviewed_file"),
    [
        ("build/names.json", "build/names.reviewed.toml"),
        ("tests/fixtures/names.sample.json", "tests/fixtures/names.sample.reviewed.toml"),
        ("x/names-of-the-run.json", "x/names-of-the-run.reviewed.toml"),
    ],
)
def test_the_reviewed_list_is_the_names_file_s_neighbour(names_file, reviewed_file):
    assert matchfile.default_reviewed_path(Path(names_file)) == Path(reviewed_file)


# ---------------------------------------------------------------- a names file of another run


def _drop_rename(names: dict[str, Any]) -> None:
    fam = family(names, "sample-sans-05")
    fam["names"] = [n for n in fam["names"] if n["relation"] != "rename"]


def _relate_build(names: dict[str, Any]) -> None:
    (alias,) = [n for n in family(names, "sample-mono-07")["names"] if n["name"] == "SMono07 NF"]
    alias["relation"] = "related"
    del alias["detail"]


NAMES_OF_ANOTHER_RUN = [
    pytest.param(
        lambda n: n["run"].update(date="2026-09-26"),
        "run.date is 2026-09-26, the catalog's is 2026-09-25",
        id="run-date",
    ),
    pytest.param(
        lambda n: n["run"].update(method_version="2026-10-20"),
        "run.method_version is 2026-10-20, the catalog's is 2026-09-29",
        id="method-version",
    ),
    pytest.param(
        lambda n: family(n, "sample-sans-40").update(in_catalog=False),
        "sample-sans-40: in the catalog, but not an in_catalog family",
        id="missing-id",
    ),
    pytest.param(
        lambda n: family(n, "sample-held-41").update(in_catalog=True),
        "sample-held-41: an in_catalog family the catalog lacks",
        id="extra-id",
    ),
    pytest.param(
        _drop_rename,
        "sample-sans-05: no rename name 'Sample Sans Classic', which the catalog lists",
        id="missing-alias",
    ),
    pytest.param(
        _relate_build,
        "sample-mono-07: no build name 'SMono07 NF', which the catalog lists",
        id="alias-relation",
    ),
    pytest.param(
        lambda n: family(n, "sample-sans-01").update(family="Sample Sans One"),
        "sample-sans-01: family 'Sample Sans One', the catalog's 'Sample Sans 01'",
        id="family-name",
    ),
    pytest.param(
        lambda n: n["families"].append(copy.deepcopy(family(n, "sample-sans-01"))),
        "family sample-sans-01 is listed 2 times",
        id="repeated-id",
    ),
]


@pytest.mark.parametrize(("mutate", "message"), NAMES_OF_ANOTHER_RUN)
def test_a_names_file_of_another_run_is_refused(tmp_path, mutate, message):
    path = write(tmp_path, names_with(mutate))
    assert errors_of(path) == [f"{path}: {message}"]


def test_another_schema_version_is_refused_before_anything_else(tmp_path):
    path = write(tmp_path, names_with(lambda n: n.update(schema_version="0.2.0", run=[])))
    assert errors_of(path) == [f"{path}: schema_version is '0.2.0', not '0.1.0-draft'"]


def test_a_names_file_of_the_wrong_shape_is_refused_before_its_run_is_read(tmp_path):
    """The stages of site/CONTRACT.md section 1: the shape's problems stop it before the run's."""

    def both(names: dict[str, Any]) -> None:
        names["run"].update(date="2026-01-02")
        names["families"][0].update(superfamily_id=5)

    path = write(tmp_path, names_with(both))
    assert errors_of(path) == [
        f"{path}: /families/0/superfamily_id is missing or not null or a string"
    ]


def test_every_problem_of_another_run_is_reported_at_once(tmp_path):
    names = copy.deepcopy(NAMES)
    for p in NAMES_OF_ANOTHER_RUN:
        p.values[0](names)
    path = write(tmp_path, names)
    assert sorted(errors_of(path)) == sorted(f"{path}: {p.values[1]}" for p in NAMES_OF_ANOTHER_RUN)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (None, "not found; the build needs the names file of the catalog's run"),
        ("{", "Expecting property name"),
        ('{"schema_version": "0.1.0-draft", "schema_version": "x"}', "key repeated"),
        ('{"a": NaN}', "NaN is not valid JSON"),
        ("[" * 200_000, "nested too deeply to read"),
        ('{"a":' * 200_000, "nested too deeply to read"),
        ("\udcff", "codec can't decode"),
    ],
)
def test_a_missing_or_unreadable_names_file_is_refused_without_a_traceback(tmp_path, text, problem):
    path = tmp_path / "names.json"
    if text is not None:
        path.write_bytes(text.encode("utf-8", "surrogateescape"))
    (error,) = errors_of(path)
    assert error.startswith(f"{path}: ")
    assert problem in error


# ------------------------------------------------------------------ a names file of the wrong shape


def _wide_build(names: dict[str, Any]) -> None:
    next(x for x in names["families"][0]["names"] if x["relation"] == "build").update(detail="wide")


MALFORMED_NAMES = [
    pytest.param(
        lambda n: n.pop("ineligible"), "/ineligible is missing or not a list", id="no-ineligible"
    ),
    pytest.param(
        lambda n: n.pop("families"), "/families is missing or not a list", id="no-families"
    ),
    pytest.param(
        lambda n: n["families"][0].pop("id"),
        "/families/0/id is missing or not a string",
        id="no-id",
    ),
    pytest.param(
        lambda n: n["families"][0].update(family=7),
        "/families/0/family is missing or not a string",
        id="number-family",
    ),
    pytest.param(
        lambda n: n["families"][0]["names"].__setitem__(0, "x"),
        "/families/0/names/0 is not an object with a name string",
        id="string-name",
    ),
    pytest.param(
        lambda n: n["families"][0]["names"].__setitem__(0, {"name": 7, "relation": "related"}),
        "/families/0/names/0 is not an object with a name string",
        id="number-name",
    ),
    pytest.param(
        lambda n: n["families"][0].update(distinct_from=None),
        "/families/0/distinct_from is missing or not a list of family ids",
        id="null-distinct-from",
    ),
    pytest.param(
        lambda n: n["families"][0].update(distinct_from=[["x"]]),
        "/families/0/distinct_from is missing or not a list of family ids",
        id="nested-distinct-from",
    ),
    pytest.param(
        lambda n: n["families"][0].update(names="x"),
        "/families/0/names is missing or not a list",
        id="string-names",
    ),
    pytest.param(
        lambda n: n["ineligible"][0].update(reason=7),
        "/ineligible/0 is not a name and a reason, both strings",
        id="number-reason",
    ),
    pytest.param(
        lambda n: n["ineligible"].__setitem__(0, "x"),
        "/ineligible/0 is not a name and a reason, both strings",
        id="string-ineligible",
    ),
    pytest.param(
        lambda n: n["families"][0]["names"][0].update(relation=[]),
        "/families/0/names/0/relation is [], not a relation of schemas/names.schema.json",
        id="list-relation",
    ),
    pytest.param(
        lambda n: n["families"][0]["names"][0].update(relation="renamed"),
        "/families/0/names/0/relation is 'renamed', not a relation of schemas/names.schema.json",
        id="unknown-relation",
    ),
    pytest.param(
        _wide_build,
        "/families/0/names/0/detail is 'wide', not a build detail of schemas/names.schema.json",
        id="unknown-detail",
    ),
    pytest.param(
        lambda n: n["families"].__setitem__(0, "x"),
        "/families/0 is not an object",
        id="string-family",
    ),
    pytest.param(lambda n: n.update(run=[]), "/run is missing or not an object", id="list-run"),
    pytest.param(lambda n: n.pop("run"), "/run is missing or not an object", id="no-run"),
    pytest.param(
        lambda n: n["run"].update(date=20260925),
        "/run/date is missing or not a string",
        id="number-date",
    ),
    pytest.param(
        lambda n: n["run"].pop("method_version"),
        "/run/method_version is missing or not a string",
        id="no-method-version",
    ),
    pytest.param(
        lambda n: n["families"][3].update(in_catalog="yes"),
        "/families/3/in_catalog is missing or not true or false",
        id="string-in-catalog",
    ),
    pytest.param(
        lambda n: n["families"][0].update(superfamily_id=7),
        "/families/0/superfamily_id is missing or not null or a string",
        id="number-superfamily",
    ),
    pytest.param(
        lambda n: n["families"][0].pop("superfamily_id"),
        "/families/0/superfamily_id is missing or not null or a string",
        id="no-superfamily",
    ),
    pytest.param(
        lambda n: n.update(extra=1),
        "/ has the key 'extra', which schemas/names.schema.json doesn't allow",
        id="unknown-key",
    ),
    pytest.param(
        lambda n: n["run"].update(when="now"),
        "/run has the key 'when', which schemas/names.schema.json doesn't allow",
        id="unknown-run-key",
    ),
    pytest.param(
        lambda n: n["families"][0].update(note="x"),
        "/families/0 has the key 'note', which schemas/names.schema.json doesn't allow",
        id="unknown-family-key",
    ),
    pytest.param(
        lambda n: n["families"][0]["names"][0].update(note="x"),
        "/families/0/names/0 has the key 'note', which schemas/names.schema.json doesn't allow",
        id="unknown-name-key",
    ),
    pytest.param(
        lambda n: n["ineligible"][0].update(note="x"),
        "/ineligible/0 has the key 'note', which schemas/names.schema.json doesn't allow",
        id="unknown-ineligible-key",
    ),
]


@pytest.mark.parametrize(("mutate", "message"), MALFORMED_NAMES)
def test_a_malformed_names_file_is_refused_saying_where(tmp_path, mutate, message):
    path = write(tmp_path, names_with(mutate))
    assert errors_of(path) == [f"{path}: {message}"]


def test_the_shape_check_allows_exactly_the_keys_the_schema_does():
    schema = json.loads((ROOT / matchfile.NAMES_SCHEMA).read_text(encoding="utf-8"))
    props = schema["properties"]
    family_schema = props["families"]["items"]
    objects = {
        matchfile.NAMES_KEYS: schema,
        matchfile.RUN_KEYS: props["run"],
        matchfile.FAMILY_KEYS: family_schema,
        matchfile.NAME_KEYS: family_schema["properties"]["names"]["items"],
        matchfile.INELIGIBLE_KEYS: props["ineligible"]["items"],
    }
    for keys, part in objects.items():
        assert part["additionalProperties"] is False
        assert keys == set(part["properties"])
    assert set(props["run"]["required"]) == matchfile.RUN_KEYS


def splice(where: str, value: str) -> str:
    """The sample's names file as JSON text with ``value`` (JSON text) put ``where``: under a
    new top-level key, as the first family's superfamily_id, or under a new key of its first
    postscript name or of the first ineligible row."""
    text = json.dumps(NAMES)
    if where == "top":
        return text[:-1] + f', "x": {value}}}'
    old = {
        "superfamily": '"superfamily_id": null',
        "name": '"relation": "postscript"',
        "ineligible": '"reason": "icon"',
    }[where]
    assert old in text
    new = f'"superfamily_id": {value}' if where == "superfamily" else f'{old}, "y": {value}'
    return text.replace(old, new, 1)


@pytest.mark.parametrize("depth", [3, data.MAX_DEPTH, 50_000])
@pytest.mark.parametrize(
    ("where", "shallow"),
    [
        ("top", "/ has the key 'x', which schemas/names.schema.json doesn't allow"),
        ("superfamily", "/families/0/superfamily_id is missing or not null or a string"),
        (
            "name",
            "/families/0/names/1 has the key 'y', which schemas/names.schema.json doesn't allow",
        ),
        (
            "ineligible",
            "/ineligible/0 has the key 'y', which schemas/names.schema.json doesn't allow",
        ),
    ],
)
def test_a_value_nested_deeply_anywhere_is_refused_without_a_traceback(
    tmp_path, where, shallow, depth
):
    """Python's serialisers recurse: a value some 47,000 levels deep, under a key the build
    doesn't read, crashed it with a ``RecursionError`` though the parser had read it.
    ``data.loads`` refuses more than ``MAX_DEPTH`` levels anywhere, and the shape check refuses
    a key the schema doesn't allow, or a superfamily_id that is neither null nor a string."""
    path = tmp_path / "names.json"
    path.write_text(splice(where, "[" * depth + "]" * depth), encoding="utf-8")
    (tmp_path / "names.reviewed.toml").write_text(REVIEWED, encoding="utf-8")
    deep = f"nested too deeply to read (more than {data.MAX_DEPTH} levels)"
    assert errors_of(path) == [f"{path}: {deep if depth >= data.MAX_DEPTH else shallow}"]


def test_a_names_file_or_a_reviewed_list_that_is_a_directory_is_refused(tmp_path):
    """An ``OSError`` other than a missing file, such as a directory where the file should be
    (or one that can't be read), is a message saying where, not a traceback."""
    names = tmp_path / "names.json"
    names.mkdir()
    (error,) = errors_of(names)
    assert error.startswith(f"{names}: ")
    assert "Is a directory" in error
    beside = tmp_path / "x"
    beside.mkdir()
    path = write(beside, reviewed=None)
    listed = beside / "names.reviewed.toml"
    listed.mkdir()
    (error,) = errors_of(path)
    assert error.startswith(f"{listed}: ")
    assert "Is a directory" in error


def test_a_names_file_that_is_no_object_is_refused(tmp_path):
    path = tmp_path / "names.json"
    path.write_text("[]", encoding="utf-8")
    assert errors_of(path) == [f"{path}: / is a list, not an object"]


def test_a_names_file_wrong_throughout_lists_twenty_problems_and_a_count():
    names = copy.deepcopy(NAMES)
    for fam in names["families"]:
        fam["distinct_from"] = None
    errors = matchfile.names_shape_errors(names)
    assert len(errors) == matchfile.MAX_SHAPE_ERRORS + 1
    assert (
        errors[-1]
        == f"and {len(names['families']) - matchfile.MAX_SHAPE_ERRORS} more such problems"
    )


def test_a_match_file_the_hopper_cannot_read_is_refused(tmp_path, monkeypatch):
    """The match file is read back with ``owned.Table.from_document``, the reader the page
    mirrors, so a broken one fails the build, not a visitor's browser."""
    real = owned.match_document
    monkeypatch.setattr(owned, "match_document", lambda *a, **k: {**real(*a, **k), "reasons": [7]})
    path = write(tmp_path)
    assert errors_of(path) == [
        f"{path}: the match file made from it is one the hopper can't read "
        "(ids and reasons must be lists of strings)"
    ]


# ------------------------------------------------------------- Milestone 3 step 3's collision check


def _meet(names: dict[str, Any]) -> None:
    """Sample Sans 01 Tight, distinct from Sample Sans 01, gets a name that reaches it."""
    family(names, "sample-sans-01-tight")["names"].append(
        {"name": "Sample Sans 01 Bold", "relation": "related"}
    )


def _share(names: dict[str, Any]) -> None:
    family(names, "sample-sans-03")["names"].append(
        {"name": "Sample Sans 01", "relation": "rename"}
    )


def _strip_out(names: dict[str, Any]) -> None:
    family(names, "sample-sans-03")["names"].append(
        {"name": "Sample Held 41 Bold", "relation": "rename"}
    )


def _share_aliases(names: dict[str, Any]) -> None:
    """Sample Sans 03 gets Sample Sans 05's rename: two families' aliases at one key."""
    family(names, "sample-sans-03")["names"].append(
        {"name": "Sample Sans Classic", "relation": "rename"}
    )


def _meet_at_two_families_aliases(names: dict[str, Any]) -> None:
    """Sample Sans 01 Tight, made distinct from Sample Sans 03 and 05, reaches the rename that
    both now have (``_share_aliases``)."""
    _share_aliases(names)
    tight = family(names, "sample-sans-01-tight")
    tight["names"].append({"name": "Sample Sans Classic Bold", "relation": "related"})
    tight["distinct_from"] = sorted({*tight["distinct_from"], "sample-sans-03", "sample-sans-05"})


def _meet_at_a_name_an_alias_spells(names: dict[str, Any]) -> None:
    """``_meet``, and Sample Sans 01 has an alias at its own name's key, "SampleSans01"."""
    _meet(names)
    family(names, "sample-sans-01")["names"].append({"name": "SampleSans01", "relation": "rename"})


def outside_family(family_id: str, name: str) -> dict[str, Any]:
    """A names.json family outside the catalog, with no aliases."""
    return {
        "id": family_id,
        "family": name,
        "superfamily_id": None,
        "in_catalog": False,
        "names": [],
        "distinct_from": [],
    }


def _share_outside(names: dict[str, Any]) -> None:
    """Sample Sans 03 gets the rename "Sample Held 41", the name of a family outside the
    catalog: a list naming that family would count Sample Sans 03 as owned."""
    family(names, "sample-sans-03")["names"].append(
        {"name": "Sample Held 41", "relation": "rename"}
    )


def _share_outside_by_an_alias(names: dict[str, Any]) -> None:
    """Sample Held 41, outside the catalog, gets the rename "Sample Sans 03": a recode of a
    family outside the catalog changes nothing, and Sample Sans 03's own name is no alias."""
    family(names, "sample-held-41")["names"].append(
        {"name": "Sample Sans 03", "relation": "rename"}
    )


def _share_outside_twice(names: dict[str, Any]) -> None:
    """``_share_outside``, and another family outside the catalog is named "Sample Held 41":
    a recode of Sample Sans 03's alias leaves only families outside the catalog at the key."""
    _share_outside(names)
    names["families"].append(outside_family("sample-held-41-old", "Sample Held 41"))


def _share_with_a_name_outside(names: dict[str, Any]) -> None:
    """``_share``, and a family outside the catalog is named "Sample Sans 01" too: a recode of
    Sample Sans 03's alias leaves Sample Sans 01 sharing the key with it."""
    _share(names)
    names["families"].append(outside_family("sample-sans-01-old", "Sample Sans 01"))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(
            _meet,
            "sample-sans-01-tight: its name 'Sample Sans 01 Bold' reaches sample-sans-01 at key "
            f"'samplesans01', and the two are distinct_from each other{UNLISTABLE}",
            id="distinct-from-meets",
        ),
        pytest.param(
            _share,
            "key 'samplesans01' is owned by several catalog families (sample-sans-01, "
            f"sample-sans-03), outside the ambiguous set{recodable('sample-sans-03')}",
            id="shared-key",
        ),
        pytest.param(
            _strip_out,
            "'Sample Held 41 Bold', a catalog name, strips to the key 'sampleheld41' of "
            f"sample-held-41, outside the catalog, which the ambiguous set can't hold{UNLISTABLE}",
            id="catalog-name-to-another-family",
        ),
    ],
)
def test_names_that_would_count_a_family_as_owned_wrongly_are_refused(tmp_path, mutate, message):
    """Milestone 3 step 3, with the site build as the gate (``inventory_match_data_site_build``).
    The planted name also makes a new ambiguous key, reported after it."""
    path = write(tmp_path, names_with(mutate))
    assert errors_of(path)[0] == f"{path}: {message}"


def every_ambiguous_name(names: dict[str, Any]) -> str:
    """Reviewed-list entries for every ambiguous name the build finds in ``names``."""
    check = owned.check_document(names, [f["id"] for f in SAMPLE["fonts"]], recodes=SAMPLE_RECODES)
    owners = matchfile.key_owners(names, check.amb)
    first: dict[str, str] = {}
    for c in check.collisions:
        first.setdefault(c.key, c.name)
    return "".join(
        f'[[ambiguous]]\nname = "{first[key]}"\nfamilies = {json.dumps(list(fids))}\n'
        f'owners = {json.dumps(list(owners[key]))}\nruling = "synthetic"\n\n'
        for key, fids in check.amb.items()
    )


@pytest.mark.parametrize(
    ("mutate", "count"),
    [
        pytest.param(_meet, 1, id="meeting-at-a-family-name"),
        pytest.param(_meet_at_a_name_an_alias_spells, 1, id="meeting-at-its-own-name-s-alias"),
        pytest.param(_meet_at_two_families_aliases, 3, id="meeting-at-two-families-aliases"),
        pytest.param(_share_aliases, 1, id="key-two-families-aliases-hold"),
        pytest.param(_share_outside_by_an_alias, 2, id="key-an-outside-alias-holds"),
        pytest.param(_share_with_a_name_outside, 1, id="key-two-names-and-an-alias-hold"),
        pytest.param(_strip_out, 1, id="catalog-name-to-another-family"),
    ],
)
def test_no_entry_in_the_reviewed_list_clears_a_collision_outside_the_ambiguous_set(
    tmp_path, mutate, count
):
    """Listing every ambiguous name the build finds leaves the problems the list can't hold,
    which say so and how they are cleared instead (ops/MONTHLY.md step 5): where the owned code
    is a family's own name, or aliases that two families hold, no recode the build takes
    ends it (``matchfile.recodable_keys``), nor any recode a stripped name."""
    names = names_with(mutate)
    path = write(tmp_path, names, every_ambiguous_name(names) + RECODE_20)
    errors = errors_of(path)
    assert len(errors) == count
    for error in errors:
        assert error.startswith(f"{path}: ")
        assert error.endswith(UNLISTABLE)


def _meet_at_an_alias(names: dict[str, Any]) -> None:
    """Sample Sans 01 Tight, made distinct from Sample Sans 05 too, gets a name that reaches
    only Sample Sans 05's alias "Sample Sans Classic", a rename, not its family name."""
    tight = family(names, "sample-sans-01-tight")
    tight["names"].append({"name": "Sample Sans Classic Bold", "relation": "related"})
    tight["distinct_from"] = sorted({*tight["distinct_from"], "sample-sans-05"})


def _meet_at_an_alias_another_family_is_named(names: dict[str, Any]) -> None:
    """``_meet_at_an_alias``, and a family outside the catalog is named "Sample Sans Classic":
    a name at the key, which no recode touches, so the recode of 05's alias still ends it."""
    _meet_at_an_alias(names)
    names["families"].append(outside_family("sample-sans-classic", "Sample Sans Classic"))


MEETING_AT_05 = (
    "sample-sans-01-tight: its name 'Sample Sans Classic Bold' reaches sample-sans-05 at key "
    "'samplesansclassic', and the two are distinct_from each other"
)


@pytest.mark.parametrize(
    ("mutate", "problems", "recode", "key", "codes"),
    [
        pytest.param(
            _meet_at_an_alias,
            [MEETING_AT_05],
            ("Sample Sans Classic", "sample-sans-05", "bundle"),
            "samplesansclassic",
            [("sample-sans-05", "u")],
            id="meeting-at-an-alias",
        ),
        pytest.param(
            _meet_at_an_alias_another_family_is_named,
            [
                MEETING_AT_05,
                "key 'samplesansclassic' is owned by several families (sample-sans-05 in the "
                "catalog, sample-sans-classic not), outside the ambiguous set",
            ],
            ("Sample Sans Classic", "sample-sans-05", "bundle"),
            "samplesansclassic",
            [("sample-sans-05", "u"), ("-", "")],
            id="meeting-at-an-alias-another-family-is-named",
        ),
        pytest.param(
            _share,
            [
                "key 'samplesans01' is owned by several catalog families (sample-sans-01, "
                "sample-sans-03), outside the ambiguous set"
            ],
            ("Sample Sans 01", "sample-sans-03", "related"),
            "samplesans01",
            [("sample-sans-01", ""), ("sample-sans-03", "x")],
            id="key-a-name-and-an-alias-hold",
        ),
        pytest.param(
            _share_outside,
            [
                "key 'sampleheld41' is owned by several families (sample-sans-03 in the "
                "catalog, sample-held-41 not), outside the ambiguous set"
            ],
            ("Sample Held 41", "sample-sans-03", "related"),
            "sampleheld41",
            [("sample-sans-03", "x"), ("-", "")],
            id="key-an-alias-and-an-outside-name-hold",
        ),
        pytest.param(
            _share_outside_twice,
            [
                "key 'sampleheld41' is owned by several families (sample-sans-03 in the "
                "catalog, sample-held-41, sample-held-41-old not), outside the ambiguous set"
            ],
            ("Sample Held 41", "sample-sans-03", "related"),
            "sampleheld41",
            [("sample-sans-03", "x"), ("-", "")],
            id="key-an-alias-and-two-outside-names-hold",
        ),
    ],
)
def test_a_collision_that_a_recode_ends_names_it_and_the_recode_clears_it(
    tmp_path, mutate, problems, recode, key, codes
):
    """A meeting or a shared key needs an owned code at its key, so where the build takes a
    recode there (``matchfile.recodable_keys``: one catalog family's aliases alone, not at its
    own name) of the family whose code it is, the recode the owner may rule ends it, and the
    message says so; another family's name at the key doesn't stop it. Once listed, with the
    ambiguous names the build also finds, the build passes, the alias a near-match."""
    names = names_with(mutate)
    listed = every_ambiguous_name(names) + RECODE_20
    path = write(tmp_path, names, listed)
    assert errors_of(path) == [f"{path}: {problem}{recodable(recode[1])}" for problem in problems]
    doc = json.loads(
        matchfile.match_file(SAMPLE, write(tmp_path, names, listed + recode_entry(*recode))).blob
    )
    want = [f"{doc['ids'].index(fid)}{letter}" if fid != "-" else "-" for fid, letter in codes]
    assert doc["k"][key] == ",".join(want)


def test_a_recode_ends_only_the_problem_whose_owned_code_it_takes(tmp_path):
    """Sample Sans 01 Tight reaches Sample Sans 01's name at a key where Sample Sans 03 now
    has an alias too (``_meet``, ``_share``): a recode of 03's alias ends the shared key, not
    the meeting, which is at 01's own name."""

    def both(names: dict[str, Any]) -> None:
        _meet(names)
        _share(names)

    names = names_with(both)
    listed = every_ambiguous_name(names) + RECODE_20
    path = write(tmp_path, names, listed)
    meeting = (
        "sample-sans-01-tight: its name 'Sample Sans 01 Bold' reaches sample-sans-01 at key "
        "'samplesans01', and the two are distinct_from each other"
    )
    shared = (
        "key 'samplesans01' is owned by several catalog families (sample-sans-01, "
        "sample-sans-03), outside the ambiguous set"
    )
    assert errors_of(path) == [
        f"{path}: {meeting}{UNLISTABLE}",
        f"{path}: {shared}{recodable('sample-sans-03')}",
    ]
    path = write(
        tmp_path, names, listed + recode_entry("Sample Sans 01", "sample-sans-03", "related")
    )
    assert errors_of(path) == [f"{path}: {meeting}{UNLISTABLE}"]


def test_a_key_that_only_families_outside_the_catalog_share_passes(tmp_path):
    """No catalog family owns it, so no list's name there hides a listed font: like a
    collision that reaches no catalog family, it changes no result (``check_errors``)."""

    def renamed(names: dict[str, Any]) -> None:
        family(names, "sample-sans-06-black")["names"].append(
            {"name": "Sample Held 41", "relation": "rename"}
        )

    doc = json.loads(matchfile.match_file(SAMPLE, write(tmp_path, names_with(renamed))).blob)
    assert doc["k"]["sampleheld41"] == "-"


def _share_ambiguous(names: dict[str, Any]) -> None:
    """Sample Sans 03 gets the rename "Sample Sans 06 Black", the reviewed ambiguous name, which
    its own family, outside the catalog, holds: the name's families stay sample-sans-06."""
    family(names, "sample-sans-03")["names"].append(
        {"name": "Sample Sans 06 Black", "relation": "rename"}
    )


SHARED_AMBIGUOUS = (
    "key 'samplesans06black' is owned by several families (sample-sans-03 in the catalog, "
    "sample-sans-06-black not), in the ambiguous set, where a trusted name counts each as owned"
)


def test_a_key_in_the_ambiguous_set_that_gains_an_owner_is_refused(tmp_path):
    """The hopper doubts an ambiguous key's owned codes only for an untrusted name: fc-list's
    first family and a PostScript name's family part count every one (``owned.match``). So a
    family that gains a name at a reviewed ambiguous key is a changed name, which
    ``inventory_ambiguous_names`` fails until the owner has reviewed it, though the entry's
    families stay the same: a shared key, and a change of the entry's ``owners``. A recode of
    the alias, once ruled, clears the shared key, and the entry then records names.json's new
    owners, recodes aside, which the recode's own entry reviews."""
    names = names_with(_share_ambiguous)
    ids = [f["id"] for f in SAMPLE["fonts"]]
    unchecked = owned.match_document(names, ids, SAMPLE["run"]["date"], recodes=SAMPLE_RECODES)
    table = owned.Table.from_document(json.loads(json.dumps(unchecked)))
    for line in (
        "/usr/share/fonts/a.ttf: Sample Sans 06 Black:style=Regular",
        "SampleSans06Black-Bold",
    ):
        assert owned.match(owned.parse(line), table).owned.keys() == {"sample-sans-03"}
    path = write(tmp_path, names)
    gained = f"{path}: {owners_changed('sample-sans-03, sample-sans-06-black')}"
    assert errors_of(path) == [f"{path}: {SHARED_AMBIGUOUS}{recodable('sample-sans-03')}", gained]
    recode = recode_entry("Sample Sans 06 Black", "sample-sans-03", "related")
    assert errors_of(write(tmp_path, names, REVIEWED + recode)) == [gained]
    both = REVIEWED.replace(
        '["sample-sans-06-black"]', '["sample-sans-03", "sample-sans-06-black"]'
    )
    doc = json.loads(matchfile.match_file(SAMPLE, write(tmp_path, names, both + recode)).blob)
    assert doc["amb"] == {"samplesans06black": [doc["ids"].index("sample-sans-06")]}
    assert doc["k"]["samplesans06black"] == f"{doc['ids'].index('sample-sans-03')}x,-"


def test_a_key_in_the_ambiguous_set_that_two_families_aliases_gain_is_unlistable(tmp_path):
    """Sample Sans 03 and 05 both get the rename: the build recodes neither alias, which
    would recode both, so no entry in the reviewed list clears the shared key."""

    def renamed(names: dict[str, Any]) -> None:
        _share_ambiguous(names)
        family(names, "sample-sans-05")["names"].append(
            {"name": "Sample Sans 06 Black", "relation": "rename"}
        )

    path = write(tmp_path, names_with(renamed))
    assert errors_of(path) == [
        f"{path}: key 'samplesans06black' is owned by several families (sample-sans-03, "
        "sample-sans-05 in the catalog, sample-sans-06-black not), in the ambiguous set, where "
        f"a trusted name counts each as owned{UNLISTABLE}",
        f"{path}: {owners_changed('sample-sans-03, sample-sans-05, sample-sans-06-black')}",
    ]


def _bypassed_by_an_alias(names: dict[str, Any]) -> None:
    """Sample Sans 03 gets the rename "Sample Grot" and its patched build "Sample Grot NF",
    which reaches the rename's key and would bypass its recode, as HackGen NF would HackGen's."""
    family(names, "sample-sans-03")["names"] += [
        {"name": "Sample Grot", "relation": "rename"},
        {"name": "Sample Grot NF", "relation": "build", "detail": "nf"},
    ]


def _bypassed_by_its_family_name(names: dict[str, Any]) -> None:
    """Sample Sans 40 is renamed "Sample Sans 11 Bold", its old name "Sample Sans 11" kept as a
    rename: the family name reaches the old name's key, Sample Sans 11's, and no recode
    touches a family's name."""
    forty = family(names, "sample-sans-40")
    forty["family"] = "Sample Sans 11 Bold"
    forty["names"].append({"name": "Sample Sans 11", "relation": "rename"})


def _bypassed_in_two_steps(names: dict[str, Any]) -> None:
    """Sample Serif 04 gets the rename "Sample Kilo", its PostScript name "SampleKilo-Bold",
    which reaches it, and the rename "Sample KiloBold Italic", which reaches the PostScript
    name's key only: a recode of the first needs both others, the second through the first."""
    family(names, "sample-serif-04")["names"] += [
        {"name": "Sample Kilo", "relation": "rename"},
        {"name": "SampleKilo-Bold", "relation": "postscript"},
        {"name": "Sample KiloBold Italic", "relation": "rename"},
    ]


def _reached_by_a_near_match(names: dict[str, Any]) -> None:
    """Sample Serif 09 gets the rename "Sample Lima" and the related name "Sample Lima Bold",
    which reaches it but, a near-match at its own key, can't bypass its recode."""
    family(names, "sample-serif-09")["names"] += [
        {"name": "Sample Lima", "relation": "rename"},
        {"name": "Sample Lima Bold", "relation": "related"},
    ]


def test_recodable_keys_are_those_at_which_the_build_takes_a_recode():
    """``recodable_keys`` decides as the build does, so the messages above can't offer a recode
    the build refuses, or miss one it takes, or the recodes it needs with it. On the sample with
    planted aliases (at a key that two families' aliases hold, at the family's own name's key,
    of a family outside the catalog, at another family's name, bypassed by another alias, by
    a name that another alias bypasses in turn, or by the family's own name, and reached by a
    near-match alias, which bypasses nothing), every set of each family's aliases with an owned
    relation is tried: a key is recodable when some set the build takes holds it
    (``refused_recodes`` and ``owned`` refuse none), and ``also`` is what the smallest such set
    adds."""

    def plant(names: dict[str, Any]) -> None:
        _share_aliases(names)
        family(names, "sample-sans-01")["names"].append(
            {"name": "SampleSans01", "relation": "rename"}
        )
        family(names, "sample-held-41")["names"].append(
            {"name": "Sample Held 41 Gen", "relation": "build", "detail": ""}
        )
        family(names, "sample-sans-06")["names"].append(
            {"name": "Sample Sans 08", "relation": "rename"}
        )
        _bypassed_by_an_alias(names)
        _bypassed_by_its_family_name(names)
        _bypassed_in_two_steps(names)
        _reached_by_a_near_match(names)

    names = names_with(plant)
    ids = [f["id"] for f in SAMPLE["fonts"]]
    want: dict[str, matchfile.Recodable] = {}
    for fam in names["families"]:
        aliases: dict[str, str] = {}
        for item in fam["names"]:
            if item["relation"] in owned.OWNED_RELATIONS:
                aliases.setdefault(match_key(item["name"]), item["name"])
        taken: list[set[str]] = []
        for size in range(1, len(aliases) + 1):
            for keys in itertools.combinations(sorted(aliases), size):
                entries = {
                    key: matchfile.Recode(aliases[key], fam["id"], "related", "synthetic")
                    for key in keys
                }
                if matchfile.refused_recodes(names, matchfile.Reviewed(recodes=entries)):
                    continue
                try:
                    owned.check_document(
                        names, ids, recodes={aliases[key]: "related" for key in keys}
                    )
                except owned.RecodeError:
                    continue
                taken.append(set(keys))
        for key in aliases:
            sets = [keys for keys in taken if key in keys]
            if sets:
                smallest = min(sets, key=len)
                also = tuple(aliases[other] for other in sorted(smallest - {key}))
                want[key] = matchfile.Recodable(fam["id"], also)
    assert matchfile.recodable_keys(names) == want
    assert want["samplesans08"] == matchfile.Recodable("sample-sans-06")
    assert want["samplemono20gen"] == matchfile.Recodable("sample-mono-20")
    assert want["samplegrot"] == matchfile.Recodable("sample-sans-03", ("Sample Grot NF",))
    assert want["samplegrotnf"] == matchfile.Recodable("sample-sans-03")
    assert want["samplekilo"] == matchfile.Recodable(
        "sample-serif-04", ("SampleKilo-Bold", "Sample KiloBold Italic")
    )
    assert want["samplelima"] == matchfile.Recodable("sample-serif-09")
    assert (
        not {"samplesansclassic", "samplesans01", "sampleheld41gen", "samplesans11"} & want.keys()
    )


def _meet_at_a_bypassed_alias(names: dict[str, Any]) -> None:
    """``_bypassed_by_an_alias``, and Sample Sans 01 Tight, made distinct from Sample Sans 03,
    gets a name that reaches "Sample Grot"."""
    _bypassed_by_an_alias(names)
    tight = family(names, "sample-sans-01-tight")
    tight["names"].append({"name": "Sample Grot Bold", "relation": "related"})
    tight["distinct_from"] = sorted({*tight["distinct_from"], "sample-sans-03"})


def _share_a_bypassed_alias(names: dict[str, Any]) -> None:
    """``_share``, and Sample Sans 03 also gets "Sample Sans 01 NF", a patched build that
    reaches the shared key and would bypass a recode of the rename there."""
    _share(names)
    family(names, "sample-sans-03")["names"].append(
        {"name": "Sample Sans 01 NF", "relation": "build", "detail": "nf"}
    )


@pytest.mark.parametrize(
    ("mutate", "problem", "first", "second"),
    [
        pytest.param(
            _meet_at_a_bypassed_alias,
            "sample-sans-01-tight: its name 'Sample Grot Bold' reaches sample-sans-03 at key "
            "'samplegrot', and the two are distinct_from each other",
            "Sample Grot",
            "Sample Grot NF",
            id="meeting",
        ),
        pytest.param(
            _share_a_bypassed_alias,
            "key 'samplesans01' is owned by several catalog families (sample-sans-01, "
            "sample-sans-03), outside the ambiguous set",
            "Sample Sans 01",
            "Sample Sans 01 NF",
            id="shared-key",
        ),
    ],
)
def test_a_collision_whose_recode_another_name_bypasses_names_both_recodes(
    tmp_path, mutate, problem, first, second
):
    """The recode that ends the problem would be bypassed by another name of the family, so the
    message names that name's recode too, before and after the first is listed, and with both
    listed the build passes: one round of the refresh's review, not two."""
    names = names_with(mutate)
    listed = every_ambiguous_name(names) + RECODE_20
    ending = recodable("sample-sans-03", second)
    path = write(tmp_path, names, listed)
    assert [e for e in errors_of(path) if "may be a weight of" not in e] == [
        f"{path}: {problem}{ending}"
    ]
    key = match_key(first)
    path = write(tmp_path, names, listed + recode_entry(first, "sample-sans-03", "related"))
    assert errors_of(path) == [
        f"{tmp_path / 'names.reviewed.toml'}: recode {key!r}: sample-sans-03's name {second!r} "
        "reaches it and still counts as owned; recode that name too",
        f"{path}: {problem}{ending}",
    ]
    both = listed + "".join(recode_entry(n, "sample-sans-03", "related") for n in (first, second))
    doc = json.loads(matchfile.match_file(SAMPLE, write(tmp_path, names, both)).blob)
    assert doc["k"][match_key(second)] == f"{doc['ids'].index('sample-sans-03')}x"


def test_a_shared_key_whose_recode_its_family_name_bypasses_is_unlistable(tmp_path):
    """Sample Sans 40, renamed "Sample Sans 11 Bold" in both files, shares its old name's key
    with Sample Sans 11. A recode of the old name would be bypassed by the family name, which
    no recode touches, so the message offers none, and a recode listed anyway is refused
    saying why, never asking for a recode of the family name."""
    names = names_with(_bypassed_by_its_family_name)
    doc = copy.deepcopy(SAMPLE)
    next(f for f in doc["fonts"] if f["id"] == "sample-sans-40")["family"] = "Sample Sans 11 Bold"
    listed = every_ambiguous_name(names) + RECODE_20
    shared = (
        "key 'samplesans11' is owned by several catalog families (sample-sans-11, "
        f"sample-sans-40), outside the ambiguous set{UNLISTABLE}"
    )
    path = write(tmp_path, names, listed)
    assert errors_of(path, doc) == [f"{path}: {shared}"]
    recode = recode_entry("Sample Sans 11", "sample-sans-40", "related")
    path = write(tmp_path, names, listed + recode)
    assert errors_of(path, doc) == [
        f"{tmp_path / 'names.reviewed.toml'}: recode 'samplesans11': sample-sans-40's name "
        "'Sample Sans 11 Bold' reaches it and still counts as owned, at its family name's key, "
        "which no recode touches",
        f"{path}: {shared}",
    ]


def test_a_meeting_at_a_family_outside_the_catalog_passes_changing_nothing(tmp_path):
    """Two families outside the catalog made distinct_from each other, a name of one reaching
    the other: the other is coded ``-`` and never owned, so the name hides no listed font, and
    the build makes the match file it makes without the pair."""

    def apart(names: dict[str, Any]) -> None:
        black = family(names, "sample-sans-06-black")
        black["names"].append({"name": "Sample Held 41 Bold", "relation": "related"})
        black["distinct_from"] = ["sample-held-41"]
        family(names, "sample-held-41")["distinct_from"] = ["sample-sans-06-black"]

    names = names_with(apart)
    made = json.loads(matchfile.match_file(SAMPLE, write(tmp_path, names)).blob)
    for fid in ("sample-sans-06-black", "sample-held-41"):
        family(names, fid)["distinct_from"] = []
    plain = json.loads(matchfile.match_file(SAMPLE, write(tmp_path, names)).blob)
    assert made["names_sha256"] != plain["names_sha256"]
    assert {**made, "names_sha256": ""} == {**plain, "names_sha256": ""}


@pytest.mark.parametrize("reviewed", [None, "", RECODE_20])
def test_an_ambiguous_name_nobody_reviewed_is_refused(tmp_path, reviewed):
    """A missing or empty reviewed list reviews nothing: the build fails closed."""
    path = write(tmp_path, reviewed=reviewed)
    assert errors_of(path) == [
        f"{path}: 'Sample Sans 06 Black' may be a weight of sample-sans-06, its key "
        "'samplesans06black' owned by sample-sans-06-black: an ambiguous name nobody has "
        f"reviewed; once the owner has, list it in {tmp_path / 'names.reviewed.toml'}"
    ]


def test_a_reviewed_ambiguous_name_whose_families_changed_is_refused(tmp_path):
    path = write(tmp_path, reviewed=REVIEWED.replace('["sample-sans-06"]', '["sample-sans-07"]'))
    assert errors_of(path) == [
        f"{path}: 'Sample Sans 06 Black' may be a weight of sample-sans-06, but was reviewed "
        "(synthetic) as one of sample-sans-07; the owner reviews the change"
    ]


def _without(names: dict[str, Any], family_id: str) -> None:
    names["families"] = [f for f in names["families"] if f["id"] != family_id]


def _owner_swapped(names: dict[str, Any]) -> None:
    """Sample Sans 06 Black, a family outside the catalog, leaves names.json in the run in
    which Sample Sans 03 gets its name as a rename: one owner of the reviewed ambiguous key for
    another, its families the same, as Inter getting "Bungee Hairline" while Bungee Hairline
    leaves would be."""
    _without(names, "sample-sans-06-black")
    _share_ambiguous(names)


def _owner_gone(names: dict[str, Any]) -> None:
    """Sample Sans 06 Black leaves, and Sample Sans 03 gets its name as a related one: the key
    is still ambiguous, and no family owns it."""
    _without(names, "sample-sans-06-black")
    family(names, "sample-sans-03")["names"].append(
        {"name": "Sample Sans 06 Black", "relation": "related"}
    )


def owners_changed(now: str) -> str:
    return (
        f"the key 'samplesans06black' of 'Sample Sans 06 Black' is owned by {now}, but was "
        "reviewed (synthetic) as owned by sample-sans-06-black; the owner reviews the change"
    )


@pytest.mark.parametrize(
    ("mutate", "now"),
    [
        pytest.param(_owner_swapped, "sample-sans-03", id="one-for-one"),
        pytest.param(_owner_gone, "no family", id="none-left"),
    ],
)
def test_a_reviewed_ambiguous_key_whose_owners_changed_is_refused(tmp_path, mutate, now):
    """An entry records the families that own its key (``owners``), so a change of them fails
    as a changed name, as ``inventory_ambiguous_names`` asks ("on any new, changed or dropped
    name until the owner has reviewed it"), where the families alone would pass it: a trusted
    "Sample Sans 06 Black" would count Sample Sans 03 as owned. No other line names the
    change, since no two families share the key."""
    path = write(tmp_path, names_with(mutate))
    assert errors_of(path) == [f"{path}: {owners_changed(now)}"]


def test_a_shared_ambiguous_key_that_lost_its_reviewed_owner_names_both_changes(tmp_path):
    """Sample Sans 03 and 05 get the name as Sample Sans 06 Black leaves: the shared key, which
    no recode clears, and the owners' change, so one round of review sees both."""

    def renamed(names: dict[str, Any]) -> None:
        _owner_swapped(names)
        family(names, "sample-sans-05")["names"].append(
            {"name": "Sample Sans 06 Black", "relation": "rename"}
        )

    path = write(tmp_path, names_with(renamed))
    assert errors_of(path) == [
        f"{path}: key 'samplesans06black' is owned by several catalog families (sample-sans-03, "
        f"sample-sans-05), in the ambiguous set, where a trusted name counts each as owned"
        f"{UNLISTABLE}",
        f"{path}: {owners_changed('sample-sans-03, sample-sans-05')}",
    ]


def test_a_one_for_one_change_of_a_committed_ambiguous_key_s_owner_is_refused(tmp_path):
    """On the committed files: the first reviewed ambiguous name that is a family of its own
    outside the catalog (Bungee Hairline) leaves names.json in the run in which a catalog family
    gets its name as a rename. Without the entry's ``owners`` this built, and a trusted name
    then counted that family as owned. The entry and the family are read from the files, so a
    refresh needs no edit here."""
    doc = data.load(COMMITTED_DATA)
    names_path = matchfile.default_names_path(COMMITTED_DATA)
    names = json.loads(names_path.read_text(encoding="utf-8"))
    reviewed_path = matchfile.default_reviewed_path(names_path)
    reviewed, _ = matchfile.load_reviewed(reviewed_path)
    by_name = {match_key(f["family"]): f for f in names["families"] if not f["in_catalog"]}
    entry = next(
        (
            a
            for key, a in sorted(reviewed.ambiguous.items())
            if key in by_name and a.owners == (by_name[key]["id"],)
        ),
        None,
    )
    if entry is None:
        pytest.skip("no reviewed ambiguous name is a family of its own outside the catalog")
    (owner,) = entry.owners
    taker = next(
        f
        for f in sorted(names["families"], key=lambda f: f["id"])
        if f["in_catalog"] and f["id"] not in entry.families and not f["distinct_from"]
    )
    _without(names, owner)
    taker["names"].append({"name": entry.name, "relation": "rename"})
    path = write(tmp_path, names, reviewed_path.read_text(encoding="utf-8"))
    assert errors_of(path, doc) == [
        f"{path}: the key {match_key(entry.name)!r} of {entry.name!r} is owned by "
        f"{taker['id']}, but was reviewed ({entry.ruling}) as owned by {owner}; the owner "
        "reviews the change"
    ]


def test_a_reviewed_name_that_is_no_longer_ambiguous_is_refused(tmp_path):
    stale = (
        REVIEWED + '\n[[ambiguous]]\nname = "Sample Serif 04 Light"\n'
        'families = ["sample-serif-04"]\nowners = []\nruling = "synthetic"\n'
    )
    path = write(tmp_path, reviewed=stale)
    assert errors_of(path) == [
        f"{path}: 'Sample Serif 04 Light' is reviewed as ambiguous in "
        f"{tmp_path / 'names.reviewed.toml'}, but no longer is; once the owner has reviewed "
        "the drop, remove it"
    ]


def test_a_reviewed_name_whose_other_family_left_the_catalog_is_one_question(tmp_path):
    """As when Archivo leaves the catalog and Archivo Black stays: the name drops out of the
    ambiguous set only because the family it may be a weight of left, so removing its entry
    would leave the collision that no entry clears; the build says the two go together."""
    listed = tmp_path / "names.reviewed.toml"
    was = (
        '[[ambiguous]]\nname = "Sample Held 41 Bold"\nfamilies = ["sample-held-41"]\n'
        'owners = ["sample-sans-03"]\nruling = "synthetic"\n'
    )
    path = write(tmp_path, names_with(_strip_out), REVIEWED + was)
    assert errors_of(path) == [
        f"{path}: 'Sample Held 41 Bold' is reviewed as ambiguous in {listed}, but now strips "
        "only to a family outside the catalog; the owner reviews it with that collision, below",
        f"{path}: 'Sample Held 41 Bold', a catalog name, strips to the key 'sampleheld41' of "
        f"sample-held-41, outside the catalog, which the ambiguous set can't hold{UNLISTABLE}",
    ]


# ---------------------------------------------------------------------------- the reviewed list


def test_the_sample_s_reviewed_list_loads():
    reviewed, blob = matchfile.load_reviewed(REVIEWED_PATH)
    assert blob == REVIEWED_PATH.read_bytes()
    assert reviewed.ambiguous == {
        "samplesans06black": matchfile.Ambiguous(
            "Sample Sans 06 Black", ("sample-sans-06",), ("sample-sans-06-black",), "synthetic"
        )
    }
    assert reviewed.recodes == {
        "samplemono20gen": matchfile.Recode(
            "Sample Mono 20 Gen", "sample-mono-20", "related", "synthetic"
        )
    }
    assert reviewed.relations() == SAMPLE_RECODES
    assert reviewed.rulings() == {"synthetic"}


def test_a_missing_reviewed_list_is_an_empty_one(tmp_path):
    assert matchfile.load_reviewed(tmp_path / "none.toml") == (matchfile.Reviewed(), b"")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        pytest.param("[[ambiguous]\n", "Expected ']]'", id="bad-toml"),
        pytest.param(
            "a = " + "[" * 5_000 + "]" * 5_000 + "\n", "nested too deeply to read", id="deep-array"
        ),
        pytest.param(
            "a = " + "{b = " * 5_000 + "1" + "}" * 5_000 + "\n",
            "nested too deeply to read",
            id="deep-inline-table",
        ),
        pytest.param("version = 1\n" + REVIEWED, "unknown key 'version'", id="unknown-key"),
        pytest.param(
            REVIEWED.replace('ruling = "synthetic"\n', 'ruling = "synthetic"\nnote = "x"\n', 1),
            "ambiguous[0]: needs exactly families, name, owners and ruling",
            id="extra-field",
        ),
        pytest.param(
            REVIEWED.replace('family = "sample-mono-20"\n', ""),
            "recode[0]: needs exactly family, name, relation and ruling",
            id="recode-without-family",
        ),
        pytest.param(
            REVIEWED.replace('["sample-sans-06"]', '["sample-sans-07", "sample-sans-06"]'),
            "ambiguous[0]: families must be catalog ids, sorted, without repeats",
            id="unsorted-families",
        ),
        pytest.param(
            REVIEWED.replace('families = ["sample-sans-06"]', "families = []"),
            "ambiguous[0]: families must be catalog ids, sorted, without repeats",
            id="no-families",
        ),
        pytest.param(
            REVIEWED + AMBIGUOUS_06.replace('"Sample Sans 06 Black"', '"SampleSans06Black"'),
            "ambiguous[1]: 'SampleSans06Black' has the key of 'Sample Sans 06 Black'",
            id="repeated-key",
        ),
        pytest.param(
            REVIEWED.replace('ruling = "synthetic"', 'ruling = ""', 1),
            "ambiguous[0]: the name and the ruling must be non-empty strings",
            id="empty-ruling",
        ),
        pytest.param(
            REVIEWED.replace('name = "Sample Sans 06 Black"', 'name = "--"'),
            "ambiguous[0]: the name and the ruling must be non-empty strings",
            id="name-without-a-key",
        ),
        pytest.param('ambiguous = "x"\n', "ambiguous is not an array of tables", id="not-an-array"),
        pytest.param(
            'ambiguous = ["x"]\n',
            "ambiguous[0]: needs exactly families, name, owners and ruling",
            id="array-of-strings",
        ),
        pytest.param(
            REVIEWED.replace('name = "Sample Sans 06 Black"', "name = 6"),
            "ambiguous[0]: the name and the ruling must be non-empty strings",
            id="number-name",
        ),
        pytest.param(
            REVIEWED.replace('families = ["sample-sans-06"]', "families = [6]"),
            "ambiguous[0]: families must be catalog ids, sorted, without repeats",
            id="number-family-id",
        ),
        pytest.param(
            REVIEWED.replace('owners = ["sample-sans-06-black"]\n', ""),
            "ambiguous[0]: needs exactly families, name, owners and ruling",
            id="no-owners-key",
        ),
        pytest.param(
            REVIEWED.replace('["sample-sans-06-black"]', '["sample-sans-06-black", "sample-a"]'),
            "ambiguous[0]: owners must be family ids, sorted, without repeats",
            id="unsorted-owners",
        ),
        pytest.param(
            REVIEWED.replace('["sample-sans-06-black"]', '["sample-a", "sample-a"]'),
            "ambiguous[0]: owners must be family ids, sorted, without repeats",
            id="repeated-owner",
        ),
        pytest.param(
            REVIEWED.replace('["sample-sans-06-black"]', "[6]"),
            "ambiguous[0]: owners must be family ids, sorted, without repeats",
            id="number-owner",
        ),
        pytest.param(
            REVIEWED.replace('["sample-sans-06-black"]', '"sample-sans-06-black"'),
            "ambiguous[0]: owners must be family ids, sorted, without repeats",
            id="owners-not-a-list",
        ),
        pytest.param(
            REVIEWED.replace('relation = "related"', 'relation = "build"'),
            "recode[0]: relation 'build' is not one of ['bundle', 'related', 'sibling']: "
            "a recode can only make an alias a near-match",
            id="owned-relation",
        ),
        pytest.param(
            REVIEWED.replace('relation = "related"', "relation = []"),
            "recode[0]: relation [] is not one of",
            id="list-relation",
        ),
        pytest.param(
            REVIEWED.replace('family = "sample-mono-20"', "family = 20"),
            "recode[0]: the family must be a family id",
            id="number-family",
        ),
    ],
)
def test_a_malformed_reviewed_list_is_refused_saying_where(tmp_path, text, problem):
    path = write(tmp_path, reviewed=text)
    (error,) = errors_of(path)
    assert error.startswith(f"{tmp_path / 'names.reviewed.toml'}: ")
    assert problem in error


def test_a_recode_must_name_an_alias_of_the_family_it_names(tmp_path):
    path = write(tmp_path, reviewed=REVIEWED.replace('"sample-mono-20"', '"sample-mono-23"'))
    assert errors_of(path) == [
        f"{tmp_path / 'names.reviewed.toml'}: 'Sample Mono 20 Gen' is reviewed as an alias of "
        "sample-mono-23 (synthetic), but names.json's aliases at its key 'samplemono20gen' are "
        "of sample-mono-20"
    ]


def test_a_recode_whose_alias_another_family_also_holds_is_refused(tmp_path):
    """A recode works on every alias row at its key, so the review of sample-mono-20's alias
    would silently recode sample-mono-02's too: the build refuses it, and the check, run
    without it, finds the key that two catalog families now own."""

    def shared(names: dict[str, Any]) -> None:
        family(names, "sample-mono-02")["names"].append(
            {"name": "Sample Mono 20 Gen", "relation": "build", "detail": ""}
        )

    path = write(tmp_path, names_with(shared))
    assert errors_of(path) == [
        f"{tmp_path / 'names.reviewed.toml'}: 'Sample Mono 20 Gen' is reviewed as an alias of "
        "sample-mono-20 (synthetic), but names.json's aliases at its key 'samplemono20gen' are "
        "of sample-mono-02, sample-mono-20",
        f"{path}: key 'samplemono20gen' is owned by several catalog families "
        f"(sample-mono-02, sample-mono-20), outside the ambiguous set{UNLISTABLE}",
    ]


def test_a_recode_whose_alias_is_gone_is_refused(tmp_path):
    """A refresh that drops or moves the alias leaves a stale recode, which recodes nothing."""

    def drop(names: dict[str, Any]) -> None:
        family(names, "sample-mono-20")["names"] = []

    path = write(tmp_path, names_with(drop))
    assert errors_of(path) == [
        f"{tmp_path / 'names.reviewed.toml'}: 'Sample Mono 20 Gen' is reviewed as an alias of "
        "sample-mono-20 (synthetic), but names.json's aliases at its key 'samplemono20gen' are "
        "of none"
    ]


@pytest.mark.parametrize(("relation", "code"), [("related", "x"), ("sibling", "s")])
def test_a_recode_whose_alias_is_already_a_near_match_is_refused(tmp_path, relation, code):
    """A refresh that makes the alias a near-match itself leaves a recode that changes
    nothing, a stale entry too."""

    def near(names: dict[str, Any]) -> None:
        family(names, "sample-mono-20")["names"] = [
            {"name": "Sample Mono 20 Gen", "relation": relation}
        ]

    path = write(tmp_path, names_with(near))
    assert errors_of(path) == [
        f"{tmp_path / 'names.reviewed.toml'}: 'Sample Mono 20 Gen' is reviewed (synthetic) as an "
        "owned alias of sample-mono-20 that the hopper makes related, but names.json's aliases "
        f"at its key 'samplemono20gen' are already {relation}, so the recode changes nothing; "
        "once the owner has reviewed the change, remove it"
    ]
    (tmp_path / "names.reviewed.toml").write_text(AMBIGUOUS_06, encoding="utf-8")
    doc = json.loads(matchfile.match_file(SAMPLE, path).blob)
    assert doc["k"]["samplemono20gen"] == f"{doc['ids'].index('sample-mono-20')}{code}"


def test_a_recode_of_a_family_outside_the_catalog_is_refused(tmp_path):
    """A refresh that takes a recoded family out of the catalog, while names.json keeps it
    with the alias row, leaves a recode that changes nothing: the match file codes a family
    outside the catalog ``-`` whatever its alias's relation. A stale entry too."""

    def held(names: dict[str, Any]) -> None:
        family(names, "sample-held-41")["names"].append(
            {"name": "Sample Held 41 Gen", "relation": "build", "detail": ""}
        )

    recode = RECODE_20.replace("Sample Mono 20 Gen", "Sample Held 41 Gen").replace(
        "sample-mono-20", "sample-held-41"
    )
    path = write(tmp_path, names_with(held), REVIEWED + recode)
    assert errors_of(path) == [
        f"{tmp_path / 'names.reviewed.toml'}: 'Sample Held 41 Gen' is reviewed (synthetic) as an "
        "alias of sample-held-41, which names.json has outside the catalog, so the recode "
        "changes nothing in the match file; once the owner has reviewed the change, remove it"
    ]
    without = matchfile.match_file(SAMPLE, write(tmp_path, names_with(held)))
    assert json.loads(without.blob)["k"]["sampleheld41gen"] == "-"


def test_a_recode_that_another_alias_bypasses_is_refused(tmp_path):
    """A patched build of the recoded alias would own the family again (``owned``'s check):
    the reviewed list must recode it too (``hopper_hackgen_related``: "HackGen NF")."""

    def patched(names: dict[str, Any]) -> None:
        family(names, "sample-mono-20")["names"].append(
            {"name": "Sample Mono 20 Gen NF", "relation": "build", "detail": "nf"}
        )

    path = write(tmp_path, names_with(patched))
    (error,) = errors_of(path)
    assert error.startswith(f"{tmp_path / 'names.reviewed.toml'}: recode 'samplemono20gen': ")
    assert "'Sample Mono 20 Gen NF' reaches it and still counts as owned" in error
    both = REVIEWED + RECODE_20.replace("Sample Mono 20 Gen", "Sample Mono 20 Gen NF")
    doc = json.loads(matchfile.match_file(SAMPLE, write(tmp_path, names_with(patched), both)).blob)
    assert doc["k"]["samplemono20gennf"] == f"{doc['ids'].index('sample-mono-20')}x"


def test_a_recode_of_any_owned_relation_is_taken(tmp_path):
    """A PostScript name and a rename count a family as owned (``owned.OWNED_RELATIONS``), so
    the build takes a recode of either, as of a build."""
    recodes = RECODE_20.replace("Sample Mono 20 Gen", "SampleSans01-Regular").replace(
        "sample-mono-20", "sample-sans-01"
    ).replace("related", "sibling") + RECODE_20.replace(
        "Sample Mono 20 Gen", "Sample Sans Classic"
    ).replace("sample-mono-20", "sample-sans-05").replace("related", "bundle")
    doc = json.loads(
        matchfile.match_file(SAMPLE, write(tmp_path, reviewed=REVIEWED + recodes)).blob
    )
    assert doc["k"]["samplesans01regular"] == f"{doc['ids'].index('sample-sans-01')}s"
    assert doc["k"]["samplesansclassic"] == f"{doc['ids'].index('sample-sans-05')}u"
    assert doc["k"]["samplemono20gen"] == f"{doc['ids'].index('sample-mono-20')}x"


# ---------------------------------------------------------------------- every problem in one pass


def _unreviewed_and_stale(names: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The sample's names with the recoded alias gone, and a reviewed list that misses
    Sample Sans 06 Black and lists Sample Serif 04 Light, which isn't ambiguous."""
    family(names, "sample-mono-20")["names"] = [
        n for n in family(names, "sample-mono-20")["names"] if n["name"] != "Sample Mono 20 Gen"
    ]
    reviewed = (
        '[[ambiguous]]\nname = "Sample Serif 04 Light"\nfamilies = ["sample-serif-04"]\n'
        'owners = []\nruling = "synthetic"\n\n' + RECODE_20
    )
    return names, reviewed


def test_a_refused_recode_and_the_check_s_findings_are_listed_together(tmp_path):
    """A refresh that moves a recoded alias and changes the ambiguous set fails once, naming
    all of it, so the owner is asked once (ops/MONTHLY.md step 5): the check runs without the
    refused recode."""
    names, reviewed = _unreviewed_and_stale(copy.deepcopy(NAMES))
    path = write(tmp_path, names, reviewed)
    listed = tmp_path / "names.reviewed.toml"
    assert errors_of(path) == [
        f"{listed}: 'Sample Mono 20 Gen' is reviewed as an alias of sample-mono-20 (synthetic), "
        "but names.json's aliases at its key 'samplemono20gen' are of none",
        f"{path}: 'Sample Sans 06 Black' may be a weight of sample-sans-06, its key "
        "'samplesans06black' owned by sample-sans-06-black: an ambiguous name nobody has "
        f"reviewed; once the owner has, list it in {listed}",
        f"{path}: 'Sample Serif 04 Light' is reviewed as ambiguous in {listed}, but no longer "
        "is; once the owner has reviewed the drop, remove it",
    ]


def test_a_bypassed_recode_and_the_check_s_findings_are_listed_together(tmp_path):
    """``owned.RecodeError`` names the bypassed recode, so the check runs on without it."""

    def patched(names: dict[str, Any]) -> None:
        family(names, "sample-mono-20")["names"].append(
            {"name": "Sample Mono 20 Gen NF", "relation": "build", "detail": "nf"}
        )

    path = write(tmp_path, names_with(patched), RECODE_20)
    listed = tmp_path / "names.reviewed.toml"
    bypass, unreviewed = errors_of(path)
    assert bypass.startswith(f"{listed}: recode 'samplemono20gen': ")
    assert unreviewed.startswith(f"{path}: 'Sample Sans 06 Black' may be a weight of ")


def test_every_name_that_bypasses_a_recode_is_named_in_one_run(tmp_path):
    """Two patched builds of the recoded alias, as HackGen NF and HackGen Nerd Font would be
    of Hack: the build names both at once, so one round of review clears them
    (ops/MONTHLY.md step 5)."""

    def patched(names: dict[str, Any]) -> None:
        family(names, "sample-mono-20")["names"] += [
            {"name": "Sample Mono 20 Gen NF", "relation": "build", "detail": "nf"},
            {"name": "Sample Mono 20 Gen Nerd Font", "relation": "build", "detail": "nerd"},
        ]

    names = names_with(patched)
    listed = tmp_path / "names.reviewed.toml"
    path = write(tmp_path, names)
    assert errors_of(path) == [
        f"{listed}: recode 'samplemono20gen': sample-mono-20's name '{name}' reaches it and "
        "still counts as owned; recode that name too"
        for name in ("Sample Mono 20 Gen NF", "Sample Mono 20 Gen Nerd Font")
    ]
    every = REVIEWED + "".join(
        recode_entry(name, "sample-mono-20", "related")
        for name in ("Sample Mono 20 Gen NF", "Sample Mono 20 Gen Nerd Font")
    )
    doc = json.loads(matchfile.match_file(SAMPLE, write(tmp_path, names, every)).blob)
    at = doc["ids"].index("sample-mono-20")
    assert [doc["k"][key] for key in ("samplemono20gennf", "samplemono20gennerdfont")] == [
        f"{at}x",
        f"{at}x",
    ]


def test_a_bypass_at_a_key_another_family_s_alias_holds_asks_for_no_recode(tmp_path):
    """Sample Mono 20's patched build "Sample Mono 20 Gen NF" bypasses the recode of "Sample Mono
    20 Gen", and Sample Mono 02 has the same name as a related one, as Roboto Mono could have
    "HackGen NF": the build refuses a recode at a key two families' aliases hold, which would
    recode both, so the message says so instead of asking for one, before and after one is
    listed anyway. No shared key comes of it (Sample Mono 02's code there is a near-match), so
    no other line says what clears it either."""

    def patched(names: dict[str, Any]) -> None:
        family(names, "sample-mono-20")["names"].append(
            {"name": "Sample Mono 20 Gen NF", "relation": "build", "detail": "nf"}
        )
        family(names, "sample-mono-02")["names"].append(
            {"name": "Sample Mono 20 Gen NF", "relation": "related"}
        )

    names = names_with(patched)
    listed = tmp_path / "names.reviewed.toml"
    bypass = (
        f"{listed}: recode 'samplemono20gen': sample-mono-20's name 'Sample Mono 20 Gen NF' "
        "reaches it and still counts as owned, at a key that an alias of sample-mono-02 also "
        "has, which the build won't recode"
    )
    assert errors_of(write(tmp_path, names)) == [bypass]
    anyway = REVIEWED + recode_entry("Sample Mono 20 Gen NF", "sample-mono-20", "related")
    assert errors_of(write(tmp_path, names, anyway)) == [
        f"{listed}: 'Sample Mono 20 Gen NF' is reviewed as an alias of sample-mono-20 "
        "(synthetic), but names.json's aliases at its key 'samplemono20gennf' are of "
        "sample-mono-02, sample-mono-20",
        bypass,
    ]


def test_a_recode_its_family_name_refuses_is_named_once_for_that(tmp_path):
    """Sample Mono 20's rename "SampleMono20", listed as a recode, has its family name's key,
    which no recode touches, so the build refuses it for that alone: the patched build "Sample
    Mono 20 NF", which reaches the key too, is not asked for as well, as no recode of it could
    save this one (and a recode of it, once listed, would make a valid owned alias a near-match
    for nothing)."""

    def renamed(names: dict[str, Any]) -> None:
        family(names, "sample-mono-20")["names"] += [
            {"name": "SampleMono20", "relation": "rename"},
            {"name": "Sample Mono 20 NF", "relation": "build", "detail": "nf"},
        ]

    path = write(
        tmp_path,
        names_with(renamed),
        REVIEWED + recode_entry("SampleMono20", "sample-mono-20", "related"),
    )
    assert errors_of(path) == [
        f"{tmp_path / 'names.reviewed.toml'}: recode 'samplemono20': the family name of "
        "sample-mono-20 has that key"
    ]


def test_a_bypass_that_only_a_dropped_listed_recode_makes_is_not_asked_for(tmp_path):
    """Sample Sans 03 gets the rename "Sample Sans 01", its PostScript name "SampleSans01-Bold"
    and the rename "Sample Sans01Bold Italic", which reaches the PostScript name's key only;
    the first two are listed. The third bypasses the second's recode, which the build leaves
    out; the second then bypasses the first's, but its recode is listed already, so the build
    doesn't ask for it again: listing the third clears both."""

    def renamed(names: dict[str, Any]) -> None:
        family(names, "sample-sans-03")["names"] += [
            {"name": "Sample Sans 01", "relation": "rename"},
            {"name": "SampleSans01-Bold", "relation": "postscript"},
            {"name": "Sample Sans01Bold Italic", "relation": "rename"},
        ]

    names = names_with(renamed)
    listed = tmp_path / "names.reviewed.toml"
    recodes = "".join(
        recode_entry(name, "sample-sans-03", "related")
        for name in ("Sample Sans 01", "SampleSans01-Bold")
    )
    path = write(tmp_path, names, every_ambiguous_name(names) + RECODE_20 + recodes)
    assert [e for e in errors_of(path) if e.startswith(f"{listed}: ")] == [
        f"{listed}: recode 'samplesans01bold': sample-sans-03's name 'Sample Sans01Bold Italic' "
        "reaches it and still counts as owned; recode that name too"
    ]
    every = recodes + recode_entry("Sample Sans01Bold Italic", "sample-sans-03", "related")
    path = write(tmp_path, names, every_ambiguous_name(names) + RECODE_20 + every)
    doc = json.loads(matchfile.match_file(SAMPLE, path).blob)
    assert doc["k"]["samplesans01"] == (
        f"{doc['ids'].index('sample-sans-01')},{doc['ids'].index('sample-sans-03')}x"
    )


def test_a_names_file_and_a_reviewed_list_with_problems_are_listed_together(tmp_path):
    names = names_with(lambda n: n["run"].update(date="2026-01-02"))
    path = write(tmp_path, names, "[[ambiguous]]\nname = 1\n")
    names_error, reviewed_error = errors_of(path)
    assert names_error.startswith(f"{path}: run.date is 2026-01-02")
    assert reviewed_error.startswith(f"{tmp_path / 'names.reviewed.toml'}: ambiguous[0]: ")


# ---------------------------------------------------------------------- the committed files


def test_the_committed_names_give_a_match_file_within_budget():
    """build/names.json with build/names.reviewed.toml, the file within ``budgets.MATCH_MAX``:
    its ``amb`` is the reviewed ambiguous names, and each reviewed recode's alias has the code
    its relation gives (HackGen only related to Hack, ``hopper_hackgen_related``). Both are read
    from the list, so a refresh that changes either edits the list, not this test
    (ops/MONTHLY.md step 5)."""
    doc = data.load(COMMITTED_DATA)
    names_path = matchfile.default_names_path(COMMITTED_DATA)
    made = matchfile.match_file(doc, names_path)
    match = json.loads(made.blob)
    assert budgets.gzip_size(made.blob) <= budgets.MATCH_MAX
    reviewed, _ = matchfile.load_reviewed(matchfile.default_reviewed_path(names_path))
    amb = {key: [match["ids"][n] for n in at] for key, at in match["amb"].items()}
    assert amb == {key: list(a.families) for key, a in reviewed.ambiguous.items()}
    letters = {"related": "x", "sibling": "s", "bundle": "u"}
    assert letters.keys() == owned.RECODE_RELATIONS
    for key, recode in reviewed.recodes.items():
        code = f"{match['ids'].index(recode.family)}{letters[recode.relation]}"
        assert code in match["k"][key].split(",")


def test_the_large_catalog_needs_no_reviewed_list(tmp_path):
    from tests.fixtures import make_large_catalog

    catalog = make_large_catalog.write(tmp_path, fonts=80)
    made = matchfile.match_file(data.load(catalog), matchfile.default_names_path(catalog))
    assert not (tmp_path / "names.reviewed.toml").exists()
    assert made.reviewed_sha256 == hashlib.sha256(b"").hexdigest()
    assert json.loads(made.blob)["amb"] == {}
