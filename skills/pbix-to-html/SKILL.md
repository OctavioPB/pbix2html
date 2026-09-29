---
name: pbix-to-html
description: Convert a Power BI .pbix file into a self-contained HTML report. Extracts pages, visuals, layout, theme, textboxes, embedded images and (where present) the data model with its DAX measures and Power Query sources; then rewrites each visual's logic as SQL and renders a single HTML file that opens in any browser with no Power BI licence. Use when someone uploads a .pbix and wants it converted, migrated off Power BI, documented, or its measures/model explained.
---

# Power BI (.pbix) → HTML

Converts a `.pbix` into one self-contained HTML file: same pages, same positions, same
theme, charts drawn with ECharts. Two scripts do the mechanical parts; you do the part
that needs judgment (turning each visual's logic into a query, choosing renderers).

## What is and isn't possible here

Be straight with the user about this up front — it shapes everything that follows.

- **Layout, theme, text and images: fully automatic.** Pages, visual positions, colours,
  fonts, textbox content and embedded logos all come out of the file exactly.
- **Numbers: not automatic.** A `.pbix` in DirectQuery mode contains *no data* — only
  metadata. Even an Import-mode file stores data compressed in a way these scripts don't
  read. So the HTML needs its numbers from somewhere, and there are exactly three
  honest options (see "Getting the numbers in" below).
- **DAX is rewritten, not ported.** There's no reliable automatic DAX→SQL converter.
  You translate the measures yourself using `reference/dax-to-sql.md`, and you say so —
  a translated measure is a draft until someone checks it against the original report.
- **Custom visuals are reinterpreted**, not reproduced. Pick the nearest standard chart
  and tell the user which ones you approximated.

## Workflow

### 1. Extract

```bash
python scripts/extract_pbix.py report.pbix --out out
```

Writes `out/<Report>/layout.json` (pages, visuals, positions, fields, filters, theme,
textbox HTML, embedded images as data URIs) and `out/<Report>/model.json` (tables, DAX
measures, relationships, RLS roles, Power Query M source).

`model.json` needs the optional `pbixray` package. If it isn't available, or the report
has no local model ("thin" reports that connect to a published dataset), you'll get
`{"error": ...}` there — **the layout still extracts fine**, you just won't see the DAX.
Say so rather than pretending the model is empty. `--no-model` skips the attempt.

### 2. Understand the report

Read `layout.json` and tell the user what's in it: how many pages, which visual types,
which fields each visual uses. If `model.json` came through, `extract_pbix.py` can also
render it for a human:

```python
import json, sys; sys.path.insert(0, "scripts")
from extract_pbix import model_summary_markdown
print(model_summary_markdown(json.load(open("out/R/model.json")), "R"))
```

That gives each measure's DAX, the relationships, the RLS rules and each table's Power
Query source as readable Markdown — useful on its own even if the user never wants HTML.

**Filters change the numbers.** `layout.json` has the filter-pane filters at three levels: report
(`layout["filters"]`), page (`page["filters"]`) and visual (`visual["filters"]`). Each has `target`
(`Table.column`), `type` (`Categorical`, `Advanced`, `TopN`, `RelativeDate`), the raw `definition` and, when
present, `how_created` (5 = drill-through, ignore its saved value) and `aggregation` (a filter on `Sum(x)`,
not on rows). Any that carries a condition must be reproduced in the SQL you write, or the number won't match
Power BI; a filter with no `definition` only lists a field. See "Filter-pane filters" in
`reference/dax-to-sql.md`. The layout also carries `bookmarks`, `slicer` descriptions (saved selections) and
`model.json → table_modes` (Import / DirectQuery / Dual per table).

### 3. Getting the numbers in

Pick one with the user; don't guess:

- **Demo data** — `--demo` fills every visual with obviously fake numbers. Right for
  reviewing layout and styling. Label it as fake in what you hand back; never present
  those figures as the report's own.
- **The user supplies data** — they upload a CSV/Excel export, or paste query results.
  Shape it into `data.json` (below). This is the usual path for a one-off conversion.
- **You write the queries, they run them** — translate each measure with
  `reference/dax-to-sql.md`, hand the user the SQL, they run it against their warehouse
  and paste the results back. This is the path for a report they'll keep using.

`data.json` maps visual id → a block:

```json
{
  "3a7f…": {"columns": ["value"], "rows": [[1234567.8]]},
  "b21c…": {"columns": ["category", "value"], "rows": [["North", 0.21], ["South", 0.18]]}
}
```

**The column names matter.** Each visual kind expects specific ones — a chart fed the
wrong columns renders empty. They're all in `reference/visual-contracts.md`; check it
rather than guessing.

### 4. Render

```bash
python scripts/render_html.py out/R/layout.json data.json -o report.html
python scripts/render_html.py out/R/layout.json --demo -o report.html
```

To override a visual's renderer or title (custom visuals, or a `kind` that guessed
wrong), build the spec yourself first:

