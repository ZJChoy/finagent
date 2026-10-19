"""知识库工具：让 Agent **自己决定**何时检索年报原文（RAG）。

注意：这一步起，RAG 不再是"每轮自动检索"，而是作为**一个工具**交给模型决定
（技术方案 §四 数据流）。好处是省上下文、也更像真实的 Agent；
代价是模型可能不调 —— 所以在系统提示词里明确要求"涉及年报原文时先检索"。
"""
from __future__ import annotations

from ..rag import retriever
from .registry import Tool, register


def _search_knowledge(query: str, top_k: int = 5) -> dict:
    hits = retriever.retrieve(query, top_k=top_k)
    if not hits:
        return {"content": "知识库中未检索到相关内容（可能尚未入库）。", "citations": []}
    return {
        "content": retriever.build_context(hits),
        "citations": retriever.citations_of(hits),
    }


register(
    Tool(
        name="search_knowledge",
        group="知识库",
        description=(
            "在已入库的上市公司年报原文中检索相关片段。"
            "当问题涉及公司业务、经营情况、管理层讨论、风险因素、分红政策等"
            "「文字描述类」内容时调用。返回结果含片段编号与来源页码，回答时必须引用。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或问题"},
                "top_k": {"type": "integer", "description": "召回片段数，默认 5"},
            },
            "required": ["query"],
        },
        func=_search_knowledge,
    )
)
