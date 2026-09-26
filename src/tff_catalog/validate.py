"""Stage "validate": schemas and the hard checks (methodology §9, milestone-1 step 15). Owner: agent P12.

Hard failures block the refresh pull request: an ineligible font ranked; a
flagged license missing from the queue; a schema failure; a rerun from the
same snapshots giving different output; an ineligible font moving any rank; a
failed known answer (Source Sans Pro → Source Sans 3, Sauce Code Pro → Source
Code Pro, Roboto Slab gets no Roboto counts, a renamed family keeps its id); a
higher count lowering a rank without the guard; the desktop views differing
except by abstentions; an abstaining Linux count moving the overall rank; a
raw source ``value`` in ``catalog.json`` (or any public output) for a source
whose ``publish_raw`` is false (rulings T2 and T4: Google and Fonts Over Time
publish ranks and rank-based z only).

How each check reads the run (``CHECKS``, in order; ``docs/catalog-schema.md``):

- ``schema``: the three outputs against their schemas; ``catalog-site.json``
  also against ``tff_site.data.semantic_errors``, the site's cross-references.
- ``ineligible_ranked``: every family with a term or a ruler count passes the
  gates before ranking (``export.ineligible`` without L3: stage "correct" runs
  before "verify"); every placed, scored or ruler family passes them and L3
  too; every catalog font also has an allowed license (``export.publishable``)
  at L3 or an owner ruling. A license waiting for the owner may be ranked, as
  stage "correct" keeps it, but not catalogued. With no ineligible family in
  ``terms.json``, ``ruler_counts.json`` or ``ruler.json``, none can move a
  rank (filter first, methodology §1).
- ``license_queue``: every candidate's verdict that needs the owner, and every
  failed L3 check the owner has not excluded (gate L3 (a)), is named in
  ``build/stage/queues/{licenses,l3}.json``.
- ``rerun_differs``: export rebuilt in memory from the same stage files gives
  the bytes on disk (``code_commit`` aside). The whole-pipeline replay is
  refresh's (``--from-snapshots``, milestone-1 step 18).
- ``known_answer``: the four known answers, each checked when the families it
  names are in the universe (a synthetic run has none of them); no mapped
  count passes a "distinct" row (``aliases.Blocks``); and no committed id
  changes its ``minted_from`` or loses its name to a new id.
- ``higher_count_lower_rank``: among fonts with the same terms (sources and
  weight factors) in a view and no guard event, one whose every value is at
  least another's, and one higher, never scores lower. Fonts with a Fonts Over
  Time term are skipped (its z is smoothed across months), and so is Rising
  (shares, not counts); the engine's property tests cover both.
- ``desktop_views_differ``: most chosen's terms equal most installed's, except
  Linux sources' missing ones; with no abstention at all the orders match.
- ``abstention_leak``: a family a Linux source abstains for in most chosen has
  no term from it in any other abstaining view, and ``weight_used`` null.
- ``raw_value_published``: no ``value`` for a source whose ``publish_raw`` is
  false in ``catalog.json``, the file's ``publish_raw`` equals the config's,
  and no committed report (``build/*.md``, ``docs/backtests/*.md``) shows such
  a source's values: a term's value, or a parsed record's of a collector only
  such sources read, of ``RAW_MIN`` or more (rounded or cut to a whole number,
  as a report may print it), on a line that also names its font (id, family
  name or source key). Both must match, so another source's published count
  that happens to equal it is no failure; the failure never repeats the number.
  Values under ``RAW_MIN`` (all of Fonts Over Time's, a few thousand sites at
  most) can't be told from chance, so the stages writing reports keep them out
  (``corrections``, ``mapping``, ``review``).

A check that can't run (a missing file, or any error in it) fails with the
reason, and the others still run. Each check lists at most ``MAX_PER_CHECK``
failures. Failures are logged and refresh turns them into a public issue, so no
message repeats a raw value: the ``value`` fields a schema error quotes, and any
number equal to a hidden value, read ``REDACTED``.
"""

