#!/usr/bin/env python3
"""
Extractor: .pbix → normalized JSON. Reads both the classic `Report/Layout` format and
PBIR (Power BI Desktop 2024+), reporting which one ran in `layout["format"]`.

For each .pbix it generates:
  out/<report>/layout.json    pages, visuals, positions, fields, filters, theme,
                              textbox content, embedded images (as data URIs)
  out/<report>/model.json     tables, DAX measures, relationships, RLS roles,
                              Power Query (M) source, mode (DirectQuery/Import)
And globally:
  out/inventory_visuals.csv   one row per visual (all reports)
  out/inventory_measures.csv  one row per measure (all reports)
  out/summary.md              summary to size the migration

Usage:
  pbix2html extract path/to/report.pbix
  pbix2html extract folder_with_pbix/ --out ./out
  pbix2html extract folder/ --no-model      # layout only (no PBIXRay)

model.json needs the optional `pbixray` dependency; without it the layout still
extracts and model.json carries an `error` instead.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import re
import sys
import zipfile
from collections import Counter
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


_TEXT_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{3,8}$")
_TEXT_SIZE_RE = re.compile(r"^\d{1,3}(\.\d+)?(pt|px|em|rem|%)$")


def extract_textbox_text(objects: dict) -> str | None:
    """A textbox's real content: objects.general[0].properties.paragraphs[].textRuns[].value
    (bold/color/italic per run), as a small HTML fragment. render.py's `text` kind inserts
    this via innerHTML, so the markup here (not just plain text) is what actually renders.

    `objects.general` exists on every visual type, not just textboxes (e.g. a chart's own
    `general` object holds unrelated settings) — checking for a `paragraphs` key first is
    what keeps this from misfiring on non-textbox visuals; only a real textbox has one.
    Was previously never called at all (see conversation notes): every textbox rendered as
    an empty box because nothing populated a `text` key on the visual.

    `color`/`fontSize` are strictly pattern-matched before being embedded in the `style`
    attribute this builds, not just checked for a leading "#" — this comes straight from
    the .pbix file's own JSON, so a crafted/corrupted file could otherwise smuggle a `"`
    into the value and break out of the attribute (e.g. inject an `onmouseover=...` onto
    the tag). `value` itself is html.escape()'d for the same reason.
    """
    paragraphs = None
    for g in (objects or {}).get("general") or []:
        props = (g or {}).get("properties") or {}
        if "paragraphs" in props:
            paragraphs = props["paragraphs"]
            break
    if not paragraphs:
        return None
    lines = []
    for para in paragraphs:
        runs = []
        for run in para.get("textRuns") or []:
            value = run.get("value") or ""
            # A run bound to a field/measure carries a dict (propertyIdentifier/selector)
            # instead of literal text; there's nothing static to render, so skip it
            # rather than losing the whole textbox (seen in a real report).
            if not value or not isinstance(value, str):
                continue
            style = run.get("textStyle") or {}
            css = []
            if style.get("fontWeight") == "bold":
                css.append("font-weight:bold")
            if style.get("fontStyle") == "italic":
                css.append("font-style:italic")
            color = style.get("color")
            if isinstance(color, str) and _TEXT_COLOR_RE.match(color):
                css.append(f"color:{color}")
            size = style.get("fontSize")
            if isinstance(size, str) and _TEXT_SIZE_RE.match(size):
                css.append(f"font-size:{size}")
            escaped = html.escape(value)
            runs.append(f'<span style="{";".join(css)}">{escaped}</span>' if css else escaped)
        lines.append("".join(runs))
    return "<p>" + "</p><p>".join(lines) + "</p>" if any(lines) else None


# Power BI visualType → Content-Type, for the handful of raster/vector formats an
# `image` visual's ResourcePackageItem can point to. Anything else is left unresolved
# rather than guessed at — see embed_image_resources.
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".webp": "image/webp",
}


def theme_palette(theme: dict | None) -> list[str] | None:
    """The palette `ThemeDataColor.ColorId` indexes into: 0 = background, 1 = foreground,
    then the theme's `dataColors` from index 2 (verified against a real report: a fill of
    ColorId 2 / Percent 0.6 is the first data colour, #FF5F02, tinted to #FFBF9A). None
    when the report's theme JSON isn't available, in which case theme references are dropped
    rather than guessed."""
    cj = (theme or {}).get("custom_json") or {}
    colors = cj.get("dataColors")
    if not colors:
        return None
    return [cj.get("background") or "#FFFFFF", cj.get("foreground") or "#000000", *colors]


def _tint(hex_color: str, percent: float) -> str:
    """Power BI's Percent: >0 lightens toward white, <0 darkens toward black (linear mix)."""
    h = hex_color.lstrip("#")
    if len(h) != 6 or not percent:
        return "#" + h.upper() if len(h) == 6 else hex_color
    target = 255 if percent > 0 else 0
    f = min(abs(float(percent)), 1.0)
    rgb = [round(int(h[k:k + 2], 16) + (target - int(h[k:k + 2], 16)) * f) for k in (0, 2, 4)]
    return "#{:02X}{:02X}{:02X}".format(*rgb)


_THEME_MARKER_RE = re.compile(r"^theme:(\d+):(-?[\d.]+)$")


