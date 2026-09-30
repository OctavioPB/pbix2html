"""DAX CALENDAR tables rebuilt on sys_calendar.calendar, and date filters that reach the facts."""
import pytest

from pbix2html import semantic
from pbix2html.query import bind


def _model(expr='CALENDAR("2017-01-01", NOW())', cols=(), **extra):
    return {"calculated_tables": [{"TableName": "Calendar", "Expression": expr}],
            "calculated_columns": [{"TableName": "Calendar", "ColumnName": n, "Expression": e} for n, e in cols],
            **extra}


def test_calendar_source_from_iso_dates_and_now():
    info = semantic.detect_calendar_tables(_model())["Calendar"]
    assert info["unsupported"] == [] and info["notes"] == []
    sql = info["sql"]
    assert "FROM sys_calendar.calendar" in sql
    assert "calendar_date BETWEEN DATE '2017-01-01' AND CURRENT_DATE" in sql
    assert 'calendar_date AS "date"' in sql
    assert semantic.validate_read_only_sql(sql)


@pytest.mark.parametrize("dax, teradata", [
    ('FORMAT(\'Calendar\'[Date], "MMMM")', "TRIM(TO_CHAR(calendar_date, 'Month'))"),
    ('FORMAT([Date], "MMMM YYYY")', "TRIM(TO_CHAR(calendar_date, 'Month')) || ' ' || TO_CHAR(calendar_date, 'YYYY')"),
    ('YEAR(Calendar[Date])', "EXTRACT(YEAR FROM calendar_date)"),
    ('VALUE(FORMAT(Calendar[Date], "YYYYMM"))', "CAST(TO_CHAR(calendar_date, 'YYYY') || TO_CHAR(calendar_date, 'MM') AS INTEGER)"),
    ('YEAR(Calendar[Date]) * 100 + MONTH(Calendar[Date])', "(EXTRACT(YEAR FROM calendar_date) * 100) + EXTRACT(MONTH FROM calendar_date)"),
    ('IF(YEAR(Calendar[Date]) = YEAR(TODAY()), "Now", "Then")',
     "CASE WHEN (EXTRACT(YEAR FROM calendar_date) = EXTRACT(YEAR FROM CURRENT_DATE)) THEN 'Now' ELSE 'Then' END"),
    ('ISBLANK(Calendar[Date])', "calendar_date IS NULL"),
    ('BLANK()', "NULL"),
    ('DATE(2024, 1, 1)', "ADD_MONTHS(CAST(CAST(2024 AS VARCHAR(4)) || '-01-01' AS DATE FORMAT 'YYYY-MM-DD'), "
                         "CAST(1 AS INTEGER) - 1) + (CAST(1 AS INTEGER) - 1)"),
    # the real-world idiom this was added for: a date bucketed to the 1st of its month
    ('IF(ISBLANK(Calendar[Date]), BLANK(), DATE(YEAR(Calendar[Date]), MONTH(Calendar[Date]), 1))',
     "CASE WHEN (calendar_date IS NULL) THEN NULL ELSE (ADD_MONTHS(CAST(CAST(EXTRACT(YEAR FROM calendar_date) "
     "AS VARCHAR(4)) || '-01-01' AS DATE FORMAT 'YYYY-MM-DD'), CAST(EXTRACT(MONTH FROM calendar_date) AS INTEGER) "
     "- 1) + (CAST(1 AS INTEGER) - 1)) END"),
])
def test_calendar_column_translations(dax, teradata):
    assert semantic._calendar_expr_sql(dax, "Date", "calendar_date") == teradata


@pytest.mark.parametrize("dax", [
    'Calendar[Other]',                        # not the calendar's date column
    'DATEDIFF(Calendar[Date], TODAY(), DAY)',   # not a supported function
    'Calendar[Date] / (',                     # malformed
    'RETURN 1',                               # RETURN without VAR
    'FORMAT(Calendar[Date], "MMMM d")',       # format token outside the supported set
    'YEAR(Calendar[Date]',                    # malformed
    'DATE(2024, 1)',                          # DATE needs exactly 3 args
])
def test_calendar_column_refusals(dax):
    assert semantic._calendar_expr_sql(dax, "Date", "calendar_date") is None


