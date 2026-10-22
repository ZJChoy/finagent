"""三列回归评测框架（框架层）：金标加载、自动初判、报告汇总。

方法论（与 Edge 项目同源）：
- 自动初判只做粗筛：出错 / 未接地 / 可疑引用 直接判负；数字与引用缺失转人工；
- 金标全部来自外部权威，**不使用 LLM 自评作为判定**；
- 每次评测冻结配置快照，同一套参数下结果才可比。

ask_fn 由调用方注入（HTTP 打服务、进程内直调均可），
返回 dict 至少含：first_latency / latency / tokens / citations /
tool_calls / invalid_citations / ungrounded / answer / error。
"""
from __future__ import annotations

import json
import statistics
from collections.abc import Callable

REFUSE_HINTS = (
    "未找到", "无法回答", "没有找到", "未披露", "无法预测", "不能预测", "不构成",
    "尚未发布", "不存在", "没有公开", "无从", "无法给出",
)


def auto_verdict(category: str, m: dict) -> str:
    """自动初判（**仅供参考**，最终要人工对照）。m 为单题指标 dict。"""
    if m.get("error"):
        return "❌ 出错"
    ans = m.get("answer") or ""
    if category == "refuse":
        return "✅ 疑似正确" if any(h in ans for h in REFUSE_HINTS) else "⚠️ 待人工确认（未见拒答措辞）"
    if m.get("ungrounded"):
        return "❌ 未接地（已拒答）"
    if m.get("invalid_citations"):
        return "❌ 可疑引用"
    if category in ("numeric", "calc") and not any(ch.isdigit() for ch in ans):
        return "⚠️ 待人工确认（未见数字）"
    if category == "qualitative" and m.get("citations", 0) == 0:
        return "⚠️ 待人工确认（无引用）"
    return "✅ 待人工确认"


def summarize(rows: list[dict]) -> dict:
    """汇总指标：rows = [{category, metrics(dict), verdict(str)}...]。"""
    ok = sum(1 for r in rows if r["verdict"].startswith("✅"))
    bad = sum(1 for r in rows if r["verdict"].startswith("❌"))
    firsts = [r["metrics"]["first_latency"] for r in rows if r["metrics"].get("first_latency")]
    lats = [r["metrics"]["latency"] for r in rows if r["metrics"].get("latency")]
    toks = [r["metrics"].get("tokens") or 0 for r in rows]

    def _med(v: list) -> float | None:
        return round(statistics.median(v), 2) if v else None

    def _p95(v: list) -> float | None:
        return round(sorted(v)[int(len(v) * 0.95) - 1], 2) if v else None

    return {
        "total": len(rows),
        "auto_pass": ok,
        "auto_fail": bad,
        "first_med": _med(firsts),
        "first_p95": _p95(firsts),
        "latency_med": _med(lats),
        "latency_p95": _p95(lats),
        "tokens_total": sum(toks),
        "suspicious_citations": sum(1 for r in rows if "可疑引用" in r["verdict"]),
        "ungrounded": sum(1 for r in rows if "未接地" in r["verdict"]),
        "errors": sum(1 for r in rows if "出错" in r["verdict"]),
    }


def render_markdown(rows: list[dict], snapshot: dict, extra_notes: list[str] | None = None) -> str:
    """把评测结果渲染为 Markdown 报告（配置快照 + 汇总 + 逐题明细）。"""
    s = summarize(rows)
    lines = [
        "# 评测结果（20 题）",
        "",
        "> 生成方式：finagent.core.evaluator。每题在**新会话**中运行。",
        "> 说明：**自动初判只做粗筛**，最终判定需人工对照金标逐题确认。",
        "",
        "## 一、配置快照（评测必须冻结配置，否则结果不可比）",
        "",
        "```json",
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 二、汇总",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 自动初判通过 | {s['auto_pass']} / {s['total']} |",
        f"| 自动初判失败 | {s['auto_fail']} |",
        f"| 首字延迟 中位 / P95 | {_fmt(s['first_med'])} / {_fmt(s['first_p95'])} |",
        f"| 完成延迟 中位 / P95 | {_fmt(s['latency_med'])} / {_fmt(s['latency_p95'])} |",
        f"| token 合计 | {s['tokens_total']} |",
        f"| 可疑引用题数 | {s['suspicious_citations']} |",
        f"| 未接地（已拒答）题数 | {s['ungrounded']} |",
        f"| 出错题数 | {s['errors']} |",
        "",
        "## 三、逐题明细",
        "",
        "| # | 类别 | 问题 | 首字 | 完成 | 字数 | tokens | 引用 | 工具调用 | 初判 |",
        "|---|------|------|------|------|------|--------|------|---------|------|",
    ]
    for i, r in enumerate(rows, 1):
        m = r["metrics"]
        lines.append(
            f"| Q{i:02d} | {r['category']} | {r['question']} "
            f"| {_fmt(m.get('first_latency'))} | {_fmt(m.get('latency'))} "
            f"| {len(m.get('answer') or '')} | {_fmt(m.get('tokens'))} "
            f"| {m.get('citations', 0)} | {'、'.join(m.get('tool_calls') or []) or '-'} "
            f"| {r['verdict']} |"
        )
    if extra_notes:
        lines += ["", *extra_notes]
    return "\n".join(lines) + "\n"


def _fmt(v) -> str:
    return "-" if v is None else str(v)


def load_questions(path: str, limit: int = 0) -> list[dict]:
    """加载金标集（YAML：{questions: [{category, question, expect...}]}）。"""
    import yaml

    qs = yaml.safe_load(open(path, encoding="utf-8"))["questions"]
    return qs[:limit] if limit else qs


def run_suite(questions: list[dict], ask_fn: Callable[[str], dict], snapshot: dict) -> tuple[list[dict], str]:
    """跑一套评测：ask_fn(question) -> 指标 dict。返回 (rows, markdown)。"""
    rows = []
    for i, q in enumerate(questions, 1):
        m = ask_fn(q["question"])
        v = auto_verdict(q["category"], m)
        rows.append({"category": q["category"], "question": q["question"], "metrics": m, "verdict": v})
        print(f"[{i:02d}/{len(questions)}] {q['question'][:30]}… -> {v}")
    return rows, render_markdown(rows, snapshot)
