# ADR-009 — HTML verifier (`pbix2html verify`)

**Status:** accepted, implemented. **Date:** 2026-09-29.

## Context
Converted reports had problems a person sees at a glance but no test catches: overlapping visuals, a title the
same colour as its background, text hidden under another visual or a picture, clipped text, chart labels that do
not fit. They were found one at a time by eye. No Power BI screenshot exists offline, so "compare with the
original" cannot be automated.

## Decision
`verify.py` works from the generated HTML alone (it embeds its spec and data as JSON) in two layers:

1. **Static checks on the spec** (no browser): overlapping content visuals (a small control on a big chart is a
   note, bookmark views that stack by design are ignored), visuals off the page or tiny, kinds with no renderer,
   visuals with no data / a failed or empty query.
2. **Rendered checks in a real browser** (Playwright, optional: `pip install 'pbix2html[verify]'`, any Chromium /
   Chrome / Edge via `--browser` or `PBIX2HTML_BROWSER`): every page is opened and measured from the live DOM
   (`templates/verify_probe.js`): text covered by an opaque element, text over text of another visual, text
   clipped by an ancestor, failed / empty visuals, broken images, ECharts axis labels that cannot fit (from the
   chart's own option), console errors.
   **Contrast is measured on pixels**, not derived from CSS: the page is screenshotted with all text made
   transparent and the pixels under each text rectangle are compared with the text colour (WCAG ratio), so a
   picture, a gradient or a page background image is judged as it looks. One finding per visual and problem.

Output: `verify_report.md` / `.json` and, per page with findings, a screenshot with the findings boxed and
numbered. Exit code 1 on errors (`--strict`: on warnings too). It reports what is objectively wrong or
suspicious; whether the Power BI original also has it is for a person to confirm.

## Consequences
- Run on the five real reports it found and led to fixing real conversion bugs: a report without a custom theme
  lost every `ThemeDataColor` (white titles became dark on dark), textbox paragraph alignment was dropped (a
  centred title started under the logo), a shape with no fill object is filled with the theme's first colour in
  Power BI (a navy panel missing), a card's number colour, slicer text white on its own white control, a
  translucent button with no text colour.
- Heuristics (label width ≈ 0.56 × font size × characters, overlap thresholds) are approximate; expect some
  noise, tune with real reports.
- Not covered: fidelity to the original (needs reference screenshots), text drawn inside charts (canvas),
  interactions.
