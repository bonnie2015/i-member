import logging
import sys
import os
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo
from app.config.config import settings

_APP_TZ = ZoneInfo("Asia/Shanghai")
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-5s | %(tid)s | %(rid)s | %(name)s | %(message)s"
)
DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

log_thread_id: ContextVar[str] = ContextVar("log_thread_id", default="-")
log_request_id: ContextVar[str] = ContextVar("log_request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.tid = log_thread_id.get()
        record.rid = log_request_id.get()
        return True


class _ShanghaiFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=_APP_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S") + f",{int(record.msecs):03d}"


def setup_logging():
    logging_level = getattr(logging, settings.log_level.upper())

    log_dir = Path(os.getenv("LOG_DIR", str(DEFAULT_LOG_DIR))).resolve()

    os.makedirs(log_dir, exist_ok=True)

    log_file = log_dir / "member_ops_agent.log"

    context_filter = _ContextFilter()
    formatter = _ShanghaiFormatter(LOG_FORMAT)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.addFilter(context_filter)
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging_level,
        handlers=[stream_handler, file_handler],
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


setup_logging()
