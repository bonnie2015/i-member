from __future__ import annotations

import json
import re
from typing import Dict, List, Literal

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_local_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.agents.skills.registry import load_skills_snapshot
from app.agents.tools import interrupt_tool, read_file
from app.config.logging import get_logger
from app.workflow.state import TicketNextAction, TicketState

logger = get_logger("ticket_scene_guard")
_MAX_REACT_RECURSION = 10
_MAX_RECENT_MESSAGES = 10
_JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", flags=re.S | re.I)


class SceneGuardOutput(BaseModel):
    ticket_scene: Literal["refund", "change", "quality", "complain", "equity", "others"]
    confidence: Literal["high", "low"] = "high"
    reason: str


def _recent_messages(messages: List[BaseMessage], limit: int = 10) -> List[BaseMessage]:
    return [message for message in messages[-limit:] if isinstance(message, BaseMessage)]


def _content_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _extract_final_result(messages: List[BaseMessage]) -> Dict[str, str] | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        raw_text = _content_text(message)
        if not raw_text:
            continue
        fenced = _JSON_BLOCK_RE.search(raw_text)
        json_text = fenced.group(1) if fenced else raw_text[raw_text.find("{") : raw_text.rfind("}") + 1]
        try:
            parsed = json.loads(json_text)
        except Exception:
            continue
        if isinstance(parsed, dict) and "ticket_scene" in parsed:
            return parsed
    return None


def _extract_selected_skill_content(messages: List[BaseMessage]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        content = _content_text(message)
        if content:
            tool_name = str(getattr(message, "name", "") or "").strip()
            if tool_name == "interrupt":
                continue
            if content.startswith("{"):
                try:
                    parsed = json.loads(content)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict) and ("request" in parsed or "answer" in parsed):
                    continue
            return content
    return None


async def _run_scene_guard_agent(state: TicketState) -> tuple[SceneGuardOutput, str | None]:
    prompt = load_prompt("ticket/scene_guard.txt").format(
        skills_snapshot=load_skills_snapshot(),
        recognized_intent=str(state.get("intent") or "").strip() or "unknown",
        router_reason=str(state.get("reason") or "").strip() or "none",
    )
    agent = create_react_agent(
        model=get_local_llm(role="scene_guard"),
        tools=[read_file, interrupt_tool],
        prompt=prompt,
    )
    agent_result = await agent.ainvoke(
        {
            "messages": _recent_messages(list(state.get("messages") or []), limit=_MAX_RECENT_MESSAGES)
        },
        {"recursion_limit": _MAX_REACT_RECURSION},
    )
    messages = list((agent_result or {}).get("messages") or [])
    final_result = _extract_final_result(messages)
    if final_result is None:
        raise ValueError("scene_guard agent did not return final JSON result")
    return (
        SceneGuardOutput.model_validate(final_result),
        _extract_selected_skill_content(messages),
    )


async def scene_guard_node(state: TicketState) -> Dict[str, Any]:
    response, selected_skill_content = await _run_scene_guard_agent(state)
    if response.ticket_scene != "others" and not selected_skill_content:
        raise ValueError(f"scene_guard resolved {response.ticket_scene} without skill content")

    logger.info(
        "[ticket_scene_guard] result=%s",
        json.dumps(
            {
                "ticket_scene": response.ticket_scene,
                "reason": response.reason,
            },
            ensure_ascii=False,
        ),
    )

    if response.ticket_scene == "others":
        return {
            "ticket_scene": "others",
            "current_goal": response.reason,
            "selected_skill_content": None,
            "next_action": TicketNextAction.END,
            "final_status": "failed",
            "final_reason": "out_of_scope",
        }

    return {
        "ticket_scene": response.ticket_scene,
        "current_goal": response.reason,
        "selected_skill_content": selected_skill_content or None,
        "next_action": TicketNextAction.PLAN,
        "final_status": None,
        "final_reason": None,
    }
