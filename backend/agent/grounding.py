"""系统预取与「双通道」注入。

两条通道：
- **通道 A · 年报原文片段** —— 本地向量检索，可核对页码；
- **通道 B · 结构化财务数据** —— akshare，需先解析出股票代码，字段与期间更明确。

动机：把 RAG 做成工具后，"要不要查"变成模型决定，实测不可靠（会跳过工具凭记忆答）。
所以涉及公司事实/数字的问题，由**代码**保底把资料取好、**按通道标注**后注入；
模型只负责"基于这些资料作答"，并在结论后写明用的是哪条通道。
"""
from __future__ import annotations

import re

from ..rag import retriever
from ..tools import finance

# 本地映射：样例语料就两家公司，最稳的一级
_NAME_TO_CODE = (
    ("贵州茅台", "600519"),
    ("茅台", "600519"),
    ("美的集团", "000333"),
    ("美的", "000333"),
)

_CODE_IN_QUERY = re.compile(r"\b([036]\d{5})\b")
_DOC_CODE = re.compile(r"^(\d{6})[_-]")
_TEXT_CODE = re.compile(r"(?:证券|股票)\s*代码[：:\s]*([036]\d{5})")

# 单位换算参照：实测出现过 10 倍换算错误，把参照直接摆在数据旁边
_UNIT_NOTE = (
    "【单位换算参照】1 亿元 = 100,000,000 元 = 100,000 千元 = 10,000 万元；"
    "1 万元 = 10,000 元。凡涉及单位换算，**必须调用 calculate 验算，不要心算**。"
)


def resolve_symbol(query: str, hits: list[dict]) -> tuple[str | None, str]:
    """公司名 / 代码 → 6 位股票代码。三级兜底，返回 (代码, 依据说明)。"""
    q = query or ""
    for name, code in _NAME_TO_CODE:
        if name in q:
            return code, f"本地映射「{name}」"
    m = _CODE_IN_QUERY.search(q)
    if m:
        return m.group(1), "问题里带了代码"
    # 我们入库时文件名就是 代码_年份_类型，最相关的那条通常就是目标公司
    for h in hits:
        m = _DOC_CODE.match(h.get("doc") or "")
        if m:
            return m.group(1), f"片段文件名「{h['doc']}」"
    for h in hits:
        m = _TEXT_CODE.search(h.get("text") or "")
        if m:
            return m.group(1), "片段正文里的「证券代码」"
    return None, "未识别"


def build_prefetch(query: str, hits: list[dict], want_snapshot: bool) -> dict:
    """按需预取两条通道，拼成注入块。"""
    parts: list[str] = []
    snapshot: str | None = None
    symbol: str | None = None
    how = ""

    if hits:
        parts.append(
            "── 通道 A｜年报原文片段（本地向量检索，可核对页码）──\n"
            + retriever.build_context(hits)
        )

    if want_snapshot:
        symbol, how = resolve_symbol(query, hits)
        if symbol:
            result = finance.fetch_financials(symbol)
            if result.get("ok", True):
                snapshot = result.get("content", "")
                parts.append(
                    f"── 通道 B｜结构化财务数据（akshare，来源识别依据：{how}）──\n{snapshot}"
                )
            else:
                parts.append(
                    "── 通道 B｜结构化财务数据（预取失败）──\n" + result.get("content", "")
                )
        else:
            parts.append("── 通道 B｜结构化财务数据（未能识别公司代码，本次未预取）──")

    block = "\n\n".join(parts)
    if snapshot:
        block = _UNIT_NOTE + "\n\n" + block  # 有数字时才附换算参照

    return {
        "block": block or None,
        "snapshot": snapshot,
        "symbol": symbol,
        "how": how,
    }