import itertools
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tff_catalog import export, jsonio, stageio
from tff_catalog.records import SourceKey

if TYPE_CHECKING:
    from tff_catalog.paths import Paths
    from tff_catalog.stages import StageContext

EPS = 1e-9  # score tolerance in the monotonicity check
MAX_PER_CHECK = 50  # failures listed per check; one more line says there are more
RAW_MIN = 100_000  # smallest whole value the report scan looks for
SMOOTHED_SOURCES = frozenset({"fot"})  # z carried across months (§6 EWMA)
KNOWN_RENAMES = (("Source Sans Pro", "Source Sans 3"),)  # (old name, family)
KNOWN_BUILDS = (("Sauce Code Pro", "Source Code Pro"),)  # (patched name, family)
KNOWN_DISTINCT = (("Roboto Slab", "Roboto"),)  # (family, family it gets no counts of)


class ValidationFailed(RuntimeError):
    """At least one hard check failed; the message lists them."""

    def __init__(self, failures: Sequence[Failure] = ()) -> None:
        self.failures = tuple(failures)
        lines = [str(f) for f in self.failures]
        super().__init__(f"{len(lines)} hard check(s) failed:\n" + "\n".join(lines))


@dataclass(frozen=True, slots=True)
class Failure:
    check: str
    message: str
    family_id: str | None = None

    def __str__(self) -> str:
        who = f" [{self.family_id}]" if self.family_id else ""
        return f"{self.check}{who}: {self.message}"


# --- schemas -----------------------------------------------------------------------------------


def _pointer(parts: Iterable[str | int]) -> str:
    escaped = [str(p).replace("~", "~0").replace("/", "~1") for p in parts]
    return "/" + "/".join(escaped) if escaped else "/"


def schema_errors(path: Path, schema: Path) -> list[str]:
    """Every JSON Schema (2020-12) error of the document at ``path``, as "pointer: message".

    Pointers are JSON pointers (RFC 6901), with "/" for the whole document. A
    file that is missing or not JSON gives one line naming it.
    """
    from jsonschema import Draft202012Validator

    try:
        doc = jsonio.load(path)
    except (OSError, ValueError) as exc:
        return [f"{path}: {exc}"]
    validator = Draft202012Validator(jsonio.load(schema))
    found = sorted(
        validator.iter_errors(doc), key=lambda e: ([str(p) for p in e.absolute_path], e.message)
    )
    return [f"{_pointer(e.absolute_path)}: {_short(e.message)}" for e in found]


def _short(message: str, limit: int = 300) -> str:
    return message if len(message) <= limit else message[: limit - 1] + "\u2026"


# --- the run as the checks see it --------------------------------------------------------------


class Run:
    """The outputs and stage files of one run, each read on first use."""

    def __init__(self, ctx: StageContext) -> None:
        self.ctx = ctx
        self.cfg = ctx.config
        self.paths = ctx.paths
        self._docs: dict[str, Any] = {}
        self.inputs = export.Inputs(ctx.paths)

    def doc(self, name: str) -> Any:
        """An output under ``build/`` (``export.SCHEMA_FILES``), or ``export.MissingInput``."""
        if name not in self._docs:
            path = self.paths.build / name
            if not path.is_file():
                raise export.MissingInput(f"{path} is missing: run the export stages first")
            self._docs[name] = jsonio.load(path)
        return self._docs[name]

    def optional(self, name: str) -> Any:
        """Stage file ``name`` (``stageio.STAGE_FILES``), or None when it does not exist."""
        if not stageio.stage_path(self.paths, name).is_file():
            return None
        return stageio.load_stage(self.paths, name)

    @cached_property
    def hidden(self) -> dict[int, set[str]]:
        """The values no output may show (``_hidden_values``), each with its font's labels."""
        return _hidden_values(self)

    @cached_property
    def mapped(self) -> list[Any]:
        """The mapped observations of stage "map" (``mapping.Mapped``); [] before it runs."""
        from tff_catalog.records import Observation

        return [m for m in self.optional("mapped") or () if isinstance(m.record, Observation)]


