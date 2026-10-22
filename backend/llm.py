"""云端模型客户端——已迁移至 finagent.core.llm（框架层）。本模块保留为应用层 shim。

行为与迁移前完全一致：configure 绑定 backend 配置，函数签名不变。
"""
from __future__ import annotations

from collections.abc import Generator

from finagent.core import llm as _core

from .config import settings

# 用 backend 配置绑定框架层客户端（幂等）
_core.configure(
    api_key=settings.API_KEY,
    base_url=settings.BASE_URL,
    chat_model=settings.CHAT_MODEL,
    ready=settings.llm_ready,
)

LLMNotConfigured = _core.LLMNotConfigured


def client():
    return _core.client()


def stream_chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    tools: list[dict] | None = None,
    max_tokens: int | None = None,
    tool_choice: str = "auto",
) -> Generator[dict, None, None]:
    return _core.stream_chat(
        messages, model=model, temperature=temperature, tools=tools,
        max_tokens=max_tokens, tool_choice=tool_choice,
    )


def complete(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> str:
    return _core.complete(messages, model=model, temperature=temperature, max_tokens=max_tokens)
