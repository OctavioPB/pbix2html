"""
Render: layout.json + metrics yaml + data → Report.html (self-contained).

Python decides the layout, theme, and which `kind` each visual is; the template's JS draws
with ECharts from `spec` + `data`. That way the same HTML works for both snapshot and live.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from . import semantic
from .semantic import KIND_MAP, ReportSpec

log = logging.getLogger(__name__)
TEMPLATES = Path(__file__).parent / "templates"
_SLICER_JS = (TEMPLATES / "slicer.js").read_text(encoding="utf-8") if (TEMPLATES / "slicer.js").exists() else ""

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
    face = cj.get("fontFamily") \
        or ((cj.get("textClasses") or {}).get("title") or {}).get("fontFace") \
        or ((cj.get("textClasses") or {}).get("label") or {}).get("fontFace")
    if face and not face.lower().startswith("segoe"):
        t["font_family"] = f"'{face}', system-ui, sans-serif"
    return t


def _text_of(visual: dict) -> str | None:
    """Text of a textbox (objects.general.paragraphs) if available in the layout."""
    return visual.get("text")


# Power BI's page-image scaling → CSS. "Normal" is assumed to keep the aspect ratio
# (contain), "Fit" to stretch to the canvas and "Fill" to cover it; identical when the
# picture has the page's own aspect ratio, which designed backgrounds normally do.
_BG_SIZE = {"Normal": "contain", "Fit": "100% 100%", "Fill": "cover"}


def _background_image_css(bg: dict | None) -> str | None:
    uri = (bg or {}).get("data_uri")
    if not uri or not re.fullmatch(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]+", uri):
        return None
    size = _BG_SIZE.get(bg.get("scaling"), "contain")
    return f"url({uri}) center / {size} no-repeat"


_HEX6 = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FLEX = {"left": "flex-start", "center": "center", "right": "flex-end",
         "top": "flex-start", "middle": "center", "bottom": "flex-end"}


def _rgba(color: str | None, transparency: float | None) -> str | None:
    """'#RRGGBB' + Power BI transparency (0-100) → a CSS colour; None if not a plain hex."""
    if not color or not _HEX6.match(color):
        return None
    alpha = 1 - min(max(transparency or 0, 0), 100) / 100
    if alpha >= 1:
        return color.upper()
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha:.2f})"


def _rgb(color: str | None) -> tuple[int, int, int, float] | None:
    """'#RRGGBB' / 'rgba(r,g,b,a)' → (r, g, b, alpha); None for anything else."""
    if not color:
        return None
    if _HEX6.match(color):
        return (*(int(color[i:i + 2], 16) for i in (1, 3, 5)), 1.0)
    m = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", color.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))) if m else None


def _luminance(rgb: tuple[int, int, int, float]) -> float:
    def lin(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2])


def _contrast(a: str, b: str) -> float | None:
    """WCAG contrast ratio of two colours (1-21); None if either isn't a plain colour."""
    ra, rb = _rgb(a), _rgb(b)
    if ra is None or rb is None:
        return None
    hi, lo = sorted((_luminance(ra), _luminance(rb)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _backdrop(v: dict, visuals: list[dict], page_bg: str | None, theme: dict) -> str:
    """The colour a visual's text actually sits on: its own opaque fill, else the fill of the highest
    visual under its centre (a dark panel drawn behind a slicer), else the page, else the theme."""
    def opaque(style: dict) -> str | None:
        c = style.get("background")
        return c if c and (style.get("transparency") or 0) < 50 and _rgb(c) else None

    own = opaque(v.get("style") or {})
    if own:
        return own
    cx, cy = (v.get("x") or 0) + (v.get("width") or 0) / 2, (v.get("y") or 0) + (v.get("height") or 0) / 2
    z = v.get("z") or 0
    under = [w for w in visuals if w is not v and not w.get("is_group") and (w.get("z") or 0) <= z
             and w.get("x") is not None and w["x"] <= cx <= w["x"] + (w.get("width") or 0)
             and w.get("y") is not None and w["y"] <= cy <= w["y"] + (w.get("height") or 0)
             and opaque(w.get("style") or {})]
    if under:
        return opaque(max(under, key=lambda w: w.get("z") or 0)["style"])       # type: ignore[arg-type]
    return page_bg if page_bg and _rgb(page_bg) else theme["background"]


def _readable_fg(backdrop: str, theme: dict) -> str | None:
    """A text colour that reads on `backdrop`, or None when the theme's foreground already does."""
    fg = theme["foreground"]
    if (_contrast(fg, backdrop) or 21) >= 3:
        return None
    candidates = [theme["background"], "#FFFFFF", "#000000"]
    return max(candidates, key=lambda c: _contrast(c, backdrop) or 0)


def _title_css(style: dict, readable: str | None) -> str:
    """Inline CSS for a visual's title: what the report set (colour, size, weight, alignment), else a
    colour that reads on what is behind it."""
    parts = []
    color = style.get("title_color") or readable
    if color and _rgb(color):
        parts.append(f"color:{color}")
    if style.get("title_size"):
        parts.append(f"font-size:{round(style['title_size'] * 4 / 3, 1)}px")
    if style.get("title_bold") is not None:
        parts.append(f"font-weight:{700 if style['title_bold'] else 400}")
    if style.get("title_align"):
        parts.append(f"text-align:{style['title_align']}")
    return ";".join(parts)


def _merged_state(states: dict, name: str) -> dict:
    """`default` overlaid with the named state, card by card (Power BI stores only the
    properties a state changes)."""
    out: dict = {}
    for src in (states.get("default") or {}, states.get(name) or {} if name != "default" else {}):
        for k, v in src.items():
            out[k] = {**out.get(k, {}), **v} if isinstance(v, dict) else v
    return out


def _button_css(button: dict | None) -> str | None:
    """A button's stored formatting as CSS custom properties for the template's `.btn` rules
    (default plus hover / pressed / disabled). Every value is rebuilt from validated pieces
    (hex colours, numbers, a fixed alignment table), never copied from the file."""
    if not button:
        return None
    states, hidden = button.get("states") or {}, set(button.get("hidden") or [])
    css: dict[str, str] = {}

    def fill(m: dict) -> str:
        f = m.get("fill") or {}
        return "transparent" if "fill" in hidden else (_rgba(f.get("color"), f.get("transparency")) or "transparent")

    def outline(m: dict) -> str:
        o = m.get("outline") or {}
        c = _rgba(o.get("color"), o.get("transparency"))
        if "outline" in hidden or not c:
            return "none"
        return f"inset 0 0 0 {min(max(o.get('weight') or 1, 0), 20):g}px {c}"

    base = _merged_state(states, "default")
    css["--bg0"], css["--ol"] = fill(base), outline(base)
    t = base.get("text") or {}
    if t.get("color") and _HEX6.match(t["color"]):
        css["--fg"] = t["color"]
    if t.get("size"):
        css["--fs"] = f"{min(max(t['size'], 4), 96):g}pt"
    if t.get("bold"):
        css["--fw"] = "bold"
    if t.get("italic"):
        css["--fi"] = "italic"
    if t.get("underline"):
        css["--td"] = "underline"
    font = re.split(r"[,]", str(t.get("font") or ""))[0].strip().strip("'\"")
    if font and re.fullmatch(r"[\w .-]{1,60}", font):
        css["--ff"] = f"'{font}', system-ui, sans-serif"
    if t.get("align") in _FLEX:
        css["--jc"] = _FLEX[t["align"]]
        css["--ta"] = t["align"]
    if t.get("valign") in _FLEX:
        css["--ai"] = _FLEX[t["valign"]]
    if isinstance(base.get("round"), (int, float)):
        css["--rad"] = f"{min(max(base['round'], 0), 200):g}px"
    for suffix, name in (("h", "hover"), ("p", "pressed"), ("d", "disabled")):
        if name not in states:
            continue
        m = _merged_state(states, name)
        css[f"--bg-{suffix}"], css[f"--ol-{suffix}"] = fill(m), outline(m)
        c = (m.get("text") or {}).get("color")
        if c and _HEX6.match(c):
            css[f"--fg-{suffix}"] = c
    return ";".join(f"{k}:{v}" for k, v in css.items())


def _slicer_entry(v: dict, page_name: str | None, spec: ReportSpec, include_sql: bool) -> dict | None:
    """What the template needs to draw a slicer widget: its mode, the parameters it drives (one per
    hierarchy level, or one per range bound) and, for `hah`, the SQL of its values. None when the
    slicer drives no parameter (e.g. a relative-date slicer, which has no widget yet)."""
    d = semantic.slicer_descriptor(v)
    names = semantic.slicer_params(v, page_name or "", spec.parameters)
    if not names or d.get("mode") in ("relative", "other", "tile"):
        return None
    params = spec.parameters
    out = {"mode": d.get("mode"), "params": names, "single": bool(d.get("single")),
           "select_all": d.get("select_all", True) is not False,
           "levels": [(params.get(n) or {}).get("label") or n for n in names],
           "dtypes": [(params.get(n) or {}).get("dtype") or "text" for n in names],
           "bounds": [(params.get(n) or {}).get("bound") for n in names],
           "style": {k: d["style"][k] for k in ("color", "background", "size")
                     if isinstance(d.get("style"), dict) and d["style"].get(k) is not None}}
    if include_sql:
        out["options_sql"] = ((spec.raw.get("slicers") or {}).get(v["id"]) or {}).get("options_sql")
    return out


def _group_chain(v: dict, by_id: dict) -> list[str]:
    """Ids of the groups a visual sits in, nearest first (cycles ignored)."""
    chain: list[str] = []
    cur = by_id.get(v.get("parent_group"))
    while cur is not None and cur.get("id") not in chain:
        chain.append(cur["id"])
        cur = by_id.get(cur.get("parent_group"))
    return chain


def _unique_group_names(visuals: list[dict]) -> dict[str, str]:
    """{group displayName: id} for the names that identify exactly one group on the page."""
    seen: dict[str, list[str]] = {}
    for v in visuals:
        if v.get("is_group") and v.get("title"):
            seen.setdefault(v["title"], []).append(v["id"])
    return {n: ids[0] for n, ids in seen.items() if len(ids) == 1}


def _bookmark_action(action: dict | None, bookmarks: dict, page: dict, all_pages: list[dict],
                     warnings: list[str]) -> dict | None:
    """A bookmark button as {"type": "bookmark", "set": {groupId: hidden}} in *this page's*
    group ids, or None (inert) when nothing in the bookmark maps onto the page.

    Two things make this more than a lookup (see ADR-005):
    - `applyOnlyToTargetVisuals`: only groups listed in the bookmark's targets change, which
      is what lets one page host several independent switchers.
    - Bookmarks are bound to the page they were saved on. A page that is a structural clone
      of that page (same group display names, new ids: how a "monthly" twin of a
      "historical" page is built) reuses them; the owner confirmed those buttons are meant to
      work, so groups are matched by display name, only when the name is unique on both
      pages, and each such use is reported through `warnings`."""
    if not action or action.get("type") != "bookmark" or not action.get("enabled"):
        return None
    bm = bookmarks.get(action.get("bookmark"))
    if not bm:
        return None
    here = {v["id"] for v in page["visuals"] if v.get("is_group")}
    src = next((q for q in all_pages if q.get("name") == bm.get("page")), None)
    src_names = ({v["id"]: v["title"] for v in src["visuals"] if v.get("is_group") and v.get("title")}
                 if src else {})
    here_by_name = _unique_group_names(page["visuals"])
    src_unique = _unique_group_names(src["visuals"]) if src else {}
    targets = set(bm.get("targets") or [])
    changes: dict[str, bool] = {}
    remapped = False
    for gid, hidden in (bm.get("groups") or {}).items():
        if bm.get("apply_only_to_targets") and gid not in targets:
            continue
        if gid in here:
            changes[gid] = hidden
            continue
        name = src_names.get(gid)
        tgt = here_by_name.get(name) if name and src_unique.get(name) == gid else None
        if tgt:
            changes[tgt] = hidden
            remapped = True
    if not changes:
        return None
    if remapped:
        warnings.append(f"page {page.get('display_name')!r}: bookmark {bm.get('name')!r} was saved on "
                        f"page {src.get('display_name') if src else '?'!r}; applied by group name")
    return {"type": "bookmark", "set": changes}


def _page_action(action: dict | None, by_name: dict, rendered: set) -> dict | None:
    """A page-navigation button that lands on a page we render; anything else (a
    bookmark, a disabled link, a target that doesn't exist) is left inert."""
    if not action or action.get("type") != "page" or not action.get("enabled"):
        return None
    j = by_name.get(action.get("page"))
    return {"type": "page", "target": f"page-{j}"} if j in rendered else None


def build_spec(layout: dict, spec: ReportSpec, values: dict[str, Any], include_hidden: bool = False,
               include_sql: bool = False) -> dict:
    """Structure consumed by the template/JS: pages → visuals with position in % and kind.

    `include_sql` (mode="hah" only, see ADR-004) additionally embeds each visual's raw
    `sql`/`params` — HAH has no server of ours to fetch data from, so the client has to
    run the query itself.
    """
    theme = resolve_theme(layout.get("theme"))
    pages = []
    all_pages = layout["pages"]
    bookmarks = {b["id"]: b for b in layout.get("bookmarks") or []}
    warnings: list[str] = []
    # Hidden pages that a visible page's button navigates to (transitively) are part of
    # the report, e.g. a "Historic Data" view behind a toggle button. Tooltip pages and
    # other hidden pages nobody links to stay out.
    shown = {i for i, p in enumerate(all_pages) if not p.get("hidden") or include_hidden}
    by_name = {p.get("name"): i for i, p in enumerate(all_pages) if p.get("name")}
    nav_only: set[int] = set()
    queue = list(shown)
    while queue:
        for v in all_pages[queue.pop()]["visuals"]:
            a = v.get("action") or {}
            j = by_name.get(a.get("page")) if a.get("type") == "page" and a.get("enabled") else None
            if j is not None and j not in shown and j not in nav_only:
                nav_only.add(j)
                queue.append(j)
    for i, p in enumerate(all_pages):
        if i not in shown and i not in nav_only:
            continue
        W, H = float(p.get("width") or 1280), float(p.get("height") or 720)
        visuals = []
        by_id = {v.get("id"): v for v in p["visuals"]}
        # Groups that start hidden; their descendants are kept in the page (a bookmark
        # button can reveal them) and shown/hidden client-side by group chain.
        hidden_groups = [v["id"] for v in p["visuals"] if v.get("is_group") and v.get("hidden")]
        for v in p["visuals"]:
            if v.get("is_group") or v.get("hidden"):
                continue
            vs = spec.visuals.get(v["id"])
            kind = vs.kind if vs else KIND_MAP.get(v["type"], "unsupported")
            slicer = None
            if kind == "slicer":
                slicer = _slicer_entry(v, p.get("display_name"), spec, include_sql)
                if slicer is None:
                    continue    # a mode with no widget (relative dates...): nothing to draw
            r = (spec.raw.get("visuals") or {}).get(v["id"]) or {}
            entry = {
                "id": v["id"], "kind": kind, "type": v["type"],
                "title": (vs.title if vs and vs.title else v.get("title")),
                "left": round(100 * (v["x"] or 0) / W, 3), "top": round(100 * (v["y"] or 0) / H, 3),
                "w": round(100 * (v["width"] or 0) / W, 3), "h": round(100 * (v["height"] or 0) / H, 3),
                "z": int(v.get("z") or 0),     # CSS z-index must be an integer: "3000.0" is dropped, layering lost
                "format": (vs.format if vs else {}), "headers": r.get("headers") or {},
                "stacked": "stacked" in v["type"].lower(),
                "percent": v["type"].lower().startswith("hundredpercent"),
                "area": "area" in v["type"].lower(),
                "inner_radius": v["type"] == "donutChart", "axis": r.get("axis") or {},
                "text": _text_of(v),
                "image": v.get("image_data_uri"),
                "action": (_page_action(v.get("action"), by_name, shown | nav_only)
                           or _bookmark_action(v.get("action"), bookmarks, p, all_pages, warnings)),
                "groups": _group_chain(v, by_id),
                "btn_css": _button_css(v.get("button")),
                "btn_off": bool((v.get("action") or {}).get("enabled") is False
                                and "disabled" in ((v.get("button") or {}).get("states") or {})),
                # subtitle / button label / axis + legend titles, as the report sets
                # them (see extract.py's visual_text). Absent keys mean "not set" —
                # the renderer shows nothing rather than inventing a label.
                "texts": v.get("texts") or {},
                # {} when the .pbix says nothing about the frame — the template then
                # leaves its own default in place instead of inventing a border.
                "style": v.get("style") or {},
            }
            # a fill Power BI shows at 100 % transparency is not drawn; a partly transparent one is rgba
            st = dict(entry["style"])
            if st.get("background") and st.get("transparency") is not None:
                st["background"] = _rgba(st["background"], st["transparency"]) if st["transparency"] < 100 else None
                if not st["background"]:
                    st.pop("background")
            entry["style"] = st
            readable = _readable_fg(_backdrop(v, p["visuals"], p.get("background"), theme), theme)
            entry["title_css"] = _title_css(v.get("style") or {}, readable)
            entry["fg"] = readable                      # default text colour of the visual's own content (slicer widget)
            entry["start_hidden"] = any(g in hidden_groups for g in entry["groups"])
            entry["params"] = list(vs.params) if vs else []      # the parameters this visual's SQL uses
            if kind == "tooltip":       # native tooltip: the header, then the text (the icon has no title)
                entry["title"] = None
                entry["tooltip"] = "\n".join(x for x in (vs.title if vs else None, r.get("text")) if x) or None
            if slicer:
                entry["slicer"] = slicer
            if include_sql:
                entry["sql"] = vs.sql if vs else None
            visuals.append(entry)
        pages.append({"id": f"page-{i}", "name": p.get("display_name") or f"Page {i + 1}",
                      "width": W, "height": H, "background": p.get("background"),
                      "background_image": _background_image_css(p.get("background_image")),
                      "nav_only": i in nav_only, "hidden_groups": hidden_groups,
                      "visuals": visuals})
    # the page that is on screen first: the first one that has a tab
    for pg in pages:
        pg["initial"] = False
    next((pg for pg in pages if not pg["nav_only"]), pages[0] if pages else {}).update(initial=True)
    for w in dict.fromkeys(warnings):
        log.warning(w)
    with_widget = {n for pg in pages for vv in pg["visuals"] if vv.get("slicer") for n in vv["slicer"]["params"]}
    rendered = {pg["name"] for pg in pages}
    parameters = {}
    for name, p in spec.parameters.items():
        p = p or {}
        # shown in the top bar only when nothing else edits it: no widget, and not a parameter that
        # belongs to pages this report doesn't show
        on_page = not p.get("pages") or any(n in rendered for n in p["pages"])
        parameters[name] = {"label": p.get("label") or name, "value": values.get(name),
                            "multi": bool(p.get("multi")), "widget": name in with_widget or not on_page}
    return {"report": spec.report, "theme": theme, "pages": pages, "parameters": parameters}


def render_html(layout: dict, spec: ReportSpec, values: dict[str, Any], data: dict[str, dict] | None,
                mode: str = "snapshot", role: str | None = None, include_hidden: bool = False,
                hah_base: str | None = None, slicer_data: dict[str, dict] | None = None) -> str:
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
            slicer_js=_SLICER_JS,
        )

    tpl = env.get_template("report.html.j2")
    page_spec = build_spec(layout, spec, values, include_hidden)
    return tpl.render(
        spec=page_spec, theme=page_spec["theme"], mode=mode, role=role, generated_at=generated_at,
        echarts_cdn=settings.echarts_cdn, api_base=settings.api_base,
        spec_json=json.dumps(page_spec, ensure_ascii=False).replace("</", "<\\/"),
        data_json=json.dumps(data or {}, ensure_ascii=False, default=str).replace("</", "<\\/"),
        slicer_json=json.dumps(slicer_data or {}, ensure_ascii=False, default=str).replace("</", "<\\/"),
        slicer_js=_SLICER_JS,
    )