def literal_color(expr: Any, theme_colors: list[str] | None = None) -> str | None:
    """A Power BI colour expression → '#RRGGBB'.

    Two shapes appear in Layout: a literal (`{"Literal": {"Value": "'#FFFFFF'"}}`) and a
    reference into the theme palette (`{"ThemeDataColor": {"ColorId": 0, "Percent": 0}}`).
    With the palette known (`theme_colors`, see `theme_palette`) the reference is resolved
    to a hex colour; without it — parsing happens before the theme is read — it comes back
    as a `theme:<id>:<percent>` marker that `resolve_theme_markers` resolves afterwards."""
    if not isinstance(expr, dict):
        return None
    literal = literal_to_text(expr)
    if literal and literal.startswith("#"):
        return literal
    theme_ref = expr.get("ThemeDataColor")
    if isinstance(theme_ref, dict):
        idx, pct = theme_ref.get("ColorId"), theme_ref.get("Percent") or 0
        if isinstance(idx, int) and isinstance(pct, (int, float)):
            if not theme_colors:
                return f"theme:{idx}:{pct}"
            if 0 <= idx < len(theme_colors):
                return _tint(theme_colors[idx], pct)
    return None


def resolve_theme_markers(layout: dict) -> None:
    """Replaces `theme:<id>:<pct>` markers (page background, visual background/border/fill)
    with real colours now that the theme is known; unresolvable ones are removed."""
    palette = theme_palette(layout.get("theme"))

    def fix(holder: dict, key: str, keep_key: bool = False) -> None:
        val = holder.get(key)
        m = _THEME_MARKER_RE.match(val) if isinstance(val, str) else None
        if not m:
            return
        idx, pct = int(m.group(1)), float(m.group(2))
        if palette and 0 <= idx < len(palette):
            holder[key] = _tint(palette[idx], pct)
        elif keep_key:
            holder[key] = None
        else:
            holder.pop(key, None)

    for page in layout.get("pages", []):
        fix(page, "background", keep_key=True)
        for v in page.get("visuals", []):
            style = v.get("style")
            if isinstance(style, dict):
                fix(style, "background")
                fix(style, "border_color")
            for state in ((v.get("button") or {}).get("states") or {}).values():
                for card in ("text", "fill", "outline"):
                    if isinstance(state.get(card), dict):
                        fix(state[card], "color")


def _object_color(objects: dict, name: str, prop: str = "color",
                  theme_colors: list[str] | None = None) -> str | None:
    """Colour out of a formatting object, e.g. vcObjects.background[0].properties.color
    .solid.color.expr — the shape both visual-container and page backgrounds use."""
    for entry in (objects or {}).get(name) or []:
        props = (entry or {}).get("properties") or {}
        solid = ((props.get(prop) or {}).get("solid") or {}).get("color") or {}
        colour = literal_color(solid.get("expr"), theme_colors)
        if colour:
            return colour
    return None


def _object_flag(objects: dict, name: str, prop: str = "show") -> bool | None:
    """True/False for a formatting toggle (e.g. vcObjects.border[0].properties.show),
    or None when the report doesn't set it either way."""
    for entry in (objects or {}).get(name) or []:
        props = (entry or {}).get("properties") or {}
        raw = literal_to_text((props.get(prop) or {}).get("expr"))
        if raw is not None:
            return raw.strip().lower() == "true"
    return None


def _object_text(objects: dict, name: str, *props: str) -> str | None:
    """Text out of a formatting object, e.g. vcObjects.title[0].properties.text.expr, or
    objects.categoryAxis[0].properties.titleText.expr. Tries each candidate property
    name in turn because Power BI isn't consistent about it (`text`, `titleText`,
    `labelText` all appear for what a reader just sees as a label)."""
    for entry in (objects or {}).get(name) or []:
        properties = (entry or {}).get("properties") or {}
        for prop in props:
            text = literal_to_text((properties.get(prop) or {}).get("expr"))
            if text:
                return text
    return None


def _is_shown(objects: dict, name: str) -> bool:
    """Whether a formatting object is switched on. Absent means on: Power BI writes the
    `show` flag only when someone has changed it."""
    return _object_flag(objects, name, "show") is not False


def visual_text(sv: dict) -> dict:
    """Every piece of human-readable text a visual carries, as
    {title, subtitle, shape_text, axis_x, axis_y, legend}, omitting whatever isn't set.

    Only `title` was read before, and it was read even when the report switches the
    title *off* — so converted reports showed headings the original hides. Everything
    else here (a subtitle, the label on a button or shape, axis and legend titles) was
    dropped outright, which is why shapes and buttons came out as blank boxes.

    `show: false` is honoured for each one independently. Dynamic titles — bound to a
    measure rather than typed — can't be resolved without running a query, so they come
    back as None rather than as a wrong literal."""
    vco = sv.get("vcObjects") or {}
    objects = sv.get("objects") or {}
    out: dict[str, str] = {}

    if _is_shown(vco, "title"):
        title = _object_text(vco, "title", "text", "titleText")
        if title:
            out["title"] = title
    if _is_shown(vco, "subTitle"):
        subtitle = _object_text(vco, "subTitle", "text", "titleText")
        if subtitle:
            out["subtitle"] = subtitle
    # Buttons and shapes: the label lives in its own object, and which one depends on
    # the visual. Checking several is cheaper than caring which; a miss yields nothing.
    for container, name in ((vco, "text"), (objects, "text"), (objects, "shape"),
                            (vco, "outline"), (objects, "general")):
        shape_text = _object_text(container, name, "text", "labelText")
        if shape_text:
            out["shape_text"] = shape_text
            break
    if _is_shown(objects, "categoryAxis"):
        axis_x = _object_text(objects, "categoryAxis", "titleText")
        if axis_x:
            out["axis_x"] = axis_x
    if _is_shown(objects, "valueAxis"):
        axis_y = _object_text(objects, "valueAxis", "titleText")
        if axis_y:
            out["axis_y"] = axis_y
    if _is_shown(objects, "legend"):
        legend = _object_text(objects, "legend", "titleText", "text")
        if legend:
            out["legend"] = legend
    return out