```python
import json, sys; sys.path.insert(0, "scripts")
from render_html import build_spec, render_html
layout = json.load(open("out/R/layout.json"))
spec = build_spec(layout, overrides={
    "4f2a…": {"kind": "column", "title": "Revenue by month", "format": {"value": "#,##0"}},
})
open("report.html", "w", encoding="utf-8").write(render_html(spec, json.load(open("data.json"))))
```

Then give the user the file.

### 5. Hand it over honestly

Say, in plain terms: which visuals have real data and which don't; which custom visuals
you approximated and with what; that any DAX you translated is unverified until they
compare it against the original; and that the file needs internet for the chart library
unless they host a local copy (`--echarts <url>`).

## Notes that save time

- **Charts need internet.** ECharts loads from a CDN. For a fully offline file, point
  `--echarts` at an internal copy of `echarts.min.js`.
- **Row-level security does not come along.** If the original filtered by role, the HTML
  shows whatever data you put in it. One file per role, or don't use it for restricted
  data — say this explicitly when `model.json` lists RLS roles.
- **Slicers become nothing** in *this skill's* renderer (the full `pbix2html` package draws real widgets, action
  buttons/bookmarks, tooltips and 100 % stacked charts; this one is a simplified, standard-library renderer). It drops them; the values they filtered by are
  baked into whatever data you supply. Mention it if the report leans on them.
- **A visual with no border in Power BI gets no border here.** That's deliberate — don't
  "fix" it by adding one, it makes the report look like a grid the original isn't.
- **Big files**: `layout.json` with embedded images can run to several MB. That's normal.

## Files

| Path | What it's for |
|---|---|
| `scripts/extract_pbix.py` | `.pbix` → `layout.json` + `model.json` (+ a CSV/Markdown inventory across many files) |
| `scripts/render_html.py` | spec/layout + data → one self-contained HTML; also `build_spec()` and `demo_data()` |
| `reference/visual-contracts.md` | The columns each visual kind needs. Check before building `data.json` |
| `reference/dax-to-sql.md` | DAX → SQL translation patterns, and what doesn't translate |

Both scripts are standard-library only and run from any directory — no install, no
package context. `pbixray` is the single optional extra (model extraction only) and is
imported lazily, so its absence never blocks the layout.

## Installing this skill

Zip the `pbix-to-html/` folder (the one holding this file) and upload it as a skill —
Settings → Capabilities → Skills on claude.ai, or drop the folder in `~/.claude/skills/`
for Claude Code. It needs the code execution / file tool enabled, since it runs Python
against an uploaded file.

Relationship to the full tool: this skill is a standalone, conversational path for a
one-off conversion. The `pbix2html` project it comes from does the same job as an
installed CLI + local web panel, and adds what a chat can't — a live Teradata
connection, per-role snapshots, and numeric validation of every visual against the
original report. Point users there when they're migrating a stack of reports rather
than converting one.
