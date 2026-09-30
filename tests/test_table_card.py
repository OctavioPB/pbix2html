"""Table header/banding colours (visual objects and theme), card text that must fit, and the default font."""
import pytest

from pbix2html import extract as ex, semantic
from pbix2html.render import _FONT_STACK, render_html, resolve_theme
from tests.test_verify import _browser


def _theme_ref(idx, pct):
    return {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": idx, "Percent": pct}}}}}


def test_table_style_reads_header_banding_and_matrix_row_headers():
    objects = {"columnHeaders": [{"properties": {"backColor": _theme_ref(2, 0.4), "fontColor": _theme_ref(0, 0)}}],
               "values": [{"properties": {"backColorPrimary": _theme_ref(0, 0), "backColorSecondary": _theme_ref(2, 0.6)}}],
               "rowHeaders": [{"properties": {"backColor": _theme_ref(2, 0.4)}}]}
    st = ex._table_style(objects)
    assert st["table_header_bg"] == "theme:2:0.4" and st["table_row_bg_alt"] == "theme:2:0.6"
    assert st["table_row_bg"] == "theme:0:0" and st["table_rowhdr_bg"] == "theme:2:0.4"
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02"]}},
              "pages": [{"visuals": [{"type": "tableEx", "style": st}]}]}
    ex.resolve_theme_markers(layout)
    assert layout["pages"][0]["visuals"][0]["style"]["table_row_bg_alt"] == "#FFBF9A"      # ColorId 2 = first data colour, 60 % lighter
    assert layout["pages"][0]["visuals"][0]["style"]["table_row_bg"] == "#FFFFFF"


def test_theme_visual_styles_fill_what_the_table_leaves_unset():
    theme = {"custom_json": {"dataColors": ["#FF5F02"], "visualStyles": {"tableEx": {"*": {
        "columnHeaders": [{"backColor": {"solid": {"color": "#FF9F67"}}}],
        "values": [{"backColorPrimary": {"solid": {"color": "#FFFFFF"}}, "backColorSecondary": {"solid": {"color": "#FFBF9A"}}}]}}}}}
    own = {"type": "tableEx", "style": {"table_header_bg": "#111111"}}          # its own header wins
    layout = {"theme": theme, "pages": [{"visuals": [own, {"type": "tableEx", "style": {}}, {"type": "card", "style": {}}]}]}
    ex.apply_theme_table_styles(layout)
    a, b, c = layout["pages"][0]["visuals"]
    assert a["style"]["table_header_bg"] == "#111111" and a["style"]["table_row_bg_alt"] == "#FFBF9A"
    assert b["style"] == {"table_header_bg": "#FF9F67", "table_row_bg": "#FFFFFF", "table_row_bg_alt": "#FFBF9A"}
    assert c["style"] == {}


def test_default_font_is_segoe_ui_and_a_theme_face_keeps_it_as_fallback():
    assert resolve_theme(None)["font_family"].startswith("'Segoe UI'")
    assert resolve_theme({"custom_json": {"fontFamily": "Georgia"}})["font_family"] == f"'Georgia', {_FONT_STACK}"
    assert resolve_theme({"custom_json": {"fontFamily": "Segoe UI Light"}})["font_family"] == _FONT_STACK


def test_card_title_and_number_shrink_to_fit_and_table_is_banded(fake_pbix, tmp_path):
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    layout = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    base = layout["pages"][0]
    common = {**base["visuals"][0], "hidden": False, "is_group": False, "parent_group": None, "groups": []}
    card = {**common, "id": "c", "type": "card", "x": 20, "y": 20, "width": 150, "height": 80, "z": 1, "title": "Average headcount for the whole period",
            "style": {}}
    tab = {**common, "id": "t", "type": "tableEx", "x": 200, "y": 20, "width": 400, "height": 200, "z": 2, "title": None,
           "style": {"table_header_bg": "#FF9F67", "table_row_bg": "#FFFFFF", "table_row_bg_alt": "#FFBF9A"}}
    layout2 = {**layout, "pages": [{**base, "visuals": [card, tab]}]}
    from pbix2html.semantic import VisualSpec
    spec.visuals = {"c": VisualSpec(id="c", kind="card", title=None, sql="select 1"), "t": VisualSpec(id="t", kind="table", title=None, sql="select 1")}
    data = {"c": {"columns": ["value"], "rows": [[123456789012.5]]},
            "t": {"columns": ["a", "b"], "rows": [["x", 1], ["y", 2], ["z", 3]]}}
    html = tmp_path / "r.html"
    html.write_text(render_html(layout2, spec, {}, data, mode="snapshot"), encoding="utf-8")
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:  # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(html.as_uri())
        pg.wait_for_timeout(500)
        fit = pg.evaluate("""() => [...document.querySelectorAll('#v-c .title, #v-c .value')].map(n => n.scrollWidth <= n.clientWidth + 1 && n.scrollHeight <= n.clientHeight + 1)""")
        rows = pg.evaluate("""() => [...document.querySelectorAll('#v-t tbody tr')].map(r => getComputedStyle(r).backgroundColor)
                              .concat(getComputedStyle(document.querySelector('#v-t th')).backgroundColor)""")
        br.close()
    assert fit and all(fit)
    assert rows == ["rgb(255, 255, 255)", "rgb(255, 191, 154)", "rgb(255, 255, 255)", "rgb(255, 159, 103)"]


def test_background_switched_off_is_not_drawn_and_line_shapes_are_rules():
    lit = lambda v: {"expr": {"Literal": {"Value": v}}}                                   # noqa: E731
    vco = {"background": [{"properties": {"show": lit("false"), "transparency": lit("50D"),
                                          "color": {"solid": {"color": lit("'#000000'")}}}}]}
    assert "background" not in ex.container_style(vco) and "transparency" not in ex.container_style(vco)
    on = {"background": [{"properties": {"show": lit("true"), "color": {"solid": {"color": lit("'#123456'")}}}}]}
    assert ex.container_style(on)["background"] == "#123456"
    objects = {"shape": [{"properties": {"tileShape": lit("'line'")}}],
               "fill": [{"selector": {"id": "default"}, "properties": {"fillColor": {"solid": {"color": lit("'#808080'")}}}}]}
    assert ex._style_with_fill({}, objects) == {"background": "#808080", "line": True}


def test_font_stack_reaches_the_css_unescaped_and_dropdown_sits_above_every_visual(fake_pbix):
    layout = ex.extract_layout(fake_pbix)
    html = render_html(layout, semantic.load("Executive_Dashboard"), {}, None, mode="live")
    assert "--font: 'Segoe UI'," in html and "&#39;" not in html.split("</style>")[0]
    assert ".sl-panel { position: fixed; z-index: 2147483000;" in html            # Power BI z values reach 20001
    assert resolve_theme({"custom_json": {"fontFamily": "x;}</style>"}})["font_family"] == _FONT_STACK
