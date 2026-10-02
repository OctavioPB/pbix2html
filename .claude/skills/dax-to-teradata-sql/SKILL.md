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

## Column names vs table aliases

A **table alias** is ours to invent, so `_sql_alias` may rewrite it (and a reserved word gets a
`_t` suffix, because an alias cannot be quoted only where it is defined). A **column name is
not**: it has to match what the mapped source query exposes. `_sql_col` therefore quotes any name
that is not a plain identifier *verbatim* — `ELT-1` becomes `"ELT-1"`, not `elt_1`. Rewriting it
asks Teradata for a column that does not exist, which is how a five-level hierarchy slicer came to
filter nothing at all (2026-10-02). Plain identifiers are still emitted bare and lower-case.

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
- **Combo charts** (`kind: combo`) draft through the same "several measures" `UNION ALL` arms as a
  plain multi-measure bar/column/line chart (`_UNION_ARM_KINDS`) — the column contract is
  identical (`category`, `series`, `value`); the only combo-specific fact, which series is a line
  vs. a column, is derived from the field's own role (`Y2` → line, `Y` → the renderer's column
  default), written to `axis` (`_combo_axis`). **One total measure** (only `Y` or only `Y2`, seen
  in a real report) drafts the *other*, simpler chart shape instead — no `series` column at all,
  just `category`/`value` — since the several-measures arm path only kicks in for 2+ measures; the
  renderer's own grouping then treats the single series as the literal key `'value'`
  (`report.html.j2`'s `series()`, no `series` column to read), so `_combo_axis` must key `axis` as
  `{"value": "line"}` in that case, not the field's own label — confirmed by a real report where a
  combo chart had only a `Y2` measure and the axis key had to match exactly this or it never applied.
  default) and written to the yaml's `axis` (`_combo_axis`), never into the SQL itself.
- **Order**: the visual's sort becomes `ORDER BY <column position>`; a sort on a field the query
  doesn't select is skipped.
- Table sources come from Power Query (`Query="..."`, `Value.NativeQuery`, plain accessors, "Enter
  Data" tables). DAX calculated tables (`CALENDAR`, `Row(...)`) have no source: see
  `pbix2html mapping` for exactly which visuals that blocks.
- **Calendar tables**: `CALENDAR(start, end)` becomes `SELECT ... FROM sys_calendar.calendar WHERE
  calendar_date BETWEEN start AND end` with its calculated columns translated (a small, closed
  grammar). The join to facts (`date_key`) is *proposed* in `metrics/<Report>.relationships.json`.
- **Slicer on a table the visual doesn't read** (typically a calendar or a dimension): applied as
  a semi-join through one relationship, wrapped in `/*if p*/ ... /*fi p*/`; `bind` removes the whole
  predicate when `p` is empty.
- **A calculated column on a regular (non-calendar) table** — e.g. a date bucketed to the 1st of
  its month, `IF(ISBLANK(d), BLANK(), DATE(YEAR(d), MONTH(d), 1))` — is translated the same way a
  calendar table's own calculated columns are (`semantic._table_calc_columns`, reusing
  `_calendar_expr_sql`, which also now understands `ISBLANK`, `BLANK()` and `DATE(y, m, d)`), tried
  against each of the table's real (non-calculated) columns as the row-context anchor. Found
  against a real report: pbixray's schema lists a calculated column exactly like a source column,
  so a bare field reference to one used to draft a plain `alias.column` — syntactically fine, but
  Teradata rejects it at runtime ("column does not exist") since it was never in the mapped query.
  One that doesn't match this grammar now correctly leaves the *whole visual* manual
  (`untranslatable_calc_column:<name>` in the mapping report) instead of drafting a
  plausible-looking wrong reference.

## Row iterators, SWITCH, and conditions over a measure (2026-09-30)

Four additions to `_DaxTranslator`, each with the same test: does it have *one* exact SQL
equivalent, or does it need the filter context rebuilt? Only the first kind is drafted.

