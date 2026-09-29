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


def test_one_select_never_sums_two_fact_tables():
    assert _draft("table", Values=["Dim.name", "Sum(FactA.x)", "Sum(FactB.y)"]) is None
    assert _draft("card", Values=["Sum(FactA.x)"]) is not None


def test_table_query_ending_in_a_line_comment_keeps_its_closing_paren():
    tm = {**TABLE_MAP, "FactA": "SELECT * FROM db.a WHERE x = 1\n--"}
    sql, _ = semantic._draft_visual_sql(
        {"projections": {"Values": ["Sum(FactA.x)"]}}, "card", {}, tm, RELS, {"p": {"from_slicer": "FactA.y"}})
    assert "\n--\n) AS facta" in sql            # the ')' is not inside the comment
