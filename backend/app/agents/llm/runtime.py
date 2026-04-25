from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping, Sequence

from app.config.logging import get_logger

logger = get_logger("llm_runtime")


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def _resolve_message_metrics(messages: Sequence[Any]) -> dict[str, int]:
    input_characters = 0
    for message in messages:
        input_characters += len(_extract_text_content(getattr(message, "content", "")))
    return {
        "message_count": len(messages),
        "input_characters": input_characters,
    }


def _serialize_messages(messages: Sequence[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for message in messages:
        payload: dict[str, Any] = {
            "type": getattr(message, "type", message.__class__.__name__),
            "content": _extract_text_content(getattr(message, "content", "")),
        }
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            payload["tool_calls"] = tool_calls
        name = str(getattr(message, "name", "") or "").strip()
        if name:
            payload["name"] = name
        serialized.append(payload)
    return serialized


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, type):
        return f"{value.__module__}.{value.__name__}"
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump())
        except Exception:
            return repr(value)
    return repr(value)


def _unwrap_llm_and_kwargs(llm: Any) -> tuple[Any, dict[str, Any], str]:
    if hasattr(llm, "bound") and hasattr(llm, "kwargs"):
        return llm.bound, dict(getattr(llm, "kwargs", {}) or {}), llm.__class__.__name__

    first = getattr(llm, "first", None)
    if first is not None and hasattr(first, "bound") and hasattr(first, "kwargs"):
        return first.bound, dict(getattr(first, "kwargs", {}) or {}), llm.__class__.__name__

    return llm, {}, llm.__class__.__name__


def _build_native_payload(llm: Any, messages: Sequence[Any]) -> dict[str, Any]:
    base_llm, bound_kwargs, wrapper_name = _unwrap_llm_and_kwargs(llm)
    base_payload = {
        "wrapper": wrapper_name,
        "base_model_class": base_llm.__class__.__name__,
        "bound_kwargs": _json_safe(bound_kwargs),
    }

    try:
        if hasattr(base_llm, "_get_request_payload"):
            payload = base_llm._get_request_payload(list(messages), **bound_kwargs)
            return {
                **base_payload,
                "payload_kind": "chat_completions",
                "payload": _json_safe(payload),
            }
        if hasattr(base_llm, "_chat_params"):
            payload = base_llm._chat_params(list(messages), **bound_kwargs)
            return {
                **base_payload,
                "payload_kind": "ollama_chat",
                "payload": _json_safe(payload),
            }
    except Exception as exc:
        return {
            **base_payload,
            "payload_kind": "unavailable",
            "error": f"{exc.__class__.__name__}: {exc}",
        }

    return {
        **base_payload,
        "payload_kind": "unsupported",
    }


def _resolve_model_name(llm: Any) -> str:
    for field_name in ("model_name", "model"):
        value = str(getattr(llm, field_name, "") or "").strip()
        if value:
            return value
    return llm.__class__.__name__


def extract_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, Mapping):
        prompt_tokens = _coerce_int(usage.get("input_tokens"))
        completion_tokens = _coerce_int(usage.get("output_tokens"))
        total_tokens = _coerce_int(usage.get("total_tokens")) or (prompt_tokens + completion_tokens)
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
            total_tokens = _coerce_int(usage_block.get("total_tokens")) or (prompt_tokens + completion_tokens)
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
    metrics = _resolve_message_metrics(messages)
    log_base = {
        "node": node,
        "provider": provider,
        "model": _resolve_model_name(llm),
        "thread_id": str(thread_id or "").strip() or "unknown",
        "user_id": str(user_id or "").strip() or "unknown",
        **metrics,
    }
    logger.info("[llm_runtime] %s", json.dumps({"event": "llm_call_start", **log_base}, ensure_ascii=False))
    logger.info(
        "[llm_runtime] %s",
        json.dumps(
            {
                "event": "llm_call_payload",
                **log_base,
                "messages": _serialize_messages(messages),
            },
            ensure_ascii=False,
        ),
    )
    logger.info(
        "[llm_runtime] %s",
        json.dumps(
            {
                "event": "llm_call_native_payload",
                **log_base,
                **_build_native_payload(llm, messages),
            },
            ensure_ascii=False,
        ),
    )

    started = time.perf_counter()
    try:
        invoke_coro = llm.ainvoke(list(messages))
        response = await asyncio.wait_for(invoke_coro, timeout=timeout_seconds) if timeout_seconds else await invoke_coro
        usage = extract_usage(response)
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "[llm_runtime] %s",
            json.dumps(
                {
                    "event": "llm_call_end",
                    **log_base,
                    "success": True,
                    "latency_ms": latency_ms,
                    **usage,
                },
                ensure_ascii=False,
            ),
        )
        return response, usage
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "[llm_runtime] %s",
            json.dumps(
                {
                    "event": "llm_call_end",
                    **log_base,
                    "success": False,
                    "latency_ms": latency_ms,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
        )
        raise
