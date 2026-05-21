from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

InteractionType = Literal[
    "select_order",
    "select_product",
    "select_ticket",
    "confirm_order",
    "confirm_product",
    "confirm_ticket",
]

_ORDER_INTERACTION_TYPES = {"select_order", "confirm_order"}
_PRODUCT_INTERACTION_TYPES = {"select_product", "confirm_product"}
_TICKET_INTERACTION_TYPES = {"select_ticket", "confirm_ticket"}


class OrderPreviewItem(BaseModel):
    """订单内商品的轻量预览信息。"""

    model_config = ConfigDict(extra="forbid")

    order_item_id: str = ""
    product_id: str = ""
    product_name: str = ""
    order_item_quantity: int | str | None = None
    product_image: str = ""


class OrderInteractionDetail(BaseModel):
    """订单实体。用于 select_order / confirm_order。"""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    order_status_label: str = ""
    source_channel: str = ""
    items_preview: list[OrderPreviewItem] = Field(default_factory=list)


class ProductInteractionDetail(BaseModel):
    """商品实体。用于 select_product / confirm_product。"""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    order_id: str = ""
    order_item_id: str = ""
    sku_id: str = ""
    product_name: str = ""
    order_item_quantity: int | str | None = None
    product_image: str = ""


class TicketInteractionDetail(BaseModel):
    """工单实体。用于 select_ticket / confirm_ticket。"""

    model_config = ConfigDict(extra="forbid")

    ticket_title: str
    ticket_id: str = ""
    ticket_type: str = ""
    ticket_status: str = ""
    ticket_status_label: str = ""


InteractionEntity = Annotated[
    Union[
        OrderInteractionDetail,
        ProductInteractionDetail,
        TicketInteractionDetail,
    ],
    Field(discriminator=None),
]


def _quantity_text(value: int | str | None) -> str:
    if value in (None, ""):
        return ""
    return f" x{value}"


def _build_order_item_key(detail: OrderInteractionDetail) -> str:
    return str(detail.order_id or "").strip()


def _build_order_item_label(
    detail: OrderInteractionDetail, *, interaction_type: InteractionType
) -> str:
    order_id = str(detail.order_id or "").strip()
    status_label = str(detail.order_status_label or "").strip()
    base = f"订单 {order_id}".strip()
    if status_label:
        base = f"{base}（{status_label}）"
    if interaction_type == "confirm_order":
        return f"确认{base}"
    return base


def _build_product_item_key(detail: ProductInteractionDetail) -> str:
    key = str(detail.order_item_id or "").strip()
    if key:
        return key
    return str(detail.product_id or "").strip()


def _build_product_item_label(
    detail: ProductInteractionDetail, *, interaction_type: InteractionType
) -> str:
    product_name = (
        str(detail.product_name or "").strip() or str(detail.product_id or "").strip()
    )
    base = f"{product_name}{_quantity_text(detail.order_item_quantity)}".strip()
    if interaction_type == "confirm_product":
        return f"确认商品：{base}"
    return base


def _build_ticket_item_key(detail: TicketInteractionDetail) -> str:
    ticket_id = str(detail.ticket_id or "").strip()
    if ticket_id:
        return ticket_id
    return str(detail.ticket_title or "").strip()


def _build_ticket_item_label(
    detail: TicketInteractionDetail, *, interaction_type: InteractionType
) -> str:
    title = (
        str(detail.ticket_title or "").strip() or str(detail.ticket_id or "").strip()
    )
    status_label = str(detail.ticket_status_label or "").strip()
    base = title
    if status_label:
        base = f"{base}｜{status_label}"
    if interaction_type == "confirm_ticket":
        return f"确认工单：{base}"
    return base


def _detail_kind(detail: InteractionEntity) -> str:
    if isinstance(detail, OrderInteractionDetail):
        return "order"
    if isinstance(detail, ProductInteractionDetail):
        return "product"
    if isinstance(detail, TicketInteractionDetail):
        return "ticket"
    raise TypeError(f"unsupported interaction detail type: {type(detail).__name__}")


