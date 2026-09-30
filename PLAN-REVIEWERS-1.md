# Plan: changes from the first reviews of the test site

This is the working checklist for the changes that came out of the first reviews of `https://staging.trulyfreefonts.com` on 2026-09-30: release `f6e84b3`, catalog run 2026-09-26, method 2026-09-29. The site code on `main` has had no changes since, apart from the v1 schema freeze. Settled decisions are in [AUTHORITY.md](AUTHORITY.md). The build work joins [docs/milestone-2.md](docs/milestone-2.md) before usability round 1 (step 13), and the inventory items join [docs/milestone-3.md](docs/milestone-3.md).

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

**Status (2026-09-30):** plan written. The owner has agreed to the outside reviewer's three priorities, which set the order of work, but no ruling is recorded in AUTHORITY.md yet and nothing is built. Step 1 comes first.

## Priorities

The owner: "I agree with the reviewer's priorities." In the reviewer's words, the three changes that matter most before launch are:

1. **Match the homepage claims to the evidence.**
   - "Public install and usage counts" instead of "how many people install them".
   - "No use restrictions" instead of "no limits".
   - Name the systems behind the "Comes with Linux" tag.
   - Check the Heavy Data and Charter licenses.

   In this plan: Q3, Q4 and step 8.
2. **Put the fonts first.** Show fonts sooner on the page, and make the previews bigger, with text visitors can switch. In this plan: step 5's first part, Q6 and step 7.
3. **Free up page weight before adding anything.** Stop shipping the list twice, and load the A–Z list only when needed, so there's room for both new features.
   - In this plan: Q10, Q12 and step 9.
   - The A–Z list loading on demand is already in [docs/milestone-more-fonts.md](docs/milestone-more-fonts.md).
   - "Before adding anything" means before the A–Z list (the more-fonts milestone) and before the inventory check joins the list page (Milestone 3's M3-D8, built in Milestone 4).

**How to read each step:**
- **Who:** the owner, Claude, or both.
- **Depends on:** what must be settled first. Q1–Q12 are the owner's questions in step 1.
- **Done when:** what must be true before the step is ticked.

Tick each item as soon as it is done and verified. Checklists nest: a parent is ticked only when every item under it is. If an item is only partly done, leave it unticked and note what's left.

**Order.** Each step is one worktree and pull request. Steps may be built side by side but land in this order:
1. Step 1.
2. **Priority 1:** step 8.
3. **Priority 2:** step 5's first part, then step 7.
4. **Priority 3:** step 9.
5. **The rest:** steps 2, 3, 4, 6 and 10, then step 5's second part, which waits for step 2.
6. Step 11.

Priorities 1 and 2, and steps 3, 4 and 6, land before usability round 1, so testers see the changed site. Priority 3 lands before the A–Z list or the inventory check is added to the list page.

---

### Step 1: AUTHORITY.md
**Who:** the owner rules; Claude records. **Depends on:** nothing.

**1a. Rulings the owner gave in chat on 2026-09-30, to record as they are:**
- [ ] **Filters stay in view.** "Obviously we should have a fixed sidebar on large screens." On wide screens the filter sidebar stays in view while the list scrolls. Built with `position: sticky`, so it never covers the footer. This extends M2-D4. Phones keep the "Filters" button. (O; C item 6)
- [ ] **"Why isn't my favorite free font here?" comes after the fonts,** on wide screens and phones. It becomes a short conversation with the reader and ends with the tip link. It also appears when a search finds nothing (step 5). This replaces `why_not_listed_layout` of 2026-09-29. (O; C items 3 and 5; R design 1)
- [ ] **Funding, amended.** It now reads "A donation button on the site, and nothing more: no donation strategy or fundraising work." The amendment adds one short pitch, at the end of that note: the owner's idea to "sell the reader on buying me coffee". The Stripe Payment Link stays. Q7 settles the wording.
- [ ] **"Popularity" replaces "Rank" over the score column.** (O note 2) Q2 settles the rest of the wording.
- [ ] **The outside reviewer's three priorities set the order of this work.** ("I agree with the reviewer's priorities.") Within them:
  - **The homepage's claims change to match the evidence** (priority 1). Q3 only confirms the exact sentences.
  - **"Comes with" names the systems** (priority 1, settling Q4). For example, Open Sans's row says "Comes with CachyOS, EndeavourOS", not "Comes with Linux". This amends the "Rows" part of **Filters (2026-09-30)**.
  - **Bigger previews, with sample text visitors can switch** (priority 2, settling Q6 as (b)). Q6 only settles which sentences, and how many.
  - **The list page gets lighter before anything is added to it** (priority 3). The list's rows stop being shipped in full in the HTML (Q12 settles how). The A–Z list loads only when needed.
- [ ] **The header stays black on white in dark mode.** The outside reviewer called it a bug. The owner: "black on white is the branding of typography." This confirms the header colour ruling of 2026-09-29.
- [ ] **Tracked docs** gains `PLAN-REVIEWERS-1.md`.

**1b. Questions for the owner,** asked in chat as grouped single-select questions, each with Claude's recommendation first:
- [ ] **Q1. The score's scale.** (O note 4; C items 1–2; R design 3)
  - **Today:** 100·Φ(z) (`score_curve`). Overall shows 53–99. How we rank says "a font scoring 90 stands above about 90% of them", which is wrong: the 500 listed fonts are the top of about 2,000 tracked families, yet the last shows 53.
  - **(a) Recommended, the owner's idea:** each view's listed fonts span 1–100. The least popular listed font gets 1, the most popular gets 100, and fonts in between are placed linearly on the engine's score, so the gaps between fonts stay.
  - **(b)** keep 100·Φ(z) and rewrite the sentence.
  - **(c)** the font's percentile by position. This loses the gaps.
  - **Either way:** it replaces `score_curve`, `catalog-site.json` v1 is untouched, and TASK-4's triangles compare the stored score, not the number shown.
- [ ] **Q2. Words.** (O notes 2–3; C item 4)
  - The select's label: keep "Rank", or "Measured by", or "Popular with".
  - The sort button's order words: today "best first" and "least used first" (`sort_header`), or "most popular first" and "least popular first".
  - The page title: "How we measure popularity", or the owner's "How we evaluate popularity".
  - The nav link: the phone nav is already full at 375 px, so a short link such as "How it works".
  - Claude recommends keeping "rank" only in a font's details, for its exact place.
- [ ] **Q3. Wording that claims too much.** (R round 1 and priority 1) The change is agreed. Only the sentences need confirming.
  - The lead's "They are ranked by how many people install them and use them in their work" becomes "They are ranked by public install and usage counts" (the reviewer's words), or a longer version, "They are ranked by public counts of installs and use in websites, code and apps".
  - The note's "with no limits on how they're used" becomes About's "with no use restrictions". The SIL Open Font License, used by 453 of the 500 fonts, does set conditions.
  - This replaces the wording of `front_page_lead_sharing`.
