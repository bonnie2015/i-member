from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List

from langgraph.errors import GraphInterrupt, GraphRecursionError
from pydantic import BaseModel, Field



class AgentStatus(str, Enum):
    SUCCESS = "success"
    CONTINUE = "continue"  # 子图未结束，等待下轮用户消息
    TIMEOUT = "timeout"
    RECURSION_LIMIT = "recursion_limit"
    PARSING_ERROR = "parsing_error"
    TOOL_ERROR = "tool_error"
    FAILED = "failed"


class AgentInput(BaseModel):
    user_query: str = ""
    user_context: Dict[str, Any] = Field(default_factory=dict)
    thread_id: str = "unknown"
    user_id: str | None = None
    extra: Dict[str, Any] = Field(default_factory=dict)
    messages: List[Any] = Field(default_factory=list)


class AgentOutput(BaseModel):
    reply: str = ""
    status: AgentStatus = AgentStatus.SUCCESS
    data: Dict[str, Any] = Field(default_factory=dict)
    error_detail: str = ""


class AgentConfig(BaseModel):
    name: str
    role: str
    timeout_seconds: int = 60
    max_recursion: int = 16
    max_tool_calls: int = 3
    fallback_reply: str = "抱歉，我暂时无法处理，请稍后再试。"


class BaseAgent(ABC):
    """Agent 基类。

    统一契约：run() 总返回 AgentOutput，不抛异常。
    超时、递归上限、异常 → 自动降级为 fallback。
    子类实现 _execute()，返回 AgentOutput。
    """

    def __init__(self, config: AgentConfig):
        self.config = config

    @abstractmethod
    async def _execute(self, input: AgentInput) -> AgentOutput:
        """子类实现。调 LLM + 工具，返回 AgentOutput。"""
        ...

    async def run(self, input: AgentInput) -> AgentOutput:
        try:
            return await asyncio.wait_for(
                self._execute(input),
                timeout=self.config.timeout_seconds,
            )
        except GraphInterrupt:
            raise
        except asyncio.TimeoutError:
            return AgentOutput(
                reply=self.config.fallback_reply,
                status=AgentStatus.TIMEOUT,
                error_detail="timeout",
            )
        except GraphRecursionError:
            return AgentOutput(
                reply=self.config.fallback_reply,
                status=AgentStatus.RECURSION_LIMIT,
                error_detail="recursion_limit",
            )
        except Exception as exc:
            return AgentOutput(
                reply=self.config.fallback_reply,
                status=AgentStatus.FAILED,
                error_detail=str(exc),
            )
