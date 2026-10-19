"""上下文预算——已迁移至 finagent.core.context（框架层）。本模块保留为应用层 shim。

职责边界不变：只管「token 估算」与「工具结果截断」。
截断上限 TOOL_RESULT_MAX_CHARS 来自 backend 配置，在此注入框架函数。
"""
from __future__ import annotations

from finagent.core.context import (  # noqa: F401
    estimate_tokens,
    total_tokens,
    trim_tool_result as _core_trim,
)

from ..config import settings


def trim_tool_result(content: str, limit: int | None = None) -> tuple[str, bool]:
    """兼容双签名：框架引擎显式传 limit 时透传；否则注入 backend 配置的上限。"""
    return _core_trim(content, limit or settings.TOOL_RESULT_MAX_CHARS)
