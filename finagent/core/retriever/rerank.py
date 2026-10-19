"""重排序客户端：向量召回 → rerank 二次精排。

## 为什么需要它

向量检索是「双塔」结构 —— query 和 document 各自编码、从不见面，
所以**相似度分数高不等于真的相关**。本次实测就撞到过：
问「茅台的分红政策」，召回 top-5 得分 0.63–0.66 的块**全是无关内容**（基酒工艺、品牌宣传）。

rerank 是「交叉编码」（query 和 document 一起过模型），更准但更慢，
所以标准做法是：**向量召回一批候选（快、宽）→ rerank 精排（慢、准）→ 取前 k**。

## 接口（2026-10-11 实测确认，不凭记忆）

百炼的 rerank **不在 OpenAI 兼容路径上**：
- `{base_url}/reranks` → **HTTP 404**（公开域名上没有这个路由）
- 原生端点 `{host}/api/v1/services/rerank/text-rerank/text-rerank` → **200 ✅**

模型用 `qwen3-rerank`（官方推荐；`gte-rerank` 系列 2026-05-30 下线）。

## 降级策略

rerank 失败（网络/限流/超时）时**不抛异常**，返回原顺序 ——
检索质量可以退化，但不能让整条问答链断掉。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from finagent.core.appconfig import settings


def endpoint() -> str:
    """从 BASE_URL 推 host，拼出原生 rerank 端点（可用 RERANK_URL 覆盖）。"""
    if settings.RERANK_URL:
        return settings.RERANK_URL
    host = settings.BASE_URL.split("/compatible-mode")[0].rstrip("/")
    return f"{host}/api/v1/services/rerank/text-rerank/text-rerank"


def rerank(
    query: str,
    documents: list[str],
    top_n: int | None = None,
    model: str | None = None,
) -> tuple[list[int], bool]:
    """对候选文档重排。

    返回 `(按相关性降序的原始下标列表, 是否真的重排成功)`。
    `top_n` 为 None 时返回全部（由调用方自己截断）。
    失败时返回 `(原顺序, False)`。
    """
    if not query.strip() or not documents:
        return list(range(len(documents))), False

    payload = {
        "model": model or settings.RERANK_MODEL,
        "input": {"query": query, "documents": documents},
        "parameters": {"return_documents": False},
    }
    if top_n:
        payload["parameters"]["top_n"] = top_n

    req = urllib.request.Request(
        endpoint(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.RERANK_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        results = body["output"]["results"]
        order = [int(r["index"]) for r in results]
        # 接口只返回参与 top_n 的项，补齐剩余的，保证顺序是完整排列
        rest = [i for i in range(len(documents)) if i not in set(order)]
        return order + rest, True
    except (urllib.error.URLError, KeyError, ValueError, json.JSONDecodeError, TimeoutError):
        return list(range(len(documents))), False
