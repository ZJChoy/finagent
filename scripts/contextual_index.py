"""索引侧「规则式上下文」：给每个块补上它在年报里的章节路径，再重新向量化。

## 为什么做这个（且不换模型）

实测发现：**自然语言长问句的召回远比短关键词差**，而且有些题（如"美的资产负债率"）连正确块都召不到。
根因是**单个 chunk 丢了上下文** —— 它不知道自己属于哪一节、在讲什么。

Anthropic 公开发表的 Contextual Retrieval 解决的就是这个（检索失败率降低 35%–49%）。
但官方做法要**为每块调一次 LLM**（用整篇文档生成上下文）。
我们先用**更便宜的第一步**：**章节路径是现成的，不需要生成**。

> 和用户定的规矩一致：**只做不换模型的改进** —— 靠换更强模型解决问题，体现的是模型能力，不是 Agent 能力。

## 两种模式

    python scripts/contextual_index.py --probe     # 先探：章节标题能识别出多少（不花钱）
    python scripts/contextual_index.py --build     # 建索引：章节路径 + 正文 → 新向量文件

`--build` 会生成 `data/index/{dialect}/vectors_ctx.npy`，
**元数据不动、原始 text 不动** —— 章节前缀只用于 embedding，不进引用、不给用户看。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from backend import dbconn, llm  # noqa: E402
from backend.rag import embedder, store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 年报里的标题层级（宁可漏，不要错：误判会把无关文本塞进向量）
_PATTERNS = [
    (1, re.compile(r"^(第[一二三四五六七八九十]+节)\s*(.{2,30})$")),      # 第三节 管理层讨论与分析
    (2, re.compile(r"^([一二三四五六七八九十]+、)\s*(.{2,30})$")),       # 一、报告期内公司所处行业情况
    (3, re.compile(r"^(（[一二三四五六七八九十]+）)\s*(.{2,30})$")),     # （一）主要业务
    (4, re.compile(r"^(\d+\.\d*)\s*(.{2,30})$")),                       # 1.1 分部信息
]
_BAD_TAIL = ("。", "；", "，", ",", ";", "：", ":", "元", "%")


def _match_heading(line: str) -> tuple[int, str] | None:
    s = line.strip()
    if not (2 <= len(s) <= 34):
        return None
    if s.endswith(_BAD_TAIL):
        return None
    for level, pat in _PATTERNS:
        m = pat.match(s)
        if m:
            title = m.group(2).strip()
            if 1 < len(title) <= 30:
                return level, title
    return None


def _scan() -> tuple[dict[int, str], dict]:
    """按 (doc, page, seq) 顺序扫全部切块，推出每块的章节路径。"""
    rows = dbconn.query(
        "SELECT row_id, doc, page, seq, text FROM rag_chunks ORDER BY doc, page, seq"
    )
    stack: dict[int, str] = {}
    sections: dict[int, str] = {}
    cur_doc = None
    stats = {"total": 0, "with_section": 0, "headings": 0, "docs": {}}

    for r in rows:
        stats["total"] += 1
        if r["doc"] != cur_doc:
            cur_doc = r["doc"]
            stack = {}
            stats["docs"][cur_doc] = {"chunks": 0, "with_section": 0, "headings": 0}
        stats["docs"][cur_doc]["chunks"] += 1

        # 在一块的正文里找标题行（按行扫）
        for line in (r["text"] or "").split("\n"):
            hit = _match_heading(line)
            if hit:
                level, title = hit
                stack = {k: v for k, v in stack.items() if k < level}
                stack[level] = title
                stats["headings"] += 1
                stats["docs"][cur_doc]["headings"] += 1

        path = " › ".join(stack[k] for k in sorted(stack))
        if path:
            sections[int(r["row_id"])] = path
            stats["with_section"] += 1
            stats["docs"][cur_doc]["with_section"] += 1
    return sections, stats


def probe() -> None:
    sections, stats = _scan()
    print("=== 章节识别可行性探测（不花钱）===")
    print(f"总块数        : {stats['total']}")
    print(f"识别到章节的块: {stats['with_section']}（{stats['with_section'] / max(1, stats['total']):.1%}）")
    print(f"识别到的标题数: {stats['headings']}")
    print()
    print("=== 分文档 ===")
    for doc, d in stats["docs"].items():
        pct = d["with_section"] / max(1, d["chunks"])
        print(f"  {doc}: {d['with_section']}/{d['chunks']} 块（{pct:.0%}）| 标题 {d['headings']} 个")
    print()
    print("=== 抽 8 个识别结果看看质量 ===")
    for rid in list(sections)[:8]:
        print(f"  #{rid}: {sections[rid]}")


def build() -> None:
    sections, stats = _scan()
    rows = dbconn.query(
        "SELECT row_id, doc, page, seq, text FROM rag_chunks ORDER BY row_id"
    )
    texts = []
    for r in rows:
        sec = sections.get(int(r["row_id"]), "")
        # 章节路径只进 embedding，不进存库的 text
        texts.append(f"【{sec}】\n{r['text']}" if sec else r["text"])

    print(f"待向量化 {len(texts)} 块（含章节路径的 {stats['with_section']} 块）…")
    vecs = embedder.embed_texts(texts, batch=16)
    arr = np.asarray(vecs, dtype=np.float32)
    out = store.INDEX_DIR / "vectors_ctx.npy"
    np.save(out, arr)
    # 登记指纹：变体行数与 row_id 必须和当前 base 完全对齐，否则检索时被判 stale 回退 base
    store._write_variant_fingerprint(out.name, store._base_fingerprint())
    print(f"已写入 {out}  形状 {arr.shape}")


_LLMCTX_SYS = (
    "你是检索增强助手。给定一份年报的章节目录，以及若干原文片段，"
    "为**每一个**片段写一句「上下文说明」，用来说明：这段在讲什么主题、"
    "涉及哪家公司、哪个报告期、哪个具体指标或事项。要求：\n"
    "- 只写一句，不超过 40 字；\n"
    "- **必须基于给定材料**，不要推测、不要补充材料里没有的信息；\n"
    "- 说明要**具体**（例如「美的集团 2025 年度资产负债率为 61.17%，较上年下降」），"
    "不要写「公司财务数据」这种放之四海皆准的话；\n"
    '只输出 JSON 数组 [{"i":1,"ctx":"..."}, ...]，不要其它文字。'
)


def _outline(rows: list[dict], doc: str, limit: int = 40) -> str:
    """取该文档的顶层标题（第X节 / 一、）作为紧凑目录。"""
    seen, out = set(), []
    for r in rows:
        if r["doc"] != doc:
            continue
        for line in (r["text"] or "").split("\n"):
            hit = _match_heading(line)
            if hit and hit[0] <= 2:
                t = hit[1]
                if t not in seen:
                    seen.add(t)
                    out.append(t)
            if len(out) >= limit:
                return " / ".join(out)
    return " / ".join(out)


def build_llm(batch: int = 10) -> None:
    """生成 **chunk 专属** 的上下文说明，再向量化。

    与 `--build` 的区别：那是规则拼章节路径（低区分度，实测失败）；
    这一步让模型写出**只属于这一块**的描述（高区分度）。
    为控制成本，输入用「目录 + 章节路径 + 正文」，**不用整篇文档**。
    """
    import json

    sections, stats = _scan()
    rows = dbconn.query(
        "SELECT row_id, doc, page, seq, text FROM rag_chunks ORDER BY row_id"
    )
    cache_path = store.INDEX_DIR / "llm_contexts.json"
    cache: dict = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    todo = [r for r in rows if str(r["row_id"]) not in cache]
    print(f"总块 {len(rows)}｜需生成 {len(todo)}｜已缓存 {len(cache)}")
    if not todo:
        print("全部已缓存，直接向量化")

    outlines: dict[str, str] = {}
    for i in range(0, len(todo), batch):
        group = todo[i : i + batch]
        doc = group[0]["doc"]
        if doc not in outlines:
            outlines[doc] = _outline(rows, doc)
        body = "\n\n".join(
            f"[{j}] 【{sections.get(int(r['row_id']), '')}】\n{r['text'][:700]}"
            for j, r in enumerate(group, start=1)
        )
        try:
            raw = llm.complete(
                [
                    {"role": "system", "content": _LLMCTX_SYS},
                    {"role": "user", "content": f"年报章节目录：{outlines[doc]}\n\n片段：\n{body}"},
                ],
                max_tokens=900,
            )
            s, e = raw.find("["), raw.rfind("]")
            arr = json.loads(raw[s : e + 1]) if s >= 0 and e > s else []
        except Exception as ex:  # noqa: BLE001
            print(f"  批次 {i // batch} 失败：{type(ex).__name__}: {ex}")
            arr = []
        for item in arr:
            try:
                j = int(item.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= j < len(group):
                cache[str(group[j]["row_id"])] = str(item.get("ctx", "")).strip()
        for r in group:  # 没生成到的记空串，避免无限重试
            cache.setdefault(str(r["row_id"]), "")
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  进度 {min(i + batch, len(todo))}/{len(todo)}", flush=True)

    texts = []
    for r in rows:
        ctx = cache.get(str(r["row_id"]), "")
        texts.append(f"【{ctx}】\n{r['text']}" if ctx else r["text"])
    filled = sum(1 for r in rows if cache.get(str(r["row_id"])))
    print(f"生成到说明的块：{filled}/{len(rows)}｜开始向量化…")
    vecs = embedder.embed_texts(texts, batch=16)
    out = store.INDEX_DIR / "vectors_llmctx.npy"
    np.save(out, np.asarray(vecs, dtype=np.float32))
    # 登记指纹：变体行数与 row_id 必须和当前 base 完全对齐，否则检索时被判 stale 回退 base
    store._write_variant_fingerprint(out.name, store._base_fingerprint())
    print(f"已写入 {out} 形状 {np.asarray(vecs).shape}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--build-llm", action="store_true")
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()
    if args.build:
        build()
    elif args.build_llm:
        build_llm(batch=args.batch)
    else:
        probe()


if __name__ == "__main__":
    main()
