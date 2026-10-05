# Plan: changes from the first reviews of the test site

This is the working checklist for the changes that came out of the first reviews of the test site on 2026-09-30: release `f6e84b3`, catalog run 2026-09-26, method 2026-09-29. The site was then Truly Free Fonts, at `https://staging.trulyfreefonts.com`. Since 2026-10-02 it is **Purely Free Fonts**, and the test site is `https://staging.purelyfreefonts.com` (**Name and domain** in AUTHORITY.md). The findings were made on the old name and still apply. Settled decisions are in [AUTHORITY.md](AUTHORITY.md). The build work joins [docs/milestone-2.md](docs/milestone-2.md) before usability round 1 (step 13), and the inventory items join [docs/milestone-3.md](docs/milestone-3.md).

Three reviews fed it. Each finding is marked with who raised it:

- **O:** the owner's first impressions, in chat.
- **C:** Claude's critique. It comes from inside the team, so it knows the rulings and may lean toward defending them.
- **R:** an outside reviewer, a Claude chat given no project context, over four rounds plus a closing list:
  1. does the site do what it says;
  2. design and usability;
  3. planned feature 1, more fonts, and the page budget;
  4. planned feature 2, the font inventory;
  5. its three priorities before launch (below).

Every factual claim below was checked against the live site, the catalog or the repo before it was written here.

