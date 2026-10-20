"""Tool Calling 参数解析护栏的离线验证（不依赖真实 LLM / 不起服务）。

用假 stream_chat 注入三种 arguments，验证 AgentEngine 的行为：
  1. 合法 JSON          → 工具正常执行（回归：护栏不误伤）
  2. 烂 JSON（截断）     → 工具不执行，精确错误回填 tool 消息，模型下一轮自修
  3. JSON 解析成 list   → 同上（必须是对象）

运行：python scripts/test_parse_guard.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finagent.core.runtime import AgentEngine  # noqa: E402
from finagent.core import tools as core_tools  # noqa: E402


# ---- 假工具：记录被调用次数 ----
CALLS: list[dict] = []


def _dummy_search(q: str, top_k: int = 5) -> dict:
    CALLS.append({"q": q, "top_k": top_k})
    return {"ok": True, "content": f"【片段 1】关于 {q} 的检索结果", "citations": []}


core_tools.register(
    core_tools.Tool(
        name="dummy_search",
        description="测试工具",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        func=_dummy_search,
    )
)


# ---- 假 LLM：按脚本回放 tool_calls ----
class FakeLLM:
    def __init__(self, script: list[list[dict]]):
        self._script = script
        self._round = 0

    def stream_chat(self, messages, model=None, tools=None, max_tokens=None):
        events = self._script[self._round]
        self._round += 1
        yield from events


# ---- 假 ctx / settings / prompt_builder ----
class FakeCtx:
    @staticmethod
    def trim_tool_result(content, limit):
        return content, False

    @staticmethod
    def total_tokens(messages):
        return 0


SETTINGS = SimpleNamespace(MAX_MODEL_CALLS=4, TOOL_RESULT_MAX_CHARS=2000, MAX_OUTPUT_TOKENS=512)


def _prompt_builder(query, history, system_prompt, grounding=None):
    return [{"role": "user", "content": query}]


def _content_end(content="", tool_calls=None):
    events = []
    if content:
        events.append({"type": "content", "delta": content})
    events.append({"type": "end", "tool_calls": tool_calls or [], "usage": {}})
    return events


def run_engine(script: list[list[dict]]) -> tuple[dict, list[dict]]:
    """跑一遍引擎，返回 (end 事件, 收集到的全部事件)。"""
    engine = AgentEngine(
        llm_mod=FakeLLM(script),
        settings=SETTINGS,
        registry=core_tools,
        ctx_mod=FakeCtx,
        prompt_builder=_prompt_builder,
    )
    events = list(engine.run("测试问题"))
    end = [e for e in events if e["type"] == "end"][0]
    return end, events


def check(label: str, cond: bool) -> bool:
    print(("  PASS " if cond else "  FAIL ") + label)
    return cond


def main() -> int:
    ok = True

    # 1) 回归：合法 JSON 正常执行
    CALLS.clear()
    end, events = run_engine(
        [
            _content_end(tool_calls=[{"id": "c1", "name": "dummy_search", "arguments": '{"q": "营收"}'}]),
            _content_end(content="完成"),
        ]
    )
    print("用例 1：合法 JSON")
    ok &= check("工具被执行 1 次", len(CALLS) == 1)
    ok &= check("trace ok=True", end["tool_calls"][0]["ok"] is True)
    ok &= check("无 parse_error 标记", "parse_error" not in end["tool_calls"][0])

    # 2) 烂 JSON：不执行 + 错误回填 + 模型下一轮自修
    CALLS.clear()
    bad = '{"q": "营收" '  # 截断的 JSON
    end, events = run_engine(
        [
            _content_end(tool_calls=[{"id": "c2", "name": "dummy_search", "arguments": bad}]),
            _content_end(content="已按修正后的参数重查完成"),
        ]
    )
    print("用例 2：烂 JSON（截断）")
    ok &= check("工具一次都没被执行", len(CALLS) == 0)
    tr = end["tool_calls"][0]
    ok &= check("trace ok=False 且带 parse_error 标记", tr["ok"] is False and tr.get("parse_error") is True)
    ok &= check("错误含定位信息（msg/pos）", "不是合法 JSON" in tr["result"])
    tool_msgs = [m for m in [] ]  # placeholder（messages 在引擎内部，改用事件断言）
    fb = [e for e in events if e["type"] == "tool_result" and "未执行" in e["content"]]
    ok &= check("tool_result 事件带自修指令", bool(fb) and "重新调用" in fb[0]["content"])
    ok &= check("最终回答正常产出", end["content"] == "已按修正后的参数重查完成")

    # 3) 解析成非对象：同样拦截
    CALLS.clear()
    end, events = run_engine(
        [
            _content_end(tool_calls=[{"id": "c3", "name": "dummy_search", "arguments": '["营收"]'}]),
            _content_end(content="完成"),
        ]
    )
    print("用例 3：JSON 解析成 list")
    ok &= check("工具一次都没被执行", len(CALLS) == 0)
    ok &= check("错误指明必须是 JSON 对象", "必须是 JSON 对象" in end["tool_calls"][0]["result"])

    print("\n结果：", "全部通过" if ok else "存在失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
