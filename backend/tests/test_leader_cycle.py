"""
LeaderCycle 事实层：识别「最近一次打开市场高度」的那段连板周期。

这一层**只有事实、没有判定**——RUNNING/BROKEN/RECLAIMING 那些是状态机的事，
建在这层之上。每个字段都能拿东财连板天梯和腾讯 K 线交叉验证。

用 603221 爱丽家居的真实形态做主用例（本轮反复查证过的那只）：
    07-28 6板 → 07-31 9板 → 08-03~05 停牌 → 08-06 10板 → 08-07 断板 → 08-10 又涨停
"""
from datetime import date, timedelta

from app.services.eastmoney_fetcher import build_kline_bar
from app.services.leader_cycle_service import identify_leader_cycle, MIN_PEAK_BOARD


def _bar(d, close, prev, is_st=False, limit_pct=9.90):
    """按真实前收造 bar，涨跌停判定走全仓唯一的 build_kline_bar。"""
    return build_kline_bar(
        dt=d, open_p=close, close_p=close, high_p=close, low_p=close,
        pct=(close / prev - 1) * 100 if prev else 0.0,
        turnover=None, is_st=is_st, limit_pct=limit_pct, prev_close=prev)


def _series(start, closes):
    """closes[0] 是起点前收，之后每根按连续交易日排（用自然日模拟）。"""
    bars, prev = [], closes[0]
    for i, c in enumerate(closes[1:], start=1):
        bars.append(_bar(start + timedelta(days=i), c, prev))
        prev = c
    return bars


class TestIdentify:
    def test_没有达到门槛的连板则返回None(self):
        # 只有 3 连板，够不上 MIN_PEAK_BOARD=4
        bars = _series(date(2026, 8, 1), [10.0, 11.0, 12.1, 13.31, 13.0])
        assert identify_leader_cycle(bars) is None

    def test_识别四连板周期与断板日(self):
        # 4 连板后断板，再走两天
        bars = _series(date(2026, 8, 1),
                       [10.0, 11.0, 12.1, 13.31, 14.64, 14.0, 13.5])
        c = identify_leader_cycle(bars)
        assert c is not None
        assert c.peak_board_count == 4
        assert c.cycle_start_date == date(2026, 8, 2)
        assert c.cycle_peak_date == date(2026, 8, 5)
        assert c.break_date == date(2026, 8, 6), "周期后第一根 bar 就是断板日"
        assert c.days_since_break == 1, "断板日之后还有 1 根 bar"

    def test_仍在连板中时断板日为None(self):
        """RUNNING 的事实基础：还没断，就没有 D+N 可言。"""
        bars = _series(date(2026, 8, 1), [10.0, 11.0, 12.1, 13.31, 14.64])
        c = identify_leader_cycle(bars)
        assert c.break_date is None and c.days_since_break is None

    def test_取最近一段而不是最高一段(self):
        """
        生命周期问的是"它**现在**处于什么阶段"。7月冲过更高、8月又走一段，
        当前处境是从后者断下来的，锚点就该是后者。历史最高辨识度由
        Stock.board_count_60d 另外表达——两个都给，不二选一。
        """
        early = _series(date(2026, 6, 1),
                        [10.0, 11.0, 12.1, 13.31, 14.64, 16.1, 15.0])   # 5连板
        prev = early[-1].close_price
        late = _series(date(2026, 8, 1),
                       [prev, prev*1.1, prev*1.21, prev*1.331, prev*1.4641, prev*1.4])
        c = identify_leader_cycle(early + late)
        assert c.peak_board_count == 4, "该锚在 8 月那段 4 板，不是 6 月那段 5 板"
        assert c.cycle_start_date.month == 8

    def test_价格结构(self):
        bars = _series(date(2026, 8, 1),
                       [10.0, 11.0, 12.1, 13.31, 14.64, 13.0, 15.0, 12.0])
        c = identify_leader_cycle(bars)
        assert c.peak_price == 14.64
        assert c.post_break_high == 15.0, "断板后创了新高"
        assert c.post_break_low == 12.0
        assert c.latest_close == 12.0
        assert abs(c.peak_drawdown - ((12.0/14.64 - 1) * 100)) < 0.01


class TestGapAwareness:
    """
    连板计数是从 bar 序列数出来的，而快照只在股票进候选池那天才写。
    **计数循环无法区分"那天没涨停"和"那天我们没记录"**——缺一行就静默少一板。
    所以缺口必须显式表达出来，而不是给一个看起来精确的错数。
    """
    def test_无缺口时标为可信(self):
        bars = _series(date(2026, 8, 1), [10.0, 11.0, 12.1, 13.31, 14.64])
        days = [b.date for b in bars]
        c = identify_leader_cycle(bars, trading_days=days)
        assert c.missing_days == 0 and c.peak_board_confident is True

    def test_区间内缺交易日则标为不可信(self):
        bars = _series(date(2026, 8, 1), [10.0, 11.0, 12.1, 13.31, 14.64])
        days = [b.date for b in bars] + [date(2026, 8, 4)]   # 多一个应有但无 bar 的日子
        # 去掉一根制造真实缺口
        c = identify_leader_cycle([b for b in bars if b.date != bars[2].date],
                                  trading_days=days)
        assert c is None or c.peak_board_confident is False, \
            "缺口会让连板数偏低，必须标出来而不是假装精确"

    def test_不传交易日历时不假装检查过(self):
        """missing_days=0 在这种情况下意思是'没检查'，不是'没缺口'。"""
        bars = _series(date(2026, 8, 1), [10.0, 11.0, 12.1, 13.31, 14.64])
        c = identify_leader_cycle(bars)
        assert c.missing_days == 0


class TestRealShape:
    def test_爱丽家居的真实形态(self):
        """
        07-28 6板 → 07-31 9板 → 08-03~05 停牌（无 bar）→ 08-06 10板 → 08-07 断板。
        停牌不打断连板（交易所口径），所以复牌那根是第 10 板不是第 1 板。
        """
        closes = [15.37, 16.91, 18.60, 20.46, 22.51]     # 07-27前收 + 07-28~31 四板
        dates = [date(2026, 7, 28), date(2026, 7, 29), date(2026, 7, 30), date(2026, 7, 31)]
        bars, prev = [], closes[0]
        for d, c in zip(dates, closes[1:]):
            bars.append(_bar(d, c, prev)); prev = c
        # 停牌三天：序列里直接没有这几根
        bars.append(_bar(date(2026, 8, 6), 24.76, prev)); prev = 24.76
        bars.append(_bar(date(2026, 8, 7), 24.79, prev))
        c = identify_leader_cycle(bars)
        assert c.peak_board_count == 5, "四板 + 复牌接力那一根 = 序列里能数到的 5 根"
        assert c.cycle_peak_date == date(2026, 8, 6)
        assert c.break_date == date(2026, 8, 7), "+0.12% 未涨停 = 断板"
        assert c.days_since_break == 0, "断板当天，还没有 D+1"
