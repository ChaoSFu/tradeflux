"""
Price Lifecycle v1 状态机（Phase 1）。

这一层**只用价格结构**：break_date / close / ma5,10,20,30 / 断板后新高新低。
RS、量能、换手、板块、大盘、监管一概不参与——它们的覆盖率还没收口，现在做成
生命周期硬门槛就会变成「某字段有没有数据」决定「某股票处于什么状态」。

用例编号对应设计文档里的 Case A~N。最要紧的三组：

  J/K   数据不足时给 UNKNOWN，不拿 0 / False / 上一个值冒充
  L/M   「连续两个 observation」必须是真实 settled 交易日；停牌和数据缺口都不能
        凭空制造一次 below-MA observation，也不能跨过去假装连续
  N     历史查询 as_of=T 不许看 T 之后的行（look-ahead guard）
"""
from datetime import date, timedelta

import pytest

from app.services.leader_cycle_state_service import (
    BROKEN, CROSS_FAILED, CROSS_SUCCESS, CROSS_WEAKENING, FADED, FORMULA_VERSION,
    REPAIRING, STREAKING, UNKNOWN, replay_price_lifecycle,
)

D0 = date(2026, 6, 1)


class Row:
    """一行 LeaderCycleSnapshot 的最小替身——状态机只读这几个字段。"""

    def __init__(self, day, close, ma5=None, ma10=None, ma20=None, ma30=None,
                 break_date=D0, days_since_break=1, new_high=None, new_low=None,
                 fresh=True, settled=True, cycle_start=D0, cycle_peak=D0):
        self.date = day if isinstance(day, date) else D0 + timedelta(days=day)
        self.latest_close = close
        self.ma5, self.ma10, self.ma20, self.ma30 = ma5, ma10, ma20, ma30
        self.break_date = break_date
        self.days_since_break = days_since_break
        self.new_post_break_high_today = new_high
        self.new_post_break_low_today = new_low
        self.data_fresh = fresh
        self.bar_settled = settled
        self.cycle_start_date = cycle_start
        self.cycle_peak_date = cycle_peak


# 观测日历。Row(day=N) 落在 D0+N 天，所以日历就是 D0 起的连续自然日。
# 「连续两个 observation」的规则**只有拿到日历才会触发**——过滤掉不可用行之后
# 两行相邻，不等于两个交易日相邻
CAL = [D0 + timedelta(days=i) for i in range(45)]


def _replay(rows, as_of=None, cal=CAL):
    return replay_price_lifecycle(rows, as_of or rows[-1].date, trading_days=cal)


def _healthy(day, close, ma5, **kw):
    """站上 MA5/MA10、且均线不空排的一行。"""
    kw.setdefault("ma10", ma5 - 1)
    kw.setdefault("ma20", ma5 - 2)
    kw.setdefault("ma30", ma5 - 3)
    return Row(day, close, ma5=ma5, **kw)


class TestCaseA:
    def test_连板到断板到修复到穿越成功(self):
        rows = [
            Row(0, 20.0, ma5=18.0, ma10=17.0, ma20=16.0, ma30=15.0, break_date=None),
            Row(1, 18.0, ma5=18.5, ma10=17.5, ma20=16.5, ma30=15.5,
                days_since_break=0, new_high=None, new_low=None),
            _healthy(2, 19.5, 18.6, days_since_break=1, new_high=False, new_low=False),
            _healthy(3, 21.0, 19.2, days_since_break=2, new_high=True, new_low=False),
        ]
        assert _replay(rows[:1]).state == STREAKING
        assert _replay(rows[:2]).state == BROKEN, "断板当天只进 BROKEN，不叫修复"
        assert _replay(rows[:3]).state == REPAIRING
        s = _replay(rows)
        assert s.state == CROSS_SUCCESS
        assert s.ever_cross_success and s.first_cross_success_date == rows[-1].date
        assert "BREAK_POST_HIGH" in s.reason_codes


