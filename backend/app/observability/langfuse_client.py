"""
Langfuse 可观测性客户端。

职责：
- 懒初始化 Langfuse CallbackHandler（仅在密钥配置后才创建）
- 提供全局单例，所有 LLM 调用和 Graph 节点共享同一个 handler
- 支持自建实例（LANGFUSE_HOST）和 cloud 版本

用法：
    from app.observability.langfuse_client import get_langfuse_handler

    handler = get_langfuse_handler()
    if handler:
        config["callbacks"] = [handler]
        config["metadata"] = {
            "langfuse_session_id": thread_id,
            "langfuse_user_id": user_id,
        }
"""

from __future__ import annotations

import os

from app.config.config import settings
from app.config.logging import get_logger

logger = get_logger("langfuse_client")

# 标记是否已尝试初始化（用于打印一次日志）
_initialized = False


def _build_callback_handler():
    """
    构建 Langfuse CallbackHandler。
    返回 None 表示未配置密钥，Langfuse 不启用。
    """
    secret_key = (settings.langfuse_secret_key or "").strip()
    public_key = (settings.langfuse_public_key or "").strip()
    host = (settings.langfuse_base_url or "https://cloud.langfuse.com").strip()

    if not secret_key or not public_key:
        return None

    from langfuse.langchain import CallbackHandler

    # SDK 通过环境变量读取配置，显式设置确保优先级
    os.environ["LANGFUSE_SECRET_KEY"] = secret_key
    os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    os.environ["LANGFUSE_HOST"] = host
    os.environ["LANGFUSE_BASE_URL"] = host

    handler = CallbackHandler()
    return handler


_HANDLER = _build_callback_handler()
_initialized = True

if _HANDLER:
    logger.info(
        "[langfuse_client] initialized base_url=%s",
        (settings.langfuse_base_url or "https://cloud.langfuse.com").strip(),
    )
else:
    logger.info(
        "[langfuse_client] not configured — LLM tracing disabled. "
        "Set LANGFUSE_SECRET_KEY + LANGFUSE_PUBLIC_KEY to enable."
    )


def get_langfuse_handler():
    """
    获取全局 Langfuse CallbackHandler 单例。

    Returns:
        CallbackHandler | None: 已初始化的 handler，未配置时返回 None
    """
    return _HANDLER


def langfuse_enabled() -> bool:
    """检查 Langfuse 是否已启用。"""
    return _HANDLER is not None
