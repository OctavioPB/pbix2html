"""Selection-dependent DAX: FILTER(T, T[c] = MIN(T[c])) (a hierarchy slicer's top level)."""
import pytest

from pbix2html import semantic as S
from pbix2html.query import bind

MEASURES = {
    ("Fact", "Top"): "CALCULATE(SUM(Fact[n]), FILTER('Hier', 'Hier'[lvl] = MIN('Hier'[lvl])))",
    ("Fact", "Female"): "Calculate(Fact[Top], Fact[gender] = \"Female\") + 0",
    ("Fact", "Share"): "CALCULATE(Fact[Top], FILTER(Fact, Fact[gender] = \"Female\")) / SUM(Fact[n])",
    ("Fact", "Odd"): "CALCULATE(SUM(Fact[n]), FILTER('Hier', 'Hier'[lvl] = MAX('Hier'[lvl])))",
}
TABLE_MAP = {"Fact": "SELECT leader, gender, n FROM db.fact",
             "Hier": "SELECT lowest_grain, lvl, name FROM db.hier"}
RELS = [{"FromTableName": "Fact", "FromColumnName": "leader", "ToTableName": "Hier",
         "ToColumnName": "lowest_grain", "IsActive": 1, "Cardinality": "M:1"}]
PARAMS = {"leaders__p1": {"from_slicer": "Hier.name", "multi": True}}


def card(measure):
    v = {"projections": {"Y": [f"Fact.{measure}"]}}
    return S._draft_visual_sql(v, "card", MEASURES, TABLE_MAP, RELS, PARAMS)


def test_selection_min_becomes_a_join_not_a_subquery_in_the_aggregate():
    sql, params = card("Top")
    assert "SUM(CASE WHEN selmin1.k IS NOT NULL THEN fact.n END)" in sql
    assert "LEFT JOIN (SELECT DISTINCT k" in sql and "MIN(hier.lvl) OVER ()" in sql
    assert "ON fact.leader = selmin1.k" in sql
    assert "leaders__p1" in params
    assert "{SELMIN" not in sql


def test_nested_measure_keeps_its_extra_filter_next_to_the_marker():
    sql, _ = card("Female")
    assert "fact.gender = 'Female' AND selmin1.k IS NOT NULL" in sql


def test_ratio_denominator_is_not_restricted_to_the_selection_level():
    sql, _ = card("Share")
    assert sql.count("selmin1.k IS NOT NULL") == 1     # only the numerator carries it
    assert "SUM(fact.n)" in sql


def test_no_slicer_selection_takes_the_minimum_over_the_whole_table():
    sql, params = card("Top")
    bound, values = bind(sql, params, {"leaders__p1": []})
    assert "1=1" in bound and values == []
    bound, values = bind(sql, params, {"leaders__p1": ["A", "B"]})
    # once for the join's selection, once for the fact table's own slicer filter (WHERE)
    assert values == ["A", "B", "A", "B"]


def test_other_shapes_stay_manual():
    assert card("Odd") is None                      # MAX, not MIN: not the pattern


def test_missing_relationship_or_source_leaves_the_visual_manual():
    v = {"projections": {"Y": ["Fact.Top"]}}
    assert S._draft_visual_sql(v, "card", MEASURES, TABLE_MAP, [], PARAMS) is None
    assert S._draft_visual_sql(v, "card", MEASURES, {"Fact": TABLE_MAP["Fact"]}, RELS, PARAMS) is None


def test_generated_sql_parses_as_teradata():
    sqlglot = pytest.importorskip("sqlglot")
    sql, params = card("Female")
    bound, _ = bind(sql, params, {"leaders__p1": ["A"]})
    sqlglot.parse_one(bound.replace("?", "'x'"), read="teradata")
