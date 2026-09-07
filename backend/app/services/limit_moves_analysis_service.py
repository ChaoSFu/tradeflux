"""
涨跌停分析页的两个跨日统计 —— **只计数，不打分**。

这里刻意不产出「接力分」「板块延续分」之类的合成指标。晋级率就是晋级率，
延续个数就是延续个数；把它们加权成一个数，就是又造一个说不清口径的黑箱。

两条贯穿全文的纪律：

1. **T-1 必须是交易日历上的前一个交易日**，不是"数据库里上一条记录"。
   这个仓库为「数组下标相邻 ≠ 交易日相邻」栽过三次（见 docs/DATA_SOURCES.md 坑16）。
   拿不到日历就返回空 + 说明，不用快照日期反推——那会把隔着一个开市日的两天
   判成相邻。

2. **「今天没有这只票的行」是第三种结果，不是「断板」。**
   previous = advanced + broken + unknown。把 unknown 并进 broken，停牌和退市
   会被算成断板，晋级率的分母也就跟着虚高。
"""
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from ..models.sector import Sector, StockSectorRelation
from ..models.stock import StockDailySnapshot
from .trading_calendar import get_trading_days


def _resolve_dates(db: Session, trade_date: Optional[date]):
    """(今天, 前一个交易日, notes)。任一拿不到就是 None，不猜。"""
    notes: List[str] = []
    today = trade_date or db.query(sqlfunc.max(StockDailySnapshot.date)).scalar()
    if today is None:
        return None, None, ["库里没有任何快照"]
    cal = get_trading_days(db, need_through=today)
    if not cal:
        # **不用快照日期反推。** 假如某天日更整个挂掉、一行都没写，日期集合会把
        # 隔着一个开市日的两天判成相邻，晋级率就成了跨两天的统计
        return today, None, ["拿不到交易日历，无法确定前一个交易日"]
    try:
        i = cal.index(today)
    except ValueError:
        return today, None, [f"{today} 不在交易日历里"]
    if i == 0:
        return today, None, ["日历里没有更早的交易日"]
    return today, cal[i - 1], notes


def _snapshots(db: Session, d: date) -> Dict[int, StockDailySnapshot]:
    return {r.stock_id: r for r in
            db.query(StockDailySnapshot).filter(StockDailySnapshot.date == d).all()}


def compute_advance_ladder(db: Session, trade_date: Optional[date] = None) -> dict:
    """
    分板位晋级：昨天 N 板的票，今天有多少继续涨停。

    `from_board = 1` 那一行读作「昨日首板 → 今日 2 板」。

    晋级 = 今日 `is_limit_up`（口径跟涨跌停总览一致）。分母用**今天有行的那些**
    （observed），不是昨天的总数——停牌的票既没晋级也没断板，把它算进分母只会
    让晋级率无故变低。两个分母都返回，谁想换口径都算得出来。
    """
    today, prev, notes = _resolve_dates(db, trade_date)
    empty = {"trade_date": today, "prev_date": prev, "rows": [], "notes": notes}
    if today is None or prev is None:
        return empty

    prev_rows = _snapshots(db, prev)
    today_rows = _snapshots(db, today)
    if not prev_rows:
        return {**empty, "notes": notes + [f"{prev} 没有快照"]}

    buckets: Dict[int, dict] = defaultdict(
        lambda: {"previous_count": 0, "advanced_count": 0,
                 "broken_count": 0, "unknown_count": 0})
    for sid, r in prev_rows.items():
        if not r.is_limit_up:
            continue
        # board_count 缺失 = 不知道它昨天是几板，**不当成 1 板**
        if r.board_count is None or r.board_count <= 0:
            continue
        b = buckets[int(r.board_count)]
        b["previous_count"] += 1
        t = today_rows.get(sid)
        if t is None:
            b["unknown_count"] += 1          # 停牌 / 退市 / 今天没抓到
        elif t.is_limit_up:
            b["advanced_count"] += 1
        else:
            b["broken_count"] += 1

    rows = []
    for from_board in sorted(buckets):
        b = buckets[from_board]
        observed = b["advanced_count"] + b["broken_count"]
        rows.append({
            "from_board": from_board,
            "to_board": from_board + 1,
            "previous_count": b["previous_count"],
            "observed_count": observed,
            "advanced_count": b["advanced_count"],
            "broken_count": b["broken_count"],
            # 今天没有这只票的行。**它既不是晋级也不是断板**
            "unknown_count": b["unknown_count"],
            # 分母是 observed。一只都没观测到就是算不出，不是 0%
            "advance_ratio": (round(b["advanced_count"] / observed, 3)
                              if observed else None),
        })

    unknown_total = sum(r["unknown_count"] for r in rows)
    if unknown_total:
        notes = notes + [
            f"{unknown_total} 只昨日涨停股今天没有快照（停牌 / 退市 / 未抓到），"
            "既不计入晋级也不计入断板；晋级率的分母是今天有行的那些。"]
    return {"trade_date": today, "prev_date": prev, "rows": rows, "notes": notes}


