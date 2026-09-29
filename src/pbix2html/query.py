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
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import settings
from .semantic import ReportSpec, VisualSpec, fix_teradata_sql

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
    """A small pool of Teradata sessions, one query at a time per session.

    `serve.py` runs each request in a worker thread and the report fires every visual at once, so a
    single shared connection meant concurrent requests interleaved on one session: their
    `SET QUERY_BAND` (the identity Teradata applies row-level security as) overwrote each other, so a
    query could run as another user's proxy, and the driver serialises or rejects overlapping requests.
    Now a request checks a session out, sets its identity, runs, clears the identity and returns it.
    Any error discards that session (its identity or state can't be trusted), and the next request opens
    a fresh one, so a dropped connection heals instead of failing every later request. Close with `close()`."""
    proxy_user: str | None = None
    max_connections: int = 4
    _idle: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _slots: threading.Semaphore | None = None

    def _connect(self):
        import teradatasql  # late import: tests don't need it
        kwargs = dict(host=settings.teradata_host, user=settings.teradata_user,
                      password=settings.teradata_password, logmech=settings.teradata_logmech,
                      encryptdata="true")
        if settings.teradata_database:
            kwargs["database"] = settings.teradata_database
        return teradatasql.connect(**kwargs)

    def _checkout(self):
        with self._lock:
            if self._slots is None:
                self._slots = threading.Semaphore(self.max_connections)
            slots = self._slots
        slots.acquire()
        try:
            with self._lock:
                if self._idle:
                    return self._idle.pop()
            return self._connect()
        except BaseException:
            slots.release()
            raise

    def _checkin(self, con, healthy: bool) -> None:
        if healthy:
            with self._lock:
                self._idle.append(con)
        else:
            try:
                con.close()
            except Exception:
                pass
        self._slots.release()

    def execute(self, sql: str, values: list[Any], proxy_user: str | None = None) -> DataBlock:
        proxy = proxy_user or self.proxy_user
        con = self._checkout()
        healthy = False
        try:
            with con.cursor() as cur:
                if proxy:
                    # Trusted session: Teradata evaluates secure views/RLS as `proxy`.
                    cur.execute(f"SET QUERY_BAND = 'PROXYUSER={_safe_ident(proxy)};APPNAME=pbix2html;' FOR SESSION;")
                # `AS value` is a syntax error on Teradata (reserved word): yamls drafted before the drafter
                # quoted it, hand-written SQL and the template still say it
                cur.execute(fix_teradata_sql(sql), values)
                columns = [d[0].lower() for d in cur.description]
                rows = [[_jsonable(c) for c in r] for r in cur.fetchall()]
                if proxy:
                    cur.execute("SET QUERY_BAND = NONE FOR SESSION;")
            healthy = True
            return {"columns": columns, "rows": rows}
        finally:
            self._checkin(con, healthy)

    def close(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for con in idle:
            try:
                con.close()
            except Exception:
                pass


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


def _cache_key(report: str, visual_id: str, values: dict[str, Any], proxy_user: str | None,
               sql: str | None = None) -> Path:
    """Cache file for one result. The SQL text is part of the key so editing a query in the yaml
    never serves the previous query's rows."""
    h = hashlib.sha1(json.dumps([report, visual_id, values, proxy_user, sql], sort_keys=True, default=str).encode()).hexdigest()
    return CACHE_DIR / f"{report}.{visual_id}.{h[:12]}.json"


def run_visual(spec: ReportSpec, visual: VisualSpec, values: dict[str, Any], backend: Backend,
               proxy_user: str | None = None, use_cache: bool = True, ttl: int | None = None) -> DataBlock:
    if not visual.has_data:
        return {"columns": [], "rows": [], "skipped": True}
    ttl = settings.cache_ttl_seconds if ttl is None else ttl
    key = _cache_key(spec.report, visual.id, {k: values.get(k) for k in visual.params}, proxy_user, visual.sql)
    if use_cache and key.exists() and time.time() - key.stat().st_mtime < ttl:
        return json.loads(key.read_text(encoding="utf-8"))
    sql, bound = bind(visual.sql, visual.params, values)
    block = backend.execute(sql, bound, proxy_user)
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        key.write_text(json.dumps(block, ensure_ascii=False, default=str), encoding="utf-8")
    return block


def run_slicer_options(spec: ReportSpec, visual_id: str, backend: Backend, proxy_user: str | None = None,
                       use_cache: bool = True, ttl: int | None = None) -> DataBlock:
    """Distinct values for one slicer's widget (yaml `slicers.<visual>.options_sql`, one column per
    hierarchy level). A slicer without a query comes back `skipped`: its widget then accepts typed
    values instead of offering a list."""
    entry = (spec.raw.get("slicers") or {}).get(visual_id) or {}
    sql = entry.get("options_sql")
    if not sql or "TODO" in sql:
        return {"columns": [], "rows": [], "skipped": True}
    ttl = settings.cache_ttl_seconds if ttl is None else ttl
    key = _cache_key(spec.report, f"slicer-{visual_id}", {}, proxy_user, sql)
    if use_cache and key.exists() and time.time() - key.stat().st_mtime < ttl:
        return json.loads(key.read_text(encoding="utf-8"))
    block = backend.execute(sql, [], proxy_user)
    if use_cache:
        CACHE_DIR.mkdir(exist_ok=True)
        key.write_text(json.dumps(block, ensure_ascii=False, default=str), encoding="utf-8")
    return block


def run_slicers(spec: ReportSpec, backend: Backend, proxy_user: str | None = None,
                use_cache: bool = True) -> dict[str, DataBlock]:
    out: dict[str, DataBlock] = {}
    for vid in (spec.raw.get("slicers") or {}):
        try:
            out[vid] = run_slicer_options(spec, vid, backend, proxy_user, use_cache)
        except Exception as e:
            out[vid] = {"columns": [], "rows": [], "error": f"{type(e).__name__}: {e}"}
    return out


def run_report(spec: ReportSpec, values: dict[str, Any], backend: Backend, proxy_user: str | None = None,
               use_cache: bool = True) -> dict[str, DataBlock]:
    out: dict[str, DataBlock] = {}
    for vid, visual in spec.visuals.items():
        try:
            out[vid] = run_visual(spec, visual, values, backend, proxy_user, use_cache)
        except Exception as e:  # the error is shown inside the visual, it doesn't take down the report
            out[vid] = {"columns": [], "rows": [], "error": f"{type(e).__name__}: {e}"}
    return out
