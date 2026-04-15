from __future__ import annotations

import json
from typing import Any, Dict

from langchain_core.messages import HumanMessage

from app.agents.llm.llm_factory import get_local_llm, get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.config.logging import get_logger
from app.workflow.state import TicketNextAction, TicketState

logger = get_logger("ticket_finalizer")


def _trim_text(value: Any, limit: int = 300) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _latest_step(state: TicketState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    if not steps:
        return {}
    current_step_index = int(state.get("current_step_index", 0))
    if current_step_index <= 0:
        step = steps[0]
        return step if isinstance(step, dict) else {}
    if 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        return step if isinstance(step, dict) else {}
    step = steps[-1]
    return step if isinstance(step, dict) else {}


def _reply_context(state: TicketState) -> Dict[str, Any]:
    step = _latest_step(state)
    result = step.get("result")
    return {
        "final_status": state.get("final_status"),
        "final_reason": state.get("final_reason"),
        "current_goal": _trim_text(state.get("current_goal"), 200),
        "slots": state.get("slots") or {},
        "step": {
            "id": step.get("id"),
            "type": step.get("type"),
            "purpose": step.get("purpose"),
            "tool_name": step.get("tool_name"),
            "is_success": step.get("is_success"),
            "result": result if isinstance(result, dict) else _trim_text(result, 200),
        },
    }


def _build_prompt(state: TicketState) -> str:
    return load_prompt("ticket/finalize.txt").format(
        context=json.dumps(_reply_context(state), ensure_ascii=False, indent=2)
    )


async def _generate_with_local_llm(prompt: str) -> str:
    response = await get_local_llm(role="router").ainvoke([HumanMessage(content=prompt)])
    reply = _trim_text(getattr(response, "content", ""))
    if not reply:
        raise ValueError("empty local finalizer reply")
    return reply


async def _generate_with_remote_llm(prompt: str) -> str:
    response = await get_remote_llm(role="ticket").ainvoke([HumanMessage(content=prompt)])
    reply = _trim_text(getattr(response, "content", ""))
    if not reply:
        raise ValueError("empty remote finalizer reply")
    return reply


async def finalizer_node(state: TicketState) -> Dict[str, Any]:
    logger.info("[ticket_finalizer] context=%s", json.dumps(_reply_context(state), ensure_ascii=False))
    prompt = _build_prompt(state)
    try:
        final_reply = await _generate_with_local_llm(prompt)
    except Exception as exc:
        logger.warning("[ticket_finalizer] local llm failed: %s", exc)
        final_reply = await _generate_with_remote_llm(prompt)

    return {
        "next_action": TicketNextAction.FINALIZE,
        "final_reply": final_reply,
    }
