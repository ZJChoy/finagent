"""公告问答实例的领域工具：注册进框架注册表（本进程独立，不与财报实例混跑）。"""
from __future__ import annotations

from finagent.core.tools import Tool, register
from finagent.core.retriever import retriever


def query_announcements(query: str, top_k: int = 5) -> dict:
    """检索公告原文片段（含编号上下文与结构化引用）。"""
    hits = retriever.retrieve(query, top_k=top_k)
    if not hits:
        return {"ok": True, "content": "未检索到相关公告片段。", "citations": []}
    return {
        "ok": True,
        "content": retriever.build_context(hits),
        "citations": retriever.citations_of(hits),
    }


register(Tool(
    name="query_announcements",
    description="检索上市公司公告原文片段。凡涉及公告内容（决议、分红、回购、人事变动等）的问题，必须先调用本工具；片段未覆盖的内容如实说明，不得编造。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要检索的公告主题关键词，如'分红' '回购'"},
            "top_k": {"type": "integer", "description": "返回片段数，默认 5"},
        },
        "required": ["query"],
    },
    func=query_announcements,
    group="公告",
))
