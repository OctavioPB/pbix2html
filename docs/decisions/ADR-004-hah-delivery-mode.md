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

### HAH's branding stops at the top bar (2026-10-02)

`pbix2html-fixv1.md` §4 lists HAH design tokens (`--td-teal/-orange/-navy`), a gradient
top bar and `.kpi-card { border-top: 4px solid teal }`, and §3 a per-visual row-count
footer. Taken literally, that restyles the report: the same `.pbix` rendered as `hah`
came out with a teal stripe on every card and KPI, teal slicer tiles, every chart 1.2rem
shorter to leave room for the row count, and — because the template emitted `border`/
`background` only when the report set them — a 1px grey frame and an opaque fill around
every visual that snapshot mode draws without either. The branded bar was also 5px
taller, and "Fit page" sizes the canvas from the height the chrome leaves, so the whole
report rendered smaller.

The delivery mode is **how the data arrives, not what the report looks like**. So the
tokens now dress the top bar only; the canvas is the report's, identical to a snapshot
render. The teal accent is an inset shadow inside the bar's own padding, so the bar's box
matches snapshot's to the pixel. The row count stays (fixv1 §3) as a corner overlay next
to `.truncated`, reserving no space. `.error-state` keeps its fixv1 name, sharing the
rule with the `.error` the shared renderers emit.

Guarded by `test_hah_mode_does_not_alter_the_canvas_design` and
`test_hah_builds_each_visual_from_the_same_markup_as_snapshot_mode`: the two templates'
canvas CSS and `<div class="visual">` markup must stay identical, so the next change to
one of them cannot quietly diverge again. Only chrome selectors (`header`, `nav.tabs`,
`.params`, the spinner) are exempt.

### The endpoint is resolved at run time, not frozen at build time (2026-10-02)

First real report from a HAH environment: **"Failed to fetch"** on every visual. That message is a
`fetch()` `TypeError` — the request never completed — so it is not a 404 and not a SQL error (the
contract above answers those with HTTP 200). It means cross-origin, a blocked scheme, or a host
that is not there.

The cause is structural: `--hah-base` is a guess made when the HTML is generated, baked into
`sql_api` and `static_base`, while HAH serves the report from its own origin at
`{base}/api/reports/{id}/view`. Build for dev and upload to uat (`dev-html-app-host` vs
`html-app-host`), reach the platform through a different ingress host, or just open the file
locally, and every POST is cross-origin — answered without CORS headers, reported as those three
words, with nothing naming the URL it tried.

So the page now works it out for itself, in this order: `?sqlApi=<url>` in the address bar; else
the mount point in its own URL (`…/<app>/api/reports/42/view` → `…/<app>/api/execute`) whenever it
is served over http(s); else the build-time base, which is all a `file://` preview has. The same
fallback covers `static/echarts.min.js`, whose `<script src>` carried the identical assumption —
a wrong base killed the chart library too, with "echarts is not defined" on every chart. Failures
now name the URL they used, say whether the call was cross-origin, and report a non-200 with its
status and body instead of dying inside `resp.json()`; the resolved endpoint is logged once to the
console.

`tests/test_hah_endpoint.py` serves a report the way HAH does, from a wrong build-time base, and
asserts it posts to the origin that served it, falls back when there is no mount point to read,
honours `?sqlApi=`, loads ECharts from the serving origin, and explains a dead endpoint.

This also removed a duplicate `function chart()` in the hah template — two identical copies, the
second winning, so a fix applied to the first would have done nothing.

### HAH does not serve ECharts, so the report carries it (2026-10-02)

Second round from the real HAH: the SQL endpoint resolved, and every chart then failed with *"No
ECharts build loaded"*. Four URLs had been tried — the configured `/static/echarts.min.js` and
three plausible variants under the serving origin — so this settles the question this ADR opened
on 2026-09-28: **HAH's `/static/` has Chart.js, Plotly and Mermaid, not ECharts.** The fix log said
as much (§10: "Chart.js, Plotly, or Mermaid served from `{base}/static/`"); the implementation hoped
otherwise and the hope was never tested.

Rewriting ten renderers against Chart.js to match what HAH happens to host is the wrong trade, so
the library travels with the report instead: `--echarts download` fetches the configured build once
into `echarts_cache` (`~/.pbix2html/echarts.min.js`) and inlines it. **For `--mode hah` this is the
default** — the panel's users cannot pass flags, and a report whose charts silently do not draw is
worse than a 1 MB file. If the build cannot be had (no network, no cache) the report is still
written and falls back to HAH's copy at run time, with a warning. `--echarts hah-static` asks for
that path on purpose; `--echarts <file|url>` points somewhere specific.

Cost: a hah HTML grows by ~1 MB (a real report came out at 2.4 MB). If HAH rejects an upload that
size, the alternatives are `--echarts <url>` pointing at a library HAH does allow, or `hah-static`
once someone runs the teradata-report skill's `list_libraries()` and finds a real ECharts path.

`tests/test_hah_endpoint.py` renders a chart from the embedded build with **every** request outside
the HAH origin aborted, and asserts the page never asks HAH for a library it already carries.

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
