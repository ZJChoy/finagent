"""入库脚本。

用法（在项目根目录执行）：

    python scripts/ingest.py --stats                    # 只看当前索引状态
    python scripts/ingest.py --dir <PDF目录> --limit 1  # 先入 1 份验证
    python scripts/ingest.py --dir <PDF目录>            # 入库目录下全部 PDF
    python scripts/ingest.py --reset                    # 清空索引（重来）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.rag import ingest, store  # noqa: E402


def show_stats() -> None:
    docs = store.list_documents()
    print(f"索引文件   : {store.VEC_PATH}")
    print(f"切块总数   : {store.count()}")
    print(f"向量模型   : {', '.join(store.embedding_models()) or '（空）'}")
    print(f"文档数     : {len(docs)}")
    for d in docs:
        print(
            f"  - {d['name']}: {d['points']} 块 / {d['pages']} 页 / "
            f"{d['chars']} 字 / {d['embedding_model']}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="FinAgent 入库脚本")
    ap.add_argument("--dir", default=str(settings.RAW_DIR), help="PDF 所在目录")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 份（0=全部）")
    ap.add_argument("--empty", action="store_true", help="入库前先清空索引")
    ap.add_argument("--reset", action="store_true", help="只清空索引后退出")
    ap.add_argument("--stats", action="store_true", help="只打印索引状态后退出")
    ap.add_argument("--chunk-size", type=int, default=500)
    ap.add_argument("--overlap", type=int, default=90)
    ap.add_argument("--batch", type=int, default=16, help="向量化批大小")
    args = ap.parse_args()

    if args.reset:
        store.reset()
        print("索引已清空。")
        return

    if args.stats:
        show_stats()
        return

    if args.empty:
        store.reset()
        print("索引已清空，开始重新入库。")

    pdf_dir = Path(args.dir)
    if not pdf_dir.is_dir():
        print(f"目录不存在：{pdf_dir}")
        sys.exit(1)
    files = sorted(p for p in pdf_dir.iterdir() if p.suffix.lower() == ".pdf")
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"{pdf_dir} 下没有 PDF。")
        return

    print(f"待入库 {len(files)} 份 · 向量模型 {settings.EMBED_MODEL}")
    for p in files:
        t0 = time.time()
        print(f"\n>> {p.name}")
        try:
            r = ingest.ingest_pdf(
                p, chunk_size=args.chunk_size, overlap=args.overlap, batch=args.batch
            )
            print(
                f"   {r['pages']} 页（空文本页 {r['empty_pages']}）→ {r['chunks']} 块 / "
                f"{r['chars']} 字 · 用时 {time.time() - t0:.1f}s"
            )
            if r["repeated_lines"]:
                print(f"   识别到的页眉页脚示例：{r['repeated_lines']}")
        except Exception as e:  # noqa: BLE001
            print(f"   失败：{type(e).__name__}: {e}")

    print("\n== 入库后状态 ==")
    show_stats()


if __name__ == "__main__":
    main()
