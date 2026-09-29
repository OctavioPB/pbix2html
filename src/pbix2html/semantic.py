"""
Semantic layer: metrics/<Report>.yaml.

- `scaffold(layout, model)` generates an initial yaml from the layout and the DAX
  measures, auto-drafting each visual's `sql` wherever it can rather than leaving a
  `TODO`. `translate_dax` handles the aggregates plus the things measures are actually
  built from — DIVIDE, arithmetic, references to other measures, CALCULATE with column
  filters — and `_draft_visual_sql` assembles those into the columns each visual kind
  expects, joining via model.json's own relationships. Time intelligence, ALL/
  ALLSELECTED, virtual tables and iterators are refused on purpose and left for a
  person: a wrong silent draft is worse than an honest blank one.
- `load(report)` loads and validates the yaml for query/render/validate.
"""
from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime
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
    "dynamicTooltip": "tooltip",     # custom visual: an info icon whose text comes from a table
}
NO_DATA_KINDS = {"slicer", "text", "static", "tooltip"}

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


def readiness(spec: "ReportSpec") -> dict[str, Any]:
    """How much of this report is actually finished, as counts a non-technical person
    can act on: {needs_sql, ready, drafted, todo, decorative, percent}.

    `ready` is every visual with usable SQL; `drafted` is the subset of those the
    auto-drafter wrote (so still unreviewed); `todo` is what a person still has to
    write by hand. This is the number that answers "is this report a quick job or does
    it need scheduling with engineering", which nothing in the tool used to say until
    someone opened the yaml and counted TODOs themselves."""
    decorative = sum(1 for v in spec.visuals.values() if v.kind in NO_DATA_KINDS)
    needs_sql = [v for v in spec.visuals.values() if v.kind not in NO_DATA_KINDS]
    ready = [v for v in needs_sql if v.has_data]
    drafted = [v for v in ready if "Auto-drafted" in (v.notes or "")]
    return {
        "needs_sql": len(needs_sql),
        "ready": len(ready),
        "drafted": len(drafted),
        "todo": len(needs_sql) - len(ready),
        "decorative": decorative,
        "percent": round(100 * len(ready) / len(needs_sql)) if needs_sql else 100,
    }


def theme_path(report: str) -> Path:
    return METRICS_DIR / f"{report}.theme.json"


# Keys render.py's resolve_theme() actually reads. A pasted/uploaded theme JSON may be a
# full official Power BI theme export (which also carries deep `visualStyles` formatting
# rules we have no renderer for) — those extra keys are silently dropped rather than
# stored, so this file's purpose stays legible: it only ever affects colors and font,
# nothing else. `fontFamily` is a flat convenience key for the panel's manual form; a
# real Power BI export instead nests it under textClasses.title.fontFace, which
# resolve_theme() also checks (see render.py).
_THEME_COLOR_KEYS = ("background", "foreground", "foregroundNeutralSecondary", "backgroundNeutral")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{3,8}$")


def validate_theme_override(data: Any) -> dict:
    """Validates a theme override — either a full Power BI theme export or just the
    handful of fields the panel's manual form fills in. Raises ValueError with a
    human-readable reason (surfaced directly in the panel); returns a cleaned dict
    with only the recognized keys, same guardrail spirit as validate_read_only_sql.
    """
    if not isinstance(data, dict):
        raise ValueError("theme must be a JSON object")
    out: dict[str, Any] = {}
    if data.get("dataColors") is not None:
        colors = data["dataColors"]
        if not isinstance(colors, list) or not colors:
            raise ValueError("dataColors must be a non-empty list of hex colors")
        for c in colors:
            if not isinstance(c, str) or not _HEX_RE.match(c):
                raise ValueError(f"'{c}' in dataColors isn't a valid hex color (e.g. #D85A30)")
        out["dataColors"] = list(colors)
    for key in _THEME_COLOR_KEYS:
        val = data.get(key)
        if val:
            if not isinstance(val, str) or not _HEX_RE.match(val):
                raise ValueError(f"'{key}' must be a hex color (e.g. #FFFFFF), got {val!r}")
            out[key] = val
    if data.get("fontFamily"):
        if not isinstance(data["fontFamily"], str):
            raise ValueError("fontFamily must be a string")
        out["fontFamily"] = data["fontFamily"]
    if not out:
        raise ValueError("no recognized fields found — expected one or more of: dataColors, "
                          "background, foreground, foregroundNeutralSecondary, backgroundNeutral, fontFamily")
    return out


def load_theme_override(report: str) -> dict | None:
    """None if there's no override file, or if it's gone stale/invalid — a broken
    metrics/<report>.theme.json must not take down `convert`, same as a bad yaml
    doesn't take down the panel's report page (see gui.py's _load_spec)."""
    path = theme_path(report)
    if not path.exists():
        return None
    try:
        return validate_theme_override(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError):
        return None


def apply_theme_override(layout: dict, override: dict | None) -> dict:
    """Merges an override on top of whatever theme the .pbix itself carries (override
    wins field by field) — render.py's resolve_theme() doesn't change at all, it just
    sees a richer custom_json than what the .pbix alone had. Returns a new layout dict;
    doesn't mutate the caller's."""
    if not override:
        return layout
    theme = dict(layout.get("theme") or {})
    custom_json = dict(theme.get("custom_json") or {})
    custom_json.update(override)
    theme = {**theme, "custom_json": custom_json}
    return {**layout, "theme": theme}


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
_SQL_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_SQL_LEADING_RE = re.compile(r"^\s*(SELECT|SEL|WITH)\b", re.IGNORECASE)
_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _sql_scan(sql: str) -> tuple[str, list[int]]:
    """`(code, positions)`: `sql` with everything that is not code blanked out — 'string
    literals' (with their '' escape), "quoted identifiers", `-- line` and `/* block */` comments —
    and, for every character kept, its index in the original. One left-to-right scan, so a `--`
    inside a literal is not taken for a comment and an apostrophe inside a comment does not open
    a literal: what remains is what the database would actually parse as statements and
    keywords. An unterminated literal or comment swallows the rest, as it would there."""
    out: list[str] = []
    pos: list[int] = []
    i, n = 0, len(sql)

    def emit(text: str, at: int) -> None:
        out.append(text)
        pos.extend([at] * len(text))

    while i < n:
        ch = sql[i]
        if ch in "'\"":
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:       # doubled quote: an escaped quote
                        j += 2
                        continue
                    break
                j += 1
            emit(ch * 2, i)
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            emit(" ", i)
            i = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            emit(" ", i)
            i = n if j == -1 else j + 2
        else:
            emit(ch, i)
            i += 1
    return "".join(out), pos


def _sql_code_only(sql: str) -> str:
    return _sql_scan(sql)[0]


def validate_read_only_sql(sql: str) -> str:
    """Rejects anything but a single read-only SELECT/WITH query.

    This is a guardrail, not a SQL parser or a security boundary: it can't catch a
    read-only-looking call to a UDF/stored function with side effects, and anyone who
    already has real Teradata credentials can run whatever they want directly — this
    only stops a careless/accidental paste (a stray DELETE, a second stacked statement)
    from getting wired into a generated report through the panel's table-map step.
    Statements and keywords are looked for only in the code (see `_sql_code_only`), so a
    ';' or the word SET inside a text value is data, not a second statement.
    Returns the query with any single trailing ';' stripped (ready to use as a
    subquery); raises ValueError with a human-readable reason otherwise.
    """
    raw = (sql or "").strip()
    if not raw:
        raise ValueError("empty query")
    code, pos = _sql_scan(raw)
    stripped = code.rstrip()
    if stripped.endswith(";"):                   # a single trailing ';' is dropped, from the text too
        at = pos[len(stripped) - 1]
        raw = (raw[:at] + raw[at + 1:]).strip()
        code = stripped[:-1]
    code = code.strip()
    if ";" in code:
        raise ValueError("only a single SELECT statement is allowed (found a second ';')")
    if not _SQL_LEADING_RE.match(code):
        raise ValueError("must start with SELECT/SEL (or WITH ... SELECT)")
    m = _SQL_FORBIDDEN_RE.search(code)
    if m:
        raise ValueError(f"'{m.group(1).upper()}' isn't allowed here — read-only queries only")
    return raw


_UNWRITTEN_SQL_PREFIXES = ("TODO", "LOCKING ROW FOR ACCESS")  # exactly what _sql_stub generates


def is_unwritten_sql(sql: str) -> bool:
    """True when `sql` is empty or is still exactly one of the placeholder shapes
    `_sql_stub` itself generates (a bare "TODO ..." or the table-map-prefilled
    "LOCKING ROW FOR ACCESS ..." skeleton). The panel's edit page (step 2c) uses this
    to let a visual stay a work-in-progress without forcing `validate_read_only_sql`
    to pass on every single save.

    Two earlier, broader versions of this check were both exploitable and got tightened
    here on review:
    - "contains the word TODO anywhere" let any real, complete SQL with a stray review
      comment mentioning it (e.g. "-- TODO: double-check this join", exactly what the
      auto-drafter's own notes invite someone to add) skip validate_read_only_sql
      entirely and get saved as-is.
    - "doesn't start with SELECT/WITH" is just as wide open: a bare `DELETE FROM x`
      doesn't start with SELECT either, so it would *also* get the free pass and be
      saved unvalidated.
    Both would then be executed as-is by query.py against a real Teradata connection —
    a hole in the "guardrail against a careless paste" for precisely the case it
    exists to catch. Matching only the exact, known stub prefixes closes both: nothing
    that isn't literally what this project itself wrote gets to skip validation."""
    raw = (sql or "").strip()
    if not raw:
        return True
    return raw.startswith(_UNWRITTEN_SQL_PREFIXES)


# ----------------------------------------------------------------------------
# Table-map auto-detection from each table's own Power Query M source
# (model.json["power_query"], captured by extract_model() via pbixray but otherwise
# unused). Mechanical pattern matching, same spirit as the DAX auto-draft in
# _draft_visual_sql: only the two M shapes below are recognized, anything else
# (a merge, a filter step, a dynamically-built query string) still needs a person —
# a wrong silent guess here is worse than an honest blank table-map entry.
# ----------------------------------------------------------------------------

_M_NATIVE_QUERY_RE = re.compile(r"Value\.NativeQuery\s*\(", re.IGNORECASE)
# `Teradata.Database("host", [HierarchicalNavigation=true, Query="select ..."])`: the
# connector's own `Query` option (what the "SQL statement" box in Get Data produces).
_M_DATABASE_CALL_RE = re.compile(r"\b\w+\.Database\s*\(", re.IGNORECASE)
_M_QUERY_OPTION_RE = re.compile(r"\bQuery\s*=\s*", re.IGNORECASE)
_M_LET_IN_RE = re.compile(r"^\s*let\b(.*?)\bin\b(.*)$", re.IGNORECASE | re.DOTALL)
_M_ACCESSOR_STEP_RE = re.compile(
    r'^\w+\s*=\s*\w+\s*\{\s*\[\s*(?:Schema\s*=\s*"([^"]*)"\s*,\s*)?'
    r'(?:Item|Name|Table)\s*=\s*"([^"]*)"\s*\]\s*\}\s*\[\s*Data\s*\]$',
    re.IGNORECASE,
)


def _m_split_args(text: str) -> list[str]:
    """Splits M function-call arguments at top-level commas, respecting nested
    (), [], {} and "..." string literals (M's own "" escape for a literal quote
    inside a string) — a plain split(",") breaks the moment a nested connector
    expression or record literal has a comma of its own. `text` starts right
    after the call's opening '(' ; stops at that call's matching ')'."""
    args: list[str] = []
    depth = 0
    in_string = False
    current: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    current.append('""')
                    i += 2
                    continue
                in_string = False
            current.append(ch)
        elif ch == '"':
            in_string = True
            current.append(ch)
        elif ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            if depth == 0:
                break  # this call's own closing paren
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        args.append("".join(current))
    return [a.strip() for a in args]


def _m_string_literal_end(text: str) -> int | None:
    """Index just past the closing quote of the M string literal that starts `text`, or
    None if `text` doesn't start with one or the literal is followed by `&` (concatenation)."""
    if not text.startswith('"'):
        return None
    i, n = 1, len(text)
    while i < n:
        if text[i] == '"':
            if i + 1 < n and text[i + 1] == '"':
                i += 2
                continue
            tail = text[i + 1:].lstrip()
            return None if tail.startswith("&") else i + 1
        i += 1
    return None


def _m_unescape_string(literal: str) -> str | None:
    """A quoted M string literal (including the surrounding quotes) → its real
    value: "" is an escaped quote, #(lf)/#(cr,lf)/#(cr)/#(tab) are M's escape
    sequences for the corresponding whitespace. Returns None if it isn't actually
    a simple quoted literal (e.g. the query is built with & concatenation or a
    parameter instead) — this deliberately doesn't evaluate M expressions."""
    s = literal.strip()
    if len(s) < 2 or not s.startswith('"') or not s.endswith('"'):
        return None
    inner = s[1:-1].replace('""', '"')
    inner = re.sub(r"#\(cr,\s*lf\)", "\n", inner)
    inner = inner.replace("#(lf)", "\n").replace("#(cr)", "\n").replace("#(tab)", "\t")
    return inner


_M_FROMROWS_RE = re.compile(
    r'Table\.FromRows\s*\(\s*Json\.Document\s*\(\s*Binary\.Decompress\s*\(\s*Binary\.FromText\s*\('
    r'\s*"([A-Za-z0-9+/=\s]+)"\s*,\s*BinaryEncoding\.Base64\s*\)\s*,\s*Compression\.Deflate\s*\)\s*\)'
    r'\s*,(.*)\)\s*(?:,|in\b|$)', re.DOTALL)
_M_TABLE_TYPE_RE = re.compile(r"type\s+table\s*\[(.*?)\]", re.DOTALL)
_INLINE_MAX_ROWS = 500


def _inline_table_data(expression: str) -> tuple[list[str], list[bool], list[list]] | None:
    """Decodes an "Enter Data" table (rows embedded in the M: `Table.FromRows(Json.Document(
    Binary.Decompress(Binary.FromText("<base64>", ...), Compression.Deflate)), type table [a = _t,
    ...])`) into (column names, numeric flags, rows). None for anything else (too many rows, a
    ragged row, an unrecognised column list)."""
    m = _M_FROMROWS_RE.search(expression or "")
    if not m:
        return None
    try:
        rows = json.loads(zlib.decompress(base64.b64decode(re.sub(r"\s+", "", m.group(1))), -15).decode("utf-8-sig"))
    except Exception:
        return None
    tm = _M_TABLE_TYPE_RE.search(m.group(2))
    if not isinstance(rows, list) or not rows or len(rows) > _INLINE_MAX_ROWS or not tm:
        return None
    cols = [(c.group(1).strip(), c.group(2)) for c in
            re.finditer(r'(#"[^"]+"|[A-Za-z_]\w*)\s*=\s*([^,\]]+)', tm.group(1))]
    if not cols or any(not isinstance(r, list) or len(r) != len(cols) for r in rows):
        return None
    names = [c.strip().removeprefix('#"').removesuffix('"') for c, _ in cols]
    numeric = [bool(re.search(r"number|Int64|Currency|Decimal|Double", t)) for _, t in cols]
    return names, numeric, rows


# Teradata error 3888: every SELECT of a UNION must reference a table, so a literal-only arm gets a one-row source
_ONE_ROW = " FROM (SELECT 1 AS one) AS one_row"


def _inline_table_sql(expression: str) -> str | None:
    """The rows of an "Enter Data" table (`_inline_table_data`) rebuilt as `SELECT ... UNION ALL
    SELECT ...`, so the table needs no Teradata source."""
    data = _inline_table_data(expression)
    if data is None:
        return None
    names, numeric, rows = data

    def lit(value: Any, is_num: bool) -> str:
        if value is None:
            return "NULL"
        if is_num and isinstance(value, (int, float)) and not isinstance(value, bool):
            return repr(value)
        return "'" + str(value).replace("'", "''") + "'"

    widths = [max((len(str(r[i])) for r in rows if r[i] is not None), default=1) for i in range(len(names))]
    arms = []
    for n, row in enumerate(rows):
        parts = []
        for i, value in enumerate(row):
            text = lit(value, numeric[i])
            if n == 0:   # the first arm fixes each column's type
                text = f"CAST({text} AS {'DECIMAL(18,6)' if numeric[i] else f'VARCHAR({max(widths[i], 1)})'})"
            parts.append(f"{text} AS {_sql_col(names[i])}" if n == 0 else text)
        arms.append("SELECT " + ", ".join(parts) + _ONE_ROW)
    return "\nUNION ALL\n".join(arms)


