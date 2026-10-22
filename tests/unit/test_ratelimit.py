"""限流与 search 边界契约（refactor-log M2.3）。"""
from fastapi.testclient import TestClient

from backend.api import ratelimit
from backend.main import app

client = TestClient(app)


class TestRateLimit:
    def test_burst_then_429_unit(self):
        """直接单测桶逻辑（不经过 app，避免与其它测试共享桶状态）。
        注意首个 key 会拿满桶（容量 2），所以第 3 次才 429。"""
        from fastapi import HTTPException

        ratelimit._BUCKETS.clear()
        req = type("R", (), {"headers": {}, "client": type("C", (), {"host": "9.9.9.9"})()})()

        codes = []
        for _ in range(3):
            try:
                ratelimit.rate_limit(req, "unittest-cap2", capacity=2, refill_per_sec=0.001)
                codes.append(200)
            except HTTPException as e:
                codes.append(e.status_code)
                assert "Retry-After" in (e.headers or {})
        assert codes == [200, 200, 429], codes

    def test_burst_then_429(self):
        """端到端：search 端点 capacity=20，第 21 次同 IP 应 429。
        清空真实桶——TestClient 全部来自同一 host。"""
        ratelimit._BUCKETS.clear()
        for i in range(20):
            r = client.post("/api/documents/search", json={"query": f"q{i}", "top_k": 1})
            assert r.status_code == 200, f"第 {i+1} 次不应限流: {r.status_code}"
        r = client.post("/api/documents/search", json={"query": "q21", "top_k": 1})
        assert r.status_code == 429
        assert "Retry-After" in r.headers

    def test_distinct_scopes_independent(self, monkeypatch):
        monkeypatch.setattr(ratelimit, "_BUCKETS", {})
        # search 打满后 chat 不受影响（chat 需要模型，只验证不 429 即走到业务层即可：
        # 这里用 /api/documents/search 的 key 与 health 无关，简单验证 scope 隔离逻辑）
        r1 = ratelimit._client_key(type("R", (), {"headers": {"x-forwarded-for": "1.2.3.4"},
                                                  "client": None})(), "chat")
        r2 = ratelimit._client_key(type("R", (), {"headers": {"x-forwarded-for": "1.2.3.4, proxy"},
                                                  "client": None})(), "search")
        assert r1 == "chat:1.2.3.4"
        assert r2 == "search:1.2.3.4"


class TestSearchBounds:
    def test_top_k_zero_rejected(self):
        r = client.post("/api/documents/search", json={"query": "x", "top_k": 0})
        assert r.status_code == 422

    def test_top_k_negative_rejected(self):
        """回归：top_k=-1 曾让 retrieve 切片返回近全库。"""
        r = client.post("/api/documents/search", json={"query": "x", "top_k": -1})
        assert r.status_code == 422

    def test_top_k_over_20_rejected(self):
        r = client.post("/api/documents/search", json={"query": "x", "top_k": 21})
        assert r.status_code == 422

    def test_empty_query_rejected(self):
        r = client.post("/api/documents/search", json={"query": "", "top_k": 5})
        assert r.status_code == 422
