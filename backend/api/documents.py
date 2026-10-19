"""知识库接口：状态查询 + 上传入库 + 删除。

检索测试（/search）与引用展示在步骤 4 接入。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from .security import require_admin
from pydantic import BaseModel

from ..config import settings
from ..rag import ingest, retriever, store

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("")
def list_docs():
    return {
        "documents": store.list_documents(),
        "chunks": store.count(),
        "embedding_models": store.embedding_models(),
        "index_file": str(store.VEC_PATH),
    }


@router.post("/upload")
def upload(file: UploadFile = File(...), chunk_size: int = 500, overlap: int = 90, _admin: None = Depends(require_admin)):
    name = file.filename or "unnamed.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "本版本只支持 PDF（年报场景实测均为 PDF）")

    # 存到 data/raw/ 后入库，保持"原始语料可复查"
    settings.RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.RAW_DIR / name
    target.write_bytes(file.file.read())

    try:
        result = ingest.ingest_pdf(target, chunk_size=chunk_size, overlap=overlap)
    except ingest.ScannedPDFError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"入库失败：{type(e).__name__}: {e}")
    return result


@router.delete("/{name}")
def delete_doc(name: str, _admin: None = Depends(require_admin)):
    store.delete_document(name)
    return {"ok": True, "deleted": name}


class SearchIn(BaseModel):
    query: str
    top_k: int = 5


@router.post("/search")
def search(body: SearchIn):
    """直接测召回（不经过模型）—— 用于调 chunk / top_k 做召回优化对比。"""
    hits = retriever.retrieve(body.query, top_k=body.top_k)
    return {
        "query": body.query,
        "top_k": body.top_k,
        "hits": [
            {
                "no": i,
                "doc": h["doc"],
                "page": h["page"],
                "seq": h["seq"],
                "score": h["score"],
                "text": h["text"],
            }
            for i, h in enumerate(hits, start=1)
        ],
    }


@router.post("/reset")
def reset(_admin: None = Depends(require_admin)):
    store.reset()
    return {"ok": True}
