from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, Optional

REQUEST_ACCESS_TOKEN_CTX: ContextVar[Optional[str]] = ContextVar("access_token", default=None)
REQUEST_USER_ID_CTX: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
REQUEST_THREAD_ID_CTX: ContextVar[Optional[str]] = ContextVar("thread_id", default=None)


@contextmanager
def business_execution_context(*, thread_id: str | None = None, user_id: str | None = None) -> Iterator[None]:
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
