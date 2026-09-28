"""
Local web panel: runs extract/scaffold/convert/validate from forms, without
using a terminal. Meant for someone non-technical (or who just doesn't want to
open a console) to generate and review reports already configured in `metrics/`.

Start:  pbix2html gui   (opens http://127.0.0.1:8765 in the browser)

This is NOT the "live" mode service (see serve.py): it doesn't apply SSO
authentication or RLS, and it isn't meant to be exposed to a report's end
users — it's a local operating tool for whoever builds/validates the reports,
with the same trust level as running the CLI by hand. Don't expose it outside localhost.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from threading import Timer
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader

from . import extract as ex
from . import semantic
from .config import settings
from .query import FakeBackend, TeradataBackend, run_report
from .render import render_html
from .validate import validate_report, write_markdown

REPORTS_DIR = Path("reports")
OUT_DIR = Path("out")
DEMO_FIXTURE = Path("tests/fixtures/fake_block.json")
TEMPLATES_DIR = Path(__file__).parent / "gui_templates"

app = FastAPI(title="pbix2html — panel")

_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


class _Templates:
    """Thin stand-in for starlette's Jinja2Templates.

    Starlette's wrapper stashes `env.globals` (a dict) inside Jinja2's template LRU
    cache key; Python 3.13 tightened hashability rules and that raises
    `TypeError: unhashable type: 'dict'` on every request. Calling Jinja2 directly
    sidesteps it — same `TemplateResponse(name, context)` call shape, so nothing
    downstream needs to change.
    """

    def TemplateResponse(self, name: str, context: dict) -> HTMLResponse:
        template = _jinja_env.get_template(name)
        return HTMLResponse(template.render(context))


templates = _Templates()


# ----------------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------------

def _pbix_files() -> list[Path]:
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.glob("*.pbix"))


def _url_reachable(url: str, timeout: float = 1.5) -> bool:
    """Best-effort, short-timeout GET — used only to warn early that `serve.py` isn't
    up yet for a `--mode live` report, not as a health check anyone should rely on."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _find_pbix(name: str) -> Path:
    """Only accepts names that match a real .pbix under reports/: prevents
    someone from editing the URL by hand and trying to read another disk path."""
    for p in _pbix_files():
        if p.stem == name:
            return p
    raise HTTPException(status_code=404, detail=f"'{name}.pbix' not found under reports/")


def _load_spec(name: str) -> tuple[semantic.ReportSpec | None, str | None]:
    """(spec, None) if it loaded fine; (None, message) if there's no yaml or it has an
    error, without taking down the detail page over a hand-written yaml gone wrong."""
    if not semantic.yaml_path(name).exists():
        return None, None
    try:
        return semantic.load(name), None
    except Exception as e:
        return None, f"metrics/{name}.yaml has an error and couldn't be read: {type(e).__name__}: {e}"


def _real_roles(spec: semantic.ReportSpec | None) -> list[str]:
    if not spec or not spec.roles:
        return []
    roles = list(spec.roles.keys())
    return [] if roles == ["default"] else roles


def _page(request: Request, pbix: Path, result: dict[str, Any] | None = None) -> HTMLResponse:
    name = pbix.stem
    spec, yaml_error = _load_spec(name)
    ctx = {
        "request": request,
        "name": name,
        "pbix": str(pbix),
        "has_yaml": semantic.yaml_path(name).exists(),
        "yaml_error": yaml_error,
        "spec": spec,
        "roles": _real_roles(spec),
        "has_teradata": settings.has_teradata,
        "demo_available": DEMO_FIXTURE.exists(),
        "result": result,
    }
    return templates.TemplateResponse("report.html", ctx)


# ----------------------------------------------------------------------------
# Home: report list + upload a new one
# ----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    rows = []
    for pbix in _pbix_files():
        name = pbix.stem
        yaml_path = semantic.yaml_path(name)
        html_files = sorted(OUT_DIR.glob(f"{name}*.html")) if OUT_DIR.exists() else []
        rows.append({
            "name": name,
            "has_yaml": yaml_path.exists(),
            "n_html": len(html_files),
        })
    return templates.TemplateResponse("index.html", {"request": request, "reports": rows})


