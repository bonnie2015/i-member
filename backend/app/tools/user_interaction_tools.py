from __future__ import annotations

import json
from typing import Any, Dict, Literal, Optional, cast

from langchain_core.tools import tool
from langgraph.types import interrupt as graph_interrupt
from pydantic import BaseModel, Field

from app.tools.business.execution_context import (
    get_business_execution_context,
    get_ticket_interaction_sources,
)
from app.config.logging import get_logger
from app.models.display_product import DisplayProductCard
from app.models.interaction import (
    InteractionEntity,
    InteractionType,
    OrderInteractionDetail,
    ProductInteractionDetail,
    TicketInteractionDetail,
    build_interaction_payload,
)

logger = get_logger("user_interaction_tools")


class InterruptToolInput(BaseModel):
    reply: str = Field(
        description=(
            "当需要向用户追问、交互、确认、获取信息时调用 ask_user。"
            "只能追问当前步骤非常需要、用户能回答、能直接推进业务的信息。"
            "不要追问已确定槽位、系统内部字段、低价值辅助字段，或工具可直接查询的信息。"
            "不要编造询问目的。"
            "reply 是直接发给用户的说明或问题，不重复结构化候选项内容。"
        )
    )
    interaction_type: Optional[InteractionType] = Field(
        default=None,
        description=(
            "可选结构化交互类型。"
            "可选值：select_order、select_product、select_ticket、confirm_order、confirm_product、confirm_ticket。"
            "纯文本澄清时留空。必须与 candidate_keys 同时传"
            "工具内部会从已执行工具的返回结果中提取交互实体，"
            "并按 candidate_keys 筛选（传了则只展示指定 key 的实体）。"
        ),
    )
    candidate_keys: Optional[list[str]] = Field(
        default=None,
        description=(
            "要展示给用户的候选项 key 列表。传了则只展示这些 key 对应的实体。"
            "key 来源：select_order/confirm_order 用 order_id，select_product/confirm_product 用 product_id 或 order_item_id，"
            "select_ticket/confirm_ticket 用 ticket_id 或 ticket_title。"
            "必须来自本步骤工具返回结果中的真实值，严禁编造。"
            "只展示与用户需求最相关的候选，不要全量展示。"
            "应根据用户意图和业务条件主动筛选：挑出与用户需求最相关的候选，不要全量展示。"
            "例如用户想退鞋类订单，查了全部订单后只传含鞋的 order_id，其他的不传。"
        ),
    )


InterruptDisplayProductInput = DisplayProductCard


class ProductSelectionInput(BaseModel):
    product_id: int = Field(description="商品 ID。不允许编造。")
    color_id: int | None = Field(
        default=None, description="颜色 ID。不允许编造；没有则留空。"
    )


class QaReplyInput(BaseModel):
    reply: str = Field(description="回复给用户的最终答案")


