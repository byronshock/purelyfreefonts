---
id: TASK-2
title: Investigate a Nerd Fonts download link beside the official one
status: To Do
assignee: []
created_date: '2026-09-28 10:03'
labels:
  - links
dependencies: []
references:
  - docs/milestone-1.md
  - schemas/catalog-site.schema.json
  - docs/ranking-methodology.md
  - 'https://github.com/ryanoasis/nerd-fonts'
type: spike
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Many developers install the Nerd Fonts build of a coding font instead of the original. About 59% of Homebrew font installs are Nerd builds (ranking-methodology.md, known biases), and D7 counts those installs toward the original family. Today each font gets only an official link and an optional designer link (`links.primary` and `links.designer` in `schemas/catalog-site.schema.json`), and M1 step 14 never allows an aggregator as the primary link. On 2026-09-28 the owner asked whether fonts that have a Nerd build could also offer a Nerd Fonts download link. Timing matters: `catalog-site.json` v1 is frozen at M1 step 20, so a new link field is cheapest to add before then.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 It lists which catalog families have a Nerd Fonts build, mapped through Nerd Fonts' `fonts.json` and the alias table, including renamed builds such as SauceCodePro (Source Code Pro)
- [ ] #2 It checks that each Nerd build still passes Rules 1-4, counting the licenses of the icon sets patched into it as well as the original font's license
- [ ] #3 It proposes a link target that follows M1 step 14's rules (a page, never a release asset or `/releases/latest`), and the link text that names its destination
- [ ] #4 It proposes where the link appears (row, details panel or both), its wording, and whether it shows on every view or only on Coding and Developers & apps
- [ ] #5 It states the schema change needed in `catalog-site.json`, and whether it must land before the M1 step 20 v1 freeze
- [ ] #6 The findings and a recommendation go to the owner for a ruling; the ruling is recorded in AUTHORITY.md, and any build work goes into the milestone checklists or new tasks
<!-- AC:END -->