def _detect_table_query(expression: str) -> str | None:
    """Recognizes two M source shapes for a table backed by Teradata (or any
    connector using the same accessor conventions) and returns a candidate
    read-only SQL query, or None if the M is anything more involved:

    1. `Value.NativeQuery(<connection>, "<SQL>", ...)` — the literal query Power
       Query itself sends. Lifted verbatim (still goes through
       validate_read_only_sql before being trusted anywhere) — matched anywhere
       in the expression since later cosmetic M steps (renaming/retyping a
       column) don't change what the query itself already selected.
    2. `let Source = <connector>, X = Source{[Schema="A", Item="B"]}[Data] in X`
       — a plain table reference with no further M transformation — turned into
       `SELECT * FROM A.B`. Unlike (1), this one requires the accessor step to
       be the *only* other step in the `let`: a table built by joining/merging
       two source tables (`Table.NestedJoin`, an extra filter step, ...) also
       contains a `Source{[Schema=...,Item=...]}[Data]` substring for one of its
       inputs, and reporting just that one input as the whole table would be a
       wrong, misleading answer — worse than leaving it blank.
    """
    if not expression:
        return None
    if "Table.FromRows" in expression:
        return _inline_table_sql(expression)
    dm = _M_DATABASE_CALL_RE.search(expression)
    if dm and not _M_NATIVE_QUERY_RE.search(expression):
        args = _m_split_args(expression[dm.end():])
        # options record is the 2nd argument; the SQL must be a plain string literal
        # (a `&`-concatenated or parameterized Query is not evaluated: left blank).
        qm = _M_QUERY_OPTION_RE.search(args[1]) if len(args) >= 2 else None
        if qm:
            rest = args[1][qm.end():]
            end = _m_string_literal_end(rest)
            sql = _m_unescape_string(rest[:end]) if end is not None else None
            return sql.strip() if sql and sql.strip() else None
        # no Query option: plain table-accessor shape, handled below
    m = _M_NATIVE_QUERY_RE.search(expression)
    if m:
        args = _m_split_args(expression[m.end():])
        if len(args) >= 2:
            sql = _m_unescape_string(args[1])
            if sql and sql.strip():
                return sql.strip()
        return None
    m2 = _M_LET_IN_RE.match(expression.strip())
    if m2:
        steps = _m_split_args(m2.group(1))
        if len(steps) == 2:
            am = _M_ACCESSOR_STEP_RE.match(steps[1].strip())
            if am:
                schema, item = am.group(1), am.group(2)
                if item:
                    return f"SELECT * FROM {schema + '.' if schema else ''}{item}"
    return None


def detect_table_map_from_power_query(model: dict) -> dict[str, str]:
    """Best-effort table_map entries auto-derived from each table's own Power
    Query M source — see `_detect_table_query` for exactly what's recognized.
    Only ever a suggestion, exactly like a hand-entered mapping: every value here
    has already passed `validate_read_only_sql`, but a caller still has to choose
    to save it (gui.py pre-fills the table-map form with these rather than
    silently trusting them)."""
    rows = model.get("power_query")
    if not isinstance(rows, list):
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        table = row.get("TableName")
        expr = row.get("Expression")
        if not table or table in out or not isinstance(expr, str):
            continue
        candidate = _detect_table_query(expr)
        if not candidate:
            continue
        try:
            out[table] = validate_read_only_sql(candidate)
        except ValueError:
            continue
    return out


# ----------------------------------------------------------------------------
# DAX calendar tables: `Calendar = CALENDAR("2017-01-01", NOW())` (+ calculated columns) is
# rebuilt from Teradata's `sys_calendar.calendar`. The report only needs it to filter by date,
# and the fact tables carry the date as `log_dt`; that join is proposed (never applied
# silently) in metrics/<Report>.relationships.json. Only the exact patterns below are
# recognised; anything else keeps the table unmapped rather than guessed.
# ----------------------------------------------------------------------------

# Power BI's automatic date/time helper tables: noise, never a real calendar or a real table
_AUTO_DATE_TABLE_RE = re.compile(r"^(LocalDateTable|DateTableTemplate)_")
DATE_KEY_COLUMNS = ("log_dt",)     # the fact-table date column a calendar joins to (owner-confirmed)
_DAX_CALENDAR_RE = re.compile(r"^\s*CALENDAR\s*\(", re.IGNORECASE)
_DAX_COL_REF = r"(?:'[^']+'|\w+)?\[([^\]]+)\]"
# DAX FORMAT tokens → Teradata TO_CHAR elements; names get TRIM (they are blank-padded)
_FORMAT_TOKENS = (("MMMM", ("Month", True)), ("MMM", ("Mon", True)), ("MM", ("MM", False)),
                  ("YYYY", ("YYYY", False)), ("YY", ("YY", False)), ("DDDD", ("Day", True)),
                  ("DDD", ("Dy", True)), ("DD", ("DD", False)))


def _dax_date_bound(text: str) -> tuple[str | None, str | None]:
    """A DAX date argument → (Teradata expression, note). `NOW()`/`TODAY()` → CURRENT_DATE,
    "YYYY-MM-DD", "MM/DD/YYYY" (US order assumed when both parts are <= 12: noted) and
    DATE(y, m, d) → DATE literals. Anything else → (None, None)."""
    t = text.strip()
    if re.fullmatch(r"(NOW|TODAY)\s*\(\s*\)", t, re.IGNORECASE):
        return "CURRENT_DATE", None
    m = re.fullmatch(r'"(\d{4})-(\d{2})-(\d{2})"', t)
    if m:
        return f"DATE '{m.group(1)}-{m.group(2)}-{m.group(3)}'", None
    m = re.fullmatch(r'"(\d{1,2})/(\d{1,2})/(\d{4})"', t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        month, day = (b, a) if a > 12 else (a, b)
        note = "assumed MM/DD/YYYY" if a <= 12 and b <= 12 and a != b else None
        return f"DATE '{y}-{month:02d}-{day:02d}'", note
    m = re.fullmatch(r"DATE\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)", t, re.IGNORECASE)
    if m:
        return f"DATE '{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'", None
    return None, None


def _dax_format_sql(fmt: str, date_expr: str) -> str | None:
    """DAX FORMAT(date, "<fmt>") → a Teradata string expression, or None if the format uses
    anything beyond the tokens above and space, '-', '/', ','. Pieces are concatenated so a
    blank-padded month name never leaves gaps inside the result."""
    pieces: list[str] = []
    i = 0
    prev_hour = False                       # `mm` right after hours (or before seconds) is minutes
    low = fmt.lower()
    if "am/pm" in low or "a/p" in low:
        return None                         # 12-hour clocks aren't translated
    while i < len(fmt):
        rest = low[i:]
        if rest.startswith("hh") or rest.startswith("h"):
            n = 2 if rest.startswith("hh") else 1
            pieces.append(f"TO_CHAR({date_expr}, 'HH24')" if n == 2 else f"CAST(EXTRACT(HOUR FROM {date_expr}) AS VARCHAR(2))")
            i += n
            prev_hour = True
            continue
        if rest.startswith("ss"):
            pieces.append(f"TO_CHAR({date_expr}, 'SS')")
            i += 2
            prev_hour = False
            continue
        if rest.startswith("nn") or (rest.startswith("mm") and (prev_hour or re.match(r"mm\s*:\s*ss", rest))):
            pieces.append(f"TO_CHAR({date_expr}, 'MI')")
            i += 2
            prev_hour = False
            continue
        for token, (element, is_name) in _FORMAT_TOKENS:
            if fmt[i:i + len(token)].upper() == token:
                one = f"TO_CHAR({date_expr}, '{element}')"
                pieces.append(f"TRIM({one})" if is_name else one)
                i += len(token)
                prev_hour = False
                break
        else:
            if fmt[i] not in " -/,:":
                return None
            pieces.append("'" + fmt[i] + "'")
            i += 1
    return " || ".join(pieces) if pieces else None


class _CalendarUnsupported(Exception):
    pass


_CAL_TOKEN_RE = re.compile(
    r"""\s*(?:(?P<str>"(?:[^"]|"")*")|(?P<num>\d+(?:\.\d+)?)|(?P<ref>(?:'[^']+'|\w+)?\[[^\]]+\])"""
    r"""|(?P<op>&&|\|\||<>|<=|>=|==|[=<>+\-*/&(),])|(?P<name>[A-Za-z_]\w*))""")
_SQL_STRING_RE = re.compile(r"^'(?:[^']|'')*'$")


def _calendar_expr_sql(expr: str, date_col: str, date_expr: str,
                       siblings: dict[str, str] | None = None, _stack: tuple = ()) -> str | None:
    """One DAX calculated column of a calendar table → a Teradata expression, or None.

    Recognised: FORMAT(date, "fmt"), YEAR/MONTH/DAY, TODAY()/NOW(), VALUE, IF, CONCATENATE and
    `&` (operands cast to text), ENDOFMONTH/STARTOFMONTH/EOMONTH, CEILING(x, 1), `VAR ... RETURN`,
    literals, comparisons (`=` and `==`), && / ||, + - * / (division is exact: DAX divides as
    decimals, SQL integers would truncate), parentheses, and references to the calendar's own
    date column or to its *other calculated columns* (inlined; cycles refused). Everything else
    is refused and that column is left out."""
    siblings = siblings or {}
    text = re.sub(r"\s+", " ", expr or "").strip()
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _CAL_TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            return None
        pos = m.end()
        kind = m.lastgroup
        if kind:
            tokens.append((kind, m.group(kind)))
    tokens.append(("end", ""))
    i = 0
    env: dict[str, str] = {}

    def peek() -> tuple[str, str]:
        return tokens[i]

    def take(value: str | None = None) -> tuple[str, str]:
        nonlocal i
        tok = tokens[i]
        if value is not None and tok[1].lower() != value.lower():
            raise _CalendarUnsupported(f"expected {value}")
        i += 1
        return tok

    def is_kw(word: str) -> bool:
        return peek()[0] == "name" and peek()[1].lower() == word

    def binary(parse_next, ops: dict[str, str]) -> str:
        left = parse_next()
        while peek()[0] == "op" and peek()[1] in ops:
            op = ops[take()[1]]
            left = f"({left} {op} {parse_next()})"
        return left

    def text_of(sql: str) -> str:
        return sql if _SQL_STRING_RE.match(sql) else f"CAST({sql} AS VARCHAR(50))"

    def parse_or() -> str:
        return binary(parse_and, {"||": "OR"})

    def parse_and() -> str:
        return binary(parse_cmp, {"&&": "AND"})

    def parse_cmp() -> str:
        left = parse_concat()
        if peek()[0] == "op" and peek()[1] in ("=", "==", "<>", "<", ">", "<=", ">="):
            op = take()[1]
            return f"({left} {'=' if op == '==' else op} {parse_concat()})"
        return left

    def parse_concat() -> str:
        parts = [parse_add()]
        while peek() == ("op", "&"):
            take()
            parts.append(parse_add())
        return parts[0] if len(parts) == 1 else "(" + " || ".join(text_of(p) for p in parts) + ")"

    def parse_add() -> str:
        return binary(parse_mul, {"+": "+", "-": "-"})

    def parse_mul() -> str:
        left = parse_primary()
        while peek()[0] == "op" and peek()[1] in ("*", "/"):
            op = take()[1]
            right = parse_primary()
            left = f"({left} * {right})" if op == "*" else f"(CAST({left} AS DECIMAL(18,6)) / {right})"
        return left

    def args() -> list[str]:
        take("(")
        out = [parse_or()]
        while peek()[1] == ",":
            take()
            out.append(parse_or())
        take(")")
        return out

    def month_start(d: str) -> str:
        return f"({d} - EXTRACT(DAY FROM {d}) + 1)"

    def parse_primary() -> str:
        kind, value = take()
        if kind == "num":
            return value
        if kind == "str":
            return "'" + value[1:-1].replace('""', '"').replace("'", "''") + "'"
        if kind == "ref":
            col = re.search(r"\[([^\]]+)\]", value).group(1).strip()
            if col.lower() == date_col.lower():
                return date_expr
            key = next((k for k in siblings if k.lower() == col.lower()), None)
            if key is None or key in _stack:
                raise _CalendarUnsupported(col)
            inner = _calendar_expr_sql(siblings[key], date_col, date_expr, siblings, _stack + (key,))
            if inner is None:
                raise _CalendarUnsupported(col)
            return f"({inner})"
        if kind == "op" and value == "(":
            inner = parse_or()
            take(")")
            return f"({inner})"
        if kind != "name":
            raise _CalendarUnsupported(value)
        fn = value.upper()
        if peek()[1] != "(":                       # a VAR
            if value.lower() in env:
                return f"({env[value.lower()]})"
            raise _CalendarUnsupported(value)
        if fn in ("TODAY", "NOW"):
            take("(")
            take(")")
            return "CURRENT_DATE"
        if fn in ("YEAR", "MONTH", "DAY"):
            (a,) = args()
            return f"EXTRACT({fn} FROM {a})"
        if fn == "VALUE":
            (a,) = args()
            return a if re.fullmatch(r"[\d.]+", a) else f"CAST({a} AS INTEGER)"
        if fn == "CONCATENATE":
            a = args()
            if len(a) != 2:
                raise _CalendarUnsupported(fn)
            return f"({text_of(a[0])} || {text_of(a[1])})"
        if fn in ("ENDOFMONTH", "EOMONTH"):
            a = args()
            if fn == "EOMONTH" and (len(a) != 2 or a[1] != "0"):
                raise _CalendarUnsupported(fn)
            return f"(ADD_MONTHS({month_start(a[0])}, 1) - 1)"
        if fn == "STARTOFMONTH":
            (a,) = args()
            return month_start(a)
        if fn == "CEILING":
            a = args()
            if len(a) != 2 or a[1] != "1":
                raise _CalendarUnsupported(fn)
            return f"CEIL({a[0]})"
        if fn == "FORMAT":
            take("(")
            target = parse_or()
            take(",")
            kind2, fmt = take()
            if kind2 != "str":
                raise _CalendarUnsupported(fn)
            take(")")
            sql = _dax_format_sql(fmt[1:-1], target)
            if sql is None:
                raise _CalendarUnsupported(fn)
            return sql
        if fn == "IF":
            a = args()
            if len(a) not in (2, 3):
                raise _CalendarUnsupported(fn)
            return f"CASE WHEN {a[0]} THEN {a[1]}" + (f" ELSE {a[2]}" if len(a) == 3 else "") + " END"
        raise _CalendarUnsupported(fn)

    try:
        while is_kw("var"):                        # VAR name = expr ... RETURN expr
            take()
            name = take()
            take("=")
            if name[0] != "name":
                raise _CalendarUnsupported("VAR")
            env[name[1].lower()] = parse_or()
        if env:
            if not is_kw("return"):
                raise _CalendarUnsupported("RETURN")
            take()
        sql = parse_or()
        if peek()[0] != "end":
            return None
    except (_CalendarUnsupported, ValueError):
        return None
    return sql[1:-1] if sql.startswith("(") and sql.endswith(")") and _balanced_outer(sql) else sql


def _balanced_outer(sql: str) -> bool:
    """True when the first '(' closes at the very end (safe to strip)."""
    depth = 0
    for n, ch in enumerate(sql):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and n < len(sql) - 1:
            return False
    return True


def _calendar_column_sql(expr: str, date_col: str, date_expr: str,
                         siblings: dict[str, str] | None = None) -> str | None:
    return _calendar_expr_sql(expr, date_col, date_expr, siblings)


def detect_calendar_tables(model: dict) -> dict[str, dict]:
    """{table: {"sql", "date_column", "unsupported": [columns], "notes": [...]}} for every DAX
    `CALENDAR(start, end)` calculated table, rebuilt on `sys_calendar.calendar`."""
    out: dict[str, dict] = {}
    columns: dict[str, list[dict]] = {}
    for c in model.get("calculated_columns") or []:
        if isinstance(c, dict) and c.get("TableName"):
            columns.setdefault(c["TableName"], []).append(c)
    for t in model.get("calculated_tables") or []:
        table, expr = (t or {}).get("TableName"), ((t or {}).get("Expression") or "")
        m = _DAX_CALENDAR_RE.match(expr)
        if not table or not m or _AUTO_DATE_TABLE_RE.match(table):
            continue
        args = _m_split_args(expr[m.end():])       # top-level comma split, string-aware
        if len(args) != 2:
            continue
        (start, n1), (end, n2) = _dax_date_bound(args[0]), _dax_date_bound(args[1])
        if not start or not end:
            continue
        date_col = "Date"
        alias = "calendar_date"
        select = [f'{alias} AS {_sql_col(date_col)}']
        unsupported: list[str] = []
        sibling_exprs = {c["ColumnName"]: c.get("Expression") or "" for c in columns.get(table, []) if c.get("ColumnName")}
        for c in columns.get(table, []):
            sql = _calendar_column_sql(c.get("Expression"), date_col, alias, sibling_exprs)
            if sql:
                select.append(f"{sql} AS {_sql_col(c['ColumnName'])}")
            else:
                unsupported.append(c["ColumnName"])
        notes = [n for n in (n1, n2) if n]
        one_line = re.sub(r"\s+", " ", expr).strip()
        header = [f"-- rebuilt from DAX: {one_line}"]
        if notes:
            header.append("-- note: " + ", ".join(notes))
        if unsupported:
            header.append("-- not translated (calculated columns): " + ", ".join(unsupported))
        query = ("\n".join(header) + "\nSELECT " + ",\n       ".join(select) +
                 "\nFROM sys_calendar.calendar\n"
                 f"WHERE {alias} BETWEEN {start} AND {end}")
        try:
            query = validate_read_only_sql(query)
        except ValueError:
            continue
        out[table] = {"sql": query, "date_column": date_col, "unsupported": unsupported, "notes": notes}
    return out


def _has_relationship(model: dict, a: str, b: str) -> bool:
    return any(e and {e[0], e[2]} == {a, b} for e in (_rel_ends(r) for r in model.get("relationships") or []))


def _tables_with_date_key(model: dict) -> dict[str, str]:
    """{table: column} for the tables that have a date-key column (`DATE_KEY_COLUMNS`). Uses the
    model's column list when it has one (`model.json["columns"]`, date-typed columns only) and
    falls back to the table's Power Query text for older extracts."""
    out: dict[str, str] = {}
    columns = model.get("columns")
    if isinstance(columns, list) and columns:
        for c in columns:
            name, table = str((c or {}).get("ColumnName") or ""), (c or {}).get("TableName")
            if table and name.lower() in DATE_KEY_COLUMNS and "datetime" in str(c.get("PandasDataType") or "datetime"):
                out.setdefault(table, name)
        return out
    for row in model.get("power_query") or []:
        fact, expr = (row or {}).get("TableName"), (row or {}).get("Expression") or ""
        key = next((k for k in DATE_KEY_COLUMNS if re.search(rf"\b{k}\b", expr, re.IGNORECASE)), None)
        if fact and key:
            out.setdefault(fact, key)
    return out


def detect_calendar_relationships(model: dict) -> list[dict]:
    """Proposed `fact.log_dt → Calendar.Date` (many-to-one, filters flow from the calendar to the
    fact) for each derived calendar and each table with a date-key column, unless the model
    already relates the two."""
    keyed = _tables_with_date_key(model)
    out = []
    for cal, info in detect_calendar_tables(model).items():
        for fact, key in keyed.items():
            if fact == cal or _has_relationship(model, fact, cal):
                continue
            out.append({"FromTableName": fact, "FromColumnName": key, "ToTableName": cal,
                        "ToColumnName": info["date_column"], "Cardinality": "M:1",
                        "CrossFilteringBehavior": "Single", "IsActive": 1, "Proposed": True})
    return out


def relationships_path(name: str) -> Path:
    return METRICS_DIR / f"{name}.relationships.json"


def read_relationships(name: str) -> list[dict]:
    """Hand-editable extra relationships (same field names as model.json), or []."""
    path = relationships_path(name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def sync_relationships(name: str, model: dict) -> tuple[list[dict], list[dict]]:
    """Saves the proposed calendar relationships to `metrics/<name>.relationships.json` (a
    reviewable file, like the table map). Existing entries, including hand-edited or removed
    ones that were once proposed, are never rewritten. Returns (all entries, newly added)."""
    existing = read_relationships(name)
    known = {(r.get("FromTableName"), r.get("FromColumnName"), r.get("ToTableName")) for r in existing}
    new = [r for r in detect_calendar_relationships(model)
           if (r["FromTableName"], r["FromColumnName"], r["ToTableName"]) not in known]
    if new:
        path = relationships_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing + new, ensure_ascii=False, indent=2), encoding="utf-8")
    return existing + new, new


