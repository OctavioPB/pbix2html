// Slicer widgets (dropdown, list, tile, hierarchy tree, date range). Shared by both templates.
// The host supplies: host.interactive, host.get(name), host.set({name: value}), host.options(visualId)
// (a Promise of {columns, rows}); values are arrays for multi-select parameters, scalars otherwise.
function slicerWidget(el, v, host) {
  const S = v.slicer, st = S.style || {};
  const isRange = S.mode === 'between' || S.mode === 'before' || S.mode === 'after';
  el.classList.add('sl');
  if (el.parentElement) el.parentElement.classList.add('sl-box');   // let the widget overflow its box
  // Light item text is written for a dark backdrop (a slicer on a navy panel): the control must then not paint its
  // own white box, or white text lands on white (found by `pbix2html verify`).
  const isLight = c => { const m = /^#([0-9a-f]{6})$/i.exec(c || ''); if (!m) return false; const n = parseInt(m[1], 16);
    return (0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255 > 0.6; };
  const fgc = st.color || (st.background ? null : v.fg);   // an explicit control background keeps dark text
  if (fgc) el.style.setProperty('--sl-fg', fgc);
  if (v.fg) el.style.setProperty('--sl-sep', v.fg);          // text that sits on the panel, outside the controls
  if (st.background) el.style.setProperty('--sl-bg', st.background);
  else if (isLight(fgc)) el.style.setProperty('--sl-bg', 'transparent');
  if (st.size) el.style.setProperty('--sl-fs', st.size + 'pt');
  const same = (a, b) => String(a) === String(b);
  const asList = x => (x === null || x === undefined || x === '') ? [] : (Array.isArray(x) ? x : [x]);
  const label = () => S.levels.join(' / ');

  // ---- date range ----
  if (isRange) {
    const box = document.createElement('div'); box.className = 'sl-range';
    const inputs = S.params.map((name, i) => {
      const inp = document.createElement('input');
      inp.type = S.dtypes[i] === 'date' ? 'date' : 'text';
      inp.disabled = !host.interactive;
      inp.title = (S.bounds[i] === 'from' ? 'From ' : 'To ') + S.levels[i];
      inp.addEventListener('change', () => host.set({ [name]: inp.value || null }));
      return inp;
    });
    inputs.forEach((inp, i) => { if (i) { const dash = document.createElement('span'); dash.textContent = '–'; dash.className = 'sl-sep'; box.appendChild(dash); } box.appendChild(inp); });
    const refresh = () => inputs.forEach((inp, i) => { const cur = host.get(S.params[i]); inp.value = cur === null || cur === undefined ? '' : String(cur).slice(0, 10); });
    el.replaceChildren(box); refresh();
    return refresh;
  }

  // ---- values (dropdown / list / hierarchy) ----
  const levels = S.params.length;
  let rows = null, error = null, panel = null;
  const uniq = i => [...new Set(rows.map(r => r[i]))].filter(x => x !== null && x !== undefined);

  // selection as a set of leaf keys; a parent-only selection (p1 set, p2 empty) means "all its children"
  const keyOf = r => r.slice(0, levels).map(String).join('\u0001');
  function selectedKeys() {
    const vals = S.params.map(n => asList(host.get(n)));
    const keys = new Set();
    rows.forEach(r => { if (vals.every((list, i) => !list.length || list.some(x => same(x, r[i])))) keys.add(keyOf(r)); });
    return vals.every(l => !l.length) ? new Set() : keys;
  }
  function commit(keys) {                 // keys → parameter values (see the note in the docs)
    const update = {};
    const sel = rows.filter(r => keys.has(keyOf(r)));
    if (levels === 1) {
      update[S.params[0]] = S.single ? (sel[0] ? sel[0][0] : null) : sel.map(r => r[0]);
    } else {
      const byParent = new Map(), total = new Map();
      rows.forEach(r => total.set(String(r[0]), (total.get(String(r[0])) || 0) + 1));
      sel.forEach(r => byParent.set(String(r[0]), (byParent.get(String(r[0])) || 0) + 1));
      const parents = [...byParent.keys()], full = parents.every(p => byParent.get(p) === total.get(p));
      const raw = p => rows.find(r => String(r[0]) === p)[0];
      if (full) { update[S.params[0]] = parents.map(raw); update[S.params[1]] = []; }
      else { update[S.params[0]] = parents.map(raw); update[S.params[1]] = [...new Set(sel.map(r => r[1]))]; }
      for (let i = 2; i < levels; i++) update[S.params[i]] = [];
    }
    host.set(update);
  }
  const summary = () => {
    if (!rows) return error ? '⚠ ' + error : '…';
    const keys = selectedKeys();
    if (!keys.size) return 'All';
    const leaves = [...new Set(rows.filter(r => keys.has(keyOf(r))).map(r => r[levels - 1]))];
    return leaves.length === 1 ? String(leaves[0]) : leaves.length + ' selected';
  };

  function listBody(container, query) {       // checkbox list or tree
    container.replaceChildren();
    const keys = selectedKeys(), q = (query || '').toLowerCase();
    const mk = (text, checked, onToggle, indent, radio) => {
      const row = document.createElement('label'); row.className = 'sl-item'; row.style.paddingLeft = (0.4 + indent) + 'rem';
      const box = document.createElement('input'); box.type = radio ? 'radio' : 'checkbox'; box.checked = checked; box.disabled = !host.interactive;
      box.addEventListener('change', () => onToggle(box.checked));
      const t = document.createElement('span'); t.textContent = String(text);
      row.append(box, t); return row;
    };
    const toggleKeys = (ks, on) => { const next = new Set(S.single && on ? [] : keys); ks.forEach(k => on ? next.add(k) : next.delete(k)); commit(next); };
    if (S.select_all && !S.single && levels === 1 && !q) {
      const all = rows.length && rows.every(r => keys.has(keyOf(r)));
      container.appendChild(mk('Select all', all, on => commit(on ? new Set(rows.map(keyOf)) : new Set()), 0, false));
    }
    if (levels === 1) {
      rows.filter(r => !q || String(r[0]).toLowerCase().includes(q)).forEach(r =>
        container.appendChild(mk(r[0], keys.has(keyOf(r)), on => toggleKeys([keyOf(r)], on), 0, S.single)));
    } else {
      uniq(0).forEach(parent => {
        const kids = rows.filter(r => same(r[0], parent) && (!q || String(r[1]).toLowerCase().includes(q) || String(parent).toLowerCase().includes(q)));
        if (!kids.length) return;
        const ks = kids.map(keyOf), n = ks.filter(k => keys.has(k)).length;
        const head = mk(parent, n === ks.length, on => toggleKeys(ks, on), 0, false);
        head.classList.add('sl-parent'); head.firstChild.indeterminate = n > 0 && n < ks.length;
        container.appendChild(head);
        kids.forEach(r => container.appendChild(mk(r[1], keys.has(keyOf(r)), on => toggleKeys([keyOf(r)], on), 1.2, false)));
      });
    }
    if (!container.children.length) { const none = document.createElement('div'); none.className = 'loading'; none.textContent = 'No values'; container.appendChild(none); }
  }

  const closePanel = () => { if (panel) { panel.remove(); panel = null; document.removeEventListener('mousedown', outside, true); } };
  const outside = e => { if (panel && !panel.contains(e.target) && !el.contains(e.target)) closePanel(); };
  const head = document.createElement('div'); head.className = 'sl-head';
  const body = document.createElement('div'); body.className = 'sl-list';

  function draw() {
    if (S.mode === 'list') {              // inline list
      el.replaceChildren(body); if (rows) listBody(body, ''); else body.textContent = error ? '⚠ ' + error : '…';
      return;
    }
    if (rows && !rows.length && levels === 1) {   // no list of values: accept typed values
      const inp = document.createElement('input'); inp.type = 'text'; inp.className = 'sl-text';
      inp.placeholder = label() + (S.single ? '' : ' (comma separated)'); inp.disabled = !host.interactive;
      const cur = asList(host.get(S.params[0])); inp.value = cur.join(', ');
      inp.addEventListener('change', () => { const parts = inp.value.split(',').map(x => x.trim()).filter(Boolean); host.set({ [S.params[0]]: S.single ? (parts[0] || null) : parts }); });
      el.replaceChildren(inp); return;
    }
    if (S.mode === 'tile' && levels === 1) {      // a row of toggle chips, not a dropdown or a checkbox list
      const wrap = document.createElement('div'); wrap.className = 'sl-tiles';
      if (!rows) { wrap.textContent = error ? '⚠ ' + error : '…'; }
      else {
        const keys = selectedKeys();
        rows.forEach(r => {
          const chip = document.createElement('button'); chip.type = 'button';
          chip.className = 'sl-tile' + (keys.has(keyOf(r)) ? ' on' : '');
          chip.textContent = String(r[0]); chip.disabled = !host.interactive;
          chip.addEventListener('click', () => {
            const on = !keys.has(keyOf(r));
            const next = new Set(S.single && on ? [] : keys);
            on ? next.add(keyOf(r)) : next.delete(keyOf(r));
            commit(next);
          });
          wrap.appendChild(chip);
        });
      }
      el.replaceChildren(wrap); return;
    }
    const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'sl-btn';
    btn.textContent = summary(); btn.title = label();
    const caret = document.createElement('span'); caret.className = 'sl-caret'; caret.setAttribute('aria-hidden', 'true'); btn.appendChild(caret);
    btn.disabled = !rows; el.replaceChildren(btn);
    btn.addEventListener('click', () => {
      if (panel) { closePanel(); return; }
      panel = document.createElement('div'); panel.className = 'sl-panel';
      const r = btn.getBoundingClientRect();
      panel.style.cssText = `left:${r.left}px;top:${r.bottom + 2}px;min-width:${r.width}px`;
      const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search'; search.className = 'sl-search';
      const list = document.createElement('div'); list.className = 'sl-list';
      search.addEventListener('input', () => listBody(list, search.value));
      panel.append(search, list); listBody(list, ''); panel._list = list; panel._search = search;
      document.body.appendChild(panel); document.addEventListener('mousedown', outside, true); search.focus();
    });
  }
  const refresh = () => { draw(); if (panel && rows) listBody(panel._list, panel._search.value); };

  // options: a list to choose from, or (no query defined) free text
  host.options(v.id).then(block => {
    if (block && block.error) { error = block.error; }
    else if (block && block.rows && block.rows.length && !block.skipped) { rows = block.rows.map(r => r.slice(0, levels)); }
    else { rows = []; }
    draw();
  }).catch(e => { error = e.message; draw(); });
  draw();
  return refresh;
}
