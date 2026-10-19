"""查询改写：多轮追问先改写成「自包含问题」，再交给 Agent。

动因：用户说"那 2025 年呢""它毛利多少"时，Agent 拿到的是一句没有主语的问句，
检索与工具调用的质量都会掉。所以先做一次轻量改写（含指代消解）。

成本控制：**只在有历史时调用**，且 `max_tokens` 很小（默认 120）；失败就退回原文。
"""
from __future__ import annotations

from .. import llm
from ..config import settings

_SYSTEM = (
    "你是查询改写器。把用户的最新问题改写成一句**自包含、可直接检索**的问题："
    "补全主语与指代（如“它”“那 2025 年呢”“那家公司的”），保留原意，不要扩写。"
    "只输出改写后的问题本身，不要解释，不要回答。"
)


def rewrite(message: str, history: list[dict] | None, model: str | None = None) -> str:
    if not history:
        return message
    msgs: list[dict] = [{"role": "system", "content": _SYSTEM}]
    msgs.extend(history[-4:])
    msgs.append({"role": "user", "content": f"请改写这句话：{message}"})
    try:
        out = llm.complete(
            msgs,
            model=model or settings.REWRITE_MODEL,
            max_tokens=settings.REWRITE_MAX_TOKENS,
        ).strip().strip("“”\"'")
        return out or message
    except Exception:  # noqa: BLE001  改写失败不影响主流程
        return message
