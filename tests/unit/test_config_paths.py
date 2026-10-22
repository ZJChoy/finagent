"""多实例隔离契约（refactor-log M1.2）。

回归背景：RAW_DIR 硬编码 data/raw，第二实例（DATA_DIR=data-announce）上传
的 PDF 落进主实例语料目录；MySQL + 非默认 DATA_DIR 时元数据共写一张表
（实测公告实例入库撞主实例主键才暴露）。
"""
import pytest

from backend.config import BASE_DIR, Settings


def _make(**env) -> Settings:
    s = Settings()
    for k, v in env.items():
        setattr(s, k, v)
    return s


class TestRawDirFollowsDataDir:
    def test_default(self):
        s = _make(DATA_DIR=BASE_DIR / "data")
        assert s.RAW_DIR == BASE_DIR / "data" / "raw"
        assert s.is_default_data_dir is True

    def test_second_instance(self):
        s = _make(DATA_DIR=BASE_DIR / "data-announce")
        assert s.RAW_DIR == BASE_DIR / "data-announce" / "raw"
        assert s.is_default_data_dir is False


class TestMysqlMultiInstanceGuard:
    def test_mysql_non_default_dir_rejected(self):
        s = _make(DATA_DIR=BASE_DIR / "data-announce", DB_BACKEND="mysql",
                  MYSQL_DATABASE="finagent")
        with pytest.raises(RuntimeError, match="多实例隔离"):
            s.ensure_dirs()

    def test_mysql_default_dir_ok(self, tmp_path):
        s = _make(DATA_DIR=BASE_DIR / "data", DB_BACKEND="mysql")
        s.ensure_dirs()  # 不抛

    def test_sqlite_non_default_dir_ok(self, tmp_path):
        s = _make(DATA_DIR=tmp_path, DB_BACKEND="sqlite")
        s.ensure_dirs()  # sqlite 的 DB_PATH 随 DATA_DIR 走，天然隔离
        assert (tmp_path / "raw").is_dir()
