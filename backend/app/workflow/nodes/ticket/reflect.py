from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.llm.llm_factory import get_local_llm, get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import build_ticket_finalize_system_prompt
from app.config.logging import get_logger
from app.workflow.state import (
    TicketNextAction,
    TicketState,
    resolve_current_step_index,
)

logger = get_logger("ticket_reflect")


def _resolve_current_step(
    steps: List[Dict[str, Any]],
    current_step_index: int | None,
) -> Tuple[Optional[int], Dict[str, Any]]:
    if not steps:
        return None, {}
    if current_step_index is not None and 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        if isinstance(step, dict):
            return current_step_index, step
    return None, {}


def _latest_step(state: TicketState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    if not steps:
        return {}
    current_step_index = resolve_current_step_index(state, steps)

    if current_step_index == len(steps):
        step = steps[-1]
        return step if isinstance(step, dict) else {}

    if current_step_index is not None and 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        if isinstance(step, dict):
            return step
    return {}


def _reply_context(state: TicketState) -> Dict[str, Any]:
    step = _latest_step(state)
    return {
        "service_key": state.get("service_key"),
        "goal": state.get("goal"),
        "final_status": state.get("final_status"),
        "final_reason": state.get("final_reason"),
        "replan_count": int(state.get("replan_count") or 0),
        "slots": state.get("slots") or {},
        "step": {
            "goal": step.get("goal"),
            "completion_signal": step.get("completion_signal"),
            "step_status": step.get("step_status"),
            "try_process": step.get("try_process"),
        },
    }


async def _generate_final_reply(state: TicketState, updates: Dict[str, Any]) -> str:
    merged_state: TicketState = {**state, **updates}
    prompt = await build_ticket_finalize_system_prompt(
        context=json.dumps(_reply_context(merged_state), ensure_ascii=False, indent=2)
    )
    try:
        response = await get_local_llm(role="reply").ainvoke([HumanMessage(content=prompt)])
        reply = str(getattr(response, "content", "") or "").strip()
        if reply:
            return reply
        raise ValueError("empty local finalizer reply")
    except Exception as exc:
        logger.warning("[reflect_node] local finalize failed: %s", exc)
        response, _ = await invoke_with_usage_logging(
            llm=get_remote_llm(role="ticket"),
            messages=[HumanMessage(content=prompt)],
            node="ticket_finalize",
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            provider="deepseek",
            timeout_seconds=30,
        )
        reply = str(getattr(response, "content", "") or "").strip()
        if not reply:
            raise ValueError("empty remote finalizer reply")
        return reply


def _executor_decision(current_index: int) -> Dict[str, Any]:
    decision: Dict[str, Any] = {
        "next_action": TicketNextAction.EXECUTOR,
        "current_step_index": current_index,
        "final_status": None,
        "final_reason": None,
    }
    return decision


def _plan_decision(current_index: int, replan_count: int) -> Dict[str, Any]:
    return {
        "next_action": TicketNextAction.PLAN,
        "current_step_index": current_index,
        "replan_count": replan_count,
        "final_status": None,
        "final_reason": None,
    }


def _end_decision(final_status: str, final_reason: str, current_step_index: int | None = None) -> Dict[str, Any]:
    decision: Dict[str, Any] = {
        "next_action": TicketNextAction.END,
        "final_status": final_status,
        "final_reason": final_reason,
    }
    if current_step_index is not None:
        decision["current_step_index"] = current_step_index
    return decision


def _existing_terminal_decision(state: TicketState) -> Dict[str, Any] | None:
    if state.get("next_action") != TicketNextAction.END:
        return None

    final_status = str(state.get("final_status") or "").strip()
    final_reason = str(state.get("final_reason") or "").strip()
    if not final_status or not final_reason:
        return None

    current_step_index = state.get("current_step_index")
    if isinstance(current_step_index, int):
        return _end_decision(final_status, final_reason, current_step_index)
    return _end_decision(final_status, final_reason)


def _decide_next_action(state: TicketState) -> Dict[str, Any]:
    terminal_decision = _existing_terminal_decision(state)
    if terminal_decision is not None:
        return terminal_decision

    steps = list(state.get("steps") or [])
    if not steps:
        final_status = str(state.get("final_status") or "failed") or "failed"
        final_reason = str(state.get("final_reason") or "no_executable_plan") or "no_executable_plan"
        return _end_decision(final_status, final_reason)

    current_index = resolve_current_step_index(state, steps)
    if current_index == len(steps):
        return _end_decision("success", "completed", current_index)

    current_index, current_step_data = _resolve_current_step(steps, current_index)
    if current_index is None:
        return _end_decision("success", "completed", len(steps) - 1)

    step_status = str(current_step_data.get("step_status") or "pending").strip() or "pending"
    if step_status == "cancelled":
        return _end_decision("cancelled", str(current_step_data.get("failed_reason") or "user_cancelled"), current_index)

    if step_status == "failed":
        return _plan_decision(current_index, int(state.get("replan_count") or 0) + 1)

    if step_status == "pending":
        return _executor_decision(current_index)

    next_state_index = current_index + 1
    next_state = {**state, "current_step_index": next_state_index}
    next_index = resolve_current_step_index(next_state, steps)
    if next_index == len(steps):
        return _end_decision("success", "completed", next_index)
    if next_index is not None:
        return _executor_decision(next_index)

    return _end_decision("success", "completed", current_index)


async def reflect_node(state: TicketState) -> Dict[str, Any]:
    try:
        decision = _decide_next_action(state)
        logger.info("[reflect_node] decision=%s", json.dumps(decision, ensure_ascii=False))
        if decision["next_action"] != TicketNextAction.END:
            return decision

        final_reply = await _generate_final_reply(state, decision)
        return {
            **decision,
            "final_reply": final_reply,
            "current_subgraph": None,
            "messages": [AIMessage(content=final_reply)],
        }
    except Exception as exc:
        logger.exception("[reflect_node] unexpected error: %s", exc)
        final_reply = "当前服务暂时没有顺利完成。您可以稍后重试，或补充更明确的信息后我再继续帮您处理。"
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "reflect_exception",
            "final_reply": final_reply,
            "current_subgraph": None,
            "messages": [AIMessage(content=final_reply)],
        }
