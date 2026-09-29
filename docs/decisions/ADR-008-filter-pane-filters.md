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
