from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ValidationError

from app.agents.llm.llm_factory import get_remote_llm
from app.agents.llm.runtime import with_usage_logging
from app.agents.prompts.prompt_builder import (
    build_recommend_guard_system_prompt,
    RecommendGuardRuntimePayload,
)
from app.agents.tools.service_memory_tools import get_service_memory_tools
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("recommend_guard")

_MAX_RECOMMEND_LOOP = 20
_MAX_TOOL_CALL_ROUNDS = 2
_FALLBACK_REPLY_ERROR = "抱歉，服务遇到了一点问题，可以再试一次吗？"
_FALLBACK_REPLY_LOOP_LIMIT = "抱歉，我需要思考一下，可以再说一遍你的需求吗～"
_FALLBACK_REPLY_NO_REPLY = "推荐服务已结束，如有其他需要，可以继续向我咨询～"


class GuardOutput(BaseModel):
    task_completed: bool = False
    summary: str = ""
    anchor_products: List[Dict[str, Any]] = []
    cursor: Dict[str, Any] = {}
    reply: str = ""

    model_config = {"extra": "forbid"}


def _get_last_trace_item(state: AgentState) -> Dict[str, Any]:
    trace = list(state.get("trace") or [])
    for item in reversed(trace):
        if isinstance(item, dict):
            return item
    return {}


def _latest_user_message_text(state: AgentState) -> str:
    for message in reversed(list(state.get("messages") or [])):
        if isinstance(message, HumanMessage):
            return str(getattr(message, "content", "") or "").strip()
    return ""


async def _build_summary_system_prompt(state: AgentState) -> str:
    recommend_context = state.get("recommend_context") or {}
    last_trace = _get_last_trace_item(state)

    return await build_recommend_guard_system_prompt(
        runtime_context=RecommendGuardRuntimePayload(
            recommend_context=recommend_context,
            last_trace=last_trace,
        ),
    )


def _parse_guard_output(content: str) -> GuardOutput:
    text = str(content or "").strip()
    if not text:
        return GuardOutput(task_completed=False, summary="无法解析模型输出")

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            json_str = text[start:end]
            parsed = json.loads(json_str)
            return GuardOutput.model_validate(parsed)
    except json.JSONDecodeError:
        pass
    except ValidationError:
        pass

    return GuardOutput(task_completed=False, summary="未得到有效的历史服务记录，直接根据最新用户消息进行推荐")


async def _execute_tool_call(tool_call: Dict[str, Any], tools: List[BaseTool]) -> Dict[str, Any]:
    tool_name = str(tool_call.get("name") or tool_call.get("id") or "").strip()
    tool_args = dict(tool_call.get("args") or {})
    tool_call_id = str(tool_call.get("id") or "").strip()

    for tool in tools:
        if tool.name == tool_name:
            try:
                result = await tool.ainvoke(tool_args)
                return {"tool_call_id": tool_call_id, "result": result}
            except Exception as e:
                logger.warning("[guard_node] tool execution failed: %s", e)
                return {"tool_call_id": tool_call_id, "error": str(e)}

    return {"tool_call_id": tool_call_id, "error": f"tool not found: {tool_name}"}


async def _invoke_model_with_tools(
    model: Any,
    messages: List[BaseMessage],
    tools: List[BaseTool],
    max_rounds: int = _MAX_TOOL_CALL_ROUNDS,
) -> str:
    current_messages = list(messages)

    for round_num in range(max_rounds):
        response = await model.ainvoke(current_messages)
        content = str(getattr(response, "content", "") or "").strip()

        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            return content

        logger.info("[guard_node] tool_calls detected, round=%s", round_num + 1)
        current_messages.append(response)

        for tool_call in tool_calls:
            tool_result = await _execute_tool_call(tool_call, tools)
            tool_message = ToolMessage(
                content=json.dumps(tool_result.get("result") or tool_result, ensure_ascii=False),
                tool_call_id=tool_result.get("tool_call_id") or "",
            )
            current_messages.append(tool_message)

    final_response = await model.ainvoke(current_messages)
    return str(getattr(final_response, "content", "") or "").strip()


async def guard_node(state: AgentState) -> Dict[str, Any]:
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"
    current_loop = int(state.get("recommend_loop") or 0)
    recommend_loop = current_loop + 1
    is_first_round = recommend_loop == 1

    logger.info("[guard_node] thread_id=%s loop=%s start", thread_id, recommend_loop)

    if recommend_loop > _MAX_RECOMMEND_LOOP:
        logger.warning("[guard_node] thread_id=%s loop_limit_reached", thread_id)
        return {
            "current_subgraph": None,
            "recommend_loop": 0,
            "recommend_context": None,
            "recommended_products": [],
            "final_reply": _FALLBACK_REPLY_LOOP_LIMIT,
            "final_status": "failed",
            "final_reason": "recommend_loop_limit_reached",
        }

    try:
        system_prompt = await _build_summary_system_prompt(state)
        tools = get_service_memory_tools() if is_first_round else []
        base_model = get_remote_llm(role="recommend")
        model = base_model.bind_tools(tools) if tools else base_model
        logged_model = with_usage_logging(
            model,
            node="recommend_guard",
            thread_id=thread_id,
            user_id=state.get("user_id"),
            provider="deepseek",
        )

        messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
        latest_user_message = _latest_user_message_text(state)
        if latest_user_message:
            messages.append(HumanMessage(content=latest_user_message))
        if tools:
            content = await _invoke_model_with_tools(logged_model, messages, tools)
        else:
            response = await logged_model.ainvoke(messages)
            content = str(getattr(response, "content", "") or "").strip()
        logger.info("[guard_node] thread_id=%s model_response=%s", thread_id, content[:200])

        output = _parse_guard_output(content)
        logger.info(
            "[guard_node] thread_id=%s task_completed=%s summary=%s",
            thread_id,
            output.task_completed,
            output.summary[:100] if output.summary else "",
        )

        if output.task_completed:
            reply = str(output.reply or "").strip()
            if not reply:
                reply = _FALLBACK_REPLY_NO_REPLY

            return {
                "current_subgraph": None,
                "recommend_loop": 0,
                "recommend_context": None,
                "recommended_products": [],
                "final_reply": reply,
                "final_status": "success",
                "final_reason": "recommendation_completed",
            }

        context_dict = output.model_dump(exclude_none=True)
        context_dict.pop("reply", None)

        return {
            "current_subgraph": "recommend",
            "recommend_loop": recommend_loop,
            "recommend_context": context_dict,
            "recommended_products": [],
        }

    except Exception as exc:
        logger.error("[guard_node] thread_id=%s error=%s", thread_id, exc)
        return {
            "current_subgraph": None,
            "recommend_loop": 0,
            "recommend_context": None,
            "recommended_products": [],
            "final_reply": _FALLBACK_REPLY_ERROR,
            "final_status": "failed",
            "final_reason": "guard_node_error",
        }
