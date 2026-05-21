from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.config.logging import get_logger
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import CHAT_LAST_THREAD_KEY, CHAT_MESSAGES_KEY

logger = get_logger("chat_history")

_CHAT_HISTORY_KEEP = 100
_CHAT_TTL_SECONDS = 7 * 24 * 60 * 60


def _last_thread_key(user_id: str) -> str:
    return CHAT_LAST_THREAD_KEY.format(user_id=user_id)


def _messages_key(user_id: str, thread_id: str) -> str:
    return CHAT_MESSAGES_KEY.format(user_id=user_id, thread_id=thread_id)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_message(message: Dict[str, Any]) -> Dict[str, Any] | None:
    role = str(message.get("role") or "").strip()
    if role not in {"user", "assistant"}:
        return None

    content = str(message.get("content") or "").strip()
    products = [
        item for item in list(message.get("products") or []) if isinstance(item, dict)
    ]
    interaction = message.get("interaction")
    if interaction is not None and not isinstance(interaction, dict):
        interaction = None
    if not content and not products and interaction is None:
        return None

    payload: Dict[str, Any] = {
        "role": role,
        "content": content,
        "products": products,
        "created_at": str(message.get("created_at") or "").strip() or _utc_now_iso(),
    }
    if interaction is not None:
        payload["interaction"] = interaction
    return payload


async def load_last_chat_thread(user_id: str) -> str | None:
    redis = await get_optional_redis_client()
    if not redis:
        return None
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return None
    try:
        thread_id = await redis.get(_last_thread_key(normalized_user_id))
        normalized_thread_id = str(thread_id or "").strip()
        return normalized_thread_id or None
    except Exception as e:
        logger.warning("[chat_history] load last thread failed: %s", e)
        return None


async def append_chat_message(
    user_id: str, thread_id: str, message: Dict[str, Any]
) -> None:
    redis = await get_optional_redis_client()
    if not redis:
        return
    normalized_user_id = str(user_id or "").strip()
    normalized_thread_id = str(thread_id or "").strip()
    payload = _normalize_message(message)
    if not normalized_user_id or not normalized_thread_id or payload is None:
        return
    try:
        key = _messages_key(normalized_user_id, normalized_thread_id)
        await redis.rpush(key, json.dumps(payload, ensure_ascii=False))
        await redis.ltrim(key, -_CHAT_HISTORY_KEEP, -1)
        await redis.expire(key, _CHAT_TTL_SECONDS)
        await redis.set(
            _last_thread_key(normalized_user_id),
            normalized_thread_id,
            ex=_CHAT_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("[chat_history] append message failed: %s", e)


async def load_chat_messages(
    user_id: str, thread_id: str, limit: int = _CHAT_HISTORY_KEEP
) -> List[Dict[str, Any]]:
    redis = await get_optional_redis_client()
    if not redis:
        return []
    normalized_user_id = str(user_id or "").strip()
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_user_id or not normalized_thread_id:
        return []
    normalized_limit = max(min(int(limit or _CHAT_HISTORY_KEEP), _CHAT_HISTORY_KEEP), 1)
    try:
        raw_items = await redis.lrange(
            _messages_key(normalized_user_id, normalized_thread_id),
            -normalized_limit,
            -1,
        )
    except Exception as e:
        logger.warning("[chat_history] load messages failed: %s", e)
        return []

    messages: List[Dict[str, Any]] = []
    for raw_item in raw_items:
        try:
            parsed = json.loads(raw_item)
        except Exception:
            continue
        if isinstance(parsed, dict):
            normalized = _normalize_message(parsed)
            if normalized is not None:
                messages.append(normalized)
    return messages
