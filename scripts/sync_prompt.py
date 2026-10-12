"""把 `config.py` 里的 `DEFAULT_SYSTEM_PROMPT` 同步到数据库的 `app_config` 表。

## 为什么需要这个脚本

系统提示词的流向是**单向**的：

```
首次启动：config.py 的 DEFAULT_SYSTEM_PROMPT ──写入──▶ app_config.system_prompt
之后每次：app_config.system_prompt ──读取──▶ 本轮对话
```

也就是说 **`app_config` 里的那一份才是"活的"**。改了 `config.py` 之后，
已有部署（数据库里已经有行）**不会自动跟着变** —— 这是刻意的（用户改了提示词不该被代码覆盖），
但**调试时常踩**：明明改了代码，行为却没变。

两种正规改法：
1. 在**配置页**手改（推荐给使用者）；
2. 跑本脚本（推荐给"我改了代码默认值、要把已有库对齐"的场景）。

    python scripts/sync_prompt.py --diff    # 只看两边差多少，不写
    python scripts/sync_prompt.py           # 写入
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402
from backend.config import DEFAULT_SYSTEM_PROMPT  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", action="store_true", help="只对比，不写入")
    args = ap.parse_args()

    current = (db.get_config() or {}).get("system_prompt") or ""
    target = DEFAULT_SYSTEM_PROMPT

    print(f"库里当前提示词：{len(current)} 字")
    print(f"代码里的提示词：{len(target)} 字")
    print(f"是否一致：{current.strip() == target.strip()}")
    if current.strip() == target.strip():
        return
    if args.diff:
        print("\n（--diff 模式，未写入）")
        return

    db.set_config(system_prompt=target)
    after = (db.get_config() or {}).get("system_prompt") or ""
    print(f"\n已写入。写入后校验：{'✅ 一致' if after.strip() == target.strip() else '❌ 仍不一致'}")


if __name__ == "__main__":
    main()
