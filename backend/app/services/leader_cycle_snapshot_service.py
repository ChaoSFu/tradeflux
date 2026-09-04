"""
每日给强势池股票落一行 LeaderCycleSnapshot（纯事实）。

## 为什么现在就要落

生命周期的价值全在**时间序列**上——「断板 D+2 有没有创新低」「RS 在改善还是恶化」
「修复了几天」都要靠相邻两天相减。今天不开始记，一个月后还是只有当天一个截面，
而且丢掉的那段永远补不回来。跟 RegulatoryStatusDaily 是同一个道理。

## 幂等

(date, stock_id) 唯一。daily_update 一天跑 2~3 次，同一天重复写是覆盖不是追加。
盘中那次写的是当时的事实，盘后那次覆盖成终值——跟股票快照同一套语义。
"""
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.leader_cycle import LeaderCycleSnapshot
from ..models.sector import Sector
from ..models.stock import Stock, StockDailySnapshot
from ..services.deviation_service import board_index_code
from ..services.leader_cycle_service import identify_leader_cycle
from ..services.turnover_rate_service import compute_turnover_rate
from ..services.relative_strength_service import (
    MarketBenchmark, compute_rs_market, compute_rs_sector_from_vendor, DEFAULT_WINDOWS,
)

# 均线窗口够不够：ma30 要 30 根，留一根余量
_MA_MIN_BARS = 30


