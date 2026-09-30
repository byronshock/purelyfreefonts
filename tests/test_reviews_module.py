"""The ruling machinery (``tff_catalog.reviews``): reading, writing, questions and answers files.

Every test works on a temporary tree, except the ones that read the committed
rulings under ``data/reviews/``. Queues come from fake provider modules, so
these tests do not depend on the stages that own them.
"""

import re
import sys
import tomllib
import types
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.helpers import ROOT

from tff_catalog import cli, clock, jsonio, reviews
from tff_catalog.paths import Paths
from tff_catalog.reviews import Answer, Group, Question, Ruling, RulingError

COMMITTED = Paths.for_root(ROOT)
DAY = date(2026, 10, 1)
FAKE = "tff_test_fake_queue"


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    """A bare repository tree (no schemas/: the checkout's schema is used)."""
    return Paths.for_root(tmp_path)


def write(paths: Paths, gate: str, day: str, text: str) -> Path:
    path = reviews.gate_dir(paths, gate) / f"{day}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def answers_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "answers.toml"
    path.write_text(text, encoding="utf-8")
    return path


def lettered(qid: str, choice: str = "a", recommended: bool = True, **values) -> Answer:
    return Answer(qid, f"ruling {qid}", f"reason {qid}", choice, recommended, tuple(values.items()))


def fake_queue(
    monkeypatch: pytest.MonkeyPatch, gate: str, provider: Callable[[Paths], list[Question]] | None
) -> None:
    """Point ``gate``'s queue at a fake module; ``None``: the module has no ``questions``."""
    module = types.ModuleType(FAKE)
    if provider is not None:
        module.questions = provider  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, FAKE, module)
    monkeypatch.setitem(reviews.QUEUE_SOURCES, gate, (f"{FAKE}:questions", "aliases"))


def alias_rows(n: int, start: int = 0) -> list[Question]:
    return [
        Question("A", f"A-{i:04d}", f"Alias row {i}", ("Accept", "Reject"), 0)
        for i in range(start, start + n)
    ]


@pytest.fixture
def frozen_day() -> Iterator[date]:
    with clock.frozen(datetime(2026, 10, 2, 12, 0, tzinfo=UTC)):
        yield date(2026, 10, 2)


# --- the committed rulings ---------------------------------------------------------------------


def test_committed_rulings_load_oldest_first_in_gate_order() -> None:
    rulings = reviews.load_rulings(COMMITTED)
    # The site file grows as the owner rules on more wording, so count its tables.
    site_file = ROOT / "data" / "reviews" / "site" / "2026-09-25.toml"
    site_count = sum(isinstance(v, dict) for v in tomllib.loads(site_file.read_text()).values())
    assert [(r.gate, r.day, len(r.answers)) for r in rulings][:3] == [
        ("T", date(2026, 9, 25), 5),
        ("M", date(2026, 9, 25), 12),
        ("SITE", date(2026, 9, 25), site_count),
    ]
    assert site_count >= 4
    method = {a.id: a for a in reviews.load_rulings(COMMITTED, "M")[0].answers}
    assert list(method) == [f"M{n}" for n in range(1, 13)]  # file order
    assert (method["M9"].choice, method["M9"].recommended) == ("b", False)
    assert method["M9"].values == ()
    site = {a.id: a for a in reviews.load_rulings(COMMITTED, "SITE")[0].answers}
    assert site["spacing_filter"].values == (
        ("label", "Spacing"),
        ("options", ("Any", "Proportional", "Monospaced")),
    )
    assert site["project_rank_label"].values == (("value", "Used in projects"),)
    assert site["project_rank_label"].choice is None


def test_committed_files_are_in_the_canonical_form() -> None:
    files = sorted(COMMITTED.reviews.rglob("*.toml"))
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        ruling = reviews.read_ruling(COMMITTED, _gate_of(path), path)
        assert reviews.render_ruling(ruling.answers, reviews.header_of(text)) == text, path


def _gate_of(path: Path) -> str:
    return {d: g for g, d in reviews.GATE_DIRS.items()}[path.parent.name]


def test_committed_rulings_agree_with_the_catalogue() -> None:
    assert reviews.audit(COMMITTED) == []
    for gate, count in (("T", 5), ("M", 12)):
        assert [q.id for q in reviews.catalogue(gate)] == [
            f"{gate}{n}" for n in range(1, count + 1)
        ]
        assert reviews.questions(COMMITTED, gate) == []
    assert reviews.questions(COMMITTED, "SITE") == []


def test_audit_finds_rulings_that_contradict_the_catalogue(paths: Paths) -> None:
    write(
        paths,
        "T",
        "2026-10-01",
        '[T3]\nchoice = "b"\nrecommended = true\nruling = "x"\nreason = "y"\n\n'
        '[T4]\nchoice = "d"\nrecommended = false\nruling = "x"\nreason = "y"\n\n'
        '[T5]\nchoice = "a"\nrecommended = true\nruling = "x"\nreason = "y"\n\n'
        '[T9]\nchoice = "z"\nrecommended = false\nruling = "x"\nreason = "y"\n',
    )
    assert reviews.audit(paths) == [
        "T 2026-10-01 [T3]: recommended = True is wrong",
        "T 2026-10-01 [T4]: choice 'd' is not an option",
    ]


