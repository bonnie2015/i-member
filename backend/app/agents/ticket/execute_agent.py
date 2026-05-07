from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import with_usage_logging
from app.prompts.prompt_builder import TicketExecuteRuntimePayload, build_ticket_execute_system_prompt
from app.tools import ask_user_tool, finish_step_tool, get_scrm_tools, onitsuka_get_product_detail
from app.tools.memory_tools import get_memory_tools
from app.tools.business.execution_context import ticket_interaction_source_context

logger = get_logger("ticket_execute_agent")

_MAX_TOOL_CALLS = 5
_MAX_GRAPH_STEPS = 20
_EXECUTE_TIMEOUT_SECONDS = 120


def _tool_registry() -> Dict[str, Any]:
    return {
        str(tool.name): tool
        for tool in [*get_scrm_tools(), onitsuka_get_product_detail, *get_memory_tools()]
    }


def _resolve_step_tools(step: Dict[str, Any]) -> list:
    registry = _tool_registry()
    selected = []
    raw_tool_names = step.get("available_tools") or []
    if isinstance(raw_tool_names, str):
        raw_tool_names = [raw_tool_names]
    for item in (raw_tool_names or []):
        tool_name = str(item or "").strip()
        tool = registry.get(tool_name)
        if tool:
            selected.append(tool)
    return [*selected, ask_user_tool, finish_step_tool]


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


def _extract_finish_result(messages: List[BaseMessage]) -> Dict[str, Any]:
    """从 React agent 消息中提取 finish_step 工具调用参数作为结构化结果。"""
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for tc in message.tool_calls or []:
            if tc.get("name") == "finish_step":
                args = dict(tc.get("args") or {})
                return {
                    "step_status": str(args.get("step_status") or "pending").strip(),
                    "slots": dict(args.get("slots") or {}),
                    "reply": str(args.get("reply") or "").strip(),
                    "reason": str(args.get("reason") or "").strip(),
                }
    return {}


def _build_try_process(messages: List[BaseMessage]) -> list:
    """从 React agent 消息中提取工具调用和结果，记录到 try_process。"""
    entries = []
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in msg.tool_calls or []:
                entries.append({
                    "tool": tc.get("name", ""),
                    "args": tc.get("args", {}),
                })
        elif isinstance(msg, ToolMessage):
            entries.append({
                "tool": getattr(msg, "name", "") or "",
                "result": _message_text(getattr(msg, "content", "")),
            })
    return entries


class TicketExecuteAgent(BaseAgent):

    def __init__(self):
        config = AgentConfig(
            name="ticket_execute",
            role="ticket",
            timeout_seconds=_EXECUTE_TIMEOUT_SECONDS,
            max_recursion=_MAX_GRAPH_STEPS,
            max_tool_calls=_MAX_TOOL_CALLS,
            fallback_reply="我暂时无法完成这一步，请稍后再试。",
        )
        super().__init__(config)

    async def _execute(self, input: AgentInput) -> AgentOutput:
        step: Dict[str, Any] = dict(input.extra.get("step") or {})
        existing_slots: Dict[str, Any] = dict(input.extra.get("slots") or {})
        expected_slots: List[Dict[str, str]] = list(input.extra.get("expected_slots") or [])

        tools = _resolve_step_tools(step)
        prompt = await build_ticket_execute_system_prompt(
            runtime_context=TicketExecuteRuntimePayload(
                step=step,
                slots=existing_slots,
                expected_slots=expected_slots,
            ),
        )

        def _model_fn(state: Dict[str, Any], runtime: Any) -> Any:
            msgs = list(state.get("messages") or [])
            count = sum(1 for m in msgs if isinstance(m, ToolMessage))
            if count >= _MAX_TOOL_CALLS:
                available_tools = [finish_step_tool, ask_user_tool]
            else:
                available_tools = tools
            model = get_llm("ticket")
            model = model.bind_tools(available_tools, tool_choice="required")
            return with_usage_logging(
                model,
                node="ticket_executor",
                thread_id=input.thread_id,
                user_id=input.user_id,
                provider="deepseek",
            )

        def _pre_model_hook(state: Dict[str, Any]) -> Dict[str, Any]:
            msgs = list(state.get("messages") or [])
            count = sum(1 for m in msgs if isinstance(m, ToolMessage))
            if count < _MAX_TOOL_CALLS:
                return {}
            from langchain_core.messages import SystemMessage
            return {
                "llm_input_messages": [
                    *msgs,
                    SystemMessage(content=(
                        f"已达到工具调用上限（{_MAX_TOOL_CALLS} 次）。必须调用 finish_step 结束本步骤，"
                        "step_status 设为 pending。"
                    )),
                ]
            }

        executor = create_react_agent(
            model=_model_fn,
            tools=tools,
            prompt=prompt,
            pre_model_hook=_pre_model_hook,
        )

        logger.info(
            "[ticket_execute_agent] thread_id=%s step_goal=%s tools=%s",
            input.thread_id,
            str(step.get("goal") or "")[:60],
            [str(t.name) for t in tools],
        )

        try:
            with ticket_interaction_source_context():
                agent_result = await asyncio.wait_for(
                    executor.ainvoke(
                        {"messages": input.messages[-4:] if input.messages else [HumanMessage(content=input.user_query)]},
                        {"recursion_limit": _MAX_GRAPH_STEPS},
                    ),
                    timeout=_EXECUTE_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            logger.warning("[ticket_execute_agent] thread_id=%s timeout", input.thread_id)
            return AgentOutput(
                reply="当前步骤执行超时，请稍后再试。",
                status=AgentStatus.TIMEOUT,
                data={"step_status": "failed", "slots": {}},
            )

        result_messages = list((agent_result or {}).get("messages") or [])
        result = _extract_finish_result(result_messages)

        step_status = str(result.get("step_status") or "pending").strip()
        new_slots = dict(result.get("slots") or {})
        reply = str(result.get("reply") or "").strip()
        reason = str(result.get("reason") or "").strip()

        # 检查是否有 ask_user 的 interrupt 未处理
        from langgraph.errors import GraphInterrupt
        for msg in result_messages:
            if isinstance(msg, ToolMessage) and "interrupt" in str(getattr(msg, "content", "") or "").lower():
                logger.info("[ticket_execute_agent] thread_id=%s interrupt pending", input.thread_id)
                break

        logger.info(
            "[ticket_execute_agent] thread_id=%s step_status=%s new_slots=%s reply_len=%s reason=%s",
            input.thread_id,
            step_status,
            len(new_slots),
            len(reply),
            reason or "none",
        )

        try_process = _build_try_process(result_messages)

        return AgentOutput(
            reply=reply,
            status=AgentStatus.SUCCESS,
            data={
                "step_status": step_status,
                "slots": new_slots,
                "reason": reason,
                "try_process": try_process,
            },
        )


ticket_execute_agent = TicketExecuteAgent()
