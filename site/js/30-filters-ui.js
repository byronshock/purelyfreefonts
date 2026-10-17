// 30-filters-ui: the controls in #filters, and the sort buttons over the list, #list-sort
// (site/CONTRACT.md section 4; M2-D4; owner rulings of 2026-09-30). Each filter control's
// name is its hash key and its value the key's value, so reading a change is generic. Both
// blocks ship with `hidden`; show() removes it once the state is known, so the controls never
// appear with the wrong values. Below 60rem, #f-toggle opens #f-more: the CSS follows its
// aria-expanded, and its text counts the filters that are on. Wording is read from the
// controls' own labels and legends, so the template stays the one home for it.
//
// The hide key has checkboxes (limited, attr) and one select (#f-os, the operating
// systems), which offers one system at a time.
//
// Sorting (owner ruling of 2026-09-30, sort_header): a button over each column, Popularity
// and Name. Clicking the one in use reverses its order; clicking the other sorts by it, in its
// usual order (most popular first, name A to Z). The words for each order are the button's own
// data-asc and data-desc (and data-asc-spoken, data-desc-spoken for screen readers), so the
// template stays their home.
const FiltersUI = (() => {
  let root = null;
  let sortBar = null; // #list-sort, the sort buttons over the list
  let views = [];
  let onChange = () => {};
  let onClear = () => {};
  let current = null; // the state last shown, for the sort buttons

  const byName = (name) => (root ? Core.$$(`[name="${name}"]`, root) : []);
  const sortButtons = () => (sortBar ? Core.$$('button[data-sort]', sortBar) : []);

  // The visible text of a control's label or a fieldset's legend, spaces collapsed.
  const textOf = (node) => (node ? node.textContent.replace(/\s+/g, ' ').trim() : '');
  const labelOf = (input) => textOf(input.closest('label') || Core.$(`label[for="${input.id}"]`));
  const legendOf = (id) => textOf(Core.$(`#${id} > legend`, root));

  const isSelect = (control) => control instanceof HTMLSelectElement;

  // The partial state one control's group now asks for.
  const read = (name) => {
    const inputs = byName(name);
    switch (name) {
      case 'rank':
        return inputs.length ? { rank: inputs[0].value } : null;
      case 'cat': {
        const on = inputs.find((input) => input.checked);
        return { cat: on ? on.value : '' };
      }
      case 'hide':
        return {
          hide: inputs
            .filter((input) => (isSelect(input) ? input.value !== '' : input.checked))
            .map((input) => input.value),
        };
      case 'var':
      case 'nerd':
        return { [name]: inputs.some((input) => input.checked) };
      case 'q':
        return { q: inputs[0].value };
      default:
        return null;
    }
  };

  // How many filters are on, for the phone button: each chosen option or box counts once.
  // The search, rank and sort order are outside #f-more, or not filters, so they don't count.
  const activeCount = (state) =>
    (state.cat ? 1 : 0) + (state.var ? 1 : 0) + (state.nerd ? 1 : 0) + state.hide.length;

  const setChecked = (input, checked) => {
    if (input.checked !== checked) input.checked = checked;
  };

  const setValue = (select, value) => {
    if (select && select.value !== value) select.value = value;
  };

  // The sort key ("rank", "name") and whether it is reversed, from a state's sort value.
  const sortOf = (state) => {
    const desc = state.sort.endsWith('-desc');
    return { key: desc ? state.sort.slice(0, -5) : state.sort, desc };
  };

  // What a click on `button` asks for: its column reversed if it is the one in use, else its
  // column in the usual order.
  const nextSort = (button, state) => {
    const { key, desc } = sortOf(state);
    const own = button.dataset.sort;
    return own === key && !desc ? `${own}-desc` : own;
  };

  // Draw one sort button: data-dir ("asc", "desc" or none) picks which of the two stacked
  // arrows the CSS fills, and the column in use shows the order's words (with what a click
  // does, for screen readers).
  const drawSort = (button, state) => {
    const { key, desc } = sortOf(state);
    const on = button.dataset.sort === key;
    const words = on ? button.dataset[desc ? 'desc' : 'asc'] : '';
    const spoken = on ? button.dataset[desc ? 'descSpoken' : 'ascSpoken'] : '';
    const other = button.dataset[desc ? 'ascSpoken' : 'descSpoken'];
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
    const label = textOf(Core.$('.sort-label', button)).toLowerCase();
    button.title = on ? `Show ${other} instead` : `Sort by ${label}`;
    if (on) button.dataset.dir = desc ? 'desc' : 'asc';
    else delete button.dataset.dir;
    const dir = Core.$('.sort-dir', button);
    Core.clear(dir);
    if (on) {
      Core.append(
        dir,
        Core.el('span', { 'aria-hidden': 'true', text: words }),
        Core.el('span', { class: 'visually-hidden', text: ` ${spoken}; select to show ${other}` }),
      );
    }
  };

  // The announcement of a sort order: "Sorted by name, Z to A".
  const sortSaid = (state) => {
    const { key, desc } = sortOf(state);
    const button = sortButtons().find((b) => b.dataset.sort === key);
    if (!button) return '';
    const label = textOf(Core.$('.sort-label', button)).toLowerCase();
    return `Sorted by ${label}, ${button.dataset[desc ? 'descSpoken' : 'ascSpoken']}`;
  };

  // Show a state in the controls. The search box is left alone while it already holds the
  // same text, so the caret never jumps.
  const reflect = (state) => {
    if (!root) return;
    setValue(Core.$('#f-rank', root), state.rank);
    for (const button of sortButtons()) drawSort(button, state);
    current = state;
    for (const input of byName('cat')) setChecked(input, input.value === state.cat);
    for (const control of byName('hide')) {
      if (isSelect(control)) {
        const chosen = [...control.options].find((o) => o.value && state.hide.includes(o.value));
        setValue(control, chosen ? chosen.value : '');
      } else {
        setChecked(control, state.hide.includes(control.value));
      }
    }
    for (const name of ['var', 'nerd']) {
      for (const input of byName(name)) setChecked(input, state[name]);
    }
    const q = Core.$('#f-q', root);
    if (q && q.value !== state.q) q.value = state.q;
    const view = views.find((v) => v.key === state.rank);
    const measures = Core.$('#f-rank-measures', root);
    if (measures && view) Core.text(measures, view.measures);
    const counter = Core.$('#f-toggle .filters-count', root);
    const on = activeCount(state);
    // A no-break space: the button is a flex box, which would drop a plain leading space.
    if (counter) Core.text(counter, on ? `\u00A0(${Core.formatCount(on)})` : '');
  };

  // The filters that are on, named as the controls name them, in the order they appear:
  // "Category: Serif", "Adjustable weight (variable font)", "Hide fonts that come with Windows",
  // "Search: “inter”".
  const describe = (state) => {
    if (!root) return [];
    const names = [];
    const boxes = (name, value) => {
      const input = byName(name).find((i) => !isSelect(i) && i.value === value);
      if (input) names.push(labelOf(input));
    };
    const cat = byName('cat').find((i) => i.value === state.cat);
    if (state.cat && cat) names.push(`${legendOf('f-cat')}: ${labelOf(cat)}`);
    if (state.var) boxes('var', '1');
    if (state.nerd) boxes('nerd', '1');
    if (state.hide.includes('limited')) boxes('hide', 'limited');
    if (state.hide.includes('attr')) boxes('hide', 'attr');
    const os = Core.$('#f-os', root);
    const system = os && [...os.options].find((o) => o.value && state.hide.includes(o.value));
    if (system) names.push(`${labelOf(os)} ${textOf(system)}`);
    // A search of spaces, hyphens or underscores only matches every font (View): not named.
    const search = textOf(Core.$('label[for="f-q"]', root));
    if (Keys.searchKey(state.q)) names.push(`${search}: “${state.q.trim()}”`);
    return names;
  };

  const handle = (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement || isSelect(input))) return;
    const typing = input.name === 'q';
    // The search follows `input`; `change` on it (blur, Enter) adds nothing.
    if (typing !== (event.type === 'input')) return;
    const partial = read(input.name);
    if (partial) onChange(partial, { typing });
  };

  // Bind to #filters and #list-sort. `index` gives the views' measures lines;
  // `handlers.change(partial, { typing })` runs for every change a visitor makes,
  // `handlers.clear()` for #f-clear.
  const init = (index, handlers) => {
    root = document.getElementById('filters');
    if (!root) return false;
    sortBar = document.getElementById('list-sort');
    views = index.views;
    onChange = handlers.change;
    onClear = handlers.clear;
    root.addEventListener('change', handle);
    root.addEventListener('input', handle);
    if (sortBar) {
      Core.on(sortBar, 'click', 'button[data-sort]', (event, button) => {
        if (current) onChange({ sort: nextSort(button, current) }, { typing: false });
      });
    }
    Core.on(root, 'click', '#f-clear', () => onClear());
    Core.on(root, 'click', '#f-toggle', (event, toggle) => {
      const open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
    });
    return true;
  };

  const show = () => {
    if (root) Core.setHidden(root, false);
    if (sortBar) Core.setHidden(sortBar, false);
  };

  return Object.freeze({ init, reflect, describe, activeCount, show, sortSaid });
})();
