---
name: html-renderer
description: How render.py turns layout.json + data + theme into a self-contained HTML with ECharts. Defines the per-visual-type (kind) column contract, the Power BI visualType → renderer mapping, and live vs snapshot mode. Use when adding a renderer, adjusting the template, or when a visual looks wrong.
---

# HTML renderer

## Output

A single `Report.html` file: inline CSS, ECharts from a CDN (`ECHARTS_CDN` in `.env`, can
point to an internal copy), data in `<script id="data" type="application/json">`
(snapshot) or `window.API_BASE` (live). No build step, no framework. Must open from disk
(file://) in snapshot mode.

## Canvas

Each .pbix page (typically 1280×720) renders as a `.page` section with `aspect-ratio`, and
visuals are absolutely positioned in **percentages** (`left = x/width*100`), which
preserves the original layout and scales with width.

**Frames come from the report, not from us.** A visual's fill and border are read from its
`vcObjects` (`extract.py`'s `container_style` → `v.style`), and the page's canvas colour
from the section's `objects.background`/`outspace` (`page.background`). Power BI's own
default is *no* border and *no* fill, so a visual that specifies neither gets neither —
don't reintroduce a default box in CSS, it makes every report look like a grid it isn't.
Palette references (`ThemeDataColor`) resolve against the report's `dataColors`; the
`Percent` shade is ignored (base colour is closer than nothing). Hidden pages (`hidden`) aren't
rendered unless `--include-hidden`. Groups (`__group__`) aren't drawn. Page navigation:
tabs at the top. Slicers render as controls in a top bar per page (not in their original
position) and trigger regeneration (live) or are informational with the snapshot's fixed
value.

## visualType → kind mapping

| Power BI `visualType` | `kind` | Notes |
|---|---|---|
| card, cardVisual | card | one big number + label |
| kpi | kpi | value + target + trend |
| multiRowCard | multicard | label/value list |
| clusteredBarChart, barChart, stackedBarChart, hundredPercent… | bar | horizontal; `series` optional |
| clusteredColumnChart, columnChart, stackedColumnChart… | column | vertical |
| lineChart, areaChart, stackedAreaChart | line | `area:true` for area |
| lineClusteredColumnComboChart, lineStackedColumnComboChart | combo | `series` with `axis: line|column` |
| pieChart, donutChart | pie | donut = `inner_radius` |
| table, tableEx | table | free-form columns (`table` = legacy Table visual, same contract) |
| pivotTable, matrix | matrix | rows × columns; v1 renders it as a flat table |
| slicer, advancedSlicerVisual, listSlicer | slicer | becomes a parameter |
| textbox | text | from `objects.general.paragraphs` |
| image, shape, basicShape, actionButton | static | `image` draws the embedded picture (extract.py's `embed_image_resources`, a data: URI in `v.image`); shape/basicShape/actionButton have no such resource and still draw as an empty frame |
| gauge | gauge | value, min, max, target |
| waterfallChart, funnel, treemap, scatterChart, map… | pending | see `out/summary.md` for priority |
| custom (is_custom) | reinterpreted | pick a standard `kind` manually in the yaml + `notes` |

`kind` is inferred in `semantic.py` and can be overridden in the yaml.

## Column contract (what the SQL must return)

| kind | columns | optional |
|---|---|---|
| card | `value` | `label` |
| kpi | `value`, `target` | `trend` (list) |
| multicard | `label`, `value` | |
| bar / column / line | `category`, `value` | `series` (one row per category×series) |
| combo | `category`, `value`, `series` | `axis` per series in the yaml |
| pie | `category`, `value` | |
| table / matrix | free-form | order = order of columns in the SELECT |
| gauge | `value` | `min`, `max`, `target` |

Lowercase aliases. **On Teradata write the value column as `AS "value"`**: `value` is a reserved word and
`SELECT SUM(x) AS value` fails with error 3707 ("expected a name … between AS and value"); sqlglot's Teradata
dialect does not flag it. The drafter quotes it, and `TeradataBackend` rewrites a bare `AS value|min|max` at run
time (older yamls, hand-written SQL), but write it quoted. Dates as ISO strings. Nulls: the renderer skips categories whose
`value` is null. Numeric formatting (`format: "#,##0.0%"`) goes in the yaml, not the SQL.

## 100 % stacked charts

`hundredPercentStacked*` visuals are drawn with `v.percent`: each category's series are rescaled to sum to
100 in the renderer (ECharts has no such mode), the value axis is fixed at 0–100 with `%` labels. The SQL is
the same as for a plain stacked chart (absolute values). Both templates (`report.html.j2`,
`report_hah.html.j2`) carry it.

Fields in a chart's `Tooltips` role are **not** drafted as SQL columns (they used to become extra series);
if the tooltip must show them, add them to the SQL by hand.

## Theme

`layout.json → theme.custom_json`: `dataColors` → series palette; `background`/`foreground`
→ background and text; `textClasses.title.fontFace` → font family (if it's a Segoe/Power
BI font, it falls back to `system-ui`). No custom theme: `DEFAULT_THEME` in `render.py`
(Power BI's standard palette). Don't introduce your own colors.

## Adding a renderer

1. Confirm the frequency in `out/summary.md`.
2. Define the column contract here.
3. Implement `_opt_<kind>(visual, data, theme)` in `render.py` returning an ECharts
   options dict, and register it in `RENDERERS`. For `table`/`card` (not ECharts), return HTML.
4. Add a fixture in `tests/fixtures/` with sample data and a test in `tests/test_render.py`
   that verifies the HTML contains the visual's id and doesn't raise.
5. Open the HTML and compare against Desktop; capture differences in the yaml (`notes`).

## Live mode

Same HTML; instead of `<script id="data">`, `window.API_BASE = "https://…"` and the JS
does `fetch(`${API_BASE}/reports/${report}/visuals/${id}?${params}`)` per visual on load
and whenever slicers change. The response has the same shape as the snapshot's data
block: `{"columns":[...],"rows":[[...]]}`. Errors are shown inside the visual's box with
the service's message, without hiding the rest of the report.


## Slicers (ADR-006)

A slicer is drawn as a widget at its own position. The layout visual carries `slicer:
{mode, fields, single, select_all, initial, style, sync_group}`; the yaml has, per slicer visual,
`slicers.<id>: {page, params, options_sql}` (options: one column per hierarchy level, named
`level1`, `level2`...). Parameters are page-scoped (`name__page`) unless the slicer is in a sync
group, and a range slicer (`between`/`before`/`after`) drives `<name>_from` / `<name>_to`. In
`snapshot` the widget is read-only and its options are embedded (`#slicer-data`); in `live` they come
from `GET /reports/{report}/slicers/{id}` and a change reloads only the visuals using the changed
parameters; in `hah` the options run client-side from `options_sql`. The widget code is
`templates/slicer.js`, shared by both templates (`slicerWidget(el, v, host)`).
