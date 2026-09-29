# Architecture

```
 report.pbix ──▶ extract ──▶ layout.json ─────────────────────────┐
                    │                                              │
                    └──▶ model.json ──▶ semantic (scaffold) ──▶ metrics/<R>.yaml
                                                                   │  (SQL per visual,
                                                                   │   parameters, roles,
                                                                   │   reference_sql)
                       ┌───────────────────────────────────────────┘
                       ▼
        snapshot mode: query.py ──▶ data per visual ──▶ render.py ──▶ Report.html (embedded data, 1 per role)
        live mode:     render.py ──▶ Report.html (API_BASE) ──▶ serve.py ──▶ query.py ──▶ Teradata (PROXYUSER)
                                                                  ▲
                                             validate.py ◀────────┘ (sql vs reference_sql)
```

## Key decisions

- **The yaml is the semantic layer.** It's the only place the migrated business logic
  lives. It's text, versionable, reviewable by a person. `layout.json` and `model.json`
  are derived from the .pbix and get regenerated; the yaml doesn't.
- **Two modes, one template.** Snapshot for simple, scheduled distribution; live for
  per-user security and current data. The HTML is the same except for the data source.
- **Security lives in Teradata, not the app.** Trusted sessions + secure views. The app
  only carries the identity through.
- **No Microsoft runtime.** Everything runs on Linux/Python; PBIXRay reads the .pbix
  without Power BI.

## Per-visual data format (contract between query, serve, and render)

```json
{"columns": ["category", "value"], "rows": [["North", 1234.5], ["South", 987.0]]}
```

## Yaml structure (`metrics/_template.yaml` is the annotated reference)

- `report`, `source`, `connection`
- `parameters`: name → {type, default, from_slicer, multi}
- `roles`: name → {proxy_user | where}
- `visuals`: layout id → {kind, title, sql, params, reference_sql, format, sort, tolerance, notes}

## How a visual's SQL is drafted (`semantic._draft_visual_sql`)

1. Fields → aggregate expressions (`_DaxTranslator`); tables → FROM/JOIN through the model's relationships
   (`_draft_from_clause`); a measure over several fact tables is split per table (`multi_fact`, ADR-007).
2. WHERE = slicer parameters (ADR-006: direct, or semi-join through a relationship, optional
   `/*if p*/…/*fi p*/` blocks that `query.bind` drops when empty) + **filter-pane filters** (ADR-008).
3. Selection-dependent pieces (`{SELMIN…}`, `{CTX…}`, `{AGG…}` markers) are expanded into joins (ADR-007).
4. `diagnose_visual` / `mapping_report` give the reason a visual wasn't drafted; `pbix2html mapping` writes
   `out/<Report>/mapping_report.md` (not drafted and why, filters not applied, drill-through and hidden pages,
   storage modes, unrelated tables, composite measures). It only reads; table map and relationships are
   previewed from Power Query, not saved.

Side files next to the yaml: `metrics/<R>.table_map.json` (Power BI table → Teradata query; auto-detected from
Power Query, `Value.NativeQuery`, inline "Enter Data", calculated calendars → `sys_calendar.calendar`),
`metrics/<R>.relationships.json` (proposed calendar → fact date key), `metrics/<R>.theme.json` (override).

## Where things are documented

ADR-001 snapshot vs live, ADR-004 HAH, ADR-005 bookmarks/buttons, ADR-006 slicers, ADR-007 filter context as
joins, ADR-008 filter-pane filters. Extraction details: skill `pbix-layout`; SQL patterns: skill
`dax-to-teradata-sql`; renderer contracts: skill `html-renderer`; validation: skill `validate-report`.
