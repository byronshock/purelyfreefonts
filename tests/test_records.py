"""The record contract (design-m1 §2.1): JSON round trips, canonical order, stable bytes."""

import dataclasses
import itertools
import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator
from tests.helpers import ROOT, synth

from tff_catalog import jsonio
from tff_catalog.records import (
    DROP_REASONS,
    FILE_ROLES,
    NAMESPACES,
    RECORD_TYPES,
    RELATION_KINDS,
    STATUSES,
    UNITS,
    URL_ROLES,
    FontFileRef,
    LicenseFact,
    Observation,
    Record,
    Relation,
    SourceKey,
    UniverseRecord,
    attrs,
    from_json,
    read_jsonl,
    sort_key,
    to_json,
    write_jsonl,
)

SCHEMA_DOC = json.loads((ROOT / "schemas/stage/records.schema.json").read_text(encoding="utf-8"))
SCHEMA = Draft202012Validator(SCHEMA_DOC)
NAME_RELATIONS = sorted(
    SCHEMA_DOC["$defs"]["UniverseRecord"]["properties"]["names"]["items"]["prefixItems"][1]["enum"]
)

# --- strategies -------------------------------------------------------------------------------

text = st.text(st.characters(codec="utf-8", exclude_categories=("Cs",)), min_size=1, max_size=12)
sources = st.from_regex(r"[a-z][a-z0-9_]{0,11}", fullmatch=True)
attr_names = st.from_regex(r"[a-z][a-z0-9_]{0,7}", fullmatch=True)
urls = st.from_regex(r"https?://[a-z]{1,8}\.example/[a-z0-9._/-]{0,12}", fullmatch=True)
sha256s = st.from_regex(r"[0-9a-f]{64}", fullmatch=True)
days = st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31))
counts = st.integers(min_value=0, max_value=2**40)
finite = st.floats(allow_nan=False, allow_infinity=False)
keys = st.builds(SourceKey, st.sampled_from(sorted(NAMESPACES)), text)
scalars = st.one_of(text, st.integers(-(2**53), 2**53), finite, st.booleans())
attr_tuples = st.dictionaries(attr_names, scalars, max_size=3).map(lambda d: attrs(**d))
strings = st.lists(text, max_size=3).map(tuple)


def optional[T](s: st.SearchStrategy[T]) -> st.SearchStrategy[T | None]:
    return st.none() | s


observations = st.builds(
    Observation,
    source=sources,
    series=text,
    key=keys,
    value=optional(finite | st.integers(0, 2**40)),
    unit=st.sampled_from(sorted(UNITS)),
    start=days,
    end=days,
    attrs=attr_tuples,
)
files = st.builds(
    FontFileRef,
    url=urls,
    sha256=optional(sha256s),
    size=optional(counts),
    role=st.sampled_from(sorted(FILE_ROLES)),
    codepoints=optional(counts),
    unicode_range=optional(text),
    git_blob=optional(st.from_regex(r"\A[0-9a-f]{40}\Z")),
)
universe_records = st.builds(
    UniverseRecord,
    source=sources,
    key=keys,
    family=text,
    display_name=optional(text),
    category=optional(text),
    classifications=strings,
    primary_script=optional(text),
    subsets=strings,
    latin_languages=optional(counts),
    is_monospace=optional(st.booleans()),
    variable=optional(st.booleans()),
    added=optional(days),
    status=st.sampled_from(sorted(STATUSES)),
    urls=st.lists(st.tuples(st.sampled_from(sorted(URL_ROLES)), urls), max_size=2).map(tuple),
    files=st.lists(files, max_size=2).map(tuple),
    names=st.lists(st.tuples(text, st.sampled_from(NAME_RELATIONS)), max_size=2).map(tuple),
    drop=optional(st.sampled_from(sorted(DROP_REASONS))),
    attrs=attr_tuples,
)
license_facts = st.builds(
    LicenseFact,
    source=sources,
    key=keys,
    raw=st.text(max_size=20),
    spdx=optional(text),
    text_url=optional(urls),
    text_sha256=optional(sha256s),
    rfn=optional(st.booleans()),
    attrs=attr_tuples,
)
relations = st.builds(
    Relation,
    source=sources,
    subject=keys,
    kind=st.sampled_from(sorted(RELATION_KINDS)),
    object=keys,
    alt=st.integers(0, 5),
    attrs=attr_tuples,
)
any_record = st.one_of(observations, universe_records, license_facts, relations)


# --- round trips --------------------------------------------------------------------------


@given(any_record)
def test_json_round_trip(r: Record) -> None:
    d = to_json(r)
    assert d["type"] == type(r).__name__
    assert from_json(d) == r
    assert from_json(json.loads(jsonio.canonical_bytes(d))) == r


@given(any_record)
def test_to_json_matches_the_schema(r: Record) -> None:
    errors = [e.message for e in SCHEMA.iter_errors(to_json(r))]
    assert errors == []


@given(any_record)
def test_canonical_bytes_survive_a_round_trip(r: Record) -> None:
    once = jsonio.canonical_bytes(to_json(r))
    again = jsonio.canonical_bytes(to_json(from_json(json.loads(once))))
    assert again == once


def test_synthetic_records_round_trip() -> None:
    recs = synth.records()
    assert {type(r) for r in recs} == set(RECORD_TYPES.values())
    for r in recs:
        assert from_json(to_json(r)) == r
        assert not list(SCHEMA.iter_errors(to_json(r)))


