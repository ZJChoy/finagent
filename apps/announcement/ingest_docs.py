"""公告语料入库：遍历 raw_announce/ 下的 PDF，走 core 检索管线入库。

    DATA_DIR=data/data-announce python -m apps.announcement.ingest_docs
"""
from __future__ import annotations

from pathlib import Path

from backend import dbconn, llm  # noqa: F401

# ⚠️ 顺序契约：appconfig.configure 必须先于 finagent.core.retriever 导入
#（store.py 有模块级常量依赖 DATA_DIR/DIALECT，见 finagent/core/appconfig.py）
# backend.llm 负责把 API Key 绑定到 core.llm（llmctx 索引的上下文标注需要）
from backend.config import settings
from finagent.core.appconfig import configure

configure(settings, DIALECT=dbconn.DIALECT, dbconn=dbconn)

from finagent.core.retriever import ingest  # noqa: E402

RAW_NAME = "raw_announce"


def ingest_all(data_dir: Path) -> list[dict]:
    raw = data_dir / RAW_NAME
    pdfs = sorted(raw.glob("*.pdf"))
    print(f"待入库：{len(pdfs)} 份公告 PDF")
    results = []
    for p in pdfs:
        try:
            r = ingest.ingest_pdf(p)
            results.append(r)
            print(f"  ✅ {r['doc']}：{r['pages']} 页 → {r['chunks']} 块")
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 跳过 {p.name}：{type(e).__name__}: {str(e)[:80]}")
    return results


if __name__ == "__main__":
    from backend.config import settings

    data_dir = Path(settings.DATA_DIR)
    ingest_all(data_dir)
