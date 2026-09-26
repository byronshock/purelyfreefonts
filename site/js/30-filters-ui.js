// 30-filters-ui: the controls in #filters (site/CONTRACT.md section 4; M2-D4). Each control's
// name is its hash key and its value the key's value, so reading a change is generic. The
// block ships with `hidden`; show() removes it once the state is known, so the controls never
// appear with the wrong values. Below 60rem, #f-toggle opens #f-more: the CSS follows its
// aria-expanded, and its text counts the filters that are on. Wording is read from the
// controls' own labels and legends, so the template stays the one home for it.
const FiltersUI = (() => {
  let root = null;
  let views = [];
  let onChange = () => {};
  let onClear = () => {};

  const byName = (name) => Core.$$(`[name="${name}"]`, root);

  // The visible text of a control's label or a fieldset's legend, spaces collapsed.
  const textOf = (node) => (node ? node.textContent.replace(/\s+/g, ' ').trim() : '');
  const labelOf = (input) => textOf(input.closest('label') || Core.$(`label[for="${input.id}"]`));
  const legendOf = (id) => textOf(Core.$(`#${id} > legend`, root));

  // The partial state one control's group now asks for.
  const read = (name) => {
    const inputs = byName(name);
    switch (name) {
      case 'rank':
        return { rank: inputs[0].value };
      case 'cat':
      case 'spacing':
      case 'sort': {
        const on = inputs.find((input) => input.checked);
        return { [name]: on ? on.value : '' };
      }
      case 'lic':
      case 'hide':
        return { [name]: inputs.filter((input) => input.checked).map((input) => input.value) };
      case 'var':
      case 'redist':
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
    (state.cat ? 1 : 0) +
    (state.spacing ? 1 : 0) +
    (state.var ? 1 : 0) +
    state.hide.length +
    state.lic.length +
    (state.redist ? 1 : 0);

  const setChecked = (input, checked) => {
    if (input.checked !== checked) input.checked = checked;
  };

  // Show a state in the controls. The search box is left alone while it already holds the
  // same text, so the caret never jumps.
  const reflect = (state) => {
    if (!root) return;
    const rank = Core.$('#f-rank', root);
    if (rank && rank.value !== state.rank) rank.value = state.rank;
    for (const name of ['cat', 'spacing', 'sort']) {
      for (const input of byName(name)) setChecked(input, input.value === state[name]);
    }
    for (const name of ['lic', 'hide']) {
      for (const input of byName(name)) setChecked(input, state[name].includes(input.value));
    }
    for (const name of ['var', 'redist']) {
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
  // "Category: Serif", "Variable fonts only", "Search: “inter”".
  const describe = (state) => {
    if (!root) return [];
    const names = [];
    const choice = (name, fieldset) => {
      const input = byName(name).find((i) => i.value === state[name]);
      if (state[name] && input) names.push(`${legendOf(fieldset)}: ${labelOf(input)}`);
    };
    const boxes = (name, value) => {
      const input = byName(name).find((i) => i.value === value);
      if (input) names.push(labelOf(input));
    };
    choice('cat', 'f-cat');
    choice('spacing', 'f-spacing');
    if (state.var) boxes('var', '1');
    if (state.hide.includes('limited')) boxes('hide', 'limited');
    if (state.lic.length) {
      const chosen = byName('lic').filter((i) => state.lic.includes(i.value)).map(labelOf);
      names.push(`${legendOf('f-license')}: ${chosen.join(' or ')}`);
    }
    if (state.hide.includes('attr')) boxes('hide', 'attr');
    if (state.redist) boxes('redist', '1');
    const systems = byName('hide')
      .filter((i) => i.closest('#f-system') && state.hide.includes(i.value))
      .map(labelOf);
    if (systems.length) names.push(`${legendOf('f-system')} ${systems.join(', ')}`);
    // A search of spaces, hyphens or underscores only matches every font (View): not named.
    const search = textOf(Core.$('label[for="f-q"]', root));
    if (Keys.searchKey(state.q)) names.push(`${search}: “${state.q.trim()}”`);
    return names;
  };

  const handle = (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement || input instanceof HTMLSelectElement)) return;
    const typing = input.name === 'q';
    // The search follows `input`; `change` on it (blur, Enter) adds nothing.
    if (typing !== (event.type === 'input')) return;
    const partial = read(input.name);
    if (partial) onChange(partial, { typing });
  };

  // Bind to #filters. `index` gives the views' measures lines; `handlers.change(partial,
  // { typing })` runs for every change a visitor makes, `handlers.clear()` for #f-clear.
  const init = (index, handlers) => {
    root = document.getElementById('filters');
    if (!root) return false;
    views = index.views;
    onChange = handlers.change;
    onClear = handlers.clear;
    root.addEventListener('change', handle);
    root.addEventListener('input', handle);
    Core.on(root, 'click', '#f-clear', () => onClear());
    Core.on(root, 'click', '#f-toggle', (event, toggle) => {
      const open = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
    });
    return true;
  };

  const show = () => {
    if (root) Core.setHidden(root, false);
  };

  return Object.freeze({ init, reflect, describe, activeCount, show });
})();
