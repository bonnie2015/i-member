from __future__ import annotations

from app.workflow.state import extract_last_service_round, get_service_clear_state


class TestGetServiceClearState:
    def test_all_runtime_fields_reset(self):
        """get_service_clear_state 返回 18 个字段，全部重置为 None/空/0。"""
        cleared = get_service_clear_state()
        assert len(cleared) == 19

        # 重置为 None 的字段
        none_fields = [
            "intent",
            "reason",
            "current_subgraph",
            "final_reply",
            "final_status",
            "final_reason",
            "started_at",
            "service_state",
            "service_key",
            "goal",
            "replan_reason",
            "slots",
            "guard_decision",
        ]
        for f in none_fields:
            assert cleared[f] is None, f"field={f} expected None, got {cleared[f]}"

        # 重置为空列表
        assert cleared["trace"] == []
        assert cleared["steps"] == []
        assert cleared["expected_slots"] == []
        assert cleared["messages"] == []

        # 重置为 0
        assert cleared["current_step_index"] == 0
        assert cleared["replan_count"] == 0


class TestExtractLastServiceRound:
    def test_empty_messages(self):
        assert extract_last_service_round([]) == []

    def test_no_human_message(self, make_ai):
        messages = [make_ai("好的")]
        assert extract_last_service_round(messages) == []

    def test_last_is_ai_takes_last_complete_round(self, make_human, make_ai):
        """最后一条是 AI → 取最后一个 Human + 其后无 tool_calls 的 AI。"""
        messages = [
            make_human("问题1"),
            make_ai("回答1"),
            make_human("问题2"),
            make_ai("回答2（这轮需要保留）"),
        ]
        result = extract_last_service_round(messages)
        assert len(result) == 2
        assert result[0].content == "问题2"
        assert result[1].content == "回答2（这轮需要保留）"

    def test_last_is_human_takes_previous_round(self, make_human, make_ai):
        """最后一条是 Human（未被回复）→ 取倒数第二轮完整对话。"""
        messages = [
            make_human("问题1"),
            make_ai("回答1"),
            make_human("问题2（新服务的第一条消息）"),
        ]
        result = extract_last_service_round(messages)
        assert len(result) == 2
        assert result[0].content == "问题1"
        assert result[1].content == "回答1"

    def test_only_one_human_last_returns_empty(self, make_human):
        messages = [make_human("唯一一条")]
        result = extract_last_service_round(messages)
        assert result == []

    def test_excludes_ai_with_tool_calls(self, make_human, make_ai):
        messages = [
            make_human("我要退货"),
            make_ai(
                "", tool_calls=[{"name": "search_order", "args": {}, "id": "call_1"}]
            ),
            make_ai("已为您查到订单"),
        ]
        result = extract_last_service_round(messages)
        assert len(result) == 2
        assert result[0].content == "我要退货"
        assert result[1].content == "已为您查到订单"

    def test_excludes_ai_with_empty_content(self, make_human, make_ai):
        messages = [
            make_human("你好"),
            make_ai(""),
            make_ai("实际回复"),
        ]
        result = extract_last_service_round(messages)
        assert len(result) == 2
        assert result[0].content == "你好"
        assert result[1].content == "实际回复"