def test_unsupported_columns_are_listed_not_guessed_and_odd_calendars_are_skipped():
    m = _model(cols=[("Ok", "YEAR(Calendar[Date])"), ("Bad", "RELATED(X[y])")])
    info = semantic.detect_calendar_tables(m)["Calendar"]
    assert info["unsupported"] == ["Bad"] and "AS ok" in info["sql"] and "-- not translated" in info["sql"]
    assert semantic.detect_calendar_tables(_model('CALENDARAUTO()')) == {}
    assert semantic.detect_calendar_tables(_model('CALENDAR(MIN(T[d]), MAX(T[d]))')) == {}


def test_ambiguous_slash_dates_are_noted():
    info = semantic.detect_calendar_tables(_model('CALENDAR("04/01/2026", TODAY())'))["Calendar"]
    assert "DATE '2026-04-01'" in info["sql"] and info["notes"] == ["assumed MM/DD/YYYY"]
    info = semantic.detect_calendar_tables(_model('CALENDAR("13/01/2026", DATE(2026, 12, 31))'))["Calendar"]
    assert "DATE '2026-01-13'" in info["sql"] and "DATE '2026-12-31'" in info["sql"] and info["notes"] == []


def test_relationship_proposals_use_the_schema_and_skip_declared_ones():
    cols = [{"TableName": "Sales", "ColumnName": "log_dt", "PandasDataType": "datetime64[ns]"},
            {"TableName": "Other", "ColumnName": "log_dt", "PandasDataType": "object"},
            {"TableName": "Plain", "ColumnName": "amount", "PandasDataType": "float64"}]
    rels = semantic.detect_calendar_relationships(_model(columns=cols))
    assert [(r["FromTableName"], r["FromColumnName"], r["ToTableName"], r["ToColumnName"]) for r in rels] == \
        [("Sales", "log_dt", "Calendar", "Date")]
    declared = [{"FromTableName": "Sales", "FromColumnName": "d", "ToTableName": "Calendar", "ToColumnName": "Date"}]
    assert semantic.detect_calendar_relationships(_model(columns=cols, relationships=declared)) == []
    # older extracts without a column list fall back to the Power Query text
    pq = [{"TableName": "Sales", "Expression": "SELECT x, d AS log_dt FROM t"}]
    assert len(semantic.detect_calendar_relationships(_model(power_query=pq))) == 1


def test_relationships_file_is_saved_once_and_hand_edits_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic, "METRICS_DIR", tmp_path)
    m = _model(columns=[{"TableName": "Sales", "ColumnName": "log_dt", "PandasDataType": "datetime64[ns]"}])
    _, new = semantic.sync_relationships("R", m)
    assert len(new) == 1 and semantic.relationships_path("R").exists()
    assert semantic.sync_relationships("R", m)[1] == []                   # not proposed twice
    # deleting an entry makes it be proposed again; to switch one off, set "IsActive": 0 (kept)
    semantic.relationships_path("R").write_text("[]", encoding="utf-8")
    assert len(semantic.sync_relationships("R", m)[1]) == 1
    off = [{**r, "IsActive": 0} for r in semantic.read_relationships("R")]
    semantic.relationships_path("R").write_text(__import__("json").dumps(off), encoding="utf-8")
    assert semantic.sync_relationships("R", m)[1] == []
    assert semantic._filter_edges(semantic.with_relationship_overrides("R", {})["relationships"]) == []
    assert semantic.with_relationship_overrides("R", {"relationships": []})["relationships"][0]["Proposed"]


TABLE_MAP = {"Sales": "SELECT * FROM db.sales", "Calendar": "SELECT calendar_date AS \"date\", 1 AS year FROM sys_calendar.calendar"}
RELS = [{"FromTableName": "Sales", "FromColumnName": "log_dt", "ToTableName": "Calendar", "ToColumnName": "Date",
         "CrossFilteringBehavior": "Single", "IsActive": 1}]
PARAMS = {"year": {"from_slicer": "Calendar.Year"}}


def test_calendar_slicer_filters_a_fact_the_visual_reads_through_a_semi_join():
    sql, params = semantic._draft_visual_sql(
        {"projections": {"Values": ["Sum(Sales.x)"]}}, "card", {}, TABLE_MAP, RELS, PARAMS)
    assert params == ["year"] and "JOIN" not in sql                       # no join: no duplicated rows
    assert 'sales.log_dt IN (SELECT "date" FROM (' in sql and 'calendar."year" IN (:year)' in sql
    assert bind(sql, params, {})[0].rstrip().endswith("WHERE 1=1")        # nothing selected: no filter
    out, vals = bind(sql, params, {"year": 2026})
    assert vals == [2026] and "1=1" not in out


