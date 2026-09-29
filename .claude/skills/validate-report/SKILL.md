---
name: validate-report
description: Procedure for validating a migrated report against Power BI before marking it ready in PLAN.md — visual-by-visual numeric comparison, tolerances, and what to do with each kind of difference. Use with /validate or when the report owner reports a different number.
---

# Validating a migrated report

## Sources of truth, in order of preference

1. **SQL captured in DBQL** (`reference_sql` in the yaml): run against Teradata with the
   same parameters and compared to `sql`. Exact and automatable.
2. **Export from Power BI**: in the Service, "Export data" on the visual → CSV; save it to
   `tests/reference/<Report>/<visual_id>.csv`. Useful when there's no DBQL access.
3. **Manual reading** from Desktop: last resort; record it in `notes`.

## What `pbix2html validate <Report>` does

For every visual with `sql` in the yaml:
- Runs `sql` and `reference_sql` (or loads the reference CSV).
- Aligns by category columns and compares `value` with `tolerance` (`abs` or `rel`; default rel 1e-6).
- Reports `OK`, `DIFF` (with the differing rows), or `SKIP` (no reference).
- Writes `out/<Report>.validation.md` and exits with a non-zero code if there's a `DIFF`.

## Before validating

1. Read `out/<Report>/mapping_report.md` (`pbix2html mapping <pbix>`): it lists what could not be drafted and,
   under *Filter-pane filters not applied*, the filters Power BI applies that the SQL doesn't (aggregate/measure
   filters, relative date, multi-hop). Those visuals will differ until fixed by hand.
2. SQL drafted **before** filter-pane filters were applied (the report's first `convert`) lacks them. A yaml
   whose `sql` is already written is never overwritten. Regenerate the drafts with `pbix2html scaffold <pbix> --overwrite` (it backs the old yaml
   up first; copy any hand-written SQL back from the backup) or reset that visual's `sql` to `TODO` and let the panel
   auto-draft again, before comparing numbers.
3. Compare with the same slicer state on both sides. Drill-through pages have no navigation in the HTML, and
   their saved filter value is ignored, so compare them for the value you pass explicitly.

## Diagnosing differences

| Symptom | Likely cause | Where to look |
|---|---|---|
| `Error 3707 … between the 'AS' keyword and the 'value' keyword` | `AS value` unquoted (reserved word) | write `AS "value"`; fixed in the drafter and at run time |
| Every value × k | A JOIN duplicates rows | relationships in `model.json`; add `DISTINCT` on the dimension |
| Missing categories | Power BI shows "(Blank)" or filters nulls differently | `HAVING`, `COALESCE` |
| Extra/missing period | date filter `<` vs `<=`; timezone | `reference_sql` |
| Small constant difference | intermediate rounding | drop `ROUND` from the SQL, format at render time |
| A role sees extra data | RLS not applied | `--role` / PROXYUSER; secure view |
| Numbers off only where the visual had a Top N / advanced filter | filter-pane filter not applied or ranked differently (direction, ties) | mapping report; `effective_filters` |
| A time-series or "starting/ending" value off by a period | `MIN/MAX(Calendar[Date])` evaluated for the whole selection instead of per group, or month compare | skill `dax-to-teradata-sql` (selection-dependent) |
| A timestamp off by hours | `NOW()` is UTC in the service, local in Desktop; Teradata session zone | `CURRENT_TIMESTAMP(0)` |
| Import table differs from the `.pbix` copy | the file holds a stale cache, Teradata is the truth | refresh Power BI first |
| Different order | `prototypeQuery.OrderBy` not replicated | `ORDER BY` in the SQL + `sort` in the yaml |

## "Ready" criteria

A report moves to `Validate ✓` in PLAN.md when: every visual with data is `OK`, every
`SKIP` is justified in `notes`, and the report owner has signed off on the accepted visual
differences (reinterpreted custom visuals, simplified conditional formatting).
