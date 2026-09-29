# Visual contracts — what columns each kind needs

`data.json` maps a visual id to one block:

```json
{"columns": ["category", "value"], "rows": [["North", 0.21], ["South", 0.18]]}
```

The **column names are the contract**. The renderer looks columns up by name, not by
position, so a block with the right numbers under the wrong names draws an empty chart —
that's the most common reason a converted report looks broken.

| kind | required columns | optional | notes |
|---|---|---|---|
| `card` | `value` | | First row only. |
| `kpi` | `value`, `target` | | |
| `gauge` | `value` | `min`, `max` | `max` defaults to `value × 1.2`. |
| `multicard` | `label`, `value` | | Rendered as a small table. |
| `bar` | `category`, `value` | `series` | Horizontal. |
| `column` | `category`, `value` | `series` | Vertical. |
| `line` | `category`, `value` | `series` | `area` comes from the original visual type. |
| `combo` | `category`, `value`, `series` | | Drawn as bars; ECharts styling per series isn't split out. |
| `pie` | `category`, `value` | | Donut if the original was a donut. |
| `table` | any columns, any order | | Column order = display order. Numeric columns right-align. |
| `matrix` | any columns | | Rendered flat, as a table. |
| `text` | — | | Content comes from the `.pbix` itself (`v.text`). |
| `static` | — | | Embedded image from the `.pbix` (`v.image`). |

## With a `series` column

One row per category × series. The renderer groups them:

```json
{"columns": ["category", "series", "value"],
 "rows": [["Jan", "North", 10], ["Jan", "South", 8], ["Feb", "North", 12]]}
```

## Conventions

- **Lowercase names.** `value`, not `Value`.
- **Dates as ISO strings** (`"2026-03-01"`), not datetime objects.
- **Nulls are allowed** — a null `value` leaves a gap rather than plotting zero, which is
  what Power BI does too. Don't substitute 0; it changes what the chart says.
- **Percentages as fractions** (`0.21`, not `21`) when the format string contains `%` —
  the renderer multiplies by 100 for display.
- **Number formatting** goes in the spec, not the data: `{"format": {"value": "#,##0.0%"}}`.

## Signalling "no data yet"

Two ways, both handled:

```json
{"columns": [], "rows": [], "skipped": true}     → "No query defined"
{"columns": [], "rows": [], "error": "…"}        → the message, shown inside that visual
```

A visual missing from `data.json` entirely renders as "No query defined" too. One failing
visual never takes down the rest of the page.
