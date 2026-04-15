from __future__ import annotations

from typing import Any, Dict

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class SubmitStepResultInput(BaseModel):
    current_slots: Dict[str, Any] = Field(
        default_factory=dict,
        description="当前 step 新获得的槽位，只应包含 target_slots 中的 key。",
    )
    is_success: bool = Field(description="当前 step 是否成功完成。")
    result: Dict[str, Any] = Field(
        default_factory=dict,
        description="支撑当前结论的关键信息，需尽量保留真实工具结果、真实交互结果、取消或无法继续原因。",
    )


@tool("submit_step_result", args_schema=SubmitStepResultInput)
def submit_step_result(current_slots: Dict[str, Any], is_success: bool, result: Dict[str, Any]) -> Dict[str, Any]:
    """Submit the final execution result for the current step without mutating external state."""
    normalized_slots: Dict[str, Any] = {}
    for key, value in (current_slots or {}).items():
        normalized_key = str(key or "").strip()
        if normalized_key:
            normalized_slots[normalized_key] = value

    normalized_result = dict(result or {})
    return {
        "submitted": True,
        "current_slots": normalized_slots,
        "is_success": bool(is_success),
        "result": normalized_result,
    }
