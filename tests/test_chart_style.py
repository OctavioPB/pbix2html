"""Chart formatting (colours, data labels, legend, axes), frame radius/title face, base-theme palette, table type, captions."""
from pbix2html import extract as ex
from pbix2html.render import _FONT_STACK, _title_css, resolve_theme


def _lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def _color(c):
    return {"solid": {"color": {"expr": {"Literal": {"Value": c}}}}}


def test_chart_style_reads_bar_colour_labels_legend_and_axes():
    objects = {
        "dataPoint": [{"properties": {"fill": _color("'#FF5F02'")}},
                      {"selector": {"metadata": "Sum(T.a)"}, "properties": {"fill": _color("'#00233C'")}}],
        "labels": [{"properties": {"show": _lit("true"), "bold": _lit("true"), "fontSize": _lit("9D"),
                                   "labelPosition": _lit("'OutsideEnd'"), "labelDisplayUnits": _lit("1D")}}],
        "legend": [{"properties": {"show": _lit("true"), "position": _lit("'Bottom'")}}],
        "valueAxis": [{"properties": {"show": _lit("false"), "gridlineShow": _lit("false")}}],
    }
    st = ex._chart_style(objects)
    assert st["point_color"] == "#FF5F02" and st["series_colors"] == {"Sum(T.a)": "#00233C"}
    assert st["labels"] is True and st["labels_bold"] is True and st["labels_size"] == 9.0 and st["labels_pos"] == "OutsideEnd"
    assert st["legend_pos"] == "Bottom" and "legend_show" not in st
    assert st["y_axis_show"] is False and st["gridlines"] is False
    assert ex._chart_style({}) == {}                                 # says nothing -> the renderer keeps its defaults
    assert "labels" not in ex._chart_style({"labels": [{"properties": {"show": _lit("false")}}]})


def test_frame_radius_and_title_face():
    vco = {"border": [{"properties": {"show": _lit("true"), "radius": _lit("10D")}}],
           "title": [{"properties": {"fontFamily": _lit("'''Segoe UI Semibold'', wf_segoe-ui_semibold, helvetica'")}}]}
    st = ex.container_style(vco)
    assert st["border_radius"] == 10.0 and st["title_family"] == "Segoe UI Semibold"
    css = _title_css(st, None)
    assert "font-family:'Segoe UI Semibold'," in css and "font-weight:600" in css         # named after its weight
    assert "font-weight" not in _title_css({"title_family": "Georgia"}, None)
    assert "font-family" not in _title_css({"title_family": "x;}</style>"}, None)


def test_builtin_base_theme_palette_when_the_file_carries_none():
    theme = {"base": {"name": "CY18SU07"}, "custom_json": None}
    assert ex.data_colors(theme)[6] == "#FE9666" and ex.theme_palette(theme)[8] == "#FE9666"     # ColorId 8
    assert resolve_theme(theme)["data_colors"][0] == "#01B8AA"
    assert ex.data_colors({"base": {"name": "Unknown"}, "custom_json": {"dataColors": ["#111111"]}}) == ["#111111"]
    assert resolve_theme(None)["font_family"] == _FONT_STACK


def test_table_type_and_column_captions():
    objects = {"columnHeaders": [{"properties": {"fontSize": _lit("13D"), "alignment": _lit("'Center'"), "bold": _lit("true")}}],
               "values": [{"properties": {"fontSize": _lit("11D")}}]}
    st = ex._table_style(objects)
    assert (st["table_header_size"], st["table_row_size"], st["table_header_align"], st["table_header_bold"]) == (13.0, 11.0, "center", True)
    sv = {"visualType": "tableEx", "projections": {"Values": [{"queryRef": "T.cld"}, {"queryRef": "Sum(T.u)"}]},
          "prototypeQuery": {"Select": [{"Name": "T.cld", "NativeReferenceName": "Cloud"},
                                        {"Name": "Sum(T.u)", "NativeReferenceName": "Units"}]}}
    assert ex._header_names_classic(sv) == ["Cloud", "Units"]
    sv["prototypeQuery"]["Select"].pop()                              # a caption is missing: no partial mapping
    assert ex._header_names_classic(sv) is None
    assert ex._header_names_classic({**sv, "visualType": "columnChart"}) is None


