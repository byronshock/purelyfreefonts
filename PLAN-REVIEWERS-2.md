# Plan: changes from the second round of reviews

This is the working checklist for the second round of reviews of the test site, `https://staging.purelyfreefonts.com`. The round started on 2026-10-06 on main `7738636`, after [PLAN-REVIEWERS-1.md](PLAN-REVIEWERS-1.md)'s steps 7a–7c landed. The owner chose to start testing then, ahead of the rest of that plan's step 11. Settled decisions are in [AUTHORITY.md](AUTHORITY.md) (**First reviews of the test site**, and the rulings of 2026-10-06 in `data/reviews/site/2026-10-06.toml`). The build work joins [docs/milestone-2.md](docs/milestone-2.md) before usability round 1 (step 13).

The reviewers so far:

- **O:** the owner, starting with the mobile site.
- **F:** a few of the owner's friends, informally (findings to come).
- **C:** Claude, inside the team, measuring and prototyping.

The round's critique from a fresh outside reviewer (PLAN-REVIEWERS-1.md step 11) comes later.

**Status (2026-10-06):** one finding, ruled on. Step 1 is this file and its rulings, and step 2 is next.

**How to read each step:** **Who**, **Depends on** and **Done when**, as in PLAN-REVIEWERS-1.md. Tick each item as soon as it is done and verified. Checklists nest: a parent is ticked only when every item under it is.

---

## Findings

### F1. Too much text before the fonts on a phone (O)

The owner, on the mobile site: "My first impression of the mobile site: There is entirely too much text on the first page and not enough of what people came for, the fonts. Please add this to the plan now that staging is live for testers."

**Measured on staging, main `7738636`, Chromium mobile emulation, after load:**

| Viewport | First font row starts at | Fonts fully on the first screen |
|---|---|---|
| 375 × 812 | 867 px | 0 (about 250 words of text above it) |
| 390 × 844 | 867 px | 0 |
| 360 × 740 | 888 px | 0 |
| 320 × 568 | 933 px | 0 |

At 375 px, the top of the page from top to bottom, in CSS px:

| Part | Height |
|---|---|
| Header: wordmark, nav, the "Early version…" line | 125 |
| H1 | 77 |
| Lead, 4 lines | 96 |
| Privacy note | 42 |
| "Why isn't my favorite free font here?", folded | 39 |
| Search, "Rank" with its select and 2-line description, the Filters button | 246 |
| "Fonts" heading and "Showing 500 of 500 fonts" | 54 |
| Sort buttons | 48 |

Each font row is 161 px tall.

**Prototype** on the live page (Claude; styles set on the elements, so only an estimate):

| Approach | First font row starts at | Fonts fully visible |
|---|---|---|
| Move and drop: the note and the privacy line below the list, no Rank description, "Fonts" and the count in the sort row | 667 px | 0 |
| Also compact the intro: a shorter early-version line, a smaller H1, a 2-line lead, search, Rank and Filters on one row | 534 px with a rough 148 px control row; about 440 px with a designed one-row bar | 1; about 2 with the designed bar |
| Also hide the H1 and the lead on phones | 385 px, about 290 px with the designed bar | 2; about 3 with the designed bar |

**Ruled on 2026-10-06 and 2026-10-07:**
- **Two fonts** (`mobile_first_screen_two_fonts`): two fonts fully on a 375 × 812 first screen, with the compact intro. The heading and the lead stay on the page.
- **Now** (`mobile_first_screen_now`): build it now, ahead of the parked Project-default branch.
- **This file** (`reviewers_round_2_plan`): round 2 goes in its own plan.
- **No early-version line on phones** (`early_line_hidden_on_phones`): the owner's choice, over a shorter line.
- **The front page stops after the lead** (2026-10-07). The owner, to the lead session: "<- WE COULD STOP HERE".
  - The note "Why isn't my favorite free font here?" moves to the About page (`why_not_listed_to_about`).
  - The privacy line goes into the footer on every page (`privacy_line_in_footer`).
- **"Popularity" now** (`step4_wording_now`): PLAN-REVIEWERS-1.md step 4's wording is built with this work.

**Pending:** the friends' findings, and the second round's outside critique.

---

