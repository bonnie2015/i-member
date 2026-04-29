from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.agents.memory.service_memory import (
    load_last_service_memory,
    load_recent_service_memories_limited,
    load_service_messages_limited,
)
from app.agents.tools.business.execution_context import REQUEST_THREAD_ID_CTX, REQUEST_USER_ID_CTX
from app.config.logging import get_logger

logger = get_logger("service_memory_tools")

_MAX_SERVICE_MEMORY_LIMIT = 5
_MAX_SERVICE_MESSAGES_LIMIT = 50


class GetServiceMemoriesInput(BaseModel):
    limit: int = Field(default=1, ge=1, le=_MAX_SERVICE_MEMORY_LIMIT, description="查询最近几条服务记忆，默认 1，最大 5。")


class GetServiceMessagesInput(BaseModel):
    messages_ref: Optional[str] = Field(
        default=None,
        description="服务消息归档引用；为空时默认读取当前线程最近一次服务的原始消息。",
    )
    offset: int = Field(default=0, ge=0, description="消息起始偏移，默认 0。")
    limit: int = Field(default=20, ge=1, le=_MAX_SERVICE_MESSAGES_LIMIT, description="返回消息条数，默认 20，最大 50。")


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


@tool("get_service_memories", args_schema=GetServiceMemoriesInput)
async def get_service_memories_tool(limit: int = 1) -> Dict[str, Any]:
    """查询当前线程最近 N 条完整服务记忆。

    仅在现有信息无法确定用户具体要求，或缺少与历史服务有关的数据，判断非常有必要对历史服务记录进行查找时，才调用此工具。

    参数:
        limit: 查询最近几条服务记忆，默认 1，最大 5

    返回:
        items: 服务记忆列表
        count: 返回条数
    """
    identity = _current_identity()
    if identity is None:
        return _missing_context_error()

    user_id, thread_id = identity
    try:
        items = await load_recent_service_memories_limited(user_id, thread_id, limit=limit)
        if not items:
            return _not_found_error("service memory")
        return {
            "items": items,
            "count": len(items),
        }
    except Exception as e:
        logger.warning("[service_memory_tools] load recent service memories failed: %s", e)
        return {
            "error": str(e),
            "error_code": "SERVICE_MEMORY_LOAD_FAILED",
        }


@tool("get_service_messages", args_schema=GetServiceMessagesInput)
async def get_service_messages_tool(
    messages_ref: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> Dict[str, Any]:
    """查询指定服务或当前线程最近一次服务的原始消息，可按 offset/limit 裁剪。

    仅在现有信息无法确定用户具体要求，或缺少与历史服务有关的数据，判断非常有必要对历史服务记录进行查找时，才调用此工具。

    参数:
        messages_ref: 服务消息归档引用（可选），为空时默认读取当前线程最近一次服务的原始消息
        offset: 消息起始偏移，默认 0
        limit: 返回消息条数，默认 20，最大 50

    返回:
        messages_ref: 服务消息归档引用
        offset: 消息起始偏移
        limit: 返回消息条数
        count: 实际返回条数
        messages: 消息列表
    """
    identity = _current_identity()
    if identity is None:
        return _missing_context_error()

    user_id, thread_id = identity
    resolved_messages_ref = str(messages_ref or "").strip()
    try:
        if not resolved_messages_ref:
            service_memory = await load_last_service_memory(user_id, thread_id)
            if not isinstance(service_memory, dict) or not service_memory:
                return _not_found_error("service memory")
            resolved_messages_ref = str(service_memory.get("messages_ref") or "").strip()

        if not resolved_messages_ref:
            return _not_found_error("service messages")

        messages = await load_service_messages_limited(
            resolved_messages_ref,
            offset=offset,
            limit=limit,
        )
        if not messages:
            return _not_found_error("service messages")
        return {
            "messages_ref": resolved_messages_ref,
            "offset": offset,
            "limit": limit,
            "count": len(messages),
            "messages": messages,
        }
    except Exception as e:
        logger.warning("[service_memory_tools] load service messages failed: %s", e)
        return {
            "error": str(e),
            "error_code": "SERVICE_MESSAGES_LOAD_FAILED",
        }


TOOLS: List[BaseTool] = [
    get_service_memories_tool,
    get_service_messages_tool,
]


def get_service_memory_tools() -> List[BaseTool]:
    return TOOLS