def container_style(vc_objects: dict, theme_colors: list[str] | None = None) -> dict:
    """The visual's own frame as the .pbix defines it: {background, border, border_color}.

    Power BI defaults a visual to *no* border and a transparent background; the renderer
    used to draw a 1px border and a solid fill on every visual regardless, which turns
    any report into a uniform grid of boxes the original doesn't have. Keys are omitted
    when the report doesn't specify them, so the renderer can tell "explicitly set" from
    "not mentioned"."""
    style: dict[str, Any] = {}
    background = _object_color(vc_objects, "background", theme_colors=theme_colors)
    if background:
        style["background"] = background
    border = _object_flag(vc_objects, "border")
    if border is not None:
        style["border"] = border
    border_colour = _object_color(vc_objects, "border", theme_colors=theme_colors)
    if border_colour:
        style["border_color"] = border_colour
        style.setdefault("border", True)
    return style


def _fill_color(objects: dict) -> str | None:
    """Fill of a shape / button (`objects.fill`): the default-state `fillColor` when the
    fill is shown and not fully transparent. Same colour forms as `literal_color`."""
    entries = (objects or {}).get("fill") or []
    shown = True
    for e in entries:
        if not (e or {}).get("selector"):
            flag = literal_to_text((((e.get("properties") or {}).get("show") or {}).get("expr")) or {})
            if flag == "false":
                shown = False
    if not shown:
        return None
    for e in entries:
        sel = (e or {}).get("selector") or {}
        props = (e or {}).get("properties") or {}
        if sel.get("id") != "default" or "fillColor" not in props:
            continue
        transp = literal_to_text(((props.get("transparency") or {}).get("expr")) or {})
        try:
            if transp is not None and float(str(transp).rstrip("DdLl")) >= 100:
                return None
        except ValueError:
            pass
        solid = ((props["fillColor"] or {}).get("solid") or {}).get("color") or {}
        return literal_color(solid.get("expr"))
    return None


_BUTTON_STATES = ("default", "hover", "pressed", "disabled", "selected")


def _num(expr: Any) -> float | None:
    """A numeric literal such as `11D` / `2L` / `0.5D` → float."""
    text = literal_to_text(expr)
    if text is None:
        return None
    try:
        return float(text.rstrip("DdLl"))
    except ValueError:
        return None


def _bool(expr: Any) -> bool | None:
    text = literal_to_text(expr)
    return {"true": True, "false": False}.get(text) if text is not None else None


def _color_prop(prop: Any) -> str | None:
    return literal_color(((prop or {}).get("solid") or {}).get("color", {}).get("expr")) if isinstance(prop, dict) else None


def parse_button(sv: dict) -> dict | None:
    """Formatting of an `actionButton`, per state: {state: {text, fill, outline, round, icon}}.

    Power BI stores each formatting card (`text`, `fill`, `outline`, `shape`, `icon`) as a list
    of entries: one without a selector that carries the card's `show` flag, and one per
    state (`selector.id`: default / hover / pressed / disabled / selected) with only the
    properties that differ from the button defaults. A state's missing properties fall back
    to `default` (merged by the renderer). Only what the report sets is returned, so absent
    keys mean "not set" and the renderer does not invent styling. The property names beyond
    `text`, `fontSize`, `fill.fillColor/transparency` and `icon.shapeType` follow Power BI's
    documented names but are unverified against a real file (see ADR-005)."""
    objects = (sv or {}).get("objects") or {}
    shown: dict[str, bool] = {}
    for card in ("text", "fill", "outline", "shape", "icon"):
        for e in objects.get(card) or []:
            if not (e or {}).get("selector"):
                flag = _bool((((e.get("properties") or {}).get("show") or {}).get("expr")))
                if flag is not None:
                    shown[card] = flag
    states: dict[str, dict] = {}
    for card in ("text", "fill", "outline", "shape", "icon"):
        for e in objects.get(card) or []:
            sel = ((e or {}).get("selector") or {}).get("id")
            if sel not in _BUTTON_STATES:
                continue
            pr = e.get("properties") or {}
            st = states.setdefault(sel, {})
            ex = lambda k: (pr.get(k) or {}).get("expr")   # noqa: E731
            if card == "text":
                t = {k: v for k, v in {
                    "label": literal_to_text(ex("text")), "size": _num(ex("fontSize")),
                    "color": _color_prop(pr.get("fontColor")), "font": literal_to_text(ex("fontFamily")),
                    "bold": _bool(ex("bold")), "italic": _bool(ex("italic")), "underline": _bool(ex("underline")),
                    "align": literal_to_text(ex("horizontalAlignment")),
                    "valign": literal_to_text(ex("verticalAlignment")),
                }.items() if v is not None}
                if t:
                    st["text"] = t
            elif card == "fill":
                f = {k: v for k, v in {"color": _color_prop(pr.get("fillColor")),
                                       "transparency": _num(ex("transparency"))}.items() if v is not None}
                if f:
                    st["fill"] = f
            elif card == "outline":
                o = {k: v for k, v in {"color": _color_prop(pr.get("lineColor")), "weight": _num(ex("weight")),
                                       "transparency": _num(ex("transparency"))}.items() if v is not None}
                if o:
                    st["outline"] = o
            elif card == "shape":
                r = _num(ex("roundEdge"))
                if r is not None:
                    st["round"] = r
            elif card == "icon":
                kind = literal_to_text(ex("shapeType"))
                if kind:
                    st["icon"] = kind
    hidden = {c for c, flag in shown.items() if flag is False}
    if not states and not hidden:
        return None
    return {"states": states, "hidden": sorted(hidden)}


