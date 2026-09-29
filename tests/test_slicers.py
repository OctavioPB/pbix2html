"""Slicers as widgets: extraction, page-scoped parameters, range predicates, options queries."""
import pytest

from pbix2html import extract as ex, semantic
from pbix2html.query import bind


def _col(entity, prop, source=None):
    ref = {"Source": source} if source else {"Entity": entity}
    return {"Column": {"Expression": {"SourceRef": ref}, "Property": prop}}


def _lit(v):
    return {"Literal": {"Value": v}}


def test_pbi_literals():
    f = ex._pbi_literal
    assert (f("2026L"), f("12.5D"), f("'a''b'"), f("true"), f("null")) == (2026, 12.5, "a'b", True, None)
    assert f("datetime'2026-04-01T00:00:00'") == "2026-04-01"


def test_saved_selection_values_and_ranges():
    flt = {"From": [{"Name": "c", "Entity": "Calendar"}], "Where": [
        {"Condition": {"In": {"Expressions": [_col("", "Year", "c")], "Values": [[_lit("2026L")], [_lit("2025L")]]}}},
        {"Condition": {"Between": {"Expression": _col("", "Date", "c"),
                                   "LowerBound": _lit("datetime'2026-06-01T00:00:00'"),
                                   "UpperBound": _lit("datetime'2026-06-13T00:00:00'")}}},
        {"Condition": {"Comparison": {"ComparisonKind": 2, "Left": _col("", "Other", "c"), "Right": _lit("5L")}}}]}
    assert ex._filter_selection(flt) == {
        "values": {"Calendar.Year": [2026, 2025]},
        "range": {"Calendar.Date": {"from": "2026-06-01", "to": "2026-06-13"}, "Calendar.Other": {"from": 5}}}
    assert ex._filter_selection(None) == {}


def test_parse_slicer_mode_selection_and_style():
    prop = lambda **kw: [{"properties": {k: {"expr": _lit(v)} for k, v in kw.items()}}]   # noqa: E731
    objs = {"data": prop(mode="'Between'"), "selection": prop(singleSelect="true", selectAllCheckboxEnabled="false"),
            "items": prop(textSize="12D")}
    d = ex.parse_slicer(objs, ["Calendar.Date"], {"groupName": "g1"})
    assert (d["mode"], d["single"], d["select_all"], d["sync_group"], d["style"]) == \
        ("between", True, False, "g1", {"size": 12.0})
    assert ex.parse_slicer({}, ["T.c"])["mode"] == "list"                    # unspecified = a plain list
    assert ex.parse_slicer({"data": prop(mode="'Dropdown'")}, ["T.c"])["mode"] == "dropdown"


def _layout(*pages):
    return {"report": "R", "pages": [{"display_name": n, "visuals": vs} for n, vs in pages]}


def _slicer(vid, fields, **d):
    return {"id": vid, "type": "slicer", "fields": fields,
            "slicer": {"mode": "dropdown", "fields": fields, "single": False, "select_all": True,
                       "initial": {}, "style": {}, **d}}


def test_same_field_on_two_pages_gets_one_parameter_per_page_with_its_own_default():
    L = _layout(("Sales", [_slicer("a", ["Org.name"], initial={"values": {"Org.name": ["Acme"]}})]),
                ("Costs", [_slicer("b", ["Org.name"], initial={"values": {"Org.name": ["Globex"]}})]),
                ("Only", [_slicer("c", ["Org.region"])]))
    P = semantic._slicer_parameters(L)
    assert set(P) == {"name__sales", "name__costs", "region"}                # single-page field keeps its name
    assert P["name__sales"]["default"] == ["Acme"] and P["name__costs"]["default"] == ["Globex"]
    assert P["name__sales"]["pages"] == ["Sales"] and P["region"]["multi"] is True
    assert semantic._params_for_page(P, "Sales").keys() == {"name__sales", "region"} - {"region"}
    assert semantic.slicer_params(L["pages"][1]["visuals"][0], "Costs", P) == ["name__costs"]


