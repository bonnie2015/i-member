from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt import create_react_agent
from langgraph.types import interrupt

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.agents.skills.registry import load_skill_context, load_skill_metadata_by_location
from app.agents.tools import get_scrm_tools, interrupt_tool
from app.agents.tools.scrm_tools import build_tool_summary_text
from app.config.logging import get_logger
from app.models.interaction import build_interaction_template_text
from app.workflow.state import TicketState

logger = get_logger("ticket_react_agent")

_MAX_RECENT_MESSAGES = 10
_MAX_REACT_RECURSION = 14
_REMOTE_AGENT_TIMEOUT_SECONDS = 70
_FINAL_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", flags=re.S | re.I)
_IDENTIFIER_PATTERNS = (
    re.compile(r"\b[A-Za-z]{1,5}\d{5,}\b"),
    re.compile(r"\b\d{6,}\b"),
)
_KEY_SLOT_FIELDS = {"ticket_id", "order_id", "order_item_id", "product_id", "sku_id"}
_APP_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _recent_messages(state: TicketState) -> List[BaseMessage]:
    messages = list(state.get("messages") or [])
    return [message for message in messages[-_MAX_RECENT_MESSAGES:] if isinstance(message, BaseMessage)]


def _current_attempt(state: TicketState) -> int:
    messages = list(state.get("messages") or [])
    human_turns = sum(1 for message in messages if isinstance(message, HumanMessage))
    return max(human_turns, 1)


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


def _latest_user_text(state: TicketState) -> str:
    for message in reversed(list(state.get("messages") or [])):
        if isinstance(message, HumanMessage):
            return _extract_text_content(getattr(message, "content", "")).strip()
    return ""


def _has_identifier(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _IDENTIFIER_PATTERNS)


def _has_key_slots(slots: Dict[str, Any]) -> bool:
    return any(str(slots.get(field) or "").strip() for field in _KEY_SLOT_FIELDS)


def _quick_clarify_reply(state: TicketState, slots: Dict[str, Any]) -> str | None:
    if str(state.get("final_status") or "").strip():
        return None
    if _current_attempt(state) > 1:
        return None
    if _has_key_slots(slots):
        return None

    user_text = _latest_user_text(state)
    if not user_text or _has_identifier(user_text):
        return None

    skill_location = str(state.get("skill_location") or "").strip()
    skill_meta = load_skill_metadata_by_location(skill_location) if skill_location else {}
    labels = [str(label).strip() for label in list(skill_meta.get("clarify_labels") or []) if str(label).strip()]
    labels = labels[:4]
    labels_text = " / ".join(labels) if labels else "当前服务范围内事项"
    return (
        f"为了不让您久等，我先快速确认一下：这次您更希望处理“{labels_text}”中的哪一项？"
        "如果方便，也可以直接回复已有编号（如订单号/工单号）和一句问题描述，我马上继续。"
    )


def _extract_final_result(messages: List[BaseMessage]) -> Dict[str, Any] | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        raw_text = _extract_text_content(getattr(message, "content", "")).strip()
        if not raw_text:
            continue
        json_text = _extract_json_object(raw_text)
        try:
            parsed = json.loads(json_text)
        except Exception:
            continue
        if isinstance(parsed, dict) and "is_finished" in parsed:
            return parsed
    return None


def _resolve_tools(available_tools: List[str]) -> List[BaseTool]:
    tool_registry: Dict[str, BaseTool] = {str(tool.name): tool for tool in get_scrm_tools()}
    selected: List[BaseTool] = []
    for tool_name in available_tools:
        tool = tool_registry.get(str(tool_name).strip())
        if tool is not None:
            selected.append(tool)
    return [*selected, interrupt_tool]


def _current_time_context() -> str:
    now = datetime.now(_APP_TIMEZONE)
    yesterday = now - timedelta(days=1)
    return (
        f"当前时间：{now.isoformat()}\n"
        f"今天：{now.strftime('%Y-%m-%d')}（Asia/Shanghai）\n"
        f"昨天：{yesterday.strftime('%Y-%m-%d')}"
    )


def _agent_prompt(state: TicketState) -> str:
    available_tools = list(state.get("available_tools") or [])
    skill_location = str(state.get("skill_location") or "").strip() or None
    selected_skill_content = load_skill_context(skill_location) if skill_location else ""
    return load_prompt("ticket/react.txt").format(
        user_id=state.get("user_id", "unknown"),
        service_key=str(state.get("service_key") or "unknown"),
        current_goal=str(state.get("current_goal") or "").strip() or "未提供",
        current_attempt="unknown",
        max_attempts="unknown",
        slots=json.dumps(state.get("slots") or {}, ensure_ascii=False, indent=2),
        selected_skill_content=selected_skill_content or "[no selected business skill]",
        tool_summary=build_tool_summary_text(available_tools),
        interaction_templates=build_interaction_template_text(),
        final_status=str(state.get("final_status") or "").strip() or "none",
        current_time_context=_current_time_context(),
    )


