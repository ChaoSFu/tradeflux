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
from ..services.relative_strength_service import (
    MarketBenchmark, compute_rs_market, DEFAULT_WINDOWS,
)

# 均线窗口够不够：ma30 要 30 根，留一根余量
_MA_MIN_BARS = 30


def _stock_interval_return(closes: Dict[date, float], anchor: Optional[date],
                           latest: Optional[date]) -> Optional[float]:
    if not anchor or not latest:
        return None
    a, b = closes.get(anchor), closes.get(latest)
    if not a or not b or a <= 0:
        return None
    return (b / a - 1) * 100


def build_snapshots(db: Session, trade_date: date,
                    klines_map: Dict[str, list],
                    trading_days: Optional[List[date]] = None,
                    stats_map: Optional[Dict[str, object]] = None) -> dict:
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
    written = no_cycle = skipped = 0

    for st in pool:
        bars = klines_map.get(st.code) or []
        if not bars:
            skipped += 1
            continue
        cyc = identify_leader_cycle(bars, trading_days=trading_days)
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
        rs_s: Dict[int, Optional[float]] = {w: None for w in DEFAULT_WINDOWS}
        if sec is not None:
            for w, attr in ((10, "pct_change_10d"), (20, "pct_change_20d"),
                            (60, "pct_change_60d")):
                base = getattr(sec, attr, None)
                mine = _stock_interval_return(closes, bench.anchor(idx, w), idx_latest)
                if base is not None and mine is not None:
                    rs_s[w] = round(mine - base, 2)

        stats = (stats_map or {}).get(st.code)
        last = bars[-1]
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
        row.latest_close = cyc.latest_close
        row.peak_drawdown = cyc.peak_drawdown
        row.missing_days = cyc.missing_days
        row.peak_board_confident = cyc.peak_board_confident
        if stats is not None:
            row.ma5, row.ma10 = stats.ma5, stats.ma10
            row.ma20, row.ma30 = stats.ma20, stats.ma30
        # 窗口不满时上面几个是 0.0，这个标志让下游能区分"均线是0"和"没攒够"
        row.ma_window_complete = len(bars) >= _MA_MIN_BARS
        row.rs_market_10, row.rs_market_20, row.rs_market_60 = (
            rs_m.get(10), rs_m.get(20), rs_m.get(60))
        row.rs_sector_10, row.rs_sector_20, row.rs_sector_60 = (
            rs_s.get(10), rs_s.get(20), rs_s.get(60))
        row.volume, row.amount = last.volume, last.amount
        row.turnover_rate = last.turnover_rate
        written += 1

    db.commit()
    return {"written": written, "skipped": skipped, "no_cycle": no_cycle}
