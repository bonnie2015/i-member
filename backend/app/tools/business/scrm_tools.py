"""
SCRM tools gateway.

职责：
- 维护内部 SCRM endpoint 路由
- 统一参数校验与请求构建
- 统一调用熔断与错误返回
"""

from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from app.config.logging import get_logger
from app.tools.business.execution_context import push_ticket_interaction_source
from app.tools.business.scrm_client import call_scrm_endpoint
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

logger = get_logger("scrm_tools")


class GetUserOrdersInput(BaseModel):
    order_status: Optional[str] = Field(
        default=None,
        description="订单状态筛选：pending/paid/shipped/delivered/closed。",
    )
    order_keyword: Optional[str] = Field(
        default=None,
        description="订单号或商品名关键词；缺少 order_id 时可先用它找候选订单。",
    )
    order_start_time: Optional[str] = Field(default=None, description="订单创建开始时间，ISO8601 格式，须含时区偏移如 +08:00。")
    order_end_time: Optional[str] = Field(default=None, description="订单创建结束时间，ISO8601 格式，须含时区偏移如 +08:00。")
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=10, ge=1, le=20, description="每页数量，默认 10，最大 20。")


class GetOrderDetailInput(BaseModel):
    order_id: str = Field(description="订单 ID；已知订单号时直接查详情。")


class GetUserScoreInput(BaseModel):
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=10, ge=1, le=20, description="每页数量，默认 10，最大 20。")
    score_start_time: Optional[str] = Field(default=None, description="积分变动开始时间，ISO8601 格式，须含时区偏移如 +08:00。")
    score_end_time: Optional[str] = Field(default=None, description="积分变动结束时间，ISO8601 格式，须含时区偏移如 +08:00。")


class CreateTicketInput(BaseModel):
    ticket_type: str = Field(description="工单类型：refund/change/quality/complain/equity。")
    problem_description: str = Field(description="用户诉求、申请原因、投诉经过或问题描述。")
    evidence_images: Optional[List[str]] = Field(default=None, description="可选，用户上传的问题凭证图片 URL 列表。")
    order_id: Optional[str] = Field(default=None, description="可选，关联订单 ID。")
    order_item_id: Optional[str] = Field(default=None, description="可选，关联订单商品项 ID。")
    sku_id: Optional[str] = Field(default=None, description="可选，关联 SKU ID。")
    ticket_quantity: Optional[int] = Field(default=None, description="可选，工单涉及数量，如退/换/问题数量。")
    source_channel: Optional[str] = Field(default=None, description="可选，来源渠道，如 app/wechat/web。")


class GetTicketInput(BaseModel):
    ticket_id: str = Field(description="工单 ID；已知工单号时直接查。")


class GetTicketsInput(BaseModel):
    ticket_type: Optional[str] = Field(default=None, description="可选，工单类型过滤。")
    ticket_status: Optional[str] = Field(default=None, description="可选，工单状态：open/processing/closed。")
    ticket_keyword: Optional[str] = Field(default=None, description="可选，工单标题或工单号关键词。")
    ticket_start_time: Optional[str] = Field(default=None, description="工单创建开始时间，ISO8601 格式，须含时区偏移如 +08:00。")
    ticket_end_time: Optional[str] = Field(default=None, description="工单创建结束时间，ISO8601 格式，须含时区偏移如 +08:00。")
    page: int = Field(default=1, ge=1, description="页码，默认 1。")
    page_size: int = Field(default=10, ge=1, le=20, description="每页数量，默认 10，最大 20。")


EndpointSpec = Tuple[str, str, Tuple[str, ...]]


_SCRM_ENDPOINTS: Dict[str, EndpointSpec] = {
    "get_user_orders": ("GET", "/order", ()),
    "get_order_detail": ("GET", "/order/{order_id}", ()),
    "get_user_level": ("GET", "/user/level", ()),
    "get_user_score": ("GET", "/user/score", ()),
    "get_user_profile": ("GET", "/api/scrm/user_profile", ("user_id",)),
    "create_ticket": ("POST", "/ticket", ("ticket_type", "content")),
    "get_ticket": ("GET", "/ticket/{ticket_id}", ()),
    "get_tickets": ("GET", "/ticket", ()),
}



def _unwrap_success_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"result": result}

    if "error" in result or "error_code" in result:
        return result

    data = result.get("data")
    if isinstance(data, dict):
        return dict(data)
    return result


