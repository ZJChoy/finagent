"""本地向量存储：numpy 存向量 + 关系库存元数据（SQLite / MySQL 双后端）。

选型理由（会写进 README）：
- 约三千块的规模下，暴力点积 < 5ms，与向量数据库**没有实质差别**；
- 换来省掉 chromadb 带来的 onnxruntime / kubernetes / grpcio 等数百 MB 依赖。
- 代价：不宣称"用了向量数据库"，表述为「自建向量索引 + 元数据过滤」。

**注意分工**：向量仍然落本地 `.npy` 文件（那是索引，不是关系数据）；
关系库（`dbconn`，SQLite 或 MySQL）只存**切块元数据**。

**为什么向量文件按后端分目录**：向量在文件里、元数据在库里，两者必须成对。
如果共用一个 `.npy`，切后端就会出现"向量有、元数据没有"的不一致状态。
所以路径带上 dialect（`data/index/{sqlite|mysql}/vectors.npy`）——
**代价是换后端要重建索引（重新入库）**，这是刻意的取舍。

一致性：每条切块都记 `embedding_model`；检索前校验，不一致就要求重建
（换 embedding 模型 = 换向量空间，新旧向量混用会**静默出错**）。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from .. import dbconn
from ..config import settings

INDEX_DIR: Path = settings.DATA_DIR / "index" / dbconn.DIALECT
VEC_PATH: Path = INDEX_DIR / "vectors.npy"
DIM = 1024

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS rag_documents (
    name            {dbconn.KEY_STR} PRIMARY KEY,
    points          {dbconn.INT} NOT NULL,
    pages           {dbconn.INT} NOT NULL,
    chars           {dbconn.INT} NOT NULL,
    embedding_model {dbconn.KEY_STR} NOT NULL,
    created_at      {dbconn.REAL} NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    row_id          {dbconn.INT} PRIMARY KEY,
    doc             {dbconn.KEY_STR} NOT NULL,
    page            {dbconn.INT} NOT NULL,
    seq             {dbconn.INT} NOT NULL,
    text            {dbconn.TEXT} NOT NULL,
    embedding_model {dbconn.KEY_STR} NOT NULL
);
"""


def init() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    dbconn.executescript(_SCHEMA)
    dbconn.create_index("idx_chunks_doc", "rag_chunks", "doc")


# ---------------------------------------------------------------- 向量文件

def _load_vectors() -> np.ndarray:
    if VEC_PATH.exists():
        arr = np.load(VEC_PATH)
        if arr.size:
            return arr.astype(np.float32)
    return np.zeros((0, DIM), dtype=np.float32)


def _save_vectors(arr: np.ndarray) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(VEC_PATH, arr.astype(np.float32))


# ---------------------------------------------------------------- 写入

def add_document(
    name: str,
    chunks: list[dict],
    embeddings: list[list[float]],
    embedding_model: str,
) -> int:
    """写入一份文档。同名文档会先删除，保证可重复入库。

    chunks: [{"page": int, "seq": int, "text": str}]
    """
    init()
    delete_document(name)
    if not chunks:
        return 0

    arr = _load_vectors()
    start = arr.shape[0]
    _save_vectors(np.vstack([arr, np.asarray(embeddings, dtype=np.float32)]))

    dbconn.executemany(
        "INSERT INTO rag_chunks (row_id, doc, page, seq, text, embedding_model) VALUES (?,?,?,?,?,?)",
        [
            (start + i, name, c["page"], c["seq"], c["text"], embedding_model)
            for i, c in enumerate(chunks)
        ],
    )
    # 不用 INSERT OR REPLACE（SQLite 专有）：先删再插，两边都认
    dbconn.execute("DELETE FROM rag_documents WHERE name=?", (name,))
    dbconn.execute(
        "INSERT INTO rag_documents (name, points, pages, chars, embedding_model, created_at) VALUES (?,?,?,?,?,?)",
        (
            name,
            len(chunks),
            max((c["page"] for c in chunks), default=0),
            sum(len(c["text"]) for c in chunks),
            embedding_model,
            time.time(),
        ),
    )
    return len(chunks)


def delete_document(name: str) -> None:
    """删除一份文档并压缩向量数组（row_id 需保持连续，等于数组下标）。"""
    init()
    keep_ids = [
        int(r["row_id"])
        for r in dbconn.query("SELECT row_id FROM rag_chunks WHERE doc != ? ORDER BY row_id", (name,))
    ]
    dbconn.execute("DELETE FROM rag_chunks WHERE doc=?", (name,))
    dbconn.execute("DELETE FROM rag_documents WHERE name=?", (name,))

    arr = _load_vectors()
    new_arr = arr[keep_ids] if keep_ids else np.zeros((0, DIM), dtype=np.float32)
    _save_vectors(new_arr)

    if keep_ids:
        rows = dbconn.query(
            "SELECT doc, page, seq, text, embedding_model FROM rag_chunks ORDER BY row_id"
        )
        dbconn.execute("DELETE FROM rag_chunks")
        dbconn.executemany(
            "INSERT INTO rag_chunks (row_id, doc, page, seq, text, embedding_model) VALUES (?,?,?,?,?,?)",
            [
                (i, r["doc"], r["page"], r["seq"], r["text"], r["embedding_model"])
                for i, r in enumerate(rows)
            ],
        )


def reset() -> None:
    init()
    dbconn.execute("DELETE FROM rag_chunks")
    dbconn.execute("DELETE FROM rag_documents")
    _save_vectors(np.zeros((0, DIM), dtype=np.float32))


# ---------------------------------------------------------------- 读取

def count() -> int:
    init()
    row = dbconn.query_one("SELECT COUNT(*) AS c FROM rag_chunks")
    return int(row["c"]) if row else 0


def list_documents() -> list[dict[str, Any]]:
    init()
    return dbconn.query(
        "SELECT name, points, pages, chars, embedding_model, created_at FROM rag_documents ORDER BY created_at"
    )


def embedding_models() -> list[str]:
    init()
    return [r["embedding_model"] for r in dbconn.query(
        "SELECT DISTINCT embedding_model FROM rag_chunks"
    )]


def search(query_vec: list[float], top_k: int = 5, doc: str | None = None) -> list[dict]:
    """向量召回：先按元数据筛选，再对子集做点积。返回带 score 的命中列表。"""
    arr = _load_vectors()
    if arr.size == 0:
        return []

    sql = "SELECT row_id, doc, page, seq, text FROM rag_chunks"
    args: tuple = ()
    if doc:
        sql += " WHERE doc=?"
        args = (doc,)
    sql += " ORDER BY row_id"
    rows = dbconn.query(sql, args)
    if not rows:
        return []

    ids = [int(r["row_id"]) for r in rows]
    sub = arr[ids]
    q = np.asarray(query_vec, dtype=np.float32)
    sims = (sub @ q) / (np.linalg.norm(sub, axis=1) * np.linalg.norm(q) + 1e-9)
    order = np.argsort(-sims)[:top_k]
    return [
        {
            "doc": rows[i]["doc"],
            "page": rows[i]["page"],
            "seq": rows[i]["seq"],
            "text": rows[i]["text"],
            "score": round(float(sims[i]), 4),
        }
        for i in order
    ]
