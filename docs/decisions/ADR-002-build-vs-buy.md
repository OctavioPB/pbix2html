# ADR-002 — Build our own host app vs. an open source tool (Superset / Metabase / Evidence)

**Status:** proposed. **Date:** 2026-09-11.

## Context
~50 executive dashboards. Phase 0's inventory will show what proportion is covered by a
small set of standard visuals.

## Criteria
Visual coverage, per-user RLS, embedding into the existing portal, branding, 3-year
operating cost, dependency on an internal team, licensing.

## Decision
Pending until `out/summary.md` covers all 50 reports. If >85% of visuals are card/bar/
column/line/table and RLS is per role (not per individual user), seriously evaluate
Superset or Evidence over Teradata; the extractor and the yaml remain useful as input to
those tools either way.
