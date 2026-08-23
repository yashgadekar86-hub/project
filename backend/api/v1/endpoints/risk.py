"""Risk management, settings, and kill-switch endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user
from backend.api.schemas import KillSwitchRequest, UserSettingsIn, UserSettingsOut
from backend.core.redis_client import is_kill_switch_active, set_kill_switch
from backend.core.state import STATE
from backend.database.session import get_db
from backend.models.user import User, UserSettings
from trading_engine.strategies import list_strategies

router = APIRouter(tags=["Risk, Settings & System"])


@router.get("/settings", response_model=UserSettingsOut)
async def get_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    if not settings:
        settings = UserSettings(user_id=user.id); db.add(settings); await db.commit(); await db.refresh(settings)
    return settings


@router.put("/settings", response_model=UserSettingsOut)
async def update_settings(body: UserSettingsIn, user: User = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    if not settings:
        settings = UserSettings(user_id=user.id); db.add(settings)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(settings, k, v)
    # Apply kill-switch state to in-memory + Redis
    if body.kill_switch_active is not None:
        STATE.kill_switch_active = body.kill_switch_active
        try:
            await set_kill_switch(body.kill_switch_active)
        except Exception:
            pass
    await db.commit(); await db.refresh(settings)
    return settings


@router.post("/system/kill-switch")
async def kill_switch(body: KillSwitchRequest, user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    if body.confirmation != "CONFIRM":
        raise HTTPException(status_code=400, detail="Send confirmation='CONFIRM' to activate")
    STATE.kill_switch_active = body.active
    try:
        await set_kill_switch(body.active)
    except Exception:
        pass
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user.id))).scalar_one_or_none()
    if settings:
        settings.kill_switch_active = body.active
        await db.commit()

    # Optionally close all paper positions
    if body.close_positions:
        positions = STATE.paper_positions.get(str(user.id), [])
        closed = []
        for p in positions:
            tick = STATE.latest_ticks.get(p.symbol)
            if tick:
                price = tick["bid"] if p.direction == "BUY" else tick["ask"]
                spec = STATE.symbol_specs.get(p.symbol)
                pip_size = spec.pip_size if spec else 1e-4
                tick_val = spec.tick_value if spec else 10
                diff = (price - p.entry_price) if p.direction == "BUY" else (p.entry_price - price)
                pips = diff / pip_size if pip_size else 0
                pnl = pips * tick_val * (pip_size/(spec.tick_size or pip_size)) * p.volume if spec else 0
                STATE.paper_trades.setdefault(str(user.id), []).append({
                    "ticket": p.ticket, "symbol": p.symbol, "direction": p.direction,
                    "volume": p.volume, "entry": p.entry_price, "exit": price, "pnl": pnl,
                    "closed_at": datetime.now(timezone.utc).isoformat(), "exit_reason": "kill_switch",
                })
                STATE.paper_balance[str(user.id)] = STATE.paper_balance.get(str(user.id),10000) + pnl
                closed.append(p.ticket)
        STATE.paper_positions[str(user.id)] = [p for p in positions if p.ticket not in closed]
    return {"active": body.active, "close_positions": body.close_positions, "reason": body.reason}


@router.get("/strategies")
async def strategies(user: User = Depends(get_current_user)):
    return list_strategies()


@router.get("/system/health")
async def health(user: User = Depends(get_current_user)):
    from backend.core.config import settings as cfg
    import time
    import psutil
    now = datetime.now(timezone.utc)
    comps = []
    # DB
    db_ok = True
    try:
        from backend.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            await s.execute(select(1))
        db_latency = 1
    except Exception as e:
        db_ok = False; db_latency = 0
    comps.append({"component":"database","status":"HEALTHY" if db_ok else "CRITICAL","latency_ms":db_latency,"last_heartbeat":now})
    # Redis
    redis_ok = False; redis_lat = 0
    try:
        from backend.core.redis_client import get_redis
        r = await get_redis()
        t0 = time.time(); await r.ping(); redis_lat = int((time.time()-t0)*1000); redis_ok = True
    except Exception: pass
    comps.append({"component":"redis","status":"HEALTHY" if redis_ok else "WARNING","latency_ms":redis_lat,"last_heartbeat":now})
    # MT5
    conn = STATE.mt5_connectors.get(f"user:{user.id}")
    comps.append({"component":"mt5","status":"HEALTHY" if conn and conn.connected else "WARNING","last_heartbeat":now,"message":("connected" if conn and conn.connected else "not connected")})
    # AI service (always local in this build)
    comps.append({"component":"ai_engine","status":"HEALTHY","last_heartbeat":now})
    # Execution engine
    comps.append({"component":"execution_engine","status":"CRITICAL" if STATE.kill_switch_active else "HEALTHY","last_heartbeat":now,"message":"kill switch active" if STATE.kill_switch_active else "running"})
    # Market data
    stale = 0
    for sym, tick in STATE.latest_ticks.items():
        try:
            if (now - datetime.fromisoformat(tick["time"])).total_seconds() > 30:
                stale += 1
        except Exception: pass
    md_status = "HEALTHY" if STATE.latest_ticks and stale == 0 else ("WARNING" if stale else "CRITICAL")
    comps.append({"component":"market_data","status":md_status,"last_heartbeat":now,"message":f"{len(STATE.latest_ticks)} symbols streamed"})
    # CPU/RAM
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory().percent
    comps.append({"component":"cpu","status":"HEALTHY" if cpu < 80 else "WARNING","latency_ms":cpu,"message":f"{cpu}%","last_heartbeat":now})
    comps.append({"component":"memory","status":"HEALTHY" if ram < 85 else "WARNING","message":f"{ram}%","last_heartbeat":now})
    return {"components": comps, "kill_switch_active": STATE.kill_switch_active}


@router.get("/sessions")
async def sessions():
    from trading_engine.utils.sessions import current_sessions, next_session
    return {"current": current_sessions(), "next": next_session()}
