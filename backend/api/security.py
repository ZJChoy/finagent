"""变更类接口的管理口令校验（D10 安全决策：公网暴露下 fail-closed）。

规则：
- `.env` 配置 ADMIN_TOKEN 后，变更类接口（PUT/POST/DELETE）要求请求头 `X-Admin-Token` 匹配；
- **未配置 ADMIN_TOKEN 时默认拒绝**——公网环境下配置页/删文档/清记忆裸奔等于送权限；
- 查询类接口（GET、/api/chat）不受影响，保持演示开放。
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from ..config import settings


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    token = getattr(settings, "ADMIN_TOKEN", None)
    if not token:
        raise HTTPException(
            status_code=403,
            detail="变更类接口已关闭（公网安全策略）：请在 .env 配置 ADMIN_TOKEN 后，"
                   "请求头携带 X-Admin-Token 重试",
        )
    if not hmac.compare_digest((x_admin_token or "").encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(status_code=403, detail="X-Admin-Token 不匹配")
