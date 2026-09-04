"""高标龙头生命周期的每日事实快照。"""
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.sql import func

from ..database import Base


class LeaderCycleSnapshot(Base):
    """
    **每日一行，只存事实，不存判定。**

    状态机（RUNNING/BROKEN/RECLAIMING/...）建在这层之上，是第二步。这里一个
    `state` 字段都没有——刻意的：状态判定的六条转换全是人定阈值，而阈值会改。
    把结论冻进历史，日后口径一变，整段历史就失去意义；只存事实则永远可以按新口径
    重算。（派生事件也同理，见 RegulatoryStatusDaily 的同一条设计决定。）

    ## 它为后续研究预留了什么

    有了逐日的 break_date / days_since_break / peak_drawdown，日后要统计
    「进入断板后 D+1/D+3/D+5 的收益分布」「修复候选的 10 日 MFE/MAE」这类问题，
    直接查这张表即可，不需要重跑历史。**但现在不算任何未来收益**——那会引入
    look-ahead：某一天的快照只能用当天及以前的数据。

    ## 可信度字段不是装饰

    `peak_board_confident` / `ma_window_complete` / RS 的 None，都是在说
    "这个数我们不确定"。连板计数无法区分"那天没涨停"和"那天我们没记录"，
    均线窗口不满时算出来的不是均线，RS 日期对不齐时相减没有意义——
    这些都必须能被下游看见，而不是给一个看起来精确的数字。
    """
    __tablename__ = "leader_cycle_snapshots"
    __table_args__ = (
        UniqueConstraint("date", "stock_id", name="uq_leader_cycle_date_stock"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)   # 冗余，免去聚合时回查

    # ── 周期本身 ────────────────────────────────────────────────────────────
    peak_board_count = Column(Integer, nullable=True)     # 这段周期冲到的最高连板
    board_count_60d = Column(Integer, nullable=True)      # 60日最高（历史辨识度，另存）
    cycle_start_date = Column(Date, nullable=True)
    cycle_peak_date = Column(Date, nullable=True)
    break_date = Column(Date, nullable=True)              # None = 仍在连板中
    days_since_break = Column(Integer, nullable=True)     # 交易日数，不是自然日

    # ── 价格结构 ────────────────────────────────────────────────────────────
    peak_price = Column(Float, nullable=True)
    post_break_high = Column(Float, nullable=True)
    post_break_low = Column(Float, nullable=True)
    latest_close = Column(Float, nullable=True)
    peak_drawdown = Column(Float, nullable=True)          # 现价相对峰值 %

    # ── 均线 ────────────────────────────────────────────────────────────────
    ma5 = Column(Float, nullable=True)
    ma10 = Column(Float, nullable=True)
    ma20 = Column(Float, nullable=True)
    ma30 = Column(Float, nullable=True)
    # 窗口不足时上面几个是 0.0（沿用 screening_service 的既有口径），这个标志
    # 让下游能区分"均线是0元"和"窗口没攒够"——后者不该参与任何判定
    ma_window_complete = Column(Boolean, nullable=True)

    # ── 相对强度（None = 不知道，见 relative_strength_service）───────────────
    rs_market_10 = Column(Float, nullable=True)
    rs_market_20 = Column(Float, nullable=True)
    rs_market_60 = Column(Float, nullable=True)
    rs_sector_10 = Column(Float, nullable=True)
    rs_sector_20 = Column(Float, nullable=True)
    rs_sector_60 = Column(Float, nullable=True)
    # RS_sector 用的是哪种口径。"index"=两边都从收盘序列算（正规）；
    # "vendor"=板块那边用东财服务端算好的区间涨幅（板块指数历史拿不到时的替代）。
    # 两者不等价，不记来源就是让两个不同定义共用一个字段名——跟 volume_source 同理
    rs_sector_source = Column(String(10), nullable=True)

    # ── 量能（2026-09-03 刚接入，来源口径见 StockDailySnapshot.volume_source）──
    volume = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    turnover_rate = Column(Float, nullable=True)

    # ── 数据可信度 ──────────────────────────────────────────────────────────
    market_sessions = Column(Integer, nullable=True)     # 周期区间内的交易日数
    absent_days = Column(Integer, nullable=True)         # 其中该股没有 bar 的（停牌+缺口）
    suspended_days = Column(Integer, nullable=True)      # 其中已确认停牌的
    missing_days = Column(Integer, nullable=True)        # 真正的数据缺口 = absent - suspended
    # 这一行的当日事实取自哪一根 bar。**必须存**：如果今天那根最终没补回来，
    # bars[-1] 还是昨天的，latest_close/volume 就会变成"昨天的值挂着今天的日期"
    # ——正是这轮反复修的那类错。不等于 date 时，当日字段一律不写。
    latest_bar_date = Column(Date, nullable=True)
    data_fresh = Column(Boolean, nullable=True)          # latest_bar_date == date
    peak_board_confident = Column(Boolean, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
