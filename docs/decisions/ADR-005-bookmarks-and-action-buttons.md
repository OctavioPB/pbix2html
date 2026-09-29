# ADR-005 — Bookmarks and action buttons

**Status:** proposed (phases 1 and 2a implemented). **Date:** 2026-09-29.

## Context

A real classic-format report (11 pages, 232 visuals) uses Power BI's *view switcher*
pattern, which the renderer did not understand: 52 `actionButton` visuals, of which 40 are
`Bookmark` actions and 9 are `PageNavigation`, plus 7 bookmarks. The result was every
alternative view drawn on top of the others, and buttons that looked like grey text.

How the pattern is actually stored (verified against that file, classic format):

- **Views are groups.** Each alternative ("By Org", "By Lvl 1", ...) is a visual *group*
  (`singleVisualGroup`) holding the chart, its title and a caption. Only one is visible.
- **Initial state** is `singleVisualGroup.isHidden` on each group. A hidden group hides all
  descendants (nested groups too). The extractor used to ignore this.
- **A bookmark is a saved page state**, in `config.bookmarks[]`:
  `explorationState.sections[<pageId>].visualContainerGroups = {<groupId>: {isHidden}}`
  is what makes a bookmark a "switch". The `visualContainers` block of the same state was
  identical across all 7 bookmarks (no per-visual `display.mode`), so visibility is carried
  by the groups, not the individual visuals.
- `options`: `applyOnlyToTargetVisuals` + `targetVisualNames` (limit what the bookmark
  touches), `suppressData` (don't apply filter/slicer state), `suppressActiveSection` (don't
  navigate). The state also snapshots the page's filters (`sections[*].filters.byExpr`).
- **A button** carries `vcObjects.visualLink[0].properties`: `type` = `'Bookmark'` +
  `bookmark` = bookmark *name* (id), or `'PageNavigation'` + `navigationSection` = page id.
  `show=false` means the link is disabled.
- **Bookmarks are bound to a page.** All 7 bookmarks in the sample describe the hidden
  `HST Spend View` page, and the visible `Spend View` page's buttons reference those same
  bookmark ids; the group ids inside the bookmarks don't exist on the visible page. In Power
  BI this is probably a no-op (**still unconfirmed in Desktop**); the HTML must not invent
  behavior the original doesn't have.
- **Page pairs are a data-source switch, not duplicates** (owner's explanation): `Spend View`
  is fed by the monthly source, its `HST ...` twin by the historical one, and a
  "Current Month / Historic Data" pair of `PageNavigation` buttons swaps between them, so
  the user perceives a filter. The `HST` pages are *hidden* and reachable only through
  those buttons. (An earlier note in PLAN.md called them stale duplicates; that was wrong.)
  The `HST` twin's own "Historic Data" button has a dangling `navigationSection` (probably
  itself), and tooltip pages (`Tooltip-*`, `DEP-Tooltip*`) are hidden and linked by nobody.

- **"Active" buttons are a per-report design convention, not a Power BI feature** (owner):
  in this report a navigation button with a destination is drawn white and the one for the
  current page/view is orange and does nothing (`navigationSection = ''`, i.e. itself). Nothing
  in the file marks the "active" state; it is only the button's own fill. The renderer must
  therefore *not* compute an active state: it reproduces each button's stored fill and leaves
  a target-less button inert, which yields the same result here and stays correct for reports
  that use a different convention.

## Decision

Three phases, each independently useful and shippable.

### Phase 1 — initial state (done)
A group's `isHidden` is extracted (`hidden` on the group) and `render._hidden_with_descendants`
drops every visual with a hidden ancestor. No JS. Fixes the stacked-views rendering.

### Phase 2a — page navigation (done)
`layout.json` visuals get `action` (`_visual_link`). `build_spec` renders every hidden page
reachable (transitively) from a visible page's enabled `PageNavigation` button, flagged
`nav_only` (no tab, excluded from print); a button whose target isn't rendered stays inert.
Both templates (`report`, `report_hah`) share one `showPage()`; `hah` lazy-loads the page's
data when it is shown. Verified in a browser on the real report (both directions).

### Phase 2b — bookmarks and buttons as a client-side state machine
1. **Extract** into `layout.json`:
   - `bookmarks: [{id, name, page, active_section, groups: {groupId: hidden}, targets: [visualIds],
     apply_only_to_targets, suppress_data, suppress_active_section}]`.
   - on each `actionButton`: `action: {type: "bookmark"|"page"|"none", bookmark, page, enabled}`.
2. **Render**: `actionButton` becomes a real `<button>` (its caption/fill/border already come
   from the visual's formatting). Every visual element gets `data-groups="g1 g2"` (its ancestor
   chain). Clicking applies a bookmark: for the groups it lists (all of them, or only
   `targets` when `applyOnlyToTargetVisuals`), set `hidden`, then show a visual iff none of its
   ancestors is hidden. `PageNavigation` activates the matching tab. Buttons don't need data.
3. **Resolve conservatively.** A button whose bookmark is missing, or whose group ids don't
   exist on the button's own page, is rendered **inert** and listed under `notes` in the
   yaml ("button X → bookmark Y is bound to page Z; likely a no-op in Power BI"). Never a
   guess. An opt-in remap by group `displayName` may be added later if owners want it.
4. **Data**: keep initially-hidden visuals in the spec flagged `hidden` instead of dropping
   them. `snapshot` queries them like any other (each is visible in some state; one HTML
   per role still applies). `live`/`hah` fetch lazily the first time a visual is revealed.
5. A selected state is per-page and ephemeral (no URL/permalink in v1).

### Phase 3 — bookmark state beyond visibility (only if an owner needs it)
Filter/slicer state captured in `explorationState` (we already set `suppressData` aside),
spotlight, focus mode, and "bookmark as the initial page state". These change *data*, so
each is a per-report decision.

## Out of scope
Custom-visual bookmark behavior, `Back` buttons, `WebUrl` links (a plain `<a target=_blank>`
is trivial, add when seen), drill-through buttons.

## Consequences
- Phase 1 removed the overlap on the sample report and 2a made the monthly/historical switch work; 2b makes the view switchers work.
- Parsing is tied to the classic `Layout`; PBIR stores bookmarks in
  `Report/definition/bookmarks/*.bookmark.json` — **unverified**, so phase 2 ships for
  classic first and PBIR is added once a real PBIR sample exists.
- `layout.json` gains two keys; consumers must tolerate their absence (older extracts).

## Acceptance (against a real report)
- Every stacked view shows exactly its initial view on load.
- Clicking a working bookmark button shows only that view's groups and hides its siblings.
- A button that Power BI wouldn't act on is inert and appears in the yaml notes.
- No behavior is added that can't be traced to a stored property.

## Tests
Synthetic fixtures only (structure, not content): groups with `isHidden`, a bookmark with
`visualContainerGroups`, a page-navigation button, and the unresolvable-button case.

## Follow-up (data model)
Because each `HST` page is the same layout over a different source, the two pages duplicate
every visual in the yaml. Once the source difference is understood, a `period: current|historic`
parameter (or a per-page `source` override) could let one set of SQL serve both; deliberately
not done here — it changes how the yaml is written, so it needs its own ADR after the pilot.
