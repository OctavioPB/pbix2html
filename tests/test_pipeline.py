"""Full pipeline without Teradata: extract → yaml → FakeBackend → render → HTML."""
import json
from pbix2html import extract as ex, semantic
from pbix2html.query import FakeBackend, bind, run_report
from pbix2html.render import render_html, build_spec
from pbix2html.validate import compare

FIX = {
    "SELECT SUM(amount) AS value FROM sales WHERE year = ?": {"columns": ["value"], "rows": [[1234567.8]]},
    "SELECT region AS category, margin AS value FROM v_margin_region WHERE year = ?":
        {"columns": ["category", "value"], "rows": [["North", 0.21], ["South", 0.18], ["Center", None]]},
    "SELECT month AS category, amount AS value FROM v_revenue_month WHERE year = ?":
        {"columns": ["category", "value"], "rows": [["Jan", 10], ["Feb", 12]]},
    "SELECT customer, amount FROM v_top_customers WHERE year = ?":
        {"columns": ["customer", "amount"], "rows": [["ACME", 100.5], ["Globex", 90.0]]},
}


def test_bind_expands_lists():
    sql, vals = bind("WHERE a = :a AND r IN (:r) AND t = :ignored", ["a", "r"], {"a": 1, "r": ["x", "y"]})
    assert sql == "WHERE a = ? AND r IN (?,?) AND t = :ignored" and vals == [1, "x", "y"]


def test_scaffold_from_layout(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, {})
    assert "year" in sc["parameters"]                  # slicer → parameter
    assert sc["visuals"]["v1"]["kind"] == "card" and "TODO" in sc["visuals"]["v1"]["sql"]
    assert sc["visuals"]["v4"]["kind"] == "custom"
    assert "v3" not in sc["visuals"]                  # slicers aren't visuals with data


def test_snapshot_html(fake_pbix, tmp_path):
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    values = semantic.resolve_params(spec, {"year": "2025"})
    assert values == {"year": 2025}
    be = FakeBackend(fixtures=FIX, calls=[])
    data = run_report(spec, values, be, use_cache=False)
    assert data["v1"]["rows"][0][0] == 1234567.8
    assert be.calls[0][1] == [2025]
    html = render_html(L, spec, values, data, mode="snapshot")
    assert 'id="v-v1"' in html and 'id="v-v2"' in html and 'id="v-v3"' not in html   # slicers aren't drawn
    assert "#0F2B46" in html                                                        # pbix theme
    assert '"kind": "column"' in html                                              # custom reinterpreted
    (tmp_path / "r.html").write_text(html)


def test_live_html_has_api_base(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    html = render_html(L, spec, {"year": 2026}, None, mode="live")
    assert "window.API_BASE" in html and 'id="data"' not in html


def test_hah_html_renders(fake_pbix):
    """ADR-004: only checks the template renders and embeds what the client-side JS
    needs (sql/params per visual, the SQL_API endpoint) — not verified against a real HAH."""
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    html = render_html(L, spec, {"year": 2026}, None, mode="hah", hah_base="https://hah.example/dev")
    assert "https://hah.example/dev/static/echarts.min.js" in html
    assert '"https://hah.example/dev/api/execute"' in html
    assert '"sql":' in html and 'SELECT SUM' in html   # visual sql embedded for client-side fetch
    assert "bindSql" in html and "safeSql" in html
    assert "__SNAPSHOT_CAPTURE__" in html
    assert 'id="data"' not in html and "window.API_BASE" not in html


def test_compare_tolerance():
    a = {"columns": ["category", "value"], "rows": [["N", 10.0], ["S", 5.0]]}
    b = {"columns": ["category", "value"], "rows": [["N", 10.0000001], ["S", 6.0]]}
    diffs = compare(a, b, {"rel": 1e-6})
    assert len(diffs) == 1 and diffs[0].startswith("('S',)")