def _style_with_fill(style: dict, objects: dict) -> dict:
    """A shape/button's own fill is its background unless the container sets one."""
    if "background" not in style and (fill := _fill_color(objects)):
        style["background"] = fill
    return style


def _image_ref(objects: dict) -> dict | None:
    """{'package': ..., 'item': ...} for an `image`-kind visual's picture — the
    resource reference at objects.general[0].properties.imageUrl.expr.ResourcePackageItem
    (PackageName/ItemName) — or None if this visual has no image set (or isn't an image
    visual at all; same `objects.general` ambiguity noted in extract_textbox_text).
    Resolved to actual bytes later by embed_image_resources, once the zip is available."""
    for g in (objects or {}).get("general") or []:
        props = (g or {}).get("properties") or {}
        item = ((props.get("imageUrl") or {}).get("expr") or {}).get("ResourcePackageItem") or {}
        if item.get("ItemName"):
            return {"package": item.get("PackageName"), "item": item.get("ItemName")}
    return None


def _page_background_image(objects: dict) -> dict | None:
    """{'package', 'item', 'scaling'} for a page's background *image*
    (objects.background[*].properties.image.image), or None. Many designed reports put
    their whole layout (panels, banners, a pale title area) in this picture, so ignoring
    it can leave white text on a white canvas. Resolved to bytes by embed_image_resources."""
    for b in (objects or {}).get("background") or []:
        img = (((b or {}).get("properties") or {}).get("image") or {}).get("image") or {}
        item = ((img.get("url") or {}).get("expr") or {}).get("ResourcePackageItem") or {}
        if item.get("ItemName"):
            return {"package": item.get("PackageName"), "item": item["ItemName"],
                    "scaling": (literal_to_text((img.get("scaling") or {}).get("expr") or {}) or "Normal")}
    return None


def _read_image_data_uri(z: zipfile.ZipFile, names: set[str], ref: dict) -> str | None:
    item = ref.get("item")
    if not item:
        return None
    package = ref.get("package")
    candidates = [f"Report/StaticResources/{package}/{item}" if package else None,
                  f"Report/StaticResources/RegisteredResources/{item}"]
    member = next((c for c in candidates if c and c in names), None)
    if member is None:
        return None
    mime = _IMAGE_MIME.get(Path(item).suffix.lower())
    if not mime:
        return None
    try:
        data = z.read(member)
    except Exception:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def embed_image_resources(z: zipfile.ZipFile, layout: dict) -> None:
    """Resolves every visual's `image_ref` (set during page/visual parsing, see
    _image_ref) to the actual embedded picture as a base64 data URI, mutating
    `layout` in place. Previously an `image`-kind visual always rendered as an empty
    frame (render.py's `static` kind drew nothing at all) even though the picture was
    sitting right there in the .pbix's StaticResources the whole time — this is what
    makes it show up. A data URI keeps the generated HTML a single self-contained
    file, same as everything else here, instead of a second asset to ship alongside
    it. Missing/unrecognized resources are left without `image_data_uri`, not fatal
    to the rest of the report."""
    names = set(z.namelist())
    cache: dict[tuple, str | None] = {}

    def resolve(ref: dict) -> str | None:
        key = (ref.get("package"), ref.get("item"))
        if key not in cache:
            cache[key] = _read_image_data_uri(z, names, ref)
        return cache[key]

    for page in layout.get("pages", []):
        bg = page.get("background_image")
        if bg and (uri := resolve(bg)):
            bg["data_uri"] = uri
        for v in page.get("visuals", []):
            ref = v.get("image_ref")
            if ref and (uri := resolve(ref)):
                v["image_data_uri"] = uri


def safe_name(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", s).strip("_") or "report"


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
            # PBIR names it "field"; classic names it "expression". Same inner shape.
            expr = f.get("expression") or f.get("field") or {}
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
        "filters": [], "has_drill_other_visuals": False, "objects_keys": [], "text": None,
        "image_ref": None,
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
                       "title": group.get("displayName"), "projections": {}, "fields": [],
                       "text": None, "image_ref": None, "filters": [],
                       # A hidden group hides all its descendants: it is how reports build
                       # "view switchers" (see ADR-005).
                       "hidden": bool(group.get("isHidden")),
                       "has_drill_other_visuals": False, "objects_keys": []})
        return visual

    sv = sv or {}
    vtype = sv.get("visualType", "unknown")
    projections = sv.get("projections") or {}
    # A projections role can come back as null (empty field well) instead of omitted.
    fields = sorted({p.get("queryRef") for role in projections.values() if isinstance(role, list)
                     for p in role if isinstance(p, dict) and p.get("queryRef")})

    qmap = _proto_query_refs(sv)     # renamed tables leave stale names in queryRef
    vco = sv.get("vcObjects") or {}
    texts = visual_text(sv)          # title, subtitle, shape/button label, axis + legend titles
    title = texts.get("title")

    display = (sv.get("display") or {}).get("mode")

    visual.update({
        "type": vtype,
        "is_group": False,
        "is_custom": vtype not in STANDARD_VISUALS and bool(CUSTOM_VISUAL_PATTERN.match(vtype)),
        "title": title,
        "hidden": display == "hidden",
        "projections": {role: [qmap.get(p.get("queryRef"), p.get("queryRef")) for p in (refs or [])
                               if isinstance(p, dict)]
                        for role, refs in projections.items()},
        "fields": sorted({qmap.get(f, f) for f in fields}),
        "filters": parse_filters(vc.get("filters")),
        "has_drill_other_visuals": bool(sv.get("drillFilterOtherVisuals")),
        "objects_keys": sorted((sv.get("objects") or {}).keys()),   # applied formatting (dataPoint, labels...)
        "text": extract_textbox_text(sv.get("objects") or {}),
        "image_ref": _image_ref(sv.get("objects") or {}),
        "style": _style_with_fill(container_style(vco), sv.get("objects") or {}),
        "texts": texts,
        "action": _visual_link(vco),
        "sort": _proto_sort(sv),
        **({"button": parse_button(sv)} if vtype == "actionButton" else {}),
    })
    return visual


