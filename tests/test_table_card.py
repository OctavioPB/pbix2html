"""Table header/banding colours (visual objects and theme), card text that must fit, and the default font."""
import re
from pathlib import Path

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


def _label_report(report_labels=True, mode="snapshot"):
    from pbix2html.semantic import ReportSpec, VisualSpec
    v = {"id": "v0", "type": "columnChart", "hidden": False, "is_group": False,
         "parent_group": None, "groups": [], "x": 20, "y": 40, "width": 600, "height": 300,
         "z": 1, "title": "Chart", "style": {"labels": report_labels, "labels_precision": 0},
         "sort": None, "cond_formats": [], "n_fields": None, "col_align": [], "y_fields": [],
         "action": None}
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02"]}},
              "pages": [{"display_name": "P", "width": 1280, "height": 400, "visuals": [v]}]}
    spec = ReportSpec(report="T", source=None, connection="", delivery="", parameters={},
                      roles={}, visuals={"v0": VisualSpec(id="v0", kind="column", title=None,
                                                          sql="s")}, raw={})
    data = {"v0": {"columns": ["category", "value"], "rows": [[f"M{m}", 1234.5 + m] for m in range(6)]}}
    return render_html(layout, spec, {}, data, mode=mode, hah_base="https://h.example")


@pytest.mark.parametrize("mode", ["snapshot", "live", "hah"])
def test_a_data_label_control_exists_and_the_formatter_is_always_built(mode):
    html = _label_report(mode=mode)
    assert 'id="label-mode"' in html
    for value in ('value="report"', 'value="on"', 'value="off"'):
        assert value in html
    # the label object is built even when the report hides them, so forcing them on keeps the
    # report's own formatter rather than dumping raw numbers on the chart
    assert "show: !!cs.labels" in html and "const chartOptions = {}" in html


def test_data_labels_can_be_hidden_or_forced_without_losing_the_reports_formatting():
    """A dense time series draws one number per point on top of itself. The viewer can hide them
    (or force them on) without re-querying: only `series[].label.show` is flipped, on the option
    the chart was built from."""
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    import tempfile
    from pathlib import Path
    probe = """() => { const c = echarts.getInstanceByDom(document.querySelector('#v-v0 .chart'));
        const s = c.getOption().series[0];
        return {show: s.label.show === true, formatter: typeof s.label.formatter}; }"""
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:                                       # noqa: BLE001
            pytest.skip(str(e))
        for report_labels in (True, False):
            f = Path(tempfile.mkdtemp()) / "r.html"
            f.write_text(_label_report(report_labels), encoding="utf-8")
            errors = []
            pg = br.new_page(viewport={"width": 1300, "height": 600})
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f.as_uri())
            pg.wait_for_timeout(1200)
            assert pg.eval_on_selector("#label-mode", "e => e.value") == "report"   # default
            # "As report" leaves each chart exactly as the .pbix set it
            assert pg.evaluate(probe)["show"] is report_labels
            pg.select_option("#label-mode", "off")
            pg.wait_for_timeout(400)
            assert pg.evaluate(probe)["show"] is False
            pg.select_option("#label-mode", "on")
            pg.wait_for_timeout(400)
            forced = pg.evaluate(probe)
            assert forced["show"] is True
            assert forced["formatter"] == "function", "forcing labels on lost the report's formatter"
            assert not errors, errors[:2]
            pg.close()
        br.close()


@pytest.mark.parametrize("mode", ["snapshot", "live", "hah"])
def test_text_inside_the_canvas_scales_with_the_page_but_the_chrome_does_not(mode):
    html = _label_report(mode=mode)
    # a Power BI size is absolute at the design width, so every size inside the page is a
    # multiple of --scale; the fixed clamp ceilings used to stop a callout growing on a big screen
    for rule in (".card .value { flex: none; font-size: clamp(calc(1rem * var(--scale, 1)), 20cqmin,",
                 ".visual .title { font-size: clamp(calc(.65rem * var(--scale, 1)), 8cqmin,",
                 "table { border-collapse: collapse; width: 100%; font-size: calc(.8rem * var(--scale, 1)); }"):
        assert rule in html, rule
    # an axis label is text inside a canvas, which --scale cannot reach: it is scaled in JS, so
    # its size has to be spelled out rather than left to ECharts' default
    assert "axisLabel: { color: theme.muted, fontSize: 12 }" in html
    assert "const scaleFonts = (o, k) =>" in html
    # the page chrome is not part of the report and must keep its own size
    assert re.search(r"header \.meta \{ color: [^;]+; font-size: \.8rem; \}", html)


