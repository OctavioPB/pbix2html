# pbix2html

Converts Power BI reports (`.pbix`) into HTML pages that open with a double-click in
any browser — no Power BI install, no license, no dependency on the Power BI service
being available.

> **Project status:** under construction, report by report. `PLAN.md` has the detail of
> which reports are already migrated and which are next. If you're looking for a specific
> report and it's not listed there, it hasn't been migrated yet.

> **Who can finish a report without engineering help?** Be clear on this before planning
> anyone's time: **migrating a report is not yet a self-service job.** The panel (section 3)
> handles the setup, the table mapping, the simple visuals, and the whole convert/publish
> side without anyone touching a terminal. But every visual whose number is anything more
> than a plain sum/average/count — a ratio, a year-over-year, a filtered total — needs
> **Teradata SQL written by hand**, and the final numeric check (`Validate`) needs Teradata
> credentials. Those two steps need someone technical, on every report that has them.
> Realistic split: a business user can drive the tool end to end and will finish the simple
> reports alone; anything else is a shared job. The panel now tells you which kind you're
> looking at before you start — the report list shows **how many visuals still need SQL by
> hand** for each report.

---

## 1. What is this, in plain terms?

Many areas use Power BI reports that connect "live" to Teradata (this is called
*DirectQuery*: the report doesn't store the data, it requests it every time you open it).
That model has licensing costs and limitations. This project takes each of those reports
and generates an **equivalent HTML report**: same numbers, same colors and style as the
original report, but as a plain web page.

Each report can be delivered in two ways:

- **📸 Snapshot mode.** An `.html` file with the data "frozen" at the moment it was
  generated (e.g. every morning). It opens without any connection, like a PDF: you get it
  by email, download it from a shared folder, open it, done. If your role should only see
  certain data (by region, by country, etc.), you get **your own file**, never one shared
  with other roles' data.
- **🔴 Live mode.** A page that queries current data every time you open it, like the
  original Power BI report. This requires signing in with your corporate user (same as
  today), and the security of what data you see is still enforced by Teradata, not the file.

In both cases: **the same HTML file decides its mode**, not you. The team building each
report chooses whichever fits the case.

## 2. If you're going to *use* a report (not build it)

1. **Request or receive it** through whatever channel the team tells you (email, shared
   folder, internal link). No installation needed.
2. **Open it with a double-click.** It opens in your usual browser (Chrome, Edge, Firefox).
   No internet needed if it's a "snapshot" report.
3. **Filters and selectors** (year, region, etc.) appear at the top of each report page,
   same as in Power BI, though not every cross-chart interaction is available yet (see
   "What it doesn't do yet" below).
4. **If something doesn't match the original Power BI report**, tell the team before using
   the number: every report goes through a visual-by-visual numeric validation before being
   published, but we appreciate the double-check.
5. **If you're a report owner** (the person who today validates and signs off on its Power
   BI version), the team will ask you to review the HTML version side by side with the
   original and confirm the numbers match before it gets replaced.

### What it doesn't do yet (on purpose)

- It doesn't reproduce interactions between charts (e.g. "clicking a bar filters the rest
  of the page") unless someone has explicitly requested it for that report.
- Highly customized visuals (non-standard Power BI ones) are reinterpreted with the
  closest standard chart, not copied pixel by pixel; this is noted in each report's
  documentation.
- It never mixes data from different roles/permissions in the same file.
- **Drill-through** (right-click a value to jump to a detail page for that value) has no navigation yet:
  those pages show all values, and the value saved in the file is not used.
- A few filters Power BI applies can't be reproduced automatically (filters on a total such as
  "sum less than 100", filters on a measure, relative dates). The team's mapping report lists them per report;
  those visuals are finished by hand before the numbers are compared.
- Pages the author hid in Power BI stay hidden unless a button leads to them.


### Two-line glossary

| Term | In plain terms |
|---|---|
| `.pbix` | The original Power BI file (Desktop). |
| DirectQuery | The original report doesn't store data, it fetches it live from Teradata. |
| Snapshot | A "photo" of the data at a given moment, embedded in the HTML. |
| Live | The HTML fetches data on the spot, requires signing in. |
| RLS (*Row-Level Security*) | The rules that determine which data rows each role can see. |
| DAX / SQL | Power BI's formula language (DAX) is rewritten by hand as Teradata SQL queries; no reliable automatic conversion exists. |

