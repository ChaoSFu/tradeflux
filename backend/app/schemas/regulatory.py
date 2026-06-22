from datetime import date
from typing import Optional, List

from pydantic import BaseModel

from .stock import StockResponse


class RegulatoryItem(BaseModel):
    info_code: str
    security_code: str
    security_name: Optional[str] = None
    exchange: Optional[str] = None
    reason_type: Optional[str] = None
    reason: Optional[str] = None
    direction: Optional[str] = None  # up | down | None（按 reason_type 解析的涨/跌方向）
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    predict_start: Optional[date] = None
    predict_end: Optional[date] = None
    notice_date: Optional[date] = None
    days_remaining: Optional[int] = None  # 距监管解除天数（按 predict_end - today；负=已过预测期）
    status: str  # monitoring | ending_soon | released
    stock: Optional[StockResponse] = None  # 命中本地快照时补充连板/龙头分等

    model_config = {"from_attributes": True}


class ApproachingItem(BaseModel):
    security_code: str
    security_name: Optional[str] = None
    direction: str          # up | down
    window: str             # 10d | 30d
    cum_deviation: float    # 累计偏离值 %
    threshold: float        # 触发阈值 %
    approach: float         # 接近度 = 累计偏离值 / 阈值（≥1 表示已达标）
    coverage: int           # 累计天数（MAX_DAYS）
    full_window: bool       # 是否取满窗口
    target_rate: Optional[float] = None  # 今日还需涨跌幅 % 即触发
    rule_label: str
    stock: Optional[StockResponse] = None

    model_config = {"from_attributes": True}


class RegulatoryWatchlistResponse(BaseModel):
    as_of: date
    monitoring: List[RegulatoryItem]
    ending_soon: List[RegulatoryItem]
    recently_released: List[RegulatoryItem]
    approaching: List[ApproachingItem]
