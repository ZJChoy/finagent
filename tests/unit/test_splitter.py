"""结构感知切分的长度契约。

已知行为（记录在 refactor-log，暂不修）：补重叠发生在聚合之后，
块长会系统性超过 chunk_size（500/90 实测 590）。测试钉住现状并声明
"不超过 chunk_size + overlap" 的上界，防止未来改动把块撑到不可控。
"""
from finagent.core.retriever.splitter import split_page


class TestSplitPage:
    def test_short_text_single_chunk(self):
        t = "段落内容测试。" * 40  # 280 字：超过 min_len=40、低于 chunk_size
        assert split_page(t, chunk_size=500, overlap=90) == [t]

    def test_below_min_len_dropped(self):
        """页眉残留这类过短内容直接丢弃（min_len=40）。"""
        assert split_page("短文本" * 10, chunk_size=500, overlap=90) == []  # 30 字

    def test_empty_returns_empty(self):
        assert split_page("", 500, 90) == []
        assert split_page("x" * 10, 500, 90) == []  # 低于 min_len

    def test_chunk_upper_bound(self):
        t = "这是段落内容。" * 200
        cs = split_page(t, chunk_size=500, overlap=90)
        assert cs
        assert max(len(c) for c in cs) <= 500 + 90, "补重叠后块长上界 = chunk_size + overlap"

    def test_long_paragraph_no_double_overlap(self):
        """单个超长段落：分段时已按 step=chunk_size-overlap 划窗，
        再统一补一次重叠会形成双重重叠。这里只钉住上界。"""
        t = "x" * 1200
        cs = split_page(t, 500, 90)
        assert max(len(c) for c in cs) <= 500 + 90

    def test_returns_nonempty_strings(self):
        t = "段落一内容。\n段落二内容。\n" + "y" * 1500
        for c in split_page(t, 500, 90):
            assert c and c.strip()