def with_relationship_overrides(name: str, model: dict) -> dict:
    """`model` with the report's extra relationships appended (a copy; the input is untouched)."""
    extra = read_relationships(name)
    if not extra:
        return model
    return {**model, "relationships": [*(model.get("relationships") or []), *extra]}


def table_map_path(name: str) -> Path:
    return METRICS_DIR / f"{name}.table_map.json"


def read_table_map(name: str) -> tuple[dict[str, str], str | None]:
    """(mapping, problem) from `metrics/<name>.table_map.json`. Only well-formed
    `{"entity": "query"}` string pairs come back as mapping; anything else is reported as
    `problem` instead of being passed on (the file can be hand-edited: a JSON list or a numeric
    value used to crash the scaffold, and silently returning {} made a person's mapping look
    like it had vanished)."""
    path = table_map_path(name)
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        return {}, f"metrics/{path.name} isn't readable as JSON ({e}). Fix or delete it."
    if not isinstance(data, dict):
        return {}, f"metrics/{path.name} should be a JSON object of \"table\": \"query\" pairs."
    bad = sorted(k for k, v in data.items() if not isinstance(k, str) or not isinstance(v, str))
    clean = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    if bad:
        return clean, (f"metrics/{path.name}: ignored {len(bad)} entry/entries that aren't "
                       f"text queries ({', '.join(map(str, bad[:3]))}).")
    return clean, None


def preview_table_map(name: str, model: dict) -> dict[str, str]:
    """`sync_table_map` without saving: what the map would be after detection (read-only callers)."""
    existing, _ = read_table_map(name)
    detected = {t: info["sql"] for t, info in detect_calendar_tables(model).items()}
    detected.update(detect_table_map_from_power_query(model))
    return {**{k: v for k, v in detected.items() if k not in existing}, **existing}


def sync_table_map(name: str, model: dict) -> tuple[dict[str, str], list[str]]:
    """The report's table map with every table auto-detected from Power Query added and saved
    (`detect_table_map_from_power_query`). Entries already there, i.e. mapped by hand, are never
    touched. Returns (merged mapping, names of the newly added tables)."""
    existing, _ = read_table_map(name)
    detected = {t: info["sql"] for t, info in detect_calendar_tables(model).items()}
    detected.update(detect_table_map_from_power_query(model))     # a real source wins
    new = {k: v for k, v in detected.items() if k not in existing}
    merged = {**existing, **new}
    if new:
        path = table_map_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged, sorted(new)


# Teradata reserved words (Teradata SQL "Reserved Words and Keywords"), lower-cased, plus `value`, the
# renderer's own column contract. Unquoted, one of these as a name is a syntax error (error 3707, e.g.
# `SELECT SUM(x) AS value` or a Power BI column called Rename), which sqlglot's Teradata dialect does not
# catch. Every generated column name and table alias is checked against this list. Quoting is harmless in a
# Teradata-mode session (names are case-insensitive); an ANSI-mode session would make quoted names
# case-sensitive. The list is from the documentation as remembered: a word missing here shows up as a 3707 at
# run time and just needs adding.
_TERADATA_RESERVED = frozenset("""
abort abortsession abs access_lock account acos acosh add add_months admin after aggregate all alter amp and
ansidate any are array as asc asin asinh at atan atan2 atanh atomic authorization ave average avg before begin
between bigint binary blob both bt but by byte byteint bytes call case case_n casespecific cast cd char
char_length char2hexint character character_length characters chars check checkpoint class clob close cluster
cm coalesce collation collect column comment commit compress condition connect constraint constructor contains
continue convert_table_header corr cos cosh count covar_pop covar_samp create cross cs csum ct cube current
current_date current_role current_time current_timestamp current_user cursor cv cycle data database datablocksize
date dateform day deallocate dec decimal declare default deferred degrees del delete dense_rank depth deref desc
describe descriptor deterministic diagnostic disabled distinct do domain double drop dual dump dynamic each echo
element else elseif enabled end eq equals error errorfiles errortables escape et except exception exec execute
exists exit exp explain external extract fallback fastexport fetch first float for foreign format found
freespace from full function ge general generated get give global go goto grant graphic group grouping gt
handler hash hashamp hashbakamp hashbucket hashrow having help hour identity if immediate in inconsistent index
indicator initially initiate inner inout input ins insert instance instead int integer integerdate intersect
interval into is iterate join journal key kurtosis le leading leave left like limit ln loading local localtime
localtimestamp locator lock locking log logging logon long loop lower lt macro map mavg max maximum mcharacters
mdiff member merge method min mindex minimum minus minute mlinreg mload mod mode modifies modify monitor
monresource monsession month msubstr msum multiset named names national natural nchar nclob ne new new_table
next no none normalize not nowait null nullif nullifzero numeric object objects octet_length of off old
old_table on only open option or order ordering out outer output over overlaps override pad parameter
parameters partial partition password path percent percent_rank perm permanent pivot position precision prepare
preserve primary prior privileges procedure profile proportional protection public qualified qualify quantile
queue query query_band radians random range_n rank reads real recursive ref references referencing relative
release rename repeat replace replacement replcontrol replication request resignal restart restore result
resume ret retrieve return returns revalidate revoke right rights role rollback rollforward rollup row
row_number rowid rows sample sampleid scroll sel select session set setresrate sets setsessrate show signal sin
sinh size skew smallint some soundex specific spool sql sqlexception sqlstate sqltext sqlwarning sqrt ss start
startup state statement static statistics stddev_pop stddev_samp stepinfo string_cs structure subscriber substr
substring sum summary suspend table tablesample tan tanh tbl_cs temporary terminate then threshold time
timestamp timezone_hour timezone_minute title to top topn trace trailing transaction translate translate_chk
translation treat trigger trim true type uc undefined under undo union unique unknown unnest until upd update
upper uppercase user using value values var_pop var_samp varbyte varchar vargraphic varying view volatile when
whenever where while width_bucket with without work year zeroifnull zone
""".split())
# names that follow AS in a CAST and are never an alias: leave them alone when rewriting legacy SQL
_SQL_TYPE_WORDS = frozenset("""
date time timestamp integer int smallint bigint byteint decimal dec numeric float real double char character
varchar clob blob byte varbyte interval graphic vargraphic number
""".split())
_TERADATA_RESERVED_COLS = _TERADATA_RESERVED          # kept under its old name

_ALIAS_TO_QUOTE_RE = re.compile(r"(?i)\bAS\s+([A-Za-z_]\w*)\b(?!\s*[.(])")
_QUALIFIED_NAME_RE = re.compile(r"\b[A-Za-z_]\w*\s*\.\s*([A-Za-z_]\w*)\b(?!\s*\()")


def quote_reserved_aliases(sql: str) -> str:
    """`... AS value` → `... AS "value"` for every reserved word used as an output alias, in the code part
    of `sql` (and `alias.rename` after a dot), for SQL and table maps written before the drafter quoted them,
    hand-written SQL and `metrics/_template.yaml`. Literals, quoted identifiers and comments are left alone,
    and so are type names after AS (`CAST(x AS DATE)`)."""
    code, pos = _sql_scan(sql)
    edits = [(pos[m.start(1)], pos[m.end(1) - 1] + 1) for m in _ALIAS_TO_QUOTE_RE.finditer(code)
             if m.group(1).lower() in _TERADATA_RESERVED and m.group(1).lower() not in _SQL_TYPE_WORDS]
    # `alias.rename`: after a dot a word is a name, never a type, so any reserved one is quoted
    edits += [(pos[m.start(1)], pos[m.end(1) - 1] + 1) for m in _QUALIFIED_NAME_RE.finditer(code)
              if m.group(1).lower() in _TERADATA_RESERVED]
    for start, end in sorted(set(edits), reverse=True):
        sql = f'{sql[:start]}"{sql[start:end].lower()}"{sql[end:]}'
    return sql


_SET_OP_RE = re.compile(r"(?i)\b(UNION(?:\s+ALL)?|INTERSECT|EXCEPT|MINUS)\b")
_SELECT_RE = re.compile(r"(?i)\bSEL(?:ECT)?\b")
_FROM_RE = re.compile(r"(?i)\bFROM\b")
_TRAILING_SET_OP_RE = re.compile(r"(?i)\b(?:UNION(?:\s+ALL)?|INTERSECT|EXCEPT|MINUS)$")
_LEGACY_TOPN_RE = re.compile(r"\) AS t QUALIFY RANK\(\) OVER \(ORDER BY a (ASC|DESC)\) <= (\d+)\)")


def add_from_to_bare_selects(sql: str) -> str:
    """Teradata error 3888: "A SELECT for a UNION, INTERSECT or MINUS must reference a table". An
    inline ("Enter Data") table saved in an older table map is `SELECT 'E', 'Employee' UNION ALL SELECT ...`;
    every arm of a set operation without a FROM gets a one-row source. Only arms that are part of a set
    operation are touched."""
    code, pos = _sql_scan(sql)
    depth, d = [], 0
    for ch in code:
        if ch == ")":
            d -= 1
        depth.append(d)
        if ch == "(":
            d += 1
    edits: list[int] = []
    for m in _SELECT_RE.finditer(code):
        dm, start = depth[m.start()], m.end()
        end = len(code)
        for i in range(start, len(code)):
            if depth[i] < dm:
                end = i
                break
            if depth[i] == dm and _SET_OP_RE.match(code, i) and (i == 0 or not code[i - 1].isalnum()):
                end = i
                break
        before = code[:m.start()].rstrip()
        prev_op = bool(_TRAILING_SET_OP_RE.search(before)) and depth[len(before) - 1] == dm
        in_set_op = prev_op or (end < len(code) and _SET_OP_RE.match(code, end) is not None)
        arm = code[start:end]
        has_from = any(depth[start + k.start()] == dm for k in _FROM_RE.finditer(arm))
        if in_set_op and not has_from:
            cut = end
            while cut > start and code[cut - 1].isspace():
                cut -= 1
            edits.append(pos[cut] if cut < len(pos) else len(sql))
            if cut >= len(pos):
                edits[-1] = len(sql)
    for at in sorted(set(edits), reverse=True):
        sql = sql[:at] + _ONE_ROW + sql[at:]
    return sql


def rewrite_legacy_top_n(sql: str) -> str:
    """Top N filters drafted before Teradata rejected `IN (SELECT k FROM (...) t QUALIFY RANK() OVER
    (ORDER BY a DIR) <= N)` (error 3706: no ordered analytics in a subquery) → the counted form the
    drafter writes now. The old shape is exact, so the rewrite is exact."""
    for m in reversed(list(_LEGACY_TOPN_RE.finditer(sql))):
        head = "SELECT k FROM (\n"
        start = sql.rfind(head, 0, m.start())
        if start < 0:
            continue
        inner = sql[start + len(head):m.start()].rstrip("\n")
        better = "<" if m.group(1) == "ASC" else ">"
        new = (f"SELECT t.k FROM (\n{inner}\n) AS t WHERE (SELECT COUNT(*) FROM (\n{inner}\n) AS u "
               f"WHERE u.a {better} t.a) < {m.group(2)})")
        sql = sql[:start] + new + sql[m.end():]
    return sql


def fix_teradata_sql(sql: str) -> str:
    """Everything `TeradataBackend` fixes before sending SQL that an earlier version of the drafter (or a
    person) wrote: a reserved word as a name, a window function in a Top N subquery, a set-operation arm with
    no table. Idempotent; SQL that has none of these comes back unchanged."""
    return quote_reserved_aliases(add_from_to_bare_selects(rewrite_legacy_top_n(sql)))


def _ident(name: str) -> str:
    """A Power BI name as a plain lower-case SQL identifier."""
    alias = re.sub(r"\W+", "_", name).strip("_").lower()
    return alias or "t"


def _sql_col(name: str) -> str:
    """A Power BI column name as a Teradata identifier (see `_sql_alias`), quoted when it is
    a reserved word."""
    alias = _ident(name)
    return f'"{alias}"' if alias in _TERADATA_RESERVED else alias


def _subquery(sql: str, alias: str) -> str:
    """`(<sql>) AS alias`, with the closing parenthesis on its own line: a table query that ends
    in a `-- comment` (common in Power Query SQL) would otherwise swallow the `)` and the alias."""
    return f"({sql}\n) AS {alias}"


def _sql_alias(entity: str) -> str:
    """A table alias for a Power BI table. A reserved word (a table called Date, Index, Rename...) gets a
    `_t` suffix: an alias can't be quoted just at its definition, every `alias.column` reference would
    have to be too."""
    alias = _ident(entity)
    return f"{alias}_t" if alias in _TERADATA_RESERVED else alias


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
        sources.append(_subquery(query, _sql_alias(entity)))
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

_DAX_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<qtable>'(?:[^']|'')*')
  | (?P<bracket>\[[^\]]*\])
  | (?P<ident>[A-Za-z_][\w.]*)
  | (?P<op><=|>=|<>|==|&&|\|\||[-+*/=<>&])
  | (?P<punct>[(),])