def compute_sector_continuation(db: Session, trade_date: Optional[date] = None) -> dict:
    """
    板块跨日延续：昨天强的板块今天是否还强。

    **只出计数，不出 continuation_score。**「延续 3 只、新增 5 只」跟「延续 5 只、
    新增 3 只」哪个更强，看的人自己判断——那个判断依赖当天的位置和情绪，不是一个
    权重能固定下来的。

    归组走 `StockSectorRelation` + `Sector.is_watched`，跟涨停板块雷达同一套；
    **不拿涨停原因文本动态造板块**。
    """
    today, prev, notes = _resolve_dates(db, trade_date)
    empty = {"trade_date": today, "prev_date": prev, "rows": [], "notes": notes}
    if today is None or prev is None:
        return empty

    watched = {s.id: s.name for s in
               db.query(Sector).filter(Sector.is_watched == True).all()}  # noqa: E712
    if not watched:
        return {**empty, "notes": notes + ["没有任何关注板块（is_watched）"]}

    members: Dict[int, List[int]] = defaultdict(list)
    for sec_id, sid in (db.query(StockSectorRelation.sector_id,
                                 StockSectorRelation.stock_id)
                        .filter(StockSectorRelation.sector_id.in_(watched)).all()):
        members[sec_id].append(sid)

    prev_rows, today_rows = _snapshots(db, prev), _snapshots(db, today)
    if not prev_rows:
        return {**empty, "notes": notes + [f"{prev} 没有快照"]}

    rows = []
    for sec_id, name in watched.items():
        sids = members.get(sec_id) or []
        y_lu = [s for s in sids
                if (r := prev_rows.get(s)) is not None and r.is_limit_up]
        cont = new = broken = unknown = 0
        for s in y_lu:
            t = today_rows.get(s)
            if t is None:
                unknown += 1
            elif t.is_limit_up:
                cont += 1
            else:
                broken += 1
        y_set = set(y_lu)
        for s in sids:
            t = today_rows.get(s)
            if t is not None and t.is_limit_up and s not in y_set:
                new += 1
        ld = sum(1 for s in sids
                 if (t := today_rows.get(s)) is not None and t.is_limit_down)
        if not (y_lu or new or ld):
            continue                     # 昨天今天都没动静的板块不占行
        rows.append({
            "sector_id": sec_id,
            "sector_name": name,
            "yesterday_limit_up_count": len(y_lu),
            "today_continued_limit_up_count": cont,
            "today_new_limit_up_count": new,
            "today_broken_count": broken,
            # 昨日涨停但今天没有快照的。不并进 broken
            "today_unknown_count": unknown,
            "today_limit_down_count": ld,
            # 分母是今天观测到的那些，一只都没有就是算不出
            "continuation_ratio": (round(cont / (cont + broken), 3)
                                   if (cont + broken) else None),
        })

    rows.sort(key=lambda r: (-r["yesterday_limit_up_count"],
                             -r["today_continued_limit_up_count"]))
    return {"trade_date": today, "prev_date": prev, "rows": rows,
            "notes": notes + ["归组走 StockSectorRelation（关注板块），"
                              "一只股票可以同时出现在多个板块里，所以各行相加"
                              "大于全市场涨停数。"]}
