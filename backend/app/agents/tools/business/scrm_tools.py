"""
SCRM tools gateway.

职责：
- 维护 tool -> API 的契约（method/path/required_fields）
- 统一参数校验与请求构建
- 统一调用熔断与错误返回
"""

import hashlib
import json
from typing import Any, Dict, List, Optional, Set

import httpx
from app.config.logging import get_logger
from app.agents.tools.business.scrm_client import call_scrm_endpoint
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import USER_BEHAVIOR_CACHE_KEY
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

logger = get_logger("scrm_tools")

_USER_BEHAVIOR_CACHE_TTL_SECONDS = 5 * 60


class GetUserOrdersInput(BaseModel):
    status: Optional[str] = Field(
        default=None,
        description="订单状态筛选：pending/paid/shipped/delivered/closed。",
    )
    keyword: Optional[str] = Field(
        default=None,
        description="订单号或商品名关键词；缺少 order_id 时可先用它找候选订单。",
    )
    start_time: Optional[str] = Field(default=None, description="开始时间，ISO8601。")
    end_time: Optional[str] = Field(default=None, description="结束时间，ISO8601。")
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100。")


class GetOrderDetailInput(BaseModel):
    order_id: str = Field(description="订单 ID；已知订单号时直接查详情。")


class SearchProductInput(BaseModel):
    keyword: Optional[str] = Field(default=None, description="商品名关键词。")
    sku_id: Optional[str] = Field(default=None, description="SKU ID；已知 SKU 时优先使用。")
    product_id: Optional[str] = Field(default=None, description="商品 ID。")
    status: Optional[str] = Field(default=None, description="商品状态筛选：on_sale/off_sale。")
    start_time: Optional[str] = Field(default=None, description="开始时间，ISO8601；按更新时间筛选。")
    end_time: Optional[str] = Field(default=None, description="结束时间，ISO8601；按更新时间筛选。")
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100。")


class ProductIdInput(BaseModel):
    product_id: str = Field(description="商品 ID。")


class GetLogisticInput(BaseModel):
    order_id: str = Field(description="订单 ID；用于查询物流轨迹和当前物流状态。")


class GetUserScoreInput(BaseModel):
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100。")
    start_time: Optional[str] = Field(default=None, description="开始时间，ISO8601。")
    end_time: Optional[str] = Field(default=None, description="结束时间，ISO8601。")


class GetUserBehaviorInput(BaseModel):
    user_id: str = Field(description="SCRM 内用户唯一标识；查询指定用户行为记录时必填。")
    start_time: Optional[str] = Field(default=None, description="可选，开始时间，ISO8601。")
    end_time: Optional[str] = Field(default=None, description="可选，结束时间，ISO8601。")
    event_types: Optional[str] = Field(
        default=None,
        description="可选，事件类型筛选，多个用英文逗号分隔，如 product_view,add_to_cart。",
    )
    channels: Optional[str] = Field(
        default=None,
        description="可选，渠道筛选，多个用英文逗号分隔，如 miniprogram,app。",
    )
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100。")
    sort: str = Field(default="desc", description="排序：asc 或 desc；默认 desc。")


class GetUserProfileInput(BaseModel):
    user_id: str = Field(
        description=(
            "SCRM 内用户唯一标识；可传 union_id、open_id、手机号或其他系统主键。"
        )
    )
    fields: Optional[str] = Field(
        default=None,
        description=(
            "可选，多个字段用英文逗号分隔。"
            "可选值：basic_info,value_segment,preferences,behavior_summary,social。"
            "basic_info 看身份和会员等级；value_segment 看消费价值；"
            "preferences 看品类/系列偏好；behavior_summary 看活跃度和渠道；"
            "social 看 KOC 和影响力。留空返回完整画像。"
        ),
    )


class UpgradeMembershipInput(BaseModel):
    target_level: str = Field(description="目标会员等级。")
    reason: Optional[str] = Field(default=None, description="可选，升级原因或备注。")


class IssueCompensationCouponInput(BaseModel):
    reason: str = Field(description="补偿原因；写明发券依据。")
    scene: Optional[str] = Field(default=None, description="可选，发券场景，如 service_recovery。")
    amount: Optional[float] = Field(default=None, description="可选，券面额。")
    expire_days: Optional[int] = Field(default=None, description="可选，过期天数。")


