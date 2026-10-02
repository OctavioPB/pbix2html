"""Where a hah-mode report sends its SQL.

`--hah-base` is a guess made at build time; HAH serves the report from its own origin at
`{base}/api/reports/{id}/view`. When the two disagree the POST is cross-origin, HAH answers it
without CORS headers, and the browser says only "Failed to fetch" — which is what a real HAH
reported. So the endpoint is resolved from the page's own URL at call time, and these tests pin
that down with a server that actually serves the report the way HAH does.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pbix2html.render import render_html
from pbix2html.semantic import ReportSpec, VisualSpec
from tests.test_verify import _browser

VIEW_PATH = "/dev-html-app-host/api/reports/42/view"      # how HAH serves a report (fixv1 §10)
EXECUTE_PATH = "/dev-html-app-host/api/execute"
PLAIN_PATH = "/static-site/report.html"                   # served over http, but no /api/ mount point
STATIC_ECHARTS = "/dev-html-app-host/static/echarts.min.js"


def _hah_html(base="https://wrong.example/other-app"):
    """A one-card report built for the wrong host — the mistake that produces "Failed to fetch"."""
    card = {"id": "c1", "type": "card", "hidden": False, "is_group": False, "parent_group": None,
            "groups": [], "x": 40, "y": 40, "width": 300, "height": 150, "z": 1, "title": "Total",
            "style": {}, "sort": None, "cond_formats": [], "n_fields": None, "col_align": [],
            "y_fields": [], "action": None}
    layout = {"theme": {"custom_json": {"dataColors": ["#118DFF"]}},
              "pages": [{"display_name": "P", "width": 1280, "height": 720, "visuals": [card]}]}
    spec = ReportSpec(report="T", source=None, connection="", delivery="", parameters={}, roles={},
                      visuals={"c1": VisualSpec(id="c1", kind="card", title=None,
                                                sql="SELECT COUNT(*) AS value FROM t")}, raw={})
    return render_html(layout, spec, {}, None, mode="hah", hah_base=base)


class _Hah(BaseHTTPRequestHandler):
    """The two routes of HAH this report touches, and a record of what it asked for."""

    html = ""
    posts: list = []
    gets: list = []

    def do_GET(self):                                            # noqa: N802  (BaseHTTPRequestHandler API)
        if self.path == STATIC_ECHARTS:                          # a stub: these tests draw no chart
            body, ctype = b"window.echarts = { stub: true };", "application/javascript"
        elif self.path.startswith(VIEW_PATH) or self.path.startswith(PLAIN_PATH):
            body, ctype = self.html.encode("utf-8"), "text/html; charset=utf-8"
        else:
            body, ctype = b"not found", "text/plain"
        self.gets.append(self.path)
        self.send_response(404 if body == b"not found" else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                           # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        self.posts.append((self.path, raw))
        payload = (json.dumps({"data": [{"value": 1234}]}) if self.path == EXECUTE_PATH
                   else json.dumps({"error": True, "error_message": "no such endpoint"}))
        body = payload.encode("utf-8")
        self.send_response(200 if self.path == EXECUTE_PATH else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                                   # keep pytest output clean
        return


@pytest.fixture
def hah_server():
    _Hah.posts, _Hah.gets = [], []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Hah)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_a_report_served_by_hah_posts_to_the_hah_that_served_it(hah_server):
    """The build-time base points at another host entirely; the report must ignore it and use the
    origin and mount point it is actually being served from, or every visual fails to fetch."""
    if not _browser():
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    _Hah.html = _hah_html()
    port = hah_server.server_address[1]
    with sync.sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{port}{VIEW_PATH}")
        pg.wait_for_timeout(1200)
        value = pg.inner_text(".card .value")
        br.close()
    assert not errors, errors
    assert [p for p, _ in _Hah.posts] == [EXECUTE_PATH], _Hah.posts
    assert "wrong.example" not in json.dumps(_Hah.posts)
    assert "1,234" in value or "1234" in value          # the data arrived and was drawn


def test_without_a_serving_origin_it_falls_back_to_the_build_time_base_and_can_be_overridden(tmp_path):
    """Opened from disk there is no mount point to read, so `--hah-base` is all there is. `?sqlApi=`
    wins over both, so a wrong endpoint can be tried on HAH without rebuilding the report."""
    if not _browser():
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    f = tmp_path / "r.html"
    f.write_text(_hah_html(), encoding="utf-8")
    seen = []

    def answer(route):
        # the glob also matches the document when `?sqlApi=` is in the address bar, so only the
        # page's own XHR is answered here — intercepting the navigation would serve JSON as the page
        if route.request.resource_type != "fetch":
            return route.continue_()
        seen.append(route.request.url)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"data": [{"value": 7}]}))

    with sync.sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page()
        pg.route("**/api/execute*", answer)
        pg.goto(f.as_uri())
        pg.wait_for_timeout(900)
        pg.goto(f.as_uri() + "?sqlApi=https://forced.example/custom/api/execute")
        pg.wait_for_timeout(900)
        br.close()
    assert seen[0] == "https://wrong.example/other-app/api/execute", seen
    assert seen[-1] == "https://forced.example/custom/api/execute", seen


def test_the_chart_library_also_falls_back_to_the_serving_origin(hah_server):
    """`<script src>` carries the same build-time guess as the SQL endpoint: a report built for the
    wrong host loads no ECharts either, and every chart dies with "echarts is not defined"."""
    if not _browser():
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    _Hah.html = _hah_html()                       # built for wrong.example, served from HAH
    port = hah_server.server_address[1]
    with sync.sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page()
        pg.goto(f"http://127.0.0.1:{port}{VIEW_PATH}")
        pg.wait_for_timeout(1000)
        loaded = pg.evaluate("() => !!(window.echarts && window.echarts.stub)")
        failed = pg.evaluate("() => window.__ECHARTS_SRC_FAILED__ || ''")
        br.close()
    assert STATIC_ECHARTS in _Hah.gets, _Hah.gets          # asked the origin serving it
    assert loaded                                          # and the library is there
    assert "wrong.example" in failed                       # after the configured copy failed


def test_a_dead_endpoint_says_where_it_tried_and_why(hah_server):
    """"Failed to fetch" on its own is unactionable. The visual has to name the URL it used and
    whether the call was cross-origin — that is the difference between a rebuild and a round trip."""
    if not _browser():
        pytest.skip("no Chromium available")
    sync = pytest.importorskip("playwright.sync_api")
    # served over http, but from a path with no `/api/` mount point to read: the report falls back
    # to the build-time base, and that host does not answer — the failure the user hit.
    _Hah.html = _hah_html(base="https://unreachable.invalid/app")
    port = hah_server.server_address[1]
    with sync.sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page()
        pg.goto(f"http://127.0.0.1:{port}{PLAIN_PATH}")
        pg.wait_for_timeout(1500)
        msg = pg.inner_text(".visual .error-state, .visual .error")
        br.close()
    assert "unreachable.invalid/app/api/execute" in msg          # the URL it actually tried
    assert "cross-origin" in msg                                 # and why it never arrived
