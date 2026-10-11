"""检索服务：query → 向量召回 → 组装上下文与引用。

参数取自技术方案 §3「关键参数」：
- 先召回 `candidates=30` 条，给**将来的重排**留候选空间；
- 最终取 `top_k=5` 条注入上下文。
本步骤**不做重排** —— 按定稿：用第 9 步的评测数据再决定它值不值。
"""
from __future__ import annotations

from . import embedder, store

CANDIDATES = 30
TOP_K = 5


def retrieve(
    query: str,
    top_k: int = TOP_K,
    candidates: int = CANDIDATES,
    doc: str | None = None,
) -> list[dict]:
    """检索最相关的若干切块。知识库为空时返回空列表（不报错）。"""
    if not query.strip() or store.count() == 0:
        return []
    qv = embedder.embed_query(query)
    hits = store.search(qv, top_k=max(candidates, top_k), doc=doc)
    return hits[:top_k]


def citations_of(hits: list[dict]) -> list[dict]:
    """把命中结果转成结构化的「引用」，供前端展示与核对。"""
    return [
        {
            "no": i,
            "doc": h["doc"],
            "page": h["page"],           # PDF 页序（稳定定位锚点）
            "seq": h["seq"],
            "score": h["score"],
            "preview": h["text"][:100].replace("\n", " "),
        }
        for i, h in enumerate(hits, start=1)
    ]


def build_context(hits: list[dict]) -> str:
    """把命中块拼成给模型看的上下文，编号与 citations 一一对应。"""
    if not hits:
        return ""
    blocks = [
        f"【片段 {i}】来源：{h['doc']} 第 {h['page']} 页（相似度 {h['score']}）\n{h['text']}"
        for i, h in enumerate(hits, start=1)
    ]
    return "\n\n".join(blocks)
