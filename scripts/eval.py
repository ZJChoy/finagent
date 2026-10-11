"""20 题评测脚本。

用法（在项目根目录执行，服务需先起来）：

    python scripts/eval.py                 # 跑全部 20 题
    python scripts/eval.py --limit 3       # 先跑 3 题
    python scripts/eval.py --out eval/results.md

做三件事：
1. **冻结并记录配置** —— 评测结果只有在同一套参数下才可比；
2. 每题在**新会话**里跑，记录首字/完成延迟、token、引用、可疑引用、是否未接地；
3. 写出一份 `results.md`（含自动初判，**最终判定仍需人工对照**）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import yaml
except ImportError:
    print("需要 pyyaml：pip install pyyaml")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_Q = ROOT / "eval" / "questions.yaml"
DEFAULT_OUT = ROOT / "eval" / "results.md"

_REFUSE_HINTS = (
    "未找到", "无法回答", "没有找到", "未披露", "无法预测", "不能预测", "不构成",
    "尚未发布", "不存在", "没有公开", "无从", "无法给出",
)


def _get(base: str, path: str, timeout: int = 30):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _ask(base: str, question: str, timeout: int = 240) -> dict:
    """在**新会话**里问一题，返回该题的指标。"""
    body = json.dumps({"message": question, "conversation_id": None}).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/chat", data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    first = end = None
    text = ""
    calls: list[str] = []
    cites = 0
    perf: dict = {}
    err = None

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            kind = ev.get("type")
            if kind == "content":
                if first is None:
                    first = time.time() - t0
                text += ev.get("delta", "")
            elif kind == "tool_call":
                calls.append(ev.get("name", "?"))
            elif kind == "citations":
                cites = len(ev.get("items") or [])
            elif kind == "end":
                end = time.time() - t0
                text = ev.get("content") or text
                perf = ev
            elif kind == "error":
                err = ev.get("message")

    return {
        "first_latency": round(first, 2) if first else None,
        "latency": round(end, 2) if end else None,
        "tokens": (perf.get("usage") or {}).get("total_tokens"),
        "citations": cites,
        "tool_calls": calls,
        "invalid_citations": perf.get("invalid_citations") or [],
        "ungrounded": bool(perf.get("ungrounded")),
        "prefetched": perf.get("prefetched") or {},
        "answer": text,
        "error": err,
    }


def _auto_verdict(category: str, m: dict) -> str:
    """自动初判（**仅供参考**，最终要人工对照）。"""
    if m["error"]:
        return "❌ 出错"
    ans = m["answer"] or ""
    if category == "refuse":
        return "✅ 疑似正确" if any(h in ans for h in _REFUSE_HINTS) else "⚠️ 待人工确认（未见拒答措辞）"
    if m["ungrounded"]:
        return "❌ 未接地（已拒答）"
    if m["invalid_citations"]:
        return "❌ 可疑引用"
    if category in ("numeric", "calc") and not any(ch.isdigit() for ch in ans):
        return "⚠️ 待人工确认（未见数字）"
    if category == "qualitative" and m["citations"] == 0:
        return "⚠️ 待人工确认（无引用）"
    return "✅ 待人工确认"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--questions", default=str(DEFAULT_Q))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    qs = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))["questions"]
    if args.limit:
        qs = qs[: args.limit]

    # 1) 冻结配置：评测结果只有同一套参数下才可比
    try:
        cfg = _get(args.base, "/api/config")
    except Exception as e:  # noqa: BLE001
        print(f"读配置失败（服务起来了吗？）：{e}")
        sys.exit(1)
    snapshot = {
        "model": cfg.get("model"),
        "tools_enabled": cfg.get("tools_enabled"),
        "memory_enabled": cfg.get("memory_enabled"),
        "memory_keep_recent": cfg.get("memory_keep_recent"),
        "memory_summarize_at": cfg.get("memory_summarize_at"),
        "memory_extract_every": cfg.get("memory_extract_every"),
        "base_url": cfg.get("base_url"),
    }
    prompt_head = (cfg.get("system_prompt") or "")[:60].replace("\n", " ")

    print(f"配置已冻结：{snapshot}")
    rows = []
    for i, q in enumerate(qs, 1):
        print(f"[{i}/{len(qs)}] {q['id']} {q['question'][:34]}…", flush=True)
        try:
            m = _ask(args.base, q["question"])
        except Exception as e:  # noqa: BLE001
            m = {"error": f"{type(e).__name__}: {e}", "answer": "", "first_latency": None,
                 "latency": None, "tokens": None, "citations": 0, "tool_calls": [],
                 "invalid_citations": [], "ungrounded": False, "prefetched": {}}
        m["verdict"] = _auto_verdict(q["category"], m)
        m["category"] = q["category"]
        m["id"] = q["id"]
        m["question"] = q["question"]
        m["expect"] = q.get("expect", "")
        rows.append(m)
        print(f"    -> {m['verdict']} | 首字 {m['first_latency']}s | 完成 {m['latency']}s | tokens {m['tokens']}")

    # 2) 汇总
    lat = [r["latency"] for r in rows if r.get("latency")]
    firsts = [r["first_latency"] for r in rows if r.get("first_latency")]
    toks = [r["tokens"] for r in rows if r.get("tokens")]
    ok = sum(1 for r in rows if r["verdict"].startswith("✅"))
    bad = sum(1 for r in rows if r["verdict"].startswith("❌"))

    def pct(vals, p):
        if not vals:
            return None
        vals = sorted(vals)
        idx = min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1))))
        return round(vals[idx], 2)

    lines = [
        "# 评测结果（20 题）",
        "",
        "> 生成方式：`python scripts/eval.py`。每题在**新会话**中运行。",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 一、配置快照（评测必须冻结配置，否则结果不可比）",
        "",
        "```json",
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        "```",
        "",
        f"系统提示词开头：`{prompt_head}…`",
        "",
        "## 二、汇总",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 题数 | {len(rows)} |",
        f"| 自动初判通过 | {ok} （{ok / len(rows):.0%}） |",
        f"| 自动初判失败 | {bad} |",
        f"| 首字延迟 中位 / P95 | {pct(firsts, 50)}s / {pct(firsts, 95)}s |",
        f"| 完成延迟 中位 / P95 | {pct(lat, 50)}s / {pct(lat, 95)}s |",
        f"| token 中位 / 合计 | {int(statistics.median(toks)) if toks else '-'} / {sum(toks) if toks else '-'} |",
        f"| 平均引用条数 | {round(statistics.mean([r['citations'] for r in rows]), 1)} |",
        f"| 可疑引用题数 | {sum(1 for r in rows if r['invalid_citations'])} |",
        f"| 未接地（已拒答）题数 | {sum(1 for r in rows if r['ungrounded'])} |",
        "",
        "> 说明：**自动初判只做粗筛**，最终「任务成功率 / 引用可核率 / 拒答正确」需人工对照 `expect` 逐题确认。",
        "> 成本需按你的实际单价折算（脚本只记 token，不预设价格）。",
        "",
        "## 三、逐题明细",
        "",
        "| # | 分类 | 问题 | 首字(s) | 完成(s) | tokens | 引用 | 预取 | 工具 | 初判 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        pre = r.get("prefetched") or {}
        pre_txt = f"A{pre.get('fragments', 0)}" + ("+B" if pre.get("snapshot") else "")
        lines.append(
            f"| {r['id']} | {r['category']} | {r['question'][:28]} | {r['first_latency']} | "
            f"{r['latency']} | {r['tokens']} | {r['citations']} | {pre_txt} | "
            f"{','.join(r['tool_calls']) or '-'} | {r['verdict']} |"
        )

    lines += ["", "## 四、回答摘录（供人工判定）", ""]
    for r in rows:
        ans = (r["answer"] or r.get("error") or "").strip().replace("\n", " ")
        lines.append(f"**{r['id']}**（{r['category']}）{r['question']}")
        lines.append("")
        lines.append(f"- 预期：{r['expect']}")
        lines.append(f"- 实答：{ans[:400]}")
        lines.append(f"- 初判：{r['verdict']}")
        lines.append("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n已写入 {out}")
    print(f"自动初判：通过 {ok} / 失败 {bad} / 共 {len(rows)}")


if __name__ == "__main__":
    main()