def test_sync_group_shares_one_parameter_across_pages():
    L = _layout(("A", [_slicer("a", ["T.c"], sync_group="g")]), ("B", [_slicer("b", ["T.c"], sync_group="g")]))
    P = semantic._slicer_parameters(L)
    assert list(P) == ["c"] and P["c"]["pages"] == ["A", "B"] and P["c"]["sync"] == "g"


def test_range_slicer_makes_bound_parameters_and_columns_with_the_same_name_do_not_collide():
    model = {"columns": [{"TableName": "Cal", "ColumnName": "Date", "PandasDataType": "datetime64[ns]"}]}
    L = _layout(("P", [_slicer("r", ["Cal.Date"], mode="between", initial={"range": {"Cal.Date": {"from": "2026-01-01"}}}),
                       _slicer("x", ["A.year"]), _slicer("y", ["B.year"])]))
    P = semantic._slicer_parameters(L, model)
    assert P["date_from"]["bound"] == "from" and P["date_from"]["default"] == "2026-01-01" and P["date_from"]["dtype"] == "date"
    assert P["date_to"]["bound"] == "to" and P["date_to"]["multi"] is False
    assert {"a_year", "b_year"} <= set(P)


TABLE_MAP = {"Sales": "SELECT * FROM db.sales", "Cal": 'SELECT calendar_date AS "date" FROM sys_calendar.calendar'}
RELS = [{"FromTableName": "Sales", "FromColumnName": "d", "ToTableName": "Cal", "ToColumnName": "date", "IsActive": 1}]
PARAMS = {"date_from": {"from_slicer": "Cal.Date", "bound": "from", "dtype": "date"},
          "date_to": {"from_slicer": "Cal.Date", "bound": "to", "dtype": "date"}}


def test_range_predicates_are_optional_and_never_nest_their_markers():
    sql, used = semantic._draft_visual_sql({"projections": {"Values": ["Sum(Sales.x)"]}}, "card", {}, TABLE_MAP, RELS, PARAMS)
    assert used == ["date_from", "date_to"]
    assert 'IN (SELECT "date" FROM' in sql and 'cal."date" >= CAST(:date_from AS DATE)' in sql
    assert sql.count("/*if date_from*/") == 1 and sql.count("/*fi date_from*/") == 1          # not nested
    open_ended, _ = bind(sql, used, {"date_from": "2026-06-01"})
    assert "1=1" in open_ended and "CAST(? AS DATE)" in open_ended and "date_to" not in open_ended
    assert bind(sql, used, {})[0].count("1=1") == 2


def test_options_sql_orders_calendars_chronologically_and_needs_a_known_source():
    cal = {"Cal": {"date_column": "Date"}}
    v = _slicer("s", ["Cal.Month Year"])
    sql = semantic._slicer_options_sql(v, TABLE_MAP, cal)
    assert "GROUP BY 1" in sql and 'ORDER BY MIN(cal."date")' in sql and "AS level1" in sql
    plain = semantic._slicer_options_sql(_slicer("s", ["Sales.region"]), TABLE_MAP, {})
    assert plain.startswith("SELECT DISTINCT sales.region AS level1") and plain.rstrip().endswith("ORDER BY 1")
    two = semantic._slicer_options_sql(_slicer("s", ["Sales.year", "Sales.month"]), TABLE_MAP, {})
    assert "AS level1" in two and "AS level2" in two and two.rstrip().endswith("ORDER BY 1, 2")
    assert semantic._slicer_options_sql(_slicer("s", ["Nope.x"]), TABLE_MAP, {}) is None      # no source
    assert semantic._slicer_options_sql(_slicer("s", ["Cal.Date"], mode="between"), TABLE_MAP, cal) is None
    assert semantic._slicer_options_sql(_slicer("s", ["Sales.a", "Cal.b"]), TABLE_MAP, cal) is None   # two tables


