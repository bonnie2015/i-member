from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from app.agents.base import AgentInput
from app.agents.summary_agent import summary_agent
from app.agents.user_facts_agent import user_facts_agent
from app.config.logging import get_logger
from app.memory.service_memory import save_service_memory

logger = get_logger("post_process")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_intent(state: Dict[str, Any]) -> str:
    intent = str(state.get("intent") or "").strip()
    return intent or "unknown"


def _build_service_payload(state: Dict[str, Any], intent: str) -> Dict[str, Any]:
    """根据 intent 组装差异化 payload。"""
    if intent == "recommend":
        raw_trace = state.get("trace")
        return {"trace": raw_trace if isinstance(raw_trace, list) else []}
    if intent == "ticket":
        return {
            "service_key": state.get("service_key"),
            "goal": state.get("goal"),
            "steps": list(state.get("steps") or []),
            "slots": dict(state.get("slots") or {}),
            "final_status": state.get("final_status"),
            "final_reason": state.get("final_reason"),
        }
    return {}


async def _build_and_save_service_memory(
    *,
    state: Dict[str, Any],
    thread_id: str,
    user_id: str,
) -> None:
    intent = _resolve_intent(state)
    started_at = str(state.get("started_at") or "").strip() or _utc_now_iso()
    ended_at = _utc_now_iso()

    try:
        summary = await summary_agent.summarize_service(
            messages=list(state.get("messages") or []),
            intent=intent,
            thread_id=thread_id,
            user_id=user_id,
        )
    except Exception as e:
        logger.warning("[post_process] thread_id=%s summary_failed error=%s", thread_id, e)
        summary = f"{intent} 服务已结束。"

    service_memory = {
        "intent": intent,
        "thread_id": thread_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "summary": summary,
        "payload": _build_service_payload(state, intent),
    }

    await save_service_memory(
        user_id=user_id,
        thread_id=thread_id,
        service_memory=service_memory,
    )
    logger.info("[post_process] thread_id=%s intent=%s saved", thread_id, intent)


async def _extract_and_save_user_facts(
    *,
    messages: list[Any],
    thread_id: str,
    user_id: str,
) -> None:
    await user_facts_agent.run(AgentInput(
        user_query=user_id,
        user_id=user_id,
        thread_id=thread_id,
        extra={"messages": messages},
    ))


def _log_background_task_result(task: asyncio.Task[Any], *, task_name: str, thread_id: str, user_id: str) -> None:
    try:
        task.result()
    except Exception as e:
        logger.warning(
            "[post_process] task=%s thread_id=%s user_id=%s failed error=%s",
            task_name, thread_id, user_id, e,
        )


def spawn_post_process_tasks(state: Dict[str, Any]) -> None:
    messages = list(state.get("messages") or [])
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    user_id = str(state.get("user_id") or "unknown")
    state_snapshot = dict(state)

    task1 = asyncio.create_task(
        _build_and_save_service_memory(
            state=state_snapshot,
            thread_id=thread_id,
            user_id=user_id,
        )
    )
    task1.add_done_callback(
        lambda t: _log_background_task_result(t, task_name="service_memory", thread_id=thread_id, user_id=user_id)
    )

    task2 = asyncio.create_task(
        _extract_and_save_user_facts(
            messages=messages,
            thread_id=thread_id,
            user_id=user_id,
        )
    )
    task2.add_done_callback(
        lambda t: _log_background_task_result(t, task_name="user_facts", thread_id=thread_id, user_id=user_id)
    )
