from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph

from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import invoke_with_usage_logging
from app.prompts.prompt_builder import FinalReplyContext, build_ticket_final_reply_prompt
from app.workflow.nodes.ticket.guard import guard_node
from app.workflow.nodes.ticket.executor import executor_node
from app.workflow.nodes.ticket.planner import plan_node
from app.workflow.state import AgentState

logger = get_logger("ticket_subgraph")

_MAX_REPLAN = 2


def _route_after_guard(state: AgentState) -> str:
    decision = str(state.get("guard_decision") or "")
    if decision == "select_service":
        return "plan"
    if decision == "clarify":
        return "clarify"
    return "end"


def _route_after_plan(state: AgentState) -> str:
    steps = state.get("steps") or []
    final_status = str(state.get("final_status") or "").strip()
    if final_status == "failed":
        return "end"
    if steps:
        return "executor"
    return "end"


def reflect_node(state: AgentState) -> dict:
    """读取 executor 产出的 step_status，写入路由决策到 state。只写不读路由。"""
    steps = list(state.get("steps") or [])
    current_step_index = int(state.get("current_step_index") or 0)
    replan_count = int(state.get("replan_count") or 0)

    if not steps or current_step_index >= len(steps):
        logger.info("[ticket_reflect] no steps or index out of range → finalize")
        return {"final_status": "success"}

    step = steps[current_step_index]
    step_status = str(step.get("step_status") or "pending").strip()
    has_next = current_step_index + 1 < len(steps)
    reason = str(step.get("failed_reason") or "").strip()

    logger.info(
        "[ticket_reflect] step=%s/%s status=%s replan=%s",
        current_step_index + 1, len(steps), step_status, replan_count,
    )

    if step_status == "done":
        if has_next:
            logger.info("[ticket_reflect] → next step (executor)")
            return {"current_step_index": current_step_index + 1, "replan_count": 0}
        logger.info("[ticket_reflect] → finalize (success)")
        return {"final_status": "success"}

    if step_status == "cancelled":
        logger.info("[ticket_reflect] → finalize (cancelled)")
        return {"final_status": "cancelled"}

    # pending / failed
    if replan_count < _MAX_REPLAN:
        logger.info("[ticket_reflect] → replan (plan), reason=%s", reason[:60])
        return {"replan_count": replan_count + 1, "planner_reason": reason or "need_replan"}

    logger.info("[ticket_reflect] → finalize (replan limit)")
    return {"final_status": "failed", "final_reason": reason or "replan_limit_reached"}


def _route_after_reflect(state: AgentState) -> str:
    """只读 state：final_status → finalize，planner_reason → plan，否则 → executor。"""
    if str(state.get("final_status") or "").strip():
        return "finalize"
    if str(state.get("planner_reason") or "").strip():
        return "plan"
    return "executor"


async def _generate_final_reply(state: AgentState) -> str:
    try:
        prompt = await build_ticket_final_reply_prompt(
            context=FinalReplyContext(
                service_key=str(state.get("service_key") or ""),
                goal=str(state.get("goal") or ""),
                final_status=str(state.get("final_status") or "success"),
                final_reason=str(state.get("final_reason") or ""),
                slots=dict(state.get("slots") or {}),
            ),
        )
        response, _ = await invoke_with_usage_logging(
            llm=get_llm("ticket"),
            messages=[HumanMessage(content=prompt)],
            node="ticket_final_reply",
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            provider="deepseek",
            timeout_seconds=30,
        )
        return str(getattr(response, "content", "") or "").strip() or "当前工单服务已结束。"
    except Exception as exc:
        logger.warning("[ticket_finalize] final_reply_gen_failed: %s", exc)
        return "当前工单服务已结束。"


async def finalize_node(state: AgentState) -> dict:
    """结束子图前生成最终回复。如果已有 final_reply（来自 guard/plan 失败），直接使用。"""
    existing_reply = str(state.get("final_reply") or "").strip()
    if existing_reply:
        return {
            "current_subgraph": None,
        }

    final_reply = await _generate_final_reply(state)
    return {
        "final_reply": final_reply,
        "current_subgraph": None,
        "messages": [*state["messages"], AIMessage(content=final_reply)],
    }


def get_ticket_workflow():
    graph = StateGraph(AgentState)

    graph.add_node("guard", guard_node)
    graph.add_node("plan", plan_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("guard")
    graph.add_conditional_edges(
        "guard",
        _route_after_guard,
        {"plan": "plan", "clarify": END, "end": "finalize"},
    )
    graph.add_conditional_edges(
        "plan",
        _route_after_plan,
        {"executor": "executor", "end": "finalize"},
    )
    graph.add_edge("executor", "reflect")
    graph.add_conditional_edges(
        "reflect",
        _route_after_reflect,
        {"executor": "executor", "plan": "plan", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)

    return graph.compile()
