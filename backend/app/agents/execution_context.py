from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_thread_id: ContextVar[str] = ContextVar("agent_thread_id", default="")
_user_id: ContextVar[str] = ContextVar("agent_user_id", default="")


@contextmanager
def agent_execution_context(*, thread_id: str | None = None, user_id: str | None = None) -> Iterator[None]:
    thread_token = _thread_id.set(str(thread_id or "").strip())
    user_token = _user_id.set(str(user_id or "").strip())
    try:
        yield
    finally:
        _thread_id.reset(thread_token)
        _user_id.reset(user_token)


def get_execution_context() -> dict[str, str]:
    return {
        "thread_id": _thread_id.get() or "unknown",
        "user_id": _user_id.get() or "unknown",
    }
