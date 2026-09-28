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
import webbrowser
from pathlib import Path
from threading import Timer
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from . import extract as ex
from . import semantic
from .config import settings
from .query import FakeBackend, TeradataBackend, run_report
from .render import render_html
from .validate import validate_report, write_markdown

REPORTES_DIR = Path("reportes")
OUT_DIR = Path("out")
DEMO_FIXTURE = Path("tests/fixtures/fake_block.json")
TEMPLATES_DIR = Path(__file__).parent / "gui_templates"

app = FastAPI(title="pbix2html — panel")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ----------------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------------

def _pbix_files() -> list[Path]:
    if not REPORTES_DIR.exists():
        return []
    return sorted(REPORTES_DIR.glob("*.pbix"))


def _find_pbix(name: str) -> Path:
    """Only accepts names that match a real .pbix under reportes/: prevents
    someone from editing the URL by hand and trying to read another disk path."""
    for p in _pbix_files():
        if p.stem == name:
            return p
    raise HTTPException(status_code=404, detail=f"No se encontró '{name}.pbix' en reportes/")


def _load_spec(name: str) -> tuple[semantic.ReportSpec | None, str | None]:
    """(spec, None) if it loaded fine; (None, message) if there's no yaml or it has an
    error, without taking down the detail page over a hand-written yaml gone wrong."""
    if not semantic.yaml_path(name).exists():
        return None, None
    try:
        return semantic.load(name), None
    except Exception as e:
        return None, f"metrics/{name}.yaml tiene un error y no se pudo leer: {type(e).__name__}: {e}"


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
        "demo_disponible": DEMO_FIXTURE.exists(),
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
    return templates.TemplateResponse("index.html", {"request": request, "reportes": rows})


@app.post("/subir")
def subir_pbix(archivo: UploadFile):
    nombre = Path(archivo.filename or "").name  # strips any path; just the filename
    if not nombre.lower().endswith(".pbix") or not re.fullmatch(r"[\w\-. ]+\.pbix", nombre, re.I):
        raise HTTPException(400, "Subí un archivo .pbix con nombre simple (letras, números, espacios, - o _).")
    REPORTES_DIR.mkdir(exist_ok=True)
    destino = REPORTES_DIR / nombre
    destino.write_bytes(archivo.file.read())
    return HTMLResponse(
        f'<meta http-equiv="refresh" content="0; url=/reportes/{destino.stem}">', status_code=303
    )


# ----------------------------------------------------------------------------
# Report detail
# ----------------------------------------------------------------------------

@app.get("/reportes/{name}", response_class=HTMLResponse)
def ver_reporte(request: Request, name: str):
    pbix = _find_pbix(name)
    return _page(request, pbix)


