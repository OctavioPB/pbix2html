"""Full pipeline without Teradata: extract → yaml → FakeBackend → render → HTML."""
import json
from pbix2html import extract as ex, semantic
from pbix2html.query import FakeBackend, bind, run_report
from pbix2html.render import render_html, build_spec
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
    assert "FROM (SELECT * FROM sales_fact) AS sales" in sql


def test_sql_stub_skips_unsafe_table_map_entry():
    sql = semantic._sql_stub(["Sales"], {"Sales": "DELETE FROM sales_fact"})
    assert sql == "TODO -- see skill dax-to-teradata-sql; columns per kind"


# ----------------------------------------------------------------------------
# Rule-based SQL auto-draft (scaffold's use of _draft_visual_sql)
# ----------------------------------------------------------------------------

MODEL = {
    "measures": [
        {"TableName": "Sales", "Name": "Net Revenue", "Expression": "SUM(Sales[Amount])"},
        {"TableName": "Sales", "Name": "Margin %", "Expression": "SUM(Sales[MarginAmount])"},
        {"TableName": "Sales", "Name": "Unfoldable", "Expression": "CALCULATE([Net Revenue], Region[Name]=\"X\")"},
    ],
    "relationships": [
        {"FromTable": "Sales", "FromColumn": "RegionId", "ToTable": "Region", "ToColumn": "Id"},
    ],
}
TABLE_MAP = {"Sales": "SELECT * FROM sales_fact", "Region": "SELECT * FROM region_dim"}


def test_translate_measure_expression_recognizes_simple_aggregates():
    assert semantic._translate_measure_expression("SUM(Sales[Amount])") == ("SUM", "Sales", "Amount", False)
    assert semantic._translate_measure_expression("COUNTROWS(Sales)") == ("COUNT", "Sales", None, False)
    assert semantic._translate_measure_expression("DISTINCTCOUNT(Customers[Id])") == \
        ("COUNT", "Customers", "Id", True)
    assert semantic._translate_measure_expression("'Sales Fact'[Amount]") is None  # not a call, no agg
    assert semantic._translate_measure_expression("CALCULATE(SUM(Sales[Amount]), Region[Name]=\"X\")") is None
    assert semantic._translate_measure_expression("DIVIDE([A],[B])") is None
    assert semantic._translate_measure_expression("") is None


def test_scaffold_auto_drafts_single_table_card(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, MODEL, TABLE_MAP)
    v1 = sc["visuals"]["v1"]
    assert v1["sql"] == "SELECT SUM(sales.amount) AS value\nFROM (SELECT * FROM sales_fact) AS sales"
    assert v1["params"] == []
    assert "Auto-drafted" in v1["notes"]


def test_scaffold_auto_drafts_joined_bar_chart_with_slicer_filter(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, MODEL, TABLE_MAP)
    v2 = sc["visuals"]["v2"]
    assert v2["sql"] == (
        "SELECT region.name AS category, SUM(sales.marginamount) AS value\n"
        "FROM (SELECT * FROM region_dim) AS region\n"
        "JOIN (SELECT * FROM sales_fact) AS sales ON region.id = sales.regionid\n"
        "GROUP BY 1"
    )
    assert v2["params"] == []  # the "year" slicer is on Calendar, not Sales/Region — correctly not attached
    assert "duplicate" in v2["notes"].lower()  # join-fan-out warning (skill validate-report)


def test_scaffold_does_not_auto_draft_without_table_map(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, MODEL)  # no table_map at all
    assert "TODO" in sc["visuals"]["v1"]["sql"]
    assert "notes" not in sc["visuals"]["v1"] or "Auto-drafted" not in sc["visuals"]["v1"].get("notes", "")


def test_scaffold_falls_back_to_todo_for_unrecognized_dax():
    layout = {
        "report": "R", "source": None,
        "pages": [{"display_name": "P", "filters": [], "visuals": [{
            "id": "vx", "type": "card", "title": None, "filters": [], "is_group": False, "is_custom": False,
            "projections": {"Values": ["Sales.Unfoldable"]},
        }]}],
    }
    sc = semantic.scaffold(layout, MODEL, TABLE_MAP)
    assert "TODO" in sc["visuals"]["vx"]["sql"]


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
    assert 'id="v-v1"' in html and 'id="v-v2"' in html and 'id="v-v3"' not in html   # slicers aren't drawn
    assert "#0F2B46" in html                                                        # pbix theme
    assert '"kind": "column"' in html                                              # custom reinterpreted
    (tmp_path / "r.html").write_text(html)


def test_live_html_has_api_base(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    html = render_html(L, spec, {"year": 2026}, None, mode="live")
    assert "window.API_BASE" in html and 'id="data"' not in html


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
