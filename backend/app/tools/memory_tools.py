from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.memory.service_memory import (
    load_recent_service_memories_limited,
)
from app.tools.business.execution_context import REQUEST_THREAD_ID_CTX, REQUEST_USER_ID_CTX
from app.config.logging import get_logger

logger = get_logger("service_memory_tools")

_MAX_SERVICE_MEMORY_LIMIT = 5


class GetServiceMemoriesInput(BaseModel):
    limit: int = Field(default=1, ge=1, le=_MAX_SERVICE_MEMORY_LIMIT, description="查询最近几条服务记忆，默认 1，最大 5。")
    intent: Optional[Literal["ticket", "recommend", "qa"]] = Field(
        default=None,
        description="可选意图过滤；不传则查询全部类型。支持 ticket、recommend、qa。",
    )
    include_payload: bool = Field(
        default=False,
        description="是否返回完整 payload。默认 false，只返回 summary 和基础元信息；确实需要完整数据时才设为 true。",
    )


def _current_identity() -> tuple[str, str] | None:
    user_id = str(REQUEST_USER_ID_CTX.get() or "").strip()
    thread_id = str(REQUEST_THREAD_ID_CTX.get() or "").strip()
    if not user_id or not thread_id:
        return None
    return user_id, thread_id


def _missing_context_error() -> Dict[str, Any]:
    return {
        "error": "missing user_id or thread_id in request context",
        "error_code": "SERVICE_MEMORY_CONTEXT_MISSING",
    }


def _not_found_error(kind: str) -> Dict[str, Any]:
    return {
        "error": f"{kind} not found",
        "error_code": "SERVICE_MEMORY_NOT_FOUND",
    }


def _compact_service_memory(item: Dict[str, Any], *, include_payload: bool = False) -> Dict[str, Any]:
    compacted = {
        "service_id": item.get("service_id"),
        "intent": item.get("intent"),
        "thread_id": item.get("thread_id"),
        "started_at": item.get("started_at"),
        "ended_at": item.get("ended_at"),
        "summary": item.get("summary") or "",
    }
    if include_payload:
        compacted["payload"] = item.get("payload") or {}
    return {key: value for key, value in compacted.items() if value not in (None, "") or key == "summary"}


@tool("get_service_memories", args_schema=GetServiceMemoriesInput)
async def get_service_memories_tool(
    limit: int = 1,
    intent: Optional[Literal["ticket", "recommend", "qa"]] = None,
    include_payload: bool = False,
) -> Dict[str, Any]:
    """查询当前线程最近 N 条服务记忆。

    仅保留最近几次服务的摘要记忆。历史的、已过期的服务记忆不在此工具范围内；
    如果用户引用了历史服务信息但工具未返回相关记录，说明该记忆已过期，需要询问用户获取信息。

    仅在现有信息无法确定用户具体要求，或缺少与历史服务有关的数据，判断非常有必要对历史服务记录进行查找时，才调用此工具。

    参数:
        limit: 查询最近几条服务记忆，默认 1，最大 5
        intent: 可选意图过滤，支持 ticket、recommend、qa
        include_payload: 是否返回完整 payload，默认 false

    返回:
        items: 服务记忆列表
        count: 返回条数
    """
    identity = _current_identity()
    if identity is None:
        return _missing_context_error()

    user_id, thread_id = identity
    try:
        items = await load_recent_service_memories_limited(
            user_id,
            thread_id,
            limit=limit,
            intent=intent,
        )
        if not items:
            return _not_found_error("service memory")
        return {
            "items": [_compact_service_memory(item, include_payload=include_payload) for item in items],
            "count": len(items),
            "include_payload": include_payload,
        }
    except Exception as e:
        logger.warning("[service_memory_tools] load recent service memories failed: %s", e)
        return {
            "error": str(e),
            "error_code": "SERVICE_MEMORY_LOAD_FAILED",
        }


TOOLS: List[BaseTool] = []


def get_memory_tools() -> List[BaseTool]:
    return TOOLS
