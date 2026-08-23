"""WebSocket endpoint for real-time updates."""
from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend.websocket.manager import manager
from backend.database.session import AsyncSessionLocal
from backend.core.config import settings

router = APIRouter()

DEV_BYPASS_TOKEN = "dev-bypass-aifx-demo-user"


async def _ws_user_from_token(token: str):
    from backend.core.security import decode_token
    from sqlalchemy import select
    from backend.models.user import User
    if not token:
        return None
    # Dev-bypass magic token (dev only)
    if token == DEV_BYPASS_TOKEN and settings.APP_ENV == "development":
        from backend.api.deps import _get_dev_bypass_user
        async with AsyncSessionLocal() as db:
            return await _get_dev_bypass_user(db)
    try:
        payload = decode_token(token)
        uid = payload.get("sub")
        if not uid:
            return None
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.id == uid))
            user = result.scalar_one_or_none()
            if user and not user.is_active:
                return None
            return user
    except Exception:
        return None


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        auth = websocket.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1]
    user = await _ws_user_from_token(token or "")

    topics: List[str] = []
    if user:
        topics.append(f"user:{user.id}")
    topics.append("broadcast")
    await manager.connect(websocket, topics, user_id=str(user.id) if user else None)
    try:
        await websocket.send_json({
            "type": "system", "topic": "hello",
            "payload": {"authenticated": bool(user), "topics": topics},
        })
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "topic":"ping","payload":{}})
                continue
            action = data.get("action")
            topic = data.get("topic")
            async with manager._lock:
                if action == "subscribe" and topic:
                    manager.active.setdefault(topic, set()).add(websocket)
                elif action == "unsubscribe" and topic:
                    s = manager.active.get(topic, set())
                    s.discard(websocket)
                    if not s:
                        manager.active.pop(topic, None)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(websocket)
