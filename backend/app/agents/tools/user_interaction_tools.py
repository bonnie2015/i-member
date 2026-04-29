from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from langchain_core.tools import tool
from langgraph.types import interrupt as graph_interrupt
from pydantic import BaseModel, Field

from app.agents.execution_context import get_execution_context
from app.agents.tools.business.onitsuka_adapter import hydrate_display_products
from app.config.logging import get_logger
from app.models.display_product import DisplayProductCard
from app.models.interaction import (
    InteractionEntity,
    InteractionType,
    build_interaction_payload,
    normalize_interaction_entities,
)

logger = get_logger("user_interaction_tools")


class InterruptToolInput(BaseModel):
    reply: str = Field(
        description=(
            "当需要向用户追问、交互、确认、获取信息时调用 ask_user。"
            "如果你需要用户补充信息，必须调用 ask_user。"
            "如果选择与用户交互，reply 应该是对用户的直接说明或提问，而不需要重复 interaction 里的内容。"
        )
    )
    interaction_type: Optional[InteractionType] = Field(
        default=None,
        description=(
            "可选的交互类型。"
            "当需要结构化交互时填写；否则可以留空。"
        ),
    )
    entities: list[InteractionEntity] = Field(
        default_factory=list,
        description=(
            "可选的真实候选实体列表。"
            "按照 interaction_type 中的实体对应选择"
        ),
    )


InterruptDisplayProductInput = DisplayProductCard


class ProductSelectionInput(BaseModel):
    product_id: int = Field(description="商品 ID。不允许编造。")
    color_id: int | None = Field(default=None, description="颜色 ID。不允许编造；没有则留空。")


class RecommendationReplyInput(BaseModel):
    reply: str = Field(
        description=(
            "发给用户的说明、推荐话术或追问。"
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


def _normalize_interaction(
    *,
    interaction_type: InteractionType | str | None,
    entities: list[InteractionEntity | Dict[str, Any]] | None,
) -> Dict[str, Any] | None:
    normalized_type = str(interaction_type or "").strip()
    if not normalized_type:
        return None
    typed_interaction_type = cast(InteractionType, normalized_type)
    normalized_entities = normalize_interaction_entities(
        interaction_type=typed_interaction_type,
        entities=list(entities or []),
    )
    if not normalized_entities:
        return None
    return build_interaction_payload(
        interaction_type=typed_interaction_type,
        entities=normalized_entities,
    ).model_dump()


def _find_selected_detail(interaction: Dict[str, Any] | None, answer: str) -> Dict[str, Any] | None:
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
    entities: list[InteractionEntity] | None = None,
) -> Dict[str, Any]:
    """向用户发起一次正式追问或交互，并暂停当前服务等待用户回复。"""
    normalized_interaction = _normalize_interaction(
        interaction_type=interaction_type,
        entities=list(entities or []),
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
        else ProductSelectionInput.model_validate(item).model_dump(exclude_none=True, exclude_defaults=True)
        for item in list(products or [])
    ]
    normalized_products = hydrate_display_products(product_refs)
    context = get_execution_context()
    logger.info(
        "[reply_with_products] thread_id=%s user_id=%s requested=%s hydrated=%s",
        context["thread_id"],
        context["user_id"],
        len(product_refs),
        len(normalized_products),
    )
    if product_refs and len(normalized_products) < len(product_refs):
        logger.warning(
            "[reply_with_products] thread_id=%s user_id=%s dropped_uncached_products requested=%s hydrated=%s",
            context["thread_id"],
            context["user_id"],
            len(product_refs),
            len(normalized_products),
        )
    request_payload = {
        "reply": str(reply or "").strip(),
        "products": normalized_products,
    }
    return request_payload
