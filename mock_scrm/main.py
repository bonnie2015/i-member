from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse


app = FastAPI(title="Mock SCRM Service")


def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


def _scrm_success(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"code": 0, "message": "success", "data": data}


def _scrm_error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": status_code, "message": message, "data": None},
    )


BASE_ORDERS = [
    {
        "order_id": "N20260305000012",
        "status": "delivered",
        "status_label": "已签收",
        "amount": 699,
        "items_summary": "小熊电饭煲 4L",
        "items": [
            {
                "order_item_id": "OI202603050001",
                "product_id": "P_RICE_4L_A",
                "sku_id": "SKU_RICE_4L_WHITE",
                "name": "小熊电饭煲 4L 白色",
                "qty": 1,
                "price": 699,
                "image": "https://mock.local/rice-4l-white.png",
            },
            {
                "order_item_id": "OI202603050002",
                "product_id": "P_RICE_4L_B",
                "sku_id": "SKU_RICE_4L_BLACK",
                "name": "小熊电饭煲 4L 黑色",
                "qty": 1,
                "price": 699,
                "image": "https://mock.local/rice-4l-black.png",
            },
        ],
        "created_at": "2026-03-05T10:00:00+08:00",
    },
    {
        "order_id": "N20260306000034",
        "status": "delivered",
        "status_label": "已签收",
        "amount": 59,
        "items_summary": "纯棉袜子 3双装",
        "items": [
            {
                "order_item_id": "OI202603060001",
                "product_id": "P_SOCK_3PACK",
                "sku_id": "SKU_SOCK_3PACK_MIX",
                "name": "纯棉袜子 3双装",
                "qty": 1,
                "price": 59,
                "image": "https://mock.local/socks-3pack.png",
            }
        ],
        "created_at": "2026-03-06T09:30:00+08:00",
    },
]

BASE_ORDER_DETAILS = {
    "N20260305000012": {
        "order_id": "N20260305000012",
        "user_id": "api_ticket_probe",
        "status": "delivered",
        "status_label": "已签收",
        "amount": 699,
        "created_at": "2026-03-05T10:00:00+08:00",
        "source_channel": "app",
        "items": [
            {
                "order_item_id": "OI202603050001",
                "product_id": "P_RICE_4L_A",
                "sku_id": "SKU_RICE_4L_WHITE",
                "name": "小熊电饭煲 4L 白色",
                "qty": 1,
                "price": 699,
                "image": "https://mock.local/rice-4l-white.png",
            },
            {
                "order_item_id": "OI202603050002",
                "product_id": "P_RICE_4L_B",
                "sku_id": "SKU_RICE_4L_BLACK",
                "name": "小熊电饭煲 4L 黑色",
                "qty": 1,
                "price": 699,
                "image": "https://mock.local/rice-4l-black.png",
            },
        ],
    },
    "N20260306000034": {
        "order_id": "N20260306000034",
        "user_id": "api_ticket_probe",
        "status": "delivered",
        "status_label": "已签收",
        "amount": 59,
        "created_at": "2026-03-06T09:30:00+08:00",
        "source_channel": "app",
        "items": [
            {
                "order_item_id": "OI202603060001",
                "product_id": "P_SOCK_3PACK",
                "sku_id": "SKU_SOCK_3PACK_MIX",
                "name": "纯棉袜子 3双装",
                "qty": 1,
                "price": 59,
                "image": "https://mock.local/socks-3pack.png",
            }
        ],
    },
}

BASE_PRODUCTS = [
    {
        "product_id": "P_RICE_4L_A",
        "sku_id": "SKU_RICE_4L_WHITE",
        "name": "小熊电饭煲 4L 白色",
        "price": 699,
        "status": "on_sale",
        "stock": 24,
        "image": "https://mock.local/product/rice-4l-white.png",
        "created_at": "2026-03-01T10:00:00+08:00",
        "updated_at": "2026-04-17T09:00:00+08:00",
    },
    {
        "product_id": "P_RICE_4L_B",
        "sku_id": "SKU_RICE_4L_BLACK",
        "name": "小熊电饭煲 4L 黑色",
        "price": 699,
        "status": "on_sale",
        "stock": 6,
        "image": "https://mock.local/product/rice-4l-black.png",
        "created_at": "2026-03-08T10:00:00+08:00",
        "updated_at": "2026-04-18T08:30:00+08:00",
    },
]

