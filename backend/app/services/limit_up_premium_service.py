"""
股性 · 涨停次日溢价 —— 每只票「库里有记录的每一次涨停，次一个交易日的涨跌幅」的平均。

回答：这只票涨停之后，第二天通常还有没有溢价。用户原话（2026-09-13）：「评估这个股票的
股性，涨停后有多高的溢价，统计有记录的涨停次日的溢价，求平均涨跌幅」。

## 口径

- 涨停：StockDailySnapshot.is_limit_up——全仓涨停的唯一口径（板块涨停数、买入检查用的也是它）。
- 次日：**交易日历**上的下一个交易日，不是这只票的下一行快照。停牌、快照缺口会让「下一行」
  跨好几天，那样算出来的不是次日溢价——这种样本直接不要，不当 0，也不拿后面的日子顶。
- 次日涨跌幅：那天快照的 pct_change（收盘对前收）。
- 连板也算：涨停第二天又涨停，那一次的溢价就是第二天的涨幅——股性本身就体现在这里。
  一字板也算：它照样是涨停，只是买不进。
- 哪些行算数：跟生命周期赚钱效应（leader_cycle_effect_service）、scripts/evaluate_lifecycle.py
  同一条规则——**只排除最新一天没结算的行**。is_settled 2026-05-28 才开始有值，更早的行全是
  False，那是收盘价；硬过滤会把老样本系统性扔掉。
- 样本数一起存。只涨停过一两次的平均值参考意义有限，界面上要让人看得出来。
- 只看 as_of（含）之前的数据，不偷看未来。

## 性能

一条窗口函数查询（LEAD 取每只票的下一行快照），数据库那边扫全表，回来的只有涨停那些行。
只在日更收盘那一跑和命令行里算，接口只读 Stock 上的两个字段。
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy import Boolean, Date, Float, func
from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot


def compute_limit_up_premium(db: Session, as_of: Optional[date] = None,
                             cal: Optional[Sequence[date]] = None) -> dict:
    """
    算，不写库。返回：
      as_of          用到哪天为止（默认库里最新一天）
      calendar       "trading_calendar" / "snapshot_dates"（没传日历时用快照日期凑，日更和命令行都会传）
      by_stock       {stock_id: (平均次日涨跌幅, 样本数)}，只含有样本的票
      samples        样本总数
      skipped_gap    次一交易日没有这只票的快照（停牌 / 缺口）或没有涨跌幅，没算
      skipped_live   次日就是最新一天、还没结算，没算
    """
    S = StockDailySnapshot
    empty = {"as_of": None, "calendar": None, "by_stock": {}, "samples": 0,
             "skipped_gap": 0, "skipped_live": 0}
    if as_of is None:
        as_of = db.query(func.max(S.date)).scalar()
        if as_of is None:
            return empty
    if cal:
        cal_source, days = "trading_calendar", sorted(d for d in set(cal) if d <= as_of)
    else:
        cal_source = "snapshot_dates"
        days = sorted(d for (d,) in db.query(S.date).filter(S.date <= as_of).distinct())
    next_day = dict(zip(days, days[1:]))

    # LEAD 的结果类型要写明：不写的话 SQLite 会把日期原样回成字符串、布尔回成 0/1
    over = {"partition_by": S.stock_id, "order_by": S.date}
    sub = (db.query(S.stock_id.label("sid"), S.date.label("d"), S.is_limit_up.label("lu"),
                    func.lead(S.date, type_=Date).over(**over).label("nd"),
                    func.lead(S.pct_change, type_=Float).over(**over).label("npct"),
                    func.lead(S.is_settled, type_=Boolean).over(**over).label("nset"))
           .filter(S.date <= as_of)
           .subquery())
    rows = (db.query(sub.c.sid, sub.c.d, sub.c.nd, sub.c.npct, sub.c.nset)
            .filter(sub.c.lu.is_(True)).all())

    acc: Dict[int, List[float]] = defaultdict(list)
    gap = live = 0
    for sid, d, nd, npct, nset in rows:
        if nd is None:
            continue                          # 最近这次涨停还没有次日
        if nd != next_day.get(d) or npct is None:
            gap += 1                          # 次一个交易日没有这只票的快照（停牌 / 缺口）
            continue
        if nd == as_of and not nset:
            live += 1                         # 次日就是今天、还没收盘：盘中价不算溢价
            continue
        acc[sid].append(npct)
    return {"as_of": as_of, "calendar": cal_source,
            "by_stock": {sid: (round(sum(v) / len(v), 2), len(v)) for sid, v in acc.items()},
            "samples": sum(len(v) for v in acc.values()),
            "skipped_gap": gap, "skipped_live": live}


def refresh_limit_up_premium(db: Session, as_of: Optional[date] = None,
                             cal: Optional[Sequence[date]] = None) -> dict:
    """算完写回 Stock。没有样本的票写 None / 0（把旧值清掉），只更新值变了的行。"""
    r = compute_limit_up_premium(db, as_of=as_of, cal=cal)
    r["updated"] = 0
    if r["as_of"] is None:
        return r
    by = r["by_stock"]
    changes = []
    for sid, avg, n in db.query(Stock.id, Stock.limit_up_next_avg_pct, Stock.limit_up_next_samples):
        new_avg, new_n = by.get(sid, (None, 0))
        same_avg = (avg is None and new_avg is None) or (
            avg is not None and new_avg is not None and abs(avg - new_avg) < 1e-9)
        if not same_avg or (n or 0) != new_n:
            changes.append({"id": sid, "limit_up_next_avg_pct": new_avg,
                            "limit_up_next_samples": new_n})
    if changes:
        db.bulk_update_mappings(Stock, changes)
        db.commit()
    r["updated"] = len(changes)
    return r
