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

## Diagnosing differences

| Symptom | Likely cause | Where to look |
|---|---|---|
| Every value × k | A JOIN duplicates rows | relationships in `model.json`; add `DISTINCT` on the dimension |
| Missing categories | Power BI shows "(Blank)" or filters nulls differently | `HAVING`, `COALESCE` |
| Extra/missing period | date filter `<` vs `<=`; timezone | `reference_sql` |
| Small constant difference | intermediate rounding | drop `ROUND` from the SQL, format at render time |
| A role sees extra data | RLS not applied | `--role` / PROXYUSER; secure view |
| Different order | `prototypeQuery.OrderBy` not replicated | `ORDER BY` in the SQL + `sort` in the yaml |

## "Ready" criteria

A report moves to `Validate ✓` in PLAN.md when: every visual with data is `OK`, every
`SKIP` is justified in `notes`, and the report owner has signed off on the accepted visual
differences (reinterpreted custom visuals, simplified conditional formatting).