""", re.VERBOSE | re.DOTALL)

# DAX aggregate → (SQL function, needs DISTINCT, takes a table rather than a column)
_DAX_AGGREGATES: dict[str, tuple[str, bool, bool]] = {
    "SUM": ("SUM", False, False), "AVERAGE": ("AVG", False, False),
    "MIN": ("MIN", False, False), "MAX": ("MAX", False, False),
    "COUNT": ("COUNT", False, False), "COUNTA": ("COUNT", False, False),
    "DISTINCTCOUNT": ("COUNT", True, False), "COUNTROWS": ("COUNT", False, True),
    # ignores blanks, which is exactly what SQL's COUNT(DISTINCT col) does
    "DISTINCTCOUNTNOBLANK": ("COUNT", True, False),
}
# ref-level wrapper Power BI puts on an auto-aggregated column ("Sum(Sales.Amount)")
_REF_AGG_TO_SQL: dict[str, tuple[str, bool]] = {
    "sum": ("SUM", False), "count": ("COUNT", False), "countnonnull": ("COUNT", False),
    "min": ("MIN", False), "max": ("MAX", False), "avg": ("AVG", False),
    "average": ("AVG", False), "distinctcount": ("COUNT", True),
}
_DAX_COMPARISONS = {"=": "=", "==": "=", "<>": "<>", ">": ">", "<": "<", ">=": ">=", "<=": "<="}


class _DaxUnsupported(Exception):
    """Raised the moment something isn't confidently translatable. Always caught — the
    visual falls back to a TODO rather than shipping a guess."""


@dataclass
class _Sql:
    """A translated fragment plus the Power BI tables it needs joined in."""
    text: str
    tables: set[str] = field(default_factory=set)
    selmins: set[tuple[str, str]] = field(default_factory=set)   # see _SELMIN_RE
    ctxs: set[tuple[str, str, str]] = field(default_factory=set)  # (MIN|MAX, table, column), see _CTX_RE
    aggs: list[tuple[str, str]] = field(default_factory=list)      # split mode: (table, aggregate SQL)


def _dax_tokenize(text: str) -> list[tuple[str, str]]:
    tokens, pos = [], 0
    while pos < len(text):
        m = _DAX_TOKEN_RE.match(text, pos)
        if not m:
            raise _DaxUnsupported(f"unparseable at {text[pos:pos + 20]!r}")
        pos = m.end()
        if m.lastgroup not in ("ws", "comment"):
            tokens.append((m.lastgroup, m.group()))
    return tokens


class _DaxTranslator:
    """Recursive-descent translation of a DAX measure into one SQL aggregate expression.

    Narrow but *compositional*, which is the whole point. The previous version matched a
    single `AGG(Table[Col])` with one regex, so any measure built out of other measures —
    a ratio, a filtered total, a difference — failed to resolve and took its whole visual
    down to a TODO. Most real measures are exactly those, which is why people kept having
    to write every query by hand. Handled:

      SUM/AVERAGE/MIN/MAX/COUNT/COUNTA/DISTINCTCOUNT/COUNTROWS
      DIVIDE(a, b[, alt]), arithmetic + - * /, parentheses, ABS/ROUND/COALESCE
      [Other Measure]                 resolved recursively (self-reference refused)
      CALCULATE(expr, T[C] = "x", …)  filters folded into each aggregate as CASE WHEN

    Anything else — time intelligence, ALL/ALLSELECTED, virtual tables, iterators —
    raises _DaxUnsupported and the caller leaves a TODO. A wrong number that looks right
    is far worse than an honest blank."""

    def __init__(self, measures: dict[tuple[str, str], str], split: bool = False):
        self.measures = measures
        self.split = split      # emit `{AGG:n}` per aggregate (see `_emit`) instead of inline SQL
        self.aggs: list[tuple[str, str]] = []
        self.tables: set[str] = set()
        self._resolving: set[str] = set()
        self.tokens: list[tuple[str, str]] = []
        self.pos = 0
        self._bare_column: str | None = None    # a column used outside any aggregate
        self.selmins: set[tuple[str, str]] = set()
        self.ctxs: set[tuple[str, str, str]] = set()
        self._vars: dict[str, str] = {}

    def translate(self, dax: str) -> _Sql:
        self.tokens, self.pos = _dax_tokenize(dax), 0
        node = self._body([])
        if self.pos != len(self.tokens):
            raise _DaxUnsupported(f"trailing tokens: {self.tokens[self.pos:][:3]}")
        if self._bare_column:
            # A measure has to reduce to one value per group. A bare column doesn't —
            # emitting it would produce `SELECT dim.name AS category, fact.amount AS
            # value ... GROUP BY 1`, which the database rejects outright.
            raise _DaxUnsupported(f"{self._bare_column} isn't aggregated")
        # a VAR that is never used must not drag its table into the query
        ctxs = {c for c in self.ctxs if f"{{CTX:{c[0]}|{c[1]}|{c[2]}}}" in node}
        return _Sql(node, set(self.tables), set(self.selmins), ctxs, list(self.aggs))

    # -- token helpers -------------------------------------------------------
    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _take(self) -> tuple[str, str]:
        if self.pos >= len(self.tokens):
            raise _DaxUnsupported("unexpected end of expression")
        self.pos += 1
        return self.tokens[self.pos - 1]

    def _expect(self, value: str) -> None:
        text = self._take()[1]
        if text != value:
            raise _DaxUnsupported(f"expected {value!r}, got {text!r}")

    # -- grammar -------------------------------------------------------------
    def _body(self, filters: list[str]) -> str:
        """A measure body: optional `VAR name = <scalar>` lines, `RETURN`, then the expression.
        A variable holds a scalar (a column, MIN/MAX of a column over the selection, a date part);
        it is substituted where used, since SQL has no variables."""
        while (tok := self._peek()) and tok[0] == "ident" and tok[1].upper() == "VAR":
            self._take()
            kind, name = self._take()
            if kind != "ident":
                raise _DaxUnsupported("VAR needs a name")
            self._expect("=")
            self._vars[name.lower()] = self._scalar()
        if self._vars:
            tok = self._take()
            if tok[1].upper() != "RETURN":
                raise _DaxUnsupported("VAR without RETURN")
        return self._expression(filters)

    def _scalar(self) -> str:
        """A scalar expression (the operands of a filter comparison, or a VAR): literals, variables,
        columns, MONTH/YEAR/DAY, CONCATENATE / `&`, TODAY, and MIN/MAX of a column, which is the
        value over the report's current selection (a `{CTX:...}` marker, see `_expand_ctxs`)."""
        left = self._scalar_primary()
        while (tok := self._peek()) and tok[1] == "&":
            self._take()
            left = f"({_as_text(left)} || {_as_text(self._scalar_primary())})"
        return left

    def _scalar_primary(self) -> str:
        kind, text = self._take()
        if kind == "number":
            return text
        if kind == "string":
            return "'" + text[1:-1].replace('""', '"').replace("'", "''") + "'"
        if kind == "op" and text == "-":
            return "-" + self._scalar_primary()
        if text == "(":
            inner = self._scalar()
            self._expect(")")
            return f"({inner})"
        if kind == "ident" and (nxt := self._peek()) and nxt[1] == "(":
            return self._scalar_function(text.upper())
        if kind == "ident" and text.lower() in self._vars:
            return self._vars[text.lower()]
        if kind in ("qtable", "ident") and (nxt := self._peek()) and nxt[0] == "bracket":
            self._take()
            table = text[1:-1].replace("''", "'") if kind == "qtable" else text
            self.tables.add(table)
            return f"{_sql_alias(table)}.{_sql_col(nxt[1][1:-1].strip())}"
        raise _DaxUnsupported(f"{text!r} isn't a literal, variable or column")

    def _scalar_function(self, name: str) -> str:
        self._expect("(")
        if name in ("TODAY", "NOW"):
            self._expect(")")
            return "CURRENT_DATE" if name == "TODAY" else "CURRENT_TIMESTAMP(0)"
        if name in ("MIN", "MAX"):
            table = self._table_name()
            nxt = self._peek()
            if not nxt or nxt[0] != "bracket":
                raise _DaxUnsupported(f"{name} needs a column")
            self._take()
            column = nxt[1][1:-1].strip()
            self._expect(")")
            self.ctxs.add((name, table, column))
            return f"{{CTX:{name}|{table}|{column}}}"
        args = [self._scalar()]
        while (tok := self._peek()) and tok[1] == ",":
            self._take()
            args.append(self._scalar())
        self._expect(")")
        if name in ("MONTH", "YEAR", "DAY") and len(args) == 1:
            return f"EXTRACT({name} FROM {args[0]})"
        if name == "CONCATENATE" and len(args) == 2:
            return f"({_as_text(args[0])} || {_as_text(args[1])})"
        raise _DaxUnsupported(f"{name}() isn't translated in a filter")

    def _expression(self, filters: list[str]) -> str:
        left = self._term(filters)
        while (tok := self._peek()) and tok[1] in ("+", "-"):
            op = self._take()[1]
            left = f"({left} {op} {self._term(filters)})"
        return left

    def _term(self, filters: list[str]) -> str:
        left = self._unary(filters)
        while (tok := self._peek()) and tok[1] in ("*", "/"):
            op = self._take()[1]
            right = self._unary(filters)
            # Plain `/` in DAX yields BLANK on divide-by-zero instead of erroring;
            # mirroring that keeps one bad row from failing a whole visual.
            left = (f"(CASE WHEN ({right}) = 0 THEN NULL ELSE ({left}) / "
                    f"CAST(({right}) AS DECIMAL(18,6)) END)") if op == "/" else f"({left} * {right})"
        return left

    def _unary(self, filters: list[str]) -> str:
        if (tok := self._peek()) and tok[1] == "-":
            self._take()
            return f"(-{self._unary(filters)})"
        return self._primary(filters)

    def _primary(self, filters: list[str]) -> str:
        kind, text = self._take()
        if kind == "number":
            return text
        if kind == "string":
            return "'" + text[1:-1].replace('""', '"').replace("'", "''") + "'"
        if text == "(":
            inner = self._expression(filters)
            self._expect(")")
            return f"({inner})"
        if kind == "bracket":                       # [Measure]
            return self._measure_reference(text[1:-1].strip(), filters)
        if kind in ("ident", "qtable"):
            nxt = self._peek()
            if nxt and nxt[0] == "bracket":         # Table[Column], outside an aggregate
                self._take()
                table = text[1:-1].replace("''", "'") if kind == "qtable" else text
                column = nxt[1][1:-1].strip()
                if (table, column) in self.measures:   # Table[Measure]: a measure, qualified by its home table
                    return self._measure_reference(column, filters)
                self.tables.add(table)
                self._bare_column = f"{table}[{column}]"
                return f"{_sql_alias(table)}.{_sql_col(column)}"
            if nxt and nxt[1] == "(":               # a function call
                return self._function(text.upper(), filters)
            raise _DaxUnsupported(f"bare identifier {text!r}")
        raise _DaxUnsupported(f"unexpected token {text!r}")

    def _measure_reference(self, name: str, filters: list[str]) -> str:
        if name in self._resolving:
            raise _DaxUnsupported(f"measure [{name}] refers to itself")
        expression = next((e for (_tbl, mname), e in self.measures.items() if mname == name and e), None)
        if expression is None:
            raise _DaxUnsupported(f"measure [{name}] isn't in the model")
        self._resolving.add(name)
        saved_tokens, saved_pos, saved_vars = self.tokens, self.pos, self._vars
        self._vars = {}
        try:
            self.tokens, self.pos = _dax_tokenize(expression), 0
            inner = self._body(filters)
            if self.pos != len(self.tokens):
                raise _DaxUnsupported(f"measure [{name}] has trailing tokens")
            return f"({inner})"
        finally:
            self.tokens, self.pos = saved_tokens, saved_pos
            self._vars = saved_vars
            self._resolving.discard(name)

    def _aggregate(self, func: str, filters: list[str]) -> str:
        sql_func, distinct, table_only = _DAX_AGGREGATES[func]
        self._expect("(")
        kind, text = self._take()
        if kind == "qtable":
            table = text[1:-1].replace("''", "'")
        elif kind == "ident":
            table = text
        else:
            raise _DaxUnsupported(f"{func} expects a table or column, got {text!r}")
        column = None
        if (nxt := self._peek()) and nxt[0] == "bracket":
            self._take()
            column = nxt[1][1:-1].strip()
        self._expect(")")
        self.tables.add(table)

        if column is None:
            if not table_only:
                raise _DaxUnsupported(f"{func} needs a column")
            return self._emit(table, f"SUM(CASE WHEN {' AND '.join(filters)} THEN 1 ELSE 0 END)"
                              if filters else "COUNT(*)")
        target = f"{_sql_alias(table)}.{_sql_col(column)}"
        if filters:
            target = f"CASE WHEN {' AND '.join(filters)} THEN {target} END"
        return self._emit(table, f"{sql_func}({'DISTINCT ' if distinct else ''}{target})")

    def _emit(self, table: str, agg: str) -> str:
        """In split mode each aggregate becomes a marker and is recorded with its table, so a
        measure over several fact tables can be computed one table at a time (`multi_fact`).
        An aggregate whose filters read another table can't be moved to that table's own query."""
        if not self.split:
            return agg
        for t in self.tables:
            if t != table and re.search(rf"\b{re.escape(_sql_alias(t))}\.", agg):
                raise _DaxUnsupported(f"an aggregate over {table} filters on {t}")
        self.aggs.append((table, agg))
        return f"{{AGG:{len(self.aggs) - 1}}}"

    def _function(self, name: str, filters: list[str]) -> str:
        if name in _DAX_AGGREGATES:
            return self._aggregate(name, filters)
        if name == "DIVIDE":
            self._expect("(")
            args = self._arguments(filters)
            if not 2 <= len(args) <= 3:
                raise _DaxUnsupported("DIVIDE takes 2 or 3 arguments")
            alt = args[2] if len(args) == 3 else "NULL"
            return (f"(CASE WHEN ({args[1]}) = 0 OR ({args[1]}) IS NULL THEN {alt} "
                    f"ELSE ({args[0]}) / CAST(({args[1]}) AS DECIMAL(18,6)) END)")
        if name in ("TODAY", "NOW"):
            self._expect("(")
            self._expect(")")
            return "CURRENT_DATE" if name == "TODAY" else "CURRENT_TIMESTAMP(0)"
        if name == "FORMAT":
            self._expect("(")
            value = self._expression(filters)
            self._expect(",")
            kind, text = self._take()
            if kind != "string":
                raise _DaxUnsupported("FORMAT needs a literal format string")
            self._expect(")")
            out = _dax_format_sql(text[1:-1], value)
            if out is None:
                raise _DaxUnsupported(f"FORMAT string {text} isn't translated")
            return f"({out})"
        if name == "TIME":
            self._expect("(")
            parts = []
            for i in range(3):
                kind, text = self._take()
                if kind != "number" or not text.isdigit():
                    raise _DaxUnsupported("TIME needs literal integers")
                parts.append(int(text))
                self._expect(")" if i == 2 else ",")
            return f"(INTERVAL '{parts[0]:02d}:{parts[1]:02d}:{parts[2]:02d}' HOUR TO SECOND)"
        if name == "CALCULATE":
            return self._calculate(filters)
        if name == "IF":
            self._expect("(")
            cond = self._boolean(None)
            branches = []
            while (tok := self._peek()) and tok[1] == ",":
                self._take()
                branches.append(self._expression(filters))
            self._expect(")")
            if not 1 <= len(branches) <= 2:
                raise _DaxUnsupported("IF takes 2 or 3 arguments")
            return f"(CASE WHEN {cond} THEN {branches[0]} ELSE {branches[1] if len(branches) == 2 else 'NULL'} END)"
        if name in ("ABS", "ROUND", "COALESCE"):
            self._expect("(")
            return f"{name}({', '.join(self._arguments(filters))})"
        raise _DaxUnsupported(f"{name}() isn't translated automatically")

    def _arguments(self, filters: list[str]) -> list[str]:
        args = [self._expression(filters)]
        while (tok := self._peek()) and tok[1] == ",":
            self._take()
            args.append(self._expression(filters))
        self._expect(")")
        return args

    def _calculate(self, filters: list[str]) -> str:
        """CALCULATE(expr, Table[Col] = "x", …) → the comparisons folded into every
        aggregate inside `expr` as CASE WHEN. Only plain column comparisons are taken;
        ALL()/ALLSELECTED(), a measure used as a filter, and table filters all raise,
        because those change the evaluation context in ways a WHERE clause doesn't."""
        self._expect("(")
        start = self.pos
        self._skip_argument()                       # re-parsed below, once filters are known
        extra: list[str] = []
        while (tok := self._peek()) and tok[1] == ",":
            self._take()
            predicate = self._filter_argument()
            if predicate:
                extra.append(predicate)
        self._expect(")")
        end = self.pos
        self.pos = start
        inner = self._expression(filters + extra)
        self.pos = end
        return inner

    def _skip_argument(self) -> None:
        depth = 0
        while (tok := self._peek()) is not None:
            if tok[1] == "(":
                depth += 1
            elif tok[1] == ")":
                if depth == 0:
                    return
                depth -= 1
            elif tok[1] == "," and depth == 0:
                return
            self._take()

    def _table_name(self) -> str:
        kind, text = self._take()
        if kind == "qtable":
            return text[1:-1].replace("''", "'")
        if kind == "ident":
            return text
        raise _DaxUnsupported(f"expected a table, got {text!r}")

    def _filter_argument(self) -> str | None:
        """One CALCULATE filter: `FILTER(Table, condition)`, a bare condition, or a bare table
        (which filters nothing). Conditions are column-vs-literal comparisons combined with
        && / ||; anything that depends on another aggregate or the report's selection raises."""
        tok, nxt = self._peek(), (self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None)
        if tok and tok[0] == "ident" and tok[1].upper() == "FILTER" and nxt and nxt[1] == "(":
            self._take()
            self._expect("(")
            table = self._table_name()
            self._expect(",")
            marker = self._selection_min(table)
            if marker:
                self._expect(")")
                return marker
            condition = self._boolean(table)
            self._expect(")")
            return condition
        if tok and tok[0] in ("ident", "qtable") and (nxt is None or nxt[1] in (",", ")")):
            self._take()                        # 'Table' as an argument: all its rows, no filter
            return None
        return self._boolean(None)

    def _selection_min(self, table: str) -> str | None:
        """`T[c] = MIN(T[c])` inside `FILTER(T, ...)`: "keep the rows of T at the lowest value of c
        among the rows the report's filters currently leave in T" (a hierarchy slicer's top level).
        It depends on the selection, so it can't be a column comparison; it becomes a marker that
        `_draft_visual_sql` expands into a join against T's slicer selection (see `_expand_selmins`)."""
        start = self.pos
        try:
            def ref():
                kind, text = self._take()
                if kind not in ("qtable", "ident"):
                    raise _DaxUnsupported("x")
                name = text[1:-1].replace("''", "'") if kind == "qtable" else text
                kb, tb = self._take()
                if kb != "bracket":
                    raise _DaxUnsupported("x")
                return name, tb[1:-1].strip()
            t1, c1 = ref()
            if self._take()[1] not in ("=", "==") or self._take()[1].upper() != "MIN":
                raise _DaxUnsupported("x")
            self._expect("(")
            t2, c2 = ref()
            self._expect(")")
            if (t1, c1) != (t2, c2) or t1 != table or (self._peek() or ("", ""))[1] != ")":
                raise _DaxUnsupported("x")
        except _DaxUnsupported:
            self.pos = start
            return None
        self.selmins.add((table, c1))
        return f"{{SELMIN:{table}|{c1}}}"

    def _boolean(self, table: str | None) -> str:
        left = self._boolean_and(table)
        while (tok := self._peek()) and tok[1] == "||":
            self._take()
            left = f"({left} OR {self._boolean_and(table)})"
        return left

    def _boolean_and(self, table: str | None) -> str:
        left = self._comparison(table)
        while (tok := self._peek()) and tok[1] == "&&":
            self._take()
            left = f"({left} AND {self._comparison(table)})"
        return left

    def _comparison(self, table: str | None) -> str:
        tok = self._peek()
        if tok and tok[1] == "(":
            self._take()
            inner = self._boolean(table)
            self._expect(")")
            return f"({inner})"
        left = self._scalar()
        op = self._take()[1]
        if op not in _DAX_COMPARISONS:
            raise _DaxUnsupported(f"filter operator {op!r} isn't supported")
        return f"{left} {_DAX_COMPARISONS[op]} {self._scalar()}"


