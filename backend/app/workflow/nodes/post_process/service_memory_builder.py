from __future__ import annotations

import json
from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, Dict, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    ServiceSummaryRuntimePayload,
    build_service_summary_system_prompt,
)
from app.config.logging import get_logger

logger = get_logger("service_memory_builder")


class ServiceSummaryOutput(BaseModel):
    summary: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_service_type(state: Mapping[str, Any]) -> str:
    service_type = str(state.get("service_type") or state.get("intent") or "").strip()
    return service_type or "unknown"


def _serialize_messages(messages: Sequence[Any]) -> list[dict[str, str]]:
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


def _fallback_summary(state: Mapping[str, Any], service_type: str) -> str:
    goal = str(state.get("goal") or "").strip()
    final_status = str(state.get("final_status") or "").strip()
    final_reason = str(state.get("final_reason") or "").strip()
    parts = [part for part in [goal, final_status, final_reason] if part]
    if parts:
        return "；".join(parts)[:120]
    return f"{service_type} 服务已结束。"


async def build_service_summary(state: Mapping[str, Any], *, service_type: str) -> str:
    messages = list(state.get("messages") or [])
    prompt = await build_service_summary_system_prompt(
        runtime_context=ServiceSummaryRuntimePayload(
            service_type=service_type,
            final_status=str(state.get("final_status") or "").strip(),
            final_reason=str(state.get("final_reason") or "").strip(),
        )
    )
    llm = get_remote_llm(role="postprocess").with_structured_output(ServiceSummaryOutput)
    llm_messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content=json.dumps(
                {"messages": _serialize_messages(messages)},
                ensure_ascii=False,
            )
        ),
    ]
    try:
        response, _ = await invoke_with_usage_logging(
            llm=llm,
            messages=llm_messages,
            node="post_process_service_summary",
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            provider="deepseek",
            timeout_seconds=30,
        )
        summary = str(response.summary or "").strip()
        return summary or _fallback_summary(state, service_type)
    except Exception as exc:
        logger.warning("[service_memory_builder] summary_failed error=%s", exc)
        return _fallback_summary(state, service_type)


async def build_service_memory(
    state: Mapping[str, Any],
) -> Dict[str, Any]:
    thread_id = str(state.get("thread_id") or "").strip()
    service_type = _resolve_service_type(state)
    started_at = str(state.get("started_at") or "").strip() or _utc_now_iso()
    ended_at = _utc_now_iso()
    raw_trace = state.get("trace")
    summary = await build_service_summary(state, service_type=service_type)

    if service_type == "recommend":
        return {
            "service_type": "recommend",
            "thread_id": thread_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "summary": summary,
            "payload": {
                "trace": raw_trace if isinstance(raw_trace, list) else [],
            },
        }

    if service_type == "ticket":
        return {
            "service_type": "ticket",
            "thread_id": thread_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "summary": summary,
            "payload": {
                "service_key": state.get("service_key"),
                "goal": state.get("goal"),
                "steps": list(state.get("steps") or []),
                "slots": dict(state.get("slots") or {}),
                "final_status": state.get("final_status"),
                "final_reason": state.get("final_reason"),
            },
        }

    return {
        "service_type": service_type,
        "thread_id": thread_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "summary": summary,
        "payload": {},
    }
