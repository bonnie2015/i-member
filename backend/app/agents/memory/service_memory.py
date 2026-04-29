from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from app.config.logging import get_logger
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import (
    CHAT_LAST_THREAD_KEY,
    SERVICE_MEMORY_ARCHIVE_MESSAGES_KEY,
    SERVICE_MEMORY_RECENT_KEY,
)

logger = get_logger("service_memory")

_RECENT_SERVICE_MEMORY_KEEP = 10


def _recent_services_key(user_id: str, thread_id: str) -> str:
    return SERVICE_MEMORY_RECENT_KEY.format(user_id=user_id, thread_id=thread_id)


def _last_thread_key(user_id: str) -> str:
    return CHAT_LAST_THREAD_KEY.format(user_id=user_id)


def _service_messages_archive_key(user_id: str, thread_id: str, service_id: str) -> str:
    return SERVICE_MEMORY_ARCHIVE_MESSAGES_KEY.format(
        user_id=user_id,
        thread_id=thread_id,
        service_id=service_id,
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _serialize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for message in messages:
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        role = "user" if message.__class__.__name__ == "HumanMessage" else "assistant"
        serialized.append({"role": role, "content": content})
    return serialized


async def save_last_chat_thread(user_id: str, thread_id: str) -> None:
    redis = await get_optional_redis_client()
    if not redis:
        return
    normalized_user_id = str(user_id or "").strip()
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_user_id or not normalized_thread_id:
        return
    try:
        await redis.set(_last_thread_key(normalized_user_id), normalized_thread_id)
    except Exception as e:
        logger.warning(f"[service_memory] save last thread failed: {e}")


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
        if normalized_thread_id:
            return normalized_thread_id

        pattern = _recent_services_key(normalized_user_id, "*")
        cursor = 0
        latest_thread_id: str | None = None
        latest_ended_at: datetime | None = None

        while True:
            cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=100)
            for key in keys:
                thread_id_from_key = str(key).removeprefix(f"svc:recent:{normalized_user_id}:").strip()
                if not thread_id_from_key:
                    continue
                last_raw = await redis.lindex(key, -1)
                if not last_raw:
                    continue
                try:
                    last_entry = json.loads(last_raw)
                except Exception:
                    continue
                if not isinstance(last_entry, dict):
                    continue
                ended_at = _parse_iso_datetime(last_entry.get("ended_at") or last_entry.get("started_at"))
                if ended_at is None:
                    continue
                if latest_ended_at is None or ended_at > latest_ended_at:
                    latest_ended_at = ended_at
                    latest_thread_id = thread_id_from_key
            if cursor == 0:
                break

        if latest_thread_id:
            await redis.set(_last_thread_key(normalized_user_id), latest_thread_id)
        return latest_thread_id
    except Exception as e:
        logger.warning(f"[service_memory] load last thread failed: {e}")
        return None


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


async def load_thread_archived_messages(user_id: str, thread_id: str) -> List[Dict[str, Any]]:
    services = await load_recent_service_memories(user_id, thread_id)
    if not services:
        return []

    archived_messages: List[Dict[str, Any]] = []
    for service in services:
        if not isinstance(service, dict):
            continue
        messages_ref = str(service.get("messages_ref") or "").strip()
        if not messages_ref:
            continue
        messages = await load_service_messages(messages_ref)
        if messages:
            archived_messages.extend(messages)
    return archived_messages
