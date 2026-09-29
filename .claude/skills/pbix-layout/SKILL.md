---
name: pbix-layout
description: Internal structure of a .pbix file (Report/Layout, theme, DataModel in DirectQuery) and how it's normalized into layout.json. Use when extending extract.py, debugging a .pbix that fails to parse, or interpreting layout.json fields for the renderer.
---

# Structure of a .pbix

A `.pbix` is a ZIP. Relevant members:

| Member | Content | Use |
|---|---|---|
| `Report/Layout` | JSON in **UTF-16LE** (with BOM `FF FE`) | pages, visuals, filters, referenced theme |
| `Report/StaticResources/RegisteredResources/*.json` | custom theme (Power BI theme JSON) | colors, fonts |
| `DataModel` | Xpress9-compressed tabular model backup | in DirectQuery: **metadata only** (TMSCHEMA), no rows |
| `DataMashup` | Power Query (M), deflate | sources / native queries |
| `Connections` | JSON with live connections (when there's no DataModel) | "thin" reports |
| `Metadata`, `Version`, `Settings`, `SecurityBindings` | various | usually irrelevant |

If `Report/Layout` doesn't exist but there's `Report/definition/report.json`, it's
**PBIR** format (Enhanced Report Format, Power BI Desktop 2024+ default): supported since
`extract.py`'s `_extract_layout_pbir` (real .pbix files created in 2024+ are commonly
PBIR, not classic — expect it more often than not on fresh extracts).

```
Report/definition/report.json                                  ← presence = PBIR
Report/definition/pages/pages.json                              → {"pageOrder": [...]}
Report/definition/pages/<pageId>/page.json                      → displayName, width, height
Report/definition/pages/<pageId>/visuals/<visualId>/visual.json → one per visual
  ├─ position{x,y,z,width,height,tabOrder}
  └─ visual
     ├─ visualType
     ├─ query.queryState{role: {projections:[{queryRef, field:{Column|Measure|Aggregation}}]}}
     ├─ visible (false = hidden — best-effort guess, see below)
     ├─ objects{}, drillFilterOtherVisuals
     └─ (on the container) visualContainerObjects.title[0].properties.text.expr.Literal.Value
```

Theme lives at `Report/StaticResources/SharedResources/BaseThemes/<Name>.json` instead of
`RegisteredResources`. `layout.json["format"]` is `"pbir"` or `"classic"` so downstream
code (and you) can tell which parser produced it.

**Mapped from Microsoft's PBIR schemas, still unconfirmed against a real file**
(`_parse_visual_pbir`/`_parse_page_pbir`/`_pbir_custom_packages`): visual `isHidden`,
`parentGroupName`, `filterConfig.filters` (filter target under `field`, handled by
`parse_filters`), page `visibility == "HiddenInViewMode"`, and custom visuals from
`report.json` (`publicCustomVisuals`, `resourcePackages` of type `CustomVisual`). If a real
file disagrees, capture the structure — see "When you find a new structure" below;
`tests/fixtures/make_fake_pbir_pbix.py` is the fixture to extend.

## Layout nesting

Many fields are **JSON serialized inside strings**: `config`, `filters`, `query`,
`dataTransforms` at the visual and page level, and `config` at the root level. Always run
them through `loads_maybe`.

```
Layout
├─ config (str→json): version, themeCollection{baseTheme, customTheme}
├─ resourcePackages[]: {resourcePackage:{name, type}}  # type 0 = custom visual
└─ sections[] (pages)
   ├─ name, displayName, ordinal, width, height
   ├─ config (str→json): {visibility:1} = hidden
   ├─ filters (str→json): [page filter]
   └─ visualContainers[]
      ├─ x, y, z, width, height, tabOrder
      ├─ filters (str→json)
      └─ config (str→json)
         ├─ name (visual id)
         ├─ layouts[0].position (redundant with x/y/...)
         ├─ parentGroupName (if it belongs to a group)
         ├─ singleVisualGroup{displayName}   ← it's a group, not a visual
         └─ singleVisual
            ├─ visualType         ("card", "clusteredBarChart", "tableEx", "slicer"...)
            ├─ projections{role:[{queryRef}]}   role = Category | Y | Values | Series | Rows | Columns | Legend | Tooltips...
            ├─ prototypeQuery     (semantic query; useful for sorting/aggregation)
            ├─ objects{}          (formatting: dataPoint, labels, categoryAxis, valueAxis, conditionalFormatting…)
            ├─ vcObjects.title[0].properties.text.expr.Literal.Value  → "'Title'"
            ├─ display{mode:"hidden"}
            └─ drillFilterOtherVisuals
```

`queryRef` has the shape `Table.Column` or `Sum(Table.Column)` / `Table.Measure`.
To know whether it's a measure or a column, cross-check with `model.json → measures`.

## Filters

Structure of a filter (page or visual):

