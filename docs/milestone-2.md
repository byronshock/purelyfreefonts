# Milestone 2 checklist: the filterable list goes live

Milestone 2 replaces the stub on purelyfreefonts.com with Milestone 1's ranked, filterable list, plus methodology, privacy and about pages and a tip link, and starts usability testing. Comparing the list with a visitor's own fonts waits for Milestone 3. Settled decisions go in [AUTHORITY.md](../AUTHORITY.md).

**How to read each step:**

- **Who:** the owner, Claude, or both.
- **Depends on:** the steps that must finish first. "M1 step N" is in [milestone-1.md](milestone-1.md), D1–D17 are in [ranking-methodology.md](ranking-methodology.md), and M2-D1 to M2-D12 are under [Decisions](#decisions).
- **Done when:** what must be true before the step is ticked.
- **Parallel:** where Claude can run several agents at once.

Tick each item as soon as it is done and verified. If an item is only partly done, leave it unticked and note what's left.

**Milestone 2 is done when:**
- purelyfreefonts.com serves the filterable list from the latest merged `catalog-site.json`, and the stub is gone;
- every font shows its linked license, official download link, ranks with tiers, per-source ranks and tags;
- every listed font is redistributable, as Rule 3 says since 2026-09-30, and no font needs a filter for it;
- the methodology, privacy and about pages are live, the blog builds and deploys with the site (M2-D12), and the tip link passes the checks in [ops/DONATIONS.md](../ops/DONATIONS.md);
- the live privacy test passes: no request to another site, no cookie, nothing stored in the browser, the CSP enforced, no Cloudflare error-report headers;
- the WCAG 2.2 AA check is clean and the performance budget is met;
- usability rounds 1 and 2 are done, with no blocker or major finding open;
- nothing has been announced (that is Milestone 4);
- AUTHORITY.md records M2-D1 to M2-D12, and the owner has accepted the handoff.

**Critical path:** 0 → 1 → 3 → 4 → 6 → 13 → 14 → 15 → 17. Steps 1–12 run on step 2's sample catalog while Milestone 1 is still building; steps 13–15 need its real data. (Step 16, the first monthly refresh on the live site, moved on 2026-09-30 to [milestone-refresh.md](milestone-refresh.md), `refresh_timing`.)

---

### Step 0: Owner reviews this checklist
**Who:** owner; Claude answers questions and makes edits. **Depends on:** nothing; it can run during Milestone 1. Only M2-D5 (d) waits on a Milestone 1 decision (D3).
- [x] Claude opens a pull request adding this checklist. ([#2](https://github.com/byronshock/trulyfreefonts/pull/2))
- [x] The owner answers the seven **[Step 0]** decisions (M2-D1 to M2-D7) *(all answered 2026-09-25; [later OK] defaults kept)* and changes any **[later OK]** default (M2-D8, M2-D10, M2-D11); M2-D9 was decided on 2026-09-25. With step 7, they cover M1 step 20's handoff list.
- [ ] *(Partly done: every answer recorded, M2-D8, M2-D10 and M2-D11 on 2026-09-30; `docs/milestone-2.md`, `docs/roadmap.md` and, on 2026-09-30, `docs/usability-test.md` are in Tracked docs. Left: PLAN.md's Milestone 2 line, which the owner updates, since PLAN.md is private.)* Claude records each answer in AUTHORITY.md with its date, adds `docs/milestone-2.md`, `docs/usability-test.md` (step 12) and, if not yet listed, `docs/roadmap.md` to "Tracked docs", and updates PLAN.md's Milestone 2 line if needed.
- [x] The owner merges the pull request. *(Merged by Claude at the owner's request, 2026-09-25.)*

**Done when:** no [Step 0] decision is open, and AUTHORITY.md and this file are merged to `main`.

### Step 1: Site skeleton and build
**Who:** Claude. **Depends on:** 0 (M2-D3); M1 step 1 under M2-D3 (a).
- [x] `site/` (templates, one stylesheet, one script, static files) builds into `build/site/` (gitignored); the stub in `public/` stays live until step 14.
- [x] `uv run tff-site build` (M2-D3 (a)) checks the data against M1 step 15's schema, writes the default rank (M2-D1) into `index.html`, hashes asset names (`/assets/<name>.<hash>.<ext>`), writes `version.txt` (commit, run date) and makes no network requests.
- [x] A local preview server sends the Caddyfile's headers.
- [x] Per page: `lang="en"`, title, meta description, canonical URL. Site-wide: `robots.txt` (the live copy must match the repo's, since Cloudflare's Bot Preference Sync adds lines when an AI bot policy blocks or disallows), `sitemap.xml`, a favicon, a 404 page (Caddy `handle_errors`) and a share image on our own domain. The interface font is Arimo (step 6; it replaced the system font stack on 2026-09-30).
- [x] CI on every pull request builds from the sample and runs the Playwright tests of steps 3, 6, 9 and 10 (Playwright never ships).

**Done when:** CI builds from the sample, two builds are byte-identical, and the local preview shows the ranked list.

### Step 2: Approve the site data and build a sample
**Who:** Claude; the owner approves. **Depends on:** 0; runs alongside step 1. M1 step 20 freezes `catalog-site.json` v1 only after this approval.
- [x] The owner approves the `catalog-site.json` fields M1 step 15 lists for this milestone: aliases, per-source states, ranges, flags, designer lists, `preview` and run metadata. *(Approved as drafted 2026-09-25, in `schemas/catalog-site.schema.json`; designer lists are dropped because D12 is usage only. License filter: four groups. The fields added since, `nerd`, the `app` system type, a link's `note`, `score` and `previous_score`, were approved for the v1 freeze on 2026-09-30, `site_fields_v1`.)*
- [x] If step 10's budget requires it, per-source data moves to a details file, fetched when a details panel first opens. *(Done: `/assets/details.<hash>.json`, beside `/assets/list.<hash>.json`; site/CONTRACT.md.)*
- [x] `tests/fixtures/catalog-site.sample.json`, the sample M1 step 20 freezes with v1: about 40 labelled-synthetic fonts covering ranks and bands, tiers A–C, no deliberate-install evidence, not redistributable (made redistributable on 2026-09-30 for Rule 3), a monospaced font the catalog files as sans-serif (added that day for `monospace_category`), attribution required, limited accents, each system's preinstalls, a package dependency, no preview, a very long name, and a font found only by alias. *(Done 2026-09-25 in [pull request #8](https://github.com/byronshock/trulyfreefonts/pull/8): 40 fonts; it validates against the draft schema.)*
- [x] Extend the schema and the sample for the nullable `links.nerd` (TASK-2 rulings of 2026-09-28 and 2026-09-29): at least one synthetic monospace font carries a Nerd Font build link to its own build page, labelled with the build's name. *(The owner approved the new field on 2026-09-29, `data/reviews/site/2026-09-29.toml`, `nerd_fonts_link`. Built 2026-09-29: both schemas and the sample, where `sample-mono-02` links a Nerd Fonts folder and `sample-mono-13` a maker's own build; the marker's wording is the catalog's `nerd`, from `config/site.toml`.)*

**Done when:** the owner has approved the fields, and the sample passes M1 step 15's schema (a draft until that step lands).

### Step 3: The list: ranks, filters, search and sorting
**Who:** Claude. **Depends on:** 1, 2; M2-D1, M2-D2, M2-D4; D3, D4, D6, D10, D13.
- [x] A rank selector: Overall; Desktop *most chosen*; Desktop *most installed*; Used in projects (the Project rank, D10); and D13's views (Coding, Developers & apps, and Rising (beta) once it has 3 months of history). One line says what the rank measures; "By category" is the category filter on Overall.
- [x] Each row: rank or band (a score bar once the score item below lands), the title (the specimen, which draws the family name, or the text name and fallback text: step 5), category, license, badges (variable, limited accents, credit required, "Comes with" the operating systems and apps; kept short by the owner's ruling of 2026-09-30, `filters_layout`: no monospace or not-redistributable badge, and "Pulled in by" only in the details), the official download link, a fixed-width **"NF"** marker at the end of the title cell when the font has a Nerd Font build (so the markers line up and rows keep their height; TASK-2 rulings of 2026-09-28 and 2026-09-29, `nerd_marker_spot_title`), and a details button.
  - [x] The name shows once (site ruling `name_once` of 2026-09-29): where a specimen shows, its first line is the visible name, and the text name stays in the page for screen readers, search and find-in-page. The text name shows wherever the specimen doesn't: no specimen, a specimen that fails to load, no CSS masks, no IntersectionObserver. *(`div.font-title`, `site/js/45-specimens.js`, `site/css/20-list.css`; `tests/site/test_specimens_loader.py`, `test_list_markup.py`, `test_a11y.py`, in Chromium and Firefox.)*
- [x] *(Retired on 2026-09-30 by the score bars below.)* Numbers follow M2-D2 until the score item below lands: each filtered list counts from 1. Fonts past the rank's exact top 100 show their band ("101–250", "251–500") instead of a number, in `order`. Fonts unranked in the view come last, unnumbered, with a reason: in *most chosen*, "no evidence of deliberate installs" plus its "Comes with" badge, or "Pulled in by" in the details (`filters_layout`). Coding lists monospace fonts only.
- [x] **Scores instead of numbers** (site rulings of 2026-09-29: `score_display`, `score_curve`, `score_held_fonts`, `score_field_timing`; replaces M2-D2). *(Built 2026-09-30. The build now ships CSS and JS without comments, so the list page on the real catalog is 79.1 KB of step 10's 100 KB.)*
  - [x] Each ranked row shows its score, 100·Φ(`ranks.<key>.score`) (the normal curve), as a blue bar where the number is now: the column is 96px wide from 640px; design the phone layout, where the bar sits beside the drawn name.
  - [x] Each bar has an accessible text (such as "Score 83 of 100"), and a legend or the methodology page says what the score means: relative standing among the fonts the sources track, not the chance that a given person chose the font.
  - [x] Filters only hide rows: no renumbering and no bands in the list. Retire M2-D2's numbering (`site/js/20-view.js` step 5, `tff_site.build.rank_labels`, site/CONTRACT.md View steps 3, 5 and 6).
  - [x] The list sorts by score. A font the two-source rule holds back (`gate_held`) takes its score's place and carries a marker: a hollow bar, explained by a legend line (owner ruling of 2026-09-30, `held_marker_style`). In Developers & apps, where every font rests on one kind of source, one note above the list replaces the marker (owner ruling of 2026-09-30, `held_marker_dev_apps`).
  - [x] Details keep the rank, band and range; the methodology page's "Numbers and bands" section is rewritten for scores. *(The methodology section is now "Scores"; the details' held line reads "Held out of the numbered top 100: its score rests on one kind of source.")*
  - [x] Browser tests: bars match scores, order by score, held-font markers, and no renumbering under filters.
- [x] Filters, laid out per M2-D4, as the owner's rulings of 2026-09-30 set them (`monospace_category`, `license_filter`, `filters_layout`): category, as pills, where Monospace is every monospaced font (it replaces the Spacing filter, which had replaced D13's "Text only" and a monospace-only box); features: variable, **"Nerd Font available"** (TASK-2 ruling of 2026-09-29), and "Accented letters" (hides limited accents, D4); license: one "No credit required" box, shown only while some font needs credit (it replaces the license-class boxes and "Hide attribution required"); "Hide fonts that come with" as one select of Windows, macOS, Linux or Android. There is no "Redistributable fonts only" box: Rule 3 lists redistributable fonts only.
- [x] Search over names and aliases by `search_key` (NFKC, case-fold, drop spaces, hyphens and underscores, strip accents), so "Source Sans Pro" finds Source Sans 3. Its test vectors are in the shared file `tests/vectors/name-keys.json` (M3 step 3), which the Python and the JavaScript tests both read.
- [x] Sort by rank (default) or name, either way round, with buttons over the rank and name columns (owner ruling of 2026-09-30, `sort_header`), a count ("Showing 48 of 540 fonts"), "Clear filters", and a no-results message naming filters to loosen. *(The order's words take a second line inside the button, so Rank fits over the rank column and Name starts over the names: owner ruling of 2026-09-30, `sort_two_lines`.)*
- [x] The view lives in the URL after `#` (`#rank=project&cat=serif&hide=limited`): links reproduce it; links with the keys retired on 2026-09-30 (`spacing`, `lic`, `redist`) still open; Back works, nothing is stored, and that part never reaches the server.
- [x] Without JavaScript, the built default list shows, with a note that filters need it.
- [x] The front page's note "Why isn't my favorite free font here?", word for word, its address a mailto link (site rulings of 2026-09-29, `data/reviews/site/2026-09-29.toml`: `why_not_listed`, `why_not_listed_layout`): on wide screens a textbook-style frame floated right beside the lead and the privacy note, with the list starting below it so rows are never narrowed; on phones a full-width frame folded to its heading, opened with one tap. It works without JavaScript, moves nothing as the page loads, gives screen readers the text once, and works in both themes.
- [x] Browser tests: filtering, numbering, band order, alias search and restoring a view from its URL. (The listed-only fonts after the ranked ones moved to [milestone-more-fonts.md](milestone-more-fonts.md) on 2026-09-30, `more_fonts_timing`.)

**Done when:**
- every rank and filter works on the sample and the latest real run, and the tests pass;
- Category Monospace shows exactly the monospaced fonts, on every rank;
- a copied URL opens the same view in a fresh browser.

**Parallel:** steps 5, 7, 9, 11 and 12 can each run in their own agent.

### Step 4: Font details
**Who:** Claude. **Depends on:** 3; D12; M1 steps 6b, 13 and 14.
- [x] Details open inside the list, and each font has its own link (`#font=inter`).
- [x] The layout of the owner's ruling of 2026-09-30 (`details_layout`): "Type your own text" first, then one short list (Get it, License, Font, Comes with, Also known as, and the rank the selector shows), then every rank and source folded behind a closed "All ranks and sources", then "Report a problem". *(The fold keeps its small "Ranks" and "Sources" headings: owner ruling of 2026-09-30, `fold_headings`.)*
- [x] License name and SPDX id, linked to `text_url`, and what to credit, if required ("No credit needed." otherwise). No "Redistributable" line: every listed font is (Rule 3). *(Licenses the owner ruled in take their names from `config/licenses.toml`; a license read inside a release archive links the page in `config/license-texts.toml` instead.)*
- [x] Official, designer and Nerd Font build download links naming their destination ("GitHub: rsms/inter"); no font-file links (M1 step 14). The Nerd link's label names the build ("SauceCodePro Nerd Font"), after the "NF" marker; the legend "NF: Nerd Font version available (adds developer icons, which have their own licenses)." (TASK-2 ruling of 2026-09-29) is the one above the list, not repeated in the panel (`details_layout`). *(`tff_site.data` refuses any link that downloads an archive or a font file, and the owner's-ten test any panel link to the font file's archive.)*
- [x] Every published rank with tier and range (a band past #100), a line if the two-group gate held the font back, and per-source ranks with their state (observed, below the floor, not covered, too new), each source linked to its credit on the methodology page.
- [x] Tags: preinstalled on, variable or static, Latin coverage, designer lists (if D12 is (a)), "also known as"; "pulled in by" goes with the folded ranks and sources, which it explains.
- [x] "Report a problem with this font": step 12's license form, prefilled with the font's id and data date, plus the email fallback.

**Done when:** for 10 fonts the owner picks, every field matches `catalog.json` and every license and download link returns HTTP 200 (M1 step 14's monthly check covers the rest).

### Step 5: Font previews
**Who:** Claude; the owner picks the sample text. **Depends on:** 1, 2; M2-D5; D3 (`preview_ok`); M1 steps 6b (each family's `font_file` {url, sha256}) and 18. M2-D5 chose (b), so every item applies.
- [x] A `specimens` stage in `tff-catalog refresh`, after the license and link stages: for each `preview_ok` font, fetch `font_file.url`, check its sha256 (a mismatch means a flag and no image), set the sample with HarfBuzz, and save the outlines to `build/specimens/<id>.svg`. The stage serves and commits no font file; specimens hold outlines only.
- [x] Sample: the family name plus the owner's line "Dolorem ipsum quaerit nemo." (site ruling `specimen_sample_latin` of 2026-09-29, in place of the accented line of 2026-09-26; "quaerit" without the æ ligature, which basic-Latin fonts lack) or the basic-Latin fallback, never a missing-glyph box (a failing font gets a flag); variable fonts at Regular (400), else their default instance. Output is byte-stable, so a refresh changes only fonts that changed.
- [x] Budget: half at 5 KB compressed or less, none over 16 KB compressed (else the family name only; the owner's ruling of 2026-09-30, `specimen_max_size`, in place of 30 KB uncompressed), all under 10 MB uncompressed.
- [x] Served as immutable `/assets/specimens/<id>.<hash>.svg` files, drawn as a CSS `mask-image` filled with `currentColor` (`CanvasText` with `forced-color-adjust: none` under forced colors), so they read in every theme within `img-src 'self'`. Accessible name "<family> sample", with the name also in text, which is hidden once the specimen shows (`name_once`).
- [x] The script sets each mask as its row nears the screen, in a fixed-size box; `<noscript>` images carry `loading="lazy"` (browsers load them all when scripts are off; the owner's ruling of 2026-09-26 keeps them). The loader can pause, for M3-D10 (previews after a comparison).
- [x] Fallbacks: "No preview: this font's license doesn't let us host its files. See it on <official page>." (no `preview_ok`); "Preview not available yet." (render failed).
- [x] Under M2-D5 (b), "Type your own text" loads the unchanged upstream file on request, after showing its size. The deploy fetches the files and checks `font_file.sha256`; they are never committed and get no CORS headers.

**Done when:**
- every `preview_ok` font has a specimen or a flag saying why not;
- the budget check passes in CI;
- the owner has checked the top 100 in light, dark and forced-colors modes.

### Step 6: Layout, themes and accessibility (WCAG 2.2 AA)
**Who:** Claude; the owner or a tester does one screen-reader pass. **Depends on:** 3, 4, 5.
- [x] A table-like list on wide screens and cards on phones, with no sideways scrolling at 320 CSS px (1.4.10); on phones, one "Filters" button that shows how many are on.
- [x] Light or dark follows the system (no switch, since nothing is stored); forced colors work; `prefers-reduced-motion` is respected.
- [x] Interface font: Arimo, upright and italic, served from the site as Latin WOFF2 and preloaded, with `font-display: optional` and fallbacks that share its widths, so no text moves when it loads (owner ruling of 2026-09-30, AUTHORITY.md "Interface font"). Replaces the system font stack.
- [x] Landmarks, one `h1`, a skip link, and each font a list item with its own heading; native, labelled controls, with fieldset and legend for filter groups.
- [x] Each change announces the new count politely (4.1.3) and keeps focus. Everything works by keyboard, with a 3:1 focus outline that no sticky header hides (2.4.11).
- [x] Contrast in both themes: 4.5:1 for text, 3:1 for large text and controls (1.4.3, 1.4.11). Targets at least 24 × 24 CSS px (2.5.8); 200% zoom and text spacing don't break the layout (1.4.4, 1.4.12). Links name their destination, and the feedback link keeps one place on every page (3.2.6).
- [x] axe-core via Playwright on every page, in both themes, at phone and desktop widths, with filters on and a details panel open; in CI from here on.
  - On the real catalog (CI's `site-real` and `deploy.yml`) a lighter pass runs: axe on every page in both themes at desktop width, with a details panel open, and reflow at 320 px. The full grid runs on the sample catalog (owner ruling of 2026-09-30, AUTHORITY.md).
- [ ] Manual passes: keyboard only, and Orca with Firefox; VoiceOver on an iPhone or NVDA if a tester has one.

**Done when:** axe reports 0 violations in CI, the manual passes leave no WCAG 2.2 AA failure open, and the results are in the pull request.

**Parallel:** the automated and manual checks.

### Step 7: Methodology, privacy and about pages
**Who:** Claude writes; the owner approves. **Depends on:** 1; M2-D9; M1 steps 3 (terms ruling, data license) and 17 (public text); D1, D10, D12, D13, D17.
- [x] `/methodology`, generated at build time from `docs/ranking-methodology.md` so they can't drift: §1 in plain words; the two desktop views and why; reading ranks, bands and tiers; known biases (§11); source credits (what each measures, link, license); data (D17) and code licenses; run date, method version and stale sources; a link to the full text.
- [x] `/privacy`: no cookies, analytics, browser storage or requests to other sites; "Check for yourself" in the Network tab of Firefox, Chrome and Safari (only trulyfreefonts.com should appear); what the server and Cloudflare log, and for how long (M2-D9); a link to `ops/Caddyfile`; a promise that Milestone 3's check keeps your font list on your device.
- [x] `/about`: what the site is; Rules 1–4 in plain words, including why ITF-licensed fonts are out; why the most-used fonts are ranked, and that the rest follow A–Z later (`more_fonts_timing`); what's next; how to report a problem or get in touch. Why a free font may be missing, and how anyone, foundries included, can ask for one, is the front page's note "Why isn't my favorite free font here?" (step 3; site rulings of 2026-09-29, `why_not_listed`), not part of `/about`.
- [x] Near the list, linked to `/privacy`: "No cookies, no tracking, and the page loads only its own files. Check the Network tab."

**Done when:** the owner has approved the text, the pages pass step 6's checks, and the credits match M1 step 3's terms ruling.

### Step 7b: Blog
**Who:** Claude builds it; the owner writes and approves posts. **Depends on:** 1, 6, 7 (`render_markdown`); M2-D12.
- [x] Each post is one file, `site/content/blog/<yyyy-mm-dd>-<slug>.md`, starting with YAML front matter: `title`, `date` and `description`, plus optional `updated` and `draft`. The build fails on a missing or unknown field, a repeated slug, or a slug that breaks the deploy's path rule (CONTRACT.md §2).
- [x] `tff-site build` writes `/blog/` (newest post first), `/blog/<slug>/` and an Atom feed at `/blog/feed.xml`, and adds the posts to `sitemap.xml`. Post text goes through step 7's `render_markdown`, with raw HTML off. Blog pages load no script.
- [x] Images sit beside their post and are published as immutable `/assets/blog/<slug>.<h>.<ext>` files. The build fails on an image with no alt text.
- [x] Dates come only from the front matter, never from file times or the build time, so the build stays byte-identical.
- [x] Posts marked `draft: true` are built only by `tff-site build --drafts`, which the staging deploy uses.
- [x] Until the first post is published, the build writes no `/blog/` pages and the nav has no Blog link.
- [x] License: `LICENSE-DATA`'s scope gains `site/content/blog/` (text and images), and each post page gives its license, CC BY-SA 4.0.
- [x] CONTRACT.md §2 (build output) and §3 (templates) are updated in the same pull request, with their contract tests.
- [x] Tests: an offline Atom check on the feed, plus step 6's axe run and step 9's privacy test on `/blog/` and one post.

**Done when:** a sample post builds, passes step 6's and step 9's checks on the test site, and its feed passes the Atom check.

### Step 8: Tip link
**Who:** Claude. **Depends on:** 1. [ops/DONATIONS.md](../ops/DONATIONS.md) steps 1–10 are done; the live link is in its Facts table (2026-09-25).
- [x] Do DONATIONS.md step 11, ticking it there: one plain footer link on every page, worded "Leave a tip ($5 suggested)" (the owner's ruling of 2026-09-26), no Stripe script or cookies, called a tip, not a donation. *(The words around the link wait for step 7's approval of the page text.)*
- [x] Step 9's test sees no Stripe request before a click; the headers need no change.

**Done when:** DONATIONS.md's Verification lines pass on the test site, then on the live site at step 14.

### Step 9: Privacy: headers, Cloudflare settings and logs
**Who:** Claude, over SSH and the Cloudflare API; the owner does what the token can't. **Depends on:** 1 (page tests only). The Cloudflare items can be done now, on the stub.
- [x] Headers in `ops/Caddyfile`: *(All written in `ops/caddy/site.caddy`, tested under Caddy, and live since 2026-09-30 (SERVER.md item 25), the CSP included from header phase B the same day.)*
  - a CSP with no inline code: `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; font-src 'self'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`;
  - `Permissions-Policy` turning off unused features, including `local-fonts=()` (M3 step 6 allows `self` on `/check/` only);
  - `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy`: `same-origin`;
  - `no-transform` on HTML (step 11), so Cloudflare can't rewrite pages even if a setting changes;
  - the current HSTS, `nosniff` and `Referrer-Policy: strict-origin-when-cross-origin`.
- [x] Via `ops/cf.sh`, on all three zones: Email Address Obfuscation off (it was on and injected a script); Rocket Loader and Always Online kept off; Browser Cache TTL "Respect Existing Headers" (was 4 hours). *(Done 2026-09-25; [ops/SERVER.md](../ops/SERVER.md) item 19.)*
- [x] Owner, in the dashboard: Web Analytics' automatic setup disabled (it was on by default, and injecting the beacon into browser requests on 2026-09-25); Bot Fight Mode off (it sets `__cf_bm`). Steps are in [ops/SERVER.md](../ops/SERVER.md) items 20–21. Cloudflare Fonts and Speed Brain were confirmed off via the API, and no Zaraz script appears in the page. *(Done and verified from outside 2026-09-25.)*
- [x] Network Error Logging off on all three zones, and the access log per M2-D9 (done 2026-09-25; [ops/SERVER.md](../ops/SERVER.md) section F; the later log rulings, of 2026-09-26 and 2026-09-28, deploy with SERVER.md item 25). The live test below and step 14's SERVER.md checks re-verify them.
- [x] A Playwright test (Chromium, Firefox) loads every page, applies filters, opens details and scrolls every specimen into view, failing on any request to another site, cookie, browser-storage write or CSP violation. It runs in CI and after each deploy on the live site, where it also checks headers (CSP present; no `set-cookie`, `nel` or `report-to`) and that the HTML has no `/cdn-cgi/` path. If M3-D13 (browser storage) stores anything, Milestone 3 turns the storage check into a key allowlist.

**Done when:** the test passes through Cloudflare on the test site (or live, under M2-D7 (b)), and the Network tab in Firefox and Chrome shows only purelyfreefonts.com.

**Parallel:** the Cloudflare items can run alongside everything else.

### Step 10: Performance budget
**Who:** Claude. **Depends on:** 3, 4, 5.
- [x] CI fails the build if the list page's HTML, CSS and JavaScript exceed 100 KB compressed, the catalog data exceeds 100 KB compressed (per-source data then moves to step 2's details file), or specimens break step 5's budget or load before their row nears the screen.
- [x] The list page in Playwright, CPU slowed 4× on slow 4G: LCP ≤ 2.5 s, CLS ≤ 0.1, TBT ≤ 200 ms.
- [x] A filter or rank change redraws the full catalog within 200 ms under the same slowdown.

**Done when:** the budgets pass in CI, and the load-speed numbers pass through Cloudflare on the test site (or live, under M2-D7 (b)).

*(Met 2026-09-30. CI's site-perf and site-real jobs passed on 0e3d4cb. Through Cloudflare, on the test site's release f6e84b3 (500 fonts; the site code on `main` is unchanged since), with the HTML `DYNAMIC` and `/assets/` a cache `HIT` (checked by hand): three runs one after another in Chromium, each the median of five loads per size, all within budget on the first set. LCP (≤ 2.5 s): 1700, 1704 and 1708 ms on a phone, 1712, 1716 and 1720 ms on a desktop. CLS (≤ 0.1): 0 in every load. TBT (≤ 200 ms): medians of 0 ms, except 1 ms on the desktop in run 3; the worst single load was 60 ms. Slowest of the 20 rank and filter changes (≤ 200 ms): 114, 120 and 116 ms, each time the first rank change. The same tests on a local build give about 1670 ms and 116 ms. To repeat: `TFF_PERF_URL=https://staging.trulyfreefonts.com uv run --group browser pytest tests/site/test_perf.py -k deployed --browser chromium -s`. The tests refuse a site with fewer than 500 fonts, and skip without `TFF_PERF_URL`, which no CI job sets.)*

### Step 11: Deploy, caching and the test site
**Who:** Claude; the owner creates the GitHub environments and secrets (or lets Claude, with `gh`) and adds the Cache Rule. **Depends on:** 1, 9; M2-D6, M2-D7, M2-D11.
- [x] Caching, per M2-D11: hashed `/assets/` files get `public, max-age=31536000, immutable`, and the owner adds the Cache Rule the token can't ("URI path starts with `/assets/`: eligible for cache, use the origin's Cache-Control"), since Cloudflare caches neither JSON nor HTML by default. *(Cache Rule created 2026-09-25, [ops/SERVER.md](../ops/SERVER.md) item 23; the Caddy headers went live on 2026-09-30 with SERVER.md item 25: HTML answers `no-cache, no-transform`, and `/assets/` is `immutable` in `ops/caddy/site.caddy`, tested under Caddy in CI.)* Under (a), HTML, `version.txt` and other unhashed files get `no-cache, no-transform`, and deploys need no purge; under (b), HTML gets `s-maxage=300` and `stale-if-error`, the Cache Rule also covers it, and each deploy purges it with a Zone · Cache Purge token (an Actions secret under M2-D6 (b)).
- [x] Each deploy uploads to `/srv/trulyfreefonts/prod/releases/<commit>/` (the stub moves there first), then switches `prod/current` to it in one step; `/srv/trulyfreefonts/public` is a root-owned symlink to `prod/current`. Test-site deploys use `/srv/trulyfreefonts/staging/releases/<commit>/` and `staging/current` the same way. Three releases are kept, so a rollback is one command. *(Done 2026-09-30: stage A, then the stub moved into `prod/releases/0e3d4cb…` with an atomic swap of `public` for the symlink; the first staging release is f6e84b3.)*
- [x] `ops/deploy.sh`, run from the laptop: build, check, upload, switch, then step 9's live test; the fallback under M2-D6 (b).
- [ ] Under M2-D6 (b): *(Code and key tests built. Left: server stage B, and the GitHub environments and secrets.)*
  - a `deploy` user with no password, sudo or interactive shell, in `AllowUsers` (keep a second SSH session open against lockout), its key forced to an `ops/` script that only uploads and switches releases; if D15 is (b), it can't reach the snapshot store;
  - GitHub environments `production` (`main` only) and `staging` holding the key, pinned host key and server address as secrets;
  - a workflow on each push to `main`, or dispatched with a commit to roll back: build → tests → upload → switch → live test, with actions pinned by SHA, `contents: read`, one deploy at a time, and an issue on failure;
  - tests that the key can't open a shell, read other files or write outside `releases/`.
- [ ] Under M2-D7 (a), `staging.purelyfreefonts.com` (`staging.trulyfreefonts.com` until the rename of 2026-10-02, step 13b; the old host then 301s to it): a proxied DNS record (the origin certificate covers it); a Caddy block rooted at `/srv/trulyfreefonts/staging/current` (not `staging/`, which also holds `history.log` and the release manifests) with the same headers plus `X-Robots-Tag: noindex`; deployed from the `staging` branch by a key that writes only there. *(Up on the old host since 2026-09-30: proxied DNS, the Caddy block live with `noindex`, and a first deploy from the laptop, f6e84b3, whose live test passed through Cloudflare. Left: the new host (step 13b), and the `staging` branch, environment and key for Actions deploys, after stage B.)*
- [x] Caddyfile changes still use the checked line at the top of that file, never Actions.
- [x] [ops/SERVER.md](../ops/SERVER.md) gets a deploy, rollback and test-site runbook in place of the rsync line. *(2026-09-30.)*

**Done when:**
- a deploy while a loop fetches the page every 100 ms causes no errors, and no page mixes old and new files;
- a rollback takes one command and restores the previous `version.txt`;
- under M2-D6 (b), a push to `main` deploys with no manual step, and the key tests pass;
- under M2-D7 (a), the test site is up and sends `noindex`.

### Step 12: Feedback channels and the usability test plan
**Who:** Claude drafts; the owner recruits and runs sessions. **Depends on:** 0 (M2-D10); runs alongside 3–11.
- [x] Per M2-D10, in one footer spot on every page: *(The five labels the forms and the deploy report apply were created on 2026-09-30.)*
  - issue forms in `.github/ISSUE_TEMPLATE/`, each applying its own labels: `license.yml` ("Wrong license or link", prefilled by field id with the font's id and data date), `missing-font.yml` (which says that anyone, foundries included, may ask for a missing one: owner rulings of 2026-09-29; until [milestone-more-fonts.md](milestone-more-fonts.md), only the ranked catalog is listed, `more_fonts_timing`), `usability.yml` and `bug.yml` (M3 step 8 adds `wrong-match.yml`);
  - `config.yml`: blank issues off, and a contact link for people without GitHub;
  - an email link to the site's `admin@` address, with a subject.
- [x] `research/` added to `.gitignore`, for raw session notes.
- [x] `docs/usability-test.md`:
  - **Tasks** (20–30 minutes): (1) a popular free coding monospace that can also ship in an app; (2) a body-text serif with Polish or Vietnamese accents; (3) is Inter free for commercial use, and where do you get it? (4) hide your computer's own fonts; (5) switch to fonts installed on purpose: why did the list change? (6) why does <font> rank above <font>, and how sure is the site? (7) on a phone, open a handwriting font's download page; (8) send this exact view to a friend; (9) what's missing? Would you paste a command's output to hide your fonts, and come back monthly?
  - **Measures:** per task, unaided, helped or failed, and 1–7 ease; problems rated blocker, major or minor; quotes. No recordings without consent; no analytics.
  - **Participants:** 5 new people per round (designers, developers, casual users), at least 2 on phones and 1 keyboard-only or screen-reader user if possible, invited by direct message from the owner's contacts and communities, never in Milestone 4's launch channels (M4-D2).
  - **Sessions:** remote video, thinking aloud, the owner guiding and taking notes (tasks by email for those who can't join); a consent script at the start; findings anonymized (P1, P2 …).
- [ ] Each finding becomes an issue labelled `usability`, with severity and no names.

**Done when:** the owner has approved the script and invitation, a test issue through each form arrives with the right labels, and round 1's testers are booked. (GitHub drops a form's label silently when the repository lacks it: the owner creates the five labels first, as `ops/deploy/README.md`, GitHub settings, lists.)

### Step 13: Usability round 1, before launch
**Who:** the owner runs sessions; Claude writes up and fixes findings. **Depends on:** 3–7, 9, 11, 12; real data from M1 step 16 or later, even before the freeze.
- [ ] A trial session checks the script; fix it.
- [ ] 5 sessions on the test site (under M2-D7 (b), a laptop build shared on screen).
- [ ] Claude turns notes into issues grouped by theme; the owner confirms severities.
- [ ] Every blocker and major is fixed or ruled out by the owner with a reason; revisit M2-D4 or the wording if testers stumbled there.

**Done when:**
- 5 sessions are done, with no blocker or major open;
- at least 4 of 5 testers completed tasks 1, 2, 3 and 7 unaided, or each failing task's fix has been checked with 2 more people.

**Parallel:** Claude writes up each session while the next is scheduled.

### Step 13b: Rename to Purely Free Fonts
**Who:** Claude, guiding the owner; the owner does the dashboard, registrar, GitHub, Stripe and Ko-fi steps. **Depends on:** the owner's rulings of 2026-10-02 (AUTHORITY.md, **Name and domain**). The method hold and the soft-launch hold stand, so production keeps serving the stub, renamed, and the catalog isn't rebuilt.
- [x] **New zones** (`ops/SERVER.md` section K): purelyfreefonts.com, .org and .net, set up like the other zones.
  - [x] The owner registers the three domains, adds them to the API token, turns on Email Routing for admin@purelyfreefonts.com and sets the new zone's bot settings. *(2026-10-02)*
  - [x] Claude applies the zone settings and reads them back. Also: the `/assets/` cache rule and DMARC on .com; the no-mail records and the edge 301 rules on .org and .net (inactive until cutover); and the Origin CA certificate for purelyfreefonts.com and `*.purelyfreefonts.com`, installed on the server. *(2026-10-02)*
- [x] **GitHub:**
  - [x] The owner renames the repository `byronshock/purelyfreefonts`. *(2026-10-02)*
  - [x] Claude updates its description. *(2026-10-02)*
  - [x] The homepage is set at cutover. *(2026-10-02: https://purelyfreefonts.com)*
- [ ] **The new name in the code,** on a branch deployed only to the test site:
  - [x] the site's name, address, contact address and mail subject (each constant has two copies; change both);
  - [x] the page text: titles, descriptions and the heading "The most popular purely free fonts"; the 404, about and privacy pages; the methodology credit line; the blog and feed titles; `robots.txt`;
  - [x] the wordmark, redrawn in League Gothic (header `<img>` 229×48), and the share card, redrawn;
  - [x] `site/CONTRACT.md`;
  - [x] the issue forms, `LICENSE-DATA`, the README, `pyproject.toml` and the glyph-set notice;
  - [x] the current docs and the runbooks' public URLs; *(`ops/SERVER.md`'s at the cutover)*
  - [x] the user agents and the repository references, including the refresh watchdog's;
  - [x] the deploy URLs in `ops/deploy.sh` and `deploy.yml`, and CI's stand-in certificate;
  - [x] Caddy: site blocks for purelyfreefonts.com and `staging.purelyfreefonts.com`, with the new certificate;
  - [x] the tests, plus a live check that every old host 301s in one hop: `tests/live/test_redirects.py`, run with `--check-redirects` from the cutover on (before it, the old hosts still serve the site);
  - [x] one line in `CLAUDE.md`;
  - [ ] with the owner's OK, the backlog's project name and TASK-3, through the CLI.
- [x] **The test site on the new host:**
  - [x] Claude: the DNS records, the Caddy install, the deploy, and step 9's live test on `staging.purelyfreefonts.com`; *(2026-10-02: `ops/deploy.sh staging` deployed 79c2685, and the live test passed: 11 passed, with the 2 redirect checks waiting for the cutover. The deploy's own site-test run hit the known Firefox load-timeout flake, so it was rerun with `--fast` after all 10 CI checks had passed on that commit.)*
  - [x] the owner checks Web Analytics on the new zone and reviews the test site. *(2026-10-02: RUM disabled; "Go ahead with the cutover.")*
- [ ] **Cutover:**
  - [x] Caddy: purelyfreefonts.com serves the renamed stub, and every old host 301s to the new one; *(2026-10-02)*
  - [x] the misspelled domain's edge rule points at the new domain;
  - [x] the .org and .net placeholder records go in;
  - [ ] the branch is merged;
  - [x] Claude checks every old host from outside. *(2026-10-02: `tests/live --check-redirects` passes on purelyfreefonts.com and staging.purelyfreefonts.com, from the US.)*
- [ ] **The owner, at cutover:**
  - [ ] Stripe: public business name, website, statement descriptor, support email and the payment page text;
  - [ ] Ko-fi closed;
  - [ ] any mail filters updated.
- [ ] **Drafts for the owner to post:** a comment on the Fonts Over Time license request (fcjr/fontsovertime#1), and a note to ecosyste.ms if the first one was sent.
- [ ] **Records:**
  - [ ] `ops/SERVER.md` section K and its Verification lines;
  - [ ] `ops/DONATIONS.md`;
  - [ ] Milestone 4's yearly check covering all seven domains;
  - [ ] the private notes that name the old site: Claude's memory, walnutbutter-site, tff-stats, and the owner's typeface project.
- [ ] **Later:** the wordmark in the owner's typeface, once it has its lowercase.

**Done when:**
- purelyfreefonts.com and `staging.purelyfreefonts.com` serve the renamed site and stub, and pass step 9's live test.
- Every old address answers with one 301 to the same path and query on the new domain.
- Mail to admin@purelyfreefonts.com and admin@trulyfreefonts.com both arrives.

### Step 14: Soft launch: the list replaces the stub
**Who:** Claude deploys; the owner gives the go-ahead. **Depends on:** 6, 7b, 8, 9, 10, 11, 13, 13b; M2-D8; M1 step 20 (`catalog-site.json` v1, frozen on 2026-09-30 by a rebuild of the run merged in #31); Caddy header phase B live on production (`ops/deploy/README.md`, step 6 of the move into releases), since /privacy says the server sends a CSP and production deploys refuse to run before it. Launching without the tip link needs an owner ruling in AUTHORITY.md, whose Funding section ties the link to this release.
- [ ] Final checks on the test site: all CI checks, step 9's live test, step 10's numbers, the tip link.
- [ ] Confirm each linked Nerd build's license check ([docs/nerd-fonts-link.md](nerd-fonts-link.md)) and hide the marker for any failure (owner ruling of 2026-09-29, `data/reviews/site/2026-09-29.toml`, `nerd_icon_licenses`): the base font's license and the patched icon sets. Stage "links" already leaves out a Nerd Fonts folder whose base license doesn't qualify; the rest is this check.
- [ ] The page says it is an early version and that free font inventory tools are coming (the owner's wording of 2026-09-29, on every page's header).
- [ ] Deploy to production, and remove the stub from `public/` in the repository.
- [ ] On the live site: SERVER.md's and DONATIONS.md's Verification lines, step 9's live test, and `version.txt` matching the merged commit and run date. Tick DONATIONS.md step 12, with its deploy line changed to M2-D6's path.
- [ ] Search engines per M2-D8 (`robots.txt`, `sitemap.xml`).
- [ ] No announcement before Milestone 4 (no Show HN, Reddit, newsletters or awesome-list pull requests); telling testers and linking from the README are fine.
- [ ] AUTHORITY.md: the soft-launch date under Releases, and "Current step".

**Done when:** the live site passes every check above, and the owner has checked it on a phone and a computer.

### Step 15: Usability rounds 2 and 3, on the live site
**Who:** both. **Depends on:** 14.
- [ ] Round 2: 5 new testers, same tasks, more on phones and assistive technology; fix blockers and majors.
- [ ] Round 3, only if round 2 found a blocker or more than 3 majors: 3 testers check the fixes.
- [ ] Review everything sent by issue form or email since the soft launch.
- [ ] Note for Milestone 3 who would use the paste path and the Chromium button, and what they expect; for Milestone 4, what must be fixed before the announcement.

**Done when:** no blocker or major is open, and the notes are in step 17's handoff.

### Step 16: The first monthly refresh reaches the live site
*Moved on 2026-09-30.* The owner moved the first live refresh after Milestone 2 (`refresh_timing`, AUTHORITY.md). This step is now step 2 of [milestone-refresh.md](milestone-refresh.md), with its items and its done-when; until then the live site shows the 2026-09-26 catalog.

### Step 17: Record decisions and hand off to Milestones 3 and 4
**Who:** both. **Depends on:** 15.
- [ ] AUTHORITY.md has dated entries for M2-D1 to M2-D12, including any that testing changed.
- [ ] SERVER.md covers the deploy, test site, headers and Cloudflare settings.
- [ ] Handoff note for Milestone 3: the aliases and step 3's `search_key` vectors; `connect-src 'self'` stays, since matching runs on the device; `local-fonts` stays off except on `/check/` (M3 step 6); the specimen loader's pause (step 5); the hook's missing way to announce a change (WCAG 4.1.3; site/CONTRACT.md section 10), for M3 step 0 to add as an additive v1 field; the test site; and what rounds 2 and 3 learned about pasting a command's output.
- [ ] Handoff note for Milestone 4: the usage baseline, open issues, what must be done before the announcement, the deploy path (M2-D6) to revisit at the two-month review, and the caching choice (M2-D11) with the outage result M4 step 4 should expect.
- [ ] Update the Milestone 2 and 3 status lines in `docs/roadmap.md`.
- [ ] Confirm every completed item here is ticked.
- [ ] "Current step" in AUTHORITY.md moves to Milestone 3.

**Done when:** the owner accepts the handoff.

---

## Decisions

Defaults are in bold. A **[Step 0]** decision is answered before building starts; a **[later OK]** one starts from its default and can change later; a decided one shows its date.

| Decision | Options |
|---|---|
| **M2-D1 (decided 2026-09-25): default rank** on first load | **Overall**, the plainest answer to "the most popular truly free fonts". (Other options were Desktop *most chosen* or Project.) |
| **M2-D2 (decided 2026-09-25): rank numbers under filters** (bands stay bands) | **Renumber 1, 2, 3 …** within each filtered list; fonts past the exact top 100 show their band instead. (Other options were keeping the rank's own numbers, or both.) Replaced on 2026-09-29 by scores shown as bars (step 3). |
| **M2-D3 (decided 2026-09-25): page technology** | **Plain HTML, CSS and one script, no framework**, built by `tff-site` in the uv project: one toolchain, nothing from npm. (Other options were Hugo/Eleventy or Svelte/Preact with Vite.) |
| **M2-D4 (decided 2026-09-25): filter layout** (round 1 may change it) | **Every filter in a sidebar** on wide screens; on phones, all but search and rank behind one "Filters" button. (The other option kept only search, rank, category and "Redistributable fonts only" visible.) The filter set itself was cut down on 2026-09-30 (step 3). |
| **M2-D5 (decided 2026-09-25): font previews.** Under D3, only `preview_ok` (redistributable) fonts get one, and no trimmed or converted font file is served. | **SVG specimens drawn at each refresh (step 5), plus "Type your own text"**, which loads the unchanged upstream file on request after showing its size. Other fonts get fallback text and their official link. (Other options were specimens only, or loading font files as rows scroll in.) |
| **M2-D6 (decided 2026-09-25): deploy path**, also for Milestones 3 and 4, revisited at Milestone 4's two-month review | **GitHub Actions deploys each push to `main`** as a restricted `deploy` user (step 11); merged refreshes go live with no further step, and `ops/deploy.sh` from the laptop is the fallback. |
| **M2-D7 (decided 2026-09-25): a test site before launch** | **`staging.trulyfreefonts.com`** (step 11): round 1 runs there and Milestone 3 reuses it. *Amended 2026-10-02:* the test site moves to `staging.purelyfreefonts.com`, and the old host 301s to it (AUTHORITY.md, **Name and domain**; step 13b). |
| **M2-D8 [later OK]: search engines during the soft launch** | **(a) Indexing allowed**; only announcements wait for Milestone 4; (b) `noindex` until Milestone 4. |
| **M2-D9 (decided 2026-09-25): server logs.** Recorded in AUTHORITY.md (Infrastructure) and [ops/SERVER.md](../ops/SERVER.md) section F. Binding on later milestones: M4 step 3 checks the log against it, and any count Milestone 4 takes from the log runs within 14 days. | Caddy masks visitor IPs to /16 (IPv4) and /32 (IPv6) and drops the port and the `Cf-Connecting-Ip` and `X-Forwarded-For` headers; Caddy's rolling is off, and logrotate keeps 14 days, rotated daily (`ops/Caddyfile`, `ops/logrotate-caddy`). Usage totals come from Cloudflare. *Owner ruling of 2026-09-26 (`log_fields`):* the log also drops `Referer` and `User-Agent`; the same Caddyfile change drops `Cookie` and the location headers finer than the country (owner ruling of 2026-09-28, `log_extra_headers`). Deployed with SERVER.md item 25. |
| **M2-D10 [later OK]: feedback channels** | **(a) GitHub issue forms plus an email link to the site's `admin@` address** for people without GitHub (expect some spam); (b) issues only; (c) email only. |
| **M2-D11 [later OK]: caching pages at Cloudflare.** Hashed assets are cached either way; M4 step 4 verifies the choice and records its outage test as the expected result. | **(a) HTML not edge-cached:** no purges or purge permission, but while the server is down visitors see Cloudflare's error page (not customizable on the Free plan); (b) HTML edge-cached and purged after each deploy (step 11), so pages survive a short outage. |
| **M2-D12 (decided 2026-09-25): blog** (step 7b) | **Markdown posts built by `tff-site`**, with no separate engine; the other options were Zola or Hugo. **Ships with the list release**, not with Milestone 4. **At `/blog/`**, with an Atom feed; `/notes/` and `/news/` were the other options. **Post text under CC BY-SA 4.0**, like the data; CC BY 4.0 and all rights reserved were the other options. |
