"""配置接口的边界钳制（backend/api/config.py::_sanitize）。"""
from backend.api.config import _BOUNDS, _sanitize


class TestSanitize:
    def test_in_range_untouched(self):
        patch, notices = _sanitize({"memory_keep_recent": 6})
        assert patch["memory_keep_recent"] == 6
        assert notices == []

    def test_out_of_range_clamped(self):
        patch, notices = _sanitize({"memory_keep_recent": 999})
        assert patch["memory_keep_recent"] == _BOUNDS["memory_keep_recent"][1]
        assert notices

    def test_non_numeric_dropped(self):
        patch, notices = _sanitize({"memory_keep_recent": "abc"})
        assert "memory_keep_recent" not in patch
        assert notices

    def test_zero_is_respected(self):
        """回归：keep_recent=0 是合法配置（全部压缩），不得被吞掉。"""
        patch, _ = _sanitize({"memory_keep_recent": 0, "memory_summarize_at": 12})
        assert patch["memory_keep_recent"] == 0

    def test_summarize_at_must_exceed_keep(self):
        patch, notices = _sanitize({"memory_keep_recent": 10, "memory_summarize_at": 5})
        assert patch["memory_summarize_at"] > patch["memory_keep_recent"]
        assert notices
