from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.ai import AIMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt import create_react_agent

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.prompts.prompt_builder import (
    TicketExecuteRuntimePayload,
    build_ticket_execute_system_prompt,
)
from app.agents.tools import ask_user_tool, get_scrm_tools, submit_step_result_tool
from app.config.logging import get_logger
from app.workflow.state import (
    TicketNextAction,
    TicketState,
    resolve_current_step_index,
)

logger = get_logger("ticket_executor")

_MAX_RECENT_MESSAGES = 8
_MAX_REACT_RECURSION = 12


def _recent_messages(state: TicketState) -> List[BaseMessage]:
    messages = list(state.get("messages") or [])
    return [message for message in messages[-_MAX_RECENT_MESSAGES:] if isinstance(message, BaseMessage)]


def _message_text(content: Any) -> str:
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


def _parse_json_text(text: str) -> Any:
    raw_text = str(text or "").strip()
    if not raw_text:
        return raw_text
    try:
        return json.loads(raw_text)
    except Exception:
        return raw_text


def _truncate_text(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _extract_final_result(messages: List[BaseMessage]) -> Dict[str, Any] | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        if str(getattr(message, "name", "") or "").strip() != "submit_step_result":
            continue
        parsed = _parse_json_text(getattr(message, "content", ""))
        if isinstance(parsed, dict) and "step_status" in parsed:
            return parsed
    return None


def _build_execution_result(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    execution_result: List[Dict[str, Any]] = []
    pending_calls: Dict[str, Dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in list(message.tool_calls or []):
                pending_calls[str(tool_call.get("id") or "").strip()] = {
                    "tool_name": str(tool_call.get("name") or "").strip(),
                    "request": tool_call.get("args") or {},
                }
            continue

        if isinstance(message, ToolMessage):
            tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            pending = pending_calls.get(tool_call_id, {})
            execution_result.append(
                {
                    "tool_name": str(getattr(message, "name", "") or pending.get("tool_name") or "").strip(),
                    "request": pending.get("request") or {},
                    "response": _parse_json_text(getattr(message, "content", "")),
                }
            )
    return execution_result


def _resolve_step_tools(step: Dict[str, Any]) -> List[BaseTool]:
    tool_registry: Dict[str, BaseTool] = {str(tool.name): tool for tool in get_scrm_tools()}
    selected: List[BaseTool] = []
    raw_tool_names = step.get("available_tools") or []
    if isinstance(raw_tool_names, str):
        raw_tool_names = [raw_tool_names]
    if not isinstance(raw_tool_names, list):
        raw_tool_names = []

    seen_tool_names: set[str] = set()
    for item in raw_tool_names:
        tool_name = str(item or "").strip()
        if not tool_name or tool_name in seen_tool_names:
            continue
        seen_tool_names.add(tool_name)
        tool = tool_registry.get(tool_name)
        if tool is not None:
            selected.append(tool)
    return [*selected, ask_user_tool, submit_step_result_tool]


def _apply_step_result(
    *,
    step: Dict[str, Any],
    existing_slots: Dict[str, Any],
    agent_result: Dict[str, Any],
    expected_slots: List[str],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    allowed_slot_keys = {
        str(key).strip()
        for key in [*(step.get("target_slots") or []), *expected_slots]
        if str(key).strip()
    }
    merged_slots = dict(existing_slots)
    current_slots = agent_result.get("current_slots")
    if isinstance(current_slots, dict):
        merged_slots.update(
            {
                str(key).strip(): value
                for key, value in current_slots.items()
                if str(key).strip() and str(key).strip() in allowed_slot_keys
            }
        )

    normalized_result = dict(agent_result)
    normalized_result["failed_reason"] = str(normalized_result.get("failed_reason") or "").strip()
    normalized_result["failed_type"] = str(normalized_result.get("failed_type") or "").strip()
    step_status = str(normalized_result.get("step_status") or "pending").strip()

    if step_status == "cancelled":
        failed_reason = normalized_result["failed_reason"] or "user_cancelled"
        if not failed_reason.startswith("executor:"):
            failed_reason = f"executor:{failed_reason}"
        normalized_result["failed_reason"] = failed_reason
    elif step_status == "successed":
        missing_slots = [
            str(key).strip()
            for key in (step.get("target_slots") or [])
            if str(key).strip() and merged_slots.get(str(key).strip()) in (None, "", [], {})
        ]
        if missing_slots:
            normalized_result["step_status"] = "failed"
            normalized_result["failed_reason"] = "当前步骤判定成功，但缺少完成该步骤所需的目标槽位：" + ", ".join(missing_slots)
            normalized_result["failed_type"] = "system"

    updated_step = {
        **dict(step),
        "step_status": str(normalized_result.get("step_status") or "failed").strip() or "failed",
        "failed_reason": str(normalized_result.get("failed_reason") or "").strip(),
        "failed_type": str(normalized_result.get("failed_type") or "").strip(),
        "try_process": [],
    }
    return merged_slots, updated_step


async def _run_step_agent(state: TicketState, step: Dict[str, Any], slots: Dict[str, Any]) -> Dict[str, Any]:
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    resolved_tools = _resolve_step_tools(step)
    recent_messages = _recent_messages(state)

    logger.info(
        "[executor] thread_id=%s step_goal=%s target_slots=%s tools=%s",
        thread_id,
        str(step.get("goal") or "").strip()[:50],
        list(step.get("target_slots") or []),
        [str(tool.name) for tool in resolved_tools],
    )

    prompt = await build_ticket_execute_system_prompt(
        user_context=state.get("user_context") or {},
        runtime_context=TicketExecuteRuntimePayload(
            goal=str(state.get("goal") or "").strip(),
            step=step,
            slots=slots,
            expected_slots=list(state.get("expected_slots") or []),
        ),
    )
    agent = create_react_agent(
        model=get_remote_llm(role="ticket"),
        tools=resolved_tools,
        prompt=prompt,
    )
    agent_result = await agent.ainvoke(
        {
            "messages": [*recent_messages]
        },
        config={"recursion_limit": _MAX_REACT_RECURSION},
    )
    messages = list((agent_result or {}).get("messages") or [])
    final_result = _extract_final_result(messages)

    if final_result is not None:
        final_result["try_process"] = _build_execution_result(messages)
        logger.info(
            "[executor] thread_id=%s step_status=%s",
            thread_id,
            final_result.get("step_status"),
        )
        return final_result

    diagnostic_tail = [
        {
            "type": message.__class__.__name__,
            "content": _truncate_text(_message_text(getattr(message, "content", ""))),
        }
        for message in messages[-3:]
    ]
    logger.error(
        "[executor] thread_id=%s missing_final_result tail=%s",
        thread_id,
        json.dumps(diagnostic_tail, ensure_ascii=False),
    )
    raise ValueError("executor generate result failed")


async def executor_node(state: TicketState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])

    if not steps:
        return {
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "no_executable_plan",
        }

    current_step_index = resolve_current_step_index(state, steps)
    if current_step_index == len(steps):
        return {
            "next_action": TicketNextAction.REFLECT,
            "current_step_index": current_step_index,
        }

    step = steps[current_step_index]
    slots = dict(state.get("slots") or {})
    expected_slots = [str(item).strip() for item in list(state.get("expected_slots") or []) if str(item).strip()]

    try:
        agent_result = await _run_step_agent(state, step, slots)
        merged_slots, updated_step = _apply_step_result(
            step=step,
            existing_slots=slots,
            agent_result=agent_result,
            expected_slots=expected_slots,
        )
    except GraphInterrupt:
        raise
    except Exception as exc:
        logger.exception("[executor_node] step agent failed: %s", exc)
        merged_slots = slots
        updated_step = {
            **dict(step),
            "step_status": "failed",
            "failed_reason": str(exc),
            "failed_type": "system",
            "try_process": [{"tool_name": "executor", "request": {}, "response": {"error": str(exc)}}],
        }

    updated_steps = list(steps)
    current = dict(updated_steps[current_step_index])
    current.update(updated_step)
    updated_steps[current_step_index] = current

    return {
        "steps": updated_steps,
        "slots": merged_slots,
        "next_action": TicketNextAction.REFLECT,
        "current_step_index": current_step_index,
    }
