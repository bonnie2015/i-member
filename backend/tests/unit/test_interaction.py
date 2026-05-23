from __future__ import annotations

from app.tools.user_interaction_tools import (
    _entity_key,
    _order_entity,
    _product_entity,
    _ticket_entity,
)
from app.models.interaction import (
    OrderInteractionDetail,
    ProductInteractionDetail,
    TicketInteractionDetail,
)


class TestOrderEntity:
    def test_extracts_from_standard_payload(self):
        payload = {
            "order_id": "ORD-123",
            "order_status_label": "已发货",
            "source_channel": "wechat",
            "order_items": [
                {"order_item_id": "OI-1", "product_name": "运动鞋", "product_id": "P1"},
            ],
        }
        entity = _order_entity(payload)
        assert isinstance(entity, OrderInteractionDetail)
        assert entity.order_id == "ORD-123"
        assert entity.order_status_label == "已发货"
        assert entity.source_channel == "wechat"
        assert len(entity.items_preview) == 1

    def test_no_order_id_returns_none(self):
        assert _order_entity({}) is None
        assert _order_entity({"items": []}) is None

    def test_uses_id_fallback(self):
        entity = _order_entity({"id": "ORD-456"})
        assert entity is not None
        assert entity.order_id == "ORD-456"

    def test_items_capped_at_3(self):
        payload = {
            "order_id": "ORD-1",
            "items": [
                {"order_item_id": str(i), "product_name": f"商品{i}"} for i in range(5)
            ],
        }
        entity = _order_entity(payload)
        assert entity is not None
        assert len(entity.items_preview) == 3


class TestProductEntity:
    def test_extracts_product_info(self):
        payload = {
            "product_id": "P-1",
            "product_name": "运动鞋",
            "sku_id": "SKU-1",
            "order_id": "ORD-1",
            "order_item_id": "OI-1",
        }
        entity = _product_entity(payload)
        assert isinstance(entity, ProductInteractionDetail)
        assert entity.product_id == "P-1"
        assert entity.product_name == "运动鞋"
        assert entity.order_id == "ORD-1"

    def test_no_id_returns_none(self):
        assert _product_entity({}) is None

    def test_order_id_from_kwarg(self):
        payload = {"product_id": "P-1"}
        entity = _product_entity(payload, order_id="ORD-99")
        assert entity is not None
        assert entity.order_id == "ORD-99"


class TestTicketEntity:
    def test_extracts_ticket_info(self):
        payload = {
            "ticket_id": "TK-1",
            "ticket_title": "退货申请",
            "ticket_type": "refund",
            "ticket_status": "open",
            "ticket_status_label": "处理中",
        }
        entity = _ticket_entity(payload)
        assert isinstance(entity, TicketInteractionDetail)
        assert entity.ticket_id == "TK-1"
        assert entity.ticket_title == "退货申请"
        assert entity.ticket_type == "refund"

    def test_no_title_no_id_returns_none(self):
        assert _ticket_entity({}) is None

    def test_title_falls_back_to_ticket_id(self):
        payload = {"ticket_id": "TK-2"}
        entity = _ticket_entity(payload)
        assert entity is not None
        assert entity.ticket_title == "TK-2"


class TestEntityKey:
    def test_order_key_is_order_id(self):
        entity = OrderInteractionDetail(
            order_id="ORD-123",
            order_status_label="",
            source_channel="",
            items_preview=[],
        )
        assert _entity_key(entity) == "ORD-123"

    def test_product_key_is_order_item_id_first(self):
        entity = ProductInteractionDetail(
            product_id="P-1",
            order_item_id="OI-1",
            product_name="鞋",
        )
        assert _entity_key(entity) == "OI-1"

    def test_product_key_falls_back_to_product_id(self):
        entity = ProductInteractionDetail(
            product_id="P-2",
            order_item_id="",
            product_name="鞋",
        )
        assert _entity_key(entity) == "P-2"

    def test_ticket_key_is_ticket_id_first(self):
        entity = TicketInteractionDetail(
            ticket_id="TK-1",
            ticket_title="标题",
        )
        assert _entity_key(entity) == "TK-1"
