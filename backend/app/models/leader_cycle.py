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
    # 窗口不足时上面几个是 **None**（2026-09-04 起；此前是 0.0，那时 0 和"没算出来"
    # 混在一起）。bar_count 才是判断"哪几条均线该有值"的依据，这个布尔只是个粗看
    # 实际参与均线计算的 bar 根数。一个布尔分不清"MA5有效但MA30无效"，
    # 而这正是 2026-09-04 把均线改成 Optional 之后下游要判断的东西
    bar_count = Column(Integer, nullable=True)
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

    # ── 变化速度（2026-09-04）───────────────────────────────────────────────
    # 静态截面回答不了"它正在变强还是已经强了很久"。RS20=+12 这个数，是刚从 -5
    # 爬上来，还是从 +30 掉下来的？含义完全相反，而截面看不出来。
    # 全部由**相邻快照相减**得到，仍然是事实，不是判定；缺任一端就是 None。
    rs_market_20_delta_1d = Column(Float, nullable=True)
    rs_market_20_delta_3d = Column(Float, nullable=True)
    rs_sector_20_delta_1d = Column(Float, nullable=True)
    # 离"二波突破"还有多远（%）。post_break_high 是断板后的阶段高点，
    # cycle_peak 是原周期顶——两个都要，前者是近的坎，后者是远的坎
    dist_to_post_break_high = Column(Float, nullable=True)
    dist_to_cycle_peak = Column(Float, nullable=True)
    # 今天创断板后新高 / 新低。三态：True=创了，False=比过没创，**None=没有可比的
    # 历史**（断板当天，post-break 还没有任何一根 bar）。断板当天写 False 等于宣称
    # "比较过了，没创新低"，而其实根本没得比
    new_post_break_high_today = Column(Boolean, nullable=True)
    new_post_break_low_today = Column(Boolean, nullable=True)
    # 修复有没有量能配合。None = 前 5 根里有 bar 没有量（不补零、不用均值顶替）
    volume_ratio_5d = Column(Float, nullable=True)
    amount_ratio_5d = Column(Float, nullable=True)

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
    # 这一行的价格事实来自的那根 bar，**是不是收盘终值**。
    # data_fresh 只回答"那根 bar 是不是今天的"，回答不了"今天收盘了没有"——腾讯
    # 盘中就发当日 bar，两者盘中同时为真。2026-09-04 之前这一层根本收不到结算
    # 信息：daily_update 算了 run_settled 却没往下传，于是盘中跑出来的盘中价，
    # 标着 data_fresh=True 躺在表里，事后无从分辨。
    # None = 调用方没告诉我们（不是 False —— "不知道"不能当"没结算"）
    bar_settled = Column(Boolean, nullable=True)
    data_fresh = Column(Boolean, nullable=True)          # latest_bar_date == date
    peak_board_confident = Column(Boolean, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
