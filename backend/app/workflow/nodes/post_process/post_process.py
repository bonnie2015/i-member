from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from app.config.config import settings
from app.config.logging import get_logger
from app.agents.memory.redis_keys import compensation_day_key, compensation_week_key
from app.workflow.state import AgentState
from app.workflow.nodes.post_process.service_summary_builder import build_service_summary_from_state

logger = get_logger("post_process_node")

_SERVICE_END_CLEAR_FIELDS = {
    "intent": None,
    "reason": None,
    "is_direct_reply": False,
    "is_continuous": False,
    "ticket_scene": None,
    "current_goal": None,
    "slots": {},
    "steps": [],
    "current_step_index": 0,
    "replan_count": 0,
    "step_retry_count": 0,
    "next_action": None,
    "final_status": None,
    "final_reason": None,
    "collected_info": {},
    "qa_turn_count": 0,
    "service_entry_message": None,
}

_COMPENSATION_DAY_TTL = 86400
_COMPENSATION_WEEK_TTL = 7 * 86400
_COMPENSATION_WEEK_LIMIT = 50


async def _score_emotion(messages) -> float:
    try:
        from app.service.emotion_service import score_emotion

        return await score_emotion(messages)
    except Exception as e:
        logger.warning(f"[post_process] emotion scoring failed: {e}")
        return 0.5

def _memory_messages(state: AgentState):
    current_messages = list(state.get("messages") or [])
    if not state.get("is_continuous"):
        return current_messages

    user_context = state.get("user_context") or {}
    last_service = user_context.get("last_service") or {}
    if not last_service:
        logger.warning("[post_process] is_continuous=True but last_service is empty")
        return current_messages

    previous = []
    for msg in last_service.get("messages", []):
        content = str(msg.get("content", "")).strip()
        if not content:
            continue
        if msg.get("role") == "user":
            previous.append(HumanMessage(content=content))
        else:
            previous.append(AIMessage(content=content))
    return previous + current_messages


async def _save_memory(user_id: str, messages) -> None:
    from app.agents.memory.long_term_memory import extract_and_save_memories

    await extract_and_save_memories(user_id, messages)


async def _save_service_history(
    user_id: str,
    intent: str,
    messages,
    final_reply: Optional[str],
    is_continuous: bool,
    service_summary_structured: Optional[Dict[str, Any]] = None,
    state_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    from app.agents.memory.service_history import save_service

    await save_service(
        user_id=user_id,
        intent=intent,
        messages=messages,
        final_reply=final_reply,
        merge_with_last=is_continuous,
        service_summary_structured=service_summary_structured,
        state_snapshot=state_snapshot,
    )


def _build_state_snapshot(state: AgentState) -> Dict[str, Any]:
    keep_fields = [
        "intent",
        "is_continuous",
        "final_reply",
        "ticket_scene",
        "current_goal",
        "slots",
        "steps",
        "current_step_index",
        "collected_info",
        "next_action",
        "qa_turn_count",
        "service_entry_message",
    ]
    snapshot: Dict[str, Any] = {}
    for field in keep_fields:
        value = state.get(field)
        if value not in (None, "", [], {}):
            snapshot[field] = value
    return snapshot


async def _check_compensation_quota(user_id: str) -> bool:
    try:
        import datetime
        from redis.asyncio import Redis as AsyncRedis

        redis = AsyncRedis.from_url(settings.redis_url)
        today = datetime.date.today().isoformat()
        day_key = compensation_day_key(user_id, today)
        week_key = compensation_week_key(user_id)

        if await redis.exists(day_key):
            await redis.aclose()
            return False

        week_total_raw = await redis.get(week_key)
        week_total = float(week_total_raw) if week_total_raw else 0.0
        await redis.aclose()
        return week_total < _COMPENSATION_WEEK_LIMIT
    except Exception as e:
        logger.warning(f"[post_process] compensation quota check failed: {e}")
        return True


async def _record_compensation(user_id: str, amount: float) -> None:
    try:
        import datetime
        from redis.asyncio import Redis as AsyncRedis

        redis = AsyncRedis.from_url(settings.redis_url)
        today = datetime.date.today().isoformat()
        day_key = compensation_day_key(user_id, today)
        week_key = compensation_week_key(user_id)

        await redis.set(day_key, "1", ex=_COMPENSATION_DAY_TTL)
        current = float(await redis.get(week_key) or 0)
        await redis.set(week_key, str(current + amount), ex=_COMPENSATION_WEEK_TTL)
        await redis.aclose()
    except Exception as e:
        logger.warning(f"[post_process] compensation record failed: {e}")


async def post_process_node(state: AgentState) -> Dict[str, Any]:
    user_id = state.get("user_id", "unknown")
    intent = state.get("intent") or "unknown"
    messages = state.get("messages", [])
    user_context = state.get("user_context") or {}

    updates: Dict[str, Any] = {}
    service_summary_structured = build_service_summary_from_state(state)
    state_snapshot = _build_state_snapshot(state)

    try:
        await _save_memory(user_id, _memory_messages(state))
    except Exception as e:
        logger.warning(f"[post_process] memory save failed: {e}")

    try:
        await _save_service_history(
            user_id=user_id,
            intent=intent,
            messages=messages,
            final_reply=state.get("final_reply"),
            is_continuous=bool(state.get("is_continuous", False)),
            service_summary_structured=service_summary_structured,
            state_snapshot=state_snapshot,
        )
    except Exception as e:
        logger.warning(f"[post_process] service history save failed: {e}")

    emotion_score = await _score_emotion(messages)
    updates["emotion_score"] = emotion_score

    if emotion_score is not None and emotion_score <= 0.2:
        profile = user_context.get("profile", {})
        tags = profile.get("tags", [])
        is_high_value = "高价值" in tags or profile.get("total_orders", 0) >= 10

        if is_high_value and await _check_compensation_quota(user_id):
            try:
                from app.agents.tools.scrm_tools import call_scrm_api

                coupon_result = await call_scrm_api(
                    "issue_compensation_coupon",
                    {"reason": "服务情绪补偿"},
                )
                if coupon_result.get("issued"):
                    await _record_compensation(user_id, coupon_result.get("value", 0))
                    final_reply = state.get("final_reply", "")
                    coupon_desc = coupon_result.get("description", "已为您发放补偿优惠券")
                    updates["final_reply"] = f"{final_reply}\n\n{coupon_desc}"
            except Exception as e:
                logger.warning(f"[post_process] coupon issuance failed: {e}")

    updates.update(_SERVICE_END_CLEAR_FIELDS)
    remove_msgs = [RemoveMessage(id=m.id) for m in messages if hasattr(m, "id") and m.id]
    if remove_msgs:
        updates["messages"] = remove_msgs

    return updates
