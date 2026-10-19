"""上下文预算：控制塞进模型的内容量。

职责边界：只管「token 估算」与「工具结果截断」（框架层，截断上限由应用注入）。
"带几条历史原文"不在这里 —— 那是记忆策略，归应用层 memory 管。
"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """中文粗略估算：字符数 ÷ 1.5。真实用量以 API usage 为准。"""
    return int(len(text or "") / 1.5) + 1


def total_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content") or "") for m in messages)


def trim_tool_result(content: str, limit: int) -> tuple[str, bool]:
    """限制单条工具结果的长度，返回 (内容, 是否被截断)。limit 由应用层注入。"""
    if len(content) <= limit:
        return content, False
    return content[:limit] + f"\n…（工具结果超过 {limit} 字，已截断）", True
