"""FinAgent 后端入口。

启动：python -m uvicorn backend.main:app --reload
前端与 API 同源，访问 http://127.0.0.1:8000
"""
from __future__ import annotations

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
