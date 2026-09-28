import json
import zipfile

from pbix2html import extract as ex


def test_layout_structure(fake_pbix):
    L = ex.extract_layout(fake_pbix)
    assert L["report"] == "Dashboard_Ejecutivo"
    assert L["theme"]["custom_json"]["dataColors"][0] == "#0F2B46"
    assert L["custom_visual_packages"] == ["Deneb"]
    p0 = L["pages"][0]
    assert p0["filters"][0]["target"] == "Calendario.Anio" and p0["filters"][0]["is_hidden"]
    by_id = {v["id"]: v for v in p0["visuals"]}
    assert by_id["v1"]["type"] == "card" and by_id["v1"]["title"] == "Ingresos"
    assert by_id["v2"]["fields"] == ["Region.Nombre", "Ventas.Margen %"]
    assert by_id["v4"]["is_custom"] is True
    assert by_id["g1"]["is_group"] is True
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


def test_theme_without_custom_theme_does_not_guess(tmp_path):
    """With no customTheme configured, it must not adopt just any JSON under StaticResources
    (e.g. a custom visual's resource file) as if it were the report's theme."""
    layout = {
        "id": 0, "resourcePackages": [],
        "config": json.dumps({"version": "5.55", "themeCollection": {"baseTheme": {"name": "CY24SU10", "type": 2}}}),
        "sections": [],
    }
    pbix = tmp_path / "SinTema.pbix"
    with zipfile.ZipFile(pbix, "w") as z:
        z.writestr("Report/Layout", b"\xff\xfe" + json.dumps(layout).encode("utf-16-le"))
        # a custom visual's resource file (not the theme) under StaticResources.
        z.writestr("Report/StaticResources/RegisteredResources/DenebSpec.json", json.dumps({"mark": "bar"}))
    L = ex.extract_layout(pbix)
    assert L["theme"]["custom_json"] is None
