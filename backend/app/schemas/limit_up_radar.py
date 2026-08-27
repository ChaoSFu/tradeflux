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
    # 断板股的最高累计板数（东财 zttj 的 N天M板，且 N>M 才算断板）。None=板块内
    # 没有断板股。跟 board_height 是两种不同的强：前者是当前还连着的高度，后者是
    # "打过高板但断了"的市场辨识度——神奇制药当前首板、历史 11日7板，只看连板会
    # 把它埋在一堆首板里
    broken_streak_height: Optional[int] = None
    board_ladder: List[BoardLadderEntry] = []

    broken_count: int = 0
    broken_stocks: List["RadarBrokenStock"] = []
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


class RadarBrokenStock(BaseModel):
    """
    炸板股：盘中触及涨停但收盘没封住。这一块回答的问题只有一个——
    **今天这个板块里有多少票封板不坚决、烂到什么程度**。

    跟涨停/炸板互斥性有关的一点（用户 2026-08-26 明确）：理论上一只票同一时刻
    只可能在其中一边，但涨停池和炸板池是两个独立接口、并发拉取，一只 14:30 炸板
    或回封的票可能同时出现在两份名单里。这种重复**可以容忍**，不做强制去重——
    强行去重要么依赖两个接口的时间戳（它们没有可比的时间戳），要么就得随便挑一边
    丢掉，那才是真的丢信息。
    """
    code: str
    name: str
    pct_change: Optional[float] = None
    price: Optional[float] = None
    limit_price: Optional[float] = None
    # 距涨停价还差多少 %（负数=已回落）。封板不坚决程度的核心量化：
    # 炸板收 -5% 和炸板收 +9% 完全是两回事
    gap_to_limit_pct: Optional[float] = None
    board_count: Optional[int] = None        # 高位板炸板是见顶信号，首板炸板只是情绪一般
    limit_stat_days: Optional[int] = None
    limit_stat_count: Optional[int] = None
    first_limit_time: Optional[time] = None  # 炸板池没有"最终封板时间"——它就是没封住
    broken_times: Optional[int] = None       # 反复开合说明分歧极大
    turnover_rate: Optional[float] = None
    amount: Optional[float] = None
    amplitude: Optional[float] = None
    # 跟 RadarTodayStock 同名同义的历史指标（2026-08-27补）：一个 6天5板 的高位股
    # 炸板，和一个从没涨停过的票冲高回落，对板块的含义天差地别
    limit_up_days_10d: Optional[int] = None
    limit_up_days_20d: Optional[int] = None
    limit_up_days_60d: Optional[int] = None
    board_count_60d: Optional[int] = None
    interval_chg_10d: Optional[float] = None
    interval_chg_20d: Optional[float] = None
    interval_chg_60d: Optional[float] = None
    scores_as_of_today: bool = True
    leader_score: Optional[float] = None
    risk_score: Optional[float] = None
    core_roles: List[str] = []
    core_reasons: List[str] = []


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
    # 板块入选门槛 + 因不达标被隐藏的板块数。规则是
    #   (涨停>=filter_min_limit_up 且 最高连板>=filter_min_board_height)
    #   或 涨停>=filter_min_limit_up_alone
    # 隐藏数要展示出来，不能悄悄丢
    filter_min_limit_up: int = 0
    filter_min_board_height: int = 0
    # 涨停只数单独达标即可入选的阈值（与上面那组是 OR）。0=关闭，退回纯 AND
    filter_min_limit_up_alone: int = 0
    hidden_sector_count: int = 0
    summary: RadarSummary = RadarSummary()
    sectors: List[RadarSector] = []
    warnings: List[str] = []


class LimitUpRadarRefreshResponse(BaseModel):
    ok: bool
    # 后台线程执行：POST 立即返回 running=true，页面轮询 /refresh/status 到 running=false
    # 再刷新数据。整个刷新实测约40秒（东财3接口+100只K线），同步返回会被前端超时掐断
    running: bool = False
    step: Optional[str] = None    # 当前进行到哪一步，给按钮上显示
    trade_date: Optional[str] = None
    limit_up_written: int = 0
    broken_written: int = 0
    scores_recomputed: int = 0    # 本次重算了几只股票的龙头分/风险分
    refreshed_at: Optional[str] = None
    # 刷新失败时不删已有数据：ok=False + 上次成功时间，页面继续显示旧数据并明确
    # 标注 REFRESH FAILED，绝不伪装成最新
    error: Optional[str] = None
    last_success_at: Optional[str] = None
    # 本次总耗时与各步耗时（秒）。报出来才知道慢在哪——"尽量快"要能被验证，
    # 不能只是感觉。2026-08-26 把明细与召回改成并行、明细内部3个接口也并发之后，
    # 这两个数字是判断改动是否真的生效的唯一依据。
    elapsed: Optional[float] = None
    timings: Optional[dict] = None


GroupMode = Literal["all_watched_sectors", "primary"]
