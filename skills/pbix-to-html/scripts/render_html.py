#!/usr/bin/env python3
"""
render_html.py — spec + data → one self-contained HTML report.

Standard library only: no Jinja2, no build step. The page is a fixed shell plus two
JSON blobs (the spec and the data); the page's own JS lays the visuals out and draws
them with ECharts. That's why this needs no template engine — everything variable is
data, not markup.

Usage:
    python render_html.py spec.json data.json -o report.html
    python render_html.py spec.json --demo -o report.html      # fake numbers, no data file

`spec.json` is what build_spec() below produces from extract_pbix.py's layout.json plus
your own per-visual choices (kind, title, format). `data.json` maps visual id → a data
block: {"columns": [...], "rows": [[...], ...]}. Column names per visual kind are in
reference/visual-contracts.md — a chart drawn from the wrong columns is the single most
common reason a converted report looks empty.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Power BI visualType → the renderer that draws it. Anything not listed draws as a
# labelled placeholder rather than failing the whole page.
KIND_MAP: dict[str, str] = {
    "card": "card", "cardVisual": "card", "kpi": "kpi", "multiRowCard": "multicard",
    "clusteredBarChart": "bar", "barChart": "bar", "stackedBarChart": "bar",
    "hundredPercentStackedBarChart": "bar",
    "clusteredColumnChart": "column", "columnChart": "column", "stackedColumnChart": "column",
    "hundredPercentStackedColumnChart": "column",
    "lineChart": "line", "areaChart": "line", "stackedAreaChart": "line",
    "lineClusteredColumnComboChart": "combo", "lineStackedColumnComboChart": "combo",
    "pieChart": "pie", "donutChart": "pie",
    "table": "table", "tableEx": "table", "pivotTable": "matrix", "matrix": "matrix",
    "slicer": "slicer", "advancedSlicerVisual": "slicer", "listSlicer": "slicer",
    "textbox": "text", "image": "static", "shape": "static", "basicShape": "static",
    "actionButton": "static", "gauge": "gauge",
}
NO_DATA_KINDS = {"slicer", "text", "static"}

DEFAULT_THEME = {
    "data_colors": ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7", "#744EC2",
                    "#D9B300", "#D64550"],
    "background": "#FFFFFF", "foreground": "#252423", "muted": "#605E5C", "border": "#E1DFDD",
    "font_family": "'Segoe UI', system-ui, -apple-system, sans-serif",
}


def resolve_theme(layout_theme: dict | None) -> dict:
    """Merges the .pbix's own theme over the defaults. Never invents colours: a report
    whose theme is only a built-in *name* (very common — Power BI doesn't store the
    palette in that case) keeps the defaults, and you set real colours by hand."""
    t = dict(DEFAULT_THEME)
    cj = (layout_theme or {}).get("custom_json") or {}
    if cj.get("dataColors"):
        t["data_colors"] = list(cj["dataColors"])
    for src, dest in (("background", "background"), ("foreground", "foreground"),
                      ("foregroundNeutralSecondary", "muted"), ("backgroundNeutral", "border")):
        if cj.get(src):
            t[dest] = cj[src]
    face = (cj.get("fontFamily")
            or ((cj.get("textClasses") or {}).get("title") or {}).get("fontFace")
            or ((cj.get("textClasses") or {}).get("label") or {}).get("fontFace"))
    if face and not str(face).lower().startswith("segoe"):
        t["font_family"] = f"'{face}', system-ui, sans-serif"
    return t


def build_spec(layout: dict, overrides: dict[str, dict] | None = None,
               include_hidden: bool = False) -> dict:
    """layout.json (from extract_pbix.py) → the spec the page's JS consumes.

    `overrides` is keyed by visual id and is where your own decisions go:
    {"kind": "bar", "title": "...", "format": {"value": "#,##0"}}. Everything else —
    position, the frame the report itself defines, textbox content, embedded images —
    comes straight from the .pbix."""
    overrides = overrides or {}
    pages = []
    for i, p in enumerate(layout.get("pages", [])):
        if p.get("hidden") and not include_hidden:
            continue
        width = float(p.get("width") or 1280)
        height = float(p.get("height") or 720)
        visuals = []
        for v in p.get("visuals", []):
            if v.get("is_group") or v.get("hidden"):
                continue
            over = overrides.get(v["id"], {})
            kind = over.get("kind") or KIND_MAP.get(v.get("type", ""), "unsupported")
            if kind == "slicer":
                continue          # slicers become filters in the top bar, not boxes
            visuals.append({
                "id": v["id"], "kind": kind, "type": v.get("type"),
                "title": over.get("title") or v.get("title"),
                "left": round(100 * (v.get("x") or 0) / width, 3),
                "top": round(100 * (v.get("y") or 0) / height, 3),
                "w": round(100 * (v.get("width") or 0) / width, 3),
                "h": round(100 * (v.get("height") or 0) / height, 3),
                "z": v.get("z") or 0,
                "format": over.get("format") or {},
                "stacked": "stacked" in str(v.get("type", "")).lower(),
                "area": "area" in str(v.get("type", "")).lower(),
                "inner_radius": v.get("type") == "donutChart",
                "text": v.get("text"),
                "image": v.get("image_data_uri"),
                "style": v.get("style") or {},
            })
        pages.append({"id": f"page-{i}", "name": p.get("display_name") or f"Page {i + 1}",
                      "width": width, "height": height,
                      "background": p.get("background"), "visuals": visuals})
    return {"report": layout.get("report") or "Report",
            "theme": resolve_theme(layout.get("theme")), "pages": pages}


def demo_data(spec: dict) -> dict[str, dict]:
    """Placeholder numbers so the layout can be reviewed before any real query exists.
    Clearly fake on purpose — never pass these off as the report's actual figures."""
    out: dict[str, dict] = {}
    for page in spec["pages"]:
        for v in page["visuals"]:
            kind = v["kind"]
            if kind in NO_DATA_KINDS or kind == "unsupported":
                continue
            if kind in ("card", "gauge"):
                out[v["id"]] = {"columns": ["value"], "rows": [[1234.5]]}
            elif kind == "kpi":
                out[v["id"]] = {"columns": ["value", "target"], "rows": [[1234.5, 1500]]}
            elif kind in ("table", "matrix", "multicard"):
                out[v["id"]] = {"columns": ["label", "value"],
                                "rows": [["Example A", 120], ["Example B", 95], ["Example C", 61]]}
            else:
                out[v["id"]] = {"columns": ["category", "value"],
                                "rows": [["Jan", 120], ["Feb", 95], ["Mar", 143], ["Apr", 61]]}
    return out