- **`SUMX/AVERAGEX/MINX/MAXX/COUNTX(T, expr)` over a plain table** (`_iterator`) → `SUM(expr)`.
  DAX's row context over a *physical* table is exactly SQL's own, so the expression maps across
  untouched: `SUMX(Sales, Sales[Qty] * Sales[Price])` is `SUM(sales.qty * sales.price)`. The whole
  safety of this rests on the table being physical, so **only a bare table name is accepted**.
  `FILTER(...)`, `VALUES(...)`, `SUMMARIZE(...)`, `ALL(...)` build a *virtual* table whose rows are
  not T's — re-deriving one in SQL is guesswork, so they still raise. Inside the row expression an
  aggregate (`SUMX(T, SUM(...))`) or a measure (`SUMX(T, [M])`) is a **context transition**, which
  re-evaluates per row and has no single-SELECT form: both raise. Another table's column is reached
  only through `RELATED(D[c])`, which DAX allows only from the many side — so the join it implies
  can never multiply T's rows. A bare `Other[c]` inside the iterator raises instead of silently
  joining (that one *would* fan out).
- **`SWITCH`** (`_switch`), both the value form and the `SWITCH(TRUE(), cond, result, …)` if/else-if
  idiom → `CASE`. Arguments are spanned before being parsed (`_argument_spans`), because only the
  count tells you whether a trailing argument is the last condition or the default value.
- **A measure or an aggregate inside an `IF`/`SWITCH` condition** (`_value_condition`). `IF([Margin]
  > 0, [Margin], 0)` and `IF(ISBLANK([M]), 0, [M])` are everyday shapes that used to take their whole
  visual down to a TODO, because a condition only accepted columns and literals. The enclosing
  `CALCULATE`'s filters are threaded into any aggregate the condition contains, so
  `CALCULATE(IF([Net] > 0, [Net], 0), Region[Name]="North")` filters *both* occurrences. This is
  deliberately **not** allowed in a `CALCULATE`/`FILTER` argument, where an aggregate is a table
  filter needing context transition, not a value — that still raises. `MIN/MAX(T[c])` keep their
  scalar "over the current selection" meaning (`{CTX:…}`) and are untouched by this.
- **`T[c] IN {"a","b"}`** → `c IN ('a','b')` (`_value_set`), plus prefix `NOT`, `ISBLANK`, `BLANK()`,
  `IFERROR(a,b)` → `COALESCE` (the only error this translator can produce is a divide-by-zero, which
  `/` and `DIVIDE` already turn into NULL). `IN` over a table expression (`IN VALUES(...)`) raises.
- Scalar functions that map one-to-one: `YEAR/MONTH/DAY`, `INT` (CAST to BIGINT — both truncate
  toward zero), `CEILING/FLOOR` **only with significance 1**, `MOD`, `POWER`, `SQRT`, `EXP`, `LN`,
  `UPPER/LOWER/TRIM`, `LEN` (→ `CHARACTER_LENGTH`), `CONCATENATE`. `CEILING(x, 0.5)` raises: DAX
  rounds to a multiple of the second argument and SQL's `CEILING` does not.

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

