"""
/pre-trade-check —— 买入检查（个人纪律工具，全部需登录）。

要登录不只是因为读交易记录：一次检查要打十几次外部行情请求，公开出去，服务器 IP
被行情源限流会连带拖垮日更。
"""
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..models.pre_trade_check import PreTradeCheck
from ..schemas.pre_trade_check import EvaluateRequest, EvaluateResponse, HistoryItem
from ..services import pre_trade_check_service as svc

router = APIRouter(prefix="/pre-trade-check", tags=["pre-trade-check"])


@router.get("/context")
def get_context(
    stock_code: str = Query(..., min_length=6, max_length=6),
    as_of: Optional[datetime] = Query(None, description="不传 = LIVE（此刻）"),
    sector_id: Optional[int] = Query(None, description="本次交易逻辑板块"),
    journal_id: Optional[int] = Query(None, description="从交易记录复盘时带上：显示当时写的理由"),
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        return svc.build_context(db, username, stock_code, as_of, sector_id, journal_id=journal_id)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate(body: EvaluateRequest, username: str = Depends(require_auth), db: Session = Depends(get_db)):
    try:
        return svc.evaluate_and_save(db, username, body)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.get("/history", response_model=List[HistoryItem])
def history(
    limit: int = Query(20, ge=1, le=100),
    stock_code: Optional[str] = None,
    username: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    q = db.query(PreTradeCheck).filter(PreTradeCheck.owner == username)
    if stock_code:
        q = q.filter(PreTradeCheck.stock_code == stock_code)
    rows = q.order_by(PreTradeCheck.created_at.desc(), PreTradeCheck.id.desc()).limit(limit * 5).all()
    # 同一只票、同一个历史时刻反复检查（改答案、改仓位）是一次复盘的几个版本，不是几笔交易：
    # 合并成一条，最新的是结论，前面的留作修改记录；以后统计只算最新那次（2026-09-12 评审）
    groups: Dict[tuple, List[PreTradeCheck]] = {}
    for r in rows:
        groups.setdefault((r.stock_code, r.as_of) if r.mode == "HISTORICAL" else ("live", r.id), []).append(r)
    return [HistoryItem(id=r.id, stock_code=r.stock_code, stock_name=r.stock_name, as_of=r.as_of,
                        mode=r.mode, verdict=r.verdict, rule_version=r.rule_version,
                        intended_price=r.intended_price, created_at=r.created_at,
                        has_outcome=r.outcome_json is not None,
                        revisions=[{"id": x.id, "verdict": x.verdict, "created_at": x.created_at} for x in rest])
            for r, *rest in list(groups.values())[:limit]]


def _own(db: Session, check_id: int, username: str) -> PreTradeCheck:
    row = db.query(PreTradeCheck).filter(PreTradeCheck.id == check_id, PreTradeCheck.owner == username).first()
    if not row:
        raise HTTPException(404, "检查记录不存在")
    return row


@router.get("/{check_id}", response_model=EvaluateResponse)
def get_check(check_id: int, username: str = Depends(require_auth), db: Session = Depends(get_db)):
    return svc.row_to_response(_own(db, check_id, username))


@router.get("/{check_id}/outcome")
def get_outcome(check_id: int, username: str = Depends(require_auth), db: Session = Depends(get_db)):
    """事后揭晓。**只读后续走势，不改当时的判定。**"""
    return svc.compute_outcome(db, _own(db, check_id, username))
