"""Sort definitions: read from PBIR / classic and turned into ORDER BY in drafted SQL."""
from pbix2html import extract as ex, semantic


def _col(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def test_pbir_sort_definition():
    vis = {"query": {"sortDefinition": {"sort": [
        {"field": {"Aggregation": {"Expression": _col("Sales", "Amount"), "Function": 0}}, "direction": "Descending"},
        {"field": _col("Calendar", "Date"), "direction": "Ascending"}]}}}
    assert ex._pbir_sort(vis) == [
        {"entity": "Sales", "property": "Amount", "direction": "desc"},
        {"entity": "Calendar", "property": "Date", "direction": "asc"}]
    assert ex._pbir_sort({}) == []


def test_classic_order_by_resolves_source_aliases():
    sv = {"prototypeQuery": {
        "From": [{"Name": "s", "Entity": "Sales"}],
        "OrderBy": [{"Direction": 2, "Expression": {"Aggregation": {"Expression": {"Column": {
            "Expression": {"SourceRef": {"Source": "s"}}, "Property": "Amount"}}, "Function": 0}}}]}}
    assert ex._proto_sort(sv) == [{"entity": "Sales", "property": "Amount", "direction": "desc"}]


def test_order_by_uses_column_positions_and_skips_unselected_fields():
    pos = {("Sales", "Region"): 1, ("Sales", "Amount"): 2}
    sort = [{"entity": "Sales", "property": "Amount", "direction": "desc"},
            {"entity": "Sales", "property": "Hidden", "direction": "asc"},     # not selected: skipped
            {"entity": "Sales", "property": "Region", "direction": "asc"}]
    assert semantic._order_by(sort, pos) == "\nORDER BY 2 DESC, 1 ASC"
    assert semantic._order_by([], pos) == "" and semantic._order_by(None, pos) == ""


def test_drafted_chart_carries_the_visuals_sort():
    visual = {"projections": {"Category": ["Sales.Region"], "Y": ["Sum(Sales.Amount)"]},
              "sort": [{"entity": "Sales", "property": "Amount", "direction": "desc"}]}
    draft = semantic._draft_visual_sql(visual, "column", {}, {"Sales": "SELECT * FROM db.sales"}, [], {})
    assert draft is not None
    sql, _ = draft
    assert sql.rstrip().endswith("GROUP BY 1\nORDER BY 2 DESC")
