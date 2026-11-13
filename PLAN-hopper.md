# Plan: the hopper's layout, from the owner's review of 2026-11-01

This is the working checklist for the owner's review of the hopper on the test site, `https://staging.purelyfreefonts.com`. Staging runs `preview/2026-10-17` at `a8ad402`, which holds the hopper's draft pull request #100 (`claude/hopper-ui`, Milestone 3, slice 1, PR 4) at its head, `7e7ec1d`. Settled decisions are in [AUTHORITY.md](AUTHORITY.md) (**Inventory tools**). The build work is part of [docs/milestone-3.md](docs/milestone-3.md)'s PR 4.

The reviewers:

- **O:** the owner, from a screenshot of the filter panel.
- **C:** Claude, measuring on staging and suggesting changes, as the owner asked, from UI design principles and psychophysics.

**Status (2026-11-11):** steps 1 to 3 are done: the rulings (#103, merged to `main`), step 2 (#104) and step 3 (#107), both merged into #100. Step 4: the lead deployed #100's step 3 head to staging on 2026-11-03, and the owner has tried part of it (2026-11-03 and 2026-11-05). What's left of his try is in step 4. The soft launch carries the hopper (`launch_with_hopper`, PLAN-launch.md). *Before 2026-11-03:* four findings from the owner (F1–F4) and nine suggestions from Claude (S1–S9). Four independent reviews checked this file against the code before it went to the owner. The owner answered Q1–Q3 the same day, each as Claude recommended: the receipt, all nine suggestions, and staging showing #100. Step 1 recorded the rulings (#103, merged). Step 2 is built in #104, merged into #100. Step 3 is built in #107, stacked on #100 in the same way, since #104 had merged; its CI runs. #100's description stays open (the lead's).

**How to read each step:** **Who**, **Depends on** and **Done when**, as in PLAN-REVIEWERS-1.md. Tick each item as soon as it is done and verified. Checklists nest: a parent is ticked only when every item under it is.

**Words used here:**

- the hopper's two numbered steps are called by their headings, "Make the list" and "Drop or paste it", so they aren't confused with this plan's steps;
- the hopper's levels of text are **the title** (its legend), **step headings** (the two above), **field labels** ("Or paste the list", "Show") and **notes** (the lead, "Make the list on a computer.").

---

## Findings

All measurements are from staging (`a8ad402`) in Chromium, with a five-name `fonts.txt` dropped by script (a `DragEvent` carrying a `File`). At 1280 px the filter panel is a 272 px sidebar with a 240 px text column; at 768 px it is the panel behind the Filters button.

### F1. The hopper doesn't read from top to bottom (O)

The owner: "Does not flow from top to bottom--the instructions are on the bottom."

On staging, `fieldset#mine` runs:

1. the title, "Fonts you have", and the lead;
2. the drop zone, "Drop fonts.txt here", with Choose file;
3. "Or paste the list", the paste box and Check;
4. "How to make the list": one closed `<details>` per system.

The steps for making the list come after the controls that take it. Once a list loads, the result box (the summary, the Show menu and "Forget this list") shows first, above the drop zone.

### F2. A dropped file leaves no trace where it was dropped (O)

The owner: "When you upload fonts.txt, it does not populate the pasted list, so you can't tell that it did anything."

The list is matched, and the result box shows. But it shows *in the drop zone's place*, and pushes everything below it down. The drop zone still asks for a file:

| Viewport | Result box | Drop zone moves down | Drop zone then says | Paste box |
|---|---|---|---|---|
| 1280 × 1800 | 215 px tall, where the drop zone was | 227 px | "Drop fonts.txt here" | empty |
| 768 × 1024 | 167 px tall, where the drop zone was | 179 px | "Drop fonts.txt here" | empty |

The result box's appearance is sudden, and `bring()` scrolls to it. What fails is the meaning: the place where the visitor acted shows the same invitation as before, a little lower, so the page seems not to have taken the file. Nielsen's first heuristic, visibility of system status (Nielsen 1994), asks for the answer where the question was asked.

The paste box is empty by design. `apply()` in `site/js/55-mine.js` empties a matched paste. `site/CONTRACT.md` section 4 says "A matched paste leaves the box", and section 11 says the page keeps "the ids of the fonts the list has and a few counts, nothing of its names". `tests/site/test_mine.py` asserts "a dropped file goes in no box". Filling the box would change that guarantee, so it is Q1.

### F3. The section's title (O)

The owner: "I want the section called "Fonts you don't have"."

It is "Fonts you have" now. The Show menu's default, "Fonts you don't have", then repeats the title. Its other two choices show what the title leaves out: under "Only fonts you have", the summary says "and only those are shown". Claude notes this for the owner's look in step 4; it changes nothing here.

### F4. The headings inside the section look like its title (O)

The owner: "Please make the headings under "Fonts you don't have" subheadings below the title in the hierarchy."

Measured at 1280 px, with two rows from the CSS:

| Text | Size | Weight | Colour |
|---|---|---|---|
| The panel's group titles: "Search fonts", "Category", "Features", "Hide fonts that come with", "Fonts you have" | 16 px | 600 | `--c-fg` |
| The hopper's subheadings: "Or paste the list", "How to make the list" | 16 px | 600 | `--c-fg` |
| "Show", once a list loads (`27-mine.css`, `.mine-result > label`) | 16 px | 600 | `--c-fg` |
| The lead | 14 px | 400 | `--c-muted` |
| "Make the list on a computer.", on phones (`27-mine.css`, `.mine-phone`) | 14 px | 600 | `--c-fg` |
| "Drop fonts.txt here", the systems' names | 16 px | 400 | `--c-fg` |

The subheadings are styled exactly like the title. The spacing doesn't separate them either:

- between the panel's groups: 16 px between Category, Features, "Hide fonts that come with" and the hopper; 24 px (`--space-5`) after Measure's line and before "Clear filters";
- between the hopper's own parts: 12 px (drop zone to "Or paste the list") and 16 px (Check to "How to make the list").

So one of the hopper's parts sits as far from the next as one filter group sits from another.

### Measured for step 2 (C)

Local builds of #100's head (`7e7ec1d`) and of step 2's pull request, in Chromium, with a five-name `fonts.txt` dropped by script on the list's first row, the page at its top. Tops are in px from the top of the window; at load, the hopper's title starts at 925 px at 1280 px wide in both builds, below a laptop screen's fold.

| Viewport | Box open | Build | Page scrolls | Title | Drop zone | Result | List's first row |
|---|---|---|---|---|---|---|---|
| 1280 × 800 | none | #100 | 460 | 465 | 812, out of view, "Drop fonts.txt here" | 585–800 | −74 |
| 1280 × 800 | none | step 2 | 867 | 58 | 344, the receipt | 588–800 | −481 |
| 1280 × 800 | Linux | #100 | 460 | 465 | 812, out of view | 585–800 | −74 |
| 1280 × 800 | Linux | step 2 | 1049 | −124 | 344, the receipt | 588–800 | −663 |
| 1280 × 1800 | none | #100 | 0 | 925 | 1272, moved down 227 | 1045–1260 | 386 |
| 1280 × 1800 | none | step 2 | 0 | 925 | 1211, unmoved, the receipt | 1455–1667 | 386 |
| 1280 × 1800 | Linux | #100 | 0 | 925 | 1272, moved down 227 | 1045–1260 | 386 |
| 1280 × 1800 | Linux | step 2 | 49 | 876 | 1344, the receipt | 1588–1800 | 337 |
| 768 × 1024 | none | #100 | 0 | 733 | 990, mostly out of view | 811–978 | below the panel |
| 768 × 1024 | none | step 2 | 361 | 372 | 616, the receipt | 860–1024 | below the panel |
| 375 × 812 | none | #100 | 270 | 448 | 824, out of view | 597–812 | below the panel |
| 375 × 812 | none | step 2 | 677 | 41 | 356, the receipt | 600–812 | below the panel |

- In #100's build the drop zone ends out of view at every size but 1280 × 1800, and where it shows, it still asks for a file (F2). In step 2's, the receipt and the result are in view together at every size.
- With a box open, "Make the list" is taller, so the title scrolls off at 1280 × 800; the receipt and the result stay in view.
- The page scrolls further in step 2's build, since the result now follows the steps, and on a laptop-height screen the list's first rows scroll out of view in both builds. S4 (fold "Make the list" once a list loads) and S6's shorter lead shorten it; S8 and S9 add height above the hopper, so step 3 measures again.
- F4's spacing for the hopper was wrong. The select over it keeps 12 px of margin under it inside its group: a select is inline-level, so that margin doesn't collapse. So in the sidebar the hopper's title has always sat 28 px under "Hide fonts that come with", not 16.
  - In the panel's grid below 60rem, the title sits 16 px under the tallest group in the row above, or 28 px where that group is "Hide fonts that come with". The 16 px case comes from 786 to 959 px, where the panel has three columns.
  - Step 2 keeps that, and #100's 24 px below. Adding the planned 24 px above would have made 36 px in the sidebar and 52 px in the grid, where margins don't collapse.
  - S7 and S9 set this spacing in step 3.

**And after step 3** (#107, `cfcbb90`; on Linux S1 opens the Linux box as the page starts, so "as loaded" has it open):

| Viewport | Boxes | Page scrolls | Title | Drop zone | Result |
|---|---|---|---|---|---|
| 1280 × 800 | as loaded | 916 | −1 | 285, the receipt | 533–745 |
| 1280 × 800 | all closed | 862 | 53 | 339, the receipt | 587–799 |
| 1280 × 1800 | either | 0 | 915 | 1201, the receipt | 1449–1661 |
| 768 × 1024 | as loaded | 429 | 286 | 551, the receipt | 799–963 |
| 375 × 812 | as loaded | 643 | 65 | 351, the receipt | 599–811 |

S7's group adds its title and the select above the hopper, and S4's fold takes back about as much once a list loads: with every box closed, 1280 × 800 is within 5 px of step 2's. The panel now runs 24 px between groups and ends at its own padding.

---

## Claude's suggestions

The owner: "I'd like you to suggest other changes to the filter bar based on UI design principles and psychophysics."

In the code, `.filters-bar` is only the row of search and Measure. These suggestions take "the filter bar" to mean the whole filter panel. Each names the principle it rests on, with the source where there is research behind it. Q2 picks which go into this work.

- **S1. The visitor's own system's commands open first.** The page reads the system in the browser (`navigator.userAgentData.platform` where there is one, else `navigator.platform`) and opens that `<details>`. Nothing is sent or stored, and nothing enters the address (M3-D13 a, amended). The others stay closed, and an unknown system opens none.
  - None opens where "Make the list on a computer." shows (the same media query as `.mine-phone`), nor for `MacIntel` with `navigator.maxTouchPoints > 1` (an iPad). Firefox on Android reports a `Linux` platform, and an iPad reports `MacIntel`.
  - It builds docs/milestone-3.md step 6's "preselected from the browser's platform". That item's "kept in the URL" was ruled out (M3-D13 a, amended).
  - *It saves a click and a scan of three labels (the keystroke-level model: Card, Moran and Newell 1980). Hick's law (Hick 1952; Hyman 1953) has choice time grow with the information in a choice, about log2(n + 1) bits for n equally likely options. A visitor who knows their system has little uncertainty to resolve, so the gain is small.*
- **S2. One filled button.** Choose file, Check, "Forget this list" and "Clear filters" all look the same. Until a list loads, Choose file, the hopper's main action, takes the filled lapis style of the Download buttons. After that no hopper button is filled, since the next action is the Show menu.
  - A visitor who has no list yet starts at "Make the list", and the fill can draw the eye past it to "Drop or paste it". The steps' numbers keep the order.
  - In forced colours the fill is dropped, as the Download link's is (`20-list.css`: Chromium's backplate hid its text). Choose file then keeps weight 600 and a 2 px `--c-border` border. It doesn't use `Highlight`, which marks focus there.
  - *Pop-out (Treisman and Gelade 1980): an item that differs in colour from its neighbours is found at once, however many there are. The isolation effect (von Restorff 1933): it is remembered better.*
- **S3. The whole drop zone opens the file chooser.** A click anywhere in the dashed box works, not only a click on the button. The button stays the only stop for the keyboard.
  - `Core.on` delegates through `closest()`, so the zone's handler ignores clicks from inside a button or input. Otherwise one click on Choose file would open two choosers, and `input.click()`'s own click would bubble back in.
  - *Fitts's law (Fitts 1954): a bigger target is quicker to hit.*
- **S4. "Make the list" folds once a list loads.** It shrinks to one disclosure, "How to make the list", so the result sits closer to the title. "Drop or paste it" stays open, so the receipt stays in sight. "Forget this list" opens it again.
  - If the fold hides the focused control (Copy, or a system's summary), the focus first moves to the Show menu, as `hide()` does.
  - *Progressive disclosure: show what the task needs now, and keep the rest one click away.*
- **S5. A changed result is marked.** The result box's first showing is already a sudden appearance, which captures attention (Yantis and Jonides 1984), and `bring()` scrolls to it. A later list, or a change of the Show menu, only rewrites the summary where it stands, and such a change can go unseen: change blindness (Rensink, O'Regan and Clark 1997).
  - Whenever the summary is written, the result box shows a tint that fades over about a second.
  - A change of colour draws attention less surely than an onset (Jonides and Yantis 1988), so the tint helps, but the summary's words carry the meaning.
  - Under reduced motion no transition runs (`test_reduced_motion_stops_every_transition`): the tint shows still for that second, then goes. In forced colours, which drop backgrounds, an outline stands in for it.
- **S6. A shorter lead.** "Fonts already on your computer are left out of the list. Your list stays in this tab: nothing is sent or saved." It keeps "left out of the list", which `site_status_line` requires and `test_the_hopper_s_markup` checks.
  - *Reading time grows with length, and in a 240 px column each extra line delays "Make the list".*
- **S7. One group per goal.** "Hide fonts that come with" joins "Fonts you don't have" as its quick option, which needs no file. Both leave out fonts the visitor already has, and AUTHORITY already calls it "the first, no-permission step" (`inventory_additions`).
  - A wrapper, `<fieldset id="f-have">` with the legend "Fonts you don't have", holds `#f-system`, then `fieldset#mine`.
  - "Hide fonts that come with" becomes a field label.
  - `fieldset#mine` keeps a legend of its own, visually hidden, "Your own list", which screen readers say as the group's name. The panel requires a legend first in every fieldset (`test_filters_ship_hidden_inside_search`). The lead goes inside `fieldset#mine`, so its "nothing is sent or saved" covers only the hopper, not the select, whose choice enters the address.
  - The select keeps its name, `hide`, and its hash key, so it stays outside `fieldset#mine`. `#mine` has no named control, as `test_the_hopper_s_markup` and `test_mine.py`'s `#mine [name]` check require.
  - Below 60rem, `27-mine.css`'s full-width rule (`grid-column: 1 / -1; max-width: 40rem`) moves from `.mine` to `#f-have`.
  - This amends `filters_layout`'s order, and `inventory_on_list_page` only in form: the two stay together.
  - *Proximity (Wertheimer 1923), with S9's hairline as a separator: the select and the hopper sit together, set off from the groups above, so they read as one group, and the panel has one group fewer to scan. Common region (Palmer 1992) would need a drawn border, which the panel's groups don't have.*
- **S8. "Clear filters" moves away from the hopper.** It sits right under the hopper's last line, so it reads as the hopper's own button, as the owner's screenshot shows. It becomes `#f-more`'s first child at every width, on its own line above Category, at the right.
  - The visual order, the order in the page and the keyboard's order then agree, and the phone row in `.filters-bar` (`mobile_first_screen_two_fonts`) is untouched.
  - It stays there whether or not a filter is on, so nothing moves under the pointer when one goes on.
  - This amends `filters_layout` (2026-09-30), which puts "Clear filters" last.
  - *Proximity (Wertheimer 1923): what is near belongs together.*
- **S9. One spacing rhythm.**
  - 4 px inside a control (a label to its box), 8 px inside a step, 16 px between the hopper's steps and 24 px between the panel's groups;
  - a hairline (`--c-divider`) above "Fonts you don't have".

  Each level of grouping then gets its own distance; today the hopper's parts and the panel's groups share 16 px (F4). *Proximity (Wertheimer 1923).*

---

## Questions for the owner

### Q1. How does the page show that it read a file? (F2)

*Answered 2026-11-01: (a), the receipt, Claude's recommendation.*

- **(a) A receipt in the drop zone** *(Claude's recommendation)*.
  - Once a file's list loads, the drop zone reads "File read: 4,332 font names." and its button "Choose another file". The tick before it is drawn with borders, as the chosen pills draw theirs: a "✓" character is outside the site's font subset, and a screen reader reads it as "check mark".
  - The count is the summary's own, so the page keeps nothing new, and section 11 stands. Naming the file ("fonts.txt read: …") is possible too, but then the page would keep the file's name, which can carry a person's or a computer's name, and section 11 would change.
  - A list that loads (a file or a paste) or "Forget this list" changes the receipt. A failed list leaves it, as it leaves the result: "a failed list leaves the last one as it was" (CONTRACT section 4).
  - It answers in the drop zone when the file was dropped there or chosen. A file dropped elsewhere on the page, the list included, gets the same answer once the page brings the hopper into view.
- **(b) Fill the paste box**, as the owner first described.
  - Once the parser accepts a file, its text goes into the paste box, and stays until "Forget this list" or another list loads. A pasted list stays too, and Check re-checks what the box holds.
  - Its gain: the box shows the first names read, so a wrong or garbled file shows at a glance, and the list can be edited and checked again.
  - Its cost: the page keeps the list's text while the tab is open. It is still never sent or saved. Sections 5 (the parser's members) and 11 change, and so do the tests that pin them.
  - Only the box's first three lines show, so a long list mostly reads as a scrollbar. The box can hold up to the parser's limit of 10,000,000 characters.
- **(c) Both.**

Whatever the answer, step 2 checks whether a browser's session restore writes the paste box's text to disk. That question exists today: a paste that is refused, or not yet checked, already stays in the box.

### Q2. Which suggestions go into this work?

*Answered 2026-11-01: (a), all nine, Claude's recommendation.*

- **(a) All nine, S1–S9** *(Claude's recommendation)*. They touch the same four files (`_filters.html.j2`, `27-mine.css`, `25-filters.css` and `site/js/55-mine.js`) and their tests, so one pass costs less than two.
- **(b) The hopper only, S1–S6.** S7–S9 wait, as a later item in docs/milestone-3.md.
- **(c) Only the owner's four points.** All nine wait.

### Q3. May staging show #100 before it merges?

*Answered 2026-11-01: (a), yes, while #100 is open, Claude's recommendation.*

`staging_previews_held_runs` says every staging deploy comes from the preview branch rebased on `main`. Staging's `a8ad402` is already rebased on #100's head instead, and that is how the owner saw the hopper. No ruling covers that.

- **(a) Yes, while #100 is open** *(Claude's recommendation)*: staging shows the preview rebased on #100's head, so the owner judges this work there before it merges.
- **(b) No:** staging goes back to `main`, and the hopper shows there only once #100 merges.

---

### Step 1: This plan and its rulings
**Who:** Claude records; the owner answers and merges. **Depends on:** nothing.
- [x] The owner answers Q1, Q2 and Q3. *(2026-11-01: (a) to each.)*
- [x] The rulings go in `data/reviews/site/2026-11-01.toml` (`tff-catalog rulings apply`) and AUTHORITY.md's **Inventory tools**:
  - `hopper_title_dont_have` (F3);
  - `hopper_steps_in_order` (F1: "Make the list", then "Drop or paste it", which holds the inputs, then the result);
  - `hopper_subheadings` (F4);
  - `hopper_file_shown` (Q1);
  - `hopper_layout_suggestions` (Q2);
  - `hopper_on_staging` (Q3), in `data/reviews/ci/2026-11-01.toml` beside `staging_previews_held_runs`, which it amends, with an *Amended 2026-11-01* note on **Held runs on staging (2026-10-19)** in AUTHORITY.md;
  - `hopper_layout_plan` (this file).
- [x] An *Amended 2026-11-01* note on AUTHORITY.md's **Filters (2026-09-30)** for S7 and S8, which Q2 took: "Hide fonts that come with" inside "Fonts you don't have", and "Clear filters" at the top of the panel.
- [x] Add `PLAN-hopper.md` to **Tracked docs** in AUTHORITY.md.
- [x] docs/milestone-3.md's PR 4 links here. Q2 took all nine suggestions, so none waits in a later item.

**Done when:** merged to `main`.

### Step 2: The owner's four points (F1–F4)
**Who:** Claude builds; the lead merges it into #100; the owner merges #100 to `main`. **Depends on:** step 1's Q1.
- **Branch:** in its own worktree, stacked on `claude/hopper-ui`, with that branch as the pull request's base, so it lands in #100. It is reviewed adversarially before it opens (docs/milestone-3.md, Slice 1). Nothing is pushed to #100 directly, and the lead is told when it's ready.

- [x] **Measure first,** at 1280 × 800 as well as 1280 × 1800, before and after the change:
  - where the hopper's title, the drop zone and the result start after a drop;
  - with no `<details>` open, and with one open.

  A laptop-height screen shows how far `bring()` scrolls the page, and whether the list's first rows scroll away. The numbers go in this plan, in a section after F4. *(Done 2026-11-01: "Measured for step 2", above, with 768 × 1024 and 375 × 812 too.)*
- [x] **The order** (`site/templates/_filters.html.j2`), top to bottom:
  - the title and the lead;
  - `<ol class="mine-steps">`, two items:
    1. "Make the list": the existing `p#mine-how`, its text changed, first in the first `<li>`. "Make the list on a computer." (phones), then `.mine-oses`, which keeps its `role="group"` and `aria-labelledby="mine-how"`;
    2. "Drop or paste it": a new `p#mine-give`, first in the second `<li>`, labelling a new `div.mine-give` with `role="group"`. That holds the drop zone, "Or paste the list", the paste box and Check;
  - "Checking your list…", the error and the result, after the list.

  Neither `<li>` takes a role, so the `<ol>` stays a list of two items, for the eye and for a screen reader. No existing id changes or goes, so `Mine`'s lookups and `hide()`'s focus moves work as they do.
- [x] **What a list brings into view:** `bring()` shows the drop zone and the result together where they fit, the result first where they don't. *(It measures what shows of the window, the visual viewport, so a phone zoomed in or with its keyboard up counts as shorter.)*
- [x] **The title:** "Fonts you don't have".
- [x] **The hierarchy** (`site/css/27-mine.css`):
  - the title keeps the panel's group-title style (16 px, 600, `25-filters.css`), so it stays a peer of Category and Features;
  - step headings: 14 px (`--fs-small`), 600, under the list's indent, with the number as a styled `::marker`. The size is 12.5% smaller, easy to miss alone, so the indent and the number carry the step down with it;
  - field labels, "Or paste the list" and "Show": 14 px, 400. `.mine > label` no longer reaches the paste label inside the `<ol>`, so its rule moves with it;
  - notes: the lead as now; "Make the list on a computer." goes to 14 px, 400, so it doesn't read as a second heading under "Make the list";
  - spacing: 8 px inside a step, 12 px between the two steps, 24 px above and below the section. With the panel's groups 16 px apart, the steps then sit closer than the groups. S9, if chosen, sets the whole rhythm. *(Built with #100's 28 px above, not 24: the select over the hopper keeps a 12 px margin that can't collapse, so 24 more made 36 px in the sidebar and 52 px in the grid. See "Measured for step 2".)*
- [x] **The receipt** (Q1 (a)): `#mine-drop` gets a receipt line and the class `is-read` once a list loads from a file. The receipt and the button change as Q1 (a) says, with the text set by `Core.text`, and change back as it says. A matched paste still leaves the box.
- [x] **The session-restore check,** since a paste that is refused or not yet checked already stays in the box: does Firefox's or Chromium's session restore write a `textarea`'s text to disk when it has `autocomplete="off"`? Record the finding in docs/owned-fonts.md. If either does, M3-D13 a ("nothing is stored") is at stake today; bring it to the owner before anything else. *(Done 2026-11-01: neither Firefox nor Chromium writes it, nor the Show menu's choice, read in their main branches; recorded in docs/owned-fonts.md. WebKit does keep both: see the WebKit item below, brought to the owner the same day.)*
- [x] **Records:**
  - `site/CONTRACT.md`: section 3 (the `mine_commands` row); section 4 (`#mine`'s title, its order, the step headings, the result after the steps, the receipt). Sections 5 and 11 stand: the receipt's count is one the page already keeps;
  - the comments in `_filters.html.j2`, `27-mine.css`, `55-mine.js`, `src/tff_site/data.py` and `src/tff_site/build.py`, docs/owned-fonts.md and the #100 description, wherever they say "Fonts you have", "How to make the list" or the old order.

  *(All done in #104 but #100's description, which is the lead's: #104's description drafts the changes. #100's description has them since 2026-11-03: its order is the lead, "Make the list", "Drop or paste it", then the result, and "Fonts you have" is gone.)*
- [x] **Tests:**
  - `tests/site/test_list_markup.py::test_the_hopper_s_markup`: the title; `ol.mine-steps` with its two headings and groups; busy, error and result after the list;
  - `tests/site/test_mine.py`: the two empty-box assertions stay. A new test checks the receipt after a drop on the zone, a drop on the list and a choice, and that a paste that loads puts back "Drop fonts.txt here". It also checks that the receipt stays after a failed list and goes after "Forget this list";
  - `tests/site/test_a11y.py`: axe on the new structure, in both themes and in forced colours.

  *(Built with more besides, from the reviews before #104 opened: bring()'s drop-zone scroll with the page scrolled past the hopper and in a window too short for both; the levels of text, computed; the receipt in the fit check at every width; the tick in forced colours.)*
- [x] **WebKit** (the owner, 2026-11-01: WebKit tests, definitely): the browser suites in Playwright's WebKit 26.6, run locally, since CI has Chromium and Firefox only. *(On #104: 492 passed, 14 failed. The same 14 fail on #100's head, so none comes from step 2, and every test step 2 adds or changes passes. By 2026-11-02 all 14 were fixed, and the suites pass: below.)*
  - [x] Triage (2026-11-01: five investigators and a skeptic each; probes under the session's scratchpad):
    - **Harness, 7:** Playwright's WebKit screenshots insert `<style>body {}</style>`, which the site's CSP blocks (6 in `test_a11y.py`; 6 more in `test_specimens_loader.py`, not run); WebKit's "Blocked by Web Inspector" isn't in a regex (1 in `test_privacy.py`). No visitor sees either.
    - **The list index downloaded twice, 5:** WebKit never lets the script's plain `fetch()` reuse the `crossorigin` preload, so the index is fetched twice: every time in Private Browsing, and on slow connections. Fix: a preload and a fetch of the same kind (no `crossorigin`; `no-cors`, credentials `include`).
    - **Back gives back the hopper's controls, 1:** WebKit (Safari's engine) ignores `autocomplete="off"` on a `textarea` or `select` unless their `<form>` has it. A pasted list not yet matched, or one refused, comes back in the paste box on a Back that loads the page again, and sits in WebKit's session history. Against CONTRACT sections 4 and 11 and M3-D13 a.
    - **WebKit's cap on history writes, 1:** 100 in 10 s; on the next, `State.set` has already changed its state but notifies nothing. Low; holding Enter on a sort button reaches it.
  - [x] The owner rules on the hopper's controls in WebKit (above). *(2026-11-01: a `<form autocomplete="off">` that never submits, Claude's recommendation over emptying both controls as the page starts, `hopper_form_autocomplete_off`.)*
  - [x] The form, built in #104, with a test that Back gives back neither control. *(`4d69a5e`. In WebKit the Back test passes, and fails without the form's `autocomplete="off"`; a probe found the paste box empty after Back in all three engines. The layout measures the same.)*
  - [x] The other fixes, and the suites passing in WebKit: a task of their own. *(2026-11-02:*
    - *#106 (`e78e1f3`, on `main`) fixed the index fetched twice, the cap on history writes and the harness's WebKit limits.*
    - *#108 (`33844d7`) brought `main` into #100 and fixed 9 `test_mine.py` tests that hold the index back: in WebKit a held index holds the load event, so their `goto` waits for DOMContentLoaded.*
    - *On #107 merged with #100's head (`00d8ff3`, local), the six suites in "Done when" pass in WebKit, Chromium and Firefox, and so do the fast suite and ruff.*
    - *CI's whole browser set (2,080 tests) has 4 more failures in WebKit, all outside those six suites: `test_details.py`, `test_keys_js.py`, `test_owned_js.py` and `test_ui_font.py`. They fail on `main` too, every time, so they're a task of their own.)*

**Done when:**
- the hopper reads "Make the list", then "Drop or paste it", then the result;
- after a file is chosen or dropped anywhere on the page, the drop zone shows its receipt, in view with the result;
- the title reads "Fonts you don't have", at 16 px, 600; the step headings are 14 px, 600, numbered and indented under it; and the field labels are 14 px, 400;
- the fast suite, `ruff check`, `ruff format --check` and the browser suites pass: `test_list_markup.py`, `test_mine.py`, `test_list.py`, `test_a11y.py`, `test_privacy.py` and `test_perf.py`, one process per browser;
- the owner gets screenshots at 1280 × 800, 768 × 1024 and 375 × 812, light and dark, including one after a drop that shows where `bring()` leaves the page;
- CI passes on the pull request (`gh pr checks`).

### Step 3: Claude's suggestions, S1–S9 (Q2: all nine)
**Who:** Claude builds, in the same pull request as step 2 (in #107 instead, since #104 had merged into #100 by then); the lead merges it into #100. **Depends on:** step 1's Q2; step 2.
- [x] S1: the visitor's system's `<details>` opens on load; none on phones, tablets or an iPad. Tests:
  - one per system, one for an unknown system, one each for Firefox on an Android phone and Safari on an iPad;
  - CI runs on Linux, where the Linux box would open, so `test_the_hopper_works_by_keyboard`, `test_copy_puts_each_command_on_the_clipboard_exactly` and `test_nothing_from_a_list_reaches_the_address_history_title_or_storage` set an unknown platform by init script before they use a summary;
  - the server's HTML keeps every box closed (`test_the_hopper_s_markup`), and CONTRACT section 4 says which box opens;
  - nothing new in the address, storage or requests (`test_privacy.py`);
  - tick docs/milestone-3.md step 6's "preselected" part, noting that "kept in the URL" was ruled out. *(A note, not a tick: that item is the old hidden page's and covers more.)*
- [x] S2: Choose file filled until a list loads; contrast checked in both themes; weight 600 and a 2 px `--c-border` border in forced colours.
- [x] S3: a click in the drop zone opens the chooser, and clicks from inside a button or input are left to them. A test counts `filechooser` events: one for a click on Choose file, one for a click on the zone's text. The zone is no extra stop for the keyboard.
- [x] S4: "Make the list" folds once a list loads and opens again on "Forget this list"; "Drop or paste it" stays. The focus moves to the Show menu if the fold would hide it. Tests that use a system's summary after a list loads open the fold first, and a test checks the focus after Copy, then a drop.
- [x] S5: the tint whenever the summary is written: a first list, a later one, a change of the Show menu. It shows still under reduced motion, and as an outline in forced colours, with no layout shift. Tests check it after the fade, so axe never reads the summary mid-fade, and `test_reduced_motion_stops_every_transition` loads a list.
- [x] S6: the shorter lead; `test_the_hopper_s_markup`'s "left out of the list" check still passes.
- [x] S7: `fieldset#f-have` with the title, `#f-system` and `fieldset#mine`, as S7 says. These follow: `test_filters_ship_hidden_inside_search`, `test_the_hopper_s_markup`'s legend and order checks (`order[-3:]`), `test_the_hopper_fits_its_panel_at_every_width`, and CONTRACT section 4's sketch and "after `#f-system`".
- [x] S8: `#f-clear` first in `#f-more`, with `25-filters.css`'s `#f-clear` rules. These follow: `test_the_hopper_s_markup`'s order check, `test_list.py`'s "Clear filters" tests, and CONTRACT section 4's sketch and "before `#f-clear`".
- [x] S9: the spacing rhythm and the hairline; the measurements in step 2 taken again. *("And after step 3", under "Measured for step 2".)*
- [x] Records and tests as in step 2, for each item built. *(2026-11-02, #107, `cfcbb90`: tests for each item, a review workflow's 13 findings fixed, among them a forced-colours hover that hid Choose file's label; Chromium 441 and Firefox 527 passed, WebKit too but for 9 tests that fail on #100 merged with `main` alone. #108 fixed those 9. On #107 merged with #100's head, step 2's six suites pass in all three engines; see "WebKit" in step 2.)*

**Done when:** each chosen item is built and tested, and step 2's checks pass again.

### Step 4: The owner looks on staging
**Who:** the lead deploys; the owner looks. **Depends on:** steps 2 and 3 merged into #100; Q3.
- [x] The lead rebases `preview/2026-10-17` on #100's new head (`hopper_on_staging`) and deploys it with `ops/deploy.sh staging`. *(2026-11-03: `ba2f627`, the four October commits on #100's `28aae7c`, which adds the previews' fix: once a list has loaded, Chromium no longer fetches previews again (the a8ad402 deploy's live privacy test had failed on that). The deploy passed: 4,951 site tests, then the live tests.)*
- [ ] The owner drops, chooses and pastes a real list in Chromium and Firefox, and rules on the screens, F3's Show menu included. Further findings go here as F5, F6, …
  *(Partly done. 2026-11-03, on `ba2f627`: previews stayed off after a list until a reload, ruled `hopper_previews_all_at_once`. 2026-11-05, on `f78889f`: "I noticed nothing that irked me", with two rulings, `hopper_clear_filters_resets_show` and `hopper_auto_open_and_drop_ring` (`data/reviews/site/2026-11-05.toml`), and the owner tried only part of it. Not yet recorded: drop, choose and paste each in both browsers, and F3's Show menu.)*

**Done when:** the owner has looked, and any new findings are recorded.
