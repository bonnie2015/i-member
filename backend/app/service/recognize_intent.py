import json
from typing import Dict, Optional
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from pydantic import BaseModel
from app.agents.llm.llm_factory import get_local_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.config.logging import get_logger

logger = get_logger("recognize_intent")
INTENT_PROMPT_FILE = "router/intent.txt"
ROUTER_GUARD_PROMPT_FILE = "router/router_guard.txt"


class IntentOutput(BaseModel):
    """意图识别输出模型"""
    intent: str
    reason: str


class RouterGuardOutput(BaseModel):
    intent: str
    is_continuous: bool
    is_simple_ack: bool
    reason: str
    direct_reply: Optional[str] = None


async def recognize_intent(
    user_input: str,
    messages: Optional[list[BaseMessage]] = None
) -> IntentOutput:

    try:
        llm = get_local_llm(role="router")

        llm_with_structured_output = llm.with_structured_output(IntentOutput)

        llm_messages = [SystemMessage(content=load_prompt(INTENT_PROMPT_FILE))]

        if messages:
            llm_messages.extend(list(messages))
        else:
            llm_messages.append(HumanMessage(content=user_input))

        response = await llm_with_structured_output.ainvoke(llm_messages)

        logger.info(f"Intent recognize response: {response}")

        if not response.intent:
            logger.info(f"Intent unrecognized, defaulting to 'qa'")
            response.intent = "qa"
        return response

    except Exception as e:
        logger.error(f"Intent recognition failed: {e}", exc_info=True)
        return IntentOutput(
            intent="qa",
            reason=f"意图识别失败: {str(e)}"
        )


async def recognize_router_guard(
    user_input: str,
    last_service: Dict,
) -> RouterGuardOutput:
    try:
        llm = get_local_llm(role="router")
        llm_with_structured_output = llm.with_structured_output(RouterGuardOutput)
        prompt = load_prompt(ROUTER_GUARD_PROMPT_FILE)
        user_payload = json.dumps(
            {
                "user_input": user_input,
                "last_service": last_service,
            },
            ensure_ascii=False,
        )
        response: RouterGuardOutput = await llm_with_structured_output.ainvoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(content=user_payload),
            ]
        )

        if response.intent not in ("ticket", "qa", "recommend"):
            response.intent = "qa"
        if response.is_simple_ack and not response.direct_reply:
            response.direct_reply = "好的，有需要随时告诉我。"
        return response
    except Exception as e:
        logger.warning(f"Router guard recognition failed: {e}")
        return RouterGuardOutput(
            intent="qa",
            is_continuous=False,
            is_simple_ack=False,
            reason=f"连续性识别失败: {e}",
            direct_reply=None,
        )
