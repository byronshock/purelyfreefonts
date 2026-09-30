# Roadmap

Status as of 2026-09-30. Settled decisions are in [AUTHORITY.md](../AUTHORITY.md); each checklist has the detail.

## Milestone 1: catalog script and catalog.json
- **Goal:** one script builds a ranked, license-checked catalog of about 500 truly free Latin font families. (Listing every other qualifying family A–Z, step 15b, moved after launch on 2026-09-30: see "More truly free fonts" below.)
- **Status:** handed off to Milestone 2 on 2026-09-30 (step 20). The 2026-09-26 catalog of 500 fonts is built, reviewed by the owner and committed, and `catalog-site.json` v1 is frozen ([handoff note](milestone-1-handoff.md); monthly tasks in [ops/MONTHLY.md](../ops/MONTHLY.md)). Left: the owner's acceptance of the handoff, and three smaller items (steps 3, 5 and 13).
- **Checklist:** [milestone-1.md](milestone-1.md)
- **Depends on:** nothing.

## Milestone 2: the filterable list goes live (intermediate release)
- **Goal:** the ranked, filterable list replaces the stub, with methodology, privacy and about pages and a tip link; usability testing starts. No owned-font comparison.
- **Status:** in progress: the current step since Milestone 1's handoff on 2026-09-30. Step 0 decisions were answered and the checklist merged on 2026-09-25 ([pull request #2](https://github.com/byronshock/trulyfreefonts/pull/2)). Every item of steps 1–5, 7, 7b, 8 and 10 is ticked, steps 6, 9, 11 and 12 have one or two left, and the test site is up. Next: the rest of the deploy path (step 11), usability round 1 (step 13) and the soft launch (step 14).
- **Checklist:** [milestone-2.md](milestone-2.md)
- **Depends on:** Milestone 1. Its step 20 froze `catalog-site.json` v1 on 2026-09-30, after this milestone's step 2 approved the fields.

## Monthly refresh goes live (after Milestone 2)
- **Goal:** the monthly refresh runs live for the first time and its schedule goes on, so the catalog updates itself each month and the update reaches the live site.
- **Status:** moved out of Milestones 1 and 2 on 2026-09-30 (`refresh_timing`). Done so far: the data store's key and secret, and a replay run on GitHub whose pull request (#31) filled `state/` with the 2026-09-26 run. Until the first live refresh, the site shows that catalog.
- **Checklist:** [milestone-refresh.md](milestone-refresh.md)
- **Depends on:** Milestone 2's live site and deploy path. Comes before Milestone 3.

## More truly free fonts (after launch)
- **Goal:** every other font that passes the gates is listed A–Z without a rank, after the ranked fonts, and fonts can be added on request (owner rulings of 2026-09-29).
- **Status:** moved out of Milestone 1 on 2026-09-30 (`more_fonts_timing`): not needed to go live. It comes after Milestone 2's launch, before or after Milestone 3; the owner chooses when Milestone 2 is done.
- **Checklist:** [milestone-more-fonts.md](milestone-more-fonts.md)
- **Depends on:** Milestone 2's live site.

## Milestone 3: owned-font tools
- **Goal:** visitors paste a one-line command's output (Linux, macOS, Windows) or, in desktop Chromium, press "Check my fonts". Matching stays in the browser. Built and tested on a hidden page beside the live list.
- **Status:** draft checklist awaiting its Step 0.
- **Checklist:** [milestone-3.md](milestone-3.md)
- **Depends on:** Milestone 1's alias table and name export; Milestone 2's live site, deploy path, headers and usability process.

## Milestone 4: full release, then monthly refreshes
- **Goal:** switch the comparison on for everyone, announce the site, and run the monthly refresh. Two months after launch, a review decides between new features and maintenance only.
- **Status:** draft checklist awaiting its Step 0.
- **Checklist:** [milestone-4.md](milestone-4.md)
- **Depends on:** Milestone 3 done; the tip link live; the monthly refresh live ([milestone-refresh.md](milestone-refresh.md)).
