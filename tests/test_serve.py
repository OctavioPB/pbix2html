"""Live endpoint with a fake backend (no Teradata)."""
from fastapi.testclient import TestClient
from pbix2html import serve
from pbix2html.query import FakeBackend


def test_visual_endpoint_uses_proxy_user():
    be = FakeBackend(fixtures={}, default={"columns": ["value"], "rows": [[42.0]]}, calls=[])
    serve.app.state.backend = be
    c = TestClient(serve.app)
    r = c.get("/reports/Dashboard_Ejecutivo/visuals/v1?anio=2024")
    assert r.status_code == 401                       # no SSO means no data
    r = c.get("/reports/Dashboard_Ejecutivo/visuals/v1?anio=2024", headers={"X-Authenticated-User": "octavio"})
    assert r.status_code == 200 and r.json()["rows"] == [[42.0]]
    assert be.calls[-1][2] == "octavio" and be.calls[-1][1] == [2024]
    assert c.get("/reports/Dashboard_Ejecutivo/visuals/nope", headers={"X-Authenticated-User": "o"}).status_code == 404
