"""索引体检与变体指纹登记（M1.1 配套运维脚本）。

用法：
    python scripts/index_doctor.py            # 打印 index_health + 建议
    python scripts/index_doctor.py --stamp    # 给与 base 行数一致的变体登记当前指纹
                                              #（用于存量 llmctx 索引升级到指纹机制）

背景：active_vector_path() 现在校验「变体行数 == 元数据行数 且 指纹一致」，
不满足就回退 base（宁可质量退化，不可静默错位）。存量部署的变体文件
没有指纹侧车（.fp），若确认它与当前语料同批构建（行数一致即可佐证），
用 --stamp 补登记指纹，检索即可恢复走变体。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import dbconn  # noqa: E402
from backend.config import settings  # noqa: E402
from finagent.core.appconfig import configure  # noqa: E402

configure(settings, DIALECT=dbconn.DIALECT, dbconn=dbconn)

from finagent.core.retriever import store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", action="store_true",
                    help="给行数与元数据一致的变体登记当前指纹")
    args = ap.parse_args()

    facts = store.index_health()
    print(f"元数据行数   : {facts['meta_rows']}")
    print(f"base 行数    : {facts['base_rows']}  （一致: {facts['consistent']}）")
    print(f"变体行数     : {facts['variant_rows'] or '无'}")
    print(f"当前生效     : {facts['active_variant']}")
    print(f"stale 变体   : {facts['stale_variants'] or '无'}")

    if not facts["consistent"]:
        print("\n⚠️ base 向量与元数据行数不一致——索引已损坏，请 reset 后重新入库。")
        sys.exit(2)

    if args.stamp:
        fp = store._base_fingerprint()
        for name in facts["stale_variants"]:
            fname = store.VARIANTS[name]
            path = store.INDEX_DIR / fname
            if not path.exists():
                continue
            import numpy as np
            rows = int(np.load(path, mmap_mode="r").shape[0])
            if rows != facts["meta_rows"]:
                print(f"  跳过 {fname}：行数 {rows} ≠ 元数据 {facts['meta_rows']}，必须重建")
                continue
            store._write_variant_fingerprint(fname, fp)
            print(f"  已登记指纹：{fname}")
        print("\n重新体检：")
        facts = store.index_health()
        print(f"  当前生效: {facts['active_variant']}｜stale: {facts['stale_variants'] or '无'}")


if __name__ == "__main__":
    main()