```json
{"name":"f1","type":"Categorical|Advanced|TopN|RelativeDate",
 "expression":{"Column":{"Expression":{"SourceRef":{"Entity":"Calendar"}},"Property":"Year"}},
 "filter":{"Version":2,"From":[{"Name":"c","Entity":"Calendar"}],
           "Where":[{"Condition":{"In":{"Expressions":[...],"Values":[[{"Literal":{"Value":"2026L"}}]]}}}]},
 "isHiddenInViewMode":true,"isLockedInViewMode":false}
```

`extract.py` stores `target` (`Calendar.Year`), `type`, and the raw `definition`. Power
BI literals carry a type suffix: `2026L` (long), `'text'`, `datetime'2026-01-01T00:00:00'`,
`12.5D`. A slicer's filter appears in the slicer visual's `filters` **and** in every other
visual's `prototypeQuery`.

### Where filters live, and which ones constrain anything

Three levels, all normalized by `parse_filters` into `{target, type, definition, how_created,
aggregation, is_hidden, is_locked}`: report (`layout["filters"]`: classic `Layout.filters`, PBIR
`report.json → filterConfig`), page (`page["filters"]`) and visual (`visual["filters"]`).

- A filter **without** `filter`/`definition` is just a field listed in the pane (no effect).
- `type`: `Categorical` (In / Not In), `Advanced` (Comparison, And/Or, Contains/StartsWith/EndsWith, and
  `Not` of any of them), `TopN` (an `In` whose table is a `Subquery` with `Top`, `OrderBy`),
  `RelativeDate`. A negated condition is `{"Not": {"Expression": <cond>}}` in `Where`.
- `aggregation` is set when the pane field is an aggregate (`Aggregation.Function`: 0 Sum, 1 Avg,
  2 Distinct count, 3 Min, 4 Max, 5 Count non-null, 6 Median, 7 StdDev, 8 Variance; Microsoft's semanticQuery
  schema): the condition is on the aggregate, not the rows. `target` is still `Table.column`.
- `how_created`: 0 Auto, 1 User, 2 Drill, 3 Include, 4 Exclude, **5 Drillthrough** (filterConfiguration
  schema; PBIR stores the name, classic the position: `parse_filters` returns the number for both). A
  drill-through filter's saved value is only the last one the author tried.
- Other schema facts: `SortDirection` 1 ascending / 2 descending; `ComparisonKind` 0 =, 1 >, 2 >=, 3 <, 4 <=;
  conditions And/Or/Not/Comparison/Between/In/Contains/StartsWith/Exists; PBIR filter `type` also has `Range`,
  `Passthrough`, `Include`, `Exclude`, `Tuple`, `RelativeTime` and `VisualTopN`.
- A filter on a **measure** has target `Table.Measure` and `expression.Measure`.

Applying them to SQL is `semantic.effective_filters` / `filter_sql` (see skill `dax-to-teradata-sql`,
ADR-008).

## Theme

`themeCollection.customTheme.name` points to the JSON under `StaticResources`. Useful
fields: `dataColors[]`, `background`, `foreground`, `tableAccent`,
`textClasses{title,label,callout}`, `visualStyles.*`. If there's no custom theme, apply the
`baseTheme` defaults (Power BI's default colors live in `render.py → DEFAULT_THEME`).

## DataModel in DirectQuery

`PBIXRay` parses the TMSCHEMA even without data. Endpoints used in `extract.py`:
`dax_measures`, `dax_columns`, `relationships`, `rls` (roles and DAX filters),
`tmschema_partitions` (`Mode`: 1 = DirectQuery), `tmschema_datasources`, `power_query`.
`get_table()` will fail or return empty: don't use it.

### Storage modes and internal partitions

`model.json → table_modes` maps each table to `Import`, `DirectQuery` or `Dual` (`partitions[].Mode`
0 / 1 / 2); `storage_modes` counts partitions by mode. Two traps: the partitions list also holds
engine-internal entries named `H$…`, `R$…`, `U$…` (column-hierarchy and relationship storage, all `Dual`),
and the auto date tables (`LocalDateTable_*`, `DateTableTemplate_*`); both are filtered out. A report can
be all Import (TestReport4), all DirectQuery (TestReport5) or composite (TestReport3). After migration every
table that came from Teradata is read live regardless of its original mode; an Import table built from inline
data or a DAX calculated table stays in the yaml/table map, and the `.pbix` copy of an Import table can be
stale relative to Teradata.

Relationships: pbixray hides those that touch a calculated table (`SystemFlags` 2, e.g. a DAX `CALENDAR`);
`extract._all_relationships` re-reads them. Hidden pages: `hidden` on the page; the renderer keeps those a
visible page's button navigates to (transitively) and the mapping report lists the rest (`--include-hidden`
renders all).

### If PBIXRay can't open the file (known gap, not yet implemented)

`PBIXRay` raises `NoEmbeddedModelError` on a report with no local model, and then
`model.json` is just `{"error": ...}` — so `detect_table_map_from_power_query()` has
nothing to work with and the table mapping falls back to manual entry.

