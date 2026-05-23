from __future__ import annotations

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.workflow.nodes.ticket.executor import (
    _maybe_compress_try_process,
    _try_process_to_messages,
)


class TestTryProcessToMessages:
    def test_empty_tp_returns_only_system_prompt(self):
        messages = _try_process_to_messages("你是客服助手", [])
        assert len(messages) == 1
        assert isinstance(messages[0], SystemMessage)
        assert messages[0].content == "你是客服助手"

    def test_tool_call_and_result_pair(self):
        tp = [
            {"tool": "search_order", "args": {"order_id": "ORD-123"}},
            {"tool": "search_order", "result": '{"found": true}'},
        ]
        messages = _try_process_to_messages("prompt", tp)
        # SystemMessage + AIMessage(tool_call) + ToolMessage
        assert len(messages) == 3
        assert isinstance(messages[0], SystemMessage)
        assert isinstance(messages[1], AIMessage)
        assert len(messages[1].tool_calls) == 1
        assert messages[1].tool_calls[0]["name"] == "search_order"
        assert isinstance(messages[2], ToolMessage)
        assert messages[2].content == '{"found": true}'

    def test_compressed_entry_as_system_message(self):
        tp = [
            {"compressed": "前序操作摘要"},
            {"tool": "finish_step", "args": {"step_status": "done"}},
        ]
        messages = _try_process_to_messages("prompt", tp)
        assert len(messages) == 3
        assert messages[0].content == "prompt"
        assert messages[1].content == "前序操作摘要"
        assert isinstance(messages[2], AIMessage)

    def test_non_string_result_uses_message_text(self):
        tp = [
            {"tool": "get_product", "args": {"id": "1"}},
            {"tool": "get_product", "result": {"name": "鞋子", "price": 999}},
        ]
        messages = _try_process_to_messages("prompt", tp)
        tool_msg = messages[2]
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.content == "{'name': '鞋子', 'price': 999}"


class TestMaybeCompressTryProcess:
    def test_under_threshold_no_compression(self):
        tp = [{"tool": "t", "args": {"x": 1}}, {"tool": "t", "result": "ok"}]
        result = _maybe_compress_try_process(tp, "测试步骤")
        assert result == tp

    def test_multiple_pairs_with_compression(self):
        """超过阈值时旧条目被压缩为一条摘要，保留最近一对 request+result。"""
        # 构造足够大的 try_process 以触发 3000 token 阈值
        tp = []
        for i in range(50):
            tp.append({"tool": f"tool_{i}", "args": {"i": i, "data": "x" * 200}})
            tp.append({"tool": f"tool_{i}", "result": "x" * 300})
        result = _maybe_compress_try_process(tp, "测试步骤")
        assert len(result) < len(tp)
        assert "compressed" in result[0]
        assert "args" in result[-2]
        assert "result" in result[-1]
