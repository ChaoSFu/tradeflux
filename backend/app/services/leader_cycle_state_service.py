"""
Price Lifecycle v1 —— 高标龙头生命周期状态机的第一期。

## 为什么分两期

不是因为第二期做不了，而是这两期回答的是**不同的问题**：

    Phase 1  Price Lifecycle
             "这只曾经 >=4 连板的高辨识度股票，从它自己的价格结构看，走到哪了？"
             只用 break_date / close / ma5,10,20,30 / 断板后新高新低

    Phase 2  Leadership Quality + Trade Permission
             "它是否仍有市场领导力？今天是否值得交易？"
             才轮到 RS / 量能 / 换手 / 同代排名 / 板块 / 大盘 / 监管 / 剩余空间

分开的四个理由，每个都具体：

A. 当前最迫切的问题不是精确预测二波，是**已经明显走弱的老龙还挂在 CROSS_SUCCESS
   里干扰买入**。MA5/10/20 足够先解决这个。

B. Phase 2 那些字段的覆盖率还没收口——RS 46/61、换手 38/61，板块指数历史这两天
   还在查"到底是限流还是连接失效"。现在把它们做成生命周期硬门槛，就会变成
   **"某字段有没有数据"决定"某股票处于什么状态"**——source-dependent bias，
   而这个仓库为这类错栽过太多次。

C. 先有 price_v1 基线，之后才能做真正的 ablation：Price only / +RS / +量能 /
   +同代排名，逐个证明谁值得加。一次全塞进去，即使有效也不知道 Edge 来自哪。

D. 从现在开始产生状态轨迹，才能积累 transition / MFE / MAE / 未来涨停 等研究样本。

**Phase 1 不是最终答案，是研究基线。**

## 状态不落库

`LeaderCycleSnapshot` 继续是 DAILY FACT ONLY。状态一律从历史事实 replay 出来。

理由跟 RegulatoryStatusDaily「派生事件不落库」是同一条：阈值以后一定会改。把
CROSS_SUCCESS 冻进历史事实表，price_v1 换成 v2 之后整段历史就变成旧口径数据，
再也没法回答「新口径下当时该是什么状态」。

replay 的代价很小：几十只 × 60~90 天。真有性能问题再加 materialized cache。

## 哪些常量是拍的，说清楚

MA5/10/20/30 穿越是公认口径，不是我们自造的。但**次数**是拍的：
「连续两个 observation 破 MA5」里的 2、「MA30 两次」里的 2，目前没有分布支撑。
所以它们是模块级常量 + FORMULA_VERSION，以后换阈值时整段历史能按新口径重算
——这正是状态不落库的价值所在。

## 一天最多推进一步，Hard Fade 除外

不允许 BROKEN → CROSS_SUCCESS 在同一个 settled day 连跳两级：REPAIRING 本来就是
重点交易观察区，没必要为了「早点叫成功」牺牲状态轨迹的可解释性。

**唯一例外是 Hard Fade。** 它要解决的是「别让 BROKEN/FAILED 永久挂几十天」，如果
也受一天一步限制，一只今天同时满足修复和硬衰竭的票会先被叫成 REPAIRING，那正好
是最不该出现在观察池里的东西。
"""
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Sequence

FORMULA_VERSION = "price_v1"

# ── 拍出来的常量（见模块 docstring）────────────────────────────────────────
# 连续几个**有效交易 observation** 收在 MA5 之下才算走弱。1 次不够：高标波动大，
# 允许一次正常分歧
BELOW_MA5_OBS = 2
# 连续几次收在 MA20 / MA30 之下才算硬衰竭
BELOW_MA20_OBS = 2
BELOW_MA30_OBS = 2

# ── 生命周期状态 ───────────────────────────────────────────────────────────
STREAKING = "STREAKING"                # 仍在连板中
BROKEN = "BROKEN"                      # 刚断板，结构还没演化
REPAIRING = "REPAIRING"                # 重新站回短趋势，且 MA5 本身在上行
CROSS_SUCCESS = "CROSS_SUCCESS"        # 二波突破断板后阶段高点，且短趋势仍健康
CROSS_WEAKENING = "CROSS_WEAKENING"    # 曾经成功，趋势已恶化 —— 立即退出核心池
CROSS_FAILED = "CROSS_FAILED"          # 本次修复失败
FADED = "FADED"                        # 当前这段 cycle 生命周期结束（terminal）
# 技术状态，**不是生命周期阶段**：今天的事实不足以可靠判断。
# 不用 BROKEN / FAILED / SUCCESS 去顶替「不知道」
UNKNOWN = "UNKNOWN"

