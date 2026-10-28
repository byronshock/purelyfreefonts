// 55-mine: the hopper, "Fonts you have" (Milestone 3, slice 1; site/CONTRACT.md sections 4, 5
// and 11; the owner's rulings of 2026-10-21 to 2026-10-23). A visitor drops a list of their
// fonts anywhere on the page, chooses it with a button or pastes it, and every font they have
// leaves every view of the list. Only once a list arrives does Mine ask Data for the parser
// and matcher module and for the match file (Data.loadMatcher and Data.loadMatch: two GETs of
// fixed same-origin URLs, M3-D7 a); nothing from the list goes in either. A file is read as
// bytes, which the parser decodes (it knows the byte-order marks), and at most one byte past
// the parser's limit, so a huge file is refused without being read whole. What the matcher
// finds is kept in this page's memory only, as the set of font ids the list has and a few
// counts: nothing is stored, and nothing enters the address, the history or the title (M3-D13
// a). A pasted list leaves the paste box once it has been matched.
//
// What a list brings is always in sight, wherever it was dropped: as it arrives, and again
// when its result or its error shows, the Filters panel opens if it is closed (below 60rem)
// and the page scrolls as little as it can to the hopper (bring). So previews pause as soon as
// a list arrives, before that scroll (a list that fails while none has loaded lets them go on
// again), and once a list loads, previews that haven't loaded stay off until a reload
// (Specimens.pause, M3-D10 a): a request for one would tell the server which rows a list left
// on the screen. Mine holds that pause as its own holder, which only it can lift, so the hook's
// resume() never lifts it, nor its release the hook's pause. While a file is dragged over the
// page, html[data-dropping] rings the window.
//
// Mine gives View one built-in filter, which Main puts ahead of the public hook's (View runs
// those that renumber first, section 5), so "Showing N of M" counts the fonts a list has as
// filtered out, the change is announced, and the phone Filters button counts it (FiltersUI's
// extras, while the menu leaves some font out). The Show menu chooses what it does: fonts you
// don't have (the default), all fonts with yours marked (a badge in the row's Milestone 3
// slot), or only fonts you have. "Clear filters" sets the menu back to its default and keeps
// the list (reset); "Forget this list" drops it, and the summary's words with it. Near-matches
// are only counted (M3-D9 waits), and so are the names a list has that the catalog doesn't
// (the line counting them waits).
//
// Failures follow section 11's Loading paragraph: a stale page (Data.Stale) asks for a reload;
// so does a matcher module whose script failed to load or to hand over ('load', 'handoff'),
// since the page adds its script once, ever; a network error, another status, a body that
// isn't JSON or a timeout ('network', 'status', 'json', 'timeout') offers "Try again", with the
// same list; anything else (a 'url', a 'format' or 'run_date' of another file, a file the
// matcher refuses) says the check can't run here. While the page's own list index has failed
// (Main's note above the count says so, and the filters never showed), no list is taken: the
// note is said again and brought into view (host.blocked), and a list on its way when the
// index fails (its 'index'), or when the page's rows turn out not to match it, ends the same
// way. A list that finished before the page's list went live, which Main couldn't redraw or
// announce and whose result was brought into a panel not yet shown, is told once it does
// (begun): its summary before the first count line, the result in view, or Main's note. No
// result is shown that couldn't be computed, and never an empty one in its place: a failed
// list leaves the last one as it was. A list the parser refuses gets its reason in plain words.
const Mine = (() => {
  // Page wording (Milestone 3, slice 1; the owner reviews it with the screens). The summary
  // follows the plan's example, and the owner's ruling of 2026-09-29, site_status_line: fonts
  // are "left out of the list", never "hidden".
  const WORDS = Object.freeze({
    mark: 'You have this font',
    nothing: 'Paste the list first.',
    unreadable: 'That file couldn’t be read. Try choosing it again.',
    cutOff: 'The top of your list seems to be missing: copy all of it, or use the file.',
    forgotten: 'The list is forgotten.',
    copied: 'Copied.',
    copyRefused:
      'Copying isn’t allowed here, so the command is selected: press Ctrl+C, or ⌘C on a Mac.',
    stale: 'The list was updated. Reload the page to check your fonts.',
    reload: 'The font check didn’t load. Check your connection, then reload the page.',
    retry: 'The font check didn’t load. Check your connection, then try again.',
    broken: 'The font check can’t run on this page, so nothing was left out of the list.',
  });
  // The parser's refusals (its ERRORS), in plain words; too_big names the parser's own limit.
  const REFUSALS = Object.freeze({
    empty: 'That list is empty.',
    binary: 'That file isn’t text. Choose the fonts.txt the command wrote.',
    font_file: 'That’s a font file, not a list of fonts. Choose the fonts.txt the command wrote.',
    html: 'That’s a web page, not a list of fonts.',
    command:
      'That’s the command itself. Run it first, then give this page the fonts.txt it writes.',
    not_found:
      'That says “command not found”, so the command didn’t run. Check that you used the one ' +
      'for your system.',
    no_names: 'No font names were found in that list.',
  });
  // The parser's formats that tell which system wrote the list; the others are plain lists.
  const SYSTEMS = Object.freeze({
    'fc-list': 'Linux',
    'fc-list-plain': 'Linux',
    'macos-text': 'macOS',
    'macos-json': 'macOS',
    'windows-registry': 'Windows',
  });
  const MODES = Object.freeze(['missing', 'all', 'only']);
  const DEFAULT_MODE = MODES[0];
  // Data's failure codes a retry can mend, and those that need a reload (section 11). An
  // 'index' never gets here: the page's own list failed, so Main's note answers it (blocked).
  const RETRY = new Set(['network', 'status', 'json', 'timeout']);
  const RELOAD = new Set(['load', 'handoff']);
  const FILES = 'Files';
  const BUSY_SAID_MS = 1000;

  let root = null; // #mine
  let host = null; // Main's { changed({ lead }), open(), blocked() }
  let owned = null; // the ids of the fonts the list has, while one is loaded
  let found = null; // that list's counts: { names, system, have, near, cutOff, total }
  let mode = DEFAULT_MODE;
  let run = 0; // the number of the latest list: a list that arrives later wins
  let pending = null; // while "Try again" shows, the list it takes again: { file } or { text }
  let table = null; // the match file read by the matcher (it holds only the file)
  let tableDoc = null;
  let held = false; // a list has loaded: previews stay off until a reload (M3-D10 a)
  let busyTimer = 0; // says "Checking your list…" if the list is still on its way (setBusy)

  const node = (id) => document.getElementById(id);
  const textOf = (element) => (element ? element.textContent.replace(/\s+/g, ' ').trim() : '');

  // ---- the summary

  const foundLine = () =>
    `Found ${Core.plural(found.names, 'font name', 'font names')} in ` +
    `${found.system ? `a ${found.system} list` : 'your list'}.`;

  const haveLine = () => {
    const of = `of the ${Core.plural(found.total, 'font', 'fonts')} here`;
    if (found.have === 0) return `You have none ${of}.`;
    const how = {
      missing: 'left out of the list',
      all: 'marked in the list',
      only: 'and only those are shown',
    }[mode];
    return `You have ${Core.formatCount(found.have)} ${of}, ${how}.`;
  };

  const nearLine = () =>
    found.near === 1
      ? '1 more font here is only a near match, so it isn’t counted as yours.'
      : `${Core.formatCount(found.near)} more fonts here are only near matches, so they ` +
        'aren’t counted as yours.';

  // Everything the summary says, as one announcement.
  const said = () => {
    if (!found) return '';
    const parts = [foundLine(), haveLine()];
    if (found.near) parts.push(nearLine());
    if (found.cutOff) parts.push(WORDS.cutOff);
    return parts.join(' ');
  };

  const refusal = (code, limits) => {
    if (code === 'too_big') {
      const mb = Core.formatCount(limits.max_bytes / 1000000);
      return `That’s too big for a list of font names: over ${mb} MB.`;
    }
    return REFUSALS[code] || REFUSALS.no_names;
  };

  // What a failure says, and whether "Try again" can mend it. `file` is the match file's own
  // failure, when the matcher module's was first: its script can't tell a page more than one
  // deploy old (a 404) from a network error, and the match file's answer can (section 11).
  const failure = (error, file = null) => {
    if (error instanceof Data.Stale || file instanceof Data.Stale) {
      return { text: WORDS.stale, retry: false };
    }
    const code = error instanceof Data.MatchError ? error.code : '';
    if (RETRY.has(code)) return { text: WORDS.retry, retry: true };
    if (RELOAD.has(code)) return { text: WORDS.reload, retry: false };
    return { text: WORDS.broken, retry: false };
  };

  // ---- the panel

  // Whether the page's own list failed to load, so that no list can be taken; Main then says
  // its note again and brings it into view.
  const blocked = () => Boolean(host && typeof host.blocked === 'function' && host.blocked());

  // Bring `target` into sight: below 60rem the Filters panel opens if it is closed (#mine has
  // no box then), and the page scrolls as little as it can. A dropped file is the hopper's main
  // way in, from anywhere on the page, so what it brings is never told to screen readers alone.
  const bring = (target) => {
    if (!root || !target || target.hidden) return;
    if (!root.getClientRects().length && host && typeof host.open === 'function') host.open();
    target.scrollIntoView({ block: 'nearest' });
  };

  // Previews pause as a list arrives, before bring scrolls the page: a row that came near the
  // screen meanwhile would ask for its preview. A list that fails, while none has loaded, lets
  // them go on again. Mine's pause is its own holder of Specimens' (HOLDER, which nothing else
  // has), so it lifts no pause of the hook's, and the hook lifts none of Mine's.
  const HOLDER = Symbol('Mine');
  const hold = () => {
    Specimens.pause(HOLDER);
  };

  const release = () => {
    if (!held) Specimens.resume(HOLDER);
  };

  // Hide `target`, first moving the focus out of it to a control that stays: the Show menu
  // while a list's summary shows (and isn't what goes), else Choose file.
  const hide = (target) => {
    if (!target || target.hidden) return;
    if (target.contains(document.activeElement)) {
      const result = node('mine-result');
      const keep = result && !result.hidden && !target.contains(result);
      const next = node(keep ? 'mine-show' : 'mine-choose');
      if (next) next.focus();
    }
    Core.setHidden(target, true);
  };

  // "Checking your list…" shows while a list is on its way, and is said once the list has been
  // on its way for BUSY_SAID_MS: a list that settles sooner is announced by its outcome alone,
  // and one that settles later never has a stale "Checking" said after its outcome.
  const setBusy = (on) => {
    clearTimeout(busyTimer);
    const busy = node('mine-busy');
    if (!busy) return;
    Core.setHidden(busy, !on);
    if (on) busyTimer = setTimeout(() => Announce.say(textOf(busy)), BUSY_SAID_MS);
  };

  // Show `text` as the hopper's error, with "Try again" if `retry`, and announce it; '' hides it.
  const showError = (text, retry = false) => {
    const box = node('mine-error');
    if (!box) return;
    if (!text) {
      hide(box);
      return;
    }
    Core.text(node('mine-error-text'), text);
    const again = node('mine-retry');
    if (retry) Core.setHidden(again, false);
    else hide(again);
    Core.setHidden(box, false);
    bring(box);
    Announce.say(text);
  };

  const draw = () => {
    const result = node('mine-result');
    if (!result) return;
    if (!found) {
      hide(result);
      return;
    }
    Core.text(node('mine-summary'), `${foundLine()} ${haveLine()}`);
    const near = node('mine-near');
    Core.text(near, found.near ? nearLine() : '');
    Core.setHidden(near, !found.near);
    const warning = node('mine-warning');
    Core.text(warning, found.cutOff ? WORDS.cutOff : '');
    Core.setHidden(warning, !found.cutOff);
    const show = node('mine-show');
    if (show.value !== mode) show.value = mode;
    Core.setHidden(result, false);
  };

  const changed = (lead) => {
    if (host && typeof host.changed === 'function') host.changed({ lead });
  };

  // ---- the filter View runs, ahead of Milestone 3's that keep the numbering

  const MARK = Object.freeze({ badge: WORDS.mark });
  const filter = Object.freeze({
    id: 'mine',
    affectsNumbering: false,
    classify: (fontId) => {
      if (!owned || mode === 'all') return 'show';
      return owned.has(fontId) === (mode === 'only') ? 'show' : 'hide';
    },
    note: (fontId) => (owned && mode === 'all' && owned.has(fontId) ? MARK : null),
  });

  // The built-in filter while a list is loaded, else null.
  const builtIn = () => (owned ? filter : null);

  // The hopper as a filter that is on, named as its controls name it ("Show: Only fonts you
  // have"), for FiltersUI's describe and activeCount; none while it leaves every font in (all
  // fonts shown, or a list that has none of them and the fonts you don't have shown).
  const extras = () => {
    const show = node('mine-show');
    if (!owned || !show || mode === 'all' || (mode === 'missing' && !owned.size)) return [];
    const label = textOf(Core.$('label[for="mine-show"]'));
    return [`${label}: ${textOf(show.selectedOptions[0])}`];
  };

  // ---- a list arrives

  const settle = (promise) => promise.then((value) => ({ value }), (error) => ({ error }));

  // At most one byte past the parser's limit, which it then refuses as too big.
  const readBytes = (file, limit) => file.slice(0, limit + 1).arrayBuffer();

  // Say the list's summary before the count line (Main redraws), and bring the result into view.
  const tell = () => {
    changed(said());
    bring(node('mine-result'));
  };

  const apply = (source, parsed, result, total) => {
    owned = new Set(Object.keys(result.owned));
    found = Object.freeze({
      names: parsed.counts.entries,
      system: SYSTEMS[parsed.format] || '',
      have: owned.size,
      near: Object.keys(result.near).length,
      // The warning's words are for a paste ("copy all of it, or use the file"): a file the
      // parser finds cut off (a JSON document cut short) gets no line until its own words are
      // ruled on.
      cutOff: source.text !== undefined && parsed.warnings.includes('cut_off'),
      total,
    });
    // Before the list redraws, so no row the list leaves on the screen asks for its preview
    // (hold paused them as the list arrived); from now on Mine never lets go (release).
    held = true;
    Specimens.pause(HOLDER);
    if (source.text !== undefined) {
      const paste = node('mine-paste');
      if (paste) paste.value = '';
    }
    draw();
    tell();
  };

  // Take a list: { file } (dropped or chosen) or { text } (pasted). None is taken while the
  // page's own list failed to load (blocked).
  const take = async (source) => {
    if (blocked()) return;
    run += 1;
    const mine = run;
    const current = () => mine === run;
    pending = null;
    showError('');
    hold();
    setBusy(true);
    bring(node('mine-busy'));
    // Both requests go now, together; neither failure is left unhandled while the other waits.
    const tool = settle(Data.loadMatcher());
    const file = settle(Data.loadMatch());
    // The list ends with no result, `text` its error, if it is still the latest.
    const stop = (text, retry = false) => {
      if (!current()) return;
      pending = retry ? source : null;
      setBusy(false);
      release();
      showError(text, retry);
    };
    // The list index failed while the list was on its way, or the page's rows don't match it:
    // Main's note answers, and the list ends with nothing. True then.
    const noted = () => {
      if (!blocked()) return false;
      setBusy(false);
      release();
      return true;
    };
    const fail = (error, fileError = null) => {
      if (!current() || noted()) return;
      const { text, retry } = failure(error, fileError);
      stop(text, retry);
    };
    const got = await tool;
    if (!current()) return;
    if (got.error) {
      fail(got.error, (await file).error);
      return;
    }
    const api = got.value;
    const limits = api.OwnedParse.LIMITS;
    let input = source.text;
    try {
      if (source.file) input = await readBytes(source.file, limits.max_bytes);
    } catch {
      stop(WORDS.unreadable);
      return;
    }
    if (!current()) return;
    const parsed = api.OwnedParse.parse(input);
    input = null;
    if (parsed.error) {
      stop(refusal(parsed.error, limits));
      return;
    }
    const answer = await file;
    if (!current()) return;
    if (answer.error) {
      fail(answer.error);
      return;
    }
    let result;
    try {
      if (tableDoc !== answer.value) {
        table = api.OwnedMatch.table(answer.value);
        tableDoc = answer.value;
      }
      result = api.OwnedMatch.match(parsed, table);
    } catch (error) {
      fail(error);
      return;
    }
    if (noted()) return;
    setBusy(false);
    apply(source, parsed, result, answer.value.ids.length);
  };

  // Main's begin has run, the list index loaded meanwhile: the page's list went live, or its
  // rows didn't match the index (Main's note shows). A list that finished before then was
  // announced to no one (Main redraws nothing before the list is live) and brought into a
  // panel not yet shown: its summary is now said before the first count line and the result
  // comes into view, or, where the rows didn't match, Main's note is said and brought into
  // view instead (blocked).
  const begun = () => {
    if (found && !blocked()) tell();
  };

  // ---- the controls

  const check = () => {
    const paste = node('mine-paste');
    const text = paste ? paste.value : '';
    // An empty box is no list: nothing is asked for.
    if (!/\S/.test(text)) {
      showError(WORDS.nothing);
      return;
    }
    take({ text });
  };

  const chosen = () => {
    const input = node('mine-file');
    const file = input.files && input.files[0];
    // Emptied, so choosing the same file again is a change too.
    input.value = '';
    if (file) take({ file });
  };

  const choose = () => {
    const value = node('mine-show').value;
    const next = MODES.includes(value) ? value : DEFAULT_MODE;
    if (next === mode) return;
    mode = next;
    if (!owned) return;
    draw();
    changed('');
  };

  const forget = () => {
    run += 1; // a list still on its way is dropped too
    owned = null;
    found = null;
    pending = null;
    mode = DEFAULT_MODE;
    const paste = node('mine-paste');
    if (paste) paste.value = '';
    const show = node('mine-show');
    if (show) show.value = DEFAULT_MODE;
    setBusy(false);
    showError('');
    hide(node('mine-result'));
    // Its words go too: nothing of the list stays in the page, hidden or not.
    for (const id of ['mine-summary', 'mine-near', 'mine-warning']) Core.text(node(id), '');
    changed(WORDS.forgotten);
  };

  // "Clear filters": the Show menu back to its default, the list kept. True if it changed.
  const reset = () => {
    if (!owned || mode === DEFAULT_MODE) return false;
    mode = DEFAULT_MODE;
    draw();
    return true;
  };

  // Copy a command, or, where the clipboard is refused, select it and say so.
  const copy = async (button) => {
    const code = node(button.dataset.command);
    if (!code) return;
    let copied = false;
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(code.textContent);
        copied = true;
      }
    } catch {
      copied = false;
    }
    if (!copied) {
      const selection = getSelection();
      if (selection) selection.selectAllChildren(code);
    }
    const text = copied ? WORDS.copied : WORDS.copyRefused;
    const note = button.parentElement && button.parentElement.querySelector('.mine-copied');
    if (note) {
      Core.text(note, text);
      Core.setHidden(note, false);
    }
    Announce.say(text);
  };

  // ---- files dropped anywhere on the page: taken, so the browser never opens them

  const hasFiles = (event) =>
    Boolean(event.dataTransfer) && Array.from(event.dataTransfer.types || []).includes(FILES);

  // The drop zone, and a ring round the window (27-mine.css), since the zone may be out of
  // sight.
  const dragging = (on) => {
    if (root) root.classList.toggle('is-dropping', on);
    document.documentElement.toggleAttribute('data-dropping', on);
  };

  const over = (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    dragging(true);
  };

  const left = (event) => {
    if (event.relatedTarget === null) dragging(false);
  };

  const dropped = (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragging(false);
    const file = event.dataTransfer.files[0];
    if (file) take({ file });
    else if (!blocked()) showError(WORDS.unreadable);
  };

  // Bind to #mine, once; given Main's { changed({ lead }), open(), blocked() }: changed redraws
  // and announces the list, open opens the Filters panel below 60rem, and blocked, while the
  // page's list failed to load, says its note again, brings it into view and returns true. A
  // page without #mine takes no list.
  const start = (given) => {
    if (root) return;
    root = node('mine');
    if (!root) return;
    host = given || null;
    for (const type of ['dragenter', 'dragover']) document.addEventListener(type, over, true);
    document.addEventListener('dragleave', left, true);
    document.addEventListener('drop', dropped, true);
    Core.on(root, 'click', '#mine-choose', () => node('mine-file').click());
    node('mine-file').addEventListener('change', chosen);
    Core.on(root, 'click', '#mine-check', check);
    Core.on(root, 'click', '#mine-forget', forget);
    Core.on(root, 'click', '#mine-retry', () => {
      if (pending) take(pending);
    });
    node('mine-show').addEventListener('change', choose);
    Core.on(root, 'click', 'button.mine-copy', (event, button) => {
      copy(button);
    });
  };

  return Object.freeze({ start, begun, filter: builtIn, extras, reset, WORDS, MODES });
})();