BASE_PRODUCT_DETAILS = {
    "P_RICE_4L_A": {
        "product_id": "P_RICE_4L_A",
        "name": "小熊电饭煲 4L 白色",
        "description": "4L 容量，白色款。",
        "brand": "小熊",
        "category": "电饭煲",
        "price": 699,
        "images": ["https://mock.local/product/rice-4l-white.png"],
    },
    "P_RICE_4L_B": {
        "product_id": "P_RICE_4L_B",
        "name": "小熊电饭煲 4L 黑色",
        "description": "4L 容量，黑色款。",
        "brand": "小熊",
        "category": "电饭煲",
        "price": 699,
        "images": ["https://mock.local/product/rice-4l-black.png"],
    },
}

BASE_TICKETS = [
    {
        "ticket_id": "TK202604150001",
        "ticket_type": "quality",
        "biz_id": "N20260305000012",
        "status": "processing",
        "status_label": "处理中",
        "title": "电饭煲内胆破损",
        "source_channel": "app",
        "created_at": "2026-04-15T11:00:00+08:00",
    },
    {
        "ticket_id": "TK202604160002",
        "ticket_type": "quality",
        "biz_id": "N20260305000012",
        "status": "open",
        "status_label": "待处理",
        "title": "电饭煲锅盖异响",
        "source_channel": "app",
        "created_at": "2026-04-16T15:20:00+08:00",
    },
]

BASE_TICKET_DETAILS = {
    "TK202604150001": {
        "ticket_id": "TK202604150001",
        "ticket_type": "quality",
        "biz_id": "N20260305000012",
        "status": "processing",
        "status_label": "处理中",
        "title": "电饭煲内胆破损",
        "content": "用户反馈电饭煲内胆破损，希望申请售后。",
        "description": "内胆边缘破损，影响正常使用。",
        "images": [
            "https://mock.local/evidence/rice-dent-closeup.png",
            "https://mock.local/evidence/rice-body.png",
            "https://mock.local/evidence/rice-order.png",
        ],
        "order_id": "N20260305000012",
        "order_item_id": "OI202603050001",
        "source_channel": "app",
        "latest_progress": "已进入质检审核",
        "expected_finish_time": "2026-04-20T18:00:00+08:00",
        "timeline": [
            {"time": "2026-04-15T11:00:00+08:00", "action": "工单创建", "operator": "system"},
            {"time": "2026-04-16T10:00:00+08:00", "action": "进入质检审核", "operator": "agent"},
        ],
    },
    "TK202604160002": {
        "ticket_id": "TK202604160002",
        "ticket_type": "quality",
        "biz_id": "N20260305000012",
        "status": "open",
        "status_label": "待处理",
        "title": "电饭煲锅盖异响",
        "content": "用户反馈锅盖异响，等待进一步确认。",
        "description": "开盖时有异响。",
        "images": [],
        "order_id": "N20260305000012",
        "order_item_id": "OI202603050002",
        "source_channel": "app",
        "latest_progress": "等待客服确认",
        "expected_finish_time": "2026-04-22T18:00:00+08:00",
        "timeline": [
            {"time": "2026-04-16T15:20:00+08:00", "action": "工单创建", "operator": "system"},
        ],
    },
}

BASE_SCORE_RECORDS = [
    {
        "record_id": "R1",
        "change": 100,
        "type": "earn",
        "reason": "购买获得",
        "time": "2026-04-01T10:00:00+08:00",
    },
    {
        "record_id": "R2",
        "change": -50,
        "type": "deduct",
        "reason": "积分兑换",
        "time": "2026-04-18T09:30:00+08:00",
    },
]

BASE_USER_EVENTS = [
    {
        "event_id": "E1",
        "type": "view",
        "target": "P_RICE_4L_A",
        "description": "浏览电饭煲商品页",
        "time": "2026-04-17T20:00:00+08:00",
    },
    {
        "event_id": "E2",
        "type": "purchase",
        "target": "N20260305000012",
        "description": "完成电饭煲订单支付",
        "time": "2026-04-16T12:00:00+08:00",
    },
]

