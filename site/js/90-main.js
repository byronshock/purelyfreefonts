// 90-main: starts the page and wires the parts (site/CONTRACT.md section 5). The same script
// runs on every page that loads it; only the list page has #list, and everything else here
// waits for it.
//
// Order on the list page: mark html[data-js]; start the specimen loader and Details; attach
// the delegated listeners on #list; fetch the list index (preloaded by the page); then, in an
// idle callback, map the rows, read the hash, render the first view without announcing it,
// show the filters, and start Ext, which publishes globalThis.tff and dispatches
// 'tff:list-ready' (Main dispatches it instead if Ext didn't, so it always fires once). If
// the index doesn't load, the server's list stays as it is, with a note above the count.
//
// Ext's side (50-ext.js): Ext.filters() lists Milestone 3's filters for View.compute, and
// Ext.start(host) runs once. host = { refresh(), onChange(fn), ready, getState(),
// setState(partial, { push }), index() }: refresh() recomputes and redraws, onChange(fn)
// reports { state, shown, total } after every redraw, and ready resolves once the list is
// live. Details owns .details-toggle and the panel: a change of the font key alone redraws
// nothing here.
const Main = (() => {
  // Page wording (owner approval: Milestone 2 step 3).
  const WORDS = Object.freeze({
    stale: 'The list was updated. Reload the page to use filters and search.',
    failed: 'Filters and search didn’t load. Check your connection, then reload the page.',
  });

  let started = false;
  let index = null;
  let list = null;
  let result = null;
  const listeners = new Set();
  let markReady = () => {};
  const ready = new Promise((resolve) => {
    markReady = resolve;
  });

  const report = (error) => {
    if (typeof reportError === 'function') reportError(error);
  };

  // The list index didn't load, or doesn't match the rows: the server's list stays, the
  // filters stay hidden, and a note above the count says why. It comes with the page, so
  // it is not announced.
  const showLoadNote = (error) => {
    const count = document.getElementById('count');
    if (!count || document.getElementById('load-note')) return;
    const text = error instanceof Data.Stale ? WORDS.stale : WORDS.failed;
    count.before(Core.el('p', { id: 'load-note', class: 'noscript-note', text }));
  };

  // Parts written by others may be absent from a build (typeof is safe for those).
  const extPart = () => (typeof Ext === 'object' && Ext ? Ext : null);

  // Call a part's start function (start, else publish, else init), if it has one.
  const startPart = (part, ...args) => {
    const names = ['start', 'publish', 'init'];
    const name = part ? names.find((n) => typeof part[n] === 'function') : null;
    if (!name) return undefined;
    const fn = part[name];
    try {
      return fn.apply(part, args);
    } catch (error) {
      report(error);
      return undefined;
    }
  };

  const extFilters = () => {
    const ext = extPart();
    if (!ext) return [];
    const found = typeof ext.filters === 'function' ? ext.filters() : ext.filters;
    return Array.isArray(found) ? found : [];
  };

  // "a", "a and b", "a; b and c" (names may hold commas)
  const listed = (items) =>
    items.length < 2 ? items.join('') : `${items.slice(0, -1).join('; ')} and ${items.at(-1)}`;

  // The no-results text: it names the filters that are on, so the visitor knows what to
  // loosen (Milestone 2 step 3).
  const noResultsText = (state) => {
    const names = FiltersUI.describe(state);
    if (!names.length) return 'No fonts to show.';
    if (names.length === 1) {
      return `No fonts match this filter: ${names[0]}. Loosen it, or clear the filters.`;
    }
    return (
      `No fonts match these filters together: ${listed(names)}. ` +
      'Loosen one of them, or clear the filters.'
    );
  };

  // Tell the 'change' listeners (Ext's, for Milestone 3) what the list now shows. An Ext
  // that takes changes through Ext.changed() instead of onChange() gets them there.
  const emit = (state) => {
    if (!result) return;
    const change = { state, shown: result.shown, total: result.total };
    const ext = extPart();
    const targets = [...listeners];
    if (!targets.length && ext && typeof ext.changed === 'function') {
      targets.push((c) => ext.changed(c));
    }
    for (const fn of targets) {
      try {
        fn(change);
      } catch (error) {
        report(error);
      }
    }
  };

  // fn({ state, shown, total }) after every redraw. Returns a function that stops it.
  const onChange = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };

  // Recompute the view from the state, the index and the external filters, and redraw.
  // `announce`: false (silent), true (at once) or 'typing' (after a pause).
  const refresh = ({ announce = false } = {}) => {
    if (!index) return null;
    const state = State.get();
    result = View.compute(state, index, extFilters());
    const message = result.shown === 0 ? noResultsText(state) : '';
    const line = Render.apply(result, { message });
    if (announce === 'typing') Announce.typing(message || line);
    else if (announce) Announce.say(message || line);
    emit(state);
    return result;
  };

  const onState = (state, previous, info) => {
    FiltersUI.reflect(state);
    // Details owns the open panel; a font change alone leaves the list as it is.
    if (State.same({ ...state, font: '' }, { ...previous, font: '' })) {
      emit(State.get());
      return;
    }
    refresh({ announce: info.source === 'typing' ? 'typing' : true });
  };

  const clearFilters = () => {
    State.set(State.cleared(State.get()));
  };

  // A click on a Milestone 3 action button in a row: tell whoever added it.
  const onAction = (event, button) => {
    const row = button.closest('li.font');
    const slot = button.closest('.ext');
    if (!row || !slot) return;
    const detail = {
      filterId: slot.dataset.filter,
      fontId: row.dataset.id,
      actionId: button.dataset.action,
    };
    document.dispatchEvent(new CustomEvent('tff:row-action', { detail }));
  };

  // Once, after the first render: let Ext publish the hook, then make sure the page has
  // announced 'tff:list-ready' exactly once.
  const publish = async () => {
    let dispatched = false;
    const seen = () => {
      dispatched = true;
    };
    document.addEventListener('tff:list-ready', seen);
    try {
      await startPart(
        extPart(),
        Object.freeze({
          refresh: () => {
            refresh();
          },
          onChange,
          ready,
          getState: () => State.get(),
          setState: (partial, options) => State.set(partial, options),
          index: () => Data.loadIndex(),
        }),
      );
    } catch (error) {
      report(error);
    }
    document.removeEventListener('tff:list-ready', seen);
    if (!dispatched) {
      document.dispatchEvent(new CustomEvent('tff:list-ready', { detail: globalThis.tff }));
    }
  };

  // The rows by font index: the i-th li.font is font i (section 4). Null if the page and
  // the index disagree, which leaves the server-rendered list as it is.
  const mapRows = () => {
    const byId = new Map();
    for (const row of list.children) {
      if (row.matches('li.font')) byId.set(row.dataset.id, row);
    }
    const rows = index.ids.map((id) => byId.get(id));
    return rows.every(Boolean) && byId.size === index.n ? rows : null;
  };

  const begin = () => {
    const rows = mapRows();
    if (!rows) {
      const error = new Error('Main: the list index does not match the rows on the page');
      showLoadNote(error);
      report(error);
      return;
    }
    State.configure(index);
    const state = State.load();
    FiltersUI.init(index, {
      change: (partial, { typing }) => State.set(partial, { typing }),
      clear: clearFilters,
    });
    FiltersUI.reflect(state);
    Render.init(list, rows);
    // The first view is silent: the page has only just loaded (WCAG 4.1.3 is about changes).
    refresh({ announce: false });
    FiltersUI.show();
    State.subscribe(onState);
    markReady();
    publish();
  };

  const start = () => {
    if (started) return;
    started = true;
    document.documentElement.setAttribute('data-js', '');
    if (typeof Specimens === 'object' && Specimens) startPart(Specimens);
    if (typeof Details === 'object' && Details) startPart(Details);
    list = document.getElementById('list');
    if (!list) return;
    Core.on(list, 'click', 'button.ext-action', onAction);
    const clear = document.getElementById('no-results-clear');
    if (clear) clear.addEventListener('click', clearFilters);
    Data.loadIndex().then(
      (loaded) => {
        index = loaded;
        Core.idle(begin);
      },
      (error) => {
        showLoadNote(error);
        report(error);
      },
    );
  };

  return Object.freeze({
    start,
    refresh,
    onChange,
    ready,
    get result() {
      return result;
    },
  });
})();
Main.start();
