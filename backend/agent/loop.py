"""Agent 主循环——已迁移至 finagent.core.runtime（框架层）。本模块保留为应用层装配。

循环本体零改动地搬进了框架层；本文件只负责注入**财报领域钩子**：
- preflight：接地门控 + 双通道强制预取（年报片段 + 结构化数据）
- prompt_builder：财报分析人设的消息构造
- rewriter：查询改写

行为与迁移前完全一致（基线锚点：eval/baseline-v2-pre.md，20/20）。
"""
from __future__ import annotations

from collections.abc import Generator

from finagent.core.runtime import AgentEngine

from .. import llm, tools
from ..config import settings
from ..rag import retriever
from . import context as ctx
from . import gate
from . import grounding as gnd
from . import rewrite as rewriter
from .prompts import build_messages


def _preflight(query: str) -> dict | None:
    """财报领域预取（原 loop 内联逻辑原样搬入）。

    门控（L0）：涉及公司事实/数字 → 由代码保底预取两条通道并注入：
      通道 A = 年报原文片段（本地向量检索）
      通道 B = 结构化财务数据（akshare，命中数值信号时）
    """
    force_grounding = gate.needs_grounding(query)
    if not force_grounding:
        return None
    want_snapshot = gate.needs_snapshot(query)

    try:
        pre_hits = retriever.retrieve(query)
    except Exception:  # noqa: BLE001  预检索失败不阻断对话
        pre_hits = []
    prefetch = gnd.build_prefetch(query, pre_hits, want_snapshot)
    grounding_block: str | None = prefetch["block"]
    snapshot: str | None = prefetch["snapshot"]
    citations: list[dict] = retriever.citations_of(pre_hits) if pre_hits else []

    channels: list[str] = []
    if pre_hits:
        channels.append(f"通道 A 年报片段 {len(pre_hits)} 个")
    if snapshot:
        channels.append(f"通道 B 结构化数据（{prefetch['symbol']}）")
    events: list[dict] = [
        {
            "type": "gate",
            "message": "本题涉及公司事实，已由系统强制预取："
            + ("、".join(channels) if channels else "但未命中任何资料"),
        }
    ]
    if citations:
        events.append({"type": "citations", "items": citations})
    if not grounding_block:
        grounding_block = (
            "（系统预取未命中任何资料——可能是问法与原文措辞差异所致。请尝试："
            "① 数值/指标类问题**调用 get_stock_financials**（6 位代码查结构化数据，"
            "问句含代码时直接用该代码）；"
            "② 文字类问题调用 **search_knowledge** 换关键词检索（拆短查询、换同义词）；"
            "③ 涉及数字计算调用 **calculate**。"
            "只有当这些工具也确实取不到依据时，才如实说明未找到依据。不要直接放弃作答。）"
        )

    return {
        "force": True,
        "block": grounding_block,
        "pre_hits": pre_hits,
        "citations": citations,
        "snapshot": bool(snapshot),
        "symbol": prefetch["symbol"],
        "events": events,
    }


_engine = AgentEngine(
    llm_mod=llm,
    settings=settings,
    registry=tools,
    ctx_mod=ctx,
    prompt_builder=build_messages,
    preflight=_preflight,
    rewriter=rewriter.rewrite,
)


def run(
    message: str,
    history: list[dict] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
) -> Generator[dict, None, None]:
    """转发至框架引擎（签名与迁移前完全一致）。"""
    return _engine.run(
        message,
        history=history,
        system_prompt=system_prompt,
        model=model,
        enabled_tools=enabled_tools,
    )
