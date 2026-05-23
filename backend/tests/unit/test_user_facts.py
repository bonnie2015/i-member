from __future__ import annotations

from app.memory.user_facts import (
    StoredUserFact,
    _apply_fact_changes,
    _coerce_fact_item,
    _dedupe_texts,
    _normalize_fact_text,
)


class TestNormalizeFactText:
    def test_strips_whitespace_and_dash(self):
        assert _normalize_fact_text("  用户喜欢红色  \t") == "用户喜欢红色"
        assert _normalize_fact_text("--用户喜欢红色") == "用户喜欢红色"

    def test_replaces_newlines_with_space(self):
        result = _normalize_fact_text("第一行\n第二行\r\n第三行")
        assert result == "第一行 第二行 第三行"

    def test_empty_and_none(self):
        assert _normalize_fact_text("") == ""
        assert _normalize_fact_text(None) == ""
        assert _normalize_fact_text("   ") == ""


class TestDedupeTexts:
    def test_dedupes_case_insensitive(self):
        result = _dedupe_texts(["用户喜欢红色", "用户喜欢红色", "用户喜欢红色"])
        assert result == ["用户喜欢红色"]

    def test_dedupes_casefold(self):
        result = _dedupe_texts(["用户喜欢红色", "用户喜欢红色", "用户喜欢紅色"])
        assert len(result) == 2

    def test_limit_truncates(self):
        result = _dedupe_texts(["a", "b", "c", "d"], limit=2)
        assert result == ["a", "b"]

    def test_exclude_set(self):
        result = _dedupe_texts(["a", "b", "c"], exclude={"a"})
        assert result == ["b", "c"]

    def test_skips_empty_after_normalize(self):
        result = _dedupe_texts(["a", "", None, "b"])
        assert result == ["a", "b"]


class TestCoerceFactItem:
    def test_from_string(self):
        result = _coerce_fact_item("用户喜欢红色")
        assert isinstance(result, StoredUserFact)
        assert result.fact == "用户喜欢红色"
        assert result.updated_at == ""

    def test_from_dict(self):
        result = _coerce_fact_item({"fact": "喜欢蓝色", "updated_at": "2026-01-01"})
        assert isinstance(result, StoredUserFact)
        assert result.fact == "喜欢蓝色"
        assert result.updated_at == "2026-01-01"

    def test_from_stored_fact(self):
        existing = StoredUserFact(fact="已有事实", updated_at="2025-01-01")
        result = _coerce_fact_item(existing)
        assert result.fact == "已有事实"
        assert result.updated_at == "2025-01-01"

    def test_empty_returns_none(self):
        assert _coerce_fact_item("") is None
        assert _coerce_fact_item(None) is None


class TestApplyFactChanges:
    def test_add_new_facts(self):
        result = _apply_fact_changes(
            existing=[],
            add_facts=["喜欢红色", "尺码偏大"],
            delete_facts=[],
            timestamp="2026-01-01T00:00:00",
        )
        assert len(result) == 2
        assert result[0].fact == "喜欢红色"
        assert result[1].fact == "尺码偏大"

    def test_delete_facts(self):
        existing = [
            StoredUserFact(fact="旧事实", updated_at="2025-01-01"),
        ]
        result = _apply_fact_changes(
            existing=existing,
            add_facts=[],
            delete_facts=["旧事实"],
            timestamp="2026-01-01T00:00:00",
        )
        assert len(result) == 0

    def test_delete_is_case_insensitive(self):
        existing = [StoredUserFact(fact="喜欢红色")]
        result = _apply_fact_changes(
            existing=existing,
            add_facts=[],
            delete_facts=["喜欢红色"],
            timestamp="",
        )
        assert len(result) == 0

    def test_preserves_existing_order(self):
        existing = [
            StoredUserFact(fact="a"),
            StoredUserFact(fact="b"),
        ]
        result = _apply_fact_changes(
            existing=existing,
            add_facts=[],
            delete_facts=[],
            timestamp="",
        )
        assert result[0].fact == "a"
        assert result[1].fact == "b"

    def test_limits_to_core_facts_limit(self):
        existing = [StoredUserFact(fact=str(i)) for i in range(8)]
        result = _apply_fact_changes(
            existing=existing,
            add_facts=["new_fact_1", "new_fact_2"],
            delete_facts=[],
            timestamp="",
        )
        assert len(result) <= 8
