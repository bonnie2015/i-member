import time
from typing import Any, Dict, Optional

import httpx

from app.config.config import settings
from app.config.logging import get_logger
from app.config.redis import get_redis_client
from app.config.redis_keys import SCRM_RATE_LIMIT_GLOBAL_KEY, SCRM_RATE_LIMIT_KEY
from app.tools.business.execution_context import (
    REQUEST_ACCESS_TOKEN_CTX,
    REQUEST_USER_ID_CTX,
)

logger = get_logger("scrm_client")
_SCRM_RATE_LIMIT_PER_MIN = 30
_SCRM_GLOBAL_LIMIT_PER_MIN = 300


def _build_base_url() -> str:
    return settings.scrm_url.rstrip("/")


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
    return SCRM_RATE_LIMIT_KEY.format(user_id=user_id, minute_bucket=minute_bucket)


async def _check_rate_limit() -> None:
    limit = max(int(_SCRM_RATE_LIMIT_PER_MIN), 0)
    if limit <= 0:
        return

    user_id = str(REQUEST_USER_ID_CTX.get() or "").strip()
    if not user_id:
        logger.warning(
            "[scrm_client] missing user_id in request context, skip rate limit"
        )
        return

    client = await get_redis_client()
    key = _rate_limit_key(user_id)
    current = await client.incr(key)
    if current == 1:
        await client.expire(key, 90)
    if current > limit:
        raise httpx.HTTPStatusError(
            message=f"SCRM rate limit exceeded for user_id={user_id}",
            request=httpx.Request("RATE_LIMIT", "redis://scrm-rate-limit"),
            response=httpx.Response(
                429, request=httpx.Request("RATE_LIMIT", "redis://scrm-rate-limit")
            ),
        )

    # 全局限制
    global_limit = _SCRM_GLOBAL_LIMIT_PER_MIN
    global_key = SCRM_RATE_LIMIT_GLOBAL_KEY.format(minute_bucket=int(time.time()) // 60)
    global_count = await client.incr(global_key)
    if global_count == 1:
        await client.expire(global_key, 90)
    if global_count > global_limit:
        raise httpx.HTTPStatusError(
            message="SCRM global rate limit exceeded",
            request=httpx.Request("RATE_LIMIT", "redis://scrm-rate-limit"),
            response=httpx.Response(
                429, request=httpx.Request("RATE_LIMIT", "redis://scrm-rate-limit")
            ),
        )


async def call_scrm_endpoint(
    method: str,
    path: str,
    *,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{_build_base_url()}{normalized_path}"
    headers = _build_headers()
    await _check_rate_limit()
    try:
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
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[scrm_client] http error method=%s path=%s status=%s err=%s",
            method, path,
            exc.response.status_code if exc.response else None,
            exc,
        )
        raise
    except Exception as exc:
        logger.warning("[scrm_client] call failed method=%s path=%s err=%s", method, path, exc)
        raise
