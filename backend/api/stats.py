"""工作台统计接口。

会话/消息/token 来自 `db`；文档与切块来自 `rag.store` —— 两边各管各的，互不越界。
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import db
from ..config import settings
from ..rag import store

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
def stats():
    data = db.get_stats()
    data["documents"] = len(store.list_documents())
    data["chunks"] = store.count()
    data["llm_ready"] = settings.llm_ready
    data["model"] = db.get_config()["model"]
    return data
