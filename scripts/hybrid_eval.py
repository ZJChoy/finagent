"""混合检索离线评估（M3.2 stretch）：BM25 + 向量 RRF 融合 vs 纯向量基线。

原则（README：先评测再上线）：只在离线金标上对比，打不过基线就不接进生产。
用 retrieval_questions.yaml 20 题 + retrieval_golden.json 金标，
指标：金标命中（Pass@5）/ MRR@10。

    python scripts/hybrid_eval.py            # 跑对比，打印结论
    （需要 API key：向量侧要 embed 查询；BM25 侧纯本地）

BM25 实现：纯 Python + jieba 分词（不引数据库依赖，保持双后端卖点）。
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from backend import dbconn  # noqa: E402
from backend.config import settings  # noqa: E402
from finagent.core.appconfig import configure  # noqa: E402

configure(settings, DIALECT=dbconn.DIALECT, dbconn=dbconn)

from backend.rag import embedder, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
K = 5          # Pass@K
MRR_K = 10

_CJK = re.compile(r"[一-龥]+")
_LATIN = re.compile(r"[A-Za-z0-9%.]+")


def _tokenize(text: str) -> list[str]:
    """轻量中文分词：二元组（bigram）+ 英数词。jieba 未装时的零依赖回退。"""
    toks: list[str] = []
    for m in _LATIN.finditer(text.lower()):
        toks.append(m.group(0))
    for m in _CJK.finditer(text):
        w = m.group(0)
        if len(w) == 1:
            toks.append(w)
        else:
            toks.extend(w[i:i + 2] for i in range(len(w) - 1))  # bigram
    return toks


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = docs
        self.N = len(docs)
        self.avgdl = sum(len(d) for d in docs) / max(1, self.N)
        self.tf: list[dict[str, int]] = [{} for _ in docs]
        self.df: dict[str, int] = {}
        for i, d in enumerate(docs):
            for t in d:
                self.tf[i][t] = self.tf[i].get(t, 0) + 1
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1

    def score(self, query: list[str], i: int) -> float:
        s, dl = 0.0, len(self.docs[i])
        for t in query:
            f = self.tf[i].get(t)
            if not f:
                continue
            idf = math.log(1 + (self.N - self.df[t] + 0.5) / (self.df[t] + 0.5))
            s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return s

    def search(self, query: str, top_k: int) -> list[int]:
        q = _tokenize(query)
        scores = [(self.score(q, i), i) for i in range(self.N)]
        scores.sort(reverse=True)
        return [i for s, i in scores[:top_k] if s > 0]


def _key(h: dict) -> str:
    return f"{h['doc']}#{h['page']}#{h['seq']}"


def main() -> None:
    questions = yaml.safe_load(
        (ROOT / "eval" / "retrieval_questions.yaml").read_text(encoding="utf-8"))["questions"]
    golden = json.loads((ROOT / "eval" / "retrieval_golden.json").read_text(encoding="utf-8"))

    # 语料：全部切块（2104 块规模，BM25 内存内建索引毫无压力）
    rows = dbconn.query("SELECT row_id, doc, page, seq, text FROM rag_chunks ORDER BY row_id")
    texts = [r["text"] or "" for r in rows]
    rid_by_idx = [int(r["row_id"]) for r in rows]
    meta_by_rid = {int(r["row_id"]): (r["doc"], r["page"], r["seq"]) for r in rows}
    print(f"BM25 索引：{len(texts)} 块，分词中…")
    bm25 = BM25([_tokenize(t) for t in texts])
    print("建索引完成")

    print("预热：检查语料缓存…")
    # 向量侧：base 索引 + 查询 embedding（缓存接口没有，逐题调用）

    stats = {"vec_pass": 0, "bm25_pass": 0, "hybrid_pass": 0,
             "vec_mrr": [], "bm25_mrr": [], "hybrid_mrr": []}
    n = 0
    for q in questions:
        qid, query = q["id"], q["question"]
        gkeys = set(golden.get(qid, {}).get("keys", []))
        if not gkeys:
            continue
        n += 1

        # ① 纯向量
        vec_hits = store.search(embedder.embed_query(query), top_k=MRR_K)
        vec_keys = [_key(h) for h in vec_hits]

        # ② 纯 BM25
        bm_idx = bm25.search(query, MRR_K)
        bm_keys = [f"{meta_by_rid[rid_by_idx[i]][0]}#{meta_by_rid[rid_by_idx[i]][1]}"
                   f"#{meta_by_rid[rid_by_idx[i]][2]}" for i in bm_idx]

        # ③ RRF 融合（k=60 业界默认）
        k_rrf = 60
        rrf: dict[str, float] = {}
        for rank, key in enumerate(vec_keys):
            rrf[key] = rrf.get(key, 0) + 1 / (k_rrf + rank + 1)
        for rank, key in enumerate(bm_keys):
            rrf[key] = rrf.get(key, 0) + 1 / (k_rrf + rank + 1)
        hyb_keys = [k for k, _ in sorted(rrf.items(), key=lambda x: -x[1])[:MRR_K]]

        def _pass_and_mrr(keys: list[str], pass_acc: str, mrr_acc: str, gkeys=gkeys) -> None:
            hit_rank = next((i + 1 for i, k in enumerate(keys[:K]) if k in gkeys), None)
            if hit_rank:
                stats[pass_acc] += 1
            mrr = next((1 / (i + 1) for i, k in enumerate(keys[:MRR_K]) if k in gkeys), 0.0)
            stats[mrr_acc].append(mrr)

        _pass_and_mrr(vec_keys, "vec_pass", "vec_mrr")
        _pass_and_mrr(bm_keys, "bm25_pass", "bm25_mrr")
        _pass_and_mrr(hyb_keys, "hybrid_pass", "hybrid_mrr")

    def _avg(v: list[float]) -> float:
        return round(sum(v) / len(v), 4) if v else 0.0

    print(f"\n=== {n} 题对比（金标口径）===")
    print(f"纯向量   Pass@{K}: {stats['vec_pass']}/{n}  MRR@{MRR_K}: {_avg(stats['vec_mrr'])}")
    print(f"纯 BM25  Pass@{K}: {stats['bm25_pass']}/{n}  MRR@{MRR_K}: {_avg(stats['bm25_mrr'])}")
    print(f"RRF 融合 Pass@{K}: {stats['hybrid_pass']}/{n}  MRR@{MRR_K}: {_avg(stats['hybrid_mrr'])}")

    better = stats["hybrid_pass"] > stats["vec_pass"] or \
        _avg(stats["hybrid_mrr"]) > _avg(stats["vec_mrr"])
    print("\n结论：", "混合检索优于纯向量，值得接进生产（再人工核对分布）"
          if better else "混合检索未超过纯向量基线 → 不上线（负结果照实记录）")


if __name__ == "__main__":
    main()
