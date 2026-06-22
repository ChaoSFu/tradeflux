"""
重点监管（严重异常波动）服务。

M1：权威名单看板——抓取东财名单 → 监管中 / 即将解除两类，join 本地快照补强势指标。
"""
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models.regulatory import RegulatoryUnusual
from ..models.stock import Stock
from ..schemas.regulatory import RegulatoryItem, RegulatoryWatchlistResponse
from ..services.eastmoney_fetcher import fetch_regulatory_unusual
from ..services.strong_stock_service import _enrich_stocks_bulk

ENDING_SOON_DAYS = 3       # 距解除 ≤ 该天数 → 「即将解除」
RECENTLY_RELEASED_DAYS = 15  # 监管期在最近该天数内结束 → 「近期解除」（日历日）


def _parse_date(v) -> Optional[date]:
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _direction(reason_type: Optional[str]) -> Optional[str]:
    if not reason_type:
        return None
    if "涨" in reason_type:
        return "up"
    if "跌" in reason_type:
        return "down"
    return None


def sync_regulatory_unusual(db: Session) -> dict:
    """
    全量替换当前（is_his=0）监管名单。返回 {"count": n, "ok": bool}。
    抓取失败（空）→ 保留旧数据，不清除，ok=False。
    """
    # 注意：东财 IS_HIS 语义与直觉相反——IS_HIS=1 是最新活跃监管，IS_HIS=0 是旧记录。
    # 故两套都拉，按各自 flag 分别全量替换（某套抓取失败则保留其旧数据）。
    count = 0
    ok_any = False
    for ishis in ("0", "1"):
        rows = fetch_regulatory_unusual(is_his=ishis)
        if not rows:
            continue
        ok_any = True
        db.query(RegulatoryUnusual).filter(RegulatoryUnusual.is_his == ishis).delete()
        seen: set[str] = set()
        for r in rows:
            info_code = (r.get("INFO_CODE") or "").strip()
            code = (r.get("SECURITY_CODE") or "").strip()
            if not info_code or not code or info_code in seen:
                continue
            seen.add(info_code)
            db.add(RegulatoryUnusual(
                info_code=info_code,
                security_code=code,
                security_name=(r.get("SECURITY_NAME_ABBR") or "").strip() or None,
                exchange=(r.get("MRAKET_TYPE") or "").strip() or None,
                unusual_type=(r.get("UNUSUAL_TYPE") or "002").strip(),
                reason_type=(r.get("UNUSUAL_REASON_TYPE") or "").strip() or None,
                reason=(r.get("UNUSUAL_REASON") or "").strip() or None,
                start_date=_parse_date(r.get("START_DATE")),
                end_date=_parse_date(r.get("END_DATE")),
                predict_start=_parse_date(r.get("PREDICT_START_DATE")),
                predict_end=_parse_date(r.get("PREDICT_END_DATE")),
                notice_date=_parse_date(r.get("NOTICE_DATE")),
                is_his=ishis,
            ))
            count += 1

    if not ok_any:
        return {"count": 0, "ok": False}
    db.commit()
    return {"count": count, "ok": True}


def get_regulatory_watchlist(db: Session) -> RegulatoryWatchlistResponse:
    """当前监管名单 → 监管中 / 即将解除，join 本地快照补强势指标。"""
    today = date.today()
    recent_floor = today - timedelta(days=RECENTLY_RELEASED_DAYS)
    # IS_HIS=0/1 两套都要（IS_HIS=1 才是最新活跃监管），合并后按监控期判定状态
    records = db.query(RegulatoryUnusual).all()

    # 同一股票可能有多条记录（不同期），按 code 去重保留 predict_end 最晚的一条
    def _dedup_by_code(rows: list) -> list:
        best: dict[str, RegulatoryUnusual] = {}
        for r in rows:
            cur = best.get(r.security_code)
            if cur is None or (r.predict_end or date.min) > (cur.predict_end or date.min):
                best[r.security_code] = r
        return list(best.values())

    # 只关心涨幅累计偏离监管，剔除跌幅类与 ST/退市股
    def _keep(r: RegulatoryUnusual) -> bool:
        name = (r.security_name or "").upper()
        if "ST" in name or "退" in name:
            return False
        return _direction(r.reason_type) != "down"

    records = [r for r in records if _keep(r)]

    # 活跃监管：今日仍在监管期内（predict_end 缺失视为活跃）
    active = _dedup_by_code([
        r for r in records
        if (r.predict_end is None or r.predict_end >= today)
        and (r.predict_start is None or r.predict_start <= today)
    ])
    active_codes = {r.security_code for r in active}
    # 近期解除：监管期已在最近 N 个日历日内结束（且当前未在活跃监管）
    released = _dedup_by_code([
        r for r in records
        if r.predict_end is not None and recent_floor <= r.predict_end < today
        and r.security_code not in active_codes
    ])

    # join 本地快照（命中才补充）
    codes = {r.security_code for r in active} | {r.security_code for r in released}
    stock_map: dict[str, object] = {}
    if codes:
        stocks = db.query(Stock).filter(Stock.code.in_(codes)).all()
        enriched = _enrich_stocks_bulk(stocks, db)
        by_id = {s.id: s for s in stocks}
        for resp in enriched:
            # resp.code 即股票代码
            stock_map[resp.code] = resp
        # 兜底：极少数 enriched 顺序问题，用 id→code 也可，但 resp.code 已足够
        del by_id

    def _to_item(r: RegulatoryUnusual, status: str) -> RegulatoryItem:
        days_remaining = (r.predict_end - today).days if r.predict_end else None
        return RegulatoryItem(
            info_code=r.info_code,
            security_code=r.security_code,
            security_name=r.security_name,
            exchange=r.exchange,
            reason_type=r.reason_type,
            reason=r.reason,
            direction=_direction(r.reason_type),
            start_date=r.start_date,
            end_date=r.end_date,
            predict_start=r.predict_start,
            predict_end=r.predict_end,
            notice_date=r.notice_date,
            days_remaining=days_remaining,
            status=status,
            stock=stock_map.get(r.security_code),  # type: ignore[arg-type]
        )

    monitoring: list[RegulatoryItem] = []
    ending_soon: list[RegulatoryItem] = []
    for r in active:
        dr = (r.predict_end - today).days if r.predict_end else None
        is_ending = dr is not None and dr <= ENDING_SOON_DAYS
        (ending_soon if is_ending else monitoring).append(
            _to_item(r, "ending_soon" if is_ending else "monitoring")
        )
    recently_released = [_to_item(r, "released") for r in released]

    def _rk(it: RegulatoryItem) -> int:
        return it.days_remaining if it.days_remaining is not None else 10**6
    monitoring.sort(key=_rk, reverse=True)   # 监管期长的在前
    ending_soon.sort(key=_rk)                # 最快解除在前
    recently_released.sort(key=_rk, reverse=True)  # 最近解除在前（days_remaining 越接近0越前）

    from ..services.deviation_service import get_approaching_regulation
    # 仅排除「当前监管中」的代码（已在里面，谈不上"进入"）；
    # 「近期解除」的票若重新逼近(o=2) 应保留为风险预警，故不排除。
    exclude_codes = {r.security_code for r in active}
    approaching = get_approaching_regulation(db, exclude_codes=exclude_codes)

    return RegulatoryWatchlistResponse(
        as_of=today,
        monitoring=monitoring,
        ending_soon=ending_soon,
        recently_released=recently_released,
        approaching=approaching,
    )
