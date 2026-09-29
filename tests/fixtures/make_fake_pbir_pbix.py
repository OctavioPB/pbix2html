"""Synthetic .pbix in PBIR / Enhanced Report Format (Power BI Desktop 2024+): no
Report/Layout blob, one JSON file per page and per visual under Report/definition/.
Mirrors the structure documented in skill pbix-layout, discovered against real
.pbix files in pbix2html-fixv1.md #6."""
import json
import zipfile


def visual_json(name, vtype, roles, title=None):
    projections = {
        role: {"projections": [{"queryRef": ref, "field": {"Column": {
            "Expression": {"SourceRef": {"Entity": ref.split(".")[0]}}, "Property": ref.split(".")[1],
        }}} for ref in refs]}
        for role, refs in roles.items()
    }
    v = {
        "position": {"x": 0, "y": 0, "z": 0, "width": 300, "height": 200, "tabOrder": 0},
        "visual": {"visualType": vtype, "query": {"queryState": projections}},
    }
    if title:
        v["visualContainerObjects"] = {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]}
    return json.dumps(v)


with zipfile.ZipFile("Dashboard_PBIR.pbix", "w") as z:
    z.writestr("Report/definition/report.json", json.dumps({}))
    z.writestr("Report/definition/pages/pages.json", json.dumps({"pageOrder": ["page1"]}))
    z.writestr("Report/definition/pages/page1/page.json",
               json.dumps({"displayName": "Executive Summary", "width": 1280, "height": 720}))
    z.writestr("Report/definition/pages/page1/visuals/v1/visual.json",
               visual_json("v1", "card", {"Values": ["Sales.Net Revenue"]}, "Revenue"))
    z.writestr("Report/definition/pages/page1/visuals/v5/visual.json", json.dumps({
        "position": {"x": 0, "y": 0, "z": 0, "width": 400, "height": 60},
        "visual": {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [
            {"textRuns": [{"value": "Q3 Summary", "textStyle": {"fontWeight": "bold", "color": "#123456"}}]},
        ]}}]}},
    }))
    z.writestr("Report/definition/pages/page1/visuals/v2/visual.json",
               visual_json("v2", "clusteredBarChart",
                           {"Category": ["Region.Name"], "Y": ["Sales.Margin %"]}, "Margin by Region"))
    # malformed visual.json: must be skipped, not crash the whole extraction.
    z.writestr("Report/definition/pages/page1/visuals/v3/visual.json", "{not valid json")
    # group container: PBIR has no "visual" key at all for these, just "visualGroup".
    z.writestr("Report/definition/pages/page1/visuals/g1/visual.json",
               json.dumps({"position": {"x": 0, "y": 0, "z": 9000, "width": 1280, "height": 720},
                           "visualGroup": {"displayName": "Header group", "groupMode": "ScaleMode"}}))
    z.writestr("Report/StaticResources/SharedResources/BaseThemes/CY24SU10.json",
               json.dumps({"name": "CY24SU10", "dataColors": ["#0F2B46", "#C8102E"]}))
    z.writestr("Version", "5.0")
