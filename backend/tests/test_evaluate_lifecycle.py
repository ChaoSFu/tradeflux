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
    def __init__(self, d, close, high=None, low=None, lu=False, open_p=None):
        self.date = d
        self.close_price = close
        self.open_price = open_p if open_p is not None else close
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


class TestNoSelfDeception:
    """
    2026-09-06 首跑抓到的三个问题，每个都会让这个工具说好话。
    """

    def test_发生比例用均值不用中位数(self):
        """
        0/1 列表的中位数只会是 0 / 0.5 / 1。首版就是这么把「5日再涨停」印成一列
        100% / 50% / 0%，而基线显示 0%——一眼就该看出荒谬，但它长得很像个比例。
        """
        from scripts.evaluate_lifecycle import _rate
        assert _rate([1.0, 0.0, 0.0, 0.0]).strip() == "25%"
        assert _rate([1.0, 1.0, 0.0]).strip() == "67%"
        assert _rate([]).strip() == "—"

    def test_从UNKNOWN转出不算转移(self):
        """
        一只票第一次出现可用快照时是 UNKNOWN→BROKEN。那不是转移，是"我们开始
        有记录了"。首版把它记成一次"转入 BROKEN"，样本和收益一起被污染。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert 's.previous_state in (None, "UNKNOWN")' in src

    def test_不在外面重造transition检测(self):
        """
        状态机自己已经输出 transitioned_today / previous_state。评估器再维护一套
        `cur != prev_state`，就等于"什么叫状态转移"有两个定义——这个仓库为
        「同一个事实两套判定」栽过 8 次，不差这第 9 次。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "s.transitioned_today" in src
        assert "cur != prev_state" not in src

    def test_事件按from到to分组(self):
        """
        BROKEN→REPAIRING（第一次转强）和 CROSS_FAILED→REPAIRING（失败后再修复）
        交易含义完全不同，混成一组「REPAIRING 68」会把信号稀释掉。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert 'f"{s.previous_state}→{s.state}"' in src

    def test_有平衡样本表(self):
        """
        T+1 含近期事件、T+10 只含更早的，两张表样本组成不同。
        「T+1 略负而 T+10 大负」不能解释成"持有越久越差"——除非在同一批样本上比。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "balanced" in src and "时间衰减" in src

    def test_每个指标单独给有效N(self):
        """事件数 68 不等于每一项都有 68：窗口、OHLC、同日对照各扣各的。"""
        from scripts.evaluate_lifecycle import _cell
        assert "(  3)" in _cell([1.0, 2.0, 3.0])
        assert _cell([]).strip() == "—"

    def test_OHLC不拿收盘顶替(self):
        """
        StockDailySnapshot 的 OHLC 是 2026-08-27 才加的，更早的行是 NULL。
        用收盘顶替会把「不知道盘中高低」变成「高低恰好等于收盘」，系统性压缩
        MFE/MAE。首版就是这么写的，而提交信息还声称"不拿收盘顶替"。
        """
        rows = [_Row(CAL[i], 10.0 + i) for i in range(5)]
        for r in rows:
            r.high_price = None
            r.low_price = None
            r.open_price = None
        b = Bars(rows, CAL)
        assert b.high == {} and b.low == {} and b.open == {}
        assert b.has_hl([CAL[1], CAL[2]]) is False
        assert b.next_session(CAL[0]) is None, "没有真实开盘价就没有可执行口径"

    def test_超额必须逐事件对同日同池比较(self):
        """
        两组中位数相减测的是行情差异，不是状态的信息量：事件集中在特定时段，
        基线摊在全部股票日上，两组根本不在同一段行情里。首跑 T+10 全线为负、
        基线却是 +1.0，很可能就是这么来的。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "cohort" in src and "同日同池" in src
        assert "len(peers) >= 3" in src, "同日样本太少时不该硬算对照"


class TestTradability:
    """
    **转入日收盘价往往买不到。** STREAKING 更是直接封在涨停板上，而它恰好是
    首跑里唯一正超额的一组——如果只按收盘口径看，会得出一个正确但无法执行的结论。
    """

    def test_次日开盘口径存在(self):
        rows = [_Row(CAL[i], 10.0 + i, open_p=9.0 + i) for i in range(10)]
        b = Bars(rows, CAL)
        assert b.next_session(CAL[0]) == CAL[1]
        assert b.open[CAL[1]] == 10.0, "开盘价要单独存，不能拿收盘顶替"

    def test_没有次日就没有可执行口径(self):
        rows = [_Row(CAL[i], 10.0 + i) for i in range(3)]
        b = Bars(rows, CAL)
        assert b.next_session(CAL[2]) is None, "买不进去就是买不进去，不猜"

    def test_次日缺数据时也算没有(self):
        rows = [_Row(CAL[0], 10.0), _Row(CAL[2], 12.0)]
        b = Bars(rows, CAL)
        assert b.next_session(CAL[0]) is None, \
            "日历上的次日是 CAL[1]，那天没数据——不能拿 CAL[2] 冒充"


class TestStatisticalHonesty:
    """
    2026-09-06 第二轮 review 抓到的三条，都是"数字看起来精确、语义却错了"。
    """

    def test_对照组剔掉事件股票自己(self):
        """
        事件股票自己也在池子里。把它算进中位数等于用它自己给自己当基准，
        会把超额往 0 拉。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "if c2 != code" in src
        assert "leave-one-out" in src or "剔掉事件股票自己" in src

    def test_可执行口径也要有超额(self):
        """
        实测 ALL_STOCK_DAYS 次日开盘 T+10 是 +2.5、收盘是 +1.0——**基线自己在
        开盘口径下就不是 0**。拿事件的绝对开盘收益去跟收盘收益比，方向都可能
        读反（BROKEN→REPAIRING 开盘 +1.1 看着是正的，其实跑输基线 +2.5）。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "cohort_op" in src and "opx" in src

    def test_可执行口径包含T加1(self):
        """
        次日开盘买、当天收盘卖——最贴近实际操作的一格。首版写了 if h > 1
        把它跳过了，而那正是最该看的。
        """
        import scripts.evaluate_lifecycle as m
        src = open(m.__file__, encoding="utf-8").read()
        assert "if nx is not None and h > 1" not in src

    def test_bootstrap按周期整段重抽(self):
        from scripts.evaluate_lifecycle import _cluster_bootstrap
        # 5 个 cluster、每个 2 个样本，全为正 → 区间应当整体在 0 以上
        vals = [1.0, 1.2] * 5
        cls = [(f"c{i}", None) for i in range(5) for _ in range(2)]
        ci = _cluster_bootstrap(vals, cls, n_boot=200)
        assert ci is not None and ci[0] > 0

    def test_样本或cluster太少时不给区间(self):
        """给一个假的区间比不给更糟。"""
        from scripts.evaluate_lifecycle import _cluster_bootstrap
        assert _cluster_bootstrap([1.0, 2.0], [("a", None)] * 2) is None
        # 20 个样本但全来自 2 只票 —— cluster 不够，同样不给
        assert _cluster_bootstrap([1.0] * 20, [("a", None)] * 10
                                  + [("b", None)] * 10) is None

    def test_同一只票的重复事件被当成一个cluster(self):
        """
        BROKEN→REPAIRING→FAILED→REPAIRING→SUCCESS 全出自一段行情，收益窗口还
        高度重叠。按独立样本算区间会严重高估把握。
        """
        from scripts.evaluate_lifecycle import _cluster_bootstrap
        # 6 个 cluster，其中一个贡献 10 个样本 —— 重抽时它要么整段进要么整段不进
        vals = [5.0] * 10 + [-1.0] * 5
        cls = [("hot", "c1")] * 10 + [(f"x{i}", "c") for i in range(5)]
        ci = _cluster_bootstrap(vals, cls, n_boot=300)
        assert ci is not None
        assert ci[0] < 0 < ci[1], "一个 cluster 主导时，区间必须宽到跨 0"


class TestJsonPayload:
    """
    界面读的是 `--json` 产物，不是 stdout 那张表。**这条通道有它自己的骗人
    方式**：把「没样本」写成 0、把算不出的区间写成 [0,0]、把免责声明留在终端
    里而数字自己跑到页面上。
    """

    def _payload(self, **over):
        from scripts.evaluate_lifecycle import _build_payload
        m = {
            "exc1": [1.0, 2.0, -1.0, 3.0, 2.5, -0.5, 4.0, 1.5, 0.5, 2.2],
            "exc1_cl": [("A", D0), ("A", D0), ("B", D0), ("C", D0), ("D", D0),
                        ("E", D0), ("F", D0), ("G", D0), ("H", D0), ("I", D0)],
            "exc3": [],                 # 一格都没有 → 必须是 None，不是 0
            "opx1": [0.5, -0.5],
            "mfe5": [7.0], "mae5": [-3.0],
        }
        m.update(over)
        full = {"BROKEN→REPAIRING": (12, m), "ALL_STOCK_DAYS": (900, m)}
        return _build_payload(full, {}, 33,
                              ["BROKEN→REPAIRING", "ALL_STOCK_DAYS"],
                              date(2026, 9, 7))

    def test_没样本的格子是None不是0(self):
        ev = self._payload()["events"][0]
        assert ev["excess"]["3"] is None, "「没有样本」不能写成 0——那是个观测"
        assert ev["excess"]["1"]["n"] == 10

    def test_每格带自己的n(self):
        ev = self._payload()["events"][0]
        assert ev["n_events"] == 12
        assert ev["excess"]["1"]["n"] == 10, "事件数 12 不等于每一项都有 12"
        assert ev["exec_excess"]["1"]["n"] == 2

    def test_算不出区间时给None不给假区间(self):
        ev = self._payload(exc1=[1.0, 2.0], exc1_cl=[("A", D0), ("A", D0)])
        e1 = ev["events"][0]["excess"]["1"]
        assert e1["ci"] is None and e1["crosses_zero"] is None, \
            "样本太少就是算不出来，给一个 [0,0] 比不给更糟"

    def test_跨0要标出来(self):
        e1 = self._payload()["events"][0]["excess"]["1"]
        assert e1["ci"] is not None
        assert e1["crosses_zero"] == (e1["ci"][0] * e1["ci"][1] <= 0)

    def test_免责声明跟数字一起走(self):
        p = self._payload()
        joined = "".join(p["caveats"])
        assert "幸存者" in joined or "进不了池" in joined, "幸存者偏差必须随数字走"
        assert "跨 0" in joined, "区间跨 0 的含义必须随数字走"
        assert "不打分" in joined or "不给结论" in joined

    def test_拆出from和to(self):
        ev = self._payload()["events"][0]
        assert (ev["from"], ev["to"]) == ("BROKEN", "REPAIRING"), \
            "BROKEN→REPAIRING 和 CROSS_FAILED→REPAIRING 交易含义完全不同"
        assert self._payload()["baseline"]["from"] is None

    def test_带上口径版本和生成时间(self):
        from scripts.evaluate_lifecycle import FORMULA_VERSION
        p = self._payload()
        assert p["formula_version"] == FORMULA_VERSION
        assert p["generated_at"] and p["as_of"] == "2026-09-07", \
            "离线产物不带生成时间，过期了没人看得出来"
