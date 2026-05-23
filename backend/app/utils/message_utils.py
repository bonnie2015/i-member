from __future__ import annotations

from typing import Any, List

from langchain_core.messages import HumanMessage

from app.workflow.state import AgentState


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def last_user_message_text(state: AgentState) -> str:
    messages = list(state.get("messages") or [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(getattr(message, "content", "") or "").strip()
    return ""
