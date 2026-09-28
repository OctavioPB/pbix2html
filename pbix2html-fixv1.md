# pbix2html — Fix Log v1 & Design Specs

**Date:** 2026-09-28  
**Scope:** All fixes applied to the repo from initial broken state to working panel + PBIR extraction + scaffold generation, plus two design additions (SQL mapping UI and teradata-report skill compatibility).  
**Target audience:** Developer maintaining the repo AND AI assistant reproducing or continuing this work.

---

## Table of Contents

1. [Environment & dependency fixes](#1-environment--dependency-fixes)
2. [Port conflict fix for Abrir_Panel.bat](#2-port-conflict-fix-for-abrir_panbat)
3. [Starlette/Jinja2 Python 3.13 incompatibility fix](#3-starlettejinja2-python-313-incompatibility-fix)
4. [Missing gui_templates — index.html and report.html](#4-missing-gui_templates--indexhtml-and-reporthtmll)
5. [Missing report.html.j2 render template](#5-missing-reporthtmlj2-render-template)
6. [PBIR format support in extract.py](#6-pbir-format-support-in-extractpy)
7. [semantic.py compatibility with PBIR layout shape](#7-semanticpy-compatibility-with-pbir-layout-shape)
8. [LDAP authentication for Teradata](#8-ldap-authentication-for-teradata)
9. [Design addition: Power BI table → SQL mapping UI in the panel](#9-design-addition-power-bi-table--sql-mapping-ui-in-the-panel)
10. [Design addition: teradata-report skill compatibility](#10-design-addition-teradata-report-skill-compatibility)

---

## 1. Environment & dependency fixes

### Problem
`python -m pip show jinja2` showed Jinja2 3.1.6 (current), but `pip install --upgrade jinja2` had no effect because the app runs under the Microsoft Store Python 3.13, which uses a user-local package path.

### Fix
Always use `python -m pip` (not bare `pip`) to ensure the correct Python environment is targeted:

```cmd
python -m pip install --upgrade fastapi starlette jinja2
```

### Notes
- The Microsoft Store Python 3.13 installs packages to:  
  `C:\Users\<user>\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_*\LocalCache\local-packages\Python313\site-packages`
- Bare `pip` may point to a different Python installation entirely.

---

## 2. Port conflict fix for Abrir_Panel.bat

### Problem
Running `Abrir_Panel.bat` a second time without closing the first instance causes:
```
ERROR: [Errno 10048] Only one usage of each socket address is normally permitted
```

### Fix
Add a kill-before-launch step to `Abrir_Panel.bat`. In the `.bat` file, before the `uvicorn` launch line, add:

```bat
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 2^>nul') do taskkill /PID %%a /F 2>nul
```

Note: inside `.bat` files use `%%a` (double `%`). In interactive CMD use single `%a`.

### Manual fix (one-time, CMD)
```cmd
for /f "tokens=5" %a in ('netstat -ano ^| findstr :8765') do taskkill /PID %a /F
```

---

## 3. Starlette/Jinja2 Python 3.13 incompatibility fix

### Problem
Every request to the panel produced:
```
TypeError: unhashable type: 'dict'
  File "jinja2/utils.py", line 515, in __getitem__
    rv = self._mapping[key]
```

### Root cause
Starlette's `Jinja2Templates` wrapper passes `env.globals` (a dict) as part of the Jinja2 LRU cache key. Python 3.13 tightened hashability rules, breaking this. The fix is to bypass `Jinja2Templates` entirely and call Jinja2 directly.

### Fix — gui.py

**Replace the import:**
```python
# OLD
from fastapi.templating import Jinja2Templates

# NEW
from jinja2 import Environment, FileSystemLoader
```

**Replace the templates initialization (around line 36):**
```python
# OLD
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# NEW
_jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)

class _Templates:
    def TemplateResponse(self, name: str, context: dict):
        from fastapi.responses import HTMLResponse
        t = _jinja_env.get_template(name)
        return HTMLResponse(t.render(context))

templates = _Templates()
```

### Also verify TEMPLATES_DIR points to the right folder
```python
TEMPLATES_DIR = Path(__file__).parent / "gui_templates"
```
This must match the folder where `index.html` and `report.html` live (see section 4).

---

## 4. Missing gui_templates — index.html and report.html

### Problem
`gui_templates/` folder existed but was empty. Every panel request crashed with:
```
jinja2.exceptions.TemplateNotFound: 'index.html'
```

### Fix
Create the following two files in `src/pbix2html/gui_templates/`.

---

### File: `src/pbix2html/gui_templates/index.html`

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>pbix2html — Panel</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; background: #f5f6f8; color: #1a1a2e; min-height: 100vh; }
    header { background: #1a1a2e; color: #fff; padding: 18px 32px; display: flex; align-items: baseline; gap: 12px; }
    header h1 { font-size: 18px; font-weight: 600; letter-spacing: -0.3px; }
    header span { font-size: 12px; color: #8888aa; }
    main { max-width: 860px; margin: 32px auto; padding: 0 24px; }
    .section-title { font-size: 11px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #666; margin-bottom: 12px; }
    .report-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 40px; }
    .report-card { background: #fff; border: 1px solid #e4e6ea; border-radius: 8px; padding: 14px 18px; display: flex; align-items: center; justify-content: space-between; transition: border-color .15s; }
    .report-card:hover { border-color: #5b6cff; }
    .report-name { font-weight: 600; font-size: 14px; }
    .report-meta { font-size: 12px; color: #888; margin-top: 2px; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
    .badge-ok { background: #e6f9f0; color: #1a7a45; }
    .badge-warn { background: #fff4e0; color: #a05c00; }
    .btn { display: inline-block; padding: 7px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; text-decoration: none; cursor: pointer; border: none; }
    .btn-primary { background: #5b6cff; color: #fff; }
    .btn-primary:hover { background: #4a5ae8; }
    .empty { background: #fff; border: 1px dashed #cdd0d8; border-radius: 8px; padding: 40px; text-align: center; color: #888; }
    .empty p { margin-bottom: 16px; }
    .upload-zone { background: #fff; border: 1px solid #e4e6ea; border-radius: 8px; padding: 24px; }
    .upload-zone h2 { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
    .upload-zone p { font-size: 12px; color: #888; margin-bottom: 16px; }
    .upload-row { display: flex; gap: 10px; align-items: center; }
    input[type="file"] { flex: 1; font-size: 13px; padding: 6px 10px; border: 1px solid #cdd0d8; border-radius: 6px; background: #fafbfc; }
  </style>
</head>
<body>
  <header>
    <h1>pbix2html</h1>
    <span>Panel local · solo vos podés verlo</span>
  </header>
  <main>
    <p class="section-title">Reportes disponibles</p>
    {% if reportes %}
      <div class="report-list">
        {% for r in reportes %}
        <div class="report-card">
          <div>
            <div class="report-name">{{ r.name }}</div>
            <div class="report-meta">
              {% if r.has_yaml %}<span class="badge badge-ok">yaml ✓</span>{% else %}<span class="badge badge-warn">sin yaml</span>{% endif %}
              &nbsp;{% if r.n_html == 0 %}sin HTML generado{% elif r.n_html == 1 %}1 HTML generado{% else %}{{ r.n_html }} HTML generados{% endif %}
            </div>
          </div>
          <a href="/reportes/{{ r.name }}" class="btn btn-primary">Abrir →</a>
        </div>
        {% endfor %}
      </div>
    {% else %}
      <div class="empty">
        <p>No hay ningún <code>.pbix</code> en la carpeta <code>reportes/</code>.<br>Subí uno con el formulario de abajo o copialo a mano.</p>
      </div>
    {% endif %}
    <p class="section-title">Subir un reporte nuevo</p>
    <div class="upload-zone">
      <h2>Agregar .pbix</h2>
      <p>El archivo se copia a <code>reportes/</code>. Nombre simple: letras, números, guiones.</p>
      <form action="/subir" method="post" enctype="multipart/form-data">
        <div class="upload-row">
          <input type="file" name="archivo" accept=".pbix" required>
          <button type="submit" class="btn btn-primary">Subir</button>
        </div>
      </form>
    </div>
  </main>
</body>
</html>
```

---

### File: `src/pbix2html/gui_templates/report.html`

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ name }} — pbix2html</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; background: #f5f6f8; color: #1a1a2e; min-height: 100vh; }
    header { background: #1a1a2e; color: #fff; padding: 18px 32px; display: flex; align-items: baseline; gap: 16px; }
    header a { color: #8888aa; text-decoration: none; font-size: 13px; }
    header a:hover { color: #fff; }
    header h1 { font-size: 18px; font-weight: 600; }
    main { max-width: 860px; margin: 32px auto; padding: 0 24px; display: flex; flex-direction: column; gap: 20px; }
    .card { background: #fff; border: 1px solid #e4e6ea; border-radius: 8px; padding: 20px 24px; }
    .card h2 { font-size: 14px; font-weight: 600; margin-bottom: 4px; }
    .card .desc { font-size: 12px; color: #888; margin-bottom: 16px; }
    .step-label { font-size: 11px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: #5b6cff; margin-bottom: 6px; }
    .btn { display: inline-block; padding: 7px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: none; }
    .btn-primary { background: #5b6cff; color: #fff; }
    .btn-primary:hover { background: #4a5ae8; }
    .btn-ghost { background: transparent; color: #5b6cff; border: 1px solid #5b6cff; }
    .result { border-radius: 8px; padding: 16px 20px; border-left: 4px solid; }
    .result.ok { background: #f0fbf5; border-color: #1a7a45; }
    .result.error { background: #fff5f5; border-color: #c0392b; }
    .result-title { font-weight: 600; margin-bottom: 4px; }
    .result-msg { font-size: 13px; color: #444; }
    .result-detail { margin-top: 8px; font-size: 12px; color: #666; }
    .result-detail li { margin-left: 16px; list-style: disc; }
    .result-link { margin-top: 10px; }
    .result-link a { color: #5b6cff; font-size: 13px; }
    label { font-size: 13px; font-weight: 500; display: block; margin-bottom: 4px; }
    select, input[type="text"] { width: 100%; padding: 7px 10px; border: 1px solid #cdd0d8; border-radius: 6px; font-size: 13px; background: #fafbfc; margin-bottom: 12px; }
    .form-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
    .form-row > div { flex: 1; min-width: 160px; }
    .checkbox-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; font-size: 13px; }
    .params-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 12px; }
    .tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
    .tag-ok { background: #e6f9f0; color: #1a7a45; }
    .tag-warn { background: #fff4e0; color: #a05c00; }
    .yaml-error { background: #fff5f5; border: 1px solid #ffcdd2; border-radius: 6px; padding: 10px 14px; font-size: 12px; color: #c0392b; margin-bottom: 12px; }
  </style>
</head>
<body>
  <header>
    <a href="/">← Panel</a>
    <h1>{{ name }}.pbix</h1>
    {% if has_yaml %}<span class="tag tag-ok">yaml ✓</span>{% else %}<span class="tag tag-warn">sin yaml</span>{% endif %}
  </header>
  <main>
    {% if result %}
    <div class="result {{ 'ok' if result.ok else 'error' }}">
      <div class="result-title">{{ result.title }}</div>
      <div class="result-msg">{{ result.message }}</div>
      {% if result.detail %}<div class="result-detail"><ul>{% for d in result.detail %}<li>{{ d }}</li>{% endfor %}</ul></div>{% endif %}
      {% if result.html_link %}<div class="result-link"><a href="{{ result.html_link }}" target="_blank">Abrir HTML generado →</a></div>{% endif %}
    </div>
    {% endif %}

    <div class="card">
      <p class="step-label">Paso 1</p>
      <h2>Extraer estructura</h2>
      <p class="desc">Lee el .pbix y genera <code>out/{{ name }}/layout.json</code> y <code>model.json</code>.</p>
      <form action="/reportes/{{ name }}/extraer" method="post">
        <button type="submit" class="btn btn-primary">Extraer estructura</button>
      </form>
    </div>

    <div class="card">
      <p class="step-label">Paso 2</p>
      <h2>Generar plantilla de métricas</h2>
      <p class="desc">Crea <code>metrics/{{ name }}.yaml</code> con un placeholder <code>sql: TODO</code> por visual.</p>
      {% if yaml_error %}<div class="yaml-error">⚠ {{ yaml_error }}</div>{% endif %}
      <form action="/reportes/{{ name }}/plantilla" method="post" style="display:flex; gap:10px; flex-wrap:wrap;">
        <button type="submit" class="btn btn-primary" {% if has_yaml and not yaml_error %}disabled{% endif %}>Generar plantilla</button>
        {% if has_yaml %}
        <label style="display:flex; align-items:center; gap:6px; font-weight:400; margin-bottom:0;">
          <input type="checkbox" name="regenerar" value="true"> Regenerar (borra el SQL ya escrito)
        </label>
        {% endif %}
      </form>
    </div>

    <div class="card">
      <p class="step-label">Paso 3</p>
      <h2>Convertir a HTML</h2>
      <p class="desc">Ejecuta las queries y genera el HTML. Elegí modo, rol y parámetros.</p>
      <form action="/reportes/{{ name }}/convertir" method="post">
        <div class="form-row">
          <div>
            <label for="modo">Modo</label>
            <select name="modo" id="modo">
              <option value="snapshot">📸 Snapshot</option>
              <option value="live">🔴 Live</option>
            </select>
          </div>
          {% if roles %}
          <div>
            <label for="rol">Rol</label>
            <select name="rol" id="rol">
              <option value="">— sin rol —</option>
              {% for r in roles %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
            </select>
          </div>
          {% endif %}
        </div>
        {% if spec and spec.parameters %}
        <p style="font-size:12px; color:#888; margin-bottom:8px;">Parámetros</p>
        <div class="params-grid">
          {% for pname, pdef in spec.parameters.items() %}
          <div>
            <label for="param__{{ pname }}">{{ pdef.label if pdef.label else pname }}</label>
            <input type="text" name="param__{{ pname }}" id="param__{{ pname }}" placeholder="{{ pdef.default if pdef.default else '' }}" value="{{ pdef.default if pdef.default else '' }}">
          </div>
          {% endfor %}
        </div>
        {% endif %}
        {% if demo_disponible %}
        <div class="checkbox-row">
          <input type="checkbox" name="usar_demo" id="usar_demo" {% if not has_teradata %}checked{% endif %}>
          <label for="usar_demo" style="margin-bottom:0; font-weight:400;">Usar datos de prueba {% if not has_teradata %}<span class="tag tag-warn">sin Teradata</span>{% endif %}</label>
        </div>
        {% endif %}
        <button type="submit" class="btn btn-primary" {% if not has_yaml %}disabled{% endif %}>Convertir</button>
      </form>
    </div>

    <div class="card">
      <p class="step-label">Paso 4</p>
      <h2>Validar contra Power BI</h2>
      <p class="desc">Compara números del HTML con la referencia. Requiere Teradata.</p>
      <form action="/reportes/{{ name }}/validar" method="post">
        <button type="submit" class="btn {% if has_teradata %}btn-primary{% else %}btn-ghost{% endif %}" {% if not has_yaml or not has_teradata %}disabled{% endif %}>Validar</button>
        {% if not has_teradata %}<span style="font-size:12px; color:#888; margin-left:10px;">Teradata no configurado en .env</span>{% endif %}
      </form>
    </div>
  </main>
</body>
</html>
```

---

## 5. Missing report.html.j2 render template

### Problem
`render.py` calls `env.get_template("report.html.j2")` but the `src/pbix2html/templates/` folder was empty.

### Fix
Create `src/pbix2html/templates/report.html.j2`. The template receives these variables from `render.py`:

| Variable | Type | Description |
|---|---|---|
| `spec` | dict | `{report, theme, pages, parameters}` |
| `theme` | dict | `{background, foreground, muted, border, font_family, data_colors[]}` |
| `mode` | str | `"snapshot"` or `"live"` |
| `role` | str or None | Active RLS role |
| `generated_at` | str | Timestamp string |
| `echarts_cdn` | str | URL to ECharts JS (from settings) |
| `api_base` | str | Base URL for live mode API calls |
| `spec_json` | str | JSON-serialized spec for JS consumption |
| `data_json` | str | JSON-serialized data dict `{visual_id: {rows: [...], error: ...}}` |

Each page in `spec.pages` contains visuals with:
- `id`, `kind`, `type`, `title`
- `left`, `top`, `w`, `h` — percentage positions relative to page dimensions
- `z` — z-index
- `stacked`, `area`, `inner_radius` — chart variant flags
- `text` — for textbox visuals

The full template content is tracked in the repo. Key design points:
- Uses CSS custom properties for theming (`--bg`, `--fg`, `--c0`…`--c7`)
- ECharts for bar, line, pie, waterfall, funnel, gauge, scatter, treemap
- Native HTML table for `table` and `matrix` kinds
- Card kind renders first column value of first row as large number
- Visuals positioned absolutely within percentage-based page canvas
- `echartsInstances` map keyed by `pageId` for resize on tab switch
- Page tabs at top; active page shown, others `display:none`

---

## 6. PBIR format support in extract.py

### Problem
All real `.pbix` files were in **PBIR / Enhanced Report Format** (Power BI 2024+). The extractor only handled the classic `Report/Layout` format, throwing:
```
ValueError: No se encontró Report/Layout (¿es formato PBIR/PBIP? ver README)
```

### PBIR vs Classic format

| Classic .pbix | PBIR .pbix |
|---|---|
| `Report/Layout` — single UTF-16LE JSON blob | `Report/definition/report.json` — present as marker |
| All pages in one JSON | `Report/definition/pages/pages.json` → page order |
| All visuals in one JSON | `Report/definition/pages/<pageId>/page.json` per page |
| | `Report/definition/pages/<pageId>/visuals/<visualId>/visual.json` per visual |

### Detection
A PBIR file has `Report/definition/report.json` in its zip entries. A classic file has `Report/Layout`.

### Fix — add to extract.py before `def extract_layout()`

Add these helper functions:

```python
def _read_zip_json(zf, path):
    try:
        with zf.open(path) as f:
            return json.loads(f.read().decode("utf-8"))
    except KeyError:
        return {}

def _pbir_visual_kind(visual_type):
    mapping = {
        "card": "card", "cardVisual": "card", "multiRowCard": "card",
        "clusteredBarChart": "bar", "clusteredColumnChart": "bar",
        "barChart": "bar", "columnChart": "bar",
        "lineChart": "line", "areaChart": "line",
        "lineStackedColumnComboChart": "combo", "lineClusteredColumnComboChart": "combo",
        "pieChart": "pie", "donutChart": "pie",
        "tableEx": "table", "pivotTable": "matrix",
        "slicer": "slicer", "textbox": "textbox", "image": "image",
        "shape": "shape", "actionButton": "button",
        "waterfallChart": "waterfall", "funnel": "funnel",
        "gauge": "gauge", "scatterChart": "scatter",
        "treemap": "treemap", "map": "map", "filledMap": "map",
        "ribbonChart": "bar", "kpi": "kpi",
    }
    return mapping.get(visual_type, visual_type)

def _pbir_fields(query_state):
    fields = []
    for role_name, role_data in query_state.items():
        for proj in role_data.get("projections", []):
            field = proj.get("field", {})
            query_ref = proj.get("queryRef", "")
            col = (field.get("Column") or field.get("Measure")
                   or field.get("Aggregation", {}).get("Expression", {}).get("Column"))
            if col:
                entity = col.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                prop = col.get("Property", "")
            else:
                entity, prop = "", ""
            fields.append({"role": role_name, "entity": entity,
                           "property": prop, "queryRef": query_ref})
    return fields

def _pbir_title(container_objects):
    try:
        val = (container_objects["title"][0]["properties"]
               .get("text", {}).get("expr", {}).get("Literal", {}).get("Value", ""))
        return val.strip("'\"")
    except (KeyError, IndexError, TypeError):
        return ""

def _extract_layout_pbir(zf):
    pages_meta = _read_zip_json(zf, "Report/definition/pages/pages.json")
    page_order = pages_meta.get("pageOrder", [])
    if not page_order:
        page_order = sorted({
            e.split("/")[3]
            for e in zf.namelist()
            if e.startswith("Report/definition/pages/") and e.count("/") >= 4
               and not e.endswith("pages.json")
        })
    theme = {}
    theme_entry = next(
        (e for e in zf.namelist()
         if e.startswith("Report/StaticResources/SharedResources/BaseThemes/")
            and e.endswith(".json")), None)
    if theme_entry:
        theme = _read_zip_json(zf, theme_entry)
    pages = []
    for page_id in page_order:
        page_data = _read_zip_json(zf, f"Report/definition/pages/{page_id}/page.json")
        if not page_data:
            continue
        visual_ids = sorted({
            e.split("/")[5]
            for e in zf.namelist()
            if e.startswith(f"Report/definition/pages/{page_id}/visuals/")
               and e.endswith("visual.json")
        })
        visuals = []
        for vid in visual_ids:
            vdata = _read_zip_json(zf, f"Report/definition/pages/{page_id}/visuals/{vid}/visual.json")
            if not vdata:
                continue
            pos = vdata.get("position", {})
            vis = vdata.get("visual", {})
            qs  = vis.get("query", {}).get("queryState", {})
            # Reshape to match classic layout shape expected by semantic.py
            fields = _pbir_fields(qs)
            projections = {}
            for f in fields:
                projections.setdefault(f["role"], []).append(f["queryRef"])
            slicer_fields = [f["queryRef"] for f in fields]
            visuals.append({
                "id":          vid,
                "type":        _pbir_visual_kind(vis.get("visualType", "unknown")),
                "kind":        _pbir_visual_kind(vis.get("visualType", "unknown")),
                "title":       _pbir_title(vdata.get("visualContainerObjects", {})),
                "x": pos.get("x", 0), "y": pos.get("y", 0), "z": pos.get("z", 0),
                "width": pos.get("width", 0), "height": pos.get("height", 0),
                "fields":      slicer_fields,
                "projections": projections,
                "filters":     [],
                "is_group":    False,
                "is_custom":   False,
                "raw":         vdata,
            })
        visuals.sort(key=lambda v: (v["z"], v["y"], v["x"]))
        pages.append({
            "name":         page_data.get("displayName", page_id),
            "display_name": page_data.get("displayName", page_id),
            "id":           page_id,
            "width":        page_data.get("width", 1280),
            "height":       page_data.get("height", 720),
            "visuals":      visuals,
            "filters":      [],
            "background":   page_data.get("objects", {}).get("background", []),
        })
    names = set(zf.namelist())
    return {
        "report": "",  # filled in by extract_layout caller
        "source": "",
        "layout_version": None,
        "has_embedded_datamodel": any(n.endswith("DataModel") for n in names),
        "theme": theme,
        "custom_visual_packages": [],
        "pages": pages,
        "format": "pbir",
    }
```

### Fix — update `extract_layout()` to detect PBIR

Find the `if layout_member is None:` block and replace:
```python
# OLD
if layout_member is None:
    raise ValueError("No se encontró Report/Layout (¿es formato PBIR/PBIP? ver README)")

# NEW
if layout_member is None:
    if "Report/definition/report.json" in names:
        result = _extract_layout_pbir(z)
        result["report"] = pbix.stem
        result["source"] = str(pbix)
        return result
    raise ValueError("Formato no reconocido: ni Report/Layout ni Report/definition/report.json encontrado.")
```

### Fix — update `extract_model()` for PBIR

PBIR files have a binary `DataModel` that cannot be parsed without TOM. Add before the classic branch:

```python
def extract_model(pbix: Path) -> dict:
    with zipfile.ZipFile(pbix) as zf:
        names = {e.filename for e in zf.infolist()}
        if "Report/definition/report.json" in names:
            connections = {}
            if "Connections" in names:
                try:
                    with zf.open("Connections") as f:
                        connections = json.loads(f.read().decode("utf-8"))
                except Exception:
                    pass
            return {
                "measures": [], "tables": [], "connections": connections, "rls": [],
                "error": (
                    "Formato PBIR: el DataModel es binario. "
                    "Las queries deben escribirse a mano en el yaml."
                ),
            }
    # ... existing classic extract_model logic below ...
```

---

## 7. semantic.py compatibility with PBIR layout shape

### Problem
`write_scaffold()` crashed with `KeyError: 'type'` because `semantic.py` expected:
- `v["type"]` — classic field name for visual type
- `page["display_name"]` — classic field name for page display name
- `page["filters"]` — list of page-level filters
- `v["projections"]` — dict of `{role: [queryRef, ...]}`
- `v["is_group"]`, `v["is_custom"]` — classic boolean flags

The PBIR extractor now outputs all of these (see section 6 — the reshape block), so **no changes are needed in semantic.py for the scaffold step**.

### Fix — null guards in `load()` and `resolve_params()`

The generated yaml has visual entries and parameter entries that are `null` in YAML (no sub-keys). `yaml.safe_load` returns `None` for these. Add null guards:

**In `load()`, after `for vid, v in (raw.get("visuals") or {}).items():`:**
```python
if v is None: v = {}
```

**In `resolve_params()`, after `for name, p in spec.parameters.items():`:**
```python
if p is None: p = {}
```

---

## 8. LDAP authentication for Teradata

### Problem
`config.py` defaulted `teradata_logmech` to `"TD2"`. Corporate Teradata (`tdprd.td.teradata.com`) requires LDAP. TD2 login produced Error 8017 (invalid credentials).

### Fix

**Add to `.env`:**
```
TERADATA_LOGMECH=LDAP
```

`config.py` already reads this:
```python
teradata_logmech: str = os.getenv("TERADATA_LOGMECH", "TD2")
```

`query.py` already passes it:
```python
kwargs = dict(host=settings.teradata_host, user=settings.teradata_user,
              password=settings.teradata_password, logmech=settings.teradata_logmech, ...)
```

**No code changes needed** — only the `.env` entry. After adding it, restart the panel (settings are loaded at module import time).

### Verify connection
```python
python -c "
import teradatasql, os
from dotenv import load_dotenv
load_dotenv()
con = teradatasql.connect(host=os.getenv('TERADATA_HOST'), user=os.getenv('TERADATA_USER'),
                          password=os.getenv('TERADATA_PASSWORD'), logmech='LDAP')
cur = con.cursor()
cur.execute('SELECT CURRENT_DATE')
print('OK:', cur.fetchone())
con.close()
"
```

---

## 9. Design addition: Power BI table → SQL mapping UI in the panel

### Problem
All visuals in the generated yaml have `sql: TODO`. The DAX expressions in .pbix files reference **Power BI table names** (e.g. `Compute Engine Mnthly`, `Calendar`, `ORG_MAP`) which are logical names — not the actual Teradata view/table names. There is no mapping inside the .pbix file.

The developer must manually write SQL for each visual, which is time-consuming and error-prone.

### Design: Table mapping step (new Paso 2b in the panel)

Add a new route and panel card between scaffold and convert: **"Mapear tablas Power BI → Teradata"**.

#### How it works

1. After extraction, parse `out/<Report>/layout.json` to collect all unique Power BI entity names referenced across all visuals (the `entity` field in each visual's `fields` list).
2. Load existing mappings from `metrics/<Report>.table_map.json` if it exists.
3. Present a form: one row per unique PBI entity, with a text input for the Teradata equivalent and an optional test button.
4. On submit, save to `metrics/<Report>.table_map.json`.
5. When generating the yaml scaffold, use the mapping to populate `reference_table:` per visual field, and pre-fill `sql:` with a best-effort `SELECT` using the mapped Teradata table name and column names from `queryRef`.

#### New file: `metrics/<Report>.table_map.json`
```json
{
  "Compute Engine Mnthly": "CERTIFIED_DB.compute_engine_mnthly_vw",
  "Calendar": "sys_calendar.calendar",
  "ORG_MAP": "CERTIFIED_DB.org_map_vw",
  "AI Tokens hrly": "CERTIFIED_DB.ai_tokens_hrly_vw"
}
```

#### New route in gui.py
```python
@app.get("/reportes/{name}/mapeo", response_class=HTMLResponse)
def ver_mapeo(request: Request, name: str):
    pbix = _find_pbix(name)
    layout_path = OUT_DIR / safe_name(name) / "layout.json"
    entities = set()
    if layout_path.exists():
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
        for page in layout.get("pages", []):
            for v in page.get("visuals", []):
                for f in v.get("fields", []):
                    # fields is list of queryRef strings like "Calendar.Year"
                    if "." in f:
                        entities.add(f.split(".")[0])
    map_path = METRICS_DIR / f"{name}.table_map.json"
    existing = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}
    return templates.TemplateResponse("table_map.html", {
        "request": request, "name": name,
        "entities": sorted(entities), "mapping": existing,
    })

@app.post("/reportes/{name}/mapeo")
async def guardar_mapeo(request: Request, name: str):
    _find_pbix(name)
    form = await request.form()
    mapping = {}
    for key, val in form.items():
        if key.startswith("td__") and val.strip():
            pbi_name = key[4:]  # strip "td__" prefix
            mapping[pbi_name] = val.strip()
    map_path = METRICS_DIR / f"{name}.table_map.json"
    map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"ok": True, "title": "Mapeo guardado",
              "message": f"{len(mapping)} tabla(s) mapeada(s) a Teradata."}
    return _page(request, _find_pbix(name), result)
```

#### New template: `src/pbix2html/gui_templates/table_map.html`
Simple form — one row per entity:
```html
<form action="/reportes/{{ name }}/mapeo" method="post">
  {% for entity in entities %}
  <div class="map-row">
    <span class="pbi-name">{{ entity }}</span>
    <span class="arrow">→</span>
    <input type="text" name="td__{{ entity }}"
           value="{{ mapping.get(entity, '') }}"
           placeholder="schema.view_or_table_name">
  </div>
  {% endfor %}
  <button type="submit" class="btn btn-primary">Guardar mapeo</button>
</form>
```

#### Update scaffold to use the mapping

In `gui.py`'s `accion_plantilla()`, load `table_map.json` and pass it to `semantic.write_scaffold()`. Update `semantic.write_scaffold()` to accept a `table_map: dict` parameter and use it to generate pre-filled SQL stubs:

```python
# In write_scaffold(), for each visual with fields:
if table_map:
    td_tables = set()
    for ref in fields_doc:
        # ref is like "Values: Calendar.Year"
        parts = ref.split(": ", 1)[-1].split(".")
        if parts[0] in table_map:
            td_tables.add(table_map[parts[0]])
    if td_tables:
        entry["sql"] = (
            f"LOCKING TABLE {list(td_tables)[0]} FOR ACCESS\n"
            f"SELECT -- TODO: add columns\n"
            f"FROM {', '.join(td_tables)}\n"
            f"WHERE -- TODO: add filters\n"
        )
```

#### DBQL auto-population (future enhancement)

When `DBC.DBQLSqlTbl` access is available, add a button "Capturar SQL de DBQL" that runs:

```sql
SELECT TOP 200
    TRIM(SqlTextInfo) AS sql_text,
    StartTime
FROM DBC.DBQLSqlTbl
WHERE StartTime >= CURRENT_DATE - 30
  AND UserName IN (SELECT UserName FROM DBC.DBQLSqlTbl
                   WHERE SqlTextInfo LIKE '%<known_column>%'
                   GROUP BY 1 ORDER BY COUNT(*) DESC)
ORDER BY StartTime DESC;
```

Match each DBQL query to a visual by comparing referenced column names from the visual's `queryRef` list against column names in the DBQL SQL text. Populate `reference_sql` automatically.

---

## 10. Design addition: teradata-report skill compatibility

### Background

The **teradata-report skill** (version 4.5.0) generates self-contained HTML dashboards that fetch live Teradata data via the **HTML App Host (HAH)** platform. It uses:

- `POST {base}/api/execute` — to run Teradata SQL server-side
- Self-hosted JS libraries (no CDN) — Chart.js, Plotly, or Mermaid served from `{base}/static/`
- Three environments: `dev`, `uat`, `prd` with different base URLs

Reports generated by pbix2html currently use:
- ECharts loaded from CDN (`echarts_cdn` setting)
- No HAH API — data embedded as JSON (snapshot) or fetched from `serve.py` (live)
- No authentication flow

### Goal

Allow pbix2html to emit a **teradata-report skill-compatible HTML** — i.e. a live dashboard that can be deployed to HAH and fetch its data from Teradata in real time using the HAH SQL API, without needing `serve.py`.

### New convert mode: `hah`

Add `--mode hah` (and a matching panel option) alongside `snapshot` and `live`.

When `mode="hah"`:
1. The HTML uses the HAH SQL API instead of the local `serve.py`
2. JS libraries are loaded from the HAH self-hosted path, not CDN
3. Each visual's SQL is embedded in the JS and sent to `POST {SQL_API}` at render time
4. Authentication is handled by the HAH platform (Teradata credentials stored in user profile)

### HAH API contract (from teradata-report skill)

```javascript
const SQL_API = "{base}/api/execute";  // base = HAH environment URL

async function fetchSQL(sql) {
    const resp = await fetch(SQL_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql })
    });
    const j = await resp.json();
    if (j.error) throw new Error(j.error_message);  // HTTP 200 with error body
    return j.data;  // array of row objects
}
```

**Critical:** HAH returns HTTP 200 even for SQL errors. Always check `j.error` before using `j.data`.

### New render.py changes

Add `hah_base` parameter to `render_html()`:

```python
def render_html(layout, spec, values, data, mode="snapshot",
                role=None, include_hidden=False,
                hah_base="https://transcend-k8s-dev.td.teradata.com/dev-html-app-host"):
    ...
    tpl.render(
        ...,
        hah_base=hah_base,
        sql_api=f"{hah_base}/api/execute",
    )
```

### New template: `src/pbix2html/templates/report_hah.html.j2`

Key differences from `report.html.j2`:

**1. No CDN — self-hosted libraries:**
```html
<!-- Chart.js from HAH -->
<script src="{{ hah_base }}/static/chart.umd.min.js"></script>
<!-- Do NOT use echarts CDN -->
```

Call `list_libraries()` via the teradata-report MCP to get available library URLs before generating.

**2. SQL embedded per visual — fetched at render time:**
```javascript
const VISUALS = {{ visuals_with_sql_json }};
// Each entry: { id, kind, sql, title, left, top, w, h, z, stacked, area, inner_radius }

async function loadVisual(v) {
    const body = document.getElementById("vb-" + v.id);
    showSpinner(body);
    try {
        const rows = await fetchSQL(v.sql);
        renderVisual(body, v, rows);
        showFooter(body, rows.length);
    } catch(e) {
        showError(body, e.message);
    }
}

async function loadAll() {
    await Promise.all(VISUALS.map(loadVisual));
}
```

**3. Spinner / error / footer states (required by teradata-report skill):**
```javascript
function showSpinner(el) {
    el.innerHTML = '<div class="spinner"></div>';
}
function showError(el, msg) {
    el.innerHTML = `<div class="error-state">⚠ ${msg}</div>`;
}
function showFooter(el, count) {
    const f = document.createElement("div");
    f.className = "card-footer";
    f.textContent = `${count} filas`;
    el.appendChild(f);
}
```

**4. Teradata design tokens (required by teradata-report skill):**
```css
:root {
    --td-teal:   #00C7B1;
    --td-orange: #FF6D00;
    --td-navy:   #1A1A2E;
}
/* Topbar with gradient + teal accent */
header {
    background: linear-gradient(135deg, var(--td-navy) 0%, #2d2d5e 100%);
    border-bottom: 3px solid var(--td-teal);
}
/* KPI cards with colored top border */
.kpi-card { border-top: 4px solid var(--td-teal); }
```

**5. `safeSql()` for filter values:**
```javascript
function safeSql(v) {
    return String(v).replace(/'/g, "''").replace(/;/g, "");
}
// Usage:
const sql = `SELECT * FROM my_table WHERE year = '${safeSql(yearFilter)}'`;
```

**6. `window.__SNAPSHOT_CAPTURE__` check for tab lazy-loading:**
```javascript
// If running inside HAH snapshot capture, eager-load all tabs
if (window.__SNAPSHOT_CAPTURE__) {
    loadAll();
} else {
    // lazy: load visible page only
    loadPage(activePage);
}
```

### New panel option

In `report.html` (gui_templates), add `hah` to the mode dropdown and an environment selector:

```html
<select name="modo" id="modo">
  <option value="snapshot">📸 Snapshot (datos incrustados)</option>
  <option value="live">🔴 Live (serve.py)</option>
  <option value="hah">🌐 HAH (HTML App Host)</option>
</select>

<div id="hah-options" style="display:none">
  <label for="hah_env">Ambiente HAH</label>
  <select name="hah_env" id="hah_env">
    <option value="dev">dev</option>
    <option value="uat">uat</option>
    <option value="prd">prd</option>
  </select>
</div>

<script>
document.getElementById("modo").addEventListener("change", function() {
    document.getElementById("hah-options").style.display =
        this.value === "hah" ? "block" : "none";
});
</script>
```

### HAH environment base URLs

| Env | Base URL |
|-----|----------|
| dev | `https://transcend-k8s-dev.td.teradata.com/dev-html-app-host` |
| uat | `https://transcend-k8s-dev.td.teradata.com/html-app-host` |
| prd | `https://transcend-k8s.td.teradata.com/html-app-host` |

### Deployment via teradata-report MCP

Once the HAH-mode HTML is generated, it can be uploaded to HAH via the `create_report` MCP tool (available through the teradata-report skill). The HTML is self-contained and does not require `serve.py` or any local server.

After upload, users receive:
- **View URL:** `{base}/api/reports/{id}/view` — live dashboard
- **Manage URL:** `{base}/reports/{id}` — team/sharing/snapshots

### Checklist for HAH-compatible output

- [ ] No CDN URLs — all `<script src>` use `{hah_base}/static/...`
- [ ] `fetchSQL()` checks `j.error` before using `j.data`
- [ ] Every visual has spinner → data → error state
- [ ] Footer shows row count and timing
- [ ] Teradata topbar: gradient background + teal accent border
- [ ] KPI cards have colored top border
- [ ] `safeSql()` applied to all filter values before SQL interpolation
- [ ] `window.__SNAPSHOT_CAPTURE__` check for tab eager-loading
- [ ] SQL uses Teradata syntax: `SAMPLE` not `LIMIT`, `QUALIFY` for ranking, `LOCKING FOR ACCESS`
- [ ] No `SELECT *` — explicit column lists only
- [ ] HTML validated with `validate_html()` before upload

---

*End of pbix2html-fixv1.md*
