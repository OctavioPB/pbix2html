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
      both reports with a simulated backend (`live` and `hah`). `tile` mode now draws a real widget
      too (2026-09-30: a row of toggle chips, same parameter as `list`/`dropdown`). Open:
      relative-date slicers, cascading options, pair-accurate hierarchy selection, a real
      Teradata / HAH run.
- [x] **`Calendar` (a DAX `CALENDAR(start, end)` table)** is rebuilt on `sys_calendar.calendar`
      (`detect_calendar_tables`; owner-confirmed pattern, e.g. `CALENDAR("2017-01-01", NOW())`),
      together with its calculated columns (`FORMAT`, `YEAR/MONTH/DAY`, `VALUE`, `IF`, `&&`/`||`,
      `+ - *`; anything else is listed as "not translated", never guessed). The fact tables' date
      column `date_key` (found in the model's column list, not only in the M) is proposed as
      `fact.date_key → Calendar.Date` in `metrics/<Report>.relationships.json` — review it; delete
      nothing, set `"IsActive": 0` to switch one off. Date slicers now reach visuals that don't read
      the calendar through a semi-join (`fact.date_key IN (SELECT date FROM calendar WHERE ... IN (:p))`,
      dropped by `bind` when nothing is selected), which is what filter propagation means in Power BI.
      Verified: 65 of 71 data visuals drafted on the second report, all parse as Teradata.
      Caveats: month names come from `TO_CHAR(..., 'Month')` (session language); a slash date like
      "04/01/2026" is read as MM/DD/YYYY and noted in the SQL; slicer parameters are named after the
      column only, so two slicers on same-named columns of different tables collide; date-*range*
      slicers (`BETWEEN`) are not modelled, only `IN`.
- [x] Composite measures over unrelated fact tables (`Grand Total = [A] + [B] + ...`) for
      table/matrix visuals (2026-09-30): `multi_fact` (already used by cards/charts) now also
      drafts table/matrix, with any number of category fields, keeping blank-measure rows
      instead of dropping them and naming its column after the measure rather than a chart's
      synthetic `value`. Still left manual: mixing a composite total with a plain single-table
      measure in the same table, and two category fields that are only related to each other
      *through* a fact table (not directly) — see skill `dax-to-teradata-sql`.
- [ ] DAX with `VAR`/`EOMONTH`/time intelligence (`Projected Monthly Avg Spend`) stays manual.
- [ ] Two hidden pages are reachable from no button (drill-through? a bookmark?): listed nowhere yet.
- [x] PBIR bookmarks (2026-09-30): `extract._parse_bookmarks_pbir` reads
      `Report/definition/bookmarks/*.bookmark.json` into the same shape `parse_bookmarks`
      produces for classic (ADR-005). Hidden visuals/groups were already read for PBIR
      (`isHidden`) — both are still **unverified against a real file**: every real PBIR sample
      seen so far had neither bookmarks nor hidden groups to check against.
- [x] Mapping report in the panel (2026-09-30): `GET /reports/{r}/mapping.md` renders it on
      demand from the already-extracted layout/model/table-map (no separate "run mapping"
      step), linked from step 1 once Extract has run.

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
- Not yet verified: MobileState (ignored), storage-mode info in the mapping report. (100%-stacked
  charts: two real bugs found and fixed later the same week — see "The 100 % stacked column" below.)

## Findings from a fourth real report, Import model (2026-09-29)

- **Filter-pane filters were never applied** to the drafted SQL (only listed in the yaml). All four real
  reports carry report/page/visual filters with real conditions (In, Not In, comparisons, Top N).
  Now applied as WHERE predicates (see skill `dax-to-teradata-sql`); the mapping report lists what
  isn't (aggregate/measure filters, relative date, multi-hop). Numbers of earlier drafts were
  therefore not comparable with Power BI; re-validate.
- All-Import model (34 partitions), every table from a Teradata custom query or inline data; calculated
  calendar `CALENDAR("01/01/2022", TODAY())` (text date). 38 of 42 data visuals drafted; the rest point to
  a table missing from the model or aggregate a dimension next to a fact (fan-out guard).
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

## HTML verifier (2026-09-29, ADR-009)

`pbix2html verify` (static + rendered checks, pixel contrast, numbered screenshots) was run on the five real reports
and exposed conversion bugs now fixed: theme colour ids lost when a report has no custom theme, textbox paragraph
alignment, default shape fill, card number colour, slicer text on its own white control, translucent button text.
Open: text drawn inside charts is not checked; chart label heuristics are approximate; no comparison with Power BI
reference screenshots.

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

- [x] `serve.py`: endpoint `GET /reports/{r}/visuals/{v}?param=...`, authentication (Entra ID or another corporate SSO).
- [ ] Trusted sessions in Teradata: `GRANT CONNECT THROUGH` to the service account; `SET QUERY_BAND='PROXYUSER=...'`.
- [x] Replicate the pilot's RLS rules in Teradata (secure views or RLS constraints). Record in ADR-003.
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
- Filter enums checked against Microsoft's JSON schemas (ADR-008); still open: Top N ties, PBIR `VisualTopN` shape,
  PBIR filter types `Range/Passthrough/Include/Exclude/Tuple/RelativeTime`.
- Filters not applied: on an aggregate or a measure, relative date, booleans, multi-column `In`, multi-hop.
- Drill-through navigation with a value from the source page; hidden pages reachable only via `--include-hidden`.
- Slicers: relative-date mode, cascading options, pair-accurate hierarchy selection (ADR-006).
- Measures: `CALCULATE` shorthand override vs `FILTER` intersect, mixing a composite multi-fact value with
  a plain measure in the same table/matrix, two category fields related only through a fact table (not
  directly), time intelligence beyond the recognised idioms.
- PBIR: bookmarks (now parsed, see ADR-005) and hidden visuals/groups still unverified on a real file.
- The packaged skill's renderer is a simplified standard-library one (no slicer widgets, buttons, tooltips,
  100 % stacked) — unlike the main package, which now also surfaces the mapping report in the panel.
