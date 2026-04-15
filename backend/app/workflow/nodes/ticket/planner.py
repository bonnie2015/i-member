"""
ticket_planner.py

根据 planner prompt 与已收敛 scene/skill 生成 TicketPlan。
特点：
- 依赖前置 scene guard 提供已选择的业务 skill
- 输出 5 步以内的宏观 TicketPlan，供 executor 做细执行
- 重规划时保留连续成功前缀，避免覆盖已完成步骤
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator, model_validator

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.agents.tools import TOOL_SPECS
from app.agents.tools.scrm_tools import build_tool_summary_text
from app.config.logging import get_logger
from app.workflow.state import TicketNextAction, TicketState, normalize_current_step_index

logger = get_logger("ticket_planner")

TICKET_PLAN_PROMPT_FILE = "ticket/plan.txt"
MAX_REPLAN_COUNT = 3
MAX_PLAN_STEPS = 5
_STEP_ID_PATTERN = re.compile(r"^step_(\d+)$", flags=re.I)
_VALID_TOOL_NAMES = set(TOOL_SPECS.keys())
class TicketPlanStep(BaseModel):
    id: str
    goal: str
    completion_signal: str = ""
    target_slots: List[str] = Field(default_factory=list)
    available_tools: List[str] = Field(default_factory=list)
    is_success: bool = False
    result: Any = Field(default_factory=dict)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_step_id(cls, value: Any) -> str:
        text = str(value or "").strip()
        match = _STEP_ID_PATTERN.match(text)
        if match:
            return f"step_{int(match.group(1)):02d}"
        return text

    @field_validator("target_slots", mode="before")
    @classmethod
    def normalize_target_slots(cls, value: Any) -> List[str]:
        return cls._normalize_string_list(value)

    @field_validator("available_tools", mode="before")
    @classmethod
    def normalize_available_tools(cls, value: Any) -> List[str]:
        return cls._normalize_string_list(value)

    @classmethod
    def _normalize_string_list(cls, value: Any) -> List[str]:
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
            if not key:
                continue
            if key not in normalized:
                normalized.append(key)
        return normalized

    @model_validator(mode="after")
    def validate_step_structure(self) -> "TicketPlanStep":
        self.available_tools = [
            tool_name for tool_name in self.available_tools if tool_name in _VALID_TOOL_NAMES
        ]
        return self


class TicketPlan(BaseModel):
    current_goal: str
    ticket_scene: Literal["refund", "change", "quality", "complain", "equity", "others"]
    slots: Dict[str, Any] = Field(default_factory=dict)
    expected_slots: List[str] = Field(default_factory=list)
    steps: List[TicketPlanStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_macro_plan(self) -> "TicketPlan":
        if len(self.steps) > MAX_PLAN_STEPS:
            raise ValueError(f"ticket plan must contain at most {MAX_PLAN_STEPS} steps")
        if self.ticket_scene == "others" and self.steps:
            raise ValueError("ticket_scene=others must not contain executable steps")
        return self


def _plan_log_view(plan_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ticket_scene": plan_data.get("ticket_scene"),
        "current_goal": plan_data.get("current_goal"),
        "slots": plan_data.get("slots") or {},
        "expected_slots": plan_data.get("expected_slots") or [],
        "steps": [
            {
                "id": step.get("id"),
                "goal": step.get("goal"),
                "completion_signal": step.get("completion_signal"),
                "target_slots": step.get("target_slots"),
                "available_tools": step.get("available_tools"),
                "is_success": step.get("is_success"),
                "result": step.get("result"),
            }
            for step in (plan_data.get("steps") or [])
            if isinstance(step, dict)
        ],
    }


def _ticket_plan_state(state: TicketState) -> Dict[str, Any]:
    return {
        "current_goal": str(state.get("current_goal") or "").strip(),
        "ticket_scene": state.get("ticket_scene") or "others",
        "slots": dict(state.get("slots") or {}),
        "expected_slots": state.get("expected_slots") or [],
        "steps": list(state.get("steps") or []),
    }


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


_SLOT_PLACEHOLDER_RE = re.compile(r"(?<!\{)\{slots\.([a-zA-Z0-9_]+)\}(?!\})")


def _normalize_slot_placeholders(value: Any) -> Any:
    if isinstance(value, str):
        return _SLOT_PLACEHOLDER_RE.sub(r"{{slots.\1}}", value)
    if isinstance(value, list):
        return [_normalize_slot_placeholders(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_slot_placeholders(item) for key, item in value.items()}
    return value


def _normalize_plan_payload(payload: Any) -> Any:
    normalized = _normalize_slot_placeholders(payload)
    if isinstance(normalized, dict) and normalized.get("ticket_scene") == "others":
        normalized = {
            **normalized,
            "expected_slots": [],
            "steps": [],
        }
    return normalized


async def _plain_llm_for_plan(messages: List[Any]) -> TicketPlan:
    response = await get_remote_llm(role="ticket").ainvoke(messages)
    raw_text = _extract_text_content(getattr(response, "content", ""))
    json_text = _extract_json_object(raw_text)
    payload = json.loads(json_text)
    normalized_payload = _normalize_plan_payload(payload)
    logger.info(
        "[plan_node] plain llm raw response received ticket_scene=%s step_count=%s",
        (normalized_payload or {}).get("ticket_scene"),
        len((normalized_payload or {}).get("steps") or []),
    )
    return TicketPlan.model_validate(normalized_payload)

async def _invoke_llm_for_plan(state: TicketState) -> TicketPlan:
    current_steps = list(state.get("steps") or [])
    ticket_plan_state = (
        ""
        if not current_steps
        else json.dumps(_ticket_plan_state(state), ensure_ascii=False, indent=2)
    )
    selected_skill_content = str(state.get("selected_skill_content") or "").strip()
    prompt_template = load_prompt(TICKET_PLAN_PROMPT_FILE)
    prompt = prompt_template.format(
        user_id=state.get("user_id", "unknown"),
        ticket_scene=state.get("ticket_scene") or "others",
        selected_skill_content=selected_skill_content or "[no selected business skill]",
        tool_summary=build_tool_summary_text(),
        ticket_plan_state=ticket_plan_state,
        current_step_index=int(state.get("current_step_index", 0)),
    )
    conversation_messages = list(state.get("messages") or [])
    logger.info(
        "[plan_node] use preselected skill ticket_scene=%s skill_present=%s",
        state.get("ticket_scene") or "others",
        bool(selected_skill_content),
    )
    messages: List[Any] = [SystemMessage(content=prompt)]
    messages.extend(conversation_messages)
    logger.info("[plan_node] start plain json output")
    plan = await _plain_llm_for_plan(messages)
    logger.info("[plan_node] plain json output finished")
    return plan


async def plan_node(state: TicketState) -> Dict[str, Any]:
    current_steps = list(state.get("steps") or [])
    normalized_index = normalize_current_step_index(
        current_steps,
        int(state.get("current_step_index", 0)),
    )
    replan_count = int(state.get("replan_count", 0))
    if replan_count >= MAX_REPLAN_COUNT:
        logger.warning("[plan_node] replan limit reached: %s", replan_count)
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "replan_limit_reached",
        }

    try:
        plan = await _invoke_llm_for_plan(state)
    except Exception as exc:
        logger.exception("[plan_node] plan generation failed: %s", exc)
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "plan_generation_failed",
        }

    plan_data = plan.model_dump()
    logger.info("[plan_node] plan result=%s", json.dumps(_plan_log_view(plan_data), ensure_ascii=False))
    steps = list(plan_data.get("steps") or [])
    current_goal = plan_data["current_goal"]
    ticket_scene = plan_data["ticket_scene"]
    slots = plan_data["slots"]
    expected_slots = list(plan_data.get("expected_slots") or [])
    current_step_index = normalized_index if normalized_index is not None else int(state.get("current_step_index", 0))

    if ticket_scene == "others":
        return {
            "ticket_scene": "others",
            "current_goal": current_goal,
            "slots": slots,
            "steps": steps,
            "expected_slots": expected_slots,
            "current_step_index": current_step_index,
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "out_of_scope",
        }

    if not steps:
        return {
            "ticket_scene": ticket_scene,
            "current_goal": current_goal,
            "slots": slots,
            "steps": [],
            "expected_slots": expected_slots,
            "current_step_index": current_step_index,
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "no_executable_plan",
        }

    return {
        "ticket_scene": ticket_scene,
        "current_goal": current_goal,
        "slots": slots,
        "steps": steps,
        "expected_slots": expected_slots,
        "current_step_index": current_step_index,
        "next_action": TicketNextAction.EXECUTOR,
        "final_status": None,
        "final_reason": None,
    }
