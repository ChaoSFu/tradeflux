"""
backfill_market_effects.py
===========================
回填 market_effect_daily：遍历 stock_daily_snapshots 里全部历史交易日，
逐日调用 market_effect_service.compute_and_cache，让市场效应页面的历史趋势图
一上线就有数据。

用法：
  cd backend
  source .venv/bin/activate
  python -m scripts.backfill_market_effects            # 回填全部缺失/旧版本行
  python -m scripts.backfill_market_effects --force    # 强制按当前 formula_version 重算全部
"""

import argparse
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models.stock import StockDailySnapshot
from app.models.market_effect import MarketEffectDaily
from app.services.market_effect_service import compute_and_cache, FORMULA_VERSION


def run(force: bool = False):
    db = SessionLocal()
    try:
        all_dates = [
            d for (d,) in (
                db.query(StockDailySnapshot.date).distinct().order_by(StockDailySnapshot.date).all()
            )
        ]
        if not all_dates:
            print("stock_daily_snapshots 无数据，无法回填。")
            return

        existing = {
            r.trade_date: r.formula_version
            for r in db.query(MarketEffectDaily).all()
        }

        print(f"共 {len(all_dates)} 个交易日（{all_dates[0]} ～ {all_dates[-1]}）")
        done, skipped = 0, 0
        for d in all_dates:
            if not force and existing.get(d) == FORMULA_VERSION:
                skipped += 1
                continue
            result = compute_and_cache(db, d)
            print(f"  [OK] {d}  赚钱={result.profit_strength:.1f}  亏钱={result.loss_strength:.1f}  "
                  f"{result.quadrant}  广度口径={result.breadth_source}")
            done += 1

        print(f"\n完成：计算 {done} 行，跳过 {skipped} 行（已是最新版本）。")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填市场效应历史结果")
    parser.add_argument("--force", action="store_true", help="强制重算全部交易日")
    args = parser.parse_args()
    run(force=args.force)
