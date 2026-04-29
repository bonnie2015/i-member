from __future__ import annotations

import ast
import asyncio
import json
from uuid import uuid4
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from app.agents.tools.business.execution_context import business_execution_context
from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import with_usage_logging
from app.agents.prompts.prompt_builder import build_recommend_system_prompt
from app.agents.tools.business.onitsuka_adapter import hydrate_display_products
from app.agents.tools import (
    get_onitsuka_tools,
    onitsuka_get_product_detail,
    reply_with_products_tool,
)
from app.config.logging import get_logger

from app.workflow.state import AgentState

logger = get_logger("recommend_node")

_MAX_RECOMMEND_MODEL_TOOL_BLOCKS = 2
_MAX_TOTAL_TOOL_CALLS = 3
_MAX_REACT_RECURSION = 12
_RECOMMEND_TIMEOUT_SECONDS = 70
_LANGGRAPH_RECURSION_REPLY = "Sorry, need more steps to process this request."
_FALLBACK_REPLY = {
    "has_product": "找到了这几款，看看有没有喜欢的？",
    "no_product": "暂时没有找到合适的商品，能不能提供更多信息让我帮你找找看？",
}


def _last_user_message(state: AgentState) -> List[BaseMessage]:
    messages = list(state.get("messages") or [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return [message]
    return []


def _last_user_message_text(state: AgentState) -> str:
    messages = _last_user_message(state)
    if not messages:
        return ""
    return _message_text(getattr(messages[-1], "content", "")).strip()


def _recommend_agent_messages(state: AgentState) -> List[BaseMessage]:
    messages = _last_user_message(state)
    latest_user = messages[-1] if messages else None
    recommend_context = state.get("recommend_context") or {}
    if not isinstance(recommend_context, dict) or not recommend_context:
        return messages

    service_summary = HumanMessage(
        content=(
            "前序服务记录：\n"
            f"{json.dumps(recommend_context, ensure_ascii=False, default=str)}\n\n"
            "以上是前面的服务压缩摘要；用户最新发言在下一条消息。"
        )
    )
    if latest_user is None:
        return [service_summary]
    return [service_summary, latest_user]


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", tool.__class__.__name__) or "").strip()


def _resolve_search_tools(_state: AgentState) -> List[Any]:
    return [*get_onitsuka_tools()]


def _resolve_tools(_state: AgentState) -> List[Any]:
    return [*_resolve_search_tools(_state), onitsuka_get_product_detail, reply_with_products_tool]


def _resolve_model_tools(
    messages: List[BaseMessage],
    *,
    tools: List[Any],
) -> List[Any]:
    if _completed_tool_call_count(messages) >= _MAX_TOTAL_TOOL_CALLS:
        return [tool for tool in tools if _tool_name(tool) == str(reply_with_products_tool.name)]
    return tools


def _compact_recommend_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    tool_blocks: List[List[BaseMessage]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AIMessage):
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            tool_call_ids = {
                str(call.get("id") or "").strip()
                for call in tool_calls
                if isinstance(call, dict) and str(call.get("id") or "").strip()
            }
            if tool_call_ids:
                block = [message]
                matched_ids = set()
                next_index = index + 1
                while next_index < len(messages) and isinstance(messages[next_index], ToolMessage):
                    tool_call_id = str(getattr(messages[next_index], "tool_call_id", "") or "").strip()
                    if not tool_call_id or tool_call_id not in tool_call_ids:
                        break
                    block.append(messages[next_index])
                    matched_ids.add(tool_call_id)
                    next_index += 1
                if tool_call_ids.issubset(matched_ids):
                    tool_blocks.append(block)
                index = next_index
                continue

        index += 1

    if not tool_blocks:
        return messages
    return [message for block in tool_blocks[-_MAX_RECOMMEND_MODEL_TOOL_BLOCKS:] for message in block]


def _completed_tool_call_count(messages: List[BaseMessage]) -> int:
    return sum(1 for message in messages if isinstance(message, ToolMessage))


def _tool_control_message(tool_count: int) -> SystemMessage | None:
    if _MAX_TOTAL_TOOL_CALLS - tool_count == 1:
        return SystemMessage(
            content=(
                f"【提醒】本轮已完成 {tool_count} 次工具调用，剩余 {_MAX_TOTAL_TOOL_CALLS - tool_count} 次工具调用机会。"
                "如果已有基本相关的候选项，请尽快调用 reply_with_products 返回推荐或追问用户锁定搜索条件。"
            )
        )
    if tool_count >= _MAX_TOTAL_TOOL_CALLS:
        return SystemMessage(
            content=(
                f"【限制】已达到本轮查询工具使用上限（{tool_count} 次）。"
                "请立即调用 reply_with_products 返回；如果没有可用候选，请调用 reply_with_products 追问一个最关键条件。"
            )
        )
    return None


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def _parse_payload(content: Any) -> Dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    text = _message_text(content).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    try:
        parsed = ast.literal_eval(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _extract_recommendation_reply_result(messages: List[BaseMessage]) -> Dict[str, Any] | None:
    calls_by_id = _tool_call_args_by_id(messages)
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        if _tool_message_name(message, calls_by_id) != str(reply_with_products_tool.name):
            continue
        payload = _parse_payload(getattr(message, "content", ""))
        if not isinstance(payload, dict):
            continue
        reply = str(payload.get("reply") or "").strip()
        products = list(payload.get("products") or [])
        if not reply and not products:
            continue
        return {
            "reply": reply,
            "products": products,
        }
    return None


def _extract_direct_ai_reply(messages: List[BaseMessage]) -> str:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if list(getattr(message, "tool_calls", None) or []):
            continue
        reply = _message_text(getattr(message, "content", "")).strip()
        if not reply or reply == _LANGGRAPH_RECURSION_REPLY:
            continue
        return reply.split("\n\n[products]", 1)[0].strip()
    return ""


def _build_fallback_reply(has_products: bool) -> str:
    return _FALLBACK_REPLY["has_product"] if has_products else _FALLBACK_REPLY["no_product"]


def _normalize_tool_args(args: Any) -> Dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    return dict(args)


def _tool_call_args_by_id(messages: List[BaseMessage]) -> Dict[str, Dict[str, Any]]:
    calls_by_id: Dict[str, Dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in list(getattr(message, "tool_calls", None) or []):
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "").strip()
            if not call_id:
                continue
            calls_by_id[call_id] = {
                "name": str(call.get("name") or "").strip(),
                "args": _normalize_tool_args(call.get("args")),
            }
    return calls_by_id


def _tool_message_name(message: ToolMessage, calls_by_id: Dict[str, Dict[str, Any]]) -> str:
    direct_name = str(getattr(message, "name", "") or "").strip()
    if direct_name:
        return direct_name
    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        additional_name = str(additional_kwargs.get("name") or "").strip()
        if additional_name:
            return additional_name
    call_id = str(getattr(message, "tool_call_id", "") or "").strip()
    call = calls_by_id.get(call_id) or {}
    return str(call.get("name") or "").strip()


def _extract_candidate_product_refs(messages: List[BaseMessage], *, limit: int = 4) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        payload = _parse_payload(getattr(message, "content", ""))
        if not isinstance(payload, dict):
            continue
        for item in list(payload.get("products") or []):
            if not isinstance(item, dict):
                continue
            try:
                product_id = int(item.get("product_id") or 0)
            except Exception:
                product_id = 0
            try:
                color_id = int(item.get("color_id") or item.get("default_color_id") or 0) or None
            except Exception:
                color_id = None
            if not product_id:
                continue
            key = (product_id, color_id)
            if key in seen:
                continue
            seen.add(key)
            ref: Dict[str, Any] = {"product_id": product_id}
            if color_id:
                ref["color_id"] = color_id
            refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


def _build_recommend_result_from_messages(messages: List[BaseMessage], round_num: int) -> Dict[str, Any]:
    trace = {"round": round_num}
    final_result = _extract_recommendation_reply_result(messages)
    if final_result is not None:
        products = list(final_result.get("products") or [])
        reply = str(final_result.get("reply") or "").strip()
        if not reply or reply == _LANGGRAPH_RECURSION_REPLY:
            reply = _build_fallback_reply(bool(products))
        return {
            "reply": reply,
            "products": products,
            "trace": trace,
        }

    reply = _extract_direct_ai_reply(messages)
    products = hydrate_display_products(_extract_candidate_product_refs(messages))
    if not reply:
        reply = _build_fallback_reply(bool(products))
    return {
        "reply": reply,
        "products": products,
        "trace": trace,
    }


def _build_recommend_message(reply: str, products: List[Dict[str, Any]]) -> str:
    normalized_reply = str(reply or "").strip()
    normalized_products = [item for item in list(products or []) if isinstance(item, dict)]
    if not normalized_products:
        return normalized_reply
    products_text = json.dumps(normalized_products, ensure_ascii=False)
    parts = [normalized_reply] if normalized_reply else []
    parts.append(f"[products]{products_text}")
    return "\n\n".join(parts)


def _build_displayed_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    displayed: List[Dict[str, Any]] = []
    for index, product in enumerate([item for item in list(products or []) if isinstance(item, dict)], start=1):
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
            "cursor",
        ):
            value = product.get(key)
            if value is not None and value != "":
                card[key] = value
        displayed.append(card)
    return displayed


async def _run_recommend(state: AgentState) -> Dict[str, Any]:
    tools = _resolve_tools(state)
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    round_num = int(state.get("recommend_loop") or 0) + 1

    recommend_context = state.get("recommend_context") or {}
    prompt = await build_recommend_system_prompt(
        user_context=state.get("user_context") or {},
        runtime_context={},
    )
    agent_messages = _recommend_agent_messages(state)

    def resolve_logged_model(_agent_state: Dict[str, Any], _runtime: Any) -> Any:
        messages = [message for message in list(_agent_state.get("messages") or []) if isinstance(message, BaseMessage)]
        model_tools = _resolve_model_tools(messages, tools=tools)
        if len(model_tools) < len(tools):
            logger.info(
                "[recommend_node] thread_id=%s tool_limited count=%s tools=%s",
                thread_id,
                _completed_tool_call_count(messages),
                [_tool_name(tool) for tool in model_tools],
            )
        bound_model = get_remote_llm(role="recommend").bind_tools(model_tools)
        return with_usage_logging(
            bound_model,
            node="recommend_llm",
            thread_id=state.get("thread_id"),
            user_id=state.get("user_id"),
            provider="deepseek",
        )

    def compact_model_input(agent_state: Dict[str, Any]) -> Dict[str, Any]:
        messages = [message for message in list(agent_state.get("messages") or []) if isinstance(message, BaseMessage)]
        compacted = _compact_recommend_messages(messages)
        if len(compacted) < len(messages):
            logger.info(
                "[recommend_node] thread_id=%s compact_model_messages before=%s after=%s",
                thread_id,
                len(messages),
                len(compacted),
            )

        tool_count = _completed_tool_call_count(messages)
        control_message = _tool_control_message(tool_count)
        if control_message is not None:
            logger.info(
                "[recommend_node] thread_id=%s tool_control_warning count=%s",
                thread_id,
                tool_count,
            )
            compacted = [*compacted, control_message]

        return {"llm_input_messages": compacted}

    agent = create_react_agent(
        model=resolve_logged_model,
        tools=tools,
        prompt=prompt,
        pre_model_hook=compact_model_input,
    )
    logger.info(
        "[recommend_node] thread_id=%s invoke_agent tools=%s last_user_message=%s context=%s",
        thread_id,
        [_tool_name(tool) for tool in tools],
        len(agent_messages),
        bool(recommend_context),
    )
    agent_result = await asyncio.wait_for(
        agent.ainvoke(
            {
                "messages": agent_messages,
            },
            {"recursion_limit": _MAX_REACT_RECURSION},
        ),
        timeout=_RECOMMEND_TIMEOUT_SECONDS,
    )
    messages = list((agent_result or {}).get("messages") or [])
    logger.info(
        "[recommend_node] thread_id=%s agent_completed messages=%s tool_messages=%s",
        str(state.get("thread_id") or "").strip() or "unknown",
        len(messages),
        len([message for message in messages if isinstance(message, ToolMessage)]),
    )
    agent_reply = _build_recommend_result_from_messages(messages, round_num)
    if _extract_recommendation_reply_result(messages) is None:
        logger.warning(
            "[recommend_node] thread_id=%s missing_final_tool recovered_reply=%s recovered_products=%s",
            str(state.get("thread_id") or "").strip() or "unknown",
            bool(agent_reply.get("reply")),
            len(list(agent_reply.get("products") or [])),
        )
    reply = str(agent_reply.get("reply") or "").strip()
    if not reply:
        raise ValueError("recommend result missing reply")
    return {
        "reply": reply,
        "products": list(agent_reply.get("products") or []),
        "trace": agent_reply.get("trace") or {},
    }


async def recommend_node(state: AgentState) -> Dict[str, Any]:
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    logger.info("[recommend_node] thread_id=%s start", thread_id)

    try:
        with business_execution_context(thread_id=thread_id, user_id=state.get("user_id")):
            try:
                agent_result = await _run_recommend(state)
            except GraphRecursionError as exc:
                logger.warning(
                    "[recommend_node] thread_id=%s recursion_limit_recovered products=0 error=%s",
                    thread_id,
                    exc,
                )
                agent_result = {
                    "reply": _build_fallback_reply(False),
                    "products": [],
                    "trace": {},
                    "status": "failed",
                    "reason": "recommendation_recursion_limit",
                }
    except Exception as exc:
        logger.error("[recommend_node] thread_id=%s failed error=%s", thread_id, exc)
        return {
            "final_reply": _build_fallback_reply(False),
            "final_status": "failed",
            "final_reason": "recommendation_error",
            "messages": [AIMessage(content=_build_fallback_reply(False))],
            "current_subgraph": None,
            "recommend_loop": 0,
            "recommend_context": None,
        }

    final_reply = agent_result["reply"]
    recommended_products = list(agent_result.get("products") or [])
    round_trace = agent_result.get("trace") or {}
    message_content = str(final_reply or "").strip()
    displayed_products = _build_displayed_products(recommended_products)
    message_id = f"recommend:{thread_id}:{uuid4().hex}"
    if isinstance(round_trace, dict):
        round_trace = {
            **round_trace,
            "message_id": message_id,
            "input_user_message": _last_user_message_text(state),
            "output_ai_message": message_content,
            "displayed_products": displayed_products,
        }

    logger.info("[recommend_node] thread_id=%s end products=%s", thread_id, len(recommended_products))
    next_trace = [*list(state.get("trace") or []), round_trace] if round_trace else list(state.get("trace") or [])

    return {
        "final_reply": final_reply,
        "final_status": str(agent_result.get("status") or "success"),
        "final_reason": str(agent_result.get("reason") or "recommendation_round_completed"),
        "recommended_products": recommended_products,
        "trace": next_trace,
        "messages": [AIMessage(content=message_content, id=message_id)],
        "current_subgraph": "recommend",
    }
