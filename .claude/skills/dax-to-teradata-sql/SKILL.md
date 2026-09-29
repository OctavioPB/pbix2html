---
name: dax-to-teradata-sql
description: How to convert DAX measures from a DirectQuery model into Teradata SQL for metrics/<Report>.yaml, using the SQL Power BI already generated (DBQL) as a reference. Use when writing or reviewing a report's yaml, or when validate reports numeric differences.
---

# DAX → Teradata SQL

## Principle

DAX isn't "translated"; each visual is rewritten as an aggregate query. The most reliable
way to know what SQL Power BI produces is to **look at what it already sent to Teradata**:

```sql
-- SQL the Power BI gateway executed (adjust user/dates)
SELECT s.QueryID, s.CollectTimeStamp, s.SqlTextInfo
FROM DBC.DBQLSqlTbl s
JOIN DBC.DBQLogTbl l ON l.QueryID = s.QueryID
WHERE l.UserName = 'PBI_GATEWAY_USER'
  AND l.CollectTimeStamp > CURRENT_TIMESTAMP - INTERVAL '1' DAY
  AND l.QueryBand LIKE '%PowerBI%'      -- the gateway usually tags this; drop the line if not
ORDER BY s.QueryID, s.SqlRowNo;
```

Open the report in Desktop/Service, interact with it visual by visual, and correlate by
time. Alternative: *Performance Analyzer* in Desktop shows "DirectQuery" with the SQL per
visual. That SQL goes into the yaml as `reference_sql`; your `sql` can be a cleaned-up
version of it.

## DAX restrictions in DirectQuery that help

In DirectQuery, Power BI only allows DAX that folds down to SQL. That means almost every
measure is a combination of: SUM/COUNT/DISTINCTCOUNT/AVERAGE/MIN/MAX, DIVIDE, CALCULATE
with column filters, and time intelligence over a calendar table. Iterators (SUMX) over
simple expressions also fold. Don't expect complex RANKX or virtual tables.

## Patterns