def translate_dax(dax: str, measures: dict[tuple[str, str], str], split: bool = False) -> _Sql | None:
    """A DAX measure → one SQL aggregate expression, or None when it isn't confidently
    translatable. See _DaxTranslator for exactly what's covered."""
    if not dax or not dax.strip():
        return None
    try:
        return _DaxTranslator(measures, split).translate(dax.strip())
    except (_DaxUnsupported, RecursionError):
        return None


@dataclass
class _Field:
    role: str
    is_value: bool
    label: str                       # the field's name as Power BI shows it
    out_name: str                    # its SQL alias
    expr: str                        # ready SQL: an aggregate for a value, a column otherwise
    tables: set[str] = field(default_factory=set)
    key: tuple[str, str] = ("", "")  # (table, column/measure): what a sort definition points at
    selmins: set[tuple[str, str]] = field(default_factory=set)   # tables whose selection this measure reads
    ctxs: set[tuple[str, str, str]] = field(default_factory=set)  # MIN/MAX of a column over the selection
    aggs: list[tuple[str, str]] = field(default_factory=list)     # per-table aggregates of a multi-fact measure


def _resolve_field(role: str, ref: str, measures: dict[tuple[str, str], str]) -> _Field | None:
    """A ref is either already agg-wrapped by Power BI ('Sum(Sales.Amount)' — a raw
    column auto-aggregated in a Values well), a named measure (whose own DAX gets
    translated, see `translate_dax`), or a plain dimension column. Returns None only
    when a named measure's DAX isn't confidently translatable; the caller then leaves
    the whole visual as a TODO rather than half-drafting it."""
    agg, table, col = query_ref_parts(ref)
    if agg:
        sql_func, distinct = _REF_AGG_TO_SQL.get(agg.lower(), (None, False))
        if sql_func is None:
            return None
        target = f"{_sql_alias(table)}.{_sql_col(col)}"
        expr = f"{sql_func}({'DISTINCT ' if distinct else ''}{target})"
        return _Field(role, True, col, _sql_col(col), expr, {table}, (table, col))
    measure_dax = measures.get((table, col))
    if measure_dax is not None:
        translated = translate_dax(measure_dax, measures)
        if translated is None:
            return None
        if len(translated.tables) > 1:      # several fact tables: also keep the per-table split
            parts = translate_dax(measure_dax, measures, split=True)
            if parts is not None:
                translated = parts
        return _Field(role, True, col, _sql_col(col), translated.text, set(translated.tables), (table, col),
                      set(translated.selmins), set(translated.ctxs), list(translated.aggs))
    return _Field(role, False, col, _sql_col(col),
                  f"{_sql_alias(table)}.{_sql_col(col)}", {table}, (table, col))


def _order_by(sort: list[dict] | None, positions: dict[tuple[str, str], int]) -> str:
    """`ORDER BY <column position> ASC|DESC, ...` for the sort entries that point at a selected
    column; entries for fields the query doesn't select are skipped (Power BI can sort by a
    field that isn't shown, which a positional ORDER BY can't express)."""
    parts = []
    for it in sort or []:
        pos = positions.get((it.get("entity"), it.get("property")))
        if pos:
            parts.append(f"{pos} {'DESC' if it.get('direction') == 'desc' else 'ASC'}")
    return "\nORDER BY " + ", ".join(parts) if parts else ""


def _rel_ends(r: dict) -> tuple[str, str, str, str] | None:
    """(from_table, from_col, to_table, to_col) of an active relationship, or None. Accepts both
    spellings: the extractor (pbixray) emits `FromTableName`/`FromColumnName`/`ToTableName`/
    `ToColumnName` and the older tests used `FromTable`/... Only the latter was read, so no
    JOIN was ever drafted from a real model. An inactive relationship (`IsActive` 0/false) is
    not a default join path and is skipped."""
    if not isinstance(r, dict) or r.get("IsActive") in (0, False, "0", "false"):
        return None
    ft = r.get("FromTable") or r.get("FromTableName")
    fc = r.get("FromColumn") or r.get("FromColumnName")
    tt = r.get("ToTable") or r.get("ToTableName")
    tc = r.get("ToColumn") or r.get("ToColumnName")
    return (ft, fc, tt, tc) if ft and fc and tt and tc else None


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
        ends = _rel_ends(r)
        if ends is None:
            continue
        ft, fc, tt, tc = ends
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
_DRAFTABLE_KINDS = {"card", "kpi", "gauge", "table", "matrix", "multicard"} | _CHART_KINDS


def _draft_from_clause(tables_needed: list[str], table_map: dict[str, str],
                       relationships: list[dict]) -> tuple[list[str], dict[str, str]] | None:
    """FROM + JOINs for the tables a visual needs, or None if they can't be reached."""
    if not tables_needed or any(t not in table_map for t in tables_needed):
        return None
    try:
        # Defensive re-validation, same as _sql_stub: table_map is normally written only
        # through the panel's step 2b (validated then), but a hand-edited
        # table_map.json could carry something that wouldn't have been accepted.
        validated = {t: validate_read_only_sql(table_map[t]) for t in tables_needed}
    except ValueError:
        return None
    join_path = _find_join_path(tables_needed, relationships)
    if join_path is None:
        return None
    aliases = {t: _sql_alias(t) for t in tables_needed}
    root = tables_needed[0]
    sources = [_subquery(validated[root], aliases[root])]
    for ft, fc, tt, tc in join_path:
        sources.append(f"JOIN {_subquery(validated[tt], aliases[tt])} "
                       f"ON {aliases[ft]}.{_sql_col(fc)} = {aliases[tt]}.{_sql_col(tc)}")
    return sources, aliases


def _filter_edges(relationships: list[dict]) -> list[tuple[str, str, str, str]]:
    """(source_table, source_col, target_table, target_col): the directions a filter flows.
    Power BI filters the many side from the one side (the relationship's To → From); a
    both-directions relationship flows the other way too."""
    edges = []
    for r in relationships or []:
        ends = _rel_ends(r)
        if ends is None:
            continue
        ft, fc, tt, tc = ends
        edges.append((tt, tc, ft, fc))
        if "both" in str(r.get("CrossFilteringBehavior") or "").lower():
            edges.append((ft, fc, tt, tc))
    return edges


_COL = "\u00abCOL\u00bb"          # placeholder for the filtered column while a condition is translated
_CMP_KIND = {0: "=", 1: ">", 2: ">=", 3: "<", 4: "<="}


def _filter_literal(node: Any) -> str | None:
    """A filter's literal as a SQL literal: numbers as they are, text quoted, `datetime'...'` as a
    DATE/TIMESTAMP, null as NULL. Booleans (Teradata has none) and anything odd → None."""
    raw = ((node or {}).get("Literal") or {}).get("Value") if isinstance(node, dict) else None
    if not isinstance(raw, str):
        return None
    t = raw.strip()
    m = re.fullmatch(r"datetime'(\d{4}-\d{2}-\d{2})(?:T([\d:.]*))?'", t)
    if m:
        time = (m.group(2) or "").rstrip("0:.") 
        return f"DATE '{m.group(1)}'" if not time else f"TIMESTAMP '{m.group(1)} {m.group(2)}'"
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return t                                        # already SQL-quoted ('' is an escaped quote in both)
    if t.lower() == "null":
        return "NULL"
    if re.fullmatch(r"-?\d+L", t):
        return t[:-1]
    if re.fullmatch(r"-?\d+(?:\.\d+)?[DM]", t):
        return t[:-1]
    return None


def _like_pattern(lit: str, prefix: str, suffix: str) -> str | None:
    if not (lit.startswith("'") and lit.endswith("'")):
        return None
    body = lit[1:-1].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{_COL} LIKE '{prefix}{body}{suffix}' ESCAPE '\\'"


def _filter_condition_sql(cond: Any, prop: str) -> str | None:
    """One filter-pane condition on column `prop` as SQL over `_COL`, or None when it isn't a shape
    this translates (TopN's subquery, several columns in one In, booleans...). Blanks follow
    Power BI: a negated condition keeps them."""
    if not isinstance(cond, dict) or len(cond) != 1:
        return None
    kind, body = next(iter(cond.items()))

    def is_col(node: Any) -> bool:
        return isinstance(node, dict) and (node.get("Column") or {}).get("Property") == prop

    if kind == "In":
        exprs, rows = body.get("Expressions") or [], body.get("Values") or []
        if len(exprs) != 1 or not is_col(exprs[0]) or not rows:
            return None
        lits = [_filter_literal(r[0]) if isinstance(r, list) and len(r) == 1 else None for r in rows]
        if any(x is None for x in lits):
            return None
        vals = [x for x in lits if x != "NULL"]
        parts = ([f"{_COL} IN ({', '.join(vals)})"] if vals else []) + ([f"{_COL} IS NULL"] if "NULL" in lits else [])
        return "(" + " OR ".join(parts) + ")"
    if kind == "Comparison":
        op, lit = _CMP_KIND.get(body.get("ComparisonKind")), _filter_literal(body.get("Right"))
        if op is None or lit is None or not is_col(body.get("Left")):
            return None
        return f"{_COL} IS NULL" if lit == "NULL" and op == "=" else f"{_COL} {op} {lit}"
    if kind == "Between" and is_col(body.get("Expression")):
        lo, hi = _filter_literal(body.get("LowerBound")), _filter_literal(body.get("UpperBound"))
        return f"{_COL} BETWEEN {lo} AND {hi}" if lo and hi and "NULL" not in (lo, hi) else None
    if kind in ("And", "Or"):
        left, right = (_filter_condition_sql(body.get(k), prop) for k in ("Left", "Right"))
        return f"({left} {kind.upper()} {right})" if left and right else None
    if kind == "Not":
        inner = _filter_condition_sql(body.get("Expression"), prop)
        return f"(NOT ({inner}) OR {_COL} IS NULL)" if inner else None
    if kind in ("Contains", "StartsWith", "EndsWith") and is_col(body.get("Left")):
        lit = _filter_literal(body.get("Right"))
        pre, suf = {"Contains": ("%", "%"), "StartsWith": ("", "%"), "EndsWith": ("%", "")}[kind]
        return _like_pattern(lit, pre, suf) if lit else None
    return None


_AGG_FUNCTION = {0: "SUM({c})", 1: "AVG({c})", 2: "COUNT(DISTINCT {c})", 3: "MIN({c})", 4: "MAX({c})", 5: "COUNT({c})"}


def _topn_sql(f: dict, parameters: dict[str, dict] | None, table_map: dict[str, str] | None,
              relationships: list[dict] | None) -> tuple[str, str, str, list[str]] | None:
    """A Top N filter (`Top 1 of T[x] by Sum(T[y])`) as (table, column, SQL over `_COL`, slicer params).

    Power BI keeps the rows whose x is among the N best x values, ranked by an aggregate of y over the
    rows the report's slicers leave in T; ties are all kept (DAX TOPN). Built as
    `_COL IN (SELECT k FROM (SELECT x AS k, AGG(y) AS a FROM T WHERE <slicers on T> GROUP BY 1) t
    WHERE <count of better values> < N)`. Direction 1 = ascending, 2 = descending; aggregate
    codes per `_AGG_FUNCTION`. Anything else in the subquery → None. UNVERIFIED against Power BI."""
    definition, target = f.get("definition"), f.get("target") or ""
    if not isinstance(definition, dict) or "." not in target or table_map is None:
        return None
    table, prop = target.split(".", 1)
    try:
        sub = next(x for x in definition.get("From") or [] if (x.get("Expression") or {}).get("Subquery"))
        q = sub["Expression"]["Subquery"]["Query"]
        top = int(q["Top"])
        x = q["Select"][0]["Column"]["Property"]
        order = q["OrderBy"][0]
        agg = order["Expression"]["Aggregation"]
        y = agg["Expression"]["Column"]["Property"]
        template = _AGG_FUNCTION[agg["Function"]]
        direction = {1: "ASC", 2: "DESC"}[order["Direction"]]
        entity = q["From"][0]["Entity"]
        cond = definition["Where"][0]["Condition"]["In"]
        outer = cond["Expressions"][0]["Column"]["Property"]
    except (KeyError, IndexError, TypeError, ValueError, StopIteration):
        return None
    if entity != table or x != prop or outer != prop or top < 1 or table not in table_map:
        return None
    try:
        source = validate_read_only_sql(table_map[table])
    except ValueError:
        return None
    where, used = _draft_where(parameters or {}, {table: "h"}, table_map, relationships or [])
    w = "\nWHERE " + " AND ".join(where) if where else ""
    inner = (f"SELECT h.{_sql_col(x)} AS k, {template.format(c='h.' + _sql_col(y))} AS a\n"
             f"FROM {_subquery(source, 'h')}{w}\nGROUP BY 1")
    # rank = 1 + the number of strictly better values, so `count(better) < N` is RANK() <= N (ties kept) without
    # a window function: Teradata rejects ordered analytical functions inside a subquery (error 3706)
    better = "<" if direction == "ASC" else ">"
    return (table, prop,
            f"{_COL} IN (SELECT t.k FROM (\n{inner}\n) AS t WHERE (SELECT COUNT(*) FROM (\n{inner}\n) AS u "
            f"WHERE u.a {better} t.a) < {top})",
            used)


def filter_sql(f: dict, parameters: dict[str, dict] | None = None, table_map: dict[str, str] | None = None,
               relationships: list[dict] | None = None) -> tuple[str, str, str, list[str]] | None:
    """(table, column, SQL over `_COL`) for a filter-pane filter that constrains rows, or None:
    it has no condition (a field merely listed in the pane), it targets a measure, or it isn't a
    supported shape (see `_filter_condition_sql`). Every `Where` entry is ANDed."""
    if f.get("type") in ("TopN", "VisualTopN"):        # VisualTopN: PBIR's name; same subquery shape assumed
        return _topn_sql(f, parameters, table_map, relationships)
    target, definition = f.get("target") or "", f.get("definition")
    if (not isinstance(definition, dict) or "." not in target or f.get("aggregation") is not None
            or f.get("type") not in ("Categorical", "Advanced")):
        return None
    table, prop = target.split(".", 1)
    parts = [_filter_condition_sql((w or {}).get("Condition"), prop) for w in definition.get("Where") or []]
    if not parts or any(p is None for p in parts):
        return None
    return table, prop, " AND ".join(parts), []


def effective_filters(layout: dict, page: dict | None, v: dict | None) -> list[dict]:
    """The filter-pane filters that apply to one visual: report level, its page, the visual itself.
    A drill-through filter (`howCreated` 5) is left out: its saved value is only the last one the
    author tried; the real value comes from the page the user drilled through from, which the HTML
    doesn't model yet (the mapping report lists those pages)."""
    return [f for f in [*(layout.get("filters") or []), *((page or {}).get("filters") or []),
                        *((v or {}).get("filters") or [])]
            if f.get("definition") and f.get("how_created") != 5]      # 5: drill-through, see below


def unapplied_filters(filters: list[dict], tables_read: set[str] | None, table_map: dict[str, str],
                      relationships: list[dict], parameters: dict[str, dict] | None = None) -> list[str]:
    """Filters that constrain the visual in Power BI but can't be added to its query, as
    `Table.column (Type)`. A filter on a table the visual reads is a plain predicate; on another
    table it needs a direct relationship into the visual and that table's source query. A filter on
    a table no filter can reach (no relationship path) has no effect in Power BI either, so it is
    not reported."""
    edges = _filter_edges(relationships)
    read = tables_read or set()
    out = []
    for f in filters or []:
        t = (f.get("target") or "").split(".", 1)[0]
        placeable = filter_sql(f, parameters, table_map, relationships) is not None
        direct = t in read or (t in table_map and any(src == t and dst in read for src, _, dst, _ in edges))
        reach, frontier = {t}, [t]
        while frontier:                                   # can a filter on t reach what the visual reads?
            cur = frontier.pop()
            for src, _, dst, _ in edges:
                if src == cur and dst not in reach:
                    reach.add(dst)
                    frontier.append(dst)
        affects = bool(reach & read)
        if affects and not (placeable and direct):
            agg = ", on an aggregate" if f.get("aggregation") is not None else ""
            out.append(f"{f.get('target')} ({f.get('type')}{agg})")
    return out


