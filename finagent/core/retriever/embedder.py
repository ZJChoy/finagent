"""云端向量化：走 OpenAI 兼容的 /embeddings 接口（默认 text-embedding-v4，1024 维）。

批量策略：默认 16 条一批；出错自动**减半重试**（入库脚本里可调 batch）。
"""
from __future__ import annotations

from finagent.core.appconfig import settings

from ..llm import client

DEFAULT_BATCH = 16


def embed_texts(
    texts: list[str],
    model: str | None = None,
    batch: int = DEFAULT_BATCH,
) -> list[list[float]]:
    """把一批文本转成向量，返回顺序与输入一致。"""
    if not texts:
        return []
    model = model or settings.EMBED_MODEL
    out: list[list[float]] = []
    i = 0
    size = max(1, batch)
    while i < len(texts):
        piece = texts[i : i + size]
        try:
            resp = client().embeddings.create(model=model, input=piece)
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
            i += len(piece)
        except Exception:
            if size <= 1:
                raise
            size = max(1, size // 2)  # 遇错减半重试
    return out


def embed_query(text: str, model: str | None = None) -> list[float]:
    return embed_texts([text], model=model)[0]


def dimension(model: str | None = None) -> int:
    """探测向量维度（用于校验索引一致性）。"""
    return len(embed_query("维度探测", model=model))
