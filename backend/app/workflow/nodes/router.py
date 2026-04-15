from datetime import datetime, timedelta, timezone

from app.config.logging import get_logger
from app.service.recognize_intent import recognize_intent, recognize_router_guard
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

    if last_intent in ("ticket", "qa", "recommend"):
        created_at = _parse_iso_datetime(last_service.get("created_at", ""))
        within_window = not created_at or (
            datetime.now(timezone.utc) - created_at <= timedelta(minutes=_FOLLOW_UP_WINDOW_MINUTES)
        )
        if within_window:
            guard = await recognize_router_guard(user_input=user_input, last_service=last_service)
            is_continuous = bool(guard.is_continuous)

            if guard.is_continuous:
                if guard.is_simple_ack:
                    return {
                        "is_direct_reply": True,
                        "is_continuous": is_continuous,
                        "intent": guard.intent or last_intent or "qa",
                        "reason": guard.reason or "用户对最近一轮服务进行了简单收口回复，直接短答即可",
                        "final_reply": guard.direct_reply or "好的，这边先帮您记下了。您后面还有需要，随时叫我就行。",
                    }

                return {
                    "is_direct_reply": False,
                    "is_continuous": is_continuous,
                    "intent": guard.intent or last_intent,
                    "service_entry_message": user_input,
                    "reason": guard.reason or "用户当前发言与最近一轮服务存在连续性，优先沿用上一轮服务意图",
                }

    try:
        result = await recognize_intent(user_input)
        return {
            "is_direct_reply": False,
            "is_continuous": False,
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
