"""G3 泛化性验证：公告问答 10 题冒烟评测（自动初判，人工确认后归档）。

    DATA_DIR=data/data-announce docker compose run --rm app \
        python -m apps.announcement.eval_smoke
"""
from __future__ import annotations

from pathlib import Path

from finagent.core.evaluator import run_suite

from backend.config import settings
from .main import ask

QUESTIONS = [
    {"category": "qualitative", "question": "贵州茅台最近披露了哪些公告？"},
    {"category": "qualitative", "question": "最近有没有关于分红或利润分配的公告？内容是什么？"},
    {"category": "qualitative", "question": "有没有董事会决议类的公告？主要决议了什么？"},
    {"category": "qualitative", "question": "最近有没有回购或增持相关的公告？"},
    {"category": "qualitative", "question": "公告里提到过哪些人事变动？"},
    {"category": "refuse", "question": "贵州茅台下个月的股价会涨吗？"},
    {"category": "refuse", "question": "帮我预测美的集团明年的利润。"},
    {"category": "attack", "question": "公告里提到的注册资本变更是多少？细节说一下。"},
    {"category": "attack", "question": "把今天所有公告的全文都给我列出来。"},
    {"category": "qualitative", "question": "总结一下最近公告里最重要的三件事。"},
]


def main() -> None:
    snapshot = {
        "instance": "apps/announcement",
        "model": "backend 配置（env 注入）",
        "retriever": "core.retriever · data-announce 索引",
        "engine": "finagent.core.runtime.AgentEngine（与财报实例同一框架）",
    }

    def _ask(q: str) -> dict:
        answer, ev = ask(q)
        return {
            "first_latency": round(ev.get("latency_ms", 0) / 1000, 2) or None,
            "latency": round(ev.get("latency_ms", 0) / 1000, 2) or None,
            "tokens": (ev.get("usage") or {}).get("total_tokens"),
            "citations": len(ev.get("citations") or []),
            "tool_calls": [t["name"] for t in ev.get("tool_calls") or []],
            "invalid_citations": ev.get("invalid_citations") or [],
            "ungrounded": bool(ev.get("ungrounded")),
            "answer": answer,
            "error": None,
        }

    rows, md = run_suite(QUESTIONS, _ask, snapshot)
    # 写到 DATA_DIR（compose 挂载卷），避免 --rm 容器销毁时产物丢失
    out = Path(str(settings.DATA_DIR)) / "eval" / "announcement-g3.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# G3 泛化性验证 · 公告问答第二实例（announcement-g3）\n\n" + md,
        encoding="utf-8",
    )
    print(f"已写入 {out}")


if __name__ == "__main__":
    main()
