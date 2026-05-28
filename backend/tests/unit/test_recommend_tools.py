from __future__ import annotations


from app.agents.recommend_agent import _product_key, RecommendAgent


class TestProductKey:
    def test_extracts_product_id_and_color_id(self):
        result = _product_key({"product_id": 123, "color_id": 456})
        assert result == (123, 456)

    def test_uses_default_color_id_when_missing(self):
        result = _product_key({"product_id": 123, "default_color_id": 789})
        assert result == (123, 789)

    def test_none_when_product_id_is_zero(self):
        assert _product_key({"product_id": 0}) is None

    def test_none_when_no_product_id(self):
        assert _product_key({}) is None

    def test_converts_string_ids(self):
        result = _product_key({"product_id": "123", "color_id": "456"})
        assert result == (123, 456)

    def test_color_id_none_when_zero(self):
        result = _product_key({"product_id": 123, "color_id": 0})
        assert result == (123, None)


class TestToolControlMessage:
    def _agent(self):
        return RecommendAgent()

    def test_tool_count_0_returns_none(self):
        agent = self._agent()
        msg = agent._tool_control_message(tool_count=0)
        assert msg is None

    def test_tool_count_1_suggests_stop(self):
        agent = self._agent()
        msg = agent._tool_control_message(tool_count=1)
        assert msg is not None
        assert "优先调用 reply_with_products" in str(msg.content)

    def test_tool_count_2_force_last(self):
        agent = self._agent()
        msg = agent._tool_control_message(tool_count=2)
        assert msg is not None
        assert "只剩最后一次" in str(msg.content)

    def test_tool_count_3_force_stop(self):
        agent = self._agent()
        msg = agent._tool_control_message(tool_count=3)
        assert msg is not None
        assert "已用完" in str(msg.content)