def _canonical_order_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return _compact_payload(
        order_item_id=item.get("order_item_id") or item.get("id"),
        product_id=item.get("product_id"),
        color_id=item.get("color_id"),
        sku_id=item.get("sku_id"),
        product_name=item.get("product_name") or item.get("name"),
        order_item_quantity=item.get("order_item_quantity") or item.get("quantity") or item.get("qty"),
        order_item_price=item.get("order_item_price") or item.get("price"),
        product_image=item.get("product_image") or item.get("image"),
    )


def _canonical_order(order: Dict[str, Any]) -> Dict[str, Any]:
    order_items = order.get("order_items")
    if not isinstance(order_items, list):
        order_items = order.get("items")
    normalized_items = [
        _canonical_order_item(item)
        for item in list(order_items or [])
        if isinstance(item, dict)
    ]
    return _compact_payload(
        order_id=order.get("order_id") or order.get("id"),
        order_status=order.get("order_status") or order.get("status"),
        order_status_label=order.get("order_status_label") or order.get("status_label"),
        order_amount=order.get("order_amount") or order.get("amount"),
        order_items_summary=order.get("order_items_summary") or order.get("items_summary"),
        source_channel=order.get("source_channel"),
        order_created_at=order.get("order_created_at") or order.get("created_at"),
        order_items=normalized_items,
    )


def _canonical_ticket_timeline_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return _compact_payload(
        ticket_progress_time=item.get("ticket_progress_time") or item.get("time"),
        ticket_progress_action=item.get("ticket_progress_action") or item.get("action"),
        ticket_progress_operator=item.get("ticket_progress_operator") or item.get("operator"),
    )


def _canonical_ticket(ticket: Dict[str, Any], *, include_detail: bool) -> Dict[str, Any]:
    payload = _compact_payload(
        ticket_id=ticket.get("ticket_id") or ticket.get("id"),
        ticket_type=ticket.get("ticket_type"),
        ticket_status=ticket.get("ticket_status") or ticket.get("status"),
        ticket_status_label=ticket.get("ticket_status_label") or ticket.get("status_label"),
        ticket_title=ticket.get("ticket_title") or ticket.get("title"),
        ticket_created_at=ticket.get("ticket_created_at") or ticket.get("created_at"),
    )
    if not include_detail:
        return payload

    timeline = ticket.get("ticket_timeline")
    if not isinstance(timeline, list):
        timeline = ticket.get("timeline")
    payload.update(
        _compact_payload(
            ticket_content=ticket.get("ticket_content") or ticket.get("content"),
            ticket_description=ticket.get("ticket_description") or ticket.get("description"),
            evidence_images=ticket.get("evidence_images") or ticket.get("images"),
            order_id=ticket.get("order_id"),
            order_item_id=ticket.get("order_item_id"),
            sku_id=ticket.get("sku_id"),
            ticket_quantity=ticket.get("ticket_quantity") or ticket.get("quantity"),
            latest_progress=ticket.get("latest_progress"),
            expected_finish_time=ticket.get("expected_finish_time"),
            ticket_timeline=[
                _canonical_ticket_timeline_item(item)
                for item in list(timeline or [])
                if isinstance(item, dict)
            ],
        )
    )
    return payload


def _normalize_user_level_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    return _compact_payload(
        user_id=result.get("user_id"),
        member_level=result.get("member_level") or result.get("level"),
        member_level_code=result.get("member_level_code") or result.get("level_code"),
        member_score=result.get("member_score") or result.get("score"),
        score_to_next_member_level=result.get("score_to_next_member_level") or result.get("score_to_next"),
        next_member_level=result.get("next_member_level") or result.get("next_level"),
    )


def _normalize_user_score_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    records = result.get("score_records")
    if not isinstance(records, list):
        records = result.get("records")
    score_records = []
    for record in list(records or []):
        if not isinstance(record, dict):
            continue
        score_records.append(
            _compact_payload(
                score_record_id=record.get("score_record_id") or record.get("record_id"),
                score_change=record.get("score_change") or record.get("change"),
                score_change_type=record.get("score_change_type") or record.get("type"),
                score_change_reason=record.get("score_change_reason") or record.get("reason"),
                score_changed_at=record.get("score_changed_at") or record.get("time"),
            )
        )
    return _compact_payload(
        total=result.get("total"),
        page=result.get("page"),
        page_size=result.get("page_size"),
        has_more=result.get("has_more"),
        user_id=result.get("user_id"),
        score_balance=result.get("score_balance"),
        score_records=score_records,
    )


def _normalize_orders_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    orders = result.get("orders")
    if not isinstance(orders, list):
        return result
    return {**result, "orders": [_canonical_order(order) for order in orders if isinstance(order, dict)]}


def _normalize_order_detail_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    return _canonical_order(result)


def _normalize_ticket_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    return _canonical_ticket(result, include_detail=True)


