"""AgentEngine 的接地门控契约（假 LLM，不联网）。

M0.3 回归：calculate 执行失败却被 trace 记为 ok=True，
"计算类工具成功"会骗过接地判定，让本该拒答的题被放行。
契约：grounded 只认**证据类**工具（检索/取数）的成功调用；
calculate 无论成败都不产生接地证据。
"""
from types import SimpleNamespace

import backend.tools  # noqa: F401  注册领域工具
import backend.agent.context as ctx
from finagent.core.runtime import AgentEngine


SETTINGS = SimpleNamespace(
    MAX_MODEL_CALLS=3, MAX_OUTPUT_TOKENS=100, TOOL_RESULT_MAX_CHARS=2400,
)


class FakeLLM:
    """第 1 轮要求调用指定工具，第 2 轮直接给最终答案。"""

    def __init__(self, name, arguments):
        self.calls = [(name, arguments)]
        self.n = 0

    def stream_chat(self, messages, **kw):
        self.n += 1
        if self.n == 1:
            yield {"type": "end", "tool_calls": [
                {"id": "c1", "name": self.calls[0][0], "arguments": self.calls[0][1]}
            ], "usage": {}}
        else:
            yield {"type": "content", "delta": "净利润 123 亿元。"}
            yield {"type": "end", "tool_calls": [], "usage": {}}


def _engine(llm):
    return AgentEngine(
        llm_mod=llm, settings=SETTINGS,
        registry=__import__("backend.tools", fromlist=["x"]), ctx_mod=ctx,
        prompt_builder=lambda q, h, s, grounding=None: [{"role": "user", "content": q}],
        # force_grounding=True 且无预取命中：只有成功调用证据类工具才能通过接地校验
        preflight=lambda q: {"force": True, "block": "（无资料）", "pre_hits": [],
                             "citations": [], "snapshot": None, "symbol": None, "events": []},
    )


def _run(eng):
    """跑完引擎，返回 (end 事件, 全部事件列表)。"""
    evs = list(eng.run("茅台营收"))
    end = [e for e in evs if e["type"] == "end"]
    assert end, "引擎必须产出 end 事件"
    return end[0], evs


class TestGroundingGate:
    def test_failed_calculate_is_not_evidence(self):
        """回归：calculate 失败（ok=False）时必须拒答，而不是 grounded=True。"""
        end, _ = _run(_engine(FakeLLM("calculate", '{"expression": "1/0"}')))
        assert end.get("ungrounded") is True, "失败的 calculate 不该算接地证据"
        assert "无法回答" in end.get("content", "")

    def test_successful_calculate_is_alone_not_evidence(self):
        """calculate 成功也只是算术，不构成'基于资料'的证据。"""
        end, _ = _run(_engine(FakeLLM("calculate", '{"expression": "1+1"}')))
        assert end.get("grounded") is False

    def test_failed_search_is_not_evidence(self):
        """检索工具异常（registry 转成 ok=False）同样不构成证据。"""
        end, _ = _run(_engine(FakeLLM("search_knowledge", '{"query": "bad"}')))
        assert end.get("ungrounded") is True

    def test_successful_search_is_evidence(self):
        end, _ = _run(_engine(FakeLLM("search_knowledge", '{"query": "分红"}')))
        assert end.get("grounded") is True
        assert end.get("ungrounded") is False


class TestParseGuard:
    """参数解析护栏（原 scripts/test_parse_guard.py 的 pytest 化）。"""

    def test_bad_json_not_executed(self):
        llm = FakeLLM("search_knowledge", '{"query": "分红')  # 截断
        end, evs = _run(_engine(llm))
        # 工具没有真正执行（无 grounding 证据），精确错误已回填
        assert end.get("ungrounded") is True
        assert any("未执行" in e.get("content", "") for e in evs if e["type"] == "tool_result")

    def test_non_object_json_not_executed(self):
        llm = FakeLLM("search_knowledge", '["query"]')
        end, evs = _run(_engine(llm))
        assert end.get("ungrounded") is True
        assert any("list" in e.get("content", "") for e in evs if e["type"] == "tool_result")


class TestDisabledTools:
    def test_disabled_tool_is_not_evidence(self):
        """被 enabled_tools 拦截的调用不算接地证据。"""
        eng = _engine(FakeLLM("search_knowledge", '{"query": "分红"}'))
        evs = list(eng.run("茅台营收", enabled_tools=["calculate"]))
        end = [e for e in evs if e["type"] == "end"][0]
        assert end.get("grounded") is False
