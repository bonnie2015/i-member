from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from langchain_core.messages import RemoveMessage

from app.agents.memory.user_facts import extract_and_save_user_facts
from app.config.logging import get_logger
from app.agents.memory.service_memory import save_service_memory
from app.workflow.nodes.post_process.service_memory_builder import build_service_memory
from app.workflow.state import AgentState

logger = get_logger("post_process")

_STATE_FIELDS_TO_KEEP = {
    "user_id",
    "thread_id",
    "channel",
    "user_context",
    "final_reply",
    "recommended_products",
    "messages",
    "tool_messages",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fallback_service_memory(state: AgentState) -> Dict[str, Any]:
    intent = str(state.get("intent") or "unknown").strip() or "unknown"
    reason = str(state.get("reason") or "").strip()
    final_reply = str(state.get("final_reply") or "").strip()
    final_status = str(state.get("final_status") or "").strip() or "failed"
    final_reason = str(state.get("final_reason") or "").strip() or "post_process_summary_failed"
    started_at = str(state.get("started_at") or "").strip() or _utc_now_iso()
    ended_at = _utc_now_iso()

    raw_trace = state.get("trace")
    if isinstance(raw_trace, list):
        trace = []
        for item in raw_trace:
            if isinstance(item, dict):
                tool_name = str(item.get("tool_name") or "").strip()
                tool_result = item.get("tool_result")
                text = f"{tool_name}: {tool_result}" if tool_name and tool_result is not None else tool_name
            else:
                text = str(item or "").strip()
            if text:
                trace.append(text)
    elif isinstance(raw_trace, str) and raw_trace.strip():
        trace = [raw_trace.strip()]
    else:
        trace = []
    summary_parts = [part for part in [final_reply, f"status={final_status}", f"reason={final_reason}"] if part]
    if not summary_parts:
        summary_parts.append("post_process fallback summary")

    facts: Dict[str, Any] = {"intent": intent, "summary_source": "fallback_rule"}
    service_key = str(state.get("service_key") or "").strip()
    if service_key:
        facts["service_key"] = service_key
    slots = state.get("slots")
    if isinstance(slots, dict):
        for key in ("order_id", "biz_id", "ticket_id", "ticket_type", "product_id", "sku_id"):
            value = str(slots.get(key) or "").strip()
            if value:
                facts[key] = value

    payload = {
        "intent": intent,
        "goal": reason or "服务收尾归档",
        "summary": "；".join(summary_parts)[:300],
        "facts": facts,
        "final_status": final_status,
        "final_reason": final_reason,
        "started_at": started_at,
        "ended_at": ended_at,
        "is_continuous_with_last": False,
    }
    if trace:
        payload["trace"] = trace[:12]
    return payload


def _build_clear_updates(state: AgentState) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for key, value in state.items():
        if key in _STATE_FIELDS_TO_KEEP:
            continue
        if isinstance(value, bool):
            updates[key] = False
        elif isinstance(value, dict):
            updates[key] = {}
        elif isinstance(value, (list, tuple, set)):
            updates[key] = []
        elif isinstance(value, int):
            updates[key] = 0
        elif isinstance(value, float):
            updates[key] = 0
        else:
            updates[key] = None
    return updates


def _log_background_task_result(task: asyncio.Task[Any], *, user_id: str) -> None:
    try:
        task.result()
    except Exception as e:
        logger.warning("[post_process] user_id=%s user_facts_extraction_failed error=%s", user_id, e)


def _spawn_user_facts_task(
    *,
    user_id: str,
    messages: list[Any],
    service_memory_summary: str,
) -> None:
    task = asyncio.create_task(
        extract_and_save_user_facts(
            user_id=user_id,
            messages=messages,
            service_memory_summary=service_memory_summary,
        )
    )
    task.add_done_callback(lambda current: _log_background_task_result(current, user_id=user_id))


async def post_process_node(state: AgentState) -> Dict[str, Any]:
    # 如果 current_subgraph 有值，不做任何处理，直接结束
    current_subgraph = state.get("current_subgraph")
    if current_subgraph:
        logger.info(
            "[post_process] thread_id=%s skip current_subgraph=%s",
            str(state.get("thread_id") or "unknown"),
            current_subgraph,
        )
        return {}

    updates: Dict[str, Any] = {}
    messages = list(state.get("messages") or [])
    tool_messages = list(state.get("tool_messages") or [])
    service_memory: Dict[str, Any]
    merge_with_last = False
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    user_id = str(state.get("user_id") or "unknown")

    try:
        service_memory = await build_service_memory(
            state,
            messages=messages,
        )
        merge_with_last = bool(service_memory.pop("is_continuous_with_last", False))
    except Exception as e:
        logger.warning("[post_process] thread_id=%s memory_build_failed error=%s", thread_id, e)
        service_memory = _fallback_service_memory(state)
        merge_with_last = False

    try:
        service_memory.pop("is_continuous_with_last", None)
        await save_service_memory(
            user_id=user_id,
            thread_id=thread_id,
            service_memory=service_memory,
            messages=messages,
            merge_with_last=merge_with_last,
        )
        logger.info(
            "[post_process] thread_id=%s intent=%s final_status=%s merge=%s",
            thread_id,
            service_memory.get("intent"),
            service_memory.get("final_status"),
            merge_with_last,
        )
        _spawn_user_facts_task(
            user_id=user_id,
            messages=messages,
            service_memory_summary=str(service_memory.get("summary") or "").strip(),
        )
    except Exception as e:
        logger.warning("[post_process] thread_id=%s save_memory_failed error=%s", thread_id, e)
        return {}

    updates.update(_build_clear_updates(state))
    remove_messages = [RemoveMessage(id=message.id) for message in messages if getattr(message, "id", None)]
    if remove_messages:
        updates["messages"] = remove_messages
    remove_tool_messages = [RemoveMessage(id=message.id) for message in tool_messages if getattr(message, "id", None)]
    if remove_tool_messages:
        updates["tool_messages"] = remove_tool_messages
    return updates
