"""应用配置桥（框架层）：让 core 模块以属性访问方式读取应用层配置。

core 模块禁止 import 应用层（backend/）；应用在 import 时调用
configure(settings_obj, **extra) 注入配置对象与附加项（如 SQL 方言模块），
此后 `from finagent.core.appconfig import settings` 的属性访问原样透传。
必须先 configure 再 import 依赖它的 core 模块（store.py 有模块级常量）。
"""
from __future__ import annotations

_settings = None
_extra: dict = {}


class _Proxy:
    def __getattr__(self, name):
        if _settings is None:
            raise RuntimeError("appconfig 未 configure：先调用 configure(settings, **extra)")
        if name in _extra:
            return _extra[name]
        return getattr(_settings, name)


settings = _Proxy()


def configure(settings_obj, **extra) -> None:
    global _settings
    _settings = settings_obj
    _extra.update(extra)
