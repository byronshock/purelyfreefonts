# Milestone 3 checklist: owned-font tools

Milestone 3 builds the tools that find the fonts a visitor already has and take them out of the list. They are built and tested on a hidden page beside Milestone 2's live list; Milestone 4 turns them on for everyone (release 2 in [AUTHORITY.md](../AUTHORITY.md#releases)). A visitor pastes the output of a one-line, read-only command (Linux, macOS or Windows) or, in desktop Chromium, presses "Check my fonts", which calls `queryLocalFonts()`. Both run entirely in the page, and font lists never leave the browser. The commands, formats and research are in [owned-fonts.md](owned-fonts.md), which step 1 creates.

**How to read each step:**

- **Who:** the owner, Claude, or both.
- **Depends on:** the steps that must finish first. M1 and M2 steps are in [milestone-1.md](milestone-1.md) and [milestone-2.md](milestone-2.md), bare D1–D17 in [ranking-methodology.md](ranking-methodology.md), and M3-D1 to M3-D14 under [Decisions](#decisions).
- **Done when:** what must be true before the step is ticked.
- **Parallel:** where Claude can run several agents at once.

Tick each item as soon as it is done and verified. If an item is only partly done, leave it unticked and note what's left.

**Milestone 3 is done when:**
- the hidden page is live, and paste (Linux, macOS, Windows) and the Chromium button work on the corpus and in the usability sessions;
- no corpus font is wrongly counted as owned, and the accuracy targets are met;
- the privacy test passes in CI and on the live site;
- both usability rounds are done, with their blocking problems fixed;
- the monthly refresh rebuilds the match file and guards accuracy;
- the owner has accepted a handoff that says how Milestone 4 joins the check to the list.

**Critical path:** 0 → 1 → 4 → 5 → 6 → 8 → 9 → 10 → 11 → 14, with step 3 done before step 10.
Step 3 waits on M1 steps 7 and 15, so steps 5–9 use a stand-in table until then; steps 2, 7, 12 and 13 run alongside, as their Depends on lines say.

---

### Step 0: Owner reviews this checklist and decides
**Who:** owner; Claude answers questions and makes edits. **Depends on:** nothing.
- [ ] Claude opens a pull request adding this checklist.
- [ ] The owner answers the **[Step 0]** decisions M3-D2 to M3-D12 (step 1's results answer M3-D1) and changes any **[later OK]** default (M3-D13, M3-D14) they disagree with.
- [ ] The owner says who runs commands on a Mac and a Windows PC for steps 1, 2 and 11: their own machines, volunteers, or a Windows evaluation VM.
- [ ] Claude records each answer in AUTHORITY.md with its date, and adds `docs/milestone-3.md` and `docs/owned-fonts.md` (created in step 1) to "Tracked docs".
- [ ] The owner merges the pull request.

**Done when:** every [Step 0] decision but M3-D1 is answered, and AUTHORITY.md and this checklist are merged to `main`.

### Step 1: Test the commands on real machines
**Who:** Claude writes the kit and the doc; the owner or a volunteer runs the kit. **Depends on:** 0.
- [ ] Claude creates `docs/owned-fonts.md` from the research notes of 2026-09-25, with their open questions.
- [ ] A test kit runs each candidate command, records its time, lines and bytes, and saves the output, on: 2 or more Linux distributions (one with an older fontconfig, such as Debian 12); the current and previous macOS (one Mac with many fonts); Windows 11 and 10 22H2, in PowerShell 5.1 and 7. The runs answer every open question in the doc.
- [ ] Per OS, the doc records the command, an anonymized sample, which name it returns, and its size, time and pitfalls.
- [ ] The owner decides **M3-D1** from the results.

**Done when:** every candidate has run on every OS listed, the doc is merged, and M3-D1 is in AUTHORITY.md.

**Parallel:** one agent per OS.

### Step 2: Test corpus, collected with consent
**Who:** the owner collects lists; Claude writes the consent note, capture kit and anonymizer. **Depends on:** 1; M3-D2; D15, D17.
- [ ] A plain-words consent note: what is collected and removed, where and how long it is kept, that it is used only to test matching, and how to withdraw. Consent records (date, handle, OS) stay with the corpus, outside the public repository.
- [ ] A capture kit (the chosen command, step 1's richer alternatives, and a page that saves the `queryLocalFonts()` result) that writes to disk; the contributor decides whether to send.
- [ ] An anonymizer that removes paths, usernames and host names and flags words the contributor lists, such as their name or employer. The contributor reviews the result before sending.
- [ ] Lists collected as M3-D2 sets (default: 5 or more per OS, across versions and distributions), including a developer machine with Nerd Fonts, a designer machine with many fonts, and 3 or more button captures per OS.
- [ ] Ground truth: each catalog family labelled present or absent in each list, from that machine's richest capture. Claude proposes; the owner reviews labels where captures disagree.
- [ ] Public fixtures are synthetic only, built from published OS font lists and step 5's hard cases. CI fails if a fixture contains a home-directory path.

**Done when:** the target counts are met with consent recorded, every list is labelled, and the public fixtures are on `main`.

### Step 3: Match data from the catalog pipeline
**Who:** Claude; the owner reviews flagged collisions. **Depends on:** M1 step 7 (alias table) and step 15 (`catalog-site.json`, and `names.json` with every eligible family's names and aliases); D2, D4. Until then, a hand-built stand-in (the old Top 100 plus Nerd Fonts' `fonts.json`) is used.
- [ ] The export stage also writes a versioned `build/match-site.json`, published under a content-hashed name, with:
  - every catalog family's names, and each alias with its relation: rename; build (Nerd Font, NF, NFM, NFP, Propo, Powerline, NL, CJK); PostScript prefix; related; folded sibling (D2);
  - the listed-only families (M1 step 15b), matched like catalog families, so owned ones leave the A–Z list too (owner ruling `more_fonts` of 2026-09-29);
  - eligible families that are held back, as "known, not listed", so they never raise a near-match;
  - ineligible names with their reason (proprietary, ITF, CJK, icon, generic, system, not a font, non-Latin, unlisted);
  - strippable style words (weights, slopes, SemBd, Med, Ret, Obl …) and Nerd suffixes;
  - an "ambiguous" set: names that are both a family and another family's weight (so far only Archivo Black).
- [ ] A test-vector file shared by the Python and JavaScript suites defines `match_key` (NFKC, case-fold, drop spaces, hyphens and underscores; accents kept) and `search_key` (`match_key` with accents stripped), which M2's search uses (M2 step 3).
- [ ] CI fails if, outside the ambiguous set, two families share a `match_key` or stripping style words from a catalog name yields another family's key, or if the file exceeds its budget (measure first; aim for 50 KB compressed).
- [ ] The schema goes in `docs/catalog-schema.md`.

**Done when:** the file validates in CI on the real run, and these pass: Source Sans Pro → Source Sans 3 (rename); FiraCode Nerd Font → Fira Code, SauceCodePro Nerd Font → Source Code Pro (build); Adwaita Sans → Inter (related, never owned); Roboto Slab ≠ Roboto; Archivo Black is ambiguous.

### Step 4: One parser per format, in the browser
**Who:** Claude. **Depends on:** 1; uses step 2's fixtures as they arrive.
- [ ] One dependency-free ES module that never touches the network. It turns pasted or dropped text into entries (family names, plus full name, PostScript name and style where present) and warnings.
- [ ] It detects every format in `docs/owned-fonts.md`: `fc-list : family` (one line per distinct set of family names, not per face; escaped, comma-separated), plain `fc-list` (paths dropped at once), one name per line, `system_profiler` text and JSON, Windows registry value names, and step 2's capture file.
- [ ] It copes with CRLF, UTF-8 and UTF-16 byte-order marks, PowerShell table headers, hidden macOS names (leading "."), and 10 MB of input.
- [ ] Friendly messages for a pasted command instead of its output, "command not found" or "is not recognized", and an empty paste; a warning when a sorted list looks cut off at the top by terminal scrollback.

**Done when:** every fixture parses to its expected number of entries; fuzzing never makes it throw; and 5 MB of `system_profiler -json` parses in under 1 second on a mid-range laptop.

**Parallel:** one agent per OS format.

### Step 5: Name matching
**Who:** Claude; the owner reviews the near-match rules. **Depends on:** 3 (the stand-in until then), 4; M3-D3 to M3-D5.
- [ ] Per entry, names are tried in order: the first name (fontconfig's WWS or typographic family, or the macOS family), the other family names, the full name, the PostScript name's family part. Each is looked up exactly by `match_key`; on a miss, trailing style words are stripped one at a time. The longest exact hit wins; nothing matches by prefix.
- [ ] Each catalog and listed-only family ends up:
  - **owned:** an exact family hit, or a rename or build alias counted by M3-D3 and M3-D4, labelled with the name matched ("as FiraCode Nerd Font");
  - **near-match:** a related or folded-sibling hit (M3-D5), an ambiguous name without confirming evidence, a known family plus an unknown remainder, or a close spelling (edit distance ≤ 2, 6+ characters) that isn't a known name;
  - **missing.**
- [ ] A near-match never counts as owned. "Archivo Black" is owned only as an entry's first name, a macOS family, or a PostScript name starting `ArchivoBlack-`.
- [ ] A public known-answer fixture, which Milestone 4's final check reuses: Inter Variable and Inter Display → Inter; Fira Code Retina → Fira Code; Source Sans 3 ExtraLight → Source Sans 3; Playfair Display Bold → Playfair Display, not Playfair; JetBrains Mono NL, Cascadia Code PL and NF → their parents, as builds; Inter Tight ≠ Inter; Noto Sans JP ≠ Noto Sans; Roboto Condensed is its own family; with static Archivo installed, Archivo is owned and Archivo Black is not.
- [ ] Output is deterministic, and 10,000 names match in under 200 ms.

**Done when:** the known answers pass, and a corpus run writes the first mismatch report for step 10.

### Step 6: The paste flow on the hidden page
**Who:** Claude; the owner reviews the wording. **Depends on:** 4, 5; M3-D6 to M3-D8; M2's list page and deploy path (M2-D6).
- [ ] The hidden page where M3-D6 puts it (default `/check/`): unlinked, with a `noindex` meta tag and a "beta" label.
- [ ] `ops/Caddyfile` headers for that path, by reviewed pull request, so step 7 works on the live site: `Permissions-Policy` with `local-fonts=(self)`, keeping M2's other settings, and `X-Robots-Tag: noindex`. M2's CSP is unchanged.
- [ ] The check is one module the list page can also load, so either option of M3-D8 needs no rewrite. The match file loads as M3-D7 says.
- [ ] Linux, macOS and Windows tabs, preselected from the browser's platform and kept in the URL after `#`. Each shows the command as plain text, a copy button that copies exactly that text, what it does ("lists your font names; changes nothing; sends nothing") and how long it takes.
- [ ] Input by paste, "Choose file" or drag-and-drop, read with FileReader; then a summary ("Found 4,332 font names in a Linux list"), the parser's warnings, and a "Clear" button that forgets the list.
- [ ] Phones and tablets get a note that the check needs a computer.
- [ ] Labelled controls, full keyboard use, and the summary announced in an aria-live region.
- [ ] "How the check works" on the methodology page (M2 step 7), from `docs/owned-fonts.md`.

**Done when:** browser tests take every public fixture from paste to results in Chromium, Firefox and WebKit; the copied text equals the visible command; axe finds no serious issue; and `curl -sI` on the path shows both headers.

### Step 7: The "Check my fonts" button (Chromium)
**Who:** Claude; the owner tests on their machines. **Depends on:** 5, 6.
- [ ] Shown only when `'queryLocalFonts' in window` on a secure, top-level desktop page, placed as M3-D14 says, after one line: Chrome will ask to let this site see your fonts; the list stays in this tab.
- [ ] `queryLocalFonts()` is called directly in the click handler, with no `postscriptNames` filter and never `blob()`. Under M3-D7's default, the match file is fetched alongside.
- [ ] Matching uses `family`, `fullName` and `postscriptName` together.
- [ ] The permission state comes from `navigator.permissions.query`, in try/catch. Both errors fall back to paste with a one-line reason: `SecurityError` (a site header, or no click) and `NotAllowedError` (denied by the visitor or their organisation). Under 30 fonts suggests trying paste.
- [ ] Tested in Chrome and Edge on all three OSes; in Brave, which randomizes font lists (suggest paste if unreliable); in a Flatpak or Snap Chromium; and under Chrome's `LocalFontsBlockedForUrls` policy, noting which error it gives.
- [ ] No font probing (`document.fonts.check`, canvas measuring): it is a fingerprinting technique.

**Done when:** the button works in Chrome and Edge on all three OSes; after a denial, paste is one click away; and the Brave, Flatpak/Snap and policy findings are in `docs/owned-fonts.md`.

### Step 8: Results view
**Who:** Claude; the owner reviews. **Depends on:** 5, 6; M3-D9, M3-D10, M3-D13; M2's filters and numbering (M2-D2); D6, D13.
- [ ] Owned fonts drop out of every published rank and view, combined with the other filters (including "No credit required", which replaced "Redistributable fonts only" on 2026-09-30).
- [ ] Rank numbers stay as published; a line reads, for example, "You have 41 of the top 100 in this view".
- [ ] A "Show fonts I have" toggle, off by default, shows owned fonts dimmed, each saying how it matched.
- [ ] Near-matches are shown as M3-D9 says. Each near-match, and each owned font under "Show fonts I have", has a "Wrong match?" link.
- [ ] `wrong-match.yml` joins M2's issue forms (M2 step 12), with its own labels. The link prefills the font, OS, paste or button, and data date, plus the installed name only if the visitor ticks a box beside it. Nothing else from the list is sent, and the form says reports are public.
- [ ] A collapsed line counts the visitor's fonts not in this list, each with its reason: not Latin, license, system font, or not in the catalog.
- [ ] Specimens follow M3-D10 (by default, none is requested once a list is loaded).
- [ ] Storage follows M3-D13 (default: nothing). If a stored option is chosen, `/privacy` (M2 step 7) names each key, and M2's privacy test (M2 step 9) switches to an allowlist of keys.

**Done when:** browser tests cover every view, with the toggle on and off and each filter; after matching, a test scrolls every view and asserts zero network requests; a test shows the "Wrong match?" URL carries the installed name only when the box is ticked; and the owner approves the screens.

### Step 9: Privacy verification
**Who:** Claude. **Depends on:** 6–8; M2 step 9. Must pass on the live site before step 11.
- [ ] A static check: the check's modules have no `fetch`, `XMLHttpRequest`, `sendBeacon`, `WebSocket`, `EventSource`, dynamic `import()` or element `src` assignment, except one fetch of the match file's fixed URL under M3-D7's default.
- [ ] M2's privacy test gains a case, in Chromium, Firefox and WebKit: once a list is loaded, matching every fixture, the stubbed button, switching views and filters, scrolling every view and clearing the list make no request, except:
  - under M3-D7's default, exactly one same-origin request for the hashed match file as the comparison starts, with no query string or body, and the same URL for every list;
  - after a click on "Load previews" or "Type your own text" (M2-D5), or on a report, download or tip link.
- [ ] It also checks that the check works offline once the match file has loaded, sets no cookie, and stores nothing M3-D13 doesn't allow.
- [ ] After each deploy it runs on the live site through Cloudflare, re-verifying M2's headers and Cloudflare privacy settings and step 6's headers.
- [ ] `/privacy` gains a section on the check, linked from the check page: the list stays in the page; what is requested, and when (M3-D7, M3-D10); and how to confirm it in the network panel, or by going offline once the check is ready.

**Done when:** the privacy test passes in all three engines, in CI and on the live site.

### Step 10: Accuracy testing
**Who:** Claude; the owner reviews mismatches. **Depends on:** 2, 3, 5; 7 for paste versus button.
- [ ] Measured against M3-D11, per OS and overall: fonts wrongly counted as owned; share of installed catalog families found; near-matches per list; paste-button agreement per machine.
- [ ] `build/match-review.md` lists every mismatch, naming catalog fonts only. Each is fixed by an alias row (through Milestone 1's review), a style word, a parser fix or an owner ruling, and the test reruns.
- [ ] The totals report goes into `docs/owned-fonts.md`; raw lists stay private.

**Done when:** M3-D11's targets are met on the whole corpus after the fixes, with no font wrongly counted as owned.

**Parallel:** one agent per OS for the mismatch review.

### Step 11: Usability testing
**Who:** the owner runs the sessions; Claude writes the tasks and summarizes. **Depends on:** 9 (on the live site), 10; M2's protocol (`docs/usability-test.md`, M2 step 12).
- [ ] The check's tasks are added to `docs/usability-test.md`.
- [ ] Round 1: 5 or more people, covering paste on all three OSes and the button; 2 or more aren't developers. It measures finishing unaided (target 4 of 5), minutes to results (target under 3), whether they can say what the command does and that nothing was uploaded, whether they read a near-match correctly, and whether they find "Show fonts I have". It settles M3-D14.
- [ ] Round 1's findings are fixed; round 2 runs with 3 or more new people.
- [ ] No font list is collected unless the participant opts into the corpus.

**Done when:** round 2 finds no blocking problem and the owner signs off.

### Step 12: Monthly refresh
**Who:** Claude. **Depends on:** 3, 10; M1 steps 18–19.
- [ ] The refresh pull request regenerates the match file and runs the known answers and public fixtures; a drop in accuracy fails it.
- [ ] `review.md` lists new aliases that change what counts as owned, such as new Nerd builds and renames.
- [ ] The private corpus reruns before the owner merges: in the private repository's CI if D15 is (a), otherwise locally.

**Done when:** a manually dispatched refresh pull request includes the match file and passes these checks.

### Step 13: Small CLI (only if M3-D12 is (b) or (c))
**Who:** Claude. **Depends on:** 5, 10.
- [ ] A CLI with M3-D12's scope that prints the paste command's list, or the missing fonts, and sends no telemetry.

**Done when:** it runs on all three OSes and meets M3-D11's targets.

### Step 14: Handoff to Milestone 4
**Who:** both. **Depends on:** 11, 12, and 13 if it runs.
- [ ] Check "Milestone 3 is done when" item by item, and confirm every completed item here is ticked.
- [ ] AUTHORITY.md records the M3 decisions with dates; `docs/owned-fonts.md` is final.
- [ ] A handoff note gives Milestone 4 step 8 its build items under M3-D8: the check on the main page (or a link to it); `noindex` removed; a 301 from `/check/` if it merges; `local-fonts=(self)` where the check goes live; the updated `/privacy`; a privacy-test rerun. It also covers the launch wording, who watches wrong-match reports, and, under M3-D7's default, the match file's path for Milestone 4's usage counts.
- [ ] Claude updates the status lines in `docs/roadmap.md`; "Current step" in AUTHORITY.md moves to Milestone 4.

**Done when:** the owner accepts the handoff.

---

## Decisions

Defaults are in bold. A **[Step 0]** decision is answered before building starts; a **[later OK]** one starts from its default and can change later.

| Decision | Options |
|---|---|
| **M3-D1 [Step 0, answered in step 1]: the command for each OS.** The parser accepts every option; trade-offs are in [owned-fonts.md](owned-fonts.md). | **Linux: (a) `fc-list : family \| sort > ~/fonts.txt`**, then choose or drop the file (`\| wl-copy` or `\| xclip -selection clipboard` shown as alternatives); its lines are already unique, so `sort` only orders them; (b) the same, copied from the terminal; (c) `fc-list --format '%{[]family{%{family}\n}}' \| sort -u`, one name per line. **macOS: (a) `system_profiler SPFontsDataType \| grep 'Family:' \| sort -u \| pbcopy`**; (b) the `osascript` AppKit one-liner: fast, but it may trip macOS 26.4's paste warning; (c) `system_profiler -json SPFontsDataType \| pbcopy`; switch to (b) only if (a) takes over a minute on a Mac with many fonts and (b) trips no warning. **Windows: (a) the registry value names from HKLM and HKCU, piped to `Set-Clipboard`**; (b) WPF `SystemFontFamilies`; (c) GDI+ `InstalledFontCollection`. |
| **M3-D2 [Step 0]: the corpus** | **(a) The owner's machines plus invited volunteers with recorded consent, 5 or more lists per OS**; raw lists stay private, in the private data repository if D15 is (a), otherwise on the owner's machine; (b) as (a), also publishing consenting contributors' lists, cut to names in our universe, under the data license (D17); (c) only the owner's machines and VMs (macOS needs Apple hardware). |
| **M3-D3 [Step 0]: old names.** Does Source Sans Pro count as having Source Sans 3? | **(a) Yes**, labelled "you have it under its older name; a newer version exists"; (b) only as a near-match; (c) no. |
| **M3-D4 [Step 0]: patched builds.** Do Nerd Font, NF, Powerline, no-ligature (NL) and CJK builds, such as FiraCode Nerd Font or Maple Mono NF CN, count as the original? This is separate from D7, which sets ranking credit. | **(a) Yes**, labelled "as a patched build", with a "Count patched builds as mine" switch that starts on; (b) only as a near-match; (c) no. |
| **M3-D5 [Step 0]: derived and folded families**, such as Adwaita Sans (from Inter) or, if D2 folds width siblings, Barlow Condensed | **(a) Never owned**; the parent stays listed with a note: "You have Adwaita Sans, which is based on Inter."; (b) owned; (c) ignored. |
| **M3-D6 [Step 0]: access before the full release** | **(a) An unlinked `/check/` page with `noindex`**, shared with testers; its path is public, so it is unlisted, not secret, and the live list is unchanged until Milestone 4; (b) a flag on the live list (`?check=1`), so the list page needs `local-fonts=(self)`; (c) a beta host: Milestone 2's test site (M2-D7) or a new subdomain. |
| **M3-D7 [Step 0]: loading the match data** (moved from Milestone 4) | **(a) Only when a comparison starts** (the visitor focuses the paste box, chooses a file or presses the button), from a fixed hashed URL carrying nothing from the list; Milestone 4 step 5 counts its downloads as comparisons started, and `/privacy` names the request; (b) with the page: only page loads are counted, and no request is allowed once a list is loaded. |
| **M3-D8 [Step 0]: how the check joins the list** at the full release (Milestone 4 step 8 builds it) | **(a) Merge into the main page**: the check sits above the list and filters it in place, `/check/` redirects (301) to `/`, and the main page gains `local-fonts=(self)`; (b) keep `/check/` as its own page, linked from the list: the main page stays as Milestone 2 left it, but two list pages must stay in step. |
| **M3-D9 [Step 0]: how near-matches are shown.** A near-match never counts as owned on its own. | **(a) The font stays listed with a "You may have this" badge** naming the installed font and the reason, and an "I have it" button that hides it for this visit; a count at the top gives the number of near-matches; (b) a separate "Check these" group above the list; (c) both. |
| **M3-D10 [Step 0]: previews after a comparison.** Once owned fonts are hidden, specimens loading on scroll (M2 step 5) would tell our server and Cloudflare which fonts the visitor lacks. | **(a) Once a list is loaded, no new specimen is requested** until the page is reloaded; unloaded specimens show fallback text and a "Load previews (reveals which fonts you're viewing)" button; (b) when a comparison starts, load every view's top-100 specimens before matching, whatever the list holds: no leak, but heavier on phones; (c) keep loading on scroll, and say on `/privacy` what that reveals. |
| **M3-D11 [Step 0]: accuracy targets** on the step 2 corpus | **(a) No font wrongly counted as owned** (each case is a bug to fix before launch); 98% or more of installed catalog families found, with every miss that leaves a name clue shown as a near-match; a median of 3 or fewer near-matches per list; 99% or more paste-button agreement on the same machine; (b) as (a), but 95% found, for a quicker launch; (c) as (a), but 99% found. |
| **M3-D12 [Step 0]: a small CLI** | **(a) Not in this milestone**; reconsider at Milestone 4's two-month review; (b) a tiny script that prints the paste command's list: packaging and signing work for little gain; (c) a CLI that downloads the catalog and prints the missing fonts: a second product, and a new data flow to explain. |
| **M3-D13 [later OK]: browser storage** | **(a) Nothing**, keeping Milestone 2's promise: the OS tab lives in the URL after `#`, and the list, the toggle and "I have it" choices last only while the page is open; (b) localStorage keeps the last OS tab and the toggle; (c) as (b), plus an opt-in "Remember my fonts on this device" with a "Forget" button; (d) the list is always remembered. |
| **M3-D14 [later OK]: button or paste first** in desktop Chromium, settled by usability round 1 | **(a) The button first**, with paste below; (b) paste first, with the button offered as "or let your browser read them". |
