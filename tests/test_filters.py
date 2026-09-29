"""Filter-pane filters (report / page / visual level) become WHERE predicates in drafted SQL."""
import pytest

from pbix2html import semantic as S
from pbix2html.query import bind

L = lambda v: {"Literal": {"Value": v}}                                   # noqa: E731
COL = lambda p: {"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": p}}   # noqa: E731


def flt(target, cond, type_="Categorical", **extra):
    return {"target": target, "type": type_, "definition": {"Version": 2, "Where": [{"Condition": cond}]}, **extra}


def IN(prop, *vals):
    return {"In": {"Expressions": [COL(prop)], "Values": [[L(v)] for v in vals]}}


def sql_of(f):
    parsed = S.filter_sql(f)
    return parsed and parsed[2].replace(S._COL, "x")


def test_in_and_not_in_keep_blanks_the_way_power_bi_does():
    assert sql_of(flt("T.c", IN("c", "'a'", "'it''s'"))) == "(x IN ('a', 'it''s'))"
    assert sql_of(flt("T.c", {"Not": {"Expression": IN("c", "'a'")}})) == "(NOT ((x IN ('a'))) OR x IS NULL)"
    assert sql_of(flt("T.c", IN("c", "1L", "null"))) == "(x IN (1) OR x IS NULL)"


def test_comparisons_ranges_and_dates():
    cmp = lambda k, v: {"Comparison": {"ComparisonKind": k, "Left": COL("c"), "Right": L(v)}}   # noqa: E731
    assert sql_of(flt("T.c", cmp(3, "100L"), "Advanced")) == "x < 100"
    assert sql_of(flt("T.c", cmp(0, "null"), "Advanced")) == "x IS NULL"
    both = {"And": {"Left": cmp(2, "datetime'2024-01-01T00:00:00'"), "Right": cmp(4, "12.5D")}}
    assert sql_of(flt("T.c", both, "Advanced")) == "(x >= DATE '2024-01-01' AND x <= 12.5)"


def test_text_search_escapes_like_wildcards():
    c = {"Contains": {"Left": COL("c"), "Right": L("'50%_off'")}}
    assert sql_of(flt("T.c", c, "Advanced")) == "x LIKE '%50\\%\\_off%' ESCAPE '\\'"


def test_unsupported_shapes_are_not_translated():
    assert S.filter_sql(flt("T.c", IN("c", "true"))) is None                      # Teradata has no boolean
    assert S.filter_sql(flt("T.c", IN("c", "'a'"), aggregation=1)) is None        # a filter on Sum(T.c)
    assert S.filter_sql({"target": "T.c", "type": "Categorical", "definition": None}) is None
    assert S.filter_sql(flt("T.c", {"Exists": {}})) is None


TM = {"Fact": "SELECT k, region, n FROM db.fact", "Dim": "SELECT k, name FROM db.dim"}
RELS = [{"FromTableName": "Fact", "FromColumnName": "k", "ToTableName": "Dim", "ToColumnName": "k",
         "IsActive": 1, "Cardinality": "M:1"}]
V = {"projections": {"Values": ["Sum(Fact.n)"]}}


def card(filters):
    return S._draft_visual_sql(V, "card", {}, TM, RELS, {}, filters)


def test_filter_on_a_table_the_visual_reads_is_a_plain_predicate():
    sql, params = card([flt("Fact.region", IN("region", "'EU'"))])
    assert "WHERE (fact.region IN ('EU'))" in sql and params == []


def test_filter_on_a_related_table_becomes_a_semi_join_and_unrelated_ones_are_ignored():
    sql, _ = card([flt("Dim.name", IN("name", "'x'"))])
    assert "fact.k IN (SELECT k FROM (SELECT k, name FROM db.dim\n) AS dim WHERE (dim.name IN ('x')))" in sql
    assert "WHERE" not in card([flt("Other.z", IN("z", "'x'"))])[0]
    assert S.unapplied_filters([flt("Other.z", IN("z", "'x'"))], {"Fact"}, TM, RELS) == []   # can't affect it


def test_unapplied_filters_are_reported_for_shapes_that_do_affect_the_visual():
    agg = flt("Fact.n", {"Comparison": {"ComparisonKind": 3, "Left": COL("n"), "Right": L("5L")}},
              "Advanced", aggregation=0)
    assert S.unapplied_filters([agg], {"Fact"}, TM, RELS) == ["Fact.n (Advanced, on an aggregate)"]


def test_filters_from_all_three_levels_apply_to_a_visual():
    layout = {"filters": [flt("Fact.region", IN("region", "'EU'"))]}
    page = {"filters": [flt("Fact.region", IN("region", "'US'"))]}
    v = {"filters": [flt("Fact.region", IN("region", "'JP'")), {"target": "Fact.k", "definition": None}]}
    assert len(S.effective_filters(layout, page, v)) == 3


def test_top_n_filter_ranks_the_values_by_an_aggregate_over_the_slicer_selection():
    sub = {"Name": "subquery", "Expression": {"Subquery": {"Query": {
        "From": [{"Name": "h", "Entity": "Dim"}], "Select": [COL("name")], "Top": 2,
        "OrderBy": [{"Direction": 2, "Expression": {"Aggregation": {"Expression": {"Column": {
            "Expression": {"SourceRef": {"Source": "h"}}, "Property": "k"}}, "Function": 0}}}]}}}}
    f = {"target": "Dim.name", "type": "TopN", "definition": {
        "From": [sub], "Where": [{"Condition": {"In": {"Expressions": [COL("name")], "Table": {}}}}]}}
    params = {"names": {"from_slicer": "Dim.name", "multi": True}}
    sql, used = S._draft_visual_sql(V, "card", {}, TM, RELS, params, [f])
    assert "QUALIFY RANK() OVER (ORDER BY a DESC) <= 2" in sql and "SUM(h.k) AS a" in sql
    assert "h.name IN (:names)" in sql and used == ["names"]          # the slicer narrows what is ranked
    assert bind(sql, used, {"names": []})[0].count("1=1") >= 1
    sqlglot = pytest.importorskip("sqlglot")
    sqlglot.parse_one(bind(sql, used, {"names": ["a"]})[0].replace("?", "'x'"), read="teradata")
