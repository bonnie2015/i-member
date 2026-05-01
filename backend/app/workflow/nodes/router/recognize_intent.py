import json
from typing import Dict, Literal, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_local_llm
from app.agents.prompts.prompt_builder import (
    PromptCapabilityContext,
    build_router_system_prompt,
)
from app.agents.skills.registry import load_skill_context
from app.config.logging import get_logger

logger = get_logger("recognize_intent")


class RouterOutput(BaseModel):
    intent: Literal["ticket", "qa", "recommend", "direct_reply"]
    reason: str
    reply: str = ""


def _build_router_messages_payload(messages: Sequence[BaseMessage]) -> str:
    return json.dumps(
        {
            "messages": [
                {
                    "role": getattr(message, "type", message.__class__.__name__),
                    "content": str(getattr(message, "content", "") or ""),
                }
                for message in messages
            ],
        },
        ensure_ascii=False,
    )


async def recognize_router(
    messages: Sequence[BaseMessage],
    thread_id: Optional[str] = None,
) -> RouterOutput:
    try:
        llm = get_local_llm(role="router").with_structured_output(RouterOutput)
        prompt = await build_router_system_prompt(
            capability_context=PromptCapabilityContext(
                ticket_skills_snapshot=load_skill_context(group="ticket"),
            ),
        )
        llm_messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=_build_router_messages_payload(messages)),
        ]
        response: RouterOutput = await llm.ainvoke(llm_messages)

        logger.info("[recognize_intent] thread_id=%s response=%s", thread_id or "unknown", response)
        return response
    except Exception as e:
        logger.error("[recognize_intent] thread_id=%s failed: %s", thread_id or "unknown", e, exc_info=True)
        return RouterOutput(
            intent="qa",
            reason=f"路由识别失败: {e}",
        )
