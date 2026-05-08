"""
UserFactsAgent — 用户画像事实提取。

每次对话结束后异步执行：从消息中提取用户偏好/事实，
与已有画像合并去重，写回 Redis。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.config.logging import get_logger

logger = get_logger("user_facts_agent")

_USER_FACTS_TIMEOUT_SECONDS = 30


class UserFactsAgent(BaseAgent):
    """从对话中提取用户事实，合并写入 Redis。

    输入：AgentInput.user_query 为 user_id，
         AgentInput.extra["messages"] 为对话消息列表。
    输出：AgentOutput.data["added"] / ["deleted"] 为变更事实列表。
    """

    def __init__(self):
        config = AgentConfig(
            name="user_facts",
            role="postprocess",
            timeout_seconds=_USER_FACTS_TIMEOUT_SECONDS,
            max_recursion=1,
            max_tool_calls=0,
            fallback_reply="",
        )
        super().__init__(config)

    async def _execute(self, input: AgentInput) -> AgentOutput:
        from app.memory.user_facts import extract_and_save_user_facts

        user_id = input.user_id or input.user_query or "unknown"
        messages: Sequence[Any] = list(input.extra.get("messages") or [])

        if not messages:
            return AgentOutput(
                reply="",
                status=AgentStatus.SUCCESS,
                data={"added": [], "deleted": []},
            )

        store = await extract_and_save_user_facts(
            user_id=user_id,
            messages=messages,
            thread_id=input.thread_id,
        )

        logger.info(
            "[user_facts_agent] user_id=%s facts_count=%s",
            user_id,
            len(store.facts),
        )

        return AgentOutput(
            reply="",
            status=AgentStatus.SUCCESS,
            data={
                "fact_count": len(store.facts),
            },
        )


user_facts_agent = UserFactsAgent()
