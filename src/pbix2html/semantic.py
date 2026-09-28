"""
Semantic layer: metrics/<Report>.yaml.

- `scaffold(layout, model)` generates an initial yaml from the layout and the DAX
  measures. When a visual's fields all resolve to the small set of unambiguous DAX
  patterns in skill `dax-to-teradata-sql` (SUM/AVG/MIN/MAX/COUNT/COUNTROWS/
  DISTINCTCOUNT of one column, joined via model.json's own documented relationships),
  `sql` is auto-drafted instead of left as `TODO` — see `_draft_visual_sql`. Anything
  outside that (CALCULATE, DIVIDE, time intelligence, multi-hop joins) still needs a
  person; a wrong silent draft is worse than an honest blank one.
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


# ----------------------------------------------------------------------------
# Rule-based SQL auto-draft: mechanically applies the patterns from skill
# dax-to-teradata-sql (a single SUM/AVG/MIN/MAX/COUNT/COUNTROWS/DISTINCTCOUNT over one
# column, joined via model.json's own documented relationships) so most visuals get a
# working `sql` draft instead of a blank TODO. Deliberately narrow: anything outside
# these patterns (CALCULATE, DIVIDE, time intelligence, a join path longer than one
# hop, more grouping columns than a chart's contract allows, kinds with a richer
# contract like kpi/matrix/combo) falls back to the TODO stub rather than guessing —
# see the module docstring. Every draft still has to pass `validate` before anyone
# trusts it, exactly like a hand-written sql; drafted visuals get a `notes` line
# saying so, and a join draft specifically calls out the classic fan-out risk
# documented in skill validate-report ("every value × k" from a duplicating JOIN).
# ----------------------------------------------------------------------------

_DAX_AGG_RE = re.compile(
    r"^\s*(SUM|COUNTROWS|COUNTA|COUNT|DISTINCTCOUNT|AVERAGE|MIN|MAX)\s*\(\s*"
    r"(?:'([^']+)'|([A-Za-z_]\w*))"
    r"(?:\s*\[\s*([^\]]+?)\s*\])?"
    r"\s*\)\s*$"
)

# ref-level agg wrapper name (from query_ref_parts, e.g. "Sum" in "Sum(Sales.Amount)")
# → (Teradata aggregate function, needs DISTINCT)
_REF_AGG_TO_SQL: dict[str, tuple[str, bool]] = {
    "sum": ("SUM", False), "count": ("COUNT", False), "countnonnull": ("COUNT", False),
    "min": ("MIN", False), "max": ("MAX", False), "avg": ("AVG", False), "average": ("AVG", False),
    "distinctcount": ("COUNT", True),
}
# DAX function name (from a measure's own Expression text) → same, for _DAX_AGG_RE
_DAX_FUNC_TO_SQL: dict[str, tuple[str, bool]] = {
    "SUM": ("SUM", False), "COUNTROWS": ("COUNT", False), "COUNTA": ("COUNT", False),
    "COUNT": ("COUNT", False), "DISTINCTCOUNT": ("COUNT", True),
    "AVERAGE": ("AVG", False), "MIN": ("MIN", False), "MAX": ("MAX", False),
}


def _translate_measure_expression(dax: str) -> tuple[str, str, str | None, bool] | None:
    """Recognizes a single aggregation function over one column or table — nothing
    calculated or filtered (no CALCULATE, DIVIDE, time intelligence: see skill
    dax-to-teradata-sql for those, still written by hand). Returns
    (sql_function, table, column_or_None, distinct) or None if it doesn't match;
    column is None only for `COUNTROWS(Table)` → `COUNT(*)`."""
    if not dax:
        return None
    m = _DAX_AGG_RE.match(dax.strip())
    if not m:
        return None
    func, table_q, table_bare, col = m.groups()
    sql_func, distinct = _DAX_FUNC_TO_SQL[func.upper()]
    table = table_q or table_bare
    if func.upper() == "COUNTROWS":
        return sql_func, table, None, distinct
    if col is None:
        return None
    return sql_func, table, col, distinct


@dataclass
class _Field:
    role: str
    is_value: bool
    table: str
    column: str | None   # None only for a COUNTROWS-based measure
    sql_func: str | None  # None for a plain (non-aggregated) column
    distinct: bool
    out_name: str


def _resolve_field(role: str, ref: str, measures: dict[tuple[str, str], str]) -> _Field | None:
    """A ref is either already agg-wrapped by Power BI ('Sum(Sales.Amount)' — a raw
    column auto-aggregated in a Values well), a named measure (looked up in `measures`
    and its own DAX expression translated), or a plain dimension column. Returns None
    when it's a named measure whose DAX isn't one of the safe patterns above — the
    caller bails on the whole visual rather than half-drafting it."""
    agg, table, col = query_ref_parts(ref)
    if agg:
        sql_func, distinct = _REF_AGG_TO_SQL.get(agg.lower(), (None, False))
        if sql_func is None:
            return None
        return _Field(role, True, table, col, sql_func, distinct, _sql_alias(col))
    measure_dax = measures.get((table, col))
    if measure_dax is not None:
        parsed = _translate_measure_expression(measure_dax)
        if parsed is None:
            return None
        sql_func, base_table, base_col, distinct = parsed
        out = _sql_alias(base_col) if base_col else _sql_alias(col)
        return _Field(role, True, base_table, base_col, sql_func, distinct, out)
    return _Field(role, False, table, col, None, False, _sql_alias(col))


def _find_join_path(tables: list[str], relationships: list[dict]) -> list[tuple[str, str, str, str]] | None:
    """BFS spanning tree connecting every table in `tables`, using only direct,
    single-hop edges from model.json's own `relationships` (FromTable/FromColumn/
    ToTable/ToColumn) — no inferred multi-hop chains through a table that isn't
    actually needed, and no guessing when a pair genuinely has no relationship.
    Returns [(from_table, from_col, to_table, to_col), ...] in join order (the first
    table in `tables` is the FROM root), or None if they can't all be connected
    this way."""
    if len(tables) <= 1:
        return []
    edges: dict[str, list[tuple[str, str, str, str]]] = {}
    for r in relationships or []:
        ft, fc, tt, tc = r.get("FromTable"), r.get("FromColumn"), r.get("ToTable"), r.get("ToColumn")
        if not (ft and fc and tt and tc):
            continue
        edges.setdefault(ft, []).append((ft, fc, tt, tc))
        edges.setdefault(tt, []).append((tt, tc, ft, fc))

    needed = set(tables)
    root = tables[0]
    visited = {root}
    path: list[tuple[str, str, str, str]] = []
    frontier = [root]
    while frontier and visited != needed:
        nxt = []
        for t in frontier:
            for (ft, fc, tt, tc) in edges.get(t, []):
                if tt in needed and tt not in visited:
                    visited.add(tt)
                    path.append((ft, fc, tt, tc))
                    nxt.append(tt)
        frontier = nxt
    return path if visited == needed else None


_CHART_KINDS = {"bar", "column", "line", "pie"}


def _draft_visual_sql(v: dict, kind: str, measures: dict[tuple[str, str], str], table_map: dict[str, str],
                       relationships: list[dict], parameters: dict[str, dict]) -> tuple[str, list[str]] | None:
    """Full auto-draft for the visual kinds whose data contract is simple enough to
    build blind: card, pie, bar/column/line, and table (see skill html-renderer for
    the column contract each expects). Returns (sql, params_used) or None — the
    caller falls back to `_sql_stub` on None."""
    if kind not in ({"card", "table"} | _CHART_KINDS):
        return None
    fields: list[_Field] = []
    for role, refs in (v.get("projections") or {}).items():
        for ref in refs:
            f = _resolve_field(role, ref, measures)
            if f is None:
                return None
            fields.append(f)
    if not fields:
        return None

    values = [f for f in fields if f.is_value]
    categories = [f for f in fields if not f.is_value]

    if kind == "card":
        if len(fields) != 1 or not values:
            return None
        select_cols = [(values[0], "value")]
        group_positions: list[int] = []
    elif kind == "pie":
        if len(values) != 1 or len(categories) != 1:
            return None
        select_cols = [(categories[0], "category"), (values[0], "value")]
        group_positions = [1]
    elif kind in _CHART_KINDS:  # bar, column, line
        if len(values) != 1 or not (1 <= len(categories) <= 2):
            return None
        names = ["category", "series"]
        select_cols = [(c, names[i]) for i, c in enumerate(categories)] + [(values[0], "value")]
        group_positions = list(range(1, len(categories) + 1))
    else:  # table: every field becomes a column, in projection order
        select_cols = [(f, f.out_name) for f in fields]
        group_positions = [i + 1 for i, (f, _) in enumerate(select_cols) if not f.is_value] if values else []

    tables_needed = list(dict.fromkeys(f.table for f, _ in select_cols))
    if any(t not in table_map for t in tables_needed):
        return None
    try:
        # Defensive re-validation, same as _sql_stub: table_map is normally only ever
        # written through the panel's step 2b (already validated then), but a hand-
        # edited table_map.json could carry something that wouldn't have been accepted.
        validated_map = {t: validate_read_only_sql(table_map[t]) for t in tables_needed}
    except ValueError:
        return None
    join_path = _find_join_path(tables_needed, relationships)
    if join_path is None:
        return None

    aliases = {t: _sql_alias(t) for t in tables_needed}
    sources = [f"({validated_map[tables_needed[0]]}) AS {aliases[tables_needed[0]]}"]
    for ft, fc, tt, tc in join_path:
        sources.append(f"JOIN ({validated_map[tt]}) AS {aliases[tt]} "
                        f"ON {aliases[ft]}.{_sql_alias(fc)} = {aliases[tt]}.{_sql_alias(tc)}")

    select_parts = []
    for f, out_name in select_cols:
        if f.is_value:
            if f.column is None:
                expr = "COUNT(*)"
            else:
                col_ref = f"{aliases[f.table]}.{_sql_alias(f.column)}"
                expr = f"{f.sql_func}({f'DISTINCT {col_ref}' if f.distinct else col_ref})"
            select_parts.append(f"{expr} AS {out_name}")
        else:
            select_parts.append(f"{aliases[f.table]}.{_sql_alias(f.column)} AS {out_name}")

    where_parts = []
    params_used: list[str] = []
    for pname, p in parameters.items():
        slicer_ref = (p or {}).get("from_slicer")
        if not slicer_ref:
            continue
        _, ptable, pcol = query_ref_parts(slicer_ref)
        if ptable in aliases:
            # IN (...) rather than "=": works unchanged whether the parameter stays a
            # single value or someone later turns on `multi` — see query.py's bind().
            where_parts.append(f"{aliases[ptable]}.{_sql_alias(pcol)} IN (:{pname})")
            params_used.append(pname)

    sql = "SELECT " + ", ".join(select_parts) + "\nFROM " + "\n".join(sources)
    if where_parts:
        sql += "\nWHERE " + " AND ".join(where_parts)
    if group_positions:
        sql += "\nGROUP BY " + ", ".join(str(p) for p in group_positions)
    return sql, params_used


def scaffold(layout: dict, model: dict, table_map: dict[str, str] | None = None) -> dict:
    """Builds the initial yaml dict. Does not write to disk.

    `table_map` (Power BI entity name → a read-only Teradata SELECT query, see
    `validate_read_only_sql`, the panel's step 2b, and `metrics/<Report>.table_map.json`)
    is optional: when given, it's used to auto-draft a full `sql` per visual (see
    `_draft_visual_sql`) where the DAX/relationships are unambiguous enough to, and a
    best-effort `FROM (<query>) AS <alias>` stub otherwise — still a bare `TODO` with
    neither `model` nor `table_map`.
    """
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    relationships = model.get("relationships") if isinstance(model.get("relationships"), list) else []
    rls = model.get("rls") if isinstance(model.get("rls"), list) else []
    table_map = table_map or {}

    # Pass 1: parameters (from slicers), across every page — a visual auto-drafted
    # below may reference a slicer declared on a page processed later than its own.
    parameters: dict[str, dict] = {}
    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group"):
                continue
            kind = KIND_MAP.get(v["type"], "custom" if v.get("is_custom") else "unsupported")
            if kind != "slicer":
                continue
            for ref in v["fields"]:
                _, table, col = query_ref_parts(ref)
                pname = re.sub(r"\W+", "_", col).lower()
                parameters[pname] = {"type": "string", "default": None, "from_slicer": ref, "multi": False}

    # Pass 2: visuals, with the full parameter set already known.
    visuals: dict[str, dict] = {}
    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group"):
                continue
            kind = KIND_MAP.get(v["type"], "custom" if v.get("is_custom") else "unsupported")
            if kind == "slicer":
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

            notes = []
            if v.get("is_custom"):
                notes.append(f"Custom visual '{v['type']}': pick a standard kind and document the differences.")
            draft = _draft_visual_sql(v, kind, measures, table_map, relationships, parameters)
            if draft:
                entry["sql"], entry["params"] = draft
                notes.append(
                    "Auto-drafted (rule-based, see skill dax-to-teradata-sql) — review the columns "
                    "and any JOIN, then run validate before trusting it, same as a hand-written sql. "
                    "A JOIN here can duplicate rows and inflate totals if the relationship's "
                    "direction/multiplicity doesn't match what this visual needs (see skill "
                    "validate-report, \"every value × k\")."
                )
            else:
                entry["sql"] = _sql_stub(_entities_used(v), table_map)
                entry["params"] = []
            entry["reference_sql"] = None
            entry["tolerance"] = {"rel": 1.0e-6}
            if notes:
                entry["notes"] = " ".join(notes)
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
