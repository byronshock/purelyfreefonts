// 25-render: puts a View result on the page (site/CONTRACT.md sections 4 and 10). It reuses
// the server-rendered li.font rows: the rows to show go back into #list, in order, through
// one DocumentFragment; the others are detached (kept here, never given `hidden`). Only the
// .rank cell (a score and its bar, or "Not ranked: <reason>", with the is-unranked class that
// follows it), the dim class and the Milestone 3 slots change inside a row, and only when they
// differ, so an unchanged view costs no DOM work. Focus stays where it was, or moves to a
// neighbouring row when its own row leaves.
const Render = (() => {
  let list = null;
  let rows = [];
  let rankNodes = [];
  let labelNow = [];
  let dimNow = [];
  // Per font index: the notes last drawn, as a string, or '' for none.
  let notesNow = [];
  let orderNow = [];
  let count = null;
  let noResults = null;
  let noResultsText = null;
  let heldLegend = null;
  let viewNote = null;

  // Bind to #list and the rows by font index (rows[i] is font i's li.font). The labels and
  // order already on the page are read back, so a result equal to the server's changes
  // nothing.
  const init = (listNode, rowsByIndex) => {
    list = listNode;
    rows = rowsByIndex;
    rankNodes = rows.map((row) => row.querySelector('.rank'));
    labelNow = rankNodes.map((node) => (node ? node.textContent : ''));
    dimNow = rows.map((row) => row.classList.contains('is-dim'));
    notesNow = rows.map(() => '');
    const position = new Map(rows.map((row, i) => [row, i]));
    orderNow = Array.from(list.children, (row) => position.get(row)).filter(
      (i) => i !== undefined,
    );
    count = document.getElementById('count');
    noResults = document.getElementById('no-results');
    noResultsText = document.getElementById('no-results-text');
    heldLegend = document.getElementById('held-legend');
    viewNote = document.getElementById('view-note');
  };
  // Draw a .rank cell as _row.html.j2 does: the score with its words for screen readers and
  // a bar (hollow when held), or, with no score (-1), the text `label` ("Not ranked: …").
  // Either way its text equals `label`.
  const hiddenText = (text) => Core.el('span', { class: 'visually-hidden', text });
  const drawRank = (node, score, held, label, words) => {
    node.classList.toggle('is-held', held);
    if (score < 0) {
      node.textContent = label;
      return;
    }
    node.replaceChildren(
      hiddenText(words.before),
      String(score),
      hiddenText(`${words.after}${held ? words.held : ''}`),
      Core.el('i', { class: `bar b${score}` }),
    );
  };

  // An href a note may use: a relative URL on this site, or https://. Anything else is
  // dropped. Returns the resolved URL or null.
  const safeHref = (href) => {
    if (typeof href !== 'string') return null;
    // The URL parser ignores these, so they must not hide a scheme from the test below.
    const raw = href.replace(/[\t\n\r]/g, '').replace(/^[\u0000-\u0020]+/, '');
    let url;
    try {
      url = new URL(raw, location.href);
    } catch {
      return null;
    }
    if (/^https:\/\//i.test(raw)) return url.protocol === 'https:' ? url.href : null;
    const relative = !/^[a-z][a-z0-9+.-]*:/i.test(raw) && !/^[\\/]{2}/.test(raw);
    return relative && url.origin === location.origin ? url.href : null;
  };

  // One div.ext for one filter's note on one row, built with Core.el only.
  const extSlot = (filterId, note) => {
    const slot = Core.el('div', { class: 'ext', dataset: { filter: filterId } });
    if (note.badge !== undefined && note.badge !== null && note.badge !== '') {
      slot.append(Core.el('span', { class: 'ext-badge', text: note.badge }));
    }
    if (note.text !== undefined && note.text !== null && note.text !== '') {
      slot.append(Core.el('p', { class: 'ext-note', text: note.text }));
    }
    for (const link of Array.isArray(note.links) ? note.links : []) {
      const href = link ? safeHref(link.href) : null;
      if (href) slot.append(Core.el('a', { class: 'ext-link', href, text: link.label ?? href }));
    }
    for (const action of Array.isArray(note.actions) ? note.actions : []) {
      if (!action || action.id === undefined || action.id === null) continue;
      slot.append(
        Core.el('button', {
          type: 'button',
          class: 'ext-action',
          dataset: { action: String(action.id) },
          text: action.label ?? String(action.id),
        }),
      );
    }
    return slot;
  };

  // The notes as drawn: what extSlot reads, so an equal note is never redrawn.
  const notesKey = (notes) =>
    notes
      ? JSON.stringify(
          notes.map(({ filter, note }) => [
            filter,
            note.badge ?? null,
            note.text ?? null,
            Array.isArray(note.links) ? note.links.map((l) => [l && l.label, l && l.href]) : [],
            Array.isArray(note.actions) ? note.actions.map((a) => [a && a.id, a && a.label]) : [],
          ]),
        )
      : '';

  // Redraw row i's Milestone 3 slots. If `active` (the element focused when apply() began)
  // was inside them, the same control drawn again (same filter, same action or link) gets
  // the focus, else the row's details button. Returns true when it moved the focus.
  const drawNotes = (i, notes, active) => {
    const key = notesKey(notes);
    if (key === notesNow[i]) return false;
    notesNow[i] = key;
    const row = rows[i];
    const box = row.querySelector('.font-row') || row;
    const oldSlots = Core.$$(':scope > .ext', box);
    const holder = active ? oldSlots.find((slot) => slot.contains(active)) : undefined;
    for (const slot of oldSlots) slot.remove();
    const slots = (notes || []).map(({ filter, note }) => extSlot(filter, note));
    box.append(...slots);
    if (!holder) return false;
    const slot = slots.find((s) => s.dataset.filter === holder.dataset.filter);
    let target = null;
    if (slot && active.classList.contains('ext-action')) {
      target = Core.$$('.ext-action', slot).find((b) => b.dataset.action === active.dataset.action);
    } else if (slot && active.classList.contains('ext-link')) {
      target = Core.$$('.ext-link', slot)[Core.$$('.ext-link', holder).indexOf(active)];
    }
    (target || row.querySelector('.details-toggle') || row).focus({ preventScroll: true });
    return true;
  };

  const sameOrder = (a, b) => a.length === b.length && a.every((value, k) => value === b[k]);

  // Where focus goes when its row leaves the list: the details button of the next row still
  // shown (by the old order), else of the one before, else #main.
  const focusNeighbour = (gone, shown) => {
    const from = orderNow.indexOf(gone);
    const after = orderNow.slice(from + 1).find((i) => shown.has(i));
    const before = orderNow
      .slice(0, Math.max(from, 0))
      .reverse()
      .find((i) => shown.has(i));
    const next = after ?? before;
    const target =
      next === undefined
        ? document.getElementById('main')
        : rows[next].querySelector('.details-toggle');
    if (target) target.focus({ preventScroll: true });
  };

  // Put a View result on the page. `message` is the no-results text (used when nothing is
  // shown). Returns the count line.
  const apply = (result, { message = '' } = {}) => {
    const active = document.activeElement;
    const activeRow = active && list.contains(active) ? active.closest('li.font') : null;
    const activeIndex = activeRow ? rows.indexOf(activeRow) : -1;

    if (!sameOrder(result.order, orderNow)) {
      const fragment = document.createDocumentFragment();
      for (const i of result.order) fragment.append(rows[i]);
      list.replaceChildren(fragment);
    }

    let focusMoved = false;
    for (let k = 0; k < result.order.length; k += 1) {
      const i = result.order[k];
      const label = result.labels[k];
      if (label !== labelNow[i] && rankNodes[i]) {
        const score = result.scores ? result.scores[k] : -1;
        const held = Boolean(result.held && result.held[k]);
        drawRank(rankNodes[i], score, held, label, result.words);
        // "Not ranked: <reason>" gets a line of its own (site ruling of 2026-09-26).
        rows[i].classList.toggle('is-unranked', label.startsWith(View.NOT_RANKED));
        labelNow[i] = label;
      }
      const dim = Boolean(result.dimmed[k]);
      if (dim !== dimNow[i]) {
        rows[i].classList.toggle('is-dim', dim);
        dimNow[i] = dim;
      }
      if (drawNotes(i, result.notes[k], active)) focusMoved = true;
    }

    const shown = new Set(result.order);
    if (activeIndex >= 0 && !focusMoved) {
      if (shown.has(activeIndex)) {
        if (active.isConnected && document.activeElement !== active) {
          active.focus({ preventScroll: true });
        }
      } else {
        focusNeighbour(activeIndex, shown);
      }
    }
    orderNow = result.order.slice();

    // The hollow bar's legend while a held font is shown; the view's note (Developers & apps).
    if (heldLegend) Core.setHidden(heldLegend, !(result.held || []).some(Boolean));
    if (viewNote) {
      Core.text(viewNote, result.note || '');
      Core.setHidden(viewNote, !result.note);
    }

    const of = Core.plural(result.total, 'font', 'fonts');
    const line = `Showing ${Core.formatCount(result.shown)} of ${of}`;
    if (count) Core.text(count, line);
    if (noResults) {
      const empty = result.shown === 0;
      if (empty && noResultsText) Core.text(noResultsText, message);
      // The clear button is about to vanish: move focus before it does.
      if (!empty && noResults.contains(document.activeElement)) {
        const first = result.order.length
          ? rows[result.order[0]].querySelector('.details-toggle')
          : null;
        (first || document.getElementById('main')).focus({ preventScroll: true });
      }
      Core.setHidden(noResults, !empty);
    }
    return line;
  };

  // The rows in their current order (font indexes), for tests and Milestone 3.
  const shownOrder = () => orderNow.slice();

  return Object.freeze({ init, apply, shownOrder, safeHref });
})();
