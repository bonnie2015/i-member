from __future__ import annotations

import time
from typing import Any, Dict

import httpx

from app.config.logging import get_logger
from app.config.redis import get_redis_client
from app.config.redis_keys import ONITSUKA_RATE_LIMIT_KEY, ONITSUKA_RATE_LIMIT_GLOBAL_KEY
from app.tools.business.execution_context import REQUEST_USER_ID_CTX

logger = get_logger("onitsuka_client")

_ONITSUKA_V2_BASE_URL = "https://lumenapiprod.onitsukatiger.com/v2/api"
_ONITSUKA_TIMEOUT_SECONDS = 10.0
_PER_USER_LIMIT = 10
_GLOBAL_LIMIT = 200


async def _check_rate_limit() -> None:
    """限流检查。超限时最多退避重试 3 次（1s/2s/3s），仍超限则抛 429。"""
    import asyncio

    now_s = int(time.time())
    minute_bucket = now_s // 60
    user_id = str(REQUEST_USER_ID_CTX.get() or "").strip()
    redis = await get_redis_client()

    # 单用户计数（只 INCR 一次）
    if user_id:
        user_key = ONITSUKA_RATE_LIMIT_KEY.format(user_id=user_id, minute_bucket=minute_bucket)
        count = await redis.incr(user_key)
        if count == 1:
            await redis.expire(user_key, 90)

    # 全局计数（只 INCR 一次）
    global_key = ONITSUKA_RATE_LIMIT_GLOBAL_KEY.format(minute_bucket=minute_bucket)
    global_count = await redis.incr(global_key)
    if global_count == 1:
        await redis.expire(global_key, 90)

    # 超限退避重试
    for attempt in range(3):
        # 重读当前计数
        over_user = user_id and await redis.get(user_key)
        over_user = over_user and int(over_user) > _PER_USER_LIMIT
        over_global = int(await redis.get(global_key) or 0) > _GLOBAL_LIMIT

        if not over_user and not over_global:
            return

        if attempt < 2:
            await asyncio.sleep(1.0 * (attempt + 1))

    raise _rate_limited("per-user" if over_user else "global")


def _rate_limited(scope: str) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        message=f"Onitsuka rate limit exceeded: {scope}",
        request=httpx.Request("RATE_LIMIT", "redis://onitsuka-rate-limit"),
        response=httpx.Response(429, request=httpx.Request("RATE_LIMIT", "redis://onitsuka-rate-limit")),
    )


async def call_onitsuka_v2(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    await _check_rate_limit()
    url = f"{_ONITSUKA_V2_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=_ONITSUKA_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response else None
        logger.warning(
            "[onitsuka_client] http error path=%s status=%s err=%s",
            path,
            status_code,
            exc,
        )
        return {
            "error": str(exc),
            "error_code": f"ONITSUKA_HTTP_{status_code or 'UNKNOWN'}",
            "path": path,
        }
    except Exception as exc:
        logger.warning("[onitsuka_client] call failed path=%s err=%s", path, exc)
        return {
            "error": str(exc),
            "error_code": "ONITSUKA_CALL_FAILED",
            "path": path,
        }

    if not isinstance(body, dict):
        return {
            "error": "unexpected response type",
            "error_code": "ONITSUKA_BAD_RESPONSE",
            "path": path,
        }

    if body.get("status") is not True or body.get("code") != 1:
        return {
            "error": str(body.get("message") or "request failed"),
            "error_code": "ONITSUKA_API_FAILED",
            "path": path,
            "raw": body,
        }

    return {
        "data": body.get("data"),
        "_meta": {
            "status": body.get("status"),
            "code": body.get("code"),
            "message": body.get("message"),
            "path": path,
        },
    }


async def search_products(
    *,
    keyword: str,
    where: Dict[str, str],
    sort: str,
    limit: int,
    page: int,
) -> Dict[str, Any]:
    return await call_onitsuka_v2(
        "/catalog/product/search",
        {
            "keyword": keyword,
            "where": where,
            "sort": sort,
            "limit": limit,
            "currPage": page,
        },
    )


async def list_products(
    *,
    category_id: int,
    where: Dict[str, str],
    sort: str,
    limit: int,
    page: int,
) -> Dict[str, Any]:
    return await call_onitsuka_v2(
        "/catalog/product/list",
        {
            "id": int(category_id),
            "where": where,
            "sort": sort,
            "limit": limit,
            "currPage": page,
        },
    )


async def get_product_detail(*, product_id: int, color_id: int) -> Dict[str, Any]:
    return await call_onitsuka_v2(
        "/catalog/product/detail",
        {
            "id": int(product_id),
            "color": int(color_id),
        },
    )