@app.post("/reportes/{name}/extraer")
def accion_extraer(request: Request, name: str):
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
            "ok": True, "title": "Estructura extraída",
            "message": f"{len(layout['pages'])} páginas, {n_vis} visuales, {n_meas} medidas.",
            "detail": [f"Aviso del modelo: {model['error']}"] if model.get("error") else [],
        }
    except Exception as e:
        result = {"ok": False, "title": "No se pudo extraer", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


@app.post("/reportes/{name}/plantilla")
def accion_plantilla(request: Request, name: str, regenerar: bool = Form(False)):
    pbix = _find_pbix(name)
    try:
        layout = ex.extract_layout(pbix)
        model = ex.extract_model(pbix)
        path = semantic.write_scaffold(layout, model, overwrite=regenerar)
        result = {"ok": True, "title": "Plantilla generada", "message": f"Se escribió {path}.",
                  "detail": ["Completá el SQL a mano (buscá \"TODO\" en el archivo) antes de convertir."]}
    except FileExistsError as e:
        result = {"ok": False, "title": "Ya existe una plantilla", "message": str(e), "detail": []}
    except Exception as e:
        result = {"ok": False, "title": "No se pudo generar la plantilla", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


@app.post("/reportes/{name}/convertir")
async def accion_convertir(request: Request, name: str):
    pbix = _find_pbix(name)
    spec, yaml_error = _load_spec(name)
    if spec is None:
        result = {"ok": False, "title": "Falta la plantilla de métricas", "detail": [yaml_error] if yaml_error else [],
                   "message": "Generá primero la plantilla (paso 2) y completá el SQL antes de convertir."}
        return _page(request, pbix, result)

    form = await request.form()
    modo = form.get("modo", "snapshot")
    rol = form.get("rol", "")
    usar_demo = form.get("usar_demo") == "on"
    overrides = {}
    for pname in spec.parameters:
        val = form.get(f"param__{pname}")
        if val not in (None, ""):
            overrides[pname] = val
    return _convertir_impl(request, pbix, spec, modo, rol, usar_demo, overrides)


def _convertir_impl(request: Request, pbix: Path, spec: semantic.ReportSpec, modo: str, rol: str,
                     usar_demo: bool, overrides: dict[str, str]) -> HTMLResponse:
    name = pbix.stem
    try:
        layout = ex.extract_layout(pbix)
        values = semantic.resolve_params(spec, overrides)

        role = rol or None
        proxy_user = None
        if role:
            r = spec.roles.get(role)
            if r is None:
                raise ValueError(f"rol '{role}' no está definido en metrics/{name}.yaml")
            proxy_user = r.get("proxy_user")

        data = None
        if modo == "snapshot":
            if settings.has_teradata:
                backend = TeradataBackend()
            elif usar_demo and DEMO_FIXTURE.exists():
                block = json.loads(DEMO_FIXTURE.read_text(encoding="utf-8"))
                backend = FakeBackend(fixtures={}, default=block)
            else:
                raise ValueError(
                    "No hay conexión a Teradata configurada (.env) y no marcaste "
                    "\"usar datos de prueba\". Pedile al equipo técnico que configure "
                    "las credenciales, o tildá la casilla de demo para probar el diseño."
                )
            data = run_report(spec, values, backend, proxy_user=proxy_user)

        html = render_html(layout, spec, values, data, mode=modo, role=role)
        out_name = f"{name}" + (f".{role}" if role else "") + ".html"
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / out_name).write_text(html, encoding="utf-8")

        n_err = sum(1 for d in (data or {}).values() if d.get("error")) if data else None
        detail = []
        if n_err:
            detail.append(f"{n_err} visual(es) con error al traer datos (se muestran dentro del HTML).")
        result = {
            "ok": True, "title": "HTML generado", "detail": detail,
            "message": f"Modo {'foto' if modo == 'snapshot' else 'en vivo'}"
                       + (f", rol {role}" if role else "") + ".",
            "html_link": f"/archivos/{out_name}",
        }
    except Exception as e:
        result = {"ok": False, "title": "No se pudo convertir", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


@app.post("/reportes/{name}/validar")
def accion_validar(request: Request, name: str):
    pbix = _find_pbix(name)
    spec, yaml_error = _load_spec(name)
    if spec is None:
        result = {"ok": False, "title": "Falta la plantilla de métricas", "detail": [yaml_error] if yaml_error else [],
                   "message": "Generá primero la plantilla (paso 2) y completá el SQL antes de validar."}
        return _page(request, pbix, result)
    if not settings.has_teradata:
        result = {"ok": False, "title": "Validar requiere Teradata", "detail": [],
                   "message": "Pedile al equipo técnico que configure TERADATA_HOST/USER/PASSWORD en .env."}
        return _page(request, pbix, result)
    try:
        values = semantic.resolve_params(spec, {})
        results = validate_report(spec, values, TeradataBackend())
        path = write_markdown(spec, results)
        counts = {s: sum(1 for r in results.values() if r["status"] == s) for s in ("OK", "DIFF", "SKIP", "ERROR")}
        detail = [f"{vid}: {r['status']}" + (f" — {r['detail'][0]}" if r.get("detail") else "")
                  for vid, r in results.items() if r["status"] != "OK"]
        result = {
            "ok": counts["DIFF"] == 0 and counts["ERROR"] == 0, "title": "Validación terminada",
            "message": f"OK {counts['OK']} · DIFF {counts['DIFF']} · SKIP {counts['SKIP']} · ERROR {counts['ERROR']} "
                       f"(detalle completo en {path}).",
            "detail": detail[:20],
        }
    except Exception as e:
        result = {"ok": False, "title": "No se pudo validar", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


# ----------------------------------------------------------------------------
# Serve the generated HTML files
# ----------------------------------------------------------------------------

@app.get("/archivos/{filename}")
def ver_archivo(filename: str):
    if "/" in filename or "\\" in filename or not filename.lower().endswith(".html"):
        raise HTTPException(400, "nombre de archivo inválido")
    path = OUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "archivo no encontrado")
    return FileResponse(path, media_type="text/html")


# ----------------------------------------------------------------------------
# Startup
# ----------------------------------------------------------------------------

def run(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn
    url = f"http://{host}:{port}/"
    if open_browser:
        Timer(1.0, webbrowser.open, [url]).start()
    print(f"Panel de pbix2html: {url}  (cerrá esta ventana para apagarlo)")
    uvicorn.run(app, host=host, port=port, log_level="warning")
