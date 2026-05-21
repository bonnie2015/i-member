from __future__ import annotations

import asyncio

from redis.asyncio import Redis as AsyncRedis

from app.config.config import settings
from app.config.logging import get_logger

logger = get_logger("redis_client")

_redis_client: AsyncRedis | None = None
_redis_lock = asyncio.Lock()
_redis_ping_ok = False


async def get_redis_client() -> AsyncRedis:
    global _redis_client, _redis_ping_ok
    if _redis_client is not None and _redis_ping_ok:
        return _redis_client

    async with _redis_lock:
        if _redis_client is None:
            _redis_client = AsyncRedis.from_url(
                settings.redis_url, decode_responses=True
            )
            _redis_ping_ok = False
        if not _redis_ping_ok:
            try:
                await _redis_client.ping()
                _redis_ping_ok = True
            except Exception:
                await _redis_client.aclose()
                _redis_client = None
                _redis_ping_ok = False
                raise
        return _redis_client


async def get_optional_redis_client() -> AsyncRedis | None:
    try:
        return await get_redis_client()
    except Exception as e:
        logger.warning("[redis_client] unavailable: %s", e)
        return None


async def close_redis_client() -> None:
    global _redis_client, _redis_ping_ok
    async with _redis_lock:
        if _redis_client is None:
            return
        try:
            await _redis_client.aclose()
        finally:
            _redis_client = None
            _redis_ping_ok = False
