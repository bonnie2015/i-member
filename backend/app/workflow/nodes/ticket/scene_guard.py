from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.agents.skills.registry import (
    list_skills,
    load_skill_context,
    load_skill_metadata,
    load_skill_metadata_by_location,
)
from app.config.logging import get_logger
from app.workflow.state import TicketState

logger = get_logger("ticket_scene_guard")

_MAX_RECENT_MESSAGES = 10
_REMOTE_SCENE_GUARD_TIMEOUT_SECONDS = 45
_NON_WORD_RE = re.compile(r"[\s\W_]+", flags=re.UNICODE)


class SceneGuardOutput(BaseModel):
    decision: Literal["select_service", "clarify", "end_service"]
    service_key: Optional[str] = None
    skill_location: Optional[str] = None
    reason: str
    clarify_question: Optional[str] = None
    final_reply: Optional[str] = None
    final_status: Optional[str] = None
    final_reason: Optional[str] = None


def _recent_messages(messages: List[BaseMessage], limit: int = 10) -> List[BaseMessage]:
    return [message for message in messages[-limit:] if isinstance(message, BaseMessage)]


def _messages_payload(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    payload: List[Dict[str, str]] = []
    for message in messages:
        payload.append(
            {
                "role": getattr(message, "type", message.__class__.__name__),
                "content": str(getattr(message, "content", "") or "").strip(),
            }
        )
    return payload


def _normalize_text(text: str) -> str:
    return _NON_WORD_RE.sub("", str(text or "").lower())


def _bigrams(text: str) -> set[str]:
    normalized = _normalize_text(text)
    if len(normalized) < 2:
        return set()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def _latest_user_text(messages: List[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(getattr(message, "content", "") or "").strip()
    return ""


def _quick_select_service(state: TicketState, messages: List[BaseMessage]) -> Dict[str, Any] | None:
    user_text = _latest_user_text(messages)
    reason_text = str(state.get("reason") or "").strip()
    reference_text = " ".join(part for part in [user_text, reason_text] if part).strip()
    if not reference_text:
        return None
    user_bigrams = _bigrams(reference_text)
    if not user_bigrams:
        return None

    scored: List[Dict[str, Any]] = []
    for skill in list_skills():
        labels = [str(label).strip() for label in list(skill.get("clarify_labels") or []) if str(label).strip()]
        skill_text = " ".join(
            [
                str(skill.get("name") or ""),
                str(skill.get("description") or ""),
                *labels,
            ]
        )
        overlap = len(user_bigrams & _bigrams(skill_text))
        exact_hits = sum(1 for label in labels if label and label in reference_text)
        score = overlap + exact_hits * 5
        if score <= 0:
            continue
        scored.append(
            {
                "score": score,
                "exact_hits": exact_hits,
                "name": str(skill.get("name") or "").strip(),
                "location": str(skill.get("location") or "").strip(),
                "available_tools": list(skill.get("available_tools") or []),
            }
        )

    if not scored:
        return None

    scored.sort(key=lambda item: (item["score"], item["exact_hits"]), reverse=True)
    top = scored[0]
    second = scored[1] if len(scored) > 1 else None

    if top["score"] < 4 and top["exact_hits"] <= 0:
        return None
    if second and top["score"] - second["score"] < 2:
        return None
    if not top["name"] or not top["location"]:
        return None

    return {
        "service_key": top["name"],
        "skill_location": top["location"],
        "available_tools": top["available_tools"],
        "reason": "quick_skill_match",
    }


async def _recognize_service_once(
    state: TicketState,
    *,
    messages: List[BaseMessage],
) -> SceneGuardOutput:
    prompt = load_prompt("ticket/scene_guard.txt").format(
        skills_snapshot=load_skill_context(),
        recognized_intent=str(state.get("intent") or "").strip() or "unknown",
        router_reason=str(state.get("reason") or "").strip() or "none",
    )
    llm = get_remote_llm(role="ticket").with_structured_output(SceneGuardOutput)
    payload = json.dumps(
        {"messages": _messages_payload(_recent_messages(messages, limit=_MAX_RECENT_MESSAGES))},
        ensure_ascii=False,
    )
    return await asyncio.wait_for(
        llm.ainvoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(content=payload),
            ]
        ),
        timeout=_REMOTE_SCENE_GUARD_TIMEOUT_SECONDS,
    )


async def _recognize_service_with_context(
    state: TicketState,
    *,
    messages: List[BaseMessage],
) -> SceneGuardOutput:
    return await _recognize_service_once(state, messages=messages)


def _resolve_selected_service(response: SceneGuardOutput) -> Dict[str, Any]:
    service_key = str(response.service_key or "").strip() or None
    skill_location = str(response.skill_location or "").strip() or None

    selected_skill_meta: Dict[str, Any] = {}
    if skill_location:
        selected_skill_meta = load_skill_metadata_by_location(skill_location)
    if not selected_skill_meta and service_key:
        selected_skill_meta = load_skill_metadata(service_key)
        skill_location = str(selected_skill_meta.get("location") or "").strip() or skill_location

    if not service_key or not skill_location or not selected_skill_meta:
        raise ValueError(
            f"scene_guard returned unresolved service selection: service_key={service_key}, skill_location={skill_location}"
        )

    return {
        "service_key": service_key,
        "skill_location": skill_location,
        "available_tools": list(selected_skill_meta.get("available_tools") or []),
    }


async def scene_guard_node(state: TicketState) -> Dict[str, Any]:
    working_messages = list(state.get("messages") or [])
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    quick_selected = _quick_select_service(state, working_messages)
    if quick_selected:
        logger.info(
            "[ticket_scene_guard] thread_id=%s quick_select service_key=%s",
            thread_id,
            quick_selected["service_key"],
        )
        return {
            "service_key": quick_selected["service_key"],
            "skill_location": quick_selected["skill_location"],
            "current_goal": quick_selected["reason"],
            "available_tools": quick_selected["available_tools"],
            "final_status": None,
            "final_reason": None,
        }

    while True:
        try:
            response = await _recognize_service_with_context(state, messages=working_messages)
        except Exception as exc:
            logger.exception("[ticket_scene_guard] thread_id=%s remote guard failed: %s", thread_id, exc)
            return {
                "service_key": None,
                "skill_location": None,
                "current_goal": None,
                "available_tools": [],
                "final_status": "failed",
                "final_reason": "scene_guard_unavailable",
                "final_reply": "当前服务暂时较忙，请稍等片刻后再试；如果方便，也可以稍后重新发送您的问题。",
            }

        if response.decision == "clarify":
            reply = str(response.clarify_question or "").strip() or "请补充更具体的服务信息，方便我继续帮您处理。"
            logger.info("[ticket_scene_guard] thread_id=%s clarify=%s", thread_id, reply)
            resumed_user_message = str(interrupt({"reply": reply, "interaction": None}) or "").strip()
            working_messages = [
                *working_messages,
                AIMessage(content=reply),
                HumanMessage(content=resumed_user_message),
            ]
            continue

        if response.decision == "end_service":
            final_reply = (
                str(response.final_reply or "").strip()
                or "当前信息还不足以继续本次服务。您可以稍后补充更具体的信息后再次发起服务。"
            )
            final_reason = str(response.final_reason or "").strip() or "insufficient_information"
            final_status = str(response.final_status or "").strip() or "failed"
            logger.info(
                "[ticket_scene_guard] thread_id=%s end=%s",
                thread_id,
                json.dumps(
                    {
                        "decision": response.decision,
                        "reason": response.reason,
                        "final_status": final_status,
                        "final_reason": final_reason,
                    },
                    ensure_ascii=False,
                ),
            )
            return {
                "service_key": None,
                "skill_location": None,
                "current_goal": response.reason,
                "available_tools": [],
                "final_status": final_status,
                "final_reason": final_reason,
                "final_reply": final_reply,
            }

        if response.decision != "select_service":
            logger.error(
                "[ticket_scene_guard] thread_id=%s invalid structured output response=%s",
                thread_id,
                json.dumps(response.model_dump(), ensure_ascii=False),
            )
            raise ValueError(f"unexpected scene_guard decision: {response.decision}")

        selected = _resolve_selected_service(response)
        logger.info(
            "[ticket_scene_guard] thread_id=%s result=%s",
            thread_id,
            json.dumps(
                {
                    "service_key": selected["service_key"],
                    "skill_location": selected["skill_location"],
                    "reason": response.reason,
                    "available_tools": selected["available_tools"],
                },
                ensure_ascii=False,
            ),
        )
        return {
            "service_key": selected["service_key"],
            "skill_location": selected["skill_location"],
            "current_goal": response.reason,
            "available_tools": selected["available_tools"],
            "final_status": None,
            "final_reason": None,
        }
