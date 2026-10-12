"""生成「检索标注 · 人工抽查表」——给 LLM 初标做人工复核用。

为什么需要：LLM 标注有噪声（本次实测就发现它会整题漏标或整题标太松），
所以最后一步必须落到人眼。抽查重点放在**最可疑的题**：相关数为 0 的（可能漏标）
和相关数很多的（可能松标）。

    python scripts/retrieval_review.py
    → eval/retrieval_labels_review.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from backend.rag import embedder, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOP = 8  # 每题展示前几块


def main() -> None:
    qs = {
        q["id"]: q
        for q in yaml.safe_load(
            (ROOT / "eval" / "retrieval_questions.yaml").read_text(encoding="utf-8")
        )["questions"]
    }
    lab = json.loads((ROOT / "eval" / "retrieval_labels.json").read_text(encoding="utf-8"))

    # 标注按 (题号 → {块key: bool}) 存；先算出每题标了多少块相关
    counts = {qid: sum(1 for v in d.values() if v) for qid, d in lab.items()}
    ranked = sorted(counts.items(), key=lambda kv: kv[1])
    review = [qid for qid, _ in ranked[:3]] + [qid for qid, _ in ranked[-2:]]

    L = [
        "# 检索标注 · 人工抽查表",
        "",
        "## 怎么用（约 10 分钟）",
        "",
        "每题下面是**基线检索的前 8 块**。LLM 已经初标了，**你只需要看有没有标错**：",
        "",
        "- 标了「相关」但你看着不相关 → 在最后一列写 `错`",
        "- 标了「不相关」但你看着其实相关 → 在最后一列写 `错`",
        "- 没问题就留空",
        "",
        "> 抽的是**最可疑的题**：标出相关最少的 3 题（可能整题漏标）和最多的 2 题（可能标太松）。",
        "> 片段顺序由同一套检索复现（索引与向量都是确定的）。",
        "",
    ]

    for qid in review:
        q = qs[qid]
        hits = store.search(embedder.embed_query(q["question"]), top_k=TOP)
        item = lab.get(qid, {})
        L += [
            f"## {qid} · {q['question']}",
            "",
            f"（整池标出 **{counts.get(qid, 0)}** 块相关；下面是基线 top-{TOP}）",
            "",
            "| 排名 | 来源 | 页码 | 相似度 | 片段开头 | LLM 判断 | 人工核对 |",
            "|---|---|---|---|---|---|---|",
        ]
        for i, h in enumerate(hits, start=1):
            key = f"{h['doc']}#{h['page']}#{h['seq']}"
            mark = "**相关**" if item.get(key) else "不相关"
            prev = h["text"][:70].replace("|", "/").replace("\n", " ")
            L.append(
                f"| {i} | {h['doc']} | p{h['page']} | {h['score']} | {prev}… | {mark} |  |"
            )
        L.append("")

    out = ROOT / "eval" / "retrieval_labels_review.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已生成 {out}")
    print(f"抽查题：{review}｜各题标出相关数：{[counts[q] for q in review]}")


if __name__ == "__main__":
    main()