USER_BEHAVIOR_EVENTS = {
    "user_123456": [
        {
            "event_id": "evt_001",
            "user_id": "user_123456",
            "event_type": "product_view",
            "event_time": "2024-06-15T10:30:00Z",
            "channel": "miniprogram",
            "properties": {
                "product_id": "P10086",
                "product_name": "MEXICO 66 复古休闲鞋",
                "price": 850.00,
            },
        },
        {
            "event_id": "evt_002",
            "user_id": "user_123456",
            "event_type": "add_to_cart",
            "event_time": "2024-06-15T10:35:00Z",
            "channel": "miniprogram",
            "properties": {
                "product_id": "P10086",
                "sku_id": "1183C123-4",
                "quantity": 1,
                "price": 850.00,
            },
        },
        {
            "event_id": "evt_003",
            "user_id": "user_123456",
            "event_type": "purchase",
            "event_time": "2024-06-16T09:20:00Z",
            "channel": "app",
            "properties": {
                "order_id": "N202406160001",
                "amount": 850.00,
                "payment_method": "wechat_pay",
            },
        },
    ],
    "api_ticket_probe": [
        {
            "event_id": "evt_probe_001",
            "user_id": "api_ticket_probe",
            "event_type": "product_view",
            "event_time": "2026-04-18T19:20:00+08:00",
            "channel": "app",
            "properties": {
                "product_id": "P_RICE_4L_A",
                "product_name": "小熊电饭煲 4L 白色",
                "price": 699.00,
            },
        },
        {
            "event_id": "evt_probe_002",
            "user_id": "api_ticket_probe",
            "event_type": "add_to_cart",
            "event_time": "2026-04-18T19:23:00+08:00",
            "channel": "app",
            "properties": {
                "product_id": "P_RICE_4L_A",
                "sku_id": "SKU_RICE_4L_WHITE",
                "quantity": 1,
                "price": 699.00,
            },
        },
        {
            "event_id": "evt_probe_003",
            "user_id": "api_ticket_probe",
            "event_type": "service_consult",
            "event_time": "2026-04-20T10:15:00+08:00",
            "channel": "wechat",
            "properties": {
                "topic": "quality_after_sales",
                "thread_id": "thread_probe_001",
            },
        },
    ],
}

USER_PROFILES = {
    "user_123456": {
        "user_id": "user_123456",
        "profile": {
            "basic_info": {
                "name": "张*三",
                "gender": "male",
                "age_range": "25-30",
                "city": "上海市",
                "member_level": "Gold",
            },
            "value_segment": {
                "rfm_level": "高价值",
                "lifetime_value": 15800,
                "avg_order_value": 1260,
            },
            "preferences": {
                "product_categories": ["鞋类", "服装"],
                "product_series": ["MEXICO 66", "DELEGATION"],
                "price_sensitivity": "medium",
            },
            "behavior_summary": {
                "last_active_time": "2024-06-15T10:30:00Z",
                "active_frequency": "high",
                "favorite_channels": ["微信小程序", "天猫旗舰店"],
            },
            "social": {
                "is_koc": True,
                "influence_score": 82,
                "active_groups": ["Onitsuka Tiger 粉丝群"],
            },
        },
        "last_update": "2024-06-15T10:30:00Z",
    },
    "api_ticket_probe": {
        "user_id": "api_ticket_probe",
        "profile": {
            "basic_info": {
                "name": "Bonnie",
                "gender": "female",
                "age_range": "31-40",
                "city": "上海市",
                "member_level": "Gold",
            },
            "value_segment": {
                "rfm_level": "潜力",
                "lifetime_value": 6320,
                "avg_order_value": 790,
            },
            "preferences": {
                "product_categories": ["鞋类", "配饰"],
                "product_series": ["SERRANO", "MEXICO 66"],
                "price_sensitivity": "medium",
            },
            "behavior_summary": {
                "last_active_time": "2026-04-20T18:20:00+08:00",
                "active_frequency": "high",
                "favorite_channels": ["微信小程序", "线下门店"],
            },
            "social": {
                "is_koc": False,
                "influence_score": 46,
                "active_groups": ["华东会员体验群"],
            },
        },
        "last_update": "2026-04-20T18:20:00+08:00",
    },
}

USER_PROFILE_FIELDS = {
    "basic_info",
    "value_segment",
    "preferences",
    "behavior_summary",
    "social",
}


