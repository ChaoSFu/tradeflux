"""
初始化筛选条件。

写入默认强势股筛选条件（主板非ST；非新股非次新；
近60日最高连板数>3 或 近60日涨停天数>9 或 近10日涨停天数>4 或 近20日涨幅前10%）。

用法：
    cd backend && .venv/bin/python scripts/init_screening.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.screening import ScreeningCriteria


def init_default_criteria():
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(ScreeningCriteria).count()
        if existing > 0:
            print(f"已存在 {existing} 条筛选条件，跳过初始化。")
            print("如需重置，请手动删除后重新运行。")
            return

        default = ScreeningCriteria(
            name="默认强势股筛选",
            description=(
                "主板非ST；非新股非次新；"
                "近60个交易日最高连板数大于3 或 "
                "近60个交易日涨停天数大于9 或 "
                "近10个交易日涨停天数大于4 或 "
                "近20个交易日涨幅前10%"
            ),
            is_active=True,
            include_sh_main=True,
            include_sz_main=True,
            exclude_st=True,
            exclude_new_stock=True,
            new_stock_months=12,
            min_board_count_60d=3,       # 连板数 > 3
            min_limit_up_days_60d=9,     # 涨停天数 > 9
            min_limit_up_days_10d=4,     # 近10日涨停 > 4
            top_pct_rank_20d=10,         # 20日涨幅前 10%
        )
        db.add(default)
        db.commit()
        db.refresh(default)
        print(f"✅ 已写入默认筛选条件（id={default.id}）：{default.name}")

    finally:
        db.close()


if __name__ == "__main__":
    init_default_criteria()
