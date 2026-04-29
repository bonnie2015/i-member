from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    UserFactsRuntimePayload,
    build_user_facts_extraction_system_prompt,
)
from app.config.logging import get_logger
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import USER_FACTS_KEY

logger = get_logger("user_facts")

_CORE_FACTS_LIMIT = 8
_RETRIEVABLE_FACTS_LIMIT = 200
_MESSAGE_CONTENT_LIMIT = 500


class StoredUserFact(BaseModel):
    fact: str
    updated_at: str = ""


class UserFactsStore(BaseModel):
    core_facts: list[StoredUserFact] = Field(default_factory=list)
    retrievable_facts: list[StoredUserFact] = Field(default_factory=list)
    updated_at: str = ""


class UserFactsExtractionOutput(BaseModel):
    add_core_facts: list[str] = Field(default_factory=list)
    delete_core_facts: list[str] = Field(default_factory=list)
    add_retrievable_facts: list[str] = Field(default_factory=list)


# Generate a stable UTC timestamp for fact updates and writes.
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Build the Redis key for one user's long-term fact store.
def _user_facts_key(user_id: str) -> str:
    return USER_FACTS_KEY.format(user_id=user_id)


# Normalize a fact sentence into a compact comparable string.
def _normalize_fact_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = " ".join(part for part in text.split())
    return text.strip(" \t\n-")


