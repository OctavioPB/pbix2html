"""Joins over real-model relationship keys, and no fan-out across fact tables."""
from pbix2html import semantic

TABLE_MAP = {"Dim": "SELECT * FROM db.dim", "FactA": "SELECT * FROM db.a", "FactB": "SELECT * FROM db.b"}
RELS = [  # the spelling the extractor really emits
    {"FromTableName": "FactA", "FromColumnName": "k", "ToTableName": "Dim", "ToColumnName": "k", "IsActive": 1},
    {"FromTableName": "FactB", "FromColumnName": "k", "ToTableName": "Dim", "ToColumnName": "k", "IsActive": 1},
]


def _draft(kind, **projections):
    return semantic._draft_visual_sql({"projections": projections}, kind, {}, TABLE_MAP, RELS, {})


def test_join_is_drafted_from_extractor_relationship_keys():
    sql, _ = _draft("column", Category=["Dim.name"], Y=["Sum(FactA.x)"])
    assert "JOIN (SELECT * FROM db.a\n) AS facta ON dim.k = facta.k" in sql


def test_inactive_relationship_is_not_a_join_path():
    rels = [{**r, "IsActive": 0} for r in RELS]
    assert semantic._find_join_path(["Dim", "FactA"], rels) is None


def test_each_union_arm_joins_only_its_own_fact_table():
    sql, _ = _draft("column", Category=["Dim.name"], Y=["Sum(FactA.x)", "Sum(FactB.x)"])
    arm_a, arm_b = sql.split("\nUNION ALL\n")
    assert "db.a" in arm_a and "db.b" not in arm_a          # FactB would multiply FactA's rows
    assert "db.b" in arm_b and "db.a" not in arm_b
    # same column name on both: the series say which table they come from
    assert "'FactA: x' AS series" in arm_a and "'FactB: x' AS series" in arm_b


def test_a_table_joins_two_fact_tables_through_separate_arms_not_one_shared_from():
    # Each fact gets its own derived table (join it to Dim alone, aggregate, then LEFT JOIN the
    # results on Dim's key) — never a single FROM with both FactA and FactB joined directly,
    # which would multiply each one's rows by the other's and inflate both sums.
    sql, _ = _draft("table", Values=["Dim.name", "Sum(FactA.x)", "Sum(FactB.y)"])
    assert "LEFT JOIN" in sql and sql.count("FROM") >= 3        # outer + one per arm
    arm_a, arm_b = sql.split("LEFT JOIN")[1], sql.split("LEFT JOIN")[2]
    assert "db.a" in arm_a and "db.b" not in arm_a
    assert "db.b" in arm_b and "db.a" not in arm_b
    assert _draft("card", Values=["Sum(FactA.x)"]) is not None


def test_table_query_ending_in_a_line_comment_keeps_its_closing_paren():
    tm = {**TABLE_MAP, "FactA": "SELECT * FROM db.a WHERE x = 1\n--"}
    sql, _ = semantic._draft_visual_sql(
        {"projections": {"Values": ["Sum(FactA.x)"]}}, "card", {}, tm, RELS, {"p": {"from_slicer": "FactA.y"}})
    assert "\n--\n) AS facta" in sql            # the ')' is not inside the comment



def _arm_selects(sql):
    """(alias, its SELECT line) for each WITH arm, so a test can find one by what it aggregates."""
    import re
    return re.findall(r"(arm\d+) AS \(\n(SELECT [^\n]+)", sql)

_SPINE_MAP = {"Cal": "SELECT * FROM db.cal", "Org": "SELECT * FROM db.org",
              "F1": "SELECT * FROM db.f1", "F2": "SELECT * FROM db.f2"}
# Cal and Org are NOT related to each other: they meet only through a fact.
_SPINE_RELS = [
    {"FromTableName": "F1", "FromColumnName": "d", "ToTableName": "Cal", "ToColumnName": "Date", "IsActive": 1},
    {"FromTableName": "F1", "FromColumnName": "o", "ToTableName": "Org", "ToColumnName": "org", "IsActive": 1},
    {"FromTableName": "F2", "FromColumnName": "d", "ToTableName": "Cal", "ToColumnName": "Date", "IsActive": 1},
    {"FromTableName": "F2", "FromColumnName": "o", "ToTableName": "Org", "ToColumnName": "org", "IsActive": 1},
]


def test_a_summary_table_over_unrelated_dimensions_builds_its_rows_from_the_data():
    """A real "unit consumption details" table: month from a calendar, org/site from an org
    dimension, and one total from each of six fact tables. The calendar and the org dimension have
    no relationship — they meet only *through* the facts — so the usual split, which joins the
    dimension tables together to form the row set, had no such join and refused the visual.

    The rows now come from a UNION of the arms, which is Power BI's own answer: the combinations
    that actually have data."""
    v = {"projections": {"Values": ["Cal.Month", "Org.org", "Sum(F1.units)", "Sum(F2.units)"]}}
    sql, _ = semantic._draft_visual_sql(v, "table", {}, _SPINE_MAP, _SPINE_RELS, {})
    assert sql.startswith("WITH arm1 AS (")
    assert "spine AS (" in sql and sql.count("UNION") == 1          # two full-key arms
    # every arm groups by its whole key, so it holds one row per key: a LEFT JOIN onto it cannot
    # multiply rows. That is the fan-out argument, and it has to stay visible in the SQL.
    for arm in ("arm1", "arm2"):
        assert f"LEFT JOIN {arm} ON" in sql
    assert sql.count("GROUP BY 1, 2") == 2
    # the two facts share a column name, so the output names are made unique
    assert "f1_units" in sql and "f2_units" in sql


def test_a_value_on_a_dimension_table_joins_on_the_keys_it_can_reach():
    """`Sum(Cal.Year)` beside those totals: its arm can only reach the calendar's own category, so
    it is grouped by that and joined on it alone — which is the filter context Power BI gives it,
    since an unrelated dimension does not filter the calendar."""
    v = {"projections": {"Values": ["Cal.Month", "Org.org", "Sum(Cal.Year)",
                                    "Sum(F1.units)", "Sum(F2.units)"]}}
    sql, _ = semantic._draft_visual_sql(v, "table", {}, _SPINE_MAP, _SPINE_RELS, {})
    assert sql is not None
    arms = dict(_arm_selects(sql))
    cal_arm = next(a for a, sel in arms.items() if 'SUM(cal."year")' in sel)
    on = sql.split(f"LEFT JOIN {cal_arm} ON ")[1].splitlines()[0]
    assert "spine.k1" in on and "spine.k2" not in on, f"the calendar arm joined on {on}"
    # it carries only that one key, so grouping by it leaves one row per key and the LEFT JOIN
    # still cannot multiply rows
    assert " AS k1" in arms[cal_arm] and " AS k2" not in arms[cal_arm]


def test_a_value_unrelated_to_every_dimension_is_still_refused():
    """The fan-out guard the whole split exists for: a total from a table with no path to any of
    the dimensions would repeat one number down the column as though it meant something."""
    v = {"projections": {"Values": ["Cal.Month", "Org.org", "Sum(F1.units)", "Sum(Lonely.x)"]}}
    table_map = {**_SPINE_MAP, "Lonely": "SELECT * FROM db.lonely"}
    assert semantic._draft_visual_sql(v, "table", {}, table_map, _SPINE_RELS, {}) is None
