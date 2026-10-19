"""数据库抽象层：SQLite / MySQL 双后端。

## 三条纪律（防止"双后端"变乱）

1. **业务 SQL 只用两边都认的公共子集**
   —— 不用 `ON CONFLICT ... DO UPDATE`、`INSERT OR REPLACE` 这类方言写法。
   需要 upsert 就写成「先查有没有，再 UPDATE 或 INSERT」（见 `db.set_config`）。
2. **建表语句统一用这里的类型别名拼**（`PK` / `KEY_STR` / `INT` / `REAL` / `TEXT`）
   —— 方言只在**本文件**出现，业务模块不写 `if backend == ...`。
3. **两个后端各跑一遍回归** —— 防"SQLite 上过了、MySQL 上挂"。

## 怎么选后端

`DB_BACKEND` 环境变量（默认 `sqlite`）：
- `sqlite`：数据落 `data/app.db`，**零部署**（别人 clone 下来不配任何东西就能跑）
- `mysql`：数据落 MySQL，需要连接参数（见 `.env.example`）

> 约定：占位符统一写 `?`，本层负责在 MySQL 下转成 `%s`。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

DIALECT = "mysql" if (settings.DB_BACKEND or "sqlite").lower() == "mysql" else "sqlite"
IS_MYSQL = DIALECT == "mysql"

# ---------------- 类型别名：业务模块的建表语句用它们拼，不写死方言 ----------------
PK = "INT AUTO_INCREMENT PRIMARY KEY" if IS_MYSQL else "INTEGER PRIMARY KEY AUTOINCREMENT"
KEY_STR = "VARCHAR(191)" if IS_MYSQL else "TEXT"   # 191：utf8mb4 下做主键/索引安全
INT = "INT" if IS_MYSQL else "INTEGER"
REAL = "DOUBLE" if IS_MYSQL else "REAL"
TEXT = "TEXT"


def _sql(sql: str) -> str:
    return sql.replace("?", "%s") if IS_MYSQL else sql


@contextmanager
def connect() -> Iterator[Any]:
    """每次操作开一个短连接。

    说明：MySQL 下"每操作一次连一次"在单机小流量场景足够，且避免线程池下的连接复用问题；
    真上量应换成连接池 —— 这是本项目的已知取舍。
    """
    if IS_MYSQL:
        import pymysql
        from pymysql.cursors import DictCursor

        conn = pymysql.connect(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD,
            database=settings.MYSQL_DATABASE,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
    else:
        conn = sqlite3.connect(settings.DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(_sql(sql), params)
        return [dict(row) for row in cur.fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    """返回受影响行数（rowcount）。"""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(_sql(sql), params)
        return cur.rowcount


def executemany(sql: str, rows: list[tuple]) -> None:
    if not rows:
        return
    with connect() as conn:
        cur = conn.cursor()
        cur.executemany(_sql(sql), rows)


def insert(sql: str, params: tuple = ()) -> int:
    """插入并返回新行 id（sqlite3 与 pymysql 的 `cursor.lastrowid` 都支持）。"""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(_sql(sql), params)
        return int(cur.lastrowid or 0)


def executescript(ddl: str) -> None:
    """执行一段建表脚本（多条语句用 `;` 分隔）。"""
    with connect() as conn:
        if IS_MYSQL:
            cur = conn.cursor()
            for stmt in (s.strip() for s in ddl.split(";")):
                if stmt:
                    cur.execute(stmt)
        else:
            conn.executescript(ddl)


def create_index(name: str, table: str, columns: str) -> None:
    """建索引；已存在则忽略（两后端报错码不同，统一吞掉，反正不影响功能）。"""
    try:
        with connect() as conn:
            conn.cursor().execute(f"CREATE INDEX {name} ON {table}({columns})")
    except Exception:  # noqa: BLE001
        pass


def ping() -> dict:
    """连通性自检，供 /health 用。"""
    try:
        query_one("SELECT 1 AS ok")
        return {"ok": True, "dialect": DIALECT}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "dialect": DIALECT, "error": f"{type(e).__name__}: {e}"}
