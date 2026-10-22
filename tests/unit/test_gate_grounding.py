"""实体一致性校验与代码解析（refactor-log M0.4）。

背景（实测事故）：问"格力电器"时兜底从美的年报文件名捞出 000333——
数字全真、主语全错。现有校验只对映射表内的公司生效，映射表之外直接放行。
"""
import pytest

from backend.agent.gate import needs_grounding, needs_snapshot
from backend.agent.grounding import resolve_symbol

HITS_MOUTAI = [{"doc": "600519_2025_annual.PDF", "text": "证券代码：600519", "page": 1, "seq": 0, "score": 0.9}]
HITS_ILI = [{"doc": "000858_伊利股份_2025年年度报告.PDF", "text": "...", "page": 1, "seq": 0, "score": 0.9}]


class TestGate:
    @pytest.mark.parametrize("msg", [
        "茅台2025年营收", "美的集团怎么样", "600519 2025", "分红政策如何",
    ])
    def test_needs_grounding_true(self, msg):
        assert needs_grounding(msg) is True

    def test_needs_grounding_false(self):
        assert needs_grounding("你好，今天天气如何") is False

    def test_needs_snapshot(self):
        assert needs_snapshot("2025年营业收入") is True
        assert needs_snapshot("讲讲你们怎么做的") is False


class TestResolveSymbol:
    def test_mapped_name(self):
        code, how = resolve_symbol("贵州茅台2025年营业收入", HITS_MOUTAI)
        assert code == "600519"

    def test_direct_code_with_space(self):
        code, _ = resolve_symbol("600519 2025年营业收入", HITS_MOUTAI)
        assert code == "600519"

    def test_code_adjacent_to_chinese(self):
        """回归：\\b 在中文与数字之间不构成词边界，导致 '查一下600519的营收' 解析不到。"""
        code, _ = resolve_symbol("查一下600519的营收", HITS_MOUTAI)
        assert code == "600519", "中文紧邻的 6 位代码必须能识别"

    def test_unmapped_company_with_hit_is_rejected(self):
        """回归：映射表外的公司（伊利）命中时，问格力必须拒答而非错配。"""
        code, how = resolve_symbol("格力电器的营业收入是多少", HITS_ILI)
        assert code is None, f"问格力却解析出 {code}——实体错配又回来了"
        assert "未收录" in how or "不在语料库" in how

    def test_mapped_company_with_hit_passes(self):
        code, _ = resolve_symbol("贵州茅台的营业收入是多少", HITS_MOUTAI)
        assert code == "600519"

    def test_no_hits_uses_local_map(self):
        """无检索命中时：问句自带公司名 → 走一级本地映射（这是设计行为）；
        无名无码 → 未识别。"""
        code, _ = resolve_symbol("贵州茅台2025年营业收入", [])
        assert code == "600519"
        code2, how2 = resolve_symbol("这家公司2025年营业收入", [])
        assert code2 is None
