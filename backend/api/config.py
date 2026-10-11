"""配置接口：系统提示词 / 模型 / 工具开关 / 记忆参数。

只负责「读写配置」，不掺业务逻辑 —— 配置的默认值与落库都在 `db.py`。
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..tools import all_tools, catalogs

router = APIRouter(prefix="/api/config", tags=["config"])

# 仅作下拉建议，实际可用模型以你的 endpoint 为准
MODEL_OPTIONS = ["qwen-plus", "qwen-max", "qwen-turbo", "deepseek-chat"]


def _payload() -> dict:
    cfg = db.get_config()
    return {
        "system_prompt": cfg["system_prompt"],
        "model": cfg["model"],
        "tools_enabled": cfg["tools_enabled"],
        "memory_enabled": cfg["memory_enabled"],
        "memory_keep_recent": cfg["memory_keep_recent"],
        "memory_summarize_at": cfg["memory_summarize_at"],
        "memory_extract_every": cfg["memory_extract_every"],
        "tools_catalog": catalogs(),
        "model_options": MODEL_OPTIONS,
        "llm_ready": settings.llm_ready,
        "base_url": settings.BASE_URL,
    }


@router.get("")
def get_config():
    return _payload()


class ConfigIn(BaseModel):
    system_prompt: str | None = None
    model: str | None = None
    tools_enabled: list[str] | None = None
    memory_enabled: bool | None = None
    memory_keep_recent: int | None = None
    memory_summarize_at: int | None = None
    memory_extract_every: int | None = None


# 参数边界 —— **后端必须校验**：配置页的 min/max 只是前端摆设，直接调接口能绕过。
# 注意 keep 的上限必须 < at 的上限，否则「at 必须 > keep」这条约束在边界处无法满足。
_BOUNDS = {
    "memory_keep_recent": (0, 30),
    "memory_summarize_at": (4, 40),
    "memory_extract_every": (1, 20),
}


def _sanitize(patch: dict) -> tuple[dict, list[str]]:
    """范围钳制 + 逻辑约束，返回 (清洗后的 patch, 调整说明)。"""
    notices: list[str] = []
    for key, (low, high) in _BOUNDS.items():
        if patch.get(key) is None:
            continue
        try:
            value = int(patch[key])
        except (TypeError, ValueError):
            patch.pop(key)
            notices.append(f"{key} 不是有效数字，已忽略")
            continue
        clamped = max(low, min(high, value))
        if clamped != value:
            notices.append(f"{key} 超出范围 [{low}, {high}]，已钳制为 {clamped}")
        patch[key] = clamped

    # 逻辑约束：压缩阈值必须 > 保留条数，否则压缩窗口为空、旋钮形同虚设
    if "memory_keep_recent" in patch or "memory_summarize_at" in patch:
        cur = db.get_config()
        keep = patch.get("memory_keep_recent", cur["memory_keep_recent"])
        at = patch.get("memory_summarize_at", cur["memory_summarize_at"])
        if at <= keep:
            fixed = min(_BOUNDS["memory_summarize_at"][1], int(keep) + 2)
            patch["memory_summarize_at"] = fixed
            notices.append(f"「触发压缩」须大于「保留原文」，已调整为 {fixed}（保留 {keep}）")
    return patch, notices


@router.put("")
def update_config(body: ConfigIn):
    patch = body.model_dump(exclude_none=True)
    if "tools_enabled" in patch:
        valid = {t.name for t in all_tools()}
        patch["tools_enabled"] = [n for n in patch["tools_enabled"] if n in valid]
    patch, notices = _sanitize(patch)
    db.set_config(**patch)
    payload = _payload()
    payload["notices"] = notices
    return payload
