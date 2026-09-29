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
| `FORMAT(date, "yyyy-mm-dd hh:mm:ss")` returned as text | `TO_CHAR` pieces joined with `\|\|` (`hh`→`HH24`; `mm` is minutes right after `hh`, month otherwise); `TIME(h,m,s)` → `INTERVAL 'hh:mm:ss' HOUR TO SECOND`; `NOW()` → `CURRENT_TIMESTAMP(0)`, `TODAY()` → `CURRENT_DATE` |
| `FORMAT(number, …)` for display | don't — number formatting belongs in the spec's `format`, not the query |

## Filter-pane filters (report, page and visual level)

They constrain the visual as much as a slicer does, and they are easy to miss because they aren't in the
measure's DAX. Every one with a `definition` becomes a `WHERE` predicate on that visual's query:

| Filter | SQL |
|---|---|
| `In` values | `col IN ('a', 'b')` (`OR col IS NULL` if null is listed) |
| `Not In` / "is not" | `(col NOT IN (...) OR col IS NULL)` — Power BI keeps blanks in a negated filter |
| comparison, range (`And` of `>=`/`<=`) | `col >= x AND col <= y`; dates as `DATE 'YYYY-MM-DD'` |
| contains / starts / ends with | `col LIKE '%x%' ESCAPE '\'` (escape `%` and `_`) |
| Top N (`Top n` of `col` by `Sum(y)`) | `col IN (SELECT t.k FROM (SELECT col k, SUM(y) a FROM T [WHERE slicers on T] GROUP BY 1) t WHERE (SELECT COUNT(*) FROM (…same…) u WHERE u.a > t.a) < n)` (no window function: Teradata error 3706 forbids them in a subquery) |
| a filter on a table the visual doesn't read | semi-join through the relationship; with no relationship it has no effect |
| filter on an aggregate (`aggregation` set) or a measure | `HAVING` / measure test — not automatic, do it by hand |

Report-level, page-level and visual-level filters all apply together. Ignore drill-through filters
(`how_created` 5): their saved value is not the real context.

## Selection-dependent measures

Some measures read the report's current selection: `MIN/MAX('Calendar'[Date])` (a slicer's first/last day, or a
month's when the visual is grouped by month), `FILTER(T, T[c] = MIN(T[c]))` (a hierarchy slicer's top level), an
`IF` choosing between two fact tables by the selected month. In SQL each becomes a small derived table joined
in (one row for a card, one row per group in a chart) — never a subquery inside `SUM(...)`, which Teradata
rejects. Translate them only when the shape is exactly one of these, and say the result is unverified.

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