Check = Callable[[Run], Iterator[Failure]]


def check_schema(run: Run) -> Iterator[Failure]:
    """The three outputs against their schemas, and the site file's cross-references."""
    for name, schema in export.SCHEMA_FILES.items():
        errors = schema_errors(run.paths.build / name, run.paths.schemas / schema)
        for line in errors:
            yield Failure("schema", f"{name} {line}")
        if name == export.SITE_FILE and not errors:
            from tff_site.data import semantic_errors

            for line in semantic_errors(run.doc(name)):
                yield Failure("schema", f"{name} {line}")


def check_ineligible_ranked(run: Run) -> Iterator[Failure]:
    """Nothing ineligible is placed, catalogued or given a term; catalog licenses are verified.

    Each file is held to the gates that come before the stage writing it: terms and
    ruler counts (stage "correct") to the gates before ranking; the ruler, scores and
    placements (stage "rank", rerun after "verify") also to L3; the catalog also to an
    allowed license at L3 or an owner ruling. A family whose license waits for the
    owner is ranked like any other until then (``corrections.eligible_families``),
    but never catalogued.
    """
    inputs = run.inputs
    gates = _memo(lambda fid: export.ineligible(fid, inputs, l3=False))
    ranked = _memo(lambda fid: export.ineligible(fid, inputs))
    listed = _memo(lambda fid: export.publishable(fid, inputs))
    seen: dict[str, list[str]] = defaultdict(list)
    whys: dict[str, str] = {}

    def note(fid: str, why: str | None, where: str) -> None:
        if why:
            seen[fid].append(where)
            whys.setdefault(fid, why)

    for font in run.doc(export.CATALOG_FILE)["fonts"]:
        fid = font["id"]
        note(fid, listed(fid), "in catalog.json")
        l3 = inputs.l3.get(fid)
        if l3 is None or l3.level not in ("L3", "ruling"):
            level = "no L3 check" if l3 is None else f"L3 {l3.level}"
            yield Failure(
                "ineligible_ranked", f"catalog font's license is unverified ({level})", fid
            )
    for key, placed in sorted(inputs.ranks.items()):
        for fid in sorted(placed):
            note(fid, ranked(fid), f"placed in {key}")
    for key, view in sorted(inputs.scores.items()):
        for fid in sorted(view.fused):
            note(fid, ranked(fid), f"scored in {key}")
    for fid in sorted(run.optional("ruler") or {}):
        note(fid, ranked(fid), "in ruler.json")
    for key, by_source in sorted(inputs.terms.items()):
        for source, by_id in sorted(by_source.items()):
            for fid in sorted(by_id):
                note(fid, gates(fid), f"term {key}/{source}")
    for fid in sorted(run.optional("ruler_counts") or {}):
        note(fid, gates(fid), "in ruler_counts.json")
    for fid, where in sorted(seen.items()):
        shown = ", ".join(where[:5]) + (f" and {len(where) - 5} more" if len(where) > 5 else "")
        yield Failure("ineligible_ranked", f"{whys[fid]}, but {shown}", fid)


def check_license_queue(run: Run) -> Iterator[Failure]:
    """Every candidate verdict that needs the owner, and every L3 failure, is in a queue."""
    inputs = run.inputs
    queued = _mentioned(run.paths.queues / "licenses.json")
    for fid in sorted(inputs.universe.families):
        fam = inputs.universe.families[fid]
        latin = inputs.latin.get(fid)
        if fam.drop is not None or latin is None or not latin.latin:
            continue  # not a candidate: the Latin gate failures never queue
        verdict = inputs.licenses.get(fid)
        if verdict is None:
            yield Failure("license_queue", "candidate has no license verdict", fid)
        elif verdict.queue is not None and fid not in queued:
            yield Failure("license_queue", f"flagged ({verdict.queue}) but not queued", fid)
    from tff_catalog.license_l3 import load_l3_rulings

    l3_queued = _mentioned(run.paths.queues / "l3.json")
    rulings = load_l3_rulings(run.paths)
    for fid, result in sorted(inputs.l3.items()):
        if result.level != "failed" or fid in l3_queued:
            continue
        ruling = rulings.get(fid)
        if ruling is not None and ruling.choice == "a" and ruling.covers(result.text_sha256):
            continue  # the owner excluded it (gate L3 (a)): nothing left to decide
        yield Failure("license_queue", "L3 failed but not in queues/l3.json", fid)


