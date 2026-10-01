"""Table header/banding colours (visual objects and theme), card text that must fit, and the default font."""
import re

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
    assert ex._style_with_fill({}, objects) == {"shape_kind": "line", "line_color": "#808080"}


def test_font_stack_reaches_the_css_unescaped_and_dropdown_sits_above_every_visual(fake_pbix):
    layout = ex.extract_layout(fake_pbix)
    html = render_html(layout, semantic.load("Executive_Dashboard"), {}, None, mode="live")
    assert "--font: 'Segoe UI'," in html and "&#39;" not in html.split("</style>")[0]
    assert ".sl-panel { position: fixed; z-index: 2147483000;" in html            # Power BI z values reach 20001
    assert resolve_theme({"custom_json": {"fontFamily": "x;}</style>"}})["font_family"] == _FONT_STACK


def test_card_value_size_units_and_decimals_are_read_and_rendered(fake_pbix):
    lit = lambda v: {"expr": {"Literal": {"Value": v}}}                                   # noqa: E731
    objects = {"labels": [{"properties": {"fontSize": lit("17D"), "labelDisplayUnits": lit("1D"), "labelPrecision": lit("0L")}}]}
    st = ex._style_with_fill({}, objects)
    assert (st["value_size"], st["value_units"], st["value_decimals"]) == (17.0, 1.0, 0.0)
    assert "value_size" not in ex._style_with_fill({}, {"labels": [{"properties": {"fontSize": lit("'big;}'")}}]})
    layout = ex.extract_layout(fake_pbix)
    base = layout["pages"][0]
    card = {**base["visuals"][0], "id": "c", "type": "card", "style": st}
    spec = semantic.load("Executive_Dashboard")
    html = render_html({**layout, "pages": [{**base, "visuals": [card]}]}, spec, {}, None, mode="live")
    assert "font-size:calc(22.7px * var(--scale, 1))" in html and 'data-w="' in html


def test_shape_style_reaches_the_page_as_px_and_a_rotated_line_is_not_transformed(fake_pbix):
    # pt -> px uses the same 4/3 factor as title_size/value_size; a rotated "line" shape keeps
    # its own colour/weight but is never CSS-rotated (see report.html.j2: a long, thin box
    # rotated around its own centre swings far outside that box — confirmed against a real
    # report where a 1280x23 header line rotated 90 degrees covered unrelated text far below).
    layout = ex.extract_layout(fake_pbix)
    base = layout["pages"][0]
    common = {**base["visuals"][0], "hidden": False, "is_group": False, "parent_group": None, "groups": [], "type": "shape"}
    box = {**common, "id": "box", "title": None,
          "style": {"shape_kind": "rectangle", "background": "#666666", "border": True,
                    "border_color": "#333333", "border_weight": 3, "round_edge": 6, "rotation": 15}}
    line = {**common, "id": "ln", "title": None,
           "style": {"shape_kind": "line", "line_color": "#F3753F", "line_weight": 6, "rotation": 90}}
    spec = semantic.load("Executive_Dashboard")
    for mode in ("live", "hah"):
        html = render_html({**layout, "pages": [{**base, "visuals": [box, line]}]}, spec, {}, None,
                           mode=mode, hah_base="https://hah.example")
        assert "border: 4.0px solid #333333" in html and "border-radius: 8.0px" in html and "rotate(15deg)" in html, mode
        assert "rotate(90deg)" not in html, mode                        # the line, not rotated
        assert '"line_weight": 8.0' in html                              # 6pt -> 8px, reaches the spec for the JS renderer


def test_table_total_row_is_drawn_from_the_block_total(fake_pbix, tmp_path):
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    from pbix2html.semantic import VisualSpec
    layout = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    base = layout["pages"][0]
    tab = {**base["visuals"][0], "id": "t", "type": "tableEx", "hidden": False, "is_group": False, "parent_group": None, "groups": [],
           "x": 20, "y": 20, "width": 400, "height": 200, "z": 1, "title": None,
           "style": {"table_total_bg": "#00233C", "table_total_fg": "#FFFFFF"}}
    spec.visuals = {"t": VisualSpec(id="t", kind="table", title=None, sql="select 1")}
    data = {"t": {"columns": ["a", "b"], "rows": [["x", 1], ["y", 2]], "total": ["Total", 3]}}
    html = tmp_path / "r.html"
    html.write_text(render_html({**layout, "pages": [{**base, "visuals": [tab]}]}, spec, {}, data, mode="snapshot"), encoding="utf-8")
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:  # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(html.as_uri())
        pg.wait_for_timeout(400)
        got = pg.evaluate("""() => { const td = [...document.querySelectorAll('#v-t tfoot td')];
            return [td.map(x => x.textContent), td.length ? getComputedStyle(td[0]).backgroundColor : null]; }""")
        br.close()
    assert got == [["Total", "3"], "rgb(0, 35, 60)"]


