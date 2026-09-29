/* Runs in the page (see verify.py): looks at ONE visible page section as rendered and returns raw findings.
   Everything is measured from the live DOM (geometry, computed colours, what is painted on top of what), so
   it sees what a person sees: text the same colour as what is behind it, text hidden under another visual or
   an image, clipped text, empty or failed visuals, chart labels that will not fit. Returns
   [{rule, severity, visual, message, hint, rect: [x, y, w, h]}] with rect relative to the section. */
(section) => {
  const out = [], collected = [];
  const sec = section.getBoundingClientRect();
  const rel = r => [Math.round(r.left - sec.left), Math.round(r.top - sec.top), Math.round(r.width), Math.round(r.height)];
  const parse = c => {
    const m = /rgba?\(([^)]+)\)/.exec(c || ''); if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const isMedia = e => ['IMG', 'CANVAS', 'VIDEO', 'SVG', 'svg'].includes(e.tagName) || e.tagName === 'IFRAME';
  const bgOf = e => { const c = parse(getComputedStyle(e).backgroundColor); return c && c.a > 0.02 ? c : null; };
  const hasBgImage = e => { const s = getComputedStyle(e).backgroundImage; return !!s && s !== 'none'; };
  const add = (rule, severity, visual, message, hint, rect) => out.push({ rule, severity, visual, message, hint: hint || '', rect });
  const visible = e => { const s = getComputedStyle(e); return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) > 0.05; };

  const visuals = [...section.querySelectorAll('.visual')].filter(v => !v.hidden && visible(v));

  // ---- 1. failed / empty visuals ------------------------------------------------------------------
  for (const v of visuals) {
    const r = v.getBoundingClientRect();
    const err = v.querySelector('.error');
    if (err) { add('visual_error', 'error', v.dataset.visual, `The visual shows an error: “${err.textContent.trim().slice(0, 160)}”`, 'Fix the query or the renderer for this kind.', rel(r)); continue; }
    const loading = v.querySelector('.loading');
    if (loading && /isn.t supported|No query|no data|TODO/i.test(loading.textContent)) {
      add('visual_empty', 'warn', v.dataset.visual, `Nothing is drawn: “${loading.textContent.trim().slice(0, 120)}”`, 'Write its SQL in the yaml or pick a supported kind.', rel(r));
      continue;
    }
    for (const img of v.querySelectorAll('img')) {
      if (img.complete && img.naturalWidth === 0) add('broken_image', 'error', v.dataset.visual, 'An image failed to load.', 'The picture is missing or its data: URI is corrupt.', rel(img.getBoundingClientRect()));
    }
  }

  // ---- 2. text: contrast, covered, clipped ------------------------------------------------------
  const texts = [];
  for (const v of visuals) {
    const walker = document.createTreeWalker(v, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const t = n.textContent.replace(/\s+/g, ' ').trim();
      if (!t) continue;
      const el = n.parentElement;
      if (!el || !visible(el) || el.closest('select, option, script, style, .loading, .error')) continue;   // failed visuals are reported once, as such
      const range = document.createRange(); range.selectNodeContents(n);
      const rr = range.getBoundingClientRect();
      if (rr.width < 1 || rr.height < 1) continue;
      texts.push({ v, el, t, rr });
    }
  }

  for (const { v, el, t, rr } of texts) {
    const vid = v.dataset.visual;
    const st = getComputedStyle(el);
    const fs = parseFloat(st.fontSize);
    let fg = parse(st.color);
    if (!fg) continue;
    // contrast is judged later from real pixels (a picture, gradient or page background has no single colour)
    const cx = rr.left + rr.width / 2, cy = rr.top + rr.height / 2;
    if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) continue;   // off-screen: nothing to sample
    const op = parseFloat(st.opacity);
    if (rr.width >= 6 && rr.height >= 6) collected.push({ visual: vid, t: t.slice(0, 40), color: [fg.r, fg.g, fg.b, fg.a * (isNaN(op) ? 1 : op)], rect: rel(rr), fs });
    // -- covered by something painted on top
    let covered = 0, total = 0, by = null;
    for (const fx of [0.2, 0.5, 0.8]) for (const fy of [0.25, 0.5, 0.75]) {
      const x = rr.left + rr.width * fx, y = rr.top + rr.height * fy;
      if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
      total++;
      const s2 = document.elementsFromPoint(x, y);
      const k = s2.findIndex(e => e === el || e.contains(el));
      for (let i = 0; i < (k < 0 ? 0 : k); i++) {
        const e = s2[i];
        if (e === el || el.contains(e) || e.contains(el)) continue;
        if (e.closest('.visual') === v) continue;                    // inside the same visual: its own layers
        const bc = bgOf(e);
        if (isMedia(e) || hasBgImage(e) || (bc && bc.a >= 0.9)) { covered++; by = by || e; break; }
      }
    }
    if (total >= 4 && covered / total >= 0.5) {
      const ov = by && by.closest('.visual');
      add('text_covered', 'error', vid, `“${t.slice(0, 40)}” is hidden under ${ov ? `another visual (${ov.dataset.visual})` : 'another element'} (${Math.round(100 * covered / total)}% covered).`, 'Overlapping visuals: check z-order and positions against the original.', rel(rr));
    }
    // -- clipped by an ancestor that hides overflow
    let vis = { l: rr.left, t: rr.top, r: rr.right, b: rr.bottom };
    for (let a = el; a && a !== section.parentElement; a = a.parentElement) {
      const s = getComputedStyle(a);
      if (s.overflow !== 'visible' || s.overflowX !== 'visible' || s.overflowY !== 'visible') {
        if (a.classList && (a.classList.contains('page') || a.tagName === 'BODY' || a.tagName === 'HTML')) break;
        const ar = a.getBoundingClientRect();
        vis = { l: Math.max(vis.l, ar.left), t: Math.max(vis.t, ar.top), r: Math.min(vis.r, ar.right), b: Math.min(vis.b, ar.bottom) };
      }
    }
    const fullA = rr.width * rr.height, visA = Math.max(0, vis.r - vis.l) * Math.max(0, vis.b - vis.t);
    if (fullA > 0 && visA / fullA < 0.75) {
      const ell = st.textOverflow === 'ellipsis';
      add(ell ? 'text_truncated' : 'text_clipped', ell ? 'info' : 'warn', vid, `“${t.slice(0, 50)}” ${ell ? 'is cut with “…”' : 'is cut off'} (${Math.round(100 * visA / fullA)}% visible).`, ell ? 'The title is longer than the visual is wide.' : 'The text does not fit its box; a smaller font or a bigger box in the original?', rel(rr));
    }
    // -- font much larger than the visual it is in
    const vr = v.getBoundingClientRect();
    if (rr.height > 0.9 * vr.height && vr.height > 0 && !/card|kpi/.test(v.dataset.kind || '')) add('text_too_big', 'warn', vid, `“${t.slice(0, 30)}” uses a ${Math.round(fs)}px font and needs ${Math.round(rr.height)}px in a visual only ${Math.round(vr.height)}px high.`, 'A Power BI font size that does not scale down with the page.', rel(rr));
  }

  // ---- 3. text over text of another visual ---------------------------------------------------------
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
    const a = texts[i], b = texts[j];
    if (a.v === b.v) continue;
    const x = Math.min(a.rr.right, b.rr.right) - Math.max(a.rr.left, b.rr.left);
    const y = Math.min(a.rr.bottom, b.rr.bottom) - Math.max(a.rr.top, b.rr.top);
    if (x > 4 && y > 4 && (x * y) / Math.min(a.rr.width * a.rr.height, b.rr.width * b.rr.height) > 0.3) {
      add('text_overlap', 'error', a.v.dataset.visual, `“${a.t.slice(0, 30)}” overlaps “${b.t.slice(0, 30)}” of another visual (${b.v.dataset.visual}).`, 'Two visuals draw text in the same place.', rel(a.rr));
    }
  }

  // ---- 4. charts: labels that will not fit -----------------------------------------------------------
  if (window.echarts) for (const v of visuals) {
    const nodes = [v.querySelector('.body'), ...v.querySelectorAll('.body div')].filter(Boolean);
    const inst = nodes.map(nn => echarts.getInstanceByDom(nn)).find(Boolean);
    if (!inst) continue;
    const opt = inst.getOption(), W = inst.getWidth(), H = inst.getHeight(), vid = v.dataset.visual, rect = rel(v.getBoundingClientRect());
    const axes = ax => Array.isArray(ax) ? ax : (ax ? [ax] : []);
    const fsOf = a => (a && a.axisLabel && a.axisLabel.fontSize) || 12;
    const px = (s, f) => String(s).length * f * 0.56;
    const cat = a => a && a.type === 'category' && Array.isArray(a.data) ? a.data.map(d => (d && d.value !== undefined) ? d.value : d) : null;
    for (const a of axes(opt.xAxis)) {
      const labs = cat(a); if (!labs || !labs.length) continue;
      const f = fsOf(a), need = labs.reduce((s, l) => s + px(l, f) + 8, 0), plot = W * 0.88;
      if (need > plot * 1.15) {
        const shown = Math.max(1, Math.floor(labs.length * plot / need));
        add('chart_labels_crowded', 'warn', vid, `${labs.length} category labels need ≈${Math.round(need)}px but the plot is ≈${Math.round(plot)}px wide: only about ${shown} will be drawn.`, 'Shorten the labels, rotate them, or make the visual wider.', rect);
      }
      if (f >= 18 && H < 260) add('chart_font_large', 'warn', vid, `Axis labels use ${f}px in a chart ${Math.round(H)}px high.`, 'A Power BI font size that does not scale with the page.', rect);
    }
    for (const a of axes(opt.yAxis)) {
      const labs = cat(a); if (!labs || !labs.length) continue;
      const f = fsOf(a), widest = Math.max(...labs.map(l => px(l, f)));
      if (widest > W * 0.4) add('chart_labels_wide', 'warn', vid, `The longest category label (≈${Math.round(widest)}px) takes ${Math.round(100 * widest / W)}% of the chart width.`, 'Shorten it or give the axis more room.', rect);
      if (labs.length * (f + 6) > H * 0.95 && labs.length > 3) add('chart_labels_crowded', 'warn', vid, `${labs.length} bars need ≈${labs.length * (f + 6)}px of height but the chart is ${Math.round(H)}px high: labels will be skipped.`, 'Show fewer categories (Top N) or make it taller.', rect);
    }
    for (const lg of (Array.isArray(opt.legend) ? opt.legend : opt.legend ? [opt.legend] : [])) {
      const n = (opt.series || []).length > 1 ? opt.series.length : ((opt.series || [])[0] && (opt.series[0].data || []).length) || 0;
      const f = (lg.textStyle && lg.textStyle.fontSize) || 12;
      if (n * (f * 6 + 30) > W * 2.2) add('chart_legend_crowded', 'info', vid, `The legend has ${n} entries and will take several rows.`, 'Fewer series, or hide the legend.', rect);
    }
    const s0 = (opt.series || [])[0];
    if (s0 && s0.type === 'pie' && (s0.data || []).length > 12) add('chart_many_slices', 'info', vid, `A pie/donut with ${(s0.data || []).length} slices.`, 'Slices this thin are unreadable; a bar chart or Top N reads better.', rect);
  }
  return { findings: out, texts: collected };
}
