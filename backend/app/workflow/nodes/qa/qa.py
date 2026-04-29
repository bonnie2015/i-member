from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import build_qa_system_prompt
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("qa_node")

_QA_TIMEOUT_SECONDS = 45


class QaOutput(BaseModel):
    reply: str


def _messages_payload(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    payload: List[Dict[str, str]] = []
    for message in messages:
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            continue
        payload.append(
            {
                "role": str(getattr(message, "type", message.__class__.__name__) or "").strip(),
                "content": content,
            }
        )
    return payload


async def qa_node(state: AgentState) -> Dict[str, Any]:
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    logger.info("[qa_node] thread_id=%s start", thread_id)
    prompt = await build_qa_system_prompt(
        user_context=state.get("user_context") or {},
    )
    llm = get_remote_llm(role="qa").with_structured_output(QaOutput)
    messages_payload = _messages_payload(list(state.get("messages") or []))

    try:
        response, _ = await invoke_with_usage_logging(
            llm=llm,
            messages=[
                SystemMessage(content=prompt),
                HumanMessage(
                    content=json.dumps(
                        {"messages": messages_payload},
                        ensure_ascii=False,
                    )
                ),
            ],
            node="qa",
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            provider="deepseek",
            timeout_seconds=_QA_TIMEOUT_SECONDS,
        )
        final_reply = str(response.reply or "").strip()
        if not final_reply:
            raise ValueError("empty qa reply")
    except Exception as exc:
        logger.exception("[qa_node] thread_id=%s failed: %s", thread_id, exc)
        final_reply = "我先帮您确认一下相关信息，您也可以把问题再说具体一点，我继续为您处理。"
        return {
            "qa_turn_count": int(state.get("qa_turn_count") or 0) + 1,
            "entry_message": str(state.get("entry_message") or (messages_payload[0]["content"] if messages_payload else "")),
            "final_reply": final_reply,
            "final_status": "failed",
            "final_reason": "qa_failed",
            "current_subgraph": None,
            "messages": [AIMessage(content=final_reply)],
        }

    logger.info("[qa_node] thread_id=%s end", thread_id)
    return {
        "qa_turn_count": int(state.get("qa_turn_count") or 0) + 1,
        "entry_message": str(state.get("entry_message") or (messages_payload[0]["content"] if messages_payload else "")),
        "final_reply": final_reply,
        "current_subgraph": None,
        "messages": [AIMessage(content=final_reply)],
    }