class TestCaseB:
    def test_修复失败后再修复再成功(self):
        rows = [
            Row(0, 18.0, ma5=18.5, ma10=17.5, ma20=16.5, ma30=15.5, days_since_break=0),
            _healthy(1, 19.0, 18.6, days_since_break=1),
            Row(2, 16.0, ma5=18.0, ma10=17.5, ma20=16.5, ma30=15.5,
                days_since_break=2, new_low=True),
            _healthy(3, 19.0, 18.1, days_since_break=3, new_low=False),
            _healthy(4, 22.0, 19.0, days_since_break=4, new_high=True, new_low=False),
        ]
        assert _replay(rows[:2]).state == REPAIRING
        assert _replay(rows[:3]).state == CROSS_FAILED, "创断板后新低即失败"
        assert _replay(rows[:4]).state == REPAIRING, "失败之后仍可再修复"
        assert _replay(rows).state == CROSS_SUCCESS


def _to_success():
    """走到 CROSS_SUCCESS 的最短前缀。"""
    return [
        Row(0, 18.0, ma5=18.5, ma10=17.5, ma20=16.5, ma30=15.5, days_since_break=0),
        _healthy(1, 19.0, 18.6, days_since_break=1),
        _healthy(2, 22.0, 19.0, days_since_break=2, new_high=True, new_low=False),
    ]


class TestCaseC:
    def test_跌破MA10单日即走弱(self):
        """这条必须刻意敏感——CROSS_SUCCESS 进的是重点买入候选池。"""
        rows = _to_success() + [
            Row(3, 18.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0, days_since_break=3)]
        s = _replay(rows)
        assert s.state == CROSS_WEAKENING and s.reason_codes == ["BELOW_MA10"]


class TestCaseD:
    def test_单日破MA5不算连续两日才算(self):
        """高标波动大，允许一次正常分歧。"""
        rows = _to_success() + [
            Row(3, 19.2, ma5=19.5, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3)]
        assert _replay(rows).state == CROSS_SUCCESS, "单日破 MA5 仍算健康"
        rows.append(
            Row(4, 19.0, ma5=19.6, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=4))
        s = _replay(rows)
        assert s.state == CROSS_WEAKENING and s.reason_codes == ["BELOW_MA5_2OBS"]


