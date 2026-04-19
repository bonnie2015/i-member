from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool

from app.config.logging import get_logger
from app.workflow.nodes.ticket.thinker import _build_ticket_tools
from app.workflow.state import AgentState

logger = get_logger("ticket_executor")


def _serialize_tool_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)


def _trim_text(value: Any, limit: int = 80) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _tool_map(service_key: str) -> Dict[str, BaseTool]:
    return {tool.name: tool for tool in _build_ticket_tools(service_key)}


def _append_trace(existing: Any, item: Any) -> List[Any]:
    trace = list(existing or [])
    if item is not None:
        trace.append(item)
    return trace


def _merge_slots(existing: Any, incoming: Any) -> Dict[str, Any]:
    base = dict(existing or {}) if isinstance(existing, dict) else {}
    if isinstance(incoming, dict):
        base.update(incoming)
    return base


async def executor_node(state: AgentState) -> Dict[str, Any]:
    service_key = str(state.get("service_key") or "").strip()
    if not service_key:
        raise ValueError("ticket executor node requires service_key")

    tool_runtime_messages = list(state.get("tool_messages") or [])
    if not tool_runtime_messages:
        raise ValueError("ticket executor node requires tool_messages")

    last_message = tool_runtime_messages[-1]
    if not isinstance(last_message, AIMessage):
        raise TypeError(f"ticket executor node expected last message to be AIMessage, got {type(last_message).__name__}")

    tool_calls = list(last_message.tool_calls or [])
    if not tool_calls:
        raise ValueError("ticket executor node expected tool_calls on last AIMessage")

    tools = _tool_map(service_key)
    tool_messages: List[ToolMessage] = []
    trace = list(state.get("trace") or [])

    for tool_call in tool_calls:
        tool_name = str(tool_call.get("name") or "").strip()
        tool_args = tool_call.get("args") or {}
        tool_call_id = str(tool_call.get("id") or "").strip()
        if not tool_name or not tool_call_id:
            raise ValueError(f"invalid tool_call payload: {tool_call}")

        if tool_name == "submit_final_answer":
            result = dict(tool_args) if isinstance(tool_args, dict) else {}
            reply = str(result.get("reply") or "").strip()
            final_status = str(result.get("final_status") or "").strip() or "success"
            final_reason = str(result.get("final_reason") or "").strip() or "ticket_completed"
            merged_slots = _merge_slots(state.get("slots"), result.get("slots"))
            tool_messages.append(
                ToolMessage(
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    content=_serialize_tool_result(result),
                )
            )
            trace = _append_trace(
                trace,
                {
                    "tool_name": tool_name,
                    "tool_result": result,
                },
            )
            trace = _append_trace(
                trace,
                {
                    "type": "final",
                    "tool_name": tool_name,
                    "tool_result": {
                        "final_status": final_status,
                        "final_reason": final_reason,
                    },
                },
            )
            updates: Dict[str, Any] = {
                "tool_messages": tool_messages,
                "messages": [AIMessage(content=reply)],
                "trace": trace,
                "slots": merged_slots,
                "final_reply": reply,
                "final_status": final_status,
                "final_reason": final_reason,
            }
            return updates

        tool = tools.get(tool_name)
        if tool is None:
            raise ValueError(f"ticket executor node unresolved tool: {tool_name}")

        result = await tool.ainvoke(tool_args)
        tool_messages.append(
            ToolMessage(
                tool_call_id=tool_call_id,
                name=tool_name,
                content=_serialize_tool_result(result),
            )
        )

        if tool_name == "ask_user":
            trace = _append_trace(
                trace,
                {
                    "tool_name": tool_name,
                    "tool_result": result,
                },
            )
            return {
                "tool_messages": tool_messages,
                "trace": trace,
            }

        trace = _append_trace(
            trace,
            {
                "tool_name": tool_name,
                "tool_result": result,
            },
        )

    return {
        "tool_messages": tool_messages,
        "trace": trace,
    }
