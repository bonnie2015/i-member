from typing import List, Optional
from app.workflow.state import AgentState
from app.service.intent_recognition import recognize_intent
from app.config.logging import get_logger

logger = get_logger("router")


def router_condition(state: AgentState) -> str:
    intent = state.get("intent", "qa")
    return intent


async def router_node(state: AgentState):

    if not state.get("messages"):
        logger.warning("Router received empty messages, defaulting to QA intent")
        return {
            "intent": "qa",
            "intent_queue": [],
            "reason": "消息为空，默认进入问答模块"
        }

    user_input = state["messages"][-1].content

    try:
        result = await recognize_intent(user_input)
        detected_intent = result.intent
        secondary_intents = result.secondary_intents
        reason = result.reason

        # 次意图最多保留2个（加主意图共3个）
        intent_queue = secondary_intents[:2]

        return {
            "intent": detected_intent,
            "intent_queue": intent_queue,
            "reason": reason
        }

    except Exception as e:
        logger.warning(f"Router error: {e}")
        return {
            "intent": "qa",
            "intent_queue": [],
            "reason": f"路由错误: {str(e)}"
        }
