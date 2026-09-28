"""Full pipeline without Teradata: extract → yaml → FakeBackend → render → HTML."""
import json
from pbix2html import extract as ex, semantic
from pbix2html.query import FakeBackend, bind, run_report
from pbix2html.render import render_html, build_spec
from pbix2html.validate import compare

FIX = {
    "SELECT SUM(importe) AS value FROM ventas WHERE anio = ?": {"columns": ["value"], "rows": [[1234567.8]]},
    "SELECT region AS category, margen AS value FROM v_margen_region WHERE anio = ?":
        {"columns": ["category", "value"], "rows": [["Norte", 0.21], ["Sur", 0.18], ["Centro", None]]},
    "SELECT mes AS category, importe AS value FROM v_ingresos_mes WHERE anio = ?":
        {"columns": ["category", "value"], "rows": [["Ene", 10], ["Feb", 12]]},
    "SELECT cliente, importe FROM v_top_clientes WHERE anio = ?":
        {"columns": ["cliente", "importe"], "rows": [["ACME", 100.5], ["Globex", 90.0]]},
}


def test_bind_expands_lists():
    sql, vals = bind("WHERE a = :a AND r IN (:r) AND t = :ignored", ["a", "r"], {"a": 1, "r": ["x", "y"]})
    assert sql == "WHERE a = ? AND r IN (?,?) AND t = :ignored" and vals == [1, "x", "y"]


def test_scaffold_from_layout(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    sc = semantic.scaffold(L, {})
    assert "anio" in sc["parameters"]                 # slicer → parameter
    assert sc["visuals"]["v1"]["kind"] == "card" and "TODO" in sc["visuals"]["v1"]["sql"]
    assert sc["visuals"]["v4"]["kind"] == "custom"
    assert "v3" not in sc["visuals"]                  # slicers aren't visuals with data


def test_snapshot_html(fake_pbix, tmp_path):
    L = ex.extract_layout(fake_pbix)
    spec = semantic.load("Dashboard_Ejecutivo")
    values = semantic.resolve_params(spec, {"anio": "2025"})
    assert values == {"anio": 2025}
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
    spec = semantic.load("Dashboard_Ejecutivo")
    html = render_html(L, spec, {"anio": 2026}, None, mode="live")
    assert "window.API_BASE" in html and 'id="data"' not in html


def test_compare_tolerance():
    a = {"columns": ["category", "value"], "rows": [["N", 10.0], ["S", 5.0]]}
    b = {"columns": ["category", "value"], "rows": [["N", 10.0000001], ["S", 6.0]]}
    diffs = compare(a, b, {"rel": 1e-6})
    assert len(diffs) == 1 and diffs[0].startswith("('S',)")