def test_a_slicer_only_flows_from_the_one_side_unless_the_relationship_is_bidirectional():
    # a Sales slicer must not filter the calendar unless the relationship is bidirectional
    params = {"amount": {"from_slicer": "Sales.amount"}}
    sql, used = semantic._draft_visual_sql(
        {"projections": {"Category": ["Calendar.Year"], "Y": ["Sum(Sales.x)"]}}, "column", {}, TABLE_MAP, RELS, params)
    assert "amount" in sql and used == ["amount"]                          # Sales is read: direct predicate
    assert semantic._filter_edges(RELS) == [("Calendar", "Date", "Sales", "log_dt")]
    both = [{**RELS[0], "CrossFilteringBehavior": "BothDirections"}]
    assert ("Sales", "log_dt", "Calendar", "Date") in semantic._filter_edges(both)


@pytest.mark.parametrize("dax, teradata", [
    ("MONTH(Calendar[Date]) / 3", "CAST(EXTRACT(MONTH FROM calendar_date) AS DECIMAL(18,6)) / 3"),   # exact, not integer
    ('"Y" & YEAR(Calendar[Date]) & "Q" & CEILING(MONTH(Calendar[Date]) / 3, 1)',
     "'Y' || CAST(EXTRACT(YEAR FROM calendar_date) AS VARCHAR(50)) || 'Q' || "
     "CAST(CEIL((CAST(EXTRACT(MONTH FROM calendar_date) AS DECIMAL(18,6)) / 3)) AS VARCHAR(50))"),
    ('CONCATENATE(FORMAT(Calendar[Date], "MM"), CONCATENATE(" - ", FORMAT(Calendar[Date], "MMMM")))',
     "CAST(TO_CHAR(calendar_date, 'MM') AS VARCHAR(50)) || "
     "CAST((' - ' || CAST(TRIM(TO_CHAR(calendar_date, 'Month')) AS VARCHAR(50))) AS VARCHAR(50))"),
    ("ENDOFMONTH(Calendar[Date])", "ADD_MONTHS((calendar_date - EXTRACT(DAY FROM calendar_date) + 1), 1) - 1"),
    ("STARTOFMONTH(Calendar[Date])", "calendar_date - EXTRACT(DAY FROM calendar_date) + 1"),
    ("Calendar[Date] - 335", "calendar_date - 335"),
    ("var m = MONTH(TODAY()) return IF(MONTH(Calendar[Date]) == m, \"now\", \"no\")",
     "CASE WHEN (EXTRACT(MONTH FROM calendar_date) = (EXTRACT(MONTH FROM CURRENT_DATE))) THEN 'now' ELSE 'no' END"),
])
def test_calendar_extended_grammar(dax, teradata):
    assert semantic._calendar_expr_sql(dax, "Date", "calendar_date") == teradata


def test_calendar_columns_can_refer_to_each_other_and_cycles_are_refused():
    sib = {"eom": "ENDOFMONTH(Calendar[Date])", "label": 'FORMAT(Calendar[eom], "MMM YYYY")',
           "a": "Calendar[b]", "b": "Calendar[a]"}
    sql = semantic._calendar_expr_sql(sib["label"], "Date", "calendar_date", sib)
    assert "ADD_MONTHS((calendar_date - EXTRACT(DAY FROM calendar_date) + 1), 1) - 1" in sql and "'Mon'" in sql
    assert semantic._calendar_expr_sql(sib["a"], "Date", "calendar_date", sib) is None      # a -> b -> a
    assert semantic._calendar_expr_sql("Calendar[nope]", "Date", "calendar_date", sib) is None


# ---- a calculated column on a *regular* (not DAX CALENDAR()) table --------------------------
# Found against a real report: pbixray's schema lists a calculated column exactly like a real
# one, so a plain field reference to it drafted a bare `alias.column` that doesn't exist at
# Teradata ("Month_Bucket" is computed by DAX, not sourced from the mapped query).

MONTH_BUCKET_DAX = ("IF(\n\tISBLANK('Dates'[calendar_date]),\n\tBLANK(),\n\tDATE(\n\t\tYEAR('Dates'[calendar_date]),\n"
                 "\t\t1 + (MONTH('Dates'[calendar_date]) - 1),\n\t\t1\n\t)\n)")