### Step 1: This plan and its rulings
**Who:** Claude records; the owner merges. **Depends on:** nothing.
- [x] The rulings `mobile_first_screen_two_fonts`, `mobile_first_screen_now` and `reviewers_round_2_plan` are in `data/reviews/site/2026-10-06.toml` (`tff-catalog rulings apply`) and in AUTHORITY.md.
- [x] PLAN-REVIEWERS-1.md links here: its step 5 moves the note under this plan's step 2, and its step 11 says round 2 began on 2026-10-06.
- [x] Add `PLAN-REVIEWERS-2.md` to Tracked docs in AUTHORITY.md.

**Done when:** merged to `main`.

### Step 2: The mobile first screen: two fonts at 375 × 812
**Who:** Claude builds; the owner looks on staging. **Depends on:** step 1.
- **Branch:** from `origin/main` after the lead's #63, the mixed-case wordmark, which makes the header about 8 px taller on phones and 13 px on desktop. Measure with its header.
- **Conflicts:** expected with the parked `claude/projects-default` in the list templates; the lead rebases it (`mobile_first_screen_now`).

- [ ] **Off the front page, which stops after the lead:**
  - [ ] the note "Why isn't my favorite free font here?" moves to the About page as it stands, at a stable anchor such as `/about/#why-not-listed` (`why_not_listed_to_about`). Its rewrite as a conversation ending with the tip pitch, and the empty-search message linking to it, stay in PLAN-REVIEWERS-1.md step 5.
  - [ ] the privacy line, "No cookies, no tracking…", goes into the footer on every page, linking to the Privacy page (`privacy_line_in_footer`). Keep the Network-tab explanation on the Privacy page.
- [ ] **On phones only** (below the 40rem breakpoint; wide screens unchanged):
  - [ ] the Rank selector's description (`#f-rank-measures`) is left out visually. It stays the select's accessible description.
  - [ ] the "Fonts" heading stays in the page for screen readers and headings navigation. The count, "Showing 500 of 500 fonts", sits in the sort row.
  - [ ] a smaller H1, and a shorter lead. Either show the lead's first sentence, or clamp it to 2 lines with the rest still in the page. Pick by what reads better, and say which in the pull request.
  - [ ] no early-version line in the header (`early_line_hidden_on_phones`). Wider screens keep it. #55's one-row header rule (`wordmark_breakpoint`) holds: check the header's height at 320 and 375 px, with #63's mixed-case wordmark.
  - [ ] search, Rank and Filters on one compact row:
    - visible labels become accessible names;
    - targets stay at least 24 px;
    - at 320 px the row may wrap to two lines, but never off-screen.
- [ ] Records: `site/CONTRACT.md` (the list page's order on phones, the controls row, the count's place) and the tests that pin the order, the controls, the count and the heading. M2-D4 (filters behind one button on phones) holds.
- [ ] Measure with Playwright on the real catalog:
  - the first screen at 375 × 812, 390 × 844, 360 × 740 and 320 × 568;
  - layout shift (0) on load and on refilter;
  - axe, reflow at 320, and keyboard order;
  - M2 step 10's load-speed and redraw timings;
  - desktop and tablet unchanged.
- [ ] **The first plan's step 4 wording, built with this** (`step4_wording_now`):
  - "Popularity" over the scores (`score_column_popularity`);
  - "Measure" on the selector (`selector_label`);
  - "most popular first" and "least popular first" (`sort_words_popular`);
  - the methodology page titled "How we measure popularity", with the nav link "How it works" (`method_page_name`).

  Also `docs/usability-test.md`'s wording. Re-measure the header's single-row breakpoint for the wider nav, and move the `47.5rem` rule and its test to match (PLAN-REVIEWERS-1.md step 5's note on `wordmark_breakpoint`). Tick PLAN-REVIEWERS-1.md step 4 when it lands.
- [ ] Ask the lead to redeploy staging.

**Done when:**
- at 375 × 812 and 390 × 844, the first two font rows are fully visible on load, with no scrolling;
- at 360 × 740, at least the first row is fully visible;
- at 320 × 568, the first row starts on the first screen;
- desktop and tablet are unchanged;
- CI passes;
- the owner has looked on staging.

### Step 3: The friends' findings
**Who:** the owner passes them on; Claude writes them up. **Depends on:** the friends' input.
- [ ] Each finding recorded here (F2, F3, …) in the owner's words, with what was measured and the owner's rulings, then built as steps of their own.
