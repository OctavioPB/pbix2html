"""Full pipeline without Teradata: extract → yaml → FakeBackend → render → HTML."""
from pathlib import Path

import pytest

from pbix2html import extract as ex, semantic
from pbix2html.query import FakeBackend, bind, run_report
from pbix2html.render import render_html
from pbix2html.validate import compare

FIX = {
    "SELECT SUM(amount) AS value FROM sales WHERE year = ?": {"columns": ["value"], "rows": [[1234567.8]]},
    "SELECT region AS category, margin AS value FROM v_margin_region WHERE year = ?":
        {"columns": ["category", "value"], "rows": [["North", 0.21], ["South", 0.18], ["Center", None]]},
    "SELECT month AS category, amount AS value FROM v_revenue_month WHERE year = ?":
        {"columns": ["category", "value"], "rows": [["Jan", 10], ["Feb", 12]]},
    "SELECT customer, amount FROM v_top_customers WHERE year = ?":
        {"columns": ["customer", "amount"], "rows": [["ACME", 100.5], ["Globex", 90.0]]},
}


def test_bind_expands_lists():
    sql, vals = bind("WHERE a = :a AND r IN (:r) AND t = :ignored", ["a", "r"], {"a": 1, "r": ["x", "y"]})
    assert sql == "WHERE a = ? AND r IN (?,?) AND t = :ignored" and vals == [1, "x", "y"]


def test_scaffold_from_layout(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, {})
    assert "year" in sc["parameters"]                  # slicer → parameter
    assert sc["visuals"]["v1"]["kind"] == "card" and "TODO" in sc["visuals"]["v1"]["sql"]
    assert sc["visuals"]["v4"]["kind"] == "custom"
    assert "v3" not in sc["visuals"]                  # slicers aren't visuals with data


def test_validate_read_only_sql_accepts_select_and_with():
    assert semantic.validate_read_only_sql("SELECT * FROM t") == "SELECT * FROM t"
    assert semantic.validate_read_only_sql("  select 1  ;  ") == "select 1"
    assert semantic.validate_read_only_sql("WITH x AS (SELECT 1) SELECT * FROM x").startswith("WITH")


def test_validate_read_only_sql_rejects_non_select():
    for bad in ["", "   ", "DELETE FROM t", "DROP TABLE t", "INSERT INTO t VALUES (1)",
                "UPDATE t SET a=1", "CREATE TABLE t (a INT)", "GRANT SELECT ON t TO u",
                "SELECT * FROM t; DROP TABLE t", "SELECT * FROM t -- ; DROP TABLE t\n; DROP TABLE t"]:
        try:
            semantic.validate_read_only_sql(bad)
            raise AssertionError(f"should have rejected: {bad!r}")
        except ValueError:
            pass


def test_validate_read_only_sql_does_not_false_positive_on_replace_function():
    # OREPLACE/REPLACE(...) is a normal read-only Teradata string function, not DDL.
    assert "OREPLACE" in semantic.validate_read_only_sql("SELECT OREPLACE(name, 'a', 'b') FROM t")
    assert semantic.validate_read_only_sql("SELECT comment FROM tickets") == "SELECT comment FROM tickets"


def test_query_ref_parts_strips_aggregation_wrapper():
    # A numeric column dropped into a Values well is commonly auto-aggregated by Power
    # BI ("Sum(Sales.Amount)"), not just a plain "Table.Column" or "Table.Measure" ref.
    assert semantic.query_ref_parts("Sum(Sales.Amount)") == ("Sum", "Sales", "Amount")
    assert semantic.query_ref_parts("Sales.Net Revenue") == (None, "Sales", "Net Revenue")


def test_entities_used_does_not_mangle_aggregated_fields():
    # Regression: a naive split on the first '.' without stripping Sum(...)/Avg(...)/...
    # first turned "Sum(Sales.Amount)" into the entity "Sum(Sales" instead of "Sales" —
    # DAX aggregation syntax fused onto a truncated table name.
    v = {"projections": {"Values": ["Sum(Sales.Amount)"], "Category": ["Region.Name"]}}
    assert semantic._entities_used(v) == ["Sales", "Region"]


def test_sql_stub_uses_validated_table_map_as_subquery():
    sql = semantic._sql_stub(["Sales"], {"Sales": "SELECT * FROM sales_fact"})
    assert "FROM (SELECT * FROM sales_fact\n) AS sales" in sql


def test_sql_stub_skips_unsafe_table_map_entry():
    sql = semantic._sql_stub(["Sales"], {"Sales": "DELETE FROM sales_fact"})
    assert sql == "TODO -- see skill dax-to-teradata-sql; columns per kind"


# ----------------------------------------------------------------------------
# Table-map auto-detection from each table's own Power Query M source
# ----------------------------------------------------------------------------

