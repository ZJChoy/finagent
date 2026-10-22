"""相关性阈值标定脚本（M3.1）。

目的：给"低于阈值 → 无依据 → 拒答"选一个分数下限。
方法：用 retrieval gold 的金标块分数分布 vs 全库块分数分布找分界。

    python scripts/threshold_calibrate.py            # 只打印分布与建议值
    python scripts/threshold_calibrate.py --apply    # 写进 backend/config.py 的建议值
"""
from __future__ import annotations

import argparse
import json
import statistics
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default=str(ROOT / "eval" / "retrieval_questions.yaml"))
    ap.add_argument("--golden", default=str(ROOT / "eval" / "retrieval_golden.json"))
    args = ap.parse_args()

    questions = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))

    # 全库块的分数分布（背景噪声基线）：用 20 个真实问题各取 top-30
    gold_scores: list[float] = []     # 金标块的相似度分数
    noise_scores: list[float] = []    # 非金标块的分数（含"主题沾边"块）
    for q in questions:
        qid = q["id"]
        query = q["question"]
        hits = store.search(embedder.embed_query(query), top_k=30)
        gkeys = set(golden.get(qid, {}).get("keys", []))
        for i, h in enumerate(hits):
            key = f"{h['doc']}#{h['page']}#{h['seq']}"
            if key in gkeys:
                gold_scores.append(h["score"])
            elif i >= 5:  # top-5 之外的才算"召回线以下"的噪声
                noise_scores.append(h["score"])

    if not gold_scores:
        print("没有金标块被召回——阈值无法标定，先检查索引。")
        return

    def pct(v: list[float], p: float) -> float:
        s = sorted(v)
        return s[int(len(s) * p)]

    print(f"金标块分数  n={len(gold_scores)}  min={min(gold_scores):.4f} "
          f"p5={pct(gold_scores, 0.05):.4f} p10={pct(gold_scores, 0.10):.4f} "
          f"中位={statistics.median(gold_scores):.4f}")
    print(f"非金标分数  n={len(noise_scores)}  max={max(noise_scores):.4f} "
          f"p95={pct(noise_scores, 0.95):.4f} p99={pct(noise_scores, 0.99):.4f} "
          f"中位={statistics.median(noise_scores):.4f}")

    # 候选阈值：容忍 5% / 10% 金标被误拒，取对应分位数
    for name, thr in [("保守（容忍 5% 误拒）", pct(gold_scores, 0.05)),
                      ("宽松（容忍 10% 误拒）", pct(gold_scores, 0.10))]:
        kept = sum(1 for s in gold_scores if s >= thr) / len(gold_scores)
        noise_cut = sum(1 for s in noise_scores if s < thr) / max(1, len(noise_scores))
        print(f"{name}: 阈值 {thr:.4f}  金标保留 {kept:.1%}  噪声拦截 {noise_cut:.1%}")



if __name__ == "__main__":
    main()
