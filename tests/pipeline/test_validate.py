"""Stage "validate" (milestone-1 step 15): the schemas and the methodology §9 hard checks.

Each test starts from the clean synthetic build of ``test_export.make_build``,
breaks one thing, and checks that exactly the right hard check fails. The last
test validates the committed ``build/*.json`` files, which is how CI checks
the real run's outputs.
"""

import dataclasses
import math
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.helpers import ROOT
from tests.pipeline.test_export import (
    PREVIEWS,
    fixed_commit,  # noqa: F401 (an autouse fixture)
    make_build,
    run_all,
)

from tff_catalog import export, jsonio, stageio, validate
from tff_catalog.corrections import Term
from tff_catalog.engine.order import Placement
from tff_catalog.facts import Facts
from tff_catalog.latin import LatinResult
from tff_catalog.licenses import LicenseClass, Verdict
from tff_catalog.mapping import Mapped
from tff_catalog.membership import MemberState
from tff_catalog.records import Observation, SourceKey
from tff_catalog.stages import StageContext
from tff_catalog.state import State
from tff_catalog.surveys import RankInputs
from tff_catalog.universe import Family


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    context = make_build(tmp_path, previews=PREVIEWS)
    run_all(context)
    return context


def checks(ctx: StageContext) -> dict[str, list[validate.Failure]]:
    out: dict[str, list[validate.Failure]] = {}
    for failure in validate.hard_checks(ctx):
        out.setdefault(failure.check, []).append(failure)
    return out


def ids(failures: list[validate.Failure]) -> set[str | None]:
    return {f.family_id for f in failures}


def edit_stage(ctx: StageContext, name: str, change: Any) -> None:
    """Load stage file ``name``, let ``change`` mutate or replace it, and write it back."""
    obj = stageio.load_stage(ctx.paths, name)
    result = change(obj)
    stageio.dump_stage(ctx.paths, name, obj if result is None else result)


def add_family(
    ctx: StageContext, fid: str, name: str, *, aliases: str = "", member: bool = False
) -> None:
    """Add an eligible OFL family (not in the catalog) to the universe and gate stages."""
    fam = Family(
        fid, name, (SourceKey("gf-family", name),), ("google_metadata",), date(2026, 3, 1), name
    )
    edit_stage(ctx, "universe", lambda u: dataclasses.replace(u, families={**u.families, fid: fam}))
    edit_stage(
        ctx, "latin", lambda m: m.update({fid: LatinResult(True, "gf_metadata", "extended")})
    )
    edit_stage(
        ctx, "facts", lambda m: m.update({fid: Facts("sans-serif", False, False, True, "x")})
    )
    lic = LicenseClass("OFL-1.1", "allowed", "open-font", True, False)
    edit_stage(
        ctx,
        "licenses",
        lambda m: m.update({fid: Verdict(fid, "OFL-1.1", lic, True, (("x", "OFL-1.1"),))}),
    )
    if aliases:
        with ctx.paths.aliases_csv.open("a", encoding="utf-8") as fh:
            fh.write(aliases)


def rerun(ctx: StageContext) -> dict[str, list[validate.Failure]]:
    run_all(ctx)
    return checks(ctx)


# --- a clean run ------------------------------------------------------------------------------


def test_clean_build_passes_every_check(ctx: StageContext) -> None:
    assert [str(f) for f in validate.hard_checks(ctx)] == []
    validate.run(ctx)  # no exception


