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
    "messages",
    "tool_messages",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render_trace_item(item: Any) -> str:
    if isinstance(item, dict):
        tool_name = str(item.get("tool_name") or "").strip()
        tool_result = item.get("tool_result")
        if tool_name:
            if tool_result is None:
                return tool_name
            return f"{tool_name}: {tool_result}"
    return str(item or "").strip()


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
        trace = [_render_trace_item(item) for item in raw_trace if _render_trace_item(item)]
    elif isinstance(raw_trace, str) and raw_trace.strip():
        trace = [raw_trace.strip()]
    else:
        trace = []
    if not trace:
        trace = ["post_process_fallback"]

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

    return {
        "intent": intent,
        "goal": reason or "服务收尾归档",
        "summary": "；".join(summary_parts)[:300],
        "trace": trace[:12],
        "facts": facts,
        "final_status": final_status,
        "final_reason": final_reason,
        "started_at": started_at,
        "ended_at": ended_at,
        "is_continuous_with_last": False,
    }


def _empty_value(value: Any) -> Any:
    if isinstance(value, bool):
        return False
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, tuple):
        return []
    if isinstance(value, set):
        return []
    if isinstance(value, int):
        return 0
    if isinstance(value, float):
        return 0
    return None


def _build_clear_updates(state: AgentState) -> Dict[str, Any]:
    updates: Dict[str, Any] = {}
    for key, value in state.items():
        if key in _STATE_FIELDS_TO_KEEP:
            continue
        updates[key] = _empty_value(value)
    return updates


def _log_background_task_result(task: asyncio.Task[Any], *, user_id: str) -> None:
    try:
        task.result()
    except Exception as e:
        logger.warning("[post_process] async user facts extraction failed for %s: %s", user_id, e)


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
    updates: Dict[str, Any] = {}
    messages = list(state.get("messages") or [])
    tool_messages = list(state.get("tool_messages") or [])
    service_memory: Dict[str, Any]
    merge_with_last = False

    try:
        service_memory = await build_service_memory(
            state,
            messages=messages,
        )
        merge_with_last = bool(service_memory.pop("is_continuous_with_last", False))
    except Exception as e:
        logger.warning("[post_process] summary build failed, using fallback memory: %s", e)
        service_memory = _fallback_service_memory(state)
        merge_with_last = False

    try:
        service_memory.pop("is_continuous_with_last", None)
        user_id = str(state.get("user_id") or "unknown")
        thread_id = str(state.get("thread_id") or "unknown")
        await save_service_memory(
            user_id=user_id,
            thread_id=thread_id,
            service_memory=service_memory,
            messages=messages,
            merge_with_last=merge_with_last,
        )
        _spawn_user_facts_task(
            user_id=user_id,
            messages=messages,
            service_memory_summary=str(service_memory.get("summary") or "").strip(),
        )
    except Exception as e:
        logger.warning(f"[post_process] save service memory failed, state preserved without cleanup: {e}")
        return {}

    updates.update(_build_clear_updates(state))
    remove_messages = [RemoveMessage(id=message.id) for message in messages if getattr(message, "id", None)]
    if remove_messages:
        updates["messages"] = remove_messages
    remove_tool_messages = [RemoveMessage(id=message.id) for message in tool_messages if getattr(message, "id", None)]
    if remove_tool_messages:
        updates["tool_messages"] = remove_tool_messages
    return updates
