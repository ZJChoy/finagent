"""RAG 检索管线——已迁移至 finagent.core.retriever。本包保留为应用层 shim。

configure 必须先于子模块 import 执行（store.py 有模块级常量依赖 DATA_DIR/DIALECT）。
对外 `from backend.rag import ingest / store / retriever ...` 签名不变。
"""
from ..config import settings
from .. import dbconn

from finagent.core.appconfig import configure

configure(settings, DIALECT=dbconn.DIALECT, dbconn=dbconn)

from finagent.core.retriever import (  # noqa: F401,E402
    embedder,
    ingest,
    loader,
    rerank,
    retriever,
    splitter,
    store,
)
