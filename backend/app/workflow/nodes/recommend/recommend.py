from __future__ import annotations

from uuid import uuid4
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.base import AgentInput, AgentStatus
from app.agents.recommend_agent import recommend_agent
from app.tools.business.execution_context import business_execution_context
from app.config.logging import get_logger
from app.config.constants import RECOMMEND_MAX_ROUNDS
from app.workflow.state import AgentState
from app.agents.recommend_guard_agent import recommend_guard_agent

logger = get_logger("recommend_node")


def _last_user_message_text(state: AgentState) -> str:
    messages = list(state.get("messages") or [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(getattr(message, "content", "") or "").strip()
    return ""


def _build_displayed_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    displayed: List[Dict[str, Any]] = []
    for index, product in enumerate(
        [item for item in list(products or []) if isinstance(item, dict)], start=1
    ):
        card: Dict[str, Any] = {"index": index}
        for key in (
            "product_id",
            "color_id",
            "name",
            "price",
            "image",
            "official_url",
            "color_name",
            "category",
            "gender",
        ):
            value = product.get(key)
            if value is not None and value != "":
                card[key] = value
        displayed.append(card)
    return displayed


async def recommend_node(state: AgentState) -> Dict[str, Any]:
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    user_id = state.get("user_id")
    trace = list(state.get("trace") or [])
    service_state = state.get("service_state") or {}
    prev_recommend_context = service_state.get("recommend_context") or {}

    round_num = len(trace) + 1
    logger.info("[recommend_node] thread_id=%s round=%s start", thread_id, round_num)

    # 0. 轮次上限：超过则强制结束，走后处理
    if len(trace) >= RECOMMEND_MAX_ROUNDS:
        reply = "推荐了好多商品，但也要记得理性购物哦～休息一会儿吧。"
        logger.info(
            "[recommend_node] thread_id=%s round_limit reached=%s", thread_id, round_num
        )
        return {
            "final_reply": reply,
            "final_status": "success",
            "current_subgraph": None,
            "trace": trace,
            "messages": [*state["messages"], AIMessage(content=reply)],
            "service_state": None,
        }

    # 1. guard：判断任务是否已完成，注入上轮的 recommend_context 和最新 trace
    guard_result = await recommend_guard_agent.run(
        AgentInput(
            user_context=state.get("user_context") or {},
            thread_id=thread_id,
            user_id=user_id,
            messages=state.get("messages") or [],
            extra={
                "trace": trace[-1:] if trace else [],  # 只传最新一轮 trace
                "recommend_context": prev_recommend_context,
            },
        )
    )

    guard_data = guard_result.data if guard_result.status == AgentStatus.SUCCESS else {}
    task_completed = bool(guard_data.get("task_completed"))

    message_id = f"recommend:{thread_id}:{uuid4().hex}"

    if task_completed:
        reply = (
            str(guard_data.get("reply") or "").strip()
            or "好的，当前推荐服务已结束，有需要随时找我。"
        )
        logger.info(
            "[recommend_node] thread_id=%s service_end reply=%s", thread_id, reply[:60]
        )
        return {
            "final_reply": reply,
            "final_status": "success",
            "current_subgraph": None,
            "trace": [
                *trace,
                {
                    "round": round_num,
                    "message_id": message_id,
                    "input_user_message": _last_user_message_text(state),
                    "output_ai_message": reply,
                    "displayed_products": [],
                    "summary": guard_data.get("summary", ""),
                    "anchor_products": guard_data.get("anchor_products", []),
                },
            ],
            "messages": [*state["messages"], AIMessage(content=reply, id=message_id)],
            "service_state": None,
        }

    # 2. guard 说继续 → agent 推荐
    recommend_context: Dict[str, Any] = {
        "summary": guard_data.get("summary", ""),
        "cursor": guard_data.get("cursor", {}),
        "anchor_products": guard_data.get("anchor_products", []),
    }

    with business_execution_context(thread_id=thread_id, user_id=user_id):
        result = await recommend_agent.run(
            AgentInput(
                user_context=state.get("user_context") or {},
                thread_id=thread_id,
                user_id=user_id,
                messages=state.get("messages") or [],
                extra={"recommend_context": recommend_context},
            )
        )

    products = result.data.get("products") or []
    displayed = _build_displayed_products(products)

    trace_item: Dict[str, Any] = {
        "round": round_num,
        "message_id": message_id,
        "input_user_message": _last_user_message_text(state),
        "output_ai_message": result.reply,
        "displayed_products": displayed,
    }
    if recommend_context["cursor"]:
        trace_item["cursor"] = recommend_context["cursor"]

    new_trace = [*trace, trace_item]

    return {
        "final_reply": result.reply,
        "current_subgraph": "recommend",
        "trace": new_trace,
        "messages": [
            *state["messages"],
            AIMessage(content=result.reply, id=message_id),
        ],
        "service_state": {
            "recommended_products": products,
            "recommend_context": recommend_context,
        },
    }