def test_month_categories_come_back_in_calendar_order_unless_the_report_sorts():
    from pathlib import Path
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    tpl = (Path(__file__).parent.parent / "src" / "pbix2html" / "templates" / "report.html.j2").read_text(encoding="utf-8")
    snippet = tpl[tpl.index("  const MONTHS = "):tpl.index("  const col = (block, name) =>")]
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:  # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page()
        pg.set_content("<html></html>")
        res = pg.evaluate("(src) => { const f = new Function(src + '; return inCalendarOrder;')(); return ["
                          "f({}, ['August 2026', 'June 2026', 'July 2026']),"
                          "f({}, ['Mar-25', 'Jan-25', 'Dec-24']),"
                          "f({}, ['March', 'January', 'February']),"
                          "f({ has_sort: true }, ['August 2026', 'June 2026']),"
                          "f({}, ['North', 'June 2026']),"
                          "f({}, ['June 2026', 'June 2026'])]; }", snippet)
        br.close()
    assert res[0] == ["June 2026", "July 2026", "August 2026"]
    assert res[1] == ["Dec-24", "Jan-25", "Mar-25"]
    assert res[2] == ["January", "February", "March"]
    assert res[3] == ["August 2026", "June 2026"]                      # the report sorts: untouched
    assert res[4] == ["North", "June 2026"] and res[5] == ["June 2026", "June 2026"]


def test_chart_tooltip_is_appended_to_body_above_every_visual(fake_pbix, tmp_path):
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    from pbix2html.semantic import VisualSpec
    layout = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    base = layout["pages"][0]
    chart = {**base["visuals"][0], "id": "c", "type": "clusteredColumnChart", "hidden": False, "is_group": False, "parent_group": None,
             "groups": [], "x": 20, "y": 20, "width": 400, "height": 200, "z": 1, "title": None, "style": {}}
    spec.visuals = {"c": VisualSpec(id="c", kind="column", title=None, sql="select 1")}
    data = {"c": {"columns": ["category", "value"], "rows": [["a", 1], ["b", 2]]}}
    html = tmp_path / "r.html"
    html.write_text(render_html({**layout, "pages": [{**base, "visuals": [chart]}]}, spec, {}, data, mode="snapshot"), encoding="utf-8")
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:  # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page()
        pg.route("**/echarts*", lambda r: r.abort())             # the stub below stands in for the library
        pg.add_init_script("window.echarts = { init: () => ({ setOption: o => { window.__tip = o.tooltip; }, resize() {} }) };")
        pg.goto(html.as_uri())
        pg.wait_for_timeout(300)
        tip = pg.evaluate("() => window.__tip")
        br.close()
    assert tip["appendToBody"] is True and "z-index:2147483600" in tip["extraCssText"]
    assert tip["trigger"] == "axis"                                  # the chart's own tooltip settings are kept


def test_natural_order_of_numeric_labels_and_the_report_sort_rules():
    from pathlib import Path
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    tpl = (Path(__file__).parent.parent / "src" / "pbix2html" / "templates" / "report.html.j2").read_text(encoding="utf-8")
    snippet = tpl[tpl.index("  const MONTHS = "):tpl.index("  const col = (block, name) =>")]
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:  # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page()
        pg.set_content("<html></html>")
        tenure = ["11-<16 Years", "0-<1 Year", "2-<4 Years", "25+ Years", "4-<6 Years", "1-<2 Years"]
        res = pg.evaluate("(src) => { const f = new Function(src + '; return inCalendarOrder;')(); const t = %s; return ["
                          "f({}, t), f({ has_sort: true, sort_by_category: true }, t), f({ has_sort: true }, t),"
                          "f({}, ['29 & under', '30-39', '40-49']), f({}, ['North', '2']), f({}, ['Sep 2017', '10 Oct'])]; }" % tenure, snippet)
        br.close()
    assert res[0] == ["0-<1 Year", "1-<2 Years", "2-<4 Years", "4-<6 Years", "11-<16 Years", "25+ Years"]
    assert res[1] == res[0]                                         # the report sorts by the label itself: a label sort, natural order is right
    assert res[2] == tenure                                          # sorted by something else (a value): untouched
    assert res[3] == ["29 & under", "30-39", "40-49"] and res[4] == ["North", "2"]


