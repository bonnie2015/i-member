from __future__ import annotations

from typing import Any, Dict, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class TicketFinalAnswerInput(BaseModel):
    reply: str = Field(description="最终发给用户的话。")
    slots: Dict[str, Any] = Field(default_factory=dict, description="本轮确认或更新后的关键信息，没有则返回空对象。")
    final_status: Literal["success", "failed", "cancelled"] = Field(description="本轮 ticket 服务最终状态。")
    final_reason: str = Field(description="一句话说明为什么以当前状态结束。")


@tool("submit_final_answer", args_schema=TicketFinalAnswerInput)
def submit_final_answer_tool(
    reply: str,
    slots: Dict[str, Any] | None = None,
    final_status: Literal["success", "failed", "cancelled"] = "success",
    final_reason: str = "",
) -> Dict[str, Any]:
    """提交本轮 ticket 服务的最终结果并结束当前 agent。"""
    payload = TicketFinalAnswerInput(
        reply=reply,
        slots=slots or {},
        final_status=final_status,
        final_reason=final_reason,
    )
    return payload.model_dump()
