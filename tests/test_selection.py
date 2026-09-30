"""Selection-dependent DAX: FILTER(T, T[c] = MIN(T[c])) (a hierarchy slicer's top level)."""
import pytest

from pbix2html import query
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


# ---- combo chart: a Y2-role measure is a line series, same UNION ALL arms as a plain chart ---

COMBO_MAP = {"Fact": "SELECT dt, amt, pct FROM db.fact",
            "Cal": 'SELECT calendar_date AS "Date", calendar_date AS month_end FROM sys_calendar.calendar'}
COMBO_RELS = [{"FromTableName": "Fact", "FromColumnName": "dt", "ToTableName": "Cal",
              "ToColumnName": "Date", "IsActive": 1, "Cardinality": "M:1"}]


def test_combo_chart_drafts_union_arms_like_a_plain_multi_measure_chart():
    v = {"projections": {"Category": ["Cal.month_end"], "Y": ["Sum(Fact.amt)"], "Y2": ["Avg(Fact.pct)"]}}
    sql, params = S._draft_visual_sql(v, "combo", {}, COMBO_MAP, COMBO_RELS, {})
    assert "UNION ALL" in sql
    assert "'amt' AS series, SUM(fact.amt)" in sql
    assert "'pct' AS series, AVG(fact.pct)" in sql
    assert params == []


def test_combo_axis_marks_only_the_y2_series_as_line():
    v = {"projections": {"Category": ["Cal.month_end"], "Y": ["Sum(Fact.amt)"], "Y2": ["Avg(Fact.pct)"]}}
    assert S._combo_axis(v, {}) == {"pct": "line"}


def test_combo_axis_uses_the_literal_value_key_when_there_is_only_one_measure():
    # _draft_visual_sql's single-value chart shape has no `series` column at all when there's
    # only one measure total, so the renderer's series() groups it under the literal key
    # 'value' (see report.html.j2) — the axis dict has to match that, not the field's label.
    y2_only = {"projections": {"Category": ["Cal.month_end"], "Y2": ["Sum(Fact.amt)"]}}
    assert S._combo_axis(y2_only, {}) == {"value": "line"}
    y_only = {"projections": {"Category": ["Cal.month_end"], "Y": ["Sum(Fact.amt)"]}}
    assert S._combo_axis(y_only, {}) == {}


def test_combo_chart_with_only_a_y2_measure_drafts_the_plain_single_value_shape():
    v = {"projections": {"Category": ["Cal.month_end"], "Y2": ["Sum(Fact.amt)"]}}
    sql, _ = S._draft_visual_sql(v, "combo", {}, COMBO_MAP, COMBO_RELS, {})
    assert '"value"' in sql and "series" not in sql
    assert S._combo_axis(v, {}) == {"value": "line"}


def test_combo_chart_with_no_category_stays_manual():
    v = {"projections": {"Y": ["Sum(Fact.amt)"], "Y2": ["Avg(Fact.pct)"]}}
    assert S._draft_visual_sql(v, "combo", {}, COMBO_MAP, COMBO_RELS, {}) is None


def test_combo_chart_with_two_line_measures_marks_both_as_line():
    v = {"projections": {"Category": ["Cal.month_end"], "Y2": ["Sum(Fact.amt)", "Avg(Fact.pct)"]}}
    sql, _ = S._draft_visual_sql(v, "combo", {}, COMBO_MAP, COMBO_RELS, {})
    assert "'amt' AS series" in sql and "'pct' AS series" in sql
    assert S._combo_axis(v, {}) == {"amt": "line", "pct": "line"}


def test_combo_sql_parses_as_teradata():
    sqlglot = pytest.importorskip("sqlglot")
    v = {"projections": {"Category": ["Cal.month_end"], "Y": ["Sum(Fact.amt)"], "Y2": ["Avg(Fact.pct)"]}}
    sql, params = S._draft_visual_sql(v, "combo", {}, COMBO_MAP, COMBO_RELS, {})
    sqlglot.parse_one(sql, read="teradata")


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


# ---- one measure over several fact tables (IF across two facts) -----------------------------