def _filter_where(filters: list[dict] | None, aliases: dict[str, str], table_map: dict[str, str],
                  relationships: list[dict], parameters: dict[str, dict] | None = None
                  ) -> tuple[list[str], list[str]]:
    """WHERE predicates for the filter-pane filters (and the slicer parameters they read): on a column
    of a table the query reads → a plain predicate; on another table → a semi-join through a
    relationship, like a slicer. Filters that can't be placed are skipped (`unapplied_filters`
    reports them). The values come from the report file itself (not from an end user), quoted by
    `_filter_literal`."""
    edges = _filter_edges(relationships or [])
    out: list[str] = []
    used: list[str] = []
    for f in filters or []:
        parsed = filter_sql(f, parameters, table_map, relationships)
        if parsed is None:
            continue
        table, prop, cond, ps = parsed
        if table in aliases:
            out.append(cond.replace(_COL, f"{aliases[table]}.{_sql_col(prop)}"))
            used += [p for p in ps if p not in used]
            continue
        if table not in table_map:
            continue
        try:
            source = validate_read_only_sql(table_map[table])
        except ValueError:
            continue
        for src, scol, dst, dcol in edges:
            if src == table and dst in aliases:
                inner = cond.replace(_COL, f"{_sql_alias(table)}.{_sql_col(prop)}")
                out.append(f"{aliases[dst]}.{_sql_col(dcol)} IN (SELECT {_sql_col(scol)} FROM "
                           f"{_subquery(source, _sql_alias(table))} WHERE {inner})")
                used += [p for p in ps if p not in used]
                break
    return out, used


def _param_predicate(column: str, pname: str, p: dict, wrap: bool = True) -> str:
    """`col IN (:p)` for a value slicer; `col >= / <= CAST(:p AS DATE)` for a range bound,
    wrapped as optional (so `bind` drops it when that bound is empty) unless `wrap` is off
    (inside a semi-join the whole predicate is already wrapped: markers must not nest)."""
    bound = (p or {}).get("bound")
    if not bound:
        return f"{column} IN (:{pname})"
    rhs = f"CAST(:{pname} AS DATE)" if (p or {}).get("dtype") == "date" else f":{pname}"
    text = f"{column} {'>=' if bound == 'from' else '<='} {rhs}"
    return f"/*if {pname}*/ {text} /*fi {pname}*/" if wrap else text


def _as_text(sql: str) -> str:
    return f"TRIM(CAST({sql} AS VARCHAR(40)))"


_CTX_RE = re.compile(r"\{CTX:(MIN|MAX)\|([^|}]+)\|([^}]+)\}")


def _expand_ctxs(fields: list["_Field"], aliases: dict[str, str], sources: list[str], where_parts: list[str],
                 table_map: dict[str, str], parameters: dict[str, dict], categories: list["_Field"],
                 relationships: list[dict], filters: list[dict] | None = None
                 ) -> tuple[list[str], dict[str, str], list[str], list[str]] | None:
    """`MIN/MAX(T[c])` as a scalar: the value over the rows of T the report's filters leave, and, in
    a visual grouped by categories, over the rows of the current group. Returns (joins, {marker: sql},
    slicer params used, extra GROUP BY terms) or None when it can't be built.

    * a category is a column of T (calendar month over `MIN(Calendar[Date])`): T's own source,
      filtered by the slicers that reach T, grouped by those columns and LEFT JOINed on them;
    * T is read by the visual (a fact table) and there are categories: the visual's own FROM + WHERE,
      grouped by every category expression, joined back on them (null-safe);
    * otherwise (a card, or a category that doesn't filter T) one value for the whole selection, a
      one-row derived table CROSS JOINed in.

    A join and never a subquery: Teradata rejects subqueries inside an aggregate's argument. The
    joined value is unique per group, so it adds no rows; it is also added to GROUP BY so a use
    outside an aggregate (an IF's condition) is legal."""
    joins: list[str] = []
    repl: dict[str, str] = {}
    used: list[str] = []
    extra: list[str] = []
    cats = [c for c in categories if not c.is_value]

    def own_where(table: str) -> list[str] | None:
        """Predicates leaving T's rows as the slicers do (direct, or through a relationship)."""
        parts, ps = _draft_where(parameters, {table: "ctx"}, table_map, relationships, filters)
        used.extend(p for p in ps if p not in used)
        return parts

    for n, (fn, table, col) in enumerate(sorted({c for f in fields for c in f.ctxs}), start=1):
        alias = f"ctx{n}"
        group_cols = [c for c in cats if c.key[0] == table]
        if group_cols or table not in aliases:
            if table not in table_map:
                return None
            try:
                source = validate_read_only_sql(table_map[table])
            except ValueError:
                return None
            where = own_where(table)
            w = "\nWHERE " + " AND ".join(where) if where else ""
            keys = [f"ctx.{_sql_col(c.key[1])} AS k{i}" for i, c in enumerate(group_cols, start=1)]
            grp = "\nGROUP BY " + ", ".join(str(i) for i in range(1, len(keys) + 1)) if keys else ""
            inner = (f"SELECT {', '.join(keys + [f'{fn}(ctx.{_sql_col(col)}) AS v'])}\n"
                     f"FROM {_subquery(source, 'ctx')}{w}{grp}")
            on = " AND ".join(f"{alias}.k{i} = {c.expr}" for i, c in enumerate(group_cols, start=1))
        else:
            frm = "\n".join(sources)
            w = "\nWHERE " + " AND ".join(where_parts) if where_parts else ""
            keys = [f"{c.expr} AS k{i}" for i, c in enumerate(cats, start=1)]
            grp = "\nGROUP BY " + ", ".join(str(i) for i in range(1, len(keys) + 1)) if keys else ""
            inner = (f"SELECT {', '.join(keys + [f'{fn}({aliases[table]}.{_sql_col(col)}) AS v'])}\n"
                     f"FROM {frm}{w}{grp}")
            on = " AND ".join(f"({alias}.k{i} = {c.expr} OR ({alias}.k{i} IS NULL AND {c.expr} IS NULL))"
                              for i, c in enumerate(cats, start=1))
            group_cols = cats
        if group_cols:
            joins.append(f"LEFT JOIN (\n{inner}\n) AS {alias} ON {on}")
            extra.append(f"{alias}.v")
        else:
            joins.append(f"CROSS JOIN (\n{inner}\n) AS {alias}")
        repl[f"{{CTX:{fn}|{table}|{col}}}"] = f"{alias}.v"
    return joins, repl, used, extra


_SELMIN_RE = re.compile(r"\{SELMIN:([^|}]+)\|([^}]+)\}")


def _expand_selmins(fields: list["_Field"], aliases: dict[str, str], table_map: dict[str, str],
                    relationships: list[dict], parameters: dict[str, dict]
                    ) -> tuple[list[str], dict[str, str], list[str]] | None:
    """Turns the `{SELMIN:T|c}` markers of `fields` into SQL: (extra LEFT JOINs, {marker: predicate},
    slicer parameters used), or None when it can't be done (no source query for T, or no
    relationship from T to a table this visual reads).

    The marker stands for `FILTER(T, T[c] = MIN(T[c]))`: the rows of T at the lowest `c` among those
    the slicers on T leave. Here that is a derived table of T's key values (DISTINCT, so the join
    can't duplicate fact rows) LEFT JOINed to the fact table; the measure's CASE WHEN then tests
    `key IS NOT NULL`. It is a join rather than a subquery in the predicate because Teradata does
    not accept subqueries inside an aggregate's argument. With no slicer selection the predicates
    fall away (bind → `1=1`) and c's minimum is taken over all of T, exactly as DAX does."""
    joins: list[str] = []
    repl: dict[str, str] = {}
    used: list[str] = []
    edges = _filter_edges(relationships)
    for n, (table, col) in enumerate(sorted({m for f in fields for m in f.selmins}), start=1):
        if table not in table_map:
            return None
        try:
            source = validate_read_only_sql(table_map[table])
        except ValueError:
            return None
        edge = next(((sc, dst, dc) for src, sc, dst, dc in edges if src == table and dst in aliases), None)
        if edge is None:
            return None
        key_col, dst, dst_col = edge
        preds = []
        for pname, p in parameters.items():
            if not (p or {}).get("from_slicer"):
                continue
            _, ptable, pcol = query_ref_parts(p["from_slicer"])
            if ptable == table:
                preds.append(_param_predicate(f"hier.{_sql_col(pcol)}", pname, p))
                used.append(pname)
        where = "\nWHERE " + " AND ".join(preds) if preds else ""
        alias = f"selmin{n}"
        inner = (f"SELECT hier.{_sql_col(key_col)} AS k, hier.{_sql_col(col)} AS lvl, "
                 f"MIN(hier.{_sql_col(col)}) OVER () AS lvl_min\nFROM {_subquery(source, 'hier')}{where}")
        joins.append(f"LEFT JOIN (SELECT DISTINCT k FROM (\n{inner}\n) AS h WHERE lvl = lvl_min) AS {alias} "
                     f"ON {aliases[dst]}.{_sql_col(dst_col)} = {alias}.k")
        repl[f"{{SELMIN:{table}|{col}}}"] = f"{alias}.k IS NOT NULL"
    return joins, repl, list(dict.fromkeys(used))


def _draft_where(parameters: dict[str, dict], aliases: dict[str, str],
                 table_map: dict[str, str] | None = None,
                 relationships: list[dict] | None = None,
                 filters: list[dict] | None = None) -> tuple[list[str], list[str]]:
    """Slicer parameters that apply to the tables this visual reads.

    A slicer on a table the visual reads becomes `alias.col IN (:p)`. A slicer on a table it
    does not read still filters it in Power BI when a relationship connects them (a date slicer
    on a calendar filtering a fact table), so it is applied as a semi-join:
    `fact.key IN (SELECT key FROM <slicer table> WHERE col IN (:p))`. A semi-join does not
    duplicate rows and, wrapped in `/*if p*/ ... /*fi p*/`, `query.bind` drops the whole
    predicate when nothing is selected. Only one relationship hop, and only when the slicer
    table has a known source query."""
    table_map = table_map or {}
    edges = _filter_edges(relationships or [])
    where_parts, params_used = [], []

    predicate = _param_predicate

    for pname, p in parameters.items():
        slicer_ref = (p or {}).get("from_slicer")
        if not slicer_ref:
            continue
        _, ptable, pcol = query_ref_parts(slicer_ref)
        if ptable in aliases:
            # IN (...) rather than "=": works unchanged whether the parameter stays a
            # single value or someone later turns on `multi` — see query.py's bind().
            where_parts.append(predicate(f"{aliases[ptable]}.{_sql_col(pcol)}", pname, p))
            params_used.append(pname)
            continue
        if ptable not in table_map:
            continue
        try:
            source = validate_read_only_sql(table_map[ptable])
        except ValueError:
            continue
        seen: set[str] = set()
        for src, scol, dst, dcol in edges:
            if src != ptable or dst not in aliases or dst in seen:
                continue
            seen.add(dst)
            inner = predicate(f"{_sql_alias(ptable)}.{_sql_col(pcol)}", pname, p, wrap=False)
            where_parts.append(
                f"/*if {pname}*/ {aliases[dst]}.{_sql_col(dcol)} IN (SELECT {_sql_col(scol)} FROM "
                f"{_subquery(source, _sql_alias(ptable))} WHERE {inner}) /*fi {pname}*/")
            if pname not in params_used:
                params_used.append(pname)
    fw, fp = _filter_where(filters, aliases, table_map, relationships or [], parameters)
    where_parts += fw
    params_used += [p for p in fp if p not in params_used]
    return where_parts, params_used


def multi_fact(values: list["_Field"], categories: list["_Field"], kind: str, sort: list[dict] | None, *,
               expand_fn, table_map: dict[str, str], relationships: list[dict],
               parameters: dict[str, dict], filters: list[dict] | None = None) -> tuple[str, list[str]] | None:
    """One measure that aggregates several fact tables (`IF(cond, SUM(A[x]), CALCULATE(SUM(B[x]), ...))`).

    Power BI evaluates each aggregate on its own table for the current group; one SELECT over a join
    of both tables would multiply rows. So every fact table gets its own derived table (its aggregates
    per category, over that table joined only to the category tables), and the measure's expression is
    evaluated over those, on a query whose FROM is just the category tables. A group whose measure
    is blank is dropped, as in Power BI. Single value, cards and charts only; anything with a
    selection-level marker (`selmins`) is left manual, since that join needs the fact table."""
    if len(values) != 1 or not values[0].aggs or values[0].selmins:
        return None
    f = values[0]
    is_chart = kind in _CHART_KINDS
    if is_chart:
        if not 1 <= len(categories) <= 2 or (kind == "pie" and len(categories) != 1):
            return None
    elif kind not in ("card", "gauge", "kpi") or categories:
        return None
    cat_tables = list(dict.fromkeys(t for c in categories for t in c.tables))
    used: list[str] = []
    by_table: dict[str, list[tuple[int, str]]] = {}
    for n, (t, text) in enumerate(f.aggs):
        by_table.setdefault(t, []).append((n, text))

    def present(marker_set, text: str, fmt) -> set:
        return {m for m in marker_set if fmt(m) in text}

    def null_safe(alias: str) -> str:
        return " AND ".join(f"({alias}.k{i} = {c.expr} OR ({alias}.k{i} IS NULL AND {c.expr} IS NULL))"
                            for i, c in enumerate(categories, start=1))

    derived: list[tuple[str, str]] = []            # (alias, sql)
    agg_ref: dict[int, str] = {}
    for i, (table, items) in enumerate(by_table.items(), start=1):
        built = _draft_from_clause(list(dict.fromkeys(cat_tables + [table])), table_map, relationships)
        if built is None:
            return None
        srcs, als = built
        where, ps = _draft_where(parameters, als, table_map, relationships, filters)
        used += [p for p in ps if p not in used]
        pfs = [_Field("Y", True, f.label, f.out_name, text, {table}, f.key, set(),
                      present(f.ctxs, text, lambda c: f"{{CTX:{c[0]}|{c[1]}|{c[2]}}}"))
               for _, text in items]
        done = expand_fn(pfs, srcs, als, where, categories)
        if done is None:
            return None
        srcs, ps, extra = done
        used += [p for p in ps if p not in used]
        cols = [f"{c.expr} AS k{j}" for j, c in enumerate(categories, start=1)]
        cols += [f"{pf.expr} AS a{n}" for (n, _), pf in zip(items, pfs)]
        sql = "SELECT " + ", ".join(cols) + "\nFROM " + "\n".join(srcs)
        if where:
            sql += "\nWHERE " + " AND ".join(where)
        if categories:
            sql += "\nGROUP BY " + ", ".join([str(j) for j in range(1, len(categories) + 1)] + extra)
        derived.append((f"arm{i}", sql))
        for n, _ in items:
            agg_ref[n] = f"arm{i}.a{n}"

    if categories:
        built = _draft_from_clause(cat_tables, table_map, relationships)
        if built is None:
            return None
        msrcs, mals = built
        mwhere, ps = _draft_where(parameters, mals, table_map, relationships, filters)
        used += [p for p in ps if p not in used]
        msrcs = msrcs + [f"LEFT JOIN {_subquery(sql, a)} ON {null_safe(a)}" for a, sql in derived]
    else:
        mals, mwhere = {}, []
        msrcs = [_subquery(derived[0][1], derived[0][0])] + [f"CROSS JOIN {_subquery(sql, a)}"
                                                             for a, sql in derived[1:]]
    outer = _Field("Y", True, f.label, f.out_name, re.sub(r"\{AGG:(\d+)\}", lambda m: agg_ref[int(m.group(1))], f.expr),
                   set(), f.key, set(), set(f.ctxs))
    done = expand_fn([outer], msrcs, mals, mwhere, categories)
    if done is None:
        return None
    msrcs, ps, _extra = done
    used += [p for p in ps if p not in used]
    if not categories:
        return f'SELECT {outer.expr} AS "value"\nFROM ' + "\n".join(msrcs), used
    names = ["category", "series"]
    cols = [f"{c.expr} AS {names[i]}" for i, c in enumerate(categories)] + [f'{outer.expr} AS "value"']
    pos = {c.key: i for i, c in enumerate(categories, start=1)}
    pos.setdefault(f.key, len(categories) + 1)
    where = mwhere + [f"({outer.expr}) IS NOT NULL"]
    sql = ("SELECT DISTINCT " + ", ".join(cols) + "\nFROM " + "\n".join(msrcs)
           + "\nWHERE " + " AND ".join(where))
    return sql + _order_by(sort, pos), used


