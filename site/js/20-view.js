// 20-view: which fonts a state shows, in which order, with which scores (site/CONTRACT.md
// section 5, "View.compute"; scores per the owner's rulings of 2026-09-29, score_display and
// score_held_fonts, which replaced M2-D2's numbers). Pure: the same state, index and filters
// give the same result, and nothing outside the arguments is read or changed, except that a
// Milestone 3 filter that throws is reported (reportError) and treated as 'show' / no note.
const View = (() => {
  // List-index bits (section 7).
  const BIT = Object.freeze({
    mono: 1,
    variable: 2,
    limited: 4,
    attr: 8,
    windows: 128,
    macos: 256,
    linux: 512,
    android: 1024,
    nerd: 8192,
  });
  // "hide" values and the bit each one hides.
  const HIDE_BITS = Object.freeze({
    limited: BIT.limited,
    attr: BIT.attr,
    windows: BIT.windows,
    macos: BIT.macos,
    linux: BIT.linux,
    android: BIT.android,
  });
  const OUTSIDE = '.';
  const UNRANKED = '-';
  const DEFAULT_RANK = 'overall';
  const NOT_RANKED = 'Not ranked';

  const report = (error) => {
    if (typeof reportError === 'function') reportError(error);
  };

  // A filter's verdict for one font: 'show', 'dim' or 'hide'.
  const classify = (filter, id) => {
    if (typeof filter.classify !== 'function') return 'show';
    try {
      const verdict = filter.classify(id);
      return verdict === 'hide' || verdict === 'dim' ? verdict : 'show';
    } catch (error) {
      report(error);
      return 'show';
    }
  };

  // A filter's note for one font, as { text?, badge?, links?, actions? } or null.
  const noteOf = (filter, id) => {
    if (typeof filter.note !== 'function') return null;
    let note;
    try {
      note = filter.note(id);
    } catch (error) {
      report(error);
      return null;
    }
    if (note === null || note === undefined || note === '') return null;
    if (typeof note === 'string') return { text: note };
    return typeof note === 'object' ? note : { text: String(note) };
  };

  // Run external filters over `order` (font indexes): drop 'hide', mark 'dim'. Filters run
  // in the order added; a 'hide' from any of them wins over 'dim'.
  const applyExternal = (order, dim, filters, ids) => {
    if (!filters.length) return order;
    const kept = [];
    for (const i of order) {
      let verdict = 'show';
      for (const filter of filters) {
        const v = classify(filter, ids[i]);
        if (v === 'hide') {
          verdict = 'hide';
          break;
        }
        if (v === 'dim') verdict = 'dim';
      }
      if (verdict === 'hide') continue;
      if (verdict === 'dim') dim.add(i);
      kept.push(i);
    }
    return kept;
  };

  // The M2 filters and the search, as one test per font index. The index's `cat` is the site
  // category, so "monospace" holds every monospaced font (owner ruling of 2026-09-30).
  const matcher = (state, index) => {
    const { bits, cat, cats, keys } = index;
    const catIndex = state.cat ? cats.indexOf(state.cat) : -1;
    let hideMask = 0;
    for (const item of state.hide) hideMask |= HIDE_BITS[item] || 0;
    const query = state.q ? Keys.searchKey(state.q) : '';
    // A "|" in the query would match across two names; test each name on its own then.
    const perName = query.includes('|');
    return (i) => {
      const b = bits[i];
      if (state.cat && cat[i] !== catIndex) return false;
      if (state.var && !(b & BIT.variable)) return false;
      if (state.nerd && !(b & BIT.nerd)) return false;
      if (b & hideMask) return false;
      if (query) {
        if (perName) return keys[i].split('|').some((key) => key.includes(query));
        return keys[i].includes(query);
      }
      return true;
    };
  };

  // compute(state, index, filters) -> { order, labels, scores, held, dimmed, notes, shown,
  //   total, note }
  //   state:   State's object (section 9 keys)
  //   index:   the list index (section 7)
  //   filters: Milestone 3's filters in the order added: { id, classify, note,
  //            affectsNumbering }
  // order lists font indexes to show; labels (each row's .rank text), scores (0-100, or -1
  // unranked), held (a hollow bar), dimmed and notes run parallel to it. notes[k] is null or
  // [{ filter, note }] in filter order. total is the size of the rank's universe; note is the
  // view's note, or null.
  const compute = (state, index, filters = []) => {
    const rank = index.r[state.rank] ? state.rank : DEFAULT_RANK;
    const view = index.r[rank];
    const { tier, why, s } = view;
    const note = view.note || null;
    const words = index.score_words;
    const n = index.n;

    // 1. The universe: every font whose tier isn't "." (Coding: monospace fonts only).
    let total = 0;
    for (let i = 0; i < n; i += 1) if (tier[i] !== OUTSIDE) total += 1;

    // 4 (first, as it doesn't depend on the filters). Ranked fonts by score (the index's
    // `order`), then unranked fonts by name.
    const sequence = view.order.slice();
    for (const i of index.by_name) if (tier[i] === UNRANKED) sequence.push(i);

    // 2. The filters and the search.
    const test = matcher(state, index);
    let order = sequence.filter(test);

    // 3 and 6. External filters: those with affectsNumbering first, then the others. With
    // scores in place of numbers both only hide or dim; the flag stays for the frozen hook.
    const dim = new Set();
    const renumbering = filters.filter((f) => f && f.affectsNumbering === true);
    const keeping = filters.filter((f) => f && f.affectsNumbering !== true);
    order = applyExternal(order, dim, renumbering, index.ids);
    order = applyExternal(order, dim, keeping, index.ids);

    // 5. Scores: filters only hide rows, so a font's score and bar never depend on them. A
    // held font's bar is hollow, except in a view with a note (held_marker_dev_apps).
    const heldOf = (i) => !note && s[i] >= 0 && view.held[i] === '1';
    const labelOf = (i) => {
      if (s[i] >= 0) {
        return `${words.before}${s[i]}${words.after}${heldOf(i) ? words.held : ''}`;
      }
      const reason = why[i] >= 0 ? index.why_labels[why[i]] : '';
      return reason ? `${NOT_RANKED}: ${reason}` : NOT_RANKED;
    };

    // 7. By name, keeping the scores; then "-desc" reverses the whole order, unranked fonts
    // included (owner ruling of 2026-09-30, sort_header: a true reverse).
    if (state.sort === 'name' || state.sort === 'name-desc') {
      const position = new Map(index.by_name.map((i, k) => [i, k]));
      order = order.slice().sort((a, b) => position.get(a) - position.get(b));
    }
    if (state.sort.endsWith('-desc')) order = order.slice().reverse();

    // 8. The result. Notes come from every filter, for every row shown.
    const labels = order.map(labelOf);
    const scores = order.map((i) => s[i]);
    const held = order.map(heldOf);
    const dimmed = order.map((i) => dim.has(i));
    const noting = filters.filter((f) => f && typeof f.note === 'function');
    const notes = order.map((i) => {
      if (!noting.length) return null;
      const found = [];
      for (const filter of noting) {
        const note = noteOf(filter, index.ids[i]);
        if (note) found.push({ filter: String(filter.id), note });
      }
      return found.length ? found : null;
    });
    return { order, labels, scores, held, words, dimmed, notes, shown: order.length, total, note };
  };

  return Object.freeze({ compute, BIT, NOT_RANKED });
})();
