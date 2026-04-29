from __future__ import annotations

import json
from typing import Any, Dict, List, Literal

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphInterrupt
from pydantic import BaseModel, Field, field_validator

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    TicketExecuteRuntimePayload,
    build_ticket_executor_result_system_prompt,
    build_ticket_execute_system_prompt,
)
from app.agents.tools import ask_user_tool, get_scrm_tools, onitsuka_get_product_detail
from app.agents.tools.business.execution_context import ticket_interaction_source_context
from app.config.logging import get_logger
from app.workflow.state import AgentState, TicketNextNode

logger = get_logger("ticket_executor")

_EXECUTOR_TIMEOUT_SECONDS = 60


class StepExecutionReview(BaseModel):
    step_status: Literal["pending", "successed", "failed", "cancelled"] = "pending"
    extracted_slots: Dict[str, Any] = Field(default_factory=dict)
    failed_reason: str = ""
    failed_type: str = ""
    executor_guidance: str = ""
    final_status: Literal["running", "success", "failed", "cancelled"] = "running"
    final_reason: str = ""
    reply: str = ""

    @field_validator("final_status", mode="before")
    @classmethod
    def normalize_final_status(cls, value: Any) -> Any:
        if value in (None, ""):
            return "running"
        if isinstance(value, str) and value.strip().lower() in {"null", "none", "nil"}:
            return "running"
        return value


def _tool_registry() -> Dict[str, BaseTool]:
    return {str(tool.name): tool for tool in [*get_scrm_tools(), onitsuka_get_product_detail]}


def _available_tools(step: Dict[str, Any]) -> List[BaseTool]:
    registry = _tool_registry()
    selected: List[BaseTool] = []
    raw_tool_names = step.get("available_tools") or []
    if isinstance(raw_tool_names, str):
        raw_tool_names = [raw_tool_names]
    if not isinstance(raw_tool_names, list):
        raw_tool_names = []

    for item in raw_tool_names:
        tool_name = str(item or "").strip()
        tool = registry.get(tool_name)
        if tool_name and tool is not None:
            selected.append(tool)
    return [*selected, ask_user_tool]


