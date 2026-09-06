"""
生命周期评估 harness 的护栏。

这个工具的危险之处在于**它很容易自证**：随手一算就能得出"修复中 T+5 平均
+3%，有 Edge"，而同期整个池子可能平均 +4%。所以这里测的不是"算得对不对"，
是"会不会骗人"。

四条护栏，每条都对应一种具体的自证方式：

  基线      没有同期对照的绝对收益毫无意义
  完整窗口  用"目前为止"的收益顶替 T+10，会系统性偏向近期，而近期样本最多
  交易日历  T+N 必须按交易日数（这个仓库今天为这条栽过三次）
  不打分    自动判定"有没有 Edge"等于又造一个黑箱
"""
from datetime import date, timedelta

import pytest

from scripts.evaluate_lifecycle import Bars, HORIZONS

D0 = date(2026, 6, 1)
CAL = [D0 + timedelta(days=i) for i in range(20)]


class _Row:
    def __init__(self, d, close, high=None, low=None, lu=False):
        self.date = d
        self.close_price = close
        self.high_price = high if high is not None else close
        self.low_price = low if low is not None else close
        self.is_limit_up = lu


class TestForwardWindow:

    def _bars(self, n=10, **kw):
        return Bars([_Row(CAL[i], 10.0 + i) for i in range(n)], CAL)

    def test_窗口不完整返回None(self):
        """
        用"目前为止"的收益顶替 T+10 会系统性偏向近期——而近期正好是样本最多的
        那段，偏差会被放大而不是抵消。
        """
        b = self._bars(n=5)                       # 只有 CAL[0..4]
        assert b.forward(CAL[0], 3) == CAL[1:4]
        assert b.forward(CAL[3], 3) is None, "只剩 1 天，不能给 3 天的窗口"
        assert b.forward(CAL[4], 1) is None

    def test_中间缺一天也算不完整(self):
        """缺的那天没有收盘价，区间收益和 MFE/MAE 都不该算。"""
        rows = [_Row(CAL[i], 10.0 + i) for i in range(10) if i != 2]
        b = Bars(rows, CAL)
        assert b.forward(CAL[0], 1) == [CAL[1]]
        assert b.forward(CAL[0], 3) is None, "窗口里 CAL[2] 缺数据"

    def test_按交易日历数不按数组下标(self):
        """
        日历里有的日子、这只股票没有 bar —— T+1 必须落在日历的下一个交易日上，
        而不是"这只股票的下一根 bar"。
        """
        rows = [_Row(CAL[0], 10.0), _Row(CAL[3], 13.0)]
        b = Bars(rows, CAL)
        assert b.forward(CAL[0], 1) is None, \
            "日历上的 T+1 是 CAL[1]，这只票那天没数据——不能拿 CAL[3] 冒充"

    def test_锚点不在日历上时不猜(self):
        b = self._bars()
        assert b.forward(date(2020, 1, 1), 1) is None


class TestHarnessContract:

    def test_必须有基线组(self):
        """
        「修复中 T+5 +3%」单独看毫无意义，同期池子可能 +4%。基线不是可选项。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "ALL_STOCK_DAYS" in src
        assert "超额" in src, "必须输出相对基线的超额，不能只给绝对收益"

    def test_不产出评分或结论(self):
        """自动判定'有没有 Edge'的评估器，等于又造一个黑箱。"""
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        for word in ("score", "edge_score", "推荐", "建议买"):
            assert word not in src.lower() or word == "score" and "scripts" in src
        assert "不给结论也不打分" in src

    def test_幸存者偏差必须打印出来(self):
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "幸存者偏差" in src and "绝对收益都偏高" in src

    def test_横轴覆盖多个时间尺度(self):
        """单一 horizon 容易被挑选。至少要能看到形状。"""
        assert len(HORIZONS) >= 3 and 1 in HORIZONS and 10 in HORIZONS
