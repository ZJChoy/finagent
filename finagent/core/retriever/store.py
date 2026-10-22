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

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from finagent.core.appconfig import settings

INDEX_DIR: Path = settings.DATA_DIR / "index" / settings.DIALECT
VEC_PATH: Path = INDEX_DIR / "vectors.npy"
DIM = 1024

# 索引变体：同一批切块可以有多套向量（正文相同，只是**向量化时喂的文本不同**）。
#
# ⚠️ **只放"值得在生产选"的变体**。`ctx`（正文前拼规则式章节路径）实测 MRR 0.7216 < 基线 0.7679，
#    结论是"章节标题是高频低区分度文本，加进去反而稀释语义"—— 所以它**不进这张表**：
#    生产不该能选到一个已知更差的变体。实验脚本自己管路径，结论留在 `eval/retrieval_report.md`。
#
# - base  ：直接用切块正文
# - llmctx：正文前拼「chunk 专属说明」（LLM 生成，实测 MRR 0.7679 → 0.8925）
#
# **读索引走变体、写索引永远写 base** —— 否则切换变体时会把压缩后的向量写错文件。
VARIANTS: dict[str, str] = {
    "base": "vectors.npy",
    "llmctx": "vectors_llmctx.npy",
}


def _base_fingerprint() -> str:
    """base 索引的内容指纹：行数 + 所有 row_id。变体与 base 的指纹一致才算有效。

    只用行数会漏掉「行数巧合但内容错位」（如 reset 后重建，行数恰好相同），
    row_id 列表能区分"同一批切块"与"不同的切块"。
    """
    ids = [int(r["row_id"]) for r in settings.dbconn.query(
        "SELECT row_id FROM rag_chunks ORDER BY row_id")]
    return f"{len(ids)}:{','.join(map(str, ids))}"


def _fingerprint_path(variant_fname: str) -> Path:
    return INDEX_DIR / (variant_fname + ".fp")


def _read_variant_fingerprint(variant_fname: str) -> str | None:
    p = _fingerprint_path(variant_fname)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("fingerprint")
    except Exception:  # noqa: BLE001
        return None


def _write_variant_fingerprint(variant_fname: str, fingerprint: str) -> None:
    _fingerprint_path(variant_fname).write_text(
        json.dumps({"fingerprint": fingerprint}, ensure_ascii=False), encoding="utf-8")


def _variant_matches_base(variant_fname: str) -> bool:
    """变体文件存在、行数与元数据一致、且指纹与当前 base 相同 → 有效。"""
    p = INDEX_DIR / variant_fname
    if not p.exists():
        return False
    meta_rows = count()
    try:
        if np.load(p, mmap_mode="r").shape[0] != meta_rows:
            return False
    except Exception:  # noqa: BLE001
        return False
    return _read_variant_fingerprint(variant_fname) == _base_fingerprint()


def active_vector_path() -> Path:
    """当前生效的向量文件；对应变体不存在、行数不符或**指纹过期**时退回 base。

    变体失效不自动重建（重建要逐块调 LLM，成本高、失败面大），而是回退 base
    并在 index_health() 里暴露 stale 状态——检索质量可以退化，
    向量与文本错位（静默答错）绝不能发生。
    """
    name = VARIANTS.get(settings.RETRIEVAL_INDEX, VARIANTS["base"])
    if name == VARIANTS["base"]:
        return VEC_PATH
    if _variant_matches_base(name):
        return INDEX_DIR / name
    return VEC_PATH