def _interaction_sources(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for item in list(step.get("try_process") or []):
        if not isinstance(item, dict):
            continue
        response = item.get("response")
        if isinstance(response, dict) and "error" not in response and "error_code" not in response:
            sources.append(response)
    return sources[-3:]


def _allowed_slot_keys(state: AgentState, step: Dict[str, Any]) -> set[str]:
    return {
        str(key).strip()
        for key in [*(step.get("target_slots") or []), *(state.get("expected_slots") or [])]
        if str(key).strip()
    }


def _valid_slot_value(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, str) and value.strip().lower() in {"unknown", "null", "none", "未知", "不明", "未明确"}:
        return False
    return True


def _confirmed_by_user(step: Dict[str, Any], value: Any) -> bool:
    normalized_value = str(value or "").strip()
    if not normalized_value:
        return False
    for item in list(step.get("try_process") or []):
        if not isinstance(item, dict) or item.get("tool_name") != "ask_user":
            continue
        response = item.get("response")
        if not isinstance(response, dict):
            continue
        detail = response.get("detail")
        if isinstance(detail, dict):
            if any(str(item_value or "").strip() == normalized_value for item_value in detail.values()):
                return True
        answer = str(response.get("answer") or "").strip()
        if normalized_value and normalized_value in answer:
            return True
    return False


def _needs_user_confirmation(slot_key: str) -> bool:
    return slot_key in {"order_id", "order_item_id", "product_id", "ticket_id"}


def _merge_slots(state: AgentState, step: Dict[str, Any], extracted_slots: Dict[str, Any]) -> Dict[str, Any]:
    allowed_keys = _allowed_slot_keys(state, step)
    merged = dict(state.get("slots") or {})
    for key, value in dict(extracted_slots or {}).items():
        normalized_key = str(key).strip()
        if normalized_key not in allowed_keys or not _valid_slot_value(value):
            continue
        if _needs_user_confirmation(normalized_key) and not _confirmed_by_user(step, value):
            continue
        merged[normalized_key] = value
    return merged


async def _execute_once(
    state: AgentState,
    *,
    step: Dict[str, Any],
    tools: List[BaseTool],
) -> Dict[str, Any]:
    prompt = await build_ticket_execute_system_prompt(
        runtime_context=TicketExecuteRuntimePayload(
            step=step,
            slots=dict(state.get("slots") or {}),
        ),
    )
    messages: List[BaseMessage] = [
        SystemMessage(content=prompt),
    ]
    bound_tools = list(tools)
    response, _ = await invoke_with_usage_logging(
        llm=get_remote_llm(role="ticket").bind_tools(bound_tools),
        messages=messages,
        node="ticket_executor",
        thread_id=state.get("thread_id"),
        user_id=state.get("user_id"),
        provider="deepseek",
        timeout_seconds=_EXECUTOR_TIMEOUT_SECONDS,
    )

    tool_calls = list(getattr(response, "tool_calls", None) or [])
    if len(tool_calls) != 1:
        return {
            "tool_name": "executor",
            "request": {},
            "response": {
                "error": "executor_must_call_exactly_one_tool",
                "content": str(getattr(response, "content", "") or "").strip(),
            },
        }

    tool_call = tool_calls[0]
    tool_name = str(tool_call.get("name") or "").strip()
    tool_args = dict(tool_call.get("args") or {})
    registry = {str(tool.name): tool for tool in bound_tools}
    tool = registry.get(tool_name)
    if tool is None:
        return {
            "tool_name": tool_name,
            "request": tool_args,
            "response": {"error": f"tool not available: {tool_name}"},
        }

    try:
        with ticket_interaction_source_context(sources=_interaction_sources(step)):
            result = await tool.ainvoke(tool_args)
    except GraphInterrupt:
        raise
    except Exception as exc:
        result = {"error": str(exc)}

    return {
        "tool_name": tool_name,
        "request": tool_args,
        "response": result,
    }


async def _review_step_execution(
    state: AgentState,
    *,
    step: Dict[str, Any],
    has_next_step: bool,
) -> StepExecutionReview:
    context = {
        "goal": state.get("goal"),
        "current_step": step,
        "slots": state.get("slots") or {},
        "expected_slots": state.get("expected_slots") or [],
        "has_next_step": has_next_step,
    }
    prompt = await build_ticket_executor_result_system_prompt(
        context=json.dumps(context, ensure_ascii=False, indent=2, default=str)
    )
    response, _ = await invoke_with_usage_logging(
        llm=get_remote_llm(role="ticket").with_structured_output(StepExecutionReview),
        messages=[SystemMessage(content=prompt)],
        node="ticket_executor_result",
        thread_id=state.get("thread_id"),
        user_id=state.get("user_id"),
        provider="deepseek",
        timeout_seconds=_EXECUTOR_TIMEOUT_SECONDS,
    )
    return response


async def executor_node(state: AgentState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    if not steps:
        return {
            "ticket_next_node": TicketNextNode.END,
            "final_status": "failed",
            "final_reason": "no_executable_plan",
        }

    current_step_index = int(state.get("current_step_index") or 0)
    if current_step_index >= len(steps):
        return {
            "ticket_next_node": TicketNextNode.REFLECT,
            "current_step_index": current_step_index,
        }

    step = steps[current_step_index]
    if not isinstance(step, dict):
        return {
            "ticket_next_node": TicketNextNode.END,
            "final_status": "failed",
            "final_reason": "invalid_step",
            "current_step_index": current_step_index,
        }

    tools = _available_tools(step)
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    try:
        execution_item = await _execute_once(state, step=step, tools=tools)
    except GraphInterrupt:
        raise
    except Exception as exc:
        logger.exception("[executor_node] thread_id=%s executor_failed: %s", thread_id, exc)
        execution_item = {
            "tool_name": "executor",
            "request": {},
            "response": {"error": str(exc)},
        }

    updated_steps = list(steps)
    current_step = dict(step)
    current_step["try_process"] = [
        *[item for item in list(current_step.get("try_process") or []) if isinstance(item, dict)],
        execution_item,
    ]

    try:
        review = await _review_step_execution(
            state,
            step=current_step,
            has_next_step=current_step_index + 1 < len(steps),
        )
    except Exception as exc:
        logger.exception("[executor_node] thread_id=%s review_failed: %s", thread_id, exc)
        review = StepExecutionReview(
            step_status="failed",
            failed_reason="executor_result_review_failed",
            failed_type="system",
        )
    merged_slots = _merge_slots(state, current_step, review.extracted_slots)
    current_step.update(
        {
            "step_status": review.step_status,
            "failed_reason": str(review.failed_reason or "").strip(),
            "failed_type": str(review.failed_type or "").strip(),
            "executor_guidance": str(review.executor_guidance or "").strip(),
        }
    )
    updated_steps[current_step_index] = current_step

    logger.info(
        "[executor_node] thread_id=%s current_step_index=%s step_status=%s tool_call=%s",
        thread_id,
        current_step_index,
        review.step_status,
        json.dumps(
            {
                "tool_name": execution_item.get("tool_name"),
            },
            ensure_ascii=False,
        ),
    )
    updates: Dict[str, Any] = {
        "steps": updated_steps,
        "slots": merged_slots,
        "ticket_next_node": TicketNextNode.REFLECT,
        "current_step_index": current_step_index,
    }
    if review.final_status != "running":
        updates["final_status"] = review.final_status
    if review.final_reason:
        updates["final_reason"] = review.final_reason
    if review.reply:
        updates["final_reply"] = review.reply
    return updates