- [x] **Q4. "Comes with Linux".** (R round 1) **Settled by priority 1** (step 1a): the row names the systems. Open Sans carries the tag only because CachyOS and EndeavourOS ship it.
  - **Left for step 8's pull request:** how a long list shortens (Claude proposes after two systems, with the rest in the details), and what "Hide fonts that come with: Linux" hides, which Claude proposes stays any Linux system.
- [ ] **Q5. A search that finds nothing.** (C item 3; R design 4)
  - **(a) Recommended:**
    - **Now:** say "No listed font matches '…'", followed by the note.
    - **After step 2:** add each unlisted font's reason, plus free fonts with the same letter widths for well-known paid ones: Arial and Helvetica → Arimo, Liberation Sans, TeX Gyre Heros; Times → Tinos, Liberation Serif, TeX Gyre Termes; Courier → Cousine, Liberation Mono.
  - **(b)** as (a), plus look-alikes chosen by eye (such as Futura → Jost), each with a stated basis.
  - **(c)** the message only.
  - **The data:** the list of excluded names is about 35 KB compressed, so it loads only on demand, outside the 100 KB.
- [ ] **Q6. Specimens and the NF column.** (O note 7; C items 7 and 9b; R design 2) **Settled as (b) by priority 2** (step 1a). What that means:
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
- [ ] **Q7. The tip wording,** in the note and in the footer: keep "Leave a tip ($5 suggested)" (2026-09-26), or wording built on "coffee".
- [ ] **Q8. How we rank's internal asides.** (C item 8) It has four "(owner ruling of …)" asides and the Dirichlet formula.
  - **(a) Recommended:** rewrite the parts of `docs/ranking-methodology.md` that the site copies, so both read as public text. The page promises that the two always agree.
  - **(b)** remove the asides when the site is built.