_M_NATIVE_QUERY = (
    'let\n'
    '    Source = Value.NativeQuery(Teradata.Database("td.example.com", [HierarchicalNavigation=true]), '
    '"SELECT snd.sf_account_name, snd.gtm_acct_name#(lf)FROM ACC_TED_VW.syscfg_cld_node_cnt AS snd#(lf)'
    'WHERE snd.flag = 1 AND snd.name = ""X""", null, [EnableFolding=true])\n'
    'in\n'
    '    Source'
)
_M_DIRECT_REF = (
    'let\n'
    '    Source = Teradata.Database("td.example.com", [HierarchicalNavigation=true]),\n'
    '    dbo_MyTable = Source{[Schema="dbo",Item="MyTable"]}[Data]\n'
    'in\n'
    '    dbo_MyTable'
)
_M_MERGE = (
    'let\n'
    '    Source = Sql.Database("host", "db"),\n'
    '    a = Source{[Schema="dbo",Item="A"]}[Data],\n'
    '    b = Source{[Schema="dbo",Item="B"]}[Data],\n'
    '    merged = Table.NestedJoin(a, {"Id"}, b, {"Id"}, "b", JoinKind.Inner)\n'
    'in\n'
    '    merged'
)
_M_DYNAMIC_QUERY = 'Value.NativeQuery(Teradata.Database("h"), "SELECT * FROM " & tableName, null)'
_M_FILTERED_REF = (
    'let\n'
    '    Source = Teradata.Database("h"),\n'
    '    t = Source{[Schema="dbo",Item="A"]}[Data],\n'
    '    filtered = Table.SelectRows(t, each [flag] = 1)\n'
    'in\n'
    '    filtered'
)


def test_detect_table_query_lifts_native_query_verbatim():
    sql = semantic._detect_table_query(_M_NATIVE_QUERY)
    assert sql == ('SELECT snd.sf_account_name, snd.gtm_acct_name\n'
                    'FROM ACC_TED_VW.syscfg_cld_node_cnt AS snd\n'
                    'WHERE snd.flag = 1 AND snd.name = "X"')


def test_detect_table_query_recognizes_direct_table_reference():
    assert semantic._detect_table_query(_M_DIRECT_REF) == "SELECT * FROM dbo.MyTable"


def test_detect_table_query_ignores_merge_instead_of_reporting_one_side():
    # Regression: an earlier version of this matched {[Schema=...,Item=...]} anywhere
    # in the expression, so a table built by joining two sources ("merged") was wrongly
    # reported as just one of its two inputs ("SELECT * FROM dbo.A") — a wrong, silently
    # misleading answer, worse than leaving it blank for a person to fill in.
    assert semantic._detect_table_query(_M_MERGE) is None


def test_detect_table_query_ignores_dynamically_built_query():
    assert semantic._detect_table_query(_M_DYNAMIC_QUERY) is None


def test_detect_table_query_ignores_reference_with_an_extra_transform_step():
    assert semantic._detect_table_query(_M_FILTERED_REF) is None


def test_detect_table_map_from_power_query_end_to_end():
    model = {"power_query": [
        {"TableName": "TD_MANAGE_APP", "Expression": _M_NATIVE_QUERY},
        {"TableName": "MyTable", "Expression": _M_DIRECT_REF},
        {"TableName": "Merged", "Expression": _M_MERGE},
    ]}
    detected = semantic.detect_table_map_from_power_query(model)
    assert set(detected) == {"TD_MANAGE_APP", "MyTable"}
    assert detected["MyTable"] == "SELECT * FROM dbo.MyTable"


# ----------------------------------------------------------------------------
# Rule-based SQL auto-draft (scaffold's use of _draft_visual_sql)
# ----------------------------------------------------------------------------

MODEL = {
    "measures": [
        {"TableName": "Sales", "Name": "Net Revenue", "Expression": "SUM(Sales[Amount])"},
        {"TableName": "Sales", "Name": "Margin %", "Expression": "SUM(Sales[MarginAmount])"},
        {"TableName": "Sales", "Name": "Unfoldable",
         "Expression": "TOTALYTD([Net Revenue], Calendar[Date])"},   # time intelligence: still manual
    ],
    "relationships": [
        {"FromTable": "Sales", "FromColumn": "RegionId", "ToTable": "Region", "ToColumn": "Id"},
    ],
}
TABLE_MAP = {"Sales": "SELECT * FROM sales_fact", "Region": "SELECT * FROM region_dim"}


