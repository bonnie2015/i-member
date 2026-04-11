from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from redis.asyncio import Redis as AsyncRedis
from app.config.config import settings
from app.config.logging import get_logger

logger = get_logger("checkpointer")


async def create_checkpointer():
    """
    创建并初始化 checkpointer，必须在 async 上下文中调用。

    成功时返回 AsyncRedisSaver（已调用 setup 建立索引）。
    Redis 不可用时降级返回 MemorySaver。
    """
    try:
        redis_client = AsyncRedis.from_url(settings.redis_url)
        await redis_client.ping()
        await redis_client.aclose()

        checkpointer = AsyncRedisSaver(redis_url=settings.redis_url)
        await checkpointer.setup()  # 创建 checkpoint / checkpoint_write 索引

        logger.info("Redis checkpointer initialized")
        return checkpointer

    except Exception as e:
        logger.warning(f"Redis unavailable ({e}), falling back to MemorySaver")
        return MemorySaver()