- [ ] **Q9. Outside points not yet discussed.** Keep or change each:
  - The sample line reads as placeholder text (`specimen_sample_latin`). Q6's preset lines may answer this.
  - The details' "Designer" row shows a repository ("GitHub: rsms/inter"). `catalog-site.json` v1 has no designer names, so only the label can change, for example to "Designer's page".
  - "Download from …" should look like the main action.
  - Phone rows could merge lines. About three fonts fit on a phone screen today.
  - A search for "mono" includes fonts matched only through a Nerd build's name (Ubuntu comes second), so exact name matches should come first.
  - A glyph strip in the details, before the 877 KB "Type your own text".
- [ ] **Q10. Brotli.** (R round 3; serves priority 3) Serve pre-compressed Brotli files.
  - **Saving:** measured on the live files, the HTML, CSS, JS and list data drop from 96.1 KB to 76.7 KB (−20%), with no markup change.
  - **Work:** a build change and a change to `ops/caddy/site.caddy`.
  - **(a) Recommended:** CI's budget then measures the Brotli size, which is what visitors download, so the saving counts as headroom.
  - **(b)** CI keeps measuring gzip, which is stricter but leaves no headroom on paper.
- [ ] **Q11. Milestone 3 additions.** (R round 4)
  - Present the "Hide fonts that come with" filter as the first step, needing no permission, before the check.
  - Add "Show only mine", to check the licenses of fonts you already use.
  - Add an explicit test that no font name ever enters the page address.
  - These become new Step 0 decisions in milestone-3.md.
- [ ] **Q12. Stop shipping the list in full in the HTML.** (priority 3; R round 3)
  - **What's actually doubled:** little. list.json holds filter and score data (ids, search keys, per-view scores, flags, categories), while the rows' HTML holds what is displayed. The weight is the HTML: all 500 rows, 639 KB raw, 48.9 KB compressed.
  - **Measured:** writing only the first 100 rows into the HTML takes it to 12.7 KB. Carrying rows 101–500 as data costs at most 17.1 KB (every text and link, before any trimming). That's a net saving of about 19 KB compressed on the first load, on top of Brotli.
  - **Cost:** a contract change. Today `Render` only reorders rows the server wrote, and without JavaScript the page shows "the full list by overall rank" (site/CONTRACT.md §3).
  - **(a) Recommended:** the HTML holds the first 100 rows of Overall, and the script builds the rest from the data on load.
    - Without JavaScript, visitors see the top 100 and a note.
    - Find-in-page works once the script has run.
    - Its time counts against M2 step 10's blocking-time budget of 200 ms.
    - The per-row `<noscript>` specimen copies drop to 100.
  - **(b)** the first 50 rows, which saves a little more and gives a no-JavaScript view of 50.
  - **(c)** all rows stay in the HTML, and only Brotli (Q10) lightens the page.
- [ ] Record each ruling and answer in AUTHORITY.md under Site (Milestone 2), and in `data/reviews/site/<date>.toml` with `choice`, `recommended`, `value`, `ruling` and `reason`, as earlier site rulings are.
- [ ] Add milestone-2.md step 12b, "Changes from the first reviews", linking this plan, and make step 13 depend on it.