POST_BREAK_STATES = (BROKEN, REPAIRING, CROSS_SUCCESS, CROSS_WEAKENING, CROSS_FAILED)

# 前端默认只看这两个
CORE_OPPORTUNITY = (REPAIRING, CROSS_SUCCESS)

REASON_TEXT = {
    "NEW_CYCLE": "识别到新的 ≥4 连板周期，状态机重置",
    "FIRST_BREAK": "首次断板",
    "STILL_STREAKING": "仍在连板中",
    "RECLAIM_MA5": "收盘重新站上 MA5",
    "MA5_TURN_UP": "MA5 本身开始上行",
    "BREAK_POST_HIGH": "创断板后收盘新高",
    "BREAK_POST_LOW": "创断板后收盘新低",
    "ABOVE_MA10": "收盘站上 MA10",
    "BELOW_MA5_2OBS": f"连续 {BELOW_MA5_OBS} 个有效交易日收在 MA5 之下",
    "BELOW_MA10": "收盘跌破 MA10",
    "RECLAIM_MA10": "收盘重新站上 MA10",
    "BELOW_MA20_2OBS": f"连续 {BELOW_MA20_OBS} 个有效交易日收在 MA20 之下",
    "BELOW_MA30_2OBS": f"连续 {BELOW_MA30_OBS} 个有效交易日收在 MA30 之下",
    "BEARISH_MA_5_10_20": "均线空头排列 MA5 < MA10 < MA20",
    "DATA_UNSETTLED": "当日尚未收盘，不用盘中价推进生命周期",
    "DATA_STALE": "最新 bar 不是当日，价格事实已过期",
    "MA_MISSING": "判定所需均线缺失（历史不足），不拿 None 当跌破",
    "HISTORY_GAP": "缺少足够的历史 observation，无法证明连续性",
    "HOLD": "无满足条件的转移，维持原状态",
}


@dataclass
class DayState:
    """某一个交易日 replay 出来的状态。每次转移都带 reason code，可解释。"""
    date: date
    state: str
    previous_state: Optional[str] = None
    state_since_date: Optional[date] = None
    transitioned_today: bool = False
    reason_codes: List[str] = field(default_factory=list)
    evaluation_status: str = "OK"          # OK | UNSETTLED | STALE | INSUFFICIENT
    formula_version: str = FORMULA_VERSION
    # 「曾经穿越成功」要单独记：CROSS_WEAKENING 必须能跟 CROSS_FAILED 区分开
    ever_cross_success: bool = False
    first_cross_success_date: Optional[date] = None

    @property
    def reasons(self) -> List[str]:
        return [REASON_TEXT.get(c, c) for c in self.reason_codes]


# ── 有效 observation ───────────────────────────────────────────────────────

def _usable(row) -> bool:
    """
    这一行的价格事实**能不能用来推进生命周期**。

    三个条件缺一不可：

      data_fresh   那根 bar 是当日的（不是隔了几天的陈值）
      bar_settled  那根 bar 是收盘终值（不是 11 点的现价）
      latest_close 真的有收盘价

    data_fresh 和 bar_settled 必须分开看：腾讯盘中就发当日 bar，盘中两者一真一假。
    只看前者，上午 11 点的价格就能永久推动 CROSS_SUCCESS → CROSS_WEAKENING。

    `bar_settled is None`（调用方没说）同样不算可用——「不知道有没有收盘」不能
    当成「已经收盘」。宁可给 UNKNOWN。
    """
    return bool(row.data_fresh) and row.bar_settled is True and row.latest_close


def _cycle_id(row):
    """
    cycle identity。状态属于**一段 LeaderCycle**，不是股票的永久身份。

    用 (cycle_start_date, cycle_peak_date) 而不只是 start：同一个 start 下峰值日
    可能因为数据修复而变，但那不该 reset；真正的新周期两者都会变。这里两个都取，
    任一变化即认为进入新周期——宁可多 reset 一次，也不要把上一轮的 FADED 继承给
    一段全新的连板。
    """
    return (row.cycle_start_date, row.cycle_peak_date)