MF_MEASURES = {
    ("A", "Ending"): "var d = max('Cal'[Date])\nRETURN IF(MONTH(d) == MONTH(TODAY()), SUM(A[n]), "
                     "CALCULATE(SUM(B[n]), FILTER(B, MONTH(B[dt]) == MONTH(d))))",
}
MF_MAP = {"A": "SELECT k, dt, n FROM db.a", "B": "SELECT k, dt, n FROM db.b",
          "Dim": "SELECT k, name FROM db.dim",
          "Cal": 'SELECT calendar_date AS "Date", calendar_date AS month_end FROM sys_calendar.calendar'}
MF_RELS = [{"FromTableName": t, "FromColumnName": "k", "ToTableName": "Dim", "ToColumnName": "k",
            "IsActive": 1, "Cardinality": "M:1"} for t in ("A", "B")] + [
    {"FromTableName": t, "FromColumnName": "dt", "ToTableName": "Cal", "ToColumnName": "Date",
     "IsActive": 1, "Cardinality": "M:1"} for t in ("A", "B")]
MF_PARAMS = {"dim_name": {"from_slicer": "Dim.name", "multi": True},
             "months": {"from_slicer": "Cal.month_end", "multi": True}}


def mf(kind, categories=()):
    v = {"projections": {"Category": list(categories), "Y": ["A.Ending"]}}
    return S._draft_visual_sql(v, kind, MF_MEASURES, MF_MAP, MF_RELS, MF_PARAMS)


def test_measure_over_two_fact_tables_is_split_into_one_derived_table_per_table():
    sql, params = mf("card")
    assert sql.startswith("SELECT (CASE WHEN EXTRACT(MONTH FROM ctx1.v) = EXTRACT(MONTH FROM CURRENT_DATE) "
                          "THEN arm1.a0 ELSE arm2.a1 END) AS \"value\"")
    assert "CROSS JOIN" in sql and sql.count("SUM(") == 2
    arm1 = sql.split(") AS arm1", 1)[0]
    assert "db.b" not in arm1 and "db.a" in arm1            # each arm reads only its own fact
    assert {"months"} <= set(params)


def test_grouped_multi_fact_joins_the_arms_on_the_category_and_drops_blank_groups():
    sql, _ = mf("column", ["Dim.name"])
    assert sql.startswith("SELECT DISTINCT dim.name AS category")
    assert "LEFT JOIN (SELECT dim.name AS k1, SUM(a.n) AS a0" in sql
    assert "GROUP BY 1\n) AS arm1 ON (arm1.k1 = dim.name OR (arm1.k1 IS NULL AND dim.name IS NULL))" in sql
    assert "IS NOT NULL" in sql.rsplit("WHERE", 1)[1]
    assert "\nFROM (SELECT k, name FROM db.dim" in sql          # the main FROM is the category table


def test_multi_fact_sql_parses_as_teradata():
    sqlglot = pytest.importorskip("sqlglot")
    for kind, cats in (("card", []), ("column", ["Dim.name"]), ("column", ["Cal.month_end"])):
        sql, params = mf(kind, cats)
        bound, _ = bind(sql, params, {"dim_name": ["x"], "months": []})
        sqlglot.parse_one(bound.replace("?", "'x'"), read="teradata")


def test_two_independent_single_table_measures_join_through_separate_arms():
    # Two separate measures, each its own single fact table (not one composite expression
    # spanning both, which is multi_fact's job) — this is _multi_value_table's case: safe to
    # draft since each fact gets its own arm, never a single FROM joining both facts directly.
    v = {"projections": {"Values": ["A.Ending", "Dim.name"]}}
    measures = {**MF_MEASURES, ("A", "Ending"): "SUM(A[n])", ("Dim", "name"): "SUM(Dim[n])"}
    sql, _ = S._draft_visual_sql(v, "table", measures, MF_MAP, MF_RELS, MF_PARAMS)
    assert "CROSS JOIN" in sql and "SUM(a.n)" in sql and "SUM(dim.n)" in sql


