# Milestone 4 checklist: full release and running it

Milestone 4 switches on Milestone 3's owned-font comparison for everyone, announces the site, and sets up the monthly routine. Two months after the launch, a review decides whether to keep adding features or run on the monthly refresh alone. Throughout, Claude drafts and the owner posts (M4-D3, M4-D6), and font lists never leave the browser. Settled decisions go in [AUTHORITY.md](../AUTHORITY.md).

**How to read each step:**

- **Who:** the owner, Claude, or both.
- **Depends on:** the steps that must finish first. "M2 …" and "M3 …" refer to [milestone-2.md](milestone-2.md) and [milestone-3.md](milestone-3.md), bare D1–D17 to [ranking-methodology.md](ranking-methodology.md), and M4-D1 to M4-D7 to [Decisions](#decisions).
- **Done when:** what must be true before the step is ticked.
- **Parallel:** where Claude can run several agents at once.

Tick each item as soon as it is done and verified. If an item is only partly done, leave it unticked and note what's left.

**Milestone 4 is done when:**

- every visitor can compare their fonts from the list (M3-D8), by paste on Linux, macOS or Windows or with the button in desktop Chromium;
- the accuracy, privacy and load checks have passed on the live site;
- the owner has posted in every channel chosen in M4-D2;
- two monthly refreshes after the full release were merged and deployed by the runbook;
- the two-month review's decision is recorded in AUTHORITY.md and `docs/roadmap.md`.

**Critical path:** 0 → 1 → (2, 3, 4, 6, 7) → 8 → 11 → 14 → 15. Steps 5 and 10 run alongside 2–8 and finish before 11; step 12 is written during 2–7 and runs monthly after 8, with step 13 inside it; step 9 runs from 8 on; step 14 comes two months after the first launch post.

---

### Step 0: Owner reviews this checklist and decides
**Who:** owner; Claude answers questions and makes edits. **Depends on:** nothing; it can run during Milestone 3, whose Step 0 settles M3-D7, M3-D8 and M3-D10.
- [ ] Claude opens a pull request adding this checklist.
- [ ] The owner answers the five **[Step 0]** decisions:
  - M4-D1 launch timing;
  - M4-D2 channels;
  - M4-D3 posting in person;
  - M4-D4 what "usage is low" means, set before any numbers exist;
  - M4-D5 a report that a listed font isn't purely free.
- [ ] The owner changes any **[later OK]** default they disagree with: M4-D6 issue replies, M4-D7 the monthly section.
- [ ] Claude records each answer in AUTHORITY.md with its date, and adds `docs/milestone-4.md`, `docs/release-checks.md` (step 2), `ops/MONTHLY.md` (step 12) and, if M4-D4 publishes usage, `docs/usage.md` to "Tracked docs".
- [ ] The owner merges the pull request.

**Done when:** no [Step 0] decision is open, and AUTHORITY.md and this checklist are merged to `main`.

### Step 1: Entry check and release candidate
**Who:** both. **Depends on:** 0; Milestone 3 done.
- [ ] Milestone 3's done-when criteria still hold on the live site.
- [ ] Features freeze until the launch (step 11): fixes only, and new ideas go into one "Ideas" issue for step 14.
- [ ] The release candidate is the live list plus the check where M3-D6 (access before the full release) put it, by default `/check/`, tagged `m4-rc1`, `m4-rc2` and so on.
- [ ] Short release notes: what changes for visitors, and what the page does with a pasted list.

**Done when:** the owner has signed off the release candidate for steps 2–7.

### Step 2: Final accuracy check
**Who:** Claude; the owner runs the tools on their own machine. **Depends on:** 1; M3-D11 (accuracy targets); final D3 and D4.
- [ ] Test lists, at least one per path: real output from the owner's CachyOS machine, a Windows 11 machine or VM, and a Mac; synthetic stock installs from Apple's and Microsoft's published font lists and Debian, Ubuntu and Fedora defaults; Milestone 3's known-answer list (renames, Nerd builds, siblings, variable builds, near-matches).
- [ ] Run every list through the release candidate for every published rank, and compare paste with the button on the same machine (M3-D11's agreement target).
- [ ] Spot check 25 random fonts (10 from the overall top 100, 10 from ranks 101–500, and 5 listed only once [milestone-more-fonts.md](milestone-more-fonts.md) has listed them) plus every owner-ruled font: class, redistributable and attribution fields against the license link, and the download link reaching the official page.
- [ ] `docs/release-checks.md` records the test lists, errors and fixes, never a real person's font list.

**Done when:**
- M3-D11's accuracy targets are met on the release candidate;
- no font is wrongly counted as owned;
- no font missed in any rank's top 100 lacks a near-match flag;
- the near-match cases come from Milestone 3's known-answer list, and each is flagged;
- the spot check finds no errors.

**Parallel:** one agent per OS test list, and one for the spot check.

### Step 3: Final privacy check
**Who:** Claude; the owner does one network-tab check. **Depends on:** 1; M2-D9 (server logs); M3-D7 (match data loading), M3-D10 (previews after a comparison), M3-D13 (browser storage).
- [ ] A request inventory lists each request's path and trigger: page load; the match file when a comparison starts (if M3-D7 loads it on demand); a "Load previews" or "Type your own text" click; a click on a report, download or tip link, the only cross-origin ones.
- [ ] A Playwright test, extending M2 step 9's, pastes a test list in Chromium, Firefox and WebKit, then switches every rank and view, toggles "Show fonts I have" and scrolls each view. It fails on:
  - any request after the paste, except a report, download, tip, "Load previews" or "Type your own text" click, or M3-D7's one match-file request, which carries no list data;
  - any name from the list in a request or report link, except the installed name when its box is ticked;
  - any request to another host, or any cookie;
  - any browser storage beyond M3-D13's allowed keys.

  It runs in CI and on the live site after every deploy.
- [ ] Owned fonts never appear in the page's URL.
- [ ] M2's CSP and headers are unchanged, except `Permissions-Policy: local-fonts=(self)` where the check goes live.
- [ ] The live privacy test re-verifies M2 step 9's Cloudflare settings, Always Online off, and no `Set-Cookie` on any kind of path (page, script, style, data, specimen SVG, 404).
- [ ] An access-log sample (`ssh tff`) matches M2-D9's masking and retention.
- [ ] The privacy page matches the inventory: Cloudflare and the server host, what is logged and for how long, step 5's counts, and network-tab steps for Chrome, Firefox and Safari, which Claude tests in each.
- [ ] The owner, with the network tab open, pastes their list and presses "Check my fonts": nothing leaves beyond the inventory.

**Done when:** the test passes on the live release candidate in all three engines, no response sets a cookie, and the owner has done the network-tab check.

**Parallel:** one agent per browser engine.

### Step 4: Load check and caching
**Who:** Claude. **Depends on:** 1; D3 and M2-D5 (font previews); M2-D11 (HTML caching).
- [ ] Verify M2 step 11's caching: hashed `/assets/` files (including specimen SVGs, and font files only if M2-D5 is (b) or (c)) are `immutable` and show `cf-cache-status: HIT` on a repeat request; HTML follows M2-D11 (`no-cache` by default, or `s-maxage` with a purge on each deploy).
- [ ] Record the first-load transfer, compressed, before any specimen.
- [ ] Origin test on the VPS against Caddy directly (`oha`), for the page and the data file; record requests per second and p95 latency in [ops/SERVER.md](../ops/SERVER.md).
- [ ] Outage test in a quiet hour: stop Caddy for 2 minutes and record what visitors see. By M2-D11's default, cached files load but the page shows Cloudflare's own error page; with HTML edge-cached and `stale-if-error`, the page keeps serving.
- [ ] After a deploy (and purge, if needed), the new data is live within 5 minutes.

**Done when:**
- every hashed path shows HIT on a repeat request;
- the first load before any specimen is within M2 step 10's budgets, plus the match file's (M3 step 3) if M3-D7 loads it with the page;
- the origin serves at least 200 requests a second without errors;
- the outage result matches M2-D11's expected outcome, or an issue is open for it.

### Step 5: Usage counts without analytics
**Who:** Claude; the owner adds a token permission. **Depends on:** 4; M4-D4; M2-D9; M3-D7.
- [ ] Owner: add Analytics (read) to the Cloudflare token, or make a read-only one, unless M2 step 16 did.
- [ ] Claude asks the GraphQL API's `settings` node how far back this plan keeps zone totals and per-path counts. With 31 days or more, a monthly laptop script records them; with less, a daily VPS timer with its own read-only token; with none, counted files get `Cache-Control: no-cache` so Caddy's log counts them.
- [ ] Counted per day and path, never per visitor: page views and requests; list-data loads (which skip most crawlers); match-file downloads as comparisons started (if M3-D7 loads on demand); feed fetches (if M4-D7 keeps the feed); 404s.
- [ ] Top referrers from the Caddy log (cache misses only), collected at least every 14 days, within M2-D9's retention.
- [ ] From GitHub: stars, forks, outside issues and pull requests, and repository traffic, recorded at least every 14 days.
- [ ] `ops/usage.sh` prints a month's table; the owner adds tips from Stripe. Numbers go into `ops/USAGE.local.md` (from M2 step 16), and `docs/usage.md` if M4-D4 publishes them. Zone IDs are looked up at run time, never written into tracked files.
- [ ] The privacy page lists these counts (step 3).

**Done when:** the script prints one full week of counts, and its page-view total is within 5% of the Cloudflare dashboard's.

### Step 6: Error pages and failure modes
**Who:** Claude. **Depends on:** 1.
- [ ] M2 step 1's 404 page (Caddy `handle_errors`) works on every path, including the check's; other Caddy errors get a plain page.
- [ ] If JavaScript is off, or the catalog or match file fails to load, the page says so instead of showing an empty list or a wrong result.
- [ ] The page shows the data date, and adds "this month's update is late" past 45 days (checked in the browser).
- [ ] M3 step 4's messages for unreadable input work on the live site and show the right command.

**Done when:** `curl -sI https://purelyfreefonts.com/no-such-page` returns 404 with the custom page, and a browser test shows each failure message.

### Step 7: "Report a wrong match or license"
**Who:** Claude; the owner files the test reports and adds the saved replies. **Depends on:** 1; M4-D5; M2-D10 (feedback channels).
- [ ] Check M2 step 12's issue forms and M3 step 8's `wrong-match.yml`: each form applies its own labels, and `config.yml` disables blank issues and offers the `admin@` contact.
- [ ] Labels: the forms' own (`wrong-match`, `license`, `missing-font`, `bug`, `usability`) exist, plus `idea`, `needs-info`, `fixed-next-update` and `wontfix` for triage.
- [ ] On the live site, the report links (M2 step 4's license link, M3 step 8's "Wrong match?" links) open the right form, prefilled with the font's id and the data date, the installed name only if the visitor ticks its box, and never the rest of the list; each form's first line says reports are public.
- [ ] Hotfix path between monthly runs: edit `data/aliases.csv` or `data/reviews/`, run `uv run tff-catalog refresh --from-snapshots <last date>`, then merge and deploy per M2-D6 (deploy path). Run it once end to end.
- [ ] Claude drafts saved replies, which the owner adds to their GitHub account: fixed, shipping with the next update; not eligible, with the rule; duplicate; more information needed, with the command that shows the installed name.
- [ ] The methodology page says what happens after a license report (M4-D5).

**Done when:** the owner has filed one test report through each form from the live site, each arrived with the right labels and fields, and the step 3 test confirms the Report link carries nothing from the list unless the box is ticked.

### Step 8: Switch the check on for everyone (full release)
**Who:** both. **Depends on:** 2, 3, 4, 6, 7; M4-D1; M3-D8 (how the check joins the list).
- [ ] Build what M3-D8 chose: **merge** (paste path, button and results move onto the list page; `/check/` redirects there with a 301) or **link** (the list's header and footer link to `/check/`, where results stay).
- [ ] Every visitor sees the paste path, desktop Chromium also "Check my fonts", and phones and tablets a note that the check needs a computer.
- [ ] Without a list, the full ranked list shows as in Milestone 2; with one, the missing fonts in rank order, with the "Show fonts I have" toggle, near-match flags and Report links.
- [ ] Remove `noindex` (meta tag and `X-Robots-Tag`) from the check, and list `/check/` in `sitemap.xml` if it stays separate. `local-fonts=(self)` moves with the check.
- [ ] Update `/privacy`: the list is read in the page and never sent; the match-file request (if on demand, M3-D7); the "Load previews" button (M3-D10) and "Type your own text" (M2-D5), each loading only on a click; any storage keys M3-D13 allows.
- [ ] The results link to the methodology page's "How the check works" (M3 step 6).
- [ ] Deploy per M2-D6 (purging if M2-D11 needs it), then rerun the step 3 test and the matching suite on the live site.
- [ ] AUTHORITY.md: "Current step" moves to the launch.

**Done when:** on the live site, a fresh browser profile completes a comparison from the list by paste on Linux, macOS and Windows and with the button in Chromium; no production page sends `noindex`; and the step 3 test passes.

### Step 9: Issue triage routine
**Who:** Claude triages and drafts replies; the owner rules and posts them (M4-D6). **Depends on:** 7. Runs from step 8 onward.
- [ ] Cadence: daily in launch week, weekly for three weeks, then monthly with the refresh. License reports are handled within a week of arriving, by M4-D5.
- [ ] Claude lists the open issues (`gh issue list`), checks each against the catalog and alias table, and proposes one action: an alias or sibling row, an owner ruling in `data/reviews/`, a code fix, or a works-as-designed reply.
- [ ] Emails to `admin@` get the same triage; one becomes a public issue only with the sender's permission.
- [ ] Every confirmed wrong match becomes a known-answer test. Fixes ship with the next refresh; license problems and wrong matches in any top 100 go sooner by the hotfix path (step 7).
- [ ] Ideas wait in the "Ideas" issue until the two-month review. The routine goes into `ops/MONTHLY.md` (step 12).

**Done when:** in launch week, every issue has a label and a reply, and every accepted fix is merged or scheduled.

### Step 10: Launch preparation
**Who:** Claude drafts; the owner approves. **Depends on:** 0 (M4-D1 to M4-D3); D1, D4, D10, D12, D13, D14 and D17 final. It can start during steps 2–7.
- [ ] Re-verify that shared links show a card (M2 step 1's share image and `og:` tags).
- [ ] The week before, Claude checks each channel's current rules: Show HN's (the maker stays to answer; no asking friends for votes), each subreddit's self-promotion and account rules, each newsletter's route, and each awesome list's contributing rules and section (`brabadu/awesome-fonts`: "Free fonts → Collections"; `Jolg42/awesome-typography`: "Font Testing Websites" or "Miscellaneous").
- [ ] Drafts in `ops/LAUNCH.local.md` (untracked through M2's `ops/*.local.md` entry):
  - Show HN: a title under 80 characters without superlatives, and a first comment on what the site does, the license rule, ranking, name matching, the privacy promise and how to check it, credited sources, known gaps, and a call for wrong-match reports;
  - r/typography, on the license rule and ranking; r/linux, on the `fc-list` paste path, the two desktop views and no tracking;
  - a pitch per newsletter, and an entry plus pull request text per awesome list.
- [ ] A reply FAQ: why Poppins or another font is missing (D4, Rules 1–4); why the button is Chromium-only and what others can do; how the ranking works; what the site logs; how to report a wrong match; the data license (D17).
- [ ] Owner: the HN and Reddit accounts meet each channel's rules, and the maker's name (M4-D3) is in the about text.
- [ ] A launch-day watch plan: every 15 minutes, Claude checks the site, `cf-cache-status` and the origin's load (`ssh tff`), and drafts replies on request.

**Done when:** the owner has approved every draft and the accounts are ready.

**Parallel:** one agent per channel draft.

### Step 11: Launch
**Who:** the owner posts; Claude watches and drafts replies. **Depends on:** 5, 8, 10; the tip link live ([ops/DONATIONS.md](../ops/DONATIONS.md) section D, finished at M2 step 14).
- [ ] Owner: post the Show HN when M4-D1 says, then stay to answer (M4-D3).
- [ ] Owner: post to Reddit on the following days, one post a day, no cross-posts.
- [ ] Owner: send the newsletter tips and open the awesome-list pull requests the week after.
- [ ] Claude runs the watch plan and records each post's URL and date in `ops/LAUNCH.local.md`.
- [ ] If the Show HN gets little attention, the owner may repost once, a week or more later (HN's FAQ allows "a small number of reposts").
- [ ] Owner: set a reminder for the two-month review (step 14).
- [ ] AUTHORITY.md: "Releases" item 2 gets the first post's date (M4-D1).

**Done when:** every channel chosen in M4-D2 has a recorded post, submission or pull request, and the launch-week triage (step 9) is done.

### Step 12: Monthly operations runbook
**Who:** Claude writes it and does the monthly work; the owner reviews, rules and merges. **Depends on:** M1 step 20 and [milestone-refresh.md](milestone-refresh.md); M2-D6 (deploy path); D12, D15. Written alongside steps 2–7.
- [ ] `ops/MONTHLY.md`, extending M1 step 20's manual tasks. Each month:
  1. The refresh pull request arrives ([milestone-refresh.md](milestone-refresh.md) step 1); after a hard failure, Claude fixes the cause and reruns it.
  2. Claude summarizes `review.md` with a proposed ruling per flag: aliases (including M3 step 12's ownership changes), licenses, preinstalled and dependency entries, stale sources, big moves, failed links.
  3. The owner rules; Claude pushes fixes (broken links through the override list) until CI passes.
  4. The owner merges, which also resets GitHub's 60-day inactivity clock.
  5. Deploy per M2-D6 (purging if M2-D11 needs it), then the step 3 test and a data-date check.
  6. The monthly section goes out (step 13); the owner closes fixed issues.
  7. Over `ssh tff`: pending reboot, disk space, and Caddy, fail2ban, unattended-upgrades, the watchdog and `cloudflare-ips-sync` running.
  8. Usage counts (step 5) and the owner's time go into `ops/USAGE.local.md`.
- [ ] Font requests (owner ruling `font_requests` of 2026-09-29), in `ops/MONTHLY.md`: the owner passes each emailed request on to Claude, and Claude reads `missing-font` issues itself. Claude checks the font against the gates and the link policy and proposes a ruling; once the owner rules, an accepted family goes into `config/foundries.toml` and is listed from the next refresh. Claude drafts the reply, and the owner sends it.
- [ ] Test the refresh watchdog ([milestone-refresh.md](milestone-refresh.md) step 1; a VPS timer that also warns after 50 days without a commit to `main`) once with a past date, and confirm the alert reaches the owner.
- [ ] When the watchdog fires: check the Actions tab, re-enable the workflow if disabled, run it by hand, read the failure issue.
- [ ] Yearly tasks, each with its timing:
  - the new Web Almanac edition, when its sheets appear;
  - designer lists in January (if D12 is (a));
  - in the launch month, L3 for every catalog font whatever its hash, and `licenses.toml` checked for new license versions;
  - `preinstalled.toml` each spring and autumn;
  - tokens and keys (the refresh's access, [ops/SERVER.md](../ops/SERVER.md) section J; M2-D6's deploy key) renewed before expiry;
  - the snapshot store's size (D15);
  - auto-renewal of all seven domains (purelyfreefonts.com, .org and .net; trulyfreefonts.com, .org and .net; truelyfreefonts.com; see **Domains** in AUTHORITY.md), payment cards and Debian 13 support;
  - Stripe's yearly export ([ops/DONATIONS.md](../ops/DONATIONS.md) step 14).
- [ ] "When something breaks": site down, run [ops/SERVER.md](../ops/SERVER.md)'s verification, then the Contabo panel; bad deploy, M2 step 11's one-command rollback; a broken source goes stale and drops out after 2 months; a license complaint follows M4-D5.
- [ ] Target: under an hour of the owner's time a month.

**Done when:** the runbook is on `main`, the watchdog test has passed, and the first two refreshes after step 8 were merged and deployed by it, with the owner's time recorded.

### Step 13: "New popular free fonts this month"
**Who:** Claude; the owner reviews it in the refresh pull request. **Depends on:** 12; D13 (Rising); M4-D7.
- [ ] If D13 keeps Rising: a dated main-page section, "New popular free fonts: <month year>", with up to 10 rising fonts (beta) and the fonts new to the catalog, each with rank, license and link, and a link to the method.
- [ ] If D13 drops Rising: "Joined the catalog this month" from the monthly diff, or no section (M4-D7).
- [ ] Rising needs 3 months of smoothed history; until the snapshots (D15) have it, the section lists only new fonts and says when Rising starts.
- [ ] A month with nothing new says so rather than padding the list.
- [ ] If M4-D7 keeps them, a static archive page per month (`/new/2026-12/`) and an Atom feed (`/new/feed.xml`), so people can return without accounts or tracking.
- [ ] The entries appear in `review.md`, where the owner can veto any, such as a spike that got past the 2-source rule.

**Done when:** two refreshes in a row have published a dated section (or its "nothing new" note) with its archive page, and the feed passes a validator (if kept).

### Step 14: Two-month review
**Who:** both. **Depends on:** 11 plus two months; 5; M4-D4.
- [ ] Claude compiles the second full month after the first launch post, without the launch spike, against M4-D4's thresholds: step 5's counts; issues, pull requests and emails from others; tips (from Stripe); mentions on HN, Reddit and newsletters, and merged awesome-list pull requests; the owner's monthly time.
- [ ] The owner decides: **keep adding features**, planning the next from the "Ideas" issue (for example the small CLI, M3-D12) as Milestone 5; or **maintenance only**: the monthly refresh, license and wrong-match fixes and security updates, no new features, views or tools, and monthly triage.
- [ ] Revisit M2-D6 (deploy path). For maintenance only, pull deploys are suggested: a VPS timer fetches the built site from the public repository, so no deploy key is kept anywhere.
- [ ] If maintenance only, the README and about text say the list is updated monthly and new features are paused.

**Done when:** the numbers and the decision are written down, and the decision is recorded in AUTHORITY.md with its date.

### Step 15: Record decisions and close
**Who:** both. **Depends on:** 14.
- [ ] AUTHORITY.md gets dated entries for the launch, the review decision and the monthly routine (pointing to `ops/MONTHLY.md`), and "Current step" is updated.
- [ ] `docs/roadmap.md`'s status lines show Milestone 4 done and the review's outcome.
- [ ] PLAN.md's two-month line (local, untracked) records the outcome.
- [ ] Confirm every completed item in this checklist is ticked.

**Done when:** the changes are merged to `main`.

---

## Decisions

Defaults are in bold. A **[Step 0]** decision is answered before building starts; a **[later OK]** one starts from its default and can change later.

| Decision | Options |
|---|---|
| **M4-D1 [Step 0]: launch timing** | **(a) Soft open, then launch:** switch the check on (step 8), then post 1–2 weeks later, once triage is quiet: Tuesday to Thursday, the 8th to the 25th (clear of the refresh), 8–10 am US Eastern, when the owner can stay online 6 hours; the first post's date counts as the full release; (b) switch on and post the same day; (c) a fixed date the owner names now. |
| **M4-D2 [Step 0]: channels** | **Default: Show HN, r/typography, r/linux, pull requests to `brabadu/awesome-fonts` and `Jolg42/awesome-typography`, and tips to three newsletters** from Typewolf, Fonts In Use, Pimp my Type, Typography Weekly, The League of Moveable Type, Web Tools Weekly and Frontend Focus. Optional, off by default: r/webdev (Showoff Saturday only), r/opensource, the owner's Mastodon or Bluesky, Lobsters (by invitation), Product Hunt (a day of the owner's time). |
| **M4-D3 [Step 0]: posting in person.** In every option, the owner picks the maker's name for the site and posts: real name or handle. | **(a) The owner posts from their own accounts as the maker** and answers comments for the first 6 hours; Claude drafts replies on request; (b) the owner posts, then answers once or twice a day; (c) a quiet launch: newsletter tips and awesome-list pull requests only, no Show HN or Reddit. |
| **M4-D4 [Step 0]: what "usage is low" means**, set before any numbers exist and measured over the second full month after the first launch post. The numbers are guesses to adjust. They live in `ops/USAGE.local.md` from M2 step 16, **private by default**; the option is to also publish monthly totals in `docs/usage.md`. | **(a) Low if at least 3 of 4 hold:** under 3,000 loads of the ranked list (about 100 a day); under 300 comparisons started; under 3 issues, pull requests or emails from people other than the owner; under 25 new GitHub stars. If M3-D7 doesn't load the match file on demand, comparisons aren't counted, and 2 of the other 3 must hold; (b) one number: under 3,000 loads of the ranked list; (c) no thresholds: the owner judges. |
| **M4-D5 [Step 0]: a report that a listed font isn't purely free** | **(a) Once the report looks credible, hide the font** by owner ruling and hotfix deploy until its license is rechecked, erring on the safe side; (b) keep it listed with a "license under review" badge; (c) deal with it in the next monthly refresh. |
| **M4-D6 [later OK]: replies to issues** | **(a) Claude drafts, and the owner posts every reply**; (b) Claude may post from the owner's `gh` login, but only step 7's "fixed, shipping with the next update", "duplicate" and "more information needed" replies, and only after the owner approves each one; every other reply stays with (a). |
| **M4-D7 [later OK]: the monthly section** | If D13 keeps Rising: **(a) the section, plus a dated archive page each month and an Atom feed**; (b) the section only. If D13 drops Rising: **(c) "Joined the catalog this month"**, from the monthly diff (the default in that case); (d) no section. |
