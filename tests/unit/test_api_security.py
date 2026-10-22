"""变更类接口的鉴权契约（refactor-log M0.5）。

回归背景：DELETE /api/conversations/{cid} 曾无鉴权——公网隧道下
任何人可枚举并删除全部会话。契约：所有变更类接口无 token / 错 token 均 403。
"""
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)
TOKEN = "test-admin-token"  # conftest.py 里注入的环境变量


class TestDeleteConversationAuth:
    def test_delete_without_token_403(self):
        assert client.delete("/api/conversations/1").status_code == 403

    def test_delete_with_wrong_token_403(self):
        r = client.delete("/api/conversations/1", headers={"X-Admin-Token": "wrong"})
        assert r.status_code == 403

    def test_delete_with_token_ok(self):
        from backend import db
        cid = db.create_conversation("t")
        r = client.delete(f"/api/conversations/{cid}", headers={"X-Admin-Token": TOKEN})
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestOtherChangeEndpoints:
    """其余变更类接口本就有鉴权，这里一并钉住，防回归。"""

    @pytest.mark.parametrize("method,path", [
        ("put", "/api/config"),
        ("post", "/api/memories"),
        ("delete", "/api/memories/1"),
        ("delete", "/api/memories"),
        ("delete", "/api/documents/some.pdf"),
        ("post", "/api/documents/reset"),
    ])
    def test_change_endpoints_require_token(self, method, path):
        fn = getattr(client, method)
        r = fn(path, **({"json": {}} if method in ("put", "post") else {}))
        assert r.status_code == 403, f"{method.upper()} {path} 未拦截匿名调用"

    @pytest.mark.parametrize("method,path", [
        ("put", "/api/config"),
        ("delete", "/api/memories"),
    ])
    def test_change_endpoints_accept_token(self, method, path):
        fn = getattr(client, method)
        r = fn(path, headers={"X-Admin-Token": TOKEN}, **({"json": {}} if method == "put" else {}))
        assert r.status_code == 200, f"{method.upper()} {path} 带正确 token 应放行"

    def test_read_endpoints_open(self):
        """查询类保持开放（演示定位，README 已声明）。"""
        assert client.get("/api/conversations").status_code == 200
