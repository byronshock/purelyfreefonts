"""Owner gates: questions and rulings (design-m1 §7). Owner: agent P14.

Rulings live in ``data/reviews/<dir>/<YYYY-MM-DD>.toml``, one file per
answered batch, where ``<dir>`` is ``GATE_DIRS[gate]`` (``gate_dir``): the
2026-09-25 rulings are ``data/reviews/terms/`` (gate T), ``method/`` (M) and
``site/`` (SITE). Every stage that reads rulings finds them through
``gate_dir``, never by spelling the path.

File format (``schemas/review.schema.json``; the committed files are the
examples). Comment lines at the top say who ruled, when and where else the
ruling is recorded. Each top-level table is one question, named by its id:

- ``ruling`` (required): what the answer means, in plain words;
- ``reason`` (required): why, and whose words those are;
- ``choice``: the option picked ("a", "b", ...), for a lettered question, with
  ``recommended`` (whether that was the recommended option);
- any other keys hold the answer's values for questions that are not lettered,
  for example site wording: ``label``, ``options``, ``value``.

``tff-catalog questions --gate G`` prints the open questions of a gate as
single-select questions with at most 4 options, the recommended one marked (no
multi-select: it cannot be submitted in the owner's UI).
``tff-catalog rulings apply FILE`` checks an answers file and writes it into
``data/reviews/``.

**Reading** (``load_rulings``). Every ``*.toml`` file of a gate directory,
which must be named ``<YYYY-MM-DD>.toml``, is checked against the schema;
other files are ignored and a missing directory holds no rulings. Rulings come
oldest first, so for one question id the latest one wins (``latest_answers``).

**Questions** (``questions``). A gate's questions come from ``CATALOGUE``, the
fixed questions of design-m1 §7 (gates T, M, C, L1, the R rounds and CI), and
from the queue of the stage that owns the gate (``QUEUE_SOURCES``): that
module's ``questions(paths) -> list[Question]``, read from the queue its last
run wrote, or a queue file of question objects. That raises
``FileNotFoundError`` when the stage has not run yet, ``NotImplementedError``
when the module has no ``questions``, and ``RulingError`` when the queue cannot
be read. A question stays open until a ruling settles its id (``settles``). A
ruling does not settle it when the chosen option starts with "Research"
(``REOPEN``): the stage keeps it queued and the owner rules again later. Nor
does it when the question pins a value the ruling lacks: a question whose text
says ``Record the answer with <key> = "<value>"`` (``pins``; gate L3 pins the
license text's ``text_sha256``) is settled only by a ruling carrying that value,
so a ruling on an older text leaves the new one open. Review round R<n+1>
opens once R<n> is answered with anything but (a).

**Printing** (``cmd_questions``). The open questions are printed in batches of
``BATCH_SIZE`` (the owner's question form takes 4 at a time). In the
row-by-row gates (``GROUPED_GATES``), alike questions (the same options and
the same recommended option) are cut into chunks of ``GROUP_SIZE`` over the
whole queue, answered rows included, and each chunk's open rows become one
group question, ``all-<gate>-<hash of their ids>``: "accept all N as
recommended?", with the other options for all N and "decide one by one". Its
members are printed under it; a group decided one by one is asked member by
member, with the options printed there. Answering one chunk's rows never
changes another chunk's id, so the groups of a printout stay answerable until
their own rows are answered or the stage rewrites its queue.

**Recording** (``cmd_apply_rulings``). An answers file is TOML: ``gate``, an
optional ``day`` (the day the owner ruled; default today, UTC) and ``header``
(the comment lines of a new rulings file), then one table per answered id, as
in a rulings file, except that for a known question ``recommended`` and any
pinned value are filled in (and checked when given) and ``ruling`` defaults to
the chosen option's text. A group's table becomes one table per member, each
with ``batch = <group id>``; a group answered "decide one by one" records
nothing, and a group id that is no longer open is an error. Any other id is
recorded as given, so it must be complete, and a lettered one gets a warning
(a typo, or a queue that has not been built). ``write_ruling`` merges into the
day's file: new ids are appended, an unchanged answer is left alone, and a
changed one is replaced only when the file is in the canonical form this
module writes, so a hand-written comment is never lost.
"""

import hashlib
import importlib
import json
import math
import re
import sys
import tomllib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import clock, jsonio

if TYPE_CHECKING:
    from tff_catalog.paths import Paths

# Gate id -> directory under data/reviews/. Frozen contract.
GATE_DIRS: dict[str, str] = {
    "T": "terms",  # source terms (M1 step 3)
    "M": "method",  # method clarifications
    "C": "config",  # preinstalled.toml and the step 2 config
    "L": "latin",  # Latin thresholds and the dual-script allowlist
    "LIC": "licenses",  # the license queue
    "A": "aliases",
    "U": "unmatched",
    "X": "corrections",  # new preinstalled or dependency cases
    "L3": "l3",  # license verification failures
    "K": "links",  # link overrides
    "R": "review",  # the top lists
    "CI": "ci",  # refresh pull requests and the watchdog
    "SITE": "site",  # site wording (Milestone 2)
}
GATES = tuple(GATE_DIRS)
MAX_OPTIONS = 4
SCHEMA = "review.schema.json"  # under schemas/

# What each gate rules on, for headers and printed questions.
GATE_TITLES: dict[str, str] = {
    "T": "the terms of each data source (Milestone 1, step 3)",
    "M": "method clarifications for Milestone 1",
    "C": "the step 2 config: preinstalled.toml and foundries.toml",
    "L": "the Latin gate: thresholds and the dual-script allowlist (step 5)",
    "LIC": "the license queue (step 6a)",
    "A": "the alias queue (step 7)",
    "U": "unmatched source keys (step 9)",
    "X": "new preinstalled and dependency cases (step 10)",
    "L3": "license verification failures (step 6b)",
    "K": "link overrides (step 14)",
    "R": "the top lists (step 16)",
    "CI": "refresh pull requests and the watchdog (step 19)",
    "SITE": "site wording (Milestone 2)",
}

