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

If `Report/Layout` doesn't exist but there's `definition/pages/...`, it's **PBIR/PBIP**
format (project) — not yet supported: each visual is its own `visual.json` with
`position`, `visual.visualType`, `visual.query.queryState`. It needs a separate parser
(see PLAN.md, risks).

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
 "expression":{"Column":{"Expression":{"SourceRef":{"Entity":"Calendario"}},"Property":"Anio"}},
 "filter":{"Version":2,"From":[{"Name":"c","Entity":"Calendario"}],
           "Where":[{"Condition":{"In":{"Expressions":[...],"Values":[[{"Literal":{"Value":"2026L"}}]]}}}]},
 "isHiddenInViewMode":true,"isLockedInViewMode":false}
```

`extract.py` stores `target` (`Calendario.Anio`), `type`, and the raw `definition`. Power
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

## When you find a new structure

1. Isolate the `config` fragment that fails.
2. Reproduce it in `tests/fixtures/make_fake_pbix.py` (add a visual/case).
3. Adjust `extract.py` and add the assertion in `tests/test_extract.py`.
