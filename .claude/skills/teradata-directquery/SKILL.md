---
name: teradata-directquery
description: Connecting to Teradata from query.py and serve.py (teradatasql), trusted sessions with PROXYUSER for per-user security, result caching, and querying DBQL to capture the SQL Power BI generated. Use when touching query.py, serve.py, .env, or anything that runs SQL.
---

# Teradata for pbix2html

## Connection

Driver: `teradatasql` (pure Python, no ODBC). Configuration only via environment
variables (`.env.example`): `TERADATA_HOST`, `TERADATA_USER`, `TERADATA_PASSWORD`,
`TERADATA_LOGMECH` (`TD2` by default; `LDAP`, `KRB5`, `BROWSER` for SSO). Never
credentials in the yaml or in code.

```python
import teradatasql
con = teradatasql.connect(host=H, user=U, password=P, logmech=LOGMECH, encryptdata="true")
with con.cursor() as cur:
    cur.execute("SELECT ... WHERE anio = ?", [2026])   # positional ? parameters
    cols = [d[0].lower() for d in cur.description]
    rows = cur.fetchall()
```

Parameters: always `?` (DB-API). Lists (`IN`) are expanded in `query.py` by building
`?,?,?` — never concatenate values.

## Per-user security: trusted sessions

The service account shouldn't see everything. Teradata lets it act *on behalf of* a user:

```sql
-- DBA, once:
GRANT CONNECT THROUGH svc_pbix2html TO PERMANENT usuario1, usuario2 WITH ROLE rol_ventas;
-- or WITHOUT ROLE to inherit the proxy user's own roles.

-- The app, per session/request:
SET QUERY_BAND = 'PROXYUSER=usuario1;APPNAME=pbix2html;REPORT=Ventas;' FOR SESSION;
```

After `SET QUERY_BAND`, secure views that filter by `USER`/`CURRENT_ROLE` and RLS
constraints are evaluated as `usuario1`, and DBQL records `ProxyUser`. To go back to the
service account: `SET QUERY_BAND = NONE FOR SESSION;` or close the session.
`serve.py` takes the user from the auth token; **never** from a URL parameter.

In `snapshot` mode there's no end user: it runs with `--role X`, which in the yaml maps
to a `PROXYUSER` representative of the role, or to a fixed predicate. One HTML = one role.

## Power BI model RLS → Teradata

`model.json → rls` carries `RoleName`, `TableName`, `FilterExpression` (DAX). Typical
pattern: `[Region] = LOOKUPVALUE(Seguridad[Region], Seguridad[Usuario],
USERPRINCIPALNAME())`. Teradata equivalent: a secure view

```sql
REPLACE VIEW sec.v_ventas AS
SELECT v.* FROM ventas v
JOIN seguridad_usuario s ON s.region = v.region
WHERE s.usuario = USER;   -- USER = the proxy user after SET QUERY_BAND
```

and the yaml queries `sec.v_ventas` instead of `ventas`. Record it in ADR-003.

## DBQL: capturing Power BI's SQL

Requires `SELECT` on `DBC.DBQLogTbl` and `DBC.DBQLSqlTbl` (ask the DBA for it). Query in
the `dax-to-teradata-sql` skill. If full SQL logging isn't active:
`BEGIN QUERY LOGGING WITH SQL ON PBI_GATEWAY_USER;`.

## Cache

`query.py` caches by key `(report, visual, sorted params, proxy_user)` in `cache/`
(snapshot) or in memory with a TTL (`CACHE_TTL_SECONDS`, live). Executive dashboards
change daily: a TTL of a few hours is reasonable; it's invalidated on regeneration.

## Costs and good practices

- One visual = one query. Before optimizing, measure with `EXPLAIN`.
- Prefer aggregated tables/views in Teradata for measures shared across reports.
- `SAMPLE` only for development; never in a production yaml.
- Timeouts: `teradatasql` has no native query timeout; `SET SESSION`... doesn't exist for
  this; control it with `asyncio.wait_for` in `serve.py` and `ABORT SESSION` if needed.