def _cmp(kind, prop, v):
    return {"Comparison": {"ComparisonKind": kind, "Left": {"Aggregation": {"Expression": {"Column": {"Expression": {"SourceRef": {"Entity": "T"}}, "Property": prop}}, "Function": 4}},
                           "Right": {"Literal": {"Value": f"{v}D"}}}}


def test_conditional_formatting_gradient_and_icon_rules_are_read_by_field_position():
    lit = lambda v: {"Literal": {"Value": v}}                                              # noqa: E731
    grad = {"FillRule": {"Input": {}, "FillRule": {"linearGradient3": {
        "min": {"color": lit("'minColor'"), "value": lit("0D")}, "mid": {"color": lit("'#fae99f'")},
        "max": {"color": lit("'#93CA89'"), "value": lit("100D")}}}}}
    icons = {"Conditional": {"Cases": [
        {"Condition": {"And": {"Left": _cmp(2, "flag", 1), "Right": _cmp(4, "flag", 100)}}, "Value": lit("'SymbolHigh'")},
        {"Condition": _cmp(0, "flag", 0), "Value": lit("'SymbolLow'")},
        {"Condition": _cmp(0, "other", 7), "Value": lit("'SymbolHigh'")}]}}                 # another field: dropped
    objects = {"values": [
        {"selector": {"metadata": "Sum(T.load)"}, "properties": {"backColor": {"solid": {"color": {"expr": grad}}}}},
        {"selector": {"metadata": "Sum(T.flag)"}, "properties": {"icon": {"value": {"expr": icons}}}},
        {"selector": {"metadata": "Sum(T.gone)"}, "properties": {"icon": {"value": {"expr": icons}}}},      # not in the wells
        {"properties": {"backColorSecondary": {}}}]}
    out = ex._cond_formats(objects, ["T.site", "Sum(T.load)", "Sum(T.flag)"])
    g = next(c for c in out if c["kind"] == "gradient")
    assert g["col"] == 1 and g["prop"] == "back"
    assert [s["color"] for s in g["stops"]] == ["#F8696B", "#FAE99F", "#93CA89"] and g["stops"][1]["value"] is None
    i = next(c for c in out if c["prop"] == "icon")
    assert i["col"] == 2 and i["rules"][0]["when"] == {"and": [{"op": ">=", "v": 1.0}, {"op": "<=", "v": 100.0}]}
    assert [r["icon"] for r in i["rules"]] == ["SymbolHigh", "SymbolLow"] and len(out) == 2
    assert ex._cond_formats({}, []) == []


