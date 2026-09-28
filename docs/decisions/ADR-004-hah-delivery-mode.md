# ADR-004 — `hah` as a third delivery mode (teradata-report skill / HTML App Host)

**Status:** proposed, implemented but **unverified**. **Date:** 2026-09-28.

## Context

ADR-001 chose between two delivery modes: `snapshot` (data embedded, no runtime) and
`live` (HTML calls our own `serve.py`, which runs SQL with a trusted-session `PROXYUSER`).

`pbix2html-fixv1.md` (external fix log, #10) proposes a third mode, `hah`, that targets
the **teradata-report skill** / **HTML App Host (HAH)** platform instead of `serve.py`:
the generated HTML calls HAH's own `POST {base}/api/execute` SQL endpoint directly,
authenticates through HAH's own session (not ours), and loads its JS libraries from
HAH's self-hosted static path instead of a CDN. Once generated, the HTML is meant to be
uploaded to HAH via that skill's `create_report` tool and no longer needs `serve.py`,
Teradata credentials in `.env`, or this project's own hosting at all.

## Decision

Add `--mode hah` / `mode="hah"` alongside `snapshot` and `live`, per the contract in
`pbix2html-fixv1.md` #10: `render_html(hah_base=...)`, a new
`templates/report_hah.html.j2`, and a matching option in the panel's convert form
(`gui_templates/report.html`), with the three HAH environment base URLs as `.env`
settings (`HAH_BASE_DEV/UAT/PRD` in `config.py`, not hardcoded — consistent with how
`API_BASE`/`ECHARTS_CDN` are already handled) rather than as Python defaults.

**This was implemented without access to a real HAH environment, the teradata-report
skill/MCP, or any file it has produced.** Nothing here has been confirmed by actually
uploading and opening a report on HAH. Specifically unverified:

- The exact `POST {base}/api/execute` request/response shape (`{sql}` in, `{data:
  [...], error, error_message}` out) — taken as given from the fix log.
- Whether HAH's `{base}/static/` actually hosts ECharts. The template reuses this
  project's existing ECharts-based rendering code (`report.html.j2`'s `R`/`fmt`/`series`/
  `chart` functions) pointed at `{hah_base}/static/echarts.min.js` instead of the CDN,
  rather than rewriting everything against Chart.js — the fix log says HAH serves
  "Chart.js, Plotly, or Mermaid" but doesn't rule out others. **If HAH doesn't actually
  host an ECharts build, `hah` mode produces a blank/broken report** until someone runs
  the skill's `list_libraries()` (not available in this session) and either confirms
  ECharts is there or this gets ported to whatever is.
- The `dev`/`uat`/`prd` base URLs themselves.
- Client-side SQL parameter binding (`bindSql`/`safeSql` in the template): mirrors
  `query.py`'s `bind()` but as string interpolation with escaping instead of driver-level
  `?` parameters, because HAH's SQL endpoint takes a single SQL string — this is a
  materially different trust model per visual query and is only as safe as `safeSql()`'s
  escaping.

## Consequences

- A `hah`-mode HTML must be manually verified against a real HAH environment (upload,
  open, check every visual and every slicer) before anyone treats it as usable, let alone
  hands it to a report owner. Don't mark a report `Live ✓`-equivalent in `PLAN.md` for
  `hah` output without that check.
- `hah` mode has no local caching or `.env`-based Teradata credentials of its own — HAH
  owns both auth and execution. `serve.py` and its trusted-session `PROXYUSER` model
  (ADR — teradata-directquery skill) are unaffected; `hah` is an alternative to `serve.py`,
  not a replacement.
- If HAH's static assets turn out not to include ECharts, expect to either special-case a
  second chart-rendering path in `report_hah.html.j2` or ask HAH to host ECharts too —
  don't discover this by shipping to a report owner first.