These come up from section 3 onward — you don't need them to *use* a report, only to
follow along while one is being built:

| Term | In plain terms |
|---|---|
| Metrics template (the "yaml") | One settings file per report holding each visual's query, its filters, and its roles. The panel edits it for you; you never have to open it. |
| Visual | One chart, table, or number tile on a report page. |
| `TODO` | A marker meaning "nobody has written this visual's query yet". The panel counts these for you. |
| Auto-drafted | A query the tool wrote by itself because the original was simple enough. It still needs a human to check it. |
| Table mapping | Saying, once per report, where each Power BI table's data actually lives in Teradata. The tool now fills most of this in by itself. |
| DBQL | A log inside Teradata that records the queries Power BI itself sent. Useful as a starting point and as the thing to check new queries against. Needs database access. |
| Proxy user / trusted session | How the live version tells Teradata *which person* is asking, so Teradata shows that person only their own rows. Set up once by the technical team. |
| Snapshot vs live vs HAH | Three ways to deliver the finished report: data frozen in the file, data fetched on open, or data fetched through the internal HTML App Host platform. |
| Validate | The numeric check that compares the new report's numbers against the original's, visual by visual. Needs Teradata configured. |
| Mapping report | A per-report checklist (`pbix2html mapping`): which visuals could be drafted, which need a person and why, which Power BI filters aren't reproduced, drill-through and hidden pages. Read it before reviewing a draft. |
| Filter-pane filters | The filters set in Power BI's Filters pane (for the report, a page or one visual). They change the numbers, so the tool adds them to each query. |
| PBIR | The newer internal format Power BI Desktop (2024+) saves `.pbix` files in. The tool reads both the old and new formats; you don't have to know which you have. |

---

## 3. Migrating a report from the panel (no terminal)

This is the complete walkthrough for doing a report from the browser. It's the same job
section 4 describes with typed commands — you don't need that section unless you're
installing the tool or want the command-line version.

**Before you start (one time, done by someone technical):** the tool is installed on the
machine and you're left an **`Open_Panel.bat`** file. Everything after that is yours.

### Step by step

1. **Open the panel.** Double-click `Open_Panel.bat`. A black window opens — leave it
   open, that's the program running — and your browser opens at `http://127.0.0.1:8765`.
   Closing that black window is how you shut everything down at the end.

2. **Add the report.** The first page lists the reports already there. Use **Upload
   .pbix** to add yours, then click **Open** next to it.
   *This list also shows, per report, how many visuals still need SQL written by hand —
   that's your early read on whether a report is a quick job or needs engineering time.*

3. **Click Extract.** Reads the `.pbix`: pages, visuals, measures. It also fills in as
   much of the table mapping (step 4) as it can on its own, and picks up any logos or
   background pictures so they show in the finished report. Nothing to configure.

4. **Check the table mapping** (*Map tables*). This says where each Power BI table's data
   actually lives in Teradata. Rows marked **detected automatically** were filled in by
   step 3 — skim them. Any row left blank needs a read-only `SELECT` query from someone
   who knows the database. You can come back to this later.

5. **Click Generate template**, then **Auto-detect queries & parameters.** The first
   creates the per-report settings file; the second works out each visual's query from
   the report's own measures and table relationships, and turns slicers into parameters.
   The result tells you exactly where you stand: *"N of M visuals have working SQL, K
   still need it written by hand."* That figure also stays at the top of the report page.

   **Auto-detect only fills what's still empty** — anything already written is left
   alone, so it's safe to run again later (after mapping more tables in step 4, say).
   *Regenerate template* is the different, destructive one: it rebuilds from the `.pbix`
   and replaces written SQL, keeping a backup you can restore.

6. **Check what it produced** (*Edit SQL, parameters & roles*). A form in the browser:
   one card per visual, its query in a text box, plus the report's filters and roles.
   - Visuals marked **Auto-drafted** already have a query — **that's a draft, not an
     answer.** Read it, and confirm it with *Validate* (step 9) before trusting a number.
   - Visuals still saying `TODO` need Teradata SQL written by hand. **This is the step
     that needs someone technical.** It's what auto-detect deliberately won't guess at:
     time intelligence (year-to-date, same-period-last-year), measures over virtual
     tables, and any table that isn't mapped yet.
   - Anything you type is checked to be a plain read-only query before it saves, and
     rejected with the reason if it isn't.

