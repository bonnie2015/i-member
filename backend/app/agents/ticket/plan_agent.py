from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, field_validator, model_validator

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import invoke_with_usage_logging
from app.prompts.prompt_builder import (
    PromptCapabilityContext,
    TicketPlanRuntimePayload,
    build_ticket_plan_system_prompt,
)
from app.skills.registry import load_skill_context, load_skill_metadata
from app.tools import get_scrm_tools, onitsuka_get_product_detail
from app.tools.memory_tools import get_memory_tools

logger = get_logger("ticket_plan_agent")

MAX_PLAN_STEPS = 5
_PLAN_TIMEOUT_SECONDS = 60


class ExpectedSlot(BaseModel):
    name: str
    description: str = ""


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
    expected_slots: List[ExpectedSlot] = Field(default_factory=list)
    slots: Dict[str, Any] = Field(default_factory=dict)
    steps: List[TicketPlanStep] = Field(default_factory=list)

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
        return text[start: end + 1]
    return text


def _load_selected_skill_content(service_key: str) -> str:
    return load_skill_context(
        load_skill_metadata(service_key, group="ticket").get("location"),
        group="ticket",
    )


def _tool_registry() -> Dict[str, BaseTool]:
    return {str(tool.name): tool for tool in [*get_scrm_tools(), onitsuka_get_product_detail, *get_memory_tools()]}


def _resolve_planner_tools(tool_names: List[str]) -> List[BaseTool]:
    registry = _tool_registry()
    resolved: List[BaseTool] = []
    for item in tool_names:
        tool_name = str(item or "").strip()
        tool = registry.get(tool_name)
        if tool is not None:
            resolved.append(tool)
    return resolved


class TicketPlanAgent(BaseAgent):

    def __init__(self):
        config = AgentConfig(
            name="ticket_plan",
            role="ticket",
            timeout_seconds=_PLAN_TIMEOUT_SECONDS,
            max_recursion=1,
            max_tool_calls=0,
            fallback_reply="",
        )
        super().__init__(config)

    async def _execute(self, input: AgentInput) -> AgentOutput:
        service_key = str(input.extra.get("service_key") or "").strip()
        goal = str(input.extra.get("goal") or "").strip()
        current_step_index = int(input.extra.get("current_step_index") or 0)
        existing_slots = dict(input.extra.get("slots") or {})

        skill_meta = load_skill_metadata(service_key, group="ticket")
        selected_skill_content = _load_selected_skill_content(service_key)
        available_tool_names = [str(item).strip() for item in skill_meta.get("available_tools") or [] if str(item).strip()]
        tools = [*_resolve_planner_tools(available_tool_names), *get_memory_tools()]
        valid_tool_names = {str(t.name) for t in tools}

        failed_step = dict(input.extra.get("failed_step") or {})

        prompt = await build_ticket_plan_system_prompt(
            capability_context=PromptCapabilityContext(
                selected_skill_content=selected_skill_content,
            ),
            runtime_context=TicketPlanRuntimePayload(
                current_goal=goal,
                current_step_index=current_step_index,
                slots=existing_slots,
                failed_step=failed_step if failed_step.get("step_status") in ("failed", "pending") else None,
            ),
        )

        llm = get_llm("ticket")
        if tools:
            llm = llm.bind_tools(tools, tool_choice="none")

        response, _ = await invoke_with_usage_logging(
            llm=llm,
            messages=[SystemMessage(content=prompt), HumanMessage(content=input.user_query)],
            node="ticket_plan",
            thread_id=input.thread_id,
            user_id=input.user_id,
            provider="deepseek",
            timeout_seconds=_PLAN_TIMEOUT_SECONDS,
        )

        payload = json.loads(_extract_json_object(_extract_text_content(getattr(response, "content", ""))))
        plan = TicketPlan.model_validate(payload)

        # 过滤无效工具名
        for step in plan.steps:
            step.available_tools = [t for t in step.available_tools if t in valid_tool_names]

        plan_data = plan.model_dump()
        reason = str(plan_data.get("reason") or "").strip()
        steps_count = len(list(plan_data.get("steps") or []))
        expected_slots_count = len(list(plan_data.get("expected_slots") or []))
        pre_filled_slots = dict(plan_data.get("slots") or {})

        logger.info(
            "[ticket_plan_agent] thread_id=%s service_key=%s steps=%s expected_slots=%s pre_filled_slots=%s reason=%s",
            input.thread_id,
            service_key,
            steps_count,
            expected_slots_count,
            len(pre_filled_slots),
            reason or "none",
        )

        return AgentOutput(
            reply="",
            status=AgentStatus.SUCCESS,
            data={
                "reason": reason,
                "expected_slots": plan_data.get("expected_slots") or [],
                "slots": pre_filled_slots,
                "steps": plan_data.get("steps") or [],
            },
        )


ticket_plan_agent = TicketPlanAgent()
