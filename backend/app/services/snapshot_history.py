"""
往快照表里补**历史日**的 K 线原始字段（2026-09-11 从 daily_update._backfill_history_from_dump 抽出）。

原来这段只写在 10 日 dump 补历史那一处。现在 10 年存档补洞也要写同样的行——规则
只能有一份，否则迟早两边写出来的行不一样。这个仓库已经有一个现成的反例：
full_group 自举那一段没写 is_settled，于是它补的历史行全是 is_settled=False
（列定义 default=False），而 dump 补的是 True。那一处这次没动（is_settled 写入端的
统一是另立的事），但新增的调用方一律走这里。

三条规则：
1. **只写 date < target_date 的历史日**，当日那一行永远归主流程。
2. **已存在的行绝不覆盖**。唯一例外：原为 NULL 的 volume/amount 补上
   （2026-09-03 加这两列之前写下的行全是空的）。
3. 只写 K 线原始字段（OHLC、涨跌幅、涨跌停标志、量额），不写连板数、评分这些
   窗口统计——那些要完整窗口才算得准。换手率一律 None：dump 和存档都没有。
"""
from datetime import date
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from ..models.stock import StockDailySnapshot


def insert_history_bars(db: Session, bars_map: Dict[str, list], sid_by_code: Dict[str, int],
                        target_date: date, *, only_dates: Optional[Set[date]] = None,
                        dry_run: bool = False,
                        counts: Optional[Dict[str, int]] = None) -> Tuple[int, int]:
    """
    返回 (新建行数, 补上量额的已有行数)。dry_run=True 时只数不写。

    only_dates：只补这些日期（存档补洞用它把范围卡在最近 N 个交易日）。
    counts：给了就按股票累计新建行数，供统计缺得最多的票。
    """
    def _want(bar) -> bool:
        return (bar.date < target_date and (bar.close_price or 0) > 0
                and (only_dates is None or bar.date in only_dates))

    dates = sorted({b.date for bars in bars_map.values() for b in bars if _want(b)})
    sids = [sid_by_code[c] for c in bars_map if c in sid_by_code]
    if not dates or not sids:
        return 0, 0

    # **只取键和量，不取整行 ORM 对象**：存档补洞一次要看 65 天 × 两千多只，
    # 整行加载就是 405MB 那次事故（/leader-cycle/effect 把整张快照表读进来）的读法
    existing: Dict[Tuple[int, date], Tuple[int, Optional[float]]] = {}
    for i in range(0, len(sids), 1000):
        q = (db.query(StockDailySnapshot.id, StockDailySnapshot.stock_id,
                      StockDailySnapshot.date, StockDailySnapshot.volume)
             .filter(StockDailySnapshot.stock_id.in_(sids[i:i + 1000]),
                     StockDailySnapshot.date >= dates[0],
                     StockDailySnapshot.date <= dates[-1]))
        for rid, sid, d, vol in q:
            existing[(sid, d)] = (rid, vol)

    added = 0
    created: Set[Tuple[int, date]] = set()
    vol_updates: List[dict] = []
    for code, bars in bars_map.items():
        sid = sid_by_code.get(code)
        if not sid:
            continue
        for bar in bars:
            if not _want(bar):
                continue
            key = (sid, bar.date)
            if key in created:
                continue
            hit = existing.get(key)
            if hit is not None:
                rid, vol = hit
                # 行已存在：只补原为空的量额，其余一概不碰——覆盖已有值会让补历史
                # 变成一次静默的历史重写
                if vol is None and bar.volume is not None:
                    vol_updates.append({"id": rid, "volume": bar.volume, "amount": bar.amount,
                                        "volume_source": bar.volume_source})
                continue
            created.add(key)
            added += 1
            if counts is not None:
                counts[code] = counts.get(code, 0) + 1
            if dry_run:
                continue
            db.add(StockDailySnapshot(
                stock_id=sid, date=bar.date,
                close_price=round(bar.close_price, 4),
                pct_change=round(bar.pct_change or 0.0, 4),
                # 0 表示这根 bar 自己就没有，落 NULL——0.0 会被下游当成"最高价是 0 元"
                open_price=(round(bar.open_price, 4) if bar.open_price else None),
                high_price=(round(bar.high_price, 4) if bar.high_price else None),
                low_price=(round(bar.low_price, 4) if bar.low_price else None),
                turnover_rate=None,          # dump / 存档都不提供换手率，None＝不知道，不写 0
                # 来源标记必须跟量一起写——fuyao 未复权、腾讯 qfq，复权会同时调整价和量
                volume=bar.volume, amount=bar.amount, volume_source=bar.volume_source,
                is_limit_up=bar.is_limit_up,
                is_limit_down=bar.is_limit_down,
                is_broken_board=bar.is_broken_board,
                is_one_word_limit_up=bar.is_one_word_limit_up,
                is_one_word_limit_down=bar.is_one_word_limit_down,
                is_settled=True,             # 收盘后生成的数据，历史日必然是终值
            ))
            if added % 2000 == 0:
                db.commit()
    if dry_run:
        return added, len(vol_updates)
    for i in range(0, len(vol_updates), 2000):
        db.bulk_update_mappings(StockDailySnapshot, vol_updates[i:i + 2000])
    if added or vol_updates:
        db.commit()
    return added, len(vol_updates)