# Gate -> (where the questions of its queue come from, the stage that writes the queue).
# The source is a "module:function" taking ``paths``, or a JSON file under
# build/stage/queues/ holding a list of objects with ``Question``'s fields.
QUEUE_SOURCES: dict[str, tuple[str, str]] = {
    "L": ("tff_catalog.latin:questions", "latin"),
    "LIC": ("tff_catalog.licenses:questions", "licenses"),
    "A": ("tff_catalog.aliases:questions", "aliases"),
    "U": ("tff_catalog.mapping:questions", "map"),
    "X": ("corrections.json", "correct"),
    "L3": ("tff_catalog.license_l3:questions", "verify"),
    "K": ("tff_catalog.links:questions", "links"),
}

BATCH_SIZE = 4  # questions per batch: the owner's question form takes at most 4 at a time
GROUP_SIZE = 8  # alike rows per group question (design-m1 §7: "batches of about 8")
MIN_GROUP = 2  # a lone row is asked on its own
GROUPED_GATES = frozenset({"L", "A", "U", "X"})  # the row-by-row gates of design-m1 §7
REOPEN = ("research",)  # an answer picking an option that starts so keeps its question open
REVIEW_ROUNDS = 3  # gate R: up to 3 rounds (milestone-1 step 16)
LETTERS = "abcdefghijklmnopqrstuvwxyz"
ANSWER_KEYS = ("choice", "recommended", "ruling", "reason")  # the rest are values
ANSWERS_FILE_KEYS = ("gate", "day", "header")  # top-level keys of an answers file
BATCH_KEY = "batch"  # value naming the group question a member was answered through

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")  # schemas/review.schema.json propertyNames
# A value a question pins for its answer (``pins``), as gate L3 words it.
_PIN = re.compile(r'Record the answer with ([a-z][a-z0-9_]*) = "([^"\\]*)"')
_CHECKOUT_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


class RulingError(ValueError):
    """A rulings or answers file, or a question list, breaks the format."""


def gate_dir(paths: Paths, gate: str) -> Path:
    """``data/reviews/<GATE_DIRS[gate]>/``; ``KeyError`` for an unknown gate."""
    return paths.reviews / GATE_DIRS[gate]


@dataclass(frozen=True, slots=True)
class Question:
    gate: str
    id: str  # "M3", "LIC-dejavu", ...
    text: str
    options: tuple[str, ...]  # at most MAX_OPTIONS
    recommended: int | None = None  # index into options


@dataclass(frozen=True, slots=True)
class Answer:
    """One question's table in a rulings file."""

    id: str  # the table name: "M9", "spacing_filter"
    ruling: str
    reason: str
    choice: str | None = None  # "a", "b", ... for a lettered question
    recommended: bool | None = None  # whether ``choice`` was the recommended option
    values: tuple[
        tuple[str, str | bool | int | float | tuple[str, ...]], ...
    ] = ()  # other keys, sorted


@dataclass(frozen=True, slots=True)
class Ruling:
    """One rulings file: ``data/reviews/<GATE_DIRS[gate]>/<day>.toml``."""

    gate: str
    day: date
    answers: tuple[Answer, ...]  # in file order


@dataclass(frozen=True, slots=True)
class Group:
    """Alike open questions asked as one: "accept all N as recommended?"."""

    question: Question  # id ``all-<gate>-<hash of the member ids>``; option (a) is recommended
    members: tuple[Question, ...]
    picks: tuple[int | None, ...]  # per group option, the members' option; None: one by one


# --- the fixed questions (design-m1 §7) --------------------------------------------------------


def _q(gate: str, qid: str, text: str, *options: str, rec: int | None = 0) -> Question:
    return Question(gate, qid, text, options, rec)


_REVIEW_OPTIONS = (
    "Approve",
    "Change weights in ranking.toml",
    "Change aliases or preinstalled.toml",
    "Another round",
)

