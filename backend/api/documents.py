"""知识库接口：状态查询 + 上传入库 + 删除。

检索测试（/search）与引用展示在步骤 4 接入。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from .security import require_admin
from pydantic import BaseModel

from ..config import settings
from ..rag import ingest, retriever, store

router = APIRouter(prefix="/api/documents", tags=["documents"])

# 上传护栏：大小上限防内存打爆；chunk 参数防 step=1 碎片爆炸（产生海量 embedding 调用）
_MAX_PDF_BYTES = 100 * 1024 * 1024
_MIN_CHUNK_SIZE, _MAX_CHUNK_SIZE = 200, 4000


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
    # 只取文件名部分：剥离 ../、绝对路径等目录成分，落盘位置锁定 RAW_DIR
    name = Path(file.filename or "").name or "unnamed.pdf"
    if chunk_size < _MIN_CHUNK_SIZE or chunk_size > _MAX_CHUNK_SIZE:
        raise HTTPException(400, f"chunk_size 需在 [{_MIN_CHUNK_SIZE}, {_MAX_CHUNK_SIZE}] 内")
    if overlap < 0 or overlap * 2 > chunk_size:
        raise HTTPException(400, "overlap 需满足 0 ≤ overlap ≤ chunk_size/2")
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "本版本只支持 PDF（年报场景实测均为 PDF）")

    # 存到 data/raw/ 后入库，保持"原始语料可复查"
    blob = file.file.read(_MAX_PDF_BYTES + 1)
    if len(blob) > _MAX_PDF_BYTES:
        raise HTTPException(400, "PDF 超过 100MB 上限")

    settings.RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.RAW_DIR / name
    target.write_bytes(blob)

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
