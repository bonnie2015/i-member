from functools import lru_cache
from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel

from app.config.config import settings
from app.config.logging import get_logger

logger = get_logger("llm_factory")

# ── 角色→提供方映射 ──────────────────────────────────
# 改这一处就能切换 local/remote，业务层无感知

_ROLE_PROVIDER: dict[str, Literal["local", "remote"]] = {
    "router": "remote",
    "summary": "local",
    "ticket_guard": "remote",
    "recommend_guard": "remote",
    "ticket": "remote",
    "qa": "remote",
    "recommend": "remote",
    "postprocess": "remote",
    "judge": "remote",
}

_LOCAL_MODEL_MAP = {
    "router": "qwen2.5:7b",
    "summary": "qwen2.5:7b",
    "ticket_guard": "qwen2.5:7b",
    "recommend_guard": "qwen2.5:7b",
}


def get_llm(role: str) -> BaseChatModel:
    """业务层唯一入口。根据 _ROLE_PROVIDER 自动选 local/remote。"""
    provider = _ROLE_PROVIDER.get(role, "remote")
    if provider == "local":
        return _get_local_llm(role)
    return _get_remote_llm(role)


@lru_cache(maxsize=4)
def _get_local_llm(role: str) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    model_name = _LOCAL_MODEL_MAP.get(role, _LOCAL_MODEL_MAP["summary"])
    kwargs = dict(
        model=model_name,
        base_url=settings.ollama_base_url,
        temperature=0,
        timeout=settings.ollama_timeout,
        keep_alive="2h",
    )
    llm = ChatOllama(**kwargs)
    logger.info("Local LLM ready: model=%s role=%s", model_name, role)
    return llm


@lru_cache(maxsize=8)
def _get_remote_llm(role: str) -> BaseChatModel:
    from langchain_deepseek import ChatDeepSeek

    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set.")

    model = "deepseek-chat"
    kwargs: dict = dict(
        model=model,
        api_key=settings.deepseek_api_key,
        timeout=90.0 if role == "judge" else 60.0,
    )
    if role != "judge":
        kwargs["temperature"] = 0.3
    llm = ChatDeepSeek(**kwargs)
    logger.info("Remote LLM ready: model=%s role=%s", model, role)
    return llm
