from datetime import time
from typing import List, Literal, Optional

from pydantic import BaseModel


class BoardLadderEntry(BaseModel):
    board: int
    count: int


class RadarCoreStock(BaseModel):
    """板块核心锚：今天**没有**涨停，但历史上有足够市场辨识度，不能从视野里消失。"""
    code: str
    name: str
    core_roles: List[str] = []
    core_reasons: List[str] = []       # 可解释理由，如"近60日涨停6次"
    primary_role: Optional[str] = None
    pct_change: Optional[float] = None  # 今日涨跌幅 —— 判断老核心正/负反馈的关键
    limit_up_days_10d: Optional[int] = None
    limit_up_days_20d: Optional[int] = None
    limit_up_days_60d: Optional[int] = None
    board_count_60d: Optional[int] = None
    # 区间涨幅：东财 INTERVAL_CHG，真实复合区间收益。跟活跃股池那几列（日涨幅简单
    # 相加的近似）不是同一个算法，大涨股票差距很大，不要直接对比
    interval_chg_10d: Optional[float] = None
    interval_chg_20d: Optional[float] = None
    interval_chg_60d: Optional[float] = None
    # 龙头分/风险分是否为本轮真实计算值。False = 该股今日不在候选池、没有当日快照，
    # 分数是冻结旧值，页面显示 — 而不是拿旧分数冒充当前
    scores_as_of_today: bool = False
    leader_score: Optional[float] = None
    risk_score: Optional[float] = None
    is_broken_today: bool = False


class RadarTodayStock(BaseModel):
    """今日涨停股。core_roles 非空表示它同时也是板块核心（最强的共振信号）。"""
    code: str
    name: str
    board_count: Optional[int] = None
    limit_stat_days: Optional[int] = None    # "N日M板" 的 N
    limit_stat_count: Optional[int] = None   # "N日M板" 的 M
    first_limit_time: Optional[time] = None
    last_limit_time: Optional[time] = None
    seal_amount: Optional[float] = None      # 封单额（元），None=东财没给，不是0
    broken_times: Optional[int] = None
    pct_change: Optional[float] = None
    price: Optional[float] = None
    turnover_rate: Optional[float] = None
    limit_reason: Optional[str] = None       # 催化剂，不是板块归属
    limit_content: Optional[str] = None
    limit_up_days_10d: Optional[int] = None
    limit_up_days_20d: Optional[int] = None
    limit_up_days_60d: Optional[int] = None
    board_count_60d: Optional[int] = None
    # 区间涨幅：东财 INTERVAL_CHG，真实复合区间收益。跟活跃股池那几列（日涨幅简单
    # 相加的近似）不是同一个算法，大涨股票差距很大，不要直接对比
    interval_chg_10d: Optional[float] = None
    interval_chg_20d: Optional[float] = None
    interval_chg_60d: Optional[float] = None
    # 龙头分/风险分是否为本轮真实计算值。False = 该股今日不在候选池、没有当日快照，
    # 分数是冻结旧值，页面显示 — 而不是拿旧分数冒充当前
    scores_as_of_today: bool = False
    leader_score: Optional[float] = None
    risk_score: Optional[float] = None
    core_roles: List[str] = []
    core_reasons: List[str] = []


class RadarSector(BaseModel):
    sector_id: int
    sector_name: str
    sector_phase: Optional[int] = None

    today_limit_up_count: int = 0
    continuation_count: int = 0      # 连板股数（board_count>=2）
    first_board_count: int = 0
    board_height: int = 0
    board_ladder: List[BoardLadderEntry] = []

    broken_count: int = 0
    seal_rate: Optional[float] = None            # 涨停/(涨停+炸板) %
    earliest_limit_time: Optional[time] = None
    total_seal_amount: Optional[float] = None    # None=没有任何一只给了封单额
    seal_amount_known_count: int = 0

    core_count: int = 0            # 召回到的核心锚真实总数
    core_shown_count: int = 0      # 实际返回的条数（展示截断，长尾不展开）
    # 核心锚今日涨跌幅需要当日 StockDailySnapshot；盘中 daily_update 还没跑时它不
    # 存在，此时下面两个字段分别是 None / 0，页面显示"待当日数据更新"而不是 0.00%
    core_pct_known_count: int = 0
    core_avg_pct_change: Optional[float] = None  # 核心锚今日平均涨跌幅

    core_stocks: List[RadarCoreStock] = []
    today_limit_up_stocks: List[RadarTodayStock] = []


class RadarSummary(BaseModel):
    limit_up_count: int = 0
    continuation_count: int = 0
    first_board_count: int = 0
    board_height: int = 0
    broken_count: int = 0
    seal_rate: Optional[float] = None
    active_sector_count: int = 0


class LimitUpRadarResponse(BaseModel):
    trade_date: Optional[str] = None
    # 数据新鲜度三件套：页面必须能显示"这份数据是哪天的、什么时候抓的、从哪来"，
    # 盘中手动刷新的页面不能让用户误以为看到的是实时数据
    refreshed_at: Optional[str] = None
    source: Optional[str] = None
    # 近10/20/60日涨停次数实际算到哪一天。盘中今天的 daily_update 还没跑时它是上一个
    # 交易日（符合预期，今天的涨停在"今日攻击"里单独看）；落后≥2个交易日会进 warnings
    history_as_of: Optional[str] = None
    history_lag_days: int = 0
    # 板块入选门槛（AND）+ 因不达标被隐藏的板块数。隐藏数要展示出来，不能悄悄丢
    filter_min_limit_up: int = 0
    filter_min_board_height: int = 0
    hidden_sector_count: int = 0
    summary: RadarSummary = RadarSummary()
    sectors: List[RadarSector] = []
    warnings: List[str] = []


class LimitUpRadarRefreshResponse(BaseModel):
    ok: bool
    trade_date: Optional[str] = None
    limit_up_written: int = 0
    broken_written: int = 0
    refreshed_at: Optional[str] = None
    # 刷新失败时不删已有数据：ok=False + 上次成功时间，页面继续显示旧数据并明确
    # 标注 REFRESH FAILED，绝不伪装成最新
    error: Optional[str] = None
    last_success_at: Optional[str] = None


GroupMode = Literal["all_watched_sectors", "primary"]
