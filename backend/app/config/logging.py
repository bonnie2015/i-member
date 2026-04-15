import logging
import sys
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler
from app.config.config import settings

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def setup_logging():
    logging_level = getattr(logging, settings.log_level.upper())

    # 默认统一写入 backend/logs，避免因启动目录不同生成多个 logs 目录
    log_dir = Path(os.getenv("LOG_DIR", str(DEFAULT_LOG_DIR))).resolve()

    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)

    # 构建日志文件路径
    log_file = log_dir / "member_ops_agent.log"

    logging.basicConfig(
        level=logging_level,
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
            RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            ),
        ],
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# 自动初始化日志配置
setup_logging()
