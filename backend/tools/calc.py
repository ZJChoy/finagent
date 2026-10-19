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


def _calculate(expression: str) -> dict:
    try:
        value = _eval(ast.parse(expression.strip(), mode="eval").body)
        return {"content": f"{expression} = {round(value, 6):g}"}
    except Exception as e:  # noqa: BLE001
        return {"content": f"计算失败：{e}"}


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
