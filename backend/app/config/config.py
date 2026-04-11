from typing import Literal, Optional, Dict, List
from pydantic_settings import BaseSettings
from pydantic import BaseModel
from pathlib import Path
import json

class Settings(BaseSettings):
    # 基础配置
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Redis配置
    redis_url: str

    # Ollama 配置
    ollama_base_url: str
    ollama_timeout: float = 60.0

    # DeepSeek 配置
    deepseek_api_key: Optional[str] = None

    # SCRM 接口配置
    scrm_base_url: str = "http://bonnie-local.com"
    scrm_access_token: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()
