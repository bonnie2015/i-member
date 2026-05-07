from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from app.config.config import settings
from app.config.logging import get_logger
from app.config.redis import get_redis_client

logger = get_logger("redis_checkpointer")


async def create_checkpointer():
    """
    创建并初始化运行态 checkpoint 存储。

    优先使用 Redis；不可用时降级为进程内 MemorySaver。
    TTL 以分钟配置，当前为 7 天。
    """
    try:
        await get_redis_client()

        checkpointer = AsyncRedisSaver(
            redis_url=settings.redis_url,
            ttl={"default_ttl": 7 * 24 * 60},
        )
        await checkpointer.setup()
        logger.info("Redis checkpoint store initialized")
        return checkpointer
    except Exception as e:
        logger.warning(f"Redis unavailable ({e}), falling back to MemorySaver")
        return MemorySaver()