- A table with no relationship path to its visual's tables (`Open Reqs` + `Workday codes`) needs a decision
  from the report owner.

- Teradata run findings: reserved words as names (3707), window function in a subquery (3706), multi default saved as text (2621); see skill `dax-to-teradata-sql`. More expected until every draft has run on a real system.

## Findings from a real `live` Teradata run (2026-09-30)

The first real gate mentioned throughout this file — nothing had run against real Teradata or been
validated numerically — is partially crossed: a user ran `serve.py`/`live` against several real
reports and sent the uvicorn error log plus three of the underlying `.pbix` files (anonymized here
and throughout as `TestReport6/7/8`; table, column and business names below are illustrative, not
the reports' real schema, per this project's convention of never persisting a customer's real data
into project files (see ADR-007's own anonymized example). Every failure in the log was a Teradata `[Error 3810] Column/Parameter 'x' does not
exist` or `[Error 3807] Object 'x' does not exist` — no Python-side crash, no HTTP layer bug;
`ConnectionResetError`s in the log are just a browser tab closing mid-request, not a bug.

- **Fixed**: a calculated column on a plain (non-calendar-table) dimension — `IF(ISBLANK(d),
  BLANK(), DATE(YEAR(d), MONTH(d), 1))`, a date bucketed to the 1st of its month — was being
  drafted as a bare source-column reference. pbixray's schema lists a calculated column exactly
  like a real source column, so `_resolve_field` had no way to tell them apart — it isn't in the
  mapped `table_map` query, so Teradata rejects it at runtime with exactly this error shape.
  Generalized the calendar table's own calculated-column translator (`_calendar_expr_sql`, now also
  understanding `ISBLANK`/`BLANK()`/`DATE(y,m,d)`) to any regular table's calculated columns
  (`_table_calc_columns`); confirmed against `TestReport6`'s real model that the same idiom now
  translates to correct Teradata. One that doesn't match the grammar now correctly leaves the
  *whole visual* manual instead of drafting a wrong reference — see skill `dax-to-teradata-sql`.
- **Not a pbix2html bug**: most of the log's other `[Error 3810]`/`[3807]` failures are consistent
  with **schema drift**: for one of them, pbixray's own cached DirectQuery schema (from
  `TestReport6`'s `model.json`) says the column exists — Power BI cached that schema when the
  report was authored against the mapped source view, and the *live* Teradata view apparently no
  longer has it. pbix2html faithfully reproduced what the `.pbix` says; the underlying view changed
  since. Couldn't confirm the rest the same way — the other two reports named in the log aren't
  among the three `.pbix` files shared — but the naming of the failing columns strongly resembles
  the same "date bucket" calculated-column family, so worth re-running mapping on those two
  specifically to check.
- **Possible follow-up, not done**: a `table_map` entry written as `SELECT *` (several of the real
  ones are) has no visible column list at all, so a source view changing its columns has zero local
  signal until Teradata errors at request time. Flagging `SELECT *`-based entries in the mapping
  report as "schema drift risk: can't tell what columns this promises" would surface exactly this
  class of failure before conversion instead of after, but needs a decision on how noisy that
  warning should be (most `Query="select * from ..."` M sources in real reports are like this).

## Full mapping review of `TestReport6`, 2026-09-30

Beyond the calculated-column fix above, a full pass over every visual this real report's mapping
report (38 data visuals, 24 → 26 now drafted) couldn't place found:

- [x] **Combo charts never auto-drafted** (`kind:combo`): `_DRAFTABLE_KINDS` didn't include `combo`
  even though `render.py` fully renders it. Fixed: it now shares the plain multi-measure chart's
  `UNION ALL`-arms drafting (`_UNION_ARM_KINDS`); which series is a line vs. a column comes from the
  field's own role (`Y2` → line) via a new `_combo_axis`, written to the yaml's `axis`, never into
  the SQL. In this report both combo visuals still don't draft — not a regression, a *different*,
  already-known limit: their `Y2` measure is a calculated column referencing two real sibling
  columns of its own table, one more than `_table_calc_columns` currently resolves (see skill
  `dax-to-teradata-sql`).
- [x] **A table/matrix with several independent value fields spanning more than one table never
  drafted** (2026-09-30, fixed — a handful of `TestReport6` table visuals mixing a fact's own
  measures with one attribute from its directly-related dimension, and turned out to be the single
  biggest gap across all three shared reports: 11 of `TestReport8`'s 37 data visuals hit it, e.g. a
  customer summary table pulling one total each from four unrelated fact tables side by side).
  `_draft_visual_sql` diverted anything with `value_tables(values) > 1` straight to `multi_fact`,
  which only ever resolves *one* composite value field — so a table with several independent
  `Sum(...)` fields from different tables always refused, whether those tables were two genuinely-
  independent facts or just a fact plus one attribute from its own directly-related dimension (a
  dimension's own numeric column next to a fact's own measures — safe, not a fan-out, since the
  fact's own row-identifying columns are already in the GROUP BY). New `_multi_value_table`: the
  same derived-table-per-table + LEFT-JOIN-on-shared-category-keys split `multi_fact` already uses
  for one composite value, generalized to carry each of several independent value fields as its own
  output column instead of one `{AGG:n}`-substituted formula — one arm per table, whether that table
  is conceptually a "fact" or a "dimension" makes no difference to the join's safety, so no separate
  safety analysis was needed after all. `TestReport6`: 26 → 27 drafted; `TestReport8`: 26 → 32.
  Still left manual: a value field that is itself a composite spanning >1 table mixed among
  independent ones, and any table where the fact-table's own join fails a category outright (seen in
  `TestReport8`: a column summed from a table with no relationship to the visual's other, dimension-
  grained table — correctly refused, not a bug; summing that particular column made no sense in the
  first place).
- [ ] **A "several measures" chart only supports exactly one category field** (2 measures × 2
  categories, a two-level date hierarchy). The `UNION ALL`-per-measure design pivots each arm on a
  single category column; a second category would need a composite key per arm. Narrower and rarer
  than the two items above — not attempted.
- **Not a bug — confirmed correct**: 6 visuals reference a table that doesn't exist anywhere in the
  model (`model.json["tables"]` has no matching table at all). They sit on an already-hidden,
  unlinked page — a genuinely orphaned/abandoned page in the original report, not a pbix2html bug.
  `unknown_table:` is the right diagnosis.
- **Not a bug — deliberate, documented boundary**: two measures use
  `CALCULATE(MIN(...), ALLCROSSFILTERED(Dates))`. `ALLCROSSFILTERED` is in the same family as
  `ALL`/`ALLSELECTED`, which `semantic.py` explicitly refuses on purpose. Correctly left manual.
- **Confirms an already-documented gap, now with real evidence**: 4 bookmarks (a small colour-named
  set, e.g. "Red"/"Green"/"Yellow"/"White") set a **page-level filter** on an unrelated status
  column, not group visibility — `groups: {}` for all four in `layout.json["bookmarks"]`. This is exactly ADR-005's
  "Phase 3 — bookmark state beyond visibility... these change data, so each is a per-report
  decision," deliberately deferred. Today these four buttons render as inert in the migrated HTML.
  Real, concrete case for anyone deciding whether Phase 3 is now worth doing.

## Full mapping review of `TestReport7`, 2026-09-30

Same exercise as `TestReport6` above, on the second of the three shared `.pbix` files (5 pages, 50
visuals, 9 data visuals, 7 → still 7 drafted — the two undrafted ones are a different, new limit).

- **Major finding: the `RelativeDate` filter's real internal shape is now confirmed**, with an
  authoritative `TimeUnit` enum (Microsoft's own `semanticQuery/1.4.0` schema — the same source
  already trusted for `ComparisonKind`/aggregate `Function` in ADR-008). Recorded in full in ADR-008.
  This was the exact thing blocking relative-date slicer/filter support (raised earlier as "genuine
  schema uncertainty, no real file to confirm against" — now there is one). **Still not implemented**:
  only the `InLast` shape has been seen once; wanted a second real example (ideally an `InThis`/
  `InNext` one, or at least a `Week`-unit one) before generalizing the translation, given how much a
  wrong turn here would silently mis-date a filter rather than fail loudly.
- [ ] **A chart with a 3-level drill hierarchy category never drafts** (2 visuals, both grouped by a
  three-level date/hour/interval hierarchy). This is a bigger gap than a drafting oversight: the
  chart *rendering* contract itself only has two slots (`category`, `series` — see skill
  `html-renderer`), so there's nowhere in the current renderer for a third hierarchy level to go
  even if the SQL drafted it. The faithful option is probably to flatten to the hierarchy's top
  level only (a static/live HTML can't offer Power BI's click-to-drill anyway) and say so in the
  visual's `notes` — a product decision, not attempted here.
- **Confirms the calculated-column fix generalizes**: this report has the same month-bucketing
  calculated column (identical DAX, different casing) as `TestReport6`, used by 3 visuals, and all 3
  now draft cleanly — direct cross-report validation of that fix, not just the one report it was
  found on.
- **Confirms the "schema drift" diagnosis, not a new bug**: one of this report's tables has genuine
  schema columns matching several of the original traceback's failing column names (none are
  calculated columns — checked `model.json["calculated_columns"]`), sourced via the *same*
  `SELECT *` view as `TestReport6`'s copy of the same table. This confirms one shared, genuinely
  schema-drifted (or table-map-stale) view reused across reports, strengthening the case for the
  `SELECT *` schema-drift-risk flag suggested above.
- `ALLCROSSFILTERED` reappears on the same measure pair, identical shape to `TestReport6` — confirms
  it's a shared idiom across this report family, not a one-off; still correctly left manual
  (deliberate boundary, see above).
- Same 4 colour-named bookmarks, **identical bookmark ids** to `TestReport6`'s — clearly a shared
  template/legend component pasted across this team's reports, not independently authored each
  time. Reinforces that ADR-005 Phase 3 (filter-setting bookmarks) would pay off across more than
  one report if ever prioritized.
- No custom visuals, no new slicer modes (`dropdown`/`between`/`list` only), no other surprises in the
  visual type inventory (`slicer` ×15, `textbox` ×10, `shape` ×6, `image` ×5, `tableEx` ×4,
  `pivotTable` ×2, `columnChart` ×2, `basicShape` ×2, `lineChart` ×1).

## Full mapping review of `TestReport8`, 2026-09-30

The third of the three shared `.pbix` files (13 pages, 169 visuals, 37 data visuals, 26 → 32 drafted
after the fixes below). By far the richest of the three: 21 tables, 33 relationships, 8 of them M:M.

- **The headline finding across all three reports**: 11 of 37 data visuals here hit the
  "several independent value fields from more than one table" gap — see `_multi_value_table` above.
  Fixed; 6 of the 11 now draft (the other 5 have a value field summing an unrelated table with no
  join path to the visual's other tables — correctly still manual, not this bug).
- [x] **Combo-axis bug, caught by this file**: a combo chart with only *one* total measure (a
  second-axis measure alone, no primary-axis measure at all) drafts through `_draft_visual_sql`'s
  plain single-value chart shape (`category`/`value`, no `series` column), not the several-measures
  `UNION ALL` arms — but `_combo_axis` was still keying its result by the field's own label instead
  of the literal `'value'` key the renderer's `series()` actually groups under when there's no
  `series` column to read. The yaml's `axis` entry was therefore silently never applied — the chart
  would have rendered as a bar instead of a line, with no error anywhere. Fixed same day the combo
  feature was added; caught before it shipped to anyone.
- A found-but-not-newly-broken risk, now with real scale: **8 many-to-many relationships** (several
  fact tables all relating into one shared dimension table). These draft as a plain join like any
  other relationship — already-documented, deliberate behavior (`validate` is what's meant to catch
  the duplication risk, per the `dax-to-teradata-sql` skill) — but a drafted visual's own `notes`
  don't currently say *which* specific visuals actually traverse one of these 8 edges, only that the
  model has them (the mapping report's "Many-to-many relationships" section is global, not per
  visual). A safe, low-risk follow-up: flag a drafted visual's notes when its own join path uses one,
  so `validate` review time concentrates on the numbers that actually need it — not attempted here.
- **Confirmed drill-through in the wild**: one page has a real drill-through field (a rate column
  on a fact table, `howCreated` 5, `definition: null`) — correctly identified and listed, saved
  value correctly not applied (per ADR-008/CLAUDE.md's documented reasoning: a drill-through's saved
  value is just the last one the author tried, not real context). No change needed; a real example
  if drill-through navigation is ever tackled.
- A measure referencing another measure by its bare `[Name]` (e.g. `[Base Count] * MAX(...)`)
  drafted correctly, inlining the referenced measure's own `DISTINCTCOUNT` — confirms measure-of-
  measure translation holds up on a third real report. A sibling measure references the same base
  measure via the fully-*qualified* `Table[Name]` form instead of the bare bracket one; it isn't
  used by any visual in this report, so its handling couldn't be observed — worth a unit test if it
  turns out to matter (the "measures built from other measures" note in the mapping report only
  picked up the bare-bracket case, suggesting the qualified form might not be recognized the same
  way, though that's only the informational note, not necessarily drafting itself).
- No custom visuals. Slicer modes: `dropdown`/`between` only (86 slicers total — most pages repeat
  the same set). 4 more "Red/Green/Yellow/White" bookmarks, same pattern as the other two reports.

## Everything inside the canvas now scales with it, 2026-10-01

A user asked why a scorecard's callout number stops being legible after resizing. Measured before
changing anything, on a card with no explicit size in the report:

| viewport (Fit page) | card width | value | value / card |
|---|---|---|---|
| 2560×1400 | 402px | **33.6px (capped)** | 8.4% |
| 1500×820 | 224px | 22.4px | 10.0% |
| 900×600 | 151px | 15.1px | 10.0% |

- [x] **The fixed `rem` ceilings were the bug.** `clamp(1rem, 20cqmin, 2.4rem)` tops out at 33.6px,
  so past a certain page size the number stops growing while its card keeps going — it shrinks
  *relative to the card*. Every size inside the page is now a multiple of `--scale`
  (`clamp(calc(1rem * var(--scale)), 20cqmin, calc(2.4rem * var(--scale)))`), the same treatment a
  report-specified size already got. Also applied to the visual title and subtitle, card label,
  KPI target, table and multicard text, shape text and text boxes. After: a constant **10.0%** at
  every viewport and zoom tested, nothing clipped. A size the report *does* specify was already
  proportional (14.5%) and is unchanged.
- [x] **Chart text could not follow at all**: it is drawn inside a canvas, which CSS `--scale`
  cannot reach, so axis labels and data labels stayed at their design pixel size — tiny on a big
  monitor, oversized at 50 %. `scaleFonts` multiplies every `fontSize` in the chart's stored option
  by the page scale and re-applies it. It rebuilds plain objects and arrays only, passing anything
  else (formatter **functions** above all) through by reference — a deep clone would have silently
  destroyed the number formatting. A 6px floor stops text vanishing at 50 %.
- `axisLabel` had no `fontSize` of its own (ECharts defaults to 12), so there was nothing to
  multiply; it is now spelled out as 12, which leaves 100 % looking exactly as it did.
- Re-applied only when the mode or the scale actually changed (`chartState`), so dragging a window
  edge does not call `setOption` on every chart on every resize event.
- **Page chrome deliberately does not scale** — the header, tabs and parameter bar belong to the
  viewer's browser, not to the report.
- Two bugs the Python suite could not see, both caught by a browser smoke test: the `hah` template
  threw `Cannot access 'labelSel' before initialization` (its script defines `chart()` far later,
  so the helpers had to move above the canvas block), and lifting a block between templates
  duplicated `fitText`/`scalePage`/`fitAll`, giving `Identifier 'scalePage' has already been
  declared`. **Render the hah template in a browser after touching its script**, not just the main one.

## Data labels are the viewer's choice too, 2026-10-01

A user pointed at a dense time series whose data labels overdraw each other into an unreadable
smear — Power BI draws one number per point regardless of how many points there are.

- [x] A **`Labels`** control beside `View`: *As report* (default), *Show*, *Hide*. The option each
  chart was built from is kept in `chartOptions[id]`, and only `series[].label.show` is flipped —
  no re-query, and a chart painted later (live mode) picks up the current choice as it is created.
- **The label object is now built even when the report hides them**, carrying `show: !!cs.labels`.
  That is the point of the change: forcing labels on keeps the report's own formatter, display
  units and precision, instead of dumping raw unformatted numbers on the chart. A pie's label
  fallback gained an explicit `show: true` (ECharts' default) so it has something to flip.
- *As report* leaves every chart exactly as the `.pbix` set it, so the default output is unchanged.
  Per viewer (`localStorage`), like the zoom and the tab position.
- Verified in Chromium for both templates, for a chart whose report says labels on *and* one whose
  report says off: the default honours each, Hide clears both, Show sets both and the formatter
  survives (`typeof label.formatter === 'function'`).
- The `hah` template needed the label helpers moved **above** the canvas-size block: its script
  defines `chart()` much later than the main template, so the `remember(labelSel, …)` call hit a
  temporal-dead-zone error on `labelSel`. Caught by a browser smoke test, not by the suite.

## Regression: an empty level slicer blanked every chart over the calendar, 2026-10-01

Reported with before/after HTML, yaml and screenshots: after the date-hierarchy slicer feature,
`Headcount (at month end)`, `Departures and New Hires` and `Voluntary Attrition` rendered as empty
boxes. Cards and tables were fine. The cause is the interaction between two mechanisms, neither
obviously wrong on its own:

- "Nothing selected" means "no filter" in Power BI, and `query.bind` delivers that by rewriting
  `<column> IN (:name)` to `1=1`. Its pattern (`[\w."]+ IN (:name)`) only recognises a **plain
  column** on the left.
- A date-hierarchy level is an *expression* — `EXTRACT(YEAR FROM calendar.end_of_month)` — so it
  never matched. The empty parameter fell through to the generic substitution, which turns an
  empty list into `NULL`: `... IN (NULL)`, which returns **no rows at all**. Every chart joined to
  the calendar came back empty, while cards reading the fact table directly were unaffected.
- [x] Fixed in `_param_predicate`: a predicate whose left side is not a plain column is emitted
  as `/*if name*/ … /*fi name*/`, the marker `bind` already uses to drop a whole predicate. Plain
  columns are untouched, so every other report's SQL is byte-identical — no churn, no re-drafting.
- Proved locally without Teradata, which is the useful part of this one: binding the **shipped**
  yaml with an empty slicer gives 2 × `IN (NULL)`; the old yaml gives 0; re-drafting the same
  three visuals from the real `.pbix` now gives 0. The regression test asserts the bound SQL, not
  the drafted text, since the drafted text looked perfectly reasonable.
- The other diff in that report (`QUALIFY RANK() OVER (...)` → a correlated subquery) is **not**
  part of this: it comes from `72e389c`, the deliberate fix for Teradata rejecting a window
  function inside a subquery (error 3706).
- Lesson for the next feature here: changing the *shape* of a predicate is not a local change.
  `bind` pattern-matches the SQL it is given, so anything that stops producing `alias.column`
  silently changes what an empty slicer means. Grep `bind` before changing predicate text.

## The page tabs can sit top, bottom or left, 2026-09-30

- [x] A **`Tabs`** control beside `View` (both templates): Top (default), Bottom, Left. The body
  is a flex column and `data-tabs` on `<body>` re-orders the strip rather than moving it in the
  DOM, so the tab buttons, their `aria-selected` state and the page-switching JS are untouched.
  `Left` turns the strip into an 11rem rail: the body becomes `row wrap`, the header keeps the
  full width, and the active page is marked with a left accent bar instead of an underline.
- Params and pages are wrapped in a `<main class="content">` so the rail has something to sit
  beside. That also makes `applyView()` measure the *content* width rather than the window's, so
  the canvas correctly uses what the rail leaves — no change needed for the other two positions.
- A left rail costs **width, not height**, so `chromeHeight()` stops counting the strip in that
  position; otherwise Fit page shrank the canvas for vertical space nothing was using.
- **Not** `data-nav`: that attribute already means "this button navigates to page X" on a visual,
  and reusing it broke a test that counts navigation buttons. Named `data-tabs` instead.
- Only offered when the report has more than one page. Per viewer (`localStorage`), like the zoom.
- Verified in Chromium for both templates: strip above / below / beside, the canvas still fits in
  all three, tabs keep switching pages from the rail, and the choice survives a reload.

## Canvas size is now the viewer's choice, 2026-09-30

A user reported the report "covers too much" on some monitors and "looks truncated" on others.
The cause is structural: a Power BI canvas has fixed proportions, and `.page { width: 100%;
aspect-ratio: var(--ratio) }` can only ever fit the **width**. On a wide-but-short screen the
bottom ran off; on a narrow one everything was squeezed. Nothing fitted the height.

- [x] **A `View` control in the header** (both `report.html.j2` and `report_hah.html.j2`): Fit
  page (the new default), Fit width, and 50/75/100/125/150 %. `applyView()` sets each page's width
  in px — `aspect-ratio` then gives the height — and re-runs `fitAll()` and the ECharts resize.
  Fit page sizes against `window.innerHeight` minus the header/tabs/parameter bar, so the whole
  canvas is visible without scrolling whatever the window shape.
- The choice is **per viewer, not per report**: it lives in `localStorage`, never in the HTML, so
  one person's zoom doesn't travel to everyone the file is sent to. Printing ignores it entirely
  (`width: 100% !important`) — paper has its own width.
- The old CSS (`width: 100%`, `max-width: 1400px`) stays as the no-JS fallback; `applyView()`
  clears `max-width` once it manages the size.
- Verified in Chromium at 1920×800, 1440×900, 1100×1400 and 900×600: Fit page never overflows in
  any of them, a portrait page re-fits when you switch tabs, and the choice survives a reload.
  Fit width and an explicit zoom are still allowed to overflow — that is what the viewer asked for.

## Date slicers listed raw dates instead of Year > Month, 2026-09-30

Reported against `TestReport3`: the real report's date slicer groups months under years with
month names (January, February…); the HTML listed raw dates (`2026-08-31`).

- [x] **Every level of a date hierarchy resolved to the same ref, and the list was de-duplicated.**
  Power BI's levels (`Calendar.Date.Variation.Date Hierarchy.Year` / `…Month`) all sit on one
  underlying column, so `_entity_prop` returned `("Calendar", "Date")` for each and
  `canonical_query_ref` rewrote them all to the identical `Calendar.Date`. The slicer's field
  list is a de-duplicated set, so a Year+Month slicer collapsed to **one** field over the raw
  date and its options query became `SELECT DISTINCT calendar.date`. The level is the only thing
  that distinguishes the entries, so it is now kept beside the ref (`_hierarchy_level`,
  `_proto_levels`, and `level` on each PBIR field) and surfaces as `slicer.levels`.
- [x] **One parameter per level, filtering on the level.** `date_year` (number) and `date_month`
  (text) over the same column; the predicate is `EXTRACT(YEAR FROM d) IN (:date_year)` and
  `TRIM(TO_CHAR(d, 'Month')) IN (:date_month)`, because picking "January" means *every* January,
  not one date. The options query groups by the level expressions and orders by `MIN(date)` — a
  month name sorts alphabetically, so the order has to come from the real date behind it.
- The widget needed no change: `slicer.js` already drew a hierarchy tree keyed on the number of
  parameters. Verified in Chromium — years with their months nested and checkboxes on both.
- Two honest caveats: `TO_CHAR(d, 'Month')` depends on the Teradata **session language** (the
  calendar translator already carries this caveat), and the level predicates are not sargable,
  so a large fact table will scan rather than seek. Both are worth revisiting if a real run
  shows wrong month names or slow slicer filtering.
- Gated entirely on `levels` being present, which nothing produced before, so every existing
  slicer behaves exactly as it did.

## The 100 % stacked column: two more root causes, 2026-09-30

Reported three times against `TestReport3`'s four header charts ("Starting Headcount", "New
Hires"…): the HTML draws **one bar per field, each full height**, instead of one column split
into shares. Two earlier attempts fixed real but *different* bugs
(`chart_dimensions`, the series-only case) and did not move this chart, because neither was its
cause. Both causes below were found only after "a bar for each **field**" pinned the shape down —
several *measures*, not one measure split by a legend.

- [x] **`columnChart` and `barChart` are Power BI's *stacked* charts.** The clustered ones carry
  the `clustered` prefix (`clusteredColumnChart`); the unprefixed name is the stacked variant.
  `render.py` decided with `"stacked" in v["type"].lower()`, which is exactly backwards for the
  two most common stacked charts in existence — they rendered clustered, one bar per measure.
  Now an explicit `_STACKED_TYPES` set. On a 100 % chart this alone produces the reported
  picture: with nothing stacked, each bar is rescaled by its own total and every one hits 100 %.
- [x] **Several measures with an empty Axis well was refused outright.** A 100 % stacked column
  whose Values well holds one measure per category and whose Axis well is empty is *one* column
  with the measures stacked in it. `_draft_visual_sql` required `len(categories) == 1` for the
  several-measures `UNION ALL` arms, so the visual stayed manual and drew nothing at all. Each
  arm now shares one constant x-axis slot (`_CONST_CATEGORY`, the same device the series-only
  case uses). Kept out: `combo` (two axes and one x slot has no sensible drawing — its existing
  "stays manual" test still passes) and a lone measure with no axis, which is a card.
- **Still not confirmed against the reporter's file.** Fix 1 explains the symptom exactly if the
  chart is drafted; fix 2 explains it if the chart was blank/manual. Run `pbix2html mapping
  <pbix>` and check what it says for those four visuals — `ok` means fix 1 was it, anything else
  names the remaining reason. Two prior sessions "fixed" this from inference alone; the lesson
  is to read the visual's real `projections` before theorising.

## A card counting a text column showed the first value, 2026-09-30

Reported from a real report: a card that counts the text values of a column showed the *first*
value instead of the count. The cause was in `query_ref_parts`, one level below the card.

- [x] **An aggregation wrapper outside the translatable list was left glued to the table name.**
  The regex only stripped `Sum|Count|CountNonNull|Min|Max|Avg|Average|DistinctCount`, so
  `First(T.name)` split into the table `First(T` and the column `name)`. Both then *sanitised
  clean* — `_sql_alias("First(T")` is `first_t`, `_sql_col("name)")` is `name` — so nothing ever
  looked malformed: a table that does not exist reached the mapping report as
  `unknown_table:First(T` and, worse, the panel's table-map step (`gui.py`, step 2b) listed it as
  a Power BI table for someone to paste a query against. Mapped, it drafts `first_t.name` — a raw
  text column where a count belonged. Now *any* `Agg(...)` wrapper is stripped before the
  table/column split; `_REF_AGG_TO_SQL` stays the list of the ones that actually translate.
- [x] **`CountAll` now drafts** (`COUNT(*)` — Power BI's "Count (All)" includes blanks, so
  `COUNT(col)` would be wrong). `Count`/`CountNonNull`/`DistinctCount` on a text column already
  worked and are now covered by a test, since that is the exact shape reported.
- [x] **`First`/`Last` are refused with an honest reason** (`unsupported_aggregation:First(name)`)
  rather than being reported as a missing table or an untranslatable measure. They mean "the first
  value in the column's own order" and a SQL table has no inherent order — MIN/MAX would be a
  different number that merely looks plausible.
- **Not confirmed against the reporter's file.** The reasoning above explains the symptom exactly,
  but the `.pbix` isn't here; if that card was instead left manual and its SQL hand-written, the
  fix won't change it. `pbix2html mapping <pbix>` now names the real reason for such a visual —
  check what it says for that card.

## Wider DAX coverage: row iterators, SWITCH, conditions over a measure, 2026-09-30

The drafter's reach is bounded by `_DaxTranslator`, and across the real reports reviewed here the
measures left manual were mostly not exotic — they were everyday shapes the grammar simply didn't
have. Added (see skill `dax-to-teradata-sql` for the full boundary and the reasoning per pattern):

- [x] **`SUMX/AVERAGEX/MINX/MAXX/COUNTX(T, expr)` over a plain table** → `SUM(expr)`. Row context
  over a *physical* table is exactly SQL's, so this is a one-to-one mapping, not an interpretation.
  `FILTER`/`VALUES`/`SUMMARIZE`/`ALL` as the iterated table (a virtual table), and an aggregate or
  measure inside the row expression (context transition), still raise — that boundary is where a
  plausible-looking wrong number would come from. `RELATED(D[c])` is allowed inside, since DAX only
  permits it from the many side and so it can never fan out; a bare `Other[c]` raises instead.
- [x] **`SWITCH`**, both forms, → `CASE`.
- [x] **A measure or aggregate inside an `IF`/`SWITCH` condition** (`IF([Margin] > 0, [Margin], 0)`,
  `IF(ISBLANK([M]), 0, [M])`) — previously a condition took only columns and literals, so these
  common shapes took their whole visual down to a TODO. An enclosing `CALCULATE`'s filters are
  threaded into the condition's aggregates. Still refused in a `CALCULATE`/`FILTER` argument, where
  an aggregate is a table filter, not a value.
- [x] **`T[c] IN {…}`**, prefix `NOT`, `ISBLANK`, `BLANK()`, `IFERROR`, and the scalar functions
  with exact Teradata equivalents (`YEAR/MONTH/DAY`, `INT`, `CEILING/FLOOR` at significance 1 only,
  `MOD/POWER/SQRT/EXP/LN`, `UPPER/LOWER/TRIM/LEN`, `CONCATENATE`).

All additive: every measure that translated before translates identically (the 326 existing tests
pass unchanged). The refusals are tested as explicitly as the translations
(`test_translate_dax_refuses_the_iterators_that_are_not_a_plain_sum`), since the refusals are the
safety property. **Not yet measured against a real report** — the gain in drafted visuals should be
counted with `pbix2html mapping` on a real `.pbix` next; that is the number that matters.

## Design shapes (line/rectangle/oval) barely captured, 2026-09-30

A user flagged that static design shapes (lines, rectangles, other shapes used only for layout —
not the report's data) weren't reliably read: colour, border, transparency, shadow. Checked
against real shapes in `TestReport6`/`TestReport8` (30 real `shape`/`basicShape` visuals — 26 of
them lines, the dominant real-world use).

- [x] **Fixed**: a shape's own border/stroke (`objects.outline` — classic "shape"; `objects.line`
  — newer "basicShape") was never read at all — only the generic `vcObjects.border` every visual
  type has, which a design shape essentially never sets (it uses its own Line card instead). A
  rectangle with a coloured 2px outline lost that outline entirely. Fixed: `extract._shape_outline`
  → `style.border_color`/`style.border_weight` (pt → px in `render.py`, same `4/3` factor as
  `title_size`).
- [x] **Fixed**: a shape's own fill *transparency* was read only to decide "≥100 % → don't draw
  it", the partial percentage itself was dropped on the floor — a 50 %-transparent rectangle
  rendered fully opaque. Fixed: propagated into `style.transparency`, reusing the existing
  background→`rgba()` conversion.
- [x] **Fixed**: `roundEdge` (rounded rectangle corners) and rotation (`objects.rotation.
  shapeAngle` classic / `.angle` basicShape) were never read at all. Fixed: `style.round_edge` →
  `border-radius`; `style.rotation` → `transform: rotate()` — **except for a `line` shape kind**,
  deliberately: a long, thin box rotated around its own centre swings far outside that box, and a
  real report proved it — a 1280×23 header-line shape rotated 90° covered unrelated text far below
  it once rotation was naively applied. `style.rotation` is still extracted for a line (visible in
  `layout.json`), just not turned into CSS; drawing a rotated line correctly needs swapping which
  of width/height is its length, not a blind transform — not attempted.
- [x] **Fixed, the biggest real-world gap**: the newer "basicShape" visual's own line-vs-rectangle
  silhouette (`objects.general.shapeType`) was never checked at all — only the classic "shape"
  visual's `objects.shape.tileShape`. A `basicShape` "line" (26 of the 30 real shapes seen, by far
  the most common) rendered as a plain empty box, no stroke, no weight. Fixed: `_shape_geometry`
  checks both; the line's own weight (`objects.line.weight`) now reaches the renderer too (was
  hardcoded to 2px regardless of the report's own setting, e.g. 6pt in a real report).
- **Shadow: not read, unconfirmed.** No real report seen so far (across all three shared files)
  sets a shadow on a shape, so the property name/card isn't known — add it once a real sample uses
  one rather than guessing at Microsoft's schema for it.
- **Separate, pre-existing finding, not a shape issue**: verifying `TestReport6` after this fix
  turned up an unrelated `text_covered` warning — a full-page background-panel *textbox*
  (`background: #E0E0E0`, `z: 0`) hiding another textbox's text underneath it. Not caused by
  anything here (it's two textboxes, no shape involved) and not investigated further this session;
  worth a look if the "INFO" page (or its z-ordering generally) is ever prioritized.

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
