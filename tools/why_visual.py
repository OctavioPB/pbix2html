"""Print what the tool actually sees for each visual: Power BI type, field wells, whether it
stacks, and whether it drafts (or the reason it won't).

    python tools/why_visual.py <report.pbix> [options]

      --page TEXT    only pages whose name contains TEXT
      --title TEXT   only visuals whose title contains TEXT ("" matches the untitled ones)
      --kind K       only this renderer kind (card, column, bar, table, slicer, ...)
      --charts       only chart kinds (bar/column/line/combo/pie) — the ones that stack
      --list         one line per visual (every page, every visual) — start here
      --sql          include the drafted SQL (off by default: a real one runs to 50k characters)

A visual's *title* belongs to whatever sits above it, so a chart under a titled card is usually
untitled — filter by kind, not by title, when looking for a chart.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pbix2html import extract as ex, semantic
from pbix2html.render import _STACKED_TYPES

CHART_KINDS = {"bar", "column", "line", "combo", "pie"}


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("pbix")
    ap.add_argument("--page", default="")
    ap.add_argument("--title", default=None)
    ap.add_argument("--kind", default="")
    ap.add_argument("--charts", action="store_true")
    ap.add_argument("--list", action="store_true", dest="list_all")
    ap.add_argument("--sql", action="store_true")
    a = ap.parse_args()

    layout = ex.extract_layout(Path(a.pbix))
    model = ex.extract_model(Path(a.pbix))
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    calc_columns = semantic._table_calc_columns(model)
    rels = model.get("relationships") if isinstance(model.get("relationships"), list) else []
    table_map = semantic.detect_table_map_from_power_query(model) or {}
    tables = {t for t in (model.get("tables") or []) if isinstance(t, str)}
    params_all = semantic._slicer_parameters(layout, model)

    if a.list_all:
        # Deliberately unfilterable: every page, every visual, one line each. A chart drawn under
        # a titled card carries no title of its own, so filtering by title hides exactly the
        # visual you are usually hunting for.
        print(f"{'page':28} {'title':26} {'type':34} {'kind':10} stk pct  drafts")
        for page in layout["pages"]:
            pname = page.get("display_name") or ""
            for v in page["visuals"]:
                if v.get("is_group"):
                    continue
                kind = semantic.KIND_MAP.get(v["type"], "unsupported")
                if kind in semantic.NO_DATA_KINDS:
                    state = "-"
                else:
                    drafted = semantic._draft_visual_sql(
                        v, kind, measures, table_map, rels,
                        semantic._params_for_page(params_all, pname),
                        semantic.effective_filters(layout, page, v), calc_columns=calc_columns)
                    state = "yes" if drafted else ",".join(semantic.diagnose_visual(
                        v, kind, measures, table_map, rels, tables,
                        semantic._params_for_page(params_all, pname),
                        semantic.effective_filters(layout, page, v), calc_columns=calc_columns))
                print(f"{pname[:28]:28} {(v.get('title') or '-')[:26]:26} {v['type'][:34]:34} "
                      f"{kind:10} {'Y' if v['type'] in _STACKED_TYPES else '.':3} "
                      f"{'Y' if v['type'].lower().startswith('hundredpercent') else '.':4} {state}")
        return 0

    shown = 0
    for page in layout["pages"]:
        pname = page.get("display_name") or ""
        if a.page and a.page.lower() not in pname.lower():
            continue
        for v in page["visuals"]:
            if v.get("is_group"):
                continue
            title = v.get("title") or ""
            kind = semantic.KIND_MAP.get(v["type"], "unsupported")
            if a.title is not None and a.title.lower() not in title.lower():
                continue
            if a.kind and kind != a.kind:
                continue
            if a.charts and kind not in CHART_KINDS:
                continue
            if kind in semantic.NO_DATA_KINDS and not (a.kind or a.title is not None):
                continue
            shown += 1
            print(f"\n=== {pname} / {title or '(untitled)'}   [{v['id']}]")
            print(f"    type={v['type']}  kind={kind}  "
                  f"stacked={v['type'] in _STACKED_TYPES}  "
                  f"percent={v['type'].lower().startswith('hundredpercent')}")
            wells = (v.get("projections") or {})
            if not wells:
                print("    wells: (none)")
            for role, refs in wells.items():
                for ref in refs or []:
                    print(f"    well {role:10} {ref}")
            if kind in semantic.NO_DATA_KINDS:
                continue
            drafted = semantic._draft_visual_sql(
                v, kind, measures, table_map, rels,
                semantic._params_for_page(params_all, pname),
                semantic.effective_filters(layout, page, v), calc_columns=calc_columns)
            if drafted:
                sql = drafted[0]
                print(f"    DRAFTED ({len(sql)} chars)"
                      + (f", params {drafted[1]}" if drafted[1] else ""))
                head = sql.split("\nFROM")[0]
                print("      " + (head if len(head) < 400 else head[:400] + " …"))
                if a.sql:
                    print("    --- full SQL ---\n        " + sql.replace("\n", "\n        "))
            else:
                why = semantic.diagnose_visual(
                    v, kind, measures, table_map, rels, tables,
                    semantic._params_for_page(params_all, pname),
                    semantic.effective_filters(layout, page, v), calc_columns=calc_columns)
                print(f"    NOT DRAFTED: {why}")
    if not shown:
        print("no visual matched those filters. What the file actually has:")
        for page in layout["pages"]:
            kinds: dict[str, int] = {}
            for v in page["visuals"]:
                if not v.get("is_group"):
                    k = semantic.KIND_MAP.get(v["type"], "unsupported")
                    kinds[k] = kinds.get(k, 0) + 1
            inv = ", ".join(f"{k}x{n}" for k, n in sorted(kinds.items()))
            print(f"  page {page.get('display_name')!r}: {inv or '(empty)'}")
        print("\nRun with --list for one line per visual.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
