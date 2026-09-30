# Usability test plan

**Status:** draft of 2026-09-25, written for [Milestone 2](milestone-2.md) step 12. The owner approves the [consent script](#consent-script) and the [invitation](#invitation) before round 1 (step 13). Round 2 (step 15) reuses this plan, and [Milestone 3](milestone-3.md) step 11 adds the check's tasks. Raw notes stay in `research/`, which git ignores; only anonymized findings leave it, as GitHub issues and the [Results](#results) table.

The test asks one question: can people find a truly free font that fits their need, trust what the list says about it, and understand why it ranks where it does?

## Rounds

| Round | When | Site | Testers |
|---|---|---|---|
| Trial | before round 1 | `https://staging.trulyfreefonts.com` | 1 person, not counted; the script is fixed afterwards (step 13) |
| 1 | before the soft launch (step 13) | `https://staging.trulyfreefonts.com`, with real data from M1 step 16 or later | 5 new people |
| 2 | after the soft launch (step 15) | `https://trulyfreefonts.com` | 5 new people, more of them on phones and assistive technology |
| 3 | only if round 2 found a blocker or more than 3 majors | `https://trulyfreefonts.com` | 3 people check the fixes |

A round is done when:

- every session is written up and its findings are issues;
- no blocker or major is open: each is fixed, or the owner has ruled it out with a reason;
- at least 4 of 5 testers finished tasks 1, 2, 3 and 7 unaided, or each failing task's fix has been checked with 2 more people.

## Participants

- **5 new people per round.** Nobody takes part twice, except in round 3 if new people can't be found.
- **A mix:** at least one designer, one developer and one casual user (someone who picks a font now and then for a document, slides or a small site).
- **Devices:** at least 2 do the whole session on a phone. At least 1 uses the keyboard only or a screen reader, if we can find one; they use their own setup.
- **Adults only** (18 or over), who haven't worked on the site or seen this plan.
- **Recruiting:** by direct message from the owner's contacts and communities. Never by a public post, and never in Milestone 4's launch channels (M4-D2), since nothing is announced before Milestone 4. Testers are asked not to share the link.
- **Codes:** each tester gets a code, numbered across rounds so a code never means two people: round 1 is P1–P5, round 2 starts at P6. Names and contact details live only in `research/usability/people.md`, apart from the notes, and are deleted with them.

## Sessions

- **Length:** 30–40 minutes. About 5 minutes of welcome and consent, 20–30 minutes of tasks, then task 9's questions and thanks.
- **Format:** a remote video call with screen sharing, in a tool the tester already has. Phone testers join from the phone and share its screen; check that this works when booking, not during the session. A screen-reader user shares the computer's sound too, so the owner hears what it reads.
- **Thinking aloud:** the tester says what they look at, what they expect and what they decide.
- **The owner guides and takes notes.** Read each task as written, and paste it into the call's chat so the tester can reread it. Use only neutral prompts: "What are you looking for?", "What do you expect that to do?", "What would you try next?" Don't explain the site, name a control the task doesn't name, or say "right" or "wrong".
- **Help and time:** a task takes about 3 minutes. If the tester is stuck for about 2 minutes, or asks for help twice, give one hint; the task then counts as *helped*. At 5 minutes, or if they give up, move on; it counts as *failed*.
- **Order:** tasks 1–9 in order; any task may be skipped. Each task starts from wherever the last one ended. Phone testers do every task on the phone. Computer testers do task 7 at the end on their own phone if one is at hand; otherwise it is skipped and noted.
- **By email,** for people who can't join a call: the [email version](#tasks-by-email) has the same tasks. It counts as a session, but outcomes are self-reported, so the owner marks a task *unaided* only when the reply shows the answer (the font chosen, the link sent).

**Before each round** (the owner, with Claude):

- Check that the site under test shows the current data, and note its `version.txt` run date in the round's notes.
- Choose task 6's two fonts from the current Overall rank: both well known, close together in the top 30, preferably with different tiers. Use the same pair all round.
- Round 1 only: run the trial session and fix the script. File a test issue through each form, check that it gets its label (step 12), then close it.
- Create `research/usability/round-N/`, with one notes file per session from the [template](#notes-template).

**Before each session:** close other tabs, silence notifications and open the notes file. The tester opens the site in a new browser window; the site stores nothing, so nothing carries over from earlier visits.

## Consent script

*Draft for the owner's approval.* Read it aloud at the start, and note both answers: to the recording, and to taking part.

> Thanks for helping. I'm building trulyfreefonts.com, a list of the most popular fonts that are free for any personal or commercial use. It isn't public yet, so please don't share the link for now.
>
> We're testing the site, not you. If something is hard, that's the site's fault, and it's exactly what I need to find. There are no wrong answers.
>
> I'll ask you to do a few short tasks and to think aloud as you go: what you're looking at, what you expect, and what surprises you. I'll mostly stay quiet and take notes. It takes about 30 minutes. You can skip any task, take a break or stop at any time, without giving a reason.
>
> My notes don't use your name; you'll be a code, like "P3". What I learn becomes public reports on GitHub under that code, with nothing that identifies you, and any quote leaves out anything personal. The notes stay on my computer until this stage of the project is finished, then I delete them.
>
> I'd like to record the screen and sound, only to check my notes. The recording stays on my computer, nobody else sees it, and I delete it within a week. Is that OK? It's fine to say no.
>
> Please share just the browser if you can, and close anything private. The site itself has no cookies, analytics or tracking.
>
> Any questions? Are you happy to go ahead?

Without a clear yes to taking part, thank the tester and end the call. If they agree to a recording, start it after their answer and say "Recording now". Record on the owner's computer, never to the call service's cloud. Without a clear yes, don't record.

## Tasks

Each task has what to **say**, when it's **done** (for the notes, not read aloud) and what to **watch for**.

### 1. A coding font for an app

- **Say:** "You're building an app and need a popular coding font, one where every letter takes the same width. You'll put the font file inside the app. Find one you're allowed to do that with."
- **Done:** they pick a monospace font (from the *Coding fonts* rank, or with Spacing: *Monospaced*) that may be redistributed (no "Not redistributable" badge, "Redistributable: yes" in its details, or "Redistributable fonts only" on), and say why they think they may ship it.
- **Watch for:** the route they take (the Coding rank, Spacing, search, or category Monospace); whether they connect "put the font file inside the app" with redistributing; whether they think every font on the list may be shipped.

### 2. A body-text serif with Polish accents

- **Say:** "You're setting a long report in Polish. Find a serif font that works for the body text and has all the Polish letters." For a tester who reads Vietnamese, say Vietnamese instead.
- **Done:** category Serif, and a font without the "Limited accents" badge (or with "Hide limited accents" on), perhaps with Spacing: *Proportional*; they check the accented letters in the preview or on the official page.
- **Watch for:** whether the "Limited accents" badge and "Hide limited accents" make sense; whether they look at the preview; whether they assume every serif suits body text (Proportional only leaves out monospace fonts). The site only knows "basic Latin" versus "more than basic Latin", so a font without the badge may still lack Vietnamese letters. If the tester trusts it for Vietnamese, note that as a finding about the data, not about the tester.

### 3. Is Inter free for commercial work?

- **Say:** "A friend says Inter is free. Can you use it in paid work for a client? Where would you get it?"
- **Done:** they find Inter, say yes with a reason from the page (its license, the SIL Open Font License), and open its official download link.
- **Watch for:** whether they trust the answer, and why; whether they look for a download button on this site (it hosts no downloads); whether they open the license link.

### 4. Hide your computer's own fonts

- **Say:** "Some fonts on this list came with your computer, so you already have them. Make the list leave those out." Phone testers: "came with your phone".
- **Done:** they tick their system under "Hide fonts that come with" (Windows, macOS, Linux or Android).
- **Watch for:** whether they expect the site to know which fonts they have (Milestone 3's check), and the words they use for it; whether "come with" is clear. An iPhone has no option of its own: note what the tester does, as a finding, not a failure.

### 5. Most chosen and most installed

- **Say:** "Switch the list to the fonts people installed on purpose. Then switch it to the fonts on the most computers. Why did the list change?"
- **Done:** they choose *Desktop: most chosen*, then *Desktop: most installed*, and explain in their own words that *most chosen* leaves out the installs that Linux systems make on their own. A bonus: they spot a font marked "no evidence of deliberate installs" and its "Comes with" or "Pulled in by" tag.
- **Watch for:** whether they read the line under the rank selector; whether "most chosen" and "most installed" make sense; confusion with *Overall*.

### 6. Why does one font rank above another?

- **Say:** "Why does <font A> rank above <font B>? How sure is the site about that?" Use the round's chosen pair.
- **Done:** they open both fonts' details, point to the per-source ranks, and read the tier (Firm, Fair or Rough) or the range, saying in their own words how sure the site is. Opening *How we rank* counts too.
- **Watch for:** jargon: tier, band, range, "below the floor", "not covered"; whether they follow a source to its credit on the methodology page; whether they trust the rank, and why.

### 7. A handwriting font's download page, on a phone

- **Say:** "On your phone: find a handwriting font you like, and open the page where you'd download it."
- **Done:** on a phone, they filter to Handwriting (behind the "Filters" button), pick a font and open its official download link, landing on the maker's page or Google Fonts.
- **Watch for:** finding the "Filters" button and reading its count; tapping small targets; whether the previews read well at phone size; whether the link says where it goes; getting back to the list afterwards.

### 8. Send this view to a friend

- **Say:** "Send the list exactly as you see it now to a friend. For now, send it to me in the chat."
- **Done:** they send the page address (from the address bar or the browser's share menu), and the owner opens it in a new window and sees the same rank, filters and search.
- **Watch for:** looking for a "Share" button; doubting that the address keeps the filters; finding the full address on a phone.

### 9. Questions

Ask each one, and note the answers in the tester's words.

- "What's missing, or what would you add?"
- "Soon the site could also hide the fonts you already have. You'd run a command that lists your fonts, and paste what it prints into the page. The list never leaves your browser. Would you do that? Why, or why not?"
- "The list is updated every month. Would you come back? What would bring you back?"

The answers on pasting feed Milestone 3's handoff (step 17): who would paste, what they would expect, and what worries them.

## Measures

| Measure | Recorded as |
|---|---|
| Outcome, per task | *unaided*; *helped* (one hint); *failed* (gave up, a wrong answer, or 5 minutes); *skipped* |
| Ease, per task | after each task: "How easy or hard was that, from 1, very hard, to 7, very easy?" |
| Problems | what happened, with a severity: blocker, major or minor |
| Quotes | short and word for word, with nothing that identifies the tester |

**Severity:**

- **Blocker:** stops the task, or leaves the tester believing something wrong that matters, for example that a font they may not redistribute can ship in their app.
- **Major:** the tester finishes only with a hint or after a long detour, or misreads something important (a license, redistributing, what a rank measures) even if they finish.
- **Minor:** it slows or annoys the tester, who recovers alone.

A wrong belief about a license or about redistributing is never minor. Claude proposes each severity; the owner confirms it (step 13).

No recording without consent, and no analytics: the site has none, and the test adds none. Screenshots show the site only, cropped of names, notifications and other tabs.

## Notes template

One file per session, `research/usability/round-N/P3.md`:

```markdown
# P3, round 1, YYYY-MM-DD

- Role: designer / developer / casual user
- Device, system, browser:
- Assistive technology: none / screen reader (which) / keyboard only / zoom
- Session: call / by email. Consent: yes. Recording: yes / no
- Site: staging, run_date YYYY-MM-DD. Task 6 pair: <font A>, <font B>

| Task | Outcome | Ease (1–7) | Minutes |
|---|---|---|---|
| 1. Coding font for an app | | | |
| 2. Body-text serif, accents | | | |
| 3. Inter | | | |
| 4. Hide own fonts | | | |
| 5. Most chosen, most installed | | | |
| 6. Why A above B | | | |
| 7. Handwriting, phone | | | |
| 8. Send the view | | | |

## Problems
- Task 1, major: …

## Quotes
- Task 5: "…"

## Task 9 answers
- Missing: …
- Would paste a command's output: …
- Would come back: …
```

## Findings

- Claude writes up each session while the next is being booked (step 13).
- **Anonymized:** codes only. No names, handles, employers, places, or a quote that could identify someone.
- **One issue per finding,** labelled `usability`, with its severity and no names. A problem several testers hit is one issue listing all of them ("P2, P4: 2 of 5").
- Issues are grouped by theme (for example ranks, filters, licenses, details, phones, sharing, wording). If testers stumbled over the filter layout (M2-D4) or the wording, the round revisits it.
- Every blocker and major is fixed, or the owner rules it out with a reason (step 13).

Claude files each finding with `gh issue create --label usability --title "[major] …" --body-file <file>`, using this body:

```markdown
**Severity:** major (proposed; the owner confirms)
**Round and task:** round 1, task 1
**Seen in:** P2, P4 (2 of 5)

**What happened:** …

**Quotes:** "…" (P2)

**Proposed fix:** …
```

## Invitation

*Draft for the owner's approval.* Sent as a direct message.

> Hi <name>, I'm building trulyfreefonts.com: a list of the most popular fonts that are free for any personal or commercial use, ranked from public download and usage data. Before anyone else sees it, I'd like to watch a few people use it and find out what's confusing.
>
> Could you give me about 30 minutes on a video call in the next two weeks? You'd use the site on your computer or phone and think aloud as you go. There's nothing to prepare or install, and you don't need to know anything about fonts. If a call doesn't suit you, I can email you the tasks instead.
>
> I don't record unless you say yes, and my notes use a code, not your name.
>
> If you're up for it, reply with a few times that suit you, and whether you'd use a computer or a phone. Please don't share the site yet; it isn't public.

For someone who uses a screen reader, the keyboard only or zoom, add: "If you use a screen reader, the keyboard only or zoom, your session would help a lot. Please use your usual setup."

**Booking reply,** once they say yes:

> Great, thank you. We're on for <day, time, time zone>, at <call link>. I'll send the site's link when we start, so there's nothing to look at beforehand.
>
> Two quick questions, so I can plan the tasks: what kind of work do you do (for example design, programming, or something else)? And do you use a screen reader, the keyboard only or zoom?
>
> If you'll use your phone, please check before the call that <call app> can share your phone's screen.

**Thank-you,** after the session:

> Thanks again for your time today. It helped a lot. I'll let you know when the fixes are live.

## Tasks by email

For testers who can't join a call. Send the site's link and the tasks in one email:

> Thanks for helping test trulyfreefonts.com: <link>. It isn't public yet, so please don't share the link.
>
> We're testing the site, not you. Please don't spend more than about 30 minutes; it's fine to skip a task or stop early. I'll use your answers under a code, not your name, in public reports on GitHub, leaving out anything personal. Your email stays private, and I delete it with my notes when this stage of the project is finished. By replying, you agree to that; if you'd rather not, just tell me and I won't use your answers.
>
> For each task below, reply with what you did, where you got stuck, and a number from 1 (very hard) to 7 (very easy).
>
> <tasks 1–8, as in "Say" above; task 7 on a phone if you have one; for task 8, paste the link into your reply>
>
> Then, if you like, answer these: <task 9's questions>

## Milestone 3 tasks

Milestone 3 step 11 adds the tasks for checking the fonts you have.

## Results

Anonymized totals per round; the details are in the linked issues.

| Round | Dates | Testers | Unaided on tasks 1, 2, 3, 7 | Blockers and majors open | Issues |
|---|---|---|---|---|---|
| 1 | not run yet | | | | |
| 2 | not run yet | | | | |
