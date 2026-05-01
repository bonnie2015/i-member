from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_validator, model_validator

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    PromptCapabilityContext,
    TicketPlanRuntimePayload,
    build_ticket_plan_system_prompt,
)
from app.agents.skills.registry import load_skill_context, load_skill_metadata
from app.agents.tools import get_scrm_tools, onitsuka_get_product_detail
from app.config.logging import get_logger
from app.workflow.state import AgentState, TicketNextNode

logger = get_logger("ticket_planner")

MAX_PLAN_STEPS = 5
_PLAN_TIMEOUT_SECONDS = 60


class TicketPlanStep(BaseModel):
    goal: str
    completion_signal: str = ""
    target_slots: List[str] = Field(default_factory=list)
    available_tools: List[str] = Field(default_factory=list)
    step_status: str = "pending"
    failed_reason: str = ""
    failed_type: str = ""
    try_process: Any = Field(default_factory=list)

    @field_validator("target_slots", "available_tools", mode="before")
    @classmethod
    def normalize_string_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_items = [value]
        elif isinstance(value, list):
            raw_items = value
        else:
            return []

        normalized: List[str] = []
        for item in raw_items:
            key = str(item or "").strip()
            if key and key not in normalized:
                normalized.append(key)
        return normalized


class TicketPlan(BaseModel):
    reason: str = ""
    expected_slots: List[str] = Field(default_factory=list)
    steps: List[TicketPlanStep] = Field(default_factory=list)

    @field_validator("expected_slots", mode="before")
    @classmethod
    def normalize_expected_slots(cls, value: Any) -> List[str]:
        return TicketPlanStep.normalize_string_list(value)

    @model_validator(mode="after")
    def validate_plan(self) -> "TicketPlan":
        self.reason = str(self.reason or "").strip()
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(f"ticket plan must contain at most {MAX_PLAN_STEPS} steps")
        if not self.steps and not self.reason:
            raise ValueError("ticket plan without steps must provide reason")
        return self


def _extract_text_content(content: Any) -> str:
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


def _extract_json_object(text: str) -> str:
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _load_selected_skill_content(service_key: str) -> str:
    return load_skill_context(
        load_skill_metadata(service_key, group="ticket").get("location"),
        group="ticket",
    )


def _tool_registry() -> Dict[str, BaseTool]:
    return {str(tool.name): tool for tool in [*get_scrm_tools(), onitsuka_get_product_detail]}


def _resolve_planner_tools(tool_names: List[str]) -> List[BaseTool]:
    registry = _tool_registry()
    resolved: List[BaseTool] = []
    for item in tool_names:
        tool_name = str(item or "").strip()
        tool = registry.get(tool_name)
        if tool is not None:
            resolved.append(tool)
    return resolved


def _filter_plan_tools(plan: TicketPlan, bound_tools: List[BaseTool]) -> TicketPlan:
    allowed_tool_names = {str(tool.name) for tool in bound_tools}
    if not allowed_tool_names:
        return plan
    for step in plan.steps:
        step.available_tools = [
            tool_name for tool_name in step.available_tools if tool_name in allowed_tool_names
        ]
    return plan


def _plan_runtime_payload(
    state: AgentState,
    *,
    current_step_index: int,
) -> TicketPlanRuntimePayload:
    return TicketPlanRuntimePayload(
        current_goal=str(state.get("goal") or "").strip(),
        current_step_index=current_step_index,
        slots=dict(state.get("slots") or {}),
    )


