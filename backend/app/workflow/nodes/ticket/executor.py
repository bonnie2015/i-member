from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt import create_react_agent

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.agents.tools import get_scrm_tools, interrupt_tool, submit_step_result
from app.config.logging import get_logger
from app.models.interaction import build_interaction_template_text
from app.workflow.state import TicketNextAction, TicketState

logger = get_logger("ticket_executor")

_MAX_RECENT_MESSAGES = 8
_MAX_REACT_RECURSION = 12


def _resolve_execution_index(steps: List[Dict[str, Any]], current_step_index: int) -> int | None:
    if not steps:
        return None
    if 0 <= current_step_index < len(steps):
        return current_step_index
    return None


def _recent_messages(state: TicketState) -> List[BaseMessage]:
    messages = list(state.get("messages") or [])
    return [message for message in messages[-_MAX_RECENT_MESSAGES:] if isinstance(message, BaseMessage)]


def _agent_prompt(state: TicketState, step: Dict[str, Any], slots: Dict[str, Any]) -> str:
    expected_slots = list(state.get("expected_slots") or [])
    return load_prompt("ticket/execute.txt").format(
        user_id=state.get("user_id", "unknown"),
        slots=json.dumps(slots, ensure_ascii=False, indent=2),
        step=json.dumps(step, ensure_ascii=False, indent=2),
        interaction_templates=build_interaction_template_text(),
        expected_slots=json.dumps(expected_slots, ensure_ascii=False),
    )


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


def _extract_submitted_result(messages: List[BaseMessage]) -> Dict[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        raw_text = _extract_text_content(message.content).strip()
        if not raw_text:
            continue
        try:
            parsed = json.loads(raw_text)
        except Exception:
            continue
        if isinstance(parsed, dict) and bool(parsed.get("submitted")):
            return parsed
    return None


def _merge_current_slots(step: Dict[str, Any], existing_slots: Dict[str, Any], current_slots: Any, expected_slots: List[str] | None = None) -> Dict[str, Any]:
    merged = dict(existing_slots)
    if isinstance(current_slots, dict):
        allowed_keys = {
            str(key).strip()
            for key in (step.get("target_slots") or [])
            if str(key).strip()
        }
        if expected_slots:
            allowed_keys |= {str(k).strip() for k in expected_slots if str(k).strip()}
        return {
            **merged,
            **{
                str(key).strip(): value
                for key, value in current_slots.items()
                if str(key).strip() and str(key).strip() in allowed_keys
            },
        }
    return merged


def _build_updated_step(step: Dict[str, Any], agent_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **dict(step),
        "is_success": bool(agent_result.get("is_success")),
        "result": agent_result.get("result") or {},
    }


def _step_log_view(step: Dict[str, Any]) -> Dict[str, Any]:
    interaction = step.get("interaction") if isinstance(step.get("interaction"), dict) else None
    interaction_type = step.get("interaction_type")
    if not interaction_type and interaction:
        interaction_type = interaction.get("interaction_type")
    return {
        "id": step.get("id"),
        "type": step.get("type"),
        "tool_name": step.get("tool_name"),
        "purpose": step.get("purpose"),
        "interaction_type": interaction_type,
        "interaction_items_len": None if not interaction else len(interaction.get("items") or []),
        "reply": step.get("reply"),
        "target_slots": step.get("target_slots"),
        "is_success": step.get("is_success"),
        "result": step.get("result"),
    }


async def _run_step_agent(state: TicketState, step: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    agent = create_react_agent(
        model=get_remote_llm(role="ticket"),
        tools=[*get_scrm_tools(), interrupt_tool, submit_step_result],
        prompt=_agent_prompt(state, step, slots),
    )
    agent_result = await agent.ainvoke(
        {
            "messages": [
                *_recent_messages(state),
                HumanMessage(content="请执行当前 step，并在结束前调用 submit_step_result 提交结果。"),
            ]
        },
        {"recursion_limit": _MAX_REACT_RECURSION},
    )
    messages = list((agent_result or {}).get("messages") or [])
    submitted_result = _extract_submitted_result(messages)
    if submitted_result is not None:
        logger.info("[executor_node] submit_step_result=%s", json.dumps(submitted_result, ensure_ascii=False))
        return submitted_result
    raise ValueError("step agent did not call submit_step_result")


def _update_step_at_index(steps: List[Dict[str, Any]], index: int, updated_step: Dict[str, Any]) -> List[Dict[str, Any]]:
    updated_steps = list(steps)
    current = dict(updated_steps[index])
    current.update(updated_step)
    updated_steps[index] = current
    return updated_steps


async def executor_node(state: TicketState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    current_step_index = int(state.get("current_step_index", 0))

    if not steps:
        return {
            "next_action": TicketNextAction.FINALIZE,
            "final_status": "failed",
            "final_reason": "no_executable_plan",
        }

    current_index = _resolve_execution_index(steps, current_step_index)
    if current_index is None:
        return {
            "next_action": TicketNextAction.REFLECT,
        }

    step = steps[current_index]
    slots = state.get("slots") or {}
    logger.info(
        "[executor_node] execute current_step_index=%s index=%s id=%s type=%s",
        current_step_index,
        current_index,
        step.get("id"),
        step.get("type"),
    )

    try:
        agent_result = await _run_step_agent(state, step, slots)
        updated_step = _build_updated_step(step, agent_result)
        merged_slots = _merge_current_slots(
            step, slots, agent_result.get("current_slots"),
            list(state.get("expected_slots") or []),
        )
    except GraphInterrupt:
        raise
    except Exception as exc:
        logger.exception("[executor_node] step agent failed: %s", exc)
        updated_step = dict(step)
        updated_step.update(
            {
                "is_success": False,
                "result": {"error": str(exc)},
            }
        )
        merged_slots = slots

    logger.info(
        "[executor_node] updated_step=%s merged_slots=%s",
        json.dumps(_step_log_view(updated_step), ensure_ascii=False),
        json.dumps(merged_slots, ensure_ascii=False),
    )

    return {
        "steps": _update_step_at_index(steps, current_index, updated_step),
        "slots": merged_slots,
        "next_action": TicketNextAction.REFLECT,
    }
