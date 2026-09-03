"""
从快照重建 K 线时，前收必须自洽（2026-09-03）。

## 病灶

`_snapshots_to_klinebars` 会重算 is_limit_up，初衷是修正历史快照里旧逻辑的浮点漏判。
但它用**前一根 bar 的收盘价**当前收——而快照只在股票进候选池那天才写，序列有缺口
是常态（实测覆盖率 94.3%）。拿缺口另一侧的收盘价当前收，算出来的涨停价必然是错的，
于是这段"来修错的"代码**把本来正确的存储值改成了错的**。

## 实测案例

603989 艾华集团：06-12、06-15 无快照行。06-16 真实 +10.01%、快照存 is_limit_up=t
（对的），重算后变成 False。连板链从 06-17 才起算，board_count_60d 由 4 掉到 3，
于是它被踢出收窄后的强势池——而东财连板天梯和腾讯 K 线都确认那天是涨停。

## 修法

前收从这一行自己的 close 和 pct_change 反推：`prev = close / (1 + pct/100)`。
两者来自同一行、同一数据源，天然自洽，跟前后有没有缺口无关。

## 影响面

board_count_60d 同时喂着强势池筛选、涨停板块雷达核心召回、破局雷达高度曲线；
limit_up_days_60d/20d/10d 按同一窗口数。任何快照有缺口的股票都会被系统性低估。
"""
import importlib.util
from datetime import date
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "_du_prevclose", Path(__file__).resolve().parents[1] / "scripts" / "daily_update.py")
du = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(du)


class _Snap:
    def __init__(self, d, close, pct, is_lu=False, is_ld=False,
                 o=None, h=None, lo=None):
        self.date, self.close_price, self.pct_change = d, close, pct
        self.is_limit_up, self.is_limit_down = is_lu, is_ld
        self.open_price, self.high_price, self.low_price = o, h, lo
        self.turnover_rate = None
        self.is_broken_board = False
        self.is_one_word_limit_up = self.is_one_word_limit_down = False


def _streak(bars):
    run = mx = 0
    for b in bars:
        run = run + 1 if b.is_limit_up else 0
        mx = max(mx, run)
    return mx


class TestPrevCloseFromOwnRow:
    def test_快照有缺口时不再翻转正确的涨停标志(self):
        """603989 的真实形态：06-12/06-15 缺行，06-16 是 +10.01% 的真涨停。"""
        snaps = [
            _Snap(date(2026, 6, 11), 31.18, 0.50),
            # 06-12、06-15 无快照行 ← 缺口
            _Snap(date(2026, 6, 16), 34.30, 10.01, is_lu=True),
            _Snap(date(2026, 6, 17), 37.73, 10.00, is_lu=True),
            _Snap(date(2026, 6, 18), 41.50, 9.99, is_lu=True),
            _Snap(date(2026, 6, 22), 45.65, 10.00, is_lu=True),
        ]
        bars = du._snapshots_to_klinebars(snaps, "603989", False)
        by_date = {b.date: b for b in bars}
        assert by_date[date(2026, 6, 16)].is_limit_up is True, \
            "缺口后第一根被判成非涨停，正是 board_count_60d 由 4 掉到 3 的原因"
        assert _streak(bars) == 4, "四天连续涨停必须数出 4 板"

    def test_没有缺口时行为不变(self):
        snaps = [
            _Snap(date(2026, 6, 16), 34.30, 10.01, is_lu=True),
            _Snap(date(2026, 6, 17), 37.73, 10.00, is_lu=True),
        ]
        assert _streak(du._snapshots_to_klinebars(snaps, "603989", False)) == 2

    def test_仍然修正旧逻辑的浮点漏判(self):
        """这段重算的初衷不能丢：存的是 False 但实际到了涨停价，要能纠回来。"""
        snaps = [_Snap(date(2026, 6, 17), 37.73, 10.00, is_lu=False)]
        bars = du._snapshots_to_klinebars(snaps, "603989", False)
        assert bars[0].is_limit_up is True, "37.73 = round(34.30×1.10,2)，是真涨停"

    def test_pct缺失时保留存储值而不是拿假前收翻转(self):
        """算不出前收就别猜——保留当初落库的判定，比用一个编出来的前收强。"""
        snaps = [_Snap(date(2026, 6, 17), 37.73, None, is_lu=True)]
        bars = du._snapshots_to_klinebars(snaps, "603989", False)
        assert bars[0].is_limit_up is True

    def test_创业板20cm用自己的档位(self):
        """300 开头是 20% 档，10% 的涨幅不该被判成涨停。"""
        snaps = [_Snap(date(2026, 6, 17), 11.0, 10.0, is_lu=False)]
        assert du._snapshots_to_klinebars(snaps, "300001", False)[0].is_limit_up is False
        snaps20 = [_Snap(date(2026, 6, 17), 12.0, 20.0, is_lu=False)]
        assert du._snapshots_to_klinebars(snaps20, "300001", False)[0].is_limit_up is True

    def test_炸板判定也用自洽前收(self):
        """缺口处的错前收同样会让炸板判错，两处必须用同一个值。"""
        snaps = [
            _Snap(date(2026, 6, 11), 31.18, 0.50),
            _Snap(date(2026, 6, 16), 33.00, 5.84, o=32.0, h=34.30, lo=31.5),
        ]
        bars = du._snapshots_to_klinebars(snaps, "603989", False)
        b = bars[-1]
        assert b.is_limit_up is False and b.is_broken_board is True, \
            "最高价触及涨停价、收盘没封住 = 炸板"