def _adjacent(d1: date, d2: date, calendar: Optional[Sequence[date]]) -> Optional[bool]:
    """
    d1 和 d2 在观测日历上**紧挨着**吗。None = 证明不了。

    没有日历就返回 None —— 「过滤掉不可用行之后两行相邻」**不等于**两个交易日相邻。
    中间那天可能停牌（那天没有交易），也可能是真实数据缺口（那天交易了但我们没
    数据）。前者两侧确实是连续的交易 observation，后者不是，而在这一层分不出来。

    分不出来就保守：spec 的原话是「如果现有数据无法证明连续，不触发 two-day rule」。
    代价是跨停牌的走弱信号会漏掉一次；但 CROSS_SUCCESS 的单日破 MA10 那条不依赖
    连续性，仍然会立刻把它踢出核心池，所以这个保守是付得起的。
    """
    if not calendar:
        return None
    try:
        i1, i2 = calendar.index(d1), calendar.index(d2)
    except ValueError:
        return None
    return i2 - i1 == 1


def _consecutive_below(obs, ma_attr: str, need: int,
                       calendar: Optional[Sequence[date]] = None) -> Optional[bool]:
    """
    最近 `need` 个 observation 是不是**连续交易日**收在某条均线之下。

    返回 None = 证明不了：observation 不够、均线缺失、或者它们在日历上不相邻。
    **不返回 False** —— 「没证明连续跌破」和「已证明没有连续跌破」是两件事，
    两者都不该触发转移，但原因不同，reason code 要能分开。

    仓库里「相邻两行就当成昨天和今天」已经错过好几次，这里不重犯：过滤掉不可用
    行之后剩下的两行相邻，不代表它们是相邻的交易日。
    """
    if len(obs) < need:
        return None
    tail = obs[-need:]
    vals = [getattr(r, ma_attr) for r in tail]
    if any(v is None for v in vals):
        return None                      # 均线缺失，不拿 None 当跌破
    for a, b in zip(tail, tail[1:]):
        if _adjacent(a.date, b.date, calendar) is not True:
            return None                  # 证明不了连续，宁可不触发
    return all(r.latest_close < v for r, v in zip(tail, vals))


def _ma5_turning_up(obs) -> Optional[bool]:
    """
    MA5 本身在不在上行。只站上一条仍在快速下行的 MA5 不算真正修复。

    比的是**上一个有效 observation** 的 MA5，不是「上一行数据库记录」。
    缺上一个 observation 或任一端 MA5 为 None 时返回 None——不能因为缺失就
    默认满足条件。
    """
    if len(obs) < 2:
        return None
    cur, prev = obs[-1].ma5, obs[-2].ma5
    if cur is None or prev is None:
        return None
    return cur > prev          # 相等算作「未上行」，不留未定义行为


def _hard_fade(obs, calendar=None) -> Optional[List[str]]:
    """
    硬衰竭：当前这一代龙头生命周期结束，不再值得占据核心观察资源。

    它必须比 CROSS_SUCCESS → CROSS_WEAKENING **慢**（那条是「快速踢出买池」，
    这条是「确认周期结束」，两个不同问题），但也不能让 BROKEN / CROSS_FAILED
    永久挂几十天。

    满足任一即可，返回命中的 reason code；都不满足返回 None。
    """
    hits: List[str] = []
    if _consecutive_below(obs, "ma30", BELOW_MA30_OBS, calendar) is True:
        hits.append("BELOW_MA30_2OBS")
    below20 = _consecutive_below(obs, "ma20", BELOW_MA20_OBS, calendar)
    if below20 is True:
        cur = obs[-1]
        if (cur.ma5 is not None and cur.ma10 is not None and cur.ma20 is not None
                and cur.ma5 < cur.ma10 < cur.ma20):
            hits.extend(["BELOW_MA20_2OBS", "BEARISH_MA_5_10_20"])
    return hits or None