def test_colours_by_series_value_column_alignment_and_grid_lines():
    scope = lambda text: {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": {"Column": {}}, "Right": {"Literal": {"Value": f"'{text}'"}}}}}]}  # noqa: E731
    objects = {"dataPoint": [
        {"properties": {"fill": _color("'#f37440'")}},
        {"selector": scope("White"), "properties": {"fill": _color("'#FF5F02'")}},
        {"selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}, "properties": {"fill": _color("'#123456'")}},   # wildcard: no value
        {"selector": {"metadata": "CountNonNull(T.id)"}, "properties": {"fill": _color("'#00233C'")}}]}
    st = ex._chart_style(objects)
    assert st["value_colors"] == {"White": "#FF5F02"} and st["point_color"] == "#f37440" and st["series_colors"] == {"CountNonNull(T.id)": "#00233C"}
    assert "#123456" not in str(st)
    tbl = {"columnFormatting": [{"selector": {"metadata": "T.b"}, "properties": {"alignment": _lit("'Center'")}}],
           "grid": [{"properties": {"gridHorizontal": _lit("false"), "gridVertical": _lit("true"), "gridVerticalWeight": _lit("5D")}}]}
    assert ex._col_align(tbl, ["T.a", "T.b"]) == [None, "center"] and ex._col_align({}, ["T.a"]) == []
    g = ex._table_style(tbl)
    assert g["table_grid_h"] is False and g["table_grid_v"] is True and g["table_grid_v_weight"] == 5.0


def test_theme_visual_styles_are_inherited_unless_the_visual_says_otherwise():
    theme = {"custom_json": {"visualStyles": {
        "*": {"*": {"border": [{"show": True, "color": {"solid": {"color": "#E1E1E1"}}, "radius": 12}],
                    "background": [{"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "transparency": 0}],
                    "title": [{"show": True, "fontFamily": "Segoe UI Semibold", "fontSize": 14, "fontColor": {"solid": {"color": "#00233C"}}, "alignment": "Left"}]}},
        "tableEx": {"*": {"total": [{"fontSize": 12}], "values": [{"backColor": {"solid": {"color": "#FFBF9A"}},
                                                                    "backColorAlternate": {"solid": {"color": "#FFFFFF"}}}]}}}}}
    layout = {"theme": theme, "pages": [{"visuals": [
        {"type": "columnChart", "style": {}},
        {"type": "slicer", "style": {"background_off": True, "border_color": "#111111", "border": True}},
        {"type": "tableEx", "style": {}},
        {"type": "image", "style": {}}]}]}
    ex.apply_theme_visual_styles(layout)
    chart, slicer, table, image = (v["style"] for v in layout["pages"][0]["visuals"])
    assert chart["border_radius"] == 12.0 and chart["border_color"] == "#E1E1E1" and chart["background"] == "#FFFFFF"
    assert chart["title_family"] == "Segoe UI Semibold" and chart["title_size"] == 14.0 and chart["title_align"] == "left"
    assert "background" not in slicer and slicer["border_color"] == "#111111"     # its own "off" and its own colour win
    assert table["table_row_bg"] == "#FFBF9A" and table["table_row_bg_alt"] == "#FFFFFF"
    assert image == {}                                                              # decoration keeps what it says itself


def test_chart_measure_fields_carry_the_caption_the_report_shows():
    assert ex._y_field("Sum(AI Studio Mnthly.ttl)", "AI Studio Node Pool Units") == {
        "entity": "AI Studio Mnthly", "prop": "ttl", "name": "AI Studio Node Pool Units", "ref": "Sum(AI Studio Mnthly.ttl)"}
    assert ex._y_field("Aggregations.Unstructured Total", None)["prop"] == "Unstructured Total"
    vis = {"query": {"queryState": {"Y": {"projections": [{"queryRef": "Sum(T.a)", "displayName": "A"}, {"queryRef": "T.m", "nativeQueryRef": "M"}]}}}}
    assert [f["name"] for f in ex._y_fields_pbir(vis)] == ["A", "M"]
    sv = {"projections": {"Y": [{"queryRef": "Sum(T.a)"}]}, "prototypeQuery": {"Select": [{"Name": "Sum(T.a)", "NativeReferenceName": "Total A"}]}}
    assert ex._y_fields_classic(sv)[0]["name"] == "Total A"


def test_table_total_sql_wraps_the_detail_query_and_blanks_non_additive_measures():
    from pbix2html import semantic
    v = {"projections": {"Values": ["T.site", "Sum(T.units)", "Avg(T.load)"]}}
    sql = "SELECT t0.site AS site, SUM(t0.units) AS units, AVG(t0.load) AS load\nFROM tbl AS t0\nWHERE t0.x = ?\nGROUP BY 1\nORDER BY 2 DESC"
    total = semantic.table_total_sql(v, sql, {})
    assert total and total.startswith("SELECT CAST('Total' AS VARCHAR(5)) AS site, SUM(t.units) AS units, CAST(NULL AS DECIMAL(18,2)) AS load")
    assert "ORDER BY" not in total and "FROM (\nSELECT t0.site" in total and total.rstrip().endswith(") AS t")
    assert semantic.table_total_sql({"projections": {"Values": ["T.site"]}}, sql, {}) is None            # nothing to add up
    assert semantic.table_total_sql(v, "WITH a AS (SELECT 1) SELECT * FROM a", {}) is None              # cannot sit in a derived table
    assert semantic._strip_final_order_by("SELECT a FROM (SELECT b FROM c ORDER BY 1) AS t ORDER BY 1") == "SELECT a FROM (SELECT b FROM c ORDER BY 1) AS t"


def test_series_only_chart_is_one_column_split_by_the_series_not_one_column_per_value():
    from pbix2html import semantic
    F = semantic._Field
    series = F(role="Series", is_value=False, label="gender", out_name="gender", expr="g.gender_name")
    category = F(role="Category", is_value=False, label="lvl", out_name="lvl", expr="m.level")
    # only a legend field: one constant x-axis slot
    dims, const = semantic.chart_dimensions([series])
    assert [n for _, n in dims] == ["series"] and const is True
    # the order of the wells in the file does not decide which is which
    dims, const = semantic.chart_dimensions([series, category])
    assert [(c.expr, n) for c, n in dims] == [("m.level", "category"), ("g.gender_name", "series")] and const is False
    # two fields in one role: unknown layout, the by-order naming stays
    dims, _ = semantic.chart_dimensions([category, F(role="Category", is_value=False, label="b", out_name="b", expr="x.b")])
    assert [n for _, n in dims] == ["category", "series"]


def test_per_value_colours_only_count_for_the_fields_the_chart_uses_now():
    def scope(entity, prop, text):
        return {"data": [{"scopeId": {"Comparison": {"Left": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
                                                     "Right": {"Literal": {"Value": f"'{text}'"}}}}}]}
    objects = {"dataPoint": [{"selector": scope("old", "Gender", "Male"), "properties": {"fill": _color("'#AAAAAA'")}},
                             {"selector": scope("Gender map", "gender_name", "Male"), "properties": {"fill": _color("'#FF5F02'")}}]}
    projections = {"Series": [{"queryRef": "Gender map.gender_name"}], "Y": [{"queryRef": "Sum(T.x)"}]}
    assert ex._chart_style(objects, ex._dim_refs(projections))["value_colors"] == {"Male": "#FF5F02"}
    assert ex._chart_style(objects)["value_colors"] == {"Male": "#AAAAAA"}         # without the chart's fields: first wins (theme)


def test_basic_shape_with_only_a_line_card_is_a_vertical_rule_not_a_filled_bar():
    line_only = {"line": [{"properties": {"lineColor": _color("'#FF5F02'")}}]}
    st = ex._style_with_fill({}, line_only)
    assert st["shape_kind"] == "line" and st["line_color"] == "#FF5F02" and "background" not in st
    # a rectangle that has a fill (or names its silhouette) is not a line
    assert ex._shape_geometry({**line_only, "fill": [{"properties": {}}]}) == {}
    assert ex._shape_geometry({**line_only, "shape": [{"properties": {"tileShape": _lit("'rectangle'")}}]})["shape_kind"] == "rectangle"


def test_pie_label_content_and_precision_are_read():
    objects = {"labels": [{"properties": {"show": _lit("true"), "labelStyle": _lit("'Category, data value'"),
                                          "percentageLabelPrecision": _lit("1L"), "labelPrecision": _lit("0L")}}]}
    st = ex._chart_style(objects)
    assert st["labels"] is True and st["pie_label"] == "Category, data value" and st["labels_pct_precision"] == 1.0
    assert "pie_label" not in ex._chart_style({"labels": [{"properties": {"show": _lit("true"), "labelStyle": _lit("'x;}<'")}}]})


def test_a_tall_thin_line_is_drawn_vertically(fake_pbix):
    from pbix2html import semantic
    from pbix2html.render import render_html
    layout = ex.extract_layout(fake_pbix)
    base = layout["pages"][0]
    rule = {**base["visuals"][0], "id": "r", "type": "basicShape", "x": 100, "y": 50, "width": 6, "height": 270, "style": {"shape_kind": "line", "line_color": "#FF5F02"}}
    html = render_html({**layout, "pages": [{**base, "visuals": [rule]}]}, semantic.load("Executive_Dashboard"), {}, None, mode="live")
    assert '"line_vertical": true' in html