class TestOneDirectionalCorrection:
    """
    重算只能**单向**修正：False → True，绝不 True → False（2026-09-03 第二次修）。

    第一次修（前收从本行 pct 反推）解决了"缺口处取到错误前收"，但引入了新问题：
    pct_change 只存两位小数，反推的前收带 ~0.002 误差，round 到分再放大就跨过
    0.005 容差——

        603773 08-04  收71.43  存 pct=9.99（真实 9.9938）
          反推前收 71.43/1.0999 = 64.9422 → round(64.9422×1.10, 2) = 71.44
          真实前收 64.94              → round(64.94  ×1.10, 2) = 71.43 = 收盘价
          边界差一分钱，判定就翻

    两种反推（前一根 bar / 本行 pct）都不可能精确，而**存储值是当时用真前收算的，
    比任何事后反推都准**。这段代码的声明目的又只是"修正浮点取整**漏判**"——
    漏判 = 该 True 却存了 False。所以：能补则补，不动已有。
    """

    def test_边界上不把正确的True改成False(self):
        """603773 沃格光电的真实形态：四天连板，第一天在容差边界上。"""
        snaps = [
            _Snap(date(2026, 8, 4), 71.43, 9.99, is_lu=True),
            _Snap(date(2026, 8, 5), 78.57, 10.00, is_lu=True),
            _Snap(date(2026, 8, 6), 86.43, 10.00, is_lu=True),
            _Snap(date(2026, 8, 7), 95.07, 10.00, is_lu=True),
        ]
        bars = du._snapshots_to_klinebars(snaps, "603773", False)
        assert bars[0].is_limit_up is True, \
            "反推前收算出 71.44 > 收盘 71.43，但存储值 True 是对的，不能被翻掉"
        assert _streak(bars) == 4

    def test_跌停同样只单向补(self):
        snaps = [_Snap(date(2026, 8, 4), 9.0, -10.0, is_ld=True)]
        assert du._snapshots_to_klinebars(snaps, "600001", False)[0].is_limit_down is True

    def test_炸板标志不会被抹掉(self):
        """涨停判定只补不删，炸板也不能反过来把已知的炸板改回 False。"""
        snaps = [_Snap(date(2026, 8, 4), 70.00, 5.00, o=69.0, h=71.43, lo=68.0)]
        snaps[0].is_broken_board = True
        assert du._snapshots_to_klinebars(snaps, "603773", False)[0].is_broken_board is True