class CreateTicketInput(BaseModel):
    ticket_type: str = Field(description="工单类型：refund/change/quality/complain/equity。")
    biz_id: str = Field(description="业务主键；通常是订单号或权益申请单号。")
    title: str = Field(description="工单标题；简短描述问题。")
    content: str = Field(description="工单正文；写明用户诉求和核心事实。")
    description: Optional[str] = Field(default=None, description="可选，补充问题描述。")
    images: Optional[List[str]] = Field(default=None, description="可选，问题图片 URL 列表。")
    order_id: Optional[str] = Field(default=None, description="可选，关联订单 ID。")
    order_item_id: Optional[str] = Field(default=None, description="可选，关联订单商品项 ID。")
    sku_id: Optional[str] = Field(default=None, description="可选，关联 SKU ID。")
    quantity: Optional[int] = Field(default=None, description="可选，问题数量。")
    source_channel: Optional[str] = Field(default=None, description="可选，来源渠道，如 app/wechat/web。")
    extra: Optional[Dict[str, Any]] = Field(
        default=None,
        description="可选，低频扩展字段对象；仅在确有需要时传。可包含 priority、contact、metadata、attachments。",
    )


class GetTicketInput(BaseModel):
    ticket_id: str = Field(description="工单 ID；已知工单号时直接查。")
    ticket_type: Optional[str] = Field(default=None, description="可选，工单类型过滤。")
    biz_id: Optional[str] = Field(default=None, description="可选，业务主键过滤。")
    source_channel: Optional[str] = Field(default=None, description="可选，来源渠道过滤。")


class GetTicketsInput(BaseModel):
    ticket_type: Optional[str] = Field(default=None, description="可选，工单类型过滤。")
    biz_id: Optional[str] = Field(default=None, description="可选，业务主键过滤。")
    source_channel: Optional[str] = Field(default=None, description="可选，来源渠道过滤。")
    status: Optional[str] = Field(default=None, description="可选，工单状态：open/processing/closed。")
    keyword: Optional[str] = Field(default=None, description="可选，工单标题或工单号关键词。")
    start_time: Optional[str] = Field(default=None, description="开始时间，ISO8601。")
    end_time: Optional[str] = Field(default=None, description="结束时间，ISO8601。")
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，默认 20，最大 100。")


TOOL_SPECS: Dict[str, Dict[str, Any]] = {
    # order
    "get_user_orders": {"method": "GET", "path": "/order", "required_fields": []},
    "get_order_detail": {"method": "GET", "path": "/order/{order_id}", "required_fields": ["order_id"]},
    # product
    "search_product": {"method": "GET", "path": "/product", "required_fields": []},
    "get_product_detail": {"method": "GET", "path": "/product/{product_id}", "required_fields": ["product_id"]},
    "get_product_stock": {"method": "GET", "path": "/product/stock/{product_id}", "required_fields": ["product_id"]},
    # logistic
    "get_logistic": {"method": "GET", "path": "/logistic", "required_fields": ["order_id"]},
    # user
    "get_user_detail": {"method": "GET", "path": "/user", "required_fields": []},
    "get_user_tag": {"method": "GET", "path": "/user/tag", "required_fields": []},
    "get_user_level": {"method": "GET", "path": "/user/level", "required_fields": []},
    "get_user_score": {"method": "GET", "path": "/user/score", "required_fields": []},
    "get_user_behavior": {"method": "GET", "path": "/user/eventLog", "required_fields": ["user_id"]},
    "get_user_profile": {
        "method": "GET",
        "path": "/api/scrm/user_profile",
        "required_fields": ["user_id"],
    },
    "upgrade_membership": {"method": "POST", "path": "/user/upgrade", "required_fields": ["target_level"]},
    "issue_compensation_coupon": {
        "method": "POST",
        "path": "/coupon/compensation",
        "required_fields": ["reason"],
    },
    # ticket
    "create_ticket": {
        "method": "POST",
        "path": "/ticket",
        "required_fields": ["ticket_type", "biz_id", "title", "content"],
    },
    "get_ticket": {
        "method": "GET",
        "path": "/ticket/{ticket_id}",
        "required_fields": ["ticket_id"],
    },
    "get_tickets": {
        "method": "GET",
        "path": "/ticket",
        "required_fields": [],
    },
}