CATALOGUE: tuple[Question, ...] = (
    _q(
        "T",
        "T1",
        "Open sources (Homebrew, pkgstats, Arch and Debian package data, popcon, npm, "
        "Fontsource and jsDelivr, GitHub counts, the Web Almanac, ecosyste.ms, Nerd Fonts "
        "fonts.json, the Fontsource registry): what may the catalog and the public repo carry?",
        "Raw values, plus small trimmed real fixtures with credit notices",
        "Ranks only; real fixtures allowed",
        "Ranks only; synthetic fixtures only",
    ),
    _q(
        "T",
        "T2",
        "Google's /metadata/stats and /metadata/fonts endpoints:",
        "Use them; publish ranks and z scores only, never view counts; synthetic public "
        "fixtures; real data only in the private store",
        "Use them and also publish year views",
        "Drop Google",
    ),
    _q(
        "T",
        "T3",
        "Chocolatey's terms forbid scripted access and republishing:",
        "Drop it from v1 and note the Windows gap in methodology §11",
        "Claude drafts a permission request; the collector stays off until a written yes",
        "Use it anyway",
    ),
    _q(
        "T",
        "T4",
        "Fonts Over Time has no license file:",
        "D11's default: use it at the phase-in weight with credit, ranks only, synthetic "
        "fixtures, and send the license request",
        "Wait for an explicit license",
        "Skip it",
    ),
    _q(
        "T",
        "T5",
        "The catalog data license:",
        "Make CC BY-SA 4.0 final now; the ecosyste.ms ShareAlike term requires it anyway",
        "Keep it provisional until the Fonts Over Time author answers",
    ),
    _q(
        "M",
        "M1",
        "The worked example versus the outlier guard:",
        "Keep the guard and change the example to 2.53",
        "Raise the guard gap to 1.6",
        "Apply the guard only with 4 or more terms",
    ),
    _q(
        "M",
        "M2",
        "GitHub's 'at most 24 months' of release history:",
        "All releases of main-channel repos, as growth between snapshots; Iosevka's latest "
        "24 releases through GraphQL",
        "The latest 24 releases per repo",
        "Keep 24 months by publish date, which drops FiraCode and JetBrains Mono",
    ),
    _q(
        "M",
        "M3",
        "The Homebrew floor for Nerd casks:",
        "A separate floor each run: the 10th percentile of Nerd casks (about 2,150 a year)",
        "Keep the flat 20 a year",
    ),
    _q(
        "M",
        "M4",
        "The Arch nerd-fonts group floor:",
        "The 10th percentile of members in the group for 6 months or more; newer members "
        "are not floored",
        "A flat 5.6%, which censors new members",
    ),
    _q(
        "M",
        "M5",
        "GitHub counters versus Homebrew for the 2-group gate:",
        "One group when the cask's url is that repo's release asset",
        "Separate groups",
    ),
    _q(
        "M",
        "M6",
        "The Web Almanac tabs:",
        "The pages tab (gid 1668708562) is the term; the services tab only flags parent merges",
        "The mean of the two tabs' z scores",
        "The services tab only",
    ),
    _q(
        "M",
        "M7",
        "ecosyste.ms scope:",
        "@fontsource and @fontsource-variable only",
        "Also Expo",
    ),
    _q(
        "M",
        "M8",
        "The Linux dependency rule:",
        "The largest single dependent at 50% or more; `a | b` credits the first alternative; "
        "35-50% flagged for review",
        "The sum of all dependents",
    ),
    _q(
        "M",
        "M9",
        "Fonts Over Time's phase-in, with Flutter switched off:",
        "Raw weights; web is 0.40 during the phase-in",
        "Renormalise to keep the group shares at web 0.55, code 0.30, apps 0.15",
    ),
    _q(
        "M",
        "M10",
        "The dev_apps view's weights:",
        "The project weights, rescaled: npm 0.15, ecosyste.ms 0.10, Expo 0.10, Flutter 0.05",
        "Equal weights",
    ),
    _q(
        "M",
        "M11",
        "Top-100 list hysteresis:",
        "Enter at 90 or better; leave after 2 runs worse than 110",
        "None",
    ),
    _q(
        "M",
        "M12",
        "Foundry families:",
        "A hand list in config/foundries.toml, seeded once by Claude from the foundry sites",
        "Scrape the foundry sites monthly",
    ),
    _q(
        "C",
        "C1",
        "config/preinstalled.toml, as shown in chat:",
        "Approve",
        "Approve without the LibreOffice bundle",
        "The owner edits it",
    ),
    _q(
        "C",
        "C2",
        "EndeavourOS has no font dependencies:",
        "Take its fonts into preinstalled.toml from eos-base-group",
        "Drop EndeavourOS",
    ),
    _q(
        "C",
        "C3",
        "config/foundries.toml, as shown in chat (ruling M12):",
        "Approve",
        "The owner edits it",
    ),
    _q(
        "L",
        "L1",
        "Latin thresholds for families outside Google (Claude measures them first):",
        "Kernel fully covered; Core with at most 2 combining marks missing; Latin share at "
        "least 40%; under 1,000 CJK code points",
        "As written: a Latin share of at least 50%",
        "No share test",
    ),
    *(
        _q(
            "R",
            f"R{n}",
            f"Review round {n} of {REVIEW_ROUNDS}: the top lists in build/review-pack/ and "
            "build/review.md.",
            *_REVIEW_OPTIONS,
            rec=None,
        )
        for n in range(1, REVIEW_ROUNDS + 1)
    ),
    _q(
        "CI",
        "CI1",
        "How refresh pull requests get CI:",
        "Claude allows Actions to create pull requests; the refresh job opens the PR with "
        "GITHUB_TOKEN and dispatches ci.yml on refresh/monthly",
        "The owner creates a GitHub App",
        "A fine-grained personal access token",
    ),
    _q(
        "CI",
        "CI2",
        "The watchdog's alert channel:",
        "A VPS timer opens a GitHub issue with a fine-grained token (issues: write, this repo "
        "only) that the owner creates",
        "Email from the VPS, which needs mail set up",
        "A log line plus a manual monthly check",
    ),
)


def catalogue(gate: str) -> tuple[Question, ...]:
    """The fixed questions of ``gate``, answered or not."""
    return tuple(q for q in CATALOGUE if q.gate == gate)


# --- reading -----------------------------------------------------------------------------------


def _file_day(path: Path) -> date:
    try:
        day = date.fromisoformat(path.stem)
    except ValueError:
        day = None
    if day is None or day.isoformat() != path.stem:
        raise RulingError(f"{path}: a rulings file is named <YYYY-MM-DD>.toml")
    return day


def _schema_file(paths: Paths) -> Path:
    # A test tree may have no schemas/; the checkout's copy is the same contract.
    for base in (paths.schemas, _CHECKOUT_SCHEMAS):
        if (base / SCHEMA).is_file():
            return base / SCHEMA
    raise RulingError(f"{SCHEMA} not found in {paths.schemas}")


@cache
def _validator(schema_file: Path) -> Any:
    from jsonschema import Draft202012Validator  # only when a file is checked: keeps --help fast

    return Draft202012Validator(json.loads(schema_file.read_text(encoding="utf-8")))


def check_doc(paths: Paths, doc: Mapping[str, Any], where: str) -> None:
    """Raise ``RulingError`` unless ``doc`` (a parsed rulings file) matches the schema."""
    validator = _validator(_schema_file(paths))
    errors = sorted(validator.iter_errors(doc), key=lambda e: (e.json_path, e.message))
    if errors:
        shown = "; ".join(f"{e.json_path}: {e.message}" for e in errors[:5])
        more = f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""
        raise RulingError(f"{where}: {shown}{more}")


