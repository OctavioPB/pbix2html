"""
Live mode: HTTP service that runs the yaml on demand with trusted sessions.

    python -m uvicorn pbix2html.serve:app --reload

The user's identity must come from authentication (a header set by the reverse
proxy/SSO, or a token validated here). NEVER from a URL parameter.
`AUTH_HEADER` (default `X-Authenticated-User`) is what the reverse proxy injects after SSO.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from . import semantic
from .query import TeradataBackend, run_slicer_options, run_visual

AUTH_HEADER = os.getenv("AUTH_HEADER", "X-Authenticated-User")
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() == "true"
_CORS_ORIGINS_ENV = os.getenv("CORS_ORIGINS")

app = FastAPI(title="pbix2html live")
# Credentialed CORS (cookies) needs a specific Access-Control-Allow-Origin, never "*" —
# a browser rejects that combination outright. The report's own fetch doesn't send
# credentials (identity comes from a proxy-injected header, see below), so this only
# matters if a deployment explicitly sets CORS_ORIGINS to forward an SSO cookie through
# a reverse proxy; leaving CORS_ORIGINS unset keeps the open "*" default, no credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS_ENV.split(",") if _CORS_ORIGINS_ENV else ["*"],
    allow_credentials=_CORS_ORIGINS_ENV is not None,
    allow_methods=["GET"], allow_headers=["*"],
)


class _AllowPrivateNetworkAccess:
    """Chrome's Local Network Access (née Private Network Access) is a *separate*
    check from CORS: a page fetching a loopback/private address gets an extra
    preflight requiring this header, which Starlette's CORSMiddleware doesn't know
    about — no combination of CORS settings above adds it. Best-effort only: newer
    Chrome versions reportedly gate this behind an interactive permission prompt
    instead and may ignore the header entirely; if a live report still fails after
    this, check the browser console for the exact message (not just "Failed to
    fetch") and whether Chrome is showing a local-network permission prompt for it.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                message["headers"].append((b"access-control-allow-private-network", b"true"))
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(_AllowPrivateNetworkAccess)


_spec_cache: dict[str, tuple[float | None, semantic.ReportSpec]] = {}


def _spec(report: str) -> semantic.ReportSpec:
    """Reloads metrics/<report>.yaml when its mtime changes, not on every request.

    A plain lru_cache here (the previous approach) would serve the report's *first*
    load forever: the panel's edit page (step 2c) can save a new sql to this file at
    any time while serve.py keeps running — that's the whole point of editing
    everything from the UI without restarting scripts — and a cache with no
    invalidation would silently keep answering with the old sql (or make an edited
    visual look stuck on "No query defined" if it was still a TODO the first time
    this ran). Checking mtime keeps the cheap-in-the-common-case behavior lru_cache
    was there for, without the staleness.
    """
    path = semantic.yaml_path(report)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    cached = _spec_cache.get(report)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        spec = semantic.load(report)
    except FileNotFoundError:
        # metrics/, reports/, out/ are all relative to serve.py's own working directory
        # — a very common way to land here is starting uvicorn from somewhere else
        # entirely. Naming the exact path it looked for (and where it's actually
        # running from) turns a bare 404 into something fixable from the browser alone.
        raise HTTPException(
            404,
            f"report {report!r} has no yaml at {semantic.yaml_path(report).resolve()}. "
            f"serve.py is running from {Path.cwd()} — start it from the project root "
            f"(the folder metrics/ and reports/ are in) instead."
        )
    _spec_cache[report] = (mtime, spec)
    return spec


def _backend() -> TeradataBackend:
    if not hasattr(app.state, "backend"):
        app.state.backend = TeradataBackend()
    return app.state.backend


def _param_values(spec, request: Request) -> dict:
    """Parameter values from the query string. A multi-select parameter arrives as repeated keys
    (`?org=A&org=B`, so a value may contain a comma); a single one as one key."""
    overrides: dict = {}
    for key in dict.fromkeys(request.query_params.keys()):
        vals = request.query_params.getlist(key)
        multi = ((spec.parameters.get(key) or {}).get("multi"))
        overrides[key] = vals if multi else vals[-1]
    try:
        return semantic.resolve_params(spec, overrides)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.get("/reports/{report}/slicers/{visual_id}")
def slicer_options(report: str, visual_id: str, request: Request):
    """Distinct values for a slicer widget (no parameters: a slicer lists all its values)."""
    spec = _spec(report)
    if visual_id not in (spec.raw.get("slicers") or {}):
        raise HTTPException(404, "slicer not defined in the yaml")
    user = request.headers.get(AUTH_HEADER)
    if REQUIRE_AUTH and not user:
        raise HTTPException(401, f"missing header {AUTH_HEADER} (SSO)")
    try:
        return run_slicer_options(spec, visual_id, _backend(), proxy_user=user, use_cache=True)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/reports/{report}/visuals/{visual_id}")
def visual(report: str, visual_id: str, request: Request):
    spec = _spec(report)
    v = spec.visuals.get(visual_id)
    if v is None:
        raise HTTPException(404, "visual not defined in the yaml")
    user = request.headers.get(AUTH_HEADER)
    if REQUIRE_AUTH and not user:
        raise HTTPException(401, f"missing header {AUTH_HEADER} (SSO)")
    values = _param_values(spec, request)
    try:
        return run_visual(spec, v, values, _backend(), proxy_user=user, use_cache=True)
    except Exception as e:
        # An unhandled exception (e.g. a Teradata connection/driver error) produces a
        # response with no CORS headers at all — the browser rejects it before the
        # report's JS ever sees the real error, showing "Failed to fetch" instead of
        # whatever actually went wrong. Raising HTTPException instead keeps CORS headers
        # on the response so the report can show the real error inline in the visual.
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/reports/{report}")
def report_info(report: str):
    """Confirms serve.py can find and parse this report's yaml — no query runs, no
    auth required. Meant for a quick "is this actually set up, not just up" check
    (see gui.py's live-mode convert step) that /healthz alone can't answer: /healthz
    succeeds regardless of serve.py's working directory, so it can't catch "running,
    but from the wrong folder" the way an actual /reports/{report}/visuals/... 404 does."""
    spec = _spec(report)
    return {"ok": True, "report": spec.report, "visuals": sorted(spec.visuals.keys())}


@app.get("/healthz")
def healthz():
    return {"ok": True}
