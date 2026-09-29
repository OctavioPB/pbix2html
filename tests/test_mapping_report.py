"""mapping_report: why each data visual could or couldn't be drafted into SQL."""
from pbix2html import semantic

MODEL = {
    "tables": ["Sales", "Region", "Calendar", "Orphan"],
    "measures": [
        {"TableName": "Sales", "Name": "Total", "Expression": "SUM(Sales[Amount])"},
        {"TableName": "Sales", "Name": "Double", "Expression": "[Total] * 2"},
    ],
    "relationships": [{"FromTable": "Sales", "FromColumn": "rid", "ToTable": "Region", "ToColumn": "id",
                       "Cardinality": "M:M"}],
    "calculated_tables": [{"TableName": "Calendar", "Expression": "CALENDAR(\"2026-01-01\", TODAY())"}],
    "calculated_columns": [{"TableName": "Calendar", "ColumnName": "Year"}],
    "power_query": [{"TableName": "Sales", "Expression": "let s = 1 in s // SELECT x, d AS log_dt"}],
    "rls": [],
}
TABLE_MAP = {"Sales": "SELECT * FROM db.sales", "Region": "SELECT * FROM db.region"}


def _visual(vtype, **projections):
    return {"id": vtype + str(len(projections)), "type": vtype, "title": None, "projections": projections}


def test_reasons_cover_each_failure_mode():
    layout = {"report": "R", "pages": [{"display_name": "P", "visuals": [
        _visual("card", Values=["Sum(Sales.Amount)"]),                                   # ok
        _visual("clusteredColumnChart", Category=["Calendar.Year"], Y=["Sum(Sales.Amount)"]),   # no source
        _visual("card", Values=["Sum(Ghost.Amount)"]),                                   # renamed/deleted table
        _visual("tableEx", Values=["Orphan.x"]),                                          # no source (unrelated table)
        {"id": "g", "type": "x", "is_group": True, "projections": {}},
        {"id": "t", "type": "textbox", "projections": {}},                               # no data: ignored
    ]}]}
    rep = semantic.mapping_report(layout, MODEL, TABLE_MAP)
    v = rep["visuals"]
    assert v["data_visuals"] == 4 and v["drafted"] == 1
    assert set(v["by_reason"]) == {"no_source:Calendar", "unknown_table:Ghost", "no_source:Orphan"}
    m = rep["model"]
    assert m["unmapped"] == ["Orphan"] and set(m["calculated_tables"]) == {"Calendar"}
    assert m["many_to_many"] == ["Sales → Region"] and m["composite_measures"] == ["Double"]
    assert m["unrelated_tables"] == ["Calendar", "Orphan"] and m["date_key_hint"] == ["Sales"]
    md = semantic.render_mapping_report(rep)
    assert "no_source:Calendar" in md and "log_dt" in md and "Many-to-many" in md


def test_not_connected_is_reported_for_tables_without_a_path():
    model = {**MODEL, "relationships": []}
    layout = {"report": "R", "pages": [{"display_name": "P", "visuals": [
        _visual("clusteredColumnChart", Category=["Region.name"], Y=["Sum(Sales.Amount)"])]}]}
    rep = semantic.mapping_report(layout, model, TABLE_MAP)
    assert list(rep["visuals"]["by_reason"]) == ["not_connected:Region+Sales"]
