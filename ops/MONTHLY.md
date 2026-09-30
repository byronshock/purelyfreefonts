# Monthly tasks

What the owner and Claude do by hand each month, in order. It starts from [methodology §10](../docs/ranking-methodology.md#10-manual-work-beyond-monthly-review) and uses only commands that exist on 2026-09-30 (Milestone 1 step 20). Milestone 4 step 12 extends it.

**Where it stands.** No live refresh has run yet, and the schedule is off. The first live refresh, the schedule and the watchdog come after Milestone 2, in [docs/milestone-refresh.md](../docs/milestone-refresh.md) (`refresh_timing`). Until then the site shows the 2026-09-26 catalog that `state/` holds, and only [Other manual tasks](#other-manual-tasks) come up. A step marked **(after milestone-refresh.md)** doesn't exist until the step of that milestone named with it.

Local commands run from the project root, with the private snapshot store:

```sh
export TFF_STORE=~/Documents/code/trulyfreefonts-data TFF_STORE_PUSH=0
```

## Each month

1. **Start the refresh (Claude).** On or after the 3rd, so the last month is complete.
   - **(after milestone-refresh.md step 1)** The schedule starts it at 06:17 UTC on the 3rd, once `REFRESH_SCHEDULE` is `on`.
   - Until then, by hand, and watch it:
     ```sh
     gh workflow run refresh.yml --repo byronshock/trulyfreefonts --ref main
     gh run list --repo byronshock/trulyfreefonts --workflow refresh.yml --limit 1
     gh run watch <run id> --repo byronshock/trulyfreefonts
     ```
   - To run a month again from the snapshots already in the store, with no fetching, add `-f from_snapshots=<YYYY-MM-DD>`.
2. **If it fails (Claude).** The `report` job opens the issue "Monthly refresh failed", or comments on it (written; its first real run is in milestone-refresh.md step 1). Read the failed steps with `gh run view <run id> --repo byronshock/trulyfreefonts --log-failed`, fix the cause in a pull request, and once it is merged, run again with `from_snapshots` set to the failed run's date. The snapshots are in the store if the store check passed.
3. **Let CI run on the refresh pull request (Claude).** The run opens or updates "Monthly refresh <date>" on the branch `refresh/monthly` and starts `ci.yml` there. GitHub holds the pull request's own CI run ("action_required"), and branch protection waits for it. Approve it, then check:
   ```sh
   gh run list --repo byronshock/trulyfreefonts --branch refresh/monthly --status action_required
   gh api -X POST repos/byronshock/trulyfreefonts/actions/runs/<run id>/approve
   gh pr checks refresh/monthly --repo byronshock/trulyfreefonts
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
6. **Record the rulings (Claude).** For each batch, write an answers file (`gate`, `day`, `header` for a new day's file, then one table per answered id; the format is in `src/tff_catalog/reviews.py`) and apply it:
   ```sh
   uv run --offline --frozen tff-catalog rulings apply <answers.toml>
   ```
   It checks the file and writes `data/reviews/<gate>/<day>.toml`. A settled decision also gets a dated entry in AUTHORITY.md. The rulings, and any config they change (such as `config/link-overrides.toml`), go to `main` in a pull request of their own.
7. **Run the month again (Claude).** Once the rulings are on `main`, run the month from its snapshots; the new run replaces the unmerged pull request:
   ```sh
   gh workflow run refresh.yml --repo byronshock/trulyfreefonts --ref main -f from_snapshots=<YYYY-MM-DD>
   ```
   Then step 3 again. Repeat steps 4 to 7 until no question is open and CI passes.
8. **Merge (the owner).** The owner reviews and merges the refresh pull request. Merging copies the run's state into `state/`, so the month becomes the published one; an unmerged pull request is replaced by next month's run. A merge also counts as activity: GitHub disables the schedule of a public repository after 60 days without any.
9. **Deploy and check (Claude; after Milestone 2 step 11 and milestone-refresh.md step 2).** The merge deploys the site by itself (M2-D6), or Claude runs `ops/deploy.sh production --commit <merge commit>`. Then `curl -s https://trulyfreefonts.com/version.txt` must show the month's `run_date`. Production deploys are off until Milestone 2's soft launch.
10. **Usage (after milestone-refresh.md step 2).** Cloudflare's monthly requests and unique visitors go into `ops/USAGE.local.md`, which is never committed.
11. **Tidy up (Claude).** `git worktree remove <scratch>/monthly`, then `uv run --offline --frozen tff-catalog store gc`, which deletes raw download directories older than 7 days.

## Other manual tasks

- **Yearly: the new Web Almanac edition.** When the new edition's Fonts chapter has its sheet, switch `edition`, `sheet_id`, `crawl_date` and both tabs together in `config/sources/almanac.toml`, as its comments say, and flag the switch in that month's pull request.
- **Once: Fonts Over Time's data license.** Asked on 2026-09-25 in [fcjr/fontsovertime#1](https://github.com/fcjr/fontsovertime/issues/1). Claude looks for an answer each month (`gh issue view 1 --repo fcjr/fontsovertime --comments`); until then ruling T4 applies.
- **Once, after the third merged refresh: the alert thresholds.** `config/ranking.toml [review]` keeps §9's thresholds until the owner revisits them after 3 merged refreshes (ruling of 2026-09-26). The backtest informs it: after a local replay (step 4), `uv run --offline --frozen tff-catalog backtest` writes `docs/backtests/<date>.md` (Milestone 1's smaller item, step 13).
- **On request: a missing font (after [milestone-more-fonts.md](../docs/milestone-more-fonts.md) step 1).** Someone asks by email to admin@trulyfreefonts.com or with the "Missing font" issue form (`gh issue list --repo byronshock/trulyfreefonts --label missing-font`). Claude checks the family against the gates and the link policy and proposes a ruling, and the owner rules. An accepted family joins `config/foundries.toml` with the date it was added, never who asked, and is listed from the next refresh. Until that step, the file has no field for the date, and the site doesn't show fonts without a rank.
