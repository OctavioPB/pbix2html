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

## Titles and text colour

A visual's title formatting is read from `vcObjects.title` (`fontColor`, `fontSize`, `bold`, `alignment`; theme
colour ids resolved like the other colours) into `style.title_*` and drawn inline (`_title_css`, values
validated). A slicer on a dark panel normally has a *white* title set by the report; the theme foreground
would make it dark on dark. When the report sets no title colour, `_backdrop` finds what the visual sits on
(its own opaque fill, else the highest visual under its centre, else the page, else the theme) and
`_readable_fg` swaps in a colour with WCAG contrast >= 3 only when the theme foreground fails that; the same
colour is the default text of a slicer widget (`--sl-fg`). A background at 100 % transparency is not painted
(`style.transparency`); a partial one is `rgba`. The title text itself comes from the yaml (`title`), so an old
yaml keeps old text until it is regenerated.

## Shapes as design elements (`kind: static`, `shape`/`basicShape`)

A plain rectangle/line/oval used only for layout (a divider, a coloured panel, a header
stripe) has its OWN formatting cards, distinct from `vcObjects` (the generic per-visual-
container frame every visual type has — background/border/title, `container_style`) and from
`objects.fill` alone: `extract._shape_outline` reads a shape's own border/stroke
(`objects.outline` for the classic "shape" visual, `objects.line` for the newer "basicShape")
into `style.border_color`/`style.border_weight`, and `extract._shape_geometry` reads its
silhouette (`objects.shape.tileShape` / `objects.general.shapeType` → `style.shape_kind`:
`line`/`rectangle`/`oval`, anything else — triangle, arrow, chevron... — still gets its
fill/border/rotation but renders as a plain rectangle), rotation (`objects.rotation.
shapeAngle` classic / `.angle` basicShape → `style.rotation`, degrees) and corner rounding
(`objects.shape.roundEdge` → `style.round_edge`, points). `render.py` converts weights/
roundEdge from points to pixels (`* 4/3`, same factor as `title_size`) and turns a partly
transparent fill's `style.transparency` into `rgba(...)` for either `background` or, for a
line, `line_color` (a line has no fill area, so it's never given a `background` at all —
`_style_with_fill` skips that key entirely when `shape_kind == "line"`).

A `line` shape is drawn as a rule through the middle of its box (`R.static` in the template
JS), not a filled rectangle — its colour is normally the Line/outline card, falling back to
the Fill card only when outline isn't set at all (some real reports populate only Fill even
for a line tileShape). **Rotation is applied to every shape except a line**: a long, thin box
rotated around its own centre swings far outside that box — confirmed against a real report
where a 1280×23 header-line shape rotated 90° covered unrelated text hundreds of pixels below
it. `style.rotation` is still extracted for a line (so it's visible in `layout.json` /
`out/summary.md` for anyone investigating), just not turned into CSS; drawing a rotated line
correctly would need swapping which of width/height is the line's length, not a blind
`transform: rotate()`. **Shadow is not read at all** — no real report seen so far uses it on a
shape, so the property name is unconfirmed; add it once one does.

## Verifying the result (`pbix2html verify`, ADR-009)

`pbix2html verify out/Report.html` opens every page in a browser and writes `verify_report.md` with numbered
screenshots. Rules: `overlap`, `outside_page`, `too_small`, `no_renderer`, `no_data` / `data_error` /
`empty_result` (static); `invisible_text`, `low_contrast`, `text_busy_background` (pixel contrast),
`text_covered`, `text_overlap`, `text_clipped` / `text_truncated`, `text_too_big`, `visual_error`, `visual_empty`,
`broken_image`, `chart_labels_crowded` / `chart_labels_wide` / `chart_font_large` / `chart_legend_crowded` /
`chart_many_slices`, `js_error`. Offline: `--echarts <local echarts.min.js>`; browser: `--browser <path>`.
Fixes it led to: theme colour ids for a report without a custom theme, textbox paragraph alignment, default shape
fill (the theme's first colour), card number colour, readable default text on dark panels and translucent buttons.

## 100 % stacked charts

`hundredPercentStacked*` visuals are drawn with `v.percent`: each category's series are rescaled to sum to
100 in the renderer (ECharts has no such mode), the value axis is fixed at 0–100 with `%` labels. The SQL is
the same as for a plain stacked chart (absolute values). Both templates (`report.html.j2`,
`report_hah.html.j2`) carry it.

Fields in a chart's `Tooltips` role are **not** drafted as SQL columns (they used to become extra series);
if the tooltip must show them, add them to the SQL by hand.

## Table style

A table/matrix's own header and row-banding colours are read from `objects.columnHeaders`
(`backColor`, `fontColor`) and `objects.values` (`backColorPrimary`, `backColorSecondary`,
`fontColorPrimary`) — what a Power BI table style preset actually sets (extract.py's
`_table_style`, table/tableEx/matrix/pivotTable only) — into `style.table_header_bg`,
`table_header_fg`, `table_row_bg`, `table_row_bg_alt`, `table_row_fg`. The template's
`R.table` turns these into CSS custom properties (`--th-bg`, `--tr-bg-alt`, ...) scoped to
the visual's `.tablewrap`; absent keys keep the theme-neutral default (no banding). Like
other visual colours these can be `ThemeDataColor` references and are resolved by
`resolve_theme_markers` once the theme is known.

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

## Text fit, tables and font (both templates)

- Default face is Segoe UI (`render._FONT_STACK`); a theme face goes first with Segoe UI behind it. Buttons and inputs inherit it.
- `fitText` shrinks a title / card value / label that does not fit (titles to 11px, then two lines and "…"; numbers
  to 9px) instead of cutting it; it re-runs on resize and page change. Do not size cards with `vw`.
- Table/matrix colours: `style.table_header_bg/fg`, `table_row_bg`, `table_row_bg_alt`, `table_row_fg`, matrix
  `table_rowhdr_bg/fg`. Read from the visual's `objects` (`columnHeaders`, `values`, `rowHeaders`), else from the
  theme's `visualStyles` (`extract.apply_theme_table_styles`). `ThemeDataColor` ColorId 2 + 0.6 = #FFBF9A on a
  #FF5F02 first data colour. Both `report.html.j2` and `report_hah.html.j2` apply them (the main template did not).


## Chart and frame formatting (from the real-report comparison)

- `extract._chart_style` → `style.point_color` (`dataPoint` fill), `series_colors` (by `selector.metadata`; used only when the report names as many as there are series), `labels*` (data labels: show, size, bold, position, unit), `legend_show/legend_pos`, `x_axis_show/y_axis_show/gridlines`. Keys are absent when the report says nothing; both templates' `cartesian()` read them.
- Frame: `border_radius` (px, container) next to a shape's `round_edge`; `title_family` (a face named *Semibold* gets weight 600 since it may not be installed). Titles default to weight 400, not bold.
- Built-in base themes carry no palette in their JSON: `extract._BASE_PALETTES` (only `CY18SU07`, "Classic", is known; add others from a real report, never guess).
- Flat tables: `header_names` (column captions from `NativeReferenceName`, used only when the count matches the SQL columns), `table_header_size/align/bold`, `table_row_size`.
- Not read yet: conditional formatting (FillRule / icon rules), multiRowCard layout, per-series names on charts, button selected state details, totals rows.
