---
id: TASK-2
title: Investigate a Nerd Fonts download link beside the official one
status: Done
assignee:
  - '@qwen'
created_date: '2026-09-28 10:03'
updated_date: '2026-09-29 16:24'
labels:
  - links
dependencies: []
references:
  - docs/milestone-1.md
  - schemas/catalog-site.schema.json
  - docs/ranking-methodology.md
  - 'https://github.com/ryanoasis/nerd-fonts'
  - PLAN-NERD-FONTS.md
type: spike
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Many developers install the Nerd Fonts build of a coding font instead of the original. About 59% of Homebrew font installs are Nerd builds (ranking-methodology.md, known biases), and D7 counts those installs toward the original family. Today each font gets only an official link and an optional designer link (`links.primary` and `links.designer` in `schemas/catalog-site.schema.json`), and M1 step 14 never allows an aggregator as the primary link. On 2026-09-28 the owner asked whether fonts that have a Nerd build could also offer a Nerd Fonts download link. Timing matters: `catalog-site.json` v1 is frozen at M1 step 20, so a new link field is cheapest to add before then.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 It lists which catalog families have a Nerd Fonts build, mapped through Nerd Fonts' `fonts.json` and the alias table, including renamed builds such as SauceCodePro (Source Code Pro)
- [x] #2 It checks that each Nerd build still passes Rules 1-4, counting the licenses of the icon sets patched into it as well as the original font's license
- [x] #3 It proposes a link target that follows M1 step 14's rules (a page, never a release asset or `/releases/latest`), and the link text that names its destination
- [x] #4 It proposes where the link appears (row, details panel or both), its wording, and whether it shows on every view or only on Coding and Developers & apps
- [x] #5 It states the schema change needed in `catalog-site.json`, and whether it must land before the M1 step 20 v1 freeze
- [x] #6 The findings and a recommendation go to the owner for a ruling; the ruling is recorded in AUTHORITY.md, and any build work goes into the milestone checklists or new tasks
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Map all Nerd Fonts builds (fonts.json) to catalog families via the alias table, incl. renamed builds (done: 72 builds enumerated)
2. Check Rules 1-4 for each build: base license from fonts.json + all 14 patched icon-set licenses (done table; verify Font Logos 'unlicensed' upstream, verify patch adds no restrictions)
3. Verify candidate link targets (nerdfonts.com/font-downloads, GitHub releases page) return 200 and satisfy step 14 (page, not asset/latest)
4. Draft placement + wording proposal (details panel vs row; views)
5. Draft catalog-site.schema.json change (links.nerd) and timing vs M1 step 20 freeze; check catalog.schema.json too
6. Write findings doc (docs/, prior-art.md style), record in task notes, present recommendation to owner for ruling
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Investigation complete; findings in docs/nerd-fonts-link.md (2026-09-28). Summary: (1) 72 builds in fonts.json; catalog intersection comes free via aliases.csv relation=build detail=nerd once seeded; full rename table in the appendix. (2) All base licenses and all 14 icon sets pass Rules 1-4; Font Logos 'unlicensed' = The Unlicense (GitHub API); only BigBlueTerm (CC-BY-SA) fails D3 and its original would never enter the catalog. (3) Proposed target https://github.com/ryanoasis/nerd-fonts/releases (repository releases page = the class step 14 allows; 200 on 2026-09-28; nerdfonts.com has no per-font deep links). (4) Placement: details panel only, every view, label 'Nerd Fonts build'. (5) Schema: nullable links.nerd in both schemas + Links dataclass; step 14/15 code is still stubs, so it is cheapest now and must land before the M1 step 20 freeze. (6) Recommendation to owner: adopt. Awaiting ruling for AUTHORITY.md.

Owner ruling received in chat 2026-09-28: adopt, with the link on the list row as well as the details panel. Recorded in AUTHORITY.md (Site (Milestone 2)) and data/reviews/site/2026-09-28.toml; build work added to milestone-1.md steps 14/15 and milestone-2.md steps 2/3/4. Verification: full pytest suite 601 passed, 1 skipped, 4 xfail (expected stubs); the new reviews TOML validates against schemas/review.schema.json; all candidate link targets returned HTTP 200 on 2026-09-28.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Spike delivered in docs/nerd-fonts-link.md: all 72 Nerd Fonts builds mapped to their originals with licenses (renames included); every base license and all 14 patched icon sets pass Rules 1-4 - Font Logos' 'unlicensed' is The Unlicense, and the only CC-BY-SA build (BigBlueTerm) has an original that D3 excludes anyway. Proposed target https://github.com/ryanoasis/nerd-fonts/releases (a repository releases page, the class step 14 allows), text 'Nerd Fonts build'. Owner ruled 2026-09-28: adopt; nullable links.nerd in both schemas before the M1 step 20 freeze; shown on the row and in the details panel, every view. Ruling recorded in AUTHORITY.md and data/reviews/site/2026-09-28.toml; checklist items added to M1 steps 14/15 and M2 steps 2/3/4. Verified: pytest 601 passed; TOML schema-valid; link targets 200.
<!-- SECTION:FINAL_SUMMARY:END -->
