from __future__ import annotations

from typing import Any, Dict, Optional

from langchain_core.tools import tool
from langgraph.types import interrupt as graph_interrupt
from pydantic import BaseModel, Field

from app.models.interaction import InteractionPayload


class InterruptToolInput(BaseModel):
    reply: str = Field(
        description=(
            "发给用户的追问、澄清。"
            "当你仍在等待用户补充信息、选择候选项或确认操作时，必须调用 ask_user。"
            "如果选择与用户交互，reply 应该是对用户的直接说明或提问，而不需要重复 interaction 里的内容。"
        )
    )
    interaction: Optional[InteractionPayload] = Field(
        default=None,
        description=(
            "可选的结构化交互信息。"
            "当当前步骤已经有真实候选项可供用户选择或确认时，优先提供 interaction，"
            "并且必须严格符合 interaction_template 约定的固定结构。"
            "若当前只是纯文本澄清、还没有足够真实数据来生成可选项，可以传 null。"
            "不要伪造 items、detail 或字段值。"
        ),
    )


def _normalize_interaction(interaction: InteractionPayload | Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not interaction:
        return None
    if isinstance(interaction, InteractionPayload):
        return interaction.model_dump()
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


@tool("ask_user", args_schema=InterruptToolInput)
def ask_user_tool(reply: str, interaction: InteractionPayload | Dict[str, Any] | None = None) -> Dict[str, Any]:
    """向用户发起一次正式追问或交互，并暂停当前服务等待用户回复。

    使用规则：
    - 只要你仍在等待用户补充信息、选择对象或确认操作，就必须调用 ask_user。
    - 可以通过已获取信息利用交互模板中的交互类型构建 interaction 与用户交互。
    - interaction 必须严格遵循 interaction_template 的固定结构，尤其是 interaction_type、items、detail。
    - 若当前只有纯文本澄清、没有足够真实数据生成可选项，可以只传 reply，不传 interaction。
    - 不要伪造 interaction 字段，不要在最终 JSON 里写待追问内容。
    """
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