def _advance(prev_state: str, obs, calendar=None) -> tuple:
    """
    从 prev_state 推进一步。返回 (新状态, reason_codes)。

    **一天最多推进一个 transition**，Hard Fade 是唯一例外（见模块 docstring）。
    obs[-1] 是今天，obs 全部是有效 settled observation。
    """
    cur = obs[-1]

    # 1) 仍在连板中 —— 纯事实，先于一切判定
    if cur.break_date is None:
        return STREAKING, ["STILL_STREAKING"]

    # 2) 刚断板。断板当天只进 BROKEN：断板只是第一段连续涨停结束，
    #    既不等于失败也不等于修复
    if prev_state == STREAKING:
        return BROKEN, ["FIRST_BREAK"]

    # 3) FADED 对当前 cycle 是 terminal，一次普通反弹不能复活。
    #    只有 cycle identity 变了（新的 >=4 连板）才 reset，那在 replay 外层处理
    if prev_state == FADED:
        return FADED, ["HOLD"]

    # 4) Hard Fade 优先于其余所有转移，且不受「一天一步」限制。
    #    否则一只今天同时满足修复和硬衰竭的票会先被叫成 REPAIRING，
    #    那正是最不该出现在观察池里的东西
    if prev_state in POST_BREAK_STATES:
        fade = _hard_fade(obs, calendar)
        if fade:
            return FADED, fade

    above_ma5 = cur.ma5 is not None and cur.latest_close > cur.ma5
    above_ma10 = cur.ma10 is not None and cur.latest_close > cur.ma10
    ma5_up = _ma5_turning_up(obs)
    below_ma5_2 = _consecutive_below(obs, "ma5", BELOW_MA5_OBS, calendar)

    # 5) CROSS_SUCCESS 的走弱判定「必须刻意敏感」——它进的是重点买入候选池。
    #    问题不是「龙头死没死」，而是「还该不该和健康二波龙头放在同一个池子里」
    if prev_state == CROSS_SUCCESS:
        if cur.ma10 is not None and cur.latest_close < cur.ma10:
            return CROSS_WEAKENING, ["BELOW_MA10"]
        if below_ma5_2 is True:
            return CROSS_WEAKENING, ["BELOW_MA5_2OBS"]
        return CROSS_SUCCESS, ["HOLD"]

    # 6) 允许二波龙头重新恢复健康。不要求再次创新高——龙头可能
    #    二波突破 → 正常回踩 MA10 → 重新站回短趋势 → 再次健康
    if prev_state == CROSS_WEAKENING:
        if above_ma5 and above_ma10 and ma5_up is True:
            return CROSS_SUCCESS, ["RECLAIM_MA5", "RECLAIM_MA10", "MA5_TURN_UP"]
        return CROSS_WEAKENING, ["HOLD"]

    # 7) REPAIRING 之后：先看失败，再看成功
    if prev_state == REPAIRING:
        if cur.new_post_break_low_today is True:
            return CROSS_FAILED, ["BREAK_POST_LOW"]
        if below_ma5_2 is True and ma5_up is False:
            return CROSS_FAILED, ["BELOW_MA5_2OBS"]
        if (cur.new_post_break_high_today is True and above_ma5 and above_ma10
                and ma5_up is True):
            return CROSS_SUCCESS, ["BREAK_POST_HIGH", "ABOVE_MA10", "MA5_TURN_UP"]
        return REPAIRING, ["HOLD"]

    # 8) BROKEN / CROSS_FAILED → REPAIRING：股价重新站回短趋势，
    #    **且 MA5 本身开始向上**。只站上一条仍在快速下行的 MA5 不算修复
    if prev_state in (BROKEN, CROSS_FAILED):
        if (cur.days_since_break is not None and cur.days_since_break >= 1
                and above_ma5 and ma5_up is True):
            return REPAIRING, ["RECLAIM_MA5", "MA5_TURN_UP"]
        return prev_state, ["HOLD"]

    return prev_state, ["HOLD"]


def _initial_state(row) -> tuple:
    """
    replay 起点。历史从中途开始时（我们的快照只有 60 天）不能假装知道更早的事：

      break_date is None  → STREAKING（纯事实）
      否则                → BROKEN（知道它断过板，其余一概不知）

    不要因为它现在站在 MA5 上就直接给 REPAIRING —— 那是转移，转移要有来处。
    """
    if row.break_date is None:
        return STREAKING, ["STILL_STREAKING"]
    return BROKEN, ["FIRST_BREAK"]


