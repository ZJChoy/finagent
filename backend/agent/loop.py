"""Agent 主循环（自研实现）。

流程：查询改写 → 构造消息 → 模型决定 → 若要求调工具则执行并回填 → 循环 → 最终回答。

本步骤（6）落地的**多方面限制**：
- 轮次：最多 `MAX_MODEL_CALLS` 次模型调用；
- 历史：条数由记忆策略 `memory_keep_recent` 统一控制（见 `agent/memory.py`）；
- 工具结果：单条超过 `TOOL_RESULT_MAX_CHARS` 截断；
- 输出：单次 `MAX_OUTPUT_TOKENS` 上限。

产出统一事件：rewrite / content / tool_call / tool_result / citations / end
"""
from __future__ import annotations

import json
import re
import time
from typing import Generator

from .. import llm
from ..config import settings
from ..rag import retriever
from ..tools import call as call_tool
from ..tools import openai_specs
from . import context as ctx
from . import gate
from . import grounding as gnd
from . import rewrite as rewriter
from .prompts import build_messages


def _shift_numbers(content: str, count: int, offset: int) -> str:
    """把工具返回文本里的「【片段 N】」整体平移 offset（一次检索多次时避免编号撞车）。"""
    for old in range(count, 0, -1):
        content = content.replace(f"【片段 {old}】", f"【片段 {old + offset}】")
    return content


def run(
    message: str,
    history: list[dict] | None = None,
    system_prompt: str | None = None,
    model: str | None = None,
    enabled_tools: list[str] | None = None,
) -> Generator[dict, None, None]:
    started = time.time()

    # 0) 查询改写（历史条数由 memory.keep_recent 统一控制，这里不再裁）
    query = rewriter.rewrite(message, history, model=model)
    if query != message:
        yield {"type": "rewrite", "original": message, "query": query}

    tools_spec = openai_specs(enabled_tools)

    citations: list[dict] = []
    trace: list[dict] = []
    prompt_tokens = completion_tokens = 0
    final_text = ""
    truncated_any = False

    # 门控（L0）：涉及公司事实/数字 → **由代码保底预取**两条通道并注入
    #   通道 A = 年报原文片段（本地向量检索）
    #   通道 B = 结构化财务数据（akshare，命中数值信号时）
    force_grounding = gate.needs_grounding(query)
    want_snapshot = gate.needs_snapshot(query)
    grounding_block: str | None = None
    pre_hits: list[dict] = []
    snapshot: str | None = None
    if force_grounding:
        try:
            pre_hits = retriever.retrieve(query)
        except Exception:  # noqa: BLE001  预检索失败不阻断对话
            pre_hits = []
        prefetch = gnd.build_prefetch(query, pre_hits, want_snapshot)
        grounding_block = prefetch["block"]
        snapshot = prefetch["snapshot"]
        if pre_hits:
            citations.extend(retriever.citations_of(pre_hits))

        channels: list[str] = []
        if pre_hits:
            channels.append(f"通道 A 年报片段 {len(pre_hits)} 个")
        if snapshot:
            channels.append(f"通道 B 结构化数据（{prefetch['symbol']}）")
        yield {
            "type": "gate",
            "message": "本题涉及公司事实，已由系统强制预取："
            + ("、".join(channels) if channels else "但未命中任何资料"),
        }
        if citations:
            yield {"type": "citations", "items": citations}
        if not grounding_block:
            grounding_block = (
                "（系统预取未命中任何资料。若问题依赖原文/数据，请明确说明未找到依据。）"
            )

    messages = build_messages(query, history, system_prompt, grounding=grounding_block)

    for _round in range(settings.MAX_MODEL_CALLS):
        round_content = ""
        tool_calls: list[dict] = []

        for ev in llm.stream_chat(
            messages,
            model=model,
            tools=tools_spec or None,
            max_tokens=settings.MAX_OUTPUT_TOKENS,
        ):
            if ev["type"] == "content":
                round_content += ev["delta"]
                yield {"type": "content", "delta": ev["delta"]}
            else:  # end
                tool_calls = ev.get("tool_calls") or []
                usage = ev.get("usage") or {}
                prompt_tokens += usage.get("prompt_tokens") or 0
                completion_tokens += usage.get("completion_tokens") or 0

        if not tool_calls:
            final_text = round_content
            break

        messages.append(
            {
                "role": "assistant",
                "content": round_content or None,
                "tool_calls": [
                    {
                        "id": tc["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"},
                    }
                    for i, tc in enumerate(tool_calls)
                ],
            }
        )

        for i, tc in enumerate(tool_calls):
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            yield {"type": "tool_call", "name": name, "arguments": args}

            result = call_tool(name, args)
            content = result.get("content", "")

            new_citations = result.get("citations") or []
            if new_citations:
                offset = len(citations)
                content = _shift_numbers(content, len(new_citations), offset)
                for c in new_citations:
                    c["no"] = offset + c["no"]
                    citations.append(c)
                yield {"type": "citations", "items": citations}

            # 限制：单条工具结果长度
            content, truncated = ctx.trim_tool_result(content)
            truncated_any = truncated_any or truncated

            trace.append(
                {
                    "name": name,
                    "arguments": args,
                    "result": content[:300],
                    "truncated": truncated,
                    "ok": bool(result.get("ok", True)),
                }
            )
            yield {"type": "tool_result", "name": name, "content": content[:800], "truncated": truncated}

            messages.append(
                {"role": "tool", "tool_call_id": tc["id"] or f"call_{i}", "content": content}
            )
    else:
        final_text = final_text or "（已达到最大工具调用轮次，请把问题拆小一点再问）"
        yield {"type": "content", "delta": final_text}

    # 引用校验：回答里的「片段 N」必须落在本轮 citations 里，否则算可疑引用
    valid_numbers = {c["no"] for c in citations}
    referenced = {int(n) for n in re.findall(r"片段\s*(\d+)", final_text)}
    invalid_citations = sorted(referenced - valid_numbers)
    if invalid_citations:
        yield {
            "type": "warning",
            "message": f"检测到可疑引用：{invalid_citations}（本轮并未检索到这些片段）",
        }

    # 接地校验（L3）：本题需要依据，却既没有预取到资料、也没成功调过任何工具 → 不允许凭记忆作答
    grounded = bool(pre_hits) or bool(snapshot) or any(t.get("ok") for t in trace)
    ungrounded = force_grounding and not grounded
    if ungrounded:
        final_text = (
            "未能在年报原文或财务数据中找到相关依据，因此无法回答该问题。"
            "请确认公司名称 / 报告期，或换一种问法。"
        )
        yield {"type": "replace", "content": final_text}
        yield {
            "type": "warning",
            "message": "未接地：本题需要依据，但本轮未成功调用任何工具，已改为拒答（不让模型凭记忆作答）。",
        }
        invalid_citations = []

    yield {
        "type": "end",
        "content": final_text,
        "citations": citations,
        "tool_calls": trace,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "context_tokens": ctx.total_tokens(messages),
        "truncated": truncated_any,
        "prefetched": {
            "fragments": len(pre_hits),
            "snapshot": bool(snapshot),
            "symbol": prefetch["symbol"] if force_grounding else None,
        },
        "invalid_citations": invalid_citations,
        "grounded": grounded or not force_grounding,
        "ungrounded": ungrounded,
        "latency_ms": int((time.time() - started) * 1000),
    }