class RecommendationReplyInput(BaseModel):
    reply: str = Field(
        description=(
            "对用户消息的回复、搜索反馈、推荐话术或细节追问等一切对用户的交流都在此字段实现。"
            "如果 products 非空，reply 不要重复商品卡片里的名称、价格、图片、颜色、尺码、库存、链接等信息。"
        )
    )
    products: list[ProductSelectionInput] = Field(
        default_factory=list,
        description=(
            "要展示给用户的真实候选商品引用。"
            "只传 product_id 和 color_id；没有候选商品时传空数组。"
        ),
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_items(value: Any) -> list[Dict[str, Any]]:
    return [item for item in list(value or []) if isinstance(item, dict)]


def _order_entity(payload: Dict[str, Any]) -> OrderInteractionDetail | None:
    order_id = _text(payload.get("order_id") or payload.get("id"))
    if not order_id:
        return None
    preview = []
    for item in _list_items(payload.get("order_items") or payload.get("items"))[:3]:
        preview.append(
            {
                "order_item_id": _text(item.get("order_item_id") or item.get("id")),
                "product_id": _text(item.get("product_id")),
                "product_name": _text(item.get("product_name") or item.get("name")),
                "order_item_quantity": item.get("order_item_quantity")
                or item.get("quantity")
                or item.get("qty"),
                "product_image": _text(item.get("product_image") or item.get("image")),
            }
        )
    return OrderInteractionDetail(
        order_id=order_id,
        order_status_label=_text(
            payload.get("order_status_label") or payload.get("status_label")
        ),
        source_channel=_text(payload.get("source_channel")),
        items_preview=preview,
    )


def _product_entity(
    payload: Dict[str, Any], *, order_id: str = ""
) -> ProductInteractionDetail | None:
    product_id = _text(payload.get("product_id") or payload.get("id"))
    order_item_id = _text(payload.get("order_item_id"))
    if not product_id and not order_item_id:
        return None
    return ProductInteractionDetail(
        product_id=product_id,
        order_id=_text(payload.get("order_id")) or order_id,
        order_item_id=order_item_id,
        sku_id=_text(payload.get("sku_id")),
        product_name=_text(payload.get("product_name") or payload.get("name")),
        order_item_quantity=payload.get("order_item_quantity")
        or payload.get("quantity")
        or payload.get("qty"),
        product_image=_text(payload.get("product_image") or payload.get("image")),
    )


def _ticket_entity(payload: Dict[str, Any]) -> TicketInteractionDetail | None:
    title = _text(payload.get("ticket_title") or payload.get("title"))
    ticket_id = _text(payload.get("ticket_id") or payload.get("id"))
    if not title and not ticket_id:
        return None
    return TicketInteractionDetail(
        ticket_title=title or ticket_id,
        ticket_id=ticket_id,
        ticket_type=_text(payload.get("ticket_type")),
        ticket_status=_text(payload.get("ticket_status") or payload.get("status")),
        ticket_status_label=_text(
            payload.get("ticket_status_label") or payload.get("status_label")
        ),
    )


def _entity_key(entity: InteractionEntity) -> str:
    if isinstance(entity, OrderInteractionDetail):
        return _text(entity.order_id)
    if isinstance(entity, ProductInteractionDetail):
        return _text(entity.order_item_id) or _text(entity.product_id)
    if isinstance(entity, TicketInteractionDetail):
        return _text(entity.ticket_id) or _text(entity.ticket_title)
    return ""


def _extract_interaction_entities(
    interaction_type: InteractionType,
    candidate_keys: list[str] | None = None,
) -> list[InteractionEntity]:
    entities: list[InteractionEntity] = []
    sources = get_ticket_interaction_sources()
    for source in sources:
        if interaction_type in {"select_order", "confirm_order"}:
            for item in _list_items(source.get("orders")):
                entity = _order_entity(item)
                if entity:
                    entities.append(entity)
            entity = _order_entity(source)
            if entity:
                entities.append(entity)

        elif interaction_type in {"select_product", "confirm_product"}:
            for item in _list_items(source.get("products")):
                entity = _product_entity(item)
                if entity:
                    entities.append(entity)
            order_id = _text(source.get("order_id") or source.get("id"))
            for item in _list_items(source.get("order_items") or source.get("items")):
                entity = _product_entity(item, order_id=order_id)
                if entity:
                    entities.append(entity)
            entity = _product_entity(source)
            if entity:
                entities.append(entity)

        elif interaction_type in {"select_ticket", "confirm_ticket"}:
            for item in _list_items(source.get("tickets")):
                entity = _ticket_entity(item)
                if entity:
                    entities.append(entity)
            entity = _ticket_entity(source)
            if entity:
                entities.append(entity)

    deduped: list[InteractionEntity] = []
    seen: set[str] = set()
    for entity in entities:
        key = json.dumps(entity.model_dump(), ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entity)
    return deduped[:10]


def _normalize_interaction(
    interaction_type: InteractionType | str | None,
    candidate_keys: list[str] | None = None,
) -> Dict[str, Any] | None:
    normalized_type = str(interaction_type or "").strip()
    if not normalized_type:
        logger.info("[normalize_interaction] empty interaction_type")
        return None
    typed_interaction_type = cast(InteractionType, normalized_type)
    entities = _extract_interaction_entities(typed_interaction_type)
    # 按 candidate_keys 筛选（传了则只保留匹配的，不传则全保留）
    if candidate_keys:
        key_set = {k for k in candidate_keys if k}
        if key_set:
            entities = [e for e in entities if _entity_key(e) in key_set]
    if not entities:
        return None
    result = build_interaction_payload(
        interaction_type=typed_interaction_type,
        entities=entities,
        selectable=normalized_type.startswith("select"),
    ).model_dump()
    return result


def _find_selected_detail(
    interaction: Dict[str, Any] | None, answer: str
) -> Dict[str, Any] | None:
    if not interaction:
        return None
    normalized_key = str(answer or "").strip()
    if not normalized_key:
        return None

    for item in interaction.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("key") or "").strip() != normalized_key:
            continue
        detail = item.get("detail")
        return detail if isinstance(detail, dict) else None
    return None


