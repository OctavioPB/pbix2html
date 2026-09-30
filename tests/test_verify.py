"""HTML verifier: static checks on the spec, and rendered checks in a real browser (skipped without one)."""
import glob
import json
import os

import pytest

from pbix2html import verify


def _v(id_, x, y, w, h, kind="column", **extra):
    return {"id": id_, "kind": kind, "type": kind, "title": id_, "left": x, "top": y, "w": w, "h": h, "z": extra.pop("z", 0),
            "style": {}, "texts": {}, **extra}


def _spec(*visuals, W=1280, H=720):
    return {"report": "T", "pages": [{"id": "p1", "name": "P1", "width": W, "height": H, "visuals": list(visuals)}]}


def _rules(findings):
    return {(f.rule, f.severity) for f in findings}


def test_overlap_outside_page_tiny_and_missing_renderer_are_reported():
    spec = _spec(_v("a", 0, 0, 50, 50), _v("b", 20, 20, 50, 50, z=1),             # partial overlap
                 _v("c", 90, 90, 30, 30),                                          # off the page
                 _v("d", 5, 90, 0.5, 0.5),                                         # tiny
                 _v("e", 60, 0, 20, 20, kind="pending"))
    got = _rules(verify.static_checks(spec))
    assert ("overlap", "warn") in got or ("overlap", "info") in got
    assert ("outside_page", "warn") in got and ("too_small", "warn") in got and ("no_renderer", "error") in got


def test_intentional_overlays_and_toggled_views_are_not_bugs():
    big = _v("chart", 0, 0, 80, 80)
    control = _v("slicer", 60, 2, 15, 6, kind="slicer", z=5)                       # small control on a chart: a note
    assert _rules(verify.static_checks(_spec(big, control))) <= {("overlap", "info")}
    a, b = _v("v1", 0, 0, 50, 50, groups=["g1"]), _v("v2", 0, 0, 50, 50, groups=["g2"], start_hidden=True)
    assert not verify.static_checks(_spec(a, b))                                   # bookmark views stack by design
    assert not verify.static_checks(_spec(_v("s", 0, 0, 100, 100, kind="static"), _v("t", 10, 10, 20, 20, kind="text", text="x")))


def test_data_problems_are_reported():
    spec = _spec(_v("q", 0, 0, 30, 30, kind="card"), _v("r", 40, 0, 30, 30, kind="card"), _v("s", 0, 40, 30, 30, kind="card"))
    data = {"q": {"columns": ["value"], "rows": []}, "r": {"error": "boom", "columns": [], "rows": []}}
    got = {f.rule: f.severity for f in verify.static_checks(spec, data)}
    assert got == {"empty_result": "warn", "data_error": "error", "no_data": "warn"}


def test_a_report_without_the_embedded_spec_is_refused():
    with pytest.raises(ValueError):
        verify.read_embedded("<html></html>")


def _browser():
    pytest.importorskip("playwright")
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return os.getenv("PBIX2HTML_BROWSER") or (found[-1] if found else None)


PAGE = """<!doctype html><meta charset=utf-8><style>
 body{margin:0;background:#fff;font:14px sans-serif} .page{position:relative;width:1200px;height:700px;background:#fff}
 .visual{position:absolute;overflow:hidden} .title{padding:4px}
</style>
<nav class="tabs"><button data-page="p1" aria-selected="true">P1</button></nav>
<section class="page" id="p1">
 <div class="visual" data-visual="ok" data-kind="text" style="left:10px;top:10px;width:300px;height:40px"><div class="title" style="color:#222">Readable title</div></div>
 <div class="visual" data-visual="ghost" data-kind="text" style="left:10px;top:60px;width:300px;height:40px"><div class="title" style="color:#fefefe">Same colour as the page</div></div>
 <div class="visual" data-visual="under" data-kind="text" style="left:10px;top:120px;width:300px;height:40px"><div class="title" style="color:#222">Hidden under a box</div></div>
 <div class="visual" data-visual="cover" data-kind="static" style="left:0;top:110px;width:400px;height:70px;background:#0a3d62;z-index:5"></div>
 <div class="visual" data-visual="dark" data-kind="text" style="left:10px;top:200px;width:300px;height:40px;background:#0a3d62"><div class="title" style="color:#fff">White on navy</div></div>
 <div class="visual" data-visual="clip" data-kind="text" style="left:10px;top:260px;width:80px;height:30px"><div class="title" style="color:#222;white-space:nowrap;overflow:hidden">A very long title that does not fit</div></div>
 <div class="visual" data-visual="err" data-kind="column" style="left:500px;top:10px;width:300px;height:100px"><div class="body"><div class="error">HTTP 500</div></div></div>
</section>
<script id="spec" type="application/json">__SPEC__</script>
"""


def test_rendered_checks_find_invisible_covered_clipped_and_failed(tmp_path):
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    ids = ["ok", "ghost", "under", "cover", "dark", "clip", "err"]
    spec = _spec(*[_v(i, 0, 0, 1, 1, kind="text") for i in ids])
    html = tmp_path / "t.html"
    html.write_text(PAGE.replace("__SPEC__", json.dumps(spec)), encoding="utf-8")
    try:
        res = verify.verify_html(html, tmp_path / "out", browser=browser)
    except RuntimeError as e:
        pytest.skip(str(e))
    by = {(f.rule, f.visual) for f in res.findings}
    assert ("invisible_text", "ghost") in by                       # #fefefe on white
    assert ("text_covered", "under") in by                         # a navy box drawn over it
    assert ("text_clipped", "clip") in by or ("text_truncated", "clip") in by
    assert ("visual_error", "err") in by
    assert not [f for f in res.findings if f.visual in ("ok", "dark")]     # readable text is not flagged
    assert res.report.exists() and (tmp_path / "out" / "page-1.png").exists()
    assert not res.ok


CHART_STUB = """<script>window.echarts={getInstanceByDom:n=>n.id==='c1'?{getWidth:()=>300,getHeight:()=>200,getOption:()=>({
 xAxis:[{type:'category',data:Array.from({length:30},(_,i)=>'Category number '+i),axisLabel:{fontSize:12}}],
 yAxis:[{type:'value'}],series:[{type:'bar',data:[]}]})}:null}</script>"""


def test_rendered_checks_flag_chart_labels_that_cannot_fit(tmp_path):
    browser = _browser()
    if not browser:
        pytest.skip("no Chromium available")
    page = (PAGE.replace('<section class="page" id="p1">', CHART_STUB + '<section class="page" id="p1">')
            .replace('<div class="body"><div class="error">HTTP 500</div></div>', '<div class="body"><div id="c1" style="width:300px;height:200px"></div></div>'))
    spec = _spec(_v("err", 0, 0, 1, 1))
    html = tmp_path / "c.html"
    html.write_text(page.replace("__SPEC__", json.dumps(spec)), encoding="utf-8")
    try:
        res = verify.verify_html(html, tmp_path / "out", browser=browser)
    except RuntimeError as e:
        pytest.skip(str(e))
    assert any(f.rule == "chart_labels_crowded" and f.visual == "err" for f in res.findings)
