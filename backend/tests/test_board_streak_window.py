"""
「近60日最高连板」的窗口口径——2026-09-04 生产实测出来的错。

603065 宿迁联盛 06-03~06-12 连 6 板，到 09-04 时这段的起点在 63 个交易日前、终点
在 60 个交易日内。旧实现 `bars[-60:]` 从中间把这段切开，数出 2 板：

    board_count_60d = 2   ← screening_service，手写循环 + bars[-60:]
    peak_board_count = 6  ← identify_leader_cycle，扫全段

同一批 bar、同一个 is_limit_up 字段，两个数字。而 2 这个数不描述任何真实的东西：
既不是这只票的连板高度，也不是任何一段真实的连板——**被切出来的残段不是一段更短
的连板**。它还解释了强势池那批"口径不符"：东财报 >=4、我们报 <4，不是对方口径松，
是我们在窗口边界上把段切碎了，方向恰好是系统性低估。
"""
from datetime import date, timedelta

from app.services.eastmoney_fetcher import (
    board_streaks, build_kline_bar, max_board_in_window)
from app.services.leader_cycle_service import identify_leader_cycle
from app.services.screening_service import compute_window_stats

D0 = date(2026, 1, 5)


def _bar(i, *, up=False, down=False):
    """i 天后的一根 bar；up=涨停，down=跌停，否则平盘。"""
    pct = 10.0 if up else (-10.0 if down else 0.0)
    prev = 10.0
    close = round(prev * (1 + pct / 100), 2)
    return build_kline_bar(dt=D0 + timedelta(days=i), open_p=close, close_p=close,
                           high_p=close, low_p=close, pct=pct, turnover=None,
                           limit_pct=9.90, prev_close=prev)


def _series(pattern):
    """pattern 是 'U'(涨停)/'D'(跌停)/'.'(平) 组成的字符串。"""
    return [_bar(i, up=c == "U", down=c == "D") for i, c in enumerate(pattern)]


def _stats(bars):
    return compute_window_stats("600001", "测试", False, bars)


class TestStraddlingWindow:

    def test_横跨窗口边界的连板段算整段而不是残段(self):
        """宿迁联盛的形态：6 连板，其中 4 板落在 60 日窗口之外。"""
        bars = _series("." * 3 + "U" * 6 + "." * 58)      # 共 67 根
        assert len(bars) == 67
        got, truncated = max_board_in_window(bars, 60)
        assert got == 6, "旧实现在这里得到 2 —— 那是被边界切剩的残段"
        assert truncated is False, "段起点没顶到 bars[0]，这个数是完整的"

    def test_与生命周期识别给出同一个数(self):
        """两条路径必须对同一批 bar 得出同一个连板高度，否则页面自相矛盾。"""
        bars = _series("." * 3 + "U" * 6 + "." * 58)
        cyc = identify_leader_cycle(bars)
        stats = _stats(bars)
        assert cyc is not None
        assert stats.board_count_60d == cyc.peak_board_count == 6

    def test_整段都在窗口之前的不算(self):
        """交集才算整段。完全落在窗口外的旧连板不能算进"近60日"。"""
        bars = _series("U" * 6 + "." * 70)
        got, _ = max_board_in_window(bars, 60)
        assert got == 0

    def test_段顶到数据边界时标为可能偏低(self):
        """
        bars[0] 就是涨停 → 更早的涨停压根没拉进来，这个数只是下界。
        不知道就说不知道，不给一个看起来精确的错数。
        """
        bars = _series("U" * 3 + "." * 58)
        got, truncated = max_board_in_window(bars, 60)
        assert got == 3 and truncated is True
        assert _stats(bars).board_count_60d_truncated is True

    def test_跌停段同样按整段算(self):
        bars = _series("." * 3 + "D" * 5 + "." * 58)
        got, _ = max_board_in_window(bars, 60, down=True)
        assert got == 5
        assert _stats(bars).board_down_count_60d == 5

    def test_当前连板数从最后一根往回数不受窗口影响(self):
        bars = _series("." * 60 + "U" * 3)
        assert _stats(bars).board_count_current == 3

    def test_没有涨停时是0不是空(self):
        assert max_board_in_window(_series("." * 70), 60) == (0, False)
        assert max_board_in_window([], 60) == (0, False)


class TestBoardStreaks:

    def test_切段给出下标和长度(self):
        assert board_streaks(_series(".UU.UUU.")) == [(1, 2, 2), (4, 6, 3)]

    def test_序列以涨停结尾时最后一段也要收口(self):
        assert board_streaks(_series("..UU")) == [(2, 3, 2)]
