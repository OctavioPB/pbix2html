"""Print what the tool actually sees for each visual: Power BI type, field wells, whether it
stacks, and whether it drafts (or the reason it won't).

    python tools/why_visual.py <report.pbix> [options]

      --page TEXT    only pages whose name contains TEXT
      --title TEXT   only visuals whose title contains TEXT ("" matches the untitled ones)
      --kind K       only this renderer kind (card, column, bar, table, slicer, ...)
      --charts       only chart kinds (bar/column/line/combo/pie) — the ones that stack
      --list         one line per visual (every page, every visual) — start here
      --yaml         compare metrics/<Report>.yaml against what the drafter produces NOW, and
                     say whether `redraft` would replace it (it only replaces its own drafts)
      --tables       every model table: does it have a Teradata source, and where from? For a
                     DAX calculated table with no source, prints the expression that was rejected
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
    ap.add_argument("--yaml", action="store_true", dest="check_yaml")
    ap.add_argument("--tables", action="store_true", dest="check_tables")
    ap.add_argument("--sql", action="store_true")
    a = ap.parse_args()

    layout = ex.extract_layout(Path(a.pbix))
    model = ex.extract_model(Path(a.pbix))
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    calc_columns = semantic._table_calc_columns(model)
    rels = model.get("relationships") if isinstance(model.get("relationships"), list) else []
    # Build the map the same way `sync_table_map` does, but without writing anything: a DAX
    # CALENDAR() table has no Power Query source and is rebuilt on sys_calendar.calendar, so
    # using only `detect_table_map_from_power_query` here reported a phantom `no_source:Calendar`
    # that the real pipeline would never produce.
    calendars = semantic.detect_calendar_tables(model)
    detected: dict[str, str] = {t: info["sql"] for t, info in calendars.items()}
    detected.update(semantic.detect_table_map_from_power_query(model) or {})
    mapped_by_hand, _problem = semantic.read_table_map(layout["report"])
    table_map = {**detected, **mapped_by_hand}      # a hand-mapped entry wins, as in the pipeline
    tables = {t for t in (model.get("tables") or []) if isinstance(t, str)}
    params_all = semantic._slicer_parameters(layout, model)

    if a.check_tables:
        pq = semantic.detect_table_map_from_power_query(model) or {}
        calc = {t.get("TableName"): (t.get("Expression") or "")
                for t in (model.get("calculated_tables") or []) if isinstance(t, dict)}
        used = {tb for page in layout["pages"] for v in page["visuals"]
                for refs in (v.get("projections") or {}).values() for ref in refs or []
                for tb in [semantic.query_ref_parts(ref)[1]]}
        print(f"{'table':34} {'source':22} used by a visual?")
        for t in sorted({*(model.get("tables") or []), *table_map, *calc, *used}):
            if not isinstance(t, str):
                continue
            where = ("hand-mapped" if t in mapped_by_hand else "Power Query" if t in pq
                     else "rebuilt CALENDAR()" if t in calendars
                     else "NONE - calculated table" if t in calc else "NONE")
            print(f"{t[:34]:34} {where:22} {'yes' if t in used else '-'}")
        missing = [t for t in calc if t not in table_map]
        if missing:
            print("\nCalculated tables with no source. `detect_calendar_tables` only rebuilds"
                  "\n`CALENDAR(a, b)` where each bound is TODAY()/NOW(), a quoted date, or"
                  "\nDATE(y, m, d) — anything else (e.g. MIN/MAX of a column) is refused:")
            for t in missing:
                expr = " ".join((calc[t] or "").split())
                print(f"\n  {t}:\n    {expr[:300]}{' …' if len(expr) > 300 else ''}")
        return 0

    if a.check_yaml:
        # `convert` reuses the SQL saved in the yaml; it never re-derives it. So a fix in the
        # drafter cannot reach a report whose yaml predates it, and `redraft` only replaces the
        # drafts it wrote itself (notes containing "Auto-drafted") — hand-written SQL is left
        # alone on purpose. This says, per visual, which of those two situations you are in.
        name = layout["report"]
        path = semantic.yaml_path(name)
        if not path.exists():
            print(f"no {path} — nothing to compare; `convert` would scaffold it fresh")
            return 0
        spec = semantic.load(name)
        print(f"{path}\n")
        print(f"{'title':26} {'in yaml':12} {'vs current draft':18} redraft replaces it?")
        for page in layout["pages"]:
            pname = page.get("display_name") or ""
            if a.page and a.page.lower() not in pname.lower():
                continue
            for v in page["visuals"]:
                if v.get("is_group"):
                    continue
                kind = semantic.KIND_MAP.get(v["type"], "unsupported")
                if kind in semantic.NO_DATA_KINDS:
                    continue
                entry = (spec.raw.get("visuals") or {}).get(v["id"]) or {}
                saved = entry.get("sql")
                auto = "Auto-drafted" in (entry.get("notes") or "")
                drafted = semantic._draft_visual_sql(
                    v, entry.get("kind") or kind, measures, table_map, rels,
                    semantic._params_for_page(params_all, entry.get("page") or pname),
                    semantic.effective_filters(layout, page, v), calc_columns=calc_columns)
                if not saved:
                    state, cmp_ = "missing", "-"
                elif semantic.is_unwritten_sql(saved):
                    state, cmp_ = "TODO stub", "-"
                else:
                    state = "auto-drafted" if auto else "hand-written"
                    cmp_ = ("same" if drafted and drafted[0].strip() == saved.strip()
                            else "STALE" if drafted else "drafter can't")
                stub = not saved or semantic.is_unwritten_sql(saved)
                if drafted and (stub or auto):
                    will = "YES"
                elif not drafted:
                    will = "no - the drafter still can't do this one"
                else:
                    will = "NO - hand-written, left alone on purpose"
                print(f"{(v.get('title') or '(untitled)')[:26]:26} {state:12} {cmp_:18} {will}")
        return 0

    if a.list_all:
        # Deliberately unfilterable: every page, every visual, one line each. A chart drawn under
        # a titled card carries no title of its own, so filtering by title hides exactly the
        # visual you are usually hunting for.
        print(f"{'page':28} {'title':26} {'type':34} {'kind':10} stk pct  drafts")
        for page in layout["pages"]:
            pname = page.get("display_name") or ""
            if a.page and a.page.lower() not in pname.lower():
                continue
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