# `Grand Total = [A]+[B]` over two unrelated fact tables (arithmetic between measures, no
# VAR/selection-context involved — that combination is covered separately above).
GRAND_MEASURES = {("A", "Grand"): "SUM(A[n]) + SUM(B[n])"}


def test_multi_fact_composite_measure_drafts_for_a_table_visual_with_categories():
    v = {"projections": {"Rows": ["Dim.name"], "Values": ["A.Grand"]}}
    sql, params = S._draft_visual_sql(v, "table", GRAND_MEASURES, MF_MAP, MF_RELS, MF_PARAMS)
    assert sql.startswith("SELECT DISTINCT dim.name AS name")
    assert "AS grand\nFROM" in sql                                  # the field's own name, not "value"
    assert "LEFT JOIN (SELECT dim.name AS k1, SUM(a.n) AS a0" in sql
    assert "IS NOT NULL" not in sql                                 # blanks stay in the table, unlike a chart
    assert {"dim_name"} <= set(params)


def test_multi_fact_composite_measure_drafts_for_a_matrix_with_two_row_fields():
    # Two category fields — a chart's arm-joining is capped at two, but a matrix's row
    # grouping isn't capped at all; here two columns of the same dimension prove it goes
    # through the "more than 2" path without also hitting the separate, pre-existing
    # limitation that two categories from *unrelated* dimension tables (joinable only
    # through the fact) can't be composed for the categories-only outer FROM.
    v = {"projections": {"Rows": ["Dim.name", "Dim.k"], "Values": ["A.Grand"]}}
    sql, params = S._draft_visual_sql(v, "matrix", GRAND_MEASURES, MF_MAP, MF_RELS, MF_PARAMS)
    assert "dim.name AS name" in sql and "dim.k AS k" in sql
    assert "AS grand\nFROM" in sql
    assert {"dim_name"} <= set(params)


def test_multi_fact_composite_measure_with_no_categories_drafts_for_a_table_visual():
    v = {"projections": {"Values": ["A.Grand"]}}
    sql, params = S._draft_visual_sql(v, "table", GRAND_MEASURES, MF_MAP, MF_RELS, MF_PARAMS)
    assert sql.startswith("SELECT (") and "AS grand\nFROM" in sql
    assert "CROSS JOIN" in sql


# ---- a table's several VALUE fields, each its own aggregate over a *different* fact table -----
# (as opposed to multi_fact above: one composite expression spanning tables). Found against a
# real report: a customer summary table pulling one total from each of several unrelated fact
# tables that share only a dimension.

def test_multi_value_table_joins_one_arm_per_fact_table():
    v = {"projections": {"Rows": ["Dim.name"], "Values": ["Sum(A.n)", "Sum(B.n)"]}}
    sql, params = S._draft_visual_sql(v, "table", {}, MF_MAP, MF_RELS, MF_PARAMS)
    assert sql.startswith("SELECT DISTINCT dim.name AS name, arm1.a0 AS n, arm2.a0 AS n")
    assert "LEFT JOIN (SELECT dim.name AS k1, SUM(a.n) AS a0" in sql
    assert "LEFT JOIN (SELECT dim.name AS k1, SUM(b.n) AS a0" in sql
    assert {"dim_name"} <= set(params)


def test_multi_value_table_with_no_categories_cross_joins_the_arms():
    v = {"projections": {"Values": ["Sum(A.n)", "Sum(B.n)"]}}
    sql, _ = S._draft_visual_sql(v, "table", {}, MF_MAP, MF_RELS, MF_PARAMS)
    assert "CROSS JOIN" in sql and "SUM(a.n)" in sql and "SUM(b.n)" in sql


def test_multi_value_table_leaves_a_selection_dependent_value_manual():
    v = {"projections": {"Rows": ["Dim.name"], "Values": ["A.Top", "Sum(B.n)"]}}
    measures = {("A", "Top"): "CALCULATE(SUM(A[n]), FILTER('Dim', 'Dim'[name] = MIN('Dim'[name])))"}
    assert S._draft_visual_sql(v, "table", measures, MF_MAP, MF_RELS, MF_PARAMS) is None


