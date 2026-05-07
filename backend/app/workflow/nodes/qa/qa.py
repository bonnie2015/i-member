from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.base import AgentInput
from app.agents.qa_agent import qa_agent
from app.agents.summary_agent import summary_agent
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("qa_node")

_MAX_QA_TURNS = 15
_FALLBACK_REPLY = "我先帮您确认一下相关信息，您也可以把问题再说具体一点，我继续为您处理。"


def _last_user_message(messages: List) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = str(getattr(msg, "content", "") or "").strip()
            if content:
                return content
    return ""


def _count_turns(messages: List) -> int:
    return sum(1 for m in messages if isinstance(m, HumanMessage))


async def qa_node(state: AgentState) -> Dict[str, Any]:
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    user_id = state.get("user_id")
    logger.info("[qa_node] thread_id=%s start", thread_id)

    messages = list(state.get("messages") or [])

    if not messages:
        return {
            "final_reply": _FALLBACK_REPLY,
            "current_subgraph": "qa",
            "messages": [AIMessage(content=_FALLBACK_REPLY)],
        }

    turns = _count_turns(messages)
    current_query = _last_user_message(messages)
    if not current_query:
        return {
            "final_reply": _FALLBACK_REPLY,
            "current_subgraph": "qa",
            "messages": [*messages, AIMessage(content=_FALLBACK_REPLY)],
        }

    if turns <= _MAX_QA_TURNS:
        result = await qa_agent.run(AgentInput(
            user_query=current_query,
            user_context=state.get("user_context") or {},
            thread_id=thread_id,
            user_id=user_id,
            messages=messages,
        ))
        reply = result.reply or _FALLBACK_REPLY
        logger.info("[qa_node] thread_id=%s end turns=%s status=%s", thread_id, turns, result.status.value)
        return {
            "final_reply": reply,
            "messages": [*messages, AIMessage(content=reply)],
            "current_subgraph": "qa",
        }

    # 超过15轮：压缩旧历史，替换 messages 为压缩摘要 + 最新用户消息 + AI 回复
    last_idx = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    history = messages[:last_idx]

    logger.info("[qa_node] thread_id=%s compress turns=%s history_msgs=%s", thread_id, turns, len(history))

    summary = await summary_agent.compress_qa(
        old_messages=history,
        existing_summary="",
        thread_id=thread_id,
        user_id=user_id,
    )

    summary_msg = HumanMessage(content=f"[历史对话摘要]\n{summary}")
    current_msg = HumanMessage(content=current_query)
    result = await qa_agent.run(AgentInput(
        user_query=current_query,
        user_context=state.get("user_context") or {},
        thread_id=thread_id,
        user_id=user_id,
        messages=[summary_msg, current_msg],
    ))

    reply = result.reply or _FALLBACK_REPLY
    logger.info("[qa_node] thread_id=%s end compressed status=%s", thread_id, result.status.value)

    return {
        "final_reply": reply,
        "messages": [summary_msg, current_msg, AIMessage(content=reply)],
        "current_subgraph": "qa",
    }
