"""检索质量评测：给「召回 / 重排」实验造一把尺子，并对比多种配置。

    python scripts/retrieval_eval.py              # 基线 + 关键词改写
    python scripts/retrieval_eval.py --relabel    # 忽略标注缓存

## 标注方式（pool 法，但池取并集）

多个配置各自会召回不同内容，如果各评各的池，就没法公平比较。
所以**池 = 所有参与对比的配置各自 top-N 的并集**，标注池内每一块，
再让每个配置在自己的召回结果里排名 —— 这样分母一致。

指标分母 = **池内相关块数**。

⚠️ 两条必须写进报告的局限：
1. 池外的相关块统计不到（pooling 的固有缺陷）→ recall 绝对值偏高，只看**相对变化**；
2. 未标注的块按「不相关」处理，这也是 pooling 的标准假设，但不是事实。

## 标注缓存

按 **(题号, 文档, 页, 序号)** 缓存，因此**跨配置、跨重跑都能复用**，不会重复花钱。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from backend import llm  # noqa: E402
from backend.rag import embedder, rerank, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
POOL = 20
K = 5
LABEL_CHARS = 380
COMPANIES = ("美的集团", "贵州茅台", "美的", "茅台")

_LABEL_SYS = (
    "你是检索相关性标注员。给定一个问题和若干文本片段，判断**每一段是否包含能回答该问题的信息**。"
    "标准要严：只有确实含有答案或关键信息的才算相关，仅仅主题沾边不算。"
    '只输出 JSON 数组，形如 [{"i":1,"rel":true},{"i":2,"rel":false}]，顺序与片段一致，不要输出其它文字。'
)

_REWRITE_SYS = (
    "你是检索查询改写器。把用户的问句改写成**最适合做语义检索的关键词串**："
    "去掉「是什么 / 是多少 / 有哪些 / 情况如何」这类问句外壳，保留关键名词与限定词（年份、指标名），保留公司名。"
    "输出一行，用空格分隔，不要标点，不要解释。"
    "例：美的集团的主要业务板块有哪些？ → 美的集团 业务板块\n"
    "例：贵州茅台 2024 年的归母净利润是多少？ → 贵州茅台 2024 归母净利润\n"
    "例：贵州茅台的分红政策是怎样的？ → 贵州茅台 分红政策"
)

# 金标（golden chunk）：每题「最直接回答该问题」的 1–2 块。
# 为什么需要它：池里动辄有十几到二十几块「沾边相关」，此时 recall@5 的理论上限只有 5/20 左右，
# 指标结构性偏低、量不出真实水平。金标块命中（Pass@k）才是业界常用口径。
_GOLDEN_SYS = (
    "你是检索金标标注员。给定一个问题与若干候选片段，选出**最直接回答该问题**的片段（最多 2 个）。"
    "「最直接回答」= 片段里含有**答案本身或回答所必需的关键信息**"
    "（例如问资产负债率，片段里就有具体比率数字）。"
    "只是同一家公司/同一份年报的其他内容、只是主题沾边、只是同一张表的其他行，都不算。"
    "如果**没有任何片段**能回答问题，picks 给空数组。"
    '只输出 JSON：{"picks":[编号...],"why":"一句话理由"}，不要其它文字。'
)


def _key(h: dict) -> str:
    return f"{h['doc']}#{h['page']}#{h['seq']}"


def _retrieve(query: str, top_n: int) -> list[dict]:
    return store.search(embedder.embed_query(query), top_k=top_n)


# 「章节上下文索引」：向量化时把章节路径拼在正文前（见 scripts/contextual_index.py）
_CTX_PATH = store.INDEX_DIR / "vectors_ctx.npy"
_VEC_CTX = store.load_vectors(_CTX_PATH) if _CTX_PATH.exists() else None


def _retrieve_ctx(query: str, top_n: int) -> list[dict]:
    if _VEC_CTX is None:
        return []
    return store.search(embedder.embed_query(query), top_k=top_n, vectors=_VEC_CTX)


# 「LLM 专属上下文索引」：每块一句 chunk 专属说明，拼在正文前再向量化
_LLMCTX_PATH = store.INDEX_DIR / "vectors_llmctx.npy"
_VEC_LLMCTX = store.load_vectors(_LLMCTX_PATH) if _LLMCTX_PATH.exists() else None


def _retrieve_llmctx(query: str, top_n: int) -> list[dict]:
    if _VEC_LLMCTX is None:
        return []
    return store.search(embedder.embed_query(query), top_k=top_n, vectors=_VEC_LLMCTX)


def _strip_company(text: str) -> str:
    out = text
    for c in COMPANIES:
        out = out.replace(c, " ")
    return " ".join(out.split()) or text


# ---------------------------------------------------------------- 标注（按块缓存）

def _label_pool(question: str, pool: list[dict], cache: dict, qid: str) -> dict:
    """返回 {块key: bool}，只对没标过的块调 LLM。"""
    known = cache.setdefault(qid, {})
    todo = [h for h in pool if _key(h) not in known]
    if not todo:
        return known

    for i in range(0, len(todo), 12):  # 每批 12 块，避免单次太长
        batch = todo[i : i + 12]
        blocks = "\n\n".join(
            f"[{j}] {h['text'][:LABEL_CHARS]}" for j, h in enumerate(batch, start=1)
        )
        try:
            raw = llm.complete(
                [
                    {"role": "system", "content": _LABEL_SYS},
                    {"role": "user", "content": f"问题：{question}\n\n片段：\n{blocks}"},
                ],
                max_tokens=600,
            )
            start, end = raw.find("["), raw.rfind("]")
            arr = json.loads(raw[start : end + 1]) if start >= 0 and end > start else []
        except Exception as e:  # noqa: BLE001
            print(f"    标注失败：{type(e).__name__}: {e}")
            arr = []
        for item in arr:
            try:
                idx = int(item.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(batch):
                known[_key(batch[idx])] = bool(item.get("rel"))
        for h in batch:  # 没被 LLM 提到的块，按不相关记，避免每次重标
            known.setdefault(_key(h), False)
    return known


# ---------------------------------------------------------------- 指标

def _pick_golden(question: str, pool: list[dict], cache: dict, qid: str) -> list[str]:
    """选金标块（返回块 key 列表）。池子变了就重选。"""
    entry = cache.get(qid)
    if entry and entry.get("pool_size") == len(pool) and entry.get("keys"):
        return entry["keys"]
    blocks = "\n\n".join(f"[{j}] {h['text'][:300]}" for j, h in enumerate(pool, start=1))
    keys: list[str] = []
    why = ""
    try:
        raw = llm.complete(
            [
                {"role": "system", "content": _GOLDEN_SYS},
                {"role": "user", "content": f"问题：{question}\n\n候选片段：\n{blocks}"},
            ],
            max_tokens=300,
        )
        s, e = raw.find("{"), raw.rfind("}")
        obj = json.loads(raw[s : e + 1]) if s >= 0 and e > s else {}
        for j in obj.get("picks", [])[:2]:
            try:
                idx = int(j) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(pool):
                keys.append(_key(pool[idx]))
        why = str(obj.get("why", ""))[:60]
    except Exception:  # noqa: BLE001
        pass
    cache[qid] = {"keys": keys, "pool_size": len(pool), "why": why}
    return keys


def _golden_metrics(hits: list[dict], golden_keys: list[str], k: int) -> dict:
    """金标块命中：金标块有没有进前 k，以及它排在第几。"""
    if not golden_keys:
        return {"golden_hit": None, "golden_mrr": None}
    rank = None
    for i, h in enumerate(hits, start=1):
        if _key(h) in golden_keys:
            rank = i
            break
    return {
        "golden_hit": (1.0 if rank and rank <= k else 0.0) if rank else 0.0,
        "golden_mrr": (1.0 / rank) if rank else 0.0,
    }


def _metrics(hits: list[dict], labels: dict, k: int) -> dict:
    total_rel = sum(1 for v in labels.values() if v)
    ranked = [bool(labels.get(_key(h))) for h in hits[:k]]
    hit = sum(1 for v in ranked if v)
    mrr = 0.0
    for i, h in enumerate(hits, start=1):
        if labels.get(_key(h)):
            mrr = 1.0 / i
            break
    return {
        "recall": (hit / total_rel) if total_rel else None,
        "precision": hit / k,
        "mrr": mrr,
        "hit": 1.0 if hit else 0.0,
        "related_in_pool": total_rel,
    }


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 4) if vals else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(ROOT / "eval" / "retrieval_questions.yaml"))
    ap.add_argument("--labels", default=str(ROOT / "eval" / "retrieval_labels.json"))
    ap.add_argument("--out", default=str(ROOT / "eval" / "retrieval_report.md"))
    ap.add_argument("--pool", type=int, default=POOL)
    ap.add_argument("--k", type=int, default=K)
    ap.add_argument("--relabel", action="store_true")
    args = ap.parse_args()

    questions = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    lpath = Path(args.labels)
    cache: dict = {}
    if lpath.exists() and not args.relabel:
        cache = json.loads(lpath.read_text(encoding="utf-8"))
        cache = {k: (v if isinstance(v, dict) else {}) for k, v in cache.items()}

    print(f"切块 {store.count()} 条｜向量模型 {store.embedding_models()}")
    print(f"题目 {len(questions)} 道｜池 {args.pool}（多配置并集）｜k={args.k}\n")

    kw_file = lpath.with_name("retrieval_rewrites.json")
    rewrites: dict = {}
    if kw_file.exists() and not args.relabel:
        rewrites = json.loads(kw_file.read_text(encoding="utf-8"))

    golden_file = lpath.with_name("retrieval_golden.json")
    gcache: dict = {}
    if golden_file.exists():
        gcache = json.loads(golden_file.read_text(encoding="utf-8"))

    per_q = []
    for i, q in enumerate(questions, 1):
        question = q["question"]
        base = _retrieve(question, args.pool)

        # 关键词改写（结果单独缓存，重跑不再花钱）
        if q["id"] not in rewrites:
            try:
                rewrites[q["id"]] = llm.complete(
                    [{"role": "system", "content": _REWRITE_SYS},
                     {"role": "user", "content": question}],
                    max_tokens=60,
                ).strip().split("\n")[0]
            except Exception:  # noqa: BLE001
                rewrites[q["id"]] = question
            kw_file.write_text(json.dumps(rewrites, ensure_ascii=False, indent=1), encoding="utf-8")
        kw = rewrites[q["id"]]
        kw_no_name = _strip_company(kw)

        kw_hits = _retrieve(kw, args.pool)
        kwn_hits = _retrieve(kw_no_name, args.pool)
        ctx_hits = _retrieve_ctx(question, args.pool)
        llmctx_hits = _retrieve_llmctx(question, args.pool)

        # 池 = 各配置 top-N 的并集
        pool, seen = [], set()
        for h in base + kw_hits + kwn_hits + ctx_hits + llmctx_hits:
            if _key(h) not in seen:
                seen.add(_key(h))
                pool.append(h)

        labels = _label_pool(question, pool, cache, q["id"])
        golden = _pick_golden(question, pool, gcache, q["id"])

        # ④⑤ 重排：对向量候选（base 池）做交叉编码精排，再取前 k
        order_a, ok_a = rerank.rerank(question, [h["text"] for h in base], top_n=args.k)
        order_b, ok_b = rerank.rerank(kw_no_name, [h["text"] for h in base], top_n=args.k)
        order_c, ok_c = rerank.rerank(question, [h["text"] for h in llmctx_hits], top_n=args.k)
        rerank_hits = [base[i] for i in order_a][: args.k] if ok_a else base[: args.k]
        rerank_kw_hits = [base[i] for i in order_b][: args.k] if ok_b else base[: args.k]
        # ⑧ = ⑦ 的候选池 + 重排（⑧ 的 top-k 仍是 ⑦ 候选的子集，不引入新块，池不变）
        rerank_llmctx = (
            [llmctx_hits[i] for i in order_c][: args.k] if ok_c else llmctx_hits[: args.k]
        )

        configs = {
            "① 基线（原问句）": base,
            "② 关键词改写": kw_hits,
            "③ 关键词（去公司名）": kwn_hits,
            "④ 向量 + 重排": rerank_hits,
            "⑤ 去名关键词 + 重排": rerank_kw_hits,
            "⑥ 章节上下文索引": ctx_hits,
            "⑦ LLM专属上下文索引": llmctx_hits,
            "⑧ LLM上下文 + 重排": rerank_llmctx,
        }
        metrics: dict = {}
        for n, h in configs.items():
            m = _metrics(h, labels, args.k)
            m.update(_golden_metrics(h, golden, args.k))
            metrics[n] = m
        rec = {
            "id": q["id"], "question": question, "kw": kw, "kw_no_name": kw_no_name,
            "pool": len(pool), "rerank_ok": ok_a and ok_b,
            "golden": golden, "metrics": metrics,
        }
        per_q.append(rec)
        m = rec["metrics"]
        name0 = "① 基线（原问句）"
        print(
            f"[{i}/{len(questions)}] {q['id']} 池 {len(pool)}｜相关 {m[name0]['related_in_pool']}｜金标 "
            f"{len(golden)} 块｜金标命中@k：" + " ".join(f"{n[0]}{m[n]['golden_hit']}" for n in metrics)
            + f"｜MRR 基线 {m[name0]['mrr']:.2f}"
            + ("" if rec["rerank_ok"] else "  ⚠️重排失败")
        )

    lpath.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    golden_file.write_text(json.dumps(gcache, ensure_ascii=False, indent=1), encoding="utf-8")

    names = list(per_q[0]["metrics"].keys())
    summary = {
        n: {
            "recall@k": _mean([r["metrics"][n]["recall"] for r in per_q]),
            "precision@k": _mean([r["metrics"][n]["precision"] for r in per_q]),
            "MRR": _mean([r["metrics"][n]["mrr"] for r in per_q]),
            "hit@k": _mean([r["metrics"][n]["hit"] for r in per_q]),
            "金标命中@k": _mean([r["metrics"][n]["golden_hit"] for r in per_q]),
            "金标MRR": _mean([r["metrics"][n]["golden_mrr"] for r in per_q]),
        }
        for n in names
    }

    L = [
        "# 检索质量评测（配置对比）",
        "",
        f"> 生成：`python scripts/retrieval_eval.py`　时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"> 切块 {store.count()}｜模型 {store.embedding_models()}｜池 {args.pool}（**多配置并集**）｜k={args.k}",
        "",
        "## 一、标注方法（先读，否则指标会被误读）",
        "",
        f"**池 = 各配置 top-{args.pool} 的并集**（不各评各的池，才能公平比较）。"
        "池内每块用 LLM 判断「是否真能回答该问题」，指标分母 = 池内相关块数。",
        "",
        "⚠️ **局限 1**：池外的相关块统计不到（pooling 固有缺陷）→ **recall 绝对值偏高，只看相对变化**。",
        "",
        "⚠️ **局限 2**：未标注块按「不相关」处理（pooling 标准假设，非事实）。",
        "",
        "⚠️ **局限 3**：相关性由 **LLM 初标**，有噪声 → 已出人工抽查表 `retrieval_labels_review.md`。",
        "",
        "## 一·补｜为什么要看「金标命中」",
        "",
        "池里常有一二十块「沾边相关」（如 R08 有 24 块），此时 **recall@k 的理论上限只有 k/20 左右**，"
        "**指标结构性偏低，量不出真实水平**。",
        "",
        "所以每题额外标出 **1–2 块「金标块」**（真正含答案的那块），并看它**有没有进前 k**"
        "（即 Pass@k，业界常用口径；Anthropic 的 Contextual Retrieval cookbook 也这么测）。",
        "金标块由 LLM 从候选池里选，**建议人工抽查**。",
        "",
        "",
        "## 二、配置对比",
        "",
        "| 指标 | " + " | ".join(names) + " |",
        "|---" * (len(names) + 1) + "|",
    ]
    for metric in ("recall@k", "precision@k", "MRR", "hit@k"):
        L.append(
            f"| {metric} | " + " | ".join(str(summary[n][metric]) for n in names) + " |"
        )
    L.append("| **金标命中@k** | " + " | ".join(f"**{summary[n]['金标命中@k']}**" for n in names) + " |")
    L.append("| **金标MRR** | " + " | ".join(f"**{summary[n]['金标MRR']}**" for n in names) + " |")

    L += [
        "",
        "> 上两行是**建议重点看**的指标 —— 它们不受「池里相关块太多」这个结构性问题的干扰。",
    ]

    L += [
        "",
        "## 三、逐题对比",
        "",
        "| 题号 | 问题 | 池 | 相关 | " + " | ".join(f"{n} recall" for n in names) + " | 改写后的检索词 |",
        "|---" * (len(names) + 4) + "|",
    ]
    for r in per_q:
        m = r["metrics"]
        L.append(
            f"| {r['id']} | {r['question'][:22]} | {r['pool']} | "
            f"{m[names[0]]['related_in_pool']} | "
            + " | ".join(str(m[n]["recall"]) for n in names)
            + f" | {r['kw']} |"
        )

    # 找出「哪个配置更好」的分布
    L += ["", "## 四、金标块（供人工抽查）", "",
          "| 题号 | 问题 | 金标块 | 选它的理由 |", "|---|---|---|---|"]
    for r in per_q:
        picks = []
        for key in r.get("golden", []):
            d, p, _ = key.split("#")
            picks.append(f"{d} p{p}")
        L.append(
            f"| {r['id']} | {r['question'][:22]} | {'、'.join(picks) or '（无）'} | "
            f"{gcache.get(r['id'], {}).get('why', '')} |"
        )

    L += ["", "## 五、结论", ""]
    for n in names[1:]:
        better = [r["id"] for r in per_q
                  if (r["metrics"][n]["mrr"] or 0) > (r["metrics"][names[0]]["mrr"] or 0)]
        worse = [r["id"] for r in per_q
                 if (r["metrics"][n]["mrr"] or 0) < (r["metrics"][names[0]]["mrr"] or 0)]
        L.append(f"- **{n}**：MRR 变好的 {len(better)} 题（{better}），变差的 {len(worse)} 题（{worse}）")

    out = Path(args.out)
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n== 汇总 ==")
    for n in names:
        print(f"  {n}: {summary[n]}")
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    main()