def test_default_container_background_button_layers_and_hah_total(fake_pbix):
    from pbix2html import semantic
    from pbix2html.render import render_html
    from pbix2html.semantic import VisualSpec
    layout = ex.extract_layout(fake_pbix)
    base = layout["pages"][0]
    common = {**base["visuals"][0], "hidden": False, "is_group": False, "parent_group": None, "groups": [], "z": 1}
    card_on = {**common, "id": "a", "type": "card", "style": {}}
    card_off = {**common, "id": "b", "type": "card", "style": {"background_off": True}}
    text = {**common, "id": "c", "type": "textbox", "style": {}}
    spec = semantic.load("Executive_Dashboard")
    spec.visuals = {"a": VisualSpec(id="a", kind="card", title=None, sql="select 1", sql_total="select 2")}
    html = render_html({**layout, "pages": [{**base, "visuals": [card_on, card_off, text]}]}, spec, {}, None, mode="live")
    import json as _json
    page = _json.loads(html.split('<script id="spec" type="application/json">')[1].split("</script>")[0])["pages"][0]["visuals"]
    by = {v["id"]: v["style"] for v in page}
    assert by["a"]["background"] == "#FFFFFF"                      # Power BI's own default for a data visual
    assert "background" not in by["b"] and "background" not in by["c"]      # switched off / a text box
    hah = render_html({**layout, "pages": [{**base, "visuals": [card_on]}]}, spec, {}, None, mode="hah", hah_base="https://h.example")
    assert '"sql_total": "select 2"' in hah


def test_icon_rule_names_map_to_round_badges():
    from pathlib import Path
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    tpl = (Path(__file__).parent.parent / "src" / "pbix2html" / "templates" / "report.html.j2").read_text(encoding="utf-8")
    snippet = tpl[tpl.index("  const iconOf = name"):tpl.index("  function condCell")]
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:  # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page()
        pg.set_content("<html></html>")
        res = pg.evaluate("(src) => { const f = new Function(src + '; return iconOf;')();"
                          "return ['SymbolHigh', 'CircleCheck', 'SymbolLow', 'CircleCross', 'Warning', 'null', 'Mystery'].map(n => { const r = f(n); return r && r[0]; }); }", snippet)
        br.close()
    assert res == ["ok", "ok", "bad", "bad", "warn", None, None]


def _one_page_report(width=1280, height=720, mode="snapshot"):
    from pbix2html.semantic import ReportSpec, VisualSpec
    card = {"id": "c1", "type": "card", "hidden": False, "is_group": False, "parent_group": None,
            "groups": [], "x": 40, "y": 40, "width": 300, "height": 150, "z": 1, "title": "Revenue",
            "style": {}, "sort": None, "cond_formats": [], "n_fields": None, "col_align": [],
            "y_fields": [], "action": None}
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02"]}},
              "pages": [{"display_name": "P", "width": width, "height": height, "visuals": [card]}]}
    spec = ReportSpec(report="T", source=None, connection="", delivery="", parameters={}, roles={},
                      visuals={"c1": VisualSpec(id="c1", kind="card", title=None, sql="s")}, raw={})
    return render_html(layout, spec, {}, {"c1": {"columns": ["value"], "rows": [[1234]]}},
                       mode=mode, hah_base="https://h.example")


@pytest.mark.parametrize("mode", ["snapshot", "live", "hah"])
def test_the_canvas_has_a_view_size_control_in_every_mode(mode):
    html = _one_page_report(mode=mode)
    assert 'id="view-mode"' in html and 'value="fit"' in html and 'value="width"' in html
    assert re.search(r'data-h="720(\.0)?"', html)   # the design height, for fitting the viewport
    # the on-screen zoom is a per-viewer choice, so it must not reach the printed page
    assert ".page { width: 100% !important" in html