class TestCaseE:
    def test_走弱后重新站回可以恢复健康(self):
        """不要求再次创新高——回踩 MA10 再站回是正常形态。"""
        rows = _to_success() + [
            Row(3, 18.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0, days_since_break=3),
            _healthy(4, 21.0, 19.6, ma10=19.2, days_since_break=4),
        ]
        s = _replay(rows)
        assert s.state == CROSS_SUCCESS
        assert s.ever_cross_success is True
        assert "MA5_TURN_UP" in s.reason_codes

    def test_MA5没上行就不算恢复(self):
        rows = _to_success() + [
            Row(3, 18.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 21.0, ma5=19.4, ma10=19.2, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        assert _replay(rows).state == CROSS_WEAKENING, "MA5 仍在下行，站上去也不算恢复"


class TestCaseF:
    def test_连续两日收在MA30之下硬衰竭(self):
        rows = _to_success() + [
            Row(3, 14.0, ma5=19.0, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 13.0, ma5=18.0, ma10=17.5, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        s = _replay(rows)
        assert s.state == FADED and "BELOW_MA30_2OBS" in s.reason_codes


class TestCaseG:
    def test_断板后长期恶化直接硬衰竭(self):
        """不能让 BROKEN 永久挂几十天。"""
        rows = [Row(0, 18.0, ma5=18.5, ma10=17.5, ma20=16.5, ma30=15.5,
                    days_since_break=0)]
        for i in (1, 2):
            rows.append(Row(i, 12.0, ma5=16.0, ma10=17.0, ma20=18.0, ma30=19.0,
                            days_since_break=i))
        assert _replay(rows).state == FADED

    def test_硬衰竭不受一天一步限制(self):
        """
        今天同时满足「修复」和「硬衰竭」时必须直接 FADED。先叫 REPAIRING 再等明天，
        等于把最不该进观察池的东西放进核心机会里。
        """
        rows = [Row(0, 18.0, ma5=18.5, ma10=17.5, ma20=16.5, ma30=15.5,
                    days_since_break=0),
                Row(1, 12.0, ma5=16.0, ma10=17.0, ma20=18.0, ma30=19.0,
                    days_since_break=1),
                # 站上 MA5 且 MA5 上行（满足 REPAIRING），但仍连续收在 MA30 之下
                Row(2, 16.5, ma5=16.2, ma10=17.0, ma20=18.0, ma30=19.0,
                    days_since_break=2)]
        s = _replay(rows)
        assert s.state == FADED, f"应直接 FADED，实际 {s.state}"


class TestCaseH:
    def test_FADED后普通大阳线不复活(self):
        rows = _to_success() + [
            Row(3, 14.0, ma5=19.0, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 13.0, ma5=18.0, ma10=17.5, ma20=17.0, ma30=16.0, days_since_break=4),
            _healthy(5, 24.0, 17.0, days_since_break=5, new_high=True),
        ]
        assert _replay(rows).state == FADED, "FADED 对当前 cycle 是 terminal"


class TestCaseI:
    def test_新周期重置状态机(self):
        """FADED 只在「当前这段 cycle」里 terminal。新的 ≥4 连板要重新开始。"""
        old = _to_success() + [
            Row(3, 14.0, ma5=19.0, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 13.0, ma5=18.0, ma10=17.5, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        assert _replay(old).state == FADED
        new_cycle = date(2026, 7, 1)
        rows = old + [Row(30, 30.0, ma5=25.0, ma10=22.0, ma20=20.0, ma30=18.0,
                          break_date=None, days_since_break=None,
                          cycle_start=new_cycle, cycle_peak=new_cycle)]
        s = _replay(rows)
        assert s.state == STREAKING
        assert "NEW_CYCLE" in s.reason_codes
        assert s.ever_cross_success is False, "上一轮的荣誉不能继承给新周期"


class TestCaseJ:
    def test_未结算不推进状态(self):
        """上午 11 点的价格不能永久推动 CROSS_SUCCESS → CROSS_WEAKENING。"""
        rows = _to_success() + [
            Row(3, 15.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0,
                days_since_break=3, settled=False)]
        s = _replay(rows)
        assert s.state == UNKNOWN
        assert s.previous_state == CROSS_SUCCESS, "内部记忆保留最近一次有效状态"
        assert s.evaluation_status == "UNSETTLED"
        assert s.reason_codes == ["DATA_UNSETTLED"]

    def test_不知道有没有收盘同样不推进(self):
        """bar_settled=None 是「不知道」，不能当「已经收盘」。"""
        rows = _to_success() + [
            Row(3, 15.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0,
                days_since_break=3, settled=None)]
        assert _replay(rows).state == UNKNOWN

    def test_行情陈旧不推进(self):
        rows = _to_success() + [
            Row(3, 15.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0,
                days_since_break=3, fresh=False)]
        s = _replay(rows)
        assert s.state == UNKNOWN and s.evaluation_status == "STALE"

    def test_UNKNOWN之后事实恢复继续从有效状态推进(self):
        """UNKNOWN 不能永久污染 lifecycle memory。"""
        rows = _to_success() + [
            Row(3, 15.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0,
                days_since_break=3, settled=False),
            Row(4, 18.0, ma5=19.6, ma10=19.2, ma20=17.0, ma30=16.0,
                days_since_break=4),
        ]
        s = _replay(rows)
        assert s.state == CROSS_WEAKENING, "从 CROSS_SUCCESS 继续推进，不是从头开始"


class TestCaseK:
    def test_均线缺失不当成跌破(self):
        """历史不足 → MA10 是 None。None 不是 0，更不是「跌破了」。"""
        rows = _to_success() + [
            Row(3, 18.0, ma5=19.5, ma10=None, ma20=None, ma30=None,
                days_since_break=3)]
        assert _replay(rows).state == CROSS_SUCCESS, "MA10 缺失不能触发 WEAKENING"

    def test_MA30缺失不触发硬衰竭(self):
        rows = _to_success() + [
            Row(3, 5.0, ma5=19.0, ma10=18.0, ma20=17.0, ma30=None, days_since_break=3),
            Row(4, 5.0, ma5=18.0, ma10=17.5, ma20=17.0, ma30=None, days_since_break=4),
        ]
        assert _replay(rows).state != FADED

    def test_没有上一个observation时MA5上行判不出来(self):
        """不能因为缺失就默认满足条件。"""
        rows = [Row(0, 18.0, ma5=18.5, ma10=17.5, ma20=16.5, ma30=15.5,
                    days_since_break=0),
                _healthy(1, 19.0, 18.6, days_since_break=1)]
        assert _replay(rows[:1]).state == BROKEN
        assert _replay(rows).state == REPAIRING, "有两个 observation 才判得出上行"


class TestCaseL:
    def test_真实数据缺口不算连续两日(self):
        """
        中间缺一天（那天没有可用行），剩下两行虽然相邻，但不是连续两个交易
        observation。仓库里「相邻两行就当成昨天和今天」已经错过好几次。
        """
        rows = _to_success() + [
            Row(3, 19.2, ma5=19.5, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            # 第 4 天整行不可用（数据缺口）
            Row(4, 19.0, ma5=19.6, ma10=18.0, ma20=17.0, ma30=16.0,
                days_since_break=4, fresh=False),
            Row(5, 19.0, ma5=19.7, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=5),
        ]
        assert _replay(rows).state == CROSS_SUCCESS, \
            "第3天和第5天虽然都收在MA5下，但中间隔着一个交易日——证明不了连续，不许触发"
        # 补上中间那天（可用且同样收在 MA5 下）→ 现在能证明连续了
        rows[-2] = Row(4, 19.0, ma5=19.6, ma10=18.0, ma20=17.0, ma30=16.0,
                       days_since_break=4)
        assert _replay(rows).state == CROSS_WEAKENING

    def test_没有日历时一律不触发连续规则(self):
        """
        不传日历 = 无从证明相邻。宁可漏一次走弱信号，也不要拿"过滤后相邻"冒充
        "交易日相邻"——单日破 MA10 那条不依赖连续性，仍会立刻踢出核心池。
        """
        rows = _to_success() + [
            Row(3, 19.2, ma5=19.5, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 19.0, ma5=19.6, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        assert _replay(rows, cal=None).state == CROSS_SUCCESS
        assert _replay(rows).state == CROSS_WEAKENING, "给了日历就该触发"


class TestCaseM:
    def test_停牌不制造below_MA观察日(self):
        """
        停牌期间没有交易，压根不该产生 observation。这里用「那几天没有行」表达
        （build_snapshots 本来就只对有 bar 的日子写价格事实）。
        """
        rows = _to_success() + [
            Row(3, 19.2, ma5=19.5, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
        ]
        assert _replay(rows).state == CROSS_SUCCESS
        # 停牌三天后复牌，重新站上 → 仍然健康，不该因为「停牌那几天低于MA5」走弱
        rows.append(_healthy(7, 21.0, 19.6, ma10=19.0, days_since_break=4))
        assert _replay(rows).state == CROSS_SUCCESS


class TestCaseN:
    def test_历史查询不看未来(self):
        """改了 T+1/T+2 的数据，T 那天的状态必须完全不变。"""
        rows = _to_success()
        base = replay_price_lifecycle(rows, rows[-1].date, trading_days=CAL)
        future = rows + [
            Row(3, 10.0, ma5=19.0, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 9.0, ma5=18.0, ma10=17.0, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        again = replay_price_lifecycle(future, rows[-1].date, trading_days=CAL)
        assert again.state == base.state == CROSS_SUCCESS
        assert again.state_since_date == base.state_since_date
        assert replay_price_lifecycle(
            future, future[-1].date, trading_days=CAL).state == FADED


class TestEngineContract:
    def test_同一批输入永远同一结果(self):
        rows = _to_success()
        as_of = rows[-1].date
        a = _replay(rows, as_of)
        b = _replay(list(reversed(rows)), as_of)      # 顺序不该影响结果
        assert (a.state, a.state_since_date) == (b.state, b.state_since_date)

    def test_没有任何历史时是UNKNOWN不是BROKEN(self):
        s = replay_price_lifecycle([], D0)
        assert s.state == UNKNOWN and s.evaluation_status == "INSUFFICIENT"

    def test_口径版本对不上要报错不能静默按旧的算(self):
        with pytest.raises(ValueError, match="price_v1"):
            replay_price_lifecycle(_to_success(), D0, trading_days=CAL,
                                   formula_version="price_v2")

    def test_每次转移都带得出原因(self):
        s = _replay(_to_success())
        assert s.reason_codes and all(isinstance(c, str) for c in s.reason_codes)
        assert s.reasons and s.reasons[0] != s.reason_codes[0], "code 要能翻成人话"
        assert s.formula_version == FORMULA_VERSION
