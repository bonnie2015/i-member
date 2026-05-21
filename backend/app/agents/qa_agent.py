from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import with_usage_logging
from app.prompts.prompt_builder import build_qa_system_prompt
from app.tools.memory_tools import get_memory_tools
from app.tools.rag_tools import get_rag_tools
from app.tools.user_interaction_tools import reply_to_user_tool

logger = get_logger("qa_agent")

_MAX_TOOL_CALLS = 3
_MAX_GRAPH_STEPS = 16
_QA_TIMEOUT_SECONDS = 60
_FALLBACK_REPLY = (
    "我先帮您确认一下相关信息，您也可以把问题再说具体一点，我继续为您处理。"
)


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


def _extract_reply_result(messages: List[BaseMessage]) -> str | None:
    reply_tool_name = str(reply_to_user_tool.name)
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        if str(getattr(message, "name", "") or "").strip() != reply_tool_name:
            continue
        content = _message_text(getattr(message, "content", "")).strip()
        if not content:
            continue
        try:
            import json

            parsed = json.loads(content)
            if isinstance(parsed, dict):
                reply = str(parsed.get("reply") or "").strip()
                if reply:
                    return reply
        except Exception:
            pass
        return content
    return None


def _extract_direct_ai_reply(messages: List[BaseMessage]) -> str:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if list(getattr(message, "tool_calls", None) or []):
            continue
        reply = _message_text(getattr(message, "content", "")).strip()
        if not reply:
            continue
        return reply
    return ""


class QAAgent(BaseAgent):
    def __init__(self):
        config = AgentConfig(
            name="qa",
            role="qa",
            timeout_seconds=_QA_TIMEOUT_SECONDS,
            max_recursion=_MAX_GRAPH_STEPS,
            max_tool_calls=_MAX_TOOL_CALLS,
            fallback_reply=_FALLBACK_REPLY,
        )
        super().__init__(config)
        self.tools = [*get_memory_tools(), *get_rag_tools(), reply_to_user_tool]
        self._executor = None

    def _get_executor(
        self, base_prompt: str, thread_id: str, user_id: str | None = None
    ):
        tools = self.tools

        def _model_fn(state: Dict[str, Any], runtime: Any) -> Any:
            msgs = list(state.get("messages") or [])
            count = sum(1 for m in msgs if isinstance(m, ToolMessage))
            available_tools = (
                [reply_to_user_tool] if count >= _MAX_TOOL_CALLS else tools
            )
            model = get_llm("qa").bind_tools(available_tools, tool_choice="required")
            return with_usage_logging(
                model,
                node="qa_llm",
                thread_id=thread_id,
                user_id=user_id,
                provider="deepseek",
            )

        def _pre_model_hook(state: Dict[str, Any]) -> Dict[str, Any]:
            msgs = list(state.get("messages") or [])
            count = sum(1 for m in msgs if isinstance(m, ToolMessage))
            if count < _MAX_TOOL_CALLS:
                return {}
            from langchain_core.messages import SystemMessage

            return {
                "llm_input_messages": [
                    *msgs,
                    SystemMessage(
                        content=(
                            f"已达到工具调用上限（{count} 次），不要再尝试调用任何检索工具。"
                            "如果已有检索结果中包含相关信息，就用检索结果回答。"
                            "如果检索结果为空或不相关，直接告知用户未找到相关政策信息，请用户提供更具体的问题。"
                        )
                    ),
                ]
            }

        return create_react_agent(
            model=_model_fn,
            tools=tools,
            prompt=base_prompt,
            pre_model_hook=_pre_model_hook,
            checkpointer=None,
        )

    async def _execute(self, input: AgentInput) -> AgentOutput:
        base_prompt = await build_qa_system_prompt(
            user_context=input.user_context or {},
        )

        executor = self._get_executor(base_prompt, input.thread_id, input.user_id)

        logger.info("[qa_agent] thread_id=%s invoke_agent", input.thread_id)

        initial_messages = (
            list(input.messages)
            if input.messages
            else [HumanMessage(content=input.user_query)]
        )
        agent_result = await asyncio.wait_for(
            executor.ainvoke(
                {"messages": initial_messages},
                {"recursion_limit": _MAX_GRAPH_STEPS},
            ),
            timeout=_QA_TIMEOUT_SECONDS,
        )

        result_messages = list((agent_result or {}).get("messages") or [])
        reply = _extract_reply_result(result_messages)
        if not reply:
            reply = _extract_direct_ai_reply(result_messages)
        if not reply:
            reply = _FALLBACK_REPLY

        logger.info(
            "[qa_agent] thread_id=%s completed reply_length=%s",
            input.thread_id,
            len(reply),
        )
        return AgentOutput(
            reply=reply,
            status=AgentStatus.SUCCESS,
        )


qa_agent = QAAgent()
