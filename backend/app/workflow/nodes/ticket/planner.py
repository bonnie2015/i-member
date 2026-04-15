"""
ticket_planner.py

根据最新 planner prompt 与 skill 约束生成完整 TicketPlan。
特点：
- 运行时直接注入统一的 interaction 协议
- 允许模型按需读取 1 个最相关的业务 skill
- 输出结构化 TicketPlan，并规范化为 executor 可直接消费的 steps/slost
- 重规划时保留连续成功前缀，避免覆盖已完成步骤
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.agents.skills.registry import load_skills_snapshot
from app.agents.tools.read_tool import read_file
from app.config.logging import get_logger
from app.models.interaction import InteractionType, build_interaction_requirements_text
from app.workflow.state import TicketNextAction, TicketState

logger = get_logger("ticket_planner")

TICKET_PLAN_PROMPT_FILE = "ticket/plan.txt"
MAX_REPLAN_COUNT = 3
_STEP_ID_PATTERN = re.compile(r"^step_(\d+)$", flags=re.I)


class TicketPlanStep(BaseModel):
    id: str
    type: Literal["ask_user", "tool", "interacting"]
    purpose: str
    tool_name: Optional[str] = None
    interaction_type: Optional[InteractionType] = None
    reply: Optional[str] = None
    completion_signal: str = ""
    target_slots: List[str] = Field(default_factory=list)
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


class TicketPlan(BaseModel):
    current_goal: str
    ticket_scene: Literal["refund", "change", "quality", "complain", "equity", "others"]
    slots: Dict[str, Any] = Field(default_factory=dict)
    expected_slots: List[str] = Field(default_factory=list)
    steps: List[TicketPlanStep] = Field(default_factory=list)


def _plan_log_view(plan_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ticket_scene": plan_data.get("ticket_scene"),
        "current_goal": plan_data.get("current_goal"),
        "slots": plan_data.get("slots") or {},
        "expected_slots": plan_data.get("expected_slots") or [],
        "steps": [
            {
                "id": step.get("id"),
                "type": step.get("type"),
                "tool_name": step.get("tool_name"),
                "purpose": step.get("purpose"),
                "interaction_type": step.get("interaction_type"),
                "reply": step.get("reply"),
                "target_slots": step.get("target_slots"),
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


async def _plain_llm_for_plan(messages: List[Any]) -> TicketPlan:
    response = await get_remote_llm(role="ticket").ainvoke(messages)
    raw_text = _extract_text_content(getattr(response, "content", ""))
    json_text = _extract_json_object(raw_text)
    payload = json.loads(json_text)
    normalized_payload = _normalize_slot_placeholders(payload)
    logger.info(
        "[plan_node] plain llm raw response=%s",
        json.dumps(normalized_payload, ensure_ascii=False, indent=2),
    )
    return TicketPlan.model_validate(normalized_payload)

async def _load_skill_content(prompt: str, conversation_messages: List[Any], snapshot: str) -> str:
    tool_llm = get_remote_llm(role="ticket").bind_tools([read_file])
    llm_messages: List[Any] = [SystemMessage(content=prompt)]
    llm_messages.extend(conversation_messages)
    llm_tool_response = await tool_llm.ainvoke(llm_messages)
    tool_calls = getattr(llm_tool_response, "tool_calls", None) or []

    loaded_skill: str = ""
    chosen_tool_call: Dict[str, Any] | None = None
    for tool_call in tool_calls:
        tool_name = str(tool_call.get("name") or "").strip()
        if tool_name != "read_file":
            continue
        chosen_tool_call = dict(tool_call)
        tool_args = tool_call.get("args") or {}
        try:
            loaded_skill = str(read_file.invoke(tool_args))
        except Exception as exc:
            loaded_skill = f"read_file failed: {exc}"
        break

    if not loaded_skill:
        logger.info(
            "[plan_node] skill loading finished selected_skill=none tool_call=%s",
            json.dumps(chosen_tool_call or {}, ensure_ascii=False),
        )
        return snapshot

    selected_skill_text = loaded_skill.strip()
    logger.info(
        "[plan_node] skill loading finished selected_skill=%s",
        json.dumps(chosen_tool_call or {}, ensure_ascii=False),
    )
    return selected_skill_text


async def _invoke_llm_for_plan(state: TicketState) -> TicketPlan:
    snapshot = load_skills_snapshot()
    current_steps = list(state.get("steps") or [])
    ticket_plan_state = (
        ""
        if not current_steps
        else json.dumps(_ticket_plan_state(state), ensure_ascii=False, indent=2)
    )
    prompt_template = load_prompt(TICKET_PLAN_PROMPT_FILE)
    prompt = prompt_template.format(
        user_id=state.get("user_id", "unknown"),
        skills=snapshot,
        interaction_requirements=build_interaction_requirements_text(),
        ticket_plan_state=ticket_plan_state,
        current_step_index=int(state.get("current_step_index", 0)),
    )
    conversation_messages = list(state.get("messages") or [])
    logger.info("[plan_node] start skill loading current_step_index=%s", int(state.get("current_step_index", 0)))
    merged_skills = await _load_skill_content(prompt, conversation_messages, snapshot)
    prompt = prompt_template.format(
        user_id=state.get("user_id", "unknown"),
        skills=merged_skills,
        interaction_requirements=build_interaction_requirements_text(),
        ticket_plan_state=ticket_plan_state,
        current_step_index=int(state.get("current_step_index", 0)),
    )
    logger.info("[plan_node] planner prompt after skill loading=%s", prompt)
    messages: List[Any] = [SystemMessage(content=prompt)]
    messages.extend(conversation_messages)
    logger.info("[plan_node] start plain json output")
    plan = await _plain_llm_for_plan(messages)
    logger.info("[plan_node] plain json output finished")
    return plan


async def plan_node(state: TicketState) -> Dict[str, Any]:
    replan_count = int(state.get("replan_count", 0))
    if replan_count >= MAX_REPLAN_COUNT:
        logger.warning("[plan_node] replan limit reached: %s", replan_count)
        return {
            "next_action": TicketNextAction.FINALIZE,
            "final_status": "failed",
            "final_reason": "replan_limit_reached",
        }

    try:
        plan = await _invoke_llm_for_plan(state)
    except Exception as exc:
        logger.exception("[plan_node] plan generation failed: %s", exc)
        return {
            "next_action": TicketNextAction.FINALIZE,
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
    current_step_index = int(state.get("current_step_index", 0))

    if ticket_scene == "others":
        return {
            "ticket_scene": "others",
            "current_goal": current_goal,
            "slots": slots,
            "steps": steps,
            "expected_slots": expected_slots,
            "current_step_index": current_step_index,
            "next_action": TicketNextAction.FINALIZE,
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
            "next_action": TicketNextAction.FINALIZE,
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
