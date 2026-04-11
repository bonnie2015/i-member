from typing import List, Dict, Any
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel
from app.agents.llm.llm_factory import get_local_llm
from app.prompts.prompt_loader import load_prompt
from app.config.logging import get_logger

logger = get_logger("intent_recognition")
INTENT_PROMPT_FILE = "intent.txt"


class IntentOutput(BaseModel):
    """意图识别输出模型"""
    intent: str
    secondary_intents: List[str]
    reason: str


async def recognize_intent(user_input: str) -> IntentOutput:

    try:
        llm = get_local_llm(role="router")

        llm_with_structured_output = llm.with_structured_output(IntentOutput)

        prompt_content = load_prompt(INTENT_PROMPT_FILE)

        response = await llm_with_structured_output.ainvoke([
            SystemMessage(content=prompt_content),
            HumanMessage(content=user_input)
        ])

        logger.info(f"Router LLM response: {response}")

        return response

    except Exception as e:
        logger.error(f"Intent recognition failed: {e}", exc_info=True)
        return IntentOutput(
            intent="qa",
            secondary_intents=[],
            reason=f"意图识别失败: {str(e)}"
        )
