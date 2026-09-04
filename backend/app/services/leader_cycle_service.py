"""
LeaderCycle —— 一只股票「最近一次打开市场高度」的那段连板周期，及断板后的演化。

## 它是什么

以 603221 爱丽家居为例：

    07-28  6板   07-29  7板   07-30  8板   07-31  9板
    08-03~05  停牌
    08-06 10板  ← cycle_peak
    08-07  断板（+0.12%）  ← break_date
    08-10  +10.00%         ← 断板后重新涨停

这一整段是一个 LeaderCycle。**生命周期的每个状态都相对它定义**，脱离它就无从谈起：
「断板 D+2」的 D 是 break_date；「没有创新低」的低点是 post_break_low；
「突破断板后阶段高点」的高点是 post_break_high；「峰值回撤」的峰值是 peak_price。

## 这一层只有事实，没有判定

不含任何 RUNNING/BROKEN/RECLAIMING 之类的状态——那是状态机的事，建在这层之上。
本层每个字段都能拿东财连板天梯和腾讯 K 线交叉验证，跟破局雷达的高度曲线一样。

## 缺口必须显式表达

连板计数是从 bar 序列数出来的，而快照只在股票进候选池那天才写（实测覆盖率 94.3%）。
**计数循环无法区分"那天没涨停"和"那天我们没记录"**——中间缺一行，连板就静默少一板。

所以 `missing_days` 如实给出周期区间内缺了几个交易日，`peak_board_confident`
标明这个高度是不是可信。宁可说"这个数可能偏低"，也不要给一个看起来精确的错数。

这跟破局雷达"窗口不满不给上沿"、RS"日期对不齐返回 None"是同一条纪律。
"""
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence

from ..services.eastmoney_fetcher import KLineBar

# 「打开过市场高度」的门槛。跟收窄后的强势池 prompt 一致：近60个交易日最高连板 >= 4
MIN_PEAK_BOARD = 4


@dataclass
class LeaderCycle:
    """全部是事实，没有一个字段是判定出来的。"""
    # ── 周期本身 ────────────────────────────────────────────────────────────
    peak_board_count: int          # 这段周期冲到的最高连板
    cycle_start_date: date         # 周期第一个涨停日
    cycle_peak_date: date          # 冲到最高连板那天
    break_date: Optional[date]     # 断板日；None = 仍在连板中（尚未断）
    days_since_break: Optional[int]  # 距断板的**交易日**数（用 bar 序列数，不是自然日）

    # ── 价格结构 ────────────────────────────────────────────────────────────
    peak_price: float                    # 周期内最高收盘价
    post_break_high: Optional[float]     # 断板后最高收盘
    post_break_low: Optional[float]      # 断板后最低收盘
    latest_close: float
    peak_drawdown: Optional[float]       # 现价相对 peak_price 的回撤 %

    # ── 数据可信度（见模块 docstring）────────────────────────────────────────
    # 周期区间内「市场开市但这只股票没有 bar」的天数。**这里面混着两件事**：
    #   · 停牌 —— 那天它本来就不该有 bar，数据是完整的
    #   · 数据缺口 —— 该有却没有，连板数会偏低
    # 只有后者影响可信度。调用方传 suspended_days 才能把两者分开；不传时保守地
    # 全算成缺口（宁可标"可能偏低"，也不要给一个虚假的确信）
    market_sessions: int = 0             # 周期区间内的交易日数
    absent_days: int = 0                 # 其中该股没有 bar 的天数（停牌 + 缺口）
    suspended_days: int = 0              # 其中已确认是停牌的
    missing_days: int = 0                # absent - suspended，真正的数据缺口
    peak_board_confident: bool = True    # 只由 missing_days 决定，跟停牌无关


def _streaks(bars: Sequence[KLineBar]) -> List[tuple]:
    """把 bar 序列切成连板段：[(起始下标, 结束下标, 段内最高连板), ...]。"""
    out, start, run = [], None, 0
    for i, b in enumerate(bars):
        if b.is_limit_up:
            if start is None:
                start = i
            run += 1
        elif start is not None:
            out.append((start, i - 1, run))
            start, run = None, 0
    if start is not None:
        out.append((start, len(bars) - 1, run))
    return out


