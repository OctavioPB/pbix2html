import json
import zipfile

from pbix2html import extract as ex


def test_layout_structure(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    assert L["report"] == "Executive_Dashboard"
    assert L["theme"]["custom_json"]["dataColors"][0] == "#0F2B46"
    assert L["custom_visual_packages"] == ["Deneb"]
    p0 = L["pages"][0]
    assert p0["filters"][0]["target"] == "Calendar.Year" and p0["filters"][0]["is_hidden"]
    by_id = {v["id"]: v for v in p0["visuals"]}
    assert by_id["v1"]["type"] == "card" and by_id["v1"]["title"] == "Revenue"
    assert by_id["v2"]["fields"] == ["Region.Name", "Sales.Margin %"]
    assert by_id["v4"]["is_custom"] is True
    assert by_id["g1"]["is_group"] is True
    # textbox content (objects.general.paragraphs) was never wired up before: every
    # textbox rendered as an empty box. See conversation notes.
    assert by_id["v8"]["text"] == '<p><span style="font-weight:bold;color:#123456">Q3 Summary</span></p>'
    assert by_id["v1"]["text"] is None  # a card's own `general` object has no `paragraphs`
    # image resource (Report/StaticResources/RegisteredResources/logo.png) resolved to a
    # real embedded data URI — previously never read at all, every `image` visual rendered
    # as an empty frame regardless of whether it actually had a picture.
    # Visual-container formatting (vcObjects.background/border) and the page's own
    # wallpaper: previously discarded, so the renderer drew its own 1px box and generic
    # fill on every visual regardless of what the report actually looked like.
    assert by_id["v1"]["style"] == {"background": "#FFEECC", "border": True, "border_color": "#FF0000"}
    assert by_id["v2"]["style"] == {}          # says nothing → renderer leaves it bare
    assert p0["background"] == "#202020"
    assert L["pages"][1]["background"] is None
    # Visible text beyond the title: a subtitle, axis and legend titles, and the label
    # on a button — all dropped before, so shapes/buttons rendered as blank boxes.
    assert by_id["v2"]["texts"] == {"title": "Margin by Region", "subtitle": "FY2026, excl. tax",
                                    "axis_x": "Region", "axis_y": "Margin %", "legend": "Segment"}
    assert by_id["v10"]["texts"]["shape_text"] == "Back to summary"
    # …and a title the report switches off must not come through at all.
    assert "title" not in by_id["v10"]["texts"] and by_id["v10"]["title"] is None
    assert by_id["v9"]["image_ref"] == {"package": "RegisteredResources", "item": "logo.png"}
    assert by_id["v9"]["image_data_uri"].startswith("data:image/png;base64,")
    assert by_id["v1"].get("image_data_uri") is None
    assert L["pages"][1]["hidden"] and L["pages"][1]["visuals"][0]["hidden"]


def test_model_without_datamodel_is_graceful(fake_pbix):
    m = ex.extract_model(fake_pbix)
    assert "error" in m  # the synthetic file has no DataModel; a real DirectQuery pbix does


def test_filter_with_null_expression_does_not_crash(fake_pbix):
    """TopN/advanced filter with no canonical field: 'expression' can be null, not absent."""
    L = ex.extract_layout(fake_pbix)
    p0 = L["pages"][0]
    assert len(p0["filters"]) == 2
    f2 = p0["filters"][1]
    assert f2["target"] is None
    assert f2["type"] == "TopN"


def test_visual_with_null_projection_role_does_not_crash(fake_pbix):
    """An emptied field well leaves the role's key as null instead of removing it."""
    L = ex.extract_layout(fake_pbix)
    by_id = {v["id"]: v for v in L["pages"][0]["visuals"]}
    assert by_id["v6"]["fields"] == []
    assert by_id["v6"]["projections"] == {"Values": []}


def test_malformed_visual_becomes_stub_without_losing_the_report(fake_pbix):
    """A single visual with an unexpected shape must not take down extraction of the whole report."""
    L = ex.extract_layout(fake_pbix)
    by_id = {v["id"]: v for v in L["pages"][0]["visuals"]}
    assert by_id["v7"]["type"] == "__parse_error__"
    assert "parse_error" in by_id["v7"]
    # the rest of the report was extracted normally
    assert by_id["v1"]["type"] == "card"
    assert len(L["pages"]) == 2


def test_pbir_layout_structure(fake_pbir_pbix):
    """PBIR / Enhanced Report Format (no Report/Layout blob): see skill pbix-layout."""
    L = ex.extract_layout(fake_pbir_pbix)
    assert L["format"] == "pbir"
    assert L["report"] == "Dashboard_PBIR"
    assert L["theme"]["custom_json"]["dataColors"][0] == "#0F2B46"
    assert len(L["pages"]) == 1
    p0 = L["pages"][0]
    assert p0["display_name"] == "Executive Summary"
    by_id = {v["id"]: v for v in p0["visuals"]}
    assert by_id["v1"]["type"] == "card" and by_id["v1"]["title"] == "Revenue"
    assert by_id["v1"]["fields"] == ["Sales.Net Revenue"]
    assert by_id["v2"]["fields"] == ["Region.Name", "Sales.Margin %"]
    # a malformed visual.json (v3) is skipped, not stubbed and not fatal to the page.
    assert "v3" not in by_id
    # a group container ({"visualGroup": {...}}, no "visual" key) must be flagged is_group,
    # not fall through to type="unknown"/is_group=False (it would then render as a
    # full-page "unsupported" box instead of being skipped like a real group).
    assert by_id["g1"]["is_group"] is True and by_id["g1"]["type"] == "__group__"
    assert by_id["g1"]["title"] == "Header group"
    # textbox content (objects.general.paragraphs) was never wired up before: every
    # textbox rendered as an empty box. See conversation notes.
    assert by_id["v5"]["text"] == '<p><span style="font-weight:bold;color:#123456">Q3 Summary</span></p>'
    assert by_id["v1"]["text"] is None  # a card's own `general` object has no `paragraphs`
    assert len(by_id) == 4


def test_classic_layout_marks_its_format(fake_pbix):
    assert ex.extract_layout(fake_pbix)["format"] == "classic"


def test_theme_without_custom_theme_does_not_guess(tmp_path):
    """With no customTheme configured, it must not adopt just any JSON under StaticResources
    (e.g. a custom visual's resource file) as if it were the report's theme."""
    layout = {
        "id": 0, "resourcePackages": [],
        "config": json.dumps({"version": "5.55", "themeCollection": {"baseTheme": {"name": "CY24SU10", "type": 2}}}),
        "sections": [],
    }
    pbix = tmp_path / "NoTheme.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/Layout", b"\xff\xfe" + json.dumps(layout).encode("utf-16-le"))
        # a custom visual's resource file (not the theme) under StaticResources.
        z.writestr("Report/StaticResources/RegisteredResources/DenebSpec.json", json.dumps({"mark": "bar"}))
    L = ex.extract_layout(pbix)
    assert L["theme"]["custom_json"] is None


