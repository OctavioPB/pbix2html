"""The packaged Claude skill in skills/pbix-to-html/.

Its extractor is a *vendored copy* of src/pbix2html/extract.py (the skill has to run in a
sandbox with no install step, so it can't import the package). A stale copy is worse than
no copy — that's exactly how the old reference/pbix_extract.py ended up silently missing
PBIR support — so the first test here fails the build the moment the two diverge.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "pbix-to-html"
VENDORED = SKILL / "scripts" / "extract_pbix.py"
SOURCE = ROOT / "src" / "pbix2html" / "extract.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vendored_extractor_is_identical_to_the_packages():
    assert VENDORED.read_text(encoding="utf-8") == SOURCE.read_text(encoding="utf-8"), (
        "skills/pbix-to-html/scripts/extract_pbix.py has drifted from "
        "src/pbix2html/extract.py. Re-copy it: the skill ships a verbatim vendored copy "
        "precisely so it can't rot the way reference/pbix_extract.py did."
    )


def test_skill_scripts_import_with_no_package_context():
    # The whole point of vendoring: these must run in a bare sandbox, so no relative
    # imports and nothing from the pbix2html package.
    for path in (VENDORED, SKILL / "scripts" / "render_html.py"):
        source = path.read_text(encoding="utf-8")
        assert "from pbix2html" not in source and "\nfrom ." not in source, path.name
    assert _load(VENDORED, "vendored_extract").extract_layout
    assert _load(SKILL / "scripts" / "render_html.py", "skill_render").render_html


def test_skill_scripts_use_only_the_standard_library():
    """No pip install is possible in the sandbox this skill runs in. pbixray is the one
    exception and it's imported lazily, inside a try, so the layout still extracts."""
    render = _load(SKILL / "scripts" / "render_html.py", "skill_render2")
    assert render.ECHARTS_CDN.startswith("https://")
    source = VENDORED.read_text(encoding="utf-8")
    top_level = [ln for ln in source.splitlines() if ln.startswith(("import ", "from "))]
    third_party = [ln for ln in top_level if "pbixray" in ln or "pandas" in ln or "jinja2" in ln]
    assert not third_party, f"top-level third-party import in the vendored script: {third_party}"


def test_skill_end_to_end_produces_a_self_contained_report(tmp_path, fake_pbix):
    """extract → build_spec → render, exactly as SKILL.md tells Claude to do it."""
    extract = _load(VENDORED, "vendored_extract2")
    render = _load(SKILL / "scripts" / "render_html.py", "skill_render3")

    layout = extract.extract_layout(fake_pbix)
    spec = render.build_spec(layout)
    assert spec["pages"] and spec["report"] == "Executive_Dashboard"
    assert spec["theme"]["data_colors"][0] == "#0F2B46"          # the .pbix's own theme
    assert all("slicer" != v["kind"] for p in spec["pages"] for v in p["visuals"])

    html = render.render_html(spec, render.demo_data(spec))
    out = tmp_path / "report.html"
    out.write_text(html, encoding="utf-8")

    assert html.lstrip().startswith("<!doctype html>")
    assert "Executive_Dashboard" in html
    assert "</script>" not in json.dumps(spec)                    # no early tag close
    for page in spec["pages"]:
        for v in page["visuals"]:
            assert f'"{v["id"]}"' in html                          # every visual shipped
    # Self-contained apart from the chart library, which is a documented CDN dependency.
    assert html.count("<script src=") == 1 and "echarts" in html


def test_skill_render_carries_style_text_and_images_through(fake_pbix):
    extract = _load(VENDORED, "vendored_extract3")
    render = _load(SKILL / "scripts" / "render_html.py", "skill_render4")
    spec = render.build_spec(extract.extract_layout(fake_pbix))
    by_id = {v["id"]: v for p in spec["pages"] for v in p["visuals"]}

    assert by_id["v1"]["style"] == {"background": "#FFEECC", "border": True,
                                    "border_color": "#FF0000"}
    assert by_id["v2"]["style"] == {}                              # no invented frame
    assert spec["pages"][0]["background"] == "#202020"
    assert "Q3 Summary" in (by_id["v8"]["text"] or "")
    assert (by_id["v9"]["image"] or "").startswith("data:image/png;base64,")


def test_skill_extractor_runs_as_a_command(tmp_path, fake_pbix):
    """SKILL.md tells Claude to shell out to it; make sure that actually works."""
    r = subprocess.run([sys.executable, str(VENDORED), str(fake_pbix), "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    layout = json.loads((tmp_path / "Executive_Dashboard" / "layout.json").read_text(encoding="utf-8"))
    assert layout["format"] == "classic" and layout["pages"]


def test_skill_renderer_runs_as_a_command(tmp_path, fake_pbix):
    extract = _load(VENDORED, "vendored_extract4")
    layout_path = tmp_path / "layout.json"
    layout_path.write_text(json.dumps(extract.extract_layout(fake_pbix), default=str), encoding="utf-8")
    out = tmp_path / "r.html"
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "render_html.py"),
                        str(layout_path), "--demo", "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.exists() and "DEMO DATA" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["SKILL.md", "reference/visual-contracts.md", "reference/dax-to-sql.md"])
def test_skill_ships_its_documentation(name):
    assert (SKILL / name).exists(), f"{name} missing from the packaged skill"


def test_skill_frontmatter_is_well_formed():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front = text.split("---", 2)[1]
    assert "name: pbix-to-html" in front
    # The description is what a model matches a request against, so it has to name both
    # the input and the job, not just the tool.
    description = next(ln for ln in front.splitlines() if ln.startswith("description:"))
    assert ".pbix" in description and "HTML" in description
    assert len(description) > 120