def _unwrap_success_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"result": result}

    if "error" in result or "error_code" in result:
        return result

    data = result.get("data")
    if isinstance(data, dict):
        normalized = dict(data)
        normalized["_meta"] = {
            "code": result.get("code"),
            "message": result.get("message"),
        }
        return normalized
    return result


def _normalize_orders_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    orders = result.get("orders")
    if not isinstance(orders, list):
        return result
    normalized_orders = []
    for order in orders:
        if not isinstance(order, dict):
            normalized_orders.append(order)
            continue
        item = dict(order)
        if "status" in item and "order_status" not in item:
            item["order_status"] = item.get("status")
        if "status_label" in item and "order_status_label" not in item:
            item["order_status_label"] = item.get("status_label")
        if "items_summary" in item and "order_items_summary" not in item:
            item["order_items_summary"] = item.get("items_summary")
        items = item.get("items")
        if isinstance(items, list):
            normalized_items = []
            for order_item in items:
                if not isinstance(order_item, dict):
                    normalized_items.append(order_item)
                    continue
                payload = dict(order_item)
                if "name" in payload and "product_name" not in payload:
                    payload["product_name"] = payload.get("name")
                if "qty" in payload and "quantity" not in payload:
                    payload["quantity"] = payload.get("qty")
                if "image" in payload and "product_image" not in payload:
                    payload["product_image"] = payload.get("image")
                normalized_items.append(payload)
            item["items"] = normalized_items
        normalized_orders.append(item)
    return {**result, "orders": normalized_orders}


def _normalize_order_detail_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(result)
    if "status" in normalized and "order_status" not in normalized:
        normalized["order_status"] = normalized.get("status")
    if "status_label" in normalized and "order_status_label" not in normalized:
        normalized["order_status_label"] = normalized.get("status_label")

    items = normalized.get("items")
    if isinstance(items, list):
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            payload = dict(item)
            if "name" in payload and "product_name" not in payload:
                payload["product_name"] = payload.get("name")
            if "qty" in payload and "quantity" not in payload:
                payload["quantity"] = payload.get("qty")
            if "image" in payload and "product_image" not in payload:
                payload["product_image"] = payload.get("image")
            normalized_items.append(payload)
        normalized["items"] = normalized_items
    return normalized


def _normalize_products_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    products = result.get("products")
    if not isinstance(products, list):
        return result
    normalized_products = []
    for product in products:
        if not isinstance(product, dict):
            normalized_products.append(product)
            continue
        item = dict(product)
        if "name" in item and "product_name" not in item:
            item["product_name"] = item.get("name")
        if "status" in item and "product_status" not in item:
            item["product_status"] = item.get("status")
        if "image" in item and "product_image" not in item:
            item["product_image"] = item.get("image")
        if "image" in item and "product_images" not in item:
            item["product_images"] = [item.get("image")]
        normalized_products.append(item)
    return {**result, "products": normalized_products}


def _normalize_product_detail_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(result)
    if "name" in normalized and "product_name" not in normalized:
        normalized["product_name"] = normalized.get("name")
    if "description" in normalized and "product_description" not in normalized:
        normalized["product_description"] = normalized.get("description")
    if "images" in normalized and "product_images" not in normalized:
        normalized["product_images"] = normalized.get("images")
    images = normalized.get("images")
    if isinstance(images, list) and images and "image" not in normalized:
        normalized["image"] = images[0]
    if isinstance(images, list) and images and "product_image" not in normalized:
        normalized["product_image"] = images[0]
    return normalized


def _normalize_ticket_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(result)
    if "status" in normalized and "ticket_status" not in normalized:
        normalized["ticket_status"] = normalized.get("status")
    if "status_label" in normalized and "ticket_status_label" not in normalized:
        normalized["ticket_status_label"] = normalized.get("status_label")
    if "title" in normalized and "ticket_title" not in normalized:
        normalized["ticket_title"] = normalized.get("title")
    if "description" in normalized and "ticket_description" not in normalized:
        normalized["ticket_description"] = normalized.get("description")
    if "images" in normalized and "evidence_images" not in normalized:
        normalized["evidence_images"] = normalized.get("images")
    if "quantity" in normalized and "qty" not in normalized:
        normalized["qty"] = normalized.get("quantity")
    return normalized