def test_fit_page_never_runs_off_a_wide_but_short_screen():
    """The canvas keeps the .pbix's own proportions, so `width:100%` + aspect-ratio can only ever
    fit the *width*: on a wide-but-short monitor the bottom of the report was cut off, and on a
    narrow one everything was squeezed. "Fit page" sizes against the viewport height too."""
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "r.html"
    f.write_text(_one_page_report(), encoding="utf-8")
    measure = """() => { const p = document.querySelector('.page'), b = p.getBoundingClientRect();
        return {overflow: Math.round(b.bottom - window.innerHeight), w: Math.round(b.width)}; }"""
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:                                       # noqa: BLE001
            pytest.skip(str(e))
        for w, h in ((1920, 800), (1440, 900), (900, 600), (1100, 1400)):
            pg = br.new_page(viewport={"width": w, "height": h})
            pg.goto(f.as_uri())
            pg.wait_for_timeout(600)
            assert pg.eval_on_selector("#view-mode", "e => e.value") == "fit"   # the default
            assert pg.evaluate(measure)["overflow"] <= 1, f"fit page overflowed at {w}x{h}"
            # an explicit zoom is allowed to overflow — that is what the viewer asked for
            pg.select_option("#view-mode", "1.5")
            pg.wait_for_timeout(300)
            assert pg.evaluate(measure)["w"] == 1920                 # 1280 design px x 1.5
            pg.close()
        br.close()


def _two_page_report(mode="snapshot"):
    from pbix2html.semantic import ReportSpec, VisualSpec
    def card(i):
        return {"id": f"c{i}", "type": "card", "hidden": False, "is_group": False,
                "parent_group": None, "groups": [], "x": 40, "y": 40, "width": 300, "height": 150,
                "z": 1, "title": f"Card {i}", "style": {}, "sort": None, "cond_formats": [],
                "n_fields": None, "col_align": [], "y_fields": [], "action": None}
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02"]}}, "pages": [
        {"display_name": "Overview", "width": 1280, "height": 720, "visuals": [card(1)]},
        {"display_name": "Detail", "width": 1280, "height": 720, "visuals": [card(2)]}]}
    spec = ReportSpec(report="T", source=None, connection="", delivery="", parameters={}, roles={},
                      visuals={f"c{i}": VisualSpec(id=f"c{i}", kind="card", title=None, sql="s")
                               for i in (1, 2)}, raw={})
    data = {f"c{i}": {"columns": ["value"], "rows": [[1234]]} for i in (1, 2)}
    return render_html(layout, spec, {}, data, mode=mode, hah_base="https://h.example")


@pytest.mark.parametrize("mode", ["snapshot", "live", "hah"])
def test_the_tab_strip_can_be_moved_and_only_offers_it_when_there_are_tabs(mode):
    html = _two_page_report(mode)
    assert 'id="nav-pos"' in html and 'value="bottom"' in html and 'value="left"' in html
    assert '<main class="content">' in html and "</main>" in html
    # the attribute is `data-tabs`: `data-nav` already means "this button goes to page X"
    assert 'body[data-tabs="left"]' in html and 'body[data-tabs="bottom"]' in html
    # a one-page report has no tab strip, so it must not offer to move one
    assert 'id="nav-pos"' not in _one_page_report(mode=mode)


def test_the_tab_strip_actually_moves_and_the_canvas_still_fits():
    """Top/bottom re-order the flex column; left turns the strip into a rail beside the page,
    which costs *width* rather than height — so the fit has to stop counting it vertically."""
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "r.html"
    f.write_text(_two_page_report(), encoding="utf-8")
    probe = """() => { const n = document.querySelector('nav.tabs').getBoundingClientRect();
        const p = [...document.querySelectorAll('.page')].find(x => !x.hidden).getBoundingClientRect();
        return {navTop: n.top, navLeft: n.left, navW: n.width, pageTop: p.top, pageLeft: p.left,
                fits: p.bottom <= window.innerHeight + 1}; }"""
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:                                       # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page(viewport={"width": 1500, "height": 820})
        pg.goto(f.as_uri())
        pg.wait_for_timeout(600)
        assert pg.eval_on_selector("#nav-pos", "e => e.value") == "top"        # default
        pg.select_option("#nav-pos", "top")
        pg.wait_for_timeout(300)
        top = pg.evaluate(probe)
        assert top["navTop"] < top["pageTop"] and top["fits"]
        pg.select_option("#nav-pos", "bottom")
        pg.wait_for_timeout(300)
        bottom = pg.evaluate(probe)
        assert bottom["navTop"] > bottom["pageTop"], "bottom strip did not move below the page"
        assert bottom["fits"], "the canvas must still fit with the strip underneath"
        pg.select_option("#nav-pos", "left")
        pg.wait_for_timeout(300)
        left = pg.evaluate(probe)
        assert left["navLeft"] < left["pageLeft"] and left["navW"] < 300, "left strip is not a rail"
        assert left["fits"]
        # tabs keep working wherever the strip is
        pg.click("nav.tabs button:nth-child(2)")
        pg.wait_for_timeout(400)
        assert pg.evaluate("() => [...document.querySelectorAll('.page')].find(x => !x.hidden).id") == "page-1"
        br.close()
