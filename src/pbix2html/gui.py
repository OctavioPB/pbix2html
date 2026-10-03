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

import atexit
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from threading import Timer
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader

from . import extract as ex
from . import semantic
from .config import settings
from .query import FakeBackend, TeradataBackend, run_report, run_slicers
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

def _default_value(text: str, multi: bool, dtype: str | None, previous: Any) -> Any:
    """A parameter default typed in the edit page. A multi-value parameter is a list (`2026` or `a, b`;
    numbers stay numbers for a numeric slicer field), never the text `[2026]`: the page shows a list as
    `a, b`, and saving what it showed must give the list back."""
    if text == "":
        return None
    if not multi:
        return text
    items = semantic.multi_values(text)
    numeric = dtype == "number" or (isinstance(previous, list) and previous
                                    and all(isinstance(x, (int, float)) for x in previous))
    if numeric:
        try:
            items = [int(x) if float(x).is_integer() else float(x) for x in items]
        except (TypeError, ValueError):
            pass
    return items


def _pbix_files() -> list[Path]:
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.glob("*.pbix"))


def _live_status(api_base: str, report: str, timeout: float = 1.5) -> str:
    """'ok' | 'unreachable' | 'not_found' — best-effort, used only to warn early on a
    `--mode live` convert, not as a health check anyone should rely on. `/healthz`
    alone isn't enough: it succeeds no matter what folder serve.py was started from,
    so it can't catch "the process is up but can't see this report's yaml" — the most
    common actual cause, since metrics/reports/out are all relative paths."""
    try:
        with urllib.request.urlopen(f"{api_base}/healthz", timeout=timeout):
            pass
    except (urllib.error.URLError, OSError, ValueError):
        return "unreachable"
    try:
        with urllib.request.urlopen(f"{api_base}/reports/{urllib.parse.quote(report)}", timeout=timeout):
            return "ok"
    except urllib.error.HTTPError:
        return "not_found"
    except (urllib.error.URLError, OSError, ValueError):
        return "unreachable"


# ----------------------------------------------------------------------------
# Live service (serve.py) control — so "start the live service" is a button, not
# a second terminal window. Only tracks a process THIS panel spawned: if someone
# started serve.py by hand instead, _live_status() above still reports it as
# reachable, but there's no PID here to stop it with (see the "not ours" case
# in report.html) — that's a deliberate limit, not a bug: this panel has no
# business killing a process it didn't start.
# ----------------------------------------------------------------------------

_live_proc: subprocess.Popen | None = None


def _live_owned_and_running() -> bool:
    return _live_proc is not None and _live_proc.poll() is None


def _start_live_process() -> None:
    global _live_proc
    if _live_owned_and_running():
        return
    parsed = urllib.parse.urlparse(settings.api_base)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 8000
    # cwd=Path.cwd() (this panel's own working directory) rather than trusting whoever
    # runs the command — this is exactly what removes the "serve.py running from the
    # wrong folder" failure mode: it always inherits the same cwd the panel itself has.
    _live_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "pbix2html.serve:app", "--host", host, "--port", str(port)],
        cwd=str(Path.cwd()),
    )


def _stop_live_process() -> None:
    global _live_proc
    if _live_proc is None:
        return
    _live_proc.terminate()
    try:
        _live_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _live_proc.kill()
    _live_proc = None


atexit.register(_stop_live_process)


@app.get("/live/status")
def live_status(name: str):
    """JSON, polled by report.html's own JS only once the user picks "Live" mode —
    NOT on every page load: this does a real (short-timeout) network call, and
    blocking every page render on it made the whole panel feel frozen for anyone not
    actively using live mode (see the fix in _page(), which no longer calls this)."""
    return {
        "status": _live_status(settings.api_base, name),
        "owned": _live_owned_and_running(),
        "api_base": settings.api_base,
    }


