from typing import Any, Dict, List

import json

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError

from app.config.logging import get_logger
from app.models.interaction import InteractionPayload
from app.workflow.state import AgentState
from app.workflow.nodes.post_process.post_process import (
    spawn_post_process_tasks,
)
from app.workflow.nodes.router.router import router_condition, router_node
from app.workflow.nodes.qa.qa import qa_node
from app.workflow.nodes.recommend.graph import get_recommend_workflow
from app.workflow.nodes.ticket.graph import get_ticket_workflow

logger = get_logger("workflow")
workflow = None  # 由 lifespan 初始化
_LANGGRAPH_RECURSION_REPLY = "Sorry, need more steps to process this request."
_GRAPH_FALLBACK_HAS_PRODUCT_REPLY = "找到了这几款，看看有没有喜欢的？"
_GRAPH_FALLBACK_WITHOUT_PRODUCT_REPLY = "暂时没有找到合适的商品，能不能提供更多信息让我帮你找找看？"
_SERVICE_END_CLEAR_UPDATES: Dict[str, Any] = {
    "intent": None,
    "reason": None,
    "current_subgraph": None,
    "final_reply": None,
    "final_status": None,
    "final_reason": None,
    "trace": [],
    "started_at": None,
    "recommend_loop": 0,
    "recommend_context": None,
    "recommended_products": [],
    "qa_turn_count": 0,
    "entry_message": None,
    "service_key": None,
    "goal": None,
    "steps": [],
    "current_step_index": 0,
    "expected_slots": [],
    "ticket_next_node": None,
    "executor_retry_count": 0,
    "replan_count": 0,
    "planner_reason": None,
    "slots": None,
    "service_type": None,
}


class _InterruptPayload(BaseModel):
    reply: str
    interaction: InteractionPayload | None = None
    products: List[Dict[str, Any]] = Field(default_factory=list)
    trace: List[Any] = Field(default_factory=list)


def get_workflow():
    return workflow


async def get_thread_owner_user_id(thread_id: str) -> str | None:
    wf = get_workflow()
    if wf is None:
        return None
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        return None

    try:
        saved_state = await wf.aget_state({"configurable": {"thread_id": normalized_thread_id}})
    except Exception as e:
        logger.warning("[workflow] get_thread_owner failed: %s", e)
        return None

    values = getattr(saved_state, "values", None)
    if not isinstance(values, dict):
        return None

    owner_user_id = str(values.get("user_id") or "").strip()
    return owner_user_id or None


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
    parts = [reply] if reply else []
    interaction = interrupt_payload.get("interaction")
    if interaction:
        interaction_text = json.dumps(interaction, ensure_ascii=False)
        parts.append(f"[interaction]{interaction_text}")
    products = list(interrupt_payload.get("products") or [])
    if products:
        products_text = json.dumps(products, ensure_ascii=False)
        parts.append(f"[products]{products_text}")
    return "\n\n".join(parts)


def _extract_last_direct_ai_reply(messages: List[Any]) -> str:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if list(getattr(message, "tool_calls", None) or []):
            continue
        content = str(getattr(message, "content", "") or "").strip()
        if not content or content == _LANGGRAPH_RECURSION_REPLY:
            continue
        return content.split("\n\n[products]", 1)[0].strip()
    return ""


async def _clear_finished_service_state(wf: Any, config: Dict[str, Any], state: Dict[str, Any]) -> None:
    if state.get("current_subgraph"):
        return
    try:
        await wf.aupdate_state(config, dict(_SERVICE_END_CLEAR_UPDATES))
    except Exception as e:
        logger.warning("[workflow] clear_finished_service_state failed: %s", e)


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
    saved_values: Dict[str, Any] = {}
    try:
        saved_state = await wf.aget_state(config)
        values = getattr(saved_state, "values", None)
        saved_values = values if isinstance(values, dict) else {}
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
        update_payload: Dict[str, Any] = {
            "messages": message_updates,
        }
        interrupt_trace = list((interrupt_payload or {}).get("trace") or [])
        if interrupt_trace:
            update_payload["trace"] = [*list(saved_values.get("trace") or []), *interrupt_trace]
        return Command(
            update=update_payload,
            resume=user_message,
        )

    user_context: Dict[str, Any] = {}
    try:
        from app.agents.memory.user_context import load_user_context

        user_context = await load_user_context(user_id, thread_id=thread_id)
    except Exception as e:
        logger.warning("[%s] user context load failed: %s", log_tag, e)

    return {
        "user_id": user_id,
        "thread_id": thread_id,
        "channel": channel,
        "messages": [HumanMessage(content=user_message)],
        "user_context": user_context,
    }


def _entry_condition(state: AgentState) -> str:
    """入口条件：如果有 current_subgraph，直接进入该子图，否则进入 router"""
    current_subgraph = state.get("current_subgraph")
    if current_subgraph:
        return current_subgraph
    return "router"


def create_workflow(checkpointer):
    graph = StateGraph(AgentState)

    graph.add_node("router_node", router_node)
    graph.add_node("ticket_agent", get_ticket_workflow())
    graph.add_node("qa_agent", qa_node)
    graph.add_node("recommend", get_recommend_workflow())

    # 入口条件：根据 current_subgraph 决定进入哪个节点
    graph.set_conditional_entry_point(
        _entry_condition,
        {
            "router": "router_node",
            "ticket": "ticket_agent",
            "qa": "qa_agent",
            "recommend": "recommend",
        },
    )
    graph.add_conditional_edges(
        "router_node",
        router_condition,
        {
            "ticket": "ticket_agent",
            "qa": "qa_agent",
            "recommend": "recommend",
            "direct_reply": END,
        },
    )
    graph.add_conditional_edges(
        "ticket_agent",
        lambda state: "end",
        {
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "qa_agent",
        lambda state: "end",
        {
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "recommend",
        lambda state: "end",
        {
            "end": END,
        },
    )
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
    saved_values = getattr(saved_state, "values", None)
    saved_values = saved_values if isinstance(saved_values, dict) else {}
    interrupt_payload = _get_last_interrupt_payload(saved_state)
    if interrupt_payload is not None:
        reply = str(interrupt_payload.get("reply") or "").strip()
        products = list(interrupt_payload.get("products") or [])
        return {
            "thread_id": thread_id,
            "reply": (
                _GRAPH_FALLBACK_HAS_PRODUCT_REPLY
                if products
                else _GRAPH_FALLBACK_WITHOUT_PRODUCT_REPLY
            )
            if reply == _LANGGRAPH_RECURSION_REPLY
            else reply,
            "interaction": interrupt_payload.get("interaction"),
            "products": products,
        }
    reply = str((result or {}).get("final_reply") or "").strip()
    products = list((result or {}).get("recommended_products") or [])
    if reply == _LANGGRAPH_RECURSION_REPLY:
        messages = list((result or {}).get("messages") or saved_values.get("messages") or [])
        reply = _extract_last_direct_ai_reply(messages) or (
            _GRAPH_FALLBACK_HAS_PRODUCT_REPLY if products else _GRAPH_FALLBACK_WITHOUT_PRODUCT_REPLY
        )
    state_snapshot = dict(saved_values)
    service_finished = not str(state_snapshot.get("current_subgraph") or "").strip()
    if service_finished and str(state_snapshot.get("intent") or "").strip() != "direct_reply":
        spawn_post_process_tasks(state_snapshot)
    await _clear_finished_service_state(wf, config, state_snapshot)
    return {
        "thread_id": thread_id,
        "reply": reply,
        "interaction": None,
        "products": products,
    }