def test_a_callout_keeps_its_proportion_at_every_zoom_and_chart_text_follows():
    """A scorecard's number is the thing people read from across a room. It used to stop growing
    at the `2.4rem` clamp ceiling, so on a large monitor it shrank *relative to its card*; chart
    text never scaled at all, because CSS cannot reach inside a canvas."""
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    import tempfile
    from pathlib import Path
    from pbix2html.semantic import ReportSpec, VisualSpec
    card = {"id": "c1", "type": "card", "hidden": False, "is_group": False, "parent_group": None,
            "groups": [], "x": 40, "y": 40, "width": 220, "height": 110, "z": 1, "title": "Headcount",
            "style": {}, "sort": None, "cond_formats": [], "n_fields": None, "col_align": [],
            "y_fields": [], "action": None}
    chart = {**card, "id": "v0", "type": "columnChart", "x": 300, "width": 600, "height": 300,
             "title": "Chart", "style": {"labels": True}}
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02"]}},
              "pages": [{"display_name": "P", "width": 1280, "height": 500, "visuals": [card, chart]}]}
    spec = ReportSpec(report="T", source=None, connection="", delivery="", parameters={}, roles={},
                      visuals={"c1": VisualSpec(id="c1", kind="card", title=None, sql="s"),
                               "v0": VisualSpec(id="v0", kind="column", title=None, sql="s")}, raw={})
    data = {"c1": {"columns": ["value"], "rows": [[4804]]},
            "v0": {"columns": ["category", "value"], "rows": [[f"M{m}", 100 + m] for m in range(6)]}}
    f = Path(tempfile.mkdtemp()) / "r.html"
    f.write_text(render_html(layout, spec, {}, data, mode="snapshot"), encoding="utf-8")
    probe = """() => { const box = document.querySelector('#v-c1').getBoundingClientRect();
        const val = document.querySelector('#v-c1 .value');
        const c = echarts.getInstanceByDom(document.querySelector('#v-v0 .chart'));
        return {ratio: parseFloat(getComputedStyle(val).fontSize) / box.width,
                clipped: val.scrollWidth > val.clientWidth + 1,
                axis: c.getOption().xAxis[0].axisLabel.fontSize}; }"""
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:                                       # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page(viewport={"width": 2400, "height": 1300})
        pg.goto(f.as_uri())
        pg.wait_for_timeout(1200)
        seen = {}
        for mode in ("0.5", "1", "1.5", "fit"):
            pg.select_option("#view-mode", mode)
            pg.wait_for_timeout(450)
            seen[mode] = pg.evaluate(probe)
        br.close()
    ratios = [r["ratio"] for r in seen.values()]
    assert max(ratios) - min(ratios) < 0.005, f"callout lost its proportion: {seen}"
    assert not any(r["clipped"] for r in seen.values()), seen
    # chart text follows the page: bigger at 150 % than at 50 %, and never below the 6px floor
    assert seen["1.5"]["axis"] > seen["1"]["axis"] > seen["0.5"]["axis"] >= 6, seen


_ALL_KINDS = [("card", "card"), ("kpi", "kpi"), ("multicard", "multiRowCard"),
              ("table", "tableEx"), ("matrix", "matrix"), ("column", "columnChart"),
              ("bar", "clusteredBarChart"), ("line", "lineChart"), ("pie", "pieChart"),
              ("gauge", "gauge"), ("text", "textbox"), ("slicer", "slicer"),
              ("tooltip", "dynamicTooltip"), ("static", "shape")]