7. **Set the colors if they came out wrong** (*Custom theme*, optional). Many `.pbix`
   files don't actually store their colors, only the name of a built-in Power BI theme —
   nothing can be recovered automatically in that case. Set them here once and every
   later conversion uses them.

8. **Click Convert to HTML.** Pick snapshot or live, the role, and the report's filters.
   You get the finished `.html`. Open it next to the original and compare.

9. **Click Validate** to check the numbers visual by visual against the original.
   **This needs Teradata credentials configured**, so it may be a step you hand over.

10. **Made a mess? Use *Restore a previous version*** on the report page. Every time the
    settings file is overwritten — including by **Regenerate template** — the version
    before it is kept, and you can put any of them back. Restoring is itself undoable.

### Everything the panel can do

1. Someone technical installs the tool once (see section 4) and leaves you an
   **`Open_Panel.bat`** file on the desktop or in a shared folder.
2. Double-click `Open_Panel.bat`. A black window opens (leave it open, that's the
   program running) and the browser opens on its own at `http://127.0.0.1:8765`.
3. There you'll see the list of available reports (`.pbix`). You can upload a new one
   with the corresponding button, or open an existing one to:
   - **Extract** its structure (pages, visuals, measures). This also tries to auto-fill
     the table mapping below by reading each table's own Power Query source (the
     `.pbix`'s own M code, not something typed anywhere) — a straight table reference,
     or a query Power BI itself was already sending, both turn into a ready mapping with
     no manual step; anything more involved (a merge, a filter, a dynamically-built
     query) is left for the next step. Embedded pictures (logos, backgrounds) are also
     picked up automatically here and show up for real in the generated report instead
     of an empty frame — nothing to configure.
   - **Map Power BI tables to Teradata** (optional): give it a read-only Teradata query
     for each Power BI table name, once per report (single `SELECT`/`WITH` only — no
     `INSERT`/`UPDATE`/`DELETE`, no DDL, checked before it's saved). Rows already filled
     in by Extract are marked **detected automatically** — review them like anything
     else here, they're a mechanical pattern match, not a guarantee.
   - **Generate the metrics template** (`metrics/<Report>.yaml`) — one click creates it.
     If you did the table-mapping step, most visuals come back with a **working `sql`
     already drafted**, not a blank `TODO`. It translates the aggregates
     (SUM/AVERAGE/MIN/MAX/COUNT/COUNTROWS/DISTINCTCOUNT) *and* the things measures are
     usually built out of: ratios (`DIVIDE`), arithmetic between measures, measures that
     reference other measures, and `CALCULATE` with column filters. Joins come from the
     model's own relationships, slicers become parameters, and a chart with several
     measures gets one series per measure. Every auto-drafted visual is marked
     `Auto-drafted` in its notes — still review it and run **Validate** (step 4) before
     trusting it. What it deliberately won't guess at: time intelligence (`TOTALYTD`,
     `SAMEPERIODLASTYEAR`), `ALL`/`ALLSELECTED`, virtual tables and iterators
     (`SUMX` over an expression), and any table it can't reach — those stay `TODO`.
     The result message says how many visuals ended up in each state, and the same count
     stays at the top of the report page and in the report list.
   - **Restore a previous version**: every overwrite of `metrics/<Report>.yaml` — the
     **Regenerate template** button included — keeps a timestamped copy under
     `metrics/backups/` first, and this puts any of them back. Restoring saves the
     current version too, so it's undoable in both directions. Nobody needs git or a
     text editor to recover from a wrong click.
   - **Edit SQL, parameters & roles**: a form in the browser — a table for parameters, a
     table for roles, and one card per visual with its `sql` in a text box, pre-filled
     when auto-drafted, blank otherwise. Nobody needs to open or hand-edit the yaml file
     itself; every query is checked to be a single read-only `SELECT`/`WITH` before it's
     saved, and rejected with the exact reason if it isn't.
   - **Custom theme** (optional): many reports' extracted theme is just the *name* of a
     built-in Power BI theme, with no actual colors stored in the `.pbix` — nothing to
     recover automatically in that case. Set colors/font by hand here (or paste a theme
     JSON exported from Power BI Desktop), and every conversion from then on uses it.
   - **Convert to HTML**: pick the mode (snapshot/live/HAH), role, and the report's
     parameters (year, region, etc.) in a form, with plain-language names taken from the yaml.
     Picking **Live** shows whether the live service is reachable and a **Start live
     service** / **Stop live service** button right there — no separate terminal command.
   - **Validate** against Power BI (requires the technical team to have configured Teradata).