def test_catalogue_questions_are_single_select() -> None:
    for gate in reviews.GATES:
        qs = reviews.check_questions(gate, reviews.catalogue(gate), "catalogue")
        for q in qs:
            assert 2 <= len(q.options) <= reviews.MAX_OPTIONS
            assert q.text
            assert all(q.options)
    assert {q.gate for q in reviews.CATALOGUE} <= set(reviews.GATES)
    assert len({(q.gate, q.id) for q in reviews.CATALOGUE}) == len(reviews.CATALOGUE)
    assert set(reviews.GATE_TITLES) == set(reviews.GATES)
    assert set(reviews.QUEUE_SOURCES) <= set(reviews.GATES)


# --- reading -----------------------------------------------------------------------------------


def test_missing_directories_hold_no_rulings(paths: Paths) -> None:
    assert reviews.load_rulings(paths) == []
    assert reviews.load_rulings(paths, "LIC") == []
    assert reviews.latest_answers(paths, "X") == {}
    with pytest.raises(KeyError):
        reviews.load_rulings(paths, "NOPE")


def test_other_files_are_ignored_but_a_misnamed_toml_is_an_error(paths: Paths) -> None:
    good = write(paths, "X", "2026-10-01", '[dep-debian-a]\nruling = "r"\nreason = "y"\n')
    (good.parent / ".gitkeep").write_text("")
    (good.parent / "README.md").write_text("notes\n")
    assert [r.day for r in reviews.load_rulings(paths, "X")] == [DAY]
    for name in ("20261001", "2026-10-1", "notes"):
        bad = write(paths, "X", name, '[a]\nruling = "r"\nreason = "y"\n')
        with pytest.raises(RulingError, match=r"named <YYYY-MM-DD>\.toml"):
            reviews.load_rulings(paths, "X")
        bad.unlink()


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('[M1]\nruling = "x"\nchoice = "a"\nrecommended = true\n', "'reason' is a required"),
        ('[M1]\nruling = "x"\nreason = "y"\nchoice = "a"\n', "'recommended' is a dependency"),
        ('[M1]\nruling = "x"\nreason = "y"\nchoice = "ab"\nrecommended = true\n', "does not match"),
        ('[M1]\nruling = ""\nreason = "y"\n', "too short|non-empty"),
        ('[M1]\nruling = "x"\nreason = "y"\nBad = 1\n', "does not match"),
        ('[M1]\nruling = "x"\nreason = "y"\nwhen = 2026-10-01\n', "not valid under any"),
        ('[M1]\nruling = "x"\nreason = "y"\nitems = []\n', "not valid under any"),
        ("# nothing but a comment\n", "should be non-empty|does not have enough properties"),
        ("[M1\n", "Expected"),
        ('top = "x"\n', "is not of type 'object'"),
    ],
)
def test_a_file_that_breaks_the_schema_is_an_error(paths: Paths, text: str, match: str) -> None:
    path = write(paths, "M", "2026-10-01", text)
    with pytest.raises(RulingError, match=match) as info:
        reviews.load_rulings(paths, "M")
    assert str(path) in str(info.value)


def test_the_latest_ruling_wins(paths: Paths) -> None:
    write(
        paths,
        "M",
        "2026-10-01",
        '[M3]\nchoice = "a"\nrecommended = true\nruling = "x"\nreason = "y"\n',
    )
    write(
        paths,
        "M",
        "2026-10-05",
        '[M3]\nchoice = "b"\nrecommended = false\nruling = "z"\nreason = "y"\n',
    )
    write(paths, "T", "2026-10-03", '[T9]\nruling = "t"\nreason = "y"\n')
    assert [(r.gate, r.day.day) for r in reviews.load_rulings(paths)] == [
        ("M", 1),
        ("T", 3),
        ("M", 5),
    ]
    answer, day = reviews.latest_answers(paths, "M")["M3"]
    assert (answer.choice, answer.ruling, day) == ("b", "z", date(2026, 10, 5))


# --- writing -----------------------------------------------------------------------------------


def test_write_ruling_makes_a_canonical_file_that_reads_back(paths: Paths) -> None:
    ruling = Ruling(
        "C",
        DAY,
        (
            lettered("C1"),
            Answer("C2", 'Say "no" \\ twice', "Tab\there\nand a new line", "b", False),
            Answer("C9", "free", "form", values=(("share", 0.55), ("n", 3), ("on", True))),
            Answer("C8", "list", "values", values=(("options", ("Any", "Monospaced")),)),
        ),
    )
    path = reviews.write_ruling(paths, ruling)
    assert path == paths.reviews / "config" / "2026-10-01.toml"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Owner rulings, gate C: the step 2 config")
    assert '[C9]\nn = 3\non = true\nshare = 0.55\nruling = "free"\nreason = "form"\n' in text
    assert 'reason = "Tab\\there\\nand a new line"' in text
    back = reviews.load_rulings(paths, "C")
    assert back == [Ruling("C", DAY, tuple(sorted_values(a) for a in ruling.answers))]
    assert reviews.render_ruling(back[0].answers, reviews.header_of(text)) == text


