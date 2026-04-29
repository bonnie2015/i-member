from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator, model_validator

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    PromptCapabilityContext,
    TicketPlanRuntimePayload,
    build_ticket_plan_system_prompt,
)
from app.agents.skills.registry import load_skill_context, load_skill_metadata
from app.agents.tools import TOOL_SPECS
from app.config.logging import get_logger
from app.workflow.state import (
    TicketNextAction,
    TicketState,
    resolve_current_step_index,
)

logger = get_logger("ticket_planner")

MAX_PLAN_STEPS = 5
MAX_REPLAN_COUNT = 3
_PLAN_TIMEOUT_SECONDS = 60


def _with_reason_prefix(reason: Any, prefix: str, *, fallback: str = "") -> str:
    normalized = str(reason or "").strip() or fallback
    if not normalized:
        return ""
    if normalized.startswith(prefix):
        return normalized
    return f"{prefix}{normalized}"


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

    @model_validator(mode="after")
    def validate_step_structure(self) -> "TicketPlanStep":
        self.available_tools = [tool for tool in self.available_tools if tool in TOOL_SPECS]
        return self


class TicketPlan(BaseModel):
    plan_status: str = "planned"
    plan_reason: str = ""
    slots: Dict[str, Any] = Field(default_factory=dict)
    expected_slots: List[str] = Field(default_factory=list)
    steps: List[TicketPlanStep] = Field(default_factory=list)

    @field_validator("expected_slots", mode="before")
    @classmethod
    def normalize_expected_slots(cls, value: Any) -> List[str]:
        return TicketPlanStep.normalize_string_list(value)

    @model_validator(mode="after")
    def validate_plan(self) -> "TicketPlan":
        self.plan_status = str(self.plan_status or "planned").strip() or "planned"
        self.plan_reason = str(self.plan_reason or "").strip()
        if self.plan_status not in {"planned", "impossible", "cancelled"}:
            raise ValueError("ticket plan_status must be one of planned/impossible/cancelled")
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(f"ticket plan must contain at most {MAX_PLAN_STEPS} steps")
        if self.plan_status == "planned" and not self.steps:
            raise ValueError("ticket plan with plan_status=planned must contain at least one step")
        if self.plan_status in {"impossible", "cancelled"} and self.steps:
            raise ValueError("ticket plan with terminal plan_status must not contain steps")
        if self.plan_status in {"impossible", "cancelled"} and not self.plan_reason:
            raise ValueError("ticket plan with terminal plan_status must provide plan_reason")
        return self


def _tool_summary(tool_names: List[str]) -> str:
    lines: List[str] = []
    for tool_name in tool_names:
        spec = TOOL_SPECS.get(tool_name) or {}
        required_fields = ", ".join(spec.get("required_fields") or []) or "-"
        path = str(spec.get("path") or "").strip() or "-"
        method = str(spec.get("method") or "").strip() or "-"
        lines.append(
            f"- {tool_name}: method={method}, path={path}, required_fields={required_fields}"
        )
    return "\n".join(lines) or "[None]"


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


def _compact_try_process(try_process: Any) -> List[Dict[str, Any]]:
    if not isinstance(try_process, list):
        return []
    compacted: List[Dict[str, Any]] = []
    for item in try_process:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                key: value
                for key, value in item.items()
                if key in {"tool_name", "request", "response"} and value not in (None, "", [], {})
            }
        )
    return [item for item in compacted if item]


def _recent_messages_payload(state: TicketState) -> List[Dict[str, str]]:
    payload: List[Dict[str, str]] = []
    messages = list(state.get("messages") or [])
    for message in messages[-2:]:
        if not isinstance(message, BaseMessage):
            continue
        payload.append(
            {
                "role": str(getattr(message, "type", message.__class__.__name__) or "").strip(),
                "content": _extract_text_content(getattr(message, "content", "")).strip(),
            }
        )
    return [item for item in payload if item.get("content")]


def _load_selected_skill_content(service_key: str) -> str:
    return load_skill_context(
        load_skill_metadata(service_key, group="ticket").get("location"),
        group="ticket",
    )


def _plan_runtime_payload(state: TicketState, current_step_index: int | None) -> TicketPlanRuntimePayload:
    current_steps = list(state.get("steps") or [])
    failed_step: Dict[str, Any] = {}
    if current_step_index is not None and 0 <= current_step_index < len(current_steps):
        step = current_steps[current_step_index]
        if isinstance(step, dict):
            failed_step = {
                "completion_signal": step.get("completion_signal"),
                "failed_reason": str(step.get("failed_reason") or "").strip(),
                "failed_type": str(step.get("failed_type") or "").strip(),
                "try_process": _compact_try_process(step.get("try_process")),
            }

    return TicketPlanRuntimePayload(
        planning_mode="replan" if failed_step else "initial_plan",
        current_goal=str(state.get("goal") or "").strip(),
        slots=dict(state.get("slots") or {}),
        failed_step=failed_step,
    )


