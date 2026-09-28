"""Panel routes, isolated from the real reports/metrics/out via monkeypatched dirs."""
import dataclasses
import json
import shutil

from fastapi.testclient import TestClient

from pbix2html import gui, semantic


def _client(tmp_path, monkeypatch, fake_pbix):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    shutil.copy(fake_pbix, reports_dir / fake_pbix.name)
    monkeypatch.setattr(gui, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(gui, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(semantic, "METRICS_DIR", tmp_path / "metrics")
    return TestClient(gui.app), fake_pbix.stem


def test_table_map_shows_entities_after_extract(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    r = c.get(f"/reports/{name}/table-map")
    assert r.status_code == 200 and "Sales" in r.text and "Region" in r.text


def test_table_map_entities_are_not_mangled_by_aggregated_fields(tmp_path, monkeypatch, fake_pbix):
    # Regression: an aggregated field ("Sum(Sales.Amount)", the common shape for a
    # numeric column dropped into a Values well) used to produce the entity
    # "Sum(Sales" instead of "Sales" — DAX aggregation syntax fused onto a mangled
    # table name. Write a layout.json directly: the shared fake_pbix fixture doesn't
    # happen to use the aggregated form, so this wouldn't be caught via extract().
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    rdir = tmp_path / "out" / name
    rdir.mkdir(parents=True)
    layout = {"pages": [{"visuals": [{"fields": ["Sum(Sales.Amount)", "Region.Name"]}]}]}
    (rdir / "layout.json").write_text(json.dumps(layout), encoding="utf-8")

    r = c.get(f"/reports/{name}/table-map")
    assert r.status_code == 200
    assert "Sales" in r.text and "Region" in r.text
    assert "Sum(Sales" not in r.text and "Sum(" not in r.text


def test_table_map_rejects_unsafe_query(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    r = c.post(f"/reports/{name}/table-map", data={"td__Sales": "DELETE FROM sales_fact"})
    assert r.status_code == 200
    assert "Mapping not saved" in r.text
    assert "DELETE" in r.text
    assert not (tmp_path / "metrics" / f"{name}.table_map.json").exists()


def test_table_map_accepts_read_only_query_and_feeds_scaffold(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")

    r = c.post(f"/reports/{name}/table-map", data={"td__Sales": "SELECT * FROM sales_fact"})
    assert r.status_code == 200 and "Mapping saved" in r.text
    saved = (tmp_path / "metrics" / f"{name}.table_map.json").read_text(encoding="utf-8")
    assert "SELECT * FROM sales_fact" in saved

    r = c.post(f"/reports/{name}/scaffold")
    assert r.status_code == 200 and "Template generated" in r.text
    yaml_text = (tmp_path / "metrics" / f"{name}.yaml").read_text(encoding="utf-8")
    assert "FROM (SELECT * FROM sales_fact) AS sales" in yaml_text


# ----------------------------------------------------------------------------
# Step 2c: edit SQL/parameters/roles from the panel (no manual yaml editing)
# ----------------------------------------------------------------------------

def _edit_form(name: str, spec, **overrides) -> dict:
    """Builds a full, faithful POST body for /reports/{name}/edit from a loaded
    spec — mirrors every field edit_metrics.html renders for existing rows, so a
    test only has to override the one or two fields it cares about."""
    data = {}
    for pname, p in spec.parameters.items():
        p = p or {}
        data[f"param__{pname}__type"] = p.get("type") or "string"
        data[f"param__{pname}__default"] = "" if p.get("default") is None else str(p.get("default"))
        data[f"param__{pname}__label"] = p.get("label") or pname
        if p.get("multi"):
            data[f"param__{pname}__multi"] = "on"
    data["newparam__name"] = ""
    data["newparam__type"] = "string"
    data["newparam__default"] = ""
    data["newparam__label"] = ""
    for rname, r in spec.roles.items():
        r = r or {}
        data[f"role__{rname}__proxy_user"] = r.get("proxy_user") or ""
        data[f"role__{rname}__where"] = r.get("where") or ""
    data["newrole__name"] = ""
    data["newrole__proxy_user"] = ""
    data["newrole__where"] = ""
    for vid, v in spec.visuals.items():
        data[f"visual__{vid}__kind"] = v.kind
        data[f"visual__{vid}__title"] = v.title or ""
        data[f"visual__{vid}__sql"] = v.sql or ""
        data[f"visual__{vid}__params"] = ", ".join(v.params)
        data[f"visual__{vid}__reference_sql"] = v.reference_sql or ""
        data[f"visual__{vid}__tolerance_rel"] = str((v.tolerance or {}).get("rel", ""))
        data[f"visual__{vid}__notes"] = v.notes or ""
    data.update(overrides)
    return data


def test_edit_page_without_yaml_prompts_to_scaffold_first(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    r = c.get(f"/reports/{name}/edit")
    text = r.text.lower()
    assert r.status_code == 200 and "generate the" in text and "step 2" in text


def test_edit_page_shows_existing_visuals_and_params(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    r = c.get(f"/reports/{name}/edit")
    assert r.status_code == 200
    assert 'name="visual__v1__sql"' in r.text or "visual__" in r.text


def test_edit_rejects_unsafe_sql_and_leaves_yaml_untouched(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    yaml_path = tmp_path / "metrics" / f"{name}.yaml"
    before = yaml_path.read_text(encoding="utf-8")
    spec = semantic.load(name)
    vid = next(iter(spec.visuals))

    data = _edit_form(name, spec, **{f"visual__{vid}__sql": "DELETE FROM sales_fact"})
    r = c.post(f"/reports/{name}/edit", data=data)
    assert r.status_code == 200
    assert "Not saved" in r.text
    assert "must start with SELECT" in r.text
    assert yaml_path.read_text(encoding="utf-8") == before


def test_edit_saves_sql_and_new_parameter(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    spec = semantic.load(name)
    vid = next(iter(spec.visuals))

    data = _edit_form(
        name, spec,
        **{f"visual__{vid}__sql": "SELECT COUNT(*) AS value FROM sales_fact",
           f"visual__{vid}__title": "Edited via UI"},
    )
    data["newparam__name"] = "top_n"
    data["newparam__type"] = "int"
    data["newparam__default"] = "10"
    data["newparam__label"] = "Top N"

    r = c.post(f"/reports/{name}/edit", data=data)
    assert r.status_code == 200 and "Metrics saved" in r.text

    saved = semantic.load(name)
    assert saved.visuals[vid].sql == "SELECT COUNT(*) AS value FROM sales_fact"
    assert saved.visuals[vid].title == "Edited via UI"
    assert saved.parameters["top_n"]["type"] == "int"
    assert saved.parameters["top_n"]["label"] == "Top N"


def test_edit_deletes_parameter(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    spec = semantic.load(name)
    data = _edit_form(name, spec)
    data["newparam__name"] = "doomed"
    data["newparam__type"] = "string"
    r = c.post(f"/reports/{name}/edit", data=data)
    assert "Metrics saved" in r.text
    assert "doomed" in semantic.load(name).parameters

    spec2 = semantic.load(name)
    data2 = _edit_form(name, spec2)
    data2["param__doomed__delete"] = "on"
    r = c.post(f"/reports/{name}/edit", data=data2)
    assert "Metrics saved" in r.text
    assert "doomed" not in semantic.load(name).parameters


# ----------------------------------------------------------------------------
# Live service control — start/stop/status as panel buttons, not a second terminal
# ----------------------------------------------------------------------------

def test_live_status_reports_unreachable_when_nothing_listening(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    monkeypatch.setattr(gui, "settings", dataclasses.replace(gui.settings, api_base="http://127.0.0.1:1"))
    r = c.get(f"/live/status?name={name}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "unreachable" and body["owned"] is False


def test_live_start_spawns_and_reports_success(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    started = []
    monkeypatch.setattr(gui, "_start_live_process", lambda: started.append(True))
    monkeypatch.setattr(gui, "_live_status", lambda api_base, report, timeout=1.5: "ok")
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    r = c.post("/live/start", data={"name": name})
    assert r.status_code == 200
    assert started == [True]
    assert "Live service started" in r.text


def test_live_start_reports_failure_when_process_never_comes_up(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    monkeypatch.setattr(gui, "_start_live_process", lambda: None)
    monkeypatch.setattr(gui, "_live_status", lambda api_base, report, timeout=1.5: "unreachable")
    monkeypatch.setattr(gui.time, "sleep", lambda s: None)
    r = c.post("/live/start", data={"name": name})
    assert r.status_code == 200
    assert "come up" in r.text  # title has an apostrophe, HTML-escaped by Jinja


def test_live_stop_calls_stop_process(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    stopped = []
    monkeypatch.setattr(gui, "_stop_live_process", lambda: stopped.append(True))
    r = c.post("/live/stop", data={"name": name})
    assert r.status_code == 200
    assert stopped == [True]
    assert "Live service stopped" in r.text
