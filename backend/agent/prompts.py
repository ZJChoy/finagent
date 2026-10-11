"""Prompt 组装：系统提示词 + 历史 + 本轮问题（+ 系统预检索的片段）。

两点纪律：
1. **引用与历史解耦**：引用编号只对当时那一轮有效。历史里的 `（片段 N）` 若原样带进下一轮，
   会诱导模型"照抄编号"——即使本轮根本没检索（实测踩到过：模型凭空写了「片段 3」）。
2. **依据由代码保底**：门控命中时，系统已先检索好片段（`grounding`），随本轮消息一起给模型，
   不再依赖模型自己决定要不要调检索工具。
"""
from __future__ import annotations

import re

from ..config import DEFAULT_SYSTEM_PROMPT

_CITE_RE = re.compile(r"[（(]\s*片段\s*\d+(?:\s*[、,，和]\s*片段\s*\d+)*\s*[)）]")
_WS_RE = re.compile(r"[ \t]{2,}")


def sanitize_history_text(text: str) -> str:
    """剥掉历史回答里的 `（片段 N）` 标记。"""
    cleaned = _CITE_RE.sub("", text or "")
    cleaned = _WS_RE.sub(" ", cleaned)
    return cleaned.strip()


def build_messages(
    message: str,
    history: list[dict] | None = None,
    system_prompt: str | None = None,
    grounding: str | None = None,
) -> list[dict]:
    """`grounding` 非 None 时，作为「系统预检索片段」附在本轮用户消息后面。"""
    messages: list[dict] = [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}
    ]
    for m in (history or []):
        role = m.get("role")
        content = m.get("content") or ""
        if role not in ("user", "assistant") or not content:
            continue
        if role == "assistant":
            content = sanitize_history_text(content)
            if not content:
                continue
        messages.append({"role": role, "content": content})

    user_content = message
    if grounding is not None:
        user_content = f"{message}\n\n----\n{grounding}"
    messages.append({"role": "user", "content": user_content})
    return messages
