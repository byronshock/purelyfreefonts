# Roadmap

Status as of 2026-09-25. Settled decisions are in [AUTHORITY.md](../AUTHORITY.md); each checklist has the detail.

## Milestone 1: catalog script and catalog.json
- **Goal:** one script builds a ranked, license-checked catalog of about 500 truly free Latin font families. (Listing every other qualifying family A–Z, step 15b, moved after launch on 2026-09-30: see "More truly free fonts" below.)
- **Status:** Step 0 done: the methodology was approved and merged on 2026-09-25 ([pull request #1](https://github.com/byronshock/trulyfreefonts/pull/1)), with every decision recorded in AUTHORITY.md. Next: Step 1, project setup.
- **Checklist:** [milestone-1.md](milestone-1.md)
- **Depends on:** nothing.

## Milestone 2: the filterable list goes live (intermediate release)
- **Goal:** the ranked, filterable list replaces the stub, with methodology, privacy and about pages and a tip link; usability testing starts. No owned-font comparison.
- **Status:** Step 0 decisions answered and the checklist merged on 2026-09-25 ([pull request #2](https://github.com/byronshock/trulyfreefonts/pull/2)). Steps 1–12 can start on a sample catalog while Milestone 1 builds. Done early: the server log policy (M2-D9), Cloudflare's error logging off, and the live tip link ([ops/DONATIONS.md](../ops/DONATIONS.md) steps 1–10).
- **Checklist:** [milestone-2.md](milestone-2.md)
- **Depends on:** Milestone 1. Its step 20 freezes `catalog-site.json` v1 after this milestone's step 2 approves the fields; the soft launch waits for that freeze.

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
- **Depends on:** Milestone 3 done; the tip link live; Milestone 1's monthly workflow.