**Status (2026-10-05):** step 1 is recorded in this pull request (#56). The owner's rulings of 2026-09-30 and answers of 2026-10-05 are in `data/reviews/site/` and AUTHORITY.md (**First reviews of the test site**). Nothing is built.

**Holds.** On 2026-10-02 the owner put the scoring-method port and the soft launch on hold: "We are on hold for now." Production serves a renamed stub. Step 1 goes ahead. Build steps that need the new scores or the new default view wait for the port: step 3, Q3's lead sentence, and the row order in Q12 and step 9b.

**Decided since the reviews:**
- **The default view** (2026-09-30). The fused Overall rank retires (`overall_retired`, D12), and the list opens on the Project rank (`default_rank_project`, M2-D1). Those records still carry the old label, "Used in projects".
  - The owner then named it **"Projects: most used"** in this plan's session. That label is recorded here, in step 1a, not elsewhere.
  - The site switch exists only on the lead's parked branch `claude/projects-default`. It isn't on `main`.
  - Wherever this plan says "the default view", it means "Projects: most used".
- **The shown score** (2026-10-01). It is linear between fixed ends on the new method's latent scale: max(1, ⌈100·clip(η̂/3, 0, 1)⌉). This replaces 100·Φ(z) (`q11_display_linear`, `q30_display_ends`; **The scoring method** in AUTHORITY.md). It settles Q1, and it lands with the port.
- **No tiers or likely ranges** (2026-09-30, `no_tiers_or_ranges`). Every rank shows just its place, and How we rank loses its tier section. This lands after the freeze.
- **The rename** (2026-10-02):
  - Public copy says "purely free" where it said "truly free".
  - The contact address is admin@purelyfreefonts.com.
  - Ko-fi is closed, so tips go through the Stripe link only (`kofi_closed`).
- **The owner's wordmark** (2026-10-04, #55): "PURELY FREE FONTS" on its white plate, which agrees with step 1a's black-on-white header. The About page credits Arimo only (`about_font_credit`).

**Who does what** (2026-10-05):
- **This plan's session** asks the owner's questions once, as batched single-select questions, records the answers and builds steps 1–10. Each step gets its own worktree and `claude/<name>` branch.
- **The owner** merges each pull request with "Create a merge commit".
- **The lead session** redeploys the test site (step 11), and keeps this plan in step with the methodology work.

## Priorities

The owner: "I agree with the reviewer's priorities." In the reviewer's words, the three changes that matter most before launch are:

1. **Match the homepage claims to the evidence.**
   - "Public install and usage counts" instead of "how many people install them".
   - "No use restrictions" instead of "no limits".
   - Name the systems behind the "Comes with Linux" tag.
   - Check the Heavy Data and Charter licenses.

   In this plan: Q3, Q4 and step 8.
2. **Put the fonts first.** Show fonts sooner on the page, and make the previews bigger, with text visitors can switch. In this plan: step 5's first part, Q6 and step 7. The owner refined it on 2026-10-05: rows show the font's name larger, and the sample lines move into the details. The larger names ship first, in step 7a, by trimming the committed specimens in the site build, with no redraw and no catalog change (`specimen_trim_served`).
3. **Free up page weight before adding anything.** Stop shipping the list twice, and load the A–Z list only when needed, so there's room for both new features.
   - In this plan: Q10, Q12 and step 9. The owner kept every row in the HTML (Q12), so Brotli and the names-only specimens carry this priority.
   - The A–Z list loading on demand is already in [docs/milestone-more-fonts.md](docs/milestone-more-fonts.md).
   - "Before adding anything" means before the A–Z list (the more-fonts milestone) and before the inventory check joins the list page (Milestone 3's M3-D8, built in Milestone 4).

**How to read each step:**
- **Who:** the owner, Claude, or both.
- **Depends on:** what must be settled first. Q1–Q12 are the owner's questions in step 1.
- **Done when:** what must be true before the step is ticked.

Tick each item as soon as it is done and verified. Checklists nest: a parent is ticked only when every item under it is. If an item is only partly done, leave it unticked and note what's left.

**Order.** Each step is one worktree and pull request. Steps may be built side by side but land in this order:
1. Step 1.
2. **Priority 2, the list** (`step7_list_first`, 2026-10-05): step 7a, then 7b and 7c. **With them, priority 1's parts that touch no template:** step 8a, the Heavy Data and Charter license checks.
3. **Priority 1, the rest:** step 8b.
4. **Priority 2, the rest:** step 5's first part.
5. **Priority 3:** step 9.
6. **The rest:** steps 2, 3, 4, 6 and 10, then step 5's second part, which waits for step 2. Step 7d goes in once the lead accepts a redraw, or at the latest at the first live refresh. Step 7e comes after the freeze.
7. Step 11.

Steps 4, 5, 8b and 9b change the templates, the no-JavaScript note or the front-page copy. They start from `origin/main` after the lead's parked `claude/projects-default` has merged, so the two don't conflict. That branch waits on the scoring-method freeze, and the owner chose on 2026-09-30 to keep these steps waiting with it.

Step 7's list parts don't wait for it: on 2026-10-05 the owner moved them ahead (`step7_list_first`). 7a changes no template. 7b and 7c touch `_list.html.j2` and `_row.html.j2`, which that branch also edits. Whichever lands second fixes `site/CONTRACT.md`'s `rows` line by hand, and re-measures the 24 px `--row-est-h` tolerance that branch adds to `test_details.py`. Branch step 7 from `origin/main` (`d66c60e` or later, which includes #55), not from this plan's branch.

Priorities 1 and 2, and steps 4 and 6, land before usability round 1, so testers see the changed site. Priority 3 lands before the A–Z list or the inventory check is added to the list page.

---

### Step 1: AUTHORITY.md
**Who:** the owner rules; Claude records. **Depends on:** nothing.

**1a. Rulings the owner gave in chat on 2026-09-30, to record as they are:**
- [x] **Filters stay in view.** "Obviously we should have a fixed sidebar on large screens." On wide screens the filter sidebar stays in view while the list scrolls. Built with `position: sticky`, so it never covers the footer. This extends M2-D4. Phones keep the "Filters" button. (O; C item 6)
- [x] **"Why isn't my favorite free font here?" comes after the fonts,** on wide screens and phones. It becomes a short conversation with the reader and ends with the tip link. It also appears when a search finds nothing (step 5). This replaces `why_not_listed_layout` of 2026-09-29. (O; C items 3 and 5; R design 1)
- [x] **Funding, amended.** It now reads "A donation button on the site, and nothing more: no donation strategy or fundraising work." The amendment adds one short pitch, at the end of that note: the owner's idea to "sell the reader on buying me coffee". The Stripe Payment Link stays. Q7 settled the wording on 2026-10-05: a "Tip Jar" nav link, and "leave a tip" with no amount.
- [x] **"Popularity" replaces "Rank" over the score column.** (O note 2) Q2 settles the rest of the wording.
- [x] **The default view is labelled "Projects: most used"** (2026-09-30), in place of "Used in projects" (**Project rank label**, 2026-09-25, and the label in `default_rank_project`).
  - The owner chose it in this plan's session, from four options Claude offered: "Projects: most used", "Popular in projects", "Web, code & apps" and "Project Picks". Of "Popularity (Projects)", the owner had said: "closer, still not there".
  - It matches "Desktop: most chosen" and "Desktop: most installed", so the view's name needn't repeat "Popularity".
  - It lands with the site switch on the lead's parked branch.
- [x] **The outside reviewer's three priorities set the order of this work.** ("I agree with the reviewer's priorities.") Within them:
  - **The homepage's claims change to match the evidence** (priority 1). Q3 only confirms the exact sentences.
  - **"Comes with" names the systems** (priority 1, settling Q4). For example, Open Sans's row says "Comes with CachyOS, EndeavourOS", not "Comes with Linux". This amends the "Rows" part of **Filters (2026-09-30)**.
  - **Bigger previews, with sample text visitors can switch** (priority 2, settling Q6 as (b)). Q6 only settles which sentences, and how many.
  - **The list page gets lighter before anything is added to it** (priority 3). The list's rows stop being shipped in full in the HTML (Q12 settles how). The A–Z list loads only when needed.
- [x] **The header stays black on white in dark mode.** The outside reviewer called it a bug. The owner: "black on white is the branding of typography." This confirms the header colour ruling of 2026-09-29.
- [x] **Tracked docs** gains `PLAN-REVIEWERS-1.md`.

**1b. Questions for the owner,** asked in chat as grouped single-select questions, each with Claude's recommendation first:
- [x] **Q1. The score's scale.** (O note 4; C items 1–2; R design 3) **Settled elsewhere, on 2026-10-01:** method answers Q11 (`q11_display_linear`) and Q30 (`q30_display_ends`) in `data/reviews/method/2026-10-01.toml`, and **The scoring method** in AUTHORITY.md.
  - The shown score is linear between 0 and 3 on the new method's latent scale: max(1, ⌈100·clip(η̂/3, 0, 1)⌉). So 1 is the average tracked font, and 100 is three standard deviations above it.
  - Option (a) below, each view's listed fonts spanning 1–100, conflicts with that ruling and is withdrawn.
  - The rest of this entry is the record of the question as it was asked.
  - **Then:** 100·Φ(z) (`score_curve`). The listed fonts in "Projects: most used", now the default, run from 33 to 100. The retired Overall ran from 53 to 99. How we rank says "a font scoring 90 stands above about 90% of them", which is wrong. The 500 listed fonts are the top of about 2,000 tracked families, so even the last should stand above about 75% of them, yet the retired Overall's last font showed 53, and fonts placed in "Projects: most used" go as low as 33.
  - **(a) Recommended, the owner's idea:** each view's listed fonts span 1–100. The least popular listed font gets 1, the most popular gets 100, and fonts in between are placed linearly on the engine's score, so the gaps between fonts stay.
  - **(b)** keep 100·Φ(z) and rewrite the sentence.
  - **(c)** the font's percentile by position. This loses the gaps.
  - **Either way:** it replaces `score_curve`, `catalog-site.json` v1 is untouched, and TASK-4's triangles compare the stored score, not the number shown.
- [x] **Q2. Words.** (O notes 2–3; C item 4) The default view's name is settled ("Projects: most used", step 1a), and so is "Popularity" over the scores. What's left:
  **Answered 2026-10-05:** the selector's label is "Measure" (`selector_label`); the sort words are "most popular first" and "least popular first" (`sort_words_popular`); the page is "How we measure popularity", with the nav link "How it works" (`method_page_name`). All as recommended.
  - The select's label: keep "Rank", or "Measured by", or "Popularity by". Its options read "Projects: most used", "Desktop: most chosen", and so on.
  - The sort button's order words: today "best first" and "least used first" (`sort_header`), or "most popular first" and "least popular first".
  - The page title: "How we measure popularity", or the owner's "How we evaluate popularity".
  - The nav link: the phone nav is already full at 375 px, so a short link such as "How it works".
  - Claude recommends keeping "rank" only in a font's details, for its exact place.
- [x] **Q3. Wording that claims too much.** (R round 1 and priority 1) The change is agreed. Only the sentences need confirming.
  **Answered 2026-10-05:** "They are ranked by public counts of use in websites, code and apps." (`front_page_lead_counts`, the owner's choice over the recommended sentence), landing with the Project default; and "with no use restrictions" in the note (`note_no_use_restrictions`).
  - The lead's "They are ranked by how many people install them and use them in their work" changes.
    - **Recommended:** the lead session's proposal, which fits the new default view: "They are ranked by how widely they are used in websites, code and apps."
    - The reviewer's "They are ranked by public install and usage counts" no longer fits, because the default view counts no installs.
    - The lead session left this question to this plan (2026-10-05), so it is asked once, here. The answer is recorded now; the sentence goes live with the default view, after the port.
  - The note's "with no limits on how they're used" becomes About's "with no use restrictions". The SIL Open Font License, used by 453 of the 500 fonts, does set conditions.
  - This replaces the wording of `front_page_lead_sharing`.
- [x] **Q4. "Comes with Linux".** (R round 1) **Settled by priority 1** (step 1a): the row names the systems. Open Sans carries the tag only because CachyOS and EndeavourOS ship it.
  - **Left for step 8's pull request:** how a long list shortens (Claude proposes after two systems, with the rest in the details), and what "Hide fonts that come with: Linux" hides, which Claude proposes stays any Linux system.
- [x] **Q5. A search that finds nothing.** (C item 3; R design 4)
  **Answered 2026-10-05:** (a), as recommended (`no_match_search`).
  - **(a) Recommended:**
    - **Now:** say "No listed font matches '…'", followed by the note.
    - **After step 2:** add each unlisted font's reason, plus free fonts with the same letter widths for well-known paid ones: Arial and Helvetica → Arimo, Liberation Sans, TeX Gyre Heros; Times → Tinos, Liberation Serif, TeX Gyre Termes; Courier → Cousine, Liberation Mono.
  - **(b)** as (a), plus look-alikes chosen by eye (such as Futura → Jost), each with a stated basis.
  - **(c)** the message only.
  - **The data:** the list of excluded names is about 35 KB compressed, so it loads only on demand, outside the 100 KB.
- [x] **Q6. Specimens and the NF column.** (O note 7; C items 7 and 9b; R design 2) **Settled as (b) by priority 2** (step 1a). What that means:
  **Answered 2026-10-05, changing the design:** "People come to see the fonts, and the font names set in sample fonts are already too small. Preset sample lines should only be visible once the dropdown has been hit."
  - Rows show only the family's name, drawn larger (`specimen_name_only_list`). No sample-line control above the list.
  - A font's details show "Hamburgefonstiv 0123", a line of code for monospace fonts (`details_sample_lines`) and a glyph strip (`glyph_strip`, the owner's choice; Claude had recommended "not now"). "Dolorem ipsum quaerit nemo." retires.
  - The NF column and legend change as below (`nf_legend_visible_rows`). The record of the question follows.
  - **Phones:** the specimen gets its own line at full row width, about twice today's 40 px height.
  - **NF column:** it takes space only in rows with a Nerd Font build.
  - **NF legend:** it shows only while an NF row is visible. Today it shows even with Handwriting (41 fonts, none with an NF build), with Display (52, none) and with an empty search.
  - **Desktop:** specimens about 80 px tall in the same 340 px width.
  - **A switch between preset sample lines.**

  **Left to decide:** which sentences, and how many.
  - **Room:** the specimens total 3.97 MB of M2 step 5's 10 MB cap (median 2.4 KB compressed each), so one or two more sentences fit, but not three without raising the cap. A visitor downloads only the specimens they scroll past, in the chosen sentence.
  - **Candidates:**
    - the current "Dolorem ipsum quaerit nemo.";
    - a headline;
    - "Hamburgefonstiv 0123" (the reviewer's suggestion);
    - a line of code, for monospace fonts.
  - Claude drafts the options; the owner picks.
- [x] **Q7. The tip wording,** in the note and in the footer: keep "Leave a tip ($5 suggested)" (2026-09-26), or wording built on "coffee".
  **Answered 2026-10-05** (`tip_jar`):
  - A "Tip Jar" link ends the header nav: Fonts, How it works, About, Privacy, Tip Jar.
  - The note's pitch asks the reader to leave a tip, with no amount and no coffee wording; the Stripe page is prefilled with $5.
  - The footer's tip link is removed.
  - The phone nav then needs two lines or tighter spacing.
- [x] **Q8. How we rank's internal asides.** (C item 8) It has four "(owner ruling of …)" asides. The Dirichlet formula sits in the tier section, which goes under `no_tiers_or_ranges`, so Q8 is now about the asides only. The methodology session will probably rewrite `docs/ranking-methodology.md`, so check with it (or the lead) before building Q8, and keep the edits to the sentences the site shows.
  **Answered 2026-10-05:** (a), as recommended (`method_asides_rewrite`).
  - **(a) Recommended:** rewrite the parts of `docs/ranking-methodology.md` that the site copies, so both read as public text. The page promises that the two always agree.
  - **(b)** remove the asides when the site is built.
- [x] **Q9. Outside points not yet discussed.** Keep or change each:
  **Answered 2026-10-05:** the sample line goes with Q6; "Designer's page" (`designer_label`); Download becomes the button (`download_button`); phone rows merge lines (`phone_rows_merged`, amended the same day by `phone_rows_trim`: no category and license merge, since main already fits about five rows); name matches first in search (`search_name_first`); a glyph strip in the details (`glyph_strip`, the owner's choice over "not now").
  - The sample line reads as placeholder text (`specimen_sample_latin`). Q6's preset lines may answer this.
  - The details' "Designer" row shows a repository ("GitHub: rsms/inter"). `catalog-site.json` v1 has no designer names, so only the label can change, for example to "Designer's page".
  - "Download from …" should look like the main action.
  - Phone rows could merge lines. About three fonts fit on a phone screen today.
  - A search for "mono" includes fonts matched only through a Nerd build's name (Ubuntu comes second), so exact name matches should come first.
  - A glyph strip in the details, before the 877 KB "Type your own text".
- [x] **Q10. Brotli.** (R round 3; serves priority 3) Serve pre-compressed Brotli files.
  **Answered 2026-10-05:** (a), as recommended: Brotli, with CI measuring the Brotli size (`brotli`).
  - **Saving:** measured on the live files, the HTML, CSS, JS and list data drop from 96.1 KB to 76.7 KB (−20%), with no markup change.
  - **Work:** a build change and a change to `ops/caddy/site.caddy`.
  - **(a) Recommended:** CI's budget then measures the Brotli size, which is what visitors download, so the saving counts as headroom.
  - **(b)** CI keeps measuring gzip, which is stricter but leaves no headroom on paper.
- [x] **Q11. Milestone 3 additions.** (R round 4)
  **Answered 2026-10-05:** all three, as recommended (`inventory_additions`).
  - Present the "Hide fonts that come with" filter as the first step, needing no permission, before the check.
  - Add "Show only mine", to check the licenses of fonts you already use.
  - Add an explicit test that no font name ever enters the page address.
  - These become new Step 0 decisions in milestone-3.md.
- [x] **Q12. Stop shipping the list in full in the HTML.** (priority 3; R round 3)
  **Answered 2026-10-05:** (c), every row stays in the front page's HTML (`rows_all_in_html`, the owner's choice; Claude had recommended (a)). Step 9b is not built.
  - **What's actually doubled:** little. list.json holds filter and score data (ids, search keys, per-view scores, flags, categories), while the rows' HTML holds what is displayed. The weight is the HTML: all 500 rows, 639 KB raw, 48.9 KB compressed.
  - **Measured:** writing only the first 100 rows into the HTML takes it to 12.7 KB. Carrying rows 101–500 as data costs at most 17.1 KB (every text and link, before any trimming). That's a net saving of about 19 KB compressed on the first load, on top of Brotli.
  - **Cost:** a contract change. Today `Render` only reorders rows the server wrote, and without JavaScript the page shows the full list. The lead's `claude/projects-default` changes that list's order to the default view.
  - **(a) Recommended, the owner's refinement:** the front page's HTML holds the top 100 rows of the default view, "Projects: most used", and a static full-list page keeps every row a click away.
    - **With JavaScript:** the script builds rows 101–500 from the data on load, so these visitors never need the full page.
      - Find-in-page works once the script has run.
      - The script's time counts against M2 step 10's blocking-time budget of 200 ms.
      - The per-row `<noscript>` specimen copies drop to 100.
    - **Without JavaScript:** visitors see the 100 and a plain link, "Show all 500 fonts", to a full-list page such as `/all/`.
      - That page is static, like today's front page: every row, in the default view's order.
      - Only visitors who follow the link download it (about 49 KB compressed), so it sits outside the front page's budget.
      - The note reads something like: "Filters and search need JavaScript. These are the top 100; show all 500 fonts."
    - **The same script runs on both pages.** It builds only the rows missing from the HTML, and `/all/` has none, so a shared `/all/` link still filters and sorts as today.
    - **One full page, not pages of 100.** The static server can't vary a page by a `?page=` query, so paging would need a path per block, such as `/fonts/2/`. One page gives the whole list in one step.
    - **Search engines:** `/all/` goes in `sitemap.xml`. It's the one page that lists every font in plain HTML.
    - **Growth:** when the A–Z list arrives (the more-fonts milestone), `/all/` grows by about 1,250 rows, to roughly 120 KB compressed. If that's too much, it can then split by letter.
  - **(b)** as (a), but with 50 rows on the front page. That saves a little more, and the full-list page stays.
  - **(c)** all rows stay in the front page's HTML, and only Brotli (Q10) lightens the page.
- [x] Record each ruling and answer:
  - in an answers file for gate SITE, saved with `uv run tff-catalog rulings apply <file>` into `data/reviews/site/<date>.toml`, with `choice`, `recommended`, `value`, `ruling` and `reason`;
  - and in an AUTHORITY.md entry under Site (Milestone 2), as earlier site rulings are.
- [x] Add milestone-2.md step 12b, "Changes from the first reviews", linking this plan, and make step 13 depend on it.

**Done when:** every ruling and answer is in AUTHORITY.md and `data/reviews/`, and merged to `main` by pull request together with this file.

### Step 2: Clean up the reasons fonts aren't listed
**Who:** Claude; the owner reviews. **Depends on:** Q5 (skip under (c)).
- [ ] In `build/names.json`, the reason `latin` also covers families that failed the Latin check because no readable file was found, such as Arial and Arial Black. Give those a reason of their own, so no visitor reads "not Latin" for Arial.
- [ ] Check 30 reasons by hand, across every reason.
- [ ] Both step 5 and Milestone 3 step 8 (the line counting a visitor's fonts that aren't listed) use these reasons.
- [ ] Follow the rebuild protocol. `names.json` is still `0.1.0-draft`, and `catalog-site.json` v1 doesn't change.

**Done when:** `tff-catalog validate` passes, and the hand check finds no misleading reason.

### Step 3: The score's scale and its explanation
**Who:** the scoring-method port, not this plan. **Depends on:** the port, which is on hold.
- [ ] The port brings the new shown score (Q1, settled), and with it How we rank's text. It must remove these two:
  - the wrong sentence, "a font scoring 90 stands above about 90% of them";
  - "Past #100 we show bands".

  This plan builds nothing for step 3. Tick it when the port has merged and both sentences are gone.
- [ ] Milestone 3 step 8's "Rank numbers stay as published" follows the port and Q2.

**Done when:** the port has merged, and How we rank describes the score the list shows.

### Step 4: "Popularity" and the other words
**Who:** Claude. **Depends on:** Q2.
- [ ] Update the templates, the sort header's script, `site/CONTRACT.md` §3, the nav, the methodology page's title, and their tests.
- [ ] The nav's new "How it works" changes its width. If step 4 lands before step 5, re-measure the header's single-row breakpoint as step 5 describes.
- [ ] Check the sort button fits its 96 px column, including at 320 px reflow.
- [ ] Update `docs/usability-test.md` to match: the "rank" wording in tasks 5 and 6, and "Choose task 6's two fonts from the current Overall rank", since Overall retires. Unless the lead's branch already does this.

**Done when:** CI passes, and no visible "Rank" is left over the score column.

### Step 5: When a font isn't here
**Who:** Claude; the owner approves the wording. **Depends on:** Q5, Q7 and Q9 (answered); step 2 for the reasons.
- [ ] **First part:**
  - [ ] Move the note below the list, on wide screens and phones. The top of the front page becomes the headline, the lead, the privacy line, then the list.
  - [ ] When search is the only filter and finds nothing, say "No listed font matches '…'" and show the note. Other empty results keep today's text.
  - [ ] Rewrite the note as a conversation that ends with the tip pitch, asking the reader to leave a tip with no amount (`tip_jar`), under Funding as amended.
  - [ ] Add the "Tip Jar" link at the end of the header nav, and remove the footer's tip link.
  - [ ] **The header stays one row** (`wordmark_breakpoint`, 2026-10-04, in #55). The wordmark is 418 px wide from 760 px up, with the nav beside it, and 278 px below. The 760 px is today's nav, about 280 px: 16 + 418 + 24 + 280 + 16 = 754.
    - "Tip Jar" and step 4's "How it works" widen the nav by roughly 80 px. Re-measure the widest single-row width, and move the breakpoint (`@media (max-width: 47.5rem)` in `site/css/10-base.css`) and its test (`tests/site/test_shell.py::test_the_wordmark_is_418_px_wide_from_760_px_and_278_below`) to match.
    - On phones the nav has its own line under the wordmark. At 320 px it may no longer fit on one line. Before building, ask the owner how to keep it to one line: tighter spacing, a smaller nav text, or Privacy moving to the footer. A second line would go against `wordmark_breakpoint`.
    - Check header height at 320 px and from 640 to 1280 px, in Chromium and Firefox, so a second row is caught.
  - [ ] Search lists name matches first, then fonts matched only through another name (`search_name_first`).
- [ ] **Second part** (after step 2):
  - [ ] Reasons and alternatives, per Q5, from a file loaded on the first empty search, outside the 100 KB.
  - [ ] M2's privacy test expects exactly that one fixed, same-origin request.

**Done when:** a phone's first screen shows at least one font; searching "Satoshi" and "Helvetica" shows the new text; and layout shift, axe and the privacy test pass.

### Step 6: Filters that stay in view
**Who:** Claude. **Depends on:** step 1a.
- [ ] Make `#filters` `position: sticky` above the wide-screen breakpoint, scrolling on its own when it's taller than the window.
- [ ] The sample-catalog accessibility grid passes: focus, reflow, text spacing and forced colours.

**Done when:** after scrolling 5,000 px on a 1280 × 900 window, the filters are still on screen, and CI passes.

### Step 7: Bigger specimens, the NF column, the row layout and the details' sample lines
**Who:** Claude. **Depends on:** Q6, Q9 and the rulings of 2026-10-05 (`step7_list_first`, `specimen_trim_served`, `specimen_box_heights`, `phone_rows_trim`). This is priority 2. Five pull requests, 7a first.

The facts behind it come from a workflow of eight agents on 2026-10-05: five readers, a planner and two adversarial checkers, both of which re-drew all 500 specimens from the cached fonts.

#### 7a: Names-only, larger specimens, trimmed by the site build
- [ ] In `src/tff_site/build.py` `_copy_specimens`, after the sha256 check, trim each committed two-line SVG to its name line:
  - [ ] Every file is one `<path>` that draws the name's contours first and the sample line's after (`render.py`). Cut the path where line 2 starts. The rule that matched all 500 files is the largest backward x jump. A rule based only on height cuts 15 files wrongly.
  - [ ] Realign the name to its own ink's left edge. In 7 fonts the sample line set the left shift, so a plain cut leaves the name 2–22 grid units right: Cinzel Decorative, Gochi Hand, Homemade Apple, Indie Flower, Reenie Beanie, Shrikhand and Sunshiney.
  - [ ] Set the viewBox to the name: two-line files are 492–560 units tall, and name-only ones 308–387.
  - [ ] Trim only files whose sha256 is in a pinned list of the 500 verified files. Any other file passes through untouched. A wrong cut would otherwise ship a plausible but wrong image, not an error. Use the catalog's `specimen_name_only` flag, not a height rule, to recognise files that are already name-only.
  - [ ] Record a one-time check against local renders of all 500 fonts in the pull request: the trimmed contours equal `render(name_only=True)`'s. CI checks the 5 pinned fixtures.
- [ ] The box: `--spec-h` 64 px on wide screens (`site/css/00-tokens.css`), with `SPEC_BOX_PX` following it (`build.py:114`, today 48). The phone box stays 40 px until 7b. `35-specimens.css` (`mask-size: contain`, left center) stays as it is.
- [ ] Re-measure the `--row-est-h` estimates in `site/css/20-list.css` for every layout, including the tablet table layout. At 700 × 800, rows grow from 172 to 188 px with a 64 px box.
- [ ] Records and wording:
  - [ ] `site/CONTRACT.md`: line 29 says the build trims, not just copies.
  - [ ] The build docstring, and `docs/milestone-2.md` lines 100 and 102.
  - [ ] The image's alt and aria-label "<family> sample" now describe the name only. Settle the wording, since it repeats the `<h3>`, and update `test_specimens_loader.py`.
- [ ] Tests: replace the `svg()` helper in `tests/site/test_build.py` (lines 207–212), which writes a 492-unit file with one contour that the trim rule rejects; this changes every build test that uses the `catalog` fixture. Add a test over all 500 committed files and the 5 fixtures.
- [ ] Re-run M2 step 10's load-speed numbers, the accessibility suites and the real-catalog site tests.
- [ ] Ask the lead to redeploy staging once it merges. Merging to `main` alone doesn't update staging, and production deploys are off. The owner then checks the vertical alignment: the score and the NF mark line up with the first line, while the name is centred in its box.

**Done when:**
- at 1280 px the median drawn name is at least 1.5 times today's 25.0 px per em (expected about 53);
- at 375 px it is at least 1.5 times today's 20.8 (expected about 32);
- desktop rows don't grow, and phone rows grow by at most 8 px;
- the specimens served total about 0.54 MB gzip, against 1.33 MB;
- CI passes.

Long names are limited by width and grow less: about 1 phone row in 10 grows under 5% (Cormorant Garamond, Libertinus Keyboard). Say so in the pull request.

#### 7b: The NF column and legend, and the phone box
- [ ] On phones, the NF column (`20-list.css`, `calc(2.25rem + var(--space-2))`) reserves its 44 px only in rows with an NF marker: `.font-title:not(:has(> .nf-mark))` gets one column. Browsers without `:has()` keep today's layout. 430 of 500 phone rows gain 44 px of specimen width.
- [ ] The phone box becomes 48 px (`specimen_box_heights`), so the median phone name reaches about 39.9 px per em.
- [ ] The NF legend (`#nf-legend`) shows only while some NF row is among the rows the filters leave, like the held legend: a `nerd_shown` flag, a server-side `hidden` attribute, and a toggle in `25-render.js` (`nf_legend_visible_rows`).
- [ ] Rewrite `test_the_nerd_marker_keeps_the_row_height` for phones.

**Done when:** the legend is hidden with Handwriting, Display and an empty search, and shown with the Nerd filter; phone rows still fit about five per 812 px screen; CI passes.

#### 7c: Download as the button, and phone row margins
- [ ] "Download from …" gets the button style, and Details the quieter one (`download_button`). In forced colours both keep a visible border.
- [ ] On phones, trim a few pixels of row margin so about five rows still fit per screen with the larger names (`phone_rows_trim`). Category and license stay on their own line.

**Done when:** at least five rows per 812 px phone screen once scrolled to the list, axe passes, and CI passes.

#### 7d: The renderer draws names only, and `build/specimens` is redrawn
- [ ] The code (`RENDERER_VERSION` 3; `render_one` draws the name only; `SAMPLE` leaves the cache key; intended name-only drawings aren't flagged `specimen_name_only`; the fixtures are regenerated) **lands in the same pull request as the redraw, never before it**. Otherwise every render-cache key misses, and the next replay redraws, or flags as failed, all 500.
- [ ] The redraw needs the lead's OK. The lead's condition ("catalog.json and catalog-site.json come out byte-identical") can't hold for a redraw in place, since both catalogs store every specimen's sha256 and `tff-site build` refuses a mismatch. The options:
  - the lead restates it as identical except the 500 `preview.sha256` values in each catalog and `run.code_commit`, with no flag changes, identical over two runs, and `validate --committed` passing;
  - or the names-only files are drawn as a new set with their own manifest, which leaves both catalogs byte-identical;
  - otherwise it waits for the first live refresh.
- [ ] Run `tff-site build` on the redrawn outputs before committing: `validate --committed` has no specimen-hash check.
- [ ] Once `build/specimens` is names-only, remove 7a's trim.

#### 7e: The details' sample lines and glyph strip (after the freeze)
- [ ] Separate SVG sets drawn by the specimens stage (`specimens/<id>.sample.svg` for "Hamburgefonstiv 0123", `<id>.code.svg` for the 97 monospace fonts, `<id>.glyphs.svg`), listed in their own manifest outside the frozen `catalog-site.json`, such as `build/specimens/sets.json` with path and sha256 for each. The site build checks the hashes, writes the files under hashed names and lists them in `details.json`. `_prune` and both budget checks learn the new files.
- [ ] **Budget:** the glyph strip as ruled brings the specimens to 11.92 MB, over the 10 MB cap, with 8 files over 16 KB. Ask the owner before building: for example, a separate total for the details sets with the 16 KB cap kept, no strip for the heaviest fonts, a coarser grid, or a smaller strip.
- [ ] The designer link reads "Designer's page" (`designer_label`).
- [ ] The code line's text needs the owner's OK.

**Done when:** the freeze has lifted, the details show the lines and the strip within the ruled budget, and CI passes.

### Step 8: Copy and data fixes
**Who:** Claude; the owner rules on links. **Depends on:** Q3, Q4 and Q8 (answered). This is priority 1.

#### 8a: The license checks (alongside step 7, `step7_list_first`)
- [ ] **Heavy Data:** re-read its license text (the file in the Nerd Fonts repository that its details link to) against Rules 1–3, and report to the owner. The ruling of 2026-09-28 stands unless the text says otherwise.
- [ ] **Charter's license link** points to a web page (`practicaltypography.com/charter.html`), unlike every other font's license text. Find the license text in its download and propose a pinned URL for the owner's ruling (gate K and L3).

#### 8b: The wording (after the lead's branch)
- [ ] Apply the wording of Q3 and Q8. The front-page lead's new sentence (`front_page_lead_counts`) lands with the Project default. The note's "with no use restrictions" and Q8's asides don't wait for the scoring change, but they do touch the templates.
- [ ] "Comes with" names the systems, as settled for Q4.
- [ ] Fix How we rank's "Choose a rank at the top of the list": the select is in the sidebar on wide screens.

**Done when:** CI passes, and `tff-catalog links --check` passes.

### Step 9: Page weight
**Who:** Claude. **Depends on:** Q10 (answered). This is priority 3.
- [ ] **9a. Brotli:**
  - [ ] The build writes `.br` files beside the HTML and hashed assets.
  - [ ] `ops/caddy/site.caddy` serves them (`file_server` with `precompressed br gzip`) and keeps `encode zstd gzip` for everything else. This goes through a reviewed pull request, and SERVER.md is updated.
  - [ ] Through Cloudflare, check that a browser gets `content-encoding: br`. Today it gets gzip, even when it offers `br` and `zstd`. If Cloudflare changes the encoding, record what it does.
  - [ ] CI's page budget measures the Brotli size (`brotli`).
- **9b. Not built:** the owner kept every row in the front page's HTML (Q12 (c), `rows_all_in_html`).

**Done when:** `curl` shows Brotli from staging through Cloudflare; the first load, compressed as sent, is lighter than today's 96.1 KB by at least 15 KB after 9a (19.4 KB measured); and M2 step 10's load-speed numbers, including blocking time, still pass.

### Step 10: Milestone 3 additions
**Who:** Claude writes; the owner answers them with Milestone 3's Step 0. **Depends on:** Q11.
- [ ] Add to milestone-3.md's Step 0 decisions the "comes with" first step and "Show only mine", plus the test that no font name enters the address (`inventory_additions`).
- [ ] Step 8's rank wording follows step 3.

**Done when:** milestone-3.md is merged with the new entries.

### Step 11: Redeploy the test site and check again
**Who:** the lead redeploys; Claude checks; the owner looks. **Depends on:** steps 3–9.
- [ ] Redeploy `https://staging.purelyfreefonts.com` (`ops/deploy.sh staging`, done by the lead), then check that `version.txt` shows the new commit.
- [ ] Repeat the checks behind these findings:
  - a search for Satoshi and Helvetica;
  - the NF legend with Handwriting;
  - the filters after scrolling;
  - the first screen on a phone;
  - Brotli through Cloudflare, and the first load's weight;
  - the names-only specimens (the lead may redeploy right after 7a merges, so the owner sees them early), and, after the freeze, the details' sample lines and glyph strip;
  - the Tip Jar link and the phone nav;
  - "Comes with" on Open Sans;
  - the Charter link.
- [ ] Optional: ask a fresh outside reviewer, again with no context, to look over the changed site.

**Done when:** every check passes on the test site, and the owner has looked.

---

## Findings that change nothing

- **The license name on every row** (C 9a) stays: "it's the most popular license".
- **The dark-mode header** stays black on white: "black on white is the branding of typography" (step 1a). The outside reviewer had called it a bug.
- **Heavy Data's license** (R round 1) was ruled on 2026-09-28. The reviewer mixed up its links: the archived vicfieger.com page is its download link, and its license link is a file in the Nerd Fonts repository. Step 8 still re-reads that text, as priority 1 asks.
- **Rank numbers first, with the score second** (R design 3) goes against the owner's direction: "we are no longer ranking things".
- **Loading the A–Z fonts on demand** (R round 3 and priority 3) is already planned in [docs/milestone-more-fonts.md](docs/milestone-more-fonts.md). Data loaded on demand counts outside the 100 KB, which answers the reviewer's question about what the budget covers.
- **The budget's headroom today:** CI's list-page budget (HTML, CSS and JS) stands at 78.6 KB of 100 KB, and the catalog data (list.json) at about 18 KB of its own 100 KB. Priority 3 still asks for room before anything new is added.
- **Launch reminders from the reviewer:**
  - **Letting search engines index production:** already set (M2-D8). Only staging has `noindex`, and M2 step 14 covers `robots.txt` and `sitemap.xml` at launch.
  - **The server setting that blocks local font access** (`local-fonts=()`): already planned to open where the check runs, in Milestone 3 steps 7 and 14.
- **Most of the reviewer's inventory advice** is already in Milestone 3:
  - the Chromium button and the paste box with a command for each system;
  - no probing of fonts;
  - old names and patched builds counted as yours, with labels;
  - near-matches;
  - the "You have 41 of the top 100" line;
  - storing nothing by default.
