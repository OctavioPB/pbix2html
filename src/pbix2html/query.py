"""
Runs SQL per visual against Teradata (migrated DirectQuery).

- Positional `?` parameters; lists expand to `IN (?,?,?)`.
- Trusted sessions: `SET QUERY_BAND = 'PROXYUSER=...'` so Teradata applies RLS.
- On-disk cache keyed by (report, visual, params, proxy_user) for snapshots.
- `FakeBackend` enables tests and development without Teradata.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import settings
from .semantic import ReportSpec, VisualSpec

CACHE_DIR = Path("cache")

DataBlock = dict[str, Any]   # {"columns": [...], "rows": [[...], ...]}

_PARAM_RE = re.compile(r":(\w+)\b")


def bind(sql: str, params: list[str], values: dict[str, Any]) -> tuple[str, list[Any]]:
    """
    Converts `WHERE year = :year AND region IN (:regions)` into SQL with `?` and a list of values.
    Only names declared in `params` are substituted; any other `:x` is left intact
    (e.g. time literals). Lists → `?,?,?`.

    A slicer with nothing selected means "no filter" in Power BI, so a predicate of the exact
    shape `<column> IN (:name)` whose parameter is empty (None, "" or an empty list) becomes
    `1=1` instead of `IN (NULL)`, which would return no rows at all (and so does a predicate
    wrapped in `/*if name*/ ... /*fi name*/`). Other uses of an empty
    parameter (e.g. `= :year`) are left to the query's author.
    """
    bound: list[Any] = []

    for name in params:
        val = values.get(name)
        if val is None or val == "" or (isinstance(val, (list, tuple)) and not val):
            # a whole predicate marked by the drafter (a semi-join through a relationship)
            sql = re.sub(rf"/\*if {re.escape(name)}\*/.*?/\*fi {re.escape(name)}\*/", "1=1", sql, flags=re.S)
            sql = re.sub(rf'(?<![\w."])[\w."]+\s+IN\s*\(\s*:{re.escape(name)}\s*\)', "1=1", sql, flags=re.I)

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in params:
            return m.group(0)
        val = values.get(name)
        if isinstance(val, (list, tuple)):
            if not val:
                return "NULL"
            bound.extend(val)
            return ",".join("?" * len(val))
        bound.append(val)
        return "?"

    return _PARAM_RE.sub(repl, sql), bound


class Backend(Protocol):
    def execute(self, sql: str, values: list[Any], proxy_user: str | None = None) -> DataBlock: ...


@dataclass
class TeradataBackend:
    """One connection per instance. Close with `close()`."""
    proxy_user: str | None = None
    _con: Any = None

    def _connect(self):
        import teradatasql  # late import: tests don't need it
        kwargs = dict(host=settings.teradata_host, user=settings.teradata_user,
                      password=settings.teradata_password, logmech=settings.teradata_logmech,
                      encryptdata="true")
        if settings.teradata_database:
            kwargs["database"] = settings.teradata_database
        self._con = teradatasql.connect(**kwargs)
        return self._con

    def execute(self, sql: str, values: list[Any], proxy_user: str | None = None) -> DataBlock:
        con = self._con or self._connect()
        proxy = proxy_user or self.proxy_user
        with con.cursor() as cur:
            if proxy:
                # Trusted session: Teradata evaluates secure views/RLS as `proxy`.
                cur.execute(f"SET QUERY_BAND = 'PROXYUSER={_safe_ident(proxy)};APPNAME=pbix2html;' FOR SESSION;")
            cur.execute(sql, values)
            columns = [d[0].lower() for d in cur.description]
            rows = [[_jsonable(c) for c in r] for r in cur.fetchall()]
            if proxy:
                cur.execute("SET QUERY_BAND = NONE FOR SESSION;")
        return {"columns": columns, "rows": rows}

    def close(self) -> None:
        if self._con:
            self._con.close()
            self._con = None


@dataclass
class FakeBackend:
    """Returns fixed data per SQL (or a default). For tests and development without Teradata."""
    fixtures: dict[str, DataBlock]
    default: DataBlock | None = None
    calls: list[tuple[str, list[Any], str | None]] | None = None

    def execute(self, sql: str, values: list[Any], proxy_user: str | None = None) -> DataBlock:
        if self.calls is not None:
            self.calls.append((sql, values, proxy_user))
        key = " ".join(sql.split())
        if key in self.fixtures:
            return self.fixtures[key]
        if self.default is not None:
            return self.default
        raise KeyError(f"FakeBackend has no fixture for: {key[:80]}")


def _safe_ident(s: str) -> str:
    if not re.fullmatch(r"[\w.@-]+", s):
        raise ValueError(f"invalid proxy_user: {s!r}")
    return s


def _jsonable(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.hex()
    try:
        from decimal import Decimal
        if isinstance(v, Decimal):
            return float(v)
    except ImportError:
        pass
    return v


def _cache_key(report: str, visual_id: str, values: dict[str, Any], proxy_user: str | None) -> Path:
    h = hashlib.sha1(json.dumps([report, visual_id, values, proxy_user], sort_keys=True, default=str).encode()).hexdigest()
    return CACHE_DIR / f"{report}.{visual_id}.{h[:12]}.json"


def run_visual(spec: ReportSpec, visual: VisualSpec, values: dict[str, Any], backend: Backend,
               proxy_user: str | None = None, use_cache: bool = True, ttl: int | None = None) -> DataBlock:
    if not visual.has_data:
        return {"columns": [], "rows": [], "skipped": True}
    ttl = settings.cache_ttl_seconds if ttl is None else ttl
    key = _cache_key(spec.report, visual.id, {k: values.get(k) for k in visual.params}, proxy_user)
    if use_cache and key.exists() and time.time() - key.stat().st_mtime < ttl:
        return json.loads(key.read_text(encoding="utf-8"))
    sql, bound = bind(visual.sql, visual.params, values)
    block = backend.execute(sql, bound, proxy_user)
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        key.write_text(json.dumps(block, ensure_ascii=False, default=str), encoding="utf-8")
    return block


def run_report(spec: ReportSpec, values: dict[str, Any], backend: Backend, proxy_user: str | None = None,
               use_cache: bool = True) -> dict[str, DataBlock]:
    out: dict[str, DataBlock] = {}
    for vid, visual in spec.visuals.items():
        try:
            out[vid] = run_visual(spec, visual, values, backend, proxy_user, use_cache)
        except Exception as e:  # the error is shown inside the visual, it doesn't take down the report
            out[vid] = {"columns": [], "rows": [], "error": f"{type(e).__name__}: {e}"}
    return out
