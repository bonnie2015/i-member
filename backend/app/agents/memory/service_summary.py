from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ServiceSummary(BaseModel):
    intent: str = "unknown"
    goal: str = ""
    result: str = ""
    point_info: Dict[str, Any] = Field(default_factory=dict)
    message_summary: str = ""


class ServiceMemorySnapshot(BaseModel):
    service_id: str = ""
    intent: str = "unknown"
    started_at: str = ""
    ended_at: str = ""
    is_continuous: bool = False
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    summary: ServiceSummary = Field(default_factory=ServiceSummary)
    final_reply: str = ""
    state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    result_snapshot: Dict[str, Any] = Field(default_factory=dict)


class ActiveServiceContext(BaseModel):
    last_service_memory: Dict[str, Any] = Field(default_factory=dict)
    current_user_input: str = ""
    merged_messages: List[Dict[str, Any]] = Field(default_factory=list)


def build_service_summary(
    *,
    intent: str = "unknown",
    goal: str = "",
    result: str = "",
    point_info: Dict[str, Any] | None = None,
    message_summary: str = "",
) -> Dict[str, Any]:
    return ServiceSummary(
        intent=intent,
        goal=goal,
        result=result,
        point_info=point_info or {},
        message_summary=message_summary,
    ).model_dump()
