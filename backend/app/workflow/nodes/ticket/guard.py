from __future__ import annotations

from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.ticket.guard_agent import ticket_guard_agent
from app.agents.base import AgentInput, AgentStatus
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("ticket_guard_node")


def _last_user_message_text(state: AgentState) -> str:
    messages = list(state.get("messages") or [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(getattr(message, "content", "") or "").strip()
    return ""


async def guard_node(state: AgentState) -> Dict[str, Any]:
    messages = list(state.get("messages") or [])
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    user_query = _last_user_message_text(state)

    result = await ticket_guard_agent.run(AgentInput(
        user_query=user_query,
        thread_id=thread_id,
        user_id=state.get("user_id"),
        messages=messages,
    ))

    if result.status != AgentStatus.SUCCESS:
        logger.error("[guard_node] thread_id=%s agent_failed status=%s", thread_id, result.status)
        fallback_reply = result.reply or "当前服务暂时较忙，请稍等片刻后再试。"
        return {
            "guard_decision": "end_service",
            "final_reply": fallback_reply,
            "final_status": "failed",
            "final_reason": "guard_unavailable",
            "current_subgraph": None,
            "service_key": None,
            "goal": None,
            "messages": [*state["messages"], AIMessage(content=fallback_reply)],
        }

    data = result.data
    decision = str(data.get("decision") or "").strip()

    if decision == "clarify":
        reply = str(data.get("reply") or result.reply or "请补充更具体的服务信息。").strip()
        logger.info("[guard_node] thread_id=%s decision=clarify", thread_id)
        return {
            "guard_decision": "clarify",
            "final_reply": reply,
            "current_subgraph": "ticket",
            "service_key": None,
            "goal": None,
            "messages": [*state["messages"], AIMessage(content=reply)],
        }

    if decision == "end_service":
        reply = str(data.get("reply") or result.reply or "当前工单服务已结束。").strip()
        final_reason = str(data.get("reason") or "ticket_service_ended").strip()
        logger.info("[guard_node] thread_id=%s decision=end reason=%s", thread_id, final_reason)
        return {
            "guard_decision": "end_service",
            "final_reply": reply,
            "final_status": "cancelled",
            "final_reason": final_reason,
            "current_subgraph": None,
            "service_key": None,
            "goal": None,
            "messages": [*state["messages"], AIMessage(content=reply)],
        }

    if decision != "select_service":
        logger.error("[guard_node] thread_id=%s invalid_decision=%s", thread_id, decision)
        return {
            "guard_decision": "end_service",
            "final_reply": "当前服务暂时较忙，请稍等片刻后再试。",
            "final_status": "failed",
            "final_reason": f"invalid_guard_decision:{decision}",
            "current_subgraph": None,
            "service_key": None,
            "goal": None,
            "messages": [*state["messages"], AIMessage(content="当前服务暂时较忙，请稍等片刻后再试。")],
        }

    service_key = str(data.get("service_key") or "").strip()
    goal = str(data.get("goal") or "").strip()
    logger.info("[guard_node] thread_id=%s decision=select service_key=%s", thread_id, service_key)
    return {
        "guard_decision": "select_service",
        "service_key": service_key,
        "goal": goal,
    }
