from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    PromptCapabilityContext,
    TicketRuntimePayload,
    build_ticket_thinker_system_prompt,
)
from app.agents.skills.registry import load_skill_context, load_skill_metadata
from app.agents.tools import get_service_memory_tools, get_scrm_tools, ask_user_tool, submit_final_answer_tool
from app.config.logging import get_logger
from app.models.interaction import build_interaction_template_text
from app.workflow.state import AgentState

logger = get_logger("ticket_thinker")
_MAX_TICKET_LOOPS = 10
_TRACE_WINDOW = 6
_THINKER_TIMEOUT_SECONDS = 70


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_skill_tools(service_key: str) -> List[BaseTool]:
    skill_meta = load_skill_metadata(service_key, group="ticket")
    available_tool_names = [str(item).strip() for item in skill_meta.get("available_tools") or [] if str(item).strip()]
    all_scrm_tools = {tool.name: tool for tool in get_scrm_tools()}

    resolved_tools: List[BaseTool] = []
    for tool_name in available_tool_names:
        tool = all_scrm_tools.get(tool_name)
        if tool is None:
            logger.warning("[ticket_think] unresolved ticket tool: service_key=%s tool=%s", service_key, tool_name)
            continue
        resolved_tools.append(tool)
    return resolved_tools


def _build_ticket_tools(service_key: str) -> List[BaseTool]:
    ordered_tools: List[BaseTool] = [
        *_resolve_skill_tools(service_key),
        *get_service_memory_tools(),
        ask_user_tool,
        submit_final_answer_tool,
    ]

    deduped_tools: List[BaseTool] = []
    seen_names: set[str] = set()
    for tool in ordered_tools:
        if tool.name in seen_names:
            continue
        seen_names.add(tool.name)
        deduped_tools.append(tool)
    return deduped_tools


def _serialize_trace(trace: Any, *, limit: int = _TRACE_WINDOW) -> str:
    if isinstance(trace, list):
        source = trace
    elif str(trace or "").strip():
        source = [trace]
    else:
        source = []
    normalized: List[str] = []
    for item in source:
        if isinstance(item, dict):
            tool_name = str(item.get("tool_name") or "").strip()
            tool_result = item.get("tool_result")
            if tool_name:
                if tool_result is None:
                    normalized.append(f"{tool_name}")
                else:
                    try:
                        rendered_result = json.dumps(tool_result, ensure_ascii=False)
                    except Exception:
                        rendered_result = str(tool_result)
                    normalized.append(f"{tool_name}: {rendered_result}")
            continue

        text = str(item).strip()
        if text:
            normalized.append(text)
    if not normalized:
        return ""
    return "\n".join(f"- {item}" for item in normalized[-limit:])


async def thinker_node(state: AgentState) -> Dict[str, Any]:
    service_key = str(state.get("service_key") or "").strip()
    if not service_key:
        raise ValueError("ticket thinker node requires service_key")

    messages = list(state.get("messages") or [])
    tool_messages = list(state.get("tool_messages") or [])
    if not messages and not tool_messages:
        raise ValueError("ticket thinker node requires messages or tool_messages")

    ticket_loop_count = int(state.get("ticket_loop_count") or 0)
    next_loop_count = ticket_loop_count + 1
    if next_loop_count > _MAX_TICKET_LOOPS:
        final_reply = "当前工单服务处理步骤已达上限，请稍后重试，或补充更明确的信息后再继续。"
        return {
            "messages": [AIMessage(content=final_reply)],
            "trace": [
                *[str(item).strip() for item in list(state.get("trace") or []) if str(item).strip()],
                f"达到最大循环次数限制（{_MAX_TICKET_LOOPS}），结束本次工单服务",
            ],
            "final_reply": final_reply,
            "final_status": "failed",
            "final_reason": "ticket_loop_limit_exceeded",
            "ticket_loop_count": ticket_loop_count,
        }

    skill_meta = load_skill_metadata(service_key, group="ticket")
    selected_skill_content = load_skill_context(skill_meta.get("location"), group="ticket")
    tools = _build_ticket_tools(service_key)
    if not tools:
        raise ValueError(f"ticket thinker node resolved no tools for service_key={service_key}")

    prompt = await build_ticket_thinker_system_prompt(
        user_context=state.get("user_context") or {},
        runtime_payload=TicketRuntimePayload(
            current_time=_utc_now_iso(),
            current_round=str(next_loop_count),
            max_rounds=str(_MAX_TICKET_LOOPS),
            execution_trace=(
                _serialize_trace(state.get("trace"))
                or "当前还没有已完成步骤，请根据用户请求开始处理。请基于后续执行步骤继续，不要重复已完成查询。"
            ),
        ),
        capability_context=PromptCapabilityContext(
            selected_skill_content=selected_skill_content,
            interaction_template=build_interaction_template_text(),
        ),
    )

    llm = get_remote_llm(role="ticket").bind_tools(tools, tool_choice="required")
    llm_messages: List[BaseMessage] = [
        SystemMessage(content=prompt),
        *messages,
    ]
    try:
        response, _ = await invoke_with_usage_logging(
            llm=llm,
            messages=llm_messages,
            node="ticket_thinker",
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            provider="deepseek",
            timeout_seconds=_THINKER_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.exception("[ticket_thinker] thread_id=%s thinker invoke failed: %s", state.get("thread_id"), exc)
        final_reason = "ticket_thinker_unavailable"
        if isinstance(exc, asyncio.TimeoutError) or "Timeout" in exc.__class__.__name__:
            final_reason = "ticket_thinker_timeout"
        final_reply = "当前服务暂时较忙，请稍后再试，或补充更明确的信息后我再继续帮您处理。"
        return {
            "ticket_loop_count": next_loop_count,
            "messages": [AIMessage(content=final_reply)],
            "trace": [
                *list(state.get("trace") or []),
                {
                    "type": "final",
                    "tool_name": "ticket_thinker",
                    "tool_result": {
                        "final_status": "failed",
                        "final_reason": final_reason,
                    },
                },
            ],
            "final_reply": final_reply,
            "final_status": "failed",
            "final_reason": final_reason,
        }
    if not isinstance(response, AIMessage):
        raise TypeError(f"ticket thinker node expected AIMessage, got {type(response).__name__}")
    if not response.tool_calls:
        raise ValueError("ticket thinker node expected tool_calls but model returned none")

    tool_message_updates: List[BaseMessage] = []
    if not tool_messages:
        tool_message_updates.extend(messages)
    tool_message_updates.append(response)

    return {
        "ticket_loop_count": next_loop_count,
        "tool_messages": tool_message_updates,
    }
