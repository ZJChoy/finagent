"""会话记忆——已迁移至 finagent.core.memory（框架层）。本模块保留为应用层 shim。

存储适配器 = backend.db（七个存储函数），llm 与模型/默认人设从 backend 配置注入。
对外函数签名与迁移前完全一致。
"""
from __future__ import annotations

from finagent.core.memory import SessionMemory, parse_items  # noqa: F401

from .. import db, llm
from ..config import DEFAULT_SYSTEM_PROMPT, settings

_sm = SessionMemory(
    db=db,
    llm_mod=llm,
    rewrite_model=settings.REWRITE_MODEL,
    default_system_prompt=DEFAULT_SYSTEM_PROMPT,
)

load_history = _sm.load_history
maybe_summarize = _sm.maybe_summarize
maybe_extract = _sm.maybe_extract
memory_block = _sm.memory_block
augment_system_prompt = _sm.augment_system_prompt