def check_rerun(run: Run) -> Iterator[Failure]:
    """Export rebuilt from the same stage files gives the same bytes as on disk.

    The rebuild uses the run date and commit the catalog records, so running
    validate on a later day or after a new commit compares like with like.
    """
    ctx = run.ctx
    on_disk = run.doc(export.CATALOG_FILE)
    day = date.fromisoformat(on_disk["run"]["date"])
    catalog = export.with_previews(
        export.catalog_document(run.inputs, run.cfg, ctx.state, day, on_disk["run"]["code_commit"]),
        export.load_previews(ctx.paths),
    )
    rebuilt = {
        export.CATALOG_FILE: catalog,
        export.SITE_FILE: export.site_document(
            catalog, run.cfg, export.available_views(run.cfg, ctx.state, day)
        ),
        export.NAMES_FILE: export.names_document(run.inputs, day),
    }
    for name, doc in rebuilt.items():
        where = _first_difference(run.doc(name), doc)
        if where is not None:
            yield Failure("rerun_differs", f"{name} differs from a rebuild at {where}")


def check_known_answers(run: Run) -> Iterator[Failure]:
    """The methodology's known answers, where the families they name exist."""
    inputs = run.inputs
    names = run.doc(export.NAMES_FILE)
    by_name = _families_by_name(inputs)
    holders = _name_holders(names)
    for old, new in KNOWN_RENAMES:
        target = _single(by_name.get(_key(new)))
        if target is not None and holders.get(_key(old), set()) != {target}:
            yield Failure("known_answer", f"{old} must resolve to {new} ({target})", target)
    for patched, family in KNOWN_BUILDS:
        target = _single(by_name.get(_key(family)))
        if target is not None and holders.get(_key(patched), set()) != {target}:
            yield Failure("known_answer", f"{patched} must resolve to {family} ({target})", target)
    for sibling, parent in KNOWN_DISTINCT:
        yield from _distinct_answer(run, holders, by_name, sibling, parent)
    yield from _distinct_rows(run)
    yield from _ids_kept(run)


def check_monotone(run: Run) -> Iterator[Failure]:
    """A font with every value at least another's, and one higher, never scores lower."""
    import numpy as np

    inputs = run.inputs
    for key in export.RANK_KEYS:
        terms = export.view_terms(inputs.terms, key)
        scores = inputs.scores.get(key)
        if key == "rising" or terms is None or scores is None:
            continue
        groups: dict[tuple[tuple[str, float], ...], list[tuple[str, list[float], float]]] = (
            defaultdict(list)
        )
        for fid, fused in sorted(scores.fused.items()):
            if fused.guard:
                continue
            counted = [(s, by_id[fid]) for s, by_id in sorted(terms.items()) if fid in by_id]
            counted = [(s, t) for s, t in counted if t.state in export.COUNTED]
            if not counted or any(s in SMOOTHED_SOURCES for s, _ in counted):
                continue
            signature = tuple((s, t.factor) for s, t in counted)
            vector = [_value(t) for _, t in counted]
            groups[signature].append((fid, vector, fused.score))
        for members in groups.values():
            if len(members) < 2:
                continue
            ids = [m[0] for m in members]
            values = np.array([m[1] for m in members], dtype=float)
            score = np.array([m[2] for m in members], dtype=float)
            for i in range(len(ids)):
                dominates = np.all(values >= values[i], axis=1) & np.any(values > values[i], axis=1)
                for j in np.flatnonzero(dominates & (score < score[i] - EPS)):
                    yield Failure(
                        "higher_count_lower_rank",
                        f"{key}: every value is at least {ids[i]}'s, yet it scores lower",
                        ids[int(j)],
                    )


