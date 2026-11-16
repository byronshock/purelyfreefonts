# Plan: the soft launch, with the hopper and October's run

This is the working checklist for Milestone 2's soft launch ([docs/milestone-2.md](docs/milestone-2.md) step 14), as the owner ruled it on 2026-11-06 and 2026-11-10 (**Soft launch** in [AUTHORITY.md](AUTHORITY.md); `data/reviews/ci/2026-11-06.toml`, `method/2026-11-06.toml`, `site/2026-11-06.toml` and `site/2026-11-10.toml`). In short:
- the site launches with October's run (2026-10-17) on the new method (`launch_data_october`);
- it launches with the hopper (`launch_with_hopper`);
- production deploys from GitHub Actions, set up first (`deploys_actions_first`);
- usability round 1 comes after the launch (`usability_round_1_after_launch`).

**Status (2026-11-12):**
- Production serves the "Coming soon." stub, and `main` holds September's catalog on the old method.
- Staging shows October's run with #100 (the hopper) and #115 (no early-version line).
- #115 (no early-version line) and #116 (the redraw budget) are merged.
- Open drafts:
  - #88: October's run, which the launch run supersedes;
  - #100: the hopper;
  - #117: this plan's records;
  - #118: the font cache (step 2A);
  - #119: Charter's text and the README-only Nerd links (step 2A).

**How to read each step:** **Who**, **Depends on** and **Done when**, as in PLAN-hopper.md. Tick each item as soon as it is done and verified. Checklists nest: a parent is ticked only when every item under it is. If an item is only partly done, leave it unticked and note what's left.

**The merge order:**
1. #115 and #116 (merged 2026-11-11);
2. this plan's records;
3. step 2's catalog-affecting changes;
4. #100;
5. the launch run;
6. step 2's site-only changes and PR 5.

Until the launch:
- `PRODUCTION_DEPLOYS` stays off, and `deploy.yml` is never dispatched on `main`, since a dispatch always runs;
- no refresh run is dispatched other than the launch run, since any run replaces `refresh/monthly`.

---

### Step 1: The records
**Who:** Claude; the owner merges. **Depends on:** the owner's rulings of 2026-11-06 and 2026-11-10.
- [x] The rulings in `data/reviews/` (four files, above).
- [x] AUTHORITY.md:
  - [x] the **Soft launch** section;
  - [x] change notes at Releases, `sn_pro_fold_timing`, the score review, M2-D7, `refresh_timing`, Collect and hold, and "Current step";
  - [x] this file among the tracked docs.
- [x] docs/milestone-2.md:
  - [x] the hopper in its opening;
  - [x] the critical path;
  - [x] step 13 follows step 14, on the live site;
  - [x] step 14's "Depends on" names step 12b's launch part, the hopper, the launch run and Actions deploys;
  - [x] the deploy item and the live checks;
  - [x] step 15 depends on step 13;
  - [x] step 16's note;
  - [x] M2-D7's row.
