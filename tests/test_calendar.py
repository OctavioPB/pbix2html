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
])
def test_calendar_column_translations(dax, teradata):
    assert semantic._calendar_expr_sql(dax, "Date", "calendar_date") == teradata


@pytest.mark.parametrize("dax", [
    'Calendar[Other]',                        # not the calendar's date column
    'DATEDIFF(Calendar[Date], TODAY(), DAY)',   # not a supported function
    'Calendar[Date] / 7',                     # division: integer vs real semantics differ
    'FORMAT(Calendar[Date], "MMMM d")',       # format token outside the supported set
    'YEAR(Calendar[Date]',                    # malformed
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
