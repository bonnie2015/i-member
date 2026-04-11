import logging
import sys
import os
from logging.handlers import RotatingFileHandler
from app.config.config import settings

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logging():
    logging_level = getattr(logging, settings.log_level.upper())

    # 从环境变量获取日志目录，默认为当前目录下的 logs
    log_dir = os.getenv("LOG_DIR", "logs")
    
    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)

    # 构建日志文件路径
    log_file = os.path.join(log_dir, "member_ops_agent.log")

    logging.basicConfig(
        level=logging_level,
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
            RotatingFileHandler(
                log_file,
                maxBytes=10*1024*1024,
                backupCount=5,
                encoding='utf-8'
            )
        ]
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# 自动初始化日志配置
setup_logging()
