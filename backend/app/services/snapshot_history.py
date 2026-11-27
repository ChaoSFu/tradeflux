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
from .eastmoney_fetcher import KLineBar, get_limit_pct


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


def snapshots_to_klinebars(snaps: list, code: str = "", is_st: bool = False) -> List[KLineBar]:
    """
    将已排序的 StockDailySnapshot 列表转换为 KLineBar 列表。
    open/high/low 在 compute_window_stats 中未被使用，填 0.0。
    close_price 为 None（老快照迁移前无此字段）时降级为 0.0，
    此时 MA60/MA30 计算结果为 0，阶段判定回退为 "normal"（可接受的保守策略）。

    涨跌停标志：按收盘价 + 0.005 容差重新判定，自动修正历史快照里旧逻辑
    （浮点取整漏判）落库的 is_limit_up/down；判不出来时才降级用存储值。

    **前收必须从这一行自己的 close 和 pct_change 反推，不能取前一根 bar 的收盘价**
    （2026-09-03 修）。快照只在股票进候选池那天才写，序列里有缺口是常态（实测覆盖率
    94.3%），拿缺口另一侧的收盘价当前收，算出来的涨停价必然是错的，而这段重算还会
    **覆盖掉本来正确的存储值**——它本是来修错的，却在缺口处制造新的错。

    实测案例 603989 艾华集团：06-12、06-15 无快照行，于是 06-16（真实 +10.01%，
    快照存的 is_limit_up=t 是对的）被重算成 False，连板链从 06-17 才起算，
    board_count_60d 由 4 变成 3。后果是它被踢出收窄后的强势池——而东财天梯和腾讯
    K 线都确认那天是涨停。

    close 与 pct_change 来自同一行、同一个数据源，天然自洽，跟前后有没有缺口无关：
        prev_close = close / (1 + pct/100)
    验算：34.3 / 1.1001 = 31.179 → 涨停价 round(31.179×1.10, 2) = 34.30
          → 34.3 >= 34.295 → True ✓

    影响面不止强势池：board_count_60d 同时喂着涨停板块雷达的核心召回、破局雷达的
    高度曲线，limit_up_days_60d/20d/10d 也按同一个窗口数。任何快照有缺口的股票
    都会被系统性低估。
    """
    snaps = sorted(snaps, key=lambda s: s.date)
    lp = get_limit_pct(code, is_st) if code else None
    bars: List[KLineBar] = []
    for s in snaps:
        close = s.close_price or 0.0
        is_lu, is_ld = bool(s.is_limit_up), bool(s.is_limit_down)
        # 这一行自己的前收（见 docstring）。pct 缺失时给 0.0 —— 后面的
        # `own_prev > 0` 会挡住，保留存储值而不是拿一个假前收去翻转它
        own_prev = (close / (1 + s.pct_change / 100.0)
                    if close > 0 and s.pct_change is not None
                    and abs(1 + s.pct_change / 100.0) > 1e-9 else 0.0)
        if lp is not None and close > 0 and own_prev > 0:
            actual = lp + 0.1
            lu_price = round(own_prev * (1 + actual / 100), 2)
            ld_price = round(own_prev * (1 - actual / 100), 2)
            # **只单向修正：False → True，绝不 True → False**（2026-09-03）。
            #
            # 这段重算的声明目的是"修正旧逻辑的浮点取整**漏判**"——漏判就是
            # "该 True 却存了 False"。所以它只该补，不该删。
            #
            # 双向改的后果实测出现过两次，每次都是把**正确的 True 改成错的 False**：
            #   603989 06-16  存 True（+10.01% 真涨停）→ 重算 False → 连板 4→3
            #   603773 08-04  存 True（+9.99%  真涨停）→ 重算 False → 连板 4→3
            #
            # 根子在于反推的前收不可能精确：pct_change 只存两位小数（9.99，真实
            # 9.9938），反推前收带 ~0.002 误差，round 到分一放大就跨过 0.005 容差
            #   71.43/1.0999 = 64.9422 → round(64.9422×1.10, 2) = 71.44
            #   而真实前收 64.94    → round(64.94  ×1.10, 2) = 71.43 = 收盘价
            # 边界上差一分钱，判定就翻。而**存储值是当时用真前收算的，比这个准**。
            #
            # 用前一根 bar 的收盘价当前收更糟（快照有缺口时直接取到几天前的价），
            # 那是这个 bug 的上一版形态。两种反推都不可靠，所以：能补则补，不动已有。
            if close >= lu_price - 0.005:
                is_lu = True
            if close <= ld_price + 0.005:
                is_ld = True
        # OHLC：2026-08-27 起快照会落库真实值；NULL 是加列之前写的老行，退回 0.0。
        # 有 high 就能重算炸板/一字板，没有就只能沿用当初落库的标志（老行为）。
        o, h, lo = s.open_price, s.high_price, s.low_price
        has_ohlc = h is not None and lo is not None and h > 0
        is_bb, is_owu, is_owd = (bool(s.is_broken_board),
                                 bool(s.is_one_word_limit_up),
                                 bool(s.is_one_word_limit_down))
        # 炸板/一字板用同一个自洽前收，不能再回头用前一根 bar 的收盘价——
        # 缺口处那个值是错的，涨停判定已经因它翻过一次
        if has_ohlc and lp is not None and close > 0 and own_prev > 0:
            actual = lp + 0.1
            lu_price = round(own_prev * (1 + actual / 100), 2)
            ld_price = round(own_prev * (1 - actual / 100), 2)
            # 炸板同理只单向补：涨停判定既然只补不删，炸板也不能反过来把
            # 已知的炸板抹掉。一字板需要 is_lu 成立，跟着上面的结果走
            if (not is_lu) and h >= lu_price - 0.005:
                is_bb = True
            is_owu = is_owu or (is_lu and lo >= lu_price - 0.005)
            is_owd = is_owd or (is_ld and h <= ld_price + 0.005)
        bars.append(KLineBar(
            date=s.date,
            open_price=o or 0.0,
            close_price=close,
            high_price=h or 0.0,
            low_price=lo or 0.0,
            pct_change=s.pct_change or 0.0,
            turnover_rate=s.turnover_rate,   # None 保持 None＝未知，不降级成0.0
            # 成交量/成交额/来源口径（2026-09-03 补）。首版漏了这三个，而绝大多数
            # 股票走的正是 DB 重建这条路（日志里"DB重建 230 只"），结果生产上
            # leader_cycle_snapshots 的 volume 和 turnover_rate 全部为空——
            # dump 和腾讯/新浪那两条路都接了，唯独这里没接，链条断在中间。
            volume=s.volume, amount=s.amount, volume_source=s.volume_source,
            is_limit_up=is_lu,
            is_limit_down=is_ld,
            is_broken_board=is_bb,
            is_one_word_limit_up=is_owu,
            is_one_word_limit_down=is_owd,
        ))
    return bars
