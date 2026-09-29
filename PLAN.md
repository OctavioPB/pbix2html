# PLAN — Power BI → HTML migration (~50 executive reports, Teradata DirectQuery)

Status: **Phase 0 done, Phase 1 in progress.** Update this file when you close out each task.

## Phase 0 — Inventory and decisions (done)

- [x] `.pbix → layout.json + model.json` extractor with tests (`extract.py`).
- [x] Global inventory `out/summary.md`, `inventory_visuals.csv`, `inventory_measures.csv`.
- [ ] Run the extractor over the ~50 real reports and paste the numbers here:
      unique visuals by type, custom visuals, unique measures, reports with RLS, storage modes.
      **Progress (2026-09-28):** an external user ran the tool against real files and hit
      several environment/format issues, written up in `pbix2html-fixv1.md` and fixed in the
      repo: a Windows `UnicodeEncodeError` printing `→ ✓ ✗` on cp1252 consoles; a
      Python 3.13 + Starlette `Jinja2Templates` incompatibility in the panel; and — the big
      one — **all of their real `.pbix` files were PBIR format** (Power BI Desktop 2024+'s
      default), which the extractor didn't support at all. PBIR support is now in
      `extract.py` (see skill `pbix-layout`), and the filter / hidden / custom-visual paths are mapped from Microsoft's PBIR
      schemas (no more `TODO`s in `extract.py`), but remain unconfirmed against a real file. Still blocked on actually getting the ~50 files copied into `reports/` to
      run the batch and fill in these numbers.
- [ ] ADR-001: snapshot vs live (draft in `docs/decisions/`). Decide per-report or globally.
      **Blocked by the previous point** (the decision depends on the real inventory).
- [ ] ADR-002: build (this app) vs buy (Superset/Metabase/Evidence over Teradata). Input: inventory.
      **Blocked by the previous point.**
- [ ] Confirm access to `DBC.DBQLSqlTbl` for the Power BI gateway user.
      **Pending coordination with the Teradata DBA** (outside this repo).

## Phase 1 — End-to-end pilot (1 report)

Pick the most representative report (common visuals, ≥1 slicer, RLS). Record here: `Pilot report: ______`

- [ ] `pbix2html extract` on the pilot; review `layout.json` against the report open in Desktop.
- [ ] Capture SQL per visual from DBQL (skill `teradata-directquery`) → `metrics/<Pilot>.yaml` (`reference_sql`).
- [ ] Write `sql` per visual in the yaml (parameterized by slicers). Skill `dax-to-teradata-sql`.
- [ ] `pbix2html convert --mode snapshot`; renderers needed for the pilot (card, bar, line, table, slicer as parameter).
- [ ] `pbix2html validate` green for every visual in the pilot (tolerance in the yaml).
- [ ] Review with the report owner. Record accepted differences in the yaml's `notes`.
- [ ] Measure: hours spent per stage. This number sizes Phase 3.

## Phase 2 — Live mode and security

- [ ] `serve.py`: endpoint `GET /reports/{r}/visuals/{v}?param=...`, authentication (Entra ID or another corporate SSO).
- [ ] Trusted sessions in Teradata: `GRANT CONNECT THROUGH` to the service account; `SET QUERY_BAND='PROXYUSER=...'`.
- [ ] Replicate the pilot's RLS rules in Teradata (secure views or RLS constraints). Record in ADR-003.
- [ ] Result cache keyed by (report, visual, parameters, role) with configurable TTL.
- [ ] `convert --mode live` produces an HTML that consumes the endpoint; validate the same numbers as snapshot.

## Phase 3 — Factory (remaining reports)

Suggested order: by business area, starting with the ones that reuse measures already written
(`inventory_measures.csv` deduplicated by expression). One row per report:

| Report | Extract | Yaml SQL | Snapshot | Validate | Live | Owner OK | Notes |
|---|:-:|:-:|:-:|:-:|:-:|:-:|---|
| (pilot) | | | | | | | |

- [ ] Additional renderers by inventory frequency (matrix, waterfall, gauge, combo…).
- [ ] Shared measures library (`metrics/_shared.yaml`) for SQL reused across reports.
- [ ] Scheduled snapshot generation (cron/Airflow) per role, with permission-based distribution.

## Phase 4 — Cutover and operation

- [ ] Coexistence period: HTML and Power BI in parallel, automated weekly validation.
- [ ] Operations runbook: how to add a measure, a report, a role.
- [ ] Retire Power BI reports one by one after owner sign-off.

## Open risks

- Measures with complex time intelligence (nested SAMEPERIODLASTYEAR, TOTALYTD with filters) need windowed SQL; estimate separately.
- Custom visuals: manual reinterpretation. List which ones and how many after Phase 0.
- Cross-filter interactions between visuals: out of scope unless an owner requires it; in that case, live mode only.
- .pbix format variations across Desktop versions; extend `make_fake_pbix.py` (classic) /
  `make_fake_pbir_pbix.py` (PBIR) and tests with each new real case found.
- **New delivery mode `hah`** (ADR-004, added 2026-09-28): emits HTML compatible with the
  teradata-report skill / HTML App Host, as an alternative to `serve.py` for live mode.
  Implemented per spec in `pbix2html-fixv1.md` #10, but **not validated against a real HAH
  environment or the teradata-report MCP tools** in this session — treat generated `hah`
  HTML as unverified until someone with HAH access tries an actual upload/view.