@tool("ask_user", args_schema=InterruptToolInput)
def ask_user_tool(
    reply: str,
    interaction_type: InteractionType | None = None,
    candidate_keys: list[str] | None = None,
) -> Dict[str, Any]:
    """向用户发起一次正式追问或交互，并暂停当前服务等待用户回复。

    使用规则：
    - 信息不足、需要用户选择、需要用户确认时调用 ask_user。
    - 只追问当前步骤非常需要、用户能回答、能直接推进业务的信息。
    - 不追问已确定槽位、系统内部字段、低价值辅助字段，或工具可直接查询的信息。
    - 不编造询问目的。
    - 纯文本澄清只传 reply。
    - 需要结构化交互时，传 interaction_type，并用 candidate_keys 精确控制展示哪些候选项。
    - candidate_keys 必须来自工具返回结果中的真实 key（order_id / product_id / ticket_id），根据用户意图筛选。
    - 如传了 candidate_keys 但全不匹配，交互卡片将不生成，此时 reply 必须说明情况。
    """
    normalized_interaction = _normalize_interaction(
        interaction_type, candidate_keys=candidate_keys
    )
    request_payload = {
        "reply": str(reply or "").strip(),
        "interaction": normalized_interaction,
    }
    answer = graph_interrupt(request_payload)
    answer_text = str(answer or "").strip()
    return {
        "answer": answer_text,
        "detail": _find_selected_detail(normalized_interaction, answer_text),
    }


@tool("reply_with_products", args_schema=RecommendationReplyInput, return_direct=True)
def reply_with_products_tool(
    reply: str,
    products: list[ProductSelectionInput] | None = None,
) -> Dict[str, Any]:
    """推荐场景唯一的用户回复出口。结束本轮时必须调用它；可返回商品卡，也可传空 products 做追问或说明。"""
    product_refs = [
        item.model_dump(exclude_none=True, exclude_defaults=True)
        if isinstance(item, ProductSelectionInput)
        else ProductSelectionInput.model_validate(item).model_dump(
            exclude_none=True, exclude_defaults=True
        )
        for item in list(products or [])
    ]
    context = get_business_execution_context()
    logger.info(
        "[reply_with_products] thread_id=%s user_id=%s selected=%s",
        context["thread_id"],
        context["user_id"],
        len(product_refs),
    )
    request_payload = {
        "reply": str(reply or "").strip(),
        "products": product_refs,
    }
    return request_payload


@tool("reply_to_user", args_schema=QaReplyInput, return_direct=True)
async def reply_to_user_tool(reply: str) -> str:
    """向用户发送最终回答。当你已经得出答案，必须调用此工具返回结果。

    Args:
        reply: 回复给用户的最终答案

    Returns:
        确认消息
    """
    return json.dumps({"reply": reply}, ensure_ascii=False)


class FinishStepInput(BaseModel):
    step_status: Literal["done", "pending", "failed", "cancelled"] = Field(
        description="步骤状态"
    )
    slots: Dict[str, Any] = Field(
        default_factory=dict, description="本次新获取并确认的槽位键值对"
    )
    reply: str = Field(
        default="", description="发给用户的自然语言回复，不需回复时填空字符串"
    )
    reason: str = Field(default="", description="简短说明最终决定原因和依据")


@tool("finish_step", args_schema=FinishStepInput, return_direct=True)
def finish_step_tool(
    step_status: str, slots: Dict[str, Any], reply: str, reason: str
) -> Dict[str, Any]:
    """结束当前步骤。必须调用此工具来正式结束步骤。

    使用规则：
    - completion_signal 达成且 target_slots 已填完 → step_status="done"
    - 达到工具调用上限仍未完成 → step_status="pending"
    - 工具调用失败或无法继续推进 → step_status="failed"
    - 用户确认取消/更换任务 → step_status="cancelled"
    - slots 只填本次新获取并确认的值，不传已有槽位
    - reply 是发给用户的最终回复，不需回复时填空
    - reason 简短说明为何做出此决定（如"工具返回完整订单数据，商品名和渠道已确认"）
    """
    return {"status": "ok"}