@app.post("/live/start")
def start_live(request: Request, name: str = Form(...)):
    pbix = _find_pbix(name)
    _start_live_process()
    time.sleep(1.5)  # give uvicorn a moment to bind before the next status check
    status = _live_status(settings.api_base, name)
    if status == "ok":
        result = {"ok": True, "title": "Live service started", "detail": [],
                  "message": f"Running at {settings.api_base} (pid {_live_proc.pid if _live_proc else '?'})."}
    else:
        result = {"ok": False, "title": "Live service didn't come up", "detail": [],
                   "message": "Started the process but it's not answering yet — reopen this page in a "
                              "few seconds, or check for a port conflict (something else already using "
                              f"{settings.api_base})."}
    return _page(request, pbix, result)


@app.post("/live/stop")
def stop_live(request: Request, name: str = Form(...)):
    pbix = _find_pbix(name)
    _stop_live_process()
    result = {"ok": True, "title": "Live service stopped", "detail": [], "message": ""}
    return _page(request, pbix, result)


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
        "api_base": settings.api_base,   # cheap: a string read, no network call — see /live/status for that
        "readiness": semantic.readiness(spec) if spec else None,
        "backups": [{"name": b.name, "when": _backup_when(b.name)} for b in semantic.list_backups(name)[:10]],
        "generated": _generated_files(name),
        "has_extract": (OUT_DIR / ex.safe_name(name) / "model.json").exists(),
        "result": result,
    }
    return templates.TemplateResponse("report.html", ctx)


def _generated_files(name: str) -> list[dict[str, Any]]:
    """Every HTML already produced for this report, newest first. Without this the only
    link to a finished report is the one-shot banner right after converting — navigate
    away and there's no way back to it from the panel at all."""
    if not OUT_DIR.exists():
        return []
    files = [p for p in OUT_DIR.glob(f"{ex.safe_name(name)}*.html") if p.is_file()]
    rows = []
    for p in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True):
        stat = p.stat()
        rows.append({
            "name": p.name,
            "when": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "size": f"{stat.st_size / 1024:,.0f} KB" if stat.st_size >= 1024 else f"{stat.st_size} B",
        })
    return rows


def _backup_when(filename: str) -> str:
    """'Executive_Dashboard.20260929-142530.yaml' → '2026-09-29 14:25' — a timestamp a
    person can match against "the version from before lunch", not a filename to parse."""
    m = re.search(r"\.(\d{8})-(\d{6})\.yaml$", filename)
    if not m:
        return filename
    d, t = m.group(1), m.group(2)
    return f"{d[:4]}-{d[4:6]}-{d[6:]} {t[:2]}:{t[2:4]}"


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
        spec, _ = _load_spec(name)
        rows.append({
            "name": name,
            "has_yaml": yaml_path.exists(),
            "n_html": len(html_files),
            # How far along each report is, right on the list — so nobody has to open a
            # report to find out whether it's nearly done or hasn't been started.
            "readiness": semantic.readiness(spec) if spec else None,
        })
    return templates.TemplateResponse("index.html", {"request": request, "reports": rows})


