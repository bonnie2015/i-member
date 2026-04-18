"""
SCRM tools gateway.

职责：
- 维护 tool -> API 的契约（method/path/required_fields）
- 统一参数校验与请求构建
- 统一调用熔断与错误返回
"""

from typing import Any, Dict, List, Optional, Set

import httpx
from app.config.logging import get_logger
from app.agents.tools.scrm_client import call_scrm_endpoint
from langchain_core.tools import BaseTool, tool

logger = get_logger("scrm_tools")


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
    "get_user_behavior": {"method": "GET", "path": "/user/eventLog", "required_fields": []},
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

_TOOL_SUMMARY: Dict[str, str] = {
    "get_user_orders": "查询当前用户的订单列表，用于在缺少 order_id 时先定位订单候选。",
    "get_order_detail": "查询单个订单详情与商品项，用于确认订单状态、渠道和具体商品。",
    "search_product": "搜索商品候选，用于换货场景下确认目标商品。",
    "get_product_detail": "查询单个商品详情，用于补充目标商品确认信息。",
    "get_product_stock": "查询商品库存，用于换货前确认是否有货。",
    "get_logistic": "查询物流信息，用于需要物流节点或签收信息时。",
    "get_user_detail": "查询当前用户基本信息。",
    "get_user_tag": "查询当前用户标签信息。",
    "get_user_level": "查询当前用户等级、等级进度和升级条件。",
    "get_user_score": "查询当前用户积分、积分明细或可用额度。",
    "get_user_behavior": "查询当前用户行为日志，用于补充上下文。",
    "upgrade_membership": "执行会员升级写操作，仅在确认符合升级条件后使用。",
    "issue_compensation_coupon": "发放补偿优惠券，仅在明确补偿条件成立时使用。",
    "create_ticket": "创建工单，适用于退换货、质量问题、投诉、权益申请等正式写操作。",
    "get_ticket": "根据工单号查询单个工单详情与进度。",
    "get_tickets": "查询工单列表，用于缺少 ticket_id 时先定位候选工单。",
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


def build_tool_summary_text(tool_names: List[str] | None = None) -> str:
    names = tool_names or list(TOOL_SPECS.keys())
    seen: Set[str] = set()
    lines: List[str] = []
    for tool_name in names:
        if tool_name in seen or tool_name not in TOOL_SPECS:
            continue
        seen.add(tool_name)
        spec = TOOL_SPECS[tool_name]
        required = spec.get("required_fields") or []
        required_text = ", ".join(required) if required else "无"
        summary = _TOOL_SUMMARY.get(tool_name, "按 tool 定义使用。")
        lines.append(f"- `{tool_name}` | 用途：{summary} | 必填：{required_text}")
    return "\n".join(lines)

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
        if isinstance(normalized_result, dict) and "_raw" not in normalized_result:
            normalized_result["_raw"] = raw_result
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


@tool("get_user_orders")
async def get_user_orders(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取用户历史订单列表。

    入参规则：
    - 无必填字段。
    - 支持可选 query 参数：page、page_size、status、keyword、start_time、end_time。
    - status 可选值：pending、paid、shipped、delivered、closed。
    - start_time / end_time 使用 ISO8601 字符串。

    成功返回：
    - 顶层常见字段：total、page、page_size、has_more、orders。
    - orders 为数组；单个订单常见字段：order_id、status、status_label、order_status、order_status_label、amount、items_summary、order_items_summary、created_at。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    - 鉴权失败时可能返回 unauthorized。
    """
    return await call_scrm_api("get_user_orders", tool_input)


@tool("get_order_detail")
async def get_order_detail(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取单笔订单完整详情。

    入参规则：
    - 必填：order_id。
    - 其他参数无。

    成功返回：
    - 顶层常见字段：order_id、user_id、status、status_label、order_status、order_status_label、amount、created_at、address、items。
    - items 为数组；单个商品常见字段：sku_id、name、product_name、qty、quantity、price。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("get_order_detail", tool_input)


@tool("search_product")
async def search_product(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """按关键词、SKU 或商品 ID 搜索商品。

    入参规则：
    - 无必填字段。
    - 支持可选 query 参数：keyword、sku_id、product_id、status、page、page_size。
    - status 可选值：on_sale、off_sale。

    成功返回：
    - 顶层常见字段：total、page、page_size、has_more、products。
    - products 为数组；单个商品常见字段：product_id、sku_id、name、product_name、price、status、product_status、stock。

    失败返回：
    - 统一返回 error、error_code、tool。
    """
    return await call_scrm_api("search_product", tool_input)


@tool("get_product_detail")
async def get_product_detail(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取商品详情。

    入参规则：
    - 必填：product_id。

    成功返回：
    - 顶层常见字段：product_id、name、product_name、description、product_description、brand、category、price、images、product_images、specs。
    - images / product_images 为字符串数组；specs 为对象。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("get_product_detail", tool_input)


@tool("get_product_stock")
async def get_product_stock(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取商品库存。

    入参规则：
    - 必填：product_id。

    成功返回：
    - 顶层常见字段：product_id、total_stock、available_stock、locked_stock、updated_at。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("get_product_stock", tool_input)


@tool("get_logistic")
async def get_logistic(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取订单物流轨迹与当前状态。

    入参规则：
    - 必填：order_id。

    成功返回：
    - 顶层常见字段：order_id、carrier、tracking_no、status、status_label、tracks。
    - tracks 为数组；单条轨迹常见字段：time、desc、location。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("get_logistic", tool_input)


@tool("get_user_detail")
async def get_user_detail(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取用户详细信息。

    入参规则：
    - 无必填字段。
    - 用户身份从鉴权上下文获取。

    成功返回：
    - 顶层常见字段：user_id、name、member_level、score、total_orders、tags。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 鉴权失败时可能返回 unauthorized。
    """
    return await call_scrm_api("get_user_detail", tool_input)


@tool("get_user_tag")
async def get_user_tag(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取用户标签列表及详情。

    入参规则：
    - 无必填字段。
    - 用户身份从鉴权上下文获取。

    成功返回：
    - 顶层常见字段：user_id、tags、tag_details。

    失败返回：
    - 统一返回 error、error_code、tool。
    """
    return await call_scrm_api("get_user_tag", tool_input)


@tool("get_user_level")
async def get_user_level(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取用户当前会员等级信息。

    入参规则：
    - 无必填字段。
    - 用户身份从鉴权上下文获取。

    成功返回：
    - 顶层常见字段：user_id、level、level_code、score、score_to_next、next_level。

    失败返回：
    - 统一返回 error、error_code、tool。
    """
    return await call_scrm_api("get_user_level", tool_input)


@tool("get_user_score")
async def get_user_score(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """查询用户积分余额与积分明细。

    入参规则：
    - 无必填字段。
    - 支持可选 query 参数：page、page_size、start_time、end_time。

    成功返回：
    - 顶层常见字段：user_id、score_balance、total、page、page_size、has_more、records。
    - records 为数组；单条记录常见字段：record_id、change、type、reason、time。
    - type 可选值：earn、deduct、expire、adjust。

    失败返回：
    - 统一返回 error、error_code、tool。
    """
    return await call_scrm_api("get_user_score", tool_input)


@tool("get_user_behavior")
async def get_user_behavior(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """获取用户行为记录。

    入参规则：
    - 无必填字段。
    - 支持可选 query 参数：event_type、page、page_size、start_time、end_time。
    - event_type 可选值：view、purchase、refund、complaint。

    成功返回：
    - 顶层常见字段：total、page、page_size、has_more、events。
    - events 为数组；单条事件常见字段：event_id、type、description、time、amount。

    失败返回：
    - 统一返回 error、error_code、tool。
    """
    return await call_scrm_api("get_user_behavior", tool_input)


@tool("upgrade_membership")
async def upgrade_membership(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """为用户执行会员升级。

    入参规则：
    - 必填：target_level。
    - 可选：reason。

    成功返回：
    - 顶层常见字段：user_id、success、old_level、new_level、next_level_condition、message。
    - next_level_condition 常见字段：next_level、required_score、current_score、gap_score。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("upgrade_membership", tool_input)


@tool("issue_compensation_coupon")
async def issue_compensation_coupon(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """发放服务补偿券。

    入参规则：
    - 必填：reason。
    - 可选：scene、amount、expire_days。

    成功返回：
    - 顶层常见字段：issued、user_id、coupon_id、coupon_code、value、description、expires_at。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("issue_compensation_coupon", tool_input)


@tool("create_ticket")
async def create_ticket(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """创建客服工单。

    入参规则：
    - 必填：ticket_type、biz_id、title、content。
    - 可选：description、images、order_id、order_item_id、sku_id、quantity、priority、source_channel、contact、metadata、attachments。
    - ticket_type 可选值：refund、change、quality、complain、equity。
    - 订单类工单通常要求 source_channel。

    成功返回：
    - 顶层常见字段：ticket_id、user_id、ticket_type、biz_id、status、ticket_status、status_label、ticket_status_label、priority、source_channel、description、ticket_description、images、evidence_images、created_at、expected_finish_time。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("create_ticket", tool_input)


@tool("get_ticket")
async def get_ticket(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """查询单个工单详情与处理进展。

    入参规则：
    - 必填：ticket_id。
    - 可选：ticket_type、biz_id、source_channel。
    - 当已有工单号时，优先直接查询单个工单，不要求额外补齐 ticket_type / biz_id。

    成功返回：
    - 顶层常见字段：ticket_id、user_id、ticket_type、biz_id、status、ticket_status、status_label、ticket_status_label、title、ticket_title、content、description、ticket_description、images、evidence_images、order_id、order_item_id、sku_id、quantity、qty、priority、source_channel、latest_progress、expected_finish_time、metadata、created_at、updated_at、timeline。
    - timeline 为数组；单条记录常见字段：time、action、operator。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("get_ticket", tool_input)


@tool("get_tickets")
async def get_tickets(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """查询当前用户工单列表。

    入参规则：
    - 无必填字段。
    - 可选：ticket_type、biz_id、source_channel。
    - 其他可选 query 参数：status、keyword、start_time、end_time、page、page_size。
    - status 可选值：open、processing、closed。
    - 当缺少工单号时，可直接先查当前用户工单列表，再让用户确认目标工单。

    成功返回：
    - 顶层常见字段：total、page、page_size、has_more、tickets。
    - tickets 为数组；单个工单常见字段：ticket_id、ticket_type、biz_id、status、ticket_status、title、ticket_title、created_at。

    失败返回：
    - 统一返回 error、error_code、tool。
    - 参数错误时还会返回 missing_fields。
    """
    return await call_scrm_api("get_tickets", tool_input)


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
    upgrade_membership,
    issue_compensation_coupon,
    create_ticket,
    get_ticket,
    get_tickets,
]


def get_scrm_tools() -> List[BaseTool]:
    """返回可挂载到模型代理的工具列表。"""
    return TOOLS
