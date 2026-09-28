#!/usr/bin/env python3
"""
pbix_extract.py — Power BI migration extractor (.pbix) → normalized JSON.

For each .pbix it generates:
  out/<report>/layout.json    pages, visuals, positions, fields, filters, theme
  out/<report>/model.json     tables, DAX measures, relationships, RLS roles, sources, mode (DirectQuery/Import)
And globally:
  out/inventory_visuals.csv   one row per visual (all reports)
  out/inventory_measures.csv  one row per measure (all reports)
  out/summary.md              summary to size the migration

Usage:
  python pbix_extract.py path/to/report.pbix
  python pbix_extract.py folder_with_pbix/ --out ./out
  python pbix_extract.py folder/ --no-model      # layout only (no PBIXRay)

Dependencies: python>=3.9, pandas; optional: pbixray (pip install pbixray)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------------

def decode_layout(raw: bytes) -> dict:
    """Report/Layout comes in UTF-16LE (with or without BOM)."""
    if raw.startswith(b"\xff\xfe"):
        raw = raw[2:]
    try:
        text = raw.decode("utf-16-le")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return json.loads(text)


def loads_maybe(value: Any) -> Any:
    """Many Layout fields are JSON serialized inside strings."""
    if isinstance(value, str):
        s = value.strip()
        if s and s[0] in "{[":
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                return value
    return value


def literal_to_text(expr: Any) -> str | None:
    """Extracts the text from an expr {"Literal": {"Value": "'Title'"}}."""
    if not isinstance(expr, dict):
        return None
    lit = expr.get("Literal", {}).get("Value")
    if isinstance(lit, str):
        return lit.strip("'\"")
    return None


def safe_name(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s).strip("_") or "reporte"


# ----------------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------------

# Prefixes/heuristics for custom visuals.
CUSTOM_VISUAL_PATTERN = re.compile(r"^(PBI_CV_|CV_|[A-Za-z0-9]+[0-9A-F]{8,})", re.I)

STANDARD_VISUALS = {
    "card", "multiRowCard", "kpi", "gauge", "slicer", "table", "tableEx", "pivotTable",
    "matrix", "barChart", "clusteredBarChart", "stackedBarChart", "hundredPercentStackedBarChart",
    "columnChart", "clusteredColumnChart", "stackedColumnChart", "hundredPercentStackedColumnChart",
    "lineChart", "areaChart", "stackedAreaChart", "lineClusteredColumnComboChart",
    "lineStackedColumnComboChart", "ribbonChart", "waterfallChart", "funnel",
    "scatterChart", "pieChart", "donutChart", "treemap", "map", "filledMap", "shapeMap",
    "azureMap", "textbox", "image", "shape", "actionButton", "basicShape", "decompositionTreeVisual",
    "keyDriversVisual", "qnaVisual", "scriptVisual", "pythonVisual", "cardVisual",
    "advancedSlicerVisual", "listSlicer", "textFilter", "esriVisual", "smartNarrative", "rdlVisual",
}


def parse_filters(raw: Any) -> list[dict]:
    """Normalizes page/visual filters: entity.property + type + raw definition."""
    filters = loads_maybe(raw)
    out = []
    if not isinstance(filters, list):
        return out
    for f in filters:
        if not isinstance(f, dict):
            continue
        try:
            target = None
            # "expression" can be absent or explicitly null (TopN filters,
            # multi-field advanced filters, etc.); don't assume it's always a dict.
            expr = f.get("expression") or {}
            for kind in ("Column", "Measure", "Aggregation", "HierarchyLevel"):
                node = expr.get(kind)
                if isinstance(node, dict):
                    inner = node.get("Expression") or {}
                    entity = (inner.get("SourceRef") or {}).get("Entity") \
                        or (((inner.get("Column") or {}).get("Expression") or {}).get("SourceRef") or {}).get("Entity")
                    prop = node.get("Property") or node.get("Level")
                    target = f"{entity}.{prop}" if entity or prop else kind
                    break
            out.append({
                "name": f.get("name"),
                "target": target,
                "type": f.get("type"),
                "is_hidden": bool(f.get("isHiddenInViewMode")),
                "is_locked": bool(f.get("isLockedInViewMode")),
                "definition": f.get("filter"),   # raw; contains Where/Condition
            })
        except Exception as e:
            # A filter with an unexpected shape must not take down the rest of the visual/page.
            out.append({"name": f.get("name"), "target": None, "type": f.get("type"),
                        "is_hidden": False, "is_locked": False, "definition": None,
                        "parse_error": f"{type(e).__name__}: {e}"})
    return out


def _empty_visual_stub(vc: dict, error: Exception | None = None) -> dict:
    """Full shape of a visual, used as a fallback when parsing fails.

    Keeps the same keys as a well-formed visual so write_inventory and the
    rest of the pipeline don't blow up with a KeyError over one odd visual.
    """
    vid = None
    try:
        cfg = loads_maybe(vc.get("config", "{}"))
        if isinstance(cfg, dict):
            vid = cfg.get("name")
    except Exception:
        pass
    stub = {
        "id": vid, "x": vc.get("x"), "y": vc.get("y"), "z": vc.get("z"),
        "width": vc.get("width"), "height": vc.get("height"), "tab_order": vc.get("tabOrder"),
        "parent_group": None, "type": "__parse_error__", "is_group": False, "is_custom": False,
        "title": None, "hidden": False, "projections": {}, "fields": [],
        "filters": [], "has_drill_other_visuals": False, "objects_keys": [],
    }
    if error is not None:
        stub["parse_error"] = f"{type(error).__name__}: {error}"
    return stub


def parse_visual(vc: dict) -> dict:
    """Must never propagate exceptions: a visual with an unexpected shape can't take
    down extraction of the whole report (see `main`: without per-visual capture, the
    whole report's inventory is lost out of a batch of ~50 .pbix files)."""
    try:
        return _parse_visual(vc)
    except Exception as e:
        return _empty_visual_stub(vc, e)


def _parse_visual(vc: dict) -> dict:
    cfg = loads_maybe(vc.get("config", "{}")) or {}
    layouts = cfg.get("layouts") or []
    pos = (layouts[0].get("position") if layouts else None) or {}

    sv = cfg.get("singleVisual")
    group = cfg.get("singleVisualGroup")

    visual: dict[str, Any] = {
        "id": cfg.get("name"),
        "x": vc.get("x", pos.get("x")),
        "y": vc.get("y", pos.get("y")),
        "z": vc.get("z", pos.get("z")),
        "width": vc.get("width", pos.get("width")),
        "height": vc.get("height", pos.get("height")),
        "tab_order": vc.get("tabOrder", pos.get("tabOrder")),
        "parent_group": cfg.get("parentGroupName"),
    }

    if group is not None:
        visual.update({"type": "__group__", "is_group": True, "is_custom": False,
                       "title": group.get("displayName"), "projections": {}, "fields": []})
        return visual

    sv = sv or {}
    vtype = sv.get("visualType", "unknown")
    projections = sv.get("projections") or {}
    # A projections role can come back as null (empty field well) instead of omitted.
    fields = sorted({p.get("queryRef") for role in projections.values() if isinstance(role, list)
                     for p in role if isinstance(p, dict) and p.get("queryRef")})

    # Title: at vcObjects.title[0].properties.text.expr
    title = None
    vco = sv.get("vcObjects") or {}
    for t in vco.get("title") or []:
        props = (t or {}).get("properties", {})
        title = literal_to_text((props.get("text") or {}).get("expr"))
        if title:
            break

    display = (sv.get("display") or {}).get("mode")

    visual.update({
        "type": vtype,
        "is_group": False,
        "is_custom": vtype not in STANDARD_VISUALS and bool(CUSTOM_VISUAL_PATTERN.match(vtype)),
        "title": title,
        "hidden": display == "hidden",
        "projections": {role: [p.get("queryRef") for p in (refs or []) if isinstance(p, dict)]
                        for role, refs in projections.items()},
        "fields": fields,
        "filters": parse_filters(vc.get("filters")),
        "has_drill_other_visuals": bool(sv.get("drillFilterOtherVisuals")),
        "objects_keys": sorted((sv.get("objects") or {}).keys()),   # applied formatting (dataPoint, labels...)
    })
    return visual


def parse_page(section: dict) -> dict:
    cfg = loads_maybe(section.get("config", "{}")) or {}
    return {
        "name": section.get("name"),
        "display_name": section.get("displayName"),
        "ordinal": section.get("ordinal"),
        "width": section.get("width"),
        "height": section.get("height"),
        "hidden": cfg.get("visibility") == 1,
        "filters": parse_filters(section.get("filters")),
        "visuals": [parse_visual(vc) for vc in section.get("visualContainers", [])],
    }


def extract_theme(z: zipfile.ZipFile, layout: dict) -> dict:
    cfg = loads_maybe(layout.get("config", "{}")) or {}
    themes = cfg.get("themeCollection") or {}
    result: dict[str, Any] = {
        "base": themes.get("baseTheme"),
        "custom": themes.get("customTheme"),
        "custom_json": None,
    }
    # The custom theme is stored as a registered resource.
    custom_name = (themes.get("customTheme") or {}).get("name")
    if not custom_name:
        # No custom theme configured: don't guess. Other JSON files under
        # StaticResources (custom visual resources like Deneb, icons, etc.)
        # are not the theme; render.py should fall back to baseTheme (see skill pbix-layout).
        return result
    for member in z.namelist():
        if "StaticResources" in member and member.lower().endswith(".json") and member.endswith(custom_name):
            try:
                result["custom_json"] = json.loads(z.read(member).decode("utf-8-sig"))
                result["custom_path"] = member
                break
            except Exception:
                pass
    return result


def extract_layout(pbix: Path) -> dict:
    with zipfile.ZipFile(pbix) as z:
        names = z.namelist()
        has_datamodel = any(n.endswith("DataModel") for n in names)
        layout_member = next((n for n in names if n.endswith("Report/Layout")), None)
        if layout_member is None:
            if "Report/definition/report.json" in names:
                return _extract_layout_pbir(z, names, pbix, has_datamodel)
            raise ValueError(
                "Unrecognized format: neither Report/Layout (classic) nor "
                "Report/definition/report.json (PBIR) found in the .pbix."
            )
        layout = decode_layout(z.read(layout_member))
        theme = extract_theme(z, layout)
        custom_packages = [
            (p.get("resourcePackage") or {}).get("name")
            for p in layout.get("resourcePackages", [])
            if (p.get("resourcePackage") or {}).get("type") == 0   # 0 = custom visual package
        ]
    return {
        "report": pbix.stem,
        "source": str(pbix),
        "layout_version": (loads_maybe(layout.get("config", "{}")) or {}).get("version"),
        "has_embedded_datamodel": has_datamodel,
        "theme": theme,
        "custom_visual_packages": [c for c in custom_packages if c],
        "pages": [parse_page(s) for s in layout.get("sections", [])],
        "format": "classic",
    }


# ----------------------------------------------------------------------------
# Layout — PBIR / Enhanced Report Format (Power BI Desktop 2024+)
#
# Structure discovered against real .pbix files that no longer ship a classic
# Report/Layout blob (see pbix2html-fixv1.md #6 and skill pbix-layout):
#   Report/definition/report.json                                  ← presence = PBIR
#   Report/definition/pages/pages.json                              → pageOrder
#   Report/definition/pages/<pageId>/page.json                      → one per page
#   Report/definition/pages/<pageId>/visuals/<visualId>/visual.json → one per visual
# A few field paths below (visual hidden/group state, page filters) are still
# best-effort guesses pending confirmation against a real file — see the TODOs.
# ----------------------------------------------------------------------------

def _pbir_read_json(z: zipfile.ZipFile, path: str) -> dict:
    """Missing entry or malformed JSON both just mean "nothing here": PBIR has one
    file per page/visual, so a single bad entry shouldn't be worse than a missing one."""
    try:
        with z.open(path) as f:
            return json.loads(f.read().decode("utf-8-sig"))
    except (KeyError, ValueError):
        return {}


def _pbir_fields(query_state: dict) -> list[dict]:
    """[{role, entity, property, queryRef}], one per field well entry."""
    fields = []
    for role_name, role_data in (query_state or {}).items():
        for proj in (role_data or {}).get("projections") or []:
            field = proj.get("field") or {}
            query_ref = proj.get("queryRef", "")
            col = (field.get("Column") or field.get("Measure")
                   or (field.get("Aggregation") or {}).get("Expression", {}).get("Column"))
            if col:
                entity = ((col.get("Expression") or {}).get("SourceRef") or {}).get("Entity", "")
                prop = col.get("Property", "")
            else:
                entity, prop = "", ""
            fields.append({"role": role_name, "entity": entity, "property": prop, "queryRef": query_ref})
    return fields


def _pbir_title(container_objects: dict) -> str | None:
    try:
        val = (container_objects["title"][0]["properties"]
               .get("text", {}).get("expr", {}).get("Literal", {}).get("Value", ""))
        return val.strip("'\"") or None
    except (KeyError, IndexError, TypeError):
        return None


def _parse_visual_pbir(vdata: dict, vid: str) -> dict:
    try:
        pos = vdata.get("position") or {}
        vis = vdata.get("visual") or {}
        qs = ((vis.get("query") or {}).get("queryState")) or {}
        vtype = vis.get("visualType", "unknown")
        fields = _pbir_fields(qs)
        projections: dict[str, list] = {}
        for f in fields:
            projections.setdefault(f["role"], []).append(f["queryRef"])
        return {
            "id": vid, "x": pos.get("x", 0), "y": pos.get("y", 0), "z": pos.get("z", 0),
            "width": pos.get("width", 0), "height": pos.get("height", 0),
            "tab_order": pos.get("tabOrder"), "parent_group": None,
            "type": vtype, "is_group": False,   # TODO: PBIR group representation not confirmed yet
            "is_custom": vtype not in STANDARD_VISUALS and bool(CUSTOM_VISUAL_PATTERN.match(vtype)),
            "title": _pbir_title(vdata.get("visualContainerObjects") or {}),
            "hidden": vis.get("visible") is False,   # TODO: confirm against a real hidden visual
            "projections": projections,
            "fields": sorted({f["queryRef"] for f in fields if f["queryRef"]}),
            "filters": [],   # TODO: PBIR page/visual filter shape not mapped yet
            "has_drill_other_visuals": bool(vis.get("drillFilterOtherVisuals")),
            "objects_keys": sorted((vis.get("objects") or {}).keys()),
        }
    except Exception as e:
        stub = _empty_visual_stub({}, e)
        stub["id"] = vid
        return stub


def _parse_page_pbir(z: zipfile.ZipFile, names: list[str], page_id: str) -> dict:
    page_data = _pbir_read_json(z, f"Report/definition/pages/{page_id}/page.json")
    visual_ids = sorted({
        n.split("/")[5] for n in names
        if n.startswith(f"Report/definition/pages/{page_id}/visuals/") and n.endswith("visual.json")
    })
    visuals = []
    for vid in visual_ids:
        vdata = _pbir_read_json(z, f"Report/definition/pages/{page_id}/visuals/{vid}/visual.json")
        if vdata:
            visuals.append(_parse_visual_pbir(vdata, vid))
    return {
        "name": page_id,
        "display_name": page_data.get("displayName", page_id),
        "ordinal": None,
        "width": page_data.get("width", 1280),
        "height": page_data.get("height", 720),
        "hidden": False,   # TODO: PBIR page-visibility key not confirmed yet
        "filters": [],   # TODO: PBIR page filter shape not mapped yet
        "visuals": visuals,
    }


def _extract_layout_pbir(z: zipfile.ZipFile, names: list[str], pbix: Path, has_datamodel: bool) -> dict:
    pages_meta = _pbir_read_json(z, "Report/definition/pages/pages.json")
    page_order = pages_meta.get("pageOrder") or sorted({
        n.split("/")[3] for n in names
        if n.startswith("Report/definition/pages/") and n.count("/") >= 4 and not n.endswith("pages.json")
    })
    theme: dict[str, Any] = {"base": None, "custom": None, "custom_json": None}
    theme_entry = next(
        (n for n in names if n.startswith("Report/StaticResources/SharedResources/BaseThemes/")
         and n.endswith(".json")), None)
    if theme_entry:
        theme = {"base": None, "custom": {"name": theme_entry}, "custom_json": _pbir_read_json(z, theme_entry)}
    return {
        "report": pbix.stem,
        "source": str(pbix),
        "layout_version": None,
        "has_embedded_datamodel": has_datamodel,
        "theme": theme,
        "custom_visual_packages": [],   # TODO: PBIR custom-visual package listing not mapped yet
        "pages": [_parse_page_pbir(z, names, pid) for pid in page_order],
        "format": "pbir",
    }


# ----------------------------------------------------------------------------
# Model (PBIXRay)
# ----------------------------------------------------------------------------

def df_records(df) -> list[dict]:
    try:
        return json.loads(df.to_json(orient="records"))
    except Exception:
        return []


def extract_model(pbix: Path) -> dict:
    try:
        from pbixray import PBIXRay
    except ImportError:
        return {"error": "pbixray no instalado (pip install pbixray)"}

    out: dict[str, Any] = {}
    try:
        m = PBIXRay(str(pbix))
    except Exception as e:  # LiveConnectionError, NoEmbeddedModelError, parse errors
        return {"error": f"{type(e).__name__}: {e}",
                "connection": getattr(e, "connections", None)}

    def grab(key: str, fn):
        try:
            out[key] = fn()
        except Exception as e:
            out[key] = {"error": f"{type(e).__name__}: {e}"}

    grab("tables", lambda: list(m.tables))
    grab("measures", lambda: df_records(m.dax_measures))
    grab("calculated_columns", lambda: df_records(m.dax_columns))
    grab("calculated_tables", lambda: df_records(m.dax_tables))
    grab("relationships", lambda: df_records(m.relationships))
    grab("rls", lambda: df_records(m.rls))
    grab("role_memberships", lambda: df_records(m.tmschema_role_memberships))
    grab("partitions", lambda: df_records(m.tmschema_partitions))   # Mode: 0=Import, 1=DirectQuery...
    grab("datasources", lambda: df_records(m.tmschema_datasources))
    grab("power_query", lambda: df_records(m.power_query))
    grab("metadata", lambda: df_records(m.metadata))

    # Storage mode per table (to know whether it's DirectQuery).
    modes = Counter()
    parts = out.get("partitions")
    if isinstance(parts, list):
        for p in parts:
            mode = p.get("Mode") if "Mode" in p else p.get("mode")
            modes[str(mode)] += 1
    out["storage_modes"] = dict(modes)   # '1' = DirectQuery in TMSCHEMA
    try:
        m.close()
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------
# Global inventory
# ----------------------------------------------------------------------------

def write_inventory(out_dir: Path, reports: list[tuple[dict, dict]]) -> None:
    vis_rows, meas_rows = [], []
    type_counter, custom_counter = Counter(), Counter()
    per_report = []

    for layout, model in reports:
        rname = layout["report"]
        n_vis, n_custom, n_hidden_pages = 0, 0, 0
        for page in layout["pages"]:
            n_hidden_pages += int(page["hidden"])
            for v in page["visuals"]:
                if v.get("is_group"):
                    continue
                n_vis += 1
                type_counter[v["type"]] += 1
                if v["is_custom"]:
                    n_custom += 1
                    custom_counter[v["type"]] += 1
                vis_rows.append({
                    "report": rname, "page": page["display_name"], "visual_id": v["id"],
                    "type": v["type"], "is_custom": v["is_custom"], "title": v["title"] or "",
                    "x": v["x"], "y": v["y"], "w": v["width"], "h": v["height"],
                    "n_fields": len(v["fields"]), "fields": " | ".join(v["fields"]),
                    "n_filters": len(v.get("filters", [])),
                    "formatting_objects": ",".join(v.get("objects_keys", [])),
                })
        measures = model.get("measures") if isinstance(model.get("measures"), list) else []
        for mrow in measures:
            meas_rows.append({"report": rname, "table": mrow.get("TableName"),
                              "measure": mrow.get("Name"), "expression": mrow.get("Expression")})
        rls = model.get("rls") if isinstance(model.get("rls"), list) else []
        per_report.append({
            "report": rname, "pages": len(layout["pages"]), "hidden_pages": n_hidden_pages,
            "visuals": n_vis, "custom_visuals": n_custom, "measures": len(measures),
            "rls_rules": len(rls), "storage_modes": model.get("storage_modes", {}),
            "model_error": model.get("error"),
        })

    def dump_csv(path: Path, rows: list[dict]):
        if not rows:
            return
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    dump_csv(out_dir / "inventory_visuals.csv", vis_rows)
    dump_csv(out_dir / "inventory_measures.csv", meas_rows)

    lines = ["# Power BI → HTML migration inventory\n",
             f"Reports processed: **{len(reports)}**  ",
             f"Total visuals: **{sum(type_counter.values())}**  ",
             f"Total measures: **{len(meas_rows)}**\n",
             "## Visuals by type\n", "| Type | Count | Custom |", "|---|---:|:---:|"]
    for t, n in type_counter.most_common():
        lines.append(f"| {t} | {n} | {'yes' if t in custom_counter else ''} |")
    lines += ["\n## By report\n",
              "| Report | Pages | Visuals | Custom | Measures | RLS rules | Modes | Model error |",
              "|---|---:|---:|---:|---:|---:|---|---|"]
    for r in per_report:
        lines.append(f"| {r['report']} | {r['pages']} | {r['visuals']} | {r['custom_visuals']} | "
                     f"{r['measures']} | {r['rls_rules']} | {r['storage_modes']} | {r['model_error'] or ''} |")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help=".pbix file or folder containing .pbix files")
    ap.add_argument("--out", default="out", help="output folder (default: ./out)")
    ap.add_argument("--no-model", action="store_true", help="skip model extraction (PBIXRay)")
    args = ap.parse_args(argv)

    src = Path(args.path)
    files = sorted(src.rglob("*.pbix")) if src.is_dir() else [src]
    if not files:
        print("No .pbix files found", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for pbix in files:
        print(f"→ {pbix.name}")
        try:
            layout = extract_layout(pbix)
        except Exception as e:
            print(f"   ✗ layout: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        model = {} if args.no_model else extract_model(pbix)
        if model.get("error"):
            print(f"   ! model: {model['error']}")

        rdir = out_dir / safe_name(pbix.stem)
        rdir.mkdir(exist_ok=True)
        (rdir / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
        (rdir / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        n_vis = sum(len(p["visuals"]) for p in layout["pages"])
        print(f"   ✓ {len(layout['pages'])} pages, {n_vis} visuals, "
              f"{len(model.get('measures') or []) if isinstance(model.get('measures'), list) else 0} measures")
        results.append((layout, model))

    write_inventory(out_dir, results)
    print(f"\nInventory at {out_dir}/summary.md")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    sys.exit(main())
