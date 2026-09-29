# ADR-006 — Slicers as real widgets

**Status:** accepted, implemented (standard and `hah` templates). **Date:** 2026-09-29.

## Context
Slicers were only "parameters shown in a top bar". Two real reports (89 slicers) showed what that
loses: 54 dropdowns (12 hierarchical), 7 date ranges, lists; a **saved selection** per slicer
(`Year = 2026`, an org, a date range) that is part of the report's state; and **the same field on
several pages with different selections**, because slicers on different pages are independent in
Power BI unless they share a sync group.

## Decision
1. **Extraction** (`extract.parse_slicer`): `layout.json` visuals get `slicer: {mode, fields (in
   hierarchy order), single, select_all, initial, style, sync_group}`; `initial` is read from
   `objects.general.filter` (`In`, `Between`, `Comparison`).
2. **Parameters are scoped** (`semantic._slicer_parameters`): one per slicer field, per page or per
   sync group. A field on one page keeps its plain name; on several pages each gets
   `<name>__<page>`. Ranges give `<name>_from` / `<name>_to` (`bound`, `dtype: date`). The saved
   selection is the default, so a snapshot comes out filtered like the report was left. A visual
   only uses the parameters of its own page (`_params_for_page`).
3. **yaml `slicers:`** per slicer visual: its `params` and `options_sql` (distinct values, one
   column `levelN` per hierarchy level; a calendar-derived table is ordered chronologically by
   `MIN(date)`, anything else by value). Hand-editable like `visuals:`.
4. **SQL**: a value slicer is `col IN (:p)`; a range bound is `/*if p*/ col >= CAST(:p AS DATE)
   /*fi p*/`. `bind` (Python) and `bindSql` (hah) drop an empty parameter's predicate (`1=1`).
5. **Widgets** (`templates/slicer.js`, shared): dropdown, list, tree for 2+ levels, date range, and a
   typed-values fallback when no options query exists. State is one object per parameter; widgets
   bound to the same parameter (sync groups) stay in step.
   - `snapshot`: options embedded at build (`run_slicers`); widgets are read-only (data is fixed).
   - `live`: options from `GET /reports/{r}/slicers/{visual}`; a change reloads only the visuals
     whose SQL uses a changed parameter; pages load when first shown. Multi values travel as
     repeated query keys (values may contain commas).
   - `hah`: options run client-side from the embedded `options_sql`.
6. The top bar keeps only parameters nothing else edits (no widget, or on a page not shown).

## Known limits (deliberate)
- **Hierarchy selection** is normalised to "fully selected parents" (`p1`) or leaves (`p1` + `p2`):
  picking children of several parents at once yields a superset (`p1 × p2`), because independent
  `IN` predicates cannot express pairs.
- Range slicers exist for `between` / `before` / `after`; relative-date and tile slicers draw
  nothing yet. Cross-page slicer *sync of fields* (`fieldChanges`) is not modelled, only grouping.
- Options are not cascading (a child list doesn't narrow by the parent selection) and are not
  filtered by other slicers, unlike Power BI's "filter the other slicers".
- Snapshot widgets are read-only; interactivity needs `live` or `hah`.
- Verified against two real reports with a simulated backend, not a real Teradata/HAH.

## Consequences
- `parameters:` in existing yamls have no `pages`, so they still apply everywhere (compatible);
  regenerate (or `autofill`) to get scoped parameters.
- The cache key now includes the SQL text, so editing a query never serves stale rows.
