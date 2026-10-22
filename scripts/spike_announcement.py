"""Spike：验证「A 股公告正文」能不能拿到并解析出来。

**这只是一次可行性验证**，用来决定第二个项目（公告事件监控）立不立项。
只回答一个问题：**从公告列表 → 正文，这条路走不走得通。**

路径假设：
1. akshare 拿到公告列表（含巨潮 detail 链接）
2. 从链接里解析出 `announcementId` 与 `announcementTime`
3. 拼出巨潮的 PDF 地址 `static.cninfo.com.cn/finalpage/{date}/{id}.PDF`
4. 下载并用 **FinAgent 已有的解析能力**（pypdf + 版面处理）抽正文

    python scripts/spike_announcement.py
"""
from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import akshare as ak  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0"}


def _fetch(url: str, timeout: int = 30) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def main() -> None:
    print("=== 1. 取公告列表（巨潮口径）===")
    df = ak.stock_zh_a_disclosure_report_cninfo(
        symbol="600519", start_date="20260101", end_date="20261011"
    )
    print(f"  拿到 {len(df)} 条")
    if df.empty:
        print("  空列表，spike 中断")
        return
    row = df.iloc[0]
    url = row["公告链接"]
    print(f"  标题: {row['公告标题']}")
    print(f"  链接: {url}")

    print("\n=== 2. 解析 announcementId / 时间 ===")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    ann_id = (q.get("announcementId") or [""])[0]
    ann_time = (q.get("announcementTime") or [""])[0]
    print(f"  announcementId  : {ann_id!r}")
    print(f"  announcementTime: {ann_time!r}")
    if not ann_id:
        print("  ❌ 解析不到 announcementId，换解析规则")
        return

    print("\n=== 3. 拼 PDF 地址并下载 ===")
    date = ann_time[:10] if re.match(r"\d{4}-\d{2}-\d{2}", ann_time) else ann_time
    pdf_url = f"http://static.cninfo.com.cn/finalpage/{date}/{ann_id}.PDF"
    print(f"  {pdf_url}")
    try:
        blob = _fetch(pdf_url)
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ 下载失败：{type(e).__name__}: {e}")
        return
    print(f"  下载 {len(blob)} 字节 | 头部 {blob[:8]!r}")
    if not blob.startswith(b"%PDF"):
        print("  ❌ 不是 PDF（可能是 HTML 拦截页）")
        return
    print("  ✅ 是合法 PDF")

    print("\n=== 4. 用 FinAgent 已有能力解析正文 ===")
    out = Path("data/spike")
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = out / f"{ann_id}.pdf"
    pdf_path.write_bytes(blob)
    try:
        from backend.rag import loader
        res = loader.load_pdf(pdf_path)
        pages = [{"page": p.page_no, "text": p.text} for p in res.pages]
        extra = f"（识别到页眉页脚 {len(res.repeated_lines)} 条，空文本页 {res.empty_pages}）"
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ loader 失败（{type(e).__name__}: {e}），改用 pypdf 直接抽")
        from pypdf import PdfReader
        pages = [{"page": i + 1, "text": p.extract_text() or ""}
                 for i, p in enumerate(PdfReader(str(pdf_path)).pages)]
        extra = ""
    text = "\n".join(p["text"] for p in pages)
    print(f"  页数 {len(pages)} | 正文 {len(text)} 字 {extra}")
    print(f"  开头：{text[:200].strip()}"[:260])
    print("\n=== 结论 ===")
    print("  ✅ 公告正文拿得到 → 第二个项目（公告事件监控）**可立项**")


if __name__ == "__main__":
    main()
