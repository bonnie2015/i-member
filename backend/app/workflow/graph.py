from typing import Any, Dict, List

import json

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

from app.config.logging import get_logger
from app.models.interaction import InteractionPayload
from app.workflow.state import AgentState
from app.workflow.nodes.post_process.post_process import post_process_node
from app.workflow.nodes.router import router_condition, router_node
from app.workflow.nodes.qa.node import qa_node
from app.workflow.nodes.recommend.node import recommend_node
from app.workflow.nodes.ticket.graph import get_ticket_workflow

logger = get_logger("workflow")
workflow = None  # 由 lifespan 初始化


class _InterruptPayload(BaseModel):
    reply: str
    interaction: InteractionPayload | None = None


def get_workflow():
    return workflow


def _list_interrupts(saved_state: Any) -> List[Any]:
    interrupts = list(getattr(saved_state, "interrupts", None) or ())
    if interrupts:
        return interrupts

    collected: List[Any] = []
    for task in getattr(saved_state, "tasks", None) or ():
        collected.extend(list(getattr(task, "interrupts", None) or ()))
    return collected


def _get_last_interrupt_payload(saved_state: Any) -> Dict[str, Any] | None:
    interrupts = _list_interrupts(saved_state)
    if not interrupts:
        return None

    payload = interrupts[-1]
    raw_payload = getattr(payload, "value", payload)
    try:
        parsed = _InterruptPayload.model_validate(raw_payload)
    except ValidationError as e:
        logger.warning("[workflow] invalid interrupt payload: %s", e)
        return None
    return parsed.model_dump()


def has_pending_interrupt(saved_state: Any) -> bool:
    if not saved_state:
        return False
    return bool(_list_interrupts(saved_state))


def _interrupt_message_content(interrupt_payload: Dict[str, Any] | None) -> str:
    if not interrupt_payload:
        return ""
    reply = str(interrupt_payload.get("reply") or "").strip()
    interaction = interrupt_payload.get("interaction")
    if interaction:
        interaction_text = json.dumps(interaction, ensure_ascii=False)
        return f"{reply}\n\n[interaction]{interaction_text}"
    return reply


async def _build_invoke_input(
    wf: Any,
    config: Dict[str, Any],
    user_message: str,
    user_id: str,
    thread_id: str,
    channel: str,
    log_tag: str,
) -> Any:
    interrupted = False
    interrupt_payload: Dict[str, Any] | None = None
    try:
        saved_state = await wf.aget_state(config)
        interrupted = has_pending_interrupt(saved_state)
        if interrupted:
            interrupt_payload = _get_last_interrupt_payload(saved_state)
    except Exception as e:
        logger.warning("[%s] get_state failed: %s", log_tag, e)

    if interrupted:
        message_updates = []
        interrupt_content = _interrupt_message_content(interrupt_payload)
        if interrupt_content:
            message_updates.append(AIMessage(content=interrupt_content))
        message_updates.append(HumanMessage(content=user_message))
        return Command(
            update={
                "messages": message_updates,
            },
            resume=user_message,
        )

    user_context: Dict[str, Any] = {}
    try:
        from app.service.user_context import load_user_context

        user_context = await load_user_context(user_id, thread_id=thread_id)
    except Exception as e:
        logger.warning("[%s] user context load failed: %s", log_tag, e)

    return {
        "user_id": user_id,
        "thread_id": thread_id,
        "channel": channel,
        "messages": [HumanMessage(content=user_message)],
        "service_entry_message": user_message,
        "final_reply": None,
        "intent": None,
        "reason": None,
        "is_direct_reply": False,
        "emotion_score": None,
        "user_context": user_context,
        "token_usage_total": 0,
    }


def create_workflow(checkpointer):
    graph = StateGraph(AgentState)

    graph.add_node("router_node", router_node)
    graph.add_node("ticket_agent", get_ticket_workflow())
    graph.add_node("qa_agent", qa_node)
    graph.add_node("recommend_agent", recommend_node)
    graph.add_node("post_process_node", post_process_node)

    graph.set_entry_point("router_node")
    graph.add_conditional_edges(
        "router_node",
        router_condition,
        {
            "post_process": "post_process_node",
            "ticket": "ticket_agent",
            "qa": "qa_agent",
            "recommend": "recommend_agent",
        },
    )
    graph.add_edge("ticket_agent", "post_process_node")
    graph.add_edge("qa_agent", "post_process_node")
    graph.add_edge("recommend_agent", "post_process_node")
    graph.add_edge("post_process_node", END)
    return graph.compile(checkpointer=checkpointer)


async def invoke_member_ops(
    user_message: str,
    user_id: str,
    thread_id: str,
    channel: str = "api",
) -> Dict[str, Any]:
    wf = get_workflow()
    config = {"configurable": {"thread_id": thread_id}}
    invoke_state = await _build_invoke_input(
        wf=wf,
        config=config,
        user_message=user_message,
        user_id=user_id,
        thread_id=thread_id,
        channel=channel,
        log_tag="invoke_agent",
    )
    result = await wf.ainvoke(invoke_state, config)
    saved_state = await wf.aget_state(config)
    interrupt_payload = _get_last_interrupt_payload(saved_state)
    if interrupt_payload is not None:
        return {
            "thread_id": thread_id,
            "reply": interrupt_payload.get("reply") or "",
            "interaction": interrupt_payload.get("interaction"),
        }
    reply = str((result or {}).get("final_reply") or "").strip()
    return {
        "thread_id": thread_id,
        "reply": reply,
        "interaction": None,
    }
