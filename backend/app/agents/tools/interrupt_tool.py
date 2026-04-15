from __future__ import annotations

from typing import Any, Dict, Optional

from langchain_core.tools import tool
from langgraph.types import interrupt as graph_interrupt
from pydantic import BaseModel, Field

from app.models.interaction import InteractionPayload


class InterruptToolInput(BaseModel):
    reply: str = Field(description="展示给用户的回复文本。")
    interaction: Optional[Dict[str, Any]] = Field(
        default=None,
        description="可选的交互模板，若存在则应符合 InteractionPayload 结构。",
    )


def _normalize_interaction(interaction: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not interaction:
        return None
    return InteractionPayload.model_validate(interaction).model_dump()


def _find_selected_detail(interaction: Dict[str, Any] | None, answer: str) -> Dict[str, Any] | None:
    if not interaction:
        return None
    normalized_key = str(answer or "").strip()
    if not normalized_key:
        return None

    for item in interaction.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "").strip() != normalized_key:
            continue
        detail = item.get("detail")
        return detail if isinstance(detail, dict) else None
    return None


@tool("interrupt", args_schema=InterruptToolInput)
def interrupt_tool(reply: str, interaction: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Pause execution for user input and return the user's reply as a tool result."""
    normalized_interaction = _normalize_interaction(interaction)
    request_payload = {
        "reply": str(reply or "").strip(),
        "interaction": normalized_interaction,
    }
    answer = graph_interrupt(
        request_payload
    )
    answer_text = str(answer or "").strip()
    return {
        "request": request_payload,
        "answer": answer_text,
        "detail": _find_selected_detail(normalized_interaction, answer_text),
    }
