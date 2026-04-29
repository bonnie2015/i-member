from __future__ import annotations

from typing import Any, Dict, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config.logging import get_logger

logger = get_logger("ticket_tools")


class TicketFinalAnswerInput(BaseModel):
    reply: str = Field(description="最终发给用户的话。")
    slots: Dict[str, Any] = Field(default_factory=dict, description="本轮确认或更新后的关键信息，没有则返回空对象。")
    final_status: Literal["success", "failed", "cancelled"] = Field(description="本轮 ticket 服务最终状态。")
    final_reason: str = Field(description="一句话说明为什么以当前状态结束。")


class TicketStepResultInput(BaseModel):
    current_slots: Dict[str, Any] = Field(
        default_factory=dict,
        description="当前步骤确认或更新后的稳定槽位信息。",
    )
    step_status: Literal["successed", "failed", "cancelled"] = Field(
        description="当前步骤最终状态。"
    )
    failed_reason: str = Field(
        default="",
        description="当步骤失败或取消时的原因；成功时留空字符串。",
    )
    failed_type: str = Field(
        default="",
        description='失败类型。建议使用空字符串、"system" 或 "user"。',
    )


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


@tool("submit_step_result", args_schema=TicketStepResultInput, return_direct=True)
def submit_step_result_tool(
    current_slots: Dict[str, Any] | None = None,
    step_status: Literal["successed", "failed", "cancelled"] = "failed",
    failed_reason: str = "",
    failed_type: str = "",
) -> Dict[str, Any]:
    """提交当前 ticket 步骤的最终结果。"""
    payload = TicketStepResultInput(
        current_slots=current_slots or {},
        step_status=step_status,
        failed_reason=failed_reason,
        failed_type=failed_type,
    )
    result = payload.model_dump()
    logger.info("[submit_step_result] payload=%s", result)
    return result