4. To close the panel, close the black window (this also stops the live service if you
   started it from here).

> The panel only runs on your machine (`127.0.0.1`, nobody else can open it from another
> computer) and doesn't replace *live* mode's security controls (`serve.py`): it's an
> operating tool for whoever builds the reports, with the same trust level as running the
> terminal by hand. Everything above — mapping, SQL, parameters, roles, starting/stopping
> the live service — is meant to be done from the panel; the only thing done outside it is
> the very first double-click on `Open_Panel.bat`.

---

## 4. If you're part of the technical team

The rest of this document assumes you're going to install, run, or extend the tool.
This project is meant to be worked with Claude Code: start with [CLAUDE.md](CLAUDE.md)
(working rules) and [PLAN.md](PLAN.md) (what phase we're in and what's next).

### Quick install

```bash
pip install -e ".[dev]"         # also installs fastapi/uvicorn (needed for the panel)
cp .env.example .env            # Teradata credentials (optional to get started)
pytest -q                       # tests, no Teradata needed
```

If you only need what the panel requires (without the test dependencies), `pip install
-e ".[live]"` is enough.

> **`'pbix2html' is not recognized...` (or `'uvicorn'`, PowerShell/cmd)?** The install
> was fine, but Python's `Scripts` folder isn't on that terminal's `PATH` (often fixed
> by just opening a **new** terminal window after installing). Until then, run
> `python -m pbix2html ...` / `python -m uvicorn ...` instead of the bare commands
> below — `python -m <anything installed>` works regardless of `PATH`.

### Commands

```bash
pbix2html extract reports/          # inventory of every .pbix → out/summary.md
pbix2html mapping reports/X.pbix    # what can/can't be drafted and why → out/X/mapping_report.md
pbix2html scaffold reports/X.pbix   # metrics/X.yaml, auto-drafting what it can (--overwrite re-drafts, keeping a backup)
pbix2html convert reports/X.pbix --mode snapshot --params year=2026 [--role Sales_North]
pbix2html convert reports/X.pbix --mode live
pbix2html convert reports/X.pbix --include-hidden  # also render pages hidden in Power BI
pbix2html convert reports/X.pbix --mode hah        # HTML App Host — see ADR-004, unverified against a real HAH
pbix2html validate X
pbix2html gui                        # local web panel (see section 3); double-click: Open_Panel.bat
python -m uvicorn pbix2html.serve:app   # live mode (needs SSO in front; see serve.py)
```

Without Teradata you can test the render with `--fake-data tests/fixtures/fake_block.json`.

