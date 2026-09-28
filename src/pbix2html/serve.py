"""
Live mode: HTTP service that runs the yaml on demand with trusted sessions.

    python -m uvicorn pbix2html.serve:app --reload

The user's identity must come from authentication (a header set by the reverse
proxy/SSO, or a token validated here). NEVER from a URL parameter.
`AUTH_HEADER` (default `X-Authenticated-User`) is what the reverse proxy injects after SSO.
"""
from __future__ import annotations

import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from . import semantic
from .query import TeradataBackend, run_visual

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


@lru_cache(maxsize=64)
def _spec(report: str) -> semantic.ReportSpec:
    try:
        return semantic.load(report)
    except FileNotFoundError:
        raise HTTPException(404, f"report {report} has no yaml")


def _backend() -> TeradataBackend:
    if not hasattr(app.state, "backend"):
        app.state.backend = TeradataBackend()
    return app.state.backend


@app.get("/reports/{report}/visuals/{visual_id}")
def visual(report: str, visual_id: str, request: Request):
    spec = _spec(report)
    v = spec.visuals.get(visual_id)
    if v is None:
        raise HTTPException(404, "visual not defined in the yaml")
    user = request.headers.get(AUTH_HEADER)
    if REQUIRE_AUTH and not user:
        raise HTTPException(401, f"missing header {AUTH_HEADER} (SSO)")
    try:
        values = semantic.resolve_params(spec, dict(request.query_params))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    try:
        return run_visual(spec, v, values, _backend(), proxy_user=user, use_cache=True)
    except Exception as e:
        # An unhandled exception (e.g. a Teradata connection/driver error) produces a
        # response with no CORS headers at all — the browser rejects it before the
        # report's JS ever sees the real error, showing "Failed to fetch" instead of
        # whatever actually went wrong. Raising HTTPException instead keeps CORS headers
        # on the response so the report can show the real error inline in the visual.
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.get("/healthz")
def healthz():
    return {"ok": True}
