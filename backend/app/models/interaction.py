from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

InteractionType = Literal[
    "select_order",
    "select_product",
    "select_ticket",
    "confirm_order",
    "confirm_product",
    "confirm_ticket",
    "confirm",
]


class _InteractionBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class OrderLineItemDetail(_InteractionBaseModel):
    order_item_id: Optional[str] = None
    product_id: Optional[str] = None
    sku_id: Optional[str] = None
    name: Optional[str] = None
    qty: Optional[Union[int, str]] = None


class SelectOrderDetail(_InteractionBaseModel):
    order_id: str
    status_label: str
    source_channel: str


class SelectProductDetail(_InteractionBaseModel):
    product_id: str
    sku_id: str
    name: str
    qty: Optional[Union[int, str]] = None
    order_item_id: Optional[str] = None


class SelectTicketDetail(_InteractionBaseModel):
    ticket_id: str
    ticket_type: str
    status: str
    title: str


class ConfirmOrderDetail(_InteractionBaseModel):
    order_id: str
    status_label: str
    items: List[OrderLineItemDetail] = Field(default_factory=list)


class ConfirmProductDetail(_InteractionBaseModel):
    order_item_id: Optional[str] = None
    product_id: str
    sku_id: str
    name: str
    qty: Optional[Union[int, str]] = None


class ConfirmTicketDetail(_InteractionBaseModel):
    ticket_type: str
    biz_id: str
    ticket_id: Optional[str] = None
    title: str


class ConfirmActionDetail(_InteractionBaseModel):
    action: str


InteractionDetail = Annotated[
    Union[
        SelectOrderDetail,
        SelectProductDetail,
        SelectTicketDetail,
        ConfirmOrderDetail,
        ConfirmProductDetail,
        ConfirmTicketDetail,
        ConfirmActionDetail,
    ],
    Field(discriminator=None),
]


_DETAIL_MODEL_BY_TYPE = {
    "select_order": SelectOrderDetail,
    "select_product": SelectProductDetail,
    "select_ticket": SelectTicketDetail,
    "confirm_order": ConfirmOrderDetail,
    "confirm_product": ConfirmProductDetail,
    "confirm_ticket": ConfirmTicketDetail,
    "confirm": ConfirmActionDetail,
}

_INTERACTION_EXAMPLES: Dict[str, Dict[str, Any]] = {
    "select_order": {
        "interaction_type": "select_order",
        "items": [
            {
                "key": "N20260305000012",
                "label": "订单 N20260305000012（待发货）",
                "detail": {
                    "order_id": "N20260305000012",
                    "status_label": "待发货",
                    "source_channel": "微信小程序",
                },
                "selectable": True,
            },
            {
                "key": "N20260305000018",
                "label": "订单 N20260305000018（已签收）",
                "detail": {
                    "order_id": "N20260305000018",
                    "status_label": "已签收",
                    "source_channel": "APP",
                },
                "selectable": True,
            },
        ],
    },
    "select_product": {
        "interaction_type": "select_product",
        "items": [
            {
                "key": "OI202603050001",
                "label": "燕窝胶原蛋白饮 6 瓶装 x1",
                "detail": {
                    "product_id": "P100286",
                    "sku_id": "SKU889201",
                    "name": "燕窝胶原蛋白饮 6 瓶装",
                    "qty": 1,
                },
                "selectable": True,
            },
            {
                "key": "OI202603050002",
                "label": "益生菌冻干粉 30 条装 x2",
                "detail": {
                    "product_id": "P100315",
                    "sku_id": "SKU889255",
                    "name": "益生菌冻干粉 30 条装",
                    "qty": 2,
                },
                "selectable": True,
            },
        ],
    },
    "select_ticket": {
        "interaction_type": "select_ticket",
        "items": [
            {
                "key": "TK202603060021",
                "label": "退货申请｜燕窝胶原蛋白饮｜处理中",
                "detail": {
                    "ticket_id": "TK202603060021",
                    "ticket_type": "refund",
                    "status": "processing",
                    "title": "燕窝胶原蛋白饮退货申请",
                    "description": "用户反馈商品破损，申请退货处理",
                    "images": [
                        "https://cdn.example.com/ticket/TK202603060021-1.jpg"
                    ],
                    "expected_finish_time": "2026-03-07T18:00:00+08:00",
                },
                "selectable": True,
            },
            {
                "key": "TK202603060028",
                "label": "质量投诉｜益生菌冻干粉｜待补充材料",
                "detail": {
                    "ticket_id": "TK202603060028",
                    "ticket_type": "complain",
                    "status": "pending_material",
                    "title": "益生菌冻干粉质量投诉",
                    "description": "用户投诉商品存在质量异常，待补充凭证",
                    "images": [
                        "https://cdn.example.com/ticket/TK202603060028-1.jpg"
                    ],
                    "expected_finish_time": "2026-03-08T18:00:00+08:00",
                },
                "selectable": True,
            },
        ],
    },
    "confirm_order": {
        "interaction_type": "confirm_order",
        "items": [
            {
                "key": "confirm_order_N20260305000012",
                "label": "确认订单 N20260305000012（待发货）",
                "detail": {
                    "order_id": "N20260305000012",
                    "status_label": "待发货",
                    "items": [
                        {
                            "order_item_id": "OI202603050001",
                            "product_id": "P100286",
                            "sku_id": "SKU889201",
                            "name": "燕窝胶原蛋白饮 6 瓶装",
                            "qty": 1,
                        },
                        {
                            "order_item_id": "OI202603050002",
                            "product_id": "P100315",
                            "sku_id": "SKU889255",
                            "name": "益生菌冻干粉 30 条装",
                            "qty": 2,
                        }
                    ],
                },
                "selectable": True,
            },
        ],
    },
    "confirm_product": {
        "interaction_type": "confirm_product",
        "items": [
            {
                "key": "confirm_product_OI202603050001",
                "label": "确认商品：燕窝胶原蛋白饮 6 瓶装 x1",
                "detail": {
                    "order_item_id": "OI202603050001",
                    "product_id": "P100286",
                    "sku_id": "SKU889201",
                    "name": "燕窝胶原蛋白饮 6 瓶装",
                    "qty": 1,
                },
                "selectable": True,
            },
        ],
    },
    "confirm_ticket": {
        "interaction_type": "confirm_ticket",
        "items": [
            {
                "key": "confirm_ticket_refund_P100286",
                "label": "确认创建退货工单：燕窝胶原蛋白饮 6 瓶装",
                "detail": {
                    "ticket_type": "refund",
                    "biz_id": "P100286",
                    "ticket_id": "",
                    "title": "燕窝胶原蛋白饮退货申请",
                    "description": "商品破损，申请退货退款",
                    "images": [
                        "https://cdn.example.com/evidence/order-1.jpg",
                        "https://cdn.example.com/evidence/product-1.jpg",
                    ],
                },
                "selectable": True,
            },
        ],
    },
    "confirm": {
        "interaction_type": "confirm",
        "items": [
            {
                "key": "confirm",
                "label": "确认并继续处理",
                "detail": {"action": "confirm"},
                "selectable": True,
            },
            {
                "key": "cancel",
                "label": "取消本次操作",
                "detail": {"action": "cancel"},
                "selectable": True,
            },
            {
                "key": "retry",
                "label": "重试上一步",
                "detail": {"action": "retry"},
                "selectable": True,
            },
        ],
    },
}


