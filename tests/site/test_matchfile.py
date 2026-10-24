"""``tff_site.matchfile``: the hopper's match file, made at build time from ``names.json`` and
the catalog (the owner's ruling of 2026-10-22, ``inventory_match_data_site_build``), with the
reviewed list beside the names file (``hopper_hackgen_related``, 2026-10-23, for its recodes).

Offline, no browser. Each test writes a names file, and usually the reviewed list beside it,
into ``tmp_path``. The build's use of the module (the hashed file, ``#list[data-match]``,
``version.txt``, ``--names``) is in test_build.py.
"""

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tff_catalog import jsonio, owned
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
    'ruling = "synthetic"\n'
)
RECODE_20 = (
    '[[recode]]\nname = "Sample Mono 20 Gen"\nfamily = "sample-mono-20"\nrelation = "related"\n'
    'ruling = "synthetic"\n'
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
        lambda n: n["families"][0].update(distinct_from=None),
        "/families/0/distinct_from is missing or not a list of family ids",
        id="null-distinct-from",
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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(
            _meet,
            "sample-sans-01-tight: its name 'Sample Sans 01 Bold' reaches sample-sans-01 at key "
            "'samplesans01', and the two are distinct_from each other",
            id="distinct-from-meets",
        ),
        pytest.param(
            _share,
            "key 'samplesans01' is owned by several catalog families (sample-sans-01, "
            "sample-sans-03), outside the ambiguous set",
            id="shared-key",
        ),
        pytest.param(
            _strip_out,
            "'Sample Held 41 Bold', a catalog name, strips to the key 'sampleheld41' of "
            "sample-held-41, outside the catalog, which the ambiguous set can't hold",
            id="catalog-name-to-another-family",
        ),
    ],
)
def test_names_that_would_count_a_family_as_owned_wrongly_are_refused(tmp_path, mutate, message):
    """Milestone 3 step 3, with the site build as the gate (``inventory_match_data_site_build``).
    The planted name also makes a new ambiguous key, reported after it."""
    path = write(tmp_path, names_with(mutate))
    assert errors_of(path)[0] == f"{path}: {message}"


@pytest.mark.parametrize("reviewed", [None, "", RECODE_20])
def test_an_ambiguous_name_nobody_reviewed_is_refused(tmp_path, reviewed):
    """A missing or empty reviewed list reviews nothing: the build fails closed."""
    path = write(tmp_path, reviewed=reviewed)
    assert errors_of(path) == [
        f"{path}: 'Sample Sans 06 Black' may be a weight of sample-sans-06: an ambiguous name "
        f"nobody has reviewed; once the owner has, list it in {tmp_path / 'names.reviewed.toml'}"
    ]


def test_a_reviewed_ambiguous_name_whose_families_changed_is_refused(tmp_path):
    path = write(tmp_path, reviewed=REVIEWED.replace('["sample-sans-06"]', '["sample-sans-07"]'))
    assert errors_of(path) == [
        f"{path}: 'Sample Sans 06 Black' may be a weight of sample-sans-06, but was reviewed "
        "(synthetic) as one of sample-sans-07; the owner reviews the change"
    ]


def test_a_reviewed_name_that_is_no_longer_ambiguous_is_refused(tmp_path):
    stale = (
        REVIEWED + '\n[[ambiguous]]\nname = "Sample Serif 04 Light"\n'
        'families = ["sample-serif-04"]\nruling = "synthetic"\n'
    )
    path = write(tmp_path, reviewed=stale)
    assert errors_of(path) == [
        f"{path}: 'Sample Serif 04 Light' is reviewed as ambiguous in "
        f"{tmp_path / 'names.reviewed.toml'}, but no longer is; once the owner has reviewed "
        "the drop, remove it"
    ]


# ---------------------------------------------------------------------------- the reviewed list


def test_the_sample_s_reviewed_list_loads():
    reviewed, blob = matchfile.load_reviewed(REVIEWED_PATH)
    assert blob == REVIEWED_PATH.read_bytes()
    assert reviewed.ambiguous == {
        "samplesans06black": matchfile.Ambiguous(
            "Sample Sans 06 Black", ("sample-sans-06",), "synthetic"
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
            "ambiguous[0]: needs exactly families, name and ruling",
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
        "(sample-mono-02, sample-mono-20), outside the ambiguous set",
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
        'ruling = "synthetic"\n\n' + RECODE_20
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
        f"{path}: 'Sample Sans 06 Black' may be a weight of sample-sans-06: an ambiguous name "
        f"nobody has reviewed; once the owner has, list it in {listed}",
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
        if recode.family in match["ids"]:
            code = f"{match['ids'].index(recode.family)}{letters[recode.relation]}"
            assert code in match["k"][key].split(",")


def test_the_large_catalog_needs_no_reviewed_list(tmp_path):
    from tests.fixtures import make_large_catalog

    catalog = make_large_catalog.write(tmp_path, fonts=80)
    made = matchfile.match_file(data.load(catalog), matchfile.default_names_path(catalog))
    assert not (tmp_path / "names.reviewed.toml").exists()
    assert made.reviewed_sha256 == hashlib.sha256(b"").hexdigest()
    assert json.loads(made.blob)["amb"] == {}
