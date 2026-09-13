"""
「这一天的快照是不是收盘终值」—— 全仓唯一的判定（2026-09-10 提取）。

原来这段逻辑只写在 strong_stock_service.list_limit_moves 里。现在涨跌停分析页的
逐日曲线也要回答同一个问题（最后一个点是不是盘中价算出来的），再抄一份就是
本项目第 N 次踩「同一个市场事实两套判定函数」，所以先提出来。

判定规则本身很短，但每一条都是踩出来的：

· **同一天的行可能一部分已结算一部分没有**。盘中跑过日更、收盘后个别股票补过，
  两种时点的行混在同一个 date 下。所以取「全都结算了才算结算」——只要还有一行
  是盘中值，这一天的任何跨股统计就都不是收盘结果。

· **一行都没有 → None（不知道），不是 False**。「这天没数据」和「这天没收盘」
  是两回事，前者不该被渲染成"盘中"。

· **is_settled 本身可能是 NULL**。该字段 2026-05-28 才加，之前的历史行全是 NULL。
  `bool(None) is False` 正是我们要的——不知道就不能算已结算。
"""
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.stock import StockDailySnapshot


def settled_by_date(db: Session, dates: Iterable[date]) -> Dict[date, Optional[bool]]:
    """
    一次问多天。**一条 distinct 查询**（每天最多回 3 行：True/False/NULL），
    不要按天循环——曲线要问 60 天。

    返回的字典对传进来的每一天都有 key，值为 True / False / None（库里没这天）。
    """
    wanted = sorted(set(dates))
    if not wanted:
        return {}
    rows = (
        db.query(StockDailySnapshot.date, StockDailySnapshot.is_settled)
        .filter(StockDailySnapshot.date.in_(wanted))
        .distinct()
        .all()
    )
    flags: Dict[date, List[Optional[bool]]] = {}
    for d, flag in rows:
        flags.setdefault(d, []).append(flag)
    return {d: (all(bool(f) for f in flags[d]) if d in flags else None) for d in wanted}


def date_is_settled(db: Session, d: date) -> Optional[bool]:
    """单日版。True=收盘终值 / False=还有盘中行 / None=库里没这天。"""
    return settled_by_date(db, [d])[d]


def settlement_counts(db: Session, dates: Iterable[date]) -> Dict[date, Tuple[int, int]]:
    """
    {日期: (已结算行数, 总行数)}，库里没这天 → (0, 0)。一条分组查询。

    settled_by_date 回答「这一天是不是**全都**结算了」，给跨股统计定性用。可生产上过去的
    交易日几乎都过不了这一关：2026-09-13 实测 09-04 ~ 09-10 每天都是 False，只有最新
    那天是 True——少数股票收盘那一跑没拉到，行还停在盘中那一跑的状态。

    板块趋势要问的是另一件事：「这一天整体是不是收盘后的数据」。盘前 09:27 那一跑写的
    是 0/N，收盘后是 N-少数/N，看比例才分得开；少数没结算的行照实写进证据。
    """
    wanted = sorted(set(dates))
    if not wanted:
        return {}
    out = {d: [0, 0] for d in wanted}
    for d, flag, n in (
            db.query(StockDailySnapshot.date, StockDailySnapshot.is_settled,
                     func.count(StockDailySnapshot.id))
            .filter(StockDailySnapshot.date.in_(wanted))
            .group_by(StockDailySnapshot.date, StockDailySnapshot.is_settled)):
        out[d][1] += n
        if flag:
            out[d][0] += n
    return {d: (s, t) for d, (s, t) in out.items()}
