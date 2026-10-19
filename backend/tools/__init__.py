"""导入即完成工具注册；上层统一用 registry.* 访问。"""
from . import calc, finance, knowledge  # noqa: F401  触发注册
from .registry import all_tools, call, catalogs, get, openai_specs, register  # noqa: F401
