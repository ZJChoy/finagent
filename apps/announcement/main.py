"""公告问答 Agent（第二实例 · 泛化性证明）。

只依赖 finagent.core（runtime/tools/llm/context/retriever）+ backend 基础设施（配置/DB）。
与财报实例的对比就是 G3 的证据：换提示词、换工具、换语料，框架零改动。

用法：
    DATA_DIR=data/data-announce docker compose run --rm app \
        python -m apps.announcement.main "贵州茅台最近有哪些公告？"
"""
from __future__ import annotations

import sys

from finagent.core.runtime import AgentEngine

from backend import llm, tools
from backend.agent import context as ctx
from backend.config import settings
from backend.rag import retriever

from . import tools_impl  # noqa: F401  导入即注册公告工具
from .prompts import build_messages


def _preflight(query: str) -> dict | None:
    """公告预取：问答前先检索，命中才作答（无依据则让模型如实说明）。"""
    hits = retriever.retrieve(query, top_k=5)
    if not hits:
        return None
    return {
        "force": True,
        "block": retriever.build_context(hits),
        "pre_hits": hits,
        "citations": retriever.citations_of(hits),
        "snapshot": None,
        "symbol": None,
        "events": [{"type": "gate", "message": f"已预取公告片段 {len(hits)} 个"}],
    }


engine = AgentEngine(
    llm_mod=llm,
    settings=settings,
    registry=tools,
    ctx_mod=ctx,
    prompt_builder=build_messages,
    preflight=_preflight,
    rewriter=None,  # 公告问答单轮为主，改写钩子留空——框架的可选性即证明
)


def ask(question: str, history: list[dict] | None = None) -> tuple[str, dict]:
    """返回 (最终回答, end 事件指标)——供评测框架复用。"""
    final, end_ev = "", {}
    for ev in engine.run(question, history=history):
        if ev["type"] == "end":
            final, end_ev = ev.get("content") or "", ev
    return final, end_ev


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "贵州茅台最近有哪些公告？"
    answer, _ = ask(q)
    print(answer)
