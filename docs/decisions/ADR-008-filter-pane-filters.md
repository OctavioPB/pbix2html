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
  (`col IN (SELECT k FROM (… GROUP BY) QUALIFY RANK() OVER (ORDER BY agg dir) <= N)` over the rows the slicers
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
- Unverified assumptions taken from Power BI's enums: aggregate codes (0 Sum … 5 Count), Top N direction
  (1 asc, 2 desc) and tie handling, and `howCreated` 5 = drill-through.
- Comparison filters on blanks: DAX treats blank as 0 in `<`; SQL drops NULLs. Left as SQL; validate.