@app.post("/upload")
def upload_pbix(file: UploadFile):
    filename = Path(file.filename or "").name  # strips any path; just the filename
    if not filename.lower().endswith(".pbix") or not re.fullmatch(r"[\w\-. ]+\.pbix", filename, re.I):
        raise HTTPException(400, "Upload a .pbix file with a simple name (letters, digits, spaces, - or _).")
    REPORTS_DIR.mkdir(exist_ok=True)
    destination = REPORTS_DIR / filename
    # Replacing a .pbix that already has a metrics yaml is allowed (updating a report is
    # a normal thing to do) but it's worth saying so: the SQL in that yaml was written
    # against the old version and may no longer line up.
    replaced = destination.exists() and semantic.yaml_path(destination.stem).exists()
    # Streamed, not file.read(): a real .pbix runs to hundreds of MB and reading one
    # wholly into memory is how the panel falls over on a big report.
    with destination.open("wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)
    suffix = "?replaced=1" if replaced else ""
    return HTMLResponse(
        f'<meta http-equiv="refresh" content="0; url=/reports/{destination.stem}{suffix}">', status_code=303
    )


# ----------------------------------------------------------------------------
# Report detail
# ----------------------------------------------------------------------------

@app.get("/reports/{name}", response_class=HTMLResponse)
def view_report(request: Request, name: str, replaced: int = 0):
    pbix = _find_pbix(name)
    result = None
    if replaced:
        result = {"ok": False, "title": "The .pbix was replaced, and this report already had SQL",
                  "message": f"metrics/{name}.yaml was written against the previous version of this "
                             f"file. Re-run Extract, then check the SQL still matches — visuals that "
                             f"changed in Power BI won't line up on their own.",
                  "detail": []}
    return _page(request, pbix, result)


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
        detail = [f"Model warning: {model['error']}"] if model.get("error") else []

        # Auto-fill the table mapping (step 2b) from each table's own Power Query M
        # source where it's unambiguous (semantic.detect_table_map_from_power_query) —
        # never touches an entity someone already mapped by hand.
        _, new_rels = semantic.sync_relationships(name, model)
        if new_rels:
            detail.append(f"Proposed {len(new_rels)} relationship(s) from a calendar table to the fact "
                          f"tables' date column — saved to metrics/{name}.relationships.json, review them.")
        model = semantic.with_relationship_overrides(name, model)
        _, new_names = semantic.sync_table_map(name, model)
        if new_names:
            detail.append(f"Auto-mapped {len(new_names)} Power BI table(s) to Teradata from their "
                          f"Power Query source — review on the table-mapping page before trusting.")

        result = {
            "ok": True, "title": "Structure extracted",
            "message": f"{len(layout['pages'])} pages, {n_vis} visuals, {n_meas} measures.",
            "detail": detail,
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
        model = semantic.with_relationship_overrides(name, model)
        table_map = _load_table_map(name)
        had_previous = semantic.yaml_path(name).exists()
        path = semantic.write_scaffold(layout, model, overwrite=regenerate, table_map=table_map)
        detail = []
        if table_map:
            detail.append(f"Used the {len(table_map)} Power BI → Teradata table mapping(s) to "
                          f"pre-fill the SQL (step 2b).")
        spec, _ = _load_spec(name)
        if spec:
            r = semantic.readiness(spec)
            detail.append(
                f"{r['ready']} of {r['needs_sql']} visuals have working SQL "
                f"({r['drafted']} written automatically — review those), {r['todo']} still need it "
                f"written by hand. {r['decorative']} more are text/images and need no query."
            )
        if had_previous:
            backups = semantic.list_backups(name)
            if backups:
                detail.append(f"Your previous version was saved as metrics/{semantic.BACKUP_DIR_NAME}/"
                              f"{backups[0].name} — use “Restore previous version” below to get it back.")
        result = {"ok": True, "title": "Template generated", "message": f"Wrote {path}.", "detail": detail}
    except FileExistsError as e:
        result = {"ok": False, "title": "A template already exists", "message": str(e), "detail": []}
    except Exception as e:
        result = {"ok": False, "title": "Couldn't generate the template", "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


@app.post("/reports/{name}/autofill")
async def action_autofill(request: Request, name: str):
    """Fill in the queries and parameters that are still missing, leaving everything
    already written alone.

    Distinct from "Regenerate template", which rebuilds from the .pbix and discards
    hand-written SQL — that used to be the only way to pick up a draft, so improving
    the drafter did nothing for a report someone had already started."""
    pbix = _find_pbix(name)
    try:
        spec, yaml_error = _load_spec(name)
        if spec is None:
            result = {"ok": False, "title": "Generate the template first",
                      "message": yaml_error or "There's no metrics template for this report yet — "
                                               "use “Generate template” above, then come back.",
                      "detail": []}
            return _page(request, pbix, result)

        layout = ex.extract_layout(pbix)
        form = await request.form()
        outcome = semantic.autofill(spec.raw, layout, semantic.with_relationship_overrides(name, ex.extract_model(pbix)),
                                    _load_table_map(name), redraft=form.get("redraft") == "on")
        detail = []
        if outcome["filled"]:
            semantic.backup_yaml(name)          # writing: keep the version before it
            semantic.save_raw(name, outcome["raw"])
            detail.append("Review them on “Edit SQL, parameters & roles” — they're drafts, "
                          "and each one is marked Auto-drafted in its notes.")
        if outcome["already"]:
            detail.append(f"{len(outcome['already'])} visual(s) already had SQL and were left untouched.")
        if outcome["parameters_added"]:
            detail.append(f"Added {len(outcome['parameters_added'])} parameter(s) from slicers: "
                          f"{', '.join(outcome['parameters_added'])}.")
        if outcome.get("slicers_added"):
            detail.append(f"Added {len(outcome['slicers_added'])} slicer entr(y/ies) (their options queries), "
                          "which a live report needs to fill its dropdowns.")
        if outcome["still_todo"]:
            detail.append(f"{len(outcome['still_todo'])} visual(s) still need SQL by hand — usually "
                          f"time intelligence, an unmapped table (step 2b), or a measure too "
                          f"involved to translate safely.")
        result = {
            "ok": True,
            "title": f"Filled in {len(outcome['filled'])} visual(s)" if outcome["filled"]
                     else "Nothing new to fill in",
            "message": ("Wrote the queries it could work out from the report's own measures "
                        "and relationships." if outcome["filled"]
                        else "Every visual either already has SQL or needs it written by hand."),
            "detail": detail,
        }
    except Exception as e:
        result = {"ok": False, "title": "Couldn't auto-detect",
                  "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


@app.post("/reports/{name}/restore")
def action_restore(request: Request, name: str, backup: str = Form(...)):
    """Puts a previous version of metrics/<name>.yaml back. The panel keeps a copy every
    time something overwrites that file, so "Regenerate template" (and a bad edit) stop
    being one-way doors for someone with no git and no text editor."""
    pbix = _find_pbix(name)
    try:
        chosen = semantic.yaml_path(name).parent / semantic.BACKUP_DIR_NAME / Path(backup).name
        semantic.restore_backup(name, chosen)
        result = {"ok": True, "title": "Previous version restored",
                  "message": f"metrics/{name}.yaml is back to the version from {_backup_when(chosen.name)}.",
                  "detail": ["The version you just replaced was itself saved as a backup, "
                             "so this is undoable too."]}
    except (ValueError, OSError) as e:
        result = {"ok": False, "title": "Couldn't restore that version",
                  "message": f"{type(e).__name__}: {e}", "detail": []}
    return _page(request, pbix, result)


# ----------------------------------------------------------------------------
# Step 2c: edit SQL / parameters / roles from the browser — no text editor, no
# opening metrics/<Report>.yaml by hand. Round-trips through the raw yaml dict
# (spec.raw) rather than semantic.scaffold(), so it only ever touches the fields
# this form actually edits and never re-derives anything from the .pbix.
# ----------------------------------------------------------------------------

_KIND_CHOICES = sorted(set(semantic.KIND_MAP.values()) | {"custom", "unsupported"})


def _edit_page(request: Request, name: str, pbix: Path, raw: dict | None = None,
               result: dict[str, Any] | None = None) -> HTMLResponse:
    spec, yaml_error = _load_spec(name)
    return templates.TemplateResponse("edit_metrics.html", {
        "request": request, "name": name, "pbix": str(pbix),
        "raw": raw if raw is not None else (spec.raw if spec else {}),
        "kinds": _KIND_CHOICES,
        "yaml_error": yaml_error if raw is None else None,
        "result": result,
    })


@app.get("/reports/{name}/edit", response_class=HTMLResponse)
def view_edit(request: Request, name: str):
    pbix = _find_pbix(name)
    return _edit_page(request, name, pbix)


@app.post("/reports/{name}/edit")
async def save_edit(request: Request, name: str):
    pbix = _find_pbix(name)
    spec, yaml_error = _load_spec(name)
    if spec is None:
        result = {"ok": False, "title": "Missing the metrics template", "detail": [yaml_error] if yaml_error else [],
                   "message": "Generate the template first (step 2) before editing it."}
        return _page(request, pbix, result)

    form = await request.form()
    raw = dict(spec.raw)
    errors: list[str] = []

    # --- parameters: type/default/label/multi per existing name, plus one new row ---
    parameters: dict[str, dict] = {}
    for pname, p in (raw.get("parameters") or {}).items():
        if form.get(f"param__{pname}__delete") == "on":
            continue
        p = dict(p or {})
        p["type"] = form.get(f"param__{pname}__type") or p.get("type") or "string"
        default = form.get(f"param__{pname}__default", "")
        p["label"] = form.get(f"param__{pname}__label") or p.get("label") or pname
        p["multi"] = form.get(f"param__{pname}__multi") == "on"
        p["default"] = _default_value(default, p["multi"], p.get("dtype"), p.get("default"))
        parameters[pname] = p
    new_pname = (form.get("newparam__name") or "").strip()
    if new_pname:
        default = form.get("newparam__default", "")
        parameters[new_pname] = {
            "type": form.get("newparam__type") or "string",
            "default": _default_value(default, form.get("newparam__multi") == "on", None, None),
            "label": form.get("newparam__label") or new_pname,
            "multi": form.get("newparam__multi") == "on",
        }
    raw["parameters"] = parameters

    # --- roles: proxy_user/where per existing name, plus one new row ---
    roles: dict[str, dict] = {}
    for rname, r in (raw.get("roles") or {}).items():
        if form.get(f"role__{rname}__delete") == "on":
            continue
        r = dict(r or {})
        r["proxy_user"] = form.get(f"role__{rname}__proxy_user") or None
        r["where"] = form.get(f"role__{rname}__where") or None
        roles[rname] = r
    new_rname = (form.get("newrole__name") or "").strip()
    if new_rname:
        # A role name becomes part of the generated HTML's filename (one file per role),
        # so anything with a path separator in it — "Sales/North" by accident, "../.." on
        # purpose — would steer that write out of out/. Names are a business label, so
        # restricting them to letters/digits/space/_/-/. costs nothing real.
        if not re.fullmatch(r"[\w .\-]+", new_rname):
            errors.append(f"role '{new_rname}': use only letters, digits, spaces, '.', '-' or '_'")
        else:
            roles[new_rname] = {
                "proxy_user": form.get("newrole__proxy_user") or None,
                "where": form.get("newrole__where") or None,
            }
    raw["roles"] = roles or {"default": {"proxy_user": None, "where": None}}

    # --- visuals: kind/title/sql/params/reference_sql/tolerance/notes per existing id ---
    visuals: dict[str, dict] = dict(raw.get("visuals") or {})
    for vid in list(visuals.keys()):
        if f"visual__{vid}__kind" not in form:
            continue  # a visual not rendered by this form (shouldn't happen); leave untouched
        v = dict(visuals[vid] or {})
        v["kind"] = form.get(f"visual__{vid}__kind") or v.get("kind") or "unsupported"
        v["title"] = form.get(f"visual__{vid}__title") or None

        sql_in = (form.get(f"visual__{vid}__sql") or "").strip()
        if semantic.is_unwritten_sql(sql_in):
            v["sql"] = sql_in or None
        else:
            try:
                v["sql"] = semantic.validate_read_only_sql(sql_in)
            except ValueError as e:
                errors.append(f"{vid} — sql: {e}")
                v["sql"] = sql_in

        params_in = form.get(f"visual__{vid}__params", "")
        v["params"] = [p.strip() for p in params_in.split(",") if p.strip()]

        ref_sql_in = (form.get(f"visual__{vid}__reference_sql") or "").strip()
        if ref_sql_in:
            try:
                v["reference_sql"] = semantic.validate_read_only_sql(ref_sql_in)
            except ValueError as e:
                errors.append(f"{vid} — reference_sql: {e}")
                v["reference_sql"] = ref_sql_in
        else:
            v["reference_sql"] = None

        tol_in = form.get(f"visual__{vid}__tolerance_rel", "")
        try:
            v["tolerance"] = {"rel": float(tol_in)} if tol_in.strip() else (v.get("tolerance") or {"rel": 1e-6})
        except ValueError:
            v["tolerance"] = v.get("tolerance") or {"rel": 1e-6}

        v["notes"] = form.get(f"visual__{vid}__notes") or None
        visuals[vid] = v
    raw["visuals"] = visuals

    if errors:
        result = {"ok": False, "title": "Not saved", "detail": errors,
                   "message": "Nothing was written — fix the entries listed below and save again. "
                              "(sql/reference_sql must each be a single read-only SELECT.)"}
        return _edit_page(request, name, pbix, raw, result)

    semantic.save_raw(name, raw)
    result = {"ok": True, "title": "Metrics saved", "detail": [],
              "message": f"Wrote metrics/{name}.yaml."}
    return _page(request, pbix, result)


# ----------------------------------------------------------------------------
# Step 2b: Power BI table → Teradata source query
# ----------------------------------------------------------------------------

def _table_map_path(name: str) -> Path:
    return semantic.table_map_path(name)


def _read_table_map(name: str) -> tuple[dict[str, str], str | None]:
    return semantic.read_table_map(name)


def _load_table_map(name: str) -> dict[str, str]:
    return _read_table_map(name)[0]


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


def _load_model(name: str) -> dict:
    path = OUT_DIR / ex.safe_name(name) / "model.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _table_map_page(request: Request, name: str, pbix: Path, result: dict[str, Any] | None = None,
                     pending: dict[str, str] | None = None) -> HTMLResponse:
    saved, problem = _read_table_map(name)
    if problem and result is None:
        result = {"ok": False, "title": "There's a problem with the saved mapping file",
                  "message": problem, "detail": []}
    return templates.TemplateResponse("table_map.html", {
        "request": request, "name": name, "pbix": str(pbix),
        "entities": _pbi_entities(name),
        "mapping": pending if pending is not None else saved,
        # Re-derived fresh from model.json (not stored) so the "detected automatically"
        # badge always reflects the current .pbix, and disappears the moment someone
        # edits a value away from what detection would produce — see table_map.html.
        "detected": semantic.detect_table_map_from_power_query(_load_model(name)),
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


# ----------------------------------------------------------------------------
# Step 2c: custom theme override (metrics/<Report>.theme.json)
# ----------------------------------------------------------------------------

def _load_theme_raw(name: str) -> dict:
    """Unvalidated, for prefilling the edit form: if a hand-edited file has gone bad,
    the person should still see what's there to fix it, not just an empty form."""
    path = semantic.theme_path(name)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _theme_page(request: Request, name: str, pbix: Path, result: dict[str, Any] | None = None,
                 pending: dict[str, Any] | None = None) -> HTMLResponse:
    theme = pending if pending is not None else _load_theme_raw(name)
    return templates.TemplateResponse("theme.html", {
        "request": request, "name": name, "pbix": str(pbix),
        "theme": theme, "has_override": semantic.theme_path(name).exists(), "result": result,
    })


@app.get("/reports/{name}/theme", response_class=HTMLResponse)
def view_theme(request: Request, name: str):
    pbix = _find_pbix(name)
    return _theme_page(request, name, pbix)


@app.post("/reports/{name}/theme")
async def save_theme(request: Request, name: str):
    pbix = _find_pbix(name)
    form = await request.form()

    pasted = (form.get("pasted_json") or "").strip()
    candidate: dict[str, Any] = {}
    try:
        if pasted:
            candidate = json.loads(pasted)
            if not isinstance(candidate, dict):
                raise ValueError("pasted theme JSON must be an object")
        else:
            colors_raw = (form.get("dataColors") or "").strip()
            if colors_raw:
                candidate["dataColors"] = [c.strip() for c in colors_raw.split(",") if c.strip()]
            for key in ("background", "foreground", "foregroundNeutralSecondary", "backgroundNeutral"):
                val = (form.get(key) or "").strip()
                if val:
                    candidate[key] = val
            font = (form.get("fontFamily") or "").strip()
            if font:
                candidate["fontFamily"] = font
        validated = semantic.validate_theme_override(candidate)
    except (ValueError, json.JSONDecodeError) as e:
        result = {"ok": False, "title": "Theme not saved", "detail": [str(e)],
                  "message": "Fix the fields below and save again."}
        return _theme_page(request, name, pbix, result, pending=candidate)

    path = semantic.theme_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"ok": True, "title": "Theme saved",
              "message": f"metrics/{name}.theme.json now overrides the report's extracted theme "
                         "on every convert (snapshot, live, and hah) from here on.", "detail": []}
    return _page(request, pbix, result)


@app.post("/reports/{name}/theme/reset")
async def reset_theme(request: Request, name: str):
    pbix = _find_pbix(name)
    path = semantic.theme_path(name)
    if path.exists():
        path.unlink()
    result = {"ok": True, "title": "Theme reset",
              "message": "Removed the override — conversions go back to the report's extracted theme.",
              "detail": []}
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
        layout = semantic.apply_theme_override(layout, semantic.load_theme_override(name))
        values = semantic.resolve_params(spec, overrides)

        role = role_name or None
        proxy_user = None
        if role:
            r = spec.roles.get(role)
            if r is None:
                raise ValueError(f"role '{role}' isn't defined in metrics/{name}.yaml")
            proxy_user = r.get("proxy_user")

        data = slicer_data = None
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
            slicer_data = run_slicers(spec, backend, proxy_user=proxy_user)

        html = render_html(layout, spec, values, data, mode=mode, role=role, hah_base=hah_base,
                           slicer_data=slicer_data)
        # Both halves go through safe_name(): `role` is a yaml key, and a role called
        # "../../x" (or just "Sales/North", which is a plausible thing to type) would
        # otherwise steer this write outside out/ entirely. save_edit() rejects such
        # names at the door now, but a hand-edited yaml never went through that.
        out_name = ex.safe_name(name) + (f".{ex.safe_name(role)}" if role else "") + ".html"
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / out_name).write_text(html, encoding="utf-8")

        n_err = sum(1 for d in (data or {}).values() if d.get("error")) if data else None
        detail = []
        from . import verify
        check, _res = verify.verify_after_convert(OUT_DIR / out_name, mode)
        detail.extend(check)
        if n_err:
            detail.append(f"{n_err} visual(s) had an error fetching data (shown inside the HTML).")
        if mode == "hah":
            detail.append(
                f"HTML generated for HAH ({hah_env}, {hah_base}); upload it with the "
                f"teradata-report skill's create_report tool. The SQL endpoint is worked out from "
                f"the URL HAH serves it from, so this environment choice only matters as a "
                f"fallback, and the chart library is embedded in the file because HAH does not "
                f"serve ECharts — which makes it about 1 MB bigger (ADR-004)."
            )
        if mode == "live":
            live_status = _live_status(settings.api_base, name)
            if live_status == "ok":
                detail.append(f"Checked {settings.api_base} just now — serve.py can see this report.")
            elif live_status == "not_found":
                detail.append(
                    f"⚠ {settings.api_base} is up, but can't find this report's yaml — it's almost "
                    f"certainly running from the wrong folder. Stop it and restart from this "
                    f"project's root (the same folder metrics/ and reports/ are in): "
                    f"python -m uvicorn pbix2html.serve:app"
                )
            else:
                detail.append(
                    f"⚠ Couldn't reach {settings.api_base} just now. This report will show "
                    f"\"Failed to fetch\" on every visual until that's running — start it with: "
                    f"python -m uvicorn pbix2html.serve:app (leave that terminal open, run it from "
                    f"this project's root), then reload the report."
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

@app.get("/reports/{name}/model.{fmt}")
def download_model(name: str, fmt: str):
    """The extracted data model, both ways: `model.json` is the machine-readable copy
    (what extract wrote), `model.md` is the same thing rendered for a person — the DAX
    behind each measure, the relationships, the RLS rules, each table's Power Query
    source. Neither leaves the machine unless someone saves it."""
    _find_pbix(name)
    if fmt not in ("json", "md"):
        raise HTTPException(404, "use model.json or model.md")
    path = OUT_DIR / ex.safe_name(name) / "model.json"
    if not path.exists():
        raise HTTPException(404, f"Run Extract on '{name}' first — there's no model.json yet.")
    if fmt == "json":
        return FileResponse(path, media_type="application/json", filename=f"{name}.model.json")
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise HTTPException(500, f"out/{ex.safe_name(name)}/model.json isn't readable JSON: {e}")
    return Response(ex.model_summary_markdown(model, name), media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{name}.model.md"'})


@app.get("/reports/{name}/mapping.md")
def download_mapping_report(name: str):
    """Which visuals could/couldn't be drafted and why, filters not applied, drill-through
    and hidden pages — the same report `pbix2html mapping` writes to a file, generated
    on demand from what's already on disk (layout.json, model.json, the saved table map)
    so there's no separate "run mapping" step to remember in the panel."""
    _find_pbix(name)
    rdir = OUT_DIR / ex.safe_name(name)
    layout_path, model_path = rdir / "layout.json", rdir / "model.json"
    if not layout_path.exists():
        raise HTTPException(404, f"Run Extract on '{name}' first — there's no layout.json yet.")
    try:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise HTTPException(500, f"out/{ex.safe_name(name)}/layout.json isn't readable JSON: {e}")
    model = _load_model(name) if model_path.exists() else {}
    table_map = _load_table_map(name)
    rep = semantic.mapping_report(layout, model, table_map)
    return Response(semantic.render_mapping_report(rep), media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{name}.mapping_report.md"'})


@app.get("/reports/{name}/layout.json")
def download_layout(name: str):
    """The extracted report structure: pages, visuals, positions, fields, theme."""
    _find_pbix(name)
    path = OUT_DIR / ex.safe_name(name) / "layout.json"
    if not path.exists():
        raise HTTPException(404, f"Run Extract on '{name}' first — there's no layout.json yet.")
    return FileResponse(path, media_type="application/json", filename=f"{name}.layout.json")


@app.get("/files/{filename}")
def serve_file(filename: str, download: int = 0):
    """Serves a generated report. `?download=1` sends it as an attachment instead of
    rendering it in the tab — that's the copy someone emails or drops in a shared
    folder, which is how these reports actually reach their readers."""
    if "/" in filename or "\\" in filename or not filename.lower().endswith(".html"):
        raise HTTPException(400, "invalid file name")
    path = OUT_DIR / filename
    if not path.exists():
        raise HTTPException(404, "file not found")
    if download:
        # filename= is what sets Content-Disposition: attachment.
        return FileResponse(path, media_type="text/html", filename=filename)
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
