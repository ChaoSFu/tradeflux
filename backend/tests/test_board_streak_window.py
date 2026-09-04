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


class TestGapBreaksStreak:
    """
    2026-09-04 生产实测，同一个错的第三次现形：**数组下标相邻 ≠ 交易日相邻**。

    603065 宿迁联盛被数成 6 连板，补齐快照空洞之后真实序列是

        06-03 涨停  06-04 +3.46%  06-05 涨停  06-08 -1.05%
        06-09 涨停  06-10 涨停    06-11 +8.04%  06-12 涨停

    最长连板是 **2**。数成 6，是因为那几个**非涨停日在库里没有行**——它们一
    消失，五个涨停日在数组里就挨在了一起。

    方向必须保守：宁可少算连板，也不要凭空造出一段不存在的连板。后者会把股票
    错误地送进高标池，还会抬高"市场投机高度"这个指标本身。
    """

    def _cal(self, n=20):
        return [D0 + timedelta(days=i) for i in range(n)]

    def _bars_on(self, offsets, ups):
        """在指定的日期偏移上造 bar；ups 里的偏移是涨停。"""
        return [_bar(i, up=(i in ups)) for i in offsets]

    def test_中间缺交易日就断开连板段(self):
        # 第 0、2 天涨停，第 1 天没有行 —— 不能算成 2 连板
        bars = self._bars_on([0, 2], ups={0, 2})
        segs = board_streaks(bars, calendar=self._cal())
        assert [s[2] for s in segs] == [1, 1], "中间那天不知道涨没涨停，不能算连续"

    def test_没有缺口时照常连成一段(self):
        bars = self._bars_on([0, 1, 2], ups={0, 1, 2})
        assert board_streaks(bars, calendar=self._cal()) == [(0, 2, 3)]

    def test_复刻宿迁联盛(self):
        """五个涨停日散落在八个交易日里，最长连板是 2 不是 5。"""
        ups = {0, 2, 4, 5, 7}
        bars = self._bars_on(sorted(ups), ups=ups)   # 只有涨停日有行（原始状态）
        assert max(s[2] for s in board_streaks(bars)) == 5, \
            "不给日历时按数组相邻数，正是这个错误产生 6 板的机制"
        assert max(s[2] for s in board_streaks(bars, calendar=self._cal())) == 2, \
            "给了日历，只有第 4、5 天是真正相邻的，那一对才算 2 板"
        # 补齐非涨停日之后结果不变 —— 这才是"日历判定"该有的性质：
        # 连板数只取决于真实的交易日相邻关系，跟库里缺不缺行无关
        full = self._bars_on(list(range(8)), ups=ups)
        assert max(s[2] for s in board_streaks(full, calendar=self._cal())) == 2

    def test_窗口内最高连板也跟着修正(self):
        ups = {0, 2, 4, 5, 7}
        full = self._bars_on(list(range(8)), ups=ups)
        got, _ = max_board_in_window(full, 60, calendar=self._cal())
        assert got == 2

    def test_不传日历退回旧行为(self):
        """旧行为只在确知序列无空洞时才对，但不能让缺参数直接报错。"""
        bars = self._bars_on([0, 2], ups={0, 2})
        assert [s[2] for s in board_streaks(bars)] == [2]
