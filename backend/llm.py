"""云端模型客户端（OpenAI 兼容）。

三件事：
1. `stream_chat` —— 流式对话，统一产出 content / end 事件；
2. 工具调用 —— 流式下 `tool_calls` 是**增量拼接**的，按 index 自行累积；
3. `complete` —— 非流式短调用（给查询改写这种轻量任务用）。
"""
from __future__ import annotations

from typing import Generator

from openai import OpenAI

from .config import settings


class LLMNotConfigured(RuntimeError):
    """未配置 API key 时抛出，便于前端给出友好提示。"""


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if not settings.llm_ready:
        raise LLMNotConfigured("未配置 DASHSCOPE_API_KEY，请在 .env 中填写")
    if _client is None:
        _client = OpenAI(api_key=settings.API_KEY, base_url=settings.BASE_URL, timeout=60)
    return _client


def _usage_dict(usage) -> dict | None:
    """把 SDK 的 usage 对象转成可 JSON 序列化的普通 dict。"""
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _create_stream(
    messages: list[dict],
    model: str,
    temperature: float,
    tools: list[dict] | None,
    max_tokens: int | None,
    tool_choice: str = "auto",
):
    """发起流式请求，逐级降级：
    1) 带 stream_options + tool_choice；
    2) 去掉 stream_options（部分厂商不支持）；
    3) tool_choice 退回 auto（部分厂商不支持 required）。
    """
    base: dict = dict(model=model, messages=messages, stream=True, temperature=temperature)
    if tools:
        base["tools"] = tools
    if max_tokens:
        base["max_tokens"] = max_tokens

    client_ = client()
    attempts = []
    if tools:
        attempts.append({**base, "tool_choice": tool_choice, "stream_options": {"include_usage": True}})
        attempts.append({**base, "tool_choice": tool_choice})
        if tool_choice != "auto":
            attempts.append({**base, "tool_choice": "auto"})
    else:
        attempts.append({**base, "stream_options": {"include_usage": True}})
        attempts.append(base)

    last_err: Exception | None = None
    for kwargs in attempts:
        try:
            return client_.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if kwargs.get("tool_choice") == "required":
                continue
    raise last_err  # type: ignore[misc]


def stream_chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    tools: list[dict] | None = None,
    max_tokens: int | None = None,
    tool_choice: str = "auto",
) -> Generator[dict, None, None]:
    """流式对话，产出统一事件：

    - {"type": "content", "delta": str}                     增量文本
    - {"type": "end", "content": str,
       "tool_calls": [{"id","name","arguments"}], "usage": {...}}   本轮结束
    """
    model = model or settings.CHAT_MODEL
    stream = _create_stream(messages, model, temperature, tools, max_tokens, tool_choice)

    parts: list[str] = []
    tool_slots: dict[int, dict] = {}
    usage = None

    for chunk in stream:
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue

        if getattr(delta, "content", None):
            parts.append(delta.content)
            yield {"type": "content", "delta": delta.content}

        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                slot = tool_slots.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function is not None:
                    if tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

    yield {
        "type": "end",
        "content": "".join(parts),
        "tool_calls": [tool_slots[i] for i in sorted(tool_slots)],
        "usage": _usage_dict(usage),
    }


def complete(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> str:
    """非流式短调用，返回文本。用于查询改写这类轻量任务。"""
    resp = client().chat.completions.create(
        model=model or settings.CHAT_MODEL,
        messages=messages,
        temperature=temperature,
        **({"max_tokens": max_tokens} if max_tokens else {}),
    )
    return (resp.choices[0].message.content or "").strip()
