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
        assert s.last_valid_state == CROSS_SUCCESS, "内部记忆保留最近一次有效状态"
        assert s.previous_state == REPAIRING, \
            "「来自」问的是展示出来那个状态的前一个，不是它自己"
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
        "交易日相邻"。

        2026-09-06 起这条的影响范围扩大了：`_ma5_turning_up` 也要求相邻，所以
        **没有日历时整个状态机推不过 BROKEN**——MA5 上行证明不了，修复就成立
        不了。这是刻意的：状态机的正确性本来就依赖交易日历，与其让它在缺参数
        时按"数组相邻"悄悄跑出一堆看似合理的状态，不如让它停在原地。
        接口层始终传真实交易日历。
        """
        rows = _to_success() + [
            Row(3, 19.2, ma5=19.5, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 19.0, ma5=19.6, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        assert _replay(rows, cal=None).state == BROKEN, \
            "没有日历，MA5 上行证明不了，修复不成立，停在断板"
        assert _replay(rows).state == CROSS_WEAKENING, "给了日历才能一路推进"


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


class TestCycleIdentity:
    """
    2026-09-04 生产轨迹抓到的：cycle identity 第一版用 (start, peak)，而**连板还在
    进行时峰值日每天都在往后走**，于是整段连板期天天 NEW_CYCLE 重置。

    003040 的实际输出：
        08-26  STREAKING  STILL_STREAKING
        08-27  STREAKING  NEW_CYCLE、STILL_STREAKING   ← 连板途中凭空重置

    起始日才是稳定标识——它是这段连板的第一个涨停日。峰值日在周期走完之前不是
    常量，拿它当身份的一部分，是把"进行中的量"当"标识"用。
    """

    def test_连板途中峰值日推进不算新周期(self):
        rows = [
            Row(0, 15.0, ma5=13.0, ma10=12.0, ma20=11.0, ma30=10.0,
                break_date=None, days_since_break=None,
                cycle_start=D0, cycle_peak=D0),
            Row(1, 16.5, ma5=14.0, ma10=12.5, ma20=11.5, ma30=10.5,
                break_date=None, days_since_break=None,
                cycle_start=D0, cycle_peak=D0 + timedelta(days=1)),
            Row(2, 18.0, ma5=15.0, ma10=13.0, ma20=12.0, ma30=11.0,
                break_date=None, days_since_break=None,
                cycle_start=D0, cycle_peak=D0 + timedelta(days=2)),
        ]
        s = _replay(rows)
        assert s.state == STREAKING
        assert "NEW_CYCLE" not in s.reason_codes, \
            "同一段连板，峰值日每天往后走是正常的，不是新周期"

    def test_起始日变了才是新周期(self):
        old = _to_success() + [
            Row(3, 14.0, ma5=19.0, ma10=18.0, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 13.0, ma5=18.0, ma10=17.5, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        assert _replay(old).state == FADED
        new_start = date(2026, 7, 1)
        rows = old + [Row(30, 30.0, ma5=25.0, ma10=22.0, ma20=20.0, ma30=18.0,
                          break_date=None, days_since_break=None,
                          cycle_start=new_start, cycle_peak=new_start)]
        s = _replay(rows, cal=[D0 + timedelta(days=i) for i in range(45)])
        assert s.state == STREAKING and "NEW_CYCLE" in s.reason_codes

    def test_连板期不会反复清掉曾经成功的记录(self):
        """
        每天 reset 顺带会把 ever_cross_success 清掉。虽然 STREAKING 在成功之前，
        影响有限，但"每天清一次记忆"本身就不该发生。
        """
        rows = _to_success() + [
            Row(10, 30.0, ma5=25.0, ma10=22.0, ma20=20.0, ma30=18.0,
                break_date=None, days_since_break=None,
                cycle_start=D0, cycle_peak=D0 + timedelta(days=10)),
        ]
        s = _replay(rows)
        assert s.state == STREAKING
        assert s.ever_cross_success is True, "同一段周期内，成功过的记录不该被清掉"


class TestBrokenIsTransient:
    """
    **BROKEN 是过渡态，不是停留态。**

    2026-09-04 生产实测：002742 冀衡医药 08-31 断板，到 09-04 已经 D+4、连续四天
    创断板后新低、从峰值回撤 31%，却仍然挂在「刚断板」里。原因是从 BROKEN 出发
    只有一条路（→REPAIRING），没修复就无限期停在原地，只能等 Hard Fade——而
    Hard Fade 在刚拉完一波的票上必然滞后：均线还没翻过来，MA10 高于 MA20
    （实测 MA10=5.10、MA20=4.72），空头排列那条入口根本不成立。

    D+0/D+1 允许观望是有依据的：实测 5 只 BROKEN 里 4 只正处在这个阶段，对它们
    「刚断板」是准确描述。只有 D+2 之后还没表态的才名不副实。
    """

    def _broken_at(self, day, close, ma5, dsb, **kw):
        """
        没修复（收盘在 MA5 之下）但也不到硬衰竭（收盘仍在 MA20/MA30 之上）。
        这正是 002742 的真实形态：刚拉完一波，长均线还在下面很远，
        Hard Fade 那两条入口都够不着——所以才需要"超期"这条路。
        """
        kw.setdefault("ma10", ma5 + 0.5)
        kw.setdefault("ma20", close - 1)    # 收盘在 MA20 上方，排除 Hard Fade
        kw.setdefault("ma30", close - 2)
        return Row(day, close, ma5=ma5, days_since_break=dsb, **kw)

    def test_断板当日和次日仍是刚断板(self):
        rows = [Row(0, 18.0, ma5=18.5, ma10=19.5, ma20=20.5, ma30=21.5,
                    days_since_break=0)]
        assert _replay(rows).state == BROKEN
        rows.append(self._broken_at(1, 17.0, 18.0, 1, new_low=True))
        assert _replay(rows).state == BROKEN, "D+1 还在观望期内"

    def test_到D2仍未修复就判修复失败(self):
        rows = [Row(0, 18.0, ma5=18.5, ma10=19.5, ma20=20.5, ma30=21.5,
                    days_since_break=0)]
        rows.append(self._broken_at(1, 17.0, 18.0, 1, new_low=True))
        rows.append(self._broken_at(2, 16.0, 17.5, 2, new_low=True))
        s = _replay(rows)
        assert s.state == CROSS_FAILED
        assert "BROKEN_TIMEOUT" in s.reason_codes
        assert "BREAK_POST_LOW" in s.reason_codes, "创了新低就一并说明"

    def test_D2当天修复了就走修复中不走失败(self):
        """修复判定优先——超期只是"没修复"的兜底，不是惩罚。"""
        rows = [Row(0, 18.0, ma5=18.5, ma10=19.5, ma20=20.5, ma30=21.5,
                    days_since_break=0)]
        rows.append(self._broken_at(1, 17.0, 18.0, 1))
        rows.append(_healthy(2, 19.5, 18.6, days_since_break=2))
        assert _replay(rows).state == REPAIRING

    def test_判了失败之后仍然可以修复(self):
        """CROSS_FAILED 不是终点，标签变了不等于被踢出观察范围。"""
        rows = [Row(0, 18.0, ma5=18.5, ma10=19.5, ma20=20.5, ma30=21.5,
                    days_since_break=0)]
        rows.append(self._broken_at(1, 17.0, 18.0, 1, new_low=True))
        rows.append(self._broken_at(2, 16.0, 17.5, 2, new_low=True))
        assert _replay(rows).state == CROSS_FAILED
        rows.append(_healthy(3, 19.0, 17.8, days_since_break=3))
        assert _replay(rows).state == REPAIRING

    def test_硬衰竭仍然优先于超期(self):
        """两个都成立时给 FADED——它是更强的结论，不该被超期这条盖住。"""
        rows = [Row(0, 18.0, ma5=18.5, ma10=19.5, ma20=20.5, ma30=21.5,
                    days_since_break=0)]
        for i in (1, 2):
            rows.append(Row(i, 12.0, ma5=16.0, ma10=17.0, ma20=18.0, ma30=19.0,
                            days_since_break=i))
        assert _replay(rows).state == FADED

    def test_没有D加天数时不超期(self):
        """days_since_break 缺失是"不知道"，不能当成"已经很久了"。"""
        rows = [Row(0, 18.0, ma5=18.5, ma10=19.5, ma20=20.5, ma30=21.5,
                    days_since_break=0)]
        rows.append(self._broken_at(1, 17.0, 18.0, None))
        assert _replay(rows).state == BROKEN

    def test_复刻冀衡医药(self):
        """
        08-31 断板 5.62，之后 5.59 / 5.03 / 4.53 / 4.28 一路创新低，
        均线 MA5 5.01 > MA20 4.72 所以不构成空头排列，Hard Fade 不触发。
        旧规则下它到 D+4 还是 BROKEN；现在 D+2 就该判失败。
        """
        rows = [Row(0, 5.62, ma5=5.47, ma10=4.86, ma20=4.51, ma30=4.14,
                    days_since_break=0)]
        for i, (close, ma5, ma10, ma20, ma30) in enumerate(
                [(5.59, 5.65, 4.99, 4.61, 4.22), (5.03, 5.63, 5.06, 4.67, 4.28),
                 (4.53, 5.40, 5.08, 4.71, 4.32), (4.28, 5.01, 5.10, 4.72, 4.35)],
                start=1):
            rows.append(Row(i, close, ma5=ma5, ma10=ma10, ma20=ma20, ma30=ma30,
                            days_since_break=i, new_high=False, new_low=True))
        assert _replay(rows[:2]).state == BROKEN, "D+1 还在观望期"
        s = _replay(rows[:3])
        assert s.state == CROSS_FAILED and s.state_since_date == rows[2].date, \
            "D+2 就该表态，而不是拖到 D+4 还叫「刚断板」"
        assert _replay(rows).state == CROSS_FAILED


class TestEntryReason:
    """
    **状态一旦进入，入场原因原本就丢了。**

    002742 是 09-02 判的修复失败，到 09-04 再看，reason 只剩一句「无满足条件的
    转移，维持原状态」——技术上没错，但对看的人毫无信息：它只说明"今天什么都
    没发生"，没说当初为什么判失败。

    状态可能持续几十天，而人想知道的从来是"它为什么在这儿"。
    """

    def test_保留入场原因(self):
        rows = _to_success() + [
            Row(3, 18.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0, days_since_break=3),
            # 之后一直维持走弱，不再有新的转移
            Row(4, 18.2, ma5=19.3, ma10=19.0, ma20=17.0, ma30=16.0, days_since_break=4),
            Row(5, 18.4, ma5=19.1, ma10=19.0, ma20=17.0, ma30=16.0, days_since_break=5),
        ]
        s = _replay(rows)
        assert s.state == CROSS_WEAKENING
        assert s.reason_codes == ["HOLD"], "今天确实什么都没发生"
        assert s.entry_reason_codes == ["BELOW_MA10"], "但当初是因为跌破 MA10"
        assert s.entry_reasons[0] != "BELOW_MA10", "code 要能翻成人话"

    def test_刚转入时两者一致(self):
        rows = _to_success() + [
            Row(3, 18.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0, days_since_break=3)]
        s = _replay(rows)
        assert s.transitioned_today is True
        assert s.reason_codes == s.entry_reason_codes == ["BELOW_MA10"]

    def test_新周期重置时入场原因也跟着换(self):
        old = _to_success()
        new_start = date(2026, 7, 1)
        rows = old + [Row(30, 30.0, ma5=25.0, ma10=22.0, ma20=20.0, ma30=18.0,
                          break_date=None, days_since_break=None,
                          cycle_start=new_start, cycle_peak=new_start)]
        s = _replay(rows, cal=[D0 + timedelta(days=i) for i in range(45)])
        assert "NEW_CYCLE" in s.entry_reason_codes


class TestUnknownIsNotFalse:
    """
    2026-09-06 review 抓到的：**「不知道有没有修复」被判成了「修复失败」。**

    D+2 超期那条规则加进去之后，MA5 缺失时 above_ma5=False、ma5_up=None，
    修复条件不成立，于是超期直接把它推成 CROSS_FAILED——而 evaluation_status
    还标着 OK，看不出任何异常。

    判失败会把股票推进「已剔除」，是个硬后果，必须建立在可信数据上。
    """

    def _no_ma(self, day, close, dsb):
        return Row(day, close, ma5=None, ma10=None, ma20=None, ma30=None,
                   days_since_break=dsb)

    def test_均线缺失时超期不判失败(self):
        rows = [Row(0, 10.0, ma5=10.5, ma10=11.0, ma20=11.5, ma30=12.0,
                    days_since_break=0),
                self._no_ma(1, 9.8, 1), self._no_ma(2, 9.6, 2)]
        s = _replay(rows)
        assert s.state == BROKEN, "证不出没修复，就不能判失败"
        assert s.reason_codes == ["MA_MISSING"]

    def test_均线补回来之后照常超期判失败(self):
        rows = [Row(0, 10.0, ma5=10.5, ma10=11.0, ma20=11.5, ma30=12.0,
                    days_since_break=0),
                self._no_ma(1, 9.8, 1),
                # MA5 回来了，且明确没站上、也没上行
                Row(2, 9.6, ma5=10.2, ma10=10.5, ma20=8.0, ma30=7.0,
                    days_since_break=2),
                Row(3, 9.4, ma5=10.0, ma10=10.4, ma20=8.0, ma30=7.0,
                    days_since_break=3)]
        s = _replay(rows)
        assert s.state == CROSS_FAILED and "BROKEN_TIMEOUT" in s.reason_codes

    def test_MA5上行判定要求相邻交易日(self):
        """
        中间隔一天没数据时，"MA5 比上次高"说明的只是"比上一次我们有记录的时候
        高"，不是"今天开始转头"。而这条判定直接决定「第一次转强」。
        """
        cal = [D0 + timedelta(days=i) for i in range(10)]
        rows = [Row(0, 10.0, ma5=10.5, ma10=11.0, ma20=11.5, ma30=12.0,
                    days_since_break=0),
                # 第 1 天整行不可用（数据缺口），第 2 天站上 MA5 且 MA5 比第 0 天高
                Row(1, 9.9, ma5=10.4, ma10=11.0, ma20=11.5, ma30=12.0,
                    days_since_break=1, fresh=False),
                Row(2, 11.0, ma5=10.6, ma10=10.0, ma20=9.0, ma30=8.0,
                    days_since_break=2)]
        s = replay_price_lifecycle(rows, rows[-1].date, trading_days=cal)
        assert s.state != REPAIRING, \
            "隔着一天缺口，证明不了 MA5 今天转头，不能算修复"

    def test_相邻时正常判出转强(self):
        cal = [D0 + timedelta(days=i) for i in range(10)]
        rows = [Row(0, 10.0, ma5=10.5, ma10=11.0, ma20=11.5, ma30=12.0,
                    days_since_break=0),
                Row(1, 11.0, ma5=10.6, ma10=10.0, ma20=9.0, ma30=8.0,
                    days_since_break=1)]
        s = replay_price_lifecycle(rows, rows[-1].date, trading_days=cal)
        assert s.state == REPAIRING and "MA5_TURN_UP" in s.reason_codes


class TestLastValidState:
    """
    2026-09-06 生产实测：盘前跑一次日更，所有行 bar_settled=False，整页塌成
    「数据不足」——59 只全部 UNKNOWN，昨天的分组全没了。

    规则本身是对的（不能用上午 11 点的价格推动跨日状态），但**界面不该因此把
    已知的东西也丢掉**。状态机内部一直保留着最近一次有效状态，只是没有一个
    明确的字段把它端出来。
    """

    def test_未结算时给出最近一次有效状态(self):
        rows = _to_success() + [
            Row(3, 15.0, ma5=19.5, ma10=19.0, ma20=17.0, ma30=16.0,
                days_since_break=3, settled=False)]
        s = _replay(rows)
        assert s.state == UNKNOWN, "当日仍然判不出，这条不放松"
        assert s.last_valid_state == CROSS_SUCCESS
        assert s.last_valid_date == rows[2].date, "要能说清是截至哪一天"

    def test_状态判得出时最近有效就是当前(self):
        s = _replay(_to_success())
        assert s.state == CROSS_SUCCESS and s.last_valid_state == CROSS_SUCCESS
        assert s.last_valid_date == s.date

    def test_previous_state只有一个意思(self):
        """
        previous_state 一度一名两义：判得出时是"上一个状态"，判不出时是"最后一
        个有效状态"。2026-09-07 界面拿它当「来自」列，盘前全员未结算，于是
        「来自」跟「状态」逐行雷同。现在两种情况下都是"展示状态的前一个"。
        """
        ok = _replay(_to_success())
        assert ok.previous_state == REPAIRING, "判得出时它是上一个状态"
        assert ok.last_valid_state == CROSS_SUCCESS, "而这个始终是最近有效状态"

        stale = _replay(_to_success() + [
            Row(3, 22.5, ma5=19.6, ma10=18.5, ma20=17.0, ma30=16.0,
                days_since_break=3, settled=False)])
        assert stale.state == UNKNOWN and stale.last_valid_state == CROSS_SUCCESS
        assert stale.previous_state == REPAIRING, "判不出时也还是同一个意思"
        assert stale.previous_state != stale.last_valid_state, \
            "「来自」等于「状态」说明这一列什么都没说"

    def test_一行都不可用时没有最近有效状态(self):
        rows = [Row(0, 10.0, ma5=10.5, ma10=11.0, ma20=11.5, ma30=12.0,
                    days_since_break=0, settled=False)]
        s = _replay(rows)
        assert s.state == UNKNOWN and s.last_valid_state is None, \
            "从来没判出来过就是没有，不编一个"


class TestDaysInState:
    """
    「它从哪来、在这待了多久」——`previous_state` 回答前半句，`days_in_state`
    回答后半句。

    **按交易日历数，不数快照行数。** 缺一行就少算一天，那正是 days_since_break
    早期踩过的坑（快照有空洞时它系统性低估）。
    """

    def test_转入当天是0(self):
        rows = _to_success()
        s = _replay(rows)
        assert s.transitioned_today is True and s.days_in_state == 0

    def test_按交易日历数(self):
        rows = _to_success() + [
            Row(3, 22.5, ma5=19.6, ma10=18.5, ma20=17.0, ma30=16.0, days_since_break=3),
            Row(4, 22.8, ma5=19.8, ma10=18.6, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        s = _replay(rows)
        assert s.state == CROSS_SUCCESS
        assert s.days_in_state == 2, "第2天转入，现在是第4天，隔了 2 个交易日"

    def test_中间缺一行也照样按日历数(self):
        """
        第 3 天的行不可用（数据缺口）。停留天数仍应是 2——它问的是"过了几个
        交易日"，不是"我们有几行记录"。
        """
        rows = _to_success() + [
            Row(3, 22.5, ma5=19.6, ma10=18.5, ma20=17.0, ma30=16.0,
                days_since_break=3, fresh=False),
            Row(4, 22.8, ma5=19.8, ma10=18.6, ma20=17.0, ma30=16.0, days_since_break=4),
        ]
        assert _replay(rows).days_in_state == 2

    def test_没有日历时不猜(self):
        assert _replay(_to_success(), cal=None).days_in_state is None

    def test_未结算时算到最后一个有效日(self):
        rows = _to_success() + [
            Row(3, 22.0, ma5=19.6, ma10=18.5, ma20=17.0, ma30=16.0,
                days_since_break=3, settled=False)]
        s = _replay(rows)
        assert s.state == UNKNOWN
        assert s.days_in_state == 0, "算到最后一个已结算日，不把未结算那天算进去"
