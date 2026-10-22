"""相关性阈值契约（refactor-log M3.1）。

行为：MIN_SCORE>0 时低于阈值的块不返回；全低 → 空列表 → 上层走"无依据"拒答。
默认 0（关闭）——阈值必须由标定数据支持后才开（README 原则：先评测再启用）。
"""

from finagent.core.retriever import retriever


class TestMinScoreFilter:
    def _fake_hits(self, scores):
        return [{"doc": "x.pdf", "page": 1, "seq": i, "text": "t", "score": s}
                for i, s in enumerate(scores)]

    def test_default_off(self, monkeypatch):
        monkeypatch.setattr(retriever, "MIN_SCORE", 0.0)
        hits = self._fake_hits([0.1, 0.2])
        assert len(retriever._apply_min_score(hits)) == 2

    def test_filters_below_threshold(self, monkeypatch):
        monkeypatch.setattr(retriever, "MIN_SCORE", 0.65)
        hits = self._fake_hits([0.70, 0.64, 0.65, 0.5])
        kept = retriever._apply_min_score(hits)
        assert [h["score"] for h in kept] == [0.70, 0.65]  # >= 阈值保留

    def test_all_below_returns_empty(self, monkeypatch):
        monkeypatch.setattr(retriever, "MIN_SCORE", 0.9)
        assert retriever._apply_min_score(self._fake_hits([0.7, 0.6])) == []

    def test_retrieve_applies_threshold(self, monkeypatch):
        """端到端：mock 掉 store/embedder，确认 retrieve 出口过滤。"""
        monkeypatch.setattr(retriever, "MIN_SCORE", 0.65)
        monkeypatch.setattr(retriever.store, "count", lambda: 5)
        monkeypatch.setattr(retriever.store, "_assert_embedding_model_compatible",
                            lambda m: None)
        monkeypatch.setattr(retriever.embedder, "embed_query", lambda q: [0.0])
        monkeypatch.setattr(retriever.store, "search",
                            lambda *a, **k: self._fake_hits_static())
        hits = retriever.retrieve("查询")
        assert all(h["score"] >= 0.65 for h in hits)

    @staticmethod
    def _fake_hits_static():
        return [{"doc": "x.pdf", "page": 1, "seq": 0, "text": "t", "score": 0.8},
                {"doc": "x.pdf", "page": 1, "seq": 1, "text": "t", "score": 0.5}]