def check_desktop_views(run: Run) -> Iterator[Failure]:
    """The two desktop views differ only by the Linux sources' missing terms."""
    terms = run.inputs.terms
    chosen, installed = terms.get("desktop_chosen"), terms.get("desktop_installed")
    if chosen is None or installed is None:
        yield Failure("desktop_views_differ", "terms.json lacks a desktop view")
        return
    sources = run.cfg.ranking.sources.all()
    abstained = False
    for s in sorted(set(chosen) | set(installed)):
        mine, all_ = chosen.get(s, {}), installed.get(s, {})
        for fid in sorted(set(mine) - set(all_)):
            yield Failure("desktop_views_differ", f"{s}: a most chosen term with no match", fid)
        for fid in sorted(set(mine) & set(all_)):
            if _evidence(mine[fid]) != _evidence(all_[fid]):
                yield Failure("desktop_views_differ", f"{s}: the two views' terms differ", fid)
        left_out = sorted(set(all_) - set(mine))
        if left_out and not (s in sources and sources[s].linux):
            yield Failure(
                "desktop_views_differ",
                f"{s} is not a Linux source but leaves {len(left_out)} font(s) out of most chosen",
            )
        abstained = abstained or bool(left_out)
    ranks = run.inputs.ranks
    if not abstained:
        a = {f: p.order for f, p in ranks.get("desktop_chosen", {}).items()}
        b = {f: p.order for f, p in ranks.get("desktop_installed", {}).items()}
        for fid in sorted(f for f in set(a) | set(b) if a.get(f) != b.get(f)):
            yield Failure("desktop_views_differ", "no abstentions, yet the orders differ", fid)


def check_abstention_leak(run: Run) -> Iterator[Failure]:
    """An abstaining Linux source reaches no abstaining view and carries no overall weight."""
    inputs, cfg = run.inputs, run.cfg
    terms = inputs.terms
    chosen, installed = terms.get("desktop_chosen", {}), terms.get("desktop_installed", {})
    sources = cfg.ranking.sources.all()
    fonts = {f["id"]: f for f in run.doc(export.CATALOG_FILE)["fonts"]}
    for s in sorted(installed):
        if s not in sources or not sources[s].linux:
            continue
        for fid in sorted(set(installed[s]) - set(chosen.get(s, {}))):
            for key in export.RANK_KEYS:
                if key in ("desktop_chosen", "desktop_installed") or key not in terms:
                    continue
                if export.view_abstains(cfg, key) and fid in terms[key].get(s, {}):
                    yield Failure(
                        "abstention_leak", f"{s} abstains in most chosen but not {key}", fid
                    )
            entry = fonts.get(fid, {}).get("sources", {}).get(s)
            if entry is not None and entry["weight_used"] is not None:
                yield Failure("abstention_leak", f"{s} abstains but carries overall weight", fid)


def check_raw_values(run: Run) -> Iterator[Failure]:
    """No raw value of a source whose terms forbid it, in catalog.json or a committed report."""
    sources = run.cfg.ranking.sources.all()
    catalog = run.doc(export.CATALOG_FILE)
    for entry in catalog["sources"]:
        src = sources.get(entry["id"])
        if src is not None and entry["publish_raw"] != src.publish_raw:
            yield Failure("raw_value_published", f"sources[] {entry['id']}: publish_raw differs")
    for font in catalog["fonts"]:
        for s, entry in sorted(font["sources"].items()):
            if "value" in entry and not (s in sources and sources[s].publish_raw):
                yield Failure(
                    "raw_value_published", f"catalog.json carries a {s} value", font["id"]
                )
    for path in _reports(run.paths):
        shown = _shown(path.read_text(encoding="utf-8"), run.hidden)
        if shown:
            yield Failure(
                "raw_value_published",
                f"{_relative(path, run.paths.root)} shows {shown} value(s) of a source that "
                "publishes ranks only",
            )


