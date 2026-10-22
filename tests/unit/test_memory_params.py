"""记忆策略参数的边界契约（refactor-log 补记）。

回归：memory_keep_recent=0 是合法配置（全部压缩），但 `0 or KEEP_RECENT`
会把用户设置吞掉换成默认 6——配置页允许 0，行为却无效。
"""
from finagent.core.memory import SessionMemory


class _DB:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_config(self):
        return self._cfg


def _mem(cfg):
    return SessionMemory(db=_DB(cfg), llm_mod=None, rewrite_model="x")


BASE = {"memory_enabled": True, "memory_keep_recent": 6,
        "memory_summarize_at": 12, "memory_extract_every": 3}


class TestMemoryParams:
    def test_zero_keep_recent_respected(self):
        cfg = {**BASE, "memory_keep_recent": 0}
        enabled, keep, at, every = _mem(cfg)._params()
        assert enabled is True
        assert keep == 0, "keep_recent=0 必须原样生效，不能被默认值吞掉"
        assert at == 12

    def test_none_falls_back_to_default(self):
        cfg = {**BASE, "memory_keep_recent": None, "memory_summarize_at": None}
        _, keep, at, _ = _mem(cfg)._params()
        assert keep == 6 and at == 12

    def test_zero_summarize_at_falls_back(self):
        """summarize_at=0 无业务意义（钳制下限是 4），退回默认。"""
        cfg = {**BASE, "memory_summarize_at": 0}
        _, _, at, _ = _mem(cfg)._params()
        assert at == 12

    def test_disabled(self):
        cfg = {**BASE, "memory_enabled": False}
        assert _mem(cfg)._params()[0] is False