> **`--mode live` report shows "Failed to fetch" on every visual?** `serve.py` isn't
> running (or isn't reachable at the `API_BASE` the report was generated with). The
> panel (section 3) has a **Start live service** button on the convert step that spawns
> it from the panel's own working directory, which is the easiest way to avoid this —
> prefer that over running `uvicorn` by hand. If you do start it manually, it must be
> `python -m uvicorn pbix2html.serve:app`, **from this project's root**. If it's already
> running and you still see this, check `serve.py`'s own terminal for the actual error;
> a generic "Failed to fetch" with the server up is almost always CORS, not the request
> itself — see the `CORS_ORIGINS` note in `.env.example`.
>
> **Visual shows `HTTP 404` instead (not "Failed to fetch")?** `serve.py` is reachable
> but running from the wrong folder — `metrics/`, `reports/`, `out/` are all relative
> paths, so it has to run from this project's root, the same folder they're in. Starting
> it from the panel's button avoids this entirely (it always uses the panel's own
> working directory); if it's still happening, the 404's own message names the exact
> path it looked for and where it's actually running from.
>
> **Visual shows `HTTP 500` with a message?** The service is running and found the yaml, and the
> failure happened when running the query; the message in the visual is the real error, and the full
> traceback is printed in the terminal where `serve.py` runs.
> `A hostname or IP address must be specified for the host connection parameter` (Teradata driver
> error 179) means `TERADATA_HOST` / `TERADATA_USER` / `TERADATA_PASSWORD` aren't set in `.env`
> (`serve.py` reads `.env` from the project, not from where you started it): live mode has no
> `--fake-data`, it always needs a real connection. Any other message comes from Teradata itself
> (wrong table/column in a drafted SQL, no access to the view, a SQL syntax error): copy the query
> from the yaml and run it in a SQL client.
>
> **Visual shows `HTTP 503`?** Teradata could not be reached at all (`Hostname lookup failed`, `Lost connection`): check the
> VPN / network; nothing is wrong with the query. A 500 is the database or the SQL.
>
> **Old drafted queries still fail after an update?** The tool repairs the known cases when it sends a query. To draft again
> what the tool wrote earlier (your own edits are never touched, a backup is kept): `pbix2html redraft <pbix>`, or tick
> *also redo queries the tool drafted earlier* in the panel.
>
> **A slicer shows `HTTP 404`?** Older versions answered 404 when the yaml had no `slicers:` entry for
> that slicer (a yaml written before slicers became widgets). It now answers "skipped" and the widget
> takes typed values; to get real dropdowns run the panel's auto-draft (it adds the missing entries) or
> `pbix2html scaffold <pbix> --overwrite`. If a *visual* also shows 404, that is the wrong-folder case above.
>
> **Visual shows `HTTP 401` instead?** This is expected, not a bug, when there's no
> reverse proxy in front of `serve.py` injecting the `AUTH_HEADER` (`X-Authenticated-User`
> by default) after SSO — which is exactly the case when previewing on your own machine.
> `serve.py` refuses to run any query without it on purpose (see rule 4 in `CLAUDE.md`):
> that header is what ties a query to a real person so Teradata's row-level security
> applies correctly, so it's never optional in a real deployment. For local preview only,
> set `REQUIRE_AUTH=false` in `.env` (see the note in `.env.example`) and restart
> `serve.py` — never do this anywhere the report is reachable by anyone but you.

### Structure

```
CLAUDE.md, PLAN.md              project governance (read first)
.claude/skills/*                reusable knowledge (pbix, DAX→SQL, Teradata, renderer, validation)
.claude/commands/*              /convert, /validate, /new-renderer
src/pbix2html/                  extract → semantic → query → render → validate; serve (live), gui (panel)
metrics/<Report>.yaml           semantic layer: SQL per visual (this is what's migrated by hand)
tests/                          synthetic .pbix + full pipeline with a fake backend
skills/pbix-to-html/          packaged Claude skill: same conversion, in a chat, no install
docs/ARCHITECTURE.md, decisions/ ADRs
```

### Step-by-step: migrate your first report

Follow this end to end for **one** report at a time (see `CLAUDE.md` rule 1 — never batch
all ~50 in a session).

