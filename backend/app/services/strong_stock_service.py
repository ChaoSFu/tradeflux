"""
Strong Stock Pool — qualification, classification, and enrichment logic.

A stock qualifies for the strong-stock pool if ANY of these hold:
  1. highest_board_count_60d > 3
  2. limit_up_days_60d > 9
  3. limit_up_days_10d > 4
  4. top_10_pct_change_20d == True

Scope: main-board only, non-ST, non-new-stock.
"""
from collections import defaultdict
from typing import List, Optional

from sqlalchemy import and_, or_, case, func as sqlfunc
from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot
from ..models.sector import StockSectorRelation, Sector
from ..schemas.stock import StockResponse, StockListResponse, LimitMoveTrendPoint


# ---------------------------------------------------------------------------
# Sector tag filter
# ---------------------------------------------------------------------------

def _should_show_sector_tag(sector: Sector) -> bool:
    """
    Return True if this sector should appear as a tag on the stock row.
    Visibility is entirely controlled by the `is_watched` flag,
    which can be toggled via the sector management page.
    """
    return bool(getattr(sector, "is_watched", False))


# ---------------------------------------------------------------------------
# Single-stock enrichment (used for /stocks/{code} endpoint)
# ---------------------------------------------------------------------------

def _enrich_stock_response(stock: Stock, db: Session) -> StockResponse:
    """Attach sector context + today's limit-up status to a single stock."""
    rels = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.stock_id == stock.id)
        .all()
    )

    # Latest snapshot for limit-up status
    latest_snap = (
        db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.stock_id == stock.id)
        .order_by(StockDailySnapshot.date.desc())
        .first()
    )

    data = StockResponse.model_validate(stock)
    data.today_is_limit_up = bool(latest_snap.is_limit_up) if latest_snap else False
    data.today_pct_change = latest_snap.pct_change if latest_snap else None
    data.today_board_count = latest_snap.board_count if latest_snap else None
    data.today_limit_down_count = latest_snap.limit_down_count if latest_snap else None

    # ── 主板块：直接读落库值（与仪表盘/龙头等模块一致）─────────────────────
    if stock.primary_sector_id and stock.primary_sector_name:
        psec = db.query(Sector).filter(Sector.id == stock.primary_sector_id).first()
        data.primary_sector = stock.primary_sector_name
        data.sector_id = stock.primary_sector_id
        data.sector_phase = psec.phase if psec else None
        for rel in rels:
            if rel.sector_id == stock.primary_sector_id:
                data.is_leader = rel.is_leader
                break

    # ── 板块标签：所有 is_watched 板块 ──────────────────────────────────
    sector_names: List[str] = []
    for rel in rels:
        sector = db.query(Sector).filter(Sector.id == rel.sector_id).first()
        if sector and _should_show_sector_tag(sector):
            sector_names.append(sector.name)

    data.sectors = sector_names
    return data


# ---------------------------------------------------------------------------
# Bulk enrichment (used for list endpoints — avoids N+1 queries)
# ---------------------------------------------------------------------------

