"""
一次性回填历史快照的 close_price。

原理：从已知的今日收盘价出发，利用各日 pct_change 向历史反推：
    prev_close = close / (1 + pct_change / 100)

前提：当日快照已有 close_price（由 daily_update 写入）。
对无 pct_change 或无锚点的股票自动跳过，不影响现有数据。

用法：
    cd backend && .venv/bin/python scripts/backfill_close_price.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db
from app.models.stock import Stock, StockDailySnapshot
from sqlalchemy import func as sqlfunc


def backfill_close_price(db) -> tuple[int, int]:
    """
    回填所有股票历史快照的 close_price。
    返回 (处理股票数, 回填快照总数)
    """
    # 只处理有今日 close_price 锚点的股票
    latest_date = db.query(sqlfunc.max(StockDailySnapshot.date)).scalar()
    if not latest_date:
        print("❌ 无快照数据")
        return 0, 0

    print(f"  锚点日期: {latest_date}")

    # 找出所有有锚点的 stock_id（今日快照有 close_price）
    anchor_rows = (
        db.query(StockDailySnapshot.stock_id, StockDailySnapshot.close_price)
        .filter(
            StockDailySnapshot.date == latest_date,
            StockDailySnapshot.close_price.isnot(None),
            StockDailySnapshot.close_price > 0,
        )
        .all()
    )
    anchor_map = {sid: cp for sid, cp in anchor_rows}
    print(f"  有锚点的股票: {len(anchor_map)} 只")

    total_stocks = 0
    total_filled = 0
    batch_size = 100

    stock_ids = list(anchor_map.keys())
    for batch_start in range(0, len(stock_ids), batch_size):
        batch_ids = stock_ids[batch_start:batch_start + batch_size]

        # 拉取该批次所有历史快照，按 stock_id + date 降序
        all_snaps = (
            db.query(StockDailySnapshot)
            .filter(StockDailySnapshot.stock_id.in_(batch_ids))
            .order_by(StockDailySnapshot.stock_id, StockDailySnapshot.date.desc())
            .all()
        )

        # 按股票分组
        from collections import defaultdict
        by_stock: dict = defaultdict(list)
        for s in all_snaps:
            by_stock[s.stock_id].append(s)

        for sid, snaps in by_stock.items():
            # snaps 已按 date 降序排列
            cur_close = anchor_map.get(sid)
            if cur_close is None:
                continue

            filled = 0
            for snap in snaps:
                if snap.close_price is not None and snap.close_price > 0:
                    # 已有值，更新锚点继续向前推
                    cur_close = snap.close_price
                    continue

                if snap.pct_change is None:
                    # 无涨跌幅，无法反推，中断此股
                    break

                # 反推前一日收盘价
                prev_close = cur_close / (1 + snap.pct_change / 100)
                if prev_close <= 0:
                    break

                snap.close_price = round(prev_close, 4)
                cur_close = prev_close
                filled += 1

            if filled > 0:
                total_filled += filled
                total_stocks += 1

        db.commit()
        done = min(batch_start + batch_size, len(stock_ids))
        print(f"  进度: {done}/{len(stock_ids)} 只，已回填快照 {total_filled} 条")

    return total_stocks, total_filled


def main():
    init_db()
    db = SessionLocal()
    try:
        print(f"\n{'='*55}")
        print(f"  历史快照 close_price 回填")
        print(f"{'='*55}\n")

        # 回填前统计
        total = db.query(StockDailySnapshot).count()
        has_close = db.query(StockDailySnapshot).filter(
            StockDailySnapshot.close_price.isnot(None)
        ).count()
        print(f"  回填前: {has_close}/{total} 条快照有 close_price ({has_close/total*100:.1f}%)\n")

        stocks, filled = backfill_close_price(db)

        # 回填后统计
        has_close_after = db.query(StockDailySnapshot).filter(
            StockDailySnapshot.close_price.isnot(None)
        ).count()
        print(f"\n{'─'*55}")
        print(f"  处理股票: {stocks} 只")
        print(f"  回填快照: {filled} 条")
        print(f"  回填后: {has_close_after}/{total} 条 ({has_close_after/total*100:.1f}%)")
        print(f"{'─'*55}")
        print("\n✅ 回填完成\n")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
