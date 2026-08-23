"""Aggregate all v1 routers."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.v1.endpoints import auth, mt5, signals, orders, dashboard, backtest, risk, websocket

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(mt5.router)
api_router.include_router(signals.router)
api_router.include_router(orders.router)
api_router.include_router(dashboard.router)
api_router.include_router(backtest.router)
api_router.include_router(risk.router)
api_router.include_router(websocket.router)
