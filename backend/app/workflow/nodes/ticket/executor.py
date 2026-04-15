from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt as graph_interrupt
from langgraph.prebuilt import create_react_agent

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.agents.tools import get_scrm_tools, interrupt_tool
from app.config.logging import get_logger
from app.models.interaction import build_interaction_template_text
from app.workflow.state import TicketNextAction, TicketState, normalize_current_step_index

logger = get_logger("ticket_executor")

_MAX_RECENT_MESSAGES = 8
_MAX_REACT_RECURSION = 12
_FINAL_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", flags=re.S | re.I)


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


def _extract_json_object(text: str) -> str:
    fenced = _FINAL_JSON_BLOCK_RE.search(text)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


def _extract_final_result(messages: List[BaseMessage]) -> Dict[str, Any] | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        raw_text = _extract_text_content(message.content).strip()
        if not raw_text:
            continue
        json_text = _extract_json_object(raw_text)
        try:
            parsed = json.loads(json_text)
        except Exception:
            continue
        if isinstance(parsed, dict) and "is_success" in parsed:
            return parsed
    return None


def _allowed_slot_keys(step: Dict[str, Any], expected_slots: List[str] | None = None) -> set[str]:
    allowed_keys = {
        str(key).strip()
        for key in (step.get("target_slots") or [])
        if str(key).strip()
    }
    if expected_slots:
        allowed_keys |= {str(k).strip() for k in expected_slots if str(k).strip()}
    return allowed_keys


