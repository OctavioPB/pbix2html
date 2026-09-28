"""Live endpoint with a fake backend (no Teradata)."""
from fastapi.testclient import TestClient
from pbix2html import serve
from pbix2html.query import FakeBackend


def test_visual_endpoint_uses_proxy_user():
    be = FakeBackend(fixtures={}, default={"columns": ["value"], "rows": [[42.0]]}, calls=[])
    serve.app.state.backend = be
    c = TestClient(serve.app)
    r = c.get("/reports/Executive_Dashboard/visuals/v1?year=2024")
    assert r.status_code == 401                       # no SSO means no data
    r = c.get("/reports/Executive_Dashboard/visuals/v1?year=2024", headers={"X-Authenticated-User": "alice"})
    assert r.status_code == 200 and r.json()["rows"] == [[42.0]]
    assert be.calls[-1][2] == "alice" and be.calls[-1][1] == [2024]
    assert c.get("/reports/Executive_Dashboard/visuals/nope", headers={"X-Authenticated-User": "o"}).status_code == 404


def test_cors_default_does_not_combine_wildcard_with_credentials():
    """Regression: `Access-Control-Allow-Origin: *` together with
    `Access-Control-Allow-Credentials: true` is invalid per the CORS spec — every
    browser rejects that combination outright. That's what made every live-mode
    report's fetch() fail with "Failed to fetch", even with serve.py reachable and
    working: the default CORS_ORIGINS ("*") plus the report's `credentials: 'include'`
    (since removed, see report.html.j2) produced exactly this response."""
    be = FakeBackend(fixtures={}, default={"columns": ["value"], "rows": [[1.0]]}, calls=[])
    serve.app.state.backend = be
    c = TestClient(serve.app)
    r = c.get("/reports/Executive_Dashboard/visuals/v1?year=2024",
              headers={"X-Authenticated-User": "alice", "Origin": "null"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in r.headers


def test_backend_error_still_carries_cors_headers():
    """Regression: an unhandled exception from the backend (e.g. a Teradata driver/
    connection error) used to produce a bare 500 with NO CORS headers at all — the
    browser rejects that response before the report's JS ever sees the real error,
    showing "Failed to fetch" instead of the actual problem. visual() now wraps
    run_visual() so backend failures come back as an HTTPException, which keeps CORS
    headers on the response."""
    class BrokenBackend:
        def execute(self, sql, values, proxy_user=None):
            raise RuntimeError("connection refused")

    serve.app.state.backend = BrokenBackend()
    c = TestClient(serve.app)
    # A distinct year avoids colliding with another test's cached result under cache/
    # (query.py's on-disk cache isn't test-isolated — a known separate issue).
    r = c.get("/reports/Executive_Dashboard/visuals/v1?year=2099",
              headers={"X-Authenticated-User": "alice", "Origin": "null"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "*"
    assert "connection refused" in r.json()["detail"]


def test_report_info_endpoint_confirms_report_without_running_a_query():
    """Used by gui.py's live-mode convert step to tell "serve.py isn't up" apart from
    "serve.py is up but can't see this report" (the two look identical to a plain
    /healthz check, but need different fixes)."""
    c = TestClient(serve.app)
    r = c.get("/reports/Executive_Dashboard")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["report"] == "Executive_Dashboard"
    assert "v1" in body["visuals"]
    assert c.get("/reports/NoSuchReport").status_code == 404


def test_missing_yaml_error_names_the_path_it_looked_for():
    """Regression: a bare "report X has no yaml" 404 gave no hint that the #1 cause is
    running serve.py from the wrong working directory (metrics/ is a relative path) —
    now the error names the exact path it checked and serve.py's actual cwd."""
    r = TestClient(serve.app).get("/reports/NoSuchReport")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "NoSuchReport.yaml" in detail
    assert "project root" in detail