def _expected_detail_kind(interaction_type: InteractionType) -> str:
    if interaction_type in _ORDER_INTERACTION_TYPES:
        return "order"
    if interaction_type in _PRODUCT_INTERACTION_TYPES:
        return "product"
    if interaction_type in _TICKET_INTERACTION_TYPES:
        return "ticket"
    raise ValueError(f"unsupported interaction_type: {interaction_type}")


class InteractionItem(BaseModel):
    """一个可点击的交互候选项。"""

    key: str = Field(description="用户点击后回传的唯一值。")
    label: str = Field(description="给用户展示的简短文案。")
    detail: InteractionEntity = Field(
        description=(
            "所选实体的结构化信息。"
            "select_order / confirm_order 对应订单实体，"
            "select_product / confirm_product 对应商品实体，"
            "select_ticket / confirm_ticket 对应工单实体。"
        )
    )
    selectable: bool = True

    @model_validator(mode="after")
    def _validate_non_empty_fields(self) -> "InteractionItem":
        if not str(self.key or "").strip():
            raise ValueError("interaction item key must not be empty")
        if not str(self.label or "").strip():
            raise ValueError("interaction item label must not be empty")
        return self


class InteractionPayload(BaseModel):
    """发给前端的结构化交互信息。"""

    interaction_type: InteractionType
    items: list[InteractionItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_payload(self) -> "InteractionPayload":
        if not self.items:
            raise ValueError("interaction.items must not be empty")

        expected_kind = _expected_detail_kind(self.interaction_type)
        for item in self.items:
            actual_kind = _detail_kind(item.detail)
            if actual_kind != expected_kind:
                raise ValueError(
                    f"interaction_type={self.interaction_type} requires {expected_kind} detail, got {actual_kind}"
                )
        return self

    def model_dump(self, *args, **kwargs):
        kwargs.setdefault("exclude_none", True)
        kwargs.setdefault("exclude_defaults", True)
        return super().model_dump(*args, **kwargs)


def build_interaction_payload(
    *,
    interaction_type: InteractionType,
    entities: list[InteractionEntity],
    selectable: bool = True,
) -> InteractionPayload:
    items: list[InteractionItem] = []
    for entity in entities:
        if isinstance(entity, OrderInteractionDetail):
            key = _build_order_item_key(entity)
            label = _build_order_item_label(entity, interaction_type=interaction_type)
        elif isinstance(entity, ProductInteractionDetail):
            key = _build_product_item_key(entity)
            label = _build_product_item_label(entity, interaction_type=interaction_type)
        elif isinstance(entity, TicketInteractionDetail):
            key = _build_ticket_item_key(entity)
            label = _build_ticket_item_label(entity, interaction_type=interaction_type)
        else:
            raise TypeError(
                f"unsupported interaction entity type: {type(entity).__name__}"
            )

        items.append(
            InteractionItem(
                key=key,
                label=label,
                detail=entity,
                selectable=selectable,
            )
        )
    return InteractionPayload(
        interaction_type=interaction_type,
        items=items,
    )


def normalize_interaction_entities(
    *,
    interaction_type: InteractionType,
    entities: list[InteractionEntity | dict[str, Any]],
) -> list[InteractionEntity]:
    normalized: list[InteractionEntity] = []
    expected_kind = _expected_detail_kind(interaction_type)
    for entity in entities:
        if expected_kind == "order":
            parsed = (
                entity
                if isinstance(entity, OrderInteractionDetail)
                else OrderInteractionDetail.model_validate(entity)
            )
        elif expected_kind == "product":
            parsed = (
                entity
                if isinstance(entity, ProductInteractionDetail)
                else ProductInteractionDetail.model_validate(entity)
            )
        else:
            parsed = (
                entity
                if isinstance(entity, TicketInteractionDetail)
                else TicketInteractionDetail.model_validate(entity)
            )
        normalized.append(parsed)
    return normalized
