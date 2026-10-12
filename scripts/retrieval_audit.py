"""检索标注的**独立审计**：盲审包生成 + 与原标注对比。

## 为什么要独立审计

原标注是「同一个模型 + 同一个 prompt」产出的，让它自己检查自己等于没检查。
所以这里把流程拆成两半：

1. `--packet` 生成**盲审包** —— 只给「问题 + 片段」，**不含原标注**；
2. 交给**另一个独立上下文/子智能体**重新判一遍；
3. `--compare` 把两份判断对齐，算**一致率**并列出**分歧清单**。

一致率低 → 原标注不可信 → 之前所有实验结论都要打折扣。这是必须知道的事。

    python scripts/retrieval_audit.py --packet
    python scripts/retrieval_audit.py --compare
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

from backend.rag import embedder, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AUDIT_QIDS = ["R02", "R04", "R07", "R09", "R13", "R14", "R16", "R18"]
TOP = 5
CHARS = 500  # 盲审时给足上下文，别因为截断把判断带偏


def _key(h: dict) -> str:
    return f"{h['doc']}#{h['page']}#{h['seq']}"


def build_packet() -> None:
    qs = {
        q["id"]: q
        for q in yaml.safe_load(
            (ROOT / "eval" / "retrieval_questions.yaml").read_text(encoding="utf-8")
        )["questions"]
    }
    L = [
        "# 检索相关性 · 盲审包",
        "",
        "**这份文件不含原标注** —— 请独立判断，不要参考任何已有结论。",
        "",
        "## 任务",
        "",
        "对下面每道题的每个片段，判断：**这个片段是否包含能回答该问题的信息？**",
        "",
        "- 标准要严：只有确实含有答案或关键信息的才判 `true`；仅仅主题沾边、或只是同一家公司/同一张表的其他行，都算 `false`。",
        "- 只输出 JSON 数组，不要任何解释文字，格式：",
        "  ```json",
        '  [{"qid":"R02","i":1,"rel":true},{"qid":"R02","i":2,"rel":false}, ...]',
        "  ```",
        f"- 一共 {len(AUDIT_QIDS)} 道题 × {TOP} 个片段 = {len(AUDIT_QIDS) * TOP} 条判断。",
        "",
        "---",
        "",
    ]
    for qid in AUDIT_QIDS:
        q = qs[qid]
        hits = store.search(embedder.embed_query(q["question"]), top_k=TOP)
        L += [f"## {qid} · {q['question']}", ""]
        for i, h in enumerate(hits, start=1):
            L += [
                f"**[{i}]** 来源：{h['doc']} 第 {h['page']} 页",
                "",
                "> " + h["text"][:CHARS].replace("\n", "\n> "),
                "",
            ]
    out = ROOT / "eval" / "audit_packet.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已生成盲审包 {out}")
    print(f"题数 {len(AUDIT_QIDS)} × {TOP} = {len(AUDIT_QIDS) * TOP} 条判断")


def compare() -> None:
    qs = {
        q["id"]: q
        for q in yaml.safe_load(
            (ROOT / "eval" / "retrieval_questions.yaml").read_text(encoding="utf-8")
        )["questions"]
    }
    orig = json.loads((ROOT / "eval" / "retrieval_labels.json").read_text(encoding="utf-8"))
    verdicts = json.loads((ROOT / "eval" / "audit_verdicts.json").read_text(encoding="utf-8"))

    # 重建每题 top-5，拿到 key，用于对齐
    rows = []
    agree = total = 0
    for qid in AUDIT_QIDS:
        hits = store.search(embedder.embed_query(qs[qid]["question"]), top_k=TOP)
        for i, h in enumerate(hits, start=1):
            key = _key(h)
            o = bool(orig.get(qid, {}).get(key, False))
            a = None
            for v in verdicts:
                if v.get("qid") == qid and int(v.get("i", 0)) == i:
                    a = bool(v.get("rel"))
                    break
            if a is None:
                continue
            total += 1
            same = (o == a)
            agree += 1 if same else 0
            rows.append(
                {
                    "qid": qid, "i": i, "doc": h["doc"], "page": h["page"],
                    "score": h["score"], "orig": o, "audit": a, "same": same,
                    "preview": h["text"][:60].replace("\n", " "),
                }
            )

    dis = [r for r in rows if not r["same"]]
    L = [
        "# 检索标注 · 独立审计报告",
        "",
        f"> 审计方式：盲审包（不含原标注）交给**独立子智能体**重新判断，再与原标注对齐。",
        f"> 样本：{len(AUDIT_QIDS)} 题 × {TOP} 块 = {total} 条",
        "",
        "## 一、一致率",
        "",
        f"- 总判断：**{total}** 条",
        f"- 一致：**{agree}** 条",
        f"- **一致率：{agree / total:.1%}**" if total else "- 无数据",
        f"- 分歧：**{len(dis)}** 条",
        "",
        "## 二、怎么解读",
        "",
        "| 一致率 | 含义 | 对前面实验结论的影响 |",
        "|---|---|---|",
        "| ≥ 85% | 标注基本可信 | 五档对比的**相对结论**可用 |",
        "| 70–85% | 有噪声但方向可用 | 结论只能当**趋势**，不能宣称「提升 X%」 |",
        "| < 70% | 标注不可信 | **前面的对比结论作废**，必须重标 |",
        "",
        "## 三、分歧清单",
        "",
    ]
    if dis:
        L += [
            "| 题号 | # | 来源 | 页码 | 相似度 | 原标注 | 独立审 | 片段开头 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in dis:
            L.append(
                f"| {r['qid']} | {r['i']} | {r['doc']} | p{r['page']} | {r['score']} | "
                f"{'相关' if r['orig'] else '不相关'} | {'相关' if r['audit'] else '不相关'} | "
                f"{r['preview']}… |"
            )
    else:
        L.append("（无分歧）")

    L += ["", "## 四、逐题一致率", "", "| 题号 | 一致 / 总数 |", "|---|---|"]
    for qid in AUDIT_QIDS:
        sub = [r for r in rows if r["qid"] == qid]
        if sub:
            ok = sum(1 for r in sub if r["same"])
            L.append(f"| {qid} | {ok} / {len(sub)} |")

    out = ROOT / "eval" / "audit_report.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已生成 {out}")
    print(f"一致率 {agree / total:.1%}（{agree}/{total}），分歧 {len(dis)} 条" if total else "无数据")


def _disagreements() -> list[dict]:
    """复现那批分歧（顺序确定，用于按序号裁定）。"""
    qs = {
        q["id"]: q
        for q in yaml.safe_load(
            (ROOT / "eval" / "retrieval_questions.yaml").read_text(encoding="utf-8")
        )["questions"]
    }
    orig = json.loads((ROOT / "eval" / "retrieval_labels.json").read_text(encoding="utf-8"))
    verdicts = json.loads((ROOT / "eval" / "audit_verdicts.json").read_text(encoding="utf-8"))

    items = []
    for qid in AUDIT_QIDS:
        hits = store.search(embedder.embed_query(qs[qid]["question"]), top_k=TOP)
        for i, h in enumerate(hits, start=1):
            o = bool(orig.get(qid, {}).get(_key(h), False))
            a = next(
                (bool(v.get("rel")) for v in verdicts
                 if v.get("qid") == qid and int(v.get("i", 0)) == i),
                None,
            )
            if a is not None and o != a:
                items.append({"qid": qid, "i": i, "h": h, "orig": o, "audit": a})
    return items


def apply_verdicts() -> None:
    """按人工裁定修正标注（人工金标准优先于两个 LLM）。"""
    vpath = ROOT / "eval" / "audit_human_verdicts.json"
    if not vpath.exists():
        items = _disagreements()
        tmpl = {str(n): None for n in range(1, len(items) + 1)}
        vpath.write_text(json.dumps(tmpl, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已生成裁定模板 {vpath}，请填入 true/false 后再跑")
        return

    verdicts = json.loads(vpath.read_text(encoding="utf-8"))
    items = _disagreements()
    lpath = ROOT / "eval" / "retrieval_labels.json"
    labels = json.loads(lpath.read_text(encoding="utf-8"))
    (ROOT / "eval" / "retrieval_labels.before_audit.json").write_text(
        json.dumps(labels, ensure_ascii=False), encoding="utf-8"
    )

    changed = []
    for n, it in enumerate(items, start=1):
        v = verdicts.get(str(n))
        if v is None:
            continue
        key = _key(it["h"])
        before = labels.get(it["qid"], {}).get(key)
        labels.setdefault(it["qid"], {})[key] = bool(v)
        if before != bool(v):
            changed.append((n, it["qid"], it["h"]["page"], before, bool(v)))

    lpath.write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已按人工裁定修正 {len(changed)} 条标注（原件备份为 retrieval_labels.before_audit.json）：")
    for n, qid, page, b, a in changed:
        print(f"  #{n} {qid} p{page}: {'相关' if b else '不相关'} → {'相关' if a else '不相关'}")


def adjudicate() -> None:
    """把分歧整理成「待人工裁定表」——每条给片段全文，人只需二选一。"""
    qs = {
        q["id"]: q
        for q in yaml.safe_load(
            (ROOT / "eval" / "retrieval_questions.yaml").read_text(encoding="utf-8")
        )["questions"]
    }
    orig = json.loads((ROOT / "eval" / "retrieval_labels.json").read_text(encoding="utf-8"))
    verdicts = json.loads((ROOT / "eval" / "audit_verdicts.json").read_text(encoding="utf-8"))

    items = []
    for qid in AUDIT_QIDS:
        hits = store.search(embedder.embed_query(qs[qid]["question"]), top_k=TOP)
        for i, h in enumerate(hits, start=1):
            o = bool(orig.get(qid, {}).get(_key(h), False))
            a = next(
                (bool(v.get("rel")) for v in verdicts
                 if v.get("qid") == qid and int(v.get("i", 0)) == i),
                None,
            )
            if a is not None and o != a:
                items.append({"qid": qid, "i": i, "q": qs[qid]["question"], "hit": h,
                              "orig": o, "audit": a})

    L = [
        "# 待裁定表（人工金标准）",
        "",
        "## 你要做什么",
        "",
        f"下面 **{len(items)} 条**，是「原标注」和「独立盲审」**判断相反**的地方。",
        "每条我都给了**片段全文**，你只需要回答一个问题：",
        "",
        "> **这个片段，到底能不能回答这道题？**",
        "",
        "在每条的「你的裁定」处写 **`相关`** 或 **`不相关`**（也可以直接回我「1 相关，2 不相关…」）。",
        "",
        "判断口径（和两轮标注一致）：",
        "- **相关** = 片段里确实含有答案本身，或回答所必需的关键信息",
        "- **不相关** = 只是同一家公司/同一份年报的其他内容、只是主题沾边、只是同一张表的其他行",
        "",
        "> 你裁定完，我用它当**金标准**修正标注 → 重跑五档对比 → 得到敢写进简历的数字。",
        "",
        "---",
        "",
    ]
    for n, it in enumerate(items, start=1):
        h = it["hit"]
        L += [
            f"## {n}. {it['qid']} · 问：{it['q']}",
            "",
            f"**片段**（{h['doc']} 第 {h['page']} 页 ｜ 相似度 {h['score']}）",
            "",
        ]
        L += ["> " + ln for ln in h["text"].split("\n")]
        L += [
            "",
            f"- 原标注判：**{'相关' if it['orig'] else '不相关'}**",
            f"- 独立审判：**{'相关' if it['audit'] else '不相关'}**",
            f"- **你的裁定**：☐ 相关　☐ 不相关",
            "",
            "---",
            "",
        ]

    out = ROOT / "eval" / "audit_adjudicate.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已生成 {out}")
    print(f"分歧 {len(items)} 条，等你裁定")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packet", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--adjudicate", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.packet:
        build_packet()
    elif args.compare:
        compare()
    elif args.adjudicate:
        adjudicate()
    elif args.apply:
        apply_verdicts()
    else:
        build_packet()


if __name__ == "__main__":
    main()
