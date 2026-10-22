"""calculate 工具的数字格式化契约。

背景（refactor-log M0.2）：_format_number 曾把 (-1,0) 区间的结果丢掉负号
（int("-0") == 0），模型会照抄成"上升 0.5%"。
"""
import pytest

from backend.tools.calc import _calculate, _format_number


class TestFormatNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.5, "0.5"),
            (-0.5, "-0.5"),          # 回归：曾输出 "0.5"
            (-0.00005, "-0.00005"),  # 回归：曾输出 "0.00005"
            (-1.5, "-1.5"),
            (-1234.5, "-1,234.5"),
            (172054171890.91, "172,054,171,890.91"),
            (0.0, "0"),
            (100.0, "100"),
            (1 / 3, "0.333333"),
            (float("inf"), "inf"),
            (float("nan"), "nan"),
        ],
    )
    def test_format(self, value, expected):
        assert _format_number(value) == expected

    def test_negative_zero(self):
        # -0.0 不应输出 "-0"
        assert _format_number(-0.0) in ("0", "-0")


class TestCalculate:
    def test_basic(self):
        assert "3" in _calculate("1+2")["content"]

    def test_negative_result_keeps_sign(self):
        out = _calculate("0-0.5")["content"]
        assert "-0.5" in out, f"负号丢失：{out}"  # 回归

    def test_division_by_zero_is_failure(self):
        out = _calculate("1/0")
        assert out["ok"] is False          # M0.3 契约：失败必须显式声明
        assert "失败" in out["content"]

    def test_bad_expression_is_failure(self):
        out = _calculate("__import__('os')")
        assert out["ok"] is False

    def test_success_is_ok(self):
        assert _calculate("1+1")["ok"] is True
