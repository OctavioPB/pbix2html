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
