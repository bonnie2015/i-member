from __future__ import annotations

import asyncio
from typing import Any, Mapping, Sequence

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import RunnableConfig, RunnableLambda

from app.config.logging import get_logger

logger = get_logger("llm_runtime")


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_messages_input(value: Any) -> list[Any]:
    if isinstance(value, PromptValue):
        return list(value.to_messages())
    if isinstance(value, Mapping):
        messages = value.get("messages")
        if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
            return list(messages)
        return [HumanMessage(content=str(value or ""))]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    if isinstance(value, BaseMessage):
        return [value]
    return [HumanMessage(content=str(value or ""))]


def extract_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, Mapping):
        prompt_tokens = _coerce_int(usage.get("input_tokens"))
        completion_tokens = _coerce_int(usage.get("output_tokens"))
        total_tokens = _coerce_int(usage.get("total_tokens")) or (
            prompt_tokens + completion_tokens
        )
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    response_metadata = getattr(response, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        for key in ("token_usage", "usage"):
            usage_block = response_metadata.get(key)
            if not isinstance(usage_block, Mapping):
                continue
            prompt_tokens = _coerce_int(usage_block.get("prompt_tokens"))
            completion_tokens = _coerce_int(usage_block.get("completion_tokens"))
            total_tokens = _coerce_int(usage_block.get("total_tokens")) or (
                prompt_tokens + completion_tokens
            )
            return {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }

    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def extract_total_tokens(response: Any) -> int:
    return extract_usage(response).get("total_tokens", 0)


async def invoke_with_usage_logging(
    *,
    llm: Any,
    messages: Sequence[Any],
    node: str,
    thread_id: str | None = None,
    user_id: str | None = None,
    provider: str = "unknown",
    timeout_seconds: float | None = None,
) -> tuple[Any, dict[str, int]]:
    try:
        invoke_coro = llm.ainvoke(list(messages))
        response = (
            await asyncio.wait_for(invoke_coro, timeout=timeout_seconds)
            if timeout_seconds
            else await invoke_coro
        )
        usage = extract_usage(response)
        return response, usage
    except Exception as exc:
        logger.warning(
            "[llm_runtime] node=%s error=%s: %s",
            node,
            exc.__class__.__name__,
            exc,
        )
        raise


async def invoke_model_input_with_usage_logging(
    *,
    llm: Any,
    model_input: Any,
    node: str,
    thread_id: str | None = None,
    user_id: str | None = None,
    provider: str = "unknown",
    timeout_seconds: float | None = None,
    config: RunnableConfig | None = None,
) -> tuple[Any, dict[str, int]]:
    try:
        invoke_coro = llm.ainvoke(model_input, config=config)
        response = (
            await asyncio.wait_for(invoke_coro, timeout=timeout_seconds)
            if timeout_seconds
            else await invoke_coro
        )
        usage = extract_usage(response)
        return response, usage
    except Exception as exc:
        logger.warning(
            "[llm_runtime] node=%s error=%s: %s",
            node,
            exc.__class__.__name__,
            exc,
        )
        raise


def with_usage_logging(
    llm: Any,
    *,
    node: str,
    thread_id: str | None = None,
    user_id: str | None = None,
    provider: str = "unknown",
    timeout_seconds: float | None = None,
) -> RunnableLambda:
    async def _ainvoke(model_input: Any, config: RunnableConfig | None = None) -> Any:
        response, _ = await invoke_model_input_with_usage_logging(
            llm=llm,
            model_input=model_input,
            node=node,
            thread_id=thread_id,
            user_id=user_id,
            provider=provider,
            timeout_seconds=timeout_seconds,
            config=config,
        )
        return response

    def _invoke(_: Any, __: RunnableConfig | None = None) -> Any:
        raise RuntimeError("with_usage_logging only supports async invocation")

    return RunnableLambda(_invoke, afunc=_ainvoke, name=f"{node}_logged_model")


def estimate_tokens(text: str) -> int:
    """DeepSeek 官方换算率估算 token 数。中文字符≈0.6, 英文字符≈0.3。"""
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    en = len(text) - cn
    return int(cn * 0.6 + en * 0.3)