def _serialize_messages(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for message in messages:
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        if len(content) > _MESSAGE_CONTENT_LIMIT:
            content = content[:_MESSAGE_CONTENT_LIMIT].rstrip() + "..."
        payload.append(
            {
                "role": getattr(message, "type", message.__class__.__name__),
                "content": content,
            }
        )
    return payload


def _coerce_fact_item(item: Any) -> StoredUserFact | None:
    if isinstance(item, StoredUserFact):
        fact = _normalize_fact_text(item.fact)
        if not fact:
            return None
        return StoredUserFact(fact=fact, updated_at=str(item.updated_at or "").strip())
    if isinstance(item, Mapping):
        fact = _normalize_fact_text(item.get("fact"))
        if not fact:
            return None
        return StoredUserFact(
            fact=fact,
            updated_at=str(item.get("updated_at") or "").strip(),
        )
    fact = _normalize_fact_text(item)
    if not fact:
        return None
    return StoredUserFact(fact=fact, updated_at="")


def _coerce_store(payload: Any) -> UserFactsStore:
    if isinstance(payload, UserFactsStore):
        return payload
    if not isinstance(payload, Mapping):
        return UserFactsStore()
    core_items = []
    for item in payload.get("core_facts") or []:
        parsed = _coerce_fact_item(item)
        if parsed:
            core_items.append(parsed)
    retrievable_items = []
    for item in payload.get("retrievable_facts") or []:
        parsed = _coerce_fact_item(item)
        if parsed:
            retrievable_items.append(parsed)
    return UserFactsStore(
        core_facts=core_items,
        retrievable_facts=retrievable_items,
        updated_at=str(payload.get("updated_at") or "").strip(),
    )


def _dedupe_texts(
    values: Sequence[Any],
    *,
    limit: int | None = None,
    exclude: set[str] | None = None,
) -> list[str]:
    seen = set(exclude or set())
    results: list[str] = []
    for item in values:
        fact = _normalize_fact_text(item)
        if not fact:
            continue
        key = fact.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(fact)
        if limit and len(results) >= limit:
            break
    return results


async def load_user_facts_store(user_id: str) -> UserFactsStore:
    redis = await get_optional_redis_client()
    if not redis:
        return UserFactsStore()
    try:
        raw = await redis.get(_user_facts_key(user_id))
        if not raw:
            return UserFactsStore()
        parsed = json.loads(raw)
        return _coerce_store(parsed)
    except Exception as e:
        logger.warning("[user_facts] load store failed for %s: %s", user_id, e)
        return UserFactsStore()


async def load_user_facts(user_id: str) -> list[str]:
    store = await load_user_facts_store(user_id)
    return [item.fact for item in store.core_facts if _normalize_fact_text(item.fact)]


async def load_retrievable_user_facts(user_id: str, limit: int = 20) -> list[dict[str, str]]:
    store = await load_user_facts_store(user_id)
    items = sorted(
        [item for item in store.retrievable_facts if _normalize_fact_text(item.fact)],
        key=lambda item: str(item.updated_at or ""),
        reverse=True,
    )
    return [
        {"fact": item.fact, "updated_at": item.updated_at}
        for item in items[: max(int(limit or 0), 0)]
    ]


def _apply_core_fact_changes(
    existing: Sequence[StoredUserFact],
    *,
    add_core_facts: Sequence[str],
    delete_core_facts: Sequence[str],
    timestamp: str,
) -> list[StoredUserFact]:
    delete_keys = {fact.casefold() for fact in _dedupe_texts(delete_core_facts)}
    ordered_existing: list[StoredUserFact] = []
    seen_existing: set[str] = set()
    for item in existing:
        fact = _normalize_fact_text(item.fact)
        if not fact:
            continue
        key = fact.casefold()
        if key in delete_keys or key in seen_existing:
            continue
        seen_existing.add(key)
        ordered_existing.append(
            StoredUserFact(
                fact=fact,
                updated_at=str(item.updated_at or "").strip(),
            )
        )

    current_keys = {item.fact.casefold() for item in ordered_existing}
    additions = _dedupe_texts(
        add_core_facts,
        limit=_CORE_FACTS_LIMIT,
        exclude=current_keys,
    )
    for fact in additions:
        ordered_existing.append(
            StoredUserFact(
                fact=fact,
                updated_at=timestamp,
            )
        )
        current_keys.add(fact.casefold())

    return ordered_existing[:_CORE_FACTS_LIMIT]


def _apply_retrievable_fact_changes(
    existing: Sequence[StoredUserFact],
    *,
    add_retrievable_facts: Sequence[str],
    core_fact_keys: set[str],
    timestamp: str,
) -> list[StoredUserFact]:
    merged: list[StoredUserFact] = []
    merged_map: dict[str, StoredUserFact] = {}

    for item in existing:
        fact = _normalize_fact_text(item.fact)
        if not fact:
            continue
        key = fact.casefold()
        if key in core_fact_keys or key in merged_map:
            continue
        record = StoredUserFact(
            fact=fact,
            updated_at=str(item.updated_at or "").strip(),
        )
        merged_map[key] = record
        merged.append(record)

    for fact in _dedupe_texts(add_retrievable_facts):
        key = fact.casefold()
        if key in core_fact_keys:
            continue
        if key in merged_map:
            merged_map[key].updated_at = timestamp
            continue
        record = StoredUserFact(fact=fact, updated_at=timestamp)
        merged_map[key] = record
        merged.append(record)

    if len(merged) <= _RETRIEVABLE_FACTS_LIMIT:
        return merged

    return sorted(
        merged,
        key=lambda item: str(item.updated_at or ""),
        reverse=True,
    )[:_RETRIEVABLE_FACTS_LIMIT]


async def save_user_facts_store(
    user_id: str,
    *,
    add_core_facts: Sequence[str],
    delete_core_facts: Sequence[str],
    add_retrievable_facts: Sequence[str],
) -> UserFactsStore:
    redis = await get_optional_redis_client()
    if not redis:
        return UserFactsStore()

    existing = await load_user_facts_store(user_id)
    timestamp = _utc_now_iso()
    core_facts = _apply_core_fact_changes(
        existing.core_facts,
        add_core_facts=add_core_facts,
        delete_core_facts=delete_core_facts,
        timestamp=timestamp,
    )
    core_fact_keys = {item.fact.casefold() for item in core_facts}
    retrievable_facts = _apply_retrievable_fact_changes(
        existing.retrievable_facts,
        add_retrievable_facts=add_retrievable_facts,
        core_fact_keys=core_fact_keys,
        timestamp=timestamp,
    )
    store = UserFactsStore(
        core_facts=core_facts,
        retrievable_facts=retrievable_facts,
        updated_at=timestamp,
    )
    try:
        await redis.set(
            _user_facts_key(user_id),
            json.dumps(store.model_dump(mode="json"), ensure_ascii=False),
        )
    except Exception as e:
        logger.warning("[user_facts] save store failed for %s: %s", user_id, e)
    return store


async def _extract_user_fact_changes(
    *,
    user_id: str,
    messages: Sequence[BaseMessage],
    service_memory_summary: str,
) -> UserFactsExtractionOutput:
    existing_store = await load_user_facts_store(user_id)
    llm = get_remote_llm(role="postprocess").with_structured_output(UserFactsExtractionOutput)
    prompt = await build_user_facts_extraction_system_prompt(
        runtime_context=UserFactsRuntimePayload(
            existing_core_facts=[item.fact for item in existing_store.core_facts],
            current_service_memory_summary=str(service_memory_summary or "").strip(),
        )
    )
    llm_messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content=json.dumps(
                {"messages": _serialize_messages(messages)},
                ensure_ascii=False,
            )
        ),
    ]
    response, _ = await invoke_with_usage_logging(
        llm=llm,
        messages=llm_messages,
        node="post_process_user_facts",
        user_id=user_id,
        provider="deepseek",
    )
    return UserFactsExtractionOutput(
        add_core_facts=_dedupe_texts(response.add_core_facts, limit=_CORE_FACTS_LIMIT),
        delete_core_facts=_dedupe_texts(response.delete_core_facts, limit=_CORE_FACTS_LIMIT),
        add_retrievable_facts=_dedupe_texts(response.add_retrievable_facts, limit=_RETRIEVABLE_FACTS_LIMIT),
    )


async def extract_and_save_user_facts(
    *,
    user_id: str,
    messages: Sequence[BaseMessage],
    service_memory_summary: str,
) -> UserFactsStore:
    changes = await _extract_user_fact_changes(
        user_id=user_id,
        messages=messages,
        service_memory_summary=service_memory_summary,
    )
    return await save_user_facts_store(
        user_id,
        add_core_facts=changes.add_core_facts,
        delete_core_facts=changes.delete_core_facts,
        add_retrievable_facts=changes.add_retrievable_facts,
    )