def _freeze(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def answer_of(qid: str, table: Mapping[str, Any]) -> Answer:
    """The ``Answer`` of one schema-checked table."""
    values = sorted((k, _freeze(v)) for k, v in table.items() if k not in ANSWER_KEYS)
    return Answer(
        id=qid,
        ruling=table["ruling"],
        reason=table["reason"],
        choice=table.get("choice"),
        recommended=table.get("recommended"),
        values=tuple(values),
    )


def _parse(text: str, where: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulingError(f"{where}: {exc}") from exc


def read_ruling(paths: Paths, gate: str, path: Path) -> Ruling:
    """One rulings file, checked against the schema."""
    day = _file_day(path)
    doc = _parse(path.read_text(encoding="utf-8"), path)
    check_doc(paths, doc, str(path))
    return Ruling(gate, day, tuple(answer_of(qid, table) for qid, table in doc.items()))


def load_rulings(paths: Paths, gate: str | None = None) -> list[Ruling]:
    """Every ruling (of ``gate``), oldest first; a later ruling overrides an earlier one."""
    out: list[Ruling] = []
    for g in GATES if gate is None else (gate,):
        directory = gate_dir(paths, g)
        if not directory.is_dir():
            continue
        files = sorted((_file_day(p), p) for p in directory.glob("*.toml") if p.is_file())
        out.extend(read_ruling(paths, g, path) for _, path in files)
    out.sort(key=lambda r: (r.day, GATES.index(r.gate)))
    return out


def latest_answers(paths: Paths, gate: str) -> dict[str, tuple[Answer, date]]:
    """The gate's answers by question id, each with its ruling's day; the latest wins."""
    out: dict[str, tuple[Answer, date]] = {}
    for ruling in load_rulings(paths, gate):
        for answer in ruling.answers:
            out[answer.id] = (answer, ruling.day)
    return out


# --- questions ---------------------------------------------------------------------------------


def letter_index(choice: object) -> int | None:
    """``"a"`` -> 0, ``"b"`` -> 1, ...; None for anything that is not one letter."""
    if isinstance(choice, str) and len(choice) == 1 and choice in LETTERS:
        return LETTERS.index(choice)
    return None


def check_questions(gate: str, qs: Iterable[Question], where: str) -> list[Question]:
    """``qs`` as a list, or ``RulingError`` naming the first question the owner cannot answer."""
    out: list[Question] = []
    seen: set[str] = set()
    for q in qs:
        problem = None
        if q.gate != gate:
            problem = f"belongs to gate {q.gate}, not {gate}"
        elif not _ID.fullmatch(q.id):
            problem = "is not a valid id (letters, digits, - and _, at most 64)"
        elif q.id in seen:
            problem = "appears twice"
        elif not 2 <= len(q.options) <= MAX_OPTIONS:
            problem = f"has {len(q.options)} options; single-select needs 2 to {MAX_OPTIONS}"
        elif not q.text.strip() or not all(o.strip() for o in q.options):
            problem = "has an empty text or option"
        elif q.recommended is not None and not 0 <= q.recommended < len(q.options):
            problem = f"recommends option {q.recommended}, which does not exist"
        if problem:
            raise RulingError(f"{where}: question {q.id!r} {problem}")
        seen.add(q.id)
        out.append(q)
    return out


def questions_from_file(path: Path) -> list[Question]:
    """The questions in a queue file: a JSON list of objects with ``Question``'s fields.

    Other keys are ignored. ``FileNotFoundError`` when the file is missing.
    """
    rows = jsonio.load(path)
    if not isinstance(rows, list):
        raise RulingError(f"{path}: expected a list of questions")
    out = []
    for n, row in enumerate(rows):
        fields = row if isinstance(row, dict) else {}
        options = fields.get("options")
        rec = fields.get("recommended")
        texts = [fields.get(k) for k in ("gate", "id", "text")]
        if (
            not all(isinstance(t, str) for t in texts)
            or not isinstance(options, list)
            or not all(isinstance(o, str) for o in options)
            or not (rec is None or (isinstance(rec, int) and not isinstance(rec, bool)))
        ):
            raise RulingError(f"{path}: item {n} is not a question")
        gate, qid, text = texts
        out.append(Question(gate, qid, text, tuple(options), rec))
    return out


def _read_queue(paths: Paths, gate: str, target: str) -> Iterable[Question]:
    if ":" not in target:
        return questions_from_file(paths.queues / target)
    module, _, name = target.partition(":")
    provider = getattr(importlib.import_module(module), name, None)
    if provider is None:
        raise NotImplementedError(f"{target}(paths), gate {gate}'s queue")
    return provider(paths)


def _queue_questions(paths: Paths, gate: str) -> list[Question]:
    source = QUEUE_SOURCES.get(gate)
    if source is None:
        return []
    target, stage = source
    try:
        rows = list(_read_queue(paths, gate, target))
    except RulingError:
        raise
    except (ValueError, KeyError) as exc:  # a corrupt or outdated queue file
        raise RulingError(
            f"gate {gate}'s queue ({target}) cannot be read: {exc}; run `tff-catalog {stage}` again"
        ) from exc
    return check_questions(gate, rows, target)


def gate_questions(paths: Paths, gate: str) -> list[Question]:
    """Every question of the gate, answered or not: ``CATALOGUE``, then the stage's queue.

    Raises ``FileNotFoundError`` when the owning stage has not written its queue
    yet, ``NotImplementedError`` when its module has no ``questions``, and
    ``RulingError`` when the queue cannot be read or holds a question the owner
    cannot answer.
    """
    merged = {q.id: q for q in catalogue(gate)}
    merged.update((q.id, q) for q in _queue_questions(paths, gate))
    return list(merged.values())


def _round_ready(q: Question, answers: Mapping[str, tuple[Answer, date]]) -> bool:
    """A review round after the first opens only once the one before asked for changes."""
    m = re.fullmatch(r"R(\d+)", q.id)
    if q.gate != "R" or m is None or int(m[1]) == 1:
        return True
    before = answers.get(f"R{int(m[1]) - 1}")
    return before is not None and before[0].choice not in (None, "a")


def reopens(q: Question, answer: Answer) -> bool:
    """Whether ``answer`` keeps ``q`` open: it picked a "research more" option."""
    i = letter_index(answer.choice)
    return i is not None and i < len(q.options) and q.options[i].casefold().startswith(REOPEN)


def pins(q: Question) -> dict[str, str]:
    """The values ``q`` pins for its answer: ``Record the answer with <key> = "<value>"``.

    Gate L3 pins ``text_sha256``, the license text a ruling is about, because
    its ruling stops applying when the text changes.
    """
    return dict(_PIN.findall(q.text))


def stale_pins(q: Question, answer: Answer) -> list[str]:
    """The keys ``q`` pins that ``answer`` does not carry with the pinned value."""
    values = dict(answer.values)
    return [key for key, value in pins(q).items() if values.get(key) != value]


def settles(q: Question, answer: Answer) -> bool:
    """Whether ``answer`` closes ``q``: no "research more" option and every pinned value."""
    return not reopens(q, answer) and not stale_pins(q, answer)


def pending(qs: Iterable[Question], answers: Mapping[str, tuple[Answer, date]]) -> list[Question]:
    """The questions of ``qs`` that are still open under ``answers`` (``latest_answers``)."""
    out = []
    for q in qs:
        got = answers.get(q.id)
        if _round_ready(q, answers) and (got is None or not settles(q, got[0])):
            out.append(q)
    return out


def questions(paths: Paths, gate: str) -> list[Question]:
    """The gate's questions that have no ruling yet."""
    return pending(gate_questions(paths, gate), latest_answers(paths, gate))


def group_of(members: Sequence[Question]) -> Group:
    """One "accept all N as recommended?" question for alike ``members`` (``plan``)."""
    lead = members[0]
    rec = lead.recommended
    if rec is None or any((q.options, q.recommended) != (lead.options, rec) for q in members):
        raise RulingError("a group needs questions with the same options and recommendation")
    n = len(members)
    others = [i for i in range(len(lead.options)) if i != rec][: MAX_OPTIONS - 2]
    options = (
        f"Yes, all {n}: ({LETTERS[rec]}) {lead.options[rec]}",
        *(f"All {n}: ({LETTERS[i]}) {lead.options[i]}" for i in others),
        "No, decide one by one",
    )
    ids = "\n".join(sorted(q.id for q in members))
    digest = hashlib.sha256(ids.encode("utf-8")).hexdigest()[:10]
    text = (
        f"Accept all {n} as recommended? Each one's recommended answer is "
        f"({LETTERS[rec]}) {lead.options[rec]}."
    )
    question = Question(lead.gate, f"all-{lead.gate}-{digest}", text, options, 0)
    return Group(question, tuple(members), (rec, *others, None))


def plan(qs: Sequence[Question], open_ids: Collection[str] | None = None) -> list[Question | Group]:
    """How the open questions are asked: in ``GROUPED_GATES``, alike rows are grouped.

    ``qs`` is every question of the gate in queue order, answered or not, and
    ``open_ids`` the ids still open (default: all of ``qs``). Rows are alike
    when they share options and recommendation. The alike rows of ``qs`` are cut
    into chunks of ``GROUP_SIZE`` in order, answered ones included, so that
    answering one chunk's rows never moves another chunk's boundaries or id.
    A chunk's open rows become a ``Group``, or are asked alone when there are
    fewer than ``MIN_GROUP``. Items keep the order of their first question.
    """
    first = {q.id: i for i, q in enumerate(qs)}
    is_open = {q.id: open_ids is None or q.id in open_ids for q in qs}
    alike: dict[tuple[tuple[str, ...], int], list[Question]] = {}
    items: list[Question | Group] = []
    for q in qs:
        if q.gate in GROUPED_GATES and q.recommended is not None:
            alike.setdefault((q.options, q.recommended), []).append(q)
        elif is_open[q.id]:
            items.append(q)
    for rows in alike.values():
        for start in range(0, len(rows), GROUP_SIZE):
            chunk = [q for q in rows[start : start + GROUP_SIZE] if is_open[q.id]]
            if len(chunk) < MIN_GROUP:
                items.extend(chunk)
            else:
                items.append(group_of(chunk))
    items.sort(key=lambda item: first[_lead(item).id])
    return items


def open_items(
    qs: Sequence[Question], answers: Mapping[str, tuple[Answer, date]]
) -> list[Question | Group]:
    """``plan`` of the gate's questions ``qs`` under ``answers``: what is asked now."""
    return plan(qs, {q.id for q in pending(qs, answers)})


def _lead(item: Question | Group) -> Question:
    return item.members[0] if isinstance(item, Group) else item


# --- writing -----------------------------------------------------------------------------------

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_str(text: str) -> str:
    out = []
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RulingError(f"{value!r} cannot go in a rulings file")
        return repr(value)
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise RulingError(f"a {type(value).__name__} cannot go in a rulings file: {value!r}")


def table_of(answer: Answer) -> dict[str, Any]:
    """``answer`` as its TOML table, keys in file order: choice, recommended, values, ruling, reason."""
    out: dict[str, Any] = {}
    if answer.choice is not None:
        out["choice"] = answer.choice
    if answer.recommended is not None:
        out["recommended"] = answer.recommended
    for key, value in sorted(answer.values, key=lambda kv: kv[0]):
        if key in ANSWER_KEYS or key in out:
            raise RulingError(f"{answer.id}: value {key!r} is reserved or repeated")
        out[key] = list(value) if isinstance(value, tuple) else value
    out["ruling"] = answer.ruling
    out["reason"] = answer.reason
    return out


def _block(answer: Answer) -> str:
    lines = [f"[{answer.id}]"]
    lines += [f"{key} = {_toml_value(value)}" for key, value in table_of(answer).items()]
    return "\n".join(lines)


def render_ruling(answers: Sequence[Answer], header: Sequence[str] = ()) -> str:
    """A rulings file's text: the header as comment lines, a blank line, one table per answer."""
    head = "".join(f"# {line}\n" if line else "#\n" for line in header)
    body = "\n\n".join(_block(a) for a in answers)
    return (head + "\n" if head else "") + body + "\n"


def header_of(text: str) -> tuple[str, ...]:
    """The comment lines at the top of a rulings file, without their ``# ``."""
    out = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        out.append(line[2:] if line.startswith("# ") else line[1:])
    return tuple(out)


def default_header(gate: str, day: date) -> tuple[str, ...]:
    """The header of a new rulings file when the caller gives none."""
    return (
        f"Owner rulings, gate {gate}: {GATE_TITLES[gate]}.",
        f"Ruled by the owner in chat on {day.isoformat()}; recorded with "
        "`tff-catalog rulings apply`.",
        "Each ruling: choice = the option picked; recommended = whether it was the recommended",
        "option; ruling = what it means; reason = why, and whose words those are.",
    )


@dataclass(frozen=True, slots=True)
class Written:
    """What ``record`` did to one rulings file."""

    path: Path
    added: tuple[str, ...]  # question ids
    replaced: tuple[str, ...]
    unchanged: tuple[str, ...]


def _merge(
    old: Sequence[Answer], new: Sequence[Answer]
) -> tuple[list[Answer], list[str], list[str], list[str]]:
    merged = {a.id: a for a in old}
    added, replaced, unchanged = [], [], []
    for a in new:
        before = merged.get(a.id)
        if before is None:
            added.append(a.id)
        elif table_of(before) == table_of(a):
            unchanged.append(a.id)
            continue
        else:
            replaced.append(a.id)
        merged[a.id] = a
    return list(merged.values()), added, replaced, unchanged


def _merged_text(
    path: Path,
    old_text: str | None,
    old: Sequence[Answer],
    merged: Sequence[Answer],
    added: Sequence[str],
    replaced: Sequence[str],
    header: Sequence[str],
) -> str:
    if old_text is None:
        return render_ruling(merged, header)
    old_header = header_of(old_text)
    if render_ruling(old, old_header) == old_text:  # canonical: nothing is lost by rewriting
        return render_ruling(merged, old_header)
    if replaced:
        raise RulingError(
            f"{path} has hand edits and already rules on {', '.join(replaced)} differently; "
            "change it by hand"
        )
    new = {a.id: a for a in merged}
    return old_text.rstrip("\n") + "\n\n" + render_ruling([new[i] for i in added])


def record(paths: Paths, ruling: Ruling, header: Sequence[str] | None = None) -> Written:
    """Merge ``ruling`` into its day's file (module docstring); ``header`` is for a new file."""
    if ruling.gate not in GATE_DIRS:
        raise RulingError(f"unknown gate {ruling.gate!r}")
    ids = [a.id for a in ruling.answers]
    if not ids or len(set(ids)) != len(ids):
        raise RulingError(f"gate {ruling.gate}: a ruling needs answers, each id once: {ids}")
    path = gate_dir(paths, ruling.gate) / f"{ruling.day.isoformat()}.toml"
    old_text = path.read_text(encoding="utf-8") if path.is_file() else None
    old = read_ruling(paths, ruling.gate, path).answers if old_text is not None else ()
    merged, added, replaced, unchanged = _merge(old, ruling.answers)
    if added or replaced:
        doc = {a.id: table_of(a) for a in merged}
        check_doc(paths, doc, str(path))
        head = default_header(ruling.gate, ruling.day) if header is None else header
        text = _merged_text(path, old_text, old, merged, added, replaced, head)
        if _parse(text, path) != doc:
            raise RulingError(f"{path}: the written text does not read back as the rulings")
        jsonio.atomic_write(path, text.encode("utf-8"))
    return Written(path, tuple(added), tuple(replaced), tuple(unchanged))


def write_ruling(paths: Paths, ruling: Ruling) -> Path:
    """Write ``data/reviews/<GATE_DIRS[gate]>/<day>.toml``."""
    return record(paths, ruling).path


def audit(paths: Paths, gate: str | None = None) -> list[str]:
    """Rulings that contradict ``CATALOGUE``: a choice it lacks, or a wrong ``recommended``."""
    by_id = {(q.gate, q.id): q for q in CATALOGUE}
    problems = []
    for ruling in load_rulings(paths, gate):
        for a in ruling.answers:
            q = by_id.get((ruling.gate, a.id))
            if q is None or a.choice is None:
                continue
            i = letter_index(a.choice)
            where = f"{ruling.gate} {ruling.day.isoformat()} [{a.id}]"
            if i is None or i >= len(q.options):
                problems.append(f"{where}: choice {a.choice!r} is not an option")
            elif a.recommended != (i == q.recommended):
                problems.append(f"{where}: recommended = {a.recommended} is wrong")
    return problems


# --- rulings apply -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnswersFile:
    """A parsed answers file (module docstring, "Recording")."""

    gate: str
    day: date | None
    header: tuple[str, ...] | None
    tables: dict[str, dict[str, Any]]  # in file order


def _answers_day(value: object, path: Path) -> date | None:
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            day = None
        if day is not None and day.isoformat() == value:
            return day
    raise RulingError(f"{path}: day must be a date such as 2026-10-01, got {value!r}")


def _header_lines(value: object, path: Path) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, list) and all(
        isinstance(v, str) and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in v) for v in value
    ):
        return tuple(value)
    raise RulingError(f"{path}: header must be a list of one-line strings")