def _json_for_script(value: Any) -> str:
    """JSON safe to sit inside a <script> block: a string containing '</script>' would
    otherwise close the tag early and break (or rewrite) the page."""
    return json.dumps(value, ensure_ascii=False, default=str).replace("</", "<\\/")


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --bg: __BG__; --fg: __FG__; --muted: __MUTED__; --border: __BORDER__;
    --font: __FONT__;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg); font-family: var(--font); font-size: 14px; }
  header { padding: 14px 20px; border-bottom: 1px solid var(--border); display: flex;
           align-items: baseline; gap: 14px; flex-wrap: wrap; }
  header h1 { font-size: 1.1rem; margin: 0; font-weight: 600; }
  header .meta { color: var(--muted); font-size: .8rem; }
  nav.tabs { display: flex; gap: 4px; padding: 8px 20px 0; flex-wrap: wrap; }
  nav.tabs button { border: 1px solid var(--border); border-bottom: none; background: transparent;
    color: var(--muted); padding: 6px 14px; border-radius: 6px 6px 0 0; cursor: pointer; font: inherit; }
  nav.tabs button[aria-selected="true"] { color: var(--fg); font-weight: 600; background: var(--bg); }
  main { padding: 0 20px 24px; }
  .page { position: relative; width: 100%; aspect-ratio: var(--ratio); border: 1px solid var(--border);
          border-radius: 4px; overflow: hidden; }
  .visual { position: absolute; overflow: hidden; display: flex; flex-direction: column; }
  .visual .title { font-size: .78rem; font-weight: 600; padding: 6px 8px 2px; }
  .visual .body { flex: 1; min-height: 0; padding: 4px 8px 8px; }
  .chart { width: 100%; height: 100%; }
  .card-value { font-size: clamp(1.1rem, 4.5vw, 2.4rem); font-weight: 600; line-height: 1.1;
                display: flex; align-items: center; height: 100%; }
  .tablewrap { height: 100%; overflow: auto; }
  table.data { width: 100%; border-collapse: collapse; font-size: .75rem; }
  table.data th, table.data td { padding: 3px 6px; border-bottom: 1px solid var(--border); text-align: left; }
  table.data td.num, table.data th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .text p { margin: 0 0 4px; }
  .note { color: var(--muted); font-size: .72rem; padding: 4px 8px; }
  .error { color: #b3261e; font-size: .72rem; padding: 4px 8px; }
  @media print { nav.tabs { display: none; } .page { page-break-after: always; } }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span class="meta">__META__</span>
</header>
<nav class="tabs" id="tabs"></nav>
<main id="main"></main>

<script id="spec" type="application/json">__SPEC__</script>
<script id="data" type="application/json">__DATA__</script>
<script src="__ECHARTS__"></script>
<script>
(function () {
  var spec = JSON.parse(document.getElementById('spec').textContent);
  var data = JSON.parse(document.getElementById('data').textContent);
  var theme = spec.theme, charts = {};

  function fmt(v, f) {
    if (v === null || v === undefined) return '—';
    if (typeof v !== 'number') return String(v);
    if (f && f.indexOf('%') >= 0) return (v * 100).toLocaleString('en-US', {maximumFractionDigits: 1}) + '%';
    var dec = f && f.indexOf('.') >= 0 ? (f.split('.')[1].match(/0/g) || []).length : 0;
    return v.toLocaleString('en-US', {minimumFractionDigits: dec, maximumFractionDigits: dec});
  }
  function col(b, name) { return (b.columns || []).indexOf(name); }
  function isNumeric(b, i) { return (b.rows || []).some(function (r) { return typeof r[i] === 'number'; }); }

  var base = {
    grid: {left: 40, right: 12, top: 18, bottom: 26},
    textStyle: {fontFamily: theme.font_family, color: theme.foreground},
    color: theme.data_colors,
    backgroundColor: 'transparent',
    tooltip: {trigger: 'axis'}
  };
  var axisStyle = {
    axisLine: {lineStyle: {color: theme.border}},
    axisLabel: {color: theme.muted, fontSize: 10},
    splitLine: {lineStyle: {color: theme.border, opacity: .5}}
  };

  function chart(el, v, option) {
    el.innerHTML = '<div class="chart"></div>';
    var c = echarts.init(el.firstChild, null, {renderer: 'canvas'});
    c.setOption(option);
    charts[v.id] = c;
  }
  function series(b) {
    var ci = col(b, 'category'), vi = col(b, 'value'), si = col(b, 'series');
    var cats = [], groups = new Map();
    (b.rows || []).forEach(function (r) {
      var c = String(r[ci]);
      if (cats.indexOf(c) < 0) cats.push(c);
      var key = si >= 0 ? String(r[si]) : 'value';
      if (!groups.has(key)) groups.set(key, new Map());
      groups.get(key).set(c, r[vi]);
    });
    return {cats: cats, groups: groups};
  }
  function cartesian(v, b, type, horizontal, area) {
    var s = series(b);
    var ser = Array.from(s.groups.entries()).map(function (e) {
      return {name: e[0], type: type === 'combo' ? 'bar' : type,
              data: s.cats.map(function (c) { return e[1].has(c) ? e[1].get(c) : null; }),
              stack: v.stacked ? 'total' : undefined,
              areaStyle: area ? {} : undefined};
    });
    var catAxis = Object.assign({type: 'category', data: s.cats}, axisStyle);
    var valAxis = Object.assign({type: 'value'}, axisStyle, {
      axisLabel: {color: theme.muted, fontSize: 10,
                  formatter: function (x) { return fmt(x, v.format.value); }}});
    return Object.assign({}, base, {
      legend: s.groups.size > 1 ? {top: 0, textStyle: {color: theme.muted}} : undefined,
      xAxis: horizontal ? valAxis : catAxis,
      yAxis: horizontal ? Object.assign({}, catAxis, {inverse: true}) : valAxis,
      series: ser});
  }

  var R = {
    card: function (el, v, b) {
      var i = col(b, 'value'), r = (b.rows || [])[0] || [];
      el.innerHTML = '<div class="card-value"></div>';
      el.firstChild.textContent = fmt(r[i >= 0 ? i : 0], v.format.value);
    },
    kpi: function (el, v, b) {
      var r = (b.rows || [])[0] || [], vi = col(b, 'value'), ti = col(b, 'target');
      el.innerHTML = '<div class="card-value"></div><div class="note"></div>';
      el.children[0].textContent = fmt(r[vi], v.format.value);
      if (ti >= 0) el.children[1].textContent = 'Target ' + fmt(r[ti], v.format.value);
    },
    gauge: function (el, v, b) {
      var r = (b.rows || [])[0] || [], g = function (n) { var i = col(b, n); return i >= 0 ? r[i] : null; };
      chart(el, v, Object.assign({}, base, {tooltip: undefined, series: [{
        type: 'gauge', min: g('min') || 0, max: g('max') || (g('value') || 1) * 1.2,
        progress: {show: true}, axisLabel: {show: false},
        detail: {color: theme.foreground, formatter: function (x) { return fmt(x, v.format.value); }},
        data: [{value: g('value')}]}]}));
    },
    multicard: function (el, v, b) { R.table(el, v, b); },
    table: function (el, v, b) {
      var cols = b.columns || [], num = cols.map(function (_, i) { return isNumeric(b, i); });
      var h = cols.map(function (c, i) { return '<th class="' + (num[i] ? 'num' : '') + '">' + c + '</th>'; });
      var rows = (b.rows || []).map(function (r) {
        return '<tr>' + r.map(function (x, i) {
          return '<td class="' + (num[i] ? 'num' : '') + '">' +
                 (num[i] ? fmt(x, v.format[cols[i]]) : (x === null || x === undefined ? '' : x)) + '</td>';
        }).join('') + '</tr>';
      });
      el.innerHTML = '<div class="tablewrap"><table class="data"><thead><tr>' + h.join('') +
                     '</tr></thead><tbody>' + rows.join('') + '</tbody></table></div>';
    },
    matrix: function (el, v, b) { R.table(el, v, b); },
    bar: function (el, v, b) { chart(el, v, cartesian(v, b, 'bar', true)); },
    column: function (el, v, b) { chart(el, v, cartesian(v, b, 'bar', false)); },
    line: function (el, v, b) { chart(el, v, cartesian(v, b, 'line', false, v.area)); },
    combo: function (el, v, b) { chart(el, v, cartesian(v, b, 'combo', false)); },
    pie: function (el, v, b) {
      var ci = col(b, 'category'), vi = col(b, 'value');
      chart(el, v, Object.assign({}, base, {
        tooltip: {trigger: 'item'},
        series: [{type: 'pie', radius: v.inner_radius ? ['45%', '75%'] : '75%',
          data: (b.rows || []).filter(function (r) { return r[vi] !== null; })
            .map(function (r) { return {name: String(r[ci]), value: r[vi]}; }),
          label: {color: theme.foreground, fontSize: 11}}]}));
    },
    text: function (el, v) { el.innerHTML = '<div class="text">' + (v.text || '') + '</div>'; },
    static: function (el, v) {
      el.innerHTML = v.image
        ? '<img src="' + v.image + '" alt="" style="width:100%;height:100%;object-fit:contain">' : '';
    }
  };

  function paint(id, block) {
    var v = byId[id], el = document.querySelector('#v-' + CSS.escape(id) + ' .body');
    if (!el) return;
    if (block && block.error) { el.innerHTML = '<div class="error"></div>';
                                el.firstChild.textContent = block.error; return; }
    if (!block || block.skipped) { el.innerHTML = '<div class="note">No query defined</div>'; return; }
    var fn = R[v.kind];
    if (!fn) { el.innerHTML = '<div class="note">Visual type "' + v.kind + '" isn\\'t supported yet</div>'; return; }
    try { fn(el, v, block); }
    catch (e) { el.innerHTML = '<div class="error"></div>'; el.firstChild.textContent = e.message; }
  }

  // ---- build the DOM from the spec (this is what replaces a template engine) ----
  var byId = {}, tabs = document.getElementById('tabs'), main = document.getElementById('main');
  spec.pages.forEach(function (p, idx) {
    var b = document.createElement('button');
    b.textContent = p.name;
    b.setAttribute('aria-selected', idx === 0 ? 'true' : 'false');
    b.dataset.page = p.id;
    tabs.appendChild(b);

    var section = document.createElement('section');
    section.className = 'page';
    section.id = p.id;
    section.style.setProperty('--ratio', p.width + ' / ' + p.height);
    if (p.background) section.style.background = p.background;
    if (idx !== 0) section.hidden = true;

    p.visuals.forEach(function (v) {
      byId[v.id] = v;
      var d = document.createElement('div');
      d.className = 'visual';
      d.id = 'v-' + v.id;
      d.style.cssText = 'left:' + v.left + '%;top:' + v.top + '%;width:' + v.w + '%;height:' + v.h + '%;z-index:' + v.z;
      // The frame comes from the report. Power BI's default is no border and no fill,
      // so a visual that specifies neither gets neither — drawing a box around every
      // visual is what makes a converted report look like a grid the original isn't.
      d.style.background = v.style.background || 'transparent';
      d.style.border = v.style.border ? ('1px solid ' + (v.style.border_color || theme.border)) : 'none';
      if (v.title) {
        var t = document.createElement('div');
        t.className = 'title';
        t.textContent = v.title;
        d.appendChild(t);
      }
      var body = document.createElement('div');
      body.className = 'body';
      d.appendChild(body);
      section.appendChild(d);
    });
    main.appendChild(section);
  });

  tabs.addEventListener('click', function (e) {
    var b = e.target.closest('button');
    if (!b) return;
    Array.prototype.forEach.call(tabs.children, function (x) {
      x.setAttribute('aria-selected', String(x === b));
    });
    spec.pages.forEach(function (p) {
      document.getElementById(p.id).hidden = p.id !== b.dataset.page;
    });
    Object.keys(charts).forEach(function (k) { charts[k].resize(); });
  });
  window.addEventListener('resize', function () {
    Object.keys(charts).forEach(function (k) { charts[k].resize(); });
  });

  Object.keys(byId).forEach(function (id) { paint(id, data[id]); });
})();
</script>
</body>
</html>
"""

ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"


def render_html(spec: dict, data: dict[str, dict] | None = None,
                echarts_cdn: str = ECHARTS_CDN, note: str = "") -> str:
    theme = spec["theme"]
    meta = f"Generated {datetime.now():%Y-%m-%d %H:%M}"
    if note:
        meta += f" · {note}"
    replacements = {
        "__TITLE__": spec.get("report", "Report"),
        "__META__": meta,
        "__BG__": theme["background"], "__FG__": theme["foreground"],
        "__MUTED__": theme["muted"], "__BORDER__": theme["border"],
        "__FONT__": theme["font_family"],
        "__SPEC__": _json_for_script(spec),
        "__DATA__": _json_for_script(data or {}),
        "__ECHARTS__": echarts_cdn,
    }
    html = PAGE_TEMPLATE
    for key, value in replacements.items():
        html = html.replace(key, str(value))
    return html


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="spec.json (+ data.json) → self-contained HTML")
    ap.add_argument("spec", help="spec.json from build_spec(), or a raw layout.json")
    ap.add_argument("data", nargs="?", help="data.json: {visual_id: {columns, rows}}")
    ap.add_argument("-o", "--out", default="report.html")
    ap.add_argument("--demo", action="store_true",
                    help="fill every visual with obviously fake numbers (layout review only)")
    ap.add_argument("--echarts", default=ECHARTS_CDN, help="ECharts URL (use an internal copy if offline)")
    args = ap.parse_args(argv)

    raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    spec = raw if "theme" in raw and "pages" in raw and raw.get("pages") and \
        "visuals" in (raw["pages"][0] or {}) and "left" in ((raw["pages"][0]["visuals"] or [{}])[0] or {}) \
        else build_spec(raw)

    data = json.loads(Path(args.data).read_text(encoding="utf-8")) if args.data else {}
    note = ""
    if args.demo:
        data = demo_data(spec)
        note = "DEMO DATA — not real figures"

    out = Path(args.out)
    out.write_text(render_html(spec, data, args.echarts, note), encoding="utf-8")
    n_vis = sum(len(p["visuals"]) for p in spec["pages"])
    print(f"HTML: {out}  ({len(spec['pages'])} pages, {n_vis} visuals, "
          f"{len([k for k, v in data.items() if v])} with data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
