"""Async Redis client wrapper for cache, pub/sub, kill-switch state."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

log = logging.getLogger("aifx.redis")

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except Exception:  # pragma: no cover
    HAS_REDIS = False

from backend.core.config import settings

_redis: Optional[Any] = None
_mem_cache: dict = {}


class _MemClient:
    """Fallback in-memory KV store when Redis is unavailable."""
    def __init__(self) -> None:
        self._d: dict = {}
        self._pubsub_tasks: dict = {}
    async def ping(self) -> bool: return True
    async def set(self, key: str, val: str, ex: Optional[int] = None) -> None:
        self._d[key] = val
    async def get(self, key: str) -> Optional[str]: return self._d.get(key)
    async def delete(self, key: str) -> None: self._d.pop(key, None)
    async def close(self) -> None: self._d.clear()
    async def aclose(self) -> None: self._d.clear()


async def get_redis():
    global _redis
    if _redis is not None:
        return _redis
    if HAS_REDIS:
        try:
            r = aioredis.from_url(
                settings.REDIS_URL, encoding="utf-8", decode_responses=True,
                max_connections=20,
                socket_connect_timeout=1.0,
            )
            await r.ping()
            _redis = r
            return _redis
        except Exception as e:
            log.warning("Redis unavailable (%s); falling back to in-memory store.", e)
    _redis = _MemClient()
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        try: await _redis.aclose() if hasattr(_redis, "aclose") else await _redis.close()
        except Exception: pass
        _redis = None


async def redis_set_json(key: str, value: Any, ex_seconds: Optional[int] = None) -> None:
    r = await get_redis()
    await r.set(key, json.dumps(value, default=str), ex=ex_seconds)


async def redis_get_json(key: str) -> Any:
    r = await get_redis()
    raw = await r.get(key)
    if raw is None: return None
    try: return json.loads(raw)
    except Exception: return raw


KILL_SWITCH_KEY = "aifx:system:kill_switch"


async def is_kill_switch_active() -> bool:
    r = await get_redis()
    val = await r.get(KILL_SWITCH_KEY)
    return val == "1"


async def set_kill_switch(active: bool) -> None:
    r = await get_redis()
    if active: await r.set(KILL_SWITCH_KEY, "1")
    else: await r.delete(KILL_SWITCH_KEY)
