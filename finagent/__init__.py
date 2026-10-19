"""finagent · 可复用的 LLM Agent 框架层。

纪律：core 不允许 import 任何 apps/backend 模块——应用相关的
提示词、领域钩子、工具实现留在应用层，通过构造参数注入。
"""
