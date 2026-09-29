# ADR-007 — DAX filter context as SQL joins (selection-dependent measures)

**Status:** accepted, implemented, **unverified against Power BI numbers**. **Date:** 2026-09-29.

## Context

Real reports (TestReport3) hold measures whose value depends on the report's current selection, which a
`WHERE` clause can't express: `FILTER('Hierarchy Leaders', lvl = MIN(lvl))` (a hierarchy slicer's top
level), `var d = min('Calendar'[Date])` compared by month, an `IF` that reads one fact table for the current
month and another otherwise. Project rule: DAX is rewritten, never evaluated in Python.

## Decision

Recognise a **small set of idioms** in `semantic._DaxTranslator` and emit SQL that reproduces the filter
context with joins; anything else raises `_DaxUnsupported` and the visual stays manual (never a guess).

- **Selection minimum** `FILTER(T, T[c] = MIN(T[c]))` → marker `{SELMIN:T|c}` → `LEFT JOIN` of a DISTINCT key
  set (`MIN(c) OVER ()` over T filtered by the slicers on T); the measure tests `key IS NOT NULL`.
- **`MIN/MAX(T[c])` outside an aggregate** (with `VAR`/`RETURN`, `IF`, `MONTH/YEAR/DAY`, `CONCATENATE`) →
  marker `{CTX:MIN|T|c}` → a derived table joined in: one row (cards), or **per group** when a category is a
  column of T (T's own source grouped by those columns, LEFT JOINed on them) or T is the visual's own fact
  (the visual's FROM/WHERE grouped by the categories, joined null-safe). A category that doesn't filter T
  leaves one value for the whole selection, as DAX does. The value is also added to `GROUP BY` (unique per
  group) so it may appear outside an aggregate.
- **One measure over several fact tables** (`multi_fact`): the translator re-runs in *split* mode (`{AGG:n}` per
  aggregate, tied to its table); each fact gets its own derived table of aggregates per category; the
  expression is evaluated on a query whose FROM is the category tables only (`SELECT DISTINCT`, blank groups
  dropped). Cards CROSS JOIN the arms.
- `FORMAT` with time parts, `TIME()`, `NOW()` → `CURRENT_TIMESTAMP(0)`.

## Why joins and not subqueries

Teradata rejects a subquery inside an aggregate's argument. A one-row (or per-group) derived table joined in
is legal, adds no rows (unique key) and keeps the aggregate's shape.

## Consequences

- Correct only for the shapes above; each has tests (`tests/test_selection.py`) and parses with sqlglot's
  Teradata dialect. None has been compared with Power BI or run on Teradata.
- Generated SQL is long (derived tables repeat the visual's WHERE and bind parameters appear twice).
- The two `CALCULATE` forms differ (`Calculate([M], T[c]="x")` overrides a slicer on `c`; `CALCULATE([M],
  FILTER(T, …))` intersects); only the intersecting one is exact today.
- Not done: relative-date/time-intelligence beyond these idioms, table/matrix visuals over several fact tables,
  selection markers mixed into a multi-fact measure, pair-accurate hierarchy selection.
