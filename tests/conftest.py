"""测试环境隔离：任何测试都不允许碰到真实 data/ 与 MySQL。

必须在项目包被 import 之前设置环境变量，所以放在 conftest 顶部。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="finagent-test-")
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ["DATA_DIR"] = _TMP          # 向量索引与 sqlite 都落临时目录
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["FIN_CACHE_ENABLED"] = "0"