There is a second, independent copy of the Power Query M in the file that would still
work in that case: the **`DataMashup`** part is a binary envelope wrapping a **zip**,
whose `Formulas/Section1.m` is the raw M for every query. (Confirmed by reading
pbi-tools' `Legacy/MashupSerializer.cs` + `MashupParts.cs`, which extract exactly that.)

Not implemented here on purpose: it needs parsing the binary framing around the zip, and
there's no real `.pbix` in this repo with a `DataMashup` but no `DataModel` to verify
against — writing that parser against a self-made fixture would only prove it matches
our own guess at the format. Pick this up when a real file of that shape shows up; it's
an hour's work with one, and it would make table-map auto-detection work on thin reports.

## When you find a new structure

1. Isolate the `config` fragment that fails.
2. Reproduce it in `tests/fixtures/make_fake_pbix.py` (add a visual/case).
3. Adjust `extract.py` and add the assertion in `tests/test_extract.py`.


## Theme colour references (`ThemeDataColor`)

`{"ThemeDataColor": {"ColorId": n, "Percent": p}}` indexes a palette that is **not** just
`dataColors`: `0` = theme `background`, `1` = theme `foreground`, `n >= 2` = `dataColors[n-2]`.
`Percent` tints linearly toward white when positive and black when negative (0.6 of #FF5F02 is
#FFBF9A, verified on a real report). `extract.literal_color` returns a `theme:<id>:<pct>`
marker while parsing (the theme is read later) and `resolve_theme_markers` turns it into hex
once `layout["theme"]` is known; references it can't resolve are dropped, never guessed.
Shapes and buttons colour themselves with `objects.fill` (default-state `fillColor`), not
with the container's `vcObjects.background`.

## Bookmarks and view switchers (classic)

`config.bookmarks[]` → `layout["bookmarks"]` (`parse_bookmarks`). The state that switches views
is `explorationState.sections[<page>].visualContainerGroups = {groupId: {isHidden}}`; group
`isHidden` on the page itself is the initial state; a bookmark's `options.targetVisualNames`
(group and visual ids) + `applyOnlyToTargetVisuals` limit what it changes. Buttons point at a
bookmark (`visualLink` type `Bookmark`) or a page (`PageNavigation`, empty target = itself).
Group children's x/y are relative to the group. See `docs/decisions/ADR-005`.

## Button formatting (`actionButton`)

`objects.text|fill|outline|shape|icon` are lists: one entry without a selector holding the
card's `show` flag, then one per state (`selector.id`: `default`, `hover`, `pressed`,
`disabled`, `selected`) with only the properties that state changes. `parse_button` returns
`{"states": {state: {text, fill, outline, round, icon}}, "hidden": [cards shown=false]}`;
the renderer overlays a state on `default` (`render._button_css`) and emits CSS variables that
the `.btn` rules in the templates use for hover / pressed / disabled. Verified on a real
report: `text.text`, `text.fontSize` (`11D` = points), `fill.fillColor` / `transparency`,
`icon.shapeType` (`blank`), all in `default`. Other property names (`fontColor`, `bold`,
`horizontalAlignment`, `lineColor`, `weight`, `roundEdge`, the hover/pressed states) follow
Power BI's documented names and are unverified. The report's theme `visualStyles` had no
`actionButton` entry, so an absent card means "transparent / inherit", not a Power BI default.

## PBIR specifics (verified on a real file)

- `visualContainerObjects` (title, background, border, `visualLink`...) is **inside** `visual`, not a
  sibling of it; `objects` (formatting of the visual itself) is beside it.
- `page.json`: `visibility: "HiddenInViewMode"` hides a page; background image/colour use the same
  `objects.background` / `outspace` shapes as classic. `report.json → themeCollection` names the
  custom theme (a `RegisteredResources` JSON) and the base one (`SharedResources/BaseThemes`).
- Children of a group (`parentGroupName`) are positioned relative to it; groups can nest.
- Sort: `query.sortDefinition.sort[{field, direction}]` (classic: `prototypeQuery.OrderBy`, Direction
  1/2); `layout.json` stores `sort: [{entity, property, direction}]` and the SQL drafter turns it
  into `ORDER BY <position>`.
- **`queryRef` can be stale** after a table rename (Power BI doesn't rewrite it); the real table is
  the field's `SourceRef.Entity`. `canonical_query_ref` rebuilds refs from it, keeping only the
  aggregation wrapper.
- Not seen yet in a real PBIR file: bookmarks (`Report/definition/bookmarks/`), `isHidden`.

## Slicers

`objects.data.mode` (`Dropdown`, `Basic` = list, `Between`, `Before`, `After`, `Relative`, `Tile`),
`objects.selection.singleSelect` / `selectAllCheckboxEnabled`, `objects.items` (font colour, size,
background) and `objects.general.filter.filter` — the saved selection: `Where[].Condition` with `In`
(`Values` are typed literals: `2026L`, `'text'`, `datetime'2026-04-01T00:00:00'`), `Between` or
`Comparison` (`ComparisonKind` 1/2 = from, 3/4 = to). Hierarchy levels are the projections, in
order. A sync group (`visual.syncGroup.groupName`, classic `singleVisual.syncGroup`) makes slicers on
different pages share one selection; otherwise each page's slicer is independent. Parsed by
`extract.parse_slicer`.