def sorted_values(a: Answer) -> Answer:
    return Answer(a.id, a.ruling, a.reason, a.choice, a.recommended, tuple(sorted(a.values)))


def test_record_merges_into_the_days_file(paths: Paths) -> None:
    first = reviews.record(paths, Ruling("M", DAY, (lettered("M1"), lettered("M2"))), ("Top",))
    assert (first.added, first.replaced, first.unchanged) == (("M1", "M2"), (), ())
    before = first.path.read_bytes()
    again = reviews.record(paths, Ruling("M", DAY, (lettered("M2"),)), ("Ignored",))
    assert (again.added, again.replaced, again.unchanged) == ((), (), ("M2",))
    assert first.path.read_bytes() == before
    changed = reviews.record(
        paths, Ruling("M", DAY, (lettered("M3"), lettered("M1", "b", False))), None
    )
    assert (changed.added, changed.replaced) == (("M3",), ("M1",))
    text = first.path.read_text(encoding="utf-8")
    assert text.startswith("# Top\n\n[M1]\n")
    answers = reviews.load_rulings(paths, "M")[0].answers
    assert [(a.id, a.choice) for a in answers] == [("M1", "b"), ("M2", "a"), ("M3", "a")]


def test_record_never_loses_a_hand_written_comment(paths: Paths) -> None:
    path = write(
        paths,
        "M",
        "2026-10-01",
        '# Top\n\n[M1]\nchoice = "a"  # the owner said so\nrecommended = true\n'
        'ruling = "x"\nreason = "y"\n',
    )
    reviews.record(paths, Ruling("M", DAY, (lettered("M2"),)))
    text = path.read_text(encoding="utf-8")
    assert "# the owner said so" in text
    assert text.endswith(
        '\n\n[M2]\nchoice = "a"\nrecommended = true\nruling = "ruling M2"\nreason = "reason M2"\n'
    )
    with pytest.raises(RulingError, match=r"hand edits.*M1"):
        reviews.record(paths, Ruling("M", DAY, (lettered("M1", "b", False),)))
    assert "# the owner said so" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("ruling", "match"),
    [
        (Ruling("M", DAY, ()), "needs answers"),
        (Ruling("M", DAY, (lettered("M1"), lettered("M1"))), "each id once"),
        (Ruling("NOPE", DAY, (lettered("M1"),)), "unknown gate"),
        (Ruling("M", DAY, (Answer("M1", "", "y"),)), "non-empty|too short"),
        (Ruling("M", DAY, (Answer("bad id", "x", "y"),)), "does not match"),
        (Ruling("M", DAY, (Answer("M1", "x", "y", recommended=True),)), "dependency"),
        (Ruling("M", DAY, (Answer("M1", "x", "y", values=(("f", float("nan")),)),)), "nan"),
        (Ruling("M", DAY, (Answer("M1", "x", "y", values=(("ruling", "z"),)),)), "reserved"),
    ],
)
def test_record_refuses_a_bad_ruling(paths: Paths, ruling: Ruling, match: str) -> None:
    with pytest.raises(RulingError, match=match):
        reviews.record(paths, ruling)
    assert not reviews.gate_dir(paths, "M").exists()


TEXT = st.text(st.characters(codec="utf-8"), min_size=1, max_size=60).filter(
    lambda s: not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", s)
)


@given(ruling=TEXT, reason=TEXT, items=st.lists(TEXT, min_size=1, max_size=3))
def test_any_schema_text_survives_a_round_trip(ruling: str, reason: str, items: list[str]) -> None:
    answer = Answer("Q1", ruling, reason, "a", True, (("items", tuple(items)),))
    text = reviews.render_ruling([answer], ('A header with "quotes"', ""))
    doc = tomllib.loads(text)
    reviews.check_doc(COMMITTED, doc, "round trip")
    assert reviews.answer_of("Q1", doc["Q1"]) == answer
    assert reviews.header_of(text) == ('A header with "quotes"', "")


@given(
    number=st.one_of(
        st.floats(allow_nan=False, allow_infinity=False), st.integers(-(2**63), 2**63 - 1)
    )
)
def test_any_number_survives_a_round_trip(number: float | int) -> None:
    text = reviews.render_ruling([Answer("Q1", "r", "y", values=(("share", number),))])
    got = tomllib.loads(text)["Q1"]["share"]
    assert (type(got), got) == (type(number), number)


# --- questions ---------------------------------------------------------------------------------


def test_a_research_answer_keeps_its_question_open() -> None:
    q = Question("LIC", "LIC-x", "?", ("Qualifies", "Not", "Excluded", "Research more"), 0)
    for choice, still_open in (("a", False), ("c", False), ("d", True)):
        answers = {q.id: (lettered(q.id, choice, False), DAY)}
        assert (reviews.pending([q], answers) == [q]) is still_open
    assert reviews.pending([q], {}) == [q]


