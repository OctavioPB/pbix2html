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

## Findings from a real classic-format report (2026-09-29)

First real `.pbix` run through extract → scaffold → convert (11 pages, 232 visuals, mixed
Import/DirectQuery/Dual model, custom visuals, no RLS). Fixed in the repo: textbox runs bound
to a field crashed the visual; bundled custom visuals were not listed; group children were
drawn at the page's top-left (their x/y are relative to the group); page background
*images* were ignored (white title text on a white canvas); the table-map detector missed
`Teradata.Database(host, [Query="..."])` and `SEL`. **Still open** (design work, in priority order):

- [x] **Bookmarks and action buttons** — `docs/decisions/ADR-005`, phases 1, 2a, 2b done and
      verified on the real report (view switchers, monthly/historical toggle). Bookmarks saved on
      the historical page are reused on its monthly clone by group name (logged; owner confirmed
      the intent). Open: bookmark-captured filter/slicer state (phase 3) and PBIR bookmarks
      (`Report/definition/bookmarks/`, unverified).
- [x] **Button formatting** — `extract.parse_button` reads text (label, size, colour, font,
      bold/italic/underline, alignment), fill, outline, corner radius and icon per state
      (default / hover / pressed / disabled), and `render._button_css` converts it to validated CSS
      variables with hover/pressed/disabled rules, in both templates. The real report only uses
      `default` text (`fontSize`) and fill; every other property name is Power BI's documented one
      but **unverified against a real file**. Not done: icons (only `blank` seen), the
      `selected` state, and Power BI's built-in defaults when a card is absent (an unstyled button
      renders transparent; if Desktop shows a fill or grey text there, capture it).
- [x] Hidden `HST ...` pages are the *historical* twin of each monthly page, reached through a
      "Current Month / Historic Data" button pair (owner-confirmed). Now rendered when a
      visible page links to them (ADR-005 phase 2a). Tooltip pages stay out. Open: the twin
      pages duplicate every visual in the yaml; consider a `period` parameter (own ADR).
- [ ] PBIR keys (`isHidden`, `filterConfig`, `parentGroupName`, group-relative positions)
      still unconfirmed: this sample was classic. Group children in PBIR are probably
      relative too; `absolutize_group_children` is only applied to classic.

## Findings from a second real report, PBIR format (2026-09-29)

15 pages (8 hidden), 237 visuals, 16 DirectQuery tables + 2 DAX calculated tables. First real
PBIR file: it **confirmed** `page.visibility = "HiddenInViewMode"`, `parentGroupName` (children
positions are relative to the group, as in classic), `filterConfig.filters[].field`,
`sortDefinition`, and that button links live in `visual.visualContainerObjects.visualLink`.
Fixed: container formatting (`visualContainerObjects`) sits *inside* `visual`, so PBIR titles,
backgrounds, borders and links were never read; the custom theme (`report.json →
themeCollection`) was ignored in favour of the base one; PBIR visuals had no style/fill/action/
button/sort; and, for both formats:

- **`queryRef` keeps a table's old name after a rename** (`Sum(Fixed Capacity Mnthly.x)` for a field
  whose entity is `Active Compute Fixed Mnthly`; also the `Table.models` seen in the first report).
  Refs are now rebuilt from the real field.
- **No JOIN had ever been drafted from a real model**: the drafter read `FromTable/…` and the
  extractor emits `FromTableName/…`. Inactive relationships are now skipped.
- **`ORDER BY`** was never emitted (69 visuals here carry a sort); it is now, by column position.
- **A slicer with nothing selected returned 0 rows** (`IN (NULL)`); it now means "no filter".
- **Fan-out**: an arm/visual summing several fact tables through one join inflated the numbers.
  Each `UNION ALL` arm now joins only its own fact; one `SELECT` over several fact tables is left to
  a person. Series that share a column name are told apart by table.
- A table query ending in `-- comment` swallowed the closing `)` of `FROM (...) AS t`.
- The CLI never used the table map (only the panel did): 0 → 46 drafted here. Detection now also
  covers `Teradata.Database(host, [Query=...])`, `SEL`, and **"Enter Data" tables** (rows decoded
  from the M into `SELECT ... UNION ALL`). `DISTINCTCOUNTNOBLANK` translates. Reserved words used
  as columns (`date`, `year`, `month`...) are double-quoted.
