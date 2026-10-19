"""拉取公告 PDF（akshare 巨潮口径 → static.cninfo.com.cn）。

用法（DATA_DIR 决定落盘位置，多实例隔离见 README）：
    DATA_DIR=data/data-announce python -m apps.announcement.fetch
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

import akshare as ak

UA = {"User-Agent": "Mozilla/5.0"}


def _fetch(url: str, timeout: int = 30) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def fetch(symbol: str = "600519", start: str = "20260101",
          end: str = "20261231", limit: int = 12, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or (Path(__file__).resolve().parents[2] / "data" / "data-announce" / "raw_announce")
    out_dir.mkdir(parents=True, exist_ok=True)

    df = ak.stock_zh_a_disclosure_report_cninfo(symbol=symbol, start_date=start, end_date=end)
    print(f"公告列表：{len(df)} 条（{symbol} {start}-{end}）")
    saved: list[Path] = []
    for _, row in df.iterrows():
        if len(saved) >= limit:
            break
        url = row["公告链接"]
        m = re.search(r"announcementId=([0-9A-Za-z]+)", url)
        t = re.search(r"announcementTime=([0-9-]+)", url)
        if not m or not t:
            continue
        date = t.group(1)[:10]
        ann_id = m.group(1)
        pdf_url = f"http://static.cninfo.com.cn/finalpage/{date}/{ann_id}.PDF"
        try:
            blob = _fetch(pdf_url)
        except Exception as e:  # noqa: BLE001
            print(f"  跳过（下载失败 {type(e).__name__}）：{row['公告标题'][:30]}")
            continue
        if not blob.startswith(b"%PDF"):
            continue
        title = re.sub(r"[\\/:*?\"<>|]", "_", str(row["公告标题"]))[:60]
        path = out_dir / f"{date}_{ann_id[:8]}_{title}.pdf"
        path.write_bytes(blob)
        saved.append(path)
        print(f"  ✅ {path.name}（{len(blob) // 1024} KB）")
    print(f"共保存 {len(saved)} 份 → {out_dir}")
    return saved


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "600519"
    fetch(symbol=sym)
