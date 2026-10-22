"""工具注册中心的 ok 契约（refactor-log M0.3）。

契约：result["ok"] 必须由工具显式给出；registry.call 对缺失 ok 的返回
默认 False——失败要显式声明成功，而不是成功要显式声明失败。
"""
import pytest

import backend.tools  # noqa: F401  触发工具注册
from finagent.core import tools as core_tools


def _register(name, func):
    core_tools.register(
        core_tools.Tool(
            name=name,
            description="测试工具",
            parameters={"type": "object", "properties": {}},
            func=func,
        )
    )


def test_call_missing_ok_defaults_false():
    """工具返回不带 ok 字段时，registry 不得替它声明成功。"""
    _register("_t_no_ok", lambda: {"content": "看起来成功"})
    out = core_tools.call("_t_no_ok", {})
    assert out["ok"] is False


def test_call_explicit_ok_true_passes_through():
    _register("_t_ok", lambda: {"ok": True, "content": "x"})
    assert core_tools.call("_t_ok", {})["ok"] is True


def test_call_unknown_tool_not_ok():
    assert core_tools.call("no_such_tool", {})["ok"] is False


def test_call_exception_not_ok():
    def boom():
        raise RuntimeError("炸了")

    _register("_t_boom", boom)
    out = core_tools.call("_t_boom", {})
    assert out["ok"] is False
    assert "炸了" in out["content"]


def test_call_type_error_not_ok():
    def needs_arg(q: str) -> dict:
        return {"content": q}

    _register("_t_args", needs_arg)
    out = core_tools.call("_t_args", {})
    assert out["ok"] is False


# ---- 领域工具的失败分支契约 ----

class TestDomainToolsContract:
    def test_calculate_failure_not_ok(self):
        out = core_tools.call("calculate", {"expression": "1/0"})
        assert out["ok"] is False

    def test_finance_bad_symbol_not_ok(self):
        out = core_tools.call("get_stock_financials", {"symbol": "abc"})
        assert out["ok"] is False
        assert "格式不对" in out["content"]

    def test_knowledge_empty_hit_is_ok(self):
        """检索无命中不是失败（空结果是合法回答），但必须显式 ok=True。"""
        out = core_tools.call("search_knowledge", {"query": "完全无关的查询xyz12345"})
        assert out["ok"] is True
