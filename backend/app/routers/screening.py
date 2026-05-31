"""
筛选条件管理接口。

GET  /api/screening/criteria          获取所有筛选条件
GET  /api/screening/criteria/active   获取当前生效条件
POST /api/screening/criteria          创建新条件
PUT  /api/screening/criteria/{id}     更新条件
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime

from ..database import get_db
from ..models.screening import ScreeningCriteria

router = APIRouter(prefix="/screening", tags=["筛选条件"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CriteriaBase(BaseModel):
    name: str
    description: Optional[str] = None
    is_active: bool = True
    include_sh_main: bool = True
    include_sz_main: bool = True
    exclude_st: bool = True
    exclude_new_stock: bool = True
    new_stock_months: int = 12
    min_board_count_60d: Optional[int] = 3
    min_limit_up_days_60d: Optional[int] = 9
    min_limit_up_days_10d: Optional[int] = 4
    top_pct_rank_20d: Optional[int] = 10


class CriteriaResponse(CriteriaBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/criteria", response_model=List[CriteriaResponse])
def list_criteria(db: Session = Depends(get_db)):
    """获取所有筛选条件"""
    return db.query(ScreeningCriteria).order_by(ScreeningCriteria.updated_at.desc()).all()


@router.get("/criteria/active", response_model=CriteriaResponse)
def get_active_criteria(db: Session = Depends(get_db)):
    """获取当前生效的筛选条件"""
    criteria = (
        db.query(ScreeningCriteria)
        .filter(ScreeningCriteria.is_active == True)  # noqa: E712
        .order_by(ScreeningCriteria.updated_at.desc())
        .first()
    )
    if not criteria:
        raise HTTPException(status_code=404, detail="未找到生效的筛选条件")
    return criteria


@router.post("/criteria", response_model=CriteriaResponse)
def create_criteria(body: CriteriaBase, db: Session = Depends(get_db)):
    """创建新筛选条件（会自动停用其他条件）"""
    # 停用现有条件
    if body.is_active:
        db.query(ScreeningCriteria).update({"is_active": False})

    obj = ScreeningCriteria(**body.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/criteria/{criteria_id}", response_model=CriteriaResponse)
def update_criteria(criteria_id: int, body: CriteriaBase, db: Session = Depends(get_db)):
    """更新筛选条件"""
    obj = db.query(ScreeningCriteria).filter(ScreeningCriteria.id == criteria_id).first()
    if not obj:
        raise HTTPException(status_code=404, detail="条件不存在")

    # 若设为生效，停用其他
    if body.is_active:
        db.query(ScreeningCriteria).filter(
            ScreeningCriteria.id != criteria_id
        ).update({"is_active": False})

    for k, v in body.model_dump().items():
        setattr(obj, k, v)

    db.commit()
    db.refresh(obj)
    return obj
