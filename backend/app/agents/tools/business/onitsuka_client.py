from __future__ import annotations

from typing import Any, Dict

import httpx

from app.config.logging import get_logger

logger = get_logger("onitsuka_client")

_ONITSUKA_V2_BASE_URL = "https://lumenapiprod.onitsukatiger.com/v2/api"
_ONITSUKA_TIMEOUT_SECONDS = 10.0

async def call_onitsuka_v2(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
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
        logger.warning("[onitsuka_client] http error path=%s status=%s err=%s", path, status_code, exc)
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
        return {"error": "unexpected response type", "error_code": "ONITSUKA_BAD_RESPONSE", "path": path}

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
