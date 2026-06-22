from sqlalchemy import Column, Integer, String, Date, DateTime, Text
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