def read_answers_file(path: Path) -> AnswersFile:
    """Parse an answers file: ``gate``, optional ``day`` and ``header``, then answer tables."""
    doc = _parse(path.read_text(encoding="utf-8"), path)
    gate = doc.pop("gate", None)
    if gate not in GATE_DIRS:
        raise RulingError(f"{path}: gate must be one of {', '.join(GATES)}, got {gate!r}")
    day = _answers_day(doc.pop("day", None), path)
    header = _header_lines(doc.pop("header", None), path)
    for key, value in doc.items():
        if not isinstance(value, dict):
            raise RulingError(
                f"{path}: {key} must be a table ([{key}]); the only other top-level keys are "
                f"{', '.join(ANSWERS_FILE_KEYS)}"
            )
    if not doc:
        raise RulingError(f"{path}: no answers")
    return AnswersFile(gate, day, header, doc)


def _choice_index(qid: str, table: Mapping[str, Any], q: Question) -> int:
    i = letter_index(table.get("choice"))
    if i is None or i >= len(q.options):
        last = LETTERS[len(q.options) - 1]
        raise RulingError(f"[{qid}]: choice must be one of a-{last}, got {table.get('choice')!r}")
    return i


def _complete(qid: str, table: Mapping[str, Any], q: Question) -> dict[str, Any]:
    """A known question's answer, with ``recommended``, pinned values and a default ``ruling``."""
    i = _choice_index(qid, table, q)
    rec = i == q.recommended
    given = table.get("recommended", rec)
    if given is not rec:
        what = "is" if rec else "is not"
        raise RulingError(
            f"[{qid}]: recommended = {given!r}, but ({LETTERS[i]}) {what} the recommended option"
        )
    row = {**table, "recommended": rec, "ruling": table.get("ruling", q.options[i])}
    for key, value in pins(q).items():
        if not value:  # the schema's text is non-empty, so the ruling could never settle it
            raise RulingError(
                f'[{qid}]: the question pins {key} = "", which a rulings file cannot hold '
                f"({SCHEMA} needs non-empty text); its stage must pin a non-empty value"
            )
        if row.setdefault(key, value) != value:
            raise RulingError(
                f"[{qid}]: {key} = {row[key]!r}, but the question is about {value!r}; "
                "print the questions again"
            )
    return row


