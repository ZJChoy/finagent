"""财务数据工具：用 akshare 取 A 股**结构化**财务数据（只读、免 token）。

按技术方案的分流原则：**数值题走结构化数据**，不靠从年报 PDF 里抠数字。
只读，不涉及任何写操作。
"""
from __future__ import annotations

from .registry import Tool, register

_MAX_ROWS = 12


def _fmt_amount(value) -> str | None:
    """把金额格式化成「原值 + 换算后」——**让模型不用心算单位**。

    动因：实测出现过 10 倍错误（`172,054,171,890.91 元` 被写成「172.05 亿元」）。
    把换算结果直接摆在模型面前，是消除这类错误最有效的手段。
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN
        return None
    if abs(num) >= 1e8:
        return f"{num:,.2f} 元（≈ {num / 1e8:,.2f} 亿元）"
    if abs(num) >= 1e4:
        return f"{num:,.2f} 元（≈ {num / 1e4:,.2f} 万元）"
    return f"{num:,.4f}"


def _get_stock_financials(symbol: str) -> dict:
    try:
        import akshare as ak  # 延迟导入：未安装时不影响其它工具
    except Exception:
        return {"content": "未安装 akshare，无法获取实时财务数据。"}

    code = "".join(ch for ch in str(symbol or "") if ch.isdigit())
    if len(code) != 6:
        return {"content": f"股票代码格式不对：{symbol}（应为 6 位数字，如 600519）"}

    try:
        df = ak.stock_financial_abstract(symbol=code)
    except Exception as e:  # noqa: BLE001
        return {
            "content": f"获取 {code} 财务数据失败（可能是网络或接口变动）：{type(e).__name__}: {e}"
        }

    if df is None or getattr(df, "empty", True):
        return {"content": f"未取到 {code} 的财务数据。"}

    cols = list(df.columns)
    label_col = cols[1] if len(cols) > 1 else cols[0]
    # 选期策略：**最新一期 + 最近两个年度**（列是连续季度，直接取前两列会把年度数据挤掉）
    dates = [str(c) for c in cols[2:]]
    latest = dates[0] if dates else None
    annual = [d for d in dates if d.endswith("1231")][:2]
    periods = [p for p in [latest] + annual if p]

    lines = [
        f"{code} 结构化财务数据（akshare·stock_financial_abstract）",
        "报告期：" + " / ".join(f"{p}{'（最新）' if p == latest else '（年度）'}" for p in periods),
    ]
    for _, row in df.head(_MAX_ROWS).iterrows():
        name = str(row.get(label_col, "")).strip()
        parts = [
            f"{p} = {text}"
            for p in periods
            for text in [_fmt_amount(row.get(p))]
            if text
        ]
        if name and parts:
            lines.append(f"- {name}：" + "；".join(parts))

    return {
        "content": "\n".join(lines),
        "source": f"akshare:stock_financial_abstract:{code}",
    }


def fetch_financials(symbol: str) -> dict:
    """对外的取数入口（供「通道 B 强制预取」直接调用，不经过模型）。"""
    return _get_stock_financials(symbol)


register(
    Tool(
        name="get_stock_financials",
        group="财务数据",
        description=(
            "获取 A 股上市公司的结构化财务数据（按报告期的营收、净利润、毛利率、ROE 等）。"
            "当问题需要**精确财务数字**时调用，不要从年报原文里猜数字。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "6 位 A 股代码，如 600519"},
            },
            "required": ["symbol"],
        },
        func=_get_stock_financials,
    )
)
