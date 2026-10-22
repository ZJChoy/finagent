"""向量存储的一致性契约（refactor-log M1.1）。

回归背景（review 实测复现）：
- RETRIEVAL_INDEX=llmctx 时上传新文档 → vectors.npy 长了、vectors_llmctx.npy
  没跟上 → search IndexError（被 preflight 吞掉变成"检索静默失效"）；
- 删除文档 → row_id 重排，变体文件没重排 → 向量与文本静默错配，检索正常
  返回但内容是错的。
契约：写入路径只碰 base；active_vector_path() 必须校验变体行数与元数据
一致，不一致时**回退 base 并标记 stale**——宁可质量退化，不可静默错答。
"""
import numpy as np
import pytest

from finagent.core.retriever import store


@pytest.fixture()
def index_env(tmp_path, monkeypatch):
    """把 store 的索引目录指到临时路径；sqlite 库也落临时路径（row_id 隔离）。"""
    from backend import dbconn
    from backend.config import settings as app_settings

    monkeypatch.setattr(store, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(store, "VEC_PATH", tmp_path / "vectors.npy")
    # 元数据在共享库的 rag_chunks 表里，row_id 必须每个用例独立，否则串台
    monkeypatch.setattr(app_settings, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(dbconn, "IS_MYSQL", False)
    store.init()
    yield tmp_path


def _add(name):
    store.add_document(name, [{"page": 1, "seq": 0, "text": f"{name} 正文"}],
                       [[1.0] * store.DIM], "text-embedding-v4")


class TestBaseWritePath:
    def test_add_and_count(self, index_env):
        _add("A.pdf")
        _add("B.pdf")
        assert store.count() == 2
        assert store.load_vectors(store.VEC_PATH).shape == (2, store.DIM)

    def test_delete_compacts_rows(self, index_env):
        _add("A.pdf")
        _add("B.pdf")
        store.delete_document("A.pdf")
        assert store.count() == 1
        assert store.load_vectors(store.VEC_PATH).shape == (1, store.DIM)


class TestVariantStaleness:
    def test_stale_variant_falls_back_to_base_after_add(self, index_env):
        """上传新文档后，旧变体行数不匹配 → 必须回退 base，绝不越界/错配。"""
        _add("A.pdf")
        _add("B.pdf")
        stale = store.load_vectors(store.VEC_PATH).copy()
        (index_env / "vectors_llmctx.npy").write_bytes((index_env / "vectors.npy").read_bytes())

        _add("C.pdf")  # base 3 行，llmctx 变体仍是 2 行
        assert store.count() == 3
        active = store.active_vector_path()
        assert active == store.VEC_PATH, "变体已 stale，必须回退 base"
        hits = store.search([1.0] * store.DIM, top_k=3)  # 曾在此处 IndexError
        assert hits and len(hits) <= 3

        assert stale.shape == (2, store.DIM)  # 供读者对照：变体落后一行

    def test_stale_variant_falls_back_after_delete(self, index_env):
        """删除文档后 row_id 重排、旧变体未重排 → 回退 base，不错配。"""
        _add("A.pdf")
        _add("B.pdf")
        (index_env / "vectors_llmctx.npy").write_bytes((index_env / "vectors.npy").read_bytes())
        store.delete_document("A.pdf")
        assert store.active_vector_path() == store.VEC_PATH
        # 旧变体行数碰巧等于新行数（2→1 之后是 1：不匹配才回退；再造一个"行数相同但内容错位"的场景）
        np.save(index_env / "vectors_llmctx.npy", np.zeros((1, store.DIM), dtype=np.float32))
        hits = store.search([1.0] * store.DIM, top_k=1)
        assert hits[0]["doc"] == "B.pdf"

    def test_reset_invalidates_variants(self, index_env):
        """reset 后旧变体必须被视为失效，否则"换模型重建索引"会拿旧向量检索。"""
        _add("A.pdf")
        (index_env / "vectors_llmctx.npy").write_bytes((index_env / "vectors.npy").read_bytes())
        store.reset()
        _add("NEW.pdf")
        assert store.active_vector_path() == store.VEC_PATH

    def test_variant_with_matching_fingerprint_still_active(self, index_env):
        """变体文件 + 指纹侧车一致 → 继续生效（正常路径不被误伤）。"""
        _add("A.pdf")
        # 模拟外部脚本构建变体后登记指纹（scripts/contextual_index.py 的职责）
        fname = store.VARIANTS["llmctx"]
        (index_env / fname).write_bytes((index_env / "vectors.npy").read_bytes())
        store._write_variant_fingerprint(fname, store._base_fingerprint())
        assert store.active_vector_path() != store.VEC_PATH
        # 任何写入（增/删）之后指纹过期 → 回退 base
        _add("B.pdf")
        assert store.active_vector_path() == store.VEC_PATH


class TestHealthFacts:
    def test_health_fields(self, index_env):
        _add("A.pdf")
        (index_env / "vectors_llmctx.npy").write_bytes((index_env / "vectors.npy").read_bytes())
        _add("B.pdf")  # 变体落后
        facts = store.index_health()
        assert facts["meta_rows"] == 2
        assert facts["base_rows"] == 2
        assert facts["active_variant"] == "base"
        assert "llmctx" in facts["stale_variants"]
