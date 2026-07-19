"""
Sector Phase Engine — rule-based lifecycle classifier.

Phase 0 Stealth     : strong_stock_count <= 1, emotion_score < 30
Phase 1 Initiation  : strong_stock_count in [2,3], first limit-ups, emotion rising
Phase 2 Expansion   : strong_stock_count >= 4, board_height >= 3, continuity high
Phase 3 Euphoria    : board_height >= 5, limit_up_count >= 3, emotion_score >= 80
Phase 4 Divergence  : board_height dropping, broken-boards appear, emotion falling
Phase 5 Decline     : limit_up_count < 1, strong_stock_count falling, risk high
Phase 6 Dead Zone   : no activity, risk_score >= 80

Each phase transition is deterministic from the snapshot metrics.
Architecture is designed for LLM override in a future enhancement.
"""
from sqlalchemy.orm import Session
from typing import List
from ..models.sector import Sector, SectorDailySnapshot
from ..schemas.sector import SectorResponse, SectorListResponse, PHASE_LABELS, PHASE_LABELS_ZH
from ..models.stock import Stock


def _classify_phase(
    strong_stock_count: int,
    limit_up_count: int,
    board_height: int,
    continuity_score: float,
    risk_score: float,
    emotion_score: float,
) -> int:
    if risk_score >= 80 and limit_up_count == 0:
        return 6  # Dead Zone

    if limit_up_count == 0 and strong_stock_count <= 1:
        return 5  # Decline

    if board_height <= 2 and emotion_score < 40 and strong_stock_count >= 2:
        return 4  # Divergence

    if board_height >= 5 and limit_up_count >= 3 and emotion_score >= 70:
        return 3  # Euphoria

    if strong_stock_count >= 4 and board_height >= 3 and continuity_score >= 50:
        return 2  # Expansion

    if strong_stock_count >= 2 and limit_up_count >= 1:
        return 1  # Initiation

    return 0  # Stealth


def _build_sector_response(sector: Sector, db: Session) -> SectorResponse:
    from ..models.sector import StockSectorRelation
    from ..schemas.sector import StockInSector

    relations = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.sector_id == sector.id)
        .all()
    )
    stocks_in_sector = []
    for rel in relations:
        stock = db.query(Stock).filter(Stock.id == rel.stock_id).first()
        if stock:
            stocks_in_sector.append(
                StockInSector(
                    id=stock.id,
                    code=stock.code,
                    name=stock.name,
                    is_leader=rel.is_leader,
                    is_core=rel.is_core,
                    is_compensation=rel.is_compensation,
                    leader_score=stock.leader_score,
                    risk_score=stock.risk_score,
                    phase=stock.phase,
                )
            )

    leader_code = None
    leader_name = None
    if sector.leader_stock_id:
        ls = db.query(Stock).filter(Stock.id == sector.leader_stock_id).first()
        if ls:
            leader_code = ls.code
            leader_name = ls.name

    return SectorResponse(
        id=sector.id,
        code=sector.code,
        name=sector.name,
        description=sector.description,
        phase=sector.phase,
        phase_label=PHASE_LABELS.get(sector.phase, "Unknown"),
        phase_label_zh=PHASE_LABELS_ZH.get(sector.phase, "未知"),
        strong_stock_count=sector.strong_stock_count,
        limit_up_count=sector.limit_up_count,
        limit_down_count=getattr(sector, "limit_down_count", 0),
        one_word_up_count=getattr(sector, "one_word_up_count", 0),
        one_word_down_count=getattr(sector, "one_word_down_count", 0),
        board_height=sector.board_height,
        continuity_score=sector.continuity_score,
        risk_score=sector.risk_score,
        emotion_score=sector.emotion_score,
        sector_type=getattr(sector, "sector_type", None),
        stock_count=getattr(sector, "stock_count", 0),
        pct_change_30d=getattr(sector, "pct_change_30d", 0.0),
        pct_change_5d=getattr(sector, "pct_change_5d",  0.0),
        pct_change_10d=getattr(sector, "pct_change_10d", 0.0),
        pct_change_20d=getattr(sector, "pct_change_20d", 0.0),
        pct_change_60d=getattr(sector, "pct_change_60d", 0.0),
        amount=getattr(sector, "amount", 0.0),
        is_watched=getattr(sector, "is_watched", False),
        leader_stock_id=sector.leader_stock_id,
        leader_stock_code=leader_code,
        leader_stock_name=leader_name,
        stocks=stocks_in_sector,
        created_at=sector.created_at,
        updated_at=sector.updated_at,
    )


def _build_rank_maps(sectors: list) -> dict:
    """
    对全量板块的 7 个维度计算 dense rank（前5名，value>0）。
    返回 {sector_id: {field: rank}} 结构。
    """
    RANK_FIELDS = [
        ("rank_5d",     "pct_change_5d"),
        ("rank_10d",    "pct_change_10d"),
        ("rank_20d",    "pct_change_20d"),
        ("rank_60d",    "pct_change_60d"),
        ("rank_lu",     "limit_up_count"),
        ("rank_board",  "board_height"),
        ("rank_strong", "strong_stock_count"),
    ]
    result: dict = {s.id: {} for s in sectors}
    for rank_key, field in RANK_FIELDS:
        eligible = [s for s in sectors if getattr(s, field, 0) > 0]
        eligible.sort(key=lambda s: getattr(s, field), reverse=True)
        rank, prev_val, count = 1, None, 0
        for s in eligible:
            val = getattr(s, field)
            if prev_val is not None and val != prev_val:
                rank = count + 1
                if rank > 5:
                    break
            if rank <= 5:
                result[s.id][rank_key] = rank
            prev_val = val
            count += 1
    return result


