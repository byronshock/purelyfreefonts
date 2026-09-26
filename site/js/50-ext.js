// 50-ext: the Milestone 3 hook, globalThis.tff (site/CONTRACT.md section 10).
//
// Ext keeps the external filters Milestone 3 adds and publishes the hook once the list is
// ready. It has no list logic of its own. Main uses two calls:
//
//   Ext.filters()      the external filters in the order added, for View.compute's third
//                      argument: frozen { id, affectsNumbering, classify(fontId), note(fontId) }.
//                      classify() always answers 'show', 'dim' or 'hide'; note() answers null or
//                      a frozen { text, badge, links: [{ label, href }], actions: [{ id, label }] },
//                      text and badge null when absent, unsafe links already dropped (an href
//                      must stay on this site or be https://).
//   Ext.start(host)    once, after the first render; host is Main's { refresh(), onChange(fn),
//                      ready }: refresh() recomputes and redraws, onChange(fn) reports
//                      { state, shown, total } after every redraw, ready resolves once the list
//                      is live. Ext then publishes globalThis.tff and dispatches tff:list-ready
//                      on document. Returns a promise of the hook.
//
// The hook's getState and setState go through State (section 9's keys only, validated like the
// hash), index() through Data.loadIndex() (a frozen copy), specimens through Specimens.
//
// A filter that throws, or answers something else, leaves its rows shown and without a note;
// the error goes to reportError() once per filter (until it is added again), so the list
// still renders and the console still shows the bug.
const Ext = (() => {
  const CLASSES = new Set(['show', 'dim', 'hide']);
  const SCHEME = /^[a-z][a-z0-9+.-]*:/i;
  // Browsers drop tabs and newlines inside URLs and read "\" as "/", which could turn a
  // harmless-looking relative link into another scheme or host.
  const UNSAFE_CHARS = /[\u0000-\u001f\u007f\\]/;
  const NONE = Object.freeze([]);

  let filters = NONE;

  const report = (error) => {
    if (typeof reportError === 'function') reportError(error);
    else console.error(error);
  };

  const copyValue = (value) => {
    if (Array.isArray(value)) return value.slice();
    if (value instanceof Set) return Array.from(value);
    return value;
  };

  // A copy of `state` holding only section 9's own keys (never extension keys).
  const copyState = (state) => {
    const copy = {};
    if (state === null || typeof state !== 'object') return copy;
    for (const key of State.KEYS) {
      if (Object.prototype.hasOwnProperty.call(state, key)) copy[key] = copyValue(state[key]);
    }
    return copy;
  };

  const frozenState = (state) => {
    const copy = copyState(state);
    for (const value of Object.values(copy)) {
      if (Array.isArray(value)) Object.freeze(value);
    }
    return Object.freeze(copy);
  };

  // A deep, frozen copy of JSON-like data, so Milestone 3 can't change the list's own index.
  const frozenCopy = (value) => {
    if (ArrayBuffer.isView(value)) return Object.freeze(Array.from(value));
    if (Array.isArray(value)) return Object.freeze(value.map(frozenCopy));
    if (value !== null && typeof value === 'object') {
      const copy = {};
      for (const [key, item] of Object.entries(value)) copy[key] = frozenCopy(item);
      return Object.freeze(copy);
    }
    return value;
  };

  const nonEmpty = (value) => (typeof value === 'string' && value.trim() !== '' ? value : null);

  // The href to use, or null: relative links must stay on this site; absolute ones must be
  // https. Everything else (javascript:, data:, http:, //host) is dropped.
  const safeHref = (href) => {
    if (typeof href !== 'string') return null;
    const trimmed = href.trim();
    if (trimmed === '' || UNSAFE_CHARS.test(trimmed)) return null;
    try {
      if (SCHEME.test(trimmed)) {
        return new URL(trimmed).protocol === 'https:' && /^https:\/\//i.test(trimmed)
          ? trimmed
          : null;
      }
      return new URL(trimmed, document.baseURI).origin === location.origin ? trimmed : null;
    } catch {
      return null;
    }
  };

  const noteLinks = (links) => {
    if (!Array.isArray(links)) return NONE;
    const kept = [];
    for (const link of links) {
      const label = nonEmpty(link && link.label);
      const href = label && safeHref(link.href);
      if (href) kept.push(Object.freeze({ label, href }));
    }
    return Object.freeze(kept);
  };

  const noteActions = (actions) => {
    if (!Array.isArray(actions)) return NONE;
    const kept = [];
    for (const action of actions) {
      const id = nonEmpty(action && action.id);
      const label = id && nonEmpty(action.label);
      if (label) kept.push(Object.freeze({ id, label }));
    }
    return Object.freeze(kept);
  };

  // A note in one shape for Render, or null when there is nothing to show.
  const normaliseNote = (note) => {
    if (note === null || note === undefined || note === false) return null;
    if (typeof note === 'string') {
      return nonEmpty(note)
        ? Object.freeze({ text: note, badge: null, links: NONE, actions: NONE })
        : null;
    }
    if (typeof note !== 'object') {
      throw new TypeError(`note() returned a ${typeof note}; want null, a string or an object`);
    }
    const shaped = {
      text: nonEmpty(note.text),
      badge: nonEmpty(note.badge),
      links: noteLinks(note.links),
      actions: noteActions(note.actions),
    };
    const empty = !shaped.text && !shaped.badge && !shaped.links.length && !shaped.actions.length;
    return empty ? null : Object.freeze(shaped);
  };

  // Wrap a Milestone 3 filter so that nothing it does can break the list.
  const guard = (id, spec) => {
    let reported = false;
    const fail = (what, error) => {
      if (reported) return;
      reported = true;
      const message = error instanceof Error ? error.message : String(error);
      report(new Error(`tff filter "${id}": ${what}: ${message}`, { cause: error }));
    };
    const classify = (fontId) => {
      try {
        const answer = spec.classify(fontId);
        if (CLASSES.has(answer)) return answer;
        fail('classify()', new TypeError(`answered ${String(answer)}; want show, dim or hide`));
      } catch (error) {
        fail('classify()', error);
      }
      return 'show';
    };
    const note = (fontId) => {
      if (!spec.note) return null;
      try {
        return normaliseNote(spec.note(fontId));
      } catch (error) {
        fail('note()', error);
        return null;
      }
    };
    return Object.freeze({ id, affectsNumbering: spec.affectsNumbering, classify, note });
  };

  const filtersNow = () => filters;

  let host = null;
  let published = null;
  const listeners = new Set();

  const refresh = () => {
    if (host) host.refresh();
  };

  const addFilter = (id, options) => {
    const { classify, note = null, affectsNumbering = false } = options ?? {};
    if (typeof id !== 'string' || id === '') {
      throw new TypeError('tff.list.addFilter: the id must be a non-empty string');
    }
    if (typeof classify !== 'function') {
      throw new TypeError('tff.list.addFilter: classify must be a function');
    }
    if (note !== null && typeof note !== 'function') {
      throw new TypeError('tff.list.addFilter: note must be a function or null');
    }
    // Called on the options object, so a filter written as an object with methods keeps its
    // `this`.
    const filter = guard(id, {
      classify: (fontId) => classify.call(options, fontId),
      note: note && ((fontId) => note.call(options, fontId)),
      affectsNumbering: Boolean(affectsNumbering),
    });
    const next = filters.slice();
    const at = next.findIndex((item) => item.id === id);
    // Adding an id again replaces that filter where it stands, keeping the order of the rest.
    if (at === -1) next.push(filter);
    else next[at] = filter;
    filters = Object.freeze(next);
    refresh();
  };

  const removeFilter = (id) => {
    const next = filters.filter((item) => item.id !== id);
    if (next.length === filters.length) return;
    filters = next.length ? Object.freeze(next) : NONE;
    refresh();
  };

  const getState = () => copyState(State.get());

  const setState = (partial, { push = true } = {}) => {
    if (partial === null || typeof partial !== 'object' || Array.isArray(partial)) {
      throw new TypeError('tff.list.setState: the partial state must be an object');
    }
    State.set(copyState(partial), { push: Boolean(push) });
  };

  let indexCopy = null;
  const index = () => {
    if (!indexCopy) {
      indexCopy = Promise.resolve()
        .then(() => Data.loadIndex())
        .then(frozenCopy, (error) => {
          indexCopy = null;
          throw error;
        });
    }
    return indexCopy;
  };

  const on = (type, fn) => {
    if (type !== 'change') {
      throw new TypeError(`tff.list.on: no event ${String(type)}; the one event is 'change'`);
    }
    if (typeof fn !== 'function') throw new TypeError('tff.list.on: the listener must be a function');
    const entry = { fn };
    listeners.add(entry);
    return () => {
      listeners.delete(entry);
    };
  };

  const setSummary = (text) => {
    const node = Core.$('#ext-summary');
    if (!node) return;
    const value = text === null || text === undefined ? '' : String(text);
    Core.text(node, value);
    Core.setHidden(node, value === '');
  };

  const specimens = Object.freeze({
    pause: () => {
      Specimens.pause();
    },
    resume: () => {
      Specimens.resume();
    },
    get paused() {
      return Boolean(Specimens.paused);
    },
  });

  // Main's onChange: tell the 'change' listeners, each with the same frozen copy.
  const emit = (change) => {
    if (!listeners.size) return;
    const { state, shown, total } = change || {};
    const detail = Object.freeze({ state: frozenState(state), shown, total });
    for (const { fn } of Array.from(listeners)) {
      try {
        fn(detail);
      } catch (error) {
        report(error);
      }
    }
  };

  const publish = () => {
    const hook = Object.freeze({
      version: 1,
      keys: Keys,
      list: Object.freeze({
        addFilter,
        removeFilter,
        refresh,
        getState,
        setState,
        index,
        on,
        setSummary,
        specimens,
      }),
    });
    globalThis.tff = hook;
    document.dispatchEvent(new CustomEvent('tff:list-ready', { detail: hook }));
    return hook;
  };

  const start = (main) => {
    if (published) return published;
    if (!main || typeof main.refresh !== 'function') {
      throw new TypeError('Ext.start: the host needs a refresh() function');
    }
    host = main;
    if (typeof main.onChange === 'function') main.onChange(emit);
    published = Promise.resolve(main.ready).then(publish);
    return published;
  };

  return Object.freeze({ filters: filtersNow, start });
})();