def _normalize_ticket_list_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    tickets = result.get("tickets")
    if not isinstance(tickets, list):
        return result
    normalized_tickets = []
    for ticket in tickets:
        if not isinstance(ticket, dict):
            normalized_tickets.append(ticket)
            continue
        item = dict(ticket)
        if "status" in item and "ticket_status" not in item:
            item["ticket_status"] = item.get("status")
        if "title" in item and "ticket_title" not in item:
            item["ticket_title"] = item.get("title")
        normalized_tickets.append(item)
    return {**result, "tickets": normalized_tickets}


def _normalize_tool_result(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _unwrap_success_payload(result)
    if "error" in normalized or "error_code" in normalized:
        return normalized
    if tool_name == "get_user_orders":
        return _normalize_orders_payload(normalized)
    if tool_name == "get_order_detail":
        return _normalize_order_detail_payload(normalized)
    if tool_name == "search_product":
        return _normalize_products_payload(normalized)
    if tool_name == "get_product_detail":
        return _normalize_product_detail_payload(normalized)
    if tool_name in {"create_ticket", "get_ticket"}:
        return _normalize_ticket_payload(normalized)
    if tool_name == "get_tickets":
        return _normalize_ticket_list_payload(normalized)
    return normalized


def _validate_required_fields(tool_name: str, tool_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    spec = TOOL_SPECS.get(tool_name)
    if not spec:
        return {
            "error": f"unsupported tool: {tool_name}",
            "error_code": "SCRM_TOOL_UNSUPPORTED",
            "tool": tool_name,
        }

    missing = [k for k in spec["required_fields"] if not str(tool_input.get(k, "")).strip()]
    if not missing:
        return None

    return {
        "error": f"missing required fields: {', '.join(missing)}",
        "error_code": "SCRM_BAD_REQUEST",
        "tool": tool_name,
        "missing_fields": missing,
    }


def _build_path(path_template: str, tool_input: Dict[str, Any]) -> str:
    path = path_template
    for key, value in tool_input.items():
        token = "{" + key + "}"
        if token in path:
            path = path.replace(token, str(value))
    return path


def _extract_path_keys(path_template: str) -> Set[str]:
    keys: Set[str] = set()
    start = 0
    while True:
        left = path_template.find("{", start)
        if left < 0:
            break
        right = path_template.find("}", left + 1)
        if right < 0:
            break
        keys.add(path_template[left + 1 : right])
        start = right + 1
    return keys


async def call_scrm_api(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """
    供 workflow/tool-executor 调用的统一入口。
    """
    normalized_input = dict(tool_input or {})

    bad_request = _validate_required_fields(tool_name, normalized_input)
    if bad_request:
        return bad_request

    spec = TOOL_SPECS[tool_name]
    method = spec["method"]
    path_template = spec["path"]
    path = _build_path(path_template, normalized_input)
    path_keys = _extract_path_keys(path_template)
    payload = {k: v for k, v in normalized_input.items() if k not in path_keys}

    query = payload if method == "GET" else None
    body = payload if method != "GET" else None

    try:
        result = await call_scrm_endpoint(
            method=method,
            path=path,
            query=query,
            body=body,
            timeout_s=10.0,
        )
        raw_result = result if isinstance(result, dict) else {"result": result}
        normalized_result = _normalize_tool_result(tool_name, raw_result)
        return normalized_result
    except PermissionError as e:
        logger.warning("[scrm_tools] auth blocked for %s: %s", tool_name, e)
        return {"error": str(e), "error_code": "SCRM_AUTH_MISSING", "unauthorized": True}
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code if e.response else None
        if status_code == 429:
            logger.warning("[scrm_tools] rate limited for %s: %s", tool_name, e)
            return {"error": str(e), "error_code": "SCRM_RATE_LIMITED", "tool": tool_name}
        logger.warning("[scrm_tools] http error for %s: %s", tool_name, e)
        return {
            "error": str(e),
            "error_code": f"SCRM_HTTP_{status_code or 'UNKNOWN'}",
            "tool": tool_name,
        }
    except Exception as e:
        logger.warning("[scrm_tools] call failed for %s: %s", tool_name, e)
        return {"error": str(e), "error_code": "SCRM_CALL_FAILED", "tool": tool_name}


def _compact_payload(**kwargs: Any) -> Dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


def _user_behavior_cache_key(payload: Dict[str, Any]) -> str:
    user_id = str(payload.get("user_id") or "").strip()
    normalized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    query_hash = hashlib.sha1(normalized_payload.encode("utf-8")).hexdigest()
    return USER_BEHAVIOR_CACHE_KEY.format(user_id=user_id, query_hash=query_hash)


async def _load_cached_user_behavior(payload: Dict[str, Any]) -> Dict[str, Any] | None:
    redis = await get_optional_redis_client()
    if not redis:
        return None
    try:
        cached = await redis.get(_user_behavior_cache_key(payload))
        if not cached:
            return None
        parsed = json.loads(cached)
        return parsed if isinstance(parsed, dict) else None
    except Exception as e:
        logger.warning("[scrm_tools] load cached user behavior failed: %s", e)
        return None


async def _save_cached_user_behavior(payload: Dict[str, Any], result: Dict[str, Any]) -> None:
    redis = await get_optional_redis_client()
    if not redis:
        return
    try:
        await redis.setex(
            _user_behavior_cache_key(payload),
            _USER_BEHAVIOR_CACHE_TTL_SECONDS,
            json.dumps(result, ensure_ascii=False),
        )
    except Exception as e:
        logger.warning("[scrm_tools] save cached user behavior failed: %s", e)


@tool("get_user_orders", args_schema=GetUserOrdersInput)
async def get_user_orders(
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """列出当前用户订单；缺少 order_id 时先用它找候选订单。"""
    return await call_scrm_api(
        "get_user_orders",
        _compact_payload(
            status=status,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        ),
    )


@tool("get_order_detail", args_schema=GetOrderDetailInput)
async def get_order_detail(order_id: str) -> Dict[str, Any]:
    """查询单笔订单详情；确认商品、金额和状态时使用。"""
    return await call_scrm_api("get_order_detail", {"order_id": order_id})


@tool("search_product", args_schema=SearchProductInput)
async def search_product(
    keyword: Optional[str] = None,
    sku_id: Optional[str] = None,
    product_id: Optional[str] = None,
    status: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """搜索商品候选；换货、推荐或查库存前可先用它定位商品。"""
    return await call_scrm_api(
        "search_product",
        _compact_payload(
            keyword=keyword,
            sku_id=sku_id,
            product_id=product_id,
            status=status,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        ),
    )


@tool("get_product_detail", args_schema=ProductIdInput)
async def get_product_detail(product_id: str) -> Dict[str, Any]:
    """查询商品详情；确认商品信息、品牌和图片时使用。"""
    return await call_scrm_api("get_product_detail", {"product_id": product_id})


@tool("get_product_stock", args_schema=ProductIdInput)
async def get_product_stock(product_id: str) -> Dict[str, Any]:
    """查询商品库存；换货或补货判断时使用。"""
    return await call_scrm_api("get_product_stock", {"product_id": product_id})


@tool("get_logistic", args_schema=GetLogisticInput)
async def get_logistic(order_id: str) -> Dict[str, Any]:
    """查询订单物流轨迹和当前物流状态。"""
    return await call_scrm_api("get_logistic", {"order_id": order_id})


@tool("get_user_detail")
async def get_user_detail() -> Dict[str, Any]:
    """获取当前用户基础资料，如姓名、等级、积分和标签。"""
    return await call_scrm_api("get_user_detail", {})


@tool("get_user_tag")
async def get_user_tag() -> Dict[str, Any]:
    """获取当前用户标签；适合做分群或话术个性化。"""
    return await call_scrm_api("get_user_tag", {})


@tool("get_user_level")
async def get_user_level() -> Dict[str, Any]:
    """获取当前会员等级和升档差距。"""
    return await call_scrm_api("get_user_level", {})


@tool("get_user_score", args_schema=GetUserScoreInput)
async def get_user_score(
    page: int = 1,
    page_size: int = 20,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """查询积分余额和积分明细。"""
    return await call_scrm_api(
        "get_user_score",
        _compact_payload(
            page=page,
            page_size=page_size,
            start_time=start_time,
            end_time=end_time,
        ),
    )


@tool("get_user_behavior", args_schema=GetUserBehaviorInput)
async def get_user_behavior(
    user_id: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    event_types: Optional[str] = None,
    channels: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    sort: str = "desc",
) -> Dict[str, Any]:
    """按用户和时间范围查询行为轨迹；查看近期浏览、加购等明细时使用。"""
    payload = _compact_payload(
        user_id=user_id,
        start_time=start_time,
        end_time=end_time,
        event_types=event_types,
        channels=channels,
        page=page,
        page_size=page_size,
        sort=sort,
    )
    cached = await _load_cached_user_behavior(payload)
    if cached is not None:
        return cached

    result = await call_scrm_api(
        "get_user_behavior",
        payload,
    )
    if isinstance(result, dict) and "error" not in result and "error_code" not in result:
        await _save_cached_user_behavior(payload, result)
    return result


@tool("get_user_profile", args_schema=GetUserProfileInput)
async def get_user_profile(user_id: str, fields: Optional[str] = None) -> Dict[str, Any]:
    """查询综合用户画像；推荐、分层服务和营销判断时使用。"""
    from app.agents.memory.user_profile import load_user_profile

    return await load_user_profile(user_id=user_id, fields=fields)


@tool("upgrade_membership", args_schema=UpgradeMembershipInput)
async def upgrade_membership(target_level: str, reason: Optional[str] = None) -> Dict[str, Any]:
    """提交会员升级操作。"""
    return await call_scrm_api(
        "upgrade_membership",
        _compact_payload(target_level=target_level, reason=reason),
    )


@tool("issue_compensation_coupon", args_schema=IssueCompensationCouponInput)
async def issue_compensation_coupon(
    reason: str,
    scene: Optional[str] = None,
    amount: Optional[float] = None,
    expire_days: Optional[int] = None,
) -> Dict[str, Any]:
    """发放补偿券；仅在补偿条件明确成立时使用。"""
    return await call_scrm_api(
        "issue_compensation_coupon",
        _compact_payload(
            reason=reason,
            scene=scene,
            amount=amount,
            expire_days=expire_days,
        ),
    )


@tool("create_ticket", args_schema=CreateTicketInput)
async def create_ticket(
    ticket_type: str,
    biz_id: str,
    title: str,
    content: str,
    description: Optional[str] = None,
    images: Optional[List[str]] = None,
    order_id: Optional[str] = None,
    order_item_id: Optional[str] = None,
    sku_id: Optional[str] = None,
    quantity: Optional[int] = None,
    source_channel: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """创建客服工单；确认要正式发起售后或投诉时使用。"""
    payload = _compact_payload(
        ticket_type=ticket_type,
        biz_id=biz_id,
        title=title,
        content=content,
        description=description,
        images=images,
        order_id=order_id,
        order_item_id=order_item_id,
        sku_id=sku_id,
        quantity=quantity,
        source_channel=source_channel,
    )
    if isinstance(extra, dict):
        payload.update({key: value for key, value in extra.items() if value is not None})
    return await call_scrm_api(
        "create_ticket",
        payload,
    )


@tool("get_ticket", args_schema=GetTicketInput)
async def get_ticket(
    ticket_id: str,
    ticket_type: Optional[str] = None,
    biz_id: Optional[str] = None,
    source_channel: Optional[str] = None,
) -> Dict[str, Any]:
    """查询单个工单详情和进度。"""
    return await call_scrm_api(
        "get_ticket",
        _compact_payload(
            ticket_id=ticket_id,
            ticket_type=ticket_type,
            biz_id=biz_id,
            source_channel=source_channel,
        ),
    )


@tool("get_tickets", args_schema=GetTicketsInput)
async def get_tickets(
    ticket_type: Optional[str] = None,
    biz_id: Optional[str] = None,
    source_channel: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """列出当前用户工单；缺少 ticket_id 时先用它找候选工单。"""
    return await call_scrm_api(
        "get_tickets",
        _compact_payload(
            ticket_type=ticket_type,
            biz_id=biz_id,
            source_channel=source_channel,
            status=status,
            keyword=keyword,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        ),
    )


TOOLS: List[BaseTool] = [
    get_user_orders,
    get_order_detail,
    search_product,
    get_product_detail,
    get_product_stock,
    get_logistic,
    get_user_detail,
    get_user_tag,
    get_user_level,
    get_user_score,
    get_user_behavior,
    get_user_profile,
    upgrade_membership,
    issue_compensation_coupon,
    create_ticket,
    get_ticket,
    get_tickets,
]


def get_scrm_tools() -> List[BaseTool]:
    """返回可挂载到模型代理的工具列表。"""
    return TOOLS
