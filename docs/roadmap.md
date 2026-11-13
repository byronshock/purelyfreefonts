# Roadmap

Status as of 2026-11-10. Settled decisions are in [AUTHORITY.md](../AUTHORITY.md); each checklist has the detail.

## Milestone 1: catalog script and catalog.json
- **Goal:** one script builds a ranked, license-checked catalog of about 500 purely free Latin font families. (Listing every other qualifying family A–Z, step 15b, moved after launch on 2026-09-30: see "More purely free fonts" below.)
- **Status:** handed off to Milestone 2 on 2026-09-30 (step 20). The 2026-09-26 catalog of 500 fonts is built, reviewed by the owner and committed, and `catalog-site.json` v1 is frozen ([handoff note](milestone-1-handoff.md); monthly tasks in [ops/MONTHLY.md](../ops/MONTHLY.md)). Left: the owner's acceptance of the handoff, and three smaller items (steps 3, 5 and 13).
- **Checklist:** [milestone-1.md](milestone-1.md)
- **Depends on:** nothing.

## Milestone 2: the filterable list goes live (intermediate release)
- **Goal:** the ranked, filterable list replaces the stub, with methodology, privacy and about pages and a tip link; usability testing starts. Its soft launch also carries Milestone 3's first slice, the hopper (`launch_with_hopper`, 2026-11-06); the rest of the owned-font tools come with Milestone 3.
- **Status:** in progress: the current step since Milestone 1's handoff on 2026-09-30. Step 0 decisions were answered and the checklist merged on 2026-09-25 ([pull request #2](https://github.com/byronshock/trulyfreefonts/pull/2)). The test site is up, and the soft launch is planned ([PLAN-launch.md](../PLAN-launch.md); AUTHORITY.md **Soft launch**, 2026-11-06). Left before it:
  - step 6's manual passes;
  - step 11's Actions deploys (stage B and the environments);
  - step 12b's launch part (PLAN-REVIEWERS-1's priority 1 and step 5's first part);
  - the owner's items in step 13b;
  - step 14 itself.

  The soft launch carries October's run and the hopper. Usability round 1 (step 13) follows it.
- **Checklist:** [milestone-2.md](milestone-2.md)
- **Depends on:** Milestone 1. Its step 20 froze `catalog-site.json` v1 on 2026-09-30, after this milestone's step 2 approved the fields.

## Monthly refresh goes live (after Milestone 2)
- **Goal:** the monthly refresh runs live for the first time and its schedule goes on, so the catalog updates itself each month and the update reaches the live site.
- **Status:** moved out of Milestones 1 and 2 on 2026-09-30 (`refresh_timing`). Done so far: the data store's key and secret, and a replay run on GitHub whose pull request (#31) filled `state/` with the 2026-09-26 run. Until the first live refresh, the site shows that catalog. *2026-11-06 (`launch_data_october`):* the first live refresh is the launch run, 2026-10-17 re-run from `main` and merged before the soft launch. The schedule goes on after the launch.
- **Checklist:** [milestone-refresh.md](milestone-refresh.md)
- **Depends on:**
  - the launch run: the catalog-affecting changes the launch needs, and the hopper (#100);
  - the schedule: Milestone 2's live site and deploy path.

## More purely free fonts (after launch)
- **Goal:** every other font that passes the gates is listed A–Z without a rank, after the ranked fonts, and fonts can be added on request (owner rulings of 2026-09-29).
- **Status:** moved out of Milestone 1 on 2026-09-30 (`more_fonts_timing`): not needed to go live. It comes after Milestone 2's launch, before or after Milestone 3; the owner chooses when Milestone 2 is done.
- **Checklist:** [milestone-more-fonts.md](milestone-more-fonts.md)
- **Depends on:** Milestone 2's live site.

## Milestone 3: owned-font tools
- **Goal:** visitors paste a one-line command's output (Linux, macOS, Windows) or, in desktop Chromium, press "Check my fonts". Matching stays in the browser. The first slice, the hopper (drop or paste a list), is built straight onto the main list (2026-10-21), and goes live with the soft launch (`launch_with_hopper`, 2026-11-06).
- **Status:** slice 1's PRs 0–3b are merged. PR 4 (#100) and PR 5, the words, land before the soft launch. The rest comes before Milestone 4's announcement:
  - the macOS and Windows test runs;
  - the consented corpus and M3-D11's targets;
  - WebKit in CI;
  - near-match badges and "Wrong match?" links;
  - the Chromium button.
- **Checklist:** [milestone-3.md](milestone-3.md)
- **Depends on:** Milestone 1's alias table and name export; Milestone 2's live site, deploy path, headers and usability process.

## Milestone 4: full release, then monthly refreshes
- **Goal:** announce the site, once Milestone 3's tools are complete (the hopper is already live from the soft launch), and run the monthly refresh. Two months after launch, a review decides between new features and maintenance only.
- **Status:** draft checklist awaiting its Step 0.
- **Checklist:** [milestone-4.md](milestone-4.md)
- **Depends on:** Milestone 3 done; the tip link live; the monthly refresh live ([milestone-refresh.md](milestone-refresh.md)).