DAX_MEASURES = {
    ("Sales", "Net Revenue"): "SUM(Sales[Amount])",
    ("Sales", "Cost"): "SUM(Sales[CostAmount])",
    ("Sales", "Margin"): "[Net Revenue] - [Cost]",
    ("Sales", "Margin %"): "DIVIDE([Margin], [Net Revenue])",
    ("Sales", "North Revenue"): 'CALCULATE(SUM(Sales[Amount]), Region[Name] = "North")',
    ("Sales", "Big Orders"): "CALCULATE(COUNTROWS(Sales), Sales[Amount] > 1000)",
    ("Sales", "Orders"): "COUNTROWS(Sales)",
    ("Sales", "Customers"): "DISTINCTCOUNT(Sales[CustomerId])",
    ("Sales", "Loop"): "[Loop] + 1",
    ("Sales", "YTD"): "TOTALYTD([Net Revenue], Calendar[Date])",
    ("Sales", "Virtual"): "SUMX(SUMMARIZE(Sales, Sales[Id]), [Net Revenue])",
    ("Sales", "Line Total"): "SUMX(Sales, Sales[Qty] * Sales[Price])",
    ("Sales", "With Related"): "SUMX(Sales, Sales[Qty] * RELATED(Product[Price]))",
    ("Sales", "Row Measure"): "SUMX(Sales, [Net Revenue])",
    ("Sales", "Row Aggregate"): "SUMX(Sales, SUM(Sales[Qty]))",
    ("Sales", "Row Foreign"): "SUMX(Sales, Sales[Qty] * Product[Price])",
    ("Sales", "Filtered Rows"): "SUMX(FILTER(Sales, Sales[Qty] > 1), Sales[Qty])",
    ("Sales", "Tier"): 'SWITCH(TRUE(), [Net Revenue] > 100, "big", [Net Revenue] > 10, "mid", "small")',
    ("Sales", "Grade"): 'SWITCH(MAX(Sales[Grade]), 1, "one", 2, "two", "other")',
    ("Sales", "Safe Margin"): "IF(ISBLANK([Margin]), 0, [Margin])",
    ("Sales", "Two Regions"): 'CALCULATE(SUM(Sales[Amount]), Region[Name] IN {"North", "South"})',
    ("Sales", "Filter By Aggregate"): "CALCULATE(SUM(Sales[Amount]), SUM(Sales[Qty]) > 5)",
}


def _dax(name):
    return semantic.translate_dax(DAX_MEASURES[("Sales", name)], DAX_MEASURES)


def test_translate_dax_handles_plain_aggregates():
    assert _dax("Net Revenue").text == "SUM(sales.amount)"
    assert _dax("Orders").text == "COUNT(*)"
    assert _dax("Customers").text == "COUNT(DISTINCT sales.customerid)"


def test_translate_dax_composes_measures_out_of_other_measures():
    # The whole reason people still wrote every query by hand: the old translator was a
    # single regex for AGG(Table[Col]), so a measure referencing other measures — most
    # real ones — failed to resolve and took its visual down to a TODO with it.
    assert _dax("Margin").text == "((SUM(sales.amount)) - (SUM(sales.costamount)))"
    ratio = _dax("Margin %").text
    assert ratio.startswith("(CASE WHEN") and "DECIMAL(18,6)" in ratio   # DIVIDE, zero-safe
    assert "sales.costamount" in ratio                                   # resolved two levels deep


def test_translate_dax_folds_calculate_filters_into_the_aggregate():
    north = _dax("North Revenue")
    assert north.text == "SUM(CASE WHEN region.name = 'North' THEN sales.amount END)"
    assert north.tables == {"Sales", "Region"}          # the filter's table gets joined in
    assert _dax("Big Orders").text == "SUM(CASE WHEN sales.amount > 1000 THEN 1 ELSE 0 END)"


def test_an_unknown_aggregation_wrapper_never_becomes_a_phantom_table():
    """`First(T.name)` used to split into the table `First(T` and the column `name)`, because
    only the translatable aggregations were stripped. Both then sanitised *clean*
    (`_sql_alias("First(T") == "first_t"`), so a table that does not exist reached the mapping
    report as `unknown_table:First(T` and the panel's table-map step offered it for mapping —
    a card counting a text column ended up pointed at a phantom table instead of saying why."""
    assert semantic.query_ref_parts("First(T.name)") == ("First", "T", "name")
    assert semantic.query_ref_parts("Last(T.name)") == ("Last", "T", "name")
    assert semantic.query_ref_parts("Median(T.n)") == ("Median", "T", "n")
    assert semantic.query_ref_parts("T.name") == (None, "T", "name")        # unwrapped, unchanged
    # a measure whose own name has brackets is not an aggregation wrapper
    assert semantic.query_ref_parts("T.Margin (%)") == (None, "T", "Margin (%)")
    # and the reason names the aggregation, not a missing table or an innocent measure
    v = {"projections": {"Values": ["First(T.name)"]}}
    assert semantic.diagnose_visual(v, "card", {}, {"T": "SELECT * FROM t"}, [], {"T"}, {}) \
        == ["unsupported_aggregation:First(name)"]


def test_a_card_counting_a_text_column_counts_it():
    # Power BI auto-aggregates a text column dropped into a card: Count / Count (Distinct) /
    # Count (All). Each has an exact SQL equivalent, so each drafts rather than staying manual.
    tm = {"T": "SELECT name FROM db.t"}
    def card(ref):
        drafted = semantic._draft_visual_sql({"projections": {"Values": [ref]}}, "card", {}, tm, [], {})
        return drafted[0].split("\n")[0] if drafted else None
    assert card("Count(T.name)") == 'SELECT COUNT(t.name) AS "value"'
    assert card("CountNonNull(T.name)") == 'SELECT COUNT(t.name) AS "value"'
    assert card("DistinctCount(T.name)") == 'SELECT COUNT(DISTINCT t.name) AS "value"'
    assert card("CountAll(T.name)") == 'SELECT COUNT(*) AS "value"'         # blanks included
    # First/Last have no SQL equivalent (a table has no inherent order); MIN would be a
    # different number that merely looks plausible, so the visual stays manual.
    assert card("First(T.name)") is None