def identify_leader_cycle(
    bars: Sequence[KLineBar],
    trading_days: Optional[Sequence[date]] = None,
    min_peak: int = MIN_PEAK_BOARD,
    suspended_days: Optional[Sequence[date]] = None,
) -> Optional[LeaderCycle]:
    """
    从 bar 序列识别**最近一次** >= min_peak 的连板周期。没有则返回 None。

    「最近」而不是「最高」：生命周期问的是"它**现在**处于什么阶段"。若这只票 7 月
    冲过 8 板、8 月又走了一段 4 板，当前处境是从 4 板断下来的，锚点就该是后者。
    历史最高辨识度由调用方另外用 `Stock.board_count_60d` 表达，两个都给，不二选一。

    `trading_days` 是该区间应有的交易日序列（来自 trading_calendar）。传了才能算出
    缺口——不传就默认无缺口，但那是"没检查"不是"没缺口"，调用方要清楚这个区别。

    `suspended_days` 是**已确认停牌**的日子。不传的话，停牌会被当成数据缺口
    ——这正是本仓库反复栽的那类错的又一个变体：**「那天没交易」和「那天我们漏了」
    是两件事**。爱丽家居 08-03~08-05 停牌三天，数据其实 100% 完整，却会被判成
    missing_days=3、peak_board_confident=False。
    停牌的硬证据是"权威源那天也没有这只股票的 bar"，只有调用方拿得到，所以由它传入。
    """
    bars = sorted(bars, key=lambda b: b.date)
    if not bars:
        return None
    segs = [s for s in _streaks(bars) if s[2] >= min_peak]
    if not segs:
        return None

    start_i, end_i, peak = segs[-1]          # 最近那段
    cycle_start = bars[start_i].date
    cycle_peak_date = bars[end_i].date        # 段内最后一根即最高连板那天

    # 断板日 = 这段之后的第一根 bar。没有下一根 = 仍在连板中
    break_date = bars[end_i + 1].date if end_i + 1 < len(bars) else None
    days_since_break = (len(bars) - 1 - (end_i + 1)) if break_date else None

    seg_bars = bars[start_i:end_i + 1]
    peak_price = max(b.close_price for b in seg_bars)
    after = bars[end_i + 1:]
    post_high = max((b.close_price for b in after), default=None)
    post_low = min((b.close_price for b in after), default=None)
    latest = bars[-1].close_price
    drawdown = round((latest / peak_price - 1) * 100, 2) if peak_price > 0 else None

    # 缺口：周期起点到最新这段，应有多少交易日、实际有多少根 bar
    sessions = absent = suspended = missing = 0
    if trading_days:
        expect = {d for d in trading_days if cycle_start <= d <= bars[-1].date}
        have = {b.date for b in bars if cycle_start <= b.date <= bars[-1].date}
        gone = expect - have
        susp = {d for d in (suspended_days or []) if d in gone}
        sessions, absent, suspended = len(expect), len(gone), len(susp)
        missing = absent - suspended      # 停牌不算缺口

    return LeaderCycle(
        peak_board_count=peak,
        cycle_start_date=cycle_start,
        cycle_peak_date=cycle_peak_date,
        break_date=break_date,
        days_since_break=days_since_break,
        peak_price=round(peak_price, 4),
        post_break_high=round(post_high, 4) if post_high is not None else None,
        post_break_low=round(post_low, 4) if post_low is not None else None,
        latest_close=round(latest, 4),
        peak_drawdown=drawdown,
        market_sessions=sessions,
        absent_days=absent,
        suspended_days=suspended,
        missing_days=missing,
        # 只看真正的数据缺口。停牌那几天本来就没有 bar，数据是完整的——
        # 把它算进"不可信"，等于因为股票没交易而怀疑自己的数据
        peak_board_confident=(missing == 0),
    )