CHECKS: tuple[tuple[str, Check], ...] = (
    ("schema", check_schema),
    ("ineligible_ranked", check_ineligible_ranked),
    ("license_queue", check_license_queue),
    ("rerun_differs", check_rerun),
    ("known_answer", check_known_answers),
    ("higher_count_lower_rank", check_monotone),
    ("desktop_views_differ", check_desktop_views),
    ("abstention_leak", check_abstention_leak),
    ("raw_value_published", check_raw_values),
)


def hard_checks(ctx: StageContext) -> list[Failure]:
    """Run every §9 hard check on the build outputs.

    Failures are logged and become a public issue (refresh), so their messages never
    repeat a raw value (``_redact``)."""
    run = Run(ctx)
    failures: list[Failure] = []
    for name, check in CHECKS:
        try:
            found = list(itertools.islice(check(run), MAX_PER_CHECK + 1))
        except Exception as exc:
            # A missing file (MissingInput), a bad stage file (StageFileError) or a
            # fault in the check itself: the check fails, and the others still run.
            failures.append(Failure(name, f"could not run: {type(exc).__name__}: {exc}"))
            continue
        failures += found[:MAX_PER_CHECK]
        if len(found) > MAX_PER_CHECK:
            failures.append(Failure(name, f"more failures past the first {MAX_PER_CHECK}"))
    try:
        hidden = frozenset(run.hidden)
    except Exception:
        hidden = frozenset()  # no terms to read: the value fields are still redacted
    return [replace(f, message=_redact(f.message, hidden)) for f in failures]


def run(ctx: StageContext) -> None:
    """Stage "validate": raise ``ValidationFailed`` if anything failed."""
    failures = hard_checks(ctx)
    for failure in failures:
        ctx.log.error("%s", failure)
    if failures:
        raise ValidationFailed(failures)
    ctx.log.info("validate: %d hard checks passed", len(CHECKS))


# --- helpers ---------------------------------------------------------------------------------


def _memo[T](fn: Callable[[str], T]) -> Callable[[str], T]:
    cache: dict[str, T] = {}

    def wrapped(key: str) -> T:
        if key not in cache:
            cache[key] = fn(key)
        return cache[key]

    return wrapped


def _key(name: str) -> str:
    from tff_catalog.keys import match_key

    return match_key(name)


def _single(ids: set[str] | None) -> str | None:
    return next(iter(ids)) if ids and len(ids) == 1 else None


def _families_by_name(inputs: export.Inputs) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for fid, fam in inputs.universe.families.items():
        if fam.drop is None:
            out[_key(fam.family)].add(fid)
    return out


def _name_holders(names: Mapping[str, Any]) -> dict[str, set[str]]:
    """match_key -> the names.json families that own that name (family, rename or build);
    a build also answers for its name without the Nerd suffix."""
    from tff_catalog.names import NERD_SUFFIX

    out: dict[str, set[str]] = defaultdict(set)
    for fam in names["families"]:
        out[_key(fam["family"])].add(fam["id"])
        for entry in fam["names"]:
            if entry["relation"] not in ("rename", "build"):
                continue
            out[_key(entry["name"])].add(fam["id"])
            m = NERD_SUFFIX.search(entry["name"])
            if m and m.start() > 0:
                out[_key(entry["name"][: m.start()])].add(fam["id"])
    return out


def _distinct_answer(
    run: Run,
    holders: Mapping[str, set[str]],
    by_name: Mapping[str, set[str]],
    sibling: str,
    parent: str,
) -> Iterator[Failure]:
    """Known answer "Roboto Slab gets no Roboto counts": the name isn't Roboto's, and no
    count keyed by it maps to Roboto (a substring test, since keys carry prefixes such as
    ``font-``)."""
    parent_id = _single(by_name.get(_key(parent)))
    if parent_id is None or _single(by_name.get(_key(sibling))) is None:
        return
    if parent_id in holders.get(_key(sibling), set()):
        yield Failure("known_answer", f"{sibling} resolves to {parent}", parent_id)
    needle = _key(sibling)
    hits = sorted(
        {
            f"{m.record.source}:{m.record.key.key}"
            for m in run.mapped
            if m.family_id == parent_id and needle in _key(m.record.key.key)
        }
    )
    if hits:
        shown = ", ".join(hits[:5])
        yield Failure("known_answer", f"{parent} gets {sibling} counts: {shown}", parent_id)