def test_translate_dax_iterates_a_plain_table_row_by_row():
    # SUMX over a physical table is the one iterator with an exact SQL equivalent: DAX's row
    # context is the table's own rows, which is what SUM(expr) already evaluates.
    assert _dax("Line Total").text == "SUM((sales.qty * sales.price))"
    related = _dax("With Related")
    assert related.text == "SUM((sales.qty * product.price))"
    assert related.tables == {"Sales", "Product"}        # RELATED is many-to-one: no fan-out
    assert _dax("Line Total").tables == {"Sales"}


def test_translate_dax_switch_becomes_a_case():
    assert _dax("Tier").text == ("(CASE WHEN (SUM(sales.amount)) > 100 THEN 'big' "
                                 "WHEN (SUM(sales.amount)) > 10 THEN 'mid' ELSE 'small' END)")
    grade = _dax("Grade").text                           # the value form, with a default
    assert grade.startswith("(CASE WHEN (MAX(sales.grade)) = (1) THEN 'one'") and grade.endswith("ELSE 'other' END)")
    # a bare column as the subject stays refused: it is not aggregated, so the query would not group
    assert semantic.translate_dax('SWITCH(Sales[Band], 1, "one", "other")', DAX_MEASURES) is None


def test_translate_dax_measures_and_aggregates_inside_an_if_condition():
    # `IF([M] > 0, [M], 0)` / `IF(ISBLANK([M]), 0, [M])` are everyday shapes; a condition used
    # to allow only columns and literals, so both took their whole visual down to a TODO.
    assert _dax("Safe Margin").text.startswith("(CASE WHEN ((((SUM(sales.amount)) - (SUM(sales.costamount)))) IS NULL)")
    # an enclosing CALCULATE still filters an aggregate used in the condition
    scoped = semantic.translate_dax('CALCULATE(IF([Net Revenue] > 0, [Net Revenue], 0), Region[Name] = "North")',
                                    DAX_MEASURES)
    assert scoped.text.count("CASE WHEN region.name = 'North' THEN sales.amount END") == 2


def test_translate_dax_in_a_literal_set_becomes_sql_in():
    assert _dax("Two Regions").text == "SUM(CASE WHEN region.name IN ('North', 'South') THEN sales.amount END)"


def test_translate_dax_refuses_what_it_cannot_do():
    # Honest blank beats a wrong number that looks right.
    assert _dax("Loop") is None                          # measure referencing itself
    assert _dax("YTD") is None                           # time intelligence
    assert _dax("Virtual") is None                       # virtual table / iterator
    assert semantic.translate_dax("", {}) is None
    assert semantic.translate_dax("'Sales Fact'[Amount]", {}) is None   # column, not a measure
    assert semantic.translate_dax("[Missing Measure]", {}) is None


def test_translate_dax_refuses_the_iterators_that_are_not_a_plain_sum():
    # Everything here would need the row context rebuilt in SQL, which is exactly where a
    # plausible-looking wrong number comes from. Each must stay manual.
    assert _dax("Filtered Rows") is None                 # FILTER(): a virtual table, not Sales' rows
    assert _dax("Row Measure") is None                   # a measure per row: context transition
    assert _dax("Row Aggregate") is None                 # an aggregate per row: context transition
    assert _dax("Row Foreign") is None                   # another table's column without RELATED
    assert semantic.translate_dax("RELATED(Product[Price])", DAX_MEASURES) is None   # no row context
    assert semantic.translate_dax("SUMX(Sales[Qty], Sales[Qty])", DAX_MEASURES) is None   # a column, not a table
    # an aggregate in a CALCULATE *filter* is a table filter, not a value condition
    assert _dax("Filter By Aggregate") is None
    # CEILING/FLOOR only translate to the plain "up to an integer" form
    assert semantic.translate_dax("CEILING(SUM(Sales[Amount]), 0.5)", DAX_MEASURES) is None


def test_scaffold_auto_drafts_single_table_card(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, MODEL, TABLE_MAP)
    v1 = sc["visuals"]["v1"]
    assert v1["sql"] == "SELECT SUM(sales.amount) AS \"value\"\nFROM (SELECT * FROM sales_fact\n) AS sales"
    assert v1["params"] == []
    assert "Auto-drafted" in v1["notes"]


def test_scaffold_auto_drafts_joined_bar_chart_with_slicer_filter(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, MODEL, TABLE_MAP)
    v2 = sc["visuals"]["v2"]
    assert v2["sql"] == (
        "SELECT region.name AS category, SUM(sales.marginamount) AS \"value\"\n"
        "FROM (SELECT * FROM region_dim\n) AS region\n"
        "JOIN (SELECT * FROM sales_fact\n) AS sales ON region.id = sales.regionid\n"
        "GROUP BY 1"
    )
    assert v2["params"] == []  # the "year" slicer is on Calendar, not Sales/Region — correctly not attached
    assert "duplicate" in v2["notes"].lower()  # join-fan-out warning (skill validate-report)


def test_scaffold_does_not_auto_draft_without_table_map(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, MODEL)  # no table_map at all
    assert "TODO" in sc["visuals"]["v1"]["sql"]
    assert "notes" not in sc["visuals"]["v1"] or "Auto-drafted" not in sc["visuals"]["v1"].get("notes", "")


