from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import invoke_with_usage_logging
from app.prompts.prompt_builder import build_recommend_guard_system_prompt

logger = get_logger("recommend_guard")


class RecommendGuardOutput(BaseModel):
    task_completed: bool
    summary: str
    reply: str = ""
    cursor: Dict[str, Any] = {}
    anchor_products: List[Dict[str, Any]] = []


class RecommendGuardAgent(BaseAgent):

    def __init__(self):
        config = AgentConfig(
            name="recommend_guard",
            role="recommend_guard",
            timeout_seconds=30,
            max_recursion=1,
            max_tool_calls=0,
            fallback_reply="",
        )
        super().__init__(config)

    async def _execute(self, input: AgentInput) -> AgentOutput:
        trace = list(input.extra.get("trace") or [])
        recommend_context = input.extra.get("recommend_context") or {}

        prompt = await build_recommend_guard_system_prompt()
        llm = get_llm("recommend_guard").with_structured_output(
            RecommendGuardOutput, method="json_mode"
        )

        # 按时间顺序拼接消息：累计总结 → 上一轮对话 → 最近对话
        llm_messages: list = [SystemMessage(content=prompt)]
        if recommend_context and isinstance(recommend_context, dict):
            llm_messages.append(HumanMessage(
                content="【当前推荐任务的累计总结，由上一轮守卫产出】\n"
                + json.dumps(recommend_context, ensure_ascii=False, indent=2, default=str)
            ))
        if trace:
            llm_messages.append(HumanMessage(
                content="【上一轮推荐过程】\n"
                + json.dumps(trace, ensure_ascii=False, indent=2, default=str)
            ))
        llm_messages.extend((input.messages or [])[-4:])

        logger.info("[recommend_guard] thread_id=%s trace_rounds=%s", input.thread_id, len(trace))

        response: RecommendGuardOutput = (
            await invoke_with_usage_logging(
                llm=llm,
                messages=llm_messages,
                node="recommend_guard",
                thread_id=input.thread_id,
                user_id=input.user_id,
                provider="deepseek",
            )
        )[0]

        logger.info(
            "[recommend_guard] thread_id=%s task_completed=%s summary=%s anchors=%s cursor=%s",
            input.thread_id,
            response.task_completed,
            response.summary,
            len(response.anchor_products),
            bool(response.cursor),
        )

        return AgentOutput(
            reply=response.reply,
            status=AgentStatus.SUCCESS,
            data={
                "task_completed": response.task_completed,
                "summary": response.summary,
                "reply": response.reply,
                "cursor": response.cursor or {},
                "anchor_products": response.anchor_products or [],
            },
        )


recommend_guard_agent = RecommendGuardAgent()
