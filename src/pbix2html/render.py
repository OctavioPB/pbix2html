"""
Render: layout.json + metrics yaml + data → Report.html (self-contained).

Python decides the layout, theme, and which `kind` each visual is; the template's JS draws
with ECharts from `spec` + `data`. That way the same HTML works for both snapshot and live.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .semantic import KIND_MAP, ReportSpec

TEMPLATES = Path(__file__).parent / "templates"

# Default Power BI palette (baseTheme with no customization).
DEFAULT_THEME = {
    "data_colors": ["#118DFF", "#12239E", "#E66C37", "#6B007B", "#E044A7", "#744EC2", "#D9B300", "#D64550"],
    "background": "#FFFFFF", "foreground": "#252423", "muted": "#605E5C", "border": "#E1DFDD",
    "font_family": "'Segoe UI', system-ui, -apple-system, sans-serif",
}


def resolve_theme(layout_theme: dict | None) -> dict:
    """Merges the .pbix custom theme with the defaults; never invents colors."""
    t = dict(DEFAULT_THEME)
    cj = (layout_theme or {}).get("custom_json") or {}
    if cj.get("dataColors"):
        t["data_colors"] = list(cj["dataColors"])
    if cj.get("background"):
        t["background"] = cj["background"]
    if cj.get("foreground"):
        t["foreground"] = cj["foreground"]
    if cj.get("foregroundNeutralSecondary"):
        t["muted"] = cj["foregroundNeutralSecondary"]
    if cj.get("backgroundNeutral"):
        t["border"] = cj["backgroundNeutral"]
    face = ((cj.get("textClasses") or {}).get("title") or {}).get("fontFace") \
        or ((cj.get("textClasses") or {}).get("label") or {}).get("fontFace")
    if face and not face.lower().startswith("segoe"):
        t["font_family"] = f"'{face}', system-ui, sans-serif"
    return t


def _text_of(visual: dict) -> str | None:
    """Text of a textbox (objects.general.paragraphs) if available in the layout."""
    return visual.get("text")


def build_spec(layout: dict, spec: ReportSpec, values: dict[str, Any], include_hidden: bool = False,
               include_sql: bool = False) -> dict:
    """Structure consumed by the template/JS: pages → visuals with position in % and kind.

    `include_sql` (mode="hah" only, see ADR-004) additionally embeds each visual's raw
    `sql`/`params` — HAH has no server of ours to fetch data from, so the client has to
    run the query itself.
    """
    pages = []
    for i, p in enumerate(layout["pages"]):
        if p.get("hidden") and not include_hidden:
            continue
        W, H = float(p.get("width") or 1280), float(p.get("height") or 720)
        visuals = []
        for v in p["visuals"]:
            if v.get("is_group") or v.get("hidden"):
                continue
            vs = spec.visuals.get(v["id"])
            kind = vs.kind if vs else KIND_MAP.get(v["type"], "unsupported")
            if kind == "slicer":
                continue  # slicers are parameters, they're shown in the top bar
            r = (spec.raw.get("visuals") or {}).get(v["id"]) or {}
            entry = {
                "id": v["id"], "kind": kind, "type": v["type"],
                "title": (vs.title if vs and vs.title else v.get("title")),
                "left": round(100 * (v["x"] or 0) / W, 3), "top": round(100 * (v["y"] or 0) / H, 3),
                "w": round(100 * (v["width"] or 0) / W, 3), "h": round(100 * (v["height"] or 0) / H, 3),
                "z": v.get("z") or 0,
                "format": (vs.format if vs else {}), "headers": r.get("headers") or {},
                "stacked": "stacked" in v["type"].lower(), "area": "area" in v["type"].lower(),
                "inner_radius": v["type"] == "donutChart", "axis": r.get("axis") or {},
                "text": _text_of(v),
            }
            if include_sql:
                entry["sql"] = vs.sql if vs else None
                entry["params"] = vs.params if vs else []
            visuals.append(entry)
        pages.append({"id": f"page-{i}", "name": p.get("display_name") or f"Page {i + 1}",
                      "width": W, "height": H, "visuals": visuals})
    parameters = {name: {"label": p.get("label") or name, "value": values.get(name)}
                  for name, p in spec.parameters.items()}
    return {"report": spec.report, "theme": resolve_theme(layout.get("theme")), "pages": pages, "parameters": parameters}


def render_html(layout: dict, spec: ReportSpec, values: dict[str, Any], data: dict[str, dict] | None,
                mode: str = "snapshot", role: str | None = None, include_hidden: bool = False,
                hah_base: str | None = None) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    if mode == "hah":
        # ADR-004: unverified against a real HAH environment.
        page_spec = build_spec(layout, spec, values, include_hidden, include_sql=True)
        tpl = env.get_template("report_hah.html.j2")
        return tpl.render(
            spec=page_spec, theme=page_spec["theme"], mode=mode, role=role, generated_at=generated_at,
            hah_base=hah_base, sql_api=f"{hah_base}/api/execute", static_base=f"{hah_base}/static",
            spec_json=json.dumps(page_spec, ensure_ascii=False).replace("</", "<\\/"),
        )

    tpl = env.get_template("report.html.j2")
    page_spec = build_spec(layout, spec, values, include_hidden)
    return tpl.render(
        spec=page_spec, theme=page_spec["theme"], mode=mode, role=role, generated_at=generated_at,
        echarts_cdn=settings.echarts_cdn, api_base=settings.api_base,
        spec_json=json.dumps(page_spec, ensure_ascii=False).replace("</", "<\\/"),
        data_json=json.dumps(data or {}, ensure_ascii=False, default=str).replace("</", "<\\/"),
    )
