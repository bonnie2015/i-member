from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm.llm_factory import get_llm
from app.llm.runtime import invoke_with_usage_logging
from app.prompts.prompt_builder import (
    UserFactsRuntimePayload,
    build_user_facts_extraction_system_prompt,
)
from app.config.logging import get_logger
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import USER_FACTS_KEY

logger = get_logger("user_facts")

_CORE_FACTS_LIMIT = 8
_USER_FACTS_TTL_SECONDS = 30 * 24 * 60 * 60


class StoredUserFact(BaseModel):
    fact: str
    updated_at: str = ""


class UserFactsStore(BaseModel):
    facts: list[StoredUserFact] = Field(default_factory=list)
    updated_at: str = ""


class UserFactsExtractionOutput(BaseModel):
    add_facts: list[str] = Field(default_factory=list)
    delete_facts: list[str] = Field(default_factory=list)


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
    facts = []
    for item in payload.get("facts") or []:
        parsed = _coerce_fact_item(item)
        if parsed:
            facts.append(parsed)
    return UserFactsStore(
        facts=facts,
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
    return [item.fact for item in store.facts if _normalize_fact_text(item.fact)]


def _apply_fact_changes(
    existing: Sequence[StoredUserFact],
    *,
    add_facts: Sequence[str],
    delete_facts: Sequence[str],
    timestamp: str,
) -> list[StoredUserFact]:
    delete_keys = {fact.casefold() for fact in _dedupe_texts(delete_facts)}
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
        add_facts,
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


async def save_user_facts_store(
    user_id: str,
    *,
    add_facts: Sequence[str],
    delete_facts: Sequence[str],
) -> UserFactsStore:
    redis = await get_optional_redis_client()
    if not redis:
        return UserFactsStore()

    existing = await load_user_facts_store(user_id)
    timestamp = _utc_now_iso()
    facts = _apply_fact_changes(
        existing.facts,
        add_facts=add_facts,
        delete_facts=delete_facts,
        timestamp=timestamp,
    )
    store = UserFactsStore(
        facts=facts,
        updated_at=timestamp,
    )
    try:
        await redis.set(
            _user_facts_key(user_id),
            json.dumps(store.model_dump(mode="json"), ensure_ascii=False),
            ex=_USER_FACTS_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning("[user_facts] save store failed for %s: %s", user_id, e)
    return store


async def _extract_user_fact_changes(
    *,
    user_id: str,
    messages: Sequence[BaseMessage],
    thread_id: str = "unknown",
) -> UserFactsExtractionOutput:
    from app.memory.user_profile import load_user_profile

    existing_store = await load_user_facts_store(user_id)

    profile_summary = ""
    try:
        profile = await load_user_profile(user_id)
        if isinstance(profile, dict):
            profile_summary = str(profile.get("summary") or "").strip()
    except Exception:
        pass

    llm = get_llm("postprocess").with_structured_output(UserFactsExtractionOutput)
    prompt = await build_user_facts_extraction_system_prompt(
        runtime_context=UserFactsRuntimePayload(
            existing_facts=[item.fact for item in existing_store.facts],
            profile_summary=profile_summary,
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
        thread_id=thread_id,
        user_id=user_id,
        provider="deepseek",
    )
    return UserFactsExtractionOutput(
        add_facts=_dedupe_texts(response.add_facts, limit=_CORE_FACTS_LIMIT),
        delete_facts=_dedupe_texts(response.delete_facts, limit=_CORE_FACTS_LIMIT),
    )


async def extract_and_save_user_facts(
    *,
    user_id: str,
    messages: Sequence[BaseMessage],
    thread_id: str = "unknown",
) -> UserFactsStore:
    changes = await _extract_user_fact_changes(
        user_id=user_id,
        messages=messages,
        thread_id=thread_id,
    )
    return await save_user_facts_store(
        user_id,
        add_facts=changes.add_facts,
        delete_facts=changes.delete_facts,
    )
