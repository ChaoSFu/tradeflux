"""
强势股筛选条件配置表。

每日更新脚本读取 is_active=True 的条件来决定哪些股票进入强势池。
条件之间是 OR 关系（满足任一即入池），静态过滤（ST/次新）是 AND 关系（全部满足才不被排除）。
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from ..database import Base


class ScreeningCriteria(Base):
    __tablename__ = "screening_criteria"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # 市场范围
    include_sh_main = Column(Boolean, default=True, nullable=False)    # 沪主板
    include_sz_main = Column(Boolean, default=True, nullable=False)    # 深主板
    exclude_st = Column(Boolean, default=True, nullable=False)         # 排除 ST / *ST
    exclude_new_stock = Column(Boolean, default=True, nullable=False)  # 排除次新
    new_stock_months = Column(Integer, default=12, nullable=False)     # 上市不足 X 月视为次新

    # 入池条件（任一满足即可）
    # None 表示该条件不启用
    min_board_count_60d = Column(Integer, nullable=True, default=3)    # 近60日最高连板数 > X
    min_limit_up_days_60d = Column(Integer, nullable=True, default=9)  # 近60日涨停天数 > X
    min_limit_up_days_10d = Column(Integer, nullable=True, default=4)  # 近10日涨停天数 > X
    top_pct_rank_20d = Column(Integer, nullable=True, default=10)      # 近20日涨幅前 X%

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
