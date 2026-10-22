"""请求 ID + 阶段日志（M2.4）。

单机定位下的可观测性最小集：stdlib logging + JSON 行到 stdout。
每条日志带 request_id，一次失败请求能从 access log 串到 agent 阶段耗时。
默认 INFO；不落用户问题原文与回答正文（只落长度/阶段/耗时/错误类型）。
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

logger = logging.getLogger("finagent")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def current_request_id() -> str:
    return _request_id.get()


def log_event(stage: str, level: str = "INFO", **fields) -> None:
    """结构化事件日志。fields 只放标量（长度/耗时/计数/错误类型），不放用户原文。"""
    record = {"ts": round(time.time(), 3), "request_id": current_request_id(),
              "stage": stage, "level": level, **fields}
    fn = logger.warning if level == "WARN" else logger.error if level == "ERROR" else logger.info
    fn(json.dumps(record, ensure_ascii=False))