def _plan_log_view(plan_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "plan_status": plan_data.get("plan_status"),
        "plan_reason": plan_data.get("plan_reason"),
        "expected_slots": plan_data.get("expected_slots") or [],
        "steps": [
            {
                "goal": step.get("goal"),
                "completion_signal": step.get("completion_signal"),
                "target_slots": step.get("target_slots"),
                "available_tools": step.get("available_tools"),
                "step_status": step.get("step_status"),
                "failed_reason": step.get("failed_reason"),
                "failed_type": step.get("failed_type"),
            }
            for step in (plan_data.get("steps") or [])
            if isinstance(step, dict)
        ],
    }


def _merge_plan_steps(
    previous_steps: List[Dict[str, Any]],
    current_step_index: int | None,
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
    replace_from = len(previous_steps) if current_step_index is None else max(0, min(int(current_step_index), len(previous_steps)))
    preserved_prefix = [
        dict(step)
        for step in previous_steps[:replace_from]
        if isinstance(step, dict)
    ]
    return [*preserved_prefix, *normalized_new_steps]


async def _invoke_llm_for_plan(state: TicketState, current_step_index: int | None) -> TicketPlan:
    service_key = str(state.get("service_key") or "").strip()
    skill_meta = load_skill_metadata(service_key, group="ticket")
    selected_skill_content = _load_selected_skill_content(service_key)
    available_tools = [str(item).strip() for item in skill_meta.get("available_tools") or [] if str(item).strip()]
    prompt = await build_ticket_plan_system_prompt(
        user_context=state.get("user_context") or {},
        capability_context=PromptCapabilityContext(
            selected_skill_content=selected_skill_content,
            tool_summary=_tool_summary(available_tools),
        ),
        runtime_context=_plan_runtime_payload(state, current_step_index),
    )
    llm_messages = [SystemMessage(content=prompt)]
    recent_messages = _recent_messages_payload(state)
    if recent_messages:
        llm_messages.append(
            HumanMessage(
                content=json.dumps(
                    {"recent_messages": recent_messages},
                    ensure_ascii=False,
                )
            )
        )
    response, _ = await invoke_with_usage_logging(
        llm=get_remote_llm(role="ticket"),
        messages=llm_messages,
        node="ticket_plan",
        thread_id=state.get("thread_id"),
        user_id=state.get("user_id"),
        provider="deepseek",
        timeout_seconds=_PLAN_TIMEOUT_SECONDS,
    )
    payload = json.loads(_extract_json_object(_extract_text_content(getattr(response, "content", ""))))
    return TicketPlan.model_validate(payload)


async def plan_node(state: TicketState) -> Dict[str, Any]:
    service_key = str(state.get("service_key") or "").strip()
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    if not service_key:
        logger.warning("[planner] thread_id=%s missing_service_key", thread_id)
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "missing_service_key",
        }

    replan_count = int(state.get("replan_count") or 0)
    if replan_count >= MAX_REPLAN_COUNT:
        logger.warning("[planner] thread_id=%s replan_limit_reached count=%d", thread_id, replan_count)
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "replan_limit_reached",
        }

    previous_steps = list(state.get("steps") or [])
    current_step_index = resolve_current_step_index(state, previous_steps)

    try:
        plan = await _invoke_llm_for_plan(state, current_step_index)
    except Exception as exc:
        logger.error("[planner] thread_id=%s plan_generation_failed error=%s", thread_id, exc)
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "plan_generation_failed",
        }

    plan_data = plan.model_dump()
    plan_status = str(plan_data.get("plan_status") or "planned").strip() or "planned"
    plan_reason = str(plan_data.get("plan_reason") or "").strip()
    steps_count = len(list(plan_data.get("steps") or []))

    logger.info(
        "[planner] thread_id=%s plan_status=%s steps_count=%d reason=%s",
        thread_id,
        plan_status,
        steps_count,
        plan_reason or "none",
    )

    if plan_status == "impossible":
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": plan_reason or "plan_impossible",
        }
    if plan_status == "cancelled":
        return {
            "next_action": TicketNextAction.END,
            "final_status": "cancelled",
            "final_reason": _with_reason_prefix(plan_reason, "plan:", fallback="plan_cancelled"),
        }

    steps = _merge_plan_steps(previous_steps, current_step_index, list(plan_data.get("steps") or []))
    return {
        "goal": str(state.get("goal") or "").strip(),
        "slots": dict(plan_data.get("slots") or state.get("slots") or {}),
        "steps": steps,
        "expected_slots": list(plan_data.get("expected_slots") or []),
        "next_action": TicketNextAction.EXECUTOR,
        "final_status": None,
        "final_reason": None,
        "current_step_index": 0 if current_step_index is None else current_step_index,
    }
