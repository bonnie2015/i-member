from app.config.logging import get_logger
from app.workflow.nodes.router.recognize_intent import recognize_router
from app.workflow.state import AgentState

logger = get_logger("router")
_ALLOWED_INTENTS = {"ticket", "qa", "recommend"}


def router_condition(state: AgentState) -> str:
    intent = str(state.get("intent") or "").strip()
    return intent if intent in _ALLOWED_INTENTS else "qa"


async def router_node(state: AgentState):
    messages = state.get("messages") or []
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    if not messages:
        logger.warning("[router] thread_id=%s messages empty, defaulting to QA", thread_id)
        return {
            "intent": "qa",
            "reason": "消息为空，默认进入问答模块",
        }

    user_input = str(getattr(messages[-1], "content", "") or "").strip()
    try:
        result = await recognize_router(
            messages=messages,
            user_context=state.get("user_context") or {},
            thread_id=thread_id,
        )
        routed_intent = str(result.intent or "").strip()
        if routed_intent not in _ALLOWED_INTENTS:
            routed_intent = "qa"

        return {
            "intent": routed_intent,
            "entry_message": user_input,
            "reason": result.reason,
        }
    except Exception as e:
        logger.warning("[router] thread_id=%s error: %s", thread_id, e)
        return {
            "intent": "qa",
            "reason": f"路由错误: {str(e)}",
        }
