"""Visual titles: the report's own title formatting, and a readable colour when it sets none."""
from pbix2html import extract as ex
from pbix2html import render as R

THEME = {"foreground": "#252423", "background": "#FFFFFF"}


def _vco():
    lit = lambda v: {"expr": {"Literal": {"Value": v}}}                                   # noqa: E731
    return {"title": [{"properties": {
        "fontColor": {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 0, "Percent": 0}}}}},
        "fontSize": lit("10D"), "bold": lit("true"), "alignment": lit("'center'")}}],
        "background": [{"properties": {"transparency": lit("100D"),
                                       "color": {"solid": {"color": lit("'#626D73'")}}}}]}


def test_container_style_reads_title_formatting_and_background_transparency():
    st = ex.container_style(_vco())
    assert st["title_color"] == "theme:0:0" and st["title_size"] == 10.0
    assert st["title_bold"] is True and st["title_align"] == "center" and st["transparency"] == 100.0


def test_title_css_uses_what_the_report_set_and_only_safe_values():
    css = R._title_css({"title_color": "#FFFFFF", "title_size": 12, "title_bold": True, "title_align": "center"}, None)
    assert css == "color:#FFFFFF;font-size:16.0px;font-weight:700;text-align:center"
    assert R._title_css({"title_color": "red;}</style><x>"}, None) == ""             # not a plain colour: dropped
    assert R._title_css({}, "#FFFFFF") == "color:#FFFFFF"                            # fallback colour


def test_dark_panel_behind_a_visual_gets_a_readable_text_colour():
    panel = {"x": 1000, "y": 0, "width": 300, "height": 400, "z": 1, "style": {"background": "#00233C"}}
    slicer = {"x": 1036, "y": 62, "width": 232, "height": 64, "z": 5, "style": {}}
    back = R._backdrop(slicer, [panel, slicer], None, THEME)
    assert back == "#00233C" and R._readable_fg(back, THEME) in ("#FFFFFF",)
    off_panel = {**slicer, "x": 10}
    assert R._readable_fg(R._backdrop(off_panel, [panel, off_panel], None, THEME), THEME) is None   # white page: theme fg
    ghost = {**panel, "style": {"background": "#00233C", "transparency": 100.0}}          # invisible fill: ignored
    assert R._backdrop(slicer, [ghost, slicer], None, THEME) == "#FFFFFF"


def test_a_transparent_slicer_background_is_not_painted(fake_pbix):
    from pbix2html import semantic
    layout = ex.extract_layout(fake_pbix)
    v = layout["pages"][0]["visuals"][0]
    v["style"] = {"background": "#626D73", "transparency": 100.0, "title_color": "#FFFFFF"}
    spec = semantic.load("Executive_Dashboard")
    entry = next(e for e in R.build_spec(layout, spec, {"year": 2025})["pages"][0]["visuals"] if e["id"] == v["id"])
    assert "background" not in entry["style"] and entry["title_css"] == "color:#FFFFFF"


# ---- things `pbix2html verify` found on real reports, now fixed at the source ------------------------------------

def test_paragraph_alignment_is_kept_in_a_textbox():
    o = {"general": [{"properties": {"paragraphs": [
        {"textRuns": [{"value": "Title", "textStyle": {"color": "#ffffff"}}], "horizontalTextAlignment": "center"},
        {"textRuns": [{"value": "plain"}]}]}}]}
    assert ex.extract_textbox_text(o) == ('<p style="text-align:center"><span style="color:#ffffff">Title</span></p>'
                                          "<p>plain</p>")
    assert ex.extract_textbox_text({"general": [{"properties": {"paragraphs": [{"textRuns": []}]}}]}) is None


def test_a_report_without_a_custom_theme_still_resolves_white_and_black():
    pal = ex.theme_palette({})
    ref = {"ThemeDataColor": {"ColorId": 0, "Percent": 0}}
    assert ex.literal_color(ref, pal) == "#FFFFFF" and pal[2] == "#118DFF"        # a white title stays white


def test_a_cards_number_colour_is_read():
    o = {"labels": [{"properties": {"color": {"solid": {"color": {"expr": {"ThemeDataColor": {"ColorId": 8, "Percent": 0}}}}}}}]}
    assert ex._style_with_fill({}, o)["value_color"] == "theme:8:0"


def test_default_shape_fill_and_translucent_button_give_readable_text(fake_pbix):
    from pbix2html import semantic
    layout = ex.extract_layout(fake_pbix)
    page = layout["pages"][0]
    base = {"x": 0, "y": 0, "width": 100, "height": 100, "z": 0, "title": None, "projections": {}, "filters": [],
            "groups": [], "is_group": False, "texts": {}, "text": None}
    shape = {**base, "id": "panel", "type": "shape", "style": {}, "objects_keys": ["shape"],
             "x": 500, "y": 0, "width": 200, "height": 500}
    button = {**base, "id": "btn", "type": "actionButton", "style": {"background": "#6B7A8D"}, "objects_keys": ["fill"],
              "x": 520, "y": 20, "width": 160, "height": 40, "z": 5, "texts": {"shape_text": "Daily View"},
              "button": {"hidden": [], "states": {"default": {"fill": {"color": "#6B7A8D", "transparency": 76},
                                                              "text": {}}}}}
    page["visuals"] += [shape, button]
    layout["theme"] = {"custom_json": {"dataColors": ["#00233C", "#FF5F02"], "foreground": "#252423", "background": "#FFFFFF"}}
    out = R.build_spec(layout, semantic.load("Executive_Dashboard"), {"year": 2025})["pages"][0]["visuals"]
    by = {e["id"]: e for e in out}
    assert by["panel"]["style"]["background"] == "#00233C"                       # the theme's first colour
    assert "--fg:#FFFFFF" in (by["btn"]["btn_css"] or "")                        # white text on the navy panel
