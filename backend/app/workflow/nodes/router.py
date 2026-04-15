from datetime import datetime, timedelta, timezone

from app.config.logging import get_logger
from app.service.recognize_intent import recognize_router
from app.workflow.state import AgentState

logger = get_logger("router")

_FOLLOW_UP_WINDOW_MINUTES = 10


def _parse_iso_datetime(raw: str):
    try:
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def router_condition(state: AgentState) -> str:
    if state.get("is_direct_reply"):
        return "post_process"
    return state.get("intent", "qa")


async def router_node(state: AgentState):
    messages = state.get("messages") or []
    if not messages:
        logger.warning("Router messages empty, defaulting to QA")
        return {
            "is_direct_reply": False,
            "is_continuous": False,
            "intent": "qa",
            "reason": "消息为空，默认进入问答模块",
        }

    user_input = str(messages[-1].content or "")
    user_context = state.get("user_context") or {}
    last_service = user_context.get("last_service") or {}
    last_intent = str(last_service.get("intent", "")).strip()
    is_fresh_turn = len(messages) == 1

    eligible_last_service = {}
    if is_fresh_turn and last_intent in ("ticket", "qa", "recommend"):
        created_at = _parse_iso_datetime(last_service.get("created_at", ""))
        within_window = not created_at or (
            datetime.now(timezone.utc) - created_at <= timedelta(minutes=_FOLLOW_UP_WINDOW_MINUTES)
        )
        if within_window:
            eligible_last_service = last_service

    try:
        result = await recognize_router(
            messages=messages,
            last_service=eligible_last_service,
        )

        if not eligible_last_service or not is_fresh_turn:
            result.is_continuous = False
            result.is_simple_ack = False
            result.direct_reply = None

        if result.is_continuous and result.is_simple_ack:
            return {
                "is_direct_reply": True,
                "is_continuous": True,
                "intent": result.intent or last_intent or "qa",
                "reason": result.reason or "用户对最近一轮服务进行了简单收口回复，直接短答即可",
                "final_reply": result.direct_reply or "好的，这边先帮您记下了。您后面还有需要，随时叫我就行。",
            }

        return {
            "is_direct_reply": False,
            "is_continuous": bool(result.is_continuous),
            "intent": result.intent,
            "service_entry_message": user_input,
            "reason": result.reason,
        }
    except Exception as e:
        logger.warning(f"Router error: {e}")
        return {
            "is_direct_reply": False,
            "is_continuous": False,
            "intent": "qa",
            "reason": f"路由错误: {str(e)}",
        }
