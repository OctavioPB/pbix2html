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


@pytest.mark.parametrize("mode", ["relative", "other"])
def test_modes_without_a_widget_are_not_drawn(mode):
    from pbix2html import render

    spec = type("S", (), {"parameters": {"c": {"from_slicer": "T.c", "pages": ["P"]}}, "raw": {}})()
    assert render._slicer_entry(_slicer("s", ["T.c"], mode=mode), "P", spec, False) is None
    entry = render._slicer_entry(_slicer("s", ["T.c"]), "P", spec, True)
    assert entry["params"] == ["c"] and entry["mode"] == "dropdown" and entry["options_sql"] is None


def test_tile_mode_drives_a_plain_widget_like_list_or_dropdown():
    from pbix2html import render

    spec = type("S", (), {"parameters": {"c": {"from_slicer": "T.c", "pages": ["P"]}}, "raw": {}})()
    entry = render._slicer_entry(_slicer("s", ["T.c"], mode="tile"), "P", spec, False)
    assert entry is not None and entry["params"] == ["c"] and entry["mode"] == "tile"


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


def test_tile_slicer_renders_chips_for_the_saved_selection_without_js_errors(fake_pbix, tmp_path):
    from tests.test_verify import _browser

    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    from pbix2html.render import render_html

    layout = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    sid = next(v["id"] for p in layout["pages"] for v in p["visuals"] if v["type"] == "slicer")
    for p in layout["pages"]:
        for v in p["visuals"]:
            if v["id"] == sid:
                v["slicer"]["mode"] = "tile"
    block = {"columns": ["level1"], "rows": [["2025"], ["2026"]]}
    html = tmp_path / "r.html"
    # A snapshot's slicer widgets are read-only by design (ADR-006) — the saved selection (year
    # 2025, the yaml's default) should still show as the chip already "on", not just render inert.
    html.write_text(render_html(layout, spec, {"year": 2025}, {}, mode="snapshot", slicer_data={sid: block}),
                    encoding="utf-8")
    with sync.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(executable_path=browser)
        except Exception as e:  # noqa: BLE001
            pytest.skip(str(e))
        errors = []
        pg = br.new_page(viewport={"width": 1280, "height": 800})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(html.as_uri())
        pg.wait_for_timeout(300)
        chips = pg.locator(f"#v-{sid} .sl-tile")
        count = chips.count()
        labels = [chips.nth(i).inner_text() for i in range(count)]
        classes = [chips.nth(i).get_attribute("class") for i in range(count)]
        disabled = [chips.nth(i).is_disabled() for i in range(count)]
        br.close()
    assert not errors
    assert labels == ["2025", "2026"]
    assert all(disabled)                                    # read-only in snapshot mode
    assert "on" in classes[0] and "on" not in classes[1]     # the saved selection (year=2025) shows selected


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


def test_autofill_adds_the_slicers_section_an_old_yaml_lacks(fake_pbix):
    layout = ex.extract_layout(fake_pbix)
    layout["pages"][0]["visuals"].append({
        "id": "sl1", "type": "slicer", "x": 0, "y": 0, "width": 100, "height": 40, "z": 0, "title": "Year",
        "projections": {"Values": ["Calendar.Year"]}, "filters": [], "groups": [], "is_group": False,
        "slicer": {"mode": "dropdown", "fields": ["Calendar.Year"], "single": False, "select_all": True,
                   "initial": {}, "style": {}}})
    old = {"report": "R", "parameters": {}, "visuals": {}}          # no `slicers:`, as before ADR-006
    out = semantic.autofill(old, layout, {"tables": ["Calendar"]}, {})
    assert "sl1" in out["slicers_added"] and "sl1" in out["raw"]["slicers"]
    again = semantic.autofill(out["raw"], layout, {"tables": ["Calendar"]}, {})
    assert again["slicers_added"] == []                             # never touches an existing entry


def _hierarchy_level_node(level):
    """A Power BI date-hierarchy level, as a slicer's prototypeQuery carries it."""
    return {"Name": f"Calendar.Date.Variation.Date Hierarchy.{level}",
            "HierarchyLevel": {"Expression": {"Hierarchy": {
                "Expression": {"PropertyVariationSource": {
                    "Expression": {"SourceRef": {"Source": "c"}}, "Name": "Variation", "Property": "Date"}},
                "Hierarchy": "Date Hierarchy"}}, "Level": level}}