def absolutize_group_children(visuals: list[dict]) -> None:
    """Classic Layout stores a grouped visual's x/y *relative to its group* (a child
    that fills its group is at 0,0 with the group's width/height), and nested groups
    are relative to their parent. Rendering them as-is piles every grouped visual into
    the page's top-left corner, so add the ancestors' offsets in place. Seen on a real
    report where half the visuals were grouped. Cycles / unknown parents are ignored."""
    by_id = {v.get("id"): v for v in visuals if v.get("id")}
    done: set[str] = set()

    def shift(v: dict, seen: frozenset = frozenset()) -> None:
        vid = v.get("id")
        parent = by_id.get(v.get("parent_group"))
        if vid in done or parent is None or vid in seen:
            return
        shift(parent, seen | {vid})
        v["x"] = (v.get("x") or 0) + (parent.get("x") or 0)
        v["y"] = (v.get("y") or 0) + (parent.get("y") or 0)
        done.add(vid)

    for v in visuals:
        shift(v)


def _visual_link(vco: dict) -> dict | None:
    """What clicking a visual/button does, from `vcObjects.visualLink`:
    {"type": "page", "page": <section name>, "enabled"} for PageNavigation, or
    {"type": "bookmark", "bookmark": <bookmark name>, "enabled"}. Other link types
    (WebUrl, Back, drill-through) are not modelled yet."""
    for link in (vco or {}).get("visualLink") or []:
        props = (link or {}).get("properties") or {}

        def lit(key: str) -> str | None:
            text = literal_to_text(((props.get(key) or {}).get("expr")) or {})
            return text.strip("'") if isinstance(text, str) else None

        kind = lit("type")
        enabled = lit("show") != "false"
        if kind == "PageNavigation":
            return {"type": "page", "page": lit("navigationSection"), "enabled": enabled}
        if kind == "Bookmark":
            return {"type": "bookmark", "bookmark": lit("bookmark"), "enabled": enabled}
    return None


