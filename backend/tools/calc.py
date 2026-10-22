"""计算工具：精确四则运算。

用 ast 解析表达式，**不走 eval**（避免执行任意代码）。
财报比率（净利率、同比增速等）应交给它算，而不是让模型心算。
"""
from __future__ import annotations

import ast
import operator as op

from .registry import Tool, register

_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.Mod: op.mod,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("表达式含不支持的运算")


def _format_number(value: float) -> str:
    """大数不用科学计数法输出：模型会原样引用字符串，必须可读。
    整数结果给千分位整数；非整数 round 6 位后取最短往返表示——
    :f 会在大数值上带出浮点尾差（如 172054171890.91 → ...910004）。

    ⚠️ 负数先取绝对值格式化再补符号：(-1, 0) 区间的整数部分是 "-0"，
    int("-0") == 0 会丢掉负号（实测 -0.5 → "0.5"，模型照抄成"上升 0.5%"）。"""
    if value != value or value in (float("inf"), float("-inf")):
        return str(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value == int(value):
        return f"{sign}{int(value):,}"
    text = repr(round(value, 6))
    if "e" in text or "E" in text:
        # 极小值（round 6 位后归零，repr 出科学计数法）：定点展开保留量级
        text = f"{value:.16f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"{sign}{int(whole):,}" + ("." + frac if frac else "")


def _calculate(expression: str) -> dict:
    try:
        value = _eval(ast.parse(expression.strip(), mode="eval").body)
        return {"ok": True, "content": f"{expression} = {_format_number(value)}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"计算失败：{e}"}


register(
    Tool(
        name="calculate",
        group="计算",
        description=(
            "做精确的数学计算（四则运算）。"
            "计算财务比率（如 净利润/营业收入）、同比增速等需要精确结果时调用，不要自己心算。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式，如 (1234/5678)*100"},
            },
            "required": ["expression"],
        },
        func=_calculate,
    )
)
