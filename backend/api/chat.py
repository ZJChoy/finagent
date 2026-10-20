"""对话接口：SSE 流式返回 Agent 全过程；会话与消息落 SQLite（L2 记忆）。

流程：会话不存在就建 → 先取历史（不含本轮）→ 落库用户消息 → 跑 Agent → 落库回答
      → 触发滚动摘要与长期记忆提炼。
"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db
from ..agent import loop as agent_loop
from ..agent import memory

router = APIRouter(prefix="/api", tags=["chat"])


class ChatIn(BaseModel):
    # 长度上限：一篇超长粘贴会让改写/预取/多轮工具全链路 token 爆炸
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- 会话

@router.get("/conversations")
def conversations():
    return db.list_conversations()


@router.get("/conversations/{cid}/messages")
def messages(cid: int):
    rows = db.list_messages(cid)
    return [
        {
            "id": m["id"],
            "role": m["role"],
            "content": m["content"],
            "citations": m["citations"],
            "tool_calls": m["tool_calls"],
            "created_at": m["created_at"],
        }
        for m in rows
    ]


@router.delete("/conversations/{cid}")
def delete_conversation(cid: int):
    db.delete_conversation(cid)
    return {"ok": True, "deleted": cid}


# ---------------------------------------------------------------- 对话

@router.post("/chat")
def chat(body: ChatIn):
    conv_id = body.conversation_id
    if not conv_id or not db.conversation_exists(conv_id):
        conv_id = db.create_conversation(body.message[:30])

    # 先取历史（此时还不含本轮），避免本轮问题被重复带上
    summary, history = memory.load_history(conv_id)
    db.add_message(conv_id, "user", body.message)

    # 配置从库里读（配置页改了就生效）
    cfg = db.get_config()
    system_prompt = memory.augment_system_prompt(cfg["system_prompt"])
    if summary:
        system_prompt += f"\n\n【本会话更早内容的摘要】\n{summary}"

    def gen():
        yield _sse({"type": "meta", "conversation_id": conv_id})
        done: dict | None = None
        try:
            for ev in agent_loop.run(
                body.message,
                history=history,
                system_prompt=system_prompt,
                model=cfg["model"],
                enabled_tools=cfg["tools_enabled"] or None,
            ):
                if ev["type"] == "end":
                    done = ev
                yield _sse(ev)
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})
            return

        if done:
            db.add_message(
                conv_id,
                "assistant",
                done.get("content", ""),
                citations=done.get("citations"),
                tool_calls=done.get("tool_calls"),
                tokens=(done.get("usage") or {}).get("total_tokens", 0),
                latency_ms=done.get("latency_ms", 0),
            )
        memory.maybe_summarize(conv_id)
        memory.maybe_extract(conv_id)

    return StreamingResponse(gen(), media_type="text/event-stream")
