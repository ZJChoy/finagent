"""按客户端的令牌桶限流（进程内存版，单机演示定位够用）。

保护目标：/api/chat 每次消耗模型 token，/api/documents/search 每次消耗
一次 embedding 调用——公网隧道下裸奔等于替陌生人付费。
多 worker 部署时各进程独立计数（README 已声明单进程定位）。
"""
from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

# 令牌桶参数：容量 = 突发上限；每秒回填速率
_BUCKETS: dict[str, list] = {}   # key -> [tokens, last_refill_ts]
_LOCK = threading.Lock()


def _client_key(request: Request, scope: str) -> str:
    # 公网隧道（cloudflared）后面是单机，无 X-Forwarded-For 时退化到 socket 地址
    fwd = request.headers.get("x-forwarded-for") or ""
    ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    return f"{scope}:{ip}"


def rate_limit(request: Request, scope: str, capacity: int = 10, refill_per_sec: float = 0.2) -> None:
    """超限抛 429（带 Retry-After）。默认：10 次突发、每 5 秒回 1 个。"""
    key = _client_key(request, scope)
    now = time.monotonic()
    with _LOCK:
        bucket = _BUCKETS.get(key)
        if bucket is None:
            # 首次只发 capacity-1 个可用令牌（当前请求视为已消耗一个），
            # 否则突发上限实际是 capacity+1
            _BUCKETS[key] = [float(capacity) - 1.0, now]
            return
        elapsed = now - bucket[1]
        bucket[0] = min(float(capacity), bucket[0] + elapsed * refill_per_sec)
        bucket[1] = now
        if bucket[0] < 1.0:
            retry = max(1, int((1.0 - bucket[0]) / refill_per_sec))
            raise HTTPException(
                status_code=429,
                detail=f"请求太频繁，请 {retry} 秒后重试",
                headers={"Retry-After": str(retry)},
            )
        bucket[0] -= 1.0
