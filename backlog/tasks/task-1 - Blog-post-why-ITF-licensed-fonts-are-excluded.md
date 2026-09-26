---
id: TASK-1
title: 'Blog post: why ITF-licensed fonts are excluded'
status: To Do
assignee: []
created_date: '2026-09-26 05:32'
labels:
  - blog
dependencies: []
references:
  - AUTHORITY.md
  - docs/milestone-2.md
type: docs
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Fontshare is a well-known source of free fonts, but 64 of its 100 fonts, including popular families such as Satoshi, General Sans and Cabinet Grotesk, are under the ITF Free Font License v2.0 (17 Aug 2026) and are left off the list under Rule 4 in AUTHORITY.md. Visitors who expect to find them deserve a full explanation. The /about page (Milestone 2 step 7) gives the rule in one line; this post explains the reasoning and gives a link to point to when someone asks about a missing font. The post can be drafted at any time, and it goes live once the blog exists (M2-D12, Milestone 2 step 7b).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The post is one Markdown file in `site/content/blog/` with `title`, `date` and `description` front matter, and it keeps `draft: true` until the owner approves the text
- [ ] #2 It explains what the license forbids (modifying the font, including subsetting and format conversion; offering it to third parties through a website, app, SaaS, design tool or template editor) and why that fails Rule 1, which requires no use restrictions
- [ ] #3 It says that Fontshare fonts under the SIL OFL are still listed, and it rechecks the ITF-licensed count against Fontshare on the day it is published
- [ ] #4 It quotes the license only briefly and links to the full text on the licensor's own site
- [ ] #5 It links to the Rules on /about, and /about links back to it
<!-- AC:END -->
