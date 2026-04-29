from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from app.config.logging import get_logger
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import (
    SERVICE_MEMORY_RECENT_KEY,
)

logger = get_logger("service_memory")

_RECENT_SERVICE_MEMORY_KEEP = 10
_SERVICE_MEMORY_TTL_SECONDS = 2 * 24 * 60 * 60


def _recent_services_key(user_id: str, thread_id: str) -> str:
    return SERVICE_MEMORY_RECENT_KEY.format(user_id=user_id, thread_id=thread_id)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _service_type_of(item: Dict[str, Any]) -> str:
    return str(item.get("service_type") or "").strip()


def _matches_service_type(item: Dict[str, Any], service_type: str | None) -> bool:
    normalized_service_type = str(service_type or "").strip()
    if not normalized_service_type:
        return True
    return _service_type_of(item) == normalized_service_type


async def save_service_memory(
    *,
    user_id: str,
    thread_id: str,
    service_memory: Dict[str, Any],
) -> None:
    redis = await get_optional_redis_client()
    if not redis:
        return

    try:
        recent_key = _recent_services_key(user_id, thread_id)
        entry = dict(service_memory or {})
        service_type = str(entry.get("service_type") or "").strip()
        if service_type:
            entry["service_type"] = service_type
        entry.setdefault("started_at", _utc_now_iso())
        entry.setdefault("ended_at", _utc_now_iso())
        entry.setdefault("thread_id", thread_id)
        entry.setdefault("summary", "")

        service_id = uuid4().hex
        new_entry = {
            **entry,
            "service_id": service_id,
        }
        await redis.rpush(recent_key, json.dumps(new_entry, ensure_ascii=False))
        await redis.expire(recent_key, _SERVICE_MEMORY_TTL_SECONDS)

        total = await redis.llen(recent_key)
        while total > _RECENT_SERVICE_MEMORY_KEEP:
            await redis.lpop(recent_key)
            total -= 1
    except Exception as e:
        logger.warning(f"[service_memory] save failed: {e}")


async def load_recent_service_memories(user_id: str, thread_id: str) -> List[Dict[str, Any]]:
    redis = await get_optional_redis_client()
    if not redis:
        return []
    try:
        raw_list = await redis.lrange(_recent_services_key(user_id, thread_id), 0, -1)
        services: List[Dict[str, Any]] = []
        for raw in raw_list:
            try:
                item = json.loads(raw)
                if isinstance(item, dict):
                    services.append(item)
            except Exception:
                pass

        return services
    except Exception as e:
        logger.warning(f"[service_memory] load recent failed: {e}")
        return []


async def load_recent_service_memories_limited(
    user_id: str,
    thread_id: str,
    limit: int = 1,
    service_type: str | None = None,
) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    services = await load_recent_service_memories(user_id, thread_id)
    if not services:
        return []
    filtered = [item for item in reversed(services) if isinstance(item, dict) and _matches_service_type(item, service_type)]
    return filtered[:limit]