def replay_price_lifecycle(snapshots, as_of_date: date,
                           trading_days: Optional[Sequence[date]] = None,
                           formula_version: str = FORMULA_VERSION) -> DayState:
    """
    从**一只股票**的历史事实 replay 出 as_of_date 那天的生命周期状态。

    纯函数：不调外部接口、不写库、不读 as_of_date 之后的任何一行。
    同一批事实输入永远得到同一结果。

    `snapshots` 是该股票的 LeaderCycleSnapshot 行（顺序随意，内部会排）。
    历史查询传 as_of_date=T 时，**T 之后的行必须被丢掉**——这是 look-ahead guard，
    改了 T+1 的数据不能让 T 的状态变化。

    `trading_days` 是观测日历（升序）。「连续两个 observation」的规则**只有拿到它
    才会触发**：过滤掉不可用行之后两行相邻，不等于两个交易日相邻，中间那天可能是
    停牌也可能是数据缺口，在这一层分不出来。不传就一律证明不了，保守不触发。
    """
    if formula_version != FORMULA_VERSION:
        raise ValueError(f"未知的口径版本 {formula_version}；本模块只实现 "
                         f"{FORMULA_VERSION}。换口径要显式改，不能静默按旧的算")

    rows = sorted((r for r in snapshots if r.date <= as_of_date),
                  key=lambda r: r.date)
    if not rows:
        return DayState(date=as_of_date, state=UNKNOWN,
                        reason_codes=["HISTORY_GAP"], evaluation_status="INSUFFICIENT",
                        formula_version=formula_version)

    # 只保留能用来推进的 observation。停牌 / 数据缺口 / 盘中未结算的行不在序列里，
    # 于是它们**不会凭空制造一次 below-MA observation**，也跨不过去假装「连续两日」
    obs: List = []
    state: Optional[str] = None
    prev_state: Optional[str] = None
    since: Optional[date] = None
    codes: List[str] = ["HOLD"]
    ever_success = False
    first_success: Optional[date] = None
    cycle: Optional[tuple] = None
    transitioned_on: Optional[date] = None

    for row in rows:
        if not _usable(row):
            continue
        cid = _cycle_id(row)
        if state is None or cid != cycle:
            # 新周期 → 整个状态机 reset。上一轮的 FADED / CROSS_FAILED 不能继承，
            # FADED 只在「当前这段 cycle」里是 terminal
            new_state, new_codes = _initial_state(row)
            if state is not None:
                new_codes = ["NEW_CYCLE"] + new_codes
                ever_success, first_success = False, None
            cycle = cid
            obs = [row]
            state, prev_state, since, codes = new_state, state, row.date, new_codes
            transitioned_on = row.date
            continue

        obs.append(row)
        nxt, new_codes = _advance(state, obs, trading_days)
        if nxt != state:
            prev_state, state, since = state, nxt, row.date
            transitioned_on = row.date
            codes = new_codes
            if nxt == CROSS_SUCCESS and not ever_success:
                ever_success, first_success = True, row.date
        else:
            codes = new_codes

    if state is None:
        # 一行都不可用：有历史但全是停牌 / 未结算 / 陈值
        last = rows[-1]
        return DayState(date=as_of_date, state=UNKNOWN,
                        reason_codes=[_why_unusable(last)],
                        evaluation_status=_eval_status(last),
                        formula_version=formula_version)

    today = rows[-1]
    if not _usable(today) or today.date != as_of_date:
        # **今天的事实不足以判断**。展示 UNKNOWN，但内部记忆保留——事实重新完整
        # 之后继续从最近一次有效状态推进，UNKNOWN 不永久污染 lifecycle memory
        return DayState(
            date=as_of_date, state=UNKNOWN, previous_state=state,
            state_since_date=since, transitioned_today=False,
            reason_codes=[_why_unusable(today) if today.date == as_of_date
                          else "DATA_STALE"],
            evaluation_status=(_eval_status(today) if today.date == as_of_date
                               else "STALE"),
            formula_version=formula_version,
            ever_cross_success=ever_success, first_cross_success_date=first_success)

    return DayState(
        date=as_of_date, state=state, previous_state=prev_state,
        state_since_date=since, transitioned_today=(transitioned_on == as_of_date),
        reason_codes=codes, evaluation_status="OK", formula_version=formula_version,
        ever_cross_success=ever_success, first_cross_success_date=first_success)


def _why_unusable(row) -> str:
    if not row.data_fresh:
        return "DATA_STALE"
    if row.bar_settled is not True:
        return "DATA_UNSETTLED"
    return "MA_MISSING" if row.latest_close is None else "HISTORY_GAP"


def _eval_status(row) -> str:
    if not row.data_fresh:
        return "STALE"
    if row.bar_settled is not True:
        return "UNSETTLED"
    return "INSUFFICIENT"
