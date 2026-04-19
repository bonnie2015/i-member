from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from app.config.logging import get_logger
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import (
    SERVICE_MEMORY_ARCHIVE_MESSAGES_KEY,
    SERVICE_MEMORY_RECENT_KEY,
)

logger = get_logger("service_memory")

_RECENT_SERVICE_MEMORY_KEEP = 10


def _recent_services_key(user_id: str, thread_id: str) -> str:
    return SERVICE_MEMORY_RECENT_KEY.format(user_id=user_id, thread_id=thread_id)


def _service_messages_archive_key(user_id: str, thread_id: str, service_id: str) -> str:
    return SERVICE_MEMORY_ARCHIVE_MESSAGES_KEY.format(
        user_id=user_id,
        thread_id=thread_id,
        service_id=service_id,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for message in messages:
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        role = "user" if message.__class__.__name__ == "HumanMessage" else "assistant"
        serialized.append({"role": role, "content": content})
    return serialized


async def save_service_memory(
    *,
    user_id: str,
    thread_id: str,
    service_memory: Dict[str, Any],
    messages: List[Any],
    merge_with_last: bool = False,
) -> None:
    redis = await get_optional_redis_client()
    if not redis:
        return

    try:
        recent_key = _recent_services_key(user_id, thread_id)
        archived_messages = _serialize_messages(messages)
        entry = dict(service_memory or {})
        entry.setdefault("started_at", _utc_now_iso())
        entry.setdefault("ended_at", _utc_now_iso())

        if merge_with_last:
            last_raw = await redis.lindex(recent_key, -1)
            if last_raw:
                try:
                    last_entry = json.loads(last_raw)
                except Exception:
                    last_entry = {}

                service_id = str(last_entry.get("service_id") or uuid4().hex)
                messages_ref = str(
                    last_entry.get("messages_ref") or _service_messages_archive_key(user_id, thread_id, service_id)
                )
                existing_messages = []
                existing_raw = await redis.get(messages_ref)
                if existing_raw:
                    try:
                        existing_messages = json.loads(existing_raw)
                    except Exception:
                        existing_messages = []
                await redis.set(messages_ref, json.dumps(existing_messages + archived_messages, ensure_ascii=False))

                merged_entry = {
                    **last_entry,
                    **entry,
                    "service_id": service_id,
                    "messages_ref": messages_ref,
                    "started_at": last_entry.get("started_at") or entry["started_at"],
                    "ended_at": entry.get("ended_at") or _utc_now_iso(),
                }
                await redis.lset(recent_key, -1, json.dumps(merged_entry, ensure_ascii=False))
            else:
                merge_with_last = False

        if not merge_with_last:
            service_id = uuid4().hex
            messages_ref = _service_messages_archive_key(user_id, thread_id, service_id)
            await redis.set(messages_ref, json.dumps(archived_messages, ensure_ascii=False))
            new_entry = {
                **entry,
                "service_id": service_id,
                "messages_ref": messages_ref,
            }
            await redis.rpush(recent_key, json.dumps(new_entry, ensure_ascii=False))

        total = await redis.llen(recent_key)
        while total > _RECENT_SERVICE_MEMORY_KEEP:
            oldest_raw = await redis.lpop(recent_key)
            if oldest_raw:
                try:
                    oldest = json.loads(oldest_raw)
                    messages_ref = str(oldest.get("messages_ref") or "").strip()
                    if messages_ref:
                        await redis.delete(messages_ref)
                except Exception:
                    pass
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


async def load_recent_service_memories_limited(user_id: str, thread_id: str, limit: int = 1) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    services = await load_recent_service_memories(user_id, thread_id)
    if not services:
        return []
    return list(reversed(services))[:limit]


async def load_last_service_memory(user_id: str, thread_id: str) -> Dict[str, Any] | None:
    redis = await get_optional_redis_client()
    if not redis:
        return None
    try:
        raw = await redis.lindex(_recent_services_key(user_id, thread_id), -1)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception as e:
        logger.warning(f"[service_memory] load last failed: {e}")
        return None


async def load_service_messages(messages_ref: str) -> List[Dict[str, Any]]:
    redis = await get_optional_redis_client()
    if not redis or not messages_ref:
        return []
    try:
        raw = await redis.get(messages_ref)
        if not raw:
            return []
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception as e:
        logger.warning(f"[service_memory] load messages failed: {e}")
        return []


async def load_service_messages_limited(messages_ref: str, *, offset: int = 0, limit: int = 20) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    messages = await load_service_messages(messages_ref)
    if not messages:
        return []
    start = max(int(offset or 0), 0)
    return messages[start : start + limit]