> This is the command-line version, for the technical team. The same job done from the
> browser is [section 3](#3-migrating-a-report-from-the-panel-no-terminal), which is a
> complete walkthrough in its own right — point non-technical people there, not here.

1. **Put the file in place.** Copy the `.pbix` into `reports/` (e.g. `reports/Sales.pbix`).

2. **Extract its structure.**
   ```bash
   pbix2html extract reports/Sales.pbix --out out
   ```
   This writes `out/Sales/layout.json` and `out/Sales/model.json`, and updates
   `out/summary.md` with the visual/measure inventory. Skim `layout.json` against the
   report open in Power BI Desktop to sanity-check pages, visuals, and filters (skill
   `pbix-layout` has the field-by-field breakdown if something looks odd — including for
   PBIR-format `.pbix` files, Power BI Desktop 2024+'s default, which is common enough
   that `layout.json["format"]` tells you which parser actually ran).

3. **(Optional) Map Power BI tables to Teradata.** Power BI's DAX references logical
   entity names (`Compute Engine Mnthly`, `ORG_MAP`...) that don't exist as such in
   Teradata — there's no mapping inside the `.pbix` itself. Do this once per report
   *before* the next step (panel step 2b, or write `metrics/Sales.table_map.json` by
   hand as `{"Power BI entity": "<a read-only SELECT query>"}`) and the scaffold below
   pre-fills each visual's `sql` with a `FROM (<query>) AS ...` clause instead of a bare
   `TODO` — still needs columns and filters written by hand. Only a single
   `SELECT`/`WITH` statement is accepted (see `semantic.validate_read_only_sql`); this
   is a guardrail against a careless paste, not a security boundary — whoever enters a
   query here already has whatever access that query would use once it runs.

4. **Generate the metrics scaffold.**
   ```bash
   pbix2html scaffold reports/Sales.pbix
   ```
   This creates `metrics/Sales.yaml` (from `metrics/_template.yaml`) with one entry per
   visual, slicers turned into `parameters:`, and RLS roles pulled from the model if any
   were found. This file is the one you edit by hand from here on; re-running `scaffold`
   again requires `--overwrite` and **replaces any SQL you've already written**, so only do
   that on purpose. An overwrite copies the current file to `metrics/backups/<Report>.<stamp>.yaml`
   first (`semantic.list_backups` / `restore_backup`, or the panel's *Restore a previous
   version*), so it's recoverable — but don't lean on that instead of committing.

5. **Capture the reference SQL from DBQL.** Open the report in Power BI, interact with
   each visual, then pull the SQL the gateway actually sent to Teradata (skill
   `teradata-directquery` has the DBQL query). Paste it into each visual's
   `reference_sql` in the yaml — it's both your best starting point and the number
   `validate` will check against.

6. **Review/write the real `sql` per visual.** Step 4's scaffold already auto-drafts
   `sql` for most visuals: `translate_dax` in `semantic.py` handles the aggregates plus
   `DIVIDE`, arithmetic, measure-to-measure references and `CALCULATE` column filters,
   and `_draft_visual_sql` assembles them into each kind's column contract — joins
   included where `model.json`'s relationships make it unambiguous, one series per
   measure on a multi-measure chart. Those are marked `Auto-drafted` in `notes` and still need
   a human review, especially the JOIN (a wrong direction/multiplicity duplicates rows
   and inflates totals — see skill `validate-report`). Everything else is still rewritten
   by hand as Teradata SQL (DAX itself is never ported — see skill `dax-to-teradata-sql`
   for the translation patterns and the column contract each `kind` expects, documented
   in skill `html-renderer`). Fill in `params`, and for any role in `roles:` set its
   `proxy_user` (see skill `teradata-directquery` for trusted sessions). Do this from the
   panel's **Edit SQL, parameters & roles** page (section 3) — it round-trips
   `metrics/<Report>.yaml` for you and checks every `sql`/`reference_sql` is a single
   read-only query before saving; hand-editing the yaml directly works too (same file,
   `yaml.safe_dump` on save just reformats it), but there's no need to.

7. **Convert it to HTML.**
   ```bash
   pbix2html convert reports/Sales.pbix --mode snapshot --params year=2026 --role Sales_North
   ```
   Drop `--role` only if the report truly has no RLS — otherwise generate one HTML per
   role, never one file with everyone's data. Open the resulting
   `out/Sales.Sales_North.html` in a browser and eyeball it next to the original.
   No Teradata yet? Add `--fake-data tests/fixtures/fake_block.json` to render with
   placeholder data instead.

8. **Validate the numbers.**
   ```bash
   pbix2html validate Sales
   ```
   Reads `out/Sales.validation.md`: every visual should land on `OK`. For each `DIFF`,
   skill `validate-report` has a symptom → likely-cause table. Adjust `sql`, never
   `reference_sql`.

9. **Get the report owner's sign-off**, recording any accepted visual differences
   (reinterpreted custom visuals, simplified formatting) in that visual's `notes` in the yaml.

10. **Mark it done in `PLAN.md`** (the Phase 3 table) before moving on to the next report.

Need it live instead of/in addition to a snapshot? See `docs/decisions/ADR-001-snapshot-vs-live.md`
for the tradeoff, then Phase 2 in `PLAN.md` for standing up `serve.py`. There's also a third
delivery mode, `--mode hah`, for the teradata-report skill / HTML App Host platform — see
`docs/decisions/ADR-004-hah-delivery-mode.md` before relying on it, it hasn't been verified
against a real HAH environment yet.

Architecture detail and data contracts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
