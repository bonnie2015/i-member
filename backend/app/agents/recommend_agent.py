from __future__ import annotations

import ast
import json
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent

from app.agents.base import AgentConfig, AgentInput, AgentOutput, AgentStatus, BaseAgent
from app.llm.llm_factory import get_llm
from app.llm.runtime import with_usage_logging
from app.prompts.prompt_builder import build_recommend_system_prompt
from app.tools import get_onitsuka_tools, onitsuka_get_product_detail, reply_with_products_tool
from app.tools.memory_tools import get_memory_tools
from app.tools.rag_tools import get_size_guide_tools
from app.config.logging import get_logger

logger = get_logger("recommend_agent")

_MAX_TOOL_BLOCKS = 2
_MAX_TOOL_CALLS = 3
_LANGGRAPH_RECURSION_REPLY = "Sorry, need more steps to process this request."
_HAS_PRODUCT_FALLBACK = "找到了这几款，看看有没有喜欢的？"
_NO_PRODUCT_FALLBACK = "暂时没有找到合适的商品，能不能提供更多信息让我帮你找找看？"


# ============================================================
# 纯函数：消息解析 & 结果提取
# ============================================================


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


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", tool.__class__.__name__) or "").strip()


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


def _normalize_tool_args(args: Any) -> Dict[str, Any]:
    return dict(args) if isinstance(args, dict) else {}


def _tool_call_args_by_id(messages: List[BaseMessage]) -> Dict[str, Dict[str, Any]]:
    calls_by_id: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for call in list(getattr(msg, "tool_calls", None) or []):
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


def _tool_message_name(msg: ToolMessage, calls_by_id: Dict[str, Dict[str, Any]]) -> str:
    direct = str(getattr(msg, "name", "") or "").strip()
    if direct:
        return direct
    additional = getattr(msg, "additional_kwargs", None)
    if isinstance(additional, dict):
        name = str(additional.get("name") or "").strip()
        if name:
            return name
    call_id = str(getattr(msg, "tool_call_id", "") or "").strip()
    return str((calls_by_id.get(call_id) or {}).get("name") or "").strip()


def _product_key(product: Dict[str, Any]) -> tuple[int, int | None] | None:
    try:
        pid = int(product.get("product_id") or 0)
    except Exception:
        pid = 0
    if not pid:
        return None
    try:
        cid = int(product.get("color_id") or product.get("default_color_id") or 0) or None
    except Exception:
        cid = None
    return pid, cid


def _find_product_by_ref(products: List[Dict[str, Any]], ref: Dict[str, Any]) -> Dict[str, Any] | None:
    ref_key = _product_key(ref)
    if ref_key is None:
        return None
    r_pid, r_cid = ref_key
    for p in products:
        pk = _product_key(p)
        if pk is None:
            continue
        pid, cid = pk
        if pid != r_pid:
            continue
        if r_cid is not None and cid != r_cid:
            continue
        return dict(p)
    return None


