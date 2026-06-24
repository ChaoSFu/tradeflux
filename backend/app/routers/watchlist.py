from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.regulatory import RegulatoryWatchlistResponse
from ..services.regulatory_service import get_regulatory_watchlist, sync_regulatory_unusual

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("/regulatory", response_model=RegulatoryWatchlistResponse)
def regulatory_watchlist(db: Session = Depends(get_db)):
    """重点监管名单：监管中 / 即将解除（join 本地快照补强势指标）。"""
    return get_regulatory_watchlist(db)


@router.get("/severe-targets")
def severe_targets():
    """全平台：code → 今日还需涨幅%触发严重异常波动（涨幅、未触发）。"""
    from ..services.deviation_service import get_severe_up_targets
    return get_severe_up_targets()


@router.post("/regulatory/sync")
def regulatory_sync(db: Session = Depends(get_db)):
    """手动触发抓取重点监管名单（盘后定时任务亦会自动同步）。"""
    return sync_regulatory_unusual(db)