def test_a_date_hierarchy_slicer_keeps_its_levels_instead_of_collapsing_to_the_raw_date():
    """Every level of a date hierarchy sits on the same underlying column, so `_entity_prop`
    resolved Year and Month to the identical ref and the de-duplicated field list kept one
    field over the raw date — the widget then listed `2026-08-31` instead of years and months."""
    for level in ("Year", "Quarter", "Month", "Day"):
        assert ex._hierarchy_level(_hierarchy_level_node(level)) == level
    assert ex._hierarchy_level({"Column": {"Expression": {"SourceRef": {"Entity": "T"}},
                                           "Property": "c"}}) is None
    sv = {"prototypeQuery": {"Select": [_hierarchy_level_node("Year"), _hierarchy_level_node("Month")]}}
    assert ex._proto_levels(sv) == {"Calendar.Date.Variation.Date Hierarchy.Year": "Year",
                                    "Calendar.Date.Variation.Date Hierarchy.Month": "Month"}
    d = ex.parse_slicer({}, ["Calendar.Date"], None, ["Year", "Month"])
    assert d["fields"] == ["Calendar.Date"] and d["levels"] == ["Year", "Month"]
    # levels are only kept when every field resolves to the one column they all share
    assert "levels" not in ex.parse_slicer({}, ["A.x", "B.y"], None, ["Year"])


def test_a_date_hierarchy_slicer_lists_years_and_month_names_in_calendar_order():
    v = {"type": "slicer", "fields": ["Calendar.Date"],
         "slicer": {"mode": "list", "fields": ["Calendar.Date"], "levels": ["Year", "Month"],
                    "single": False, "select_all": True, "initial": {}, "style": {}}}
    sql = semantic._slicer_options_sql(v, {"Calendar": "SELECT date FROM db.calendar"}, {})
    assert "EXTRACT(YEAR FROM calendar.\"date\") AS level1" in sql
    assert "TRIM(TO_CHAR(calendar.\"date\", 'Month')) AS level2" in sql
    # a month name sorts alphabetically, so the order has to come from the real date behind it
    assert sql.rstrip().endswith('ORDER BY MIN(calendar."date")')


def test_each_hierarchy_level_is_its_own_parameter_and_filters_on_the_level():
    v = {"type": "slicer", "fields": ["Calendar.Date"],
         "slicer": {"mode": "list", "fields": ["Calendar.Date"], "levels": ["Year", "Month"],
                    "single": False, "select_all": True, "initial": {}, "style": {}}}
    layout = {"pages": [{"display_name": "P", "visuals": [v]}]}
    model = {"columns": [{"TableName": "Calendar", "ColumnName": "Date",
                          "PandasDataType": "datetime64[ns]"}]}
    params = semantic._slicer_parameters(layout, model)
    assert set(params) == {"date_year", "date_month"}
    assert params["date_year"]["level"] == "Year" and params["date_year"]["dtype"] == "number"
    assert params["date_month"]["level"] == "Month" and params["date_month"]["dtype"] == "text"
    assert params["date_month"]["from_slicer"] == "Calendar.Date"   # still the real column
    # picking "January" means every January, so the predicate is over the level, not the date —
    # and it must be marked optional, see the regression test below
    where, used = semantic._draft_where(params, {"Calendar": "calendar"},
                                        {"Calendar": "SELECT date FROM db.calendar"}, [], None)
    assert any('EXTRACT(YEAR FROM calendar."date") IN (:date_year)' in w for w in where)
    assert any('TRIM(TO_CHAR(calendar."date", \'Month\')) IN (:date_month)' in w for w in where)
    assert semantic.slicer_params(v, "P", params) == ["date_year", "date_month"]


def test_an_empty_level_slicer_drops_its_predicate_instead_of_returning_no_rows():
    """Regression, found on a real report: every chart over the calendar rendered blank.

    "Nothing selected" means "no filter" in Power BI, and `query.bind` delivers that by rewriting
    `<column> IN (:name)` to `1=1` — but its pattern only recognises a *plain* column on the left.
    A date-hierarchy level is an expression (`EXTRACT(YEAR FROM c.d)`), so it never matched: the
    empty parameter fell through to `IN (NULL)`, which returns no rows at all. The predicate is
    now marked `/*if name*/ … /*fi name*/` so `bind` drops the whole thing."""
    v = {"type": "slicer", "fields": ["Calendar.end_of_month"],
         "slicer": {"mode": "list", "fields": ["Calendar.end_of_month"], "levels": ["Year", "Month"],
                    "single": False, "select_all": True, "initial": {}, "style": {}}}
    layout = {"pages": [{"display_name": "P", "visuals": [v]}]}
    model = {"columns": [{"TableName": "Calendar", "ColumnName": "end_of_month",
                          "PandasDataType": "datetime64[ns]"}]}
    params = semantic._slicer_parameters(layout, model)
    where, used = semantic._draft_where(params, {"Calendar": "calendar"},
                                        {"Calendar": "SELECT end_of_month FROM db.cal"}, [], None)
    sql = "SELECT 1 FROM t WHERE " + " AND ".join(where)
    out, values = bind(sql, used, {n: [] for n in used})
    assert "IN (NULL)" not in out, "an empty level slicer still filtered everything out"
    assert out.split("WHERE", 1)[1].strip() == "1=1 AND 1=1"
    assert values == []
    # a real selection still filters, on the level expression
    out, values = bind(sql, used, {"end_of_month_year": [2026], "end_of_month_month": ["January"]})
    assert "EXTRACT(YEAR FROM calendar.end_of_month) IN (?)" in out and values == [2026, "January"]
    # a plain column keeps the exact SQL it always had: no churn for every other report
    plain = {"type": "string", "from_slicer": "Dim.name", "multi": True, "dtype": "text"}
    assert semantic._param_predicate("dim.name", "name", plain) == "dim.name IN (:name)"