def _normalize_agent_result(agent_result: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(agent_result)
    normalized.setdefault("reply", "")
    normalized.setdefault("slots", {})
    normalized.setdefault("is_finished", False)
    normalized.setdefault("final_status", "failed")
    normalized.setdefault("final_reason", "react_agent_invalid_result")
    normalized.setdefault("result", {})

    if not isinstance(normalized.get("slots"), dict):
        normalized["slots"] = {}
    if not isinstance(normalized.get("result"), dict):
        normalized["result"] = {}

    if normalized.get("is_finished") is not True:
        normalized["is_finished"] = True
        normalized["final_status"] = "failed"
        normalized["final_reason"] = str(normalized.get("final_reason") or "react_agent_not_finished_without_interrupt")
        result = dict(normalized.get("result") or {})
        result.setdefault("unsolvable", True)
        result.setdefault("reason", "agent returned without finishing or interrupting")
        result.setdefault("reason_type", "system")
        normalized["result"] = result

    return normalized


def _diagnostic_tail(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    return [
        {
            "type": message.__class__.__name__,
            "content": _extract_text_content(getattr(message, "content", ""))[:500],
        }
        for message in messages[-3:]
    ]


async def _run_react_agent_once(state: TicketState) -> Dict[str, Any]:
    agent = create_react_agent(
        model=get_remote_llm(role="ticket"),
        tools=_resolve_tools(list(state.get("available_tools") or [])),
        prompt=_agent_prompt(state),
    )
    agent_result = await asyncio.wait_for(
        agent.ainvoke(
            {
                "messages": [
                    *_recent_messages(state),
                    HumanMessage(content="请继续当前 ticket 服务。若需要用户补充、选择或确认，请调用 interrupt；若当前服务完成，请只输出一个最终 JSON。"),
                ]
            },
            {"recursion_limit": _MAX_REACT_RECURSION},
        ),
        timeout=_REMOTE_AGENT_TIMEOUT_SECONDS,
    )
    messages = list((agent_result or {}).get("messages") or [])
    final_result = _extract_final_result(messages)
    if final_result is None:
        thread_id = str(state.get("thread_id") or "").strip() or "unknown"
        logger.error(
            "[ticket_react_agent] thread_id=%s missing final JSON source=%s service_key=%s tail=%s",
            thread_id,
            "remote",
            state.get("service_key") or "unknown",
            json.dumps(_diagnostic_tail(messages), ensure_ascii=False),
        )
        raise ValueError("ticket react agent did not return final JSON result")
    return _normalize_agent_result(final_result)


async def _run_react_agent(state: TicketState) -> Dict[str, Any]:
    return await _run_react_agent_once(state)


async def ticket_react_agent_node(state: TicketState) -> Dict[str, Any]:
    slots = dict(state.get("slots") or {})
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    logger.info(
        "[ticket_react_agent] thread_id=%s start service_key=%s available_tools=%s final_status=%s",
        thread_id,
        state.get("service_key") or "unknown",
        state.get("available_tools") or [],
        state.get("final_status") or None,
    )
    quick_reply = _quick_clarify_reply(state, slots)
    if quick_reply:
        logger.info(
            "[ticket_react_agent] thread_id=%s quick_clarify service_key=%s",
            thread_id,
            state.get("service_key") or "unknown",
        )
        interrupt({"reply": quick_reply, "interaction": None})

    try:
        agent_result = await _run_react_agent(state)
    except GraphInterrupt:
        raise
    except Exception as exc:
        logger.exception("[ticket_react_agent] thread_id=%s remote agent failed: %s", thread_id, exc)
        final_reason = "react_agent_unavailable"
        final_status = "failed"
        return {
            "slots": slots,
            "final_status": final_status,
            "final_reason": final_reason,
            "final_reply": "当前服务暂时较忙，请稍等片刻后再试；如果方便，也可以稍后重新发送您的问题。",
        }

    merged_slots = {**slots, **dict(agent_result.get("slots") or {})}
    final_reply = str(agent_result.get("reply") or "").strip()
    final_status = str(agent_result.get("final_status") or "failed").strip() or "failed"
    final_reason = str(agent_result.get("final_reason") or "").strip() or "react_agent_completed"

    logger.info(
        "[ticket_react_agent] thread_id=%s result=%s",
        thread_id,
        json.dumps(
            {
                "service_key": state.get("service_key") or "unknown",
                "final_status": final_status,
                "final_reason": final_reason,
                "slots": merged_slots,
            },
            ensure_ascii=False,
        ),
    )

    return {
        "slots": merged_slots,
        "final_status": final_status,
        "final_reason": final_reason,
        "final_reply": final_reply,
    }
