"""检索服务：query → 向量召回 →（可选）重排 → 组装上下文与引用。

参数取自技术方案 §3「关键参数」：
- 先召回 `candidates=30` 条，给**重排**留候选空间；
- 最终取 `top_k=5` 条注入上下文。

**重排默认关闭**（`RERANK_ENABLED=1` 才开）—— 按定稿原则：先有评测数据，再决定它值不值。
评测对比见 `eval/retrieval_report.md`。

**相关性阈值**（M3.1，`RETRIEVAL_MIN_SCORE`，默认 0=关闭）：
低于 MIN_SCORE 的块直接丢弃——检索"永远返回 top-k"曾让无关内容被当成依据注入。
阈值由金标分布标定（scripts/threshold_calibrate.py），不是拍的。
"""
from __future__ import annotations

import os

from finagent.core.appconfig import settings

from . import embedder, rerank, store

CANDIDATES = 30
TOP_K = 5
# 相关性下限（M3.1）：低于此分的块不算"依据"。标定：eval/retrieval_questions.yaml
# 20 题金标块分数 p10=0.6736（保留 90% 金标、拦截 78% 召回线以下噪声），
# 详见 scripts/threshold_calibrate.py 与 eval/threshold-report.md。
# 取 0 下方可整体关闭（RETRIEVAL_MIN_SCORE=0 时不启用阈值）。
MIN_SCORE = float(os.getenv("RETRIEVAL_MIN_SCORE", "0.0") or 0.0)


def retrieve(
    query: str,
    top_k: int = TOP_K,
    candidates: int = CANDIDATES,
    doc: str | None = None,
) -> list[dict]:
    """检索最相关的若干切块。知识库为空时返回空列表（不报错）。"""
    if not query.strip() or store.count() == 0:
        return []
    # 与 add_document 同源的护栏：库内模型与当前配置不一致时拒绝检索（fail-closed）
    store._assert_embedding_model_compatible(settings.EMBED_MODEL)
    qv = embedder.embed_query(query)
    hits = store.search(qv, top_k=max(candidates, top_k), doc=doc)

    if settings.RERANK_ENABLED and len(hits) > top_k:
        order, ok = rerank.rerank(query, [h["text"] for h in hits], top_n=top_k)
        if ok:
            hits = [hits[i] for i in order][:top_k]
        # 重排失败就退回向量顺序 —— 质量可以退化，链路不能断

    hits = hits[:top_k]
    return _apply_min_score(hits)


def _apply_min_score(hits: list[dict]) -> list[dict]:
    """MIN_SCORE>0 时丢弃低于阈值的块（供测试直接覆盖模块变量验证行为）。"""
    if MIN_SCORE <= 0:
        return hits
    return [h for h in hits if h.get("score", 0) >= MIN_SCORE]


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
