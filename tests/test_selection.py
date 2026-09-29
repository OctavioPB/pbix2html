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


# ---- MIN/MAX of a column over the selection, VAR/RETURN, month comparisons -------------------

DATE_MEASURES = {
    ("Fact", "Start"): "var d = min('Cal'[Date])\nRETURN CALCULATE(SUM(Fact[n]), FILTER(Fact, "
                       "CONCATENATE(MONTH(Fact[dt]), YEAR(Fact[dt])) == CONCATENATE(MONTH(d), YEAR(d))))",
    ("Fact", "OwnMin"): "var unused = min('Cal'[Date])\nvar m = min(Fact[dt])\nRETURN CALCULATE(SUM(Fact[n]), "
                        "FILTER(Fact, MONTH(Fact[dt]) == MONTH(m)))",
    ("Fact", "Pick"): "var d = max('Cal'[Date])\nRETURN IF(MONTH(d) == MONTH(TODAY()), SUM(Fact[n]), 0)",
}
DATE_MAP = {"Fact": "SELECT dt, n FROM db.fact",
            "Cal": 'SELECT calendar_date AS "Date", calendar_date AS month_end FROM sys_calendar.calendar'}
DATE_RELS = [{"FromTableName": "Fact", "FromColumnName": "dt", "ToTableName": "Cal",
              "ToColumnName": "Date", "IsActive": 1, "Cardinality": "M:1"}]
DATE_PARAMS = {"months": {"from_slicer": "Cal.month_end", "multi": True}}


def date_card(measure):
    v = {"projections": {"Y": [f"Fact.{measure}"]}}
    return S._draft_visual_sql(v, "card", DATE_MEASURES, DATE_MAP, DATE_RELS, DATE_PARAMS)


def test_min_of_a_column_outside_the_visual_is_a_one_row_join_over_its_slicer():
    sql, params = date_card("Start")
    assert "CROSS JOIN (" in sql and "SELECT MIN(ctx.\"date\") AS v" in sql
    assert "ctx.month_end IN (:months)" in sql and "EXTRACT(MONTH FROM ctx1.v)" in sql
    assert "{CTX" not in sql and "months" in params
    bound, values = bind(sql, params, {"months": []})
    assert "ctx.month_end IN" not in bound            # no selection: the whole calendar


def test_min_of_the_visuals_own_table_reads_the_same_filtered_rows_and_unused_vars_are_dropped():
    sql, _ = date_card("OwnMin")
    assert "MIN(fact.dt) AS v" in sql and "ctx1" in sql and "ctx2" not in sql
    assert "Cal" not in sql.split("FROM", 1)[0]       # the unused VAR added nothing


def test_if_with_a_scalar_condition_is_a_case_expression():
    sql, _ = date_card("Pick")
    assert "CASE WHEN EXTRACT(MONTH FROM ctx1.v) = EXTRACT(MONTH FROM CURRENT_DATE) THEN SUM(fact.n) ELSE 0 END" in sql


def test_date_context_sql_parses_as_teradata():
    sqlglot = pytest.importorskip("sqlglot")
    sql, params = date_card("Start")
    bound, _ = bind(sql, params, {"months": ["x"]})
    sqlglot.parse_one(bound.replace("?", "'x'"), read="teradata")


# ---- per-category evaluation in grouped visuals ---------------------------------------------

def chart(measure, category="Cal.month_end", kind="column", extra=None):
    proj = {"Category": [category], "Y": [f"Fact.{measure}"]}
    v = {"projections": proj}
    maps = {**DATE_MAP, "Dim": "SELECT k, name FROM db.dim", **(extra or {})}
    rels = DATE_RELS + [{"FromTableName": "Fact", "FromColumnName": "k", "ToTableName": "Dim",
                         "ToColumnName": "k", "IsActive": 1, "Cardinality": "M:1"}]
    return S._draft_visual_sql(v, kind, DATE_MEASURES, maps, rels, DATE_PARAMS)


def test_min_over_a_calendar_category_is_computed_per_group_of_that_calendar_column():
    sql, params = chart("Start")
    assert "LEFT JOIN (\nSELECT ctx.month_end AS k1, MIN(ctx.\"date\") AS v" in sql
    assert "GROUP BY 1\n) AS ctx1 ON ctx1.k1 = cal.month_end" in sql
    assert sql.rstrip().endswith("GROUP BY 1, ctx1.v")
    assert "months" in params and "{CTX" not in sql
    # the group's value comes from the calendar alone: no fact rows in that inner query
    inner = sql.split("LEFT JOIN (", 1)[1].split(") AS ctx1", 1)[0]
    assert "db.fact" not in inner


def test_min_of_the_visuals_own_fact_is_computed_per_group_over_the_visuals_rows():
    sql, _ = chart("OwnMin", category="Dim.name")
    inner = sql.split("LEFT JOIN (", 1)[1].split(") AS ctx1", 1)[0]
    assert "dim.name AS k1, MIN(fact.dt) AS v" in inner and "db.fact" in inner and "GROUP BY 1" in inner
    assert "(ctx1.k1 = dim.name OR (ctx1.k1 IS NULL AND dim.name IS NULL))" in sql


def test_a_category_that_does_not_filter_the_calendar_keeps_one_value_for_the_whole_selection():
    sql, _ = chart("Start", category="Dim.name")
    assert "CROSS JOIN (" in sql and "LEFT JOIN" not in sql


def test_tooltip_measures_are_not_drafted_as_extra_series():
    v = {"projections": {"Category": ["Dim.name"], "Y": ["Fact.OwnMin"], "Tooltips": ["Sum(Other.x)"]}}
    maps = {**DATE_MAP, "Dim": "SELECT k, name FROM db.dim"}
    rels = DATE_RELS + [{"FromTableName": "Fact", "FromColumnName": "k", "ToTableName": "Dim",
                         "ToColumnName": "k", "IsActive": 1, "Cardinality": "M:1"}]
    sql, _ = S._draft_visual_sql(v, "column", DATE_MEASURES, maps, rels, DATE_PARAMS)
    assert "other" not in sql.lower() and "UNION ALL" not in sql


def test_per_group_sql_parses_as_teradata():
    sqlglot = pytest.importorskip("sqlglot")
    for cat in ("Cal.month_end", "Dim.name"):
        sql, params = chart("Start", category=cat)
        bound, _ = bind(sql, params, {"months": ["x"]})
        sqlglot.parse_one(bound.replace("?", "'x'"), read="teradata")
