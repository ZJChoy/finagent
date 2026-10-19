"""D6 探针：对候选问题做检索，输出 doc/page/片段预览 —— 供人工核定金标页码。

    docker compose exec app python -c "import sys; sys.path.insert(0,'.');
    from scripts.citation_probe import probe_all; probe_all()"
"""
from __future__ import annotations

from backend.rag import retriever

CANDIDATES = [
    ("贵州茅台的主营业务是什么？", ["茅台酒", "生产经营"]),
    ("贵州茅台的销售渠道分为哪几类？", ["直销", "批发", "渠道"]),
    ("贵州茅台的分红政策是怎样的？", ["分红", "利润分配"]),
    ("贵州茅台在年报里提到的行业竞争格局如何？", ["竞争"]),
    ("美的集团的主要业务板块有哪些？", ["楼宇", "机器人", "新能源", "工业技术"]),
    ("美的集团 2025 年营业收入的构成是怎样的？", ["营业收入", "主营业务"]),
    ("贵州茅台 2025 年营业收入是多少？", ["营业收入"]),
    ("美的集团的主要风险有哪些？", ["风险"]),
    ("贵州茅台 2025 年直销渠道收入占总营收的比例是多少？", ["直销"]),
    ("贵州茅台的生产工艺有什么特点？", ["工艺", "生产"]),
]

REFUSAL = [
    "贵州茅台 2030 年的营业收入是多少？",
    "美的集团下个月的股价会怎么走？",
    "贵州茅台在 2019 年有没有披露 ESG 报告？",
]


def probe_one(q: str, kws: list[str]) -> None:
    hits = retriever.retrieve(q, top_k=5)
    print(f"\n### {q}")
    for h in hits:
        page_text_flag = any(kw in h["text"] for kw in kws)
        mark = "🟢" if page_text_flag else "  "
        print(f"  {mark} {h['doc']} p{h['page']} score={h['score']:.3f} | {h['text'][:60]}")


def probe_all() -> None:
    for q, kws in CANDIDATES:
        probe_one(q, kws)
    print("\n### 拒答组（预期：无依据/低分）")
    for q in REFUSAL:
        hits = retriever.retrieve(q, top_k=3)
        top = hits[0] if hits else None
        print(f"  {q} → top score={top['score']:.3f} {top['doc']} p{top['page']}" if top else f"  {q} → 无命中")