def test_scaffold_falls_back_to_todo_for_untranslatable_dax():
    layout = {
        "report": "R", "source": None,
        "pages": [{"display_name": "P", "filters": [], "visuals": [{
            "id": "vx", "type": "card", "title": None, "filters": [], "is_group": False, "is_custom": False,
            "projections": {"Values": ["Sales.Unfoldable"]},
        }]}],
    }
    sc = semantic.scaffold(layout, MODEL, TABLE_MAP)
    assert "TODO" in sc["visuals"]["vx"]["sql"]


_RICH_MEASURES = {
    ("Sales", "Net Revenue"): "SUM(Sales[Amount])",
    ("Sales", "Cost"): "SUM(Sales[CostAmount])",
    ("Sales", "Margin"): "[Net Revenue] - [Cost]",
    ("Sales", "Margin %"): "DIVIDE([Margin], [Net Revenue])",
    ("Sales", "YTD"): "TOTALYTD([Net Revenue], Calendar[Date])",
}
_RICH_REL = [{"FromTable": "Sales", "FromColumn": "RegionId", "ToTable": "Region", "ToColumn": "Id"}]
_RICH_MAP = {"Sales": "SELECT * FROM sales_fact", "Region": "SELECT * FROM region_dim"}


def _draft(kind, projections, parameters=None):
    return semantic._draft_visual_sql({"projections": projections}, kind, _RICH_MEASURES,
                                      _RICH_MAP, _RICH_REL, parameters or {})


def test_draft_handles_a_measure_built_from_other_measures():
    sql, _ = _draft("card", {"Values": ["Sales.Margin %"]})
    assert sql.startswith("SELECT (CASE WHEN")
    assert "sales.costamount" in sql                     # resolved through two measures
    semantic.validate_read_only_sql(sql)                 # the editor would accept it


def test_draft_puts_several_measures_on_one_chart_as_series():
    # Revenue vs. cost vs. margin on one chart is ordinary; refusing it was a big part
    # of why so many visuals still came back as TODO.
    sql, _ = _draft("bar", {"Category": ["Region.Name"],
                            "Y": ["Sales.Net Revenue", "Sales.Cost", "Sales.Margin"]})
    assert sql.count("UNION ALL") == 2
    for label in ("'Net Revenue' AS series", "'Cost' AS series", "'Margin' AS series"):
        assert label in sql
    assert sql.count("GROUP BY 1, 2") == 3               # category × series, every arm
    semantic.validate_read_only_sql(sql)


def test_draft_applies_slicer_filters_to_every_union_arm():
    sql, params = _draft("bar", {"Category": ["Region.Name"],
                                 "Y": ["Sales.Net Revenue", "Sales.Cost"]},
                         {"year": {"from_slicer": "Sales.Year"}})
    assert params == ["year"]
    assert sql.count('sales."year" IN (:year)') == 2       # not just the first arm


def test_draft_now_covers_kpi_gauge_and_matrix():
    kpi, _ = _draft("kpi", {"Values": ["Sales.Net Revenue", "Sales.Cost"]})
    assert "AS \"value\"" in kpi and "AS target" in kpi
    assert _draft("gauge", {"Values": ["Sales.Net Revenue"]})[0].endswith("AS sales_fact") is False
    assert "AS \"value\"" in _draft("gauge", {"Values": ["Sales.Net Revenue"]})[0]
    assert _draft("matrix", {"Values": ["Region.Name", "Sales.Net Revenue"]}) is not None


def test_draft_still_refuses_what_it_cannot_translate():
    assert _draft("card", {"Values": ["Sales.YTD"]}) is None          # time intelligence
    assert _draft("card", {"Values": ["Region.Name"]}) is None        # a bare column isn't a measure
    assert _draft("line", {"Category": ["Calendar.Month"],            # Calendar is unmapped
                           "Y": ["Sales.Net Revenue"]}) is None


def test_draft_visual_sql_bails_when_join_path_is_missing():
    v = {"projections": {"Category": ["Region.Name"], "Y": ["Sum(Other.Amount)"]}}
    draft = semantic._draft_visual_sql(
        v, "bar", {}, {"Region": "SELECT * FROM region_dim", "Other": "SELECT * FROM other_fact"},
        [],  # no relationships at all
        {},
    )
    assert draft is None


def test_snapshot_html(fake_pbix, tmp_path):
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    values = semantic.resolve_params(spec, {"year": "2025"})
    assert values == {"year": 2025}
    be = FakeBackend(fixtures=FIX, calls=[])
    data = run_report(spec, values, be, use_cache=False)
    assert data["v1"]["rows"][0][0] == 1234567.8
    assert be.calls[0][1] == [2025]
    html = render_html(L, spec, values, data, mode="snapshot")
    assert 'id="v-v1"' in html and 'id="v-v2"' in html and 'id="v-v3"' in html   # a slicer is a widget now
    assert "#0F2B46" in html                                                        # pbix theme
    assert '"kind": "column"' in html                                              # custom reinterpreted
    (tmp_path / "r.html").write_text(html, encoding="utf-8")


