from sqlalchemy import (
    Column, Integer, String, Date, DateTime, Text, Float, Boolean, UniqueConstraint,
)
from sqlalchemy.sql import func

from ..database import Base


class RegulatoryUnusual(Base):
    """
    交易所「严重异常波动 / 重点监控」名单（东财 RPT_APP_UNUSUALBASIC，UNUSUAL_TYPE=002）。
    以 info_code 为去重逻辑键（对应一条公告）。
    """
    __tablename__ = "regulatory_unusual"

    id = Column(Integer, primary_key=True, index=True)
    info_code = Column(String(64), nullable=False, unique=True, index=True)  # 公告唯一码

    security_code = Column(String(16), nullable=False, index=True)  # 股票代码
    security_name = Column(String(64), nullable=True)               # 股票简称
    exchange = Column(String(16), nullable=True)                    # 交易所（上交所/深交所）

    unusual_type = Column(String(8), nullable=False, default="002")  # 002=严重异常波动
    reason_type = Column(String(128), nullable=True)                 # 触发规则（UNUSUAL_REASON_TYPE）
    reason = Column(Text, nullable=True)                             # 完整原因文本

    start_date = Column(Date, nullable=True)     # 触发观察窗口起
    end_date = Column(Date, nullable=True)       # 触发观察窗口止
    predict_start = Column(Date, nullable=True)  # 重点监控期起
    predict_end = Column(Date, nullable=True)    # 重点监控期止
    notice_date = Column(Date, nullable=True)    # 公告日

    is_his = Column(String(4), nullable=False, default="0", index=True)  # 0=当前 1=历史

    fetched_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class RegulatoryStatusDaily(Base):
    """
    **每日监管状态快照**——每个交易日给每只相关股票记一行"那天它处于什么状态"。

    ## 为什么必须单独建这张表

    `RegulatoryUnusual` 是上游公告名单的**镜像**，不是时间序列：
    `sync_regulatory_unusual` 每次同步都 `DELETE` 掉整批再重新插入。也就是说
    昨天的名单是被**物理删除**的，连"昨天 8 只、今天 11 只、新增哪 3 只"都答不出来，
    更不用说"某只原定 9/3 结束、现在延长到 9/10"。

    破局雷达要问的恰恰是这类问题——**监管约束是在变紧还是变松**：
      · APPROACHING → MONITORING   进入监管
      · MONITORING  → RELEASED     解除
      · predict_end 往后挪          延长（这个信号最难得，也最能说明态度）
      · RELEASED    → APPROACHING   刚解除又逼近

    这些全部只能靠**相邻两天的快照相减**得到。今天不开始记，一个月后还是答不出来，
    而且丢掉的那段永远补不回来——这是为什么它排在展示层前面。

    不去改 `sync_regulatory_unusual` 的 DELETE 语义，是因为那个函数作为"上游名单的
    镜像"本身是对的：上游撤回一条公告，镜像就该跟着消失。时间序列是另一件事，
    分开存，各自语义干净。

    ## 只存状态不够，判定依据也要一起存

    `status` 是从 `predict_end` 和今天的距离推出来的（见 regulatory_service 的
    ENDING_SOON_DAYS / RECENTLY_RELEASED_DAYS）。如果只存一个 MONITORING 标签，
    日后想复核"为什么 8/25 那天它是 ENDING_SOON"就查不了了，阈值一改历史也跟着
    失去意义。所以 predict_start / predict_end / days_remaining / reason_type
    一并落库——**存事实，不只存结论**。

    ## 派生事件（ENTER / RELEASE / EXTENSION）刻意不落库

    它们由相邻两天的快照现算。落库等于把"怎么算"也冻结进历史，日后口径一改就得
    全量重刷；而现算的话，改口径只是换个读法，历史事实不动。

    ## 幂等

    (date, security_code) 唯一。daily_update 一天跑 2~3 次（盘前/盘后/手动），
    同一天重复写就是覆盖，不产生重复行。注意**盘中跑写进来的是当时的状态**，
    收盘后那次会覆盖成终值——跟快照表同一套语义。
    """
    __tablename__ = "regulatory_status_daily"
    __table_args__ = (
        UniqueConstraint("date", "security_code", name="uq_reg_status_date_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)          # 观测日（交易日）
    security_code = Column(String(16), nullable=False, index=True)
    security_name = Column(String(64), nullable=True)

    # APPROACHING（逼近监管）/ MONITORING（监管中）/ ENDING_SOON（即将解除）
    # / RELEASED（近期解除）
    status = Column(String(16), nullable=False, index=True)

    # ── 判定依据（见上面 docstring：存事实不只存结论）────────────────────────
    reason_type = Column(String(128), nullable=True)   # 触发规则
    direction = Column(String(8), nullable=True)       # up | down
    predict_start = Column(Date, nullable=True)        # 重点监控期起
    predict_end = Column(Date, nullable=True)          # 重点监控期止 ← 延长看它
    days_remaining = Column(Integer, nullable=True)    # 距 predict_end 的日历天数

    # ── APPROACHING 专有：来自东财严重异动预测，落库才能算「逼近→进入」转化率 ──
    approach = Column(Float, nullable=True)        # 接近度 = 累计偏离 / 阈值
    target_rate = Column(Float, nullable=True)     # 今日还需涨跌幅 % 即触发
    window = Column(String(8), nullable=True)      # 10d | 30d
    full_window = Column(Boolean, nullable=True)   # 窗口是否取满（不满则接近度偏低，不可比）

    observed_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime, server_default=func.now())
