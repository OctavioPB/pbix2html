# pbix2html — Power BI to HTML report migration

Converts Power BI reports (`.pbix`, DirectQuery to Teradata) into self-contained HTML
reports, one report at a time:

    pbix2html convert reports/Sales.pbix --html out/Sales.html --mode snapshot
    pbix2html convert reports/Sales.pbix --html out/Sales.html --mode live

Read `PLAN.md` to know what phase we're in and what's next. Read `docs/ARCHITECTURE.md`
before touching more than one module.

## Pipeline (each stage is a module in `src/pbix2html/`)

| Stage | Module | Input → Output | Status |
|---|---|---|---|
| 1. extract | `extract.py` | `.pbix` → `layout.json` + `model.json` | done, with tests; both classic and PBIR format (`layout.json["format"]`) |
| 2. semantic | `semantic.py` | `model.json` + DAX + DBQL capture → `metrics/<Report>.yaml` | scaffold; the SQL is written by a person (assisted) |
| 3. query | `query.py` | `metrics yaml` + parameters → data per visual (Teradata) | scaffold |
| 4. render | `render.py` | `layout.json` + data + theme → `Report.html` | minimally functional |
| 5. validate | `validate.py` | HTML vs. Power BI reference → diff report | scaffold |
| live | `serve.py` | FastAPI: runs the yaml on demand with a proxy user | scaffold |
| hah (ADR-004) | `render.py --mode hah` + `templates/report_hah.html.j2` | alternative to `serve.py`: HTML fetches Teradata data itself via the teradata-report skill / HAH | implemented, **unverified** against a real HAH |

`extract.py` is the only extractor. (An older standalone copy lived in
`reference/pbix_extract.py`; it was deleted once it had drifted far enough — no PBIR
support, no textbox or image extraction — to be misleading rather than useful. It's in
git history if you ever need it.)

## Working rules

1. **One report at a time.** Never "migrate all 50" in a single session. The flow per report is
   `/convert <file>` → review `metrics/<Report>.yaml` → `/validate <Report>` → mark it in `PLAN.md`.
2. **DAX isn't ported, it's rewritten.** Each measure is converted to Teradata SQL in
   `metrics/<Report>.yaml`. The SQL captured in DBQL (what Power BI already generated) is the
   validation reference and the best starting point; see skill `dax-to-teradata-sql`.
3. **Numeric fidelity before visual fidelity.** A visual isn't migrated until `validate`
   marks it `OK`. Numbers are compared with the tolerance defined in the yaml.
4. **Security.**
   - Never credentials in code or in the yaml. Only `.env` (see `.env.example`).
   - `snapshot` mode: data is embedded in the HTML. **One HTML per role** is generated
     (`--role`), never a single HTML with every role's data filtered in JS.
   - `live` mode: the service runs with `QUERY_BAND PROXYUSER` (trusted sessions);
     Teradata applies row-level security. See skill `teradata-directquery`.
   - Model RLS rules (`model.json → rls`) are documented in the report's yaml;
     their replication in Teradata is tracked in `PLAN.md`.
   - Slicers are widgets with page-scoped parameters (ADR-006); `snapshot` widgets are read-only, so
     an interactive report needs `live` or `hah`.
   - `metrics/<Report>.relationships.json` holds extra relationships (a proposed calendar → fact
     date key); it is proposed automatically but must be reviewed like the table map.
   - The panel's table-map step (`metrics/<Report>.table_map.json`) only accepts a
     single read-only `SELECT`/`WITH` query per Power BI table — enforced by
     `semantic.validate_read_only_sql` on save. It's a guardrail against a careless
     paste, not a security boundary.
5. **The HTML inherits the .pbix theme** (`layout.json → theme`). Don't introduce a
   custom palette; colors come from `dataColors`, background, and fonts from the theme.
6. **Data contracts per visual type** live in the `html-renderer` skill. SQL in the
   yaml must return exactly the columns the renderer expects for that `kind`.
7. Before adding a new renderer, check `out/summary.md`: renderers are implemented by
   inventory frequency, not by preference.

## Commands

    pip install -e ".[dev]"            # install
    pytest -q                          # tests (use a synthetic .pbix; no Teradata)
    pbix2html extract <pbix|folder>    # inventory only → out/
    pbix2html convert <pbix> [--mode snapshot|live|hah] [--role X] [--params k=v]
    pbix2html mapping <pbix>           # why each visual could / couldn't be drafted → out/<Report>/mapping_report.md
    pbix2html validate <Report>
    pbix2html gui                          # local web panel (extract/scaffold/convert/validate without a CLI)
    python -m uvicorn pbix2html.serve:app --reload   # live mode (python -m: see README PATH note)

With real Teradata, define `TERADATA_HOST/USER/PASSWORD` in `.env`. Tests never
require a connection; if something needs Teradata, mark it `@pytest.mark.teradata`.

## Conventions

- Python 3.11+, typed, `ruff` for formatting. Identifiers in English, docs and
  comments in English.
- Teradata SQL: use `QUALIFY`, `SAMPLE`, `TOP n`; lowercase aliases; parameters
  with `?` (DB-API style from `teradatasql`). Never interpolate strings into SQL.
- Every non-trivial architecture decision goes into `docs/decisions/ADR-nnn-*.md`.
- Commits reference the report when applicable: `feat(Sales): waterfall renderer`.

## What NOT to do

- Don't try to evaluate DAX in Python or look for a library that does; none exist.
- Don't read the .pbix `DataModel` looking for data: in DirectQuery there are no rows, only metadata.
- Don't embed data from multiple roles in the same HTML.
- Don't reproduce custom visuals pixel-for-pixel; they're reinterpreted with the
  closest standard renderer and it's documented in the yaml (`notes`).
- Don't modify `tests/fixtures/make_fake_pbix.py` (classic format) or
  `make_fake_pbir_pbix.py` (PBIR format) just to make a test pass; extend them if a new
  structure is discovered in a real .pbix.

## Skills available (`.claude/skills/`)

- `pbix-layout` — internal structure of a .pbix and how it's normalized into `layout.json`.
- `dax-to-teradata-sql` — DAX → Teradata SQL translation patterns and capture via DBQL.
- `teradata-directquery` — connection, proxy users, cache, DBQL.
- `html-renderer` — template, per-visual data contracts, mapping to ECharts.
- `validate-report` — how to validate a migrated report against Power BI.
