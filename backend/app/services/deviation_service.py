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
from ..services.eastmoney_fetcher import fetch_index_kline, fetch_price_anomaly_list
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
DAILY_PCT_CAP = 21.0   # 单日涨跌幅钳制上限（覆盖各板涨跌停，过滤脏数据）


def _clamp_pct(p: float) -> float:
    return max(-DAILY_PCT_CAP, min(DAILY_PCT_CAP, p))


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


# dycalchis e 字段 → (方向, 阈值, 规则文案)
_RULE_BY_E: dict[int, tuple[str, float, str]] = {
    4: ("up",   100.0, "连续10日涨幅偏离值累计→+100%"),
    6: ("up",   200.0, "连续30日涨幅偏离值累计→+200%"),
    5: ("down", -50.0, "连续10日跌幅偏离值累计→-50%"),
    7: ("down", -70.0, "连续30日跌幅偏离值累计→-70%"),
}


def get_approaching_regulation(db: Session, exclude_codes: Optional[set] = None) -> list[ApproachingItem]:
    """
    「即将进入监管」采用东财实时「严重异动预测」(dycalchis price-anomaly/list)，
    仅取 o=2（东财判定"今日可触发"，已排除如今日下跌+窗口滚动导致无法触发的消退股），
    严重异动四规则(e∈4/5/6/7)，按接近度降序。
    exclude_codes：已在监管名单（活跃/近期解除）的代码，剔除以保证前瞻语义。
    """
    exclude_codes = exclude_codes or set()
    rows = fetch_price_anomaly_list()
    if not rows:
        return []

    # 每只股票保留接近度最高的一条 o=2 规则
    best_by_code: dict[str, tuple[float, dict, tuple]] = {}
    for r in rows:
        if r.get("o") != 2:
            continue  # 仅"今日可触发"的活跃风险
        rule = _RULE_BY_E.get(r.get("e"))
        if not rule or rule[0] != "up":
            continue  # 仅涨幅累计偏离监管（不关心跌幅）
        code = (r.get("c") or "").strip()
        name = (r.get("n") or "").strip()
        x = r.get("x")
        if not code or code in exclude_codes or x is None:
            continue
        if "退" in name or "ST" in name.upper():
            continue  # 剔除退市整理期 + ST 股
        approach = x / rule[1]
        prev = best_by_code.get(code)
        if prev is None or approach > prev[0]:
            best_by_code[code] = (approach, r, rule)

    ranked = sorted(best_by_code.items(), key=lambda kv: kv[1][0], reverse=True)[:TOP_N]
    codes = [c for c, _ in ranked]
    stocks = db.query(Stock).filter(Stock.code.in_(codes)).all() if codes else []
    stock_map = {resp.code: resp for resp in _enrich_stocks_bulk(stocks, db)}

    items: list[ApproachingItem] = []
    for code, (approach, r, rule) in ranked:
        direction, threshold, label = rule
        days = int(r.get("d") or 0)
        items.append(ApproachingItem(
            security_code=code,
            security_name=(r.get("n") or "").strip() or None,
            direction=direction,
            window=f"{days}日",
            cum_deviation=round(float(r.get("x")), 2),
            threshold=threshold,
            approach=round(approach, 3),
            coverage=days,
            full_window=True,
            target_rate=r.get("t"),
            rule_label=label,
            stock=stock_map.get(code),
        ))
    return items
