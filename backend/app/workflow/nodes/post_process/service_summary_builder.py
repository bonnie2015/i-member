from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List

from app.agents.memory.service_summary import build_service_summary


def _trim_text(text: Any, limit: int = 160) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _slots_to_dict(raw_slots: Any) -> Dict[str, Any]:
    if isinstance(raw_slots, dict):
        return dict(raw_slots)

    slots: Dict[str, Any] = {}
    if isinstance(raw_slots, list):
        for item in raw_slots:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            slots[key] = item.get("value")
    return slots


def _summarize_messages(messages: List[Any]) -> str:
    user_lines = []
    for msg in messages:
        role = "user" if msg.__class__.__name__ == "HumanMessage" else "assistant"
        content = str(getattr(msg, "content", "")).strip()
        if role == "user" and content:
            user_lines.append(content)
    if not user_lines:
        return ""
    if len(user_lines) == 1:
        return _trim_text(user_lines[0], 120)
    return _trim_text(f"用户先提到：{user_lines[0]}；随后补充：{user_lines[-1]}", 160)


def _extract_point_info(state: Mapping[str, Any]) -> Dict[str, Any]:
    point_info: Dict[str, Any] = {}
    intent = state.get("intent")

    if intent == "ticket":
        slots = _slots_to_dict(state.get("slots") or {})
        service_key = state.get("service_key")
        point_info.update({
            "service_key": service_key,
            "order_id": slots.get("order_id") or slots.get("biz_id"),
            "ticket_id": slots.get("ticket_id"),
            "ticket_type": slots.get("ticket_type") or service_key,
            "confirmed_slots": sorted([key for key, value in slots.items() if value not in (None, "", [], {})]),
        })
    elif intent == "qa":
        point_info["qa_turn_count"] = state.get("qa_turn_count", 0)

    compact = {}
    for key, value in point_info.items():
        if value not in (None, "", [], {}, 0):
            compact[key] = value
    return compact


def build_service_summary_from_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    intent = str(state.get("intent") or "unknown")
    goal = str(state.get("current_goal") or state.get("service_entry_message") or "").strip()

    final_reply = str(state.get("final_reply") or "").strip()
    if final_reply:
        result = _trim_text(final_reply, 160)
    else:
        result = "当前服务已结束"

    return build_service_summary(
        intent=intent,
        goal=goal,
        result=result,
        point_info=_extract_point_info(state),
        message_summary=_summarize_messages(list(state.get("messages") or [])),
    )