def test_slicers_section_lists_params_and_options_per_slicer_visual():
    L = _layout(("P", [_slicer("s", ["Sales.region"])]))
    P = semantic._slicer_parameters(L)
    sec = semantic.slicers_section(L, P, TABLE_MAP)
    assert sec["s"]["params"] == ["region"] and sec["s"]["page"] == "P" and "options_sql" in sec["s"]


@pytest.mark.parametrize("mode", ["relative", "other", "tile"])
def test_modes_without_a_widget_are_not_drawn(mode):
    from pbix2html import render

    spec = type("S", (), {"parameters": {"c": {"from_slicer": "T.c", "pages": ["P"]}}, "raw": {}})()
    assert render._slicer_entry(_slicer("s", ["T.c"], mode=mode), "P", spec, False) is None
    entry = render._slicer_entry(_slicer("s", ["T.c"]), "P", spec, True)
    assert entry["params"] == ["c"] and entry["mode"] == "dropdown" and entry["options_sql"] is None


def test_snapshot_html_draws_the_widget_embeds_options_and_keeps_it_out_of_the_top_bar(fake_pbix):
    import json

    from pbix2html.render import render_html

    layout = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    sid = next(v["id"] for p in layout["pages"] for v in p["visuals"] if v["type"] == "slicer")
    block = {"columns": ["level1"], "rows": [["2025"], ["2026"]]}
    html = render_html(layout, spec, {"year": 2025}, {}, mode="snapshot", slicer_data={sid: block})
    assert f'id="v-{sid}"' in html and 'id="slicer-data"' in html and "function slicerWidget" in html
    embedded = html.split('id="slicer-data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(embedded) == {sid: block}
    spec_json = html.split('id="spec" type="application/json">')[1].split("</script>")[0]
    assert '"slicer": {"mode"' in spec_json
    assert "<label>year" not in html                       # the widget edits it: no duplicate in the top bar


def test_custom_visuals_get_a_standard_meaning():
    assert ex.normalize_visual_type("HierarchySlicer1458836712039") == ("slicer", "HierarchySlicer1458836712039")
    assert ex.normalize_visual_type("dynamicTooltip1859AB39DB23051788ADF752BCB90749")[0] == "dynamicTooltip"
    assert ex.normalize_visual_type("Tachometer1474636471549") == ("Tachometer1474636471549", None)
    assert ex.normalize_visual_type("barChart") == ("barChart", None)


def test_dynamic_tooltip_content_is_read_from_its_literals():
    lit = lambda v: {"expr": {"Literal": {"Value": v}}}   # noqa: E731
    objs = {"tooltip": [{"properties": {"header": lit("'New Hires'"), "text": lit("'It''s the count.'")}}]}
    assert ex.parse_tooltip(objs) == {"header": "New Hires", "text": "It's the count."}
    assert ex.parse_tooltip({}) is None and ex.parse_tooltip({"tooltip": [{"properties": {}}]}) is None


def test_select_all_sentinel_of_custom_hierarchy_slicers_is_no_selection():
    flt = {"From": [{"Name": "h", "Entity": "H"}], "Where": [
        {"Condition": {"In": {"Expressions": [_col("", "lvl", "h")], "Values": [[_lit("'Select All'")]]}}}]}
    assert ex._filter_selection(flt) == {}
    flt["Where"][0]["Condition"]["In"]["Values"].append([_lit("'Smith'")])
    assert ex._filter_selection(flt) == {"values": {"H.lvl": ["Smith"]}}


def test_text_literals_unescape_doubled_quotes_and_z_index_is_an_integer(fake_pbix):
    assert ex.literal_to_text({"Literal": {"Value": "'Today''s Headcount'"}}) == "Today's Headcount"
    assert ex.literal_to_text({"Literal": {"Value": "10D"}}) == "10D"
    from pbix2html.render import render_html

    layout = ex.extract_layout(fake_pbix)
    layout["pages"][0]["visuals"][0]["z"] = 3000.0
    html = render_html(layout, semantic.load("Executive_Dashboard"), {"year": 2025}, {}, mode="snapshot")
    assert "z-index:3000" in html and "3000.0" not in html