def test_pbir_filters_hidden_and_custom_visuals(tmp_path):
    """PBIR keys per Microsoft's published schemas (not yet seen in a real file)."""
    import json
    import zipfile

    from pbix2html.extract import extract_layout

    flt = {"name": "f1", "type": "Categorical", "isHiddenInViewMode": True,
           "field": {"Column": {"Expression": {"SourceRef": {"Entity": "Region"}}, "Property": "Name"}},
           "filter": {"Where": []}}
    pbix = tmp_path / "R.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/definition/report.json", json.dumps({
            "publicCustomVisuals": ["Sankey1.0"],
            "resourcePackages": [{"name": "Acme", "type": "CustomVisual"}, {"name": "Theme", "type": "SharedResources"}]}))
        z.writestr("Report/definition/pages/pages.json", json.dumps({"pageOrder": ["p1"]}))
        z.writestr("Report/definition/pages/p1/page.json", json.dumps(
            {"displayName": "P", "visibility": "HiddenInViewMode", "filterConfig": {"filters": [flt]}}))
        z.writestr("Report/definition/pages/p1/visuals/v1/visual.json", json.dumps({
            "position": {"x": 0, "y": 0, "width": 10, "height": 10}, "isHidden": True,
            "parentGroupName": "g1", "filterConfig": {"filters": [flt]},
            "visual": {"visualType": "card"}}))
    lay = extract_layout(pbix)
    page = lay["pages"][0]
    v = page["visuals"][0]
    assert page["hidden"] is True
    assert page["filters"][0]["target"] == "Region.Name" and page["filters"][0]["is_hidden"]
    assert v["hidden"] is True and v["parent_group"] == "g1"
    assert v["filters"][0]["target"] == "Region.Name"
    assert lay["custom_visual_packages"] == ["Sankey1.0", "Acme"]


def test_textbox_with_field_bound_run_is_not_lost():
    """Real report: a textbox run whose `value` is a dict (field reference)."""
    from pbix2html.extract import extract_textbox_text

    objs = {"general": [{"properties": {"paragraphs": [{"textRuns": [
        {"value": "Last Data Available "},
        {"value": {"propertyIdentifier": {"objectName": "values"}, "selector": {"id": "lu"}}},
    ]}]}}]}
    assert extract_textbox_text(objs) == "<p>Last Data Available </p>"


def test_classic_group_has_same_keys_and_bundled_custom_visuals(tmp_path):
    import json
    import zipfile

    from pbix2html.extract import extract_layout

    cfg = json.dumps({"name": "g1", "singleVisualGroup": {"displayName": "G"}})
    layout = {"sections": [{"name": "s", "displayName": "S", "visualContainers": [
        {"config": cfg, "x": 0, "y": 0, "width": 1, "height": 1}]}], "resourcePackages": []}
    pbix = tmp_path / "C.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/Layout", json.dumps(layout).encode("utf-16-le"))
        z.writestr("Report/CustomVisuals/MyViz123/package.json", "{}")
        z.writestr("Report/CustomVisuals/MyViz123/resources/MyViz123.pbiviz.json", "{}")
    lay = extract_layout(pbix)
    g = lay["pages"][0]["visuals"][0]
    assert g["is_group"] and g["hidden"] is False and g["filters"] == []
    assert lay["custom_visual_packages"] == ["MyViz123"]
