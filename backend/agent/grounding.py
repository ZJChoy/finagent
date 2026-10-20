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

# 公司映射收敛到 gate.COMPANY_CODE_MAP（唯一定义），避免多处映射漂移
from .gate import COMPANY_CODE_MAP as _NAME_TO_CODE

_CODE_IN_QUERY = re.compile(r"\b([036]\d{5})\b")

# 问句里出现的"公司名"，但语料库没收录 —— 必须显式拒答而不是从无关命中里捞代码
# （实测踩坑：问"格力电器"→ 第 3 级兜底从美的年报文件名捞出 000333 → 数字全真、主语全错）
_DOC_CODE = re.compile(r"^(\d{6})[_-]")
_TEXT_CODE = re.compile(r"(?:证券|股票)\s*代码[：:\s]*([036]\d{5})")

# 单位换算参照：实测出现过 10 倍换算错误，把参照直接摆在数据旁边
_UNIT_NOTE = (
    "【单位换算参照】1 亿元 = 100,000,000 元 = 100,000 千元 = 10,000 万元；"
    "1 万元 = 10,000 元。凡涉及单位换算，**必须调用 calculate 验算，不要心算**。"
)


def resolve_symbol(query: str, hits: list[dict]) -> tuple[str | None, str]:
    """公司名 / 代码 → 6 位股票代码。返回 (代码, 依据说明)。

    ⚠️ 实体一致性校验（实测踩坑：问"格力电器"→ 第 3 级兜底从美的年报文件名捞出
    000333 → 数字全真、主语全错）：解析出的代码，其公司名必须出现在问句里；
    唯一豁免 = 用户直接给了 6 位代码。不满足 → 判"未收录"，拒答而非错配。
    """
    q = query or ""

    # ① 本地映射（问句自带公司名，天然一致）
    for name, code in _NAME_TO_CODE:
        if name in q:
            return code, f"本地映射「{name}」"

    # ② 问句直接给了 6 位代码（用户显式指定，豁免一致性校验）
    m = _CODE_IN_QUERY.search(q)
    if m:
        return m.group(1), "问题里带了代码"

    # ③④ 从检索命中兜底捞码 —— 必须过实体一致性校验
    code = how = None
    for h in hits:
        m = _DOC_CODE.match(h.get("doc") or "")
        if m:
            code, how = m.group(1), f"片段文件名「{h['doc']}」"
            break
    if code is None:
        for h in hits:
            m = _TEXT_CODE.search(h.get("text") or "")
            if m:
                code, how = m.group(1), "片段正文里的「证券代码」"
                break
    if code is None:
        return None, "未识别"

    canon = [name for name, c in _NAME_TO_CODE if c == code]
    if canon and not any(name in q for name in canon):
        return None, (
            f"查询的公司不在语料库收录范围（检索命中指向 {code}，"
            f"但问句未提及 {canon[0]}——实体错配拦截，拒答而非张冠李戴）"
        )
    return code, how


def build_prefetch(query: str, hits: list[dict], want_snapshot: bool) -> dict:
    """按需预取两条通道，拼成注入块。"""
    parts: list[str] = []
    snapshot: str | None = None
    symbol: str | None = None
    how = ""

    symbol, how = resolve_symbol(query, hits) if hits else (None, "无检索命中")
    not_indexed = "未收录" in how or "不在语料库" in how

    if hits and not not_indexed:
        parts.append(
            "── 通道 A｜年报原文片段（本地向量检索，可核对页码）──\n"
            + retriever.build_context(hits)
        )

    if not_indexed:
        parts.append(
            f"── ⚠️ 公司未收录 ──\n{how}。请如实告知用户：本系统目前仅收录茅台、美的的年报，"
            "无法提供该公司的财务数据，**绝不可以引用其他公司的数据作答**。"
        )

    if want_snapshot and not not_indexed:
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