- [x] docs/milestone-refresh.md, docs/milestone-method.md (the score review, ticked, and its refresh rule), docs/milestone-3.md, docs/milestone-4.md (its opening), docs/usability-test.md (round 1's row) and docs/roadmap.md.
- [x] ops/MONTHLY.md: no refresh run but the launch run until it merges.
- [x] PLAN-REVIEWERS-1.md: step 4 ticked (built in #65), and the order before and after the launch, step 7d's item included.
- [x] README.md: the first release carries the hopper.

**Done when:** the records pull request is merged.

### Step 2: The work before the launch

#### 2A. Catalog-affecting changes, before the launch run
**Who:** Claude; the owner rules on each report. **Depends on:** step 1.
- [x] **Nerd Font builds** (step 14's license item): confirm each linked build's license check ([docs/nerd-fonts-link.md](docs/nerd-fonts-link.md)), and hide any that fail in `config/nerd-hidden.toml`, which stage "links" reads. *(2026-11-11, at v3.5.1: none of the 65 fails, so none is hidden; `nerd_builds_checked`, 2026-11-12.)*
- [x] **Heavy Data** (PLAN-REVIEWERS-1 step 8a): re-read its license text against Rules 1–3, and report to the owner. *(The ruling of 2026-09-28 stands, `heavy_data_reread`, 2026-11-12. It is outside October's catalog.)*
- [x] **Charter** (step 8a): propose a pinned license link for the owner's ruling. Its text is in the download (`config/license-texts.toml`). A new `texts` URL needs an L3 question and a store top-up, as Luciole's did.
  - [x] Proposed and ruled (2026-11-12, `charter_license_text`): X.Org's COPYING at font-bitstream-type1 1.0.4, the same words as the canonical Bitstream Charter text.
  - [x] `config/license-texts.toml`: that URL in `texts`, and no `page` (#119, merged 2026-11-13).
- [x] **Nerd folders that hold only a README** (`K-nerd-readme-folders`, 2026-11-12): those builds link to the release page at the tag, not the folder (`src/tff_catalog/links.py`). That is 12 links at v3.5.1. *(#119, merged 2026-11-13.)*
- [x] **The store top-up** (`store_topup_charter_nerd`), once both changes above are on `main` (#119): `tff-catalog verify --date 2026-10-17 --refetch` for Charter's text and `tff-catalog links --date 2026-10-17` for the release page's check, every changed row diffed before the store commit and pushed. It must come before the launch run, which otherwise stops at export. On a scratch copy (2026-11-12) it added two rows, the X.Org text and the release page's check, and dropped 42 texts the switch's top-up had added, which the owner accepted (`store_topup_texts_rebuilt`); the replay changed only Charter's text and the 12 links. *(Done 2026-11-13 from `main` at 83eb564: store commit e91c89f, pushed. Every changed row matches the scratch test's byte for byte: the same two rows added and 43 removed. A replay of 2026-10-17 after it succeeds, with the same `build/` as the scratch test's apart from `code_commit`, and with a full local font cache no specimen missing.)*
- [x] **The font cache:** fill it before a replay (milestone-method.md, "Later: from the port's adversarial review"), so the launch run draws every specimen it can, reproducibly. Merienda's stays out, flagged under Milestone 2 step 5. *(Built in #118: a replay fills the cache from its own catalog and runs again. Checked locally with an empty cache: 120 specimens lacking after the first pass, none after the second, and a clean replay identical. Merged 2026-11-13. milestone-method.md's item stays open until the launch run's pull request shows every specimen.)*

**Done when:** each is merged, or ruled unchanged.

#### 2B. The hopper
**Who:** Claude; the owner rules on the words and merges. **Depends on:** 2A for the merge.
- [ ] **#100's PR 4 items** (docs/milestone-3.md, slice 1): verified and ticked.
  - [x] Items 2 to 5 (2026-11-11, on #100's branch at 604ce2c): each checked against the code and tests. The count's M is now pinned with a list, and the privacy item notes `hopper_previews_all_at_once`.
  - [ ] Item 1, the owner's layout review: its last part is his try, which folds into step 3's final try (`hopper_try_in_final_try`).
- [ ] **#100 merged,** before the launch run, so the run's CI checks the hopper on October's data.
- [ ] **PR 5, the words** (the owner rules on the wording):
  - [ ] privacy.md's "Coming next" becomes a section on how the check works:
    - nothing of the list leaves the device;
    - two fixed requests, the matcher and the match file;
    - one batch of previews, its count computed by the build or not given;
    - nothing stored;
  - [ ] about.md's "What's next" changes to match.
- [ ] **docs/milestone-4.md:** step 3's stale lines on the "Load previews" button and the one match-file request, fixed after #100 merges. The opening now notes the hopper (step 1); step 8 and Step 0 are revisited when Milestone 4 starts.

**Done when:** #100 and PR 5 are merged.

#### 2C. The launch run
**Who:** Claude runs it and approves the held CI (ops/MONTHLY.md step 3); the owner merges. **Depends on:** 2A, #100.
- [ ] Re-run 2026-10-17 from `main`, as ops/MONTHLY.md step 7 says. Its pull request supersedes #88, and the records cite its number.
- [ ] On its branch:
  - [ ] Gentium Book: its entry, its two known answers, and `build/names.reviewed.toml`;
  - [ ] the stopgap specimen pins (`src/tff_site/trim.sha256`, `tests/site/trimmed.sha256`), recomputed (`launch_weight_after`).
- [ ] Before it merges:
  - [ ] `validate --committed`;
  - [ ] the site built twice, byte-identical;
  - [ ] `tff-site check`;
  - [ ] the held CI green.
- [ ] Merged. Then November's run, started by hand at once (docs/milestone-refresh.md step 1's live run and npm check), its pull request held until after the launch.

**Done when:** the launch run is on `main`, and November's run has opened its held pull request.

#### 2D. Site-only changes
**Who:** Claude; the owner rules where noted. **Depends on:** the launch run, so CI tests them on the launch data.
- [ ] **Priority 1's wording** (PLAN-REVIEWERS-1 step 8b):
  - [ ] About's "with no use restrictions";
  - [ ] the methodology text without its "(owner ruling of …)" asides;
  - [ ] the sentence on where the measure is chosen.
- [ ] **"Comes with" names the systems** (`comes_with_names_systems`), after single-select questions on Q4's two loose ends.
- [ ] **Step 5's first part:**
  - [ ] the Tip Jar link at the end of the header nav, and the footer's tip link removed. The one-row header is re-measured, with a question to the owner on the phone nav at 320 px;
  - [ ] "No listed font matches '…'" when search alone finds nothing;
  - [ ] name matches first in search (`search_name_first`);
  - [ ] About's note rewritten to end with the tip pitch, its words OK'd by the owner.
- [ ] **The docs:**
  - [ ] ops/DONATIONS.md step 11, for the Tip Jar;
  - [ ] ops/deploy/README.md's item 6 and ops/SERVER.md's header-phase lines, which still word Caddy's phase B as pending.

**Done when:** each is merged.

#### 2E. Infrastructure
**Who:** the owner (the deploy accounts and keys, and their secrets); Claude (the environments, deploys, checks and records), as `deploys_actions_first` divides it. **Depends on:** nothing.
- [ ] **Claude, first:** create the environments with `gh`, each with its branch rule (`production` from `main` only, `staging` from `staging` only), before anything is pushed to `staging` or any secret is added; then their variables.
- [ ] **The owner, in this order** ([ops/deploy/README.md](ops/deploy/README.md)):
  1. [ ] run stage A again with the deploy keys;
  2. [ ] run stage B, following its lockout precautions;
  3. [ ] run the key tests;
  4. [ ] set each environment's secrets;
  5. [ ] shred the local key copies.
- [ ] **Claude, then:**
  - [ ] the first Actions staging deploy, from the branch `staging`, holding the current preview commit, never `main`'s September data. Staging dispatches use `--ref staging` only;
  - [ ] Milestone 2 step 11's done-when on the real server: the 100 ms loop check (`errors=0 mixed=0`) and the one-command rollback;
  - [ ] SERVER.md records stage B.

**Done when:** an Actions deploy to staging has passed its live tests, and the rollback has been tried.

#### 2F. The owner's items
**Who:** the owner. **Depends on:** 2D for the methodology read.
- [ ] Milestone 2 step 13b:
  - [ ] Stripe's public business name, website, statement descriptor, support email and payment page text;
  - [ ] mail filters;
  - [ ] the private notes that still name the old site.
- [ ] Accept Milestone 1's handoff (Milestone 1 step 20), and update PLAN.md's Milestone 2 line (Milestone 2 step 0).
- [ ] Milestone 2 step 6, by hand: the keyboard-only pass and the Orca-with-Firefox pass, over the list and the hopper. Claude runs a scripted pass first, to catch faults before the owner's time.
- [ ] Read the methodology page on staging (docs/milestone-method.md step 12), once 2D's fixes are on it.

**Done when:** each is done, or ruled out with a reason.

### Step 3: The launch (Milestone 2 step 14)
**Who:** Claude deploys and checks; the owner gives the go-ahead. **Depends on:** step 2.
- [ ] `staging` moves to `main`'s head, and Actions deploys it; the site suite and the live tests pass.
- [ ] The owner's final try on staging, on a phone and a computer: drop, choose and paste his own list in Chromium and Firefox, with the Network tab open. It finishes PLAN-hopper.md step 4's try, F3's Show menu included (`hopper_try_in_final_try`), and then PR 4's item 1 is ticked.
- [ ] Final checks: every CI check, step 9's live test, step 10's numbers, and the tip link with Stripe's renamed page.
- [ ] **Go**, on the owner's word:
  - [ ] set `PRODUCTION_DEPLOYS` to on;
  - [ ] merge the pull request that removes the stub from `public/`. It also updates ops/SERVER.md's robots.txt check and its stub lines, docs/milestone-2.md's, and README.md's Status. Its push deploys `main` to production, and from then on every merge to `main` deploys.
- [ ] **Verify the live site:**
  - [ ] SERVER.md's and DONATIONS.md's Verification lines;
  - [ ] the live tests against production in Chromium and Firefox, and the hopper's privacy test in WebKit, run locally (Milestone 3 step 9 stays open for WebKit in CI);
  - [ ] `version.txt` names the merged commit and run date 2026-10-17;
  - [ ] ops/DONATIONS.md step 12 ticked, its deploy line changed to the Actions path.
- [ ] Search engines: `robots.txt` and `sitemap.xml` allow indexing (M2-D8). No announcement.
- [ ] AUTHORITY.md: the soft-launch date under Releases, and "Current step".

**Done when:** the owner has checked the live site on a phone and a computer.

### Step 4: After the launch
**Who:** both. **Depends on:** step 3.
- [ ] November's run merges, as the first monthly update.
- [ ] docs/milestone-refresh.md step 1's other items, then `REFRESH_SCHEDULE` on:
  - the held-CI decision;
  - a link check that tells someone;
  - the watchdog.
- [ ] **Before usability round 1:**
  - [ ] PLAN-REVIEWERS-1 step 11, the second review round;
  - [ ] PLAN-REVIEWERS-1 step 6, sticky filters;
  - [ ] step 7e, unless the owner rules it out of the round;
  - [ ] docs/usability-test.md rewritten for the live site and the hopper: round 1's place, task 9 (the paste is live now), task 4, and Milestone 3 step 11's tasks;
  - [ ] the owner approves the script and the invitation.
- [ ] Usability round 1 (Milestone 2 step 13), then step 15's rounds 2 and 3.
- [ ] Priority 3, before the A–Z list: Brotli (9a) and the names-only redraw (7d).
- [ ] PLAN-REVIEWERS-1's other steps (2, 3 and 10, and step 5's second part).
- [ ] The rest of Milestone 3, WebKit in CI among it, then Milestone 4's announcement.

**Done when:** this checklist hands its last items to Milestones 2 to 4.