def _parse_iso_datetime(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    normalized = str(raw).strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid datetime: {raw}") from exc


def _get_item_datetime(item: Dict[str, Any], *field_names: str) -> datetime | None:
    for field_name in field_names:
        raw_value = item.get(field_name)
        if raw_value:
            return _parse_iso_datetime(str(raw_value))
    return None


def _within_time_range(item_time: datetime | None, start_time: str | None, end_time: str | None) -> bool:
    start_dt = _parse_iso_datetime(start_time)
    end_dt = _parse_iso_datetime(end_time)
    if not start_dt and not end_dt:
        return True
    if item_time is None:
        return False
    if start_dt and item_time < start_dt:
        return False
    if end_dt and item_time > end_dt:
        return False
    return True


def _paginate(items: list[Dict[str, Any]], page: int, page_size: int) -> Dict[str, Any]:
    normalized_page = max(int(page or 1), 1)
    normalized_page_size = max(min(int(page_size or 20), 100), 1)
    total = len(items)
    start = (normalized_page - 1) * normalized_page_size
    end = start + normalized_page_size
    return {
        "total": total,
        "page": normalized_page,
        "page_size": normalized_page_size,
        "has_more": end < total,
        "items": items[start:end],
    }


def _require_order(order_id: str) -> Dict[str, Any]:
    detail = deepcopy(BASE_ORDER_DETAILS.get(order_id))
    if not detail:
        raise HTTPException(status_code=404, detail="order not found")
    return detail


def _require_product(product_id: str) -> Dict[str, Any]:
    detail = deepcopy(BASE_PRODUCT_DETAILS.get(product_id))
    if not detail:
        raise HTTPException(status_code=404, detail="product not found")
    return detail


def _require_ticket(ticket_id: str) -> Dict[str, Any]:
    detail = deepcopy(BASE_TICKET_DETAILS.get(ticket_id))
    if not detail:
        raise HTTPException(status_code=404, detail="ticket not found")
    return detail


def _filter_profile_fields(profile: Dict[str, Any], fields: str | None) -> Dict[str, Any]:
    if fields is None:
        return deepcopy(profile)

    requested_fields = [item.strip() for item in str(fields).split(",") if item.strip()]
    if not requested_fields:
        return deepcopy(profile)

    invalid_fields = [item for item in requested_fields if item not in USER_PROFILE_FIELDS]
    if invalid_fields:
        raise HTTPException(status_code=400, detail=f"invalid fields: {', '.join(invalid_fields)}")

    return {field_name: deepcopy(profile[field_name]) for field_name in requested_fields if field_name in profile}


def _split_csv(raw_value: str | None) -> set[str]:
    return {item.strip() for item in str(raw_value or "").split(",") if item.strip()}


def _event_time_sort_key(item: Dict[str, Any]) -> float:
    event_dt = _get_item_datetime(item, "event_time")
    if event_dt is None:
        return float("-inf")
    return event_dt.timestamp()


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/scrm/user_profile")
def get_user_profile(user_id: str | None = None, fields: str | None = None) -> Any:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return _scrm_error(400, "Missing required parameter: user_id")

    profile_record = deepcopy(USER_PROFILES.get(normalized_user_id))
    if not profile_record:
        return _scrm_error(404, "User not found")

    try:
        filtered_profile = _filter_profile_fields(profile_record["profile"], fields)
    except HTTPException as exc:
        return _scrm_error(exc.status_code, str(exc.detail))

    return _scrm_success(
        {
            "user_id": profile_record["user_id"],
            "profile": filtered_profile,
            "last_update": profile_record["last_update"],
        }
    )


def _query_user_behavior_response(
    user_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    event_types: str | None = None,
    channels: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str = "desc",
) -> Any:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return _scrm_error(400, "Missing required parameter: user_id")

    events = deepcopy(USER_BEHAVIOR_EVENTS.get(normalized_user_id))
    if events is None:
        return _scrm_error(404, "User not found")

    normalized_sort = str(sort or "desc").strip().lower() or "desc"
    if normalized_sort not in {"asc", "desc"}:
        return _scrm_error(400, "Invalid parameter: sort")

    filtered_events = [
        event
        for event in events
        if _within_time_range(_get_item_datetime(event, "event_time"), start_time, end_time)
    ]

    requested_event_types = _split_csv(event_types)
    if requested_event_types:
        filtered_events = [
            event for event in filtered_events if str(event.get("event_type") or "").strip() in requested_event_types
        ]

    requested_channels = _split_csv(channels)
    if requested_channels:
        filtered_events = [
            event for event in filtered_events if str(event.get("channel") or "").strip() in requested_channels
        ]

    filtered_events.sort(
        key=_event_time_sort_key,
        reverse=normalized_sort == "desc",
    )

    page_result = _paginate(filtered_events, page, page_size)
    page_payload = {key: value for key, value in page_result.items() if key not in {"items", "has_more"}}
    return _scrm_success({**page_payload, "events": page_result["items"]})


@app.get("/order")
def list_orders(
    status: str | None = None,
    keyword: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    orders = deepcopy(BASE_ORDERS)
    normalized_keyword = str(keyword or "").strip().lower()
    if status:
        orders = [order for order in orders if str(order.get("status") or "").strip() == status]
    if normalized_keyword:
        orders = [
            order
            for order in orders
            if normalized_keyword in str(order.get("order_id") or "").lower()
            or normalized_keyword in str(order.get("items_summary") or "").lower()
        ]
    orders = [
        order
        for order in orders
        if _within_time_range(_get_item_datetime(order, "created_at", "updated_at"), start_time, end_time)
    ]
    page_result = _paginate(orders, page, page_size)
    page_payload = {key: value for key, value in page_result.items() if key != "items"}
    return _ok({**page_payload, "orders": page_result["items"]})


@app.get("/order/{order_id}")
def get_order(order_id: str) -> Dict[str, Any]:
    return _ok(_require_order(order_id))


@app.get("/product")
def list_products(
    keyword: str | None = None,
    sku_id: str | None = None,
    product_id: str | None = None,
    status: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    products = deepcopy(BASE_PRODUCTS)
    normalized_keyword = str(keyword or "").strip().lower()
    if sku_id:
        products = [product for product in products if str(product.get("sku_id") or "").strip() == sku_id]
    if product_id:
        products = [product for product in products if str(product.get("product_id") or "").strip() == product_id]
    if status:
        products = [product for product in products if str(product.get("status") or "").strip() == status]
    if normalized_keyword:
        products = [
            product
            for product in products
            if normalized_keyword in str(product.get("name") or "").lower()
            or normalized_keyword in str(product.get("sku_id") or "").lower()
            or normalized_keyword in str(product.get("product_id") or "").lower()
        ]
    products = [
        product
        for product in products
        if _within_time_range(_get_item_datetime(product, "updated_at", "created_at"), start_time, end_time)
    ]
    page_result = _paginate(products, page, page_size)
    page_payload = {key: value for key, value in page_result.items() if key != "items"}
    return _ok({**page_payload, "products": page_result["items"]})


@app.get("/product/{product_id}")
def get_product(product_id: str) -> Dict[str, Any]:
    return _ok(_require_product(product_id))


@app.get("/product/stock/{product_id}")
def get_stock(product_id: str) -> Dict[str, Any]:
    _require_product(product_id)
    return _ok(
        {
            "product_id": product_id,
            "total_stock": 24,
            "available_stock": 20,
            "locked_stock": 4,
            "updated_at": "2026-04-18T10:00:00+08:00",
        }
    )


@app.get("/logistic")
def get_logistic(order_id: str) -> Dict[str, Any]:
    _require_order(order_id)
    return _ok(
        {
            "order_id": order_id,
            "carrier": "顺丰",
            "tracking_no": "SF12345678",
            "status": "delivered",
            "status_label": "已签收",
            "tracks": [
                {"time": "2026-03-07T13:00:00+08:00", "desc": "已签收", "location": "上海"}
            ],
        }
    )


@app.get("/user")
def get_user() -> Dict[str, Any]:
    return _ok(
        {
            "user_id": "api_ticket_probe",
            "name": "Bonnie",
            "member_level": "gold",
            "score": 8500,
            "total_orders": 18,
            "tags": ["高频购买", "厨房电器"],
        }
    )


@app.get("/user/tag")
def get_user_tag() -> Dict[str, Any]:
    return _ok(
        {
            "user_id": "api_ticket_probe",
            "tags": ["高频购买", "厨房电器"],
            "tag_details": [{"code": "kitchen", "name": "厨房电器偏好"}],
        }
    )


@app.get("/user/level")
def get_user_level() -> Dict[str, Any]:
    return _ok(
        {
            "user_id": "api_ticket_probe",
            "level": "gold",
            "level_code": "gold",
            "score": 8500,
            "score_to_next": 1500,
            "next_level": "platinum",
        }
    )


@app.get("/user/score")
def get_user_score(
    page: int = 1,
    page_size: int = 20,
    start_time: str | None = None,
    end_time: str | None = None,
) -> Dict[str, Any]:
    records = [
        record
        for record in deepcopy(BASE_SCORE_RECORDS)
        if _within_time_range(_get_item_datetime(record, "time"), start_time, end_time)
    ]
    page_result = _paginate(records, page, page_size)
    page_payload = {key: value for key, value in page_result.items() if key != "items"}
    return _ok(
        {
            "user_id": "api_ticket_probe",
            "score_balance": 8500,
            **page_payload,
            "records": page_result["items"],
        }
    )


@app.get("/user/eventLog")
def get_user_behavior(
    user_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    event_types: str | None = None,
    channels: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str = "desc",
) -> Any:
    return _query_user_behavior_response(
        user_id=user_id,
        start_time=start_time,
        end_time=end_time,
        event_types=event_types,
        channels=channels,
        page=page,
        page_size=page_size,
        sort=sort,
    )


@app.post("/user/upgrade")
def upgrade_membership(payload: Dict[str, Any]) -> Dict[str, Any]:
    target_level = str(payload.get("target_level") or "").strip()
    if not target_level:
        raise HTTPException(status_code=400, detail="target_level required")
    return _ok(
        {
            "user_id": "api_ticket_probe",
            "current_level": "gold",
            "target_level": target_level,
            "status": "rejected",
            "reason": "积分不足，暂不满足升级条件",
        }
    )


@app.post("/coupon/compensation")
def issue_coupon(payload: Dict[str, Any]) -> Dict[str, Any]:
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason required")
    return _ok({"coupon_id": "C202604180001", "status": "issued", "reason": reason})


@app.get("/ticket")
def list_tickets(
    ticket_type: str | None = None,
    biz_id: str | None = None,
    source_channel: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    tickets = deepcopy(BASE_TICKETS)
    normalized_keyword = str(keyword or "").strip().lower()
    if ticket_type:
        tickets = [ticket for ticket in tickets if str(ticket.get("ticket_type") or "").strip() == ticket_type]
    if biz_id:
        tickets = [ticket for ticket in tickets if str(ticket.get("biz_id") or "").strip() == biz_id]
    if source_channel:
        tickets = [ticket for ticket in tickets if str(ticket.get("source_channel") or "").strip() == source_channel]
    if status:
        tickets = [ticket for ticket in tickets if str(ticket.get("status") or "").strip() == status]
    if normalized_keyword:
        tickets = [
            ticket
            for ticket in tickets
            if normalized_keyword in str(ticket.get("ticket_id") or "").lower()
            or normalized_keyword in str(ticket.get("title") or "").lower()
        ]
    tickets = [
        ticket
        for ticket in tickets
        if _within_time_range(_get_item_datetime(ticket, "created_at", "updated_at"), start_time, end_time)
    ]
    page_result = _paginate(tickets, page, page_size)
    page_payload = {key: value for key, value in page_result.items() if key != "items"}
    return _ok({**page_payload, "tickets": page_result["items"]})


@app.get("/ticket/{ticket_id}")
def get_ticket(ticket_id: str) -> Dict[str, Any]:
    return _ok(_require_ticket(ticket_id))


@app.post("/ticket")
def create_ticket(payload: Dict[str, Any]) -> Dict[str, Any]:
    ticket_type = str(payload.get("ticket_type") or "").strip()
    biz_id = str(payload.get("biz_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not all([ticket_type, biz_id, title, content]):
        raise HTTPException(status_code=400, detail="ticket_type, biz_id, title, content are required")
    ticket_id = "TK202604180003"
    return _ok(
        {
            "ticket_id": ticket_id,
            "ticket_type": ticket_type,
            "biz_id": biz_id,
            "status": "open",
            "status_label": "待处理",
            "title": title,
            "content": content,
            "latest_progress": "工单已创建，等待处理",
            "expected_finish_time": "2026-04-25T18:00:00+08:00",
        }
    )
