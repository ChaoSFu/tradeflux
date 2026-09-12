"""买入检查的请求 / 响应结构（2026-09-11）。"""
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

from ..services.pre_trade_rules import DEFAULT_ACCOUNT_RISK_BUDGET_PCT, DEFAULT_STRESS_LOSS_PCT


class ManualAnswers(BaseModel):
    """None = 还没回答。**没回答不等于回答了"否"**——READY 要求全部答完。"""
    q1: Optional[bool] = None
    q2: Optional[bool] = None
    q3: Optional[bool] = None
    q4: Optional[bool] = None
    q5: Optional[bool] = None
    q6: Optional[bool] = None
    q7: Optional[bool] = None
    q8: Optional[bool] = None
    q9: Optional[bool] = None
    a_plus: Optional[bool] = None          # 09:45 前才问：是否有事前写好的 A+ 例外
    new_market_fact: str = ""              # 5 日内重入才要：新的市场事实
    second_trade_note: str = ""            # 今天第 2 笔才要：比第一笔多出的证据
    # v2：买入理由三句 + 失效条件（失效 ≠ 止损价）。q9 不再使用，留着兼容旧客户端
    why_sector: str = ""
    why_stock: str = ""
    why_now: str = ""
    invalidation_type: Optional[Literal["price", "structure", "sector", "time"]] = None
    invalidation_text: str = ""


class EvaluateRequest(BaseModel):
    stock_code: str = Field(..., min_length=6, max_length=6)
    as_of: Optional[datetime] = None       # 不传 = LIVE（此刻）
    intended_price: Optional[float] = Field(None, gt=0)
    position_pct: Optional[float] = Field(None, gt=0, le=100)
    planned_stop: Optional[float] = Field(None, gt=0)
    reason: str = ""
    thesis_sector_id: Optional[int] = None
    journal_id: Optional[int] = None       # 从交易记录复盘时带上
    manual_answers: ManualAnswers = ManualAnswers()
    account_risk_budget_pct: float = Field(DEFAULT_ACCOUNT_RISK_BUDGET_PCT, gt=0, le=10)
    stress_loss_pct: float = Field(DEFAULT_STRESS_LOSS_PCT, ge=1, le=40)


class CheckItem(BaseModel):
    key: str
    level: str                             # PASS | WARN | FAIL | UNKNOWN | INFO
    group: str                             # positive | caution | unmet | unknown | info
    text: str
    evidence: dict = {}
    module: Optional[str] = None


class CheckModule(BaseModel):
    key: str
    title: str
    status: str                            # PASS | WARN | FAIL | UNKNOWN | NEUTRAL
    items: List[CheckItem]
    known: int
    unknown: int
    meta: dict = {}


class Dimension(BaseModel):
    key: str                               # setup | execution | risk
    title: str
    verdict: str
    lead: str
    counts: dict = {}


class Decision(BaseModel):
    verdict: str                           # READY | WAIT | BLOCKED
    summary: str
    rule_version: str
    vetoes: List[CheckItem]
    unmet: List[CheckItem]
    cautions: List[CheckItem]
    unknowns: List[CheckItem]
    positives: List[CheckItem]
    dimensions: List[Dimension] = []       # v2 起才有；v1 的存档没有


class EvaluateResponse(BaseModel):
    id: Optional[int] = None
    mode: str
    as_of: datetime
    stock_code: str
    stock_name: Optional[str] = None
    modules: List[CheckModule]
    decision: Decision
    context: dict[str, Any]
    data_quality: List[dict]


class HistoryItem(BaseModel):
    id: int
    stock_code: str
    stock_name: Optional[str] = None
    as_of: datetime
    mode: str
    verdict: str
    rule_version: str
    intended_price: Optional[float] = None
    created_at: Optional[datetime] = None
    has_outcome: bool = False
    revisions: List[dict] = []             # 同一只票同一历史时刻之前的几次检查（旧→新之前）
