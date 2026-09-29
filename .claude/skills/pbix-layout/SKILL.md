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