def _collect_tool_products(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    calls_by_id = _tool_call_args_by_id(messages)
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if _tool_message_name(msg, calls_by_id) == str(reply_with_products_tool.name):
            continue
        payload = _parse_payload(getattr(msg, "content", ""))
        if not isinstance(payload, dict):
            continue
        for item in list(payload.get("products") or []):
            if isinstance(item, dict):
                products.append(item)
        if payload.get("product_id") and not payload.get("products"):
            products.append(payload)
    return products


def _collect_anchor_products(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in list(ctx.get("anchor_products") or []) if isinstance(item, dict)]


def _resolve_selected_products(
    messages: List[BaseMessage],
    product_refs: List[Dict[str, Any]],
    recommend_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    resolved: List[Dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    tool_products = _collect_tool_products(messages)
    anchor_products = _collect_anchor_products(recommend_context)
    for ref in product_refs:
        product = _find_product_by_ref(tool_products, ref) or _find_product_by_ref(anchor_products, ref)
        if not product:
            continue
        key = _product_key(product)
        if key is None or key in seen:
            continue
        seen.add(key)
        resolved.append(product)
    return resolved


def _extract_candidate_product_refs(messages: List[BaseMessage], *, limit: int = 4) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    seen: set[tuple[int, int | None]] = set()
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        payload = _parse_payload(getattr(msg, "content", ""))
        if not isinstance(payload, dict):
            continue
        for item in list(payload.get("products") or []):
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("product_id") or 0)
            except Exception:
                pid = 0
            try:
                cid = int(item.get("color_id") or item.get("default_color_id") or 0) or None
            except Exception:
                cid = None
            if not pid:
                continue
            key = (pid, cid)
            if key in seen:
                continue
            seen.add(key)
            ref: Dict[str, Any] = {"product_id": pid}
            if cid:
                ref["color_id"] = cid
            refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


def _extract_reply_result(
    messages: List[BaseMessage],
    recommend_context: Dict[str, Any],
) -> Dict[str, Any] | None:
    calls_by_id = _tool_call_args_by_id(messages)
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        if _tool_message_name(msg, calls_by_id) != str(reply_with_products_tool.name):
            continue
        payload = _parse_payload(getattr(msg, "content", ""))
        if not isinstance(payload, dict):
            continue
        reply = str(payload.get("reply") or "").strip()
        refs = [item for item in list(payload.get("products") or []) if isinstance(item, dict)]
        products = _resolve_selected_products(messages, refs, recommend_context)
        if not reply and not products:
            continue
        return {"reply": reply, "products": products}
    return None


def _extract_direct_ai_reply(messages: List[BaseMessage]) -> str:
    for msg in reversed(messages):
        if not isinstance(msg, AIMessage):
            continue
        if list(getattr(msg, "tool_calls", None) or []):
            continue
        reply = _message_text(getattr(msg, "content", "")).strip()
        if not reply or reply == _LANGGRAPH_RECURSION_REPLY:
            continue
        return reply.split("\n\n[products]", 1)[0].strip()
    return ""



# ============================================================
# RecommendAgent
# ============================================================


class RecommendAgent(BaseAgent):

    def __init__(self, config: AgentConfig | None = None):
        if config is None:
            config = AgentConfig(
                name="recommend",
                role="recommend",
                timeout_seconds=70,
                max_recursion=12,
                max_tool_calls=_MAX_TOOL_CALLS,
                fallback_reply=_NO_PRODUCT_FALLBACK,
            )
        super().__init__(config)
        self._search_tools: List[Any] = []
        self._all_tools: List[Any] = []

    def _get_search_tools(self) -> List[Any]:
        if not self._search_tools:
            self._search_tools = [*get_onitsuka_tools()]
        return self._search_tools

    def _get_all_tools(self) -> List[Any]:
        if not self._all_tools:
            self._all_tools = [*self._get_search_tools(), onitsuka_get_product_detail, *get_size_guide_tools(), reply_with_products_tool]
        return self._all_tools

    @staticmethod
    def _tool_call_count(messages: List[BaseMessage]) -> int:
        return sum(1 for m in messages if isinstance(m, ToolMessage))

    @staticmethod
    def _compact_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
        blocks: List[List[BaseMessage]] = []
        idx = 0
        while idx < len(messages):
            msg = messages[idx]
            if not isinstance(msg, AIMessage):
                idx += 1
                continue
            tool_calls = list(getattr(msg, "tool_calls", None) or [])
            call_ids = {
                str(c.get("id") or "").strip()
                for c in tool_calls
                if isinstance(c, dict) and str(c.get("id") or "").strip()
            }
            if not call_ids:
                idx += 1
                continue
            block = [msg]
            matched: set[str] = set()
            nxt = idx + 1
            while nxt < len(messages) and isinstance(messages[nxt], ToolMessage):
                tcid = str(getattr(messages[nxt], "tool_call_id", "") or "").strip()
                if not tcid or tcid not in call_ids:
                    break
                block.append(messages[nxt])
                matched.add(tcid)
                nxt += 1
            if call_ids.issubset(matched):
                blocks.append(block)
            idx = nxt
        if not blocks:
            return messages
        return [m for b in blocks[-_MAX_TOOL_BLOCKS:] for m in b]

    @staticmethod
    def _tool_control_message(tool_count: int) -> SystemMessage | None:
        remaining = _MAX_TOOL_CALLS - tool_count
        if remaining == 1:
            return SystemMessage(content=(
                "【系统指令】你只剩最后一次工具调用了，必须在本次调用 reply_with_products 结束本轮。"
                "不要再搜索，立即调用 reply_with_products。"
            ))
        if remaining <= 0:
            return SystemMessage(content=(
                "【系统指令】搜索次数已用完！你必须立即调用 reply_with_products，禁止再调用任何搜索工具。"
            ))
        return SystemMessage(content=(
            f"【系统指令】本轮最多 {_MAX_TOOL_CALLS} 次工具调用，你已使用 {tool_count} 次，还需搜索的话尽快。"
        ))

    # ---- _execute ----

    async def _execute(self, input: AgentInput) -> AgentOutput:
        recommend_context = input.extra.get("recommend_context") or {}

        tools = [*get_memory_tools(), *self._get_all_tools()]

        prompt = await build_recommend_system_prompt(
            user_context=input.user_context,
            runtime_context=recommend_context if isinstance(recommend_context, dict) else {},
        )

        def _model_fn(agent_state: Dict[str, Any], _runtime: Any) -> Any:
            msgs = [
                m for m in list(agent_state.get("messages") or [])
                if isinstance(m, BaseMessage)
            ]
            count = self._tool_call_count(msgs)
            available = (
                [t for t in tools if _tool_name(t) == str(reply_with_products_tool.name)]
                if count >= self.config.max_tool_calls
                else list(tools)
            )
            logger.info(
                "[recommend_agent] thread_id=%s tool_count=%s/%s available=%s",
                input.thread_id,
                count,
                self.config.max_tool_calls,
                [_tool_name(t) for t in available],
            )
            # DEBUG: 输出 state 中每条消息的类型，确认计数是否正确
            _msg_types = [type(m).__name__ for m in msgs]
            _has_tool_calls = [
                len(list(getattr(m, "tool_calls", None) or []))
                for m in msgs if isinstance(m, AIMessage)
            ]
            logger.info(
                "[recommend_agent] thread_id=%s _model_fn msg_types=%s ai_tool_call_counts=%s",
                input.thread_id, _msg_types, _has_tool_calls,
            )
            model = get_llm("recommend").bind_tools(available, tool_choice="auto")
            return with_usage_logging(
                model,
                node="recommend_llm",
                thread_id=input.thread_id,
                user_id=input.user_id,
                provider="deepseek",
            )

        def _compact_model_input(agent_state: Dict[str, Any]) -> Dict[str, Any]:
            msgs = [
                m for m in list(agent_state.get("messages") or [])
                if isinstance(m, BaseMessage)
            ]
            state_count = self._tool_call_count(msgs)
            compacted = self._compact_messages(msgs)
            control = self._tool_control_message(state_count)
            if control is not None:
                compacted = [*compacted, control]
            # DEBUG: 看 compact 后实际发给模型的消息
            _compact_types = [type(m).__name__ for m in compacted]
            _compact_content_preview = [
                (type(m).__name__, str(getattr(m, "content", ""))[:80])
                for m in compacted
            ]
            logger.info(
                "[recommend_agent] thread_id=%s compact: state_msgs=%s state_tool_count=%s llm_input_count=%s types=%s",
                input.thread_id, len(msgs), state_count, len(compacted), _compact_types,
            )
            logger.info(
                "[recommend_agent] thread_id=%s llm_input_preview=%s",
                input.thread_id, _compact_content_preview,
            )
            return {"llm_input_messages": compacted}

        agent = create_react_agent(
            model=_model_fn,
            tools=tools,
            prompt=prompt,
            pre_model_hook=_compact_model_input,
            checkpointer=None,
        )

        logger.info(
            "[recommend_agent] thread_id=%s invoke tools=%s",
            input.thread_id,
            [_tool_name(t) for t in tools],
        )

        # 若 guard 失败（summary 为空），用最近 4 条非工具消息作为上下文
        summary = str(recommend_context.get("summary") or "").strip()
        if summary:
            init_messages: List[BaseMessage] = []
        else:
            logger.warning(
                "[recommend_agent] thread_id=%s guard_fallback: using last messages instead",
                input.thread_id,
            )
            init_messages = [
                m for m in (input.messages or [])[-4:]
                if isinstance(m, HumanMessage)
                or (isinstance(m, AIMessage) and not list(getattr(m, "tool_calls", None) or []))
            ]

        agent_result = await agent.ainvoke(
            {"messages": init_messages},
            {"recursion_limit": self.config.max_recursion},
        )

        messages = list((agent_result or {}).get("messages") or [])
        tool_results = len([m for m in messages if isinstance(m, ToolMessage)])
        # DEBUG: 最终消息结构
        _final_types = [(type(m).__name__, len(list(getattr(m, "tool_calls", None) or []))) for m in messages]
        _last_msg = messages[-1] if messages else None
        _last_has_tool_calls = bool(list(getattr(_last_msg, "tool_calls", None) or [])) if _last_msg else False
        logger.info(
            "[recommend_agent] thread_id=%s done messages=%s tool_results=%s",
            input.thread_id,
            len(messages),
            tool_results,
        )
        logger.info(
            "[recommend_agent] thread_id=%s final_msg_types=%s last_msg_type=%s last_has_tool_calls=%s",
            input.thread_id, _final_types,
            type(_last_msg).__name__ if _last_msg else "None",
            _last_has_tool_calls,
        )

        # 提取结果
        final = _extract_reply_result(messages, recommend_context)
        if final is not None:
            products = list(final.get("products") or [])
            reply = str(final.get("reply") or "").strip()
            if not reply or reply == _LANGGRAPH_RECURSION_REPLY:
                logger.warning(
                    "[recommend_agent] thread_id=%s fallback products=%s original_reply=%s",
                    input.thread_id, len(products), reply[:80],
                )
                reply = _HAS_PRODUCT_FALLBACK if products else _NO_PRODUCT_FALLBACK
            return AgentOutput(
                reply=reply,
                status=AgentStatus.CONTINUE,
                data={"products": products},
            )

        reply = _extract_direct_ai_reply(messages)
        products = _resolve_selected_products(
            messages, _extract_candidate_product_refs(messages), recommend_context
        )
        if not reply:
            logger.warning(
                "[recommend_agent] thread_id=%s fallback_direct products=%s",
                input.thread_id, len(products),
            )
            reply = _HAS_PRODUCT_FALLBACK if products else _NO_PRODUCT_FALLBACK
        return AgentOutput(
            reply=reply,
            status=AgentStatus.CONTINUE,
            data={"products": products},
        )


# 单例
recommend_agent = RecommendAgent()
