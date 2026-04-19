from __future__ import annotations

import json
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    PostProcessRuntimePayload,
    build_post_process_system_prompt,
)
from app.agents.memory.service_memory import load_last_service_memory


class ServiceMemorySummary(BaseModel):
    goal: str
    summary: str
    is_continuous: bool


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_text(text: Any, limit: int = 180) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _render_trace_item(item: Any) -> str:
    if isinstance(item, dict):
        tool_name = str(item.get("tool_name") or "").strip()
        tool_result = item.get("tool_result")
        if tool_name:
            if tool_result is None:
                return tool_name
            return f"{tool_name}: {_trim_text(tool_result, 200)}"
    return str(item or "").strip()


def _normalize_trace(raw_trace: Any, *, intent: str, final_status: str, final_reply: str) -> List[str]:
    if isinstance(raw_trace, list):
        trace = [_render_trace_item(item) for item in raw_trace if _render_trace_item(item)]
    elif isinstance(raw_trace, str) and raw_trace.strip():
        trace = [raw_trace.strip()]
    else:
        trace = []

    if trace:
        return trace[:8]

    fallback = [f"识别为{intent or 'unknown'}服务"]
    if final_status:
        fallback.append(f"服务以{final_status}状态结束")
    if final_reply:
        fallback.append(f"最终答复：{_trim_text(final_reply, 80)}")
    return fallback


def _serialize_messages(messages: Sequence[BaseMessage]) -> List[Dict[str, str]]:
    payload: List[Dict[str, str]] = []
    for message in messages:
        payload.append(
            {
                "role": getattr(message, "type", message.__class__.__name__),
                "content": _trim_text(getattr(message, "content", ""), 500),
            }
        )
    return payload


def _extract_common_facts(state: Mapping[str, Any], intent: str) -> Dict[str, Any]:
    common_facts: Dict[str, Any] = {}
    if intent:
        common_facts["intent"] = intent
    return common_facts


def _extract_ticket_facts(state: Mapping[str, Any]) -> Dict[str, Any]:
    facts: Dict[str, Any] = {}
    service_key = str(state.get("service_key") or "").strip()
    if service_key:
        facts["service_key"] = service_key

    slots = state.get("slots") or {}
    if isinstance(slots, dict):
        for key in ("order_id", "biz_id", "ticket_id", "ticket_type", "product_id", "sku_id"):
            value = str(slots.get(key) or "").strip()
            if value:
                facts[key] = value
    return facts


def _extract_qa_facts(state: Mapping[str, Any]) -> Dict[str, Any]:
    facts: Dict[str, Any] = {}
    qa_turn_count = int(state.get("qa_turn_count") or 0)
    if qa_turn_count > 0:
        facts["qa_turn_count"] = qa_turn_count
    return facts


def _extract_facts(state: Mapping[str, Any], intent: str) -> Dict[str, Any]:
    facts = _extract_common_facts(state, intent)
    # 各服务独有事实在这里分开扩展，最终仍合并为一个扁平 facts。
    if intent == "ticket":
        facts.update(_extract_ticket_facts(state))
    elif intent == "qa":
        facts.update(_extract_qa_facts(state))
    return facts


async def _summarize_service_memory(
    *,
    user_id: str,
    thread_id: str,
    intent: str,
    reason: str,
    trace: List[str],
    final_reply: str,
    final_status: str,
    facts: Dict[str, Any],
    messages: Sequence[BaseMessage],
    user_context: Mapping[str, Any] | None,
) -> ServiceMemorySummary:
    llm = get_remote_llm(role="postprocess").with_structured_output(ServiceMemorySummary)
    prompt = await build_post_process_system_prompt(
        user_context=user_context,
        runtime_payload=PostProcessRuntimePayload(
            intent=intent,
            reason=reason,
            final_status=final_status,
            final_reply=final_reply,
            trace="\n".join(trace),
            facts=json.dumps(facts, ensure_ascii=False, indent=2) if facts else "",
        ),
    )
    llm_messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content=json.dumps(
                {
                    "messages": _serialize_messages(messages),
                },
                ensure_ascii=False,
            )
        ),
    ]
    response, _ = await invoke_with_usage_logging(
        llm=llm,
        messages=llm_messages,
        node="post_process_service_memory",
        thread_id=thread_id,
        user_id=user_id,
        provider="deepseek",
    )
    return response


async def build_service_memory(
    state: Mapping[str, Any],
    *,
    messages: Sequence[BaseMessage],
) -> Dict[str, Any]:
    user_id = str(state.get("user_id") or "unknown")
    thread_id = str(state.get("thread_id") or "").strip()
    intent = str(state.get("intent") or "unknown").strip() or "unknown"
    reason = str(state.get("reason") or "").strip()
    final_reply = str(state.get("final_reply") or "").strip()
    final_status = str(state.get("final_status") or "").strip()
    final_reason = str(state.get("final_reason") or "").strip()
    started_at = str(state.get("started_at") or "").strip() or _utc_now_iso()
    ended_at = _utc_now_iso()
    trace = _normalize_trace(
        state.get("trace"),
        intent=intent,
        final_status=final_status,
        final_reply=final_reply,
    )
    facts = _extract_facts(state, intent)
    last_service = await load_last_service_memory(user_id, thread_id) if thread_id else {}

    summary_result = await _summarize_service_memory(
        user_id=user_id,
        thread_id=thread_id,
        intent=intent,
        reason=reason,
        trace=trace,
        final_reply=final_reply,
        final_status=final_status,
        facts=facts,
        messages=messages,
        user_context=state.get("user_context") if isinstance(state.get("user_context"), Mapping) else {},
    )
    goal = _trim_text(summary_result.goal, 120)
    summary = _trim_text(summary_result.summary, 300)
    is_continuous = bool(summary_result.is_continuous)

    merged_trace = trace
    merged_facts = facts
    if is_continuous and isinstance(last_service, Mapping):
        previous_trace = last_service.get("trace") or []
        if isinstance(previous_trace, str):
            previous_trace = [previous_trace]
        merged_trace = []
        for item in [*previous_trace, *trace]:
            text = str(item or "").strip()
            if text and text not in merged_trace:
                merged_trace.append(text)
        previous_facts = last_service.get("facts") or {}
        if isinstance(previous_facts, Mapping):
            merged_facts = {**dict(previous_facts), **facts}

    return {
        "intent": intent,
        "goal": _trim_text(goal or str((last_service or {}).get("goal") or ""), 120),
        "summary": summary,
        "trace": merged_trace,
        "facts": merged_facts,
        "final_status": final_status,
        "final_reason": final_reason,
        "started_at": started_at,
        "ended_at": ended_at,
        "is_continuous_with_last": is_continuous,
    }