def expand(group: Group, table: Mapping[str, Any]) -> dict[str, dict[str, Any]] | None:
    """One table per member for a group's answer; None when the owner decides one by one."""
    gid = group.question.id
    _complete(gid, table, group.question)  # checks choice and any recommended flag
    pick = group.picks[_choice_index(gid, table, group.question)]
    if pick is None:
        return None
    if "reason" not in table:
        raise RulingError(f"[{gid}]: reason is required")
    extra = {k: v for k, v in table.items() if k not in ANSWER_KEYS}
    out = {}
    for m in group.members:
        row = {"choice": LETTERS[pick], **extra, BATCH_KEY: gid, "reason": table["reason"]}
        if "ruling" in table:
            row["ruling"] = table["ruling"]
        out[m.id] = _complete(m.id, row, m)
    return out


@dataclass(frozen=True, slots=True)
class Applied:
    """What ``apply_answers`` recorded."""

    gate: str
    written: Written | None  # None when nothing was left to record
    one_by_one: tuple[Group, ...]  # groups answered "decide one by one"
    warnings: tuple[str, ...]


def _known(paths: Paths, gate: str, warnings: list[str]) -> list[Question]:
    try:
        return gate_questions(paths, gate)
    except (FileNotFoundError, NotImplementedError) as exc:
        stage = QUEUE_SOURCES[gate][1]
        warnings.append(
            f"gate {gate}'s queue is not available ({exc}; run `tff-catalog {stage}`), "
            "so answers to its items are recorded as given"
        )
        return list(catalogue(gate))


