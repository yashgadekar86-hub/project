"""Connection manager for WebSockets."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket
from pydantic import BaseModel


class WSMessage(BaseModel):
    type: str                       # e.g. "tick", "signal", "position", "alert", "system"
    topic: str
    payload: Dict[str, Any]
    timestamp: datetime = None

    def __init__(self, **data):
        super().__init__(**data)
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class ConnectionManager:
    def __init__(self) -> None:
        self.active: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, topics: List[str], user_id: Optional[str] = None) -> None:
        await ws.accept()
        async with self._lock:
            for t in topics:
                self.active.setdefault(t, set()).add(ws)
            if user_id:
                self.active.setdefault(f"user:{user_id}", set()).add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            for topic, conns in list(self.active.items()):
                conns.discard(ws)
                if not conns:
                    self.active.pop(topic, None)

    async def broadcast(self, topic: str, msg: WSMessage) -> None:
        payload = json.dumps(msg.model_dump(mode="json"), default=str)
        dead: List[WebSocket] = []
        conns = list(self.active.get(topic, set()))
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


manager = ConnectionManager()
