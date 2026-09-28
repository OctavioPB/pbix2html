import json, zipfile
def cfg(name, vtype, proj, title=None, hidden=False):
    c = {"name": name, "layouts":[{"id":0,"position":{"x":10,"y":20,"z":0,"width":300,"height":200,"tabOrder":0}}],
         "singleVisual":{"visualType":vtype,"projections":proj,"drillFilterOtherVisuals":True,
            "objects":{"dataPoint":[{}],"labels":[{}]}}}
    if title: c["singleVisual"]["vcObjects"]={"title":[{"properties":{"text":{"expr":{"Literal":{"Value":f"'{title}'"}}}}}]}
    if hidden: c["singleVisual"]["display"]={"mode":"hidden"}
    return json.dumps(c)
pf = json.dumps([
    {"name":"f1","expression":{"Column":{"Expression":{"SourceRef":{"Entity":"Calendar"}},"Property":"Year"}},
     "type":"Categorical","filter":{"Version":2,"From":[],"Where":[{"Condition":{"In":{}}}]},"isHiddenInViewMode":True},
    # TopN/advanced filter with no canonical "expression" (null instead of absent): must not break parsing.
    {"name":"f2","expression":None,"type":"TopN","filter":{"Version":2,"From":[],"Where":[]}},
])
layout = {"id":0,"resourcePackages":[{"resourcePackage":{"name":"Deneb","type":0}},{"resourcePackage":{"name":"SharedResources","type":2}}],
 "config": json.dumps({"version":"5.55","themeCollection":{"baseTheme":{"name":"CY24SU10","type":2},"customTheme":{"name":"OPB.json","type":1}}}),
 "sections":[
  {"id":0,"name":"ReportSection1","displayName":"Executive Summary","ordinal":0,"width":1280,"height":720,"filters":pf,"config":"{}",
   "visualContainers":[
     {"x":0,"y":0,"z":0,"width":200,"height":100,"filters":"[]","config":cfg("v1","card",{"Values":[{"queryRef":"Sales.Net Revenue"}]},"Revenue")},
     {"x":220,"y":0,"z":1,"width":600,"height":300,"filters":"[]","config":cfg("v2","clusteredBarChart",{"Category":[{"queryRef":"Region.Name"}],"Y":[{"queryRef":"Sales.Margin %"}]},"Margin by Region")},
     {"x":0,"y":320,"z":2,"width":300,"height":80,"filters":"[]","config":cfg("v3","slicer",{"Values":[{"queryRef":"Calendar.Year"}]})},
     {"x":0,"y":420,"z":3,"width":300,"height":200,"filters":"[]","config":cfg("v4","Deneb2B2C3F1A2","{}" and {"Values":[{"queryRef":"Sales.Net Revenue"}]},"Custom")},
     {"x":0,"y":0,"z":9,"width":10,"height":10,"config":json.dumps({"name":"g1","singleVisualGroup":{"displayName":"KPI Group"}})},
     # Field well emptied by the user but the role's key stays null (not absent).
     {"x":0,"y":520,"z":4,"width":300,"height":80,"filters":"[]","config":cfg("v6","card",{"Values":None})},
     # Visual with an unexpected shape (projections isn't a dict): must not take down the rest of the report.
     {"x":0,"y":620,"z":5,"width":300,"height":80,"filters":"[]","config":cfg("v7","card","BROKEN")},
   ]},
  {"id":1,"name":"ReportSection2","displayName":"Detail","ordinal":1,"width":1280,"height":720,"filters":"[]","config":json.dumps({"visibility":1}),
   "visualContainers":[{"x":0,"y":0,"z":0,"width":1280,"height":720,"filters":"[]","config":cfg("v5","tableEx",{"Values":[{"queryRef":"Sales.Customer"},{"queryRef":"Sales.Net Revenue"}]},hidden=True)}]}
 ]}
with zipfile.ZipFile("Executive_Dashboard.pbix","w") as z:
    z.writestr("Report/Layout", b"\xff\xfe"+json.dumps(layout).encode("utf-16-le"))
    z.writestr("Report/StaticResources/RegisteredResources/OPB.json", json.dumps({"name":"OPB","dataColors":["#0F2B46","#C8102E"]}))
    z.writestr("Version", "1.28")
