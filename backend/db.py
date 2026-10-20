"""SQLite / MySQL 双后端的数据层：会话 / 消息 / 长期记忆 / 应用配置。

所有 SQL 都走 `dbconn`，并且**只用两种数据库都认的写法**：
- 不用 `ON CONFLICT ... DO UPDATE`、`INSERT OR REPLACE`（SQLite 专有）；
- 需要 upsert 的地方写成「先查有没有，再 UPDATE / INSERT」；
- 建表语句用 `dbconn` 的类型别名拼，不写死方言。

四层记忆（定稿 §6.1）里，这里负责持久化的三层：
- **L2 会话记忆** —— `conversations` + `messages`，超预算时把最老一半压成 `summary`；
- **L4 长期记忆** —— `memories`（偏好 / 常用公司 / 显示口径），下次注入 system prompt；
- L3 知识记忆（文档切块与向量）由 `rag/store.py` 管，不要和这里混谈。
"""
from __future__ import annotations

import json
import time

from . import dbconn
from .config import DEFAULT_SYSTEM_PROMPT, settings

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS conversations (
    id           {dbconn.PK},
    title        {dbconn.KEY_STR} NOT NULL,
    summary      {dbconn.TEXT} NOT NULL,
    summary_upto {dbconn.INT} NOT NULL DEFAULT 0,
    created_at   {dbconn.REAL} NOT NULL,
    updated_at   {dbconn.REAL} NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          {dbconn.PK},
    conv_id     {dbconn.INT} NOT NULL,
    role        {dbconn.KEY_STR} NOT NULL,
    content     {dbconn.TEXT} NOT NULL,
    citations   {dbconn.TEXT} NOT NULL,
    tool_calls  {dbconn.TEXT} NOT NULL,
    tokens      {dbconn.INT} NOT NULL DEFAULT 0,
    latency_ms  {dbconn.INT} NOT NULL DEFAULT 0,
    created_at  {dbconn.REAL} NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id             {dbconn.PK},
    kind           {dbconn.KEY_STR} NOT NULL,
    content        {dbconn.TEXT} NOT NULL,
    source_conv_id {dbconn.INT},
    created_at     {dbconn.REAL} NOT NULL
);

