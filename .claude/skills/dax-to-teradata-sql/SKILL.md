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
| `SUM(Ventas[Importe])` | `SUM(v.importe)` |
| `DIVIDE([A],[B])` | `CASE WHEN SUM(b)=0 THEN NULL ELSE SUM(a)/SUM(b) END` (use `CAST(... AS DECIMAL(18,6))` if they're integers) |
| `DISTINCTCOUNT(Clientes[Id])` | `COUNT(DISTINCT c.id)` |
| `CALCULATE([M], Tabla[Col]="X")` | same aggregate with `WHERE col='X'` **in addition to** the visual's filters; or `SUM(CASE WHEN col='X' THEN … END)` if the visual groups by `col` |
| `CALCULATE([M], ALL(Tabla[Col]))` | aggregate without that predicate: subquery or `SUM(...) OVER ()` |
| `CALCULATE([M], ALLSELECTED(...))` | `SUM(...) OVER ()` within the set already filtered by slicers |
| `TOTALYTD([M], Calendario[Fecha])` | `SUM(m) OVER (PARTITION BY anio ORDER BY fecha ROWS UNBOUNDED PRECEDING)` or `WHERE fecha BETWEEN inicio_anio AND fecha_ref` |
| `SAMEPERIODLASTYEAR` | self-join with `ADD_MONTHS(fecha, -12)` or a predicate over `anio - 1` |
| `DATEADD(..., -1, MONTH)` | `ADD_MONTHS(fecha, -1)` |
| `[Var %] = DIVIDE([Actual]-[PY],[PY])` | two aggregates in the same query and the division in `SELECT` |
| `RELATED(Dim[Col])` | JOIN to the dimension via the relationship from `model.json → relationships` |
| `USERELATIONSHIP` | JOIN via the alternate column indicated |
| `TOPN(10, ...)` | `QUALIFY ROW_NUMBER() OVER (ORDER BY m DESC) <= 10` |
| `FORMAT(...)` | no; formatting is applied in the renderer (`format` in the yaml) |

Relationships: `model.json → relationships` gives `FromTable/FromColumn/ToTable/ToColumn`
and the filter direction. A visual that shows `Region.Nombre` alongside `Ventas.Margen`
implies `JOIN region r ON r.id = v.region_id` per that relationship.

## Slicers → parameters

Each slicer in the layout becomes a yaml parameter (`parameters:`), and every SQL that
uses it carries `WHERE col = ?` with the name in `params: [anio]`. A multi-select slicer
is modeled as a list and expands to `IN (?,?,?)` in `query.py`. Page/visual filters with
`isLockedInViewMode` are written as fixed values in the SQL, not as a parameter.

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
