# The scoring-method port checklist (2026-10-19)

This checklist brings the owner's scoring-method rulings of 2026-09-30 and 2026-10-01 (`data/reviews/method/`, [AUTHORITY.md](../AUTHORITY.md) "The scoring method") into the code, then switches the held October run to the new method. On 2026-10-19 the owner lifted the hold on the port and the freeze on the work tied to it (`port_freeze_lifted`), had the lead session port it (`port_by_lead`), made one GitHub replay of the held run of 2026-10-17 the switch (`october_replay_is_the_switch`), and ruled the shown score A′ in every view (`score_display_linear_all_views`), membership starting fresh at the switch (`membership_fresh_at_switch`), a new method version for all seven ranks (`method_versions_all_seven`), the encrypted store bundle (`store_bundle_encrypted`) and the October top-ups (`october_topups_preauthorized`).

**How to read it.** *Who* says who does the work, *Needs* what must be finished first, and *Done when* what must be true to close a step. Tick each item as soon as it is done and verified; a parent is ticked only when every item under it is. If an item is only partly done, leave it unticked and note what's left.

**Rules for every step:**
- Each step is one pull request from `main`, merged by the owner. None changes `build/` or `state/`: only merging a refresh pull request does, and that waits for the soft launch (`refresh_timing`, `refresh_collect_and_hold`). Each keeps `validate --committed` and every CI check green on the committed 2026-09-26 catalog.
- **Private values (rulings T2, T4, and the scoring plan's leak rule).** No Google Fonts or Fonts Over Time value, and no estimate, share or fitted parameter of the model, goes into the repository, a log, a pull request, a test message or the public methodology. The fitted parameters live only in the private store. A step that adds method text gets the leak check of step 0.
- The model's reference is the private workbench tff-stats; its plan's numbers stay there.

**This checklist is done when:** the port is merged; the October replay's draft pull request has replaced #70 with its CI noted; staging shows it; and the owner has seen its scores. The soft launch is then the owner's call.

---

### Step 0: The leak check (a procedure, run on every step that adds method text)
- [x] A scratch script, never committed, lists every number in the step's public diff and compares it with the private plan's marked values. *(On every pull request that added method text; on 2026-10-21 over everything the port added since main's 1aa3a0d: only config values, public rulings, section numbers and dates.)*
- [x] A read for derived values (percentages, shares, ratios).
- [x] It covers the methodology text, schema descriptions, comments in `config/ranking.toml` and `config/site.toml`, and any review text.

### Step 1: The records
- [x] The rulings of 2026-10-19 (`data/reviews/method/2026-10-19.toml`, `site/2026-10-19.toml`, `ci/2026-10-19.toml`), AUTHORITY.md, this checklist and the pointers to it. *(This pull request.)*

### Step 2: The store bundle, encrypted (`store_bundle_encrypted`)
**Needs:** the owner's new environment secret. **Must land before** step 14 and November's live run.
- [x] The October run's public `store-bundle` artifact deleted. *(The owner, 2026-10-20; GitHub answers 404 for artifact 12095776025.)*
- [x] `refresh.yml`: the refresh job encrypts the bundle with `STORE_BUNDLE_KEY` (a secret of the environment `refresh`) and uploads only the encrypted file; it fails, uploading nothing, without the key.
- [x] The store job decrypts, verifies and pushes, then deletes the artifact. *(#77)*
- [x] Tests pin it: no unencrypted bundle is uploaded, and the store job deletes the artifact. *(#77)*
- [x] The owner sets the secret, and Claude checks it read-only. *(2026-10-21: `gh secret list --env refresh` lists `STORE_BUNDLE_KEY`.)*

### Step 3: The list opens on Projects (`default_rank_project`)
- [x] The parked branch's site work (673e5bb, 9e0efd3) on `main`, without its rulings commit or its rebuild.
- [x] `src/tff_site/views.py` takes the views' order and a retired Overall from `config/site.toml`, so the site needs no rebuild.
- [x] The default view is Projects in the build and the scripts; "left out of" never names Overall.
- [x] The front page's lead (`front_page_lead_counts`) and the Projects measures line without the retired 55/30/15. *(The lead in #78; the measures line in this pull request.)*
- [x] `docs/usability-test.md` chooses task 6's pair from the Projects rank, not Overall (PLAN-REVIEWERS-1.md step 4).

### Step 4: No tiers or ranges on the site (`no_tiers_or_ranges`)
- [x] The details panel shows the place only ("#12", "Band 101–250"), with no tier, range or legend. *(#84)*
- [x] The list index and details files drop tiers and ranges; the methodology page drops its tiers section.
- [x] `catalog-site.json` keeps `tier` and `range`, unshown, as v1 requires.
- [x] `docs/usability-test.md`'s task 6 no longer asks for the tier or the range.

### Step 5: Engine: Overall as a stand-in, and the catalog (`q12_overall_stand_in`, `q13_catalog_membership`)
- [x] `ranks.overall` repeats the Projects entry; the catalog is the Projects top 500 plus each rank's top 100, and license research reaches the Projects top 700. *(#86)*
- [x] A rank whose method changes starts membership afresh (`membership_fresh_at_switch`). *(#86)*

### Step 6: Engine: the measurement model's module, not yet wired in
- [x] scipy added (the lock changes by scipy only). *(#79)*
- [x] `src/tff_catalog/engine/measure.py`: the count terms and the fit, ported from the reference, with its tests. *(#79, with the review's fixes)*

### Step 7: Engine: the Projects and Developers & apps terms
- [x] The term builders, reading only the run's stage files (`q4_fot_two_terms`, `q7_age_scaled_bulk_web`, `q24_almanac_merged_parent`, `q29_jsdelivr_zero`, `q26_project_frames`). *(#86)*
- [x] The parity test against the reference, run with the private store; its messages carry no values. *(#86; it passes after the review's fixes)*

### Step 8: Engine: method versions per rank (`q20_previous_score_at_switch`, `method_versions_all_seven`)
- [x] Each run records a method version per rank; `previous_score` bootstraps for a rank whose method changed, and both validate checks follow. *(#80)*

### Step 9: Engine: the fitted parameters in the store (`q9_theta_cadence`, `october_replay_is_the_switch`)
- [x] Written once per view and date in the private store; a replay reads them; a merged date without them is fitted in memory and writes nothing; a held date's first new-method replay on GitHub writes them once. *(#86; rehearsed locally on 2026-10-21: a --write-fit replay of 2026-10-17 and its offline replay gave the same build/)*

### Step 10: Engine: the model in the pipeline, then on for Projects and Developers & apps (`q1_model_views`, `q15_views_meanwhile`)
- [x] The model's ranks, with the two-group rule meaning "above bulk" (`q14_two_groups`), hidden deterministic ranges (`q19_ranges_tiers_stand_in`), the sources' state, z and weight (`q16_weight_used`, `q18a_state_observed`, `q18b_z`), and the review flag in place of the guard (`q2_guard_retired`). *Amended 2026-10-20:* `weight_used` is a share among the font's open sources (`q16_open_source_shares`), and a font with only Fonts Over Time terms is unranked (`fot_only_needs_another_source`). *(#86)*
- [x] Two local replays of 2026-09-26 identical, and matching the parity reference. *(Before the review's fixes on 2026-09-26; after them, the rehearsal's replays of 2026-10-17. The parity test passes.)*

### Step 11: Engine: the interim desktop rules
- [x] No young-font scaling with the 60-day grace kept (`h3_young_casks_not_scaled`, `b8a_no_scaling_any_source`, `b8b_grace_kept`); the Debian frame (`debian_frame`, `de1_frame_effect`, `de3_drop_vote`); pooled Arch months (`a2_arch_pool_months`); GitHub and Nerd Fonts out of every rank (`g1_github_deferred`, `g2_nerd_deferred`), still collected. *(#83, with the Debian frame's relation ends)*

### Step 12: The methodology text and the schema descriptions
- [ ] `docs/ranking-methodology.md` and the methodology page describe both methods side by side, disclose `q25_residual_dependence` and `q23_google_own_traffic`, and drop "a font scoring 90 stands above about 90%" and "Past #100 we show bands" (PLAN-REVIEWERS-1.md step 3). Step 0's check, a fresh reader, and the owner's review. *(The text, the leak check and a fresh reader are in this pull request; the owner's review is still to come.)*
- [x] Description-only changes to the catalog schemas. *(This pull request; #86 also lets `sources[].weight` be null)*

### Step 13: The shown score A′ in every view (`score_display_linear_all_views`)
**Merges last, right before step 14:** no staging deploy shows A′ on old-method scores.
- [x] max(1, ⌈100·clip(score/3, 0, 1)⌉), computed exactly at its steps. *(This pull request)*

### Step 14: The switch: October replayed on the new method
- [x] A local dry run of 2026-10-17 that writes nothing: new members and any gate questions listed; store top-ups (`october_topups_preauthorized`), diffed before the store commit; questions ruled in a pull request. *(Gate questions ruled in #81. Dry runs pass; the top-ups wait in a store clone, unpushed, for the review fixes, which can move the Projects top-500 cut.)* *(Gate questions ruled in #81 and #85. The top-ups were pushed to the store after a row-by-row diff: the refetched snapshots were unioned with the committed rows, so nothing was removed (33dd502).)*
- [x] The GitHub replay (`from_snapshots=2026-10-17`, `write_fit=true`: the workflow writes a fit only when a dispatch asks): the parameters written once, the in-job replay identical, the bundle encrypted, the log clean. *(2026-10-21, run 39592471047 from main at 9abb12d: each view's fit written once to the store (f0e5258), bit-identical to the local rehearsal's; the in-job replay identical; the bundle encrypted and its artifact deleted; the log scanned for this run's private values, with none found.)*
- [x] Its pull request replaces #70 as the held draft, with its CI noted. *(#88, a draft. Its held CI passes except the two specimen-pin tests, as expected while step 7d stays frozen for main; noted on #88.)*

### Step 15: Staging, and the owner's look
- [x] `preview/2026-10-17` rebuilt from the new pull request, with the specimen pins (`preview_stopgap_pins`), CI passing, deployed; the browser checks done. *(5f04524: #88's scores, with the 109 specimens the GitHub replay couldn't draw drawn locally from the same font files (Merienda's stays out), and 118 stopgap pins. CI passed; deployed to staging, where the live test and the browser checks pass; production unchanged.)*
- [ ] A summary for the owner (ranks only for Google and Fonts Over Time), then the October score review that `refresh_gate_review_now` deferred. *(The summary went to the owner on 2026-10-21; the review is still to come.)*

### Later: the drift flag (`q9_theta_cadence`)
Not needed for this checklist. It needs months on the held fit, so it comes after the switch.
- [ ] Each run also fits its model views afresh and keeps that fit in the private store, beside the held one, so a replay can repeat the flag.
- [ ] Calibrate the flag's alarm levels against a null that allows for the model's misfit (a sandwich covariance, or a bootstrap over fonts). The first levels would fire on the misfit alone.
- [ ] Then turn it on as a review flag: the order under the fresh fit against the order under the held one. An early refit stays an owner ruling.

### Later: from the port's adversarial review (2026-10-20)
None of these changes October's scores. Each is fixed before it can.
- [ ] Bundle packages in the model's terms: a bundle package counts as half a package with half its count, in every source (`de4_bundle_credit_half`). The model reads only mapped rows, which carry no bundle rows. Today no bundle row reaches a Projects frame, so nothing is affected.
- [ ] An ecosyste.ms package that ecosyste.ms doesn't know reads as 0 dependents with a full bulk. Settle whether that is evidence or no coverage (`q3_coverage_mar`), before a known font hits it. In October it touched 11 fonts, none in the catalog.
- [ ] The refresh's publish job should wait for the store push when the store bundle carries a model fit, so a pull request never names a fit the store lacks.
- [ ] A replay from snapshots draws a specimen only from the font cache, since it never downloads: the switch run lacked 109. Fill the cache before a replay (`tff-site fetch-fonts`), so a held run's pull request has every specimen.
