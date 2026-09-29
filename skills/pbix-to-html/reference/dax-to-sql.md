# DAX → SQL

DAX isn't translated mechanically — each visual is rewritten as one aggregate query that
returns the columns that visual's kind expects (see `visual-contracts.md`).

## Before translating anything

**The best source is the query the report already ran.** If the user has database access,
the SQL Power BI itself sent is far more reliable than re-deriving it from DAX:

- **Power BI Desktop → View → Performance Analyzer → Start recording → refresh visuals.**
  Each DirectQuery visual shows the exact SQL it sent. Copy it.
- On the warehouse side, the query log (`DBC.DBQLSqlTbl` on Teradata, `sys.dm_exec_query_stats`
  on SQL Server, `INFORMATION_SCHEMA.JOBS` on BigQuery) has the same thing.

Ask for this before hand-translating. It turns a judgment call into a copy.

**Also check `model.json`'s `power_query` section** — each table's M source often contains
the literal source query (`Value.NativeQuery(..., "SELECT ...")`) or a plain
`Schema`/`Item` table reference. That gives you the real table names, which DAX alone
never does: DAX refers to model tables (`Sales`), not warehouse objects
(`ACC_VW.sales_fact_v2`).

## What DirectQuery guarantees

In DirectQuery, Power BI only permits DAX that folds to SQL. That means nearly every
measure is some combination of `SUM`/`COUNT`/`DISTINCTCOUNT`/`AVERAGE`/`MIN`/`MAX`,
`DIVIDE`, `CALCULATE` with column filters, and time intelligence over a date table.
Import-mode models can be far worse.

## Patterns

| DAX | SQL |
|---|---|
| `SUM(Sales[Amount])` | `SUM(s.amount)` |
| `AVERAGE(Sales[Amount])` | `AVG(s.amount)` |
| `COUNTROWS(Sales)` | `COUNT(*)` |
| `DISTINCTCOUNT(Customer[Id])` | `COUNT(DISTINCT c.id)` |
| `DIVIDE([A], [B])` | `CASE WHEN SUM(b) = 0 THEN NULL ELSE SUM(a) / CAST(SUM(b) AS DECIMAL(18,6)) END` |
| `CALCULATE([M], T[Col] = "X")` | the same aggregate plus `AND col = 'X'` — **in addition to** the visual's own filters |
| `CALCULATE([M], ALL(T[Col]))` | the aggregate *without* that predicate — a subquery, or `SUM(...) OVER ()` |
| `CALCULATE([M], ALLSELECTED(...))` | `SUM(...) OVER ()` within the already-filtered set |
| `TOTALYTD([M], 'Date'[Date])` | `SUM(m) OVER (PARTITION BY year ORDER BY date ROWS UNBOUNDED PRECEDING)`, or a `WHERE date BETWEEN year_start AND ref_date` |
| `SAMEPERIODLASTYEAR` | self-join on `date - 1 year`, or a predicate on `year - 1` |
| `DATEADD(…, -1, MONTH)` | `ADD_MONTHS(date, -1)` / `DATEADD(month, -1, date)` — dialect-specific |
| `RELATED(Dim[Col])` | `JOIN` to that dimension, using `model.json`'s `relationships` |
| `USERELATIONSHIP` | join on the alternate column instead of the default one |
| `TOPN(10, …)` | `ORDER BY m DESC LIMIT 10` (or `QUALIFY ROW_NUMBER() OVER (ORDER BY m DESC) <= 10`) |
| `RANKX` | `RANK() OVER (ORDER BY …)` — check ties, DAX and SQL differ on them |
| `FORMAT(…)` | don't — formatting belongs in the spec's `format`, not the query |

## What doesn't translate cleanly

Flag these to the user instead of guessing:

- **Nested `CALCULATE` with several filter modifiers.** Filter context doesn't map onto
  `WHERE` one-to-one; get the Performance Analyzer SQL for these.
- **Virtual tables** (`SUMMARIZE`, `ADDCOLUMNS`, `GENERATE` inside a measure).
- **Iterators over row context** (`SUMX` over a non-trivial expression).
- **Calculation groups** — they rewrite measures at query time; there is no static SQL.
- **`USERELATIONSHIP` / bidirectional filtering** where the direction changes the result.

## Two things that quietly produce wrong numbers

1. **A join that fans out.** Joining a fact to a dimension on a non-unique key multiplies
   rows and inflates every `SUM`. If a total comes out as a clean multiple of the
   expected one, this is why.
2. **Period boundaries.** Power BI's date filters are half-open; a naive
   `BETWEEN start AND end` double-counts the boundary day. Check a single period against
   the original before trusting a time series.

Always tell the user a translated measure is unverified until they've compared at least
one number against the original report.