def test_a_plain_slicer_is_untouched_by_the_hierarchy_path():
    v = {"type": "slicer", "fields": ["Dim.name"],
         "slicer": {"mode": "list", "fields": ["Dim.name"], "single": False,
                    "select_all": True, "initial": {}, "style": {}}}
    layout = {"pages": [{"display_name": "P", "visuals": [v]}]}
    params = semantic._slicer_parameters(layout, {"columns": []})
    assert set(params) == {"name"} and "level" not in params["name"]
    sql = semantic._slicer_options_sql(v, {"Dim": "SELECT name FROM db.dim"}, {})
    assert sql.startswith("SELECT DISTINCT dim.name AS level1")
    # an unrecognised level name disables the whole hierarchy path rather than half-applying it
    assert semantic.slicer_levels({"levels": ["Year", "Fortnight"]}) == []


@pytest.mark.parametrize("levels", [["Year", "Month"], None])
@pytest.mark.parametrize("params", [
    {"date": {"type": "string", "from_slicer": "Calendar.Date", "label": "Date",
              "dtype": "date", "multi": True, "pages": ["P"], "default": None}},
    {"date_year": {"type": "string", "from_slicer": "Calendar.Date", "level": "Year",
                   "label": "Year", "dtype": "number", "multi": True, "pages": ["P"], "default": None},
     "date_month": {"type": "string", "from_slicer": "Calendar.Date", "level": "Month",
                    "label": "Month", "dtype": "text", "multi": True, "pages": ["P"], "default": None}},
])
def test_a_slicer_never_vanishes_when_its_field_has_parameters(levels, params):
    """Regression: adding hierarchy levels made `slicer_params` demand a level-matched parameter.
    A yaml written before levels existed has one parameter over the date column, so nothing
    matched, `_slicer_entry` returned None and the date filter disappeared from the page.

    The yaml and the extractor version are independent — a report's parameters can predate any
    extractor change — so every combination of the two must still find the slicer's parameters.
    Losing a widget silently hides a filter the report depends on."""
    from pbix2html.render import _slicer_entry
    from pbix2html.semantic import ReportSpec
    d = {"mode": "list", "fields": ["Calendar.Date"], "single": False, "select_all": True,
         "initial": {}, "style": {}, **({"levels": levels} if levels else {})}
    v = {"id": "s1", "type": "slicer", "fields": ["Calendar.Date"], "slicer": d}
    assert semantic.slicer_params(v, "P", params), "no parameters matched"
    spec = ReportSpec(report="R", source=None, connection="", delivery="", parameters=params,
                      roles={}, visuals={}, raw={"slicers": {"s1": {}}})
    assert _slicer_entry(v, "P", spec, False) is not None, "the slicer would not render"


def test_a_slicer_with_no_widget_still_draws_nothing():
    """The fallbacks must not resurrect the modes that deliberately have no widget."""
    from pbix2html.render import _slicer_entry
    from pbix2html.semantic import ReportSpec
    params = {"date": {"type": "string", "from_slicer": "Calendar.Date", "label": "Date",
                       "dtype": "date", "multi": True, "pages": ["P"], "default": None}}
    spec = ReportSpec(report="R", source=None, connection="", delivery="", parameters=params,
                      roles={}, visuals={}, raw={"slicers": {}})
    for mode in ("relative", "other"):
        v = {"id": "s2", "type": "slicer", "fields": ["Calendar.Date"],
             "slicer": {"mode": mode, "fields": ["Calendar.Date"], "single": False,
                        "select_all": True, "initial": {}, "style": {}}}
        assert _slicer_entry(v, "P", spec, False) is None, mode
    # and a slicer whose field drives no parameter at all still has no widget
    v = {"id": "s3", "type": "slicer", "fields": ["Other.col"],
         "slicer": {"mode": "list", "fields": ["Other.col"], "single": False,
                    "select_all": True, "initial": {}, "style": {}}}
    assert _slicer_entry(v, "P", spec, False) is None