def _every_kind_report():
    from pbix2html.semantic import ReportSpec, VisualSpec
    vis, specs, data = [], {}, {}
    for i, (kind, vtype) in enumerate(_ALL_KINDS):
        vid = f"x{i}"
        vis.append({"id": vid, "type": vtype, "hidden": False, "is_group": False,
                    "parent_group": None, "groups": [], "x": 20 + (i % 5) * 250,
                    "y": 20 + (i // 5) * 220, "width": 230, "height": 200, "z": 1,
                    "title": f"{kind} title", "style": {"labels": True}, "sort": None,
                    "cond_formats": [], "n_fields": None, "col_align": [], "y_fields": [],
                    "action": None,
                    "text": "<p style='font-size:11pt'>a text box</p>" if kind == "text" else None,
                    "fields": ["D.n"] if kind == "slicer" else [],
                    "slicer": {"mode": "list", "fields": ["D.n"], "single": False,
                               "select_all": True, "initial": {}, "style": {}} if kind == "slicer" else None,
                    "texts": {"shape_text": "shape label"} if kind == "static" else {},
                    "tooltip": "info" if kind == "tooltip" else None})
        specs[vid] = VisualSpec(id=vid, kind=kind, title=None, sql="s")
        data[vid] = ({"columns": ["value", "target", "min", "max"], "rows": [[70, 80, 0, 100]]}
                     if kind in ("kpi", "gauge")
                     else {"columns": ["label", "value"], "rows": [["a", 1], ["b", 2]]} if kind == "multicard"
                     else {"columns": ["category", "value"], "rows": [["a", 1], ["b", 2], ["c", 3]]}
                     if kind in ("column", "bar", "line", "pie")
                     else {"columns": ["c1", "c2"], "rows": [["x", 1], ["y", 2]]} if kind in ("table", "matrix")
                     else {"columns": ["value"], "rows": [[1234]]})
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02", "#0B2036"]}},
              "pages": [{"display_name": "P", "width": 1280, "height": 720, "visuals": vis}]}
    params = {"n": {"type": "string", "from_slicer": "D.n", "label": "n", "dtype": "text",
                    "multi": True, "pages": ["P"], "default": None}}
    spec = ReportSpec(report="T", source=None, connection="", delivery="", parameters=params,
                      roles={}, visuals=specs, raw={"slicers": {"x11": {}}})
    return render_html(layout, spec, {}, data, mode="snapshot",
                       slicer_data={"x11": {"columns": ["level1"], "rows": [["North"], ["South"]]}})


def test_every_renderer_kind_scales_its_text_with_the_page():
    """The claim "everything resizes" is only worth making if it is measured. One page carrying
    every renderer kind is rendered at 100 % and at 150 %, and *every* piece of text in every
    visual — DOM and inside the ECharts canvas — must grow by the same factor. Written after an
    audit found the gauge's callout, a failed visual's error text, the loading text, the
    dynamicTooltip icon and the slicer widget all pinned at their design size."""
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    import tempfile
    from pathlib import Path
    f = Path(tempfile.mkdtemp()) / "r.html"
    f.write_text(_every_kind_report(), encoding="utf-8")
    probe = """() => { const out = {};
      document.querySelectorAll('.visual').forEach(v => {
        const got = {};
        v.querySelectorAll('*').forEach(n => {
          if (!n.children.length && (n.textContent || '').trim()) {
            const cls = (typeof n.className === 'string' && n.className) ? n.className.split(' ')[0]
                                                                        : n.tagName.toLowerCase();
            got[cls] = parseFloat(getComputedStyle(n).fontSize); }});
        const host = v.querySelector('.chart');
        const inst = host && window.echarts ? echarts.getInstanceByDom(host) : null;
        if (inst) { const o = inst.getOption(); const s = (o.series || [])[0] || {};
          if (s.label && s.label.fontSize) got['~dataLabel'] = s.label.fontSize;
          if (s.detail && s.detail.fontSize) got['~gaugeValue'] = s.detail.fontSize;
          if (o.xAxis && o.xAxis[0] && o.xAxis[0].axisLabel && o.xAxis[0].axisLabel.fontSize)
            got['~axis'] = o.xAxis[0].axisLabel.fontSize; }
        out[v.dataset.kind + ':' + v.dataset.visual] = got; });
      return out; }"""
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:                                       # noqa: BLE001
            pytest.skip(str(e))
        pg = br.new_page(viewport={"width": 2000, "height": 1100})
        pg.goto(f.as_uri())
        pg.wait_for_timeout(2500)
        pg.select_option("#view-mode", "1")
        pg.wait_for_timeout(700)
        at100 = pg.evaluate(probe)
        pg.select_option("#view-mode", "1.5")
        pg.wait_for_timeout(900)
        at150 = pg.evaluate(probe)
        br.close()
    measured, pinned = 0, []
    for key, elements in at100.items():
        for el, small in elements.items():
            big = (at150.get(key) or {}).get(el)
            if not small or not big:
                continue
            measured += 1
            if big / small <= 1.3:                      # 1.5x, with room for integer rounding
                pinned.append(f"{key}/{el}: {small} -> {big}")
    assert measured >= 25, f"the audit only reached {measured} elements; it has stopped covering the page"
    assert not pinned, "text pinned at its design size:\n  " + "\n  ".join(pinned)


