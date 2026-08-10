"""Tests for the rule-based memory extractor."""

from __future__ import annotations

from personal_ai_os.memory_engine import RuleBasedExtractor


def test_extracts_preferences_and_profiles():
    extractor = RuleBasedExtractor()
    text = "我喜欢Python。以后算法面试用Python。我的邮箱是abc@example.com。"
    candidates = extractor.extract(text)

    by_type = {}
    for c in candidates:
        by_type.setdefault(c["type"], []).append(c["content"])

    assert "用户喜欢Python" in by_type.get("preference", [])
    assert "用户以后算法面试用Python" in by_type.get("preference", [])
    assert "用户的邮箱是abc@example.com" in by_type.get("profile", [])


def test_extracts_facts_and_negative_preferences():
    extractor = RuleBasedExtractor()
    text = "记住这个项目叫AstrBot。不要自动删除文件。"
    candidates = extractor.extract(text)

    contents = {c["content"] for c in candidates}
    types = {c["type"]: c["content"] for c in candidates}

    assert types.get("fact") == "用户需要记住：这个项目叫AstrBot"
    assert "用户不要自动删除文件" in contents


def test_candidate_shape_contract():
    extractor = RuleBasedExtractor()
    candidates = extractor.extract("我的语言是Python")
    assert len(candidates) == 1
    c = candidates[0]
    for key in ("content", "summary", "type", "confidence", "importance", "scope", "sensitivity"):
        assert key in c
    assert c["type"] == "profile"
    assert c["scope"] == "global"
    assert c["sensitivity"] == "personal"
    assert 0 <= c["confidence"] <= 1
    assert 0 <= c["importance"] <= 1


def test_overlapping_patterns_do_not_double_extract():
    extractor = RuleBasedExtractor()
    # "记住" consumes the sentence, so "我的...是..." must not also fire.
    candidates = extractor.extract("记住我的生日是1月1日")
    assert len(candidates) == 1
    assert candidates[0]["type"] == "fact"


def test_empty_or_boring_text():
    extractor = RuleBasedExtractor()
    assert extractor.extract("") == []
    assert extractor.extract("今天天气不错") == []