Drafted by `multi_fact` for a single-value card/kpi/gauge, a chart with 1–2 categories, or a table/matrix
with any number of category (row/column) fields. The translator re-runs in *split* mode (`{AGG:n}` per
aggregate, each tied to its table); every fact table gets its own derived table (its aggregates per
category, over that table joined only to the category tables and the slicers reaching it), and the
expression is evaluated on a query whose FROM is just the category tables (`SELECT DISTINCT`). A card
is the arms CROSS JOINed. A table/matrix keeps every category row even where the composite measure comes
back blank (a table doesn't drop rows just because one cell is empty) and names its column after the
measure's own name, not the synthetic `value`/`category`/`series` a chart uses; a chart drops a blank
group instead, as Power BI's own charts do.
Left manual: an aggregate that filters on another table, a `Promotions-Slicer`-style marker mixed in
(a selection-dependent `MIN`/`MAX` needs the fact table itself for its join, which the per-fact split
can't provide), several such composite values in one visual (mixing a composite total with a plain
single-table measure in the same table/matrix is not modelled either — it's still one composite value
at a time). Every fact table must relate to every category table, **and every pair of category tables
must be directly reachable from each other** for the categories-only outer query — two categories that
are only related to each other *through* a fact table (the common case for two unrelated dimensions)
still can't be composed; two columns of the *same* dimension table can. Note the category domain is the
category table's rows (after slicers on it), not "values that have facts".
- **A table/matrix with several independent value fields from more than one table** (not one
  composite expression spanning tables — see above) drafts via `_multi_value_table`: one derived
  table per table (whether it's conceptually a fact or a dimension makes no difference — its own
  fields, aggregated per category, joined only to the category tables), LEFT JOINed together on the
  shared category keys, each field kept as its own output column. Confirmed against two real
  reports (11 of 37 visuals in one). Left manual: any value field that is itself a composite
  spanning several tables (mixing that with independent ones isn't modelled), and any table where
  one arm's own join to the category tables has no relationship path at all (a category from a
  table genuinely unrelated to the value's table — correctly refused, not a bug).
- `_table_calc_columns` only ever tries one of the table's own real columns as the row-context anchor
  per calculated column (see below) — a calculated column referencing two different real sibling
  columns (e.g. `IF(a = 0, 0, b / a)` over two distinct source columns) is left unsupported today,
  confirmed against a real report (a "percent of usage" calculated column dividing two sibling
  usage columns): it correctly stays manual
  rather than mistranslating, but doesn't draft either. Widening this needs `_calendar_expr_sql` to
  take a dict of real columns instead of one `date_col`/`date_expr` pair — not done, since that
  signature is shared with the calendar-table path and well covered by tests.

## Filter-pane filters (report / page / visual level)

Filters with a condition are added to every drafted query as fixed WHERE predicates
(`semantic.effective_filters`, `filter_sql`, `_filter_where`): `In` / `Not In` (blanks kept when negated,
as Power BI does), comparisons (`=`, `<>`, `>`, `>=`, `<`, `<=`, `IS NULL`), `And`/`Or` ranges,
`Contains`/`StartsWith`/`EndsWith` (`LIKE` with `ESCAPE`), and **Top N** (`col IN (SELECT k FROM
(… GROUP BY) t WHERE (SELECT COUNT(*) FROM (… GROUP BY) u WHERE u.a <better> t.a) < N)`, i.e. RANK() <= N
counted, not windowed (Teradata error 3706 forbids ordered analytics in a subquery), ranked over the rows the
slicers leave; ties kept). A filter on a table the visual doesn't read is a semi-join through a direct relationship (like
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

## Teradata rejections found on a real system (2026-09-29)

Each of these passed sqlglot's Teradata parser, so **parsing with sqlglot is not proof the SQL runs**.

| Error | Cause | Fix |
|---|---|---|
| 3707 `... between AS and value` (also `rename`) | a reserved word used as an alias or column name: the renderer's own `value`, or a Power BI column/table called Rename, Date, Index... | every generated name is checked against `_TERADATA_RESERVED` (columns are double-quoted, table aliases get `_t`); `quote_reserved_aliases` quotes `AS <reserved>` and `alias.<reserved>` in SQL and table maps written earlier (type names after AS are left alone) |
| 3706 ordered analytical functions not allowed in subqueries | `IN (SELECT ... QUALIFY RANK() OVER ...)` for a Top N filter | count of strictly better values `< N` (same as RANK() <= N); windows are only safe in a derived table joined in FROM (`MIN(x) OVER ()` in the selection-min join) |
| 3888 A SELECT for a UNION, INTERSECT or MINUS must reference a table | an inline ("Enter Data") table is `SELECT 'E', 'x' UNION ALL SELECT ...` with no FROM | every literal-only arm gets `FROM (SELECT 1 AS one) AS one_row` |
| 2621 `Bad character in format or data of CALDATES.cdate` | a multi-value parameter default saved as the text `[2026]` (the edit page wrote `str(list)`), compared with an INTEGER year | the page shows a list as `a, b` and saves a list; `multi_values` also heals `[2026]`, `['a','b']` coming from a yaml or a URL |

**Older drafts are repaired when they are sent** (`fix_teradata_sql`, run by `TeradataBackend`): quoted reserved names, the counted Top N in place of the `QUALIFY` form (checked on a real report: the 48 old Top N visuals come out identical to the current drafter's), and the one-row source for set-operation arms. A yaml/table map written by an older version therefore works without regenerating; `pbix2html redraft <pbix>` (or the panel's *also redo queries the tool drafted earlier*) redoes the auto-drafted visuals, with a backup, and never touches SQL a person wrote.

Network failures (`Hostname lookup failed`, `Error 503 Lost connection`) come back as HTTP 503 "Teradata isn't reachable (network / VPN?)", not 500.

Reserved words: `_TERADATA_RESERVED` (documentation list as remembered; a missing word shows as a 3707 and is added).
Quoting is harmless in a Teradata-mode session (names are case-insensitive); an ANSI-mode session would make
quoted names case-sensitive. Send the error text back for anything new so the pattern is fixed and tested.
