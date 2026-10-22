"""Agent 运行时引擎（框架层，自研 ReAct 循环）。

流程：查询改写（可选钩子）→ 构造消息 → 模型决定 → 若要求调工具则执行并回填 → 循环 → 最终回答。

限制（构造时经 settings 注入）：
- 轮次：最多 `MAX_MODEL_CALLS` 次模型调用；
- 工具结果：单条超过 `TOOL_RESULT_MAX_CHARS` 截断（经 ctx 钩子）；
- 输出：单次 `MAX_OUTPUT_TOKENS` 上限（经 llm 钩子）。

应用通过构造参数注入四类钩子，core 不感知任何领域：
- prompt_builder(query, history, system_prompt, grounding) -> messages
- preflight(query) -> dict | None   领域预取（接地门控），返回
      {"force", "block", "pre_hits", "citations", "snapshot", "symbol", "events"}
- rewriter(message, history, model) -> query
- registry / llm / ctx              工具注册表、模型客户端、上下文预算

产出统一事件：rewrite / gate / citations / content / tool_call / tool_result / warning / replace / end
"""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Generator


def _shift_numbers(content: str, count: int, offset: int) -> str:
    """把工具返回文本里的「【片段 N】」整体平移 offset（一次检索多次时避免编号撞车）。"""
    for old in range(count, 0, -1):
        content = content.replace(f"【片段 {old}】", f"【片段 {old + offset}】")
    return content


class AgentEngine:
    def __init__(
        self,
        *,
        llm_mod,
        settings,
        registry,
        ctx_mod,
        prompt_builder: Callable,
        preflight: Callable[[str], dict | None] | None = None,
        rewriter: Callable | None = None,
    ):
        self._llm = llm_mod
        self._settings = settings
        self._registry = registry
        self._ctx = ctx_mod
        self._prompt_builder = prompt_builder
        self._preflight = preflight
        self._rewriter = rewriter

    def run(
        self,
        message: str,
        history: list[dict] | None = None,
        system_prompt: str | None = None,
        model: str | None = None,
        enabled_tools: list[str] | None = None,
    ) -> Generator[dict, None, None]:
        started = time.time()

        # 0) 查询改写（历史条数由应用层记忆策略统一控制，这里不再裁）
        query = message
        if self._rewriter is not None:
            query = self._rewriter(message, history, model=model)
        if query != message:
            yield {"type": "rewrite", "original": message, "query": query}

        tools_spec = self._registry.openai_specs(enabled_tools)

        citations: list[dict] = []
        trace: list[dict] = []
        prompt_tokens = completion_tokens = 0
        final_text = ""
        truncated_any = False

        # 领域预取（应用钩子）：涉及事实/数字 → 由代码保底预取并注入接地块
        pf = self._preflight(query) if self._preflight else None
        force_grounding = bool(pf and pf.get("force"))
        pre_hits: list[dict] = pf.get("pre_hits", []) if pf else []
        snapshot = pf.get("snapshot") if pf else None
        grounding_block = pf.get("block") if pf else None
        if pf:
            # 预取命中的引用必须并入主列表（编号从 1 起）——否则答案引用会被误判为可疑引用
            citations.extend(pf.get("citations", []))
        for ev in pf.get("events", []) if pf else []:
            yield ev

        messages = self._prompt_builder(
            query, history, system_prompt, grounding=grounding_block
        )

        for _round in range(self._settings.MAX_MODEL_CALLS):
            round_content = ""
            tool_calls: list[dict] = []

            for ev in self._llm.stream_chat(
                messages,
                model=model,
                tools=tools_spec or None,
                max_tokens=self._settings.MAX_OUTPUT_TOKENS,
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

                # 参数解析护栏：arguments 必须是合法 JSON 对象。
                # 解析失败不执行工具，把精确错误作为 tool 消息回填，让模型下一轮自修
                #（轮次上限 MAX_MODEL_CALLS 兜底，不会死循环）。
                parse_error: str | None = None
                try:
                    args = json.loads(tc["arguments"] or "{}")
                    if not isinstance(args, dict):
                        parse_error = f"arguments 解析成了 {type(args).__name__}，必须是 JSON 对象"
                except json.JSONDecodeError as e:
                    parse_error = f"arguments 不是合法 JSON（{e.msg}，位置 {e.pos}）"

                if parse_error is not None:
                    feedback = (
                        f"工具 {name} 未执行：{parse_error}。"
                        "请重新调用该工具，arguments 必须是合法 JSON 对象字符串。"
                    )
                    trace.append(
                        {
                            "name": name,
                            "arguments": None,
                            "result": feedback[:300],
                            "truncated": False,
                            "ok": False,
                            "parse_error": True,
                        }
                    )
                    yield {"type": "tool_call", "name": name, "arguments": None}
                    yield {"type": "tool_result", "name": name, "content": feedback, "truncated": False}
                    messages.append(
                        {"role": "tool", "tool_call_id": tc["id"] or f"call_{i}", "content": feedback}
                    )
                    continue

                yield {"type": "tool_call", "name": name, "arguments": args}

                if enabled_tools is not None and name not in enabled_tools:
                    result = {"ok": False, "content": f"工具 {name} 未启用，请改用其他工具或直接回答", "citations": []}
                else:
                    result = self._registry.call(name, args)
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
                content, truncated = self._ctx.trim_tool_result(
                    content, self._settings.TOOL_RESULT_MAX_CHARS
                )
                truncated_any = truncated_any or truncated

                trace.append(
                    {
                        "name": name,
                        "arguments": args,
                        "result": content[:300],
                        "truncated": truncated,
                        "ok": bool(result.get("ok", True)),
                        "citations": len(result.get("citations") or []),
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

        # 接地校验（应用钩子提供 force 语义）：需要依据却无任何成功工具调用 → 拒答。
        # 证据 = 检索/取数类工具的成功调用，且**必须带回引用**（citations）——
        # "检索了但一无所获"不是依据；calculate 这类算术辅助成败都不算
        #（实测：算术成功曾骗过本判定放行幻觉）。
        # 证据性用轻量约定声明：工具名含 search/query/get/fetch/retrieve。
        _EVIDENCE_HINTS = ("search", "query", "get_", "fetch", "retrieve")
        grounded = bool(pre_hits) or bool(snapshot) or any(
            t.get("ok")
            and (t.get("citations") or 0) > 0
            and any(h in (t.get("name") or "") for h in _EVIDENCE_HINTS)
            for t in trace
        )
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
            "context_tokens": self._ctx.total_tokens(messages),
            "truncated": truncated_any,
            "prefetched": {
                "fragments": len(pre_hits),
                "snapshot": bool(snapshot),
                "symbol": pf.get("symbol") if force_grounding else None,
            },
            "invalid_citations": invalid_citations,
            "grounded": grounded or not force_grounding,
            "ungrounded": ungrounded,
            "latency_ms": int((time.time() - started) * 1000),
        }
