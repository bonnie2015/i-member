from datetime import datetime, timezone
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage

from app.agents.base import AgentInput, AgentStatus
from app.agents.router_agent import router_agent
from app.config.logging import get_logger
from app.workflow.nodes.post_process.post_process import spawn_post_process_tasks
from app.workflow.state import (
    AgentState,
    extract_last_service_round,
    get_service_clear_state,
)

logger = get_logger("router")
_ALLOWED_INTENTS = {"ticket", "qa", "recommend"}


def router_condition(state: AgentState) -> str:
    intent = str(state.get("intent") or "").strip()
    return intent if intent in _ALLOWED_INTENTS else "qa"


def _build_transition_from_qa(
    new_intent: str,
    reason: str,
    user_input: str,
    state: AgentState,
) -> Dict[str, Any]:
    """从 QA 转出到其他服务：善后 + 保留最后一轮对话 + 设置新路由。"""
    all_messages = list(state.get("messages") or [])
    qa_state = dict(state)
    qa_state["messages"] = all_messages[:-1]  # 排除触发切换的最后一条用户消息
    spawn_post_process_tasks(qa_state)
    last_round = extract_last_service_round(all_messages)

    preserved: List[BaseMessage] = [*last_round, HumanMessage(content=user_input)]

    updates = get_service_clear_state()
    updates["messages"] = preserved
    updates["intent"] = new_intent
    updates["reason"] = reason
    updates["current_subgraph"] = new_intent
    updates["started_at"] = datetime.now(timezone.utc).isoformat()
    return updates


async def router_node(state: AgentState):
    messages = state.get("messages") or []
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    previous_subgraph = state.get("current_subgraph")

    if not messages:
        logger.warning(
            "[router] thread_id=%s messages empty, defaulting to QA", thread_id
        )
        return {
            "intent": "qa",
            "current_subgraph": "qa",
            "reason": "消息为空，默认进入问答模块",
        }

    user_input = str(getattr(messages[-1], "content", "") or "").strip()

    result = await router_agent.run(
        AgentInput(
            user_query=user_input,
            thread_id=thread_id,
            user_id=state.get("user_id"),
            messages=messages,
        )
    )

    routed_intent = (
        result.data.get("intent", "qa")
        if result.status == AgentStatus.SUCCESS
        else "qa"
    )
    if routed_intent not in _ALLOWED_INTENTS:
        routed_intent = "qa"

    reason = str(result.data.get("reason", "") or "")

    # QA 转出到 ticket/recommend：善后 + 清状态 + 进入新子图
    if previous_subgraph == "qa" and routed_intent != "qa":
        logger.info(
            "[router] thread_id=%s qa→%s transition",
            thread_id,
            routed_intent,
        )
        return _build_transition_from_qa(
            new_intent=routed_intent,
            reason=reason,
            user_input=user_input,
            state=state,
        )

    result: Dict[str, Any] = {
        "intent": routed_intent,
        "current_subgraph": routed_intent,
        "reason": reason,
    }
    if routed_intent != "qa":
        result["started_at"] = datetime.now(timezone.utc).isoformat()
    return result
