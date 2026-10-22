"""FinAgent 后端入口。

启动：python -m uvicorn backend.main:app --reload
前端与 API 同源，访问 http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, dbconn
from .api import chat as api_chat
from .api import config as api_config
from .api import documents as api_documents
from .api import memories as api_memories
from .api import stats as api_stats
from .config import settings
from .rag import store

app = FastAPI(title="FinAgent · 上市公司财报分析 Agent", version="0.1.0")

app.include_router(api_chat.router)
app.include_router(api_documents.router)
app.include_router(api_memories.router)
app.include_router(api_config.router)
app.include_router(api_stats.router)

db.init()
store.init()


def _startup_check() -> None:
    """启动自检：只警告不阻断（除索引损坏——带着坏索引服务必然答错）。

    输出结构化 JSON 到 stdout，便于公网部署时 grep。
    """
    problems: list[str] = []

    ping = dbconn.ping()
    if not ping.get("ok"):
        problems.append(f"数据库不可达：{ping.get('error')}")

    facts = store.index_health()
    if not facts["consistent"]:
        # fail-closed：base 向量与元数据行数不一致 = 检索必然错位，拒绝带病启动
        print(json.dumps({
            "level": "FATAL",
            "check": "index",
            "message": (
                f"向量索引与元数据不一致（base {facts['base_rows']} 行 vs 元数据 "
                f"{facts['meta_rows']} 行）。检索会答错，拒绝启动。"
                "请运行 scripts/index_doctor.py 体检，或 reset 后重新入库。"
            ),
            "index": facts,
        }, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    if facts["stale_variants"]:
        problems.append(
            f"索引变体已过期（{facts['stale_variants']}），已自动回退 base。"
            "如需恢复变体，重建后运行 scripts/index_doctor.py --stamp。"
        )

    if not settings.llm_ready:
        problems.append("未配置 DASHSCOPE_API_KEY——对话不可用，其余功能正常。")

    host = (settings.HOST or "127.0.0.1").lower()
    if host in ("0.0.0.0", "::") and not settings.ADMIN_TOKEN:
        problems.append(
            "HOST 绑定到公网地址但未设置 ADMIN_TOKEN：变更类接口已 fail-closed（403），"
            "但仍建议配置以启用知识库管理。"
        )

    for p in problems:
        print(json.dumps({"level": "WARN", "check": "startup", "message": p},
                         ensure_ascii=False), file=sys.stderr)


_startup_check()


@app.get("/health")
def health():
    ping = dbconn.ping()
    return {
        "ok": True,
        "llm_ready": settings.llm_ready,
        "chat_model": settings.CHAT_MODEL,
        "embed_model": settings.EMBED_MODEL,
        "base_url": settings.BASE_URL,
        "db": dbconn.DIALECT,
        "db_ok": ping.get("ok"),
        "retrieval_index": store.active_vector_path().name,
        "index": store.index_health(),   # 行数一致性与 stale 变体（错位 = 必须重建）
        "rerank": settings.RERANK_ENABLED,
    }


# ---------------- 前端静态页 ----------------
assets_dir = settings.FRONTEND_DIR / "assets"
assets_dir.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/")
def page_index():
    return FileResponse(str(settings.FRONTEND_DIR / "index.html"))


@app.get("/knowledge")
def page_knowledge():
    return FileResponse(str(settings.FRONTEND_DIR / "knowledge.html"))


@app.get("/config")
def page_config():
    return FileResponse(str(settings.FRONTEND_DIR / "config.html"))
