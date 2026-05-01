from __future__ import annotations

import json
from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    build_ticket_reflect_plan_failed_system_prompt,
)
from app.config.logging import get_logger
from app.workflow.state import AgentState, TicketNextNode

logger = get_logger("ticket_reflect")

_MAX_EXECUTOR_RETRY = 2
_MAX_REPLAN = 2


async def _generate_planner_failed_reply(state: AgentState) -> str:
    context = {
        "service_key": state.get("service_key"),
        "goal": state.get("goal"),
        "planner_reason": state.get("planner_reason") or state.get("final_reason"),
        "slots": state.get("slots") or {},
    }
    prompt = await build_ticket_reflect_plan_failed_system_prompt(
        context=json.dumps(context, ensure_ascii=False, indent=2, default=str)
    )
    response, _ = await invoke_with_usage_logging(
        llm=get_remote_llm(role="ticket"),
        messages=[HumanMessage(content=prompt)],
        node="ticket_reflect_plan_failed",
        thread_id=state.get("thread_id"),
        user_id=state.get("user_id"),
        provider="deepseek",
        timeout_seconds=30,
    )
    return str(getattr(response, "content", "") or "").strip() or "当前工单服务暂时无法继续推进，本次服务已结束。"


def _planner_failed(state: AgentState) -> bool:
    return (
        state.get("ticket_next_node") == TicketNextNode.END
        and str(state.get("final_status") or "").strip() == "failed"
        and bool(str(state.get("planner_reason") or "").strip())
    )


def _next_executor(current_step_index: int, executor_retry_count: int) -> Dict[str, Any]:
    return {
        "ticket_next_node": TicketNextNode.EXECUTOR,
        "current_step_index": current_step_index,
        "executor_retry_count": executor_retry_count,
        "final_status": None,
        "final_reason": None,
    }


def _next_planner(current_step_index: int, replan_count: int, reason: str) -> Dict[str, Any]:
    return {
        "ticket_next_node": TicketNextNode.PLAN,
        "current_step_index": current_step_index,
        "executor_retry_count": 0,
        "replan_count": replan_count,
        "planner_reason": reason,
        "final_status": None,
        "final_reason": None,
    }


def _end(final_status: str, final_reason: str, current_step_index: int) -> Dict[str, Any]:
    return {
        "ticket_next_node": TicketNextNode.END,
        "current_step_index": current_step_index,
        "final_status": final_status,
        "final_reason": final_reason,
    }


def _final_reply(state: AgentState, final_status: str) -> str:
    reply = str(state.get("final_reply") or "").strip()
    if reply:
        return reply
    if final_status == "success":
        return "已完成本次工单服务。"
    if final_status == "cancelled":
        return "好的，本次工单服务已结束。"
    return "当前工单服务暂时无法继续推进，本次服务已结束。"


def _route_after_step_execution(state: AgentState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    if not steps:
        return _end("failed", str(state.get("final_reason") or "no_executable_plan"), 0)

    current_step_index = int(state.get("current_step_index") or 0)
    if current_step_index >= len(steps):
        return _end("success", "completed", current_step_index)

    step = steps[current_step_index]
    if not isinstance(step, dict):
        return _end("failed", "invalid_step", current_step_index)

    if not step.get("try_process"):
        return _next_executor(current_step_index, int(state.get("executor_retry_count") or 0))

    step_status = str(step.get("step_status") or "pending").strip() or "pending"
    has_next_step = current_step_index + 1 < len(steps)
    executor_retry_count = int(state.get("executor_retry_count") or 0)
    replan_count = int(state.get("replan_count") or 0)

    if step_status == "successed":
        if has_next_step:
            return _next_executor(current_step_index + 1, 0)
        return {
            "final_reply": _final_reply(state, "success"),
            **_end("success", str(state.get("final_reason") or "completed"), current_step_index + 1),
        }

    if step_status == "cancelled":
        return {
            "final_reply": _final_reply(state, "cancelled"),
            **_end("cancelled", str(step.get("failed_reason") or state.get("final_reason") or "user_cancelled"), current_step_index),
        }

    if step_status == "pending" and executor_retry_count < _MAX_EXECUTOR_RETRY:
        return _next_executor(current_step_index, executor_retry_count + 1)

    if step_status in {"pending", "failed"} and replan_count < _MAX_REPLAN:
        reason = str(step.get("failed_reason") or "need_replan").strip() or "need_replan"
        return _next_planner(current_step_index, replan_count + 1, reason)

    return {
        "final_reply": _final_reply(state, "failed"),
        **_end("failed", str(step.get("failed_reason") or state.get("final_reason") or "retry_limit_reached"), current_step_index),
    }


async def reflect_node(state: AgentState) -> Dict[str, Any]:
    try:
        if _planner_failed(state):
            final_reply = await _generate_planner_failed_reply(state)
            return {
                "ticket_next_node": TicketNextNode.END,
                "final_status": "failed",
                "final_reason": str(state.get("planner_reason") or state.get("final_reason") or "plan_failed"),
                "final_reply": final_reply,
                "current_subgraph": None,
                "messages": [AIMessage(content=final_reply)],
            }

        decision = _route_after_step_execution(state)
        logger.info("[reflect_node] decision=%s", json.dumps(decision, ensure_ascii=False, default=str))
        if decision["ticket_next_node"] != TicketNextNode.END:
            return decision

        final_reply = str(decision.get("final_reply") or "").strip() or "当前工单服务已结束。"
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
            "ticket_next_node": TicketNextNode.END,
            "final_status": "failed",
            "final_reason": "reflect_exception",
            "final_reply": final_reply,
            "current_subgraph": None,
            "messages": [AIMessage(content=final_reply)],
        }