def test_run_raises_with_every_failure(ctx: StageContext) -> None:
    (ctx.paths.build / export.NAMES_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(validate.ValidationFailed) as caught:
        validate.run(ctx)
    assert caught.value.failures
    assert all(isinstance(f, validate.Failure) for f in caught.value.failures)
    assert "schema: names.json /: 'families' is a required property" in str(caught.value)


def test_a_missing_output_fails_instead_of_crashing(ctx: StageContext) -> None:
    (ctx.paths.build / export.CATALOG_FILE).unlink()
    found = checks(ctx)
    assert "could not run" in found["ineligible_ranked"][0].message
    assert any("catalog.json" in f.message for f in found["schema"])


def test_a_check_that_breaks_fails_and_the_others_still_run(
    ctx: StageContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(run: validate.Run) -> Any:
        raise ZeroDivisionError("oops")

    monkeypatch.setattr(validate, "CHECKS", (("broken", broken), *validate.CHECKS))
    failures = validate.hard_checks(ctx)
    assert [str(f) for f in failures] == ["broken: could not run: ZeroDivisionError: oops"]


# --- schemas ----------------------------------------------------------------------------------


def test_schema_errors_are_json_pointers(tmp_path: Path) -> None:
    doc = jsonio.load(ROOT / "tests" / "fixtures" / "catalog-site.sample.json")
    doc["fonts"][0]["id"] = "Not An Id"
    del doc["fonts"][1]["family"]
    path = tmp_path / "site.json"
    jsonio.dump(doc, path)
    errors = validate.schema_errors(path, ROOT / "schemas" / "catalog-site.schema.json")
    assert errors == [
        "/fonts/0/id: 'Not An Id' does not match '^[a-z0-9-]+$'",
        "/fonts/1: 'family' is a required property",
    ]


def test_schema_errors_of_a_file_that_is_not_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    [line] = validate.schema_errors(path, ROOT / "schemas" / "names.schema.json")
    assert line.startswith(f"{path}: ")


def test_sample_site_catalog_stays_valid() -> None:
    sample = ROOT / "tests" / "fixtures" / "catalog-site.sample.json"
    assert validate.schema_errors(sample, ROOT / "schemas" / "catalog-site.schema.json") == []


def test_site_cross_references_are_checked(ctx: StageContext) -> None:
    path = ctx.paths.build / export.SITE_FILE
    doc = jsonio.load(path)
    doc["fonts"][0]["preinstalled_on"] = [{"system": "beos"}]
    jsonio.dump(doc, path)
    found = checks(ctx)
    assert any("unknown system 'beos'" in f.message for f in found["schema"])


# --- eligibility and licenses -------------------------------------------------------------------


def test_an_ineligible_font_is_never_placed(ctx: StageContext) -> None:
    edit_stage(
        ctx, "ranks", lambda r: r["project"].update({"iota-sans": Placement(99, 99, None, False)})
    )
    found = rerun(ctx)
    assert ids(found["ineligible_ranked"]) == {"iota-sans"}
    assert "license" in found["ineligible_ranked"][0].message


def test_an_ineligible_font_has_no_term(ctx: StageContext) -> None:
    def change(terms: dict[str, Any]) -> None:
        terms["project"]["google"]["theta-cjk"] = Term(5.0, "observed", "google")

    edit_stage(ctx, "terms", change)
    found = rerun(ctx)
    assert ids(found["ineligible_ranked"]) == {"theta-cjk"}
    assert "term project/google" in found["ineligible_ranked"][0].message


def test_a_failed_l3_check_leaves_the_catalog(ctx: StageContext) -> None:
    edit_stage(
        ctx,
        "l3",
        lambda m: m.update({"alpha-sans": dataclasses.replace(m["alpha-sans"], level="failed")}),
    )
    found = rerun(ctx)
    assert "alpha-sans" in ids(found["ineligible_ranked"])
    assert any("unverified (L3 failed)" in f.message for f in found["ineligible_ranked"])
    assert ids(found["license_queue"]) == {"alpha-sans"}  # not in queues/l3.json


def test_a_flagged_license_must_be_queued(ctx: StageContext) -> None:
    jsonio.dump({"items": []}, ctx.paths.queues / "licenses.json")
    found = checks(ctx)
    assert list(found) == ["license_queue"]
    assert ids(found["license_queue"]) == {"zeta-sans"}


def test_a_license_awaiting_a_ruling_is_neither_ranked_nor_catalogued(ctx: StageContext) -> None:
    """A license waiting for the owner is unverified: filter first (methodology §1 and §9),
    so it may take no term, no place and no catalog entry until the ruling."""
    edit_stage(
        ctx,
        "terms",
        lambda t: t["project"]["google"].update({"zeta-sans": Term(9.0, "observed", "google")}),
    )
    edit_stage(
        ctx, "ranks", lambda r: r["project"].update({"zeta-sans": Placement(7, 7, None, False)})
    )
    found = rerun(ctx)["ineligible_ranked"]
    assert ids(found) == {"zeta-sans"}
    assert found[0].message.startswith("ineligible: license, but placed in project")
    edit_stage(ctx, "terms", lambda t: t["project"]["google"].pop("zeta-sans") and None)
    edit_stage(ctx, "ranks", lambda r: r["project"].pop("zeta-sans") and None)
    assert rerun(ctx) == {}
    edit_stage(
        ctx,
        "membership",
        lambda m: dataclasses.replace(
            m, catalog={**m.catalog, "zeta-sans": MemberState(True, date(2026, 10, 3), 0)}
        ),
    )
    edit_stage(ctx, "links", lambda m: m.update({"zeta-sans": m["alpha-sans"]}))
    edit_stage(
        ctx,
        "l3",
        lambda m: m.update(
            {"zeta-sans": dataclasses.replace(m["alpha-sans"], family_id="zeta-sans")}
        ),
    )
    found = rerun(ctx)["ineligible_ranked"]
    assert ids(found) == {"zeta-sans"}
    assert found[0].message == "ineligible: license, but in catalog.json"


def drop_everywhere(ctx: StageContext, fid: str) -> None:
    """Remove ``fid`` from the files stage "rank" writes (its rerun after "verify")."""

    def scores(m: dict[str, Any]) -> None:
        for key, view in m.items():
            fused = {f: v for f, v in view.fused.items() if f != fid}
            placed = {f: v for f, v in view.placements.items() if f != fid}
            m[key] = dataclasses.replace(view, fused=fused, placements=placed)

    edit_stage(ctx, "scores", scores)
    edit_stage(
        ctx, "ranks", lambda m: {k: {f: p for f, p in v.items() if f != fid} for k, v in m.items()}
    )
    edit_stage(
        ctx,
        "confidence",
        lambda m: {k: {f: c for f, c in v.items() if f != fid} for k, v in m.items()},
    )
    edit_stage(ctx, "ruler", lambda m: {f: z for f, z in m.items() if f != fid})


def test_an_l3_failure_keeps_its_terms_but_loses_its_places(ctx: StageContext) -> None:
    """Stage "correct" runs before "verify", so terms.json keeps an L3 failure; the rank
    rerun must drop it from the ruler, the scores and the placements."""
    edit_stage(
        ctx,
        "l3",
        lambda m: m.update(
            {
                "alpha-slab": dataclasses.replace(
                    m["alpha-sans"], family_id="alpha-slab", level="failed"
                )
            }
        ),
    )
    jsonio.dump({"items": [{"family_id": "alpha-slab"}]}, ctx.paths.queues / "l3.json")
    found = rerun(ctx)["ineligible_ranked"]
    assert ids(found) == {"alpha-slab"}
    assert "placed in desktop_chosen" in found[0].message
    assert "term " not in found[0].message
    assert "ruler_counts" not in found[0].message
    drop_everywhere(ctx, "alpha-slab")
    assert rerun(ctx) == {}


def test_an_owner_excluded_l3_failure_needs_no_queue_entry(
    ctx: StageContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tff_catalog import license_l3

    failed = None

    def fail(m: dict[str, Any]) -> None:
        nonlocal failed
        failed = dataclasses.replace(m["alpha-sans"], family_id="alpha-slab", level="failed")
        m["alpha-slab"] = failed

    edit_stage(ctx, "l3", fail)
    drop_everywhere(ctx, "alpha-slab")
    run_all(ctx)
    assert ids(checks(ctx)["license_queue"]) == {"alpha-slab"}
    for choice, queued in (("a", False), ("c", True)):
        ruling = license_l3.L3Ruling("alpha-slab", date(2026, 10, 1), choice, failed.text_sha256)
        monkeypatch.setattr(
            license_l3, "load_l3_rulings", lambda paths, log=None, r=ruling: {"alpha-slab": r}
        )
        assert ("license_queue" in checks(ctx)) is queued, choice


# --- determinism -------------------------------------------------------------------------------


def test_an_edited_output_differs_from_the_rebuild(ctx: StageContext) -> None:
    path = ctx.paths.build / export.CATALOG_FILE
    doc = jsonio.load(path)
    doc["fonts"][0]["family"] = "Alpha Sans Edited"
    jsonio.dump(doc, path)
    found = checks(ctx)
    assert list(found) == ["rerun_differs"]
    assert (
        found["rerun_differs"][0].message
        == "catalog.json differs from a rebuild at /fonts/0/family"
    )


def test_validating_on_a_later_day_rebuilds_for_the_recorded_day(ctx: StageContext) -> None:
    later = dataclasses.replace(ctx, run_date=date(2026, 11, 20))
    assert checks(later) == {}


def test_failures_are_capped_per_check(ctx: StageContext) -> None:
    def change(ranks: dict[str, Any]) -> None:
        for n in range(validate.MAX_PER_CHECK + 5):
            ranks["project"][f"ghost-{n}"] = Placement(200 + n, None, None, False)

    edit_stage(ctx, "ranks", change)
    found = checks(ctx)["ineligible_ranked"]
    assert len(found) == validate.MAX_PER_CHECK + 1
    assert found[-1].message == f"more failures past the first {validate.MAX_PER_CHECK}"


def test_a_new_commit_is_not_a_difference(ctx: StageContext) -> None:
    path = ctx.paths.build / export.CATALOG_FILE
    doc = jsonio.load(path)
    doc["run"]["code_commit"] = "f" * 40
    jsonio.dump(doc, path)
    assert checks(ctx) == {}


# --- raw values --------------------------------------------------------------------------------


def test_a_google_value_in_the_catalog_fails(ctx: StageContext) -> None:
    path = ctx.paths.build / export.CATALOG_FILE
    doc = jsonio.load(path)
    doc["fonts"][0]["sources"]["google"]["value"] = 10_000_000.0
    jsonio.dump(doc, path)
    found = checks(ctx)
    assert ids(found["raw_value_published"]) == {"alpha-sans"}
    assert "schema" in found  # the schema forbids it too


@pytest.mark.parametrize("value", [10_000_000.0, 12_345_678.5])
def test_failure_messages_never_repeat_a_hidden_value(ctx: StageContext, value: float) -> None:
    """Failures go to the log and a public issue: the schema error that quotes the source
    entry, and any other message, must not show the Google count."""
    path = ctx.paths.build / export.CATALOG_FILE
    doc = jsonio.load(path)
    doc["fonts"][0]["sources"]["google"]["value"] = value
    doc["fonts"][0]["sources"]["google"]["weight_used"] = -1.0  # a second schema error
    jsonio.dump(doc, path)
    failures = validate.hard_checks(ctx)
    text = "\n".join(str(f) for f in failures)
    assert "schema" in {f.check for f in failures}
    assert validate.REDACTED in text
    for shown in (f"{value:.1f}", f"{int(value)}", f"{int(value):,}", "10000000", "12345678"):
        assert shown not in text, shown


def test_redact_keeps_other_numbers() -> None:
    hidden = frozenset({10_000_000})
    assert validate._redact("order 12 of 10,000,000.0 and 'value': 3.5", hidden) == (
        f"order 12 of {validate.REDACTED} and 'value': {validate.REDACTED}"
    )


def test_a_google_value_in_a_committed_report_fails_without_repeating_it(ctx: StageContext) -> None:
    (ctx.paths.build / "review.md").write_text(
        "| Alpha Sans | 10,000,000 views |\n| year | 2026 |\n", encoding="utf-8"
    )
    found = checks(ctx)
    [failure] = found["raw_value_published"]
    assert "review.md shows 1 value" in failure.message
    assert "10" not in failure.message.replace("review.md", "")


def test_another_sources_count_equal_to_a_google_value_is_no_leak(ctx: StageContext) -> None:
    """A report number counts as a Google value only on a line naming that value's font:
    Beta Mono's published npm count may equal Alpha Sans's Google views by chance."""
    (ctx.paths.build / "corrections.md").write_text(
        "| Beta Mono (`beta-mono`) | npm_fontsource | 10,000,000 a month |\n", encoding="utf-8"
    )
    assert "raw_value_published" not in checks(ctx)


def test_a_google_value_printed_with_decimals_or_cut_fails(ctx: StageContext) -> None:
    for text in ("alpha-sans: 10000000.0 views\n", "Alpha Sans | 9,999,999.6\n"):
        (ctx.paths.build / "review.md").write_text(text, encoding="utf-8")
        assert ids(checks(ctx)["raw_value_published"]) == {None}, text


def test_an_unmatched_google_key_in_a_report_fails(ctx: StageContext) -> None:
    """Values of keys no family took (build/unmatched.md) come from the parsed records."""
    from tff_catalog.records import write_jsonl

    obs = Observation(
        "gf_stats",
        "year",
        SourceKey("gf-family", "Omega Grotesk"),
        2_345_678.0,
        "views",
        date(2025, 10, 1),
        date(2026, 9, 30),
    )
    write_jsonl([obs], ctx.paths.records / "gf_stats.jsonl")
    (ctx.paths.build / "unmatched.md").write_text(
        "| Omega Grotesk | 2,345,678 |\n", encoding="utf-8"
    )
    [failure] = checks(ctx)["raw_value_published"]
    assert failure.message.startswith("build/unmatched.md shows 1 value")


def test_committed_backtest_reports_are_scanned_too(ctx: StageContext) -> None:
    folder = ctx.paths.root / "docs" / "backtests"
    folder.mkdir(parents=True)
    (folder / "2026-10-03.md").write_text("Alpha Sans: 10,000,000 views\n", encoding="utf-8")
    [failure] = checks(ctx)["raw_value_published"]
    assert failure.message.startswith("docs/backtests/2026-10-03.md shows 1 value")


def test_publish_raw_must_match_the_config(ctx: StageContext) -> None:
    path = ctx.paths.build / export.CATALOG_FILE
    doc = jsonio.load(path)
    next(s for s in doc["sources"] if s["id"] == "fot")["publish_raw"] = True
    jsonio.dump(doc, path)
    assert "fot: publish_raw differs" in checks(ctx)["raw_value_published"][0].message


# --- known answers -----------------------------------------------------------------------------


def test_source_sans_pro_resolves_to_source_sans_3(ctx: StageContext) -> None:
    add_family(ctx, "source-sans-3", "Source Sans 3")
    found = rerun(ctx)
    assert ids(found["known_answer"]) == {"source-sans-3"}
    with ctx.paths.aliases_csv.open("a", encoding="utf-8") as fh:
        fh.write("Source Sans Pro,gf-family,source-sans-3,rename,,synthetic,2026-09-01,owner\n")
    assert rerun(ctx) == {}


def test_sauce_code_pro_resolves_to_source_code_pro(ctx: StageContext) -> None:
    add_family(ctx, "source-code-pro", "Source Code Pro")
    assert ids(rerun(ctx)["known_answer"]) == {"source-code-pro"}
    with ctx.paths.aliases_csv.open("a", encoding="utf-8") as fh:
        fh.write(
            "SauceCodePro Nerd Font,font-name,source-code-pro,build,nerd,synthetic,2026-09-01,owner\n"
        )
    assert rerun(ctx) == {}


def test_roboto_slab_gets_no_roboto_counts(ctx: StageContext) -> None:
    add_family(ctx, "roboto", "Roboto")
    add_family(ctx, "roboto-slab", "Roboto Slab")
    assert rerun(ctx) == {}
    obs = Observation(
        "npm",
        "last-year",
        SourceKey("npm", "@fontsource/roboto-slab"),
        5.0,
        "downloads",
        date(2025, 10, 1),
        date(2026, 9, 30),
    )
    stageio.dump_stage(ctx.paths, "mapped", [Mapped(obs, "roboto", "direct")])
    found = checks(ctx)
    assert ids(found["known_answer"]) == {"roboto"}
    assert "@fontsource/roboto-slab" in found["known_answer"][0].message


def test_a_distinct_row_blocks_its_key(ctx: StageContext) -> None:
    def mapped(ns: str, key: str) -> Mapped:
        obs = Observation(
            "gf_stats",
            "year",
            SourceKey(ns, key),
            5.0,
            "views",
            date(2025, 10, 1),
            date(2026, 9, 30),
        )
        return Mapped(obs, "alpha-sans", "direct")

    stageio.dump_stage(ctx.paths, "mapped", [mapped("gf-family", "Alpha Slab")])
    found = checks(ctx)
    assert ids(found["known_answer"]) == {"alpha-sans"}
    assert "is blocked" in found["known_answer"][0].message
    # A name is the same name in every name namespace (aliases.Blocks).
    stageio.dump_stage(ctx.paths, "mapped", [mapped("fot-name", "alpha slab")])
    assert ids(checks(ctx)["known_answer"]) == {"alpha-sans"}
    stageio.dump_stage(ctx.paths, "mapped", [mapped("npm", "@fontsource/alpha-slab")])
    assert "known_answer" not in checks(ctx)  # an exact row blocks only that name


def test_a_renamed_family_keeps_its_id(ctx: StageContext) -> None:
    committed = {
        "alpha-sans": {
            "family": "Alpha Sans",
            "minted_from": "Alpha Sans",
            "first_seen": "2026-03-01",
        }
    }
    same = dataclasses.replace(ctx, state=State(ids=committed))
    assert checks(same) == {}
    # A registry id no source lists any more is a delisted family, not a failed answer.
    delisted = {**committed, "old-sans": {"family": "Old Sans", "minted_from": "Old Sans"}}
    assert checks(dataclasses.replace(ctx, state=State(ids=delisted))) == {}
    renamed = dataclasses.replace(
        ctx,
        state=State(ids={**committed, "zeta": {"family": "Zeta Sans", "minted_from": "Zeta Sans"}}),
    )
    found = checks(renamed)["known_answer"]
    assert ids(found) == {"zeta-sans"}  # a new id took the committed family's name
    assert found[0].message == "new id for Zeta Sans, which was zeta"
    moved = {"alpha-sans": {**committed["alpha-sans"], "minted_from": "Alfa Sans"}}
    found = checks(dataclasses.replace(ctx, state=State(ids=moved)))["known_answer"]
    assert [f.message for f in found] == ["a committed id changed its minted_from"]


def test_a_distinct_row_lets_a_new_id_share_a_name(ctx: StageContext) -> None:
    """The universe mints a new family on purpose when a distinct row forbids the old id."""
    with ctx.paths.aliases_csv.open("a", encoding="utf-8") as fh:
        fh.write("Zeta Sans,fot-name,zeta,distinct,,synthetic,2026-09-01,owner\n")
    run_all(ctx)
    state = State(ids={"zeta": {"family": "Zeta Sans", "minted_from": "Zeta Sans"}})
    assert checks(dataclasses.replace(ctx, state=state)) == {}


# --- the rank properties ----------------------------------------------------------------------------


def swap_scores(ctx: StageContext, key: str, a: str, b: str) -> None:
    def change(scores: dict[str, Any]) -> None:
        fused = dict(scores[key].fused)
        fused[a], fused[b] = (
            dataclasses.replace(fused[a], score=fused[b].score),
            dataclasses.replace(fused[b], score=fused[a].score),
        )
        scores[key] = dataclasses.replace(scores[key], fused=fused)

    edit_stage(ctx, "scores", change)


def test_a_higher_count_never_scores_lower(ctx: StageContext) -> None:
    swap_scores(ctx, "desktop_installed", "alpha-slab", "epsilon-hand")  # 1,000 vs 200 installs
    found = rerun(ctx)
    assert list(found) == ["higher_count_lower_rank"]
    assert ids(found["higher_count_lower_rank"]) == {"alpha-slab"}
    assert "desktop_installed" in found["higher_count_lower_rank"][0].message


def test_the_guard_excuses_a_lower_score(ctx: StageContext) -> None:
    swap_scores(ctx, "desktop_installed", "alpha-slab", "epsilon-hand")

    def guard(scores: dict[str, Any]) -> None:
        view = scores["desktop_installed"]
        fused = dict(view.fused)
        fused["alpha-slab"] = dataclasses.replace(fused["alpha-slab"], guard=(("homebrew", 0.5),))
        scores["desktop_installed"] = dataclasses.replace(view, fused=fused)

    edit_stage(ctx, "scores", guard)
    assert rerun(ctx) == {}


def test_fonts_over_time_terms_are_not_compared(ctx: StageContext) -> None:
    swap_scores(ctx, "project", "alpha-sans", "kappa-sans")  # both carry a smoothed FOT term
    assert "higher_count_lower_rank" not in rerun(ctx)


def test_desktop_views_differ_only_by_abstentions(ctx: StageContext) -> None:
    def change(terms: dict[str, Any]) -> None:
        terms["desktop_chosen"]["homebrew"]["alpha-sans"] = Term(49000.0, "observed", "homebrew")
        del terms["desktop_chosen"]["homebrew"]["beta-mono"]

    edit_stage(ctx, "terms", change)
    found = rerun(ctx)["desktop_views_differ"]
    messages = [f.message for f in found]
    assert "homebrew: the two views' terms differ" in messages
    assert any("homebrew is not a Linux source" in m for m in messages)


def test_without_abstentions_the_desktop_orders_must_match(ctx: StageContext) -> None:
    def no_abstentions(terms: dict[str, Any]) -> None:
        terms["desktop_chosen"] = terms["desktop_installed"]
        for view in ("coding", "rising"):  # Arch's terms return to every abstaining view
            terms[view]["arch"] = dict(terms["desktop_installed"]["arch"])

    edit_stage(ctx, "terms", no_abstentions)
    edit_stage(ctx, "tags", lambda m: {f: dataclasses.replace(t, flags=()) for f, t in m.items()})
    found = rerun(ctx)
    assert list(found) == ["desktop_views_differ"]
    assert all(
        f.message == "no abstentions, yet the orders differ" for f in found["desktop_views_differ"]
    )
    edit_stage(ctx, "ranks", lambda r: r.update({"desktop_chosen": r["desktop_installed"]}))
    assert "desktop_views_differ" not in rerun(ctx)


def test_an_abstaining_linux_count_never_reaches_another_view(ctx: StageContext) -> None:
    def change(terms: dict[str, Any]) -> None:
        terms["coding"]["arch"]["beta-mono"] = terms["desktop_installed"]["arch"]["beta-mono"]

    edit_stage(ctx, "terms", change)
    found = rerun(ctx)
    assert ids(found["abstention_leak"]) == {"beta-mono"}
    assert "not coding" in found["abstention_leak"][0].message


def test_an_abstaining_linux_count_never_reaches_rising(ctx: StageContext) -> None:
    def change(terms: dict[str, Any]) -> None:
        terms["rising"]["arch"]["kappa-sans"] = Term(0.11, "observed", "arch")

    edit_stage(ctx, "terms", change)
    found = rerun(ctx)
    assert ids(found["abstention_leak"]) == {"kappa-sans"}
    assert "not rising" in found["abstention_leak"][0].message


def test_an_abstaining_source_carries_no_overall_weight(ctx: StageContext) -> None:
    path = ctx.paths.build / export.CATALOG_FILE
    doc = jsonio.load(path)
    beta = next(f for f in doc["fonts"] if f["id"] == "beta-mono")
    beta["sources"]["arch"]["weight_used"] = 0.1
    jsonio.dump(doc, path)
    assert ids(checks(ctx)["abstention_leak"]) == {"beta-mono"}


# --- the real engine --------------------------------------------------------------------------------


ENGINE_GROUPS = {
    "homebrew": "homebrew",
    "arch": "arch",
    "github": "github_counters",
    "nerd": "github_counters",
    "debian": "debian",
    "fot": "fot",
    "almanac": "http_archive",
    "google": "google",
    "npm_fontsource": "npm_registry",
    "ecosystems": "npm_registry",
    "jsdelivr": "jsdelivr",
    "npm_expo": "npm_registry",
}


def engine_build(
    tmp_path: Path, n: int = 90, *, catalog: int = 60, rising: bool = False
) -> tuple[StageContext, RankInputs]:
    """``make_build``, with the ranking stage files replaced by what the real engine
    (``surveys.rank_inputs`` and ``surveys.views``) makes of ``n`` invented families with
    seeded random values, and the engine's inputs. The catalog is the overall top
    ``catalog``. Fonts Over Time is smoothed from a made-up previous month, as stage
    "rank" does (``surveys.smooth``), and this month's smoothing part is written; with
    ``rising``, Rising gets log-ratio terms from four sources."""
    import numpy as np

    from tff_catalog.confidence import Confidence
    from tff_catalog.corrections import Tags
    from tff_catalog.engine.equate import build_ruler
    from tff_catalog.license_l3 import L3Result
    from tff_catalog.links import Link, Links
    from tff_catalog.membership import Membership
    from tff_catalog.state import write_part
    from tff_catalog.surveys import rank_inputs, smooth, views
    from tff_catalog.universe import Universe

    ctx = make_build(tmp_path)
    cfg, rng = ctx.config.ranking, np.random.default_rng(7)
    ids = [f"fam-{i:03d}" for i in range(n)]
    mono = frozenset(ids[::5])

    def terms(sources: list[str], share: float) -> dict[str, dict[str, Term]]:
        out: dict[str, dict[str, Term]] = {}
        for s in sources:
            out[s] = {}
            for fid in ids:
                if rng.random() < share:
                    value = round(float(rng.lognormal(8, 2)), 1) if rng.random() > 0.1 else None
                    state = "observed" if value is not None else "censored"
                    reason = None if value is not None else "no_value"
                    out[s][fid] = Term(value, state, ENGINE_GROUPS[s], reason)
        return out

    installed = terms(list(cfg.surveys.desktop.weights), 0.8)
    abstain = {("arch", f) for f in ids[:6]} | {("debian", f) for f in ids[3:9]}
    chosen = {
        s: {f: t for f, t in m.items() if (s, f) not in abstain} for s, m in installed.items()
    }
    project = terms([s for s in cfg.surveys.project.weights if s != "flutter"], 0.85)
    both = chosen | project
    all_terms = {
        "desktop_installed": installed,
        "desktop_chosen": chosen,
        "project": project,
        "coding": {s: dict(both[s]) for s in cfg.ranks.coding.weights if s in both},
        "dev_apps": {s: dict(project[s]) for s in cfg.ranks.dev_apps.weights if s in project},
        "rising": {},
    }
    counts = {f: t.value or 0.0 for f in ids if (t := installed["homebrew"].get(f))}
    counts |= {f: 0.0 for f in ids if f not in counts}
    fams = {
        f: Family(
            f,
            f"Fam {f[4:]}",
            (SourceKey("gf-family", f"Fam {f[4:]}"),),
            ("x",),
            date(2026, 1, 1),
            f"Fam {f[4:]}",
        )
        for f in ids
    }
    ruler = build_ruler(counts)
    previous = {f: round(float(rng.normal(0, 1)), 3) for f in ids[::2]}
    fot_terms, ewma = smooth(project["fot"], ruler, previous, cfg.sources.fot.ewma_lambda)
    rises: dict[str, dict[str, Term]] = {}
    if rising:
        for s in ("homebrew", "arch", "npm_fontsource", "google"):
            rises[s] = {
                f: Term(round(float(rng.normal(0, 0.5)), 4), "observed", ENGINE_GROUPS[s])
                for f in ids
                if rng.random() < 0.6
            }
    inputs = rank_inputs(
        {**all_terms, "project": {**project, "fot": fot_terms}},
        ruler,
        {f: fams[f].family for f in ids},
        cfg,
        monospace=mono,
        phasing={"fot": True},
        rising=rises or None,
    )
    scores = views(inputs, cfg)
    ranks = {k: dict(s.placements) for k, s in scores.items()}
    members = sorted(f for f, p in ranks["overall"].items() if p.order <= catalog)
    lic = LicenseClass("OFL-1.1", "allowed", "open-font", True, False)
    sha = "a" * 64
    files: dict[str, object] = {
        "universe": Universe(families=fams, unmapped=()),
        "latin": {f: LatinResult(True, "gf_metadata", "extended") for f in ids},
        "facts": {
            f: Facts("monospace" if f in mono else "sans-serif", f in mono, False, True, "x")
            for f in ids
        },
        "licenses": {
            f: Verdict(f, "OFL-1.1", lic, True, (("google_repo", "OFL-1.1"),)) for f in ids
        },
        "terms": all_terms,
        "tags": {f: Tags(preinstalled_on=(), pulled_in_by=()) for f in ids[:3]},
        "ruler": inputs.ruler,
        "ruler_counts": counts,
        "scores": scores,
        "ranks": ranks,
        "membership": Membership({f: MemberState(True, date(2026, 10, 3), 0) for f in members}, {}),
        "l3": {
            f: L3Result(
                f,
                "L3",
                date(2026, 10, 2),
                f"https://example.com/{f}/OFL.txt",
                sha,
                "OFL-1.1",
                (None, None),
                "1.0",
                None,
            )
            for f in members
        },
        "confidence": {
            k: {
                f: Confidence((max(1, p.order - 2), p.order + 2), "A" if p.rank else "C")
                for f, p in v.items()
            }
            for k, v in ranks.items()
        },
        "links": {f: Links(Link(f"https://example.com/{f}"), None, "gf") for f in members},
    }
    for name, obj in files.items():
        stageio.dump_stage(ctx.paths, name, obj)
    write_part(ctx.paths, "smoothing", {"month": "2026-10", "fot_ewma": ewma}, stage="rank")
    ctx.paths.aliases_csv.write_text(
        "alias,ns,family_id,relation,detail,source,first_seen,reviewed_by\n", encoding="utf-8"
    )
    ctx.paths.superfamilies_csv.unlink()
    jsonio.dump({"items": []}, ctx.paths.queues / "licenses.json")
    return ctx, inputs


def engine_z(inputs: RankInputs, view: str, source: str) -> dict[str, float]:
    """The z the engine equates ``source`` to in ``view`` (``surveys`` ``_part``): observed
    values as it ranked on them (Fonts Over Time's smoothed), censored ones at -inf."""
    from tff_catalog.engine.equate import equate_source

    terms = inputs.terms[view].get(source, {})
    xs = {
        f: (t.value if t.state == "observed" and t.value is not None else -math.inf)
        for f, t in terms.items()
        if t.state in export.COUNTED
    }
    return equate_source(xs, inputs.ruler)


def test_the_real_engines_output_passes_every_check(tmp_path: Path) -> None:
    """Export and validate agree with the engine's own placements, weights and guards."""
    ctx, _ = engine_build(tmp_path)
    docs = run_all(ctx)
    assert checks(ctx) == {}
    # Developers & apps' sources are all one independence group: nobody passes the
    # gate, and every font sits past the top 100.
    dev_apps = [f["ranks"]["dev_apps"] for f in docs[export.CATALOG_FILE]["fonts"]]
    placed = [e for e in dev_apps if e["order"] is not None]
    assert placed
    assert all(e["rank"] is None and e["order"] > 100 and e["gate_held"] for e in placed)
    assert min(e["order"] for e in placed) == 101


def test_published_z_and_weights_are_the_engines(tmp_path: Path) -> None:
    """Each source's z is the one the engine equated (Fonts Over Time's smoothed one
    included), and a font's weights in overall add up to the engine's Σw'."""
    ctx, inputs = engine_build(tmp_path)
    catalog = run_all(ctx)[export.CATALOG_FILE]
    scores = stageio.load_stage(ctx.paths, "scores")
    sources = ctx.config.ranking.sources.all()
    compared = 0
    for s in (e["id"] for e in catalog["sources"]):
        view = "desktop_installed" if sources[s].survey == "desktop" else "project"
        want = engine_z(inputs, view, s)
        for font in catalog["fonts"]:
            got = font["sources"][s]["z"]
            if font["id"] in want:
                assert got == pytest.approx(want[font["id"]], abs=1e-6), (s, font["id"])
                compared += 1
            else:
                assert got is None, (s, font["id"])
    assert compared > 0
    fot = [f["sources"]["fot"]["z"] for f in catalog["fonts"] if f["sources"]["fot"]["z"]]
    assert fot  # the smoothed source is among those compared
    overall = scores["overall"].fused
    for font in catalog["fonts"]:
        total = sum(e["weight_used"] or 0.0 for e in font["sources"].values())
        assert total == pytest.approx(overall[font["id"]].weight, abs=1e-5), font["id"]


def test_bands_and_rising_from_the_real_engine_pass_every_check(tmp_path: Path) -> None:
    ctx, _ = engine_build(tmp_path, n=400, catalog=300, rising=True)
    history = ({"run_date": "2026-08-03"}, {"run_date": "2026-09-03"})
    ctx = dataclasses.replace(ctx, state=State(run_history=history))
    docs = run_all(ctx)
    assert checks(ctx) == {}
    entries = [e for f in docs[export.CATALOG_FILE]["fonts"] for e in f["ranks"].values()]
    assert {e["band"] for e in entries} >= {None, "101\u2013250", "251\u2013500"}
    rising = [f["ranks"]["rising"] for f in docs[export.CATALOG_FILE]["fonts"]]
    assert any(e["rank"] is not None for e in rising)
    assert any(e["unranked"] == "no_evidence" for e in rising)


# --- the committed outputs ---------------------------------------------------------------------------


def test_committed_build_outputs_validate() -> None:
    """CI's check of the real run: every committed build/*.json against its schema."""
    present = [name for name in export.SCHEMA_FILES if (ROOT / "build" / name).is_file()]
    if not present:
        pytest.skip("no committed build outputs yet (the first real run commits them)")
    for name in present:
        schema = ROOT / "schemas" / export.SCHEMA_FILES[name]
        assert validate.schema_errors(ROOT / "build" / name, schema) == [], name
    if export.SITE_FILE in present:
        from tff_site.data import validate as validate_site

        validate_site(jsonio.load(ROOT / "build" / export.SITE_FILE))


# --- the committed outputs alone (validate --committed) --------------------------------------


def test_the_committed_checks_pass_on_a_clean_build(ctx: StageContext) -> None:
    assert [str(f) for f in validate.hard_checks(ctx, validate.COMMITTED_CHECKS)] == []
    assert validate.cmd_committed(ctx) == 0


def test_a_hole_in_the_exact_ranks_is_a_review_flag_not_a_failure(ctx: StageContext) -> None:
    # Owner ruling of 2026-09-28 (rank_holes): export closes the ranks up over a held-back
    # member, so a gap left in catalog.json is for review (review.rank_gap_flags).
    from tff_catalog import review

    catalog = ctx.paths.build / export.CATALOG_FILE
    doc = jsonio.load(catalog)
    ranked = [f for f in doc["fonts"] if f["ranks"]["overall"]["rank"] is not None]
    assert len(ranked) >= 2
    doc["fonts"] = [f for f in doc["fonts"] if f["ranks"]["overall"]["rank"] != 1]
    jsonio.dump(doc, catalog)
    assert "rank_gaps" not in {name for name, _ in validate.COMMITTED_CHECKS + validate.CHECKS}
    assert [str(f) for f in validate.hard_checks(ctx, validate.COMMITTED_CHECKS)] == []
    flags = [f for f in review.rank_gap_flags(catalog) if f.rank_key == "overall"]
    assert [f.message.split(" are")[0] for f in flags] == ["exact ranks 1"]


def test_a_ranks_only_source_holds_nothing_else_in_any_committed_file() -> None:
    private = frozenset({"google", "fot"})
    ok = {"sources": {"google": {"rank_in_source": 3, "z": 1.2, "state": "observed"}}}
    assert list(validate.private_fields(ok, private)) == []
    leak = {
        "fonts": [{"sources": {"google": {"z": 1.0, "value": 5}}}],
        "rising": {"google": {"inter": [0.1]}, "homebrew": {"inter": [0.2]}},
        "fot": 12,
    }
    assert list(validate.private_fields(leak, private)) == [
        "/fonts/0/sources/google",
        "/fot",
        "/rising/google",
    ]


def test_committed_known_answers_read_names_json(ctx: StageContext) -> None:
    names_path = ctx.paths.build / export.NAMES_FILE
    names = jsonio.load(names_path)
    names["families"] += [
        {
            "id": "source-sans-3",
            "family": "Source Sans 3",
            "names": [],
            "distinct_from": [],
            "in_catalog": False,
            "superfamily_id": None,
        },
        {
            "id": "source-sans-pro",
            "family": "Source Sans Pro",
            "names": [],
            "distinct_from": [],
            "in_catalog": False,
            "superfamily_id": None,
        },
    ]
    jsonio.dump(names, names_path)
    found = [
        f for f in validate.hard_checks(ctx, validate.COMMITTED_CHECKS) if f.check == "known_answer"
    ]
    assert [f.message for f in found] == [
        "Source Sans Pro must resolve to Source Sans 3 (source-sans-3)"
    ]