def test_multi_value_table_sql_parses_as_teradata():
    sqlglot = pytest.importorskip("sqlglot")
    v = {"projections": {"Rows": ["Dim.name"], "Values": ["Sum(A.n)", "Sum(B.n)"]}}
    sql, params = S._draft_visual_sql(v, "table", {}, MF_MAP, MF_RELS, MF_PARAMS)
    bound, _ = bind(sql, params, {"dim_name": ["x"]})
    sqlglot.parse_one(bound.replace("?", "'x'"), read="teradata")


# ---- FORMAT with time parts, TIME(), NOW() ----------------------------------------------------

def _translate(dax):
    r = S.translate_dax(dax, {})
    return r and r.text


def test_format_with_time_parts_and_time_arithmetic():
    sql = _translate('FORMAT(MAX(T[ts]) + TIME(4,0,0), "yyyy-mm-dd hh:mm:ss")')
    assert "(INTERVAL '04:00:00' HOUR TO SECOND)" in sql
    for element in ("'YYYY'", "'MM'", "'DD'", "'HH24'", "'MI'", "'SS'"):
        assert f", {element})" in sql
    assert sql.count("'MM'") == 1 and sql.count("'MI'") == 1      # mm is month first, minutes after hh


def test_now_keeps_the_time_and_unsupported_formats_stay_manual():
    assert _translate("NOW()") == "CURRENT_TIMESTAMP(0)" or "CURRENT_TIMESTAMP(0)" in (_translate("MAX(T[ts]) - NOW()") or "")
    assert _translate('FORMAT(MAX(T[ts]), "hh:mm AM/PM")') is None          # 12-hour clock: not translated
    assert _translate("TIME(a, 0, 0)") is None


# ---- `value` is a reserved word on Teradata (error 3707 on `AS value`) -----------------------------

def test_drafted_sql_quotes_the_value_alias_and_legacy_sql_is_rewritten_at_run_time():
    sql, _ = card("Top")
    assert 'AS "value"' in sql and "AS value" not in sql
    q = S.quote_reserved_aliases
    assert q("SELECT SUM(x) AS value FROM t") == 'SELECT SUM(x) AS "value" FROM t'
    assert q("SELECT a AS Value, b AS MAX FROM t") == 'SELECT a AS "value", b AS "max" FROM t'
    assert q("SELECT 'x AS value' AS s, y AS category FROM t -- AS value") == "SELECT 'x AS value' AS s, y AS category FROM t -- AS value"
    assert q('SELECT a AS "value" FROM t') == 'SELECT a AS "value" FROM t'          # already quoted
    assert q("SELECT CAST(a AS DATE), b AS values2 FROM t") == "SELECT CAST(a AS DATE), b AS values2 FROM t"


def test_teradata_backend_sends_the_quoted_alias(monkeypatch):
    from tests.test_backend_pool import FakeCon
    log = []
    b = query.TeradataBackend()
    monkeypatch.setattr(b, "_connect", lambda: FakeCon(log))
    b.execute("SELECT 1 AS value", [], None)
    assert log[-1][2] == 'SELECT 1 AS "value"'


# ---- more Teradata rejections found on a real system (TestReport2 / TestReport3 runs) ----------------

def test_a_power_bi_column_or_table_named_like_a_reserved_word_is_never_emitted_bare():
    assert S._sql_col("Rename") == '"rename"' and S._sql_col("Value") == '"value"'      # error 3707 ... 'rename'
    assert S._sql_col("Region") == "region"
    assert S._sql_alias("Date") == "date_t" and S._sql_alias("Index") == "index_t"       # an alias can't be quoted once
    assert S._sql_alias("Sales") == "sales"
    q = S.quote_reserved_aliases
    assert q("SELECT a AS rename, CAST(b AS DATE) AS d FROM t") == 'SELECT a AS "rename", CAST(b AS DATE) AS d FROM t'


