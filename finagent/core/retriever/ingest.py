"""入库管线：PDF → 解析 → 版面处理 → 切分 → 云端向量化 → 本地索引。"""
from __future__ import annotations

from pathlib import Path

from finagent.core.appconfig import settings

from . import embedder, loader, splitter, store


class ScannedPDFError(RuntimeError):
    """扫描件（无文字层）本版本不支持，明确报错而不静默降级。"""


def ingest_pdf(
    path: str | Path,
    chunk_size: int = 500,
    overlap: int = 90,
    batch: int = 16,
) -> dict:
    path = Path(path)
    name = path.name

    res = loader.load_pdf(path)
    if res.total_pages and res.empty_ratio > 0.5:
        raise ScannedPDFError(
            f"{name}：空文本页占比 {res.empty_ratio:.0%}，疑似扫描件；"
            f"本版本不支持 OCR，请换文字型 PDF。"
        )

    chunks: list[dict] = []
    for page in res.pages:
        for seq, text in enumerate(splitter.split_page(page.text, chunk_size, overlap)):
            chunks.append({"page": page.page_no, "seq": seq, "text": text})
    if not chunks:
        raise ValueError(f"{name}：未切出任何内容（可能是空文档或解析失败）")

    vectors = embedder.embed_texts([c["text"] for c in chunks], batch=batch)
    store.add_document(name, chunks, vectors, settings.EMBED_MODEL)

    return {
        "doc": name,
        "pages": res.total_pages,
        "empty_pages": res.empty_pages,
        "chunks": len(chunks),
        "chars": sum(len(c["text"]) for c in chunks),
        "embedding_model": settings.EMBED_MODEL,
        "repeated_lines": res.repeated_lines,
    }
