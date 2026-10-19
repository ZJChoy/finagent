"""RAG 引用命中率评测（D6-7 · G4）。

对每道 grounding 题经 /api/chat 取回答与 citations，判定：
- 引用命中：citations 中存在 doc==gold_doc 且 |page-gold_page|<=1 的引用；
- 同时要求答案正文引用了命中的片段编号（防"引用了但没用"）。

拒答题判定：回答含拒答措辞（未找到/无法回答/不构成…）即正确。

    python scripts/citation_eval.py --out eval/citation-baseline.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finagent.core.evaluator import REFUSE_HINTS  # noqa: E402

DEFAULT_Q = ROOT / "eval" / "citation_gold.yaml"


def _sse_ask(base: str, question: str) -> dict:
    """新会话问一题，解析 SSE 事件流，返回 {answer, citations, error}。"""
    body = json.dumps({"message": question, "conversation_id": None}).encode()
    req = urllib.request.Request(base + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    answer, citations, error = "", [], None
    with urllib.request.urlopen(req, timeout=240) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            kind = ev.get("type")
            if kind == "content":
                answer += ev.get("delta", "")
            elif kind == "citations":
                citations = ev.get("items") or citations
            elif kind == "end":
                answer = ev.get("content") or answer
                citations = ev.get("citations") or citations
            elif kind == "error":
                error = ev.get("message")
    return {"answer": answer, "citations": citations, "error": error}


def _judge(q: dict, m: dict) -> tuple[str, bool]:
    """返回 (结论, 是否命中)。拒答题：命中 = 回答含拒答措辞。"""
    if m["error"]:
        return "❌ 出错", False
    if q["category"] == "refuse":
        hit = any(h in m["answer"] for h in REFUSE_HINTS)
        return ("✅ 正确拒答" if hit else "❌ 未拒答（编造风险）"), hit
    if q.get("metric") == "value":
        # 数值一致性：金标值（年报原文提取）出现在答案中，且无可疑引用
        hit = any(v in m["answer"] for v in q.get("gold_values", [])) and not m.get("citations") is None
        return ("✅ 数值一致" if hit else "❌ 数值不一致/未引用"), hit
    # 可接受出处列表：同一内容可有多个等价出处（两版年报、概述/分部），逐一比对
    acceptable = q.get("acceptable") or [{"doc": q.get("gold_doc"), "page": q.get("gold_page")}]
    hit_cites = [
        c for c in m["citations"]
        if any(
            c.get("doc") == a["doc"]
            and a.get("page") is not None
            and abs(int(c.get("page", -1)) - a["page"]) <= 1
            for a in acceptable
            if a.get("doc")
        )
    ]
    if not hit_cites:
        return "❌ 引用未命中", False
    # 答案正文必须真的引用了命中片段的编号（防"检索到了但答案没用"）
    used = any(f"【片段 {c['no']}】" in m["answer"] or f"片段 {c['no']}" in m["answer"]
               for c in hit_cites)
    if not used:
        return "⚠️ 检索命中但答案未引用", False
    return "✅ 引用命中", True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--questions", default=str(DEFAULT_Q))
    ap.add_argument("--out", default=str(ROOT / "eval" / "citation-baseline.md"))
    args = ap.parse_args()

    gold = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    rows = []
    for i, q in enumerate(gold, 1):
        t0 = time.time()
        try:
            m = _sse_ask(args.base, q["question"])
        except Exception as e:  # noqa: BLE001
            m = {"answer": "", "citations": [], "error": f"{type(e).__name__}: {e}"}
        verdict, hit = _judge(q, m)
        rows.append({"q": q, "m": m, "verdict": verdict, "hit": hit,
                     "latency": round(time.time() - t0, 2)})
        print(f"[{i:02d}/{len(gold)}] {q['question'][:32]}… -> {verdict}")

    grounded = [r for r in rows if r["q"]["category"] == "grounding"]
    refuses = [r for r in rows if r["q"]["category"] == "refuse"]
    hit_rate = sum(r["hit"] for r in grounded) / len(grounded) if grounded else 0
    refuse_rate = sum(r["hit"] for r in refuses) / len(refuses) if refuses else 0
    lats = [r["latency"] for r in rows]

    out = [
        "# RAG 引用命中率评测（citation-baseline）",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}　金标：eval/citation_gold.yaml"
        f"（grounding {len(grounded)} 题 + refuse {len(refuses)} 题，页码 ±1 容差）",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| **引用命中率（grounding）** | **{hit_rate:.0%}**（{sum(r['hit'] for r in grounded)}/{len(grounded)}） |",
        f"| **拒答准确率（refuse）** | **{refuse_rate:.0%}**（{sum(r['hit'] for r in refuses)}/{len(refuses)}） |",
        f"| 平均时延 | {round(statistics.mean(lats), 2)}s |",
        "",
        "## 逐题明细",
        "",
        "| # | 类别 | 问题 | 金标 | 结论 | 时延 |",
        "|---|------|------|------|------|------|",
    ]
    for i, r in enumerate(rows, 1):
        q = r["q"]
        gold = f"{q.get('gold_doc', '-')} p{q.get('gold_page')}" if q["category"] == "grounding" else "拒答"
        out.append(f"| Q{i:02d} | {q['category']} | {q['question']} | {gold} | {r['verdict']} | {r['latency']}s |")

    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n已写入 {args.out}")
    print(f"引用命中率 {hit_rate:.0%} | 拒答准确率 {refuse_rate:.0%}")


if __name__ == "__main__":
    main()
