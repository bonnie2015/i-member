from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import invoke_with_usage_logging
from app.prompts.prompt_builder import PromptCapabilityContext, build_ticket_guard_system_prompt
from app.skills.registry import load_skill_context

logger = get_logger("ticket_guard_agent")

_RECENT_DIALOG_LIMIT = 4


class GuardOutput(BaseModel):
    decision: Literal["select_service", "clarify", "end_service"]
    service_key: Optional[str] = None
    goal: Optional[str] = None
    reason: str = ""
    reply: Optional[str] = None


class TicketGuardAgent(BaseAgent):

    def __init__(self):
        config = AgentConfig(
            name="ticket_guard",
            role="ticket_guard",
            timeout_seconds=30,
            max_recursion=1,
            max_tool_calls=0,
            fallback_reply="当前服务暂时较忙，请稍等片刻后再试。",
        )
        super().__init__(config)

    async def _execute(self, input: AgentInput) -> AgentOutput:
        messages: List[BaseMessage] = list(input.messages or [])
        # 只取最近两轮（4条消息）
        messages = messages[-_RECENT_DIALOG_LIMIT:] if len(messages) > _RECENT_DIALOG_LIMIT else messages
        messages = [
            msg for msg in messages
            if isinstance(msg, (HumanMessage, AIMessage))
            and str(getattr(msg, "content", "") or "").strip()
        ]
        skill_snapshot = str(input.extra.get("skill_snapshot") or "")
        if not skill_snapshot:
            skill_snapshot = load_skill_context(group="ticket")

        prompt = await build_ticket_guard_system_prompt(
            capability_context=PromptCapabilityContext(
                ticket_skills_snapshot=skill_snapshot,
            ),
        )

        llm = get_llm("ticket_guard").with_structured_output(GuardOutput)
        llm_messages: List[BaseMessage] = [
            SystemMessage(content=prompt),
            *messages,
        ]

        logger.info("[ticket_guard_agent] thread_id=%s dialog_count=%s", input.thread_id, len(messages))

        response: GuardOutput = (
            await invoke_with_usage_logging(
                llm=llm,
                messages=llm_messages,
                node="ticket_guard",
                thread_id=input.thread_id,
                user_id=input.user_id,
                provider="ollama",
            )
        )[0]

        logger.info(
            "[ticket_guard_agent] thread_id=%s decision=%s service_key=%s",
            input.thread_id,
            response.decision,
            response.service_key,
        )

        return AgentOutput(
            reply=str(response.reply or "").strip(),
            status=AgentStatus.SUCCESS,
            data={
                "decision": response.decision,
                "service_key": response.service_key,
                "goal": str(response.goal or "").strip(),
                "reason": response.reason,
            },
        )


ticket_guard_agent = TicketGuardAgent()