@app.post("/upload")
def upload_pbix(file: UploadFile):
    filename = Path(file.filename or "").name  # strips any path; just the filename
    if not filename.lower().endswith(".pbix") or not re.fullmatch(r"[\w\-. ]+\.pbix", filename, re.I):
        raise HTTPException(400, "Upload a .pbix file with a simple name (letters, digits, spaces, - or _).")
    REPORTS_DIR.mkdir(exist_ok=True)
    destination = REPORTS_DIR / filename
    destination.write_bytes(file.file.read())
    return HTMLResponse(
        f'<meta http-equiv="refresh" content="0; url=/reports/{destination.stem}">', status_code=303
    )


# ----------------------------------------------------------------------------
# Report detail
# ----------------------------------------------------------------------------

@app.get("/reports/{name}", response_class=HTMLResponse)
def view_report(request: Request, name: str):
    pbix = _find_pbix(name)
    return _page(request, pbix)


@app.post("/reports/{name}/extract")
def action_extract(request: Request, name: str):
    pbix = _find_pbix(name)
    try:
        layout = ex.extract_layout(pbix)
        model = ex.extract_model(pbix)
        rdir = OUT_DIR / ex.safe_name(name)
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "layout.json").write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
        (rdir / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        n_vis = sum(len(p["visuals"]) for p in layout["pages"])
        n_meas = len(model.get("measures") or []) if isinstance(model.get("measures"), list) else 0
        result = {
            "ok": True, "title": "Structure extracted",
            "message": f"{len(layout['pages'])} pages, {n_vis} visuals, {n_meas} measures.",
            "detail": [f"Model warning: {model['error']}"] if model.get("error") else [],
        }
    except Exception as e:
        result = {"ok": False, "title": "Couldn't extract", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


@app.post("/reports/{name}/scaffold")
def action_scaffold(request: Request, name: str, regenerate: bool = Form(False)):
    pbix = _find_pbix(name)
    try:
        layout = ex.extract_layout(pbix)
        model = ex.extract_model(pbix)
        table_map = _load_table_map(name)
        path = semantic.write_scaffold(layout, model, overwrite=regenerate, table_map=table_map)
        detail = ["Fill in the SQL by hand (look for \"TODO\" in the file) before converting."]
        if table_map:
            detail.append(f"Used the {len(table_map)} Power BI → Teradata table mapping(s) to "
                          f"pre-fill the SQL (step 2b).")
        result = {"ok": True, "title": "Template generated", "message": f"Wrote {path}.", "detail": detail}
    except FileExistsError as e:
        result = {"ok": False, "title": "A template already exists", "message": str(e), "detail": []}
    except Exception as e:
        result = {"ok": False, "title": "Couldn't generate the template", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


# ----------------------------------------------------------------------------
# Step 2b: Power BI table → Teradata source query
# ----------------------------------------------------------------------------

def _table_map_path(name: str) -> Path:
    return semantic.METRICS_DIR / f"{name}.table_map.json"


def _load_table_map(name: str) -> dict[str, str]:
    path = _table_map_path(name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pbi_entities(name: str) -> list[str]:
    """Unique Power BI table names referenced by the report's visuals, taken from the
    layout already extracted in step 1 (out/<name>/layout.json). Uses
    `semantic.query_ref_parts` to strip any Sum(...)/Avg(...)/... aggregation wrapper
    before reading the table name — a naive split on the first '.' mangles those
    (`"Sum(Sales.Amount)"` → `"Sum(Sales"`, not `"Sales"`)."""
    layout_path = OUT_DIR / ex.safe_name(name) / "layout.json"
    if not layout_path.exists():
        return []
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    entities: set[str] = set()
    for page in layout.get("pages", []):
        for v in page.get("visuals", []):
            for ref in v.get("fields") or []:
                if isinstance(ref, str):
                    _, table, _ = semantic.query_ref_parts(ref)
                    if table:
                        entities.add(table)
    return sorted(entities)


def _table_map_page(request: Request, name: str, pbix: Path, result: dict[str, Any] | None = None,
                     pending: dict[str, str] | None = None) -> HTMLResponse:
    return templates.TemplateResponse("table_map.html", {
        "request": request, "name": name, "pbix": str(pbix),
        "entities": _pbi_entities(name),
        "mapping": pending if pending is not None else _load_table_map(name),
        "result": result,
    })


@app.get("/reports/{name}/table-map", response_class=HTMLResponse)
def view_table_map(request: Request, name: str):
    pbix = _find_pbix(name)
    return _table_map_page(request, name, pbix)


@app.post("/reports/{name}/table-map")
async def save_table_map(request: Request, name: str):
    pbix = _find_pbix(name)
    form = await request.form()
    submitted = {}
    for key, val in form.items():
        if key.startswith("td__") and str(val).strip():
            submitted[key[len("td__"):]] = str(val).strip()

    validated: dict[str, str] = {}
    errors: list[str] = []
    for entity, query in submitted.items():
        try:
            validated[entity] = semantic.validate_read_only_sql(query)
        except ValueError as e:
            errors.append(f"{entity}: {e}")

    if errors:
        result = {"ok": False, "title": "Mapping not saved", "detail": errors,
                   "message": "Only single, read-only SELECT queries are allowed. Fix the entries below and save again."}
        return _table_map_page(request, name, pbix, result, pending=submitted)

    path = _table_map_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"ok": True, "title": "Mapping saved",
              "message": f"{len(validated)} Power BI table(s) mapped to a Teradata query.", "detail": []}
    return _page(request, pbix, result)


@app.post("/reports/{name}/convert")
async def action_convert(request: Request, name: str):
    pbix = _find_pbix(name)
    spec, yaml_error = _load_spec(name)
    if spec is None:
        result = {"ok": False, "title": "Missing the metrics template", "detail": [yaml_error] if yaml_error else [],
                   "message": "Generate the template first (step 2) and fill in the SQL before converting."}
        return _page(request, pbix, result)

    form = await request.form()
    mode = form.get("mode", "snapshot")
    role_name = form.get("role", "")
    use_demo = form.get("use_demo") == "on"
    hah_env = form.get("hah_env", "dev")
    overrides = {}
    for pname in spec.parameters:
        val = form.get(f"param__{pname}")
        if val not in (None, ""):
            overrides[pname] = val
    return _convert_impl(request, pbix, spec, mode, role_name, use_demo, overrides, hah_env)


def _convert_impl(request: Request, pbix: Path, spec: semantic.ReportSpec, mode: str, role_name: str,
                   use_demo: bool, overrides: dict[str, str], hah_env: str = "dev") -> HTMLResponse:
    name = pbix.stem
    try:
        layout = ex.extract_layout(pbix)
        values = semantic.resolve_params(spec, overrides)

        role = role_name or None
        proxy_user = None
        if role:
            r = spec.roles.get(role)
            if r is None:
                raise ValueError(f"role '{role}' isn't defined in metrics/{name}.yaml")
            proxy_user = r.get("proxy_user")

        data = None
        hah_base = None
        if mode == "hah":
            # ADR-004: the HAH platform fetches each visual's SQL itself, client-side —
            # there's nothing to run against Teradata here, same as live mode.
            hah_base = settings.hah_bases.get(hah_env, settings.hah_bases["dev"])
        elif mode == "snapshot":
            if settings.has_teradata:
                backend = TeradataBackend()
            elif use_demo and DEMO_FIXTURE.exists():
                block = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))
                backend = FakeBackend(fixtures={}, default=block)
            else:
                raise ValueError(
                    "No Teradata connection is configured (.env) and you didn't check "
                    "\"use test data\". Ask the technical team to set up the credentials, "
                    "or check the demo box to preview the design."
                )
            data = run_report(spec, values, backend, proxy_user=proxy_user)

        html = render_html(layout, spec, values, data, mode=mode, role=role, hah_base=hah_base)
        out_name = f"{name}" + (f".{role}" if role else "") + ".html"
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / out_name).write_text(html, encoding="utf-8")

        n_err = sum(1 for d in (data or {}).values() if d.get("error")) if data else None
        detail = []
        if n_err:
            detail.append(f"{n_err} visual(s) had an error fetching data (shown inside the HTML).")
        if mode == "hah":
            detail.append(
                f"HTML generated for HAH ({hah_env}, {hah_base}); not tested against a real HAH "
                f"yet (see ADR-004). Upload it with the teradata-report skill's create_report "
                f"tool before trusting it for production."
            )
        if mode == "live":
            if _url_reachable(f"{settings.api_base}/healthz"):
                detail.append(f"Checked {settings.api_base}/healthz just now — reachable.")
            else:
                detail.append(
                    f"⚠ Couldn't reach {settings.api_base}/healthz just now. This report will show "
                    f"\"Failed to fetch\" on every visual until that's running — start it with: "
                    f"uvicorn pbix2html.serve:app (leave that terminal open), then reload the report."
                )
        mode_label = {"snapshot": "snapshot", "live": "live", "hah": f"HAH ({hah_env})"}.get(mode, mode)
        result = {
            "ok": True, "title": "HTML generated", "detail": detail,
            "message": f"{mode_label} mode" + (f", role {role}" if role else "") + ".",
            "html_link": f"/files/{out_name}",
        }
    except Exception as e:
        result = {"ok": False, "title": "Couldn't convert", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


@app.post("/reports/{name}/validate")
def action_validate(request: Request, name: str):
    pbix = _find_pbix(name)
    spec, yaml_error = _load_spec(name)
    if spec is None:
        result = {"ok": False, "title": "Missing the metrics template", "detail": [yaml_error] if yaml_error else [],
                   "message": "Generate the template first (step 2) and fill in the SQL before validating."}
        return _page(request, pbix, result)
    if not settings.has_teradata:
        result = {"ok": False, "title": "Validating needs Teradata", "detail": [],
                   "message": "Ask the technical team to set TERADATA_HOST/USER/PASSWORD in .env."}
        return _page(request, pbix, result)
    try:
        values = semantic.resolve_params(spec, {})
        results = validate_report(spec, values, TeradataBackend())
        path = write_markdown(spec, results)
        counts = {s: sum(1 for r in results.values() if r["status"] == s) for s in ("OK", "DIFF", "SKIP", "ERROR")}
        detail = [f"{vid}: {r['status']}" + (f" — {r['detail'][0]}" if r.get("detail") else "")
                  for vid, r in results.items() if r["status"] != "OK"]
        result = {
            "ok": counts["DIFF"] == 0 and counts["ERROR"] == 0, "title": "Validation finished",
            "message": f"OK {counts['OK']} · DIFF {counts['DIFF']} · SKIP {counts['SKIP']} · ERROR {counts['ERROR']} "
                       f"(full detail in {path}).",
            "detail": detail[:20],
        }
    except Exception as e:
        result = {"ok": False, "title": "Couldn't validate", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


# ----------------------------------------------------------------------------
# Serve the generated HTML files
# ----------------------------------------------------------------------------

@app.get("/files/{filename}")
def serve_file(filename: str):
    if "/" in filename or "\\" in filename or not filename.lower().endswith(".html"):
        raise HTTPException(400, "invalid file name")
    path = OUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type="text/html")


# ----------------------------------------------------------------------------
# Startup
# ----------------------------------------------------------------------------

def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn
    url = f"http://{host}:{port}/"
    if open_browser:
        Timer(1.0, webbrowser.open, [url]).start()
    print(f"pbix2html panel: {url}  (close this window to shut it down)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
