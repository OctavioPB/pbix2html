# ADR-005 — Bookmarks and action buttons

**Status:** accepted; phases 1, 2a and 2b implemented, phase 3 open. **Date:** 2026-09-29.

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
- **Bookmarks are bound to a page, and this report reuses them on a clone.** All 7 bookmarks
  were saved on the hidden `HST Spend View` page, yet the visible `Spend View` page's buttons
  call the same bookmark ids. `Spend View` is a structural clone of `HST Spend View`: same
  group display names, positions and children, new ids (its section even lacks the `id` /
  `objectId` keys the original has). The file alone can't show what Power BI does with those
  buttons; the report owner states white buttons switch which visuals are shown, so the intent
  is that they act on the clone. (My first reading, "probably a no-op", was wrong.)
- **Two independent switchers on one page.** `By Org`/`By Lvl 1-3` target the four
  Org/Lvl groups (24 target ids: the groups *and* their children) and `By Model`/`By Product`
  the two Model/Product groups (8). With `applyOnlyToTargetVisuals` a bookmark changes only the
  groups it targets. A bookmark with no targets and no `applyOnly` (`Bookmark 7`, the
  unlabeled buttons) applies its whole saved state.
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

### Phase 2b — bookmarks and buttons as a client-side state machine (done)
- **Extract**: `layout.json["bookmarks"]` = `[{id, name, page, groups: {gid: hidden}, targets,
  apply_only_to_targets}]` (`parse_bookmarks`); a button's `action` already carries the id.
- **Resolve per page** (`render._bookmark_action`): use the group ids as they are when they
  exist on the page; otherwise match groups by display name, **only when the name is unique
  on both pages**; then keep just the targeted groups when `apply_only_to_targets`. Each use of
  the name-based remap is logged as a warning ("applied by group name") so the owner can check
  it. Nothing mappable, a missing/disabled bookmark → the button stays inert.
- **Render**: every visual carries its ancestor-group chain (`data-groups`); a page holds the
  set of hidden groups (initially those with `isHidden`); a bookmark button adds/removes groups
  and every visual is shown unless an ancestor group is hidden. Visuals inside initially-hidden
  groups are kept in the page (and queried in `snapshot`) rather than dropped. Same JS in the
  `report` and `report_hah` templates.
- Verified in a browser on the real report: each of the six view buttons shows exactly its
  group and hides its siblings, the two switchers are independent, no JS errors.

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
