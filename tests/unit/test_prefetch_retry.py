"""预取未命中时给模型重试提示（M4 评测回归后修复）。

背景：40 题评测发现 4 个误拒——预取检索分数低于阈值（0.59~0.66 < 0.67）时，
preflight 注入"未命中任何资料"块，模型看到后**一轮工具都不调就放弃**，
最终被接地门判拒答。其中计算题（Q27）根本不需要检索依据。
修法：未命中块的文案改为"先调工具换关键词检索，取不到再如实说明"。
"""
from backend.agent.loop import _preflight
from backend.rag import retriever


class TestPrefetchEmptyHint:
    def test_empty_prefetch_suggests_tool_retry(self, monkeypatch):
        monkeypatch.setattr(retriever, "retrieve", lambda *a, **k: [])
        pf = _preflight("贵州茅台和美的集团的销售模式有什么不同？")
        assert pf is not None
        assert "search_knowledge" in pf["block"], "未命中时必须引导模型自行调工具"
        assert "不要直接放弃" in pf["block"]

    def test_hit_prefetch_unchanged(self, monkeypatch):
        hits = [{"doc": "600519_2025_annual.PDF", "page": 1, "seq": 0,
                 "text": "分红政策：…", "score": 0.9}]
        monkeypatch.setattr(retriever, "retrieve", lambda *a, **k: hits)
        monkeypatch.setattr("backend.agent.grounding.build_prefetch",
                            lambda q, h, w: {"block": "── 通道 A ──", "snapshot": None,
                                             "symbol": "600519", "how": "本地映射"})
        pf = _preflight("贵州茅台的分红政策")
        assert pf["block"] == "── 通道 A ──"  # 命中时不叠加重试文案
