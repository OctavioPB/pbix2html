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