def _blocks(run: Run) -> Any:
    """``aliases.Blocks`` of the alias table: the rule the universe and map stages apply."""
    from tff_catalog.aliases import Blocks

    return Blocks.of(run.inputs.aliases)


def _distinct_rows(run: Run) -> Iterator[Failure]:
    """No count maps to a family through a key its "distinct" alias rows block (exact rows
    in any name namespace alike, sibling rows on every key containing the name)."""
    blocks = _blocks(run)
    if not blocks.exact and not blocks.contains:
        return
    for m in run.mapped:
        key = m.record.key
        if m.family_id and blocks.blocks(key, m.family_id):
            yield Failure(
                "known_answer",
                f"{m.record.source} key {key.ns}:{key.key} is blocked for it",
                m.family_id,
            )


def _ids_kept(run: Run) -> Iterator[Failure]:
    """Known answer "a renamed family keeps its id": no committed id changes what it was
    minted from, and no new id carries a committed family's name, unless a "distinct"
    row keeps that name from the old id (the universe then mints a new one on purpose).

    A committed id no source lists this run is not a failure: the family was delisted
    (``build/universe.md`` lists such ids), and its entry stays in the registry.
    """
    families = run.inputs.universe.families
    committed = run.ctx.state.ids
    old_names: dict[str, str] = {}
    for fid, entry in sorted(committed.items()):
        for name in (entry.get("family"), entry.get("minted_from")):
            if name:
                old_names.setdefault(_key(name), fid)
        fam = families.get(fid)
        if fam is not None and entry.get("minted_from") not in (None, fam.minted_from):
            yield Failure("known_answer", "a committed id changed its minted_from", fid)
    rows = run.inputs.rows_by_family
    blocks = _blocks(run)
    for fid in sorted(set(families) - set(committed)):
        fam = families[fid]
        names = {fam.family, fam.minted_from} | {
            r.alias for r in rows.get(fid, ()) if r.relation == "rename"
        }
        for name in sorted(names):
            old = old_names.get(_key(name))
            if old is None or old == fid or blocks.blocks(SourceKey("font-name", name), old):
                continue
            yield Failure("known_answer", f"new id for {name}, which was {old}", fid)


def _mentioned(path: Path) -> frozenset[str]:
    """Every string in a queue file (keys and values), whatever its layout."""
    if not path.is_file():
        return frozenset()
    found: set[str] = set()
    stack: list[Any] = [jsonio.load(path)]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            found.add(node)
        elif isinstance(node, Mapping):
            found.update(k for k in node if isinstance(k, str))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return frozenset(found)


def _evidence(term: Any) -> tuple[Any, ...]:
    """What a term puts into a rank; its review flags are left out."""
    return (term.value, term.state, term.group, term.reason, term.factor)


def _value(term: Any) -> float:
    """A term's value on the engine's scale: censored values tie at the bottom."""
    if term.state != "observed" or term.value is None:
        return -math.inf
    return float(term.value)


def _first_difference(a: Any, b: Any, where: str = "") -> str | None:
    """JSON pointer of the first place two documents differ, or None when equal."""
    if type(a) is not type(b):
        return where or "/"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{where}/{k}"
            found = _first_difference(a[k], b[k], f"{where}/{k}")
            if found is not None:
                return found
        return None
    if isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            found = _first_difference(x, y, f"{where}/{i}")
            if found is not None:
                return found
        return f"{where}/{min(len(a), len(b))}" if len(a) != len(b) else None
    return None if json.dumps(a) == json.dumps(b) else where or "/"