def _draft_visual_sql(v: dict, kind: str, measures: dict[tuple[str, str], str], table_map: dict[str, str],
                       relationships: list[dict], parameters: dict[str, dict],
                       filters: list[dict] | None = None) -> tuple[str, list[str]] | None:
    """Auto-draft one visual's SQL, or None to leave it a TODO.

    Covers card, kpi, gauge, pie, bar/column/line, table/matrix/multicard, returning
    exactly the columns each kind expects (see skill html-renderer). Each field is
    translated by `_resolve_field` → `translate_dax`, so a measure built out of other
    measures (a ratio, a filtered total, a difference) drafts like any other; only a
    field whose DAX isn't confidently translatable stops the whole visual.

    A chart with several measures becomes one series per measure via UNION ALL — that
    shape is common enough (revenue vs. cost vs. margin on one chart) that refusing it
    was a large part of why people still wrote most queries by hand."""
    if kind not in _DRAFTABLE_KINDS:
        return None
    fields: list[_Field] = []
    for role, refs in (v.get("projections") or {}).items():
        if role.lower() == "tooltips":       # only shown on hover; the renderer has no place for them
            continue
        for ref in refs or []:
            f = _resolve_field(role, ref, measures)
            if f is None:
                return None
            fields.append(f)
    if not fields:
        return None

    values = [f for f in fields if f.is_value]
    categories = [f for f in fields if not f.is_value]
    def expand(fs: list[_Field], srcs: list[str], als: dict[str, str], arm_where: list[str] | None = None,
               cats: list[_Field] | None = None) -> tuple[list[str], list[str], list[str]] | None:
        """Resolve selection-dependent markers (`_expand_selmins`, `_expand_ctxs`) in `fs`' expressions
        in place; returns the sources with the extra joins, the slicer parameters they use and
        the extra GROUP BY terms."""
        if not any(f.selmins or f.ctxs for f in fs):
            return srcs, [], []
        done = _expand_selmins(fs, als, table_map, relationships, parameters)
        ctx = _expand_ctxs(fs, als, srcs, arm_where if arm_where is not None else where_parts,
                           table_map, parameters, categories if cats is None else cats, relationships, filters)
        if done is None or ctx is None:
            return None
        joins, repl, used = done
        cjoins, crepl, cused, extra = ctx
        for f in fs:
            f.expr = _CTX_RE.sub(lambda m: crepl[m.group(0)], _SELMIN_RE.sub(lambda m: repl[m.group(0)], f.expr))
        return srcs + joins + cjoins, used + [u for u in cused if u not in used], extra

    sort = v.get("sort")

    # Aggregates over several fact tables cannot share one joined FROM: every extra table
    # multiplies the rows of the others and inflates the sums ("every value x k"). Power BI
    # aggregates each table separately; a single SELECT over the join does not, so those
    # visuals are left for a person rather than drafted into a plausible-looking wrong number.
    def value_tables(fs: list[_Field]) -> set[str]:
        return {t for f in fs for t in f.tables}

    if len(value_tables(values)) > 1 and not (kind in _CHART_KINDS and len(values) > 1
                                              and all(len(f.tables) == 1 for f in values)):
        return multi_fact(values, categories, kind, sort, expand_fn=expand,
                          table_map=table_map, relationships=relationships, parameters=parameters,
                          filters=filters)

    tables_needed = list(dict.fromkeys(t for f in fields for t in f.tables))
    built = _draft_from_clause(tables_needed, table_map, relationships)
    if built is None:
        return None
    sources, aliases = built
    where_parts, params_used = _draft_where(parameters, aliases, table_map, relationships, filters)

    # single-FROM shapes (everything but the multi-measure chart, which builds one FROM per arm)
    single_extra: list[str] = []
    multi_arm = kind in _CHART_KINDS and len(values) > 1 and len(categories) == 1 and kind != "pie"
    if not multi_arm:
        done = expand(fields, sources, aliases)
        if done is None:
            return None
        sources = done[0]
        params_used += [p for p in done[1] if p not in params_used]
        single_extra = done[2]

    def assemble(select_parts: list[str], group_positions: list[int], order: str = "",
                 sources_: list[str] | None = None, where_: list[str] | None = None,
                 extra_group: list[str] | None = None) -> str:
        sql = "SELECT " + ", ".join(select_parts) + "\nFROM " + "\n".join(sources_ or sources)
        if where_ is None:
            where_ = where_parts
        if where_:
            sql += "\nWHERE " + " AND ".join(where_)
        if group_positions:
            sql += "\nGROUP BY " + ", ".join([str(p) for p in group_positions]
                                              + (single_extra if extra_group is None else extra_group))
        return sql + order

    if kind in ("card", "gauge"):
        if len(values) != 1 or categories:
            return None
        return assemble([f'{values[0].expr} AS "value"'], []), params_used

    if kind == "kpi":
        # value + target, in field order; a kpi with only a value still renders.
        if not 1 <= len(values) <= 2 or categories:
            return None
        parts = [f'{values[0].expr} AS "value"']
        if len(values) == 2:
            parts.append(f"{values[1].expr} AS target")
        return assemble(parts, []), params_used

    if kind in _CHART_KINDS:
        if not values or not categories:
            return None
        if kind == "pie" and len(categories) != 1:
            return None
        if len(categories) > 2:
            return None
        if len(values) == 1:
            names = ["category", "series"]
            parts = [f"{c.expr} AS {names[i]}" for i, c in enumerate(categories)]
            parts.append(f'{values[0].expr} AS "value"')
            pos = {c.key: i for i, c in enumerate(categories, start=1)}
            pos.setdefault(values[0].key, len(categories) + 1)
            return assemble(parts, list(range(1, len(categories) + 1)), _order_by(sort, pos)), params_used
        # Several measures: one arm per measure, the measure's own name as the series.
        if len(categories) != 1 or kind == "pie":
            return None
        arms, all_params = [], []
        # two measures with the same column name (Sum of x over table A / table B): tell them apart
        dup = {f.label for f in values if [g.label for g in values].count(f.label) > 1}
        for f in values:
            arm_tables = list(dict.fromkeys([t for c in categories for t in c.tables] + sorted(f.tables)))
            arm_built = _draft_from_clause(arm_tables, table_map, relationships)
            if arm_built is None:
                return None
            arm_sources, arm_aliases = arm_built
            arm_where, arm_params = _draft_where(parameters, arm_aliases, table_map, relationships, filters)
            all_params += [p for p in arm_params if p not in all_params]
            arm_field = replace(f)
            done = expand([arm_field], arm_sources, arm_aliases, arm_where, categories)
            if done is None:
                return None
            arm_sources = done[0]
            arm_extra = done[2]
            all_params += [p for p in done[1] if p not in all_params]
            f = arm_field
            name = f"{next(iter(f.tables))}: {f.label}" if f.label in dup and f.tables else f.label
            label = name.replace("'", "''")
            arms.append(assemble([f"{categories[0].expr} AS category",
                                  f"'{label}' AS series",
                                  f'{f.expr} AS "value"'], [1, 2], "", arm_sources, arm_where, arm_extra))
        # a sort on the category applies to the whole union (column 1)
        return "\nUNION ALL\n".join(arms) + _order_by(sort, {categories[0].key: 1}), all_params

    # table / matrix / multicard: every field becomes a column, in projection order.
    parts, group_positions = [], []
    for i, f in enumerate(fields, start=1):
        parts.append(f"{f.expr} AS {f.out_name}")
        if not f.is_value:
            group_positions.append(i)
    pos: dict[tuple[str, str], int] = {}
    for i, f in enumerate(fields, start=1):
        pos.setdefault(f.key, i)
    return assemble(parts, group_positions if values else [], _order_by(sort, pos)), params_used


def diagnose_visual(v: dict, kind: str, measures: dict[tuple[str, str], str], table_map: dict[str, str],
                    relationships: list[dict], model_tables: set[str], parameters: dict,
                    filters: list[dict] | None = None) -> list[str]:
    """Why a data visual can (or cannot) be drafted into SQL, as reason codes: `ok`, or one or
    more of `kind:<kind>` (no drafter for it), `unknown_table:<t>` (visual points at a table the
    model doesn't have: renamed/deleted), `no_source:<t>` (no Teradata query for that table, e.g.
    a DAX calculated table), `not_connected:<t1>+<t2>` (no relationship between them), 
    `untranslatable_measure:<name>`, `shape` (fields don't fit the kind's column contract)."""
    if kind not in _DRAFTABLE_KINDS:
        return [f"kind:{kind}"]
    reasons: list[str] = []
    fields: list[_Field] = []
    for role, refs in (v.get("projections") or {}).items():
        if role.lower() == "tooltips":
            continue
        for ref in refs or []:
            _, table, col = query_ref_parts(ref)
            if (table, col) not in measures and model_tables and table not in model_tables:
                reasons.append(f"unknown_table:{table}")
                continue
            f = _resolve_field(role, ref, measures)
            if f is None:
                reasons.append(f"untranslatable_measure:{col}")
            else:
                fields.append(f)
    if reasons:
        return sorted(set(reasons))
    if not fields:
        return ["shape"]
    if _draft_visual_sql(v, kind, measures, table_map, relationships, parameters, filters):
        return ["ok"]
    tables = list(dict.fromkeys(t for f in fields for t in f.tables))
    missing = [t for t in [*tables, *sorted({m[0] for f in fields for m in f.selmins} | {c[1] for f in fields for c in f.ctxs})] if t not in table_map]
    if missing:
        return [f"no_source:{t}" for t in missing]
    if len(tables) > 1 and _find_join_path(tables, relationships) is None:
        return ["not_connected:" + "+".join(sorted(tables))]
    return ["shape"]