def _merge_plan_steps(
    previous_steps: List[Dict[str, Any]],
    current_step_index: int,
    new_steps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    normalized_new_steps = []
    for step in new_steps:
        if not isinstance(step, dict):
            continue
        normalized_step = dict(step)
        normalized_step["step_status"] = "pending"
        normalized_step["failed_reason"] = ""
        normalized_step["failed_type"] = ""
        normalized_step["try_process"] = []
        normalized_new_steps.append(normalized_step)
    preserved_prefix = [
        dict(step)
        for step in previous_steps[:current_step_index]
        if isinstance(step, dict)
    ]
    return [*preserved_prefix, *normalized_new_steps]


async def _invoke_llm_for_plan(
    state: AgentState,
    *,
    current_step_index: int,
) -> TicketPlan:
    service_key = str(state.get("service_key") or "").strip()
    skill_meta = load_skill_metadata(service_key, group="ticket")
    selected_skill_content = _load_selected_skill_content(service_key)
    available_tools = [str(item).strip() for item in skill_meta.get("available_tools") or [] if str(item).strip()]
    bound_tools = _resolve_planner_tools(available_tools)
    prompt = await build_ticket_plan_system_prompt(
        capability_context=PromptCapabilityContext(
            selected_skill_content=selected_skill_content,
        ),
        runtime_context=_plan_runtime_payload(
            state,
            current_step_index=current_step_index,
        ),
    )
    llm_messages = [SystemMessage(content=prompt)]
    llm = get_remote_llm(role="ticket")
    if bound_tools:
        llm = llm.bind_tools(bound_tools)
    response, _ = await invoke_with_usage_logging(
        llm=llm,
        messages=llm_messages,
        node="ticket_plan",
        thread_id=state.get("thread_id"),
        user_id=state.get("user_id"),
        provider="deepseek",
        timeout_seconds=_PLAN_TIMEOUT_SECONDS,
    )
    payload = json.loads(_extract_json_object(_extract_text_content(getattr(response, "content", ""))))
    return _filter_plan_tools(TicketPlan.model_validate(payload), bound_tools)


async def plan_node(state: AgentState) -> Dict[str, Any]:
    service_key = str(state.get("service_key") or "").strip()
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    if not service_key:
        logger.warning("[planner] thread_id=%s missing_service_key", thread_id)
        return {
            "ticket_next_node": TicketNextNode.END,
            "final_status": "failed",
            "final_reason": "missing_service_key",
            "planner_reason": "missing_service_key",
        }

    previous_steps = list(state.get("steps") or [])
    current_step_index = int(state.get("current_step_index") or 0)

    try:
        plan = await _invoke_llm_for_plan(
            state,
            current_step_index=current_step_index,
        )
    except Exception as exc:
        logger.error("[planner] thread_id=%s plan_generation_failed error=%s", thread_id, exc)
        return {
            "ticket_next_node": TicketNextNode.END,
            "final_status": "failed",
            "final_reason": "plan_generation_failed",
            "planner_reason": "plan_generation_failed",
        }

    plan_data = plan.model_dump()
    reason = str(plan_data.get("reason") or "").strip()
    steps_count = len(list(plan_data.get("steps") or []))
    remaining_step_count = max(MAX_PLAN_STEPS - current_step_index, 0)

    logger.info(
        "[planner] thread_id=%s current_step_index=%d steps_count=%d remaining_step_count=%d reason=%s",
        thread_id,
        current_step_index,
        steps_count,
        remaining_step_count,
        reason or "none",
    )

    if not steps_count:
        return {
            "ticket_next_node": TicketNextNode.END,
            "final_status": "failed",
            "final_reason": reason or "plan_impossible",
            "planner_reason": reason or "plan_impossible",
        }

    if steps_count > remaining_step_count:
        logger.warning(
            "[planner] thread_id=%s plan_steps_exceed_budget current_step_index=%d steps_count=%d remaining_step_count=%d",
            thread_id,
            current_step_index,
            steps_count,
            remaining_step_count,
        )
        return {
            "ticket_next_node": TicketNextNode.END,
            "final_status": "failed",
            "final_reason": "plan_steps_exceed_budget",
            "planner_reason": "plan_steps_exceed_budget",
        }

    steps = _merge_plan_steps(previous_steps, current_step_index, list(plan_data.get("steps") or []))
    return {
        "goal": str(state.get("goal") or "").strip(),
        "steps": steps,
        "expected_slots": list(plan_data.get("expected_slots") or []),
        "ticket_next_node": TicketNextNode.EXECUTOR,
        "final_status": None,
        "final_reason": None,
        "planner_reason": None,
        "executor_retry_count": 0,
        "current_step_index": current_step_index,
    }