| DAX | Teradata SQL |
|---|---|
| `SUM(Sales[Amount])` | `SUM(v.amount)` |
| `DIVIDE([A],[B])` | `CASE WHEN SUM(b)=0 THEN NULL ELSE SUM(a)/SUM(b) END` (use `CAST(... AS DECIMAL(18,6))` if they're integers) |
| `DISTINCTCOUNT(Customers[Id])` | `COUNT(DISTINCT c.id)` |
| `CALCULATE([M], Table[Col]="X")` | same aggregate with `WHERE col='X'` **in addition to** the visual's filters; or `SUM(CASE WHEN col='X' THEN … END)` if the visual groups by `col` |
| `CALCULATE([M], ALL(Table[Col]))` | aggregate without that predicate: subquery or `SUM(...) OVER ()` |
| `CALCULATE([M], ALLSELECTED(...))` | `SUM(...) OVER ()` within the set already filtered by slicers |
| `TOTALYTD([M], Calendar[Date])` | `SUM(m) OVER (PARTITION BY year ORDER BY date ROWS UNBOUNDED PRECEDING)` or `WHERE date BETWEEN year_start AND ref_date` |
| `SAMEPERIODLASTYEAR` | self-join with `ADD_MONTHS(date, -12)` or a predicate over `year - 1` |
| `DATEADD(..., -1, MONTH)` | `ADD_MONTHS(date, -1)` |
| `[Var %] = DIVIDE([Actual]-[PY],[PY])` | two aggregates in the same query and the division in `SELECT` |
| `RELATED(Dim[Col])` | JOIN to the dimension via the relationship from `model.json → relationships` |
| `USERELATIONSHIP` | JOIN via the alternate column indicated |
| `TOPN(10, ...)` | `QUALIFY ROW_NUMBER() OVER (ORDER BY m DESC) <= 10` |
| `FORMAT(...)` | no; formatting is applied in the renderer (`format` in the yaml) |

Relationships: `model.json → relationships` gives `FromTable/FromColumn/ToTable/ToColumn`
and the filter direction. A visual that shows `Region.Name` alongside `Sales.Margin`
implies `JOIN region r ON r.id = v.region_id` per that relationship.

## Slicers → parameters

Each slicer in the layout becomes a yaml parameter (`parameters:`), and every SQL that
uses it carries `WHERE col = ?` with the name in `params: [year]`. A multi-select slicer
is modeled as a list and expands to `IN (?,?,?)` in `query.py`. Page/visual filters with
`isLockedInViewMode` are written as fixed values in the SQL, not as a parameter (see *Filter-pane filters* below).

## Column contract

The SQL must return the columns the visual's `kind` expects (see skill `html-renderer`):
`card → value`; `bar|line|column → category, value[, series]`; `table → free-form
columns`; `kpi → value, target`. Aliases always lowercase.

## Validation

`tolerance` in the yaml (absolute or relative). Typical differences and their cause:
- Off by a factor: a missing JOIN (duplication) or an extra one (relationship's implicit filter).
- Off at period boundaries: Power BI uses `<` at period end, DBQL shows it.
- NULL vs 0: Power BI hides rows with no value; use `HAVING` or let the renderer skip nulls.
- Rounding: compare with ≥6 decimals; display formatting doesn't count.

## What the auto-drafter does and does not do (learned on real reports)

- **Slicers**: a drafted visual filters with `col IN (:param)` for each slicer on a table it reads.
  `query.bind` turns that predicate into `1=1` when the parameter is empty ("nothing selected"
  means "no filter" in Power BI); other empty uses (`= :year`) are your responsibility.
- **Joins** follow the model's active relationships (single hops only). A relationship that is
  M:M can duplicate rows; validate shows it as "every value × k".
- **Several fact tables**: one `SELECT` never sums two fact tables (their join multiplies rows), so
  such a visual is left as `TODO`; a chart with several measures gets one `UNION ALL` arm per
  measure, each joining only its own fact table.
- **Order**: the visual's sort becomes `ORDER BY <column position>`; a sort on a field the query
  doesn't select is skipped.
- Table sources come from Power Query (`Query="..."`, `Value.NativeQuery`, plain accessors, "Enter
  Data" tables). DAX calculated tables (`CALENDAR`, `Row(...)`) have no source: see
  `pbix2html mapping` for exactly which visuals that blocks.
- **Calendar tables**: `CALENDAR(start, end)` becomes `SELECT ... FROM sys_calendar.calendar WHERE
  calendar_date BETWEEN start AND end` with its calculated columns translated (a small, closed
  grammar). The join to facts (`log_dt`) is *proposed* in `metrics/<Report>.relationships.json`.
- **Slicer on a table the visual doesn't read** (typically a calendar or a dimension): applied as
  a semi-join through one relationship, wrapped in `/*if p*/ ... /*fi p*/`; `bind` removes the whole
  predicate when `p` is empty.

## Selection-dependent measures: `FILTER(T, T[c] = MIN(T[c]))`

Recognised automatically (`semantic._DaxTranslator._selection_min`, `_expand_selmins`). Meaning: keep
the rows of `T` at the lowest `c` among the rows the slicers on `T` leave (a hierarchy slicer's top
level; with no selection, the lowest level of the whole table). Emitted as a `LEFT JOIN` of a
`DISTINCT` key set (`MIN(c) OVER ()` over the slicer-filtered `T`) to the fact table, and the
measure's `CASE WHEN selminN.k IS NOT NULL`. A join, not a subquery, because Teradata rejects
subqueries inside an aggregate's argument. Needs a source query for `T` and a relationship from `T`
to the fact table; otherwise the visual stays manual. `MAX`, other columns, or any other shape are
not matched. **Unverified against Power BI numbers**: the reading (flattened path table, one row
per leader/ancestor pair) is inferred from the model, so validate one card per report.

Note the two CALCULATE filter forms differ: `Calculate([M], T[c] = "x")` overrides a slicer on `c`;
`CALCULATE([M], FILTER(T, T[c] = "x"))` intersects with it. Only the second is translated exactly
today (the first also intersects, so a slicer on the same column will differ).

## Selection-dependent dates: `MIN/MAX(T[c])`, `VAR`, month comparisons

`VAR x = <scalar> RETURN …`, `IF`, `MONTH/YEAR/DAY`, `CONCATENATE`/`&` and `MIN/MAX(T[c])` are translated
(`_body`, `_scalar`, `_expand_ctxs`). `MIN/MAX(T[c])` is "the value over the rows the report's filters
leave in T": a one-row derived table `CROSS JOIN`ed in (`ctxN.v`) — over T's source filtered by the
slicers on T when the visual doesn't read T (a calendar), over the visual's own FROM + WHERE when it does.
Unused VARs are dropped. In a **grouped** visual it is evaluated per group, as DAX does:
a category that is a column of T (calendar month over `MIN(Calendar[Date])`) → T's own source grouped by
those columns and LEFT JOINed on them; T read by the visual (a fact) → the visual's own FROM + WHERE
grouped by every category, joined back null-safe; a category that doesn't filter T (gender over the
calendar) → one value for the whole selection, as in DAX. The joined value is also added to `GROUP BY`
(unique per group, so no rows change) so an `IF` condition outside an aggregate is legal. Fields in a
chart's `Tooltips` role are not drafted (they were becoming extra series).
The generated SQL repeats the visual's WHERE inside the derived table (bind params appear twice).

## One measure over several fact tables (`IF(cond, SUM(A[x]), CALCULATE(SUM(B[x]), ...))`)

Drafted by `multi_fact` for a single-value card/kpi/gauge or a chart with 1–2 categories. The translator
re-runs in *split* mode (`{AGG:n}` per aggregate, each tied to its table); every fact table gets its own
derived table (its aggregates per category, over that table joined only to the category tables and the
slicers reaching it), and the expression is evaluated on a query whose FROM is just the category tables
(`SELECT DISTINCT`, groups whose value is blank dropped, as Power BI does). A card is the arms CROSS JOINed.
Left manual: an aggregate that filters on another table, a `Promotions-Slicer`-style marker mixed in,
table/matrix visuals, several such values in one visual. Every fact table must relate to every category table.
Note the category domain is the category table's rows (after slicers on it), not "values that have facts".

## Filter-pane filters (report / page / visual level)

Filters with a condition are added to every drafted query as fixed WHERE predicates
(`semantic.effective_filters`, `filter_sql`, `_filter_where`): `In` / `Not In` (blanks kept when negated,
as Power BI does), comparisons (`=`, `<>`, `>`, `>=`, `<`, `<=`, `IS NULL`), `And`/`Or` ranges,
`Contains`/`StartsWith`/`EndsWith` (`LIKE` with `ESCAPE`), and **Top N** (`col IN (SELECT k FROM
(… GROUP BY) QUALIFY RANK() OVER (ORDER BY agg dir) <= N)`, ranked over the rows the slicers leave; ties
kept). A filter on a table the visual doesn't read is a semi-join through a direct relationship (like
a slicer); on a table no relationship reaches it has no effect, as in Power BI. Report + page + visual
filters all apply. Values come from the .pbix and are quoted by `_filter_literal`.

Not applied (listed in the mapping report and in the visual's `notes`): filters on an aggregate
(`Sum(T.c) < 100`, needs HAVING), on a measure, booleans, relative-date, multi-column `In`, and filters
through more than one relationship hop. Drill-through filters (`howCreated` 5) are skipped on purpose:
their saved value is only the last one the author tried, and the HTML has no drill-through navigation yet.
Aggregate-function codes, sort direction, comparison kinds and `howCreated` were checked against Microsoft's
published JSON schemas (see ADR-008). Unverified: Top N tie handling (`RANK` keeps ties), and whether PBIR's
`VisualTopN` has the same shape as `TopN`. Also handled: `Between`.

## FORMAT with time, TIME(), NOW()

`FORMAT(x, "yyyy-mm-dd hh:mm:ss")` → `TO_CHAR` pieces joined with `||` (`hh`→`HH24`; `mm` is minutes right after
`hh` or before `ss`, month otherwise; 12-hour `AM/PM` isn't translated). `TIME(h,m,s)` with literal integers →
`INTERVAL 'hh:mm:ss' HOUR TO SECOND`. `NOW()` → `CURRENT_TIMESTAMP(0)` (keeps the time), `TODAY()` → `CURRENT_DATE`.
Time zone: Power BI's `NOW()` is UTC in the service and local in Desktop; the Teradata session zone decides here.
