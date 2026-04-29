from functools import lru_cache
from typing import Literal
from langchain_core.language_models.chat_models import BaseChatModel
from app.config.config import settings
from app.config.logging import get_logger

logger = get_logger("llm_factory")

_LOCAL_MODELS = {
    "router": "qwen2.5:3b",
    "profile": "qwen2.5:3b",
    "reply": "qwen2.5:3b",
}
_SUBGRAPH_MODEL = "deepseek-chat"


@lru_cache(maxsize=4)
def get_local_llm(
    role: Literal["router", "profile", "reply"] = "router",
) -> BaseChatModel:

    from langchain_ollama import ChatOllama
    model_name = _LOCAL_MODELS.get(role, _LOCAL_MODELS["router"])

    kwargs = dict(
        model=model_name,
        base_url=settings.ollama_base_url,
        temperature=0,          # 路由判断不需要随机性
        timeout=settings.ollama_timeout,
    )
    llm = ChatOllama(**kwargs)
    logger.info(f"Local LLM ready: model={model_name}, role={role}")
    return llm


@lru_cache(maxsize=4)
def get_remote_llm(
    role: Literal["ticket", "qa", "recommend", "postprocess"] = "qa",
) -> BaseChatModel:

    from langchain_deepseek import ChatDeepSeek

    if not settings.deepseek_api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set. "
            "Add it to .env or environment variables."
        )

    llm = ChatDeepSeek(
        model=_SUBGRAPH_MODEL,
        api_key=settings.deepseek_api_key,
        temperature=0.3,
        timeout=60.0,
    )
    logger.info(f"Remote LLM ready: model={_SUBGRAPH_MODEL}, role={role}")
    return llm