_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![\d,]|\.\d)")


_VALUE_FIELD = re.compile(r"(['\"]value['\"]\s*:\s*)[-+0-9.eE]+")
REDACTED = "[not published]"


def _as_whole(m: re.Match[str]) -> set[int]:
    """A ``_NUMBER`` match as whole numbers: "1234567.8" gives 1234567 and 1234568."""
    whole = int(m.group(1).replace(",", ""))
    return {whole, round(whole + float(m.group(2)))} if m.group(2) else {whole}


def _numbers(text: str) -> set[int]:
    """Every number in ``text`` as whole numbers, cut and rounded (a report may do either):
    "1,234,567" gives 1234567, and "1234567.8" gives 1234567 and 1234568."""
    return {n for m in _NUMBER.finditer(text) for n in _as_whole(m)}


def _redact(message: str, hidden: frozenset[int]) -> str:
    """``message`` without a raw value: every ``value`` field a schema message quotes
    (whatever the source), and every number equal to a hidden value."""
    message = _VALUE_FIELD.sub(rf"\g<1>{REDACTED}", message)
    if not hidden:
        return message
    return _NUMBER.sub(lambda m: REDACTED if _as_whole(m) & hidden else m.group(0), message)


def _whole(value: float) -> set[int]:
    """The whole numbers a report may print for ``value``: cut and rounded."""
    return {math.floor(value), round(value)}


def _hidden_values(run: Run) -> dict[int, set[str]]:
    """{whole number: casefolded labels of its font} for every value of ``RAW_MIN`` or more
    that a source whose ``publish_raw`` is false carries: its terms (labels: the family id
    and name) and the parsed records of the collectors only such sources read (labels:
    the source key, and its family's id and name when the universe holds the key)."""
    sources = run.cfg.ranking.sources.all()
    hidden = {s for s, src in sources.items() if not src.publish_raw}
    families = run.inputs.universe.families
    out: dict[int, set[str]] = defaultdict(set)

    def add(value: float | None, labels: Iterable[str]) -> None:
        if value is not None and math.isfinite(value) and value >= RAW_MIN:
            for n in _whole(value):
                out[n].update(label.casefold() for label in labels if label)

    def family_labels(fid: str) -> tuple[str, ...]:
        fam = families.get(fid)
        return (fid, fam.family) if fam is not None else (fid,)

    for by_source in run.inputs.terms.values():
        for s in sorted(hidden & set(by_source)):
            for fid, term in by_source[s].items():
                add(term.value, family_labels(fid))
    published = {src.collector for src in sources.values() if src.publish_raw}
    for collector in sorted({sources[s].collector for s in hidden} - published):
        for path in sorted(run.paths.records.glob(f"{collector}*.jsonl")):
            if path.stem != collector and not path.stem.startswith(f"{collector}@"):
                continue
            for rec in _records(path):
                fam = run.inputs.universe.by_key(rec.key)
                add(rec.value, (rec.key.key, *(family_labels(fam.id) if fam else ())))
    return dict(out)


def _records(path: Path) -> Iterator[Any]:
    """The observations of a parsed records file (``build/stage/records/``)."""
    from tff_catalog.records import Observation, read_jsonl

    return (r for r in read_jsonl(path) if isinstance(r, Observation))


def _shown(text: str, hidden: Mapping[int, set[str]]) -> int:
    """How many hidden values ``text`` shows on a line that also names their font."""
    found: set[int] = set()
    for line in text.splitlines():
        folded = line.casefold()
        for n in _numbers(line):
            labels = hidden.get(n)
            if labels and any(label in folded for label in labels):
                found.add(n)
    return len(found)


def _reports(paths: Paths) -> list[Path]:
    """The committed Markdown reports: ``build/*.md`` and ``docs/backtests/*.md``."""
    return [
        *sorted(paths.build.glob("*.md")),
        *sorted((paths.root / "docs" / "backtests").glob("*.md")),
    ]


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