def get_all_sectors(db: Session) -> SectorListResponse:
    """
    返回 is_watched=True 的板块列表，rank 字段直接读 DB（由 daily_update 写入）。
    批量预取关联数据，避免 N+1 查询。
    """
    from ..models.sector import StockSectorRelation
    from ..schemas.sector import StockInSector
    from collections import defaultdict

    sectors = (
        db.query(Sector)
        .filter(Sector.is_watched == True)   # noqa: E712
        .order_by(Sector.emotion_score.desc())
        .all()
    )
    sector_ids = [s.id for s in sectors]

    # ── 批量预取：板块成员关联 ────────────────────────────────────────────
    all_rels = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.sector_id.in_(sector_ids))
        .all()
    )
    # {sector_id: [rel, ...]}
    rels_by_sector: dict = defaultdict(list)
    for rel in all_rels:
        rels_by_sector[rel.sector_id].append(rel)

    # ── 批量预取：所有涉及的股票 ──────────────────────────────────────────
    all_stock_ids = {rel.stock_id for rel in all_rels}
    # 加上龙头股 id
    leader_ids = {s.leader_stock_id for s in sectors if s.leader_stock_id}
    all_stock_ids |= leader_ids

    stock_map: dict = {
        s.id: s
        for s in db.query(Stock).filter(Stock.id.in_(all_stock_ids)).all()
    } if all_stock_ids else {}

    # ── 组装响应 ──────────────────────────────────────────────────────────
    items = []
    for s in sectors:
        rels = rels_by_sector.get(s.id, [])
        stocks_in_sector = []
        for rel in rels:
            stock = stock_map.get(rel.stock_id)
            if stock:
                stocks_in_sector.append(StockInSector(
                    id=stock.id,
                    code=stock.code,
                    name=stock.name,
                    is_leader=rel.is_leader,
                    is_core=rel.is_core,
                    is_compensation=rel.is_compensation,
                    leader_score=stock.leader_score,
                    risk_score=stock.risk_score,
                    phase=stock.phase,
                ))

        leader_stock = stock_map.get(s.leader_stock_id) if s.leader_stock_id else None

        resp = SectorResponse(
            id=s.id,
            code=s.code,
            name=s.name,
            description=s.description,
            phase=s.phase,
            phase_label=PHASE_LABELS.get(s.phase, "Unknown"),
            phase_label_zh=PHASE_LABELS_ZH.get(s.phase, "未知"),
            strong_stock_count=s.strong_stock_count,
            limit_up_count=s.limit_up_count,
            limit_down_count=getattr(s, "limit_down_count", 0),
        one_word_up_count=getattr(s, "one_word_up_count", 0),
        one_word_down_count=getattr(s, "one_word_down_count", 0),
            board_height=s.board_height,
            continuity_score=s.continuity_score,
            risk_score=s.risk_score,
            emotion_score=s.emotion_score,
            sector_type=getattr(s, "sector_type", None),
            stock_count=getattr(s, "stock_count", 0),
            pct_change_30d=getattr(s, "pct_change_30d", 0.0),
            pct_change_5d=getattr(s, "pct_change_5d",  0.0),
            pct_change_10d=getattr(s, "pct_change_10d", 0.0),
            pct_change_20d=getattr(s, "pct_change_20d", 0.0),
            pct_change_60d=getattr(s, "pct_change_60d", 0.0),
            amount=getattr(s, "amount", 0.0),
            is_watched=getattr(s, "is_watched", False),
            leader_stock_id=s.leader_stock_id,
            leader_stock_code=leader_stock.code if leader_stock else None,
            leader_stock_name=leader_stock.name if leader_stock else None,
            stocks=stocks_in_sector,
            created_at=s.created_at,
            updated_at=s.updated_at,
            rank_5d=getattr(s, "rank_5d", None),
            rank_10d=getattr(s, "rank_10d", None),
            rank_20d=getattr(s, "rank_20d", None),
            rank_60d=getattr(s, "rank_60d", None),
            rank_lu=getattr(s, "rank_lu", None),
            rank_board=getattr(s, "rank_board", None),
            rank_strong=getattr(s, "rank_strong", None),
        )
        items.append(resp)

    return SectorListResponse(items=items, total=len(items))


def get_sector_by_code(db: Session, code: str) -> SectorResponse | None:
    sector = db.query(Sector).filter(Sector.code == code).first()
    if not sector:
        return None
    return _build_sector_response(sector, db)


def refresh_sector_phases(db: Session) -> None:
    """Recompute phase for every sector based on current snapshot metrics."""
    sectors = db.query(Sector).all()
    for s in sectors:
        new_phase = _classify_phase(
            s.strong_stock_count,
            s.limit_up_count,
            s.board_height,
            s.continuity_score,
            s.risk_score,
            s.emotion_score,
        )
        s.phase = new_phase
    db.commit()