def mapping_report(layout: dict, model: dict, table_map: dict[str, str] | None = None) -> dict:
    """How well the model + visuals map to SQL: model facts worth a person's attention, and for
    every data visual the reason it was (or wasn't) drafted. Read-only: writes nothing."""
    table_map = table_map or {}
    tables = [t for t in (model.get("tables") or [])
              if isinstance(t, str) and not _AUTO_DATE_TABLE_RE.match(t)]
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    rels = model.get("relationships") if isinstance(model.get("relationships"), list) else []
    params = _slicer_parameters(layout, model)
    calc_tables = {t.get("TableName"): (t.get("Expression") or "") for t in model.get("calculated_tables") or []
                   if not _AUTO_DATE_TABLE_RE.match(t.get("TableName") or "")}
    visuals: dict[str, list[str]] = {}
    not_applied: dict[str, list[str]] = {}
    n_data = 0
    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group"):
                continue
            kind = KIND_MAP.get(v["type"], "custom" if v.get("is_custom") else "unsupported")
            if kind in NO_DATA_KINDS:
                continue
            n_data += 1
            label = f"{page.get('display_name')} / {v.get('title') or v['type']}"
            vfilters = effective_filters(layout, page, v)
            reasons = diagnose_visual(v, kind, measures, table_map, rels, set(tables),
                                      _params_for_page(params, page.get("display_name")), vfilters)
            for reason in reasons:
                visuals.setdefault(reason, []).append(label)
            if "ok" in reasons:
                for f in unapplied_filters(vfilters, set(_entities_used(v)), table_map, rels,
                                        _params_for_page(params, page.get("display_name"))):
                    not_applied.setdefault(f, []).append(label)
    # hidden pages: reachable ones (a visible page's button navigates to them, transitively) are
    # rendered; the rest are left out (tooltip/drillthrough pages nobody links to)
    pages = layout["pages"]
    by_name = {p.get("name"): p for p in pages if p.get("name")}
    reach = [p for p in pages if not p.get("hidden")]
    seen = {id(p) for p in reach}
    while reach:
        for v in reach.pop()["visuals"]:
            a = v.get("action") or {}
            t = by_name.get(a.get("page")) if a.get("type") == "page" and a.get("enabled") else None
            if t is not None and id(t) not in seen:
                seen.add(id(t))
                reach.append(t)
    hidden_pages = {"reachable": [p.get("display_name") for p in pages if p.get("hidden") and id(p) in seen],
                    "left_out": [p.get("display_name") for p in pages if p.get("hidden") and id(p) not in seen]}
    table_modes = {t: m for t, m in (model.get("table_modes") or {}).items() if t in tables}
    drillthrough = {p.get("display_name"): [f"{f.get('target')}" + (" (saved value ignored)" if f.get("definition") else "")
                                            for f in p.get("filters") or [] if f.get("how_created") == 5]
                    for p in pages}
    drillthrough = {k: v for k, v in drillthrough.items() if v}
    measure_names = {n for (_, n) in measures}
    composite = sorted(n for (_, n), dax in measures.items()
                       if any(ref in measure_names for ref in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", dax or "")))
    return {
        "report": layout.get("report"),
        "model": {
            "tables": len(tables), "mapped": sorted(t for t in tables if t in table_map),
            "unmapped": sorted(t for t in tables if t not in table_map and t not in calc_tables),
            "calculated_tables": {t: re.sub(r"\s+", " ", e).strip()[:120] for t, e in calc_tables.items()},
            "calculated_columns": [f"{c.get('TableName')}.{c.get('ColumnName')}"
                                   for c in model.get("calculated_columns") or []
                                   if not _AUTO_DATE_TABLE_RE.match(c.get("TableName") or "")],
            "relationships": len(rels),
            "storage_modes": table_modes,
            "drillthrough_pages": drillthrough,
            "hidden_pages": hidden_pages,
            "many_to_many": [f"{e[0]} → {e[2]}" for r in rels if r.get("Cardinality") == "M:M"
                             and (e := _rel_ends(r))],
            "unrelated_tables": sorted(t for t in tables if not any(
                t in (e[0], e[2]) for r in rels if (e := _rel_ends(r)))),
            "composite_measures": composite,
            "rls_rules": len(model.get("rls") or []),
            # fact tables whose Power Query adds a date column: the likely join key for an
            # undeclared calendar relationship (a hint, never applied automatically)
            "date_key_hint": sorted(_tables_with_date_key(model)),
        },
        "visuals": {"data_visuals": n_data, "drafted": len(visuals.get("ok", [])),
                    "by_reason": {k: v for k, v in sorted(visuals.items()) if k != "ok"},
                    "filters_not_applied": {k: sorted(set(v)) for k, v in sorted(not_applied.items())}},
    }


def render_mapping_report(rep: dict) -> str:
    m, v = rep["model"], rep["visuals"]
    L = [f"# Mapping coverage: {rep['report']}", "",
         f"- Data visuals: **{v['data_visuals']}**, SQL auto-drafted: **{v['drafted']}**, "
         f"needing a person: **{v['data_visuals'] - v['drafted']}**",
         f"- Tables: {m['tables']} (Teradata query known for {len(m['mapped'])}), "
         f"relationships: {m['relationships']}, RLS rules: {m['rls_rules']}", ""]
    if m["calculated_tables"]:
        L += ["## Calculated tables (DAX, no Teradata source)", ""]
        L += [f"- `{t}` = `{e}`" for t, e in m["calculated_tables"].items()]
        if m["calculated_columns"]:
            L += ["- calculated columns: " + ", ".join(f"`{c}`" for c in m["calculated_columns"])]
        L += [""]
    if m.get("drillthrough_pages"):
        L += ["## Drill-through pages", "",
              "Pages with drill-through fields (`howCreated` 5, taken from Power BI's enum: verify). Drilling through from another page passes the value of the "
              "field below. The HTML has no such navigation yet, so these pages render for all values "
              "(a saved value in the file is not applied: it was just the last one the author tried).", ""]
        L += [f"- `{p}`: " + ", ".join(f"`{x}`" for x in fs) for p, fs in m["drillthrough_pages"].items()]
        L += [""]
    kinds = sorted(set(m["storage_modes"].values()))
    if len(kinds) > 1:
        L += ["## Composite model (mixed storage modes)", ""]
        for k in kinds:
            L += [f"- {k}: " + ", ".join(f"`{t}`" for t, x in sorted(m["storage_modes"].items()) if x == k)]
        L += ["", "Import tables that come from Teradata become live queries after migration (inline or calculated ones stay in the yaml): "
              "their data can differ from the (stale) copy inside the .pbix, so validate them against a fresh refresh.", ""]
    hp = m["hidden_pages"]
    if hp["reachable"] or hp["left_out"]:
        L += ["## Hidden pages", ""]
        if hp["reachable"]:
            L += ["- rendered (a button navigates to them): " + ", ".join(f"`{n}`" for n in hp["reachable"])]
        if hp["left_out"]:
            L += ["- left out (nothing links to them; `--include-hidden` keeps them): "
                  + ", ".join(f"`{n}`" for n in hp["left_out"])]
        L += [""]
    if m["unmapped"]:
        L += ["## Tables without a Teradata query", "", ", ".join(f"`{t}`" for t in m["unmapped"]), ""]
    if m["unrelated_tables"]:
        L += ["## Tables with no relationship at all", "",
              ", ".join(f"`{t}`" for t in m["unrelated_tables"]),
              "", "A visual combining one of these with another table cannot be joined automatically; in "
              "Power BI such a visual shows unfiltered values, so check that the report really means that.", ""]
    if m["date_key_hint"] and m["calculated_tables"]:
        L += ["## Hint: possible undeclared calendar relationship", "",
              "These tables' Power Query adds a date column named `log_dt`: " +
              ", ".join(f"`{t}`" for t in m["date_key_hint"]) + ". A `Calendar[Date] = log_dt` "
              "relationship is probably what the report intends, but the model doesn't declare it "
              "(confirm with the report owner; it is not applied automatically).", ""]
    if m["many_to_many"]:
        L += ["## Many-to-many relationships", "", *[f"- {r}" for r in m["many_to_many"]],
              "", "A join over these can duplicate rows and inflate sums; `validate` will show it.", ""]
    if m["composite_measures"]:
        L += ["## Measures built from other measures", "", ", ".join(f"`{n}`" for n in m["composite_measures"]), ""]
    if v["by_reason"]:
        L += ["## Why visuals were not drafted", ""]
        for reason, labels in v["by_reason"].items():
            L += [f"- `{reason}` × {len(labels)}: " + "; ".join(labels[:4]) + (" ..." if len(labels) > 4 else "")]
        L += [""]
    if v.get("filters_not_applied"):
        L += ["## Filter-pane filters not applied to drafted SQL", "",
              "These filters constrain visuals in Power BI but the draft can't express them (TopN, "
              "relative date, an unsupported shape) or has no path to their table: the numbers will "
              "differ until they are added by hand.", ""]
        for f, labels in v["filters_not_applied"].items():
            L += [f"- `{f}` × {len(labels)}: " + "; ".join(labels[:3]) + (" ..." if len(labels) > 3 else "")]
        L += [""]
    return "\n".join(L)


AUTOFILL_NOTE = ("Auto-drafted (rule-based, see skill dax-to-teradata-sql) — review the columns "
                 "and any JOIN, then run validate before trusting it, same as a hand-written sql. "
                 "A JOIN here can duplicate rows and inflate totals if the relationship's "
                 "direction/multiplicity doesn't match what this visual needs (see skill "
                 "validate-report, \"every value × k\").")


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
    parameters: dict[str, dict] = _slicer_parameters(layout, model)

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
            if kind == "tooltip":
                tip = v.get("tooltip") or {}
                entry["title"] = tip.get("header")
                entry["text"] = tip.get("text")
                entry["notes"] = (f"Custom visual '{v.get('custom_type') or v['type']}' reinterpreted as an info "
                                  "icon; header and text are the ones set in the report.")
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
            vfilters = effective_filters(layout, page, v)
            draft = _draft_visual_sql(v, kind, measures, table_map, relationships,
                                      _params_for_page(parameters, page["display_name"]), vfilters)
            if draft:
                entry["sql"], entry["params"] = draft
                notes.append(AUTOFILL_NOTE)
                skipped = unapplied_filters(vfilters, set(_entities_used(v)), table_map, relationships,
                                            _params_for_page(parameters, page["display_name"]))
                if skipped:
                    notes.append("Filter-pane filters NOT applied to this SQL (unsupported shape, e.g. TopN, "
                                 "or no path to the table): " + ", ".join(skipped) + ".")
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
        "slicers": slicers_section(layout, parameters, table_map, model),
        "roles": {r.get("RoleName", "role"): {"proxy_user": None, "where": None,
                                             "dax": r.get("FilterExpression"), "table": r.get("TableName")}
                  for r in rls} or {"default": {"proxy_user": None, "where": None}},
        "visuals": visuals,
    }


def _slug(text: str) -> str:
    return re.sub(r"\W+", "_", str(text or "")).strip("_").lower() or "x"


def slicer_descriptor(v: dict) -> dict:
    """The slicer description of a layout visual, with defaults for layouts extracted before
    `parse_slicer` existed: a plain multi-select list over the visual's fields."""
    d = v.get("slicer")
    if isinstance(d, dict) and d.get("fields") is not None:
        return d
    return {"mode": "list", "fields": list(v.get("fields") or []), "single": False, "select_all": True,
            "initial": {}, "style": {}}


_BOUNDS_BY_MODE = {"between": ("from", "to"), "before": ("to",), "after": ("from",)}


def _column_dtype(model: dict | None, ref: str) -> str:
    """'date' | 'number' | 'text' for `Table.Column`, from the model's column list."""
    table, _, col = ref.partition(".")
    for c in (model or {}).get("columns") or []:
        if isinstance(c, dict) and c.get("TableName") == table and c.get("ColumnName") == col:
            kind = str(c.get("PandasDataType") or "").lower()
            return "date" if "datetime" in kind else "number" if any(k in kind for k in ("int", "float", "decimal")) else "text"
    return "text"


def _slicer_parameters(layout: dict, model: dict | None = None) -> dict[str, dict]:
    """Every slicer of the report as named parameters, scoped like Power BI scopes them.

    Slicers on different pages are independent unless they share a sync group, so a parameter
    belongs to the page (or sync group) it was set on: the same field on two pages gives two
    parameters (`org_name__elastic_compute`, `org_name__node_pool`), each with its own saved
    selection. A field used on one page keeps the plain column name. A `between`/`before`/
    `after` slicer yields `<name>_from` / `<name>_to` bound parameters. The slicer's saved
    selection becomes the default."""
    entries: dict[tuple[str, str | None], dict[str, dict]] = {}
    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group") or KIND_MAP.get(v.get("type", ""), "") != "slicer":
                continue
            d = slicer_descriptor(v)
            scope = d.get("sync_group") or f"page:{page['display_name']}"
            label = d.get("sync_group") or page["display_name"]
            for ref in d.get("fields") or []:
                for bound in _BOUNDS_BY_MODE.get(d.get("mode"), (None,)):
                    slot = entries.setdefault((ref, bound), {}).setdefault(scope, {
                        "label": label, "pages": [], "d": d, "sync": d.get("sync_group")})
                    if page["display_name"] not in slot["pages"]:
                        slot["pages"].append(page["display_name"])
    # base names, disambiguated between different tables that share a column name
    def base(ref: str, bound: str | None) -> str:
        return _slug(ref.partition(".")[2]) + (f"_{bound}" if bound else "")
    by_base: dict[str, set[str]] = {}
    for (ref, bound) in entries:
        by_base.setdefault(base(ref, bound), set()).add(ref)
    parameters: dict[str, dict] = {}
    for (ref, bound), scopes in entries.items():
        stem = base(ref, bound)
        if len(by_base[stem]) > 1:
            stem = f"{_slug(ref.partition('.')[0])}_{stem}"
        dtype = "date" if bound and _column_dtype(model, ref) == "text" else _column_dtype(model, ref)
        for scope, slot in scopes.items():
            name = stem if len(scopes) == 1 else f"{stem}__{_slug(slot['label'])}"
            n = 2
            while name in parameters:
                name, n = f"{name}_{n}", n + 1
            d, initial = slot["d"], slot["d"].get("initial") or {}
            if bound:
                default = ((initial.get("range") or {}).get(ref) or {}).get(bound)
            else:
                vals = (initial.get("values") or {}).get(ref)
                default = None if not vals else (vals[0] if d.get("single") else list(vals))
            p: dict[str, Any] = {"type": "string", "default": default, "from_slicer": ref,
                                 "multi": bool(not bound and not d.get("single")),
                                 "label": ref.partition(".")[2], "dtype": dtype, "pages": slot["pages"]}
            if bound:
                p["bound"] = bound
            if slot["sync"]:
                p["sync"] = slot["sync"]
            parameters[name] = p
    return parameters


def _params_for_page(parameters: dict[str, dict], page_name: str | None) -> dict[str, dict]:
    """The parameters a visual on `page_name` may use: those set by a slicer on its page (or in a
    sync group that reaches it). A parameter without a `pages` list (an older yaml, or one added
    by hand) applies everywhere."""
    return {n: p for n, p in parameters.items()
            if not (p or {}).get("pages") or page_name in (p or {}).get("pages", [])}


def slicer_params(v: dict, page_name: str, parameters: dict[str, dict]) -> list[str]:
    """Parameter names a slicer visual drives, one per level (or per bound), in field order."""
    d = slicer_descriptor(v)
    out: list[str] = []
    for ref in d.get("fields") or []:
        for bound in _BOUNDS_BY_MODE.get(d.get("mode"), (None,)):
            for name, p in _params_for_page(parameters, page_name).items():
                if (p or {}).get("from_slicer") == ref and (p or {}).get("bound") == bound:
                    out.append(name)
                    break
    return out


def _slicer_options_sql(v: dict, table_map: dict[str, str], calendars: dict[str, dict]) -> str | None:
    """Distinct values for a slicer's widget: one column per level (all levels must come from
    the same table with a known source). A calendar-derived table is ordered chronologically
    (by the earliest date of each value); anything else by value. None for range slicers or
    when the source is unknown."""
    d = slicer_descriptor(v)
    if d.get("mode") in _BOUNDS_BY_MODE or not d.get("fields"):
        return None
    tables = {ref.partition(".")[0] for ref in d["fields"]}
    if len(tables) != 1:
        return None
    table = next(iter(tables))
    if table not in table_map:
        return None
    try:
        source = validate_read_only_sql(table_map[table])
    except ValueError:
        return None
    alias = _sql_alias(table)
    cols = [f"{alias}.{_sql_col(ref.partition('.')[2])}" for ref in d["fields"]]
    cal = calendars.get(table)
    if cal:
        order = f"MIN({alias}.{_sql_col(cal['date_column'])})"
    else:
        order = ", ".join(str(i) for i in range(1, len(cols) + 1))
    select = ", ".join(f"{c} AS level{i}" for i, c in enumerate(cols, start=1))
    group = ", ".join(str(i) for i in range(1, len(cols) + 1))
    if cal:
        return f"SELECT {select}\nFROM {_subquery(source, alias)}\nGROUP BY {group}\nORDER BY {order}"
    return f"SELECT DISTINCT {select}\nFROM {_subquery(source, alias)}\nORDER BY {order}"


def slicers_section(layout: dict, parameters: dict[str, dict], table_map: dict[str, str] | None,
                    model: dict | None = None) -> dict[str, dict]:
    """The yaml `slicers:` section: for each slicer visual, the parameters it drives and the SQL
    of its distinct values. Written for people to edit, like `visuals:`."""
    calendars = detect_calendar_tables(model or {})
    out: dict[str, dict] = {}
    for page in layout["pages"]:
        for v in page["visuals"]:
            if v.get("is_group") or KIND_MAP.get(v.get("type", ""), "") != "slicer":
                continue
            names = slicer_params(v, page["display_name"], parameters)
            entry: dict[str, Any] = {"page": page["display_name"], "params": names}
            sql = _slicer_options_sql(v, table_map or {}, calendars)
            if sql:
                entry["options_sql"] = sql
            out[v["id"]] = entry
    return out


def autofill(raw: dict, layout: dict, model: dict, table_map: dict[str, str] | None = None,
             redraft: bool = False) -> dict[str, Any]:
    """Fills in what's still missing in an existing metrics yaml, touching nothing that
    already has an answer. Returns {"raw": <updated>, "filled": [...], "already": [...],
    "still_todo": [...], "parameters_added": [...], "slicers_added": [...]}.

    This is the difference between this and regenerating: `write_scaffold(overwrite=True)`
    rebuilds the file from the .pbix and throws away every hand-written query, so it was
    the only way to pick up a better draft — including after the translator itself
    improves. Here, a visual whose `sql` someone has actually written is left exactly as
    it is, and only the `TODO`s get a draft.

    With `redraft`, a visual whose notes still say "Auto-drafted" is drafted again too: what an earlier version of
    the drafter wrote (before filters, Top N, reserved words...) is replaced by the current draft, while SQL a
    person wrote (no such note) is still left alone. A visual the drafter can't do any more keeps its old SQL.

    Each visual is drafted against its *current* `kind` in the yaml, not the one the
    .pbix implies — someone who retyped a custom visual as a `column` in the editor
    should get a draft for a column chart, which is the whole point of that override."""
    measures = {(m.get("TableName"), m.get("Name")): m.get("Expression")
                for m in (model.get("measures") or []) if isinstance(m, dict)}
    relationships = model.get("relationships") if isinstance(model.get("relationships"), list) else []
    table_map = table_map or {}

    raw = dict(raw)
    parameters = dict(raw.get("parameters") or {})
    added_parameters = []
    for name, p in _slicer_parameters(layout, model).items():
        if name not in parameters:          # never overwrite a default someone set
            parameters[name] = p
            added_parameters.append(name)
    raw["parameters"] = parameters
    # `slicers:` (ADR-006) is written by scaffold only, so a yaml older than that feature has none and the
    # live HTML's requests for slicer options found nothing (HTTP 404). Add the missing entries; never
    # touch one that exists (someone may have hand-edited its options_sql).
    slicers = dict(raw.get("slicers") or {})
    added_slicers = []
    for vid, entry in slicers_section(layout, parameters, table_map, model).items():
        if vid not in slicers:
            slicers[vid] = entry
            added_slicers.append(vid)
    if slicers:
        raw["slicers"] = slicers

    visuals = dict(raw.get("visuals") or {})
    by_id = {v["id"]: v for page in layout["pages"] for v in page["visuals"]}
    page_of = {v["id"]: page for page in layout["pages"] for v in page["visuals"]}
    filled, already, still_todo = [], [], []

    for vid, entry in visuals.items():
        entry = dict(entry or {})
        kind = entry.get("kind") or KIND_MAP.get((by_id.get(vid) or {}).get("type", ""), "unsupported")
        if kind in NO_DATA_KINDS:
            continue
        auto = "Auto-drafted" in (entry.get("notes") or "")
        if not is_unwritten_sql(entry.get("sql")) and not (redraft and auto):
            already.append(vid)
            continue
        source = by_id.get(vid)
        draft = (_draft_visual_sql(source, kind, measures, table_map, relationships,
                                   _params_for_page(parameters, entry.get("page")),
                                   effective_filters(layout, page_of.get(vid), source))
                 if source else None)
        if draft is None:
            still_todo.append(vid)
            continue
        entry["sql"], entry["params"] = draft
        notes = entry.get("notes") or ""
        if "Auto-drafted" not in notes:
            entry["notes"] = f"{notes} {AUTOFILL_NOTE}".strip()
        visuals[vid] = entry
        filled.append(vid)
    raw["visuals"] = visuals
    return {"raw": raw, "filled": filled, "already": already,
            "still_todo": still_todo, "parameters_added": added_parameters, "slicers_added": added_slicers}


YAML_HEADER = ("# Report semantic layer. Edit by hand: this is where the migrated logic lives.\n"
               "# Column contracts per kind: .claude/skills/html-renderer/SKILL.md\n")


BACKUP_DIR_NAME = "backups"


def backup_yaml(report: str) -> Path | None:
    """Copies the current metrics/<report>.yaml to metrics/backups/<report>.<stamp>.yaml
    before something overwrites it. Returns the backup path, or None if there was
    nothing to back up yet.

    Regenerating the scaffold discards every hand-written SQL in the file, and the only
    thing standing between a person and that loss used to be one browser confirm()
    dialog — which anyone clicks through by habit. A copy on disk costs nothing and is
    something a non-technical person can actually be pointed at ("your previous version
    is in metrics/backups/"), unlike telling them to have used git."""
    path = yaml_path(report)
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = path.parent / BACKUP_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing backup: the stamp is only second-resolution, and two
    # saves inside the same second are entirely normal (regenerate, then immediately
    # restore). Silently replacing the older file would throw away the very version
    # someone is about to reach for.
    dest = folder / f"{report}.{stamp}.yaml"
    n = 2
    while dest.exists():
        dest = folder / f"{report}.{stamp}-{n}.yaml"
        n += 1
    dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def list_backups(report: str) -> list[Path]:
    """Existing backups for a report, newest first — what the panel offers as
    "restore a previous version"."""
    folder = yaml_path(report).parent / BACKUP_DIR_NAME
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{report}.*.yaml"), reverse=True)


def restore_backup(report: str, backup: Path) -> Path:
    """Puts a backup back as the live metrics/<report>.yaml, backing up whatever is
    there right now first — so restoring is itself undoable and can't be the move that
    loses work."""
    backups_dir = (yaml_path(report).parent / BACKUP_DIR_NAME).resolve()
    resolved = backup.resolve()
    if resolved.parent != backups_dir or not resolved.is_file():
        raise ValueError(f"{backup} isn't a backup of {report}")
    content = resolved.read_text(encoding="utf-8")   # read first: backup_yaml() writes into this same folder
    backup_yaml(report)
    path = yaml_path(report)
    path.write_text(content, encoding="utf-8")
    return path


def write_scaffold(layout: dict, model: dict, overwrite: bool = False,
                    table_map: dict[str, str] | None = None) -> Path:
    path = yaml_path(layout["report"])
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists; use --overwrite to regenerate (you'll lose the written SQL)")
    if path.exists():
        backup_yaml(layout["report"])   # overwrite is destructive; keep the old one
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


def multi_values(val: Any) -> list:
    """A multi-value parameter as a list. Accepts a list, `a, b`, or the text of a list (`[2026]`,
    `['a', 'b']`) - what a yaml default looked like after a form round-trip that wrote `str(list)` (the panel's
    edit page did, and the live report then sent `year=[2026]`, which Teradata could not convert)."""
    import ast

    def unwrap(x: Any) -> list:
        if isinstance(x, str) and x.strip().startswith("[") and x.strip().endswith("]"):
            try:
                parsed = ast.literal_eval(x.strip())
            except (ValueError, SyntaxError):
                return [x.strip()[1:-1].strip("'\" ")]
            return list(parsed) if isinstance(parsed, (list, tuple)) else [parsed]
        return [x]

    if val is None:
        return []
    if isinstance(val, str):
        if val.strip().startswith("["):
            return unwrap(val)
        return [x.strip() for x in val.split(",") if x.strip()]
    return [y for x in val for y in unwrap(x)]


def resolve_params(spec: ReportSpec, overrides: dict[str, str]) -> dict[str, Any]:
    """Final parameter values: yaml default overridden by --params k=v."""
    values: dict[str, Any] = {}
    for name, p in spec.parameters.items():
        p = p or {}  # a parameter written as "year:" with nothing under it parses as None
        val = overrides.get(name, p.get("default"))
        if p.get("multi") and val is not None:
            val = multi_values(val)
        if p.get("type") == "int" and val is not None and not isinstance(val, list):
            val = int(val)
        values[name] = val
    unknown = set(overrides) - set(spec.parameters)
    if unknown:
        raise KeyError(f"Parameters not defined in the yaml: {sorted(unknown)}")
    return values
