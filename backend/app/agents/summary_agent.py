"""
SummaryAgent — 摘要类任务统一入口。

提供 QA 对话压缩、服务摘要、RAG 上下文压缩、用户画像摘要四个操作，
共享 LLM 生命周期（超时/兜底/日志）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.llm.llm_factory import get_llm
from app.llm.runtime import invoke_with_usage_logging
from app.config.logging import get_logger

logger = get_logger("summary_agent")

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "summary"


@lru_cache(maxsize=1)
def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


class _SummaryOutput(BaseModel):
    summary: str = ""


class SummaryAgent:
    """摘要 / 压缩类 LLM 操作统一入口。

    不继承 BaseAgent（多入口而非单 _execute），
    但共享统一的超时、兜底、日志模式。
    """

    def __init__(
        self,
        *,
        compress_timeout: int = 20,
        service_timeout: int = 30,
        rag_timeout: int = 20,
        profile_timeout: int = 15,
    ):
        self._compress_timeout = compress_timeout
        self._service_timeout = service_timeout
        self._rag_timeout = rag_timeout
        self._profile_timeout = profile_timeout

    # ---- 内部工具 ----

    @staticmethod
    def _serialize_messages(messages: List[BaseMessage]) -> list[dict[str, str]]:
        payload: list[dict[str, str]] = []
        for msg in messages:
            content = str(getattr(msg, "content", "") or "").strip()
            if not content:
                continue
            payload.append(
                {
                    "role": getattr(msg, "type", msg.__class__.__name__),
                    "content": content,
                }
            )
        return payload

    async def _invoke_llm(
        self,
        *,
        llm: Any,
        messages: List[Any],
        node: str,
        thread_id: str,
        user_id: Optional[str],
        provider: str,
        timeout_seconds: int,
    ) -> Optional[Any]:
        """统一的 LLM 调用，失败时返回 None。"""
        try:
            response, _ = await invoke_with_usage_logging(
                llm=llm,
                messages=messages,
                node=node,
                thread_id=thread_id,
                user_id=user_id,
                provider=provider,
                timeout_seconds=timeout_seconds,
            )
            return response
        except Exception as exc:
            logger.warning(
                "[summary] node=%s thread_id=%s failed: %s", node, thread_id, exc
            )
            return None

    # ---- QA 对话在线压缩 ----

    async def compress_qa(
        self,
        *,
        old_messages: List[BaseMessage],
        existing_summary: str = "",
        thread_id: str = "unknown",
        user_id: Optional[str] = None,
    ) -> str:
        """将旧消息 + 已有摘要合并压缩为简短摘要。"""
        if not old_messages and not existing_summary:
            return ""

        parts: List[str] = []
        if existing_summary:
            parts.append(f"【已有历史摘要】\n{existing_summary}")

        message_lines: List[str] = []
        for msg in old_messages:
            role = "用户" if isinstance(msg, HumanMessage) else "客服"
            content = str(getattr(msg, "content", "") or "").strip()
            if content:
                message_lines.append(f"{role}: {content}")
        if message_lines:
            parts.append("【待压缩的旧对话】\n" + "\n".join(message_lines))

        if not parts:
            return existing_summary

        logger.info(
            "[summary] thread_id=%s compress old_turns=%s", thread_id, len(old_messages)
        )

        response = await self._invoke_llm(
            llm=get_llm("summary"),
            messages=[
                SystemMessage(content=_load_prompt("compress.txt")),
                HumanMessage(content="\n\n".join(parts)),
            ],
            node="qa_summary_compress",
            thread_id=thread_id,
            user_id=user_id,
            provider="ollama",
            timeout_seconds=self._compress_timeout,
        )

        if response:
            summary = str(getattr(response, "content", "") or "").strip()
            if summary:
                logger.info(
                    "[summary] thread_id=%s compressed length=%s",
                    thread_id,
                    len(summary),
                )
                return summary

        return existing_summary

    # ---- 服务结束总结 ----

    async def summarize_service(
        self,
        messages: List[BaseMessage],
        intent: str,
        *,
        thread_id: str = "unknown",
        user_id: Optional[str] = None,
    ) -> str:
        """从 messages 生成一次服务摘要。intent 决定摘要侧重。"""
        if not messages:
            return f"{intent} 服务已结束。"

        prompt = _load_prompt("service.txt").format(intent=intent)
        llm = get_llm("summary").with_structured_output(_SummaryOutput)

        logger.info("[summary] thread_id=%s intent=%s", thread_id, intent)

        response = await self._invoke_llm(
            llm=llm,
            messages=[
                SystemMessage(content=prompt),
                HumanMessage(
                    content=json.dumps(
                        {"messages": self._serialize_messages(messages)},
                        ensure_ascii=False,
                    )
                ),
            ],
            node="service_summary",
            thread_id=thread_id,
            user_id=user_id,
            provider="ollama",
            timeout_seconds=self._service_timeout,
        )

        if response:
            summary = str(getattr(response, "summary", "") or "").strip()
            if summary:
                return summary

        return f"{intent} 服务已结束。"

    # ---- RAG 上下文即时压缩 ----

    async def compress_rag(
        self,
        content: str,
        *,
        thread_id: str = "unknown",
        user_id: Optional[str] = None,
    ) -> str:
        """将过长 RAG 检索结果即时压缩，保留关键事实、规则、数字。"""
        if not content:
            return ""

        logger.info(
            "[rag_context] thread_id=%s original_length=%s", thread_id, len(content)
        )

        response = await self._invoke_llm(
            llm=get_llm("summary"),
            messages=[
                SystemMessage(content=_load_prompt("rag_context.txt")),
                HumanMessage(content=content),
            ],
            node="rag_context_compress",
            thread_id=thread_id,
            user_id=user_id,
            provider="ollama",
            timeout_seconds=self._rag_timeout,
        )

        if response:
            compressed = str(getattr(response, "content", "") or "").strip()
            if compressed:
                logger.info(
                    "[rag_context] thread_id=%s compressed_length=%s",
                    thread_id,
                    len(compressed),
                )
                return compressed

        return content

    # ---- 用户画像总结 ----

    async def summarize_profile(
        self,
        profile_data: dict,
        *,
        thread_id: str = "unknown",
        user_id: Optional[str] = None,
    ) -> str:
        """从原始用户画像 JSON 生成 1-3 句中文摘要。"""
        if not profile_data:
            return "暂无用户画像信息"

        prompt = _load_prompt("profile.txt")
        llm = get_llm("summary").with_structured_output(_SummaryOutput)

        logger.info("[summary] thread_id=%s profile", thread_id)

        response = await self._invoke_llm(
            llm=llm,
            messages=[
                SystemMessage(content=prompt),
                HumanMessage(
                    content=json.dumps(profile_data, ensure_ascii=False, indent=2)
                ),
            ],
            node="user_profile_summary",
            thread_id=thread_id,
            user_id=user_id,
            provider="ollama",
            timeout_seconds=self._profile_timeout,
        )

        if response:
            summary = str(getattr(response, "summary", "") or "").strip()
            if summary:
                return summary

        # fallback: 从原始数据拼
        parts = []
        for key in (
            "name",
            "member_level",
            "value_segment",
            "preferences",
            "behavior_summary",
        ):
            value = profile_data.get(key)
            text = str(value or "").strip()
            if text:
                parts.append(f"{key}={text}")
        return "；".join(parts[:6]) if parts else "暂无用户画像信息"


summary_agent = SummaryAgent()
