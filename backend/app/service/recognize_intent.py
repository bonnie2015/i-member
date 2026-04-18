import json
from typing import Dict, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_local_llm
from app.agents.prompts.prompt_loader import load_prompt
from app.config.logging import get_logger

logger = get_logger("recognize_intent")
ROUTER_PROMPT_FILE = "router/router.txt"


class RouterOutput(BaseModel):
    intent: str
    is_continuous: bool
    is_simple_ack: bool
    reason: str
    direct_reply: Optional[str] = None


async def recognize_router(
    messages: Sequence[BaseMessage],
    last_service: Optional[Dict] = None,
) -> RouterOutput:
    try:
        llm = get_local_llm(role="router")
        llm_with_structured_output = llm.with_structured_output(RouterOutput)
        payload = json.dumps(
            {
                "messages": [
                    {
                        "role": getattr(message, "type", message.__class__.__name__),
                        "content": str(getattr(message, "content", "") or ""),
                    }
                    for message in messages
                ],
                "last_service": last_service or {},
            },
            ensure_ascii=False,
        )
        response: RouterOutput = await llm_with_structured_output.ainvoke(
            [
                SystemMessage(content=load_prompt(ROUTER_PROMPT_FILE)),
                HumanMessage(content=payload),
            ]
        )

        if response.intent not in ("ticket", "qa", "recommend"):
            response.intent = "qa"
        if response.is_simple_ack and not response.direct_reply:
            response.direct_reply = "好的，有需要随时告诉我。"

        logger.info("Router recognize response: %s", response)
        return response
    except Exception as e:
        logger.error("Router recognition failed: %s", e, exc_info=True)
        return RouterOutput(
            intent="qa",
            is_continuous=False,
            is_simple_ack=False,
            reason=f"路由识别失败: {e}",
            direct_reply=None,
        )
