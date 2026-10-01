# Monthly refresh goes live checklist (after Milestone 2, before Milestone 3)

This milestone runs the monthly refresh live for the first time and switches its schedule on, so the catalog updates itself each month. On 2026-09-30 the owner moved it out of Milestones 1 and 2 (`refresh_timing`, [AUTHORITY.md](../AUTHORITY.md)): it comes after Milestone 2's launch and before Milestone 3's owned-font tools. Until then the site shows the catalog of 2026-09-26, which `state/` holds as the first published run ([#31](https://github.com/byronshock/trulyfreefonts/pull/31)). Its items were the rest of Milestone 1 step 19, one item each of Milestone 1 steps 3 and 14, and Milestone 2 step 16; the wording is theirs, with only the references updated.

**How to read it.** *Who* says who does the work, *Needs* what must be finished first, and *Done when* what must be true to close a step. Tick each item as soon as it is done and verified. If an item is only partly done, leave it unticked and note what's left.

**This milestone is done when:**
- a live refresh started by hand has opened a correct pull request, CI has run on it, and the owner has merged it;
- the schedule is on, and the watchdog is installed;
- a refresh has reached the live site with no step beyond the merge (or one command under M2-D6 (a)), and the post-deploy test has passed.

---

### Step 1: The first live refresh, and the schedule on
**Who:** Claude runs it with `gh` (owner ruling CI1, 2026-09-26); the owner reviews and merges. **Needs:** Milestone 2's live site. Run it on or after the 3rd of a month, so the last month is complete.

Already done in Milestone 1 step 19, on 2026-09-30: GitHub Actions may open pull requests; the data store's deploy key and `DATA_STORE_KEY` secret exist ([ops/SERVER.md](../ops/SERVER.md), section J); and run A, a replay of the 2026-09-26 snapshots on GitHub, opened #31, which filled `state/`.

- [ ] Move `DATA_STORE_KEY` into an environment that only `main` may use, as [ops/deploy/README.md](../ops/deploy/README.md) (item 4) asks: create the environment `refresh` with its deployment branches limited to `main`, set the secret there, delete the repository secret, and have `refresh.yml`'s `refresh` and `store` jobs name `environment: refresh`. On 2026-09-30 the key went in as a repository secret, because the workflow names no environment yet; a repository secret can be read by the workflows of every branch in this repository, though never by a pull request from a fork.
- [ ] Run it live by hand (`gh workflow run refresh.yml --repo byronshock/trulyfreefonts --ref main`, with no `from_snapshots`) and check:
  - that the collectors work on GitHub's runners: rate limits, the GitHub API budget, and whether Google's endpoints respond;
  - that it opens or updates the pull request on the fixed branch `refresh/monthly` with `catalog.json`, `catalog-site.json`, `review.md`, the `state/` changes, and the alias rows and seeds the run added to `data/`, and that a hard failure fails the job and opens an issue instead (the `report` job, written but not yet run);
  - that fetching a real source twice on one date keeps exactly one snapshot per date (from Milestone 1 step 3; the fake-collector tests in `tests/test_store.py` and `tests/test_fetch.py` pass).
  - that the file-pick fix of 2026-09-30 (31771b4) has taken effect where the frozen 2026-09-26 snapshots stopped it: Charter, Gentium, Ioskeley Mono, Pretendard Std, psudoFont Liga Mono and Zed Sans pin their Regular file instead of a Bold or Black one and get new specimens, and Mukta's license text URL is `ofl/mukta/OFL.txt` instead of `ofl/ekmukta/OFL.txt` (the same text). The rebuild could change only Mukta's font file, because a replay has no network and reads only files and texts whose facts the store already holds.
- [ ] Make the refresh pull request's CI count without a hand. GitHub holds the `pull_request` CI run on a pull request the bot opens ("action_required"), and branch protection waits for that run even though the CI the workflow dispatches on `refresh/monthly` passes: #31 was BLOCKED until the held run was approved (`gh api -X POST repos/byronshock/trulyfreefonts/actions/runs/<id>/approve`). Decide between a monthly runbook step that approves it and a change to the workflow, before the schedule is on.
- [ ] A monthly link check that tells someone (from Milestone 1 step 14). The check runs in every refresh and as `tff-catalog links --check`, but a failure reaches only the log and `build/stage/queues/links.json`: `review.md`, validate and gate K don't report it, and `refresh.yml` doesn't run `links --check`. So a link that breaks later would stay published without a flag.
- [ ] Install the watchdog on the VPS. GitHub disables scheduled workflows in public repositories after 60 days without activity, so a timer outside GitHub, reading its public API, warns the owner if no refresh pull request has appeared for 35 days, or `main` has had no commit for 50 days. (Written: `ops/refresh-watchdog/`, a script with a systemd timer.)
- [ ] Turn the schedule on with `gh variable set REFRESH_SCHEDULE --body on`. The workflow then runs at 06:17 UTC on the 3rd of each month (`17 6 3 * *`) and on demand, with a uv cache, the `refresh` concurrency group, and write permission for `contents`, `pull-requests` and `issues`.

**Done when:** a live run started by hand opens a correct pull request, CI runs on it, the owner merges it, and the schedule is on.

### Step 2: The first monthly refresh reaches the live site
**Who:** Claude; the owner merges. **Needs:** step 1; Milestone 2's deploy path (M2 step 11) and live site (M2 step 14). This was Milestone 2 step 16.
- [ ] CI on the refresh pull request (step 1) also builds and tests the site, so a catalog that breaks the page can't merge.
- [ ] After the merge the site updates by itself (M2-D6 (b)) or by `ops/deploy.sh` (a); the live `version.txt` shows the new run date.
- [ ] Only fonts whose files changed get new specimens; old ones leave with old releases.
- [ ] Usage baseline without analytics: Claude adds `ops/*.local.md` to `.gitignore`; each refresh, Cloudflare's monthly requests and unique visitors go into `ops/USAGE.local.md` for Milestone 4's two-month review (M4-D4), read by the owner from the dashboard or fetched by Claude if the token gains Analytics read.

**Done when:** one refresh has gone from merge to live site with no step beyond the merge (or one command under M2-D6 (a)), and the post-deploy test has passed.
