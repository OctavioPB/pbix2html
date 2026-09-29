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


def test_extract_auto_fills_table_map_from_power_query(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    fake_model = {"power_query": [
        {"TableName": "Sales", "Expression":
            'let\n    Source = Teradata.Database("h"),\n    t = Source{[Schema="dbo",Item="SalesFact"]}[Data]\nin\n    t'},
    ]}
    monkeypatch.setattr(gui.ex, "extract_model", lambda pbix: fake_model)

    r = c.post(f"/reports/{name}/extract")
    assert r.status_code == 200
    assert "Auto-mapped 1 Power BI table" in r.text

    saved = json.loads((tmp_path / "metrics" / f"{name}.table_map.json").read_text())
    assert saved == {"Sales": "SELECT * FROM dbo.SalesFact"}

    # The table-map page shows it as pre-filled and flags it as auto-detected.
    r2 = c.get(f"/reports/{name}/table-map")
    assert "SELECT * FROM dbo.SalesFact" in r2.text
    assert "✓ detected automatically" in r2.text


def test_extract_never_overwrites_a_manually_entered_table_map(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    # A human maps "Sales" by hand, deliberately different from what detection would say.
    c.post(f"/reports/{name}/table-map", data={"td__Sales": "SELECT * FROM my_custom_sales_view"})

    fake_model = {"power_query": [
        {"TableName": "Sales", "Expression":
            'let\n    Source = Teradata.Database("h"),\n    t = Source{[Schema="dbo",Item="SalesFact"]}[Data]\nin\n    t'},
    ]}
    monkeypatch.setattr(gui.ex, "extract_model", lambda pbix: fake_model)
    c.post(f"/reports/{name}/extract")  # re-extract, as if refreshing the report

    saved = json.loads((tmp_path / "metrics" / f"{name}.table_map.json").read_text())
    assert saved == {"Sales": "SELECT * FROM my_custom_sales_view"}  # untouched

    # And the page must NOT badge this row as auto-detected anymore, since what's saved
    # no longer matches what detection would currently produce (the static explainer
    # paragraph still says "detected automatically" in general, so check the row badge
    # specifically, not just the substring).
    r = c.get(f"/reports/{name}/table-map")
    assert "✓ detected automatically" not in r.text


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


def test_theme_view_has_no_override_by_default(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    r = c.get(f"/reports/{name}/theme")
    assert r.status_code == 200 and "currently saved" not in r.text


def test_theme_saves_from_individual_fields(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    r = c.post(f"/reports/{name}/theme", data={
        "dataColors": "#AA00AA, #00AA00", "background": "#111111", "fontFamily": "Georgia, serif",
    })
    assert r.status_code == 200 and "Theme saved" in r.text
    saved = json.loads((tmp_path / "metrics" / f"{name}.theme.json").read_text())
    assert saved == {"dataColors": ["#AA00AA", "#00AA00"], "background": "#111111", "fontFamily": "Georgia, serif"}


def test_theme_saves_from_pasted_json_and_drops_unrecognized_keys(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    pasted = json.dumps({"name": "Corp", "visualStyles": {"*": {}}, "background": "#222222"})
    r = c.post(f"/reports/{name}/theme", data={"pasted_json": pasted})
    assert r.status_code == 200 and "Theme saved" in r.text
    saved = json.loads((tmp_path / "metrics" / f"{name}.theme.json").read_text())
    assert saved == {"background": "#222222"}


def test_theme_rejects_invalid_hex_and_leaves_no_file(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    r = c.post(f"/reports/{name}/theme", data={"background": "not-a-color"})
    assert r.status_code == 200
    assert "Theme not saved" in r.text and "not-a-color" in r.text
    assert not (tmp_path / "metrics" / f"{name}.theme.json").exists()


def test_theme_reset_removes_override(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/theme", data={"background": "#111111"})
    assert (tmp_path / "metrics" / f"{name}.theme.json").exists()
    r = c.post(f"/reports/{name}/theme/reset")
    assert r.status_code == 200 and "Theme reset" in r.text
    assert not (tmp_path / "metrics" / f"{name}.theme.json").exists()


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


def test_edit_rejects_unsafe_sql_even_with_a_todo_comment_attached(tmp_path, monkeypatch, fake_pbix):
    # Regression: the "is this still an unwritten placeholder?" check used to be
    # "contains the word TODO anywhere" (later, briefly, "doesn't start with SELECT") —
    # both let a destructive statement skip validate_read_only_sql entirely and get
    # saved as-is, later executed as-is by query.py against a real Teradata connection.
    # A comment mentioning TODO is exactly the kind of thing a person reviewing an
    # auto-drafted visual would plausibly add ("-- TODO: verify this join").
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    yaml_path = tmp_path / "metrics" / f"{name}.yaml"
    before = yaml_path.read_text(encoding="utf-8")
    spec = semantic.load(name)
    vid = next(iter(spec.visuals))

    data = _edit_form(name, spec, **{f"visual__{vid}__sql": "DELETE FROM sales_fact -- TODO: cleanup later"})
    r = c.post(f"/reports/{name}/edit", data=data)
    assert r.status_code == 200
    assert "Not saved" in r.text
    assert "must start with SELECT" in r.text
    assert yaml_path.read_text(encoding="utf-8") == before  # not silently written to disk


def test_edit_still_allows_saving_an_untouched_todo_stub(tmp_path, monkeypatch, fake_pbix):
    # The fix above must not block the ordinary case: saving the form with a visual
    # whose sql is still exactly the auto-generated stub (nobody has written it yet).
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    spec = semantic.load(name)
    vid = next(v for v in spec.visuals if spec.visuals[v].sql and "TODO" in spec.visuals[v].sql)

    data = _edit_form(name, spec)  # unmodified — vid's sql is still whatever scaffold wrote
    r = c.post(f"/reports/{name}/edit", data=data)
    assert r.status_code == 200 and "Metrics saved" in r.text
    assert "TODO" in semantic.load(name).visuals[vid].sql


def test_regenerate_backs_up_the_previous_yaml_and_can_restore_it(tmp_path, monkeypatch, fake_pbix):
    # From BusinessReport.md ask 5: "Regenerate template" used to be a one-way door
    # guarded by a single confirm() dialog, with no way back for someone with no git
    # and no text editor. It now keeps a copy first, and the panel can put it back.
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    spec = semantic.load(name)
    vid = next(iter(spec.visuals))
    c.post(f"/reports/{name}/edit",
           data=_edit_form(name, spec, **{f"visual__{vid}__sql": "SELECT 1 AS value FROM sales_fact"}))
    assert semantic.load(name).visuals[vid].sql == "SELECT 1 AS value FROM sales_fact"

    r = c.post(f"/reports/{name}/scaffold", data={"regenerate": "on"})   # destructive
    assert "previous version was saved" in r.text
    assert semantic.load(name).visuals[vid].sql != "SELECT 1 AS value FROM sales_fact"   # really wiped

    backups = semantic.list_backups(name)
    assert len(backups) == 1
    r = c.post(f"/reports/{name}/restore", data={"backup": backups[0].name})
    assert r.status_code == 200 and "Previous version restored" in r.text
    assert semantic.load(name).visuals[vid].sql == "SELECT 1 AS value FROM sales_fact"   # got it back
    assert len(semantic.list_backups(name)) == 2   # restoring is itself undoable


def test_restore_rejects_a_path_outside_the_backups_folder(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    before = semantic.yaml_path(name).read_text(encoding="utf-8")
    secret = tmp_path / "metrics" / "not-a-backup.yaml"
    secret.write_text("report: somethingelse\n", encoding="utf-8")

    for payload in ["../not-a-backup.yaml", "../../etc/passwd", "..\\..\\.env"]:
        r = c.post(f"/reports/{name}/restore", data={"backup": payload})
        assert r.status_code == 200 and "Couldn" in r.text          # refused, not applied
    assert semantic.yaml_path(name).read_text(encoding="utf-8") == before


def test_data_model_downloads_in_both_formats(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    # Before Extract there's nothing to download, and the buttons aren't offered.
    assert c.get(f"/reports/{name}/model.json").status_code == 404
    assert "Data model (readable)" not in c.get(f"/reports/{name}").text

    model = {
        "tables": ["Sales", "Region"],
        "storage_modes": {"1": 2},
        "measures": [{"TableName": "Sales", "Name": "Net Revenue", "Expression": "SUM(Sales[Amount])"}],
        "relationships": [{"FromTable": "Sales", "FromColumn": "RegionId",
                           "ToTable": "Region", "ToColumn": "Id"}],
        "rls": [{"RoleName": "North", "TableName": "Region", "FilterExpression": "[Name]=\"North\""}],
        "power_query": [{"TableName": "Sales", "Expression": 'let Source = Teradata.Database("h") in Source'}],
    }
    rdir = tmp_path / "out" / name
    rdir.mkdir(parents=True)
    (rdir / "model.json").write_text(json.dumps(model), encoding="utf-8")
    (rdir / "layout.json").write_text('{"pages": []}', encoding="utf-8")

    assert "Data model (readable)" in c.get(f"/reports/{name}").text

    raw = c.get(f"/reports/{name}/model.json")
    assert raw.status_code == 200 and json.loads(raw.content)["tables"] == ["Sales", "Region"]
    assert "attachment" in raw.headers["content-disposition"]

    md = c.get(f"/reports/{name}/model.md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]
    body = md.text
    assert "SUM(Sales[Amount])" in body          # the DAX, in a code block
    assert "DirectQuery" in body                 # storage mode decoded, not a raw "1"
    assert "RoleName" in body and "North" in body            # RLS rendered
    assert "Teradata.Database" in body                       # Power Query source
    assert "| FromTable |" in body                           # relationships as a table

    assert c.get(f"/reports/{name}/layout.json").status_code == 200
    assert c.get(f"/reports/{name}/model.txt").status_code == 404
    assert c.get("/reports/NoSuchReport/model.json").status_code == 404


def test_data_model_download_explains_a_thin_report(tmp_path, monkeypatch, fake_pbix):
    # A report with no local model (live connection to a published dataset) is the
    # common real case — the download must explain that, not hand over a bare error key.
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    rdir = tmp_path / "out" / name
    rdir.mkdir(parents=True)
    (rdir / "model.json").write_text(json.dumps(
        {"error": "NoEmbeddedModelError: no data model", "connection": {"DatasetId": "abc"}}), encoding="utf-8")

    body = c.get(f"/reports/{name}/model.md").text
    assert "No model could be read" in body
    assert "connects live to a published dataset" in body
    assert "DatasetId" in body


def test_generated_html_can_be_opened_and_downloaded(tmp_path, monkeypatch, fake_pbix):
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / f"{name}.html").write_text("<html>report</html>", encoding="utf-8")

    # Listed persistently on the report page, not only in the banner right after convert.
    page = c.get(f"/reports/{name}").text
    assert f"{name}.html" in page and "Finished reports" in page
    assert f"/files/{name}.html?download=1" in page

    opened = c.get(f"/files/{name}.html")
    assert opened.status_code == 200
    assert "attachment" not in opened.headers.get("content-disposition", "")

    downloaded = c.get(f"/files/{name}.html?download=1")
    assert downloaded.status_code == 200
    assert "attachment" in downloaded.headers["content-disposition"]
    assert f"{name}.html" in downloaded.headers["content-disposition"]
    assert downloaded.content == b"<html>report</html>"

    # The download param must not become a way around the filename check.
    assert c.get("/files/../../.env?download=1").status_code in (400, 404)


def test_upload_warns_when_it_replaces_a_report_that_already_has_sql(tmp_path, monkeypatch, fake_pbix):
    # Re-uploading a .pbix over one that already has a metrics yaml is legitimate
    # (updating a report), but the SQL in that yaml was written against the old file —
    # silently swapping it out leaves someone debugging a mismatch with no clue why.
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    payload = fake_pbix.read_bytes()

    r = c.post("/upload", files={"file": (f"{name}.pbix", payload, "application/octet-stream")},
               follow_redirects=False)
    assert "replaced=1" in r.text
    assert "already had SQL" in c.get(f"/reports/{name}?replaced=1").text

    # A genuinely new report gets no warning.
    r = c.post("/upload", files={"file": ("Brand_New.pbix", payload, "application/octet-stream")},
               follow_redirects=False)
    assert "replaced=1" not in r.text
    assert (tmp_path / "reports" / "Brand_New.pbix").read_bytes() == payload   # streamed intact


def test_upload_cannot_write_outside_the_reports_folder(tmp_path, monkeypatch, fake_pbix):
    c, _ = _client(tmp_path, monkeypatch, fake_pbix)
    reports = (tmp_path / "reports").resolve()

    # Non-.pbix and punctuation-bearing names are refused outright...
    for bad in ["evil.exe", "a;b.pbix", "sh.pbix.exe"]:
        assert c.post("/upload", files={"file": (bad, b"x", "application/octet-stream")}).status_code == 400

    # ...while a traversal in the name is neutralised (only the basename is used), so it
    # lands inside reports/ rather than being an error.
    for traversal in ["../evil.pbix", "..\\evil.pbix", "/tmp/evil.pbix"]:
        c.post("/upload", files={"file": (traversal, b"x", "application/octet-stream")})
    for p in tmp_path.rglob("evil.pbix"):
        assert reports in p.resolve().parents, f"{p} escaped {reports}"
    assert not list(tmp_path.rglob("*.exe"))


def test_malformed_table_map_file_is_reported_not_crashed_on(tmp_path, monkeypatch, fake_pbix):
    # A hand-edited metrics/<Report>.table_map.json used to reach scaffold as-is: a JSON
    # list hit table_map.get(...) (AttributeError) and a numeric value hit .strip().
    # Returning {} silently was no better — the mapping looks like it vanished and the
    # next scaffold quietly writes TODO stubs instead of the person's queries.
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    mpath = tmp_path / "metrics" / f"{name}.table_map.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)

    for content in ['["a", "b"]', '{"Sales": 123}', 'not json at all']:
        mpath.write_text(content, encoding="utf-8")
        r = c.get(f"/reports/{name}/table-map")
        assert r.status_code == 200, f"crashed on {content}"
        assert "problem with the saved mapping" in r.text or "ignored" in r.text, content
        assert c.post(f"/reports/{name}/scaffold", data={"regenerate": "on"}).status_code == 200

    mpath.write_text('{"Sales": "SELECT * FROM sales_fact"}', encoding="utf-8")
    r = c.get(f"/reports/{name}/table-map")
    assert "problem with the saved mapping" not in r.text   # valid file: no false alarm


def test_role_name_with_a_path_separator_is_rejected(tmp_path, monkeypatch, fake_pbix):
    # A role name becomes part of the generated HTML's filename (one file per role), so
    # "../../.." in one steered that write anywhere on disk; "Sales/North" did it by
    # accident. Rejected at the door now, and safe_name()'d again when the file is
    # written, in case the yaml was edited outside the panel.
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    spec = semantic.load(name)

    for bad in ["../../../../evil", "Sales/North", r"..\..\evil"]:
        data = _edit_form(name, spec)
        data["newrole__name"] = bad
        r = c.post(f"/reports/{name}/edit", data=data)
        assert "Not saved" in r.text, f"accepted role name {bad!r}"
        assert bad not in semantic.load(name).roles

    data = _edit_form(name, spec)
    data["newrole__name"] = "Sales North-2.0"       # ordinary business label still fine
    assert "Metrics saved" in c.post(f"/reports/{name}/edit", data=data).text
    assert "Sales North-2.0" in semantic.load(name).roles


def test_convert_never_writes_outside_out_dir(tmp_path, monkeypatch, fake_pbix):
    # Defence in depth for the same issue: even if a traversal role name got into the
    # yaml some other way (hand-edited file), the output path must stay under out/.
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    raw = semantic.load(name).raw
    raw["roles"] = {"../../../../escapee": {"proxy_user": None, "where": None}}
    semantic.save_raw(name, raw)

    c.post(f"/reports/{name}/convert", data={"mode": "snapshot", "role": "../../../../escapee",
                                             "use_demo": "on"})
    out_dir = (tmp_path / "out").resolve()
    written = [p.resolve() for p in tmp_path.rglob("*.html")]
    assert written, "expected the convert to produce something"
    for p in written:
        assert out_dir in p.parents, f"{p} escaped {out_dir}"


def test_readiness_counts_what_still_needs_sql(tmp_path, monkeypatch, fake_pbix):
    # From BusinessReport.md ask 3: the panel now answers "is this a quick job or does
    # it need engineering time" without anyone opening the yaml to count TODOs.
    c, name = _client(tmp_path, monkeypatch, fake_pbix)
    c.post(f"/reports/{name}/extract")
    c.post(f"/reports/{name}/scaffold")
    spec = semantic.load(name)
    fresh = semantic.readiness(spec)
    assert fresh["needs_sql"] > 0 and fresh["ready"] == 0 and fresh["todo"] == fresh["needs_sql"]

    vid = next(iter(spec.visuals))
    c.post(f"/reports/{name}/edit",
           data=_edit_form(name, spec, **{f"visual__{vid}__sql": "SELECT 1 AS value FROM sales_fact"}))
    after = semantic.readiness(semantic.load(name))
    assert after["ready"] == 1 and after["todo"] == fresh["todo"] - 1

    assert f"{after['ready']} of {after['needs_sql']} visuals ready" in c.get(f"/reports/{name}").text
    assert "Still need SQL by hand" in c.get("/").text


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
