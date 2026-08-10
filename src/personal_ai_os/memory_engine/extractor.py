"""Rule-based memory candidate extraction (MVP, no LLM required).

Extracts preference / fact / profile candidates from conversation text using
Chinese-language patterns, e.g.::

    "我喜欢Python"          -> preference  "用户喜欢Python"
    "以后算法面试用Python"   -> preference  "用户以后算法面试用Python"
    "我的邮箱是x@y.com"     -> profile     "用户的邮箱是x@y.com"
    "不要自动删除文件"       -> preference  "用户不要自动删除文件"
    "记住这个项目叫AstrBot"  -> fact        "用户需要记住：这个项目叫AstrBot"

Each candidate is a dict conforming to the memory pipeline's ``candidates``
shape (dept 05 §4.1): ``{"content", "summary", "type", "confidence",
"importance", "scope", "sensitivity"}``.
"""

from __future__ import annotations

import re

# (regex, type, normalized_content_format, summary_prefix, confidence, importance)
_PATTERNS: list[tuple[re.Pattern, str, str, str, float, float]] = [
    (re.compile(r"我(?:更)?(?:喜欢|偏好)(.+?)(?=[。！？!?，,\n]|$)"),
     "preference", "用户喜欢{}", "喜好", 0.8, 0.6),
    (re.compile(r"我(?:使用|用)(.+?)(?=[。！？!?，,\n]|$)"),
     "fact", "用户使用{}", "使用", 0.7, 0.5),
    (re.compile(r"(?:以后|今后)(.+?)(?=[。！？!?，,\n]|$)"),
     "preference", "用户以后{}", "以后", 0.7, 0.6),
    (re.compile(r"下次(.+?)(?=[。！？!?，,\n]|$)"),
     "preference", "用户下次{}", "下次", 0.6, 0.4),
    (re.compile(r"记住[:：]?\s*(.+?)(?=[。！？!?，,\n]|$)"),
     "fact", "用户需要记住：{}", "记住", 0.75, 0.5),
    (re.compile(r"我的([^，。！？!?\n]{1,24})是([^，。！？!?\n]{1,80})"),
     "profile", "用户的{}是{}", "资料", 0.85, 0.7),
    (re.compile(r"不要(.+?)(?=[。！？!?，,\n]|$)"),
     "preference", "用户不要{}", "不要", 0.7, 0.5),
]


class RuleBasedExtractor:
    """Deterministic candidate extraction via pattern matching."""

    def extract(self, text: str) -> list[dict]:
        if not text or not text.strip():
            return []
        consumed: list[tuple[int, int]] = []
        candidates: list[dict] = []
        for pattern, mtype, content_fmt, prefix, confidence, importance in _PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span()
                if any(start < c_end and end > c_start for c_start, c_end in consumed):
                    continue
                consumed.append((start, end))
                groups = match.groups()
                if mtype == "profile":
                    x, y = groups[0].strip(), groups[1].strip()
                    content = content_fmt.format(x, y)
                    summary = f"{prefix}: {x} = {y}"
                else:
                    x = groups[0].strip()
                    content = content_fmt.format(x)
                    summary = f"{prefix}: {x}"
                candidates.append(
                    {
                        "content": content,
                        "summary": summary,
                        "type": mtype,
                        "confidence": confidence,
                        "importance": importance,
                        "scope": "global",
                        "sensitivity": "personal",
                    }
                )
        return candidates