def _normalized_step_list(step: Dict[str, Any], key: str) -> List[str]:
    raw_items = step.get(key) or []
    if key == "available_tools" and not raw_items and step.get("tool_name"):
        raw_items = [step.get("tool_name")]
    if isinstance(raw_items, str):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        return []

    normalized: List[str] = []
    for item in raw_items:
        value = str(item or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _required_target_slots(step: Dict[str, Any]) -> List[str]:
    return [
        str(key).strip()
        for key in (step.get("target_slots") or [])
        if str(key).strip()
    ]


def _derive_current_slots(step: Dict[str, Any], agent_result: Dict[str, Any], expected_slots: List[str] | None = None) -> Dict[str, Any]:
    allowed_keys = _allowed_slot_keys(step, expected_slots)
    derived: Dict[str, Any] = {}

    current_slots = agent_result.get("current_slots")
    if isinstance(current_slots, dict):
        for key, value in current_slots.items():
            normalized_key = str(key).strip()
            if normalized_key and normalized_key in allowed_keys:
                derived[normalized_key] = value

    result = agent_result.get("result") or {}
    if isinstance(result, dict):
        interaction_result = result.get("interaction_result") or {}
        if isinstance(interaction_result, dict):
            response = interaction_result.get("response") or {}
            if isinstance(response, dict):
                detail = response.get("detail")
                if isinstance(detail, dict):
                    for key, value in detail.items():
                        normalized_key = str(key).strip()
                        if normalized_key and normalized_key in allowed_keys and normalized_key not in derived:
                            derived[normalized_key] = value
    return derived


def _interaction_response_has_answer(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    answer = response.get("answer")
    return bool(str(answer or "").strip())


def _pending_interaction_request(agent_result: Dict[str, Any]) -> Dict[str, Any] | None:
    result = agent_result.get("result") or {}
    if not isinstance(result, dict):
        return None
    interaction_result = result.get("interaction_result") or {}
    if not isinstance(interaction_result, dict):
        return None
    request = interaction_result.get("request") or {}
    response = interaction_result.get("response") or {}
    if not isinstance(request, dict):
        return None
    if _interaction_response_has_answer(response):
        return None
    reply = str(request.get("reply") or "").strip()
    if not reply:
        return None
    return request


def _selected_interaction_detail(interaction: Any, answer: str) -> Dict[str, Any] | None:
    if not isinstance(interaction, dict):
        return None
    normalized_key = str(answer or "").strip()
    if not normalized_key:
        return None
    for item in interaction.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "").strip() != normalized_key:
            continue
        detail = item.get("detail")
        return detail if isinstance(detail, dict) else None
    return None


def _materialize_pending_interaction(agent_result: Dict[str, Any]) -> Dict[str, Any]:
    request = _pending_interaction_request(agent_result)
    if request is None:
        return agent_result

    interaction = request.get("interaction")
    answer = str(graph_interrupt(request) or "").strip()

    result = dict(agent_result.get("result") or {})
    interaction_result = dict(result.get("interaction_result") or {})
    interaction_result["request"] = request
    interaction_result["response"] = {
        "answer": answer,
        "detail": _selected_interaction_detail(interaction, answer),
    }
    result["interaction_result"] = interaction_result
    return {
        **agent_result,
        "result": result,
    }


def _merge_current_slots(step: Dict[str, Any], existing_slots: Dict[str, Any], current_slots: Any, expected_slots: List[str] | None = None) -> Dict[str, Any]:
    merged = dict(existing_slots)
    if isinstance(current_slots, dict):
        allowed_keys = _allowed_slot_keys(step, expected_slots)
        return {
            **merged,
            **{
                str(key).strip(): value
                for key, value in current_slots.items()
                if str(key).strip() and str(key).strip() in allowed_keys
            },
        }
    return merged


def _missing_target_slots(step: Dict[str, Any], merged_slots: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    for key in _required_target_slots(step):
        value = merged_slots.get(key)
        if value in (None, "", [], {}):
            missing.append(key)
    return missing


def _normalize_agent_result(
    step: Dict[str, Any],
    agent_result: Dict[str, Any],
    merged_slots: Dict[str, Any],
) -> Dict[str, Any]:
    normalized = dict(agent_result)
    if not bool(normalized.get("is_success")):
        return normalized

    missing = _missing_target_slots(step, merged_slots)
    if not missing:
        return normalized

    result = dict(normalized.get("result") or {})
    result["reason"] = (
        "当前步骤被判定为成功，但没有沉淀完成该步骤所需的目标槽位："
        + ", ".join(missing)
    )
    normalized["is_success"] = False
    normalized["result"] = result
    return normalized


def _build_updated_step(step: Dict[str, Any], agent_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **dict(step),
        "is_success": bool(agent_result.get("is_success")),
        "result": agent_result.get("result") or {},
    }


def _step_log_view(step: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": step.get("id"),
        "goal": step.get("goal") or step.get("purpose"),
        "available_tools": _normalized_step_list(step, "available_tools"),
        "completion_signal": step.get("completion_signal"),
        "target_slots": step.get("target_slots"),
        "is_success": step.get("is_success"),
        "result": step.get("result"),
    }


def _resolve_step_tools(step: Dict[str, Any]) -> List[BaseTool]:
    tool_registry: Dict[str, BaseTool] = {
        str(tool.name): tool for tool in get_scrm_tools()
    }
    selected: List[BaseTool] = []
    for tool_name in _normalized_step_list(step, "available_tools"):
        tool = tool_registry.get(tool_name)
        if tool is not None:
            selected.append(tool)
    return [*selected, interrupt_tool]


async def _run_step_agent(state: TicketState, step: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    agent = create_react_agent(
        model=get_remote_llm(role="ticket"),
        tools=_resolve_step_tools(step),
        prompt=_agent_prompt(state, step, slots),
    )
    agent_result = await agent.ainvoke(
        {
            "messages": [
                *_recent_messages(state),
                HumanMessage(content="请执行当前 step。结束时只输出一个符合约定的 JSON 结果，不要再调用额外工具提交结果。"),
            ]
        },
        {"recursion_limit": _MAX_REACT_RECURSION},
    )
    messages = list((agent_result or {}).get("messages") or [])
    final_result = _extract_final_result(messages)
    if final_result is not None:
        logger.info("[executor_node] final_result=%s", json.dumps(final_result, ensure_ascii=False))
        return final_result
    raise ValueError("step agent did not return final JSON result")


def _update_step_at_index(steps: List[Dict[str, Any]], index: int, updated_step: Dict[str, Any]) -> List[Dict[str, Any]]:
    updated_steps = list(steps)
    current = dict(updated_steps[index])
    current.update(updated_step)
    updated_steps[index] = current
    return updated_steps


async def executor_node(state: TicketState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    current_step_index = normalize_current_step_index(
        steps,
        int(state.get("current_step_index", 0)),
    )

    if not steps:
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "no_executable_plan",
        }

    current_index = current_step_index
    if current_index is None:
        return {
            "next_action": TicketNextAction.REFLECT,
        }

    step = steps[current_index]
    slots = state.get("slots") or {}
    logger.info(
        "[executor_node] execute current_step_index=%s index=%s id=%s goal=%s",
        current_step_index,
        current_index,
        step.get("id"),
        step.get("goal") or step.get("purpose"),
    )

    try:
        agent_result = await _run_step_agent(state, step, slots)
        agent_result = _materialize_pending_interaction(agent_result)
        derived_current_slots = _derive_current_slots(
            step,
            agent_result,
            list(state.get("expected_slots") or []),
        )
        merged_slots = _merge_current_slots(
            step, slots, derived_current_slots,
            list(state.get("expected_slots") or []),
        )
        agent_result = _normalize_agent_result(step, agent_result, merged_slots)
        updated_step = _build_updated_step(step, agent_result)
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