def _normalize_ticket_list_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    tickets = result.get("tickets")
    if not isinstance(tickets, list):
        return result
    normalized_tickets = []
    for ticket in tickets:
        if not isinstance(ticket, dict):
            normalized_tickets.append(ticket)
            continue
        normalized_tickets.append(_canonical_ticket(ticket, include_detail=False))
    return {**result, "tickets": normalized_tickets}


def _normalize_tool_result(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _unwrap_success_payload(result)
    if "error" in normalized or "error_code" in normalized:
        return normalized
    if tool_name == "get_user_orders":
        return _normalize_orders_payload(normalized)
    if tool_name == "get_order_detail":
        return _normalize_order_detail_payload(normalized)
    if tool_name == "get_user_level":
        return _normalize_user_level_payload(normalized)
    if tool_name == "get_user_score":
        return _normalize_user_score_payload(normalized)
    if tool_name in {"create_ticket", "get_ticket"}:
        return _normalize_ticket_payload(normalized)
    if tool_name == "get_tickets":
        return _normalize_ticket_list_payload(normalized)
    return normalized


def _validate_required_fields(tool_name: str, tool_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    endpoint = _SCRM_ENDPOINTS.get(tool_name)
    if endpoint is None:
        return {
            "error": f"unsupported tool: {tool_name}",
            "error_code": "SCRM_TOOL_UNSUPPORTED",
            "tool": tool_name,
        }

    _, path_template, extra_required_fields = endpoint
    required_names = [*_extract_path_keys(path_template), *extra_required_fields]
    missing = [k for k in required_names if not str(tool_input.get(k, "")).strip()]
    if not missing:
        return None

    return {
        "error": f"missing required fields: {', '.join(missing)}",
        "error_code": "SCRM_BAD_REQUEST",
        "tool": tool_name,
        "missing_fields": missing,
    }


_TICKET_TYPE_LABELS = {
    "refund": "退货退款",
    "change": "换货",
    "quality": "质量问题",
    "complain": "投诉反馈",
    "equity": "会员权益",
}


def _short_text(value: Any, *, limit: int = 32) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _build_ticket_title(ticket_type: str, problem_description: str) -> str:
    label = _TICKET_TYPE_LABELS.get(str(ticket_type or "").strip(), "客服工单")
    summary = _short_text(problem_description)
    return f"{label}: {summary}" if summary else label


_TIME_FIELDS = {"start_time", "end_time"}
_TIME_TZ_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


def _ensure_timezone(value: Any) -> str:
    """ISO8601 无时区时默认补 +08:00，保证 mock OffsetDateTime.parse() 不抛异常。"""
    text = str(value or "").strip()
    if not text:
        return text
    if _TIME_TZ_RE.match(text):
        return f"{text}+08:00"
    return text


def _prepare_tool_input(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    prepared = dict(tool_input or {})
    if tool_name == "get_user_orders":
        return _compact_payload(
            status=prepared.get("order_status"),
            keyword=prepared.get("order_keyword"),
            start_time=_ensure_timezone(prepared.get("order_start_time")),
            end_time=_ensure_timezone(prepared.get("order_end_time")),
            page=prepared.get("page"),
            page_size=prepared.get("page_size"),
        )
    if tool_name == "get_user_score":
        return _compact_payload(
            start_time=_ensure_timezone(prepared.get("score_start_time")),
            end_time=_ensure_timezone(prepared.get("score_end_time")),
            page=prepared.get("page"),
            page_size=prepared.get("page_size"),
        )
    if tool_name == "get_tickets":
        return _compact_payload(
            ticket_type=prepared.get("ticket_type"),
            status=prepared.get("ticket_status"),
            keyword=prepared.get("ticket_keyword"),
            start_time=_ensure_timezone(prepared.get("ticket_start_time")),
            end_time=_ensure_timezone(prepared.get("ticket_end_time")),
            page=prepared.get("page"),
            page_size=prepared.get("page_size"),
        )
    if tool_name != "create_ticket":
        return prepared

    problem_description = str(
        prepared.get("problem_description")
        or prepared.get("content")
        or prepared.get("description")
        or ""
    ).strip()
    evidence_images = prepared.get("evidence_images")
    if evidence_images is None:
        evidence_images = prepared.get("images")

    prepared["title"] = str(
        prepared.get("title")
        or _build_ticket_title(str(prepared.get("ticket_type") or ""), problem_description)
    ).strip()
    prepared["content"] = str(prepared.get("content") or problem_description).strip()
    prepared["description"] = str(prepared.get("description") or problem_description).strip()
    if evidence_images is not None:
        prepared["images"] = evidence_images
    if "ticket_quantity" in prepared and "quantity" not in prepared:
        prepared["quantity"] = prepared.get("ticket_quantity")
    prepared.pop("evidence_images", None)
    prepared.pop("problem_description", None)
    prepared.pop("ticket_quantity", None)
    prepared.pop("extra", None)
    return prepared


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
    normalized_input = _prepare_tool_input(tool_name, dict(tool_input or {}))

    bad_request = _validate_required_fields(tool_name, normalized_input)
    if bad_request:
        return bad_request

    method, path_template, _ = _SCRM_ENDPOINTS[tool_name]
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
        if "error" not in normalized_result and "error_code" not in normalized_result:
            push_ticket_interaction_source(normalized_result)
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


@tool("get_user_orders", args_schema=GetUserOrdersInput)
async def get_user_orders(
    order_status: Optional[str] = None,
    order_keyword: Optional[str] = None,
    order_start_time: Optional[str] = None,
    order_end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 10,
) -> Dict[str, Any]:
    """列出当前用户订单。缺失 order_id 时可先用它找候选订单。返回空列表说明确实没有符合筛选条件的订单，不要反复试。"""
    return await call_scrm_api(
        "get_user_orders",
        _compact_payload(
            order_status=order_status,
            order_keyword=order_keyword,
            order_start_time=order_start_time,
            order_end_time=order_end_time,
            page=page,
            page_size=page_size,
        ),
    )


@tool("get_order_detail", args_schema=GetOrderDetailInput)
async def get_order_detail(order_id: str) -> Dict[str, Any]:
    """查询单笔订单详情；确认商品、金额和状态时使用。"""
    return await call_scrm_api("get_order_detail", {"order_id": order_id})


@tool("get_user_level")
async def get_user_level() -> Dict[str, Any]:
    """获取当前会员等级和升档差距。"""
    return await call_scrm_api("get_user_level", {})


@tool("get_user_score", args_schema=GetUserScoreInput)
async def get_user_score(
    page: int = 1,
    page_size: int = 20,
    score_start_time: Optional[str] = None,
    score_end_time: Optional[str] = None,
) -> Dict[str, Any]:
    """查询积分余额和积分明细。返回空列表说明该时段无积分变动，不要反复试。"""
    return await call_scrm_api(
        "get_user_score",
        _compact_payload(
            page=page,
            page_size=page_size,
            score_start_time=score_start_time,
            score_end_time=score_end_time,
        ),
    )


@tool("create_ticket", args_schema=CreateTicketInput)
async def create_ticket(
    ticket_type: str,
    problem_description: str,
    evidence_images: Optional[List[str]] = None,
    order_id: Optional[str] = None,
    order_item_id: Optional[str] = None,
    sku_id: Optional[str] = None,
    ticket_quantity: Optional[int] = None,
    source_channel: Optional[str] = None,
) -> Dict[str, Any]:
    """创建客服工单；确认要正式发起售后或投诉时使用。"""
    payload = _compact_payload(
        ticket_type=ticket_type,
        problem_description=problem_description,
        evidence_images=evidence_images,
        order_id=order_id,
        order_item_id=order_item_id,
        sku_id=sku_id,
        ticket_quantity=ticket_quantity,
        source_channel=source_channel,
    )
    return await call_scrm_api(
        "create_ticket",
        payload,
    )


@tool("get_ticket", args_schema=GetTicketInput)
async def get_ticket(ticket_id: str) -> Dict[str, Any]:
    """查询单个工单详情和进度。"""
    return await call_scrm_api(
        "get_ticket",
        {"ticket_id": ticket_id},
    )


@tool("get_tickets", args_schema=GetTicketsInput)
async def get_tickets(
    ticket_type: Optional[str] = None,
    ticket_status: Optional[str] = None,
    ticket_keyword: Optional[str] = None,
    ticket_start_time: Optional[str] = None,
    ticket_end_time: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """列出当前用户工单。缺失 ticket_id 时可先用它找候选工单。返回空列表说明没有符合筛选条件的工单，不要反复试。"""
    return await call_scrm_api(
        "get_tickets",
        _compact_payload(
            ticket_type=ticket_type,
            ticket_status=ticket_status,
            ticket_keyword=ticket_keyword,
            ticket_start_time=ticket_start_time,
            ticket_end_time=ticket_end_time,
            page=page,
            page_size=page_size,
        ),
    )


TOOLS: List[BaseTool] = [
    get_user_orders,
    get_order_detail,
    get_user_level,
    get_user_score,
    create_ticket,
    get_ticket,
    get_tickets,
]


def get_scrm_tools() -> List[BaseTool]:
    """返回可挂载到模型代理的工具列表。"""
    return TOOLS
