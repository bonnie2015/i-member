from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional

from app.config.logging import get_logger

_logger = get_logger("execution_context")

REQUEST_ACCESS_TOKEN_CTX: ContextVar[Optional[str]] = ContextVar(
    "access_token", default=None
)
REQUEST_USER_ID_CTX: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
REQUEST_THREAD_ID_CTX: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)

# Module-level dict 替代 ContextVar，绕过 asyncio.create_task 的 ContextVar 隔离
# key 为 thread_id，保证不同请求之间互不干扰
_interaction_sources_by_thread: Dict[str, list[Dict[str, Any]]] = {}


@contextmanager
def business_execution_context(
    *, thread_id: str | None = None, user_id: str | None = None
) -> Iterator[None]:
    thread_token = REQUEST_THREAD_ID_CTX.set(str(thread_id or "").strip() or None)
    user_token = REQUEST_USER_ID_CTX.set(str(user_id or "").strip() or None)
    try:
        yield
    finally:
        REQUEST_THREAD_ID_CTX.reset(thread_token)
        REQUEST_USER_ID_CTX.reset(user_token)


def get_business_execution_context() -> Dict[str, str]:
    return {
        "thread_id": str(REQUEST_THREAD_ID_CTX.get() or "").strip() or "unknown",
        "user_id": str(REQUEST_USER_ID_CTX.get() or "").strip() or "unknown",
    }


@contextmanager
def ticket_interaction_source_context(
    *, sources: list[Dict[str, Any]] | None = None
) -> Iterator[None]:
    """初始化并清理当前 thread 的交互源数据。"""
    thread_id = get_business_execution_context().get("thread_id", "unknown")
    if sources:
        _interaction_sources_by_thread[thread_id] = [
            item for item in sources if isinstance(item, dict)
        ]
    else:
        _interaction_sources_by_thread[thread_id] = []
    try:
        yield
    finally:
        _interaction_sources_by_thread.pop(thread_id, None)


def get_ticket_interaction_sources() -> list[Dict[str, Any]]:
    thread_id = get_business_execution_context().get("thread_id", "unknown")
    return list(_interaction_sources_by_thread.get(thread_id, []))


def push_ticket_interaction_source(source: Dict[str, Any]) -> None:
    """将工具结果推送至交互源，供 ask_user 消费时自动拼装交互卡片。

    由工具统一入口在成功返回后调用，不暴露给 LLM。
    使用 module-level dict 存储（key=thread_id），绕过 ContextVar 的 async task 隔离。
    """
    if not isinstance(source, dict) or not source:
        return
    thread_id = get_business_execution_context().get("thread_id", "unknown")
    if thread_id not in _interaction_sources_by_thread:
        _interaction_sources_by_thread[thread_id] = []
    _interaction_sources_by_thread[thread_id].append(dict(source))
