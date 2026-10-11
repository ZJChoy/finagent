"""上下文预算：控制塞进模型的内容量（技术方案 §7）。

职责边界：**只管「token 估算」与「工具结果截断」**。
"带几条历史原文"不在这里 —— 那是记忆策略，归 `agent/memory.py` 的 `keep_recent` 管
（早前这里也有一份，和配置页参数重复、还会互相覆盖，已合并掉）。
"""
from __future__ import annotations

from ..config import settings


def estimate_tokens(text: str) -> int:
    """中文粗略估算：字符数 ÷ 1.5（定稿 §7 采用的口径）。真实用量以 API usage 为准。"""
    return int(len(text or "") / 1.5) + 1


def total_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content") or "") for m in messages)


def trim_tool_result(content: str) -> tuple[str, bool]:
    """限制单条工具结果的长度，返回 (内容, 是否被截断)。"""
    limit = settings.TOOL_RESULT_MAX_CHARS
    if len(content) <= limit:
        return content, False
    return content[:limit] + f"\n…（工具结果超过 {limit} 字，已截断）", True
