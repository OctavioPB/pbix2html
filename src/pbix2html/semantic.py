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


def query_ref_parts(ref: str) -> tuple[str | None, str, str]:
    """'Sum(Sales.Amount)' → ('Sum', 'Sales', 'Amount'); 'Sales.Margin' → (None, 'Sales', 'Margin').

    Strip the Agg(...) wrapper *before* splitting on '.' — a numeric field dropped into
    a Values well is commonly auto-aggregated by Power BI (Sum/Count/Avg/...), and
    splitting on the first '.' without stripping that first mangles the table name
    (e.g. 'Sum(Sales.Amount)'.split(".")[0] == 'Sum(Sales', not 'Sales'). Also used by
    `gui.py`'s table-map step (panel step 2b) to list the actual Power BI table names.
    """
    agg = None
    m = _AGG_RE.match(ref)
    if m:
        agg, ref = m.group(1), m.group(2)
    table, _, col = ref.partition(".")
    return agg, table, col


# Read-only guardrail for table-map queries (panel step 2b / metrics/<Report>.table_map.json).
# This is NOT a SQL parser and isn't a security boundary against someone who already has
# Teradata access — see validate_read_only_sql's docstring. It's a guardrail against an
# accidental/careless paste ending up wired into a generated report's SQL.
_SQL_SINGLE_STATEMENT_FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL", "COMMIT", "ROLLBACK", "SET", "INTO",
)
_SQL_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(_SQL_SINGLE_STATEMENT_FORBIDDEN) + r")\b", re.IGNORECASE)
_SQL_LEADING_RE = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)
_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def validate_read_only_sql(sql: str) -> str:
    """Rejects anything but a single read-only SELECT/WITH query.

    This is a guardrail, not a SQL parser or a security boundary: it can't catch a
    read-only-looking call to a UDF/stored function with side effects, and anyone who
    already has real Teradata credentials can run whatever they want directly — this
    only stops a careless/accidental paste (a stray DELETE, a second stacked statement)
    from getting wired into a generated report through the panel's table-map step.
    Returns the query with any single trailing ';' stripped (ready to use as a
    subquery); raises ValueError with a human-readable reason otherwise.
    """
    raw = (sql or "").strip()
    if not raw:
        raise ValueError("empty query")
    if raw.endswith(";"):
        raw = raw[:-1].strip()
    if ";" in raw:
        raise ValueError("only a single SELECT statement is allowed (found a second ';')")
    uncommented = _SQL_BLOCK_COMMENT_RE.sub(" ", _SQL_LINE_COMMENT_RE.sub(" ", raw)).strip()
    if not _SQL_LEADING_RE.match(uncommented):
        raise ValueError("must start with SELECT (or WITH ... SELECT)")
    m = _SQL_FORBIDDEN_RE.search(uncommented)
    if m:
        raise ValueError(f"'{m.group(1).upper()}' isn't allowed here — read-only queries only")
    return raw


def _sql_alias(entity: str) -> str:
    alias = re.sub(r"\W+", "_", entity).strip("_").lower()
    return alias or "t"


def _entities_used(v: dict) -> list[str]:
    """Unique Power BI table names a visual's fields reference, in first-seen order.
    Goes straight to `v["projections"]`'s raw queryRefs (via `query_ref_parts`, which
    strips any Sum(...)/Avg(...)/... aggregation wrapper first) rather than re-parsing
    the human-readable `fields_doc` strings scaffold() builds for the yaml — the
    formatted "role: ref  -- DAX: ..." text is meant to be read by a person, not split
    on ':'/'.' again to recover the table name."""
    seen: list[str] = []
    for refs in (v.get("projections") or {}).values():
        for ref in refs:
            _, table, _ = query_ref_parts(ref)
            if table and table not in seen:
                seen.append(table)
    return seen