def resolve(
    paths: Paths, answers_file: AnswersFile
) -> tuple[dict[str, dict[str, Any]], list[Group], list[str]]:
    """The tables to record, the groups left to decide one by one, and warnings."""
    gate, warnings = answers_file.gate, []
    known = _known(paths, gate, warnings)
    earlier = latest_answers(paths, gate)
    by_id = {q.id: q for q in known}
    groups = {g.question.id: g for g in open_items(known, earlier) if isinstance(g, Group)}
    group_id = re.compile(rf"all-{re.escape(gate)}-[0-9a-f]{{10}}")
    doc: dict[str, dict[str, Any]] = {}
    skipped: list[Group] = []
    for qid, table in answers_file.tables.items():
        if qid in groups:
            rows = expand(groups[qid], table)
            if rows is None:
                skipped.append(groups[qid])
                continue
        elif group_id.fullmatch(qid):
            raise RulingError(
                f"[{qid}] is not an open group of gate {gate}: some of its rows were answered, "
                "or the queue changed, since it was printed. Print the questions again "
                f"(`tff-catalog questions --gate {gate}`), or answer its rows by their own ids"
            )
        elif qid in by_id:
            rows = {qid: _complete(qid, table, by_id[qid])}
        else:
            if "choice" in table and qid not in earlier:  # a lettered answer: likely a typo
                warnings.append(
                    f"[{qid}] is not a known question of gate {gate}; recorded as given"
                )
            rows = {qid: dict(table)}
        for rid, row in rows.items():
            if rid in doc:
                raise RulingError(f"[{rid}] is answered twice")
            doc[rid] = row
    return doc, skipped, warnings


def apply_answers(paths: Paths, file: Path) -> Applied:
    """Check an answers file and merge it into ``data/reviews/<gate dir>/<day>.toml``."""
    answers_file = read_answers_file(file)
    doc, skipped, warnings = resolve(paths, answers_file)
    if not doc:
        return Applied(answers_file.gate, None, tuple(skipped), tuple(warnings))
    check_doc(paths, doc, str(file))
    day = answers_file.day or clock.utc_today()
    answers = tuple(answer_of(qid, table) for qid, table in doc.items())
    written = record(paths, Ruling(answers_file.gate, day, answers), answers_file.header)
    return Applied(answers_file.gate, written, tuple(skipped), tuple(warnings))


# --- commands ----------------------------------------------------------------------------------


def _options(q: Question) -> list[str]:
    return [
        f"- ({LETTERS[i]}) {text}{' (rec)' if i == q.recommended else ''}"
        for i, text in enumerate(q.options)
    ]


