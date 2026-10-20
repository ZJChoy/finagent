"""工具调用门控：把「要不要先查」变成确定性判断，而不是靠模型自觉。

动机（实测踩到）：问「美的集团2025年营业收入是多少」，模型会**跳过工具**直接凭记忆答，
甚至断言"尚未发布 2025 年报"——而语料里就有这份年报。

现在的用法（L0 + 双通道）：
- `needs_grounding` 命中 → 代码**强制预检索**年报原文（通道 A）；
- `needs_snapshot` 命中 → 代码**强制预取**结构化财务数据（通道 B）。
"""
from __future__ import annotations

import re

_CODE_RE = re.compile(r"\d{6}")

# 公司→代码映射（唯一定义）：门控信号与实体解析共用，新增公司只改这里
COMPANY_CODE_MAP: tuple[tuple[str, str], ...] = (
    ("贵州茅台", "600519"),
    ("茅台", "600519"),
    ("美的集团", "000333"),
    ("美的", "000333"),
)

# 定性信号：需要「年报原文」作为依据
_QUALITATIVE = (
    "分红", "派息", "股息", "渠道", "业务", "主营", "风险", "年报", "报表",
    "现金流", "股东", "研发", "产能", "库存", "战略", "管理层", "治理",
    "员工", "客户", "供应商", "行业", "竞争",
)

# 数值信号：需要「结构化财务数据」作为依据
_NUMERIC = (
    "营收", "营业收入", "营业总收入", "收入", "净利润", "净利", "利润", "毛利",
    "roe", "净资产收益率", "每股", "资产负债率", "总资产", "总负债", "负债",
    "同比", "增速", "增长率", "财务数据", "财务指标", "会计数据", "报表数据",
)

_COMPANIES = tuple(name for name, _ in COMPANY_CODE_MAP)


def needs_grounding(message: str) -> bool:
    """本题是否必须基于资料回答（命中则强制预检索年报原文）。"""
    m = (message or "").lower()
    if _CODE_RE.search(m):
        return True
    if any(k in m for k in _QUALITATIVE):
        return True
    if any(k in m for k in _NUMERIC):
        return True
    if any(c in m for c in _COMPANIES):
        return True
    return False


def needs_snapshot(message: str) -> bool:
    """本题是否要**精确数字**（命中则强制预取结构化财务数据）。"""
    m = (message or "").lower()
    return any(k in m for k in _NUMERIC)
