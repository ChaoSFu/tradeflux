"""
偏离值服务（M2 指数管道 + M3 即将进入监管预警）。

偏离值 = 个股涨跌幅 − 对应板块基准指数涨跌幅；累计偏离值（连续 N 个交易日）逼近
严重异常波动阈值（10日±100% / 30日±200%）即「即将进入监管」。
"""
import time
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models.market_index import IndexDailySnapshot
from ..models.stock import Stock, StockDailySnapshot
from ..schemas.regulatory import ApproachingItem
from ..services.eastmoney_fetcher import fetch_index_kline
from ..services.strong_stock_service import _enrich_stocks_bulk

# 基准指数：index_code → secid（东财 K线）。如需校准，改这里即可。
INDEX_SECIDS: dict[str, str] = {
    "000001": "1.000001",  # 上证综指 —— 沪市主板
    "399001": "0.399001",  # 深证成指 —— 深市主板
    "399006": "0.399006",  # 创业板指 —— 创业板
    "000688": "1.000688",  # 科创50  —— 科创板
}


def board_index_code(code: str) -> Optional[str]:
    """个股代码 → 对应基准指数 index_code（北交所/其他返回 None）。"""
    if code.startswith("688"):
        return "000688"
    if code.startswith(("30", "31")):
        return "399006"
    if code.startswith("6"):
        return "000001"
    if code.startswith(("00",)):
        return "399001"
    return None


# 严重异常波动阈值（累计偏离值）：(window_days, direction, threshold, 标签)
THRESHOLDS = [
    (10, "up",   100.0, "连续10日涨幅偏离值累计→100%"),
    (30, "up",   200.0, "连续30日涨幅偏离值累计→200%"),
    (10, "down", -50.0, "连续10日跌幅偏离值累计→-50%"),
    (30, "down", -70.0, "连续30日跌幅偏离值累计→-70%"),
]

APPROACH_FLOOR = 0.6   # 接近度 ≥ 该值才进预警区（距阈值 ≤ 40%）
TOP_N = 60


def sync_indices(db: Session, days: int = 70) -> dict:
    """抓取并 upsert 基准指数日线。返回 {"ok": bool, "count": n}。"""
    total = 0
    ok_any = False
    for i, (index_code, secid) in enumerate(INDEX_SECIDS.items()):
        if i:
            time.sleep(1.5)  # 防止东财限流
        bars = []
        for attempt in range(3):  # 最多 3 次，递增退避
            bars = fetch_index_kline(secid, days=days)
            if bars:
                break
            time.sleep(2.0 * (attempt + 1))
        if not bars:
            continue
        ok_any = True
        existing = {
            s.date: s
            for s in db.query(IndexDailySnapshot)
            .filter(IndexDailySnapshot.index_code == index_code)
            .all()
        }
        for b in bars:
            try:
                d = date.fromisoformat(b["date"])
            except (ValueError, KeyError):
                continue
            row = existing.get(d)
            if row:
                row.close = b.get("close")
                row.pct_change = b.get("pct_change")
            else:
                db.add(IndexDailySnapshot(
                    index_code=index_code, date=d,
                    close=b.get("close"), pct_change=b.get("pct_change"),
                ))
                total += 1
    db.commit()
    return {"ok": ok_any, "count": total}


def _index_pct_maps(db: Session) -> dict[str, dict[date, float]]:
    maps: dict[str, dict[date, float]] = {}
    for row in db.query(IndexDailySnapshot).all():
        if row.pct_change is None:
            continue
        maps.setdefault(row.index_code, {})[row.date] = row.pct_change
    return maps


def get_approaching_regulation(db: Session) -> list[ApproachingItem]:
    """
    计算候选池个股的累计偏离值，返回逼近严重异常波动阈值（接近度≥APPROACH_FLOOR）的个股。
    """
    index_maps = _index_pct_maps(db)
    if not index_maps:
        return []

    # 候选池 = stocks 表（强势池 + 涨跌停），取最近 ~45 个交易日快照
    floor_date = date.today() - timedelta(days=70)
    snaps = (
        db.query(StockDailySnapshot.stock_id, StockDailySnapshot.date, StockDailySnapshot.pct_change)
        .filter(StockDailySnapshot.date >= floor_date)
        .all()
    )
    pct_by_stock: dict[int, dict[date, float]] = {}
    for sid, d, pct in snaps:
        if pct is None:
            continue
        pct_by_stock.setdefault(sid, {})[d] = pct

    stocks = db.query(Stock).all()
    results: list[tuple[float, ApproachingItem, Stock]] = []

    for st in stocks:
        idx_code = board_index_code(st.code)
        if not idx_code or idx_code not in index_maps:
            continue
        idx_pct = index_maps[idx_code]
        spct = pct_by_stock.get(st.id)
        if not spct:
            continue
        # 个股与指数共有的交易日（升序），取最近 30 个
        common = sorted(d for d in spct if d in idx_pct)
        if len(common) < 5:
            continue
        recent = common[-30:]

        best_ratio = -1e9
        best: Optional[tuple] = None
        for window, direction, threshold, label in THRESHOLDS:
            win_dates = recent[-window:]
            if len(win_dates) < min(window, 5):
                continue
            cum = sum(spct[d] - idx_pct[d] for d in win_dates)
            ratio = cum / threshold  # 同向时为正，越接近 1 越逼近触发
            if ratio > best_ratio:
                best_ratio = ratio
                best = (window, direction, threshold, label, cum, len(win_dates))

        if best is None or best_ratio < APPROACH_FLOOR:
            continue
        window, direction, threshold, label, cum, coverage = best
        results.append((
            best_ratio,
            ApproachingItem(
                security_code=st.code,
                security_name=st.name,
                direction=direction,
                window=f"{window}d",
                cum_deviation=round(cum, 2),
                threshold=threshold,
                approach=round(best_ratio, 3),
                coverage=coverage,
                full_window=(coverage >= window),
                rule_label=label,
                stock=None,
            ),
            st,
        ))

    results.sort(key=lambda x: x[0], reverse=True)
    top = results[:TOP_N]

    # 富化命中个股
    enriched = {s.code: r for s, r in zip(
        [t[2] for t in top], _enrich_stocks_bulk([t[2] for t in top], db)
    )}
    items: list[ApproachingItem] = []
    for _, item, _st in top:
        item.stock = enriched.get(item.security_code)  # type: ignore[assignment]
        items.append(item)
    return items
