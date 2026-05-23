from __future__ import annotations

from app.utils.message_utils import message_text, last_user_message_text


class TestMessageText:
    def test_str_returns_as_is(self):
        assert message_text("hello") == "hello"

    def test_list_extracts_text_type_items(self):
        content = [
            {"type": "text", "text": "第一段"},
            {"type": "image", "url": "http://example.com/img.png"},
            {"type": "text", "text": "第二段"},
        ]
        result = message_text(content)
        assert (
            result
            == "第一段\n{'type': 'image', 'url': 'http://example.com/img.png'}\n第二段"
        )

    def test_list_item_text_field_none(self):
        result = message_text([{"type": "text", "text": None}])
        assert result == ""

    def test_dict_returns_string_representation(self):
        result = message_text({"key": "value"})
        assert result == "{'key': 'value'}"

    def test_none_returns_empty_string(self):
        assert message_text(None) == ""

    def test_int_returns_string(self):
        assert message_text(42) == "42"


class TestLastUserMessageText:
    def test_returns_last_human_content(self, make_human, make_ai):
        state = {
            "messages": [
                make_human("第一条"),
                make_ai("回复1"),
                make_human("第二条"),
            ]
        }
        assert last_user_message_text(state) == "第二条"

    def test_no_human_returns_empty(self, make_ai):
        state = {"messages": [make_ai("只有 AI")]}
        assert last_user_message_text(state) == ""

    def test_empty_messages_returns_empty(self):
        state = {"messages": []}
        assert last_user_message_text(state) == ""

    def test_message_content_is_empty_string(self, make_human):
        state = {"messages": [make_human("")]}
        assert last_user_message_text(state) == ""
