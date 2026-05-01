from app.config.logging import get_logger
from app.workflow.nodes.router.recognize_intent import recognize_router
from app.workflow.state import AgentState
from langchain_core.messages import AIMessage

logger = get_logger("router")
_ALLOWED_INTENTS = {"ticket", "qa", "recommend", "direct_reply"}
_DEFAULT_DIRECT_REPLY = "收到，有需要随时告诉我。"


def _build_direct_reply(user_input: str) -> str:
    text = str(user_input or "").strip()
    if not text:
        return _DEFAULT_DIRECT_REPLY
    if any(item in text for item in ["谢谢", "感谢", "辛苦"]):
        return "不客气，有需要随时告诉我。"
    if any(item in text for item in ["不用", "不需要", "先这样", "再说", "算了"]):
        return "好的，有需要再告诉我。"
    if any(item in text for item in ["好的", "好", "嗯", "嗯嗯", "收到", "ok", "OK"]):
        return "好的。"
    if any(item in text for item in ["哈哈", "嘿嘿", "hh", "HH"]):
        return "哈哈，好的。"
    return _DEFAULT_DIRECT_REPLY


def router_condition(state: AgentState) -> str:
    intent = str(state.get("intent") or "").strip()
    if intent == "direct_reply":
        return "direct_reply"
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
            thread_id=thread_id,
        )
        routed_intent = str(result.intent or "").strip()
        if routed_intent not in _ALLOWED_INTENTS:
            routed_intent = "qa"

        if routed_intent == "direct_reply":
            reply = str(result.reply or "").strip() or _build_direct_reply(user_input)
            return {
                "intent": routed_intent,
                "current_subgraph": None,
                "entry_message": user_input,
                "reason": result.reason,
                "final_reply": reply,
                "final_status": "success",
                "final_reason": "router_direct_reply",
                "messages": [AIMessage(content=reply)],
            }

        return {
            "intent": routed_intent,
            "current_subgraph": routed_intent,
            "entry_message": user_input,
            "reason": result.reason,
        }
    except Exception as e:
        logger.warning("[router] thread_id=%s error: %s", thread_id, e)
        return {
            "intent": "qa",
            "current_subgraph": "qa",
            "reason": f"路由错误: {str(e)}",
        }
