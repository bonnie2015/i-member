from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from fastapi import FastAPI, HTTPException


app = FastAPI(title="Mock SCRM Service")


def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


BASE_ORDERS = [
    {
        "order_id": "N20260305000012",
        "status": "delivered",
        "status_label": "已签收",
        "amount": 699,
        "items_summary": "小熊电饭煲 4L",
        "created_at": "2026-03-05T10:00:00+08:00",
    },
    {
        "order_id": "N20260306000034",
        "status": "delivered",
        "status_label": "已签收",
        "amount": 59,
        "items_summary": "纯棉袜子 3双装",
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
    },
    {
        "product_id": "P_RICE_4L_B",
        "sku_id": "SKU_RICE_4L_BLACK",
        "name": "小熊电饭煲 4L 黑色",
        "price": 699,
        "status": "on_sale",
        "stock": 6,
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
        "created_at": "2026-04-15T11:00:00+08:00",
    },
    {
        "ticket_id": "TK202604160002",
        "ticket_type": "quality",
        "biz_id": "N20260305000012",
        "status": "open",
        "status_label": "待处理",
        "title": "电饭煲锅盖异响",
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


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok"}


@app.get("/order")
def list_orders() -> Dict[str, Any]:
    orders = deepcopy(BASE_ORDERS)
    return _ok({"total": len(orders), "page": 1, "page_size": len(orders), "has_more": False, "orders": orders})


@app.get("/order/{order_id}")
def get_order(order_id: str) -> Dict[str, Any]:
    return _ok(_require_order(order_id))


@app.get("/product")
def list_products() -> Dict[str, Any]:
    products = deepcopy(BASE_PRODUCTS)
    return _ok({"total": len(products), "page": 1, "page_size": len(products), "has_more": False, "products": products})


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
def get_user_score() -> Dict[str, Any]:
    return _ok(
        {
            "user_id": "api_ticket_probe",
            "score_balance": 8500,
            "total": 2,
            "page": 1,
            "page_size": 20,
            "has_more": False,
            "records": [
                {
                    "record_id": "R1",
                    "change": 100,
                    "type": "earn",
                    "reason": "购买获得",
                    "time": "2026-04-01T10:00:00+08:00",
                }
            ],
        }
    )


@app.get("/user/eventLog")
def get_user_behavior() -> Dict[str, Any]:
    return _ok(
        {
            "user_id": "api_ticket_probe",
            "events": [
                {
                    "event_id": "E1",
                    "type": "view",
                    "target": "P_RICE_4L_A",
                    "time": "2026-04-17T20:00:00+08:00",
                }
            ],
        }
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
def list_tickets() -> Dict[str, Any]:
    tickets = deepcopy(BASE_TICKETS)
    return _ok({"total": len(tickets), "page": 1, "page_size": len(tickets), "has_more": False, "tickets": tickets})


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