def _enrich_stocks_bulk(stocks: List[Stock], db: Session) -> List[StockResponse]:
    """
    Enrich a list of stocks with sector tags and today's limit-up status.
    Uses 3 bulk queries regardless of list size (avoids N+1).
    """
    if not stocks:
        return []

    stock_ids = [s.id for s in stocks]

    # ── 1. All sector relations ────────────────────────────────────────────
    rels = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.stock_id.in_(stock_ids))
        .all()
    )
    sector_id_set = {r.sector_id for r in rels}
    sector_map: dict[int, Sector] = {}
    if sector_id_set:
        sector_map = {
            s.id: s
            for s in db.query(Sector).filter(Sector.id.in_(sector_id_set)).all()
        }

    rels_by_stock: dict[int, list] = defaultdict(list)
    for rel in rels:
        rels_by_stock[rel.stock_id].append(rel)

    # ── 2. Latest snapshot per stock ─────────────────────────────────────
    subq = (
        db.query(
            StockDailySnapshot.stock_id,
            sqlfunc.max(StockDailySnapshot.date).label("max_date"),
        )
        .filter(StockDailySnapshot.stock_id.in_(stock_ids))
        .group_by(StockDailySnapshot.stock_id)
        .subquery()
    )
    snaps_list = (
        db.query(StockDailySnapshot)
        .join(
            subq,
            and_(
                StockDailySnapshot.stock_id == subq.c.stock_id,
                StockDailySnapshot.date == subq.c.max_date,
            ),
        )
        .all()
    )
    snap_map: dict[int, StockDailySnapshot] = {s.stock_id: s for s in snaps_list}

    # ── 3. primary_sector_id → Sector (批量，用于 sector_phase 展示) ────────
    primary_sids = {s.primary_sector_id for s in stocks if s.primary_sector_id}
    primary_sector_objs: dict[int, Sector] = {}
    if primary_sids:
        primary_sector_objs = {
            s.id: s for s in db.query(Sector).filter(Sector.id.in_(primary_sids)).all()
        }

    # ── 4. Build responses ────────────────────────────────────────────────
    results: List[StockResponse] = []
    for stock in stocks:
        data = StockResponse.model_validate(stock)

        snap = snap_map.get(stock.id)
        data.today_is_limit_up = bool(snap.is_limit_up) if snap else False
        data.today_pct_change = snap.pct_change if snap else None
        data.today_board_count = snap.board_count if snap else None
        data.today_limit_down_count = snap.limit_down_count if snap else None

        # ── 主板块：直接读落库值（与仪表盘/龙头等模块一致）───────────────────
        if stock.primary_sector_id and stock.primary_sector_name:
            psec = primary_sector_objs.get(stock.primary_sector_id)
            data.primary_sector = stock.primary_sector_name
            data.sector_id = stock.primary_sector_id
            data.sector_phase = psec.phase if psec else None
            # is_leader: check rel for primary sector
            for rel in rels_by_stock.get(stock.id, []):
                if rel.sector_id == stock.primary_sector_id:
                    data.is_leader = rel.is_leader
                    break

        # ── 板块标签：只包含 is_watched=True 的板块，与 Sector Config 保持一致 ──
        sector_names: List[str] = []
        for rel in rels_by_stock.get(stock.id, []):
            sec = sector_map.get(rel.sector_id)
            if sec and _should_show_sector_tag(sec):
                sector_names.append(sec.name)

        data.sectors = sector_names
        results.append(data)

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_strong_pool(
    db: Session,
    page: int = 1,
    page_size: int = 50,
    sector_id: Optional[int] = None,
    phase: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = "leader_score",
    sort_order: str = "desc",
) -> StockListResponse:
    q = db.query(Stock).filter(Stock.in_strong_pool == True)  # noqa: E712

    if sector_id:
        q = q.join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id).filter(
            StockSectorRelation.sector_id == sector_id
        )
    if phase:
        q = q.filter(Stock.phase == phase)
    if search:
        q = q.filter(
            (Stock.code.ilike(f"%{search}%")) | (Stock.name.ilike(f"%{search}%"))
        )

    sort_col = getattr(Stock, sort_by, Stock.leader_score)
    if sort_order == "desc":
        q = q.order_by(sort_col.desc())
    else:
        q = q.order_by(sort_col.asc())

    total = q.count()
    stocks = q.offset((page - 1) * page_size).limit(page_size).all()

    return StockListResponse(
        items=_enrich_stocks_bulk(stocks, db),
        total=total,
        page=page,
        page_size=page_size,
    )