def parse_bookmarks(root_config: dict) -> list[dict]:
    """Classic `config.bookmarks[]` → [{id, name, page, groups, targets, apply_only_to_targets}].

    A bookmark is a saved page state. What matters for view switchers is
    `explorationState.sections[<page>].visualContainerGroups` = {groupId: {isHidden}}: which
    groups are visible. `options.targetVisualNames` (group *and* visual ids) plus
    `applyOnlyToTargetVisuals` limit which groups the bookmark is allowed to change, which is
    how one page hosts independent switchers. Filter/slicer state (`suppressData`) is not
    modelled (ADR-005, phase 3). See ADR-005."""
    out: list[dict] = []

    def visit(items: Any) -> None:
        for b in items if isinstance(items, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("children"):
                visit(b["children"])
            es = b.get("explorationState") or {}
            sections = es.get("sections") or {}
            sid = es.get("activeSection") if es.get("activeSection") in sections else next(iter(sections), None)
            sec = sections.get(sid) or {}
            groups = {gid: bool((st or {}).get("isHidden"))
                      for gid, st in (sec.get("visualContainerGroups") or {}).items()}
            opts = b.get("options") or {}
            if b.get("name"):
                out.append({"id": b["name"], "name": b.get("displayName"), "page": sid, "groups": groups,
                            "targets": list(opts.get("targetVisualNames") or []),
                            "apply_only_to_targets": bool(opts.get("applyOnlyToTargetVisuals"))})

    visit((root_config or {}).get("bookmarks"))
    return out


def parse_page(section: dict) -> dict:
    cfg = loads_maybe(section.get("config", "{}")) or {}
    objects = cfg.get("objects") or {}
    # A page's own canvas colour. Power BI splits it in two: `background` is the canvas
    # itself and `outspace` is the area around it (what the UI calls Wallpaper); a report
    # that sets only the wallpaper still reads as "that colour" to anyone looking at it,
    # so either one is better than falling back to the theme default.
    page_background = (_object_color(objects, "background")
                       or _object_color(objects, "outspace"))
    visuals = [parse_visual(vc) for vc in section.get("visualContainers", [])]
    absolutize_group_children(visuals)
    return {
        "name": section.get("name"),
        "display_name": section.get("displayName"),
        "ordinal": section.get("ordinal"),
        "width": section.get("width"),
        "height": section.get("height"),
        "hidden": cfg.get("visibility") == 1,
        "background": page_background,
        "background_image": _page_background_image(objects),
        "filters": parse_filters(section.get("filters")),
        "visuals": visuals,
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
        result = {
            "report": pbix.stem,
            "source": str(pbix),
            "layout_version": (loads_maybe(layout.get("config", "{}")) or {}).get("version"),
            "has_embedded_datamodel": has_datamodel,
            "theme": theme,
            "custom_visual_packages": list(dict.fromkeys(
                [c for c in custom_packages if c] + _zip_custom_visuals(names))),
            "pages": [parse_page(s) for s in layout.get("sections", [])],
            "format": "classic",
            "bookmarks": parse_bookmarks(loads_maybe(layout.get("config", "{}")) or {}),
        }
        resolve_theme_markers(result)
        embed_image_resources(z, result)   # needs the zip still open
    return result


# ----------------------------------------------------------------------------
# Layout — PBIR / Enhanced Report Format (Power BI Desktop 2024+)
#
# Structure discovered against real .pbix files that no longer ship a classic
# Report/Layout blob (see pbix2html-fixv1.md #6 and skill pbix-layout):
#   Report/definition/report.json                                  ← presence = PBIR
#   Report/definition/pages/pages.json                              → pageOrder
#   Report/definition/pages/<pageId>/page.json                      → one per page
#   Report/definition/pages/<pageId>/visuals/<visualId>/visual.json → one per visual
# The hidden/filter/custom-visual keys below follow Microsoft's published PBIR JSON
# schemas (visualContainer `isHidden`/`filterConfig`/`parentGroupName`, page `visibility`/
# `filterConfig`, report `publicCustomVisuals`/`resourcePackages`) but have not been
# confirmed against a real file yet; they degrade to "not hidden / no filters".
# ----------------------------------------------------------------------------

def _pbir_read_json(z: zipfile.ZipFile, path: str) -> dict:
    """Missing entry or malformed JSON both just mean "nothing here": PBIR has one
    file per page/visual, so a single bad entry shouldn't be worse than a missing one."""
    try:
        with z.open(path) as f:
            return json.loads(f.read().decode("utf-8-sig"))
    except (KeyError, ValueError):
        return {}


def canonical_query_ref(query_ref: str, entity: str, prop: str) -> str:
    """Rebuilds a projection's `queryRef` from its real field. Power BI does not rewrite
    `queryRef` when a table is renamed, so it keeps the old name (`Sum(Fixed Capacity
    Mnthly.x)` for a field whose SourceRef.Entity is now `Active Compute Fixed Mnthly`),
    and everything keyed on it then points at a table that no longer exists. Only the
    aggregation wrapper is kept from the original."""
    if not entity or not prop:
        return query_ref
    m = re.match(r"^([A-Za-z]+)\((.*)\)$", query_ref or "")
    inner = f"{entity}.{prop}"
    return f"{m.group(1)}({inner})" if m else inner


def _entity_prop(node: Any, aliases: dict | None = None) -> tuple[str, str] | None:
    """First (entity, property) found in a field/expression node (any nesting: Column,
    Measure, Aggregation, HierarchyLevel...). `aliases` maps a classic `Source` alias to its
    table; PBIR carries the entity directly."""
    aliases = aliases or {}
    if isinstance(node, dict):
        expr = node.get("Expression")
        src = expr.get("SourceRef") if isinstance(expr, dict) else None
        if src and ("Property" in node or "Level" in node):
            return (src.get("Entity") or aliases.get(src.get("Source")) or "",
                    node.get("Property") or node.get("Level") or "")
        for v in node.values():
            found = _entity_prop(v, aliases)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _entity_prop(v, aliases)
            if found:
                return found
    return None


def _sort_entries(items: list, direction_of) -> list[dict]:
    """[{entity, property, direction: 'asc'|'desc'}] for a visual's sort definition."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        ep = _entity_prop(it.get("field") or it.get("Expression"), it.get("_aliases"))
        if ep and ep[0] and ep[1]:
            out.append({"entity": ep[0], "property": ep[1], "direction": direction_of(it)})
    return out


def _proto_sort(sv: dict) -> list[dict]:
    """Classic: `prototypeQuery.OrderBy` (Direction 1 = ascending, 2 = descending)."""
    pq = (sv or {}).get("prototypeQuery") or {}
    aliases = {f.get("Name"): f.get("Entity") for f in pq.get("From") or [] if isinstance(f, dict)}
    items = [{**o, "_aliases": aliases} for o in pq.get("OrderBy") or [] if isinstance(o, dict)]
    return _sort_entries(items, lambda o: "desc" if o.get("Direction") == 2 else "asc")


def _pbir_sort(vis: dict) -> list[dict]:
    """PBIR: `query.sortDefinition.sort[{field, direction: 'Ascending'|'Descending'}]`."""
    items = (((vis.get("query") or {}).get("sortDefinition")) or {}).get("sort") or []
    return _sort_entries(items, lambda o: "desc" if str(o.get("direction")).lower().startswith("desc") else "asc")


def _proto_query_refs(sv: dict) -> dict[str, str]:
    """Classic: {stale Select.Name -> canonical queryRef} from `prototypeQuery`, whose
    expressions carry the real entity (through the `From` aliases)."""
    pq = (sv or {}).get("prototypeQuery") or {}
    aliases = {f.get("Name"): f.get("Entity") for f in pq.get("From") or [] if isinstance(f, dict)}
    out: dict[str, str] = {}
    for sel in pq.get("Select") or []:
        name = (sel or {}).get("Name")
        ep = _entity_prop(sel, aliases)
        if name and ep and ep[0] and ep[1]:
            fixed = canonical_query_ref(name, ep[0], ep[1])
            if fixed != name:
                out[name] = fixed
    return out


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
            fields.append({"role": role_name, "entity": entity, "property": prop,
                           "queryRef": canonical_query_ref(query_ref, entity, prop)})
    return fields


def _pbir_texts(container_objects: dict, vis: dict) -> dict:
    """Same job as `visual_text` for PBIR, where the container's formatting sits in
    `visualContainerObjects` rather than `vcObjects` while the visual's own objects
    keep the classic shape."""
    return visual_text({"vcObjects": container_objects or {}, "objects": vis.get("objects") or {}})


def _pbir_title(container_objects: dict) -> str | None:
    return _pbir_texts(container_objects, {}).get("title")


def _parse_visual_pbir(vdata: dict, vid: str) -> dict:
    try:
        pos = vdata.get("position") or {}
        group = vdata.get("visualGroup")
        if group is not None:
            # PBIR represents a group container as {"visualGroup": {...}}, no "visual" key at
            # all — confirmed against a real file (see pbix2html-fixv1.md). Without this branch
            # it fell through to type="unknown"/is_group=False and rendered as a full-page
            # "unsupported" box instead of being skipped like the legacy-format group is.
            return {
                "id": vid, "x": pos.get("x", 0), "y": pos.get("y", 0), "z": pos.get("z", 0),
                "width": pos.get("width", 0), "height": pos.get("height", 0),
                "tab_order": pos.get("tabOrder"), "parent_group": vdata.get("parentGroupName"),
                "type": "__group__", "is_group": True, "is_custom": False,
                "title": group.get("displayName"), "hidden": bool(vdata.get("isHidden")),
                "projections": {}, "fields": [], "filters": [],
                "has_drill_other_visuals": False, "objects_keys": [], "text": None,
                "image_ref": None,
            }
        vis = vdata.get("visual") or {}
        qs = ((vis.get("query") or {}).get("queryState")) or {}
        vtype = vis.get("visualType", "unknown")
        fields = _pbir_fields(qs)
        projections: dict[str, list] = {}
        for f in fields:
            projections.setdefault(f["role"], []).append(f["queryRef"])
        # Container formatting (title, background, border, visualLink...) sits inside `visual`
        # in real PBIR files; the older fixture put it at the top level, so accept both.
        vco = vis.get("visualContainerObjects") or vdata.get("visualContainerObjects") or {}
        return {
            "id": vid, "x": pos.get("x", 0), "y": pos.get("y", 0), "z": pos.get("z", 0),
            "width": pos.get("width", 0), "height": pos.get("height", 0),
            "tab_order": pos.get("tabOrder"), "parent_group": vdata.get("parentGroupName"),
            "type": vtype, "is_group": False,   # a real group container returns above instead
            "is_custom": vtype not in STANDARD_VISUALS and bool(CUSTOM_VISUAL_PATTERN.match(vtype)),
            "title": _pbir_texts(vco, vis).get("title"),
            "hidden": bool(vdata.get("isHidden")) or vis.get("visible") is False,
            "projections": projections,
            "fields": sorted({f["queryRef"] for f in fields if f["queryRef"]}),
            "filters": parse_filters((vdata.get("filterConfig") or {}).get("filters")),
            "has_drill_other_visuals": bool(vis.get("drillFilterOtherVisuals")),
            "objects_keys": sorted((vis.get("objects") or {}).keys()),
            "text": extract_textbox_text(vis.get("objects") or {}),
            "image_ref": _image_ref(vis.get("objects") or {}),
            "texts": _pbir_texts(vco, vis),
            "style": _style_with_fill(container_style(vco), vis.get("objects") or {}),
            "action": _visual_link(vco),
            "sort": _pbir_sort(vis),
            **({"button": parse_button(vis)} if vtype == "actionButton" else {}),
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
    objects = page_data.get("objects") or {}
    absolutize_group_children(visuals)   # PBIR child positions are relative to the group too
    return {
        "name": page_id,
        "display_name": page_data.get("displayName", page_id),
        "ordinal": None,
        "width": page_data.get("width", 1280),
        "height": page_data.get("height", 720),
        "hidden": page_data.get("visibility") == "HiddenInViewMode",
        "background": _object_color(objects, "background") or _object_color(objects, "outspace"),
        "background_image": _page_background_image(objects),
        "filters": parse_filters((page_data.get("filterConfig") or {}).get("filters")),
        "visuals": visuals,
    }


def _zip_custom_visuals(names: list[str]) -> list[str]:
    """Custom visuals bundled inside the file: `Report/CustomVisuals/<visualType>/...`.
    Real reports list them here rather than in `resourcePackages`."""
    return sorted({n.split("/")[2] for n in names
                   if n.startswith("Report/CustomVisuals/") and n.count("/") >= 3})


def _pbir_custom_packages(report_meta: dict) -> list[str]:
    """Custom visual names declared in report.json: `publicCustomVisuals` (AppSource ids)
    plus `resourcePackages` entries of type CustomVisual."""
    names = [n for n in report_meta.get("publicCustomVisuals") or [] if isinstance(n, str)]
    for pkg in report_meta.get("resourcePackages") or []:
        if isinstance(pkg, dict) and pkg.get("type") == "CustomVisual" and pkg.get("name"):
            names.append(pkg["name"])
    return list(dict.fromkeys(names))


def _pbir_theme(z: zipfile.ZipFile, names: list[str], report_meta: dict) -> dict:
    """`report.json → themeCollection`: the custom theme (a RegisteredResources JSON) wins over
    the base one (`SharedResources/BaseThemes/<name>.json`), as in Power BI. Without a
    declared custom theme the base theme JSON is used, as before."""
    tc = report_meta.get("themeCollection") or {}
    custom, base = tc.get("customTheme") or {}, tc.get("baseTheme") or {}
    theme: dict[str, Any] = {"base": base or None, "custom": custom or None, "custom_json": None}
    if custom.get("name"):
        path = f"Report/StaticResources/{custom.get('type') or 'RegisteredResources'}/{custom['name']}"
        if path in names:
            theme["custom_json"] = _pbir_read_json(z, path) or None
            theme["custom_path"] = path
            return theme
    entry = next((n for n in names if n.startswith("Report/StaticResources/SharedResources/BaseThemes/")
                  and n.endswith(".json") and (not base.get("name") or n.endswith(f"/{base['name']}.json"))), None)
    if entry:
        theme["custom"] = theme["custom"] or {"name": entry}
        theme["custom_json"] = _pbir_read_json(z, entry)
    return theme


def _extract_layout_pbir(z: zipfile.ZipFile, names: list[str], pbix: Path, has_datamodel: bool) -> dict:
    pages_meta = _pbir_read_json(z, "Report/definition/pages/pages.json")
    report_meta = _pbir_read_json(z, "Report/definition/report.json")
    page_order = pages_meta.get("pageOrder") or sorted({
        n.split("/")[3] for n in names
        if n.startswith("Report/definition/pages/") and n.count("/") >= 4 and not n.endswith("pages.json")
    })
    theme = _pbir_theme(z, names, report_meta)
    result = {
        "report": pbix.stem,
        "source": str(pbix),
        "layout_version": None,
        "has_embedded_datamodel": has_datamodel,
        "theme": theme,
        "custom_visual_packages": list(dict.fromkeys(
            _pbir_custom_packages(report_meta) + _zip_custom_visuals(names))),
        "pages": [_parse_page_pbir(z, names, pid) for pid in page_order],
        "format": "pbir",
    }
    resolve_theme_markers(result)
    embed_image_resources(z, result)
    return result


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
    grab("columns", lambda: df_records(m.schema))     # [{TableName, ColumnName, PandasDataType}]
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

def _md_escape(value: Any) -> str:
    """One cell's worth of text: pipes and newlines would otherwise break the table."""
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _md_table(rows: list[dict], columns: list[str] | None = None, limit: int = 200) -> list[str]:
    """Renders records as a markdown table using whichever keys they actually carry.

    Deliberately not a fixed column list: this data comes from pbixray, whose column
    names differ between versions and between model formats, and silently dropping a
    column because it was renamed upstream is worse than showing one we didn't expect."""
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return ["_none_", ""]
    if columns is None:
        columns = list(dict.fromkeys(k for r in rows for k in r))
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for r in rows[:limit]:
        out.append("| " + " | ".join(_md_escape(r.get(c)) for c in columns) + " |")
    if len(rows) > limit:
        out.append(f"| _… {len(rows) - limit} more_ |" + " |" * (len(columns) - 1))
    out.append("")
    return out


def _md_code_sections(rows: list[dict], name_keys: tuple[str, ...], body_key: str,
                      lang: str, heading: str) -> list[str]:
    """DAX and M don't belong in table cells — one fenced block each, titled by whatever
    name-ish keys the record has (e.g. TableName + Name)."""
    rows = [r for r in rows if isinstance(r, dict) and r.get(body_key)]
    if not rows:
        return []
    out = [f"## {heading}", ""]
    for r in rows:
        label = " · ".join(str(r[k]) for k in name_keys if r.get(k)) or "(unnamed)"
        out += [f"### {label}", "", f"```{lang}", str(r[body_key]).strip(), "```", ""]
    return out


def model_summary_markdown(model: dict, report: str) -> str:
    """The extracted data model as something a person can actually read: the DAX behind
    each measure, the relationships, the RLS rules, and each table's Power Query source.

    model.json is the machine-readable copy of the same thing; this is the one you send
    to someone, or read next to Power BI while writing the SQL. Both are offered in the
    panel for exactly that reason."""
    lines = [f"# Data model — {report}", "",
             "Extracted from the .pbix. This is a **read-only description** of the original "
             "Power BI model: what it measures, how its tables relate, and where its data "
             "comes from. Nothing here is executed.", ""]

    if model.get("error"):
        lines += ["## No model could be read", "",
                  f"`{model['error']}`", "",
                  "This usually means the report has no local data model — it connects live to a "
                  "published dataset. The report's visuals and layout still extract normally; only "
                  "the measures/relationships below are unavailable.", ""]
        if model.get("connection"):
            lines += ["Connection info found in the file:", "", "```json",
                      json.dumps(model["connection"], indent=2, default=str), "```", ""]
        return "\n".join(lines)

    modes = model.get("storage_modes") or {}
    if modes:
        pretty = {"0": "Import", "1": "DirectQuery", "2": "DirectQuery (push)"}
        lines += ["## Storage", "",
                  ", ".join(f"{n} partition(s) {pretty.get(str(k), f'mode {k}')}"
                            for k, n in modes.items()), ""]

    tables = model.get("tables")
    if isinstance(tables, list) and tables:
        lines += ["## Tables", "", *[f"- {t}" for t in tables], ""]

    for key, heading in (("relationships", "Relationships"),
                         ("rls", "Row-level security (RLS)"),
                         ("role_memberships", "Role memberships"),
                         ("partitions", "Partitions"),
                         ("datasources", "Data sources")):
        rows = model.get(key)
        if isinstance(rows, list) and rows:
            lines += [f"## {heading}", "", *_md_table(rows)]

    lines += _md_code_sections(model.get("measures") or [], ("TableName", "Name"),
                               "Expression", "dax", "Measures (DAX)")
    lines += _md_code_sections(model.get("calculated_columns") or [], ("TableName", "ColumnName", "Name"),
                               "Expression", "dax", "Calculated columns (DAX)")
    lines += _md_code_sections(model.get("calculated_tables") or [], ("Name", "TableName"),
                               "Expression", "dax", "Calculated tables (DAX)")
    lines += _md_code_sections(model.get("power_query") or [], ("TableName",),
                               "Expression", "text", "Power Query source (M)")
    return "\n".join(lines)


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
