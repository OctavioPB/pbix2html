"""bind(): an unselected slicer means "no filter", not "no rows"."""
from pbix2html.query import bind


def test_empty_in_predicate_becomes_no_filter():
    sql = "SELECT 1 FROM t WHERE t.region IN (:region) AND t.\"year\" IN (:year) AND t.x = :x"
    out, vals = bind(sql, ["region", "year", "x"], {"region": None, "year": [], "x": 5})
    assert out == "SELECT 1 FROM t WHERE 1=1 AND 1=1 AND t.x = ?" and vals == [5]


def test_selected_values_still_bind():
    out, vals = bind("WHERE t.region IN (:region)", ["region"], {"region": ["A", "B"]})
    assert out == "WHERE t.region IN (?,?)" and vals == ["A", "B"]
    out, vals = bind("WHERE t.region IN (:region)", ["region"], {"region": "A"})
    assert out == "WHERE t.region IN (?)" and vals == ["A"]


def test_other_empty_uses_and_undeclared_names_are_left_alone():
    out, vals = bind("WHERE year = :year AND ts > '10:30:00' AND a IN (:other)", ["year"], {"year": None})
    assert out == "WHERE year = ? AND ts > '10:30:00' AND a IN (:other)" and vals == [None]
