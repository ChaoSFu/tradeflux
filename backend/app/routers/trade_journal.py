"""交易复盘日志接口（个人私有数据，全部需登录）。"""
from datetime import date as _date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func as sqlfunc
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..models.trade_journal import TradeJournal
from ..schemas.trade_journal import (
    TradeJournalCreate, TradeJournalUpdate,
    TradeJournalResponse, TradeJournalListResponse,
    ACTIONS, EXIT_ACTIONS, EMOTION_TAGS, EXIT_REASONS,
)
from ..services.market_state_service import get_current_market_state

router = APIRouter(prefix="/trade-journal", tags=["trade-journal"])


def _validate(payload) -> None:
    if payload.action is not None and payload.action not in ACTIONS:
        raise HTTPException(422, f"action 非法，应为 {ACTIONS}")
    if payload.emotion_tag not in (None, "") and payload.emotion_tag not in EMOTION_TAGS:
        raise HTTPException(422, f"emotion_tag 非法，应为 {EMOTION_TAGS}")
    if payload.exit_reason not in (None, "") and payload.exit_reason not in EXIT_REASONS:
        raise HTTPException(422, f"exit_reason 非法，应为 {EXIT_REASONS}")


@router.get("", response_model=TradeJournalListResponse)
def list_entries(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    stock: Optional[str] = Query(None, description="按代码/名称模糊筛选"),
    action: Optional[str] = None,
    emotion_tag: Optional[str] = None,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    q = db.query(TradeJournal).filter(TradeJournal.owner == username)
    if stock:
        like = f"%{stock}%"
        q = q.filter((TradeJournal.stock_code.ilike(like)) | (TradeJournal.stock_name.ilike(like)))
    if action:
        q = q.filter(TradeJournal.action == action)
    if emotion_tag:
        q = q.filter(TradeJournal.emotion_tag == emotion_tag)

    total = q.count()
    items = (
        q.order_by(TradeJournal.trade_time.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )

    # 汇总（全筛选范围，非仅当前页）
    agg = (
        q.with_entities(
            sqlfunc.coalesce(sqlfunc.sum(TradeJournal.realized_pnl), 0.0),
            sqlfunc.sum(case((TradeJournal.realized_pnl > 0, 1), else_=0)),
            sqlfunc.sum(case((TradeJournal.realized_pnl < 0, 1), else_=0)),
        ).one()
    )
    return TradeJournalListResponse(
        items=[TradeJournalResponse.model_validate(x) for x in items],
        total=total,
        realized_pnl_sum=float(agg[0] or 0.0),
        win_count=int(agg[1] or 0),
        loss_count=int(agg[2] or 0),
    )


@router.post("", response_model=TradeJournalResponse)
def create_entry(
    body: TradeJournalCreate,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    _validate(body)
    if body.action not in ACTIONS:
        raise HTTPException(422, f"action 非法，应为 {ACTIONS}")

    entry = TradeJournal(owner=username, **body.model_dump())

    # 自动带入交易当下的市场环境快照（失败不阻塞记录）
    try:
        ms = get_current_market_state(db)
        entry.mkt_temperature = ms.emotional_temperature
        entry.mkt_phase = ms.market_phase
        entry.mkt_suggested_position = ms.suggested_position_level
    except Exception:
        pass

    db.add(entry)
    db.commit()
    db.refresh(entry)
    return TradeJournalResponse.model_validate(entry)


@router.patch("/{entry_id}", response_model=TradeJournalResponse)
def update_entry(
    entry_id: int,
    body: TradeJournalUpdate,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    _validate(body)
    entry = (
        db.query(TradeJournal)
        .filter(TradeJournal.id == entry_id, TradeJournal.owner == username)
        .first()
    )
    if not entry:
        raise HTTPException(404, "记录不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(entry, k, v)
    db.commit()
    db.refresh(entry)
    return TradeJournalResponse.model_validate(entry)


@router.delete("/{entry_id}")
def delete_entry(
    entry_id: int,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    entry = (
        db.query(TradeJournal)
        .filter(TradeJournal.id == entry_id, TradeJournal.owner == username)
        .first()
    )
    if not entry:
        raise HTTPException(404, "记录不存在")
    db.delete(entry)
    db.commit()
    return {"ok": True}
