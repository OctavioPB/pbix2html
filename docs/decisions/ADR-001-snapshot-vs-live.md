# ADR-001 — Snapshot vs. live as the delivery mode

**Status:** proposed. **Date:** 2026-09-11.

## Context
Executive reports with DirectQuery to Teradata and (possibly) RLS. We need `report.pbix → Report.html`.

## Options
1. **Snapshot**: data embedded in the HTML, generated on a schedule, one file per role. No
   runtime infrastructure; security = permissions of the folder/portal where it's published;
   data as fresh as the last generation.
2. **Live**: HTML queries a service that runs SQL with a PROXYUSER. Data as of the moment,
   security evaluated on read, real slicer interactivity. Requires a service, authentication,
   and operations.
3. **Both**, decided per report (`delivery` field in the yaml).

## Decision
Pending. Initial recommendation: start the pilot in **snapshot** (smaller surface area), and
move to **live** the reports whose RLS is per individual user or whose slicers have high
cardinality. The renderer supports both from the start so as not to close that door.

## Consequences
- Snapshot requires one HTML per role; never data from multiple roles in one file.
- Live introduces an operated component; requires ADR-002 (build vs buy) to be resolved.
