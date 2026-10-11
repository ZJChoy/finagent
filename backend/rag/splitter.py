"""结构感知切分。

设计取舍（写进 README 的"关键实现"）：
- **按页切分、不跨页合并** —— 跨页合并会让一个块对应两个页码，引用就说不清了；
  代价是短页会产生小块，但换来「每个块 = 一个明确页码」。
- 块内目标 400–600 汉字，重叠 80–100 —— 尺寸服务的不是模型输入上限，而是**引用粒度 + 检索精度**。
"""
from __future__ import annotations

import re


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_page(
    text: str,
    chunk_size: int = 500,
    overlap: int = 90,
    min_len: int = 40,
) -> list[str]:
    """把一页的正文切成若干块（大小接近 chunk_size）。过短的内容（页眉残留等）直接丢弃。"""
    text = _clean(text)
    if len(text) < min_len:
        return []
    if len(text) <= chunk_size:
        return [text]

    # 先按段落聚合，尽量不切断语义
    paras = [p for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(p) >= chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            step = max(1, chunk_size - overlap)
            for i in range(0, len(p), step):
                chunks.append(p[i : i + chunk_size])
            continue
        if len(buf) + len(p) + 1 <= chunk_size:
            buf = f"{buf}\n{p}" if buf else p
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)

    # 给相邻块补重叠，缓解边界信息丢失
    if overlap > 0 and len(chunks) > 1:
        chunks = [
            c if i == 0 else chunks[i - 1][-overlap:] + c
            for i, c in enumerate(chunks)
        ]
    return [c for c in chunks if len(c) >= min_len]
