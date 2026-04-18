"""
ticket_reflect.py — reflect 节点

规则：
- 当前步骤成功 -> 下一步或结束
- 当前步骤失败 -> plan
- 当前步骤取消/不可解 -> 结束
- reflect 不负责恢复 step 指针，只负责分发与收口
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage

from app.agents.llm.llm_factory import get_local_llm, get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.config.logging import get_logger
from app.workflow.state import (
    TicketNextAction,
    TicketState,
    normalize_current_step_index,
)

logger = get_logger("ticket_reflect")


def _log_decision(label: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("[reflect_node] %s=%s", label, payload)
    return payload


def _resolve_current_step(steps: List[Dict[str, Any]], current_step_index: int | None) -> Tuple[Optional[int], Dict[str, Any]]:
    if not steps:
        return None, {}
    if current_step_index is not None and 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        if isinstance(step, dict):
            return current_step_index, step
    return None, {}


def _trim_text(value: Any, limit: int = 300) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _latest_step(state: TicketState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    if not steps:
        return {}
    current_step_index = int(state.get("current_step_index", 0))
    if current_step_index <= 0:
        step = steps[0]
        return step if isinstance(step, dict) else {}
    if 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        return step if isinstance(step, dict) else {}
    step = steps[-1]
    return step if isinstance(step, dict) else {}


def _reply_context(state: TicketState) -> Dict[str, Any]:
    step = _latest_step(state)
    result = step.get("result")
    return {
        "final_status": state.get("final_status"),
        "final_reason": state.get("final_reason"),
        "current_goal": _trim_text(state.get("current_goal"), 200),
        "slots": state.get("slots") or {},
        "step": {
            "id": step.get("id"),
            "goal": step.get("goal") or step.get("purpose"),
            "available_tools": step.get("available_tools") or [],
            "is_success": step.get("is_success"),
            "result": result if isinstance(result, dict) else _trim_text(result, 200),
        },
    }


def _build_finalize_prompt(state: TicketState) -> str:
    return load_prompt("ticket/finalize.txt").format(
        context=json.dumps(_reply_context(state), ensure_ascii=False, indent=2)
    )


async def _generate_final_reply(state: TicketState) -> str:
    prompt = _build_finalize_prompt(state)
    try:
        response = await get_local_llm(role="router").ainvoke([HumanMessage(content=prompt)])
        reply = _trim_text(getattr(response, "content", ""))
        if not reply:
            raise ValueError("empty local finalizer reply")
        return reply
    except Exception as exc:
        logger.warning("[reflect_node] local final reply failed: %s", exc)
        response = await get_remote_llm(role="ticket").ainvoke([HumanMessage(content=prompt)])
        reply = _trim_text(getattr(response, "content", ""))
        if not reply:
            raise ValueError("empty remote finalizer reply")
        return reply


async def _finalize(state: TicketState, updates: Dict[str, Any]) -> Dict[str, Any]:
    merged_state: TicketState = {**state, **updates}
    logger.info("[reflect_node] finalize_context=%s", json.dumps(_reply_context(merged_state), ensure_ascii=False))
    final_reply = await _generate_final_reply(merged_state)
    return {
        **updates,
        "next_action": TicketNextAction.END,
        "final_reply": final_reply,
    }


ReflectDecision = Dict[str, Any]


def _executor_decision(current_index: int, replan_count: int | None = None) -> ReflectDecision:
    decision: ReflectDecision = {
        "next_action": TicketNextAction.EXECUTOR,
        "current_step_index": current_index,
        "final_status": None,
        "final_reason": None,
    }
    if replan_count is not None:
        decision["replan_count"] = replan_count
    return decision


def _plan_decision(current_index: int, replan_count: int) -> ReflectDecision:
    return {
        "next_action": TicketNextAction.PLAN,
        "current_step_index": current_index,
        "replan_count": replan_count,
        "final_status": None,
        "final_reason": None,
    }


def _end_decision(final_status: str, final_reason: str, current_step_index: int | None = None) -> ReflectDecision:
    decision: ReflectDecision = {
        "next_action": TicketNextAction.END,
        "final_status": final_status,
        "final_reason": final_reason,
    }
    if current_step_index is not None:
        decision["current_step_index"] = current_step_index
    return decision


def _decide_next_action(state: TicketState) -> ReflectDecision:
    steps = list(state.get("steps") or [])
    if not steps:
        final_status = str(state.get("final_status") or "failed")
        final_reason = str(state.get("final_reason") or "no_executable_plan")
        return _end_decision(final_status, final_reason)

    current_index = normalize_current_step_index(
        steps,
        int(state.get("current_step_index", 0)),
    )
    current_index, current_step_data = _resolve_current_step(steps, current_index)
    if current_index is None:
        return _end_decision("success", "completed", int(state.get("current_step_index", 0)))

    result = current_step_data.get("result") or {}
    if isinstance(result, dict) and bool(result.get("cancelled")):
        return _end_decision(
            "cancelled",
            str(result.get("reason") or "user_cancelled"),
            current_index,
        )

    if not bool(current_step_data.get("is_success")) and isinstance(result, dict) and bool(result.get("unsolvable")):
        return _end_decision("failed", "executor_unsolvable", current_index)

    if not bool(current_step_data.get("is_success")):
        return _plan_decision(current_index, int(state.get("replan_count", 0)) + 1)

    next_index = normalize_current_step_index(steps, current_index + 1)
    if next_index is not None:
        return _executor_decision(next_index, replan_count=0)

    return _end_decision("success", "completed", current_index)


async def reflect_node(state: TicketState) -> Dict[str, Any]:
    try:
        decision = _decide_next_action(state)
        _log_decision("decision", decision)
        if decision["next_action"] == TicketNextAction.END:
            return await _finalize(state, decision)
        return decision
    except Exception as exc:
        logger.exception("[reflect_node] unexpected error: %s", exc)
        decision = _end_decision("failed", "reflect_exception")
        return await _finalize(state, decision)
