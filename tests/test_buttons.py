"""Action-button formatting: reading per state (extract) and converting to CSS (render)."""
from pbix2html import extract as ex, semantic
from pbix2html.render import _button_css, render_html


def _lit(v):
    return {"expr": {"Literal": {"Value": v}}}


def _color(c):
    return {"solid": {"color": {"expr": {"Literal": {"Value": c}}}}}


def test_parse_button_reads_states_and_show_flags():
    sv = {"visualType": "actionButton", "objects": {
        "text": [{"properties": {"show": _lit("true")}},
                 {"selector": {"id": "default"}, "properties": {
                     "text": _lit("'Go'"), "fontSize": _lit("11D"), "bold": _lit("true"),
                     "fontColor": _color("'#112233'"), "horizontalAlignment": _lit("'left'")}}],
        "fill": [{"properties": {"show": _lit("true")}},
                 {"selector": {"id": "default"},
                  "properties": {"fillColor": _color("'#FFFFFF'"), "transparency": _lit("0D")}},
                 {"selector": {"id": "hover"}, "properties": {"fillColor": _color("'#FF5F02'")}}],
        "outline": [{"properties": {"show": _lit("false")}}],
    }}
    b = ex.parse_button(sv)
    assert b["hidden"] == ["outline"]
    assert b["states"]["default"]["text"] == {
        "label": "Go", "size": 11.0, "color": "#112233", "bold": True, "align": "left"}
    assert b["states"]["default"]["fill"] == {"color": "#FFFFFF", "transparency": 0.0}
    assert b["states"]["hover"]["fill"] == {"color": "#FF5F02"}
    assert ex.parse_button({"visualType": "actionButton", "objects": {}}) is None


def test_button_css_merges_states_and_sanitises():
    btn = {"hidden": [], "states": {
        "default": {"fill": {"color": "#FFFFFF", "transparency": 50},
                    "text": {"size": 11, "bold": True, "align": "right", "color": "#00233C",
                             "font": "Segoe UI Semibold, sans-serif"},
                    "round": 8, "outline": {"color": "#E1E1E1", "weight": 2}},
        "hover": {"fill": {"color": "#FF5F02"}},
        "disabled": {"text": {"color": "#999999"}},
    }}
    css = dict(kv.split(":", 1) for kv in _button_css(btn).split(";"))
    assert css["--bg0"] == "rgba(255,255,255,0.50)"
    # hover only changes the colour: it inherits default's transparency and outline
    assert css["--bg-h"] == "rgba(255,95,2,0.50)" and css["--ol-h"] == css["--ol"]
    assert css["--fg-d"] == "#999999" and css["--bg-d"] == css["--bg0"]     # disabled inherits the fill
    assert css["--fs"] == "11pt" and css["--fw"] == "bold" and css["--jc"] == "flex-end"
    assert css["--ff"] == "'Segoe UI Semibold', system-ui, sans-serif" and css["--rad"] == "8px"
    assert css["--ol"] == "inset 0 0 0 2px #E1E1E1"
    # nothing from the file reaches the CSS unvalidated
    evil = {"hidden": [], "states": {"default": {"fill": {"color": "red;}</style><x>"},
                                                 "text": {"font": "a';}x", "color": "url(x)", "size": 9999}}}}
    out = _button_css(evil)
    assert "url" not in out and "<" not in out and "}" not in out and "--ff" not in out
    assert "--fs:96pt" in out                                               # clamped
    assert _button_css(None) is None


def test_button_renders_with_state_rules_in_both_templates(fake_pbix):
    layout = ex.extract_layout(fake_pbix)
    spec = semantic.load("Executive_Dashboard")
    base = layout["pages"][0]
    b = {**base["visuals"][0], "id": "btn", "type": "actionButton", "texts": {"shape_text": "Hi"},
         "button": {"hidden": [], "states": {"default": {"fill": {"color": "#FFFFFF"}},
                                             "hover": {"fill": {"color": "#FF5F02"}}}}}
    layout2 = {**layout, "pages": [{**base, "visuals": base["visuals"] + [b]}]}
    for mode in ("live", "hah"):
        html = render_html(layout2, spec, {"year": 2025}, None, mode=mode, hah_base="https://hah.example")
        assert "--bg0:#FFFFFF;--ol:none;--bg-h:#FF5F02" in html and " btn" in html, mode


def _pbir_bookmark_zip(tmp_path, state):
    """A minimal PBIR bookmark folder, as a .pbix-shaped zip."""
    import json
    import zipfile
    path = tmp_path / "b.pbix"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/definition/bookmarks/bookmarks.json",
                   json.dumps({"items": [{"name": "bm1"}]}))
        z.writestr("Report/definition/bookmarks/bm1.bookmark.json", json.dumps({
            "displayName": "By Org", "name": "bm1",
            "options": {"applyOnlyToTargetVisuals": True, "targetVisualNames": ["g1", "g2"]},
            "explorationState": {"activeSection": "sec1", "sections": {"sec1": state}}}))
    return path


def test_a_pbir_bookmark_reads_group_visibility_where_a_real_file_puts_it(tmp_path):
    """PBIR carries a group's visibility in `visualContainerGroups[*].isHidden`, exactly as the
    classic format does. The parser was written before any real PBIR file with bookmarks existed
    and guessed the per-visual `display.mode` instead; a real one (TestReport9) has no `display`
    on any container, so `groups` came back empty and every view-switcher button was inert."""
    import zipfile
    from pbix2html.extract import _parse_bookmarks_pbir
    state = {"visualContainerGroups": {"g1": {"isHidden": False}, "g2": {"isHidden": True}},
             "visualContainers": {"v1": {"singleVisual": {"visualType": "card", "objects": {}}}}}
    path = _pbir_bookmark_zip(tmp_path, state)
    with zipfile.ZipFile(path) as z:
        got = _parse_bookmarks_pbir(z, z.namelist())
    assert len(got) == 1
    assert got[0]["groups"] == {"g1": False, "g2": True}
    assert got[0]["name"] == "By Org" and got[0]["apply_only_to_targets"] is True
    assert got[0]["targets"] == ["g1", "g2"]
    # a file that *does* use the per-visual form is still honoured, and never overrides a group
    state2 = {"visualContainerGroups": {"g1": {"isHidden": True}},
              "visualContainers": {"g1": {"singleVisual": {"display": {"mode": "visible"}}},
                                   "v9": {"singleVisual": {"display": {"mode": "hidden"}}}}}
    path2 = _pbir_bookmark_zip(tmp_path / "b2", state2) if (tmp_path / "b2").mkdir() is None else None
    with zipfile.ZipFile(path2) as z:
        got2 = _parse_bookmarks_pbir(z, z.namelist())
    assert got2[0]["groups"] == {"g1": True, "v9": True}, "the group's own state must win"