**Done when:** every ruling and answer is in AUTHORITY.md and `data/reviews/`, and merged to `main` by pull request together with this file.

### Step 2: Clean up the reasons fonts aren't listed
**Who:** Claude; the owner reviews. **Depends on:** Q5 (skip under (c)).
- [ ] In `build/names.json`, the reason `latin` also covers families that failed the Latin check because no readable file was found, such as Arial and Arial Black. Give those a reason of their own, so no visitor reads "not Latin" for Arial.
- [ ] Check 30 reasons by hand, across every reason.
- [ ] Both step 5 and Milestone 3 step 8 (the line counting a visitor's fonts that aren't listed) use these reasons.
- [ ] Follow the rebuild protocol. `names.json` is still `0.1.0-draft`, and `catalog-site.json` v1 doesn't change.

**Done when:** `tff-catalog validate` passes, and the hand check finds no misleading reason.

### Step 3: The score's scale and its explanation
**Who:** Claude. **Depends on:** Q1.
- [ ] `display_score` in `src/tff_site/data.py` maps each view's scores per Q1, from that view's listed fonts, with its tests.
- [ ] Screen readers still hear "Score N of 100".
- [ ] How we rank's "Scores" text (from `docs/ranking-methodology.md`) matches the new number. Also fix "Past #100 we show bands": bands now appear only in the details.
- [ ] A note on TASK-4: its triangles compare `score` with `previous_score`.
- [ ] Milestone 3 step 8's "Rank numbers stay as published" follows Q1 and Q2.

**Done when:** CI passes, each view on the real catalog spans the new range, and the page's text matches the number.

### Step 4: "Popularity" and the other words
**Who:** Claude. **Depends on:** Q2.
- [ ] Update the templates, the sort header's script, `site/CONTRACT.md` §3, the nav, the methodology page's title, and their tests.
- [ ] Check the sort button fits its 96 px column, including at 320 px reflow.
- [ ] Update the "rank" wording in `docs/usability-test.md` tasks 5 and 6 to match.

**Done when:** CI passes, and no visible "Rank" is left over the score column.

### Step 5: When a font isn't here
**Who:** Claude; the owner approves the wording. **Depends on:** Q3, Q5, Q7; step 2 for the reasons.
- [ ] **First part:**
  - [ ] Move the note below the list, on wide screens and phones. The top of the front page becomes the headline, the lead, the privacy line, then the list.
  - [ ] When search is the only filter and finds nothing, say "No listed font matches '…'" and show the note. Other empty results keep today's text.
  - [ ] Rewrite the note as a conversation that ends with the tip link, in Q7's wording, under Funding as amended.
- [ ] **Second part** (after step 2):
  - [ ] Reasons and alternatives, per Q5, from a file loaded on the first empty search, outside the 100 KB.
  - [ ] M2's privacy test expects exactly that one fixed, same-origin request.

**Done when:** a phone's first screen shows at least one font; searching "Satoshi" and "Helvetica" shows the new text; and layout shift, axe and the privacy test pass.

### Step 6: Filters that stay in view
**Who:** Claude. **Depends on:** step 1a.
- [ ] Make `#filters` `position: sticky` above the wide-screen breakpoint, scrolling on its own when it's taller than the window.
- [ ] The sample-catalog accessibility grid passes: focus, reflow, text spacing and forced colours.

**Done when:** after scrolling 5,000 px on a 1280 × 900 window, the filters are still on screen, and CI passes.

### Step 7: Bigger specimens, switchable sample text, and the NF column
**Who:** Claude; the owner picks the sentences. **Depends on:** Q6. This is priority 2.
- [ ] Specimen height (`--spec-h`), the phone row layout, and the NF column only where needed.
- [ ] Tie the NF legend to the visible rows: `Render` updates it with the count.
- [ ] **Preset sample lines:**
  - [ ] The specimen step draws one set per sentence, within M2 step 5's budgets (16 KB compressed per file, 10 MB in total).
  - [ ] A control above the list switches the sentence. Only the chosen set loads, still lazily.
  - [ ] The choice goes in the address, like the filters. A shared link keeps it.
