// 20-view: which fonts a state shows, in which order, with which rank labels (site/CONTRACT.md
// section 5, "View.compute"; numbering per M2-D2). Pure: the same state, index and filters
// give the same result, and nothing outside the arguments is read or changed, except that a
// Milestone 3 filter that throws is reported (reportError) and treated as 'show' / no note.
const View = (() => {
  // List-index bits (section 7).
  const BIT = Object.freeze({
    mono: 1,
    variable: 2,
    limited: 4,
    attr: 8,
    noRedist: 16,
    windows: 128,
    macos: 256,
    linux: 512,
    android: 1024,
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

  // The M2 filters and the search, as one test per font index.
  const matcher = (state, index) => {
    const { bits, cat, cats, lic, lics, keys } = index;
    const catIndex = state.cat ? cats.indexOf(state.cat) : -1;
    const licSet = state.lic.length ? new Set(state.lic.map((id) => lics.indexOf(id))) : null;
    let hideMask = 0;
    for (const item of state.hide) hideMask |= HIDE_BITS[item] || 0;
    const query = state.q ? Keys.searchKey(state.q) : '';
    // A "|" in the query would match across two names; test each name on its own then.
    const perName = query.includes('|');
    return (i) => {
      const b = bits[i];
      if (state.cat && cat[i] !== catIndex) return false;
      if (state.spacing === 'proportional' && b & BIT.mono) return false;
      if (state.spacing === 'monospaced' && !(b & BIT.mono)) return false;
      if (state.var && !(b & BIT.variable)) return false;
      if (b & hideMask) return false;
      if (licSet && !licSet.has(lic[i])) return false;
      if (state.redist && b & BIT.noRedist) return false;
      if (query) {
        if (perName) return keys[i].split('|').some((key) => key.includes(query));
        return keys[i].includes(query);
      }
      return true;
    };
  };

  // compute(state, index, filters) -> { order, labels, dimmed, notes, shown, total }
  //   state:   State's object (section 9 keys)
  //   index:   the list index (section 7)
  //   filters: Milestone 3's filters in the order added: { id, classify, note,
  //            affectsNumbering }
  // order lists font indexes to show; labels, dimmed and notes run parallel to it. notes[k]
  // is null or [{ filter, note }] in filter order. total is the size of the rank's universe.
  const compute = (state, index, filters = []) => {
    const rank = index.r[state.rank] ? state.rank : DEFAULT_RANK;
    const view = index.r[rank];
    const { tier, top, band, why } = view;
    const n = index.n;

    // 1. The universe: every font whose tier isn't "." (Coding: monospace fonts only).
    let total = 0;
    for (let i = 0; i < n; i += 1) if (tier[i] !== OUTSIDE) total += 1;

    // 4 (first, as it doesn't depend on the filters). Ranked fonts by order, then unranked
    // fonts by name.
    const sequence = view.order.slice();
    for (const i of index.by_name) if (tier[i] === UNRANKED) sequence.push(i);

    // 2. The filters and the search.
    const test = matcher(state, index);
    let order = sequence.filter(test);

    // 3. External filters that renumber.
    const dim = new Set();
    const renumbering = filters.filter((f) => f && f.affectsNumbering === true);
    const keeping = filters.filter((f) => f && f.affectsNumbering !== true);
    order = applyExternal(order, dim, renumbering, index.ids);

    // 5. Numbers (M2-D2): the exact top 100 count from 1; others show their band, and
    // unranked fonts "Not ranked: <reason>".
    const labelOf = new Map();
    let count = 0;
    for (const i of order) {
      if (top[i] > 0) {
        count += 1;
        labelOf.set(i, String(count));
      } else if (band[i] >= 0) {
        labelOf.set(i, index.bands[band[i]]);
      } else {
        const reason = why[i] >= 0 ? index.why_labels[why[i]] : '';
        labelOf.set(i, reason ? `${NOT_RANKED}: ${reason}` : NOT_RANKED);
      }
    }

    // 6. External filters that keep the published numbers.
    order = applyExternal(order, dim, keeping, index.ids);

    // 7. By name, keeping the labels.
    if (state.sort === 'name') {
      const position = new Map(index.by_name.map((i, k) => [i, k]));
      order = order.slice().sort((a, b) => position.get(a) - position.get(b));
    }

    // 8. The result. Notes come from every filter, for every row shown.
    const labels = order.map((i) => labelOf.get(i));
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
    return { order, labels, dimmed, notes, shown: order.length, total };
  };

  return Object.freeze({ compute, BIT, NOT_RANKED });
})();
