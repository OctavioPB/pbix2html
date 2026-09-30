# ADR-008 — Filter-pane filters applied to drafted SQL

**Status:** accepted, implemented, **unverified against Power BI numbers**. **Date:** 2026-09-29.

## Context

`layout.json` carried report/page/visual filters but the drafter only listed them in the yaml
(`page_filters`, `visual_filters`); no drafted query applied them. All four real reports reviewed have
filters with conditions (In, Not In, comparisons, text search, Top N), so drafted numbers could not match.

## Decision

`semantic.effective_filters(layout, page, visual)` returns report + page + visual filters that carry a
condition; `_draft_where` adds them to every drafted query (single FROM, chart arms, multi-fact arms, context
derived tables) as **fixed predicates**, next to the slicer parameters.

- Supported: `In`/`Not In` (blanks kept when negated, as Power BI), `= <> > >= < <=`, `IS NULL`, `And`/`Or`,
  `Contains/StartsWith/EndsWith` (`LIKE … ESCAPE`), and Top N
  (`col IN (SELECT t.k FROM (… GROUP BY) t WHERE (SELECT COUNT(*) FROM (… GROUP BY) u WHERE u.a <better> t.a) < N)`, i.e. RANK() <= N without a window function: Teradata error 3706 forbids ordered analytics in a subquery; over the rows the slicers
  leave, ties kept).
- On a table the visual doesn't read: a semi-join through a direct relationship (like a slicer); with no
  relationship path the filter has no effect in Power BI either and is ignored.
- **Values are embedded, not parameters**, quoted by `_filter_literal`. They come from the .pbix, not from an
  end user (same trust as the inline-table and DAX literals already embedded); making them parameters would let
  a live client override a report author's filter.
- **Drill-through filters (`howCreated` 5) are skipped**: the saved value is the last one the author tried; the
  real value comes from a source page the HTML can't navigate from yet. The mapping report lists those pages.
- Not applied, and reported (`mapping_report.md`, visual `notes`): filters on an aggregate (`Sum(T.c) < 100`,
  would need HAVING), on a measure, booleans (Teradata has none), relative date, multi-column `In`, multi-hop.

## Consequences

- Drafts made before this change lack the filters; regenerate (`scaffold --overwrite`) before validating.
- Checked (2026-09-29) against Microsoft's published schemas (`microsoft/json-schemas`, `fabric/item/report/
  definition/semanticQuery/1.4.0` and `filterConfiguration/1.3.0`): aggregate `Function` codes (0 Sum,
  1 Average, 2 Distinct count, 3 Min, 4 Max, 5 Count of non-null, 6 Median, 7 StdDev, 8 Variance), `SortDirection`
  (1 ascending, 2 descending), `ComparisonKind` (0 =, 1 >, 2 >=, 3 <, 4 <=), the condition kinds (And, Or, Not,
  Comparison, Between, In, Contains, StartsWith, Exists) and the `howCreated` names (Auto, User, Drill, Include,
  Exclude, Drillthrough). PBIR writes `howCreated` as that name and the classic Layout as its position in
  the list (Drillthrough = 5, consistent with the real reports); `parse_filters` normalizes both to the number.
- Still **not** verified: Top N tie handling (`RANK` keeps ties, as DAX `TOPN` documents from memory: the docs
  page was unreachable from the sandbox), that PBIR's `VisualTopN` filter type has the same subquery shape as
  `TopN` (its structure is delegated to the semanticQuery schema; the code accepts it only if the shape matches),
  and the PBIR types `Range`, `Passthrough`, `Include`, `Exclude`, `Tuple`, `RelativeTime` (reported as not
  applied).
- Comparison filters on blanks: DAX treats blank as 0 in `<`; SQL drops NULLs. Left as SQL; validate.

## RelativeDate: shape confirmed against a real report (2026-09-30), still not implemented

A classic-format `type: "RelativeDate"` filter (`TestReport7`, a fact table's date column, an "in
the last N months" filter pane entry) has its `definition` as an ordinary `Between` condition, but with
`LowerBound`/`UpperBound` built from `DateSpan`/`DateAdd`/`Now` query-expression nodes instead of a
literal:

```json
"Between": {
  "Expression": {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "date_key"}},
  "LowerBound": {"DateSpan": {"TimeUnit": 0, "Expression":
    {"DateAdd": {"Amount": -13, "TimeUnit": 2, "Expression":
      {"DateAdd": {"Amount": 1, "TimeUnit": 0, "Expression": {"Now": {}}}}}}}},
  "UpperBound": {"DateSpan": {"TimeUnit": 0, "Expression": {"Now": {}}}}
}
```

`TimeUnit` here is **not** the embed-API's `RelativeDateFilterTimeUnit` (Days/Weeks/CalendarWeeks/
Months/...) — it's the semanticQuery schema's own enum, confirmed from the same Microsoft source this
ADR already cites for `ComparisonKind`/aggregate `Function` (`fabric/item/report/definition/
semanticQuery/1.4.0/schema.json`, `QueryDateAddExpression`/`QueryDateSpanExpression`): **0 Day, 1
Week, 2 Month, 3 Year, 4 Decade, 5 Second, 6 Minute, 7 Hour**. Decoded, this filter reads `date_key
BETWEEN DateSpan(Day, (tomorrow) - 13 Month) AND DateSpan(Day, now)` — Power BI's standard
"+1 day then subtract N units" construction for an inclusive "in the last 13 months" window;
`DateSpan(Day, x)` floors `x` to its date (drops the time-of-day). `Operator` (`InLast`/`InThis`/
`InNext`, per the embed-API's `RelativeDateOperators`) is presumably distinguished by which side gets
the `DateAdd` and the sign of `Amount` — only the `InLast` shape has been seen in a real file so far.

**Not implemented.** A fixed WHERE predicate matching this shape could reasonably compile straight to
`date_key BETWEEN CAST(CURRENT_DATE + 1 - INTERVAL '13' MONTH AS DATE) AND CURRENT_DATE` (letting
Teradata's own `CURRENT_DATE` evaluate fresh on every run sidesteps the snapshot-vs-live staleness
question entirely — the SQL text is fixed, what `CURRENT_DATE` evaluates to on execution is not).
Held back pending: a second real example to confirm the `InThis`/`InNext` shapes before generalizing,
and confirming the `TimeUnit` mapping above holds for `Week` (only `Day` and `Month` seen so far).
