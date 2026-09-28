"""
Semantic layer: metrics/<Report>.yaml.

- `scaffold(layout, model)` generates an initial yaml from the layout and the DAX
  measures, with `sql: TODO` for a person (assisted by Claude) to fill in.
- `load(report)` loads and validates the yaml for query/render/validate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

METRICS_DIR = Path("metrics")

# Power BI visualType → renderer kind (see skill html-renderer)
KIND_MAP: dict[str, str] = {
    "card": "card", "cardVisual": "card", "kpi": "kpi", "multiRowCard": "multicard",
    "clusteredBarChart": "bar", "barChart": "bar", "stackedBarChart": "bar",
    "hundredPercentStackedBarChart": "bar",
    "clusteredColumnChart": "column", "columnChart": "column", "stackedColumnChart": "column",
    "hundredPercentStackedColumnChart": "column",
    "lineChart": "line", "areaChart": "line", "stackedAreaChart": "line",
    "lineClusteredColumnComboChart": "combo", "lineStackedColumnComboChart": "combo",
    "pieChart": "pie", "donutChart": "pie",
    "table": "table", "tableEx": "table", "pivotTable": "matrix", "matrix": "matrix",
    "slicer": "slicer", "advancedSlicerVisual": "slicer", "listSlicer": "slicer",
    "textbox": "text", "image": "static", "shape": "static", "basicShape": "static",
    "actionButton": "static", "gauge": "gauge",
}
NO_DATA_KINDS = {"slicer", "text", "static"}

_AGG_RE = re.compile(r"^(Sum|Count|CountNonNull|Min|Max|Avg|Average|DistinctCount)\((.+)\)$")


@dataclass
class VisualSpec:
    id: str
    kind: str
    title: str | None
    sql: str | None
    params: list[str] = field(default_factory=list)
    reference_sql: str | None = None
    format: dict[str, str] = field(default_factory=dict)
    tolerance: dict[str, float] = field(default_factory=lambda: {"rel": 1e-6})
    sort: str | None = None
    notes: str | None = None

    @property
    def has_data(self) -> bool:
        return self.kind not in NO_DATA_KINDS and bool(self.sql) and "TODO" not in self.sql


@dataclass
class ReportSpec:
    report: str
    source: str | None
    connection: str
    delivery: str
    parameters: dict[str, dict[str, Any]]
    roles: dict[str, dict[str, Any]]
    visuals: dict[str, VisualSpec]
    raw: dict[str, Any]


def yaml_path(report: str) -> Path:
    return METRICS_DIR / f"{report}.yaml"


def _query_ref_parts(ref: str) -> tuple[str | None, str, str]:
    """'Sum(Sales.Amount)' → ('Sum', 'Sales', 'Amount'); 'Sales.Margin' → (None, 'Sales', 'Margin')."""
    agg = None
    m = _AGG_RE.match(ref)
    if m:
        agg, ref = m.group(1), m.group(2)
    table, _, col = ref.partition(".")
    return agg, table, col


def scaffold(layout: dict, model: dict) -> dict:
    """Builds the initial yaml dict. Does not write to disk."""
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    rls = model.get("rls") if isinstance(model.get("rls"), list) else []

    parameters: dict[str, dict] = {}
    visuals: dict[str, dict] = {}
    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group"):
                continue
            kind = KIND_MAP.get(v["type"], "custom" if v.get("is_custom") else "unsupported")
            if kind == "slicer":
                for ref in v["fields"]:
                    _, table, col = _query_ref_parts(ref)
                    pname = re.sub(r"\W+", "_", col).lower()
                    parameters[pname] = {"type": "string", "default": None, "from_slicer": ref, "multi": False}
                continue
            entry: dict[str, Any] = {"kind": kind, "page": page["display_name"], "title": v.get("title")}
            if kind in NO_DATA_KINDS:
                visuals[v["id"]] = entry
                continue
            # Inventory of fields and DAX of the measures involved, as a guide for writing the SQL.
            fields_doc = []
            for role, refs in v["projections"].items():
                for ref in refs:
                    agg, table, col = _query_ref_parts(ref)
                    dax = measures.get((table, col))
                    fields_doc.append(f"{role}: {ref}" + (f"  -- DAX: {dax}" if dax else ""))
            entry["fields"] = fields_doc
            entry["sql"] = "TODO -- ver skill dax-to-teradata-sql; columnas según kind"
            entry["params"] = []
            entry["reference_sql"] = None
            entry["tolerance"] = {"rel": 1.0e-6}
            if v.get("is_custom"):
                entry["notes"] = f"Custom visual '{v['type']}': elegir kind estándar y documentar diferencias."
            if v.get("filters"):
                entry["visual_filters"] = [f"{f['target']} ({f['type']})" for f in v["filters"]]
            visuals[v["id"]] = entry

    return {
        "report": layout["report"],
        "source": layout.get("source"),
        "connection": "teradata",
        "delivery": "snapshot",  # snapshot | live  (ADR-001)
        "page_filters": [f"{f['target']} ({f['type']})" for p in layout["pages"] for f in p["filters"]],
        "parameters": parameters,
        "roles": {r.get("RoleName", "rol"): {"proxy_user": None, "where": None,
                                             "dax": r.get("FilterExpression"), "table": r.get("TableName")}
                  for r in rls} or {"default": {"proxy_user": None, "where": None}},
        "visuals": visuals,
    }


def write_scaffold(layout: dict, model: dict, overwrite: bool = False) -> Path:
    path = yaml_path(layout["report"])
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} ya existe; usa --overwrite para regenerar (perderás el SQL escrito)")
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ("# Capa semántica del reporte. Editar a mano: aquí vive la lógica migrada.\n"
              "# Contratos de columnas por kind: .claude/skills/html-renderer/SKILL.md\n")
    path.write_text(header + yaml.safe_dump(scaffold(layout, model), allow_unicode=True, sort_keys=False, width=110),
                    encoding="utf-8")
    return path


def load(report: str) -> ReportSpec:
    path = yaml_path(report)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    visuals = {}
    for vid, v in (raw.get("visuals") or {}).items():
        visuals[str(vid)] = VisualSpec(
            id=str(vid), kind=v.get("kind", "unsupported"), title=v.get("title"), sql=v.get("sql"),
            params=list(v.get("params") or []), reference_sql=v.get("reference_sql"),
            format=dict(v.get("format") or {}), tolerance=dict(v.get("tolerance") or {"rel": 1e-6}),
            sort=v.get("sort"), notes=v.get("notes"),
        )
    return ReportSpec(
        report=raw["report"], source=raw.get("source"), connection=raw.get("connection", "teradata"),
        delivery=raw.get("delivery", "snapshot"), parameters=raw.get("parameters") or {},
        roles=raw.get("roles") or {}, visuals=visuals, raw=raw,
    )


def resolve_params(spec: ReportSpec, overrides: dict[str, str]) -> dict[str, Any]:
    """Final parameter values: yaml default overridden by --params k=v."""
    values: dict[str, Any] = {}
    for name, p in spec.parameters.items():
        val = overrides.get(name, p.get("default"))
        if p.get("multi") and isinstance(val, str):
            val = [x.strip() for x in val.split(",") if x.strip()]
        if p.get("type") == "int" and val is not None and not isinstance(val, list):
            val = int(val)
        values[name] = val
    unknown = set(overrides) - set(spec.parameters)
    if unknown:
        raise KeyError(f"Parámetros no definidos en el yaml: {sorted(unknown)}")
    return values
