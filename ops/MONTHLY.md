# Monthly tasks

What the owner and Claude do by hand each month, in order. It starts from [methodology §10](../docs/ranking-methodology.md#10-manual-work-beyond-monthly-review) and uses only commands that exist on 2026-09-30 (Milestone 1 step 20). Milestone 4 step 12 extends it.

**Where it stands.** No live refresh has been merged yet, and the schedule is off. Since 2026-10-16 (`refresh_collect_and_hold`) each month's live run is dispatched by hand and its pull request kept as an unmerged draft until Milestone 2's launch and the scoring-method port; the first was 2026-10-17. Its steps are steps 1 to 6 below, without the merge, and the review covers only the questions that don't depend on the method. Since 2026-10-19 (`staging_previews_held_runs`) a held run may also be previewed on staging, from a branch `preview/<date>` that is never merged ([milestone-refresh.md](../docs/milestone-refresh.md), "Collect and hold"). The first merged refresh, the schedule and the watchdog come after Milestone 2, in [docs/milestone-refresh.md](../docs/milestone-refresh.md) (`refresh_timing`). Until then `state/` holds the 2026-09-26 catalog as the published one, and staging shows it, or a held run's preview. A step marked **(after milestone-refresh.md)** doesn't exist until the step of that milestone named with it.

Local commands run from the project root, with the private snapshot store:

```sh
export TFF_STORE=~/Documents/code/trulyfreefonts-data TFF_STORE_PUSH=0
```

## Each month

1. **Start the refresh (Claude).** On or after the 3rd, so the last month is complete.
   - **(after milestone-refresh.md step 1)** The schedule starts it at 06:17 UTC on the 3rd, once `REFRESH_SCHEDULE` is `on`.
   - Until then, by hand, and watch it:
     ```sh
     gh workflow run refresh.yml --repo byronshock/purelyfreefonts --ref main
     gh run list --repo byronshock/purelyfreefonts --workflow refresh.yml --limit 1
     gh run watch <run id> --repo byronshock/purelyfreefonts
     ```
   - To run a month again from the snapshots already in the store, with no fetching, add `-f from_snapshots=<YYYY-MM-DD>`.
   - Dispatch only with `--ref main`: the data store's key is a secret of the environment `refresh`, which refuses every other branch ([SERVER.md](SERVER.md) item 27a).
2. **If it fails (Claude).** The `report` job opens the issue "Monthly refresh failed", or comments on it (written; its first real run is in milestone-refresh.md step 1). Read the failed steps with `gh run view <run id> --repo byronshock/purelyfreefonts --log-failed`, fix the cause in a pull request, and once it is merged, run again with `from_snapshots` set to the failed run's date. The snapshots are in the store if the store check passed.
3. **Let CI run on the refresh pull request (Claude).** The run opens or updates "Monthly refresh <date>" on the branch `refresh/monthly` and starts `ci.yml` there. GitHub holds the pull request's own CI run ("action_required"), and branch protection waits for it. Approve it, then check:
   ```sh
   gh run list --repo byronshock/purelyfreefonts --branch refresh/monthly --status action_required
   gh api -X POST repos/byronshock/purelyfreefonts/actions/runs/<run id>/approve
   gh pr checks refresh/monthly --repo byronshock/purelyfreefonts
   ```
   Before the schedule goes on, milestone-refresh.md step 1 decides whether this stays a step here or the workflow changes.
4. **Replay the month locally (Claude).** The pull request carries `build/review.md`, but not the queues in `build/stage/` that `questions` reads. Replay the run as the workflow's replay does: on `main`, with the pull request's alias table and seeds.
   ```sh
   git -C "$TFF_STORE" pull --ff-only
   git fetch origin main refresh/monthly
   git worktree add --detach <scratch>/monthly origin/main
   cd <scratch>/monthly
   rm -rf data/alias-seeds
   git checkout origin/refresh/monthly -- data/aliases.csv data/alias-seeds
   uv sync --offline --frozen
   unshare -rn uv run --offline --frozen tff-catalog refresh --from-snapshots <YYYY-MM-DD>
   ```
   Then print each gate's open questions: A and U (new aliases and unmatched names), LIC and L3 (licenses and failed license checks), X (preinstalled and dependency cases), L (new dual-script families) and K (download links). Also check the links, which the refresh doesn't report yet (a check that tells someone is milestone-refresh.md step 1):
   ```sh
   uv run --offline --frozen tff-catalog questions --gate <gate>
   uv run --offline --frozen tff-catalog links --check --date <YYYY-MM-DD>
   ```
5. **Review (Claude, then the owner).** Claude reads `build/review.md` in the pull request: entries, exits, big moves, license changes and every flag of methodology §9. Claude asks the owner the open questions in chat, four at a time, single-select, with the recommended option marked, and explains the flags that need no ruling.
   - **The hopper's reviewed list** (Milestone 3; [site/CONTRACT.md](../site/CONTRACT.md) section 11). The site build refuses a `build/names.json` that disagrees with `build/names.reviewed.toml` (and three collisions that no edit to it clears, in the next item): a run that adds, changes or drops an ambiguous name (a family of its own that is also, without a style word, a catalog family's weight, like Archivo Black), or that moves or drops a recoded alias (HackGen, `hopper_hackgen_related`) or takes its family out of the catalog (then the recode changes nothing), or adds a name of its family that reaches it and would own the family again (a HackGen NF build of Hack needs a recode of its own). Then CI fails on the pull request (the tests that read the committed names, and `site-real`), and so would its deploy. The message names each name and its families, and a recoded alias's problem with them, all in one run of the build; locally, `uv run --offline --frozen tff-site build --data build/catalog-site.json --out <scratch>/site` prints it. Claude asks the owner about each one with the other questions, records the ruling (step 6), and edits the list to match, citing it; a change that a recorded ruling already covers needs no new question, only the edit, citing that ruling; the tests read the ambiguous set and the recodes from the list, so they need no edit for either. One checks that an entry citing `inventory_ambiguous_names` is one of its five names, with the families it names, and that an entry citing `hopper_hackgen_related` is HackGen or a name that reaches its key, such as HackGen NF, on hack as related (so a recode for a new HackGen NF cites that ruling with no new question); a new name, or a change a ruling doesn't cover, cites the new ruling. The hopper's known answers (`tests/vectors/owned-known-answers.json`) are the other thing a run can break: they hold on the committed `names.json`, so `tests/test_owned.py` fails when a run drops a family a case names, or changes a case's result (a run that drops HackGen does: two cases expect it to be only near Hack). A case that names a dropped family is edited in `known_answers()` in `tests/helpers/owned_vectors.py` and the file rewritten (`uv run --offline --frozen python -m tests.helpers.owned_vectors`; review the diff); a changed result is a question for the owner first, as the cases are Milestone 3's checks. These edits go on the refresh pull request (step 7), never to `main` ahead of the month: the list must agree with the `names.json` beside it, so on `main` it would fail the published month's build. A held run's `preview/<date>` branch needs the same edits. `preview/2026-10-17` does: its Gentium Book may be a weight of gentium-plus, not gentium, which `inventory_ambiguous_names` already covers ("Gentium Plus on the October data"), so its entry's `families` becomes `["gentium-plus"]`, citing that ruling, with no new question; and two Gentium cases name the family gentium, which that run no longer has.
   - **Collisions the list can't clear.** Three of the build's refusals end with "no entry in the reviewed list clears this": a `distinct_from` pair that meets, a key that two catalog families own outside the ambiguous set, and a catalog name that strips to the key of a family outside the catalog. Milestone 3 step 3 fails these "outside the ambiguous set", which holds catalog families only, so no `[[ambiguous]]` entry clears them, and no `[[recode]]` does either: the build refuses a recode of a key that two families hold, stripping reads no relation, and a family's own name can't be recoded. The one exception is a meeting at a key that one catalog family holds alone, through its aliases: a recode that makes them near-matches ends it, so that message ends instead with the recode the owner may rule (or an alias row, or a change to the build), and a ruled recode goes in the list on the refresh pull request, as in the item above. An entry or an exit can bring the third: Alkalami Light entering the catalog while Alkalami stays out (the committed `names.json` has ten such pairs with both ends outside the catalog, Alkalami's, Padauk's, Salaowu's, Shimenkan's and Dai Banna SIL's), or Archivo leaving while Archivo Black stays, which also drops Archivo Black from the ambiguous set (one question, not two). Claude asks the owner about each with the other questions, saying what the hopper would do (from a plain list, "Alkalami Light", Alkalami's Light weight, would count Alkalami Light as owned), and the ruling is carried out on `main`, then the month runs again (step 7):
     - when the two names are one family, or a name sits on the wrong family, by an alias row through Milestone 1's review, which goes to `main` with the rulings (step 6), so the next run's `names.json` no longer has the collision;
     - otherwise (two families, as Archivo and Archivo Black are, or a style word that shouldn't be stripped), by the change to the build that the ruling asks for, such as an ambiguous set that can hold a family outside the catalog, or `tff_catalog.owned`'s style words, which the page's matcher follows: the lead's pull request to `main`.

     Until then the refresh pull request stays red and unmerged, the published month stays, and a held run's `preview/<date>` build fails the same way.
6. **Record the rulings (Claude).** For each batch, write an answers file (`gate`, `day`, `header` for a new day's file, then one table per answered id; the format is in `src/tff_catalog/reviews.py`) and apply it:
   ```sh
   uv run --offline --frozen tff-catalog rulings apply <answers.toml>
   ```
   It checks the file and writes `data/reviews/<gate>/<day>.toml`. A settled decision also gets a dated entry in AUTHORITY.md. The rulings, and any config they change (such as `config/link-overrides.toml`), go to `main` in a pull request of their own.
7. **Run the month again (Claude).** Once the rulings are on `main`, run the month from its snapshots; the new run replaces the unmerged pull request:
   ```sh
   gh workflow run refresh.yml --repo byronshock/purelyfreefonts --ref main -f from_snapshots=<YYYY-MM-DD>
   ```
   Then step 3 again. Repeat steps 4 to 7 until no question is open and CI passes. A run force-creates `refresh/monthly` from `main`, so a change to the hopper's reviewed list or its known answers (step 5) is committed on `refresh/monthly` after the last run, and CI approved again (step 3).
8. **Merge (the owner).** The owner reviews and merges the refresh pull request. Merging copies the run's state into `state/`, so the month becomes the published one; an unmerged pull request is replaced by next month's run. A merge also counts as activity: GitHub disables the schedule of a public repository after 60 days without any.
9. **Deploy and check (Claude; after Milestone 2 step 11 and milestone-refresh.md step 2).** The merge deploys the site by itself (M2-D6), or Claude runs `ops/deploy.sh production --commit <merge commit>`. Then `curl -s https://purelyfreefonts.com/version.txt` must show the month's `run_date`. Production deploys are off until Milestone 2's soft launch.
10. **Usage (after milestone-refresh.md step 2).** Cloudflare's monthly requests and unique visitors go into `ops/USAGE.local.md`, which is never committed.
11. **Tidy up (Claude).** `git worktree remove <scratch>/monthly`, then `uv run --offline --frozen tff-catalog store gc`, which deletes raw download directories older than 7 days.

## Other manual tasks

- **Yearly: the new Web Almanac edition.** When the new edition's Fonts chapter has its sheet, switch `edition`, `sheet_id`, `crawl_date` and both tabs together in `config/sources/almanac.toml`, as its comments say, and flag the switch in that month's pull request.
- **Once: Fonts Over Time's data license.** Asked on 2026-09-25 in [fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1). Claude looks for an answer each month (`gh issue view 1 --repo fcjr/fontsovertime --comments`); until then ruling T4 applies.
- **Once, after the third merged refresh: the alert thresholds.** `config/ranking.toml [review]` keeps §9's thresholds until the owner revisits them after 3 merged refreshes (ruling of 2026-09-26). The backtest informs it: after a local replay (step 4), `uv run --offline --frozen tff-catalog backtest` writes `docs/backtests/<date>.md` (Milestone 1's smaller item, step 13).
- **On request: a missing font (after [milestone-more-fonts.md](../docs/milestone-more-fonts.md) step 1).** Someone asks by email to admin@purelyfreefonts.com or with the "Missing font" issue form (`gh issue list --repo byronshock/purelyfreefonts --label missing-font`). Claude checks the family against the gates and the link policy and proposes a ruling, and the owner rules. An accepted family joins `config/foundries.toml` with the date it was added, never who asked, and is listed from the next refresh. Until that step, the file has no field for the date, and the site doesn't show fonts without a rank.
