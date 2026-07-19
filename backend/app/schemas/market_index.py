"""大盘指数趋势分析响应类型"""
from typing import List, Optional
from pydantic import BaseModel


class IndexTrendPoint(BaseModel):
    """图表用：单日OHLC + 各均线值"""
    date: str
    close: float
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma120: Optional[float] = None
    ma250: Optional[float] = None


class IndexSignal(BaseModel):
    """近期均线信号（金叉/死叉/突破/跌破/乖离提示）"""
    date: str
    kind: str    # golden_cross | death_cross | break_above_ma20 | break_below_ma20
                 # | break_above_ma60 | break_below_ma60 | overbought | oversold
    label: str   # 中文描述
    side: str    # 'bull' | 'bear' | 'warn'


class IndexTrendAnalysis(BaseModel):
    code: str
    name: str
    close: float
    pct_change: float          # 今日涨跌幅 %
    pct_5d: float              # 近5日累计涨跌幅 %
    pct_20d: float             # 近20日累计涨跌幅 %
    score: int                 # 趋势强度分 0-100
    state: str                 # strong | bullish | range | bearish | weak
    state_label: str           # 强势 / 偏强 / 震荡 / 偏弱 / 弱势
    alignment: str             # bull（多头排列）| bear（空头排列）| mixed（纠缠）
    above_ma5: bool
    above_ma10: bool
    above_ma20: bool
    above_ma60: bool
    ma20_slope_pct: float      # MA20 五日斜率 %（中期趋势方向）
    ma60_slope_pct: float      # MA60 十日斜率 %（长期趋势方向）
    bias20: float              # (close-MA20)/MA20 ×100，乖离率
    signals: List[IndexSignal]
    series: List[IndexTrendPoint]


class MarketTrendResponse(BaseModel):
    updated_at: str            # 数据计算时间（ISO）
    indices: List[IndexTrendAnalysis]
    errors: List[str] = []     # 拉取失败的指数说明（部分失败时仍返回其余）