- [ ] `site/CONTRACT.md` and the specimen tests cover the new sets.
- [ ] Re-run M2 step 10's load-speed numbers.

**Done when:** at 375 px the median specimen is about twice today's height; each preset line shows in every row that has a specimen; the legend is hidden when no NF row shows; and CI passes.

### Step 8: Copy and data fixes
**Who:** Claude; the owner rules on links. **Depends on:** Q3, Q8, Q9; Q4 as settled. This is priority 1.
- [ ] Apply the wording of Q3 and Q8.
- [ ] "Comes with" names the systems, as settled for Q4.
- [ ] **Heavy Data:** re-read its license text (the file in the Nerd Fonts repository that its details link to) against Rules 1–3, and report to the owner. The ruling of 2026-09-28 stands unless the text says otherwise.
- [ ] Fix How we rank's "Choose a rank at the top of the list": the select is in the sidebar on wide screens.
- [ ] Relabel the "Designer" row, if Q9 says so.
- [ ] **Charter's license link** points to a web page (`practicaltypography.com/charter.html`), unlike every other font's license text. Find the license text in its download and propose a pinned URL for the owner's ruling (gate K and L3).

**Done when:** CI passes, and `tff-catalog links --check` passes.

### Step 9: Page weight
**Who:** Claude. **Depends on:** Q10, Q12. This is priority 3. Two pull requests, Brotli first.
- [ ] **9a. Brotli:**
  - [ ] The build writes `.br` files beside the HTML and hashed assets.
  - [ ] `ops/caddy/site.caddy` serves them (`file_server` with `precompressed br gzip`) and keeps `encode zstd gzip` for everything else. This goes through a reviewed pull request, and SERVER.md is updated.
  - [ ] Through Cloudflare, check that a browser gets `content-encoding: br`. Today it gets gzip, even when it offers `br` and `zstd`. If Cloudflare changes the encoding, record what it does.
  - [ ] CI's budget measures what Q10 says.
- [ ] **9b. Rows past the first ones built from data** (per Q12):
  - [ ] The build writes Q12's number of rows into the HTML, plus a compact data file for the rest. Trim it by writing links from ids where the pattern allows, such as the Google Fonts specimen pages.
  - [ ] `Render` builds the missing rows once on load, then reorders as today. Focus, announcements and layout shift behave as before.
  - [ ] The no-JavaScript note says the page shows the top rows, and that the full list needs JavaScript.
  - [ ] `site/CONTRACT.md` §2, §3 and §5 and their contract tests change in the same pull request. The accessibility and privacy tests run on the built rows.
  - [ ] `catalog-site.json` v1 doesn't change. This is display only, in `tff_site`.

**Done when:** `curl` shows Brotli from staging through Cloudflare; the first load, compressed as sent, is lighter than today's 96.1 KB by at least 15 KB after 9a (19.4 KB measured) and by at least 30 KB after 9b, unless Q12 is (c); and M2 step 10's load-speed numbers, including blocking time, still pass.

### Step 10: Milestone 3 additions
**Who:** Claude writes; the owner answers them with Milestone 3's Step 0. **Depends on:** Q11.
- [ ] Add to milestone-3.md's decisions the "comes with" first step and "Show only mine", plus the test that no font name enters the address.
- [ ] Step 8's rank wording follows step 3.

**Done when:** milestone-3.md is merged with the new entries.

### Step 11: Redeploy the test site and check again
**Who:** Claude; the owner looks. **Depends on:** steps 3–9.
- [ ] `ops/deploy.sh staging`, then check that `version.txt` shows the new commit.
- [ ] Repeat the checks behind these findings:
  - the score range per view;
  - a search for Satoshi and Helvetica;
  - the NF legend with Handwriting;
  - the filters after scrolling;
  - the first screen on a phone;
  - Brotli through Cloudflare, and the first load's weight;
  - the preset sample lines;
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
