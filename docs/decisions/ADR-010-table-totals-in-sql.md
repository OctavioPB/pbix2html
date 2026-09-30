# ADR-010: a table's grand-total row is a second SQL query

**Status:** accepted · **Applies to:** flat tables (`tableEx`, `table`) and matrices (`pivotTable`, rendered flat) in `snapshot`, `live` and `hah` mode

## Context

Power BI shows a grand-total row under a table unless `objects.total.totals` is false. The row is not in the
detail data, and summing the rows in the browser is wrong whenever the table is truncated, filtered client-side or
paged. The project rule is that numbers come from Teradata.

## Decision

The drafter emits `sql_total` next to `sql` in the yaml: `SELECT 'Total', NULL, SUM(a), ... FROM (<detail sql
without its ORDER BY>) AS t` (`semantic.table_total_sql`). It reuses the detail query's parameters, so the total
follows the same slicers and filters. `query.run_visual` runs it after the detail query and attaches the row as
`block["total"]`; the renderer draws it as a `tfoot` row (colours from the visual's or the theme's `total` object).

- Additive measures only: `Sum`/`Count`/`CountNonNull` → `SUM`, `Min` → `MIN`, `Max` → `MAX`. Averages, distinct
  counts and DAX ratios are not additive, so their cell is left blank rather than shown wrong.
- Not generated when the detail query is a `WITH`, has unresolved markers, or has no additive column.
- If the total query fails, the table still renders, without the row (logged).
- A person can edit or remove `sql_total` in the yaml like any other query; `redraft` regenerates the drafted ones.

## Not covered

Sub-totals per hierarchy level of a matrix (only the grand total is drawn). In `hah` mode the HTML runs the second
query itself (`fetchSQL`), like the detail one; unverified against a real HAH (ADR-004).
