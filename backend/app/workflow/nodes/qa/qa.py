from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.base import AgentInput
from app.agents.qa_agent import qa_agent
from app.agents.summary_agent import summary_agent
from app.config.logging import get_logger
from app.llm.runtime import estimate_tokens
from app.workflow.state import AgentState

logger = get_logger("qa_node")

_MAX_QA_TOKENS = 3000
_FALLBACK_REPLY = "我先帮您确认一下相关信息，您也可以把问题再说具体一点，我继续为您处理。"


def _last_user_message(messages: List) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            content = str(getattr(msg, "content", "") or "").strip()
            if content:
                return content
    return ""


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

    # 用 token 估算代替固定轮数判断是否压缩
    msg_text = json.dumps([{"role": type(m).__name__, "content": str(getattr(m, "content", ""))[:200]} for m in messages], ensure_ascii=False)
    if estimate_tokens(msg_text) <= _MAX_QA_TOKENS:
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
