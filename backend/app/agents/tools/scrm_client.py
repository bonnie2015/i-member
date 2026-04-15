import time
from contextvars import ContextVar
from typing import Any, Dict, Optional

import httpx
from redis.asyncio import Redis as AsyncRedis

from app.config.config import settings
from app.config.logging import get_logger

REQUEST_ACCESS_TOKEN_CTX: ContextVar[Optional[str]] = ContextVar("access_token", default=None)
REQUEST_USER_ID_CTX: ContextVar[Optional[str]] = ContextVar("user_id", default=None)

logger = get_logger("scrm_client")
_SCRM_RATE_LIMIT_PER_MIN = 30


def _build_base_url() -> str:
    return settings.scrm_base_url.rstrip("/")


def _build_api_prefix() -> str:
    prefix = str(settings.scrm_api_prefix or "").strip()
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"
    return prefix.rstrip("/")


def _build_headers() -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}

    access_token = REQUEST_ACCESS_TOKEN_CTX.get()
    if not access_token:
        raise PermissionError("missing access token in request context")

    headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _rate_limit_key(user_id: str, now_s: Optional[int] = None) -> str:
    timestamp = int(now_s or time.time())
    minute_bucket = timestamp // 60
    return f"scrm:rate_limit:{user_id}:{minute_bucket}"


async def _check_rate_limit() -> None:
    limit = max(int(_SCRM_RATE_LIMIT_PER_MIN), 0)
    if limit <= 0:
        return

    user_id = str(REQUEST_USER_ID_CTX.get() or "").strip()
    if not user_id:
        logger.warning("[scrm_client] missing user_id in request context, skip rate limit")
        return

    client = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    key = _rate_limit_key(user_id)
    try:
        current = await client.incr(key)
        if current == 1:
            await client.expire(key, 90)
        if current > limit:
            raise httpx.HTTPStatusError(
                message=f"SCRM rate limit exceeded for user_id={user_id}",
                request=httpx.Request("RATE_LIMIT", "redis://scrm-rate-limit"),
                response=httpx.Response(429, request=httpx.Request("RATE_LIMIT", "redis://scrm-rate-limit")),
            )
    finally:
        await client.aclose()


async def call_scrm_endpoint(
    method: str,
    path: str,
    *,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{_build_base_url()}{_build_api_prefix()}{normalized_path}"
    headers = _build_headers()
    await _check_rate_limit()
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.request(
            method=method.upper(),
            url=url,
            params=query or None,
            json=body or None,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