def build_snapshots(db: Session, trade_date: date,
                    klines_map: Dict[str, list],
                    trading_days: Optional[List[date]] = None,
                    stats_map: Optional[Dict[str, object]] = None,
                    suspended_map: Optional[Dict[str, List[date]]] = None) -> dict:
    """
    `klines_map` 是 daily_update 已经拿到的 {code: [KLineBar]}，直接复用，
    **不重新拉任何数据**。`stats_map` 是 {code: StockWindowStats}（含均线）。
    """
    pool = db.query(Stock).filter(Stock.in_strong_pool.is_(True)).all()
    if not pool:
        return {"written": 0, "skipped": 0, "no_cycle": 0}

    bench = MarketBenchmark(db, as_of=trade_date, windows=DEFAULT_WINDOWS)

    # 主板块的区间涨幅：Sector 表里 f109/f110/f160/f165 每天同步，直接用。
    # 板块指数序列（SectorIndexDaily）现在只有当天那一根，回填被数据源封锁，
    # 所以这一版 RS_sector 走这条——它是东财算好的板块区间收益，口径干净。
    sec_by_id = {s.id: s for s in db.query(Sector).all()}

    existing = {
        r.stock_id: r for r in db.query(LeaderCycleSnapshot)
        .filter(LeaderCycleSnapshot.date == trade_date).all()
    }
    written = no_cycle = skipped = stale = 0

    for st in pool:
        bars = klines_map.get(st.code) or []
        if not bars:
            skipped += 1
            continue
        # 停牌日单独传：不传的话停牌会被当成数据缺口，等于因为股票没交易而
        # 怀疑自己的数据（爱丽家居 08-03~05 停牌三天，数据其实是完整的）
        cyc = identify_leader_cycle(
            bars, trading_days=trading_days,
            suspended_days=(suspended_map or {}).get(st.code))
        if cyc is None:
            # 在池里但当前 60 日窗口内识别不出 >=4 连板周期。**这是事实不是错误**
            # ——东财召回口径与本地重算存在已知差异（2026-09-03 实测 61 只里 3 只）。
            # 不建行，而不是建一行字段全空的：缺行表达"没有周期"，比一行 NULL 清楚。
            no_cycle += 1
            continue

        closes = {b.date: b.close_price for b in bars if b.close_price}
        rs_m = compute_rs_market(bench, st.code, closes)

        # RS_sector：个股区间收益 − 板块区间收益。锚点沿用大盘指数的交易日，
        # 跟 RS_market 同一套，两个 RS 才可比
        sec = sec_by_id.get(st.primary_sector_id) if st.primary_sector_id else None
        idx = board_index_code(st.code) or ""
        idx_latest = bench.latest(idx)
        # RS_sector 走 relative_strength_service 里的统一实现，**不在这里内联第二套**。
        # 板块指数历史被 push2his 限流拿不到，所以用的是 vendor 口径（东财服务端
        # 算好的区间涨幅），落库时记 rs_sector_source 让看数的人能分辨。
        rs_s: Dict[int, Optional[float]] = {w: None for w in DEFAULT_WINDOWS}
        if sec is not None:
            rs_s = compute_rs_sector_from_vendor(
                {10: getattr(sec, "pct_change_10d", None),
                 20: getattr(sec, "pct_change_20d", None),
                 60: getattr(sec, "pct_change_60d", None)},
                closes,
                {w: bench.anchor(idx, w) for w in DEFAULT_WINDOWS},
                idx_latest,
            )

        stats = (stats_map or {}).get(st.code)
        last = bars[-1]
        # **今天那根到底是不是今天的**。如果上游最终没补回来，bars[-1] 还是昨天的，
        # 直接写下去就变成"昨天的值挂着今天的日期"——这轮反复修的正是这类错
        # （盘中价冒充收盘价、停牌日顺延陈旧连板数）。这一层要守自己的数据契约，
        # 不能完全相信调用方。
        fresh = last.date == trade_date
        row = existing.get(st.id)
        if row is None:
            row = LeaderCycleSnapshot(date=trade_date, stock_id=st.id, stock_code=st.code)
            db.add(row)
        row.peak_board_count = cyc.peak_board_count
        row.board_count_60d = st.board_count_60d
        row.cycle_start_date = cyc.cycle_start_date
        row.cycle_peak_date = cyc.cycle_peak_date
        row.break_date = cyc.break_date
        row.days_since_break = cyc.days_since_break
        row.peak_price = cyc.peak_price
        row.post_break_high = cyc.post_break_high
        row.post_break_low = cyc.post_break_low
        row.peak_drawdown = cyc.peak_drawdown
        row.market_sessions = cyc.market_sessions
        row.absent_days = cyc.absent_days
        row.suspended_days = cyc.suspended_days
        row.missing_days = cyc.missing_days
        row.peak_board_confident = cyc.peak_board_confident
        row.latest_bar_date = last.date
        row.data_fresh = fresh
        # 当日事实只在那根 bar 确实是今天时才写。不新鲜就留空——
        # 宁可缺失，也不要让昨天的收盘价挂着今天的日期
        row.latest_close = cyc.latest_close if fresh else None
        if stats is not None:
            row.ma5, row.ma10 = stats.ma5, stats.ma10
            row.ma20, row.ma30 = stats.ma20, stats.ma30
        # 窗口不满时上面几个是 0.0，这个标志让下游能区分"均线是0"和"没攒够"
        row.ma_window_complete = len(bars) >= _MA_MIN_BARS
        row.rs_market_10, row.rs_market_20, row.rs_market_60 = (
            rs_m.get(10), rs_m.get(20), rs_m.get(60))
        row.rs_sector_10, row.rs_sector_20, row.rs_sector_60 = (
            rs_s.get(10), rs_s.get(20), rs_s.get(60))
        # 口径来源。两种 RS_sector 定义不等价（见 compute_rs_sector_from_vendor），
        # 不记来源就等于让两个不同的东西共用一个字段名
        row.rs_sector_source = ("vendor" if any(v is not None for v in rs_s.values())
                                else None)
        row.volume, row.amount = (last.volume, last.amount) if fresh else (None, None)
        # 换手率：K线源一律不提供（腾讯/新浪/dump 都不给），必须自己算——
        # 成交量 ÷ 流通股本。首版这里直接写了 last.turnover_rate，于是 60 只全是
        # None：**零件造好了没装上**。流通股本是 refresh_float_shares() 从涨停池的
        # 流通市值反推的，观测过旧（>45天）时 compute 返回 None 而不是一个悄悄
        # 错 20% 的数——除权、解禁会让流通股本台阶式跳变。
        row.turnover_rate = compute_turnover_rate(
            last.volume if fresh else None,
            st.float_shares, st.float_shares_date, trade_date)
        written += 1
        if not fresh:
            stale += 1

    db.commit()
    return {"written": written, "skipped": skipped, "no_cycle": no_cycle,
            "stale": stale}
