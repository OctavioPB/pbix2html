# ADR-011 — Report-page tooltips

Status: **detection done, rendering deferred** (2026-10-02)

## Context

Power BI lets a visual use a whole *report page* as its tooltip. On hover, that page is drawn
next to the cursor, **filtered to the data point under it**.

Found in a real report (`TestReport9`): 20 visuals across 6 pages bind to one of three tooltip
pages. The binding is `visualContainerObjects.visualTooltip[].properties.section` = the page's
name; a tooltip page declares itself with `page.type == "Tooltip"` (PBIR) and is much smaller than
a canvas (175×80 and 338×190 here, against 1280×720). `___AUTO___` in that property means Power
BI's own built-in data tooltip, not a page.

These pages **carry data**. One holds three cards (`SUM(input tokens)`, `SUM(output tokens)`,
`SUM(cost)`); another a table broken down by product.

## Decision

Extract the binding and report it. **Do not draw the page yet.**

The reason is the whole point of the feature: hovering a bar in "Spend by Org" is supposed to show
*that org's* tokens and cost. Drawing the page without the hovered point's filter would show the
report-wide totals — the same three numbers on every bar — presented in the place a reader expects
a per-point number. That is a confident wrong answer, which this project refuses everywhere else
(CLAUDE.md rule 3, ADR-007). An honest gap in the mapping report beats a tooltip that lies.

So today:

- `extract.py` records `page.is_tooltip` and, per visual, `tooltip_page`.
- A tooltip page is never a tab: it is hidden, and a tooltip binding is not a navigation link, so
  the "hidden page reachable by a button" rule does not pull it in.
- `pbix2html mapping` gains a **Report-page tooltips** section naming every visual that depends on
  one, so a reviewer sees what is missing rather than discovering it on the page.

## What drawing them would take

The hard part is not the hover, it is the filter. The tooltip page's visuals need the host
visual's category field constrained to the hovered value — the same unsolved problem as
drill-through navigation ("doesn't carry the clicked value", PLAN).

- **live / hah**: feasible. The hovered point gives (field, value), and the existing parameter
  machinery already expresses exactly that as `col IN (:param)` (`_draft_where`). The cost is a
  query per hover, and one SQL variant per *(tooltip page, host visual)* pair, because different
  hosts constrain different columns — 3 pages × 20 hosts here, not 3 queries.
- **snapshot**: not generally possible. The data is embedded, so every point of every bound visual
  would have to be precomputed. Fine for a 7-bar chart, hopeless for a scatter.

That asymmetry is the real decision to make, and it is a product decision rather than a technical
one: a feature that works in `live` and silently does nothing in `snapshot` may be worse than one
that is documented as absent in both.

## Consequences

- A reviewer reading `mapping_report.md` knows which visuals lose their tooltip.
- Nothing renders differently today; no existing report changes.
- If drawing is taken up, start from `tooltip_page` + `is_tooltip`, and treat the per-host SQL
  variant count as the sizing number.