@pytest.mark.parametrize("mode", ["snapshot", "live", "hah"])
def test_the_canvas_is_framed_on_a_grey_surround_and_parameters_live_in_the_header(mode):
    html = _label_report(mode=mode)
    assert "html, body { margin: 0; background: #E2E2E2;" in html
    # the frame is an outline, never a border: a border would eat 4px of the content box and
    # shift every visual, because positions are percentages of it
    assert "outline: 2px solid #000;" in html and "box-shadow: 0 6px 24px rgba(0, 0, 0, .18);" in html
    assert "background: var(--bg); outline:" in html       # the canvas still looks like paper
    assert "if (s === '.params' && el.closest('header')) return h;" in html   # not counted twice
    # the parameter controls sit with View/Labels/Tabs instead of taking a row of their own
    from pbix2html.semantic import ReportSpec
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02"]}},
              "pages": [{"display_name": "P", "width": 1280, "height": 720, "visuals": []}]}
    spec = ReportSpec(report="T", source=None, connection="", delivery="",
                      parameters={"RunningMonth": {"type": "string", "label": "RunningMonth",
                                                   "dtype": "text", "multi": False, "default": None}},
                      roles={}, visuals={}, raw={})
    with_param = render_html(layout, spec, {}, None, mode=mode, hah_base="https://h.example")
    head = with_param.split("</header>")[0]
    assert '<div class="params">' in head, "the parameter bar is not inside the header"
    assert "RunningMonth" in head


def test_a_header_full_of_controls_wraps_instead_of_overflowing():
    """View, Labels, Tabs and one row of report parameters all share the header now, so it has to
    wrap on a narrow window rather than push the page sideways — and Fit page has to notice that
    the chrome got taller."""
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    import tempfile
    from pathlib import Path
    from pbix2html.semantic import ReportSpec, VisualSpec
    card = {"id": "c1", "type": "card", "hidden": False, "is_group": False, "parent_group": None,
            "groups": [], "x": 40, "y": 40, "width": 220, "height": 110, "z": 1, "title": "Spend",
            "style": {}, "sort": None, "cond_formats": [], "n_fields": None, "col_align": [],
            "y_fields": [], "action": None}
    layout = {"theme": {"custom_json": {"dataColors": ["#FF5F02"]}},
              "pages": [{"display_name": "A", "width": 1280, "height": 720, "visuals": [card]},
                        {"display_name": "B", "width": 1280, "height": 720, "visuals": [card]}]}
    params = {f"Param{i}": {"type": "string", "label": f"Parameter {i}", "dtype": "text",
                            "multi": False, "default": None} for i in range(3)}
    spec = ReportSpec(report="A Long Report Name Here", source=None, connection="", delivery="",
                      parameters=params, roles={},
                      visuals={"c1": VisualSpec(id="c1", kind="card", title=None, sql="s")}, raw={})
    f = Path(tempfile.mkdtemp()) / "r.html"
    f.write_text(render_html(layout, spec, {}, {"c1": {"columns": ["value"], "rows": [[12]]}},
                             mode="snapshot"), encoding="utf-8")
    probe = """() => { const h = document.querySelector('header');
        const p = document.querySelector('.page').getBoundingClientRect();
        return {headerH: h.offsetHeight,
                overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
                fits: p.bottom <= window.innerHeight + 1}; }"""
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:                                       # noqa: BLE001
            pytest.skip(str(e))
        seen = {}
        for w in (1600, 820):
            pg = br.new_page(viewport={"width": w, "height": 760})
            pg.goto(f.as_uri())
            pg.wait_for_timeout(800)
            seen[w] = pg.evaluate(probe)
            pg.close()
        br.close()
    for w, r in seen.items():
        assert not r["overflowX"], f"the header pushed the page sideways at {w}px"
        assert r["fits"], f"the canvas did not fit at {w}px"
    assert seen[820]["headerH"] > seen[1600]["headerH"], "the header should wrap when it runs out of room"


@pytest.mark.parametrize("mode", ["snapshot", "live", "hah"])
def test_page_tabs_make_the_active_page_obvious(mode):
    """The active page reads as a raised white tab carrying the theme's accent; the rest sit flat
    on the grey surround. The accent moves to whichever edge the strip is docked against."""
    html = _two_page_report(mode)
    accent = "var(--accent)"            # the theme's, in every mode: hah brands only the top bar
    assert 'nav.tabs button[aria-selected="true"] { background: #FFFFFF; color: #111111; font-weight: 600;' in html
    assert f"border-top-color: {accent}" in html
    assert f'body[data-tabs="bottom"] nav.tabs button[aria-selected="true"] {{ border-bottom-color: {accent}' in html
    assert f'body[data-tabs="left"] > nav.tabs button[aria-selected="true"] {{ border-left-color: {accent}' in html
    assert "nav.tabs button:hover" in html                       # inactive tabs respond to the pointer