CALC_COL_MODEL = {
    "columns": [{"TableName": "Dates", "ColumnName": "calendar_date"},
               {"TableName": "Dates", "ColumnName": "year_of_calendar"},
               {"TableName": "Dates", "ColumnName": "Month_Bucket"}],       # materialized like any other column
    "calculated_columns": [{"TableName": "Dates", "ColumnName": "Month_Bucket", "Expression": MONTH_BUCKET_DAX}],
    "calculated_tables": [],
}


def test_table_calc_columns_translates_a_supported_expression():
    out = semantic._table_calc_columns(CALC_COL_MODEL)
    assert set(out) == {("Dates", "Month_Bucket")}
    sql = out[("Dates", "Month_Bucket")]
    assert sql is not None and "dates.calendar_date IS NULL" in sql and "ADD_MONTHS" in sql


def test_table_calc_columns_marks_an_unsupported_expression_as_none_not_absent():
    model = {**CALC_COL_MODEL,
             "calculated_columns": [{"TableName": "Dates", "ColumnName": "Month_Bucket", "Expression": "RELATED(X[y])"}]}
    out = semantic._table_calc_columns(model)
    assert ("Dates", "Month_Bucket") in out and out[("Dates", "Month_Bucket")] is None


def test_table_calc_columns_skips_a_dax_calendar_table_of_its_own():
    # A DAX CALENDAR() table's calculated columns are handled by detect_calendar_tables
    # (a full replacement query), not by this per-column mechanism.
    model = {"columns": [{"TableName": "Calendar", "ColumnName": "Year"}],
            "calculated_columns": [{"TableName": "Calendar", "ColumnName": "Year", "Expression": "YEAR([Date])"}],
            "calculated_tables": [{"TableName": "Calendar", "Expression": 'CALENDAR("2026-01-01", TODAY())'}]}
    assert semantic._table_calc_columns(model) == {}


def test_resolve_field_inlines_a_supported_calculated_column_bare_and_aggregated():
    calc = semantic._table_calc_columns(CALC_COL_MODEL)
    bare = semantic._resolve_field("Category", "Dates.Month_Bucket", {}, calc)
    assert bare is not None and bare.is_value is False and "ADD_MONTHS" in bare.expr and bare.out_name == "month_bucket"
    agg = semantic._resolve_field("Values", "Sum(Dates.Month_Bucket)", {}, calc)
    assert agg is not None and agg.expr.startswith("SUM((CASE WHEN")


def test_resolve_field_refuses_an_unsupported_calculated_column():
    model = {**CALC_COL_MODEL,
             "calculated_columns": [{"TableName": "Dates", "ColumnName": "Month_Bucket", "Expression": "RELATED(X[y])"}]}
    calc = semantic._table_calc_columns(model)
    assert semantic._resolve_field("Category", "Dates.Month_Bucket", {}, calc) is None
    assert semantic._resolve_field("Values", "Sum(Dates.Month_Bucket)", {}, calc) is None


def test_draft_visual_sql_leaves_the_whole_visual_manual_when_a_calc_column_is_unsupported():
    model = {**CALC_COL_MODEL,
             "calculated_columns": [{"TableName": "Dates", "ColumnName": "Month_Bucket", "Expression": "RELATED(X[y])"}]}
    calc = semantic._table_calc_columns(model)
    v = {"projections": {"Category": ["Dates.Month_Bucket"], "Values": ["Sum(Dates.year_of_calendar)"]}}
    table_map = {"Dates": "SELECT calendar_date, year_of_calendar FROM db.dates"}
    assert semantic._draft_visual_sql(v, "column", {}, table_map, [], {}, calc_columns=calc) is None


def test_diagnose_visual_reports_untranslatable_calc_column_distinctly():
    model = {**CALC_COL_MODEL,
             "calculated_columns": [{"TableName": "Dates", "ColumnName": "Month_Bucket", "Expression": "RELATED(X[y])"}]}
    calc = semantic._table_calc_columns(model)
    v = {"projections": {"Category": ["Dates.Month_Bucket"]}}
    reasons = semantic.diagnose_visual(v, "table", {}, {}, [], {"Dates"}, {}, calc_columns=calc)
    assert reasons == ["untranslatable_calc_column:Month_Bucket"]