CREATE TABLE IF NOT EXISTS app_config (
    id                   {dbconn.INT} PRIMARY KEY,
    system_prompt        {dbconn.TEXT},
    model                {dbconn.KEY_STR},
    tools_enabled        {dbconn.TEXT} NOT NULL,
    memory_enabled       {dbconn.INT} NOT NULL DEFAULT 1,
    memory_keep_recent   {dbconn.INT} NOT NULL DEFAULT 6,
    memory_summarize_at  {dbconn.INT} NOT NULL DEFAULT 12,
    memory_extract_every {dbconn.INT} NOT NULL DEFAULT 3,
    updated_at           {dbconn.REAL} NOT NULL
);
"""


def init() -> None:
    dbconn.executescript(_SCHEMA)
    dbconn.create_index("idx_msg_conv", "messages", "conv_id")


# ---------------------------------------------------------------- 会话

def create_conversation(title: str = "新对话") -> int:
    init()
    now = time.time()
    return dbconn.insert(
        "INSERT INTO conversations (title, summary, summary_upto, created_at, updated_at) "
        "VALUES (?,?,?,?,?)",
        ((title or "新对话")[:30], "", 0, now, now),
    )


def list_conversations() -> list[dict]:
    init()
    return dbconn.query(
        "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC"
    )


def conversation_exists(cid: int) -> bool:
    init()
    return dbconn.query_one("SELECT id FROM conversations WHERE id=?", (cid,)) is not None


def touch_conversation(cid: int) -> None:
    dbconn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))


def delete_conversation(cid: int) -> None:
    init()
    dbconn.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
    dbconn.execute("DELETE FROM conversations WHERE id=?", (cid,))


# ---------------------------------------------------------------- 消息

def add_message(
    conv_id: int,
    role: str,
    content: str,
    citations: list | None = None,
    tool_calls: list | None = None,
    tokens: int = 0,
    latency_ms: int = 0,
) -> None:
    init()
    dbconn.execute(
        "INSERT INTO messages (conv_id, role, content, citations, tool_calls, tokens, latency_ms, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            conv_id,
            role,
            content or "",
            json.dumps(citations or [], ensure_ascii=False),
            json.dumps(tool_calls or [], ensure_ascii=False),
            int(tokens or 0),
            int(latency_ms or 0),
            time.time(),
        ),
    )
    touch_conversation(conv_id)


def list_messages(conv_id: int, after_id: int = 0) -> list[dict]:
    init()
    rows = dbconn.query(
        "SELECT id, conv_id, role, content, citations, tool_calls, tokens, latency_ms, created_at "
        "FROM messages WHERE conv_id=? AND id>? ORDER BY id",
        (conv_id, after_id),
    )
    for r in rows:
        try:
            r["citations"] = json.loads(r["citations"] or "[]")
        except (json.JSONDecodeError, TypeError):
            r["citations"] = []
        try:
            r["tool_calls"] = json.loads(r["tool_calls"] or "[]")
        except (json.JSONDecodeError, TypeError):
            r["tool_calls"] = []
    return rows


def count_user_messages(conv_id: int) -> int:
    init()
    row = dbconn.query_one(
        "SELECT COUNT(*) AS c FROM messages WHERE conv_id=? AND role='user'", (conv_id,)
    )
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------- 摘要（L2）

def get_summary(conv_id: int) -> tuple[str, int]:
    init()
    row = dbconn.query_one("SELECT summary, summary_upto FROM conversations WHERE id=?", (conv_id,))
    if row is None:
        return "", 0
    return row["summary"] or "", int(row["summary_upto"] or 0)


def set_summary(conv_id: int, summary: str, upto_id: int) -> None:
    init()
    dbconn.execute(
        "UPDATE conversations SET summary=?, summary_upto=? WHERE id=?", (summary, upto_id, conv_id)
    )


# ---------------------------------------------------------------- 长期记忆（L4）

def list_memories() -> list[dict]:
    init()
    return dbconn.query(
        "SELECT id, kind, content, source_conv_id, created_at FROM memories ORDER BY id DESC"
    )


def add_memory(kind: str, content: str, source_conv_id: int | None = None) -> None:
    """同一 kind 下内容重复就不重复写入。"""
    init()
    content = (content or "").strip()
    if not content:
        return
    if dbconn.query_one("SELECT id FROM memories WHERE kind=? AND content=?", (kind, content)):
        return
    dbconn.execute(
        "INSERT INTO memories (kind, content, source_conv_id, created_at) VALUES (?,?,?,?)",
        (kind, content, source_conv_id, time.time()),
    )


def delete_memory(mid: int) -> None:
    init()
    dbconn.execute("DELETE FROM memories WHERE id=?", (mid,))


def clear_memories() -> None:
    init()
    dbconn.execute("DELETE FROM memories")


# ---------------------------------------------------------------- 应用配置

_CONFIG_FIELDS = (
    "system_prompt",
    "model",
    "tools_enabled",
    "memory_enabled",
    "memory_keep_recent",
    "memory_summarize_at",
    "memory_extract_every",
)


def _default_config() -> dict:
    return {
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "model": settings.CHAT_MODEL,
        "tools_enabled": [],
        "memory_enabled": True,
        "memory_keep_recent": 6,
        "memory_summarize_at": 12,
        "memory_extract_every": 3,
    }


def get_config() -> dict:
    init()
    row = dbconn.query_one(
        "SELECT system_prompt, model, tools_enabled, memory_enabled, memory_keep_recent, "
        "memory_summarize_at, memory_extract_every FROM app_config WHERE id=1"
    )
    if row is None:
        return _default_config()
    cfg = dict(row)
    cfg["tools_enabled"] = json.loads(cfg["tools_enabled"] or "[]")
    cfg["memory_enabled"] = bool(cfg["memory_enabled"])
    for key, val in _default_config().items():
        if cfg.get(key) in (None, ""):
            cfg[key] = val
    return cfg


def set_config(**patch) -> dict:
    init()
    cfg = get_config()
    for key, val in patch.items():
        if key in _CONFIG_FIELDS and val is not None:
            cfg[key] = val

    values = (
        cfg["system_prompt"],
        cfg["model"],
        json.dumps(cfg["tools_enabled"], ensure_ascii=False),
        int(bool(cfg["memory_enabled"])),
        int(cfg["memory_keep_recent"]),
        int(cfg["memory_summarize_at"]),
        int(cfg["memory_extract_every"]),
        time.time(),
    )
    # upsert 写成「先判断再写」—— 两边都认，不引入 ON CONFLICT / ON DUPLICATE KEY
    if dbconn.query_one("SELECT id FROM app_config WHERE id=1"):
        dbconn.execute(
            "UPDATE app_config SET system_prompt=?, model=?, tools_enabled=?, memory_enabled=?, "
            "memory_keep_recent=?, memory_summarize_at=?, memory_extract_every=?, updated_at=? WHERE id=1",
            values,
        )
    else:
        dbconn.execute(
            "INSERT INTO app_config (system_prompt, model, tools_enabled, memory_enabled, "
            "memory_keep_recent, memory_summarize_at, memory_extract_every, updated_at, id) "
            "VALUES (?,?,?,?,?,?,?,?,1)",
            values,
        )
    return get_config()


# ---------------------------------------------------------------- 工作台统计
# 注意：知识库（文档/切块）统计不在这里 —— 那属于 rag 模块，由 API 层去问 store。

def get_stats() -> dict:
    init()
    convs = dbconn.query_one("SELECT COUNT(*) AS c FROM conversations")
    msgs = dbconn.query_one("SELECT COUNT(*) AS c FROM messages")
    tokens = dbconn.query_one("SELECT COALESCE(SUM(tokens),0) AS s FROM messages")
    latency = dbconn.query_one(
        "SELECT COALESCE(AVG(latency_ms),0) AS a FROM messages WHERE latency_ms>0"
    )
    mems = dbconn.query_one("SELECT COUNT(*) AS c FROM memories")
    rows = dbconn.query("SELECT tool_calls FROM messages WHERE tool_calls != '[]'")

    tool_count = 0
    for r in rows:
        try:
            tool_count += len(json.loads(r["tool_calls"]))
        except Exception:  # noqa: BLE001
            pass
    return {
        "conversations": int(convs["c"]) if convs else 0,
        "messages": int(msgs["c"]) if msgs else 0,
        "tokens": int(tokens["s"]) if tokens else 0,
        "avg_latency_ms": round(float(latency["a"]), 0) if latency else 0,
        "memories": int(mems["c"]) if mems else 0,
        "tool_calls": tool_count,
    }
