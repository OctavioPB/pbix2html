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
