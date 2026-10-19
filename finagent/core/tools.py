"""工具注册中心：统一管理「可被大模型调用的函数」。（框架层，无应用依赖）

每个工具用 OpenAI Function Calling 的 JSON Schema 描述参数；
Agent loop 只依赖这里导出的 openai_specs / get / catalogs。
工具实现由应用层在 import 时调用 register() 完成注册。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., dict]
    group: str = "通用"


_REGISTRY: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    _REGISTRY[tool.name] = tool


def get(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def openai_specs(names: list[str] | None = None) -> list[dict]:
    """生成传给大模型的 tools 参数；names=None 表示全部启用。"""
    tools = all_tools() if names is None else [t for t in all_tools() if t.name in names]
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def catalogs() -> list[dict]:
    """给配置页展示的工具清单。"""
    return [
        {"name": t.name, "description": t.description, "group": t.group}
        for t in all_tools()
    ]


def call(name: str, arguments: dict[str, Any]) -> dict:
    """执行工具，统一把异常转成可读文本（不让异常打断整轮对话）。

    返回 {"ok": bool, "content": str, "citations": list}。
    `ok` 用于上层的**接地校验**：没成功调过任何工具，就不该给出事实性回答。
    """
    tool = get(name)
    if tool is None:
        return {"ok": False, "content": f"未知工具：{name}", "citations": []}
    try:
        result = tool.func(**arguments)
    except TypeError as e:  # 参数不对
        return {"ok": False, "content": f"工具 {name} 参数错误：{e}", "citations": []}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "content": f"工具 {name} 执行出错：{type(e).__name__}: {e}",
            "citations": [],
        }
    result.setdefault("citations", [])
    result.setdefault("ok", True)
    return result
