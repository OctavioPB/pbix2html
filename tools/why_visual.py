"""Print what the tool actually sees for a visual: its Power BI type, its field wells, whether
it stacks, and the SQL it drafts (or the reason it won't).

    python tools/why_visual.py <report.pbix> [text to match in the title]

Written for a chart that renders wrong: the shape of the field wells is what decides which code
path draws it, and guessing that shape from a screenshot has been wrong more than once.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pbix2html import extract as ex, semantic
from pbix2html.render import _STACKED_TYPES


def main(pbix: str, needle: str = "") -> int:
    layout = ex.extract_layout(Path(pbix))
    model = ex.extract_model(Path(pbix))
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    calc_columns = semantic._table_calc_columns(model)
    rels = model.get("relationships") if isinstance(model.get("relationships"), list) else []
    table_map = semantic.detect_table_map_from_power_query(model) or {}
    tables = {t for t in (model.get("tables") or []) if isinstance(t, str)}

    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group"):
                continue
            title = v.get("title") or ""
            if needle and needle.lower() not in title.lower():
                continue
            kind = semantic.KIND_MAP.get(v["type"], "unsupported")
            if kind in semantic.NO_DATA_KINDS:
                continue
            print(f"\n=== {page.get('display_name')} / {title or '(untitled)'}")
            print(f"    type    : {v['type']}  -> kind {kind}")
            print(f"    stacked : {v['type'] in _STACKED_TYPES}"
                  f"   percent: {v['type'].lower().startswith('hundredpercent')}")
            for role, refs in (v.get("projections") or {}).items():
                for ref in refs or []:
                    agg, table, col = semantic.query_ref_parts(ref)
                    dax = measures.get((table, col))
                    print(f"    well    : {role:10} {ref}" + (f"   DAX: {dax}" if dax else ""))
            params = semantic._params_for_page(semantic._slicer_parameters(layout, model),
                                               page.get("display_name"))
            filters = semantic.effective_filters(layout, page, v)
            drafted = semantic._draft_visual_sql(v, kind, measures, table_map, rels, params,
                                                 filters, calc_columns=calc_columns)
            if drafted:
                print("    SQL     :\n        " + drafted[0].replace("\n", "\n        "))
            else:
                why = semantic.diagnose_visual(v, kind, measures, table_map, rels, tables,
                                               params, filters, calc_columns=calc_columns)
                print(f"    NOT DRAFTED: {why}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
