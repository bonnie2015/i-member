"""轻量 async Redis 分布式锁。"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.config.logging import get_logger

logger = get_logger("redis_lock")

_UNLOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""


@asynccontextmanager
async def redis_lock(key: str, ttl: int = 60) -> AsyncIterator[bool]:
    """获取 Redis 分布式锁，退出时只释放自己的。持有期间自动续期。

    Args:
        key: 锁的 Redis key
        ttl: 过期时间（秒），默认 60

    Yields:
        True 获取成功，False 已被别人持有（不会阻塞）
    """
    from app.config.redis import get_redis_client

    owner = uuid.uuid4().hex
    lock_key = f"im:lock:{key}"

    try:
        redis = await get_redis_client()
    except Exception:
        logger.warning("[redis_lock] Redis unavailable, skip lock: %s", key)
        yield False
        return

    acquired = await redis.set(lock_key, owner, nx=True, ex=ttl)
    if not acquired:
        logger.info("[redis_lock] already locked: %s", key)
        yield False
        return

    # 后台续期：每 ttl/2 刷新一次
    renew_task = asyncio.create_task(_renew_loop(redis, lock_key, owner, ttl))

    try:
        yield True
    finally:
        renew_task.cancel()
        try:
            await renew_task
        except asyncio.CancelledError:
            pass
        await redis.eval(_UNLOCK_SCRIPT, 1, lock_key, owner)


async def _renew_loop(redis, lock_key: str, owner: str, ttl: int) -> None:
    """每 ttl/2 秒续期一次，直到被取消。"""
    interval = max(ttl // 2, 1)
    while True:
        await asyncio.sleep(interval)
        await redis.eval(_RENEW_SCRIPT, 1, lock_key, owner, ttl)