def test_every_field_is_written() -> None:
    for r in synth.records():
        assert set(to_json(r)) == {"type", *(f.name for f in dataclasses.fields(r))}


def test_observation_values_are_written_as_floats() -> None:
    obs = synth.observation(synth.families()[0], synth.INSTALLS, synth.DAYS[0], 5066)
    as_int = jsonio.canonical_bytes(to_json(obs))
    as_float = jsonio.canonical_bytes(to_json(dataclasses.replace(obs, value=5066.0)))
    assert as_int == as_float
    assert b'"value":5066.0' in as_int


_REL = to_json(synth.relation(synth.families()[0]))
_OBS = to_json(synth.records()[0])
BAD_JSON = {
    "unknown-type": ({"type": "Nope"}, "unknown record type"),
    "no-type": ({"source": "x"}, "unknown record type"),
    "unknown-field": ({**_REL, "weight": 1}, "unknown fields ['weight']"),
    "bad-unit": ({**_OBS, "unit": "furlongs"}, "Observation.unit: 'furlongs'"),
    "bad-kind": ({**_REL, "kind": "suggests"}, "Relation.kind: 'suggests'"),
    "bad-key": ({**_REL, "subject": {"ns": "npm"}}, "Relation.subject: bad SourceKey"),
    "bad-date": ({**_OBS, "start": "2026-13-01"}, "Observation.start"),
    "attrs-as-list": ({**_OBS, "attrs": [["a", 1]]}, "attrs must be an object"),
}


@pytest.mark.parametrize("case", sorted(BAD_JSON))
def test_from_json_rejects_bad_input(case: str) -> None:
    doc, message = BAD_JSON[case]
    with pytest.raises(ValueError, match=re.escape(message)):
        from_json(doc)


# --- canonical order ----------------------------------------------------------------------


@given(
    st.lists(any_record, max_size=8).flatmap(lambda xs: st.tuples(st.just(xs), st.permutations(xs)))
)
def test_sort_key_order_ignores_input_order(pair: tuple[list[Record], list[Record]]) -> None:
    recs, shuffled = pair
    assert sorted(shuffled, key=sort_key) == sorted(recs, key=sort_key)


@given(any_record, any_record)
def test_sort_key_ties_only_for_identical_records(a: Record, b: Record) -> None:
    same_bytes = jsonio.canonical_bytes(to_json(a)) == jsonio.canonical_bytes(to_json(b))
    assert (sort_key(a) == sort_key(b)) == same_bytes


def test_sort_key_orders_by_type_then_source_then_key() -> None:
    fam = synth.families()[0]
    obs_b = synth.observation(fam, synth.PACKAGES, synth.DAYS[0], 1.0)
    obs_a = synth.observation(fam, synth.INSTALLS, synth.DAYS[0], 1.0)
    uni = synth.universe_record(fam)
    lic = synth.license_fact(fam)
    rel = synth.relation(fam)
    other = dataclasses.replace(obs_a, key=SourceKey("brew-cask", "font-zzz"))
    ordered = sorted([rel, lic, uni, other, obs_b, obs_a], key=sort_key)
    assert ordered == [obs_a, other, obs_b, uni, lic, rel]
    assert list(RECORD_TYPES) == ["Observation", "UniverseRecord", "LicenseFact", "Relation"]


def test_write_jsonl_is_byte_stable(tmp_path: Path) -> None:
    recs = synth.records()
    outputs = set()
    for i, order in enumerate(itertools.islice(itertools.permutations(recs[:6]), 5)):
        path = tmp_path / f"{i}.jsonl"
        write_jsonl([*order, *recs[6:]], path)
        outputs.add(path.read_bytes())
    assert len(outputs) == 1
    assert read_jsonl(tmp_path / "0.jsonl") == sorted(recs, key=sort_key)
    gz = tmp_path / "r.jsonl.gz"
    write_jsonl(recs, gz)
    first = gz.read_bytes()
    write_jsonl(list(reversed(recs)), gz)
    assert gz.read_bytes() == first, "gzip output must not carry a timestamp"
    assert read_jsonl(gz) == sorted(recs, key=sort_key)


# --- canonical_bytes --------------------------------------------------------------------------


def test_canonical_bytes_ignore_dict_order() -> None:
    a = {"b": 1, "a": {"y": [1, 2], "x": "é"}}
    b = {"a": {"x": "é", "y": [1, 2]}, "b": 1}
    assert jsonio.canonical_bytes(a) == jsonio.canonical_bytes(b)
    assert jsonio.canonical_bytes(a) == '{"a":{"x":"é","y":[1,2]},"b":1}'.encode()


def test_canonical_bytes_encode_dates_and_utc_times() -> None:
    moment = datetime(2026, 10, 3, 8, 17, 22, tzinfo=timezone(timedelta(hours=2)))
    assert jsonio.canonical_bytes([date(2026, 10, 3), moment]) == (
        b'["2026-10-03","2026-10-03T06:17:22Z"]'
    )
    assert jsonio.canonical_bytes(moment) == jsonio.canonical_bytes(moment.astimezone(UTC))


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), datetime(2026, 10, 3, 6, 0), {1, 2}, object()],
    ids=["nan", "inf", "naive-datetime", "set", "object"],
)
def test_canonical_bytes_refuse_unstable_values(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        jsonio.canonical_bytes({"v": value})