def test_unsupported_kind_renders_a_friendly_placeholder_not_a_raw_error():
    # Regression: every visual whose Power BI type has no renderer (kind "unsupported"
    # — see build_spec) used to always try `R[v.kind]` first and fail with a raw
    # "No renderer for X" that looked like a crash, even for a freshly-scaffolded
    # visual that still had `sql: TODO` and was never queried. Confirmed against a
    # real browser render (see conversation) that: a TODO'd one now reads "No query
    # defined" like any other kind, and one with real sql gets an actionable message
    # instead of a raw error. This only checks the template source doesn't regress
    # back to the old wording — the JS itself isn't executed here.
    templates_dir = Path(__file__).resolve().parents[1] / "src/pbix2html/templates"
    for name in ("report.html.j2", "report_hah.html.j2"):
        src = (templates_dir / name).read_text(encoding="utf-8")
        assert "No renderer for" not in src
        assert "isn't supported yet" in src


def test_live_html_has_api_base(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    html = render_html(L, spec, {"year": 2026}, None, mode="live")
    assert "window.API_BASE" in html and 'id="data"' not in html


def test_validate_theme_override_rejects_bad_hex():
    with pytest.raises(ValueError):
        semantic.validate_theme_override({"background": "blue"})


def test_validate_theme_override_rejects_empty_object():
    with pytest.raises(ValueError):
        semantic.validate_theme_override({})


def test_validate_theme_override_accepts_flat_font_family():
    # The panel's manual form submits a flat `fontFamily`; a real Power BI export
    # instead nests it under textClasses.title.fontFace (render.py checks both).
    assert semantic.validate_theme_override({"fontFamily": "Georgia, serif"}) == {"fontFamily": "Georgia, serif"}


def test_validate_theme_override_drops_unrecognized_keys():
    # A pasted full Power BI theme export carries deep `visualStyles` formatting rules
    # with no renderer for them — silently dropped, not stored, so the override file's
    # purpose stays legible to whatever reads it back (only ever colors + font).
    out = semantic.validate_theme_override({"name": "Corp", "visualStyles": {"*": {}}, "background": "#FFFFFF"})
    assert out == {"background": "#FFFFFF"}


def test_apply_theme_override_is_noop_without_one(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    assert semantic.apply_theme_override(L, None) is L


def test_theme_override_replaces_extracted_colors_in_rendered_html(fake_pbix):
    # Regression scenario from the conversation: the .pbix's own extracted theme
    # (#0F2B46, a real custom theme in this fixture) should be fully replaceable by
    # an override, for the common real-world case where the extracted theme has no
    # usable colors at all (a built-in Power BI theme name, no dataColors of its own).
    L = ex.extract_layout(fake_pbix)
    override = semantic.validate_theme_override({"dataColors": ["#AA00AA"], "background": "#111111"})
    L = semantic.apply_theme_override(L, override)
    spec = semantic.load("Executive_Dashboard")
    html = render_html(L, spec, {"year": 2025}, None, mode="snapshot")
    assert "#AA00AA" in html and "#111111" in html
    assert "#0F2B46" not in html


def test_hah_html_renders(fake_pbix):
    """ADR-004: only checks the template renders and embeds what the client-side JS
    needs (sql/params per visual, the SQL_API endpoint) — not verified against a real HAH."""
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    html = render_html(L, spec, {"year": 2026}, None, mode="hah", hah_base="https://hah.example/dev")
    assert "https://hah.example/dev/static/echarts.min.js" in html
    assert '"https://hah.example/dev/api/execute"' in html
    assert '"sql":' in html and 'SELECT SUM' in html   # visual sql embedded for client-side fetch
    assert "bindSql" in html and "safeSql" in html
    assert "__SNAPSHOT_CAPTURE__" in html
    assert 'id="data"' not in html and "window.API_BASE" not in html


def test_compare_tolerance():
    a = {"columns": ["category", "value"], "rows": [["N", 10.0], ["S", 5.0]]}
    b = {"columns": ["category", "value"], "rows": [["N", 10.0000001], ["S", 6.0]]}
    diffs = compare(a, b, {"rel": 1e-6})
    assert len(diffs) == 1 and diffs[0].startswith("('S',)")


def test_table_map_detects_connector_query_option():
    """Real report shape: Teradata.Database(host, [.., Query="<SQL>"]) — not NativeQuery."""
    from pbix2html import semantic

    def m(query: str) -> str:
        return ('let\n    Source = Teradata.Database("host", [HierarchicalNavigation=true, '
                f'Query="{query}"])\nin\n    Source')

    model = {"power_query": [
        {"TableName": "A", "Expression": m('SELECT a, b#(lf)FROM db.t WHERE x = ""y""')},
        {"TableName": "B", "Expression": m("SEL d#(lf)FROM cal.days")},          # Teradata SEL
        {"TableName": "C", "Expression": m('SELECT 1" & "x')},                   # concatenated: skipped
        {"TableName": "D", "Expression": 'let Source = Table.FromRows({}) in Source'},
    ]}
    got = semantic.detect_table_map_from_power_query(model)
    assert got["A"] == 'SELECT a, b\nFROM db.t WHERE x = "y"'
    assert got["B"].startswith("SEL d")
    assert "C" not in got and "D" not in got


def test_group_chain_lists_ancestors_nearest_first():
    from pbix2html.render import _group_chain

    vs = [
        {"id": "g1", "is_group": True, "parent_group": None},
        {"id": "g2", "is_group": True, "parent_group": "g1"},
        {"id": "a", "parent_group": "g2"},
        {"id": "b", "parent_group": None},
        {"id": "loop1", "is_group": True, "parent_group": "loop2"},
        {"id": "loop2", "is_group": True, "parent_group": "loop1"},
    ]
    by_id = {v["id"]: v for v in vs}
    assert _group_chain(by_id["a"], by_id) == ["g2", "g1"]
    assert _group_chain(by_id["b"], by_id) == []
    assert len(_group_chain(by_id["loop1"], by_id)) <= 2               # a cycle terminates


def test_visual_link_parsing():
    from pbix2html.extract import _visual_link

    def link(**props):
        return {"visualLink": [{"properties": {
            k: {"expr": {"Literal": {"Value": v}}} for k, v in props.items()}}]}

    assert _visual_link(link(type="'PageNavigation'", navigationSection="'p2'", show="true")) == \
        {"type": "page", "page": "p2", "enabled": True}
    assert _visual_link(link(type="'Bookmark'", bookmark="'b1'", show="false")) == \
        {"type": "bookmark", "bookmark": "b1", "enabled": False}
    assert _visual_link(link(type="'WebUrl'")) is None and _visual_link({}) is None


def test_hidden_page_reachable_by_a_button_is_rendered_and_wired(fake_pbix):
    """A visible page's button targets a hidden page ("Historic Data" view): that page is
    rendered without a tab and the button navigates; unlinked hidden pages stay out."""
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    base = L["pages"][0]
    hist = {**base, "name": "histId", "display_name": "HST", "hidden": True,
            "visuals": [{**base["visuals"][0], "id": "h1", "action": {"type": "page", "page": base["name"], "enabled": True}}]}
    tooltip = {**base, "name": "tipId", "display_name": "Tip", "hidden": True, "visuals": []}
    button = {**base["visuals"][0], "id": "btn", "type": "actionButton",
              "action": {"type": "page", "page": "histId", "enabled": True}}
    dead = {**base["visuals"][0], "id": "dead", "type": "actionButton",
            "action": {"type": "page", "page": "missing", "enabled": True}}
    L2 = {**L, "pages": [{**base, "visuals": base["visuals"] + [button, dead]}, hist, tooltip]}
    html = render_html(L2, spec, {"year": 2025}, None, mode="live")
    assert 'id="page-1"' in html and 'class="page navonly"' in html   # reachable hidden page
    assert 'id="page-2"' not in html                                   # tooltip-style page: not linked
    assert 'data-nav="page-1"' in html and 'data-nav="page-0"' in html  # button + way back
    assert html.count("data-nav=") == 2                                # dangling target stays inert
    assert html.count('role="tab"') == 0                                # one visible page: no tab bar


def _bm_pages():
    def g(i, title, hidden=False):
        return {"id": i, "is_group": True, "title": title, "hidden": hidden, "parent_group": None}
    hist = {"name": "H", "display_name": "HST", "visuals": [g("h_org", "Org"), g("h_l1", "Lvl 1"), g("h_mod", "Model")]}
    cur = {"name": "C", "display_name": "Cur", "visuals": [g("c_org", "Org"), g("c_l1", "Lvl 1", True), g("c_mod", "Model")]}
    bm = {"id": "b1", "name": "By Lvl 1", "page": "H",
          "groups": {"h_org": True, "h_l1": False, "h_mod": True},
          "targets": ["h_org", "h_l1"], "apply_only_to_targets": True}
    return hist, cur, {"b1": bm}


def test_bookmark_action_targets_and_direct_ids():
    from pbix2html.render import _bookmark_action

    hist, cur, bms = _bm_pages()
    warns: list[str] = []
    act = {"type": "bookmark", "bookmark": "b1", "enabled": True}
    got = _bookmark_action(act, bms, hist, [hist, cur], warns)
    # only the targeted groups change (h_mod is not a target), ids used as they are
    assert got == {"type": "bookmark", "set": {"h_org": True, "h_l1": False}} and warns == []


def test_bookmark_action_remaps_a_cloned_page_by_group_name_and_warns():
    from pbix2html.render import _bookmark_action

    hist, cur, bms = _bm_pages()
    warns: list[str] = []
    got = _bookmark_action({"type": "bookmark", "bookmark": "b1", "enabled": True}, bms, cur, [hist, cur], warns)
    assert got == {"type": "bookmark", "set": {"c_org": True, "c_l1": False}}
    assert len(warns) == 1 and "applied by group name" in warns[0]
    # ambiguous name on the clone -> that group is not mapped; nothing mappable -> inert
    cur["visuals"].append({"id": "c_org2", "is_group": True, "title": "Org", "parent_group": None})
    got = _bookmark_action({"type": "bookmark", "bookmark": "b1", "enabled": True}, bms, cur, [hist, cur], [])
    assert got == {"type": "bookmark", "set": {"c_l1": False}}
    other = {"name": "X", "display_name": "X", "visuals": [{"id": "z", "is_group": True, "title": "Other"}]}
    assert _bookmark_action({"type": "bookmark", "bookmark": "b1", "enabled": True}, bms, other, [hist, other], []) is None
    assert _bookmark_action({"type": "bookmark", "bookmark": "nope", "enabled": True}, bms, cur, [hist, cur], []) is None
    assert _bookmark_action({"type": "bookmark", "bookmark": "b1", "enabled": False}, bms, cur, [hist, cur], []) is None


def test_parse_bookmarks_reads_group_state_and_targets():
    from pbix2html.extract import parse_bookmarks

    cfg = {"bookmarks": [{"name": "b1", "displayName": "By Org", "options": {
        "targetVisualNames": ["g1"], "applyOnlyToTargetVisuals": True},
        "explorationState": {"activeSection": "s1", "sections": {"s1": {"visualContainerGroups": {
            "g1": {"isHidden": False}, "g2": {"isHidden": True}}}}}}]}
    assert parse_bookmarks(cfg) == [{"id": "b1", "name": "By Org", "page": "s1",
                                     "groups": {"g1": False, "g2": True}, "targets": ["g1"],
                                     "apply_only_to_targets": True}]
    assert parse_bookmarks({}) == []


def test_pbir_bookmarks_read_into_the_same_shape_as_classic(tmp_path):
    """PBIR keys per a documented (unverified — ADR-005) bookmark.json schema: one file per
    bookmark under Report/definition/bookmarks/, an index listing their ids, each carrying
    explorationState.sections[*].visualContainers[*].singleVisual.display.mode instead of
    classic's visualContainerGroups[*].isHidden."""
    import json
    import zipfile

    from pbix2html.extract import extract_layout

    bm = {"name": "b1", "displayName": "By Org", "options": {
        "targetVisualNames": ["g1"], "applyOnlyToTargetVisuals": True},
        "explorationState": {"activeSection": "p1", "sections": {"p1": {"visualContainers": {
            "g1": {"singleVisual": {"display": {"mode": "visible"}}},
            "g2": {"singleVisual": {"display": {"mode": "hidden"}}}}}}}}
    pbix = tmp_path / "R.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/definition/report.json", json.dumps({}))
        z.writestr("Report/definition/pages/pages.json", json.dumps({"pageOrder": ["p1"]}))
        z.writestr("Report/definition/pages/p1/page.json", json.dumps({"displayName": "P"}))
        z.writestr("Report/definition/bookmarks/bookmarks.json", json.dumps({"items": [{"name": "b1"}]}))
        z.writestr("Report/definition/bookmarks/b1.bookmark.json", json.dumps(bm))
    assert extract_layout(pbix)["bookmarks"] == [{"id": "b1", "name": "By Org", "page": "p1",
                                                  "groups": {"g1": False, "g2": True}, "targets": ["g1"],
                                                  "apply_only_to_targets": True}]


def test_pbir_group_hidden_flag_is_read_like_classics_ishidden(tmp_path):
    """PBIR carries a group's hidden state as `isHidden` on the group's own visual.json, same
    key as a leaf visual — unverified against a real file (every real PBIR sample seen so far
    had no hidden groups), but it's the same field the PBIR schema already confirms for leaf
    visuals (test_pbir_filters_hidden_and_custom_visuals)."""
    import json
    import zipfile

    from pbix2html.extract import extract_layout

    pbix = tmp_path / "R.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/definition/report.json", json.dumps({}))
        z.writestr("Report/definition/pages/pages.json", json.dumps({"pageOrder": ["p1"]}))
        z.writestr("Report/definition/pages/p1/page.json", json.dumps({"displayName": "P"}))
        z.writestr("Report/definition/pages/p1/visuals/g1/visual.json", json.dumps({
            "position": {"x": 0, "y": 0, "width": 10, "height": 10}, "isHidden": True,
            "visualGroup": {"displayName": "Alt view"}}))
    g = extract_layout(pbix)["pages"][0]["visuals"][0]
    assert g["is_group"] is True and g["hidden"] is True and g["title"] == "Alt view"


def test_pbir_bookmarks_fall_back_to_the_files_present_without_an_index(tmp_path):
    import json
    import zipfile

    from pbix2html.extract import extract_layout

    bm = {"name": "b1", "displayName": "Solo", "explorationState": {"sections": {"p1": {}}}}
    pbix = tmp_path / "R.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/definition/report.json", json.dumps({}))
        z.writestr("Report/definition/pages/pages.json", json.dumps({"pageOrder": ["p1"]}))
        z.writestr("Report/definition/pages/p1/page.json", json.dumps({"displayName": "P"}))
        z.writestr("Report/definition/bookmarks/b1.bookmark.json", json.dumps(bm))
    bms = extract_layout(pbix)["bookmarks"]
    assert bms == [{"id": "b1", "name": "Solo", "page": "p1", "groups": {}, "targets": [],
                    "apply_only_to_targets": False}]