def index_health() -> dict:
    """索引一致性事实，供 /health 与启动自检使用。"""
    meta_rows = count()
    base_rows = 0
    if VEC_PATH.exists():
        try:
            base_rows = int(np.load(VEC_PATH, mmap_mode="r").shape[0])
        except Exception:  # noqa: BLE001
            base_rows = -1
    stale: list[str] = []
    variants: dict[str, int] = {}
    for name, fname in VARIANTS.items():
        if name == "base":
            continue
        p = INDEX_DIR / fname
        if not p.exists():
            continue
        try:
            rows = int(np.load(p, mmap_mode="r").shape[0])
        except Exception:  # noqa: BLE001
            rows = -1
        variants[name] = rows
        if not _variant_matches_base(fname):
            stale.append(name)
    return {
        "meta_rows": meta_rows,
        "base_rows": base_rows,
        "consistent": meta_rows == base_rows,
        "active_variant": "base" if active_vector_path() == VEC_PATH
        else settings.RETRIEVAL_INDEX,
        "variant_rows": variants,
        "stale_variants": stale,
    }

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS rag_documents (
    name            {settings.dbconn.KEY_STR} PRIMARY KEY,
    points          {settings.dbconn.INT} NOT NULL,
    pages           {settings.dbconn.INT} NOT NULL,
    chars           {settings.dbconn.INT} NOT NULL,
    embedding_model {settings.dbconn.KEY_STR} NOT NULL,
    created_at      {settings.dbconn.REAL} NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    row_id          {settings.dbconn.INT} PRIMARY KEY,
    doc             {settings.dbconn.KEY_STR} NOT NULL,
    page            {settings.dbconn.INT} NOT NULL,
    seq             {settings.dbconn.INT} NOT NULL,
    text            {settings.dbconn.TEXT} NOT NULL,
    embedding_model {settings.dbconn.KEY_STR} NOT NULL
);
"""


def init() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    settings.dbconn.executescript(_SCHEMA)
    settings.dbconn.create_index("idx_chunks_doc", "rag_chunks", "doc")


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

def _assert_embedding_model_compatible(model: str) -> None:
    """注释里承诺的"检索前校验"落地：模型混用 = 向量空间不同 = 静默错乱，
    直接 fail-closed 要求重建索引。"""
    existing = embedding_models()
    if existing and not all(m == model for m in existing):
        raise ValueError(
            f"embedding 模型混用：库中已有 {existing}，本次为 {model}。"
            "换模型必须重建索引（先 reset 再重新入库），否则检索结果静默错乱"
        )


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
    _assert_embedding_model_compatible(embedding_model)
    delete_document(name)
    if not chunks:
        return 0

    arr = _load_vectors()
    start = arr.shape[0]
    _save_vectors(np.vstack([arr, np.asarray(embeddings, dtype=np.float32)]))

    settings.dbconn.executemany(
        "INSERT INTO rag_chunks (row_id, doc, page, seq, text, embedding_model) VALUES (?,?,?,?,?,?)",
        [
            (start + i, name, c["page"], c["seq"], c["text"], embedding_model)
            for i, c in enumerate(chunks)
        ],
    )
    # 不用 INSERT OR REPLACE（SQLite 专有）：先删再插，两边都认
    settings.dbconn.execute("DELETE FROM rag_documents WHERE name=?", (name,))
    settings.dbconn.execute(
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
        for r in settings.dbconn.query("SELECT row_id FROM rag_chunks WHERE doc != ? ORDER BY row_id", (name,))
    ]
    settings.dbconn.execute("DELETE FROM rag_chunks WHERE doc=?", (name,))
    settings.dbconn.execute("DELETE FROM rag_documents WHERE name=?", (name,))

    arr = _load_vectors()
    if arr.size == 0:
        # 空索引护栏：全新实例（向量文件尚不存在）删除文档时，metadata 与向量
        # 天然一致（都是空），直接落空数组返回——否则 arr[keep_ids] 越界崩溃
        _save_vectors(np.zeros((0, DIM), dtype=np.float32))
        return
    new_arr = arr[keep_ids] if keep_ids else np.zeros((0, DIM), dtype=np.float32)
    _save_vectors(new_arr)

    if keep_ids:
        rows = settings.dbconn.query(
            "SELECT doc, page, seq, text, embedding_model FROM rag_chunks ORDER BY row_id"
        )
        settings.dbconn.execute("DELETE FROM rag_chunks")
        settings.dbconn.executemany(
            "INSERT INTO rag_chunks (row_id, doc, page, seq, text, embedding_model) VALUES (?,?,?,?,?,?)",
            [
                (i, r["doc"], r["page"], r["seq"], r["text"], r["embedding_model"])
                for i, r in enumerate(rows)
            ],
        )


def reset() -> None:
    init()
    settings.dbconn.execute("DELETE FROM rag_chunks")
    settings.dbconn.execute("DELETE FROM rag_documents")
    _save_vectors(np.zeros((0, DIM), dtype=np.float32))


# ---------------------------------------------------------------- 读取

def count() -> int:
    init()
    row = settings.dbconn.query_one("SELECT COUNT(*) AS c FROM rag_chunks")
    return int(row["c"]) if row else 0


def list_documents() -> list[dict[str, Any]]:
    init()
    return settings.dbconn.query(
        "SELECT name, points, pages, chars, embedding_model, created_at FROM rag_documents ORDER BY created_at"
    )


def embedding_models() -> list[str]:
    init()
    return [r["embedding_model"] for r in settings.dbconn.query(
        "SELECT DISTINCT embedding_model FROM rag_chunks"
    )]


def load_vectors(path: Path | None = None) -> np.ndarray:
    """读取向量文件。`path` 为 None 时用默认索引。"""
    target = path or VEC_PATH
    if target.exists():
        arr = np.load(target)
        if arr.size:
            return arr.astype(np.float32)
    return np.zeros((0, DIM), dtype=np.float32)


def search(
    query_vec: list[float],
    top_k: int = 5,
    doc: str | None = None,
    vectors: np.ndarray | None = None,
) -> list[dict]:
    """向量召回：先按元数据筛选，再对子集做点积。返回带 score 的命中列表。

    `vectors` 可注入另一套向量（用于对比「原始索引 vs 章节上下文索引」这类实验）；
    不传则用**当前生效的索引变体**（`RETRIEVAL_INDEX`，见 `active_vector_path()`）。
    """
    arr = vectors if vectors is not None else load_vectors(active_vector_path())
    if arr.size == 0:
        return []

    sql = "SELECT row_id, doc, page, seq, text FROM rag_chunks"
    args: tuple = ()
    if doc:
        sql += " WHERE doc=?"
        args = (doc,)
    sql += " ORDER BY row_id"
    rows = settings.dbconn.query(sql, args)
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
