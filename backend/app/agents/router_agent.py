"""
RouterAgent — 意图分类。

单次 LLM 调用，将用户消息路由到 ticket / qa / recommend。
"""

from __future__ import annotations

import json
from typing import Dict, Literal, Optional, Sequence

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import invoke_with_usage_logging
from app.prompts.prompt_builder import (
    PromptCapabilityContext,
    build_router_system_prompt,
)
from app.skills.registry import load_skill_context

logger = get_logger("router_agent")

_ROUTER_TIMEOUT_SECONDS = 15


class RouterOutput(BaseModel):
    intent: Literal["ticket", "qa", "recommend"]
    reason: str


def _serialize_messages(messages: Sequence[BaseMessage]) -> str:
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


class RouterAgent(BaseAgent):
    def __init__(self):
        config = AgentConfig(
            name="router",
            role="router",
            timeout_seconds=_ROUTER_TIMEOUT_SECONDS,
            max_recursion=1,
            max_tool_calls=0,
            fallback_reply="",
        )
        super().__init__(config)

    async def _execute(self, input: AgentInput) -> AgentOutput:
        llm = get_llm("router").with_structured_output(RouterOutput)
        prompt = await build_router_system_prompt(
            capability_context=PromptCapabilityContext(
                ticket_skills_snapshot=load_skill_context(group="ticket"),
            ),
        )

        if not input.messages:
            return AgentOutput(
                reply="",
                status=AgentStatus.SUCCESS,
                data={"intent": "qa", "reason": "消息为空，默认进入问答模块"},
            )

        # 只取最近两轮（4条消息），避免上下文过长
        recent = input.messages[-4:] if len(input.messages) > 4 else input.messages

        llm_messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=_serialize_messages(recent)),
        ]

        response: RouterOutput = (
            await invoke_with_usage_logging(
                llm=llm,
                messages=llm_messages,
                node="router",
                thread_id=input.thread_id,
                user_id=input.user_id,
                provider="deepseek",
            )
        )[0]

        logger.info("[router_agent] thread_id=%s intent=%s reason=%s",
                     input.thread_id, response.intent, response.reason)

        return AgentOutput(
            reply="",
            status=AgentStatus.SUCCESS,
            data={
                "intent": response.intent,
                "reason": response.reason,
            },
        )


router_agent = RouterAgent()
