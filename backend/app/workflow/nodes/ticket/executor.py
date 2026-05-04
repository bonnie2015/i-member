from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.errors import GraphInterrupt

from app.agents.ticket.execute_agent import ticket_execute_agent
from app.agents.base import AgentInput, AgentStatus
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("ticket_executor_node")


def _last_user_message_text(state: AgentState) -> str:
    messages = list(state.get("messages") or [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(getattr(message, "content", "") or "").strip()
    return ""


async def executor_node(state: AgentState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    if not steps:
        logger.warning("[executor_node] thread_id=%s no_steps", thread_id)
        return {
            "final_status": "failed",
            "final_reason": "no_executable_plan",
            "current_step_index": 0,
        }

    current_step_index = int(state.get("current_step_index") or 0)
    if current_step_index >= len(steps):
        logger.warning("[executor_node] thread_id=%s index_out_of_range index=%s total=%s", thread_id, current_step_index, len(steps))
        return {
            "current_step_index": current_step_index,
        }

    step = dict(steps[current_step_index])
    existing_slots = dict(state.get("slots") or {})
    expected_slots = list(state.get("expected_slots") or [])

    logger.info(
        "[executor_node] thread_id=%s step_index=%s goal=%s",
        thread_id, current_step_index, str(step.get("goal") or "")[:60],
    )

    try:
        result = await ticket_execute_agent.run(AgentInput(
            user_query=_last_user_message_text(state),
            thread_id=thread_id,
            user_id=state.get("user_id"),
            messages=state.get("messages") or [],
            extra={
                "step": step,
                "slots": existing_slots,
                "expected_slots": expected_slots,
            },
        ))
    except GraphInterrupt:
        raise
    except Exception as exc:
        logger.exception("[executor_node] thread_id=%s agent_error: %s", thread_id, exc)
        updated_steps = list(steps)
        step["step_status"] = "failed"
        step["failed_reason"] = str(exc)
        step["failed_type"] = "system"
        updated_steps[current_step_index] = step
        return {
            "steps": updated_steps,
            "slots": existing_slots,
            "current_step_index": current_step_index,
        }

    if result.status in {AgentStatus.TIMEOUT, AgentStatus.FAILED, AgentStatus.RECURSION_LIMIT}:
        logger.warning("[executor_node] thread_id=%s agent_degraded status=%s", thread_id, result.status)
        updated_steps = list(steps)
        step["step_status"] = "failed"
        step["failed_reason"] = str(result.error_detail or result.status.value)
        step["failed_type"] = "system"
        updated_steps[current_step_index] = step
        response: Dict[str, Any] = {
            "steps": updated_steps,
            "slots": existing_slots,
            "current_step_index": current_step_index,
        }
        if result.reply:
            response["messages"] = [*state["messages"], AIMessage(content=result.reply)]
        return response

    new_slots = dict(result.data.get("slots") or {})
    merged_slots = {**existing_slots, **new_slots}
    step_status = str(result.data.get("step_status") or "pending").strip()
    try_process = list(result.data.get("try_process") or [])
    reason = str(result.data.get("reason") or "").strip()

    updated_steps = list(steps)
    step["step_status"] = step_status
    step["try_process"] = try_process
    if reason:
        step["failed_reason"] = step.get("failed_reason") or reason
    updated_steps[current_step_index] = step

    logger.info(
        "[executor_node] thread_id=%s step_status=%s new_slots=%s try_process=%s reason=%s",
        thread_id, step_status, len(new_slots), len(try_process), reason or "none",
    )

    response: Dict[str, Any] = {
        "steps": updated_steps,
        "slots": merged_slots,
        "current_step_index": current_step_index,
    }
    if result.reply:
        response["messages"] = [*state["messages"], AIMessage(content=result.reply)]

    return response
