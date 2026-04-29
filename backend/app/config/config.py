from typing import Literal, Optional, Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel
from pathlib import Path
import json

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

    # SCRM 服务
    scrm_url: str = "http://scrm:3658"

    # Ollama 配置
    ollama_base_url: str
    ollama_timeout: float = 60.0

    # DeepSeek 配置
    deepseek_api_key: Optional[str] = None

    # JWT 鉴权
    jwt_secret_key: str = "change-me-in-production"
    jwt_issuer: str = "member-ops-agent"
    jwt_clock_skew_seconds: int = 30

    # Workflow debug switches
    skip_recommend_post_process: bool = True


settings = Settings()
