from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 基础配置
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Redis配置
    redis_url: str

    # 业务 API 服务
    business_api_url: str

    # Ollama 配置
    ollama_base_url: str
    ollama_timeout: float = 60.0

    # DeepSeek 配置
    deepseek_api_key: Optional[str] = None

    # Router LLM 提供方：local=Ollama本地, remote=DeepSeek远端
    router_provider: Literal["local", "remote"] = "remote"

    # Qdrant 配置
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "brand_knowledge"

    # LlamaParse 配置
    llama_cloud_api_key: Optional[str] = None

    # JWT 鉴权
    jwt_secret_key: str = "change-me-in-production"
    jwt_issuer: str = "member-ops-agent"
    jwt_clock_skew_seconds: int = 30

    # Langfuse 可观测性
    langfuse_secret_key: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_base_url: Optional[str] = None

    # Workflow debug switches
    skip_recommend_post_process: bool = True


settings = Settings()
