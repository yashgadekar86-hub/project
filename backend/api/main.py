"""
FastAPI entrypoint — AI Forex Command Center.

Run with: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.core.config import settings
from backend.core.logging import configure_logging, log_event
from backend.core.redis_client import close_redis, get_redis
from backend.database.init_db import init_db
from backend.api.v1.router import api_router


# ---- Pure-ASGI middleware: convert absolute-URL 3xx Location to relative ----
# Starlette's redirect_slashes responds with 307/308 whose Location header is
# an absolute URL pointing at the backend's host/port (e.g.
# http://localhost:8000/api/v1/signals/). When the backend runs behind the
# Next.js dev proxy (or any reverse proxy) and the browser follows that
# redirect, the Location becomes cross-origin and the browser strips the
# Authorization header → silent 401 → instant logout. This middleware rewrites
# such Location headers to path-only (relative) URLs so the redirect stays
# same-origin through the proxy and auth headers are preserved.
#
# Implemented as a raw ASGI middleware (NOT BaseHTTPMiddleware) so it sits
# cleanly outside the Starlette routing/Mount stack and can intercept the
# redirect responses emitted by redirect_slashes itself.
class RelativeRedirectMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        started = False

        async def wrapped_send(message):
            nonlocal started
            if message["type"] == "http.response.start" and not started:
                started = True
                status = message.get("status", 0)
                if 300 <= status < 400:
                    new_headers = []
                    for name, value in message.get("headers", []):
                        if name.lower() == b"location":
                            loc = value.decode("latin-1")
                            parsed = urlparse(loc)
                            if parsed.scheme or parsed.netloc:
                                new_loc = urlunparse(("", "", parsed.path, parsed.params, parsed.query, parsed.fragment))
                                new_headers.append((b"location", new_loc.encode("latin-1")))
                                continue
                        new_headers.append((name, value))
                    message = {**message, "headers": new_headers}
            await send(message)

        await self.inner(scope, receive, wrapped_send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log_event("system", "boot", f"{settings.APP_NAME} starting up (env={settings.APP_ENV})")
    try:
        await init_db()
    except Exception as e:
        log_event("system", "db_init", f"DB init failed (non-fatal in dev): {e}", severity="WARNING")
    try:
        r = await get_redis()
        await r.ping()
        log_event("system", "redis", "Redis connected")
    except Exception as e:
        log_event("system", "redis", f"Redis unavailable: {e} (using in-memory fallback)", severity="WARNING")
    yield
    await close_redis()
    log_event("system", "shutdown", f"{settings.APP_NAME} shutting down")


app = FastAPI(
    title="AI Forex Command Center",
    description=(
        "Production-grade AI-powered Forex trading platform for MetaTrader 5. "
        "Separates BACKTEST / PAPER / LIVE modes. Requires explicit user activation "
        "for live trading. The Risk Engine is never bypassed."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# IMPORTANT: add middleware OUTSIDE to INSIDE order. RelativeRedirect is added
# FIRST so it ends up outermost (it wraps everything added later, including
# CORS and the router).
app.add_middleware(RelativeRedirectMiddleware)

if settings.APP_ENV == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/", tags=["Meta"])
async def root():
    return {
        "app": settings.APP_NAME,
        "version": "0.1.0",
        "env": settings.APP_ENV,
        "docs": "/docs",
        "trading_mode": "PAPER",
        "safety": {
            "live_trading_requires_explicit_activation": True,
            "default_risk_per_trade_pct": settings.DEFAULT_RISK_PER_TRADE_PCT,
            "default_max_daily_loss_pct": settings.DEFAULT_MAX_DAILY_LOSS_PCT,
            "default_max_drawdown_pct": settings.DEFAULT_MAX_DRAWDOWN_PCT,
            "kill_switch_available": True,
        },
        "disclaimer": (
            "Trading FX involves substantial risk of loss. Past performance does not "
            "guarantee future results. Never trade with money you cannot afford to lose."
        ),
    }


@app.get("/healthz", tags=["Meta"])
async def healthz():
    return {"status": "ok"}


# ---- API v1 ----
app.include_router(api_router)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):  # pragma: no cover
    log_event("system", "unhandled_exception", str(exc), severity="ERROR")
    if settings.APP_DEBUG:
        raise
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/internal/generate-fernet-key", include_in_schema=False)
def generate_key():
    from backend.core.security import generate_fernet_key
    return {"fernet_key": generate_fernet_key()}