def test_review_rounds_open_one_after_another(paths: Paths) -> None:
    assert [q.id for q in reviews.questions(paths, "R")] == ["R1"]
    write(
        paths,
        "R",
        "2026-11-01",
        '[R1]\nchoice = "b"\nrecommended = false\nruling = "w"\nreason = "y"\n',
    )
    assert [q.id for q in reviews.questions(paths, "R")] == ["R2"]
    write(
        paths,
        "R",
        "2026-11-08",
        '[R2]\nchoice = "a"\nrecommended = false\nruling = "ok"\nreason = "y"\n',
    )
    assert reviews.questions(paths, "R") == []


def test_plan_groups_alike_rows_of_a_row_by_row_gate() -> None:
    odd = Question("A", "A-0005x", "Odd row", ("Accept", "Reject"), 1)
    none = Question("A", "A-none", "?", ("x", "y"))
    rows = [*alias_rows(5), odd, *alias_rows(6, start=5), none]
    items = reviews.plan(rows)
    kinds = [(type(i).__name__, len(i.members) if isinstance(i, Group) else 1) for i in items]
    assert kinds == [("Group", 8), ("Question", 1), ("Group", 3), ("Question", 1)]
    first, second = items[0], items[2]
    assert isinstance(first, Group)
    assert isinstance(second, Group)
    assert [m.id for m in first.members] == [f"A-{i:04d}" for i in range(8)]
    assert first.question.options == (
        "Yes, all 8: (a) Accept",
        "All 8: (b) Reject",
        "No, decide one by one",
    )
    assert (first.question.recommended, first.picks) == (0, (0, 1, None))
    assert re.fullmatch(r"all-A-[0-9a-f]{10}", first.question.id)
    assert reviews.group_of(list(reversed(first.members))).question.id == first.question.id
    assert reviews.group_of(first.members[:7]).question.id != first.question.id
    assert reviews.plan(alias_rows(1)) == alias_rows(1)  # a lone row is asked on its own
    other_gate = [Question("LIC", q.id, q.text, q.options, 0) for q in alias_rows(3)]
    assert reviews.plan(other_gate) == other_gate


def test_a_group_of_four_option_rows_still_has_four_options() -> None:
    rows = [Question("X", f"x{i}", "?", ("A", "B", "C", "D"), 2) for i in range(3)]
    group = reviews.group_of(rows)
    assert group.question.options == (
        "Yes, all 3: (c) C",
        "All 3: (a) A",
        "All 3: (b) B",
        "No, decide one by one",
    )
    assert group.picks == (2, 0, 1, None)
    with pytest.raises(RulingError, match="same options"):
        reviews.group_of([*rows, Question("X", "x9", "?", ("A", "B", "C", "D"), 1)])