def _sql_stub(entities: list[str], table_map: dict[str, str]) -> str:
    """Best-effort SQL skeleton once the Power BI entities involved are mapped to a
    Teradata source query (panel step 2b); a bare TODO otherwise. Always needs a
    person to fill in real columns/filters — see skill dax-to-teradata-sql."""
    if not table_map:
        return "TODO -- see skill dax-to-teradata-sql; columns per kind"
    sources = []
    for entity in entities:
        query = table_map.get(entity)
        if not query:
            continue
        try:
            query = validate_read_only_sql(query)
        except ValueError:
            # A hand-edited table_map.json can carry something the panel wouldn't have
            # accepted; skip it here rather than propagate an unsafe query into the yaml.
            continue
        sources.append(f"({query}) AS {_sql_alias(entity)}")
    if not sources:
        return "TODO -- see skill dax-to-teradata-sql; columns per kind"
    return (
        f"LOCKING ROW FOR ACCESS\n"
        f"SELECT -- TODO: columns per kind (see skill html-renderer)\n"
        f"FROM {', '.join(sources)}\n"
        f"WHERE -- TODO: filters / parameters (:param)"
    )


def scaffold(layout: dict, model: dict, table_map: dict[str, str] | None = None) -> dict:
    """Builds the initial yaml dict. Does not write to disk.

    `table_map` (Power BI entity name → a read-only Teradata SELECT query, see
    `validate_read_only_sql`, the panel's step 2b, and `metrics/<Report>.table_map.json`)
    is optional: when given, it's used to pre-fill a best-effort `sql` stub per visual
    (as a `FROM (<query>) AS <alias>` subquery) instead of a bare `TODO`.
    """
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    rls = model.get("rls") if isinstance(model.get("rls"), list) else []
    table_map = table_map or {}

    parameters: dict[str, dict] = {}
    visuals: dict[str, dict] = {}
    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group"):
                continue
            kind = KIND_MAP.get(v["type"], "custom" if v.get("is_custom") else "unsupported")
            if kind == "slicer":
                for ref in v["fields"]:
                    _, table, col = query_ref_parts(ref)
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
                    agg, table, col = query_ref_parts(ref)
                    dax = measures.get((table, col))
                    fields_doc.append(f"{role}: {ref}" + (f"  -- DAX: {dax}" if dax else ""))
            entry["fields"] = fields_doc
            entry["sql"] = _sql_stub(_entities_used(v), table_map)
            entry["params"] = []
            entry["reference_sql"] = None
            entry["tolerance"] = {"rel": 1.0e-6}
            if v.get("is_custom"):
                entry["notes"] = f"Custom visual '{v['type']}': pick a standard kind and document the differences."
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
        "roles": {r.get("RoleName", "role"): {"proxy_user": None, "where": None,
                                             "dax": r.get("FilterExpression"), "table": r.get("TableName")}
                  for r in rls} or {"default": {"proxy_user": None, "where": None}},
        "visuals": visuals,
    }


YAML_HEADER = ("# Report semantic layer. Edit by hand: this is where the migrated logic lives.\n"
               "# Column contracts per kind: .claude/skills/html-renderer/SKILL.md\n")


def write_scaffold(layout: dict, model: dict, overwrite: bool = False,
                    table_map: dict[str, str] | None = None) -> Path:
    path = yaml_path(layout["report"])
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; use --overwrite to regenerate (you'll lose the written SQL)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        YAML_HEADER + yaml.safe_dump(scaffold(layout, model, table_map), allow_unicode=True, sort_keys=False, width=110),
        encoding="utf-8")
    return path


def save_raw(report: str, raw: dict) -> Path:
    """Writes an already-loaded/edited yaml dict back to metrics/<report>.yaml as-is —
    unlike write_scaffold(), this never re-derives anything from the .pbix, so it's
    safe to call after the panel's SQL/parameters/roles editor changes just a few
    keys. Callers are responsible for validating anything security-sensitive (SQL)
    before it gets here — see validate_read_only_sql."""
    path = yaml_path(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        YAML_HEADER + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, width=110),
        encoding="utf-8")
    return path


def load(report: str) -> ReportSpec:
    path = yaml_path(report)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    visuals = {}
    for vid, v in (raw.get("visuals") or {}).items():
        v = v or {}  # a visual written as "v3:" with nothing under it parses as None
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
        p = p or {}  # a parameter written as "year:" with nothing under it parses as None
        val = overrides.get(name, p.get("default"))
        if p.get("multi") and isinstance(val, str):
            val = [x.strip() for x in val.split(",") if x.strip()]
        if p.get("type") == "int" and val is not None and not isinstance(val, list):
            val = int(val)
        values[name] = val
    unknown = set(overrides) - set(spec.parameters)
    if unknown:
        raise KeyError(f"Parameters not defined in the yaml: {sorted(unknown)}")
    return values
