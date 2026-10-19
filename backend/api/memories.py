"""长期记忆接口（L4）—— 纪律：记忆必须**可见可删**。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from .security import require_admin
from pydantic import BaseModel

from .. import db

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.get("")
def list_memories():
    return db.list_memories()


class MemoryIn(BaseModel):
    kind: str = "偏好"
    content: str


@router.post("")
def add_memory(body: MemoryIn, _admin: None = Depends(require_admin)):
    db.add_memory(body.kind, body.content, None)
    return db.list_memories()


@router.delete("/{mid}")
def delete_memory(mid: int, _admin: None = Depends(require_admin)):
    db.delete_memory(mid)
    return {"ok": True, "deleted": mid}


@router.delete("")
def clear_memories(_admin: None = Depends(require_admin)):
    db.clear_memories()
    return {"ok": True}
