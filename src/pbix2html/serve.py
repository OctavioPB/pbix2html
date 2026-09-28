"""
Live mode: HTTP service that runs the yaml on demand with trusted sessions.

    uvicorn pbix2html.serve:app --reload

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

app = FastAPI(title="pbix2html live")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
                   allow_credentials=True, allow_methods=["GET"], allow_headers=["*"])


@lru_cache(maxsize=64)
def _spec(report: str) -> semantic.ReportSpec:
    try:
        return semantic.load(report)
    except FileNotFoundError:
        raise HTTPException(404, f"reporte {report} no tiene yaml")


def _backend() -> TeradataBackend:
    if not hasattr(app.state, "backend"):
        app.state.backend = TeradataBackend()
    return app.state.backend


@app.get("/reports/{report}/visuals/{visual_id}")
def visual(report: str, visual_id: str, request: Request):
    spec = _spec(report)
    v = spec.visuals.get(visual_id)
    if v is None:
        raise HTTPException(404, "visual no definido en el yaml")
    user = request.headers.get(AUTH_HEADER)
    if REQUIRE_AUTH and not user:
        raise HTTPException(401, f"falta cabecera {AUTH_HEADER} (SSO)")
    try:
        values = semantic.resolve_params(spec, dict(request.query_params))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return run_visual(spec, v, values, _backend(), proxy_user=user, use_cache=True)


@app.get("/healthz")
def healthz():
    return {"ok": True}
