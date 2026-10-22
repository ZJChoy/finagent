"""PDF 加载与版面处理。

把年报 PDF 变成「按页的正文段落」，并处理两类版面问题：

1. **页眉 / 页脚**是噪声，但要清理得当：
   - 只在每页**首尾两行**里找重复行（页眉页脚一定在边缘）——避免误删正文里的
     `单位：元 币种：人民币`、`√适用 □不适用` 这类高频正文行；
   - 页脚里的**页码是引用锚点**，按模式识别后抽成 metadata 并从正文移除。
2. **扫描件**（空文本页占比过高）明确报错，不静默降级成"检索不到"。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

_EMPTY_PAGE_CHARS = 20      # 整页少于这么多字符 → 视作空文本页
_REPEAT_RATIO = 0.4         # 边缘行出现在这么多比例的页里 → 判为页眉/页脚
_MIN_PAGES_FOR_REPEAT = 8
_MAX_HEADER_LEN = 80        # 过长的行不像页眉页脚，不参与判定

# 页码模式（整行匹配，避免误抓正文里的 "3/5 权益法" 之类）
_PAGE_PATTERNS = [
    re.compile(r"第\s*(\d{1,4})\s*页"),
    re.compile(r"(\d{1,4})\s*/\s*(\d{1,4})"),
    re.compile(r"[-—–]\s*(\d{1,4})\s*[-—–]?"),   # 形如 "- 59 -"，必须带破折号，避免误抓表格里的孤立数字
]


@dataclass
class Page:
    page_no: int                 # PDF 页码，从 1 开始（引用定位用这个最稳）
    printed_page: int | None     # 页脚里印的页码（若识别到）
    text: str


@dataclass
class LoadResult:
    pages: list[Page] = field(default_factory=list)
    total_pages: int = 0
    empty_pages: int = 0
    repeated_lines: list[str] = field(default_factory=list)

    @property
    def empty_ratio(self) -> float:
        return (self.empty_pages / self.total_pages) if self.total_pages else 0.0


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _match_page_number(line: str) -> int | None:
    s = line.strip()
    m = _PAGE_PATTERNS[0].search(s)
    if m:
        return int(m.group(1))
    m = _PAGE_PATTERNS[1].fullmatch(s)
    if m:
        return int(m.group(1))
    m = _PAGE_PATTERNS[2].fullmatch(s)
    if m:
        return int(m.group(1))
    return None


def _find_repeated_lines(raw_pages: list[list[str]]) -> set[str]:
    """只在每页首尾两行里找跨页高频行 —— 基本就是页眉页脚。"""
    if len(raw_pages) < _MIN_PAGES_FOR_REPEAT:
        return set()
    counter: Counter[str] = Counter()
    for lines in raw_pages:
        edge = set(lines[:2] + lines[-2:]) if len(lines) > 4 else set(lines)
        for line in edge:
            if len(line) <= _MAX_HEADER_LEN:
                counter[line] += 1
    threshold = max(3, int(len(raw_pages) * _REPEAT_RATIO))
    return {line for line, n in counter.items() if n >= threshold}


def load_pdf(path: str | Path) -> LoadResult:
    reader = PdfReader(str(path))
    raw_pages: list[list[str]] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        raw_pages.append([ln for ln in (_norm(x) for x in text.split("\n")) if ln])

    result = LoadResult(total_pages=len(raw_pages))
    result.empty_pages = sum(
        1 for lines in raw_pages if len("".join(lines)) < _EMPTY_PAGE_CHARS
    )
    repeated = _find_repeated_lines(raw_pages)
    result.repeated_lines = sorted(repeated, key=len)[:5]

    for i, lines in enumerate(raw_pages, start=1):
        body = list(lines)

        # 1) 移除页眉/页脚
        body = [ln for ln in body if ln not in repeated]

        # 2) 从首尾找页码行 → 抽成 metadata 并从正文移除
        #    （实测：茅台的页码在页首 "60 / 143"，美的在页首 "- 59 -"）
        printed = None
        candidates: list[str] = []
        for line in body[:3] + body[-3:]:
            if line not in candidates:
                candidates.append(line)
        for line in candidates:
            n = _match_page_number(line)
            if n is not None:
                printed = n
                body = [ln for ln in body if ln != line]
                break

        # 3) 兜底：有些年报的页码是页首一个光秃数字（如美的的 "59"）。
        #    只有它和 PDF 页序接近时才认，避免把表格里的孤立数字当页码。
        if printed is None and body:
            first = body[0].strip()
            if re.fullmatch(r"\d{1,4}", first):
                n = int(first)
                if abs(n - i) <= 5:
                    printed = n
                    body = body[1:]

        result.pages.append(
            Page(page_no=i, printed_page=printed, text="\n".join(body))
        )
    return result