- `pbix2html mapping <pbix>` (also run by `scaffold`) writes `out/<Report>/mapping_report.md`: per
  visual, why it was or wasn't drafted (`no_source`, `not_connected`, `unknown_table`,
  `untranslatable_measure`, ...), plus model facts (M:M relationships, tables with no relationship,
  calculated tables, composite measures).

Result: 91 of 94 drafted queries parse under sqlglot's Teradata dialect at first, 94 of 94 after the
comment fix (a syntax check only: none has been run on Teradata). **Still open**, in priority order:

- [x] **Slicers as real widgets** (`docs/decisions/ADR-006`): dropdown / list / hierarchy tree / date
      range, page- or sync-group-scoped parameters, saved selection as default, `slicers:` in the yaml
      with `options_sql`, and `/reports/{r}/slicers/{visual}` for `live`. Verified in a browser on
      both reports with a simulated backend (`live` and `hah`). Open: relative-date and tile
      slicers, cascading options, pair-accurate hierarchy selection, a real Teradata / HAH run.
- [x] **`Calendar` (a DAX `CALENDAR(start, end)` table)** is rebuilt on `sys_calendar.calendar`
      (`detect_calendar_tables`; owner-confirmed pattern, e.g. `CALENDAR("2017-01-01", NOW())`),
      together with its calculated columns (`FORMAT`, `YEAR/MONTH/DAY`, `VALUE`, `IF`, `&&`/`||`,
      `+ - *`; anything else is listed as "not translated", never guessed). The fact tables' date
      column `log_dt` (found in the model's column list, not only in the M) is proposed as
      `fact.log_dt → Calendar.Date` in `metrics/<Report>.relationships.json` — review it; delete
      nothing, set `"IsActive": 0` to switch one off. Date slicers now reach visuals that don't read
      the calendar through a semi-join (`fact.log_dt IN (SELECT date FROM calendar WHERE ... IN (:p))`,
      dropped by `bind` when nothing is selected), which is what filter propagation means in Power BI.
      Verified: 65 of 71 data visuals drafted on the second report, all parse as Teradata.
      Caveats: month names come from `TO_CHAR(..., 'Month')` (session language); a slash date like
      "04/01/2026" is read as MM/DD/YYYY and noted in the SQL; slicer parameters are named after the
      column only, so two slicers on same-named columns of different tables collide; date-*range*
      slicers (`BETWEEN`) are not modelled, only `IN`.
- [ ] Composite measures over unrelated fact tables (`Grand Total = [A] + [B] + ...`): one scalar
      subquery per term instead of a join.
- [ ] DAX with `VAR`/`EOMONTH`/time intelligence (`Projected Monthly Avg Spend`) stays manual.
- [ ] Two hidden pages are reachable from no button (drill-through? a bookmark?): listed nowhere yet.
- [ ] PBIR bookmarks and hidden visuals/groups: this file had none, so still unverified.
- [ ] Mapping report in the panel (only the CLI writes it).

## Findings from a third real report, composite model (2026-09-29)

- **Composite model** (Import + DirectQuery): 36 tables recovered from Power Query (`Value.NativeQuery`,
  `Teradata.Database(..., [Query=...])`, "Enter Data" → `UNION ALL`), plus a calculated `CALENDAR()` table
  with 14 calculated columns (FORMAT/EOMONTH/VAR-RETURN/sibling references) translated to `sys_calendar.calendar`.
- pbixray drops relationships touching calculated tables (SystemFlags=2) → `_all_relationships` reads them itself.
- Custom visuals mapped: HierarchySlicer→slicer, dynamicTooltip→tooltip. Doubled quotes in text literals are unescaped.
- CSS `z-index` must be an integer (`3000.0` is silently dropped, which hid slicers behind panels).
- Mapping: 54 data visuals, 44 drafted. Still manual: selection-dependent measures (`MIN(level)` over a
  slicer, `VAR` + `min(Calendar[Date])`), one table not connected to any fact (`not_connected`), 24 of 38 measures.
- `FILTER(T, T[c] = MIN(T[c]))` (selection-dependent "top level") is now drafted as a DISTINCT-key LEFT JOIN; unverified against real numbers. `MIN/MAX(T[c])` + `VAR` + month-comparison measures are drafted for card context (mapping 44 → 49 of 54). Grouped visuals evaluate MIN/MAX per group (calendar-column category, own-fact category, or whole selection when the category doesn't filter T); `Tooltips`-role fields are no longer drafted. A measure over several fact tables (`Headcount Ending`) is split into one derived table per fact (`multi_fact`). TestReport3 mapping: 53 of 54; only `Open Reqs`+`Workday codes` (no relationship) is left.
- Not yet verified: 100%-stacked charts in the renderer, MobileState (ignored), storage-mode info in the mapping report.

## Findings from a fourth real report, Import model (2026-09-29)

- **Filter-pane filters were never applied** to the drafted SQL (only listed in the yaml). All four real
  reports carry report/page/visual filters with real conditions (In, Not In, comparisons, Top N).
  Now applied as WHERE predicates (see skill `dax-to-teradata-sql`); the mapping report lists what
  isn't (aggregate/measure filters, relative date, multi-hop). Numbers of earlier drafts were
  therefore not comparable with Power BI; re-validate.
- All-Import model (34 partitions), every table from a Teradata custom query or inline data; calculated
  calendar `CALENDAR("01/01/2022", TODAY())` (text date). 38 of 42 data visuals drafted; the rest point to
  a table missing from the model (`Elastic Usage`) or aggregate a dimension next to a fact (fan-out guard).
- `model.json → table_modes` was polluted by engine-internal partitions (`H$…`, `R$…`, `U$…`): filtered.
- Drill-through pages (`howCreated` 5 filters): listed in the mapping report; navigation with a value
  from the source page is not modelled.
- `pbix2html mapping` now previews the table map from Power Query without saving it.

## Findings from a fifth real report, DirectQuery ops dashboard (2026-09-29)

- Small (5 pages, 4 hidden and unlinked; 9 DirectQuery tables from custom Teradata SQL with regexp/time-zone
  syntax that passes through untouched). 6 of 6 data visuals drafted after adding `FORMAT` with time parts,
  `TIME()` and `NOW()` (was: `FORMAT(MAX(ts) + TIME(4,0,0), "yyyy-mm-dd hh:mm:ss")` untranslatable).
- `NOW()` used to become `CURRENT_DATE` (lost the time): now `CURRENT_TIMESTAMP(0)`.
- A visual-level filter on a table with no relationship path to the visual has no effect in Power BI; not reported.
- Custom theme (`TeradataTheme*.json`) renders; the report has one visible page, so most of its content is
  reachable only with `--include-hidden`.

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

## Open items (consolidated, 2026-09-29)

Known and unresolved after five real reports; none has been checked against real Power BI numbers or a real
Teradata/HAH, which is the actual gate for every "implemented" line above.

- Validation: no reference values yet for cards drafted with filter-pane filters, Top N, selection-dependent
  (`MIN/MAX`, `FILTER … = MIN`) or multi-fact measures (ADR-007, ADR-008).
- Enum assumptions taken from memory (verify): filter aggregate codes, Top N direction/ties, `howCreated` 5.
- Filters not applied: on an aggregate or a measure, relative date, booleans, multi-column `In`, multi-hop.
- Drill-through navigation with a value from the source page; hidden pages reachable only via `--include-hidden`.
- Slicers: relative-date and tile modes, cascading options, pair-accurate hierarchy selection (ADR-006).
- Measures: `CALCULATE` shorthand override vs `FILTER` intersect, table/matrix over several fact tables,
  time intelligence beyond the recognised idioms.
- PBIR: bookmarks, hidden visuals and groups unverified on a real file.
- Mapping report exists only as CLI/markdown, not in the panel; the packaged skill's renderer is a simplified
  standard-library one (no slicer widgets, buttons, tooltips, 100 % stacked).
- A table with no relationship path to its visual's tables (`Open Reqs` + `Workday codes`) needs a decision
  from the report owner.

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
