# pbix2html

Converts Power BI reports (`.pbix`) into HTML pages that open with a double-click in
any browser — no Power BI install, no license, no dependency on the Power BI service
being available.

> **Project status:** under construction, report by report. `PLAN.md` has the detail of
> which reports are already migrated and which are next. If you're looking for a specific
> report and it's not listed there, it hasn't been migrated yet.

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

### Two-line glossary

| Term | In plain terms |
|---|---|
| `.pbix` | The original Power BI file (Desktop). |
| DirectQuery | The original report doesn't store data, it fetches it live from Teradata. |
| Snapshot | A "photo" of the data at a given moment, embedded in the HTML. |
| Live | The HTML fetches data on the spot, requires signing in. |
| RLS (*Row-Level Security*) | The rules that determine which data rows each role can see. |
| DAX / SQL | Power BI's formula language (DAX) is rewritten by hand as Teradata SQL queries; no reliable automatic conversion exists. |

---

## 3. Control panel (build/validate reports without using a terminal)

If you need to generate or review reports but don't want to use the command line, there's
a local web panel with buttons and forms for the same four steps the technical team uses:

1. Someone technical installs the tool once (see section 4) and leaves you an
   **`Open_Panel.bat`** file on the desktop or in a shared folder.
2. Double-click `Open_Panel.bat`. A black window opens (leave it open, that's the
   program running) and the browser opens on its own at `http://127.0.0.1:8765`.
3. There you'll see the list of available reports (`.pbix`). You can upload a new one
   with the corresponding button, or open an existing one to:
   - **Extract** its structure (pages, visuals, measures).
   - **Map Power BI tables to Teradata** (optional): give it a read-only Teradata query
     for each Power BI table name, once per report (single `SELECT`/`WITH` only — no
     `INSERT`/`UPDATE`/`DELETE`, no DDL, checked before it's saved).
   - **Generate the metrics template** (`metrics/<Report>.yaml`) — the SQL still has to
     be written by a technical person, but the template is generated with one click (and
     pre-filled with the table mapping above, if you did that step).
   - **Convert to HTML**: pick the mode (snapshot/live/HAH), role, and the report's
     parameters (year, region, etc.) in a form, with plain-language names taken from the yaml.
   - **Validate** against Power BI (requires the technical team to have configured Teradata).
4. To close the panel, close the black window.

> The panel only runs on your machine (`127.0.0.1`, nobody else can open it from another
> computer) and doesn't replace *live* mode's security controls (`serve.py`): it's an
> operating tool for whoever builds the reports, with the same trust level as running the
> terminal by hand.

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

> **`'pbix2html' is not recognized...` (PowerShell/cmd)?** The install was fine, but
> Python's `Scripts` folder isn't on that terminal's `PATH` (often fixed by just opening a
> **new** terminal window after installing). Until then, replace `pbix2html` with
> `python -m pbix2html` in any command below — it works regardless of `PATH`.

### Commands

```bash
pbix2html extract reports/          # inventory of every .pbix → out/summary.md
pbix2html scaffold reports/X.pbix   # metrics/X.yaml with sql: TODO per visual
pbix2html convert reports/X.pbix --mode snapshot --params year=2026 [--role Sales_North]
pbix2html convert reports/X.pbix --mode live
pbix2html convert reports/X.pbix --mode hah        # HTML App Host — see ADR-004, unverified against a real HAH
pbix2html validate X
pbix2html gui                        # local web panel (see section 3); double-click: Open_Panel.bat
uvicorn pbix2html.serve:app          # live mode (needs SSO in front; see serve.py)
```

Without Teradata you can test the render with `--fake-data tests/fixtures/fake_block.json`.

> **`--mode live` report shows "Failed to fetch" on every visual?** `serve.py` isn't
> running (or isn't reachable at the `API_BASE` the report was generated with) — start
> it with `uvicorn pbix2html.serve:app` and reload the report. If it's already running
> and you still see this, check `serve.py`'s own terminal for the actual error; a
> generic "Failed to fetch" with the server up is almost always CORS, not the request
> itself — see the `CORS_ORIGINS` note in `.env.example`.

### Structure

```
CLAUDE.md, PLAN.md              project governance (read first)
.claude/skills/*                reusable knowledge (pbix, DAX→SQL, Teradata, renderer, validation)
.claude/commands/*              /convert, /validate, /new-renderer
src/pbix2html/                  extract → semantic → query → render → validate; serve (live), gui (panel)
metrics/<Report>.yaml           semantic layer: SQL per visual (this is what's migrated by hand)
reference/pbix_extract.py       original extraction script (reference)
tests/                          synthetic .pbix + full pipeline with a fake backend
docs/ARCHITECTURE.md, decisions/ ADRs
```

### Step-by-step: migrate your first report

Follow this end to end for **one** report at a time (see `CLAUDE.md` rule 1 — never batch
all ~50 in a session). Everything below can be done with these CLI commands, or through
the [panel](#3-control-panel-buildvalidate-reports-without-using-a-terminal) (section 3)
if you'd rather click through it.

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
   again requires `--overwrite` and **wipes any SQL you've already written**, so only do
   that on purpose.

5. **Capture the reference SQL from DBQL.** Open the report in Power BI, interact with
   each visual, then pull the SQL the gateway actually sent to Teradata (skill
   `teradata-directquery` has the DBQL query). Paste it into each visual's
   `reference_sql` in the yaml — it's both your best starting point and the number
   `validate` will check against.

6. **Write the real `sql` per visual.** Rewrite each DAX measure as Teradata SQL (DAX
   itself is never ported — see skill `dax-to-teradata-sql` for the translation patterns
   and the column contract each `kind` expects, documented in skill `html-renderer`).
   Fill in `params`, and for any role in `roles:` set its `proxy_user` (see skill
   `teradata-directquery` for trusted sessions).

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