def _render_item(item: Question | Group, earlier: Mapping[str, tuple[Answer, date]]) -> list[str]:
    if isinstance(item, Group):
        q, lead = item.question, item.members[0]
        each = ", ".join(
            f"({LETTERS[i]}) {t}{' (rec)' if i == lead.recommended else ''}"
            for i, t in enumerate(lead.options)
        )
        lines = [f"### {q.id} (a group of {len(item.members)})", q.text, *_options(q)]
        lines += ["", f"Its questions, each with the options {each}:"]
        return lines + [f"- {m.id}: {m.text}" for m in item.members]
    lines = [f"### {item.id}", item.text, *_options(item)]
    if item.id in earlier:
        answer, day = earlier[item.id]
        stale = stale_pins(item, answer)
        about = f", on another {' and '.join(stale)}" if stale else ""
        lines.append(
            f"Earlier ruling ({day.isoformat()}{about}): ({answer.choice}) {answer.ruling}"
        )
    return lines


def _n(count: int, noun: str) -> str:
    plural = noun + ("es" if noun.endswith("ch") else "s")
    return f"{count} {noun if count == 1 else plural}"


def _answers_help(gate: str, example: str) -> list[str]:
    return [
        "## Answers file",
        "",
        "```toml",
        f'gate = "{gate}"',
        f"day = {clock.utc_today().isoformat()}  # the day the owner ruled (default: today, UTC)",
        'header = ["Owner rulings, gate ...", "Ruled by the owner in chat on ..."]  # optional',
        "",
        f"[{example}]  # one table per answered question or group id",
        'choice = "a"  # the option picked',
        'reason = "..."  # why, and whose words those are',
        "# ruling defaults to the option's text; recommended is filled in; other keys are values",
        "```",
    ]


def render_questions(
    gate: str,
    items: Sequence[Question | Group],
    earlier: Mapping[str, tuple[Answer, date]],
    unlisted: str = "",
) -> str:
    """The text ``questions --gate`` prints: the open questions in batches of ``BATCH_SIZE``.

    ``unlisted`` says why the gate's queue is missing from ``items``, if it is.
    """
    where = f"data/reviews/{GATE_DIRS[gate]}/"
    lines = [f"# Gate {gate}: {GATE_TITLES[gate]}", ""]
    if unlisted:
        lines += [f"Only the fixed questions are listed, not the queue: {unlisted}.", ""]
    if not items:
        latest = max((day for _, day in earlier.values()), default=None)
        when = f", the latest on {latest.isoformat()}" if latest else ""
        lines.append(f"No open questions. {len(earlier)} answered in `{where}`{when}.")
        return "\n".join(lines) + "\n"
    count = sum(len(i.members) if isinstance(i, Group) else 1 for i in items)
    batches = [items[i : i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    lines += [
        f"{_n(count, 'open question')}, asked as {len(items)} in {_n(len(batches), 'batch')} "
        f"of up to {BATCH_SIZE}. {len(earlier)} answered so far in `{where}`.",
        "",
        "Ask each batch in chat as single-select questions (never multi-select), with the "
        "options below; (rec) marks the recommended one. Write the answers to a TOML file "
        "and run `tff-catalog rulings apply FILE`.",
    ]
    if any(isinstance(i, Group) for i in items):
        lines += [
            "",
            "When the owner decides a group one by one, ask its questions in later batches, "
            "each with the options listed under the group, and answer them by their own ids.",
        ]
    for n, batch in enumerate(batches, 1):
        lines += ["", f"## Batch {n} of {len(batches)}"]
        for item in batch:
            lines += ["", *_render_item(item, earlier)]
    first = items[0].question.id if isinstance(items[0], Group) else items[0].id
    lines += ["", *_answers_help(gate, first)]
    return "\n".join(lines) + "\n"


def cmd_questions(paths: Paths, gate: str) -> int:
    """``tff-catalog questions --gate G``."""
    status, note = 0, ""
    try:
        earlier = latest_answers(paths, gate)
        try:
            known = gate_questions(paths, gate)
        except FileNotFoundError as exc:
            known, status = list(catalogue(gate)), 1
            stage = QUEUE_SOURCES[gate][1]
            note = f"no queue yet ({exc.filename or exc}); run `tff-catalog {stage}` first"
        except NotImplementedError as exc:
            known, status = list(catalogue(gate)), 2
            note = f"{exc} is not implemented yet"
        sys.stdout.write(render_questions(gate, open_items(known, earlier), earlier, note))
    except RulingError as exc:
        print(f"tff-catalog questions: {exc}", file=sys.stderr)
        return 1
    if note:
        print(f"tff-catalog questions: gate {gate}: {note}", file=sys.stderr)
    return status


def _where(path: Path, paths: Paths) -> str:
    return str(path.relative_to(paths.root)) if path.is_relative_to(paths.root) else str(path)


def cmd_apply_rulings(paths: Paths, file: Path) -> int:
    """``tff-catalog rulings apply FILE``."""
    try:
        applied = apply_answers(paths, file)
    except (RulingError, OSError) as exc:
        print(f"tff-catalog rulings apply: {exc}", file=sys.stderr)
        return 1
    for warning in applied.warnings:
        print(f"tff-catalog rulings apply: warning: {warning}", file=sys.stderr)
    w = applied.written
    if w is None:
        print("Nothing to record.")
    else:
        total = len(w.added) + len(w.replaced) + len(w.unchanged)
        print(
            f"{_where(w.path, paths)}: {_n(total, 'answer')} ({len(w.added)} new, "
            f"{len(w.replaced)} replaced, {len(w.unchanged)} already recorded)."
        )
    for g in applied.one_by_one:
        print(f"{g.question.id}: decided one by one, so its {len(g.members)} questions stay open.")
    try:
        left = len(questions(paths, applied.gate))
    except FileNotFoundError, NotImplementedError, RulingError:
        return 0  # the rulings are recorded; only the open count is unknown
    print(f"Gate {applied.gate}: {_n(left, 'open question')} left.")
    return 0