def get_all_stocks(
    db: Session,
    page: int = 1,
    page_size: int = 50,
    in_strong_pool: Optional[bool] = None,
    sector_id: Optional[int] = None,
    search: Optional[str] = None,
) -> StockListResponse:
    q = db.query(Stock)
    if in_strong_pool is not None:
        q = q.filter(Stock.in_strong_pool == in_strong_pool)
    if sector_id:
        q = q.join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id).filter(
            StockSectorRelation.sector_id == sector_id
        )
    if search:
        q = q.filter(
            (Stock.code.ilike(f"%{search}%")) | (Stock.name.ilike(f"%{search}%"))
        )

    q = q.order_by(Stock.leader_score.desc())
    total = q.count()
    stocks = q.offset((page - 1) * page_size).limit(page_size).all()

    return StockListResponse(
        items=_enrich_stocks_bulk(stocks, db),
        total=total,
        page=page,
        page_size=page_size,
    )


def get_limit_moves_pool(
    db: Session,
    page: int = 1,
    page_size: int = 500,
    search: Optional[str] = None,
    move_type: Optional[str] = None,  # 'limit_up' | 'limit_down' | None (both)
) -> StockListResponse:
    """
    Non-ST stocks filtered by today's price action:
      move_type='limit_up'   → is_limit_up = True
      move_type='limit_down' → is_limit_down = True
      move_type=None         → both (OR)
    Sorted by pct_change desc (limit-up first).
    """
    latest_date = db.query(sqlfunc.max(StockDailySnapshot.date)).scalar()
    if not latest_date:
        return StockListResponse(items=[], total=0, page=page, page_size=page_size)

    q = (
        db.query(Stock)
        .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
        .filter(StockDailySnapshot.date == latest_date)
        .filter(Stock.is_st == False)  # noqa: E712
    )

    if move_type == "limit_up":
        q = q.filter(StockDailySnapshot.is_limit_up == True)  # noqa: E712
    elif move_type == "limit_down":
        q = q.filter(StockDailySnapshot.is_limit_down == True)  # noqa: E712
    else:
        q = q.filter(
            or_(
                StockDailySnapshot.is_limit_up == True,   # noqa: E712
                StockDailySnapshot.is_limit_down == True, # noqa: E712
            )
        )

    q = q.order_by(StockDailySnapshot.pct_change.desc())

    if search:
        q = q.filter(
            (Stock.code.ilike(f"%{search}%")) | (Stock.name.ilike(f"%{search}%"))
        )

    total = q.count()
    stocks = q.offset((page - 1) * page_size).limit(page_size).all()
    return StockListResponse(
        items=_enrich_stocks_bulk(stocks, db),
        total=total,
        page=page,
        page_size=page_size,
    )


def get_limit_moves_trend(db: Session, days: int = 20) -> list[LimitMoveTrendPoint]:
    """
    Return daily 涨停 and 跌停 counts (non-ST) for the most recent `days` trading days.
    """
    rows = (
        db.query(
            StockDailySnapshot.date,
            sqlfunc.sum(
                case((StockDailySnapshot.is_limit_up == True, 1), else_=0)  # noqa: E712
            ).label("limit_up_count"),
            sqlfunc.sum(
                case((StockDailySnapshot.is_limit_down == True, 1), else_=0)  # noqa: E712
            ).label("limit_down_count"),
        )
        .join(Stock, Stock.id == StockDailySnapshot.stock_id)
        .filter(Stock.is_st == False)  # noqa: E712
        .group_by(StockDailySnapshot.date)
        .order_by(StockDailySnapshot.date.desc())
        .limit(days)
        .all()
    )
    return [
        LimitMoveTrendPoint(
            date=str(r.date),
            limit_up_count=int(r.limit_up_count or 0),
            limit_down_count=int(r.limit_down_count or 0),
        )
        for r in reversed(rows)
    ]


def recalculate_strong_pool(db: Session) -> int:
    """
    从东方财富选股 API 拉取最新强势股列表，同步更新 in_strong_pool 标志。
    返回发生变更的股票数。
    """
    from .eastmoney_fetcher import fetch_strong_pool_codes
    api_codes = fetch_strong_pool_codes()
    if not api_codes:
        return 0

    stocks = db.query(Stock).all()
    updated = 0
    for s in stocks:
        new_val = s.code in api_codes
        if s.in_strong_pool != new_val:
            s.in_strong_pool = new_val
            updated += 1
    db.commit()
    return updated