def test_multi_value_parameters_survive_a_round_trip_through_a_text_field():
    assert S.multi_values("[2026]") == [2026] and S.multi_values("2026, 2027") == ["2026", "2027"]
    assert S.multi_values(["[2026]", 2027]) == [2026, 2027]
    assert S.multi_values("['a', 'b']") == ["a", "b"] and S.multi_values(None) == []
    from types import SimpleNamespace
    spec = SimpleNamespace(parameters={"year": {"multi": True, "default": "[2026]"}})    # a yaml damaged that way
    assert S.resolve_params(spec, {})["year"] == [2026]
    assert S.resolve_params(spec, {"year": ["[2026]"]})["year"] == [2026]                 # ?year=%5B2026%5D


def test_gui_saves_a_multi_default_as_a_list_not_as_the_text_of_a_list():
    from pbix2html.gui import _default_value
    assert _default_value("2026", True, "number", [2026]) == [2026]
    assert _default_value("A, B", True, "text", None) == ["A", "B"]
    assert _default_value("", True, None, [2026]) is None and _default_value("x", False, None, None) == "x"


def test_legacy_sql_with_reserved_names_after_a_dot_is_quoted_too():
    q = S.quote_reserved_aliases
    old = "SELECT lvl.rename, lvl.date, COUNT(*) FROM (SELECT 1 AS lvl, 'x' AS rename) AS lvl"
    assert q(old) == 'SELECT lvl."rename", lvl."date", COUNT(*) FROM (SELECT 1 AS lvl, \'x\' AS "rename") AS lvl'
    assert q("SELECT 'a.value' FROM t -- x.value\nWHERE t.region = 1 AND f(t.k) > 2.5") == \
        "SELECT 'a.value' FROM t -- x.value\nWHERE t.region = 1 AND f(t.k) > 2.5"


# ---- SQL written by an earlier drafter is repaired when it is sent (Teradata 3888 / 3706) -----------

def test_set_operation_arms_without_a_table_get_one_row_source():
    legacy = ("SELECT x FROM (SELECT CAST('N' AS VARCHAR(1)) AS x\nUNION ALL\nSELECT 'E'\nUNION ALL\nSELECT 'I'\n) AS t "
              "WHERE t.x IN (?)")
    fixed = S.add_from_to_bare_selects(legacy)
    assert fixed.count("FROM (SELECT 1 AS one) AS one_row") == 3
    assert S.add_from_to_bare_selects(fixed) == fixed                          # idempotent
    plain = "SELECT a FROM t UNION ALL SELECT b FROM u"
    assert S.add_from_to_bare_selects(plain) == plain                          # arms with a table: untouched
    assert S.add_from_to_bare_selects("SELECT 1") == "SELECT 1"                # not a set operation
    assert S.add_from_to_bare_selects("SELECT 'a UNION ALL SELECT b' AS s FROM t") == "SELECT 'a UNION ALL SELECT b' AS s FROM t"


def test_legacy_top_n_with_a_window_function_is_rewritten_to_the_counted_form():
    old = ("WHERE h.lvl IN (SELECT k FROM (\nSELECT h.lvl AS k, SUM(h.lvl) AS a\nFROM (SELECT * FROM x\n) AS h\nGROUP BY 1\n"
           ") AS t QUALIFY RANK() OVER (ORDER BY a ASC) <= 1)")
    new = S.rewrite_legacy_top_n(old)
    assert "QUALIFY" not in new and "OVER" not in new
    assert "WHERE (SELECT COUNT(*) FROM (" in new and "WHERE u.a < t.a) < 1)" in new and new.count("SUM(h.lvl) AS a") == 2
    assert S.rewrite_legacy_top_n(new) == new
    assert S.rewrite_legacy_top_n(old.replace("ASC", "DESC").replace(" <= 1", " <= 3")).endswith("u.a > t.a) < 3)")


def test_the_backend_sends_repaired_sql(monkeypatch):
    from tests.test_backend_pool import FakeCon
    log = []
    b = query.TeradataBackend()
    monkeypatch.setattr(b, "_connect", lambda: FakeCon(log))
    b.execute("SELECT 'a' AS value UNION ALL SELECT 'b'", [], None)
    assert log[-1][2] == ('SELECT \'a\' AS "value" FROM (SELECT 1 AS one) AS one_row UNION ALL '
                          "SELECT 'b' FROM (SELECT 1 AS one) AS one_row")
