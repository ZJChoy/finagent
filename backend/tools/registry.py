"""工具注册中心——已迁移至 finagent.core.tools（框架层）。本模块保留为应用层 shim。

`_REGISTRY` 现在住在框架层；calc/finance/knowledge 等**领域工具实现仍在应用层**，
import 时经本 shim 的 register() 注册进框架注册表。
"""
from finagent.core.tools import (  # noqa: F401
    Tool,
    all_tools,
    call,
    catalogs,
    get,
    openai_specs,
    register,
)