def test_questions_come_from_the_catalogue_and_the_queue(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_queue(monkeypatch, "A", lambda p: alias_rows(3))
    assert [q.id for q in reviews.questions(paths, "A")] == ["A-0000", "A-0001", "A-0002"]
    write(
        paths,
        "A",
        "2026-10-01",
        '[A-0001]\nchoice = "b"\nrecommended = false\nruling = "r"\nreason = "y"\n',
    )
    assert [q.id for q in reviews.questions(paths, "A")] == ["A-0000", "A-0002"]
    assert [q.id for q in reviews.gate_questions(paths, "A")] == ["A-0000", "A-0001", "A-0002"]
    fake_queue(monkeypatch, "C", lambda p: [Question("C", "C1", "Newer text", ("y", "n"), 1)])
    assert [(q.id, q.text) for q in reviews.questions(paths, "C")][:2] == [
        ("C1", "Newer text"),  # the queue's version of a catalogue question wins
        ("C2", reviews.catalogue("C")[1].text),
    ]


@pytest.mark.parametrize(
    ("question", "match"),
    [
        (Question("A", "A-1", "?", ("only one",), None), "has 1 options"),
        (Question("A", "A-1", "?", ("a", "b", "c", "d", "e"), 0), "has 5 options"),
        (Question("A", "A-1", "?", ("a", "b"), 2), "recommends option 2"),
        (Question("LIC", "A-1", "?", ("a", "b"), 0), "belongs to gate LIC"),
        (Question("A", "A 1", "?", ("a", "b"), 0), "not a valid id"),
        (Question("A", "A-1", " ", ("a", "b"), 0), "empty text or option"),
        (Question("A", "A-1", "?", ("a", ""), 0), "empty text or option"),
    ],
)
def test_a_question_the_owner_cannot_answer_is_an_error(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, question: Question, match: str
) -> None:
    fake_queue(monkeypatch, "A", lambda p: [question])
    with pytest.raises(RulingError, match=match):
        reviews.questions(paths, "A")
    fake_queue(monkeypatch, "A", lambda p: [alias_rows(1)[0], alias_rows(1)[0]])
    with pytest.raises(RulingError, match="appears twice"):
        reviews.questions(paths, "A")


def test_group_ids_survive_answers_to_other_chunks(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_queue(monkeypatch, "A", lambda p: alias_rows(20))
    known = reviews.gate_questions(paths, "A")
    before = reviews.open_items(known, {})
    assert [len(i.members) for i in before if isinstance(i, Group)] == [8, 8, 4]
    answered = {q: (lettered(q), DAY) for q in ("A-0001", "A-0005")}
    after = reviews.open_items(known, answered)
    groups = [i for i in after if isinstance(i, Group)]
    assert [len(g.members) for g in groups] == [6, 8, 4]  # chunk 1 shrank; 2 and 3 kept their rows
    assert [g.question.id for g in groups[1:]] == [g.question.id for g in before[1:]]
    assert groups[0].question.id != before[0].question.id
    seven = {f"A-{i:04d}": (lettered(f"A-{i:04d}"), DAY) for i in range(1, 8)}
    lone = reviews.open_items(known, seven)[0]
    assert lone == alias_rows(1)[0]  # a chunk with one open row asks it alone


def test_apply_refuses_a_group_that_is_no_longer_open(
    paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, frozen_day: date
) -> None:
    fake_queue(monkeypatch, "A", lambda p: alias_rows(20))
    first, second, _ = reviews.open_items(reviews.gate_questions(paths, "A"), {})
    one = 'gate = "A"\n[A-0003]\nchoice = "b"\nreason = "y"\n'
    assert reviews.cmd_apply_rulings(paths, answers_file(tmp_path, one)) == 0
    capsys.readouterr()
    old = f'gate = "A"\n[{first.question.id}]\nchoice = "a"\nreason = "y"\n'
    assert reviews.cmd_apply_rulings(paths, answers_file(tmp_path, old)) == 1
    assert f"[{first.question.id}] is not an open group of gate A" in capsys.readouterr().err
    later = f'gate = "A"\n[{second.question.id}]\nchoice = "a"\nreason = "y"\n'
    assert reviews.cmd_apply_rulings(paths, answers_file(tmp_path, later)) == 0
    assert "8 answers (8 new" in capsys.readouterr().out
    assert len(reviews.latest_answers(paths, "A")) == 9


L3_TEXT = 'Foo failed the L3 license check. Record the answer with text_sha256 = "{}".'
L3_OPTIONS = ("Exclude it from the catalog", "Keep it (owner ruling)", "Research more")


def test_a_pinned_value_is_filled_in_and_a_new_text_reopens_the_question(
    paths: Paths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sha = {"now": "a" * 64}
    fake_queue(
        monkeypatch,
        "L3",
        lambda p: [Question("L3", "L3-foo", L3_TEXT.format(sha["now"]), L3_OPTIONS)],
    )
    (q,) = reviews.questions(paths, "L3")
    assert reviews.pins(q) == {"text_sha256": "a" * 64}
    file = answers_file(
        tmp_path, 'gate = "L3"\nday = 2026-10-01\n[L3-foo]\nchoice = "b"\nreason = "y"\n'
    )
    assert reviews.cmd_apply_rulings(paths, file) == 0
    answer, _ = reviews.latest_answers(paths, "L3")["L3-foo"]
    assert (answer.choice, answer.recommended) == ("b", False)
    assert answer.values == (("text_sha256", "a" * 64),)  # what license_l3 needs to apply it
    assert reviews.questions(paths, "L3") == []
    sha["now"] = "b" * 64  # the license text changed: the old ruling no longer applies
    assert [q.id for q in reviews.questions(paths, "L3")] == ["L3-foo"]
    capsys.readouterr()
    assert reviews.cmd_questions(paths, "L3") == 0
    assert (
        "Earlier ruling (2026-10-01, on another text_sha256): (b) Keep it"
        in capsys.readouterr().out
    )
    wrong = 'gate = "L3"\n[L3-foo]\nchoice = "a"\nreason = "y"\ntext_sha256 = "' + "a" * 64 + '"\n'
    assert reviews.cmd_apply_rulings(paths, answers_file(tmp_path, wrong)) == 1
    assert "but the question is about 'bbbb" in capsys.readouterr().err
    sha["now"] = ""  # no text at all: the schema cannot hold an empty value
    assert reviews.cmd_apply_rulings(paths, file) == 1
    assert 'pins text_sha256 = ""' in capsys.readouterr().err


def test_research_keeps_a_pinned_question_open() -> None:
    q = Question("L3", "L3-foo", L3_TEXT.format("abc"), L3_OPTIONS)
    research = Answer("L3-foo", "r", "y", "c", False, (("text_sha256", "abc"),))
    keep = Answer("L3-foo", "k", "y", "b", False, (("text_sha256", "abc"),))
    assert reviews.pending([q], {q.id: (research, DAY)}) == [q]
    assert reviews.pending([q], {q.id: (keep, DAY)}) == []
    assert reviews.pins(Question("A", "A-1", 'No pin here = "x".', ("a", "b"))) == {}


def test_the_l3_question_wording_pins_its_text_hash() -> None:
    """The contract with license_l3: its questions name the text a ruling is about."""
    from tff_catalog import license_l3

    make = getattr(license_l3, "_question", None)
    if make is None:
        pytest.skip("license_l3 builds its questions elsewhere")
    result = license_l3.L3Result(
        family_id="foo",
        level="failed",
        checked_on=DAY,
        text_url="https://example.org/OFL.txt",
        text_sha256="c" * 64,
        matched=None,
        name_ids=(None, None),
        font_version=None,
        font_file=None,
        problems=("text does not match OFL-1.1",),
    )
    question = make(result, "Foo", "OFL-1.1")
    assert reviews.pins(question) == {"text_sha256": "c" * 64}


def test_questions_reports_a_queue_it_cannot_read(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths.queues.mkdir(parents=True)
    (paths.queues / "corrections.json").write_text("{not json", encoding="utf-8")
    assert reviews.cmd_questions(paths, "X") == 1
    assert "gate X's queue (corrections.json) cannot be read" in capsys.readouterr().err

    def outdated(p: Paths) -> list[Question]:
        raise ValueError("$.items[0]: unknown field 'old'")  # as stageio.StageFileError does

    fake_queue(monkeypatch, "A", outdated)
    with pytest.raises(RulingError, match=r"unknown field 'old'; run `tff-catalog aliases` again"):
        reviews.questions(paths, "A")

    def keyless(p: Paths) -> list[Question]:
        return [{}["items"]]  # a queue file without the key its reader expects

    fake_queue(monkeypatch, "A", keyless)
    with pytest.raises(RulingError, match=r"cannot be read: 'items'"):
        reviews.questions(paths, "A")


def test_gate_x_reads_its_queue_file(paths: Paths) -> None:
    with pytest.raises(FileNotFoundError):
        reviews.questions(paths, "X")
    rows = [
        {
            "gate": "X",
            "id": f"dep-debian-font{i}",
            "text": f"Font {i} is pulled in by a package. Leave it out of most chosen?",
            "options": ["Yes: leave it out of most chosen", "No: keep counting it"],
            "recommended": 0,
            "kind": "dependency",
            "share": 0.6,
        }
        for i in range(3)
    ]
    jsonio.dump(rows, paths.queues / "corrections.json")
    got = reviews.questions(paths, "X")
    assert [q.id for q in got] == [r["id"] for r in rows]
    assert got[0].options == tuple(rows[0]["options"])
    jsonio.dump([{**rows[0], "options": "yes"}], paths.queues / "corrections.json")
    with pytest.raises(RulingError, match="item 0 is not a question"):
        reviews.questions(paths, "X")


# --- tff-catalog questions ---------------------------------------------------------------------


def test_questions_prints_batches_of_single_select_questions(
    paths: Paths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    frozen_day: date,
) -> None:
    lone = Question("A", "A-lone", "A lone row", ("Accept", "Reject", "Research more"), None)
    rec_b = [Question("A", f"A-r{i}", "?", ("Accept", "Reject"), 1) for i in range(3)]
    fake_queue(monkeypatch, "A", lambda p: [*alias_rows(20), lone, *rec_b])
    assert reviews.cmd_questions(paths, "A") == 0
    out = capsys.readouterr().out
    assert out.startswith("# Gate A: the alias queue (step 7)\n")
    assert "24 open questions, asked as 5 in 2 batches of up to 4." in out
    batches = re.split(r"^## Batch \d of 2$", out.split("## Answers file")[0], flags=re.M)[1:]
    assert [len(re.findall(r"^### ", b, flags=re.M)) for b in batches] == [4, 1]
    for block in re.split(r"^### ", out, flags=re.M)[1:]:
        options = re.findall(r"^- \([a-z]\) ", block.split("\n\n")[0], flags=re.M)
        assert len(options) <= reviews.MAX_OPTIONS
    assert (
        "- (a) Yes, all 8: (a) Accept (rec)\n- (b) All 8: (b) Reject\n- (c) No, decide one by one"
        in out
    )
    assert "each with the options (a) Accept (rec), (b) Reject:\n- A-0000: Alias row 0\n" in out
    assert "### A-lone\nA lone row\n- (a) Accept\n- (b) Reject\n- (c) Research more\n" in out
    assert 'gate = "A"\nday = 2026-10-02' in out
    assert "multi-select" in out
    assert "When the owner decides a group one by one, ask its questions" in out


def test_questions_shows_an_earlier_research_ruling(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    q = Question("A", "A-x", "Which?", ("Accept", "Research more"), 0)
    fake_queue(monkeypatch, "A", lambda p: [q])
    write(
        paths,
        "A",
        "2026-10-01",
        '[A-x]\nchoice = "b"\nrecommended = false\nruling = "Look again"\nreason = "y"\n',
    )
    assert reviews.cmd_questions(paths, "A") == 0
    assert "Earlier ruling (2026-10-01): (b) Look again" in capsys.readouterr().out


def test_questions_with_nothing_open(capsys: pytest.CaptureFixture[str]) -> None:
    assert reviews.cmd_questions(COMMITTED, "M") == 0
    out = capsys.readouterr().out
    assert "No open questions." in out
    assert "answered in `data/reviews/method/`" in out


def test_questions_says_which_stage_to_run(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing(p: Paths) -> list[Question]:
        raise FileNotFoundError(2, "No such file", str(p.queues / "aliases.json"))

    fake_queue(monkeypatch, "A", missing)
    assert reviews.cmd_questions(paths, "A") == 1
    err = capsys.readouterr().err
    assert "run `tff-catalog aliases` first" in err
    fake_queue(monkeypatch, "A", None)
    assert reviews.cmd_questions(paths, "A") == 2
    assert "is not implemented" in capsys.readouterr().err
    write(paths, "A", "2026-10-01", "[broken\n")
    assert reviews.cmd_questions(paths, "A") == 1
    assert "2026-10-01.toml" in capsys.readouterr().err


# --- tff-catalog rulings apply -----------------------------------------------------------------

GATE_C_FILE = """\
# Owner rulings, gate C: the step 2 config: preinstalled.toml and foundries.toml.
# Ruled by the owner in chat on 2026-10-01; recorded with `tff-catalog rulings apply`.
# Each ruling: choice = the option picked; recommended = whether it was the recommended
# option; ruling = what it means; reason = why, and whose words those are.

[C1]
choice = "a"
recommended = true
ruling = "Approve"
reason = "The owner approved the list as shown."

[C2]
choice = "b"
recommended = false
ruling = "EndeavourOS is dropped."
reason = "Effect (the owner gave no reason): no EndeavourOS source."
"""


def test_apply_records_a_static_gate(
    paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = answers_file(
        tmp_path,
        'gate = "C"\nday = 2026-10-01\n\n'
        '[C1]\nchoice = "a"\nreason = "The owner approved the list as shown."\n\n'
        '[C2]\nchoice = "b"\nruling = "EndeavourOS is dropped."\n'
        'reason = "Effect (the owner gave no reason): no EndeavourOS source."\n',
    )
    assert reviews.cmd_apply_rulings(paths, file) == 0
    out = capsys.readouterr().out
    assert "data/reviews/config/2026-10-01.toml: 2 answers (2 new, 0 replaced" in out
    assert "Gate C: 1 open question left." in out
    assert (paths.reviews / "config" / "2026-10-01.toml").read_text(encoding="utf-8") == GATE_C_FILE
    assert [q.id for q in reviews.questions(paths, "C")] == ["C3"]
    assert reviews.cmd_apply_rulings(paths, file) == 0
    assert "(0 new, 0 replaced, 2 already recorded)" in capsys.readouterr().out


def test_apply_honours_the_recommendation_given_in_chat(
    paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A question asked in chat with another recommended option records that letter."""
    file = answers_file(
        tmp_path,
        'gate = "C"\nday = 2026-10-01\n\n'
        '[C2]\nchoice = "b"\nrecommendation = "b"\nreason = "Claude recommended (b) in chat."\n',
    )
    assert reviews.cmd_apply_rulings(paths, file) == 0
    doc = tomllib.loads((paths.reviews / "config" / "2026-10-01.toml").read_text(encoding="utf-8"))
    assert (doc["C2"]["choice"], doc["C2"]["recommended"], doc["C2"]["recommendation"]) == (
        "b",
        True,
        "b",
    )
    assert reviews.audit(paths, "C") == []
    capsys.readouterr()
    bad = answers_file(
        tmp_path,
        'gate = "C"\n[C1]\nchoice = "a"\nrecommendation = "b"\nrecommended = true\nreason = "y"\n',
    )
    assert reviews.cmd_apply_rulings(paths, bad) == 1
    assert re.search(r"\(a\) is not the recommended", capsys.readouterr().err)
    wrong = answers_file(
        tmp_path, 'gate = "C"\n[C1]\nchoice = "a"\nrecommendation = "q"\nreason = "y"\n'
    )
    assert reviews.cmd_apply_rulings(paths, wrong) == 1
    assert "recommendation must be one of a-c" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("tables", "match"),
    [
        ('[C1]\nchoice = "a"\nrecommended = false\nreason = "y"\n', r"\(a\) is the recommended"),
        ('[C2]\nchoice = "b"\nrecommended = true\nreason = "y"\n', r"\(b\) is not the recommended"),
        ('[C1]\nchoice = "d"\nreason = "y"\n', "choice must be one of a-c"),
        ('[C1]\nreason = "y"\n', "choice must be one of a-c, got None"),
        ('[C1]\nchoice = "a"\n', "'reason' is a required"),
        ('[C9]\nchoice = "a"\nruling = "x"\nreason = "y"\n', "'recommended' is a dependency"),
        ('[C1]\nchoice = "a"\nreason = "y"\n[C1x]\nruling = ""\nreason = "y"\n', "non-empty|short"),
    ],
)
def test_apply_checks_each_answer(
    paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str], tables: str, match: str
) -> None:
    file = answers_file(tmp_path, f'gate = "C"\n{tables}')
    assert reviews.cmd_apply_rulings(paths, file) == 1
    assert re.search(match, capsys.readouterr().err)
    assert not reviews.gate_dir(paths, "C").exists()


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('[C1]\nchoice = "a"\nreason = "y"\n', "gate must be one of"),
        ('gate = "Z"\n[C1]\nchoice = "a"\nreason = "y"\n', "gate must be one of"),
        ('gate = "C"\nday = "1 Oct"\n[C1]\nchoice = "a"\nreason = "y"\n', "day must be a date"),
        ('gate = "C"\nday = 2026-10-01T10:00:00Z\n[C1]\nchoice = "a"\nreason = "y"\n', "day must"),
        ('gate = "C"\nheader = "one"\n[C1]\nchoice = "a"\nreason = "y"\n', "header must be"),
        ('gate = "C"\nnote = "x"\n[C1]\nchoice = "a"\nreason = "y"\n', "note must be a table"),
        ('gate = "C"\n', "no answers"),
        ('gate = "C"\n[C1\n', "Expected"),
    ],
)
def test_apply_checks_the_answers_file(
    paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str, match: str
) -> None:
    assert reviews.cmd_apply_rulings(paths, answers_file(tmp_path, text)) == 1
    assert match in capsys.readouterr().err


def test_apply_reports_a_missing_file(paths: Paths, tmp_path: Path, capsys) -> None:
    assert reviews.cmd_apply_rulings(paths, tmp_path / "nope.toml") == 1
    assert "nope.toml" in capsys.readouterr().err


def test_apply_expands_a_group_answer(
    paths: Paths,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    frozen_day: date,
) -> None:
    fake_queue(monkeypatch, "A", lambda p: alias_rows(11))
    first, second = [i for i in reviews.plan(reviews.questions(paths, "A")) if isinstance(i, Group)]
    file = answers_file(
        tmp_path,
        f'gate = "A"\nheader = ["Gate A, batch 1."]\n\n'
        f'[{first.question.id}]\nchoice = "b"\nreason = "The owner rejected them all."\n\n'
        f'[{second.question.id}]\nchoice = "c"\n',
    )
    assert reviews.cmd_apply_rulings(paths, file) == 0
    out = capsys.readouterr().out
    assert "8 answers (8 new" in out
    assert f"{second.question.id}: decided one by one, so its 3 questions stay open." in out
    assert "Gate A: 3 open questions left." in out
    ruling = reviews.load_rulings(paths, "A")[0]
    assert ruling.day == frozen_day
    assert [a.id for a in ruling.answers] == [m.id for m in first.members]
    assert ruling.answers[0] == Answer(
        "A-0000", "Reject", "The owner rejected them all.", "b", False,
        (("batch", first.question.id),),
    )  # fmt: skip
    text = (reviews.gate_dir(paths, "A") / "2026-10-02.toml").read_text(encoding="utf-8")
    assert text.startswith("# Gate A, batch 1.\n\n[A-0000]\n")
    left = reviews.plan(reviews.questions(paths, "A"))
    assert [i.question.id for i in left if isinstance(i, Group)] == [second.question.id]


def test_apply_refuses_an_answer_given_twice(
    paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    fake_queue(monkeypatch, "A", lambda p: alias_rows(3))
    (group,) = reviews.plan(reviews.questions(paths, "A"))
    assert isinstance(group, Group)
    file = answers_file(
        tmp_path,
        f'gate = "A"\n[{group.question.id}]\nchoice = "a"\nreason = "y"\n'
        '[A-0001]\nchoice = "b"\nreason = "y"\n',
    )
    assert reviews.cmd_apply_rulings(paths, file) == 1
    assert "[A-0001] is answered twice" in capsys.readouterr().err


def test_apply_takes_unknown_ids_only_when_complete(
    paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = 'gate = "SITE"\nday = 2026-10-01\n[footer_note]\nvalue = "Data: CC BY-SA 4.0"\n'
    site += 'ruling = "The footer names the data license."\nreason = "y"\n'
    assert reviews.cmd_apply_rulings(paths, answers_file(tmp_path, site)) == 0
    assert capsys.readouterr().err == ""
    (answer,) = reviews.load_rulings(paths, "SITE")[0].answers
    assert answer.values == (("value", "Data: CC BY-SA 4.0"),)
    typo = 'gate = "M"\n[M99]\nchoice = "a"\nrecommended = true\nruling = "x"\nreason = "y"\n'
    assert reviews.cmd_apply_rulings(paths, answers_file(tmp_path, typo)) == 0
    assert "[M99] is not a known question of gate M" in capsys.readouterr().err


def test_apply_without_a_queue_records_as_given(
    paths: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    fake_queue(monkeypatch, "A", None)
    file = answers_file(
        tmp_path,
        'gate = "A"\nday = 2026-10-01\n[A-0001]\nchoice = "a"\nrecommended = true\n'
        'ruling = "Accept"\nreason = "y"\n',
    )
    assert reviews.cmd_apply_rulings(paths, file) == 0
    captured = capsys.readouterr()
    assert "queue is not available" in captured.err
    assert "Gate A:" not in captured.out  # the open count is unknown
    assert reviews.latest_answers(paths, "A")["A-0001"][0].choice == "a"


def test_the_cli_runs_both_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "tff-catalog"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TFF_STORE", raising=False)
    assert cli.main(["questions", "--gate", "CI"]) == 0
    assert "### CI1\n" in capsys.readouterr().out
    file = answers_file(
        tmp_path, 'gate = "CI"\nday = 2026-10-01\n[CI2]\nchoice = "c"\nreason = "y"\n'
    )
    assert cli.main(["rulings", "apply", str(file)]) == 0
    assert "Gate CI: 1 open question left." in capsys.readouterr().out
    assert (tmp_path / "data" / "reviews" / "ci" / "2026-10-01.toml").is_file()