class InteractionItem(BaseModel):
    key: str
    label: str
    detail: InteractionDetail
    selectable: bool = False


class InteractionPayload(BaseModel):
    interaction_type: InteractionType
    items: List[InteractionItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_detail_by_type(cls, value: Any):
        if not isinstance(value, dict):
            return value

        interaction_type = str(value.get("interaction_type") or "").strip()
        detail_model = _DETAIL_MODEL_BY_TYPE.get(interaction_type)
        if not detail_model:
            return value

        normalized = dict(value)
        normalized_items: List[Dict[str, Any]] = []
        for item in value.get("items") or []:
            if not isinstance(item, dict):
                normalized_items.append(item)
                continue
            normalized_item = dict(item)
            detail = normalized_item.get("detail")
            if isinstance(detail, BaseModel):
                normalized_item["detail"] = detail
            elif isinstance(detail, dict):
                normalized_item["detail"] = detail_model.model_validate(detail)
            elif detail is None:
                normalized_item["detail"] = None
            normalized_items.append(normalized_item)
        normalized["items"] = normalized_items
        return normalized

    @model_validator(mode="after")
    def _validate_non_empty_items(self):
        if not self.items:
            raise ValueError("interaction.items must not be empty")
        return self

    def model_dump(self, *args, **kwargs):
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(*args, **kwargs)


def build_interaction_template_text() -> str:
    lines: List[str] = [
        "【交互模板】",
        "",
        "以下内容用于在执行阶段根据当前步骤信息、当前可用槽位和当前步骤结果，拼装最终发给前端的 interaction。",
        "interaction 的结构固定，不要改字段名，不要新增字段，不要输出空 items 或空 detail。",
        "当某种 interaction_type 的必需字段还不够时，不要硬拼假的 interaction，应先继续收敛信息。",
        "",
        "通用结构：",
        "```json",
        json.dumps(
            {
                "interaction_type": "固定交互类型",
                "items": [
                    {
                        "key": "用户点击后回传的关键值",
                        "label": "给用户展示的文本",
                        "detail": {"...": "当前交互对象详情"},
                        "selectable": True,
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        "```",
        "",
        "字段填充规则：",
        "- `key`：填写用户选择后应回传的关键值。",
        "- `label`：填写给用户直接看的展示文案。",
        "- `detail`：严格按该 interaction_type 的固定结构填写，使用当前步骤已经拿到的真实数据。",
        "- 若某字段当前没有真实值，不要伪造；应先继续收敛信息，而不是输出错误 interaction。",
        "- `status_label`：订单状态展示文案，例如“待发货 / 已签收 / 已完成”。",
        "- `status`：工单状态机器值，例如 `processing / pending_material / closed`；若同时需要给用户展示，再由 `label` 或其他展示文本表达。",
        "- `images`：图片数组，应为实际图片 URL 列表；不是单个字符串。",
        "- `action`：通用确认场景下的动作枚举，仅允许 `confirm`、`cancel`、`retry`。",
        "",
        "各类型固定结构示例：",
    ]

    for interaction_type, example in _INTERACTION_EXAMPLES.items():
        InteractionPayload.model_validate(example)
        lines.extend(
            [
                "",
                f"`{interaction_type}` 固定结构示例：",
                "```json",
                json.dumps(example, ensure_ascii=False, indent=2),
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "执行规则：",
            "- 当当前步骤 type = `interacting` 时，先根据步骤中的 `interaction_type` 确定要使用哪种固定模板。",
            "- 再结合当前步骤信息、当前可用 slots、当前步骤实际拿到的数据，拼装最终 interaction。",
            "- 中断时必须同时提供 `reply` 和拼装好的 `interaction`，即调用 `interrupt(reply=..., interaction=...)`。",
        ]
    )
    return "\n".join(lines)