def test_the_top_bar_keeps_its_own_light_palette_whatever_the_report_theme_is():
    """The bar is the app's chrome, not the report's. A dark report theme would otherwise paint
    `var(--fg)` (near-white) onto a white bar and make the title vanish."""
    from pbix2html.semantic import ReportSpec
    dark = {"custom_json": {"background": "#1A1A2E", "foreground": "#FFFFFF",
                            "dataColors": ["#00C7B1"]}}
    layout = {"theme": dark, "pages": [{"display_name": "P", "width": 1280, "height": 720,
                                        "visuals": []}]}
    spec = ReportSpec(report="T", source=None, connection="", delivery="", parameters={},
                      roles={}, visuals={}, raw={})
    html = render_html(layout, spec, {}, None, mode="snapshot")
    assert "padding: .75rem 1.25rem; background: #FFFFFF; color: #1A1A1A;" in html
    assert "header .meta { color: #666;" in html                 # not var(--muted) from the theme
    # the canvas still follows the report's own theme
    assert "background: var(--bg); outline: 2px solid #000;" in html


# Rules that dress the viewer's chrome (the bar, the tab strip, the parameter menu) or the page
# surround, not the report: hah is allowed its own branding there and nowhere else.
_CHROME = ("header", "nav.tabs", "data-tabs", ".params", ".view", ":root", "html", "body",
           "*", "button,", ".spinner", "keyframes")


def _canvas_css(name):
    """{selector: declarations} for every rule that paints something inside the canvas."""
    src = (Path(__file__).resolve().parents[1] / "src/pbix2html/templates" / name).read_text(encoding="utf-8")
    block = re.sub(r"/\*.*?\*/", "", src.split("<style>")[1].split("</style>")[0], flags=re.S)
    rules = {}
    for sel, body in re.findall(r"(?m)^\s*([^@{}][^{]*)\{([^}]*)\}", block):
        sel = " ".join(sel.split())
        if any(c in sel for c in _CHROME):
            continue
        # fixv1 §3 wants `.error-state`; the shared renderers emit `.error`. Same rule, one name here.
        rules.setdefault(sel.replace(", .error-state", ""), " ".join(body.split()))
    return rules


def test_hah_mode_does_not_alter_the_canvas_design():
    """hah fetches its own data; it does not restyle the report. Every rule that reaches inside the
    canvas must be the one snapshot mode uses, or a visual silently looks different depending on
    how it was delivered — hah used to add a teal stripe to every card and shorten every chart by
    1.2rem to make room for a row count."""
    main, hah = _canvas_css("report.html.j2"), _canvas_css("report_hah.html.j2")
    extra = {"card-footer"}                 # hah-only, and an overlay: it reserves no space
    for sel, body in main.items():
        assert sel in hah, f"{sel} is missing from the hah canvas"
        assert hah[sel] == body, f"{sel} differs:\n  snapshot: {body}\n  hah:      {hah[sel]}"
    for sel in hah:
        assert sel in main or sel.strip(".") in extra, f"{sel} styles the canvas in hah only"
    # "Fit page" sizes the canvas with the height the chrome leaves over, so the bar's box is a
    # canvas concern even though its colours are not: hah may be branded, not taller.
    for name in ("report.html.j2", "report_hah.html.j2"):
        css = (Path(__file__).resolve().parents[1] / "src/pbix2html/templates" / name).read_text(encoding="utf-8")
        header = re.search(r"(?m)^\s*header \{(.*?)\}", css, re.S).group(1)
        assert "padding: .75rem 1.25rem" in " ".join(header.split()), name
        assert "border-bottom: 1px solid" in " ".join(header.split()), name


def _visual_tag(name):
    """The `<div class="visual …">` opening tag as the template writes it, comments stripped."""
    src = (Path(__file__).resolve().parents[1] / "src/pbix2html/templates" / name).read_text(encoding="utf-8")
    start = src.index('<div class="visual')
    end = src.index('<div class="body"></div>', start)
    return " ".join(re.sub(r"\{#.*?#\}", "", src[start:end], flags=re.S).split())


def test_hah_builds_each_visual_from_the_same_markup_as_snapshot_mode():
    """The frame is the .pbix's in both modes. hah used to emit `border`/`background` only when the
    report set them, which left the template's own 1px grey box and opaque fill in place: every
    visual came out boxed in hah and unboxed in snapshot, from the same file."""
    assert _visual_tag("report_hah.html.j2") == _visual_tag("report.html.j2")
