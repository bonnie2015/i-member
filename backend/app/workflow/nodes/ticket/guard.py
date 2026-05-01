from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import invoke_with_usage_logging
from app.agents.prompts.prompt_builder import (
    PromptCapabilityContext,
    build_ticket_guard_system_prompt,
)
from app.agents.skills.registry import (
    load_skill_context,
    load_skill_metadata,
)
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("ticket_guard")

_REMOTE_GUARD_TIMEOUT_SECONDS = 45
_RECENT_DIALOG_MESSAGE_LIMIT = 4


class GuardOutput(BaseModel):
    decision: Literal["select_service", "clarify", "end_service"]
    service_key: Optional[str] = None
    goal: Optional[str] = None
    reason: str
    reply: Optional[str] = None


def _recent_dialog_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    dialog_messages = [
        message
        for message in messages
        if isinstance(message, (HumanMessage, AIMessage))
        and str(getattr(message, "content", "") or "").strip()
    ]
    return dialog_messages[-_RECENT_DIALOG_MESSAGE_LIMIT:]


async def _recognize_service_once(
    state: AgentState,
    *,
    messages: List[BaseMessage],
) -> GuardOutput:
    prompt = await build_ticket_guard_system_prompt(
        capability_context=PromptCapabilityContext(
            ticket_skills_snapshot=load_skill_context(group="ticket"),
        ),
    )
    llm = get_remote_llm(role="ticket").with_structured_output(GuardOutput)
    llm_messages = [
        SystemMessage(content=prompt),
        *_recent_dialog_messages(messages),
    ]
    response, _ = await invoke_with_usage_logging(
        llm=llm,
        messages=llm_messages,
        node="ticket_guard",
        thread_id=state.get("thread_id"),
        user_id=state.get("user_id"),
        provider="deepseek",
        timeout_seconds=_REMOTE_GUARD_TIMEOUT_SECONDS,
    )
    return response


def _resolve_selected_service(response: GuardOutput) -> Dict[str, Any]:
    service_key = str(response.service_key or "").strip() or None
    selected_skill_meta: Dict[str, Any] = load_skill_metadata(service_key, group="ticket") if service_key else {}
    if not service_key or not selected_skill_meta:
        raise ValueError(f"guard returned unresolved service selection: service_key={service_key}")

    return {
        "service_key": service_key,
        "goal": str(response.goal or "").strip(),
    }


async def guard_node(state: AgentState) -> Dict[str, Any]:
    working_messages = list(state.get("messages") or [])
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    while True:
        try:
            response = await _recognize_service_once(state, messages=working_messages)
        except Exception as exc:
            logger.error("[guard] thread_id=%s guard_unavailable error=%s", thread_id, exc)
            final_reply = "当前服务暂时较忙，请稍等片刻后再试；如果方便，也可以稍后重新发送您的问题。"
            return {
                "service_key": None,
                "final_status": "failed",
                "final_reason": "guard_unavailable",
                "final_reply": final_reply,
                "current_subgraph": None,
                "messages": [AIMessage(content=final_reply)],
            }

        if response.decision == "clarify":
            reply = str(response.reply or "").strip() or "请补充更具体的服务信息，方便我继续帮您处理。"
            logger.info("[guard] thread_id=%s decision=clarify", thread_id)
            resumed_user_message = str(interrupt({"reply": reply, "interaction": None}) or "").strip()
            working_messages = [
                *working_messages,
                AIMessage(content=reply),
                HumanMessage(content=resumed_user_message),
            ]
            continue

        if response.decision == "end_service":
            final_reply = (
                str(response.reply or "").strip()
                or "当前工单服务已结束。如果您有其他新需求，可以在下一轮对话中重新告诉我，我会继续为您处理。"
            )
            final_reason = str(response.reason or "").strip() or "ticket_service_ended"
            final_status = "cancelled"
            logger.info(
                "[guard] thread_id=%s decision=end final_status=%s reason=%s",
                thread_id,
                final_status,
                final_reason,
            )
            return {
                "service_key": None,
                "final_status": final_status,
                "final_reason": final_reason,
                "final_reply": final_reply,
                "current_subgraph": None,
                "messages": [AIMessage(content=final_reply)],
            }

        if response.decision != "select_service":
            logger.error(
                "[guard] thread_id=%s invalid_decision decision=%s",
                thread_id,
                response.decision,
            )
            raise ValueError(f"unexpected guard decision: {response.decision}")

        selected = _resolve_selected_service(response)
        logger.info(
            "[guard] thread_id=%s decision=select service_key=%s",
            thread_id,
            selected["service_key"],
        )
        return {
            "service_key": selected["service_key"],
            "goal": selected["goal"],
        }
