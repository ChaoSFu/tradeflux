"""
个股停牌日：记录、读取、换算成「这只票自己的交易日」。表见 models.stock.StockSuspensionDay。

为什么要有：2026-09 龙版传媒（605577）09-09~09-11 停牌。日更拿实时行情补当日 bar 时，
腾讯对停牌股报的现价就是昨收、成交量 0，于是库里多了三根「涨跌 0、成交量 0」的假 bar；
龙头周期那边又没人告诉它停牌，把复牌那天当成断板后的第 4 天——它只是断板后第 1 个交易日。

用法只有一个口径：`stock_calendar(市场交易日, 停牌日)`。凡是按交易日判「相邻」、数「隔了
几天」的地方（连板计数、龙头周期识别、状态机），对单只股票都用它，不各写一套。
"""
from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot, StockSuspensionDay


def stock_calendar(cal: Optional[Sequence[date]], suspended: Optional[Iterable[date]],
                   traded: Optional[Iterable[date]] = None) -> Optional[List[date]]:
    """
    这只票自己的交易日：市场交易日去掉它停牌的日子。没有日历（None）原样返回 None。

    `traded` 是手上有成交证据的日子（它那天有 K 线）。**有成交的证据优先**：万一某条停牌
    记录是错的（比如 dump 那天恰好漏了它），也不能把真交易过的一天从它的日历里抠掉——
    抠掉了，那根 bar 两边就不再「相邻」，一段连板会被切断。
    """
    if cal is None:
        return None
    s = set(suspended or ()) - set(traded or ())
    return [d for d in cal if d not in s] if s else list(cal)


def suspended_days_from_rows(dates_by_code: Dict[str, Iterable[date]],
                             market_days: Sequence[date]) -> Dict[str, List[date]]:
    """
    权威日K源（fuyao dump / 10 年存档）**停牌期间没有行**：某只票在它第一根之后、源的最后
    一天之前缺的交易日 = 停牌（至今没复牌的，最后一根之后的也算）。第一根之前的不算——那时
    可能还没上市，也可能只是源没往前取。

    market_days 必须是这份源覆盖范围内的**交易日**，不能拿「库里有哪些日期」顶替。
    """
    days = sorted(set(market_days))
    out: Dict[str, List[date]] = {}
    for code, ds in dates_by_code.items():
        have = set(ds)
        if not have:
            continue
        first = min(have)
        gone = [d for d in days if d > first and d not in have]
        if gone:
            out[code] = gone
    return out


def record_suspensions(db: Session, entries: Iterable[Tuple[int, date]], source: str) -> int:
    """幂等写入，已有的跳过。返回新增条数。调用方负责 commit。"""
    want = {(sid, d) for sid, d in entries if sid is not None and d is not None}
    if not want:
        return 0
    have = {(sid, d) for sid, d in db.query(StockSuspensionDay.stock_id, StockSuspensionDay.date)
            .filter(StockSuspensionDay.stock_id.in_({s for s, _ in want}),
                    StockSuspensionDay.date.in_({d for _, d in want}))}
    new = sorted(want - have)
    db.add_all([StockSuspensionDay(stock_id=s, date=d, source=source) for s, d in new])
    db.flush()
    return len(new)


def load_suspensions_by_code(db: Session, codes: Optional[Iterable[str]] = None,
                             since: Optional[date] = None) -> Dict[str, Set[date]]:
    """{股票代码: {停牌日}}。一条查询。"""
    q = (db.query(Stock.code, StockSuspensionDay.date)
         .join(Stock, Stock.id == StockSuspensionDay.stock_id))
    if codes is not None:
        codes = list(codes)
        if not codes:
            return {}
        q = q.filter(Stock.code.in_(codes))
    if since is not None:
        q = q.filter(StockSuspensionDay.date >= since)
    out: Dict[str, Set[date]] = defaultdict(set)
    for code, d in q:
        out[code].add(d)
    return dict(out)


# ── 以前误写进快照的零成交假行 ──────────────────────────────────────────────────

def zero_volume_rows(db: Session) -> List[tuple]:
    """
    成交量**恰好为 0** 的日快照：[(行id, 股票id, 代码, 名称, 日期)]。

    A 股有成交才有日 K，一字板也有成交；零成交就是那天没交易——停牌日被当成交易日写进来的
    假 bar（2026-09-15 之前行情兜底会这样写）。volume 为 NULL 的老行不在内：那是「不知道」，
    不是 0。
    """
    return (db.query(StockDailySnapshot.id, StockDailySnapshot.stock_id, Stock.code,
                     Stock.name, StockDailySnapshot.date)
            .join(Stock, Stock.id == StockDailySnapshot.stock_id)
            .filter(StockDailySnapshot.volume == 0)
            .order_by(Stock.code, StockDailySnapshot.date).all())


def convert_zero_volume_rows(db: Session, apply: bool = False) -> dict:
    """
    把零成交假行转成停牌记录、删掉假行；同一天的龙头周期快照是拿这根假 bar 算的
    （当天有「价格事实」、涨跌 0），留着会让状态机把停牌日当成一次观测，一起删。
    连板、龙头周期等下一次日更按新口径重算。apply=False 只统计。
    """
    rows = zero_volume_rows(db)
    per: Dict[str, dict] = {}
    for _rid, _sid, code, name, d in rows:
        per.setdefault(code, {"name": name, "dates": []})["dates"].append(d)
    out = {"rows": len(rows), "stocks": len(per), "per_stock": per, "applied": apply,
           "recorded": 0, "deleted": 0, "cycle_rows_deleted": 0}
    if not apply or not rows:
        return out
    from ..models.leader_cycle import LeaderCycleSnapshot
    pairs = [(sid, d) for _rid, sid, _c, _n, d in rows]
    out["recorded"] = record_suspensions(db, pairs, "zero_volume_row")
    out["deleted"] = (db.query(StockDailySnapshot)
                      .filter(StockDailySnapshot.id.in_([rid for rid, *_ in rows]))
                      .delete(synchronize_session=False))
    out["cycle_rows_deleted"] = sum(
        db.query(LeaderCycleSnapshot)
        .filter(LeaderCycleSnapshot.stock_id == sid, LeaderCycleSnapshot.date == d)
        .delete(synchronize_session=False)
        for sid, d in pairs)
    db.commit()
    return out
