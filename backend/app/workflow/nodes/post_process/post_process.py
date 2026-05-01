from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict

from app.agents.memory.user_facts import extract_and_save_user_facts
from app.config.logging import get_logger
from app.agents.memory.service_memory import save_service_memory
from app.workflow.nodes.post_process.service_memory_builder import build_service_memory

logger = get_logger("post_process")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fallback_service_memory(state: Dict[str, Any]) -> Dict[str, Any]:
    service_type = str(state.get("service_type") or state.get("intent") or "unknown").strip() or "unknown"
    started_at = str(state.get("started_at") or "").strip() or _utc_now_iso()
    ended_at = _utc_now_iso()

    payload = {
        "service_type": service_type,
        "thread_id": str(state.get("thread_id") or "").strip(),
        "started_at": started_at,
        "ended_at": ended_at,
        "summary": "",
        "payload": {},
    }
    if service_type == "recommend":
        raw_trace = state.get("trace")
        payload["payload"] = {"trace": raw_trace if isinstance(raw_trace, list) else []}
    elif service_type == "ticket":
        payload["payload"] = {
            "service_key": state.get("service_key"),
            "goal": state.get("goal"),
            "steps": list(state.get("steps") or []),
            "slots": dict(state.get("slots") or {}),
            "final_status": state.get("final_status"),
            "final_reason": state.get("final_reason"),
        }
    return payload


def _log_background_task_result(task: asyncio.Task[Any], *, task_name: str, thread_id: str, user_id: str) -> None:
    try:
        task.result()
    except Exception as e:
        logger.warning(
            "[post_process] task=%s thread_id=%s user_id=%s failed error=%s",
            task_name,
            thread_id,
            user_id,
            e,
        )


async def _build_and_save_service_memory_task(
    *,
    state: Dict[str, Any],
    thread_id: str,
    user_id: str,
) -> None:
    try:
        service_memory = await build_service_memory(state)
    except Exception as e:
        logger.warning("[post_process] thread_id=%s memory_build_failed error=%s", thread_id, e)
        service_memory = _fallback_service_memory(state)

    await save_service_memory(
        user_id=user_id,
        thread_id=thread_id,
        service_memory=service_memory,
    )
    logger.info(
        "[post_process] thread_id=%s service_type=%s",
        thread_id,
        service_memory.get("service_type"),
    )


def _spawn_service_memory_task(
    *,
    state: Dict[str, Any],
    thread_id: str,
    user_id: str,
) -> None:
    task = asyncio.create_task(
        _build_and_save_service_memory_task(
            state=state,
            thread_id=thread_id,
            user_id=user_id,
        )
    )
    task.add_done_callback(
        lambda current: _log_background_task_result(
            current,
            task_name="service_memory",
            thread_id=thread_id,
            user_id=user_id,
        )
    )


def _spawn_user_facts_task(
    *,
    thread_id: str,
    user_id: str,
    messages: list[Any],
) -> None:
    task = asyncio.create_task(
        extract_and_save_user_facts(
            user_id=user_id,
            messages=messages,
        )
    )
    task.add_done_callback(
        lambda current: _log_background_task_result(
            current,
            task_name="user_facts",
            thread_id=thread_id,
            user_id=user_id,
        )
    )


def spawn_post_process_tasks(state: Dict[str, Any]) -> None:
    messages = list(state.get("messages") or [])
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    user_id = str(state.get("user_id") or "unknown")
    state_snapshot = dict(state)
    _spawn_service_memory_task(
        state=state_snapshot,
        thread_id=thread_id,
        user_id=user_id,
    )
    _spawn_user_facts_task(
        thread_id=thread_id,
        user_id=user_id,
        messages=messages,
    )
