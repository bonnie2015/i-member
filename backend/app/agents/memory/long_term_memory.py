"""
long_term_memory.py — 长期记忆服务

基于 Redis 存储用户的有价值记忆片段（偏好、习惯等）。

存储结构：
  key: mem:{user_id}  → Redis list，每个元素为 JSON 对象
  每条记忆：{"content": "...", "confidence": 0.8, "created_at": "..."}

策略：
  - 保留最近 20 条，超过后删除置信度最低的
  - 同义/重复记忆通过 LLM 去重（简单文本比较）
"""

import json
from datetime import datetime
from typing import Any, Dict, List

from app.config.logging import get_logger
from app.agents.memory.redis_keys import long_term_memory_key

logger = get_logger("long_term_memory")

_MAX_MEMORIES = 20


async def _get_redis():
    try:
        from redis.asyncio import Redis as AsyncRedis
        from app.config.config import settings
        client = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return client
    except Exception:
        return None


async def load_memories(user_id: str) -> List[Dict[str, Any]]:
    """加载用户长期记忆列表。"""
    redis = await _get_redis()
    if not redis:
        return []
    try:
        key = long_term_memory_key(user_id)
        raw_list = await redis.lrange(key, 0, -1)
        memories = []
        for raw in raw_list:
            try:
                memories.append(json.loads(raw))
            except Exception:
                pass
        return memories
    except Exception as e:
        logger.warning(f"[long_term_memory] load failed for {user_id}: {e}")
        return []
    finally:
        await redis.aclose()


async def save_memories(user_id: str, new_memories: List[Dict[str, Any]]) -> None:
    """
    将新记忆合并保存，自动去重并限制上限。

    new_memories: [{"content": "...", "confidence": 0.8}, ...]
    """
    if not new_memories:
        return

    redis = await _get_redis()
    if not redis:
        logger.warning("[long_term_memory] Redis unavailable, skipping memory save")
        return

    try:
        key = long_term_memory_key(user_id)
        existing = await load_memories(user_id)
        existing_texts = {m.get("content", "") for m in existing}

        now = datetime.utcnow().isoformat()
        added = 0
        for mem in new_memories:
            content = mem.get("content", "").strip()
            if not content or content in existing_texts:
                continue
            entry = {
                "content": content,
                "confidence": mem.get("confidence", 0.7),
                "created_at": now,
            }
            await redis.rpush(key, json.dumps(entry, ensure_ascii=False))
            existing_texts.add(content)
            added += 1

        # 超过上限时，删除置信度最低的旧记忆
        total = await redis.llen(key)
        while total > _MAX_MEMORIES:
            await redis.lpop(key)
            total -= 1

        logger.info(f"[long_term_memory] saved {added} memories for {user_id}, total={total}")
    except Exception as e:
        logger.warning(f"[long_term_memory] save failed for {user_id}: {e}")
    finally:
        await redis.aclose()


async def extract_and_save_memories(user_id: str, messages) -> None:
    """
    从对话中提取有价值的记忆并保存。
    """
    if not messages:
        return

    conversation_lines = []
    for msg in messages:
        role = "用户" if msg.__class__.__name__ == "HumanMessage" else "客服"
        conversation_lines.append(f"{role}：{msg.content}")
    conversation = "\n".join(conversation_lines)

    try:
        from langchain_core.messages import HumanMessage
        from app.agents.llm.llm_factory import get_remote_llm
        from app.agents.prompts.prompt_loader import load_prompt

        llm = get_remote_llm(role="postprocess")
        prompt = load_prompt("post_process/memory_extract.txt").format(conversation=conversation)
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = resp.content.strip()

        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            memories = json.loads(raw[start:end])
            if memories:
                await save_memories(user_id, memories)
    except Exception as e:
        logger.warning(f"[long_term_memory] extract failed for {user_id}: {e}")
