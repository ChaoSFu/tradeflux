"""
一次性为现有强势池 + 今日涨跌停股批量拉取 65 日 K 线，填充历史快照 close_price。

只写 close_price 到已有快照，不新建快照，不改动其他字段。
运行后 DB 重建路径（_build_klines_from_db）即可正常工作。

用法：
    cd backend && .venv/bin/python scripts/seed_kline_history.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from sqlalchemy import func as sqlfunc

from app.database import SessionLocal, init_db
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import StockBasicInfo, fetch_klines_batch


def main():
    init_db()
    db = SessionLocal()
    try:
        print(f"\n{'='*60}")
        print(f"  历史 K 线 close_price 批量填充")
        print(f"{'='*60}\n")

        latest_date = db.query(sqlfunc.max(StockDailySnapshot.date)).scalar()
        if not latest_date:
            print("❌ 无快照数据，请先运行 daily_update.py")
            return

        # ── 目标股票：强势池 + 有快照的股票（候选过的）─────────────────
        target_stocks = (
            db.query(Stock)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(Stock.is_st == False)  # noqa
            .distinct()
            .all()
        )
        print(f"  目标股票（有快照记录）: {len(target_stocks)} 只")

        # 过滤掉已有足够 close_price 历史的股票（≥60 条）
        from scripts.daily_update import _MIN_SNAPSHOTS_FOR_DB_REBUILD
        needs_fill = []
        for stock in target_stocks:
            cnt = db.query(StockDailySnapshot).filter(
                StockDailySnapshot.stock_id == stock.id,
                StockDailySnapshot.close_price.isnot(None),
            ).count()
            if cnt < _MIN_SNAPSHOTS_FOR_DB_REBUILD:
                needs_fill.append(stock)

        print(f"  需要填充的股票: {len(needs_fill)} 只（历史 close_price < {_MIN_SNAPSHOTS_FOR_DB_REBUILD} 条）\n")

        if not needs_fill:
            print("✅ 所有股票已有足够历史，无需填充")
            return

        # ── 批量拉取 K 线 ─────────────────────────────────────────────
        infos = [
            StockBasicInfo(
                code=s.code,
                name=s.name,
                market=1 if s.market == "SH" else 0,
                is_st=s.is_st,
                pct_change=0.0,
                turnover_rate=0.0,
            )
            for s in needs_fill
        ]

        print(f"  开始并发拉取 65 日 K 线（{len(infos)} 只，max_workers=8）...")
        import time
        t0 = time.time()
        klines_map = fetch_klines_batch(infos, days=65, max_workers=8)
        elapsed = time.time() - t0
        fetched = sum(1 for v in klines_map.values() if v)
        print(f"  拉取完成: {fetched}/{len(infos)} 只，耗时 {elapsed:.1f}s\n")

        # ── 按股票写入/新建快照 ───────────────────────────────────────
        filled_stocks = 0
        filled_snaps = 0
        created_snaps = 0

        BATCH_COMMIT = 50
        for i, stock in enumerate(needs_fill):
            bars = klines_map.get(stock.code)
            if not bars:
                continue

            # 查该股已有快照的日期集合
            existing_dates = {
                row[0]
                for row in db.query(StockDailySnapshot.date)
                .filter(StockDailySnapshot.stock_id == stock.id)
                .all()
            }

            updated = 0
            created = 0
            for bar in bars:
                if bar.close_price <= 0:
                    continue
                if bar.date in existing_dates:
                    # 已有快照：只补 close_price（若缺失）
                    snap = (
                        db.query(StockDailySnapshot)
                        .filter(
                            StockDailySnapshot.stock_id == stock.id,
                            StockDailySnapshot.date == bar.date,
                        )
                        .first()
                    )
                    if snap and not snap.close_price:
                        snap.close_price = round(bar.close_price, 4)
                        updated += 1
                else:
                    # 缺失快照：新建，只存 K 线原始字段
                    db.add(StockDailySnapshot(
                        stock_id=stock.id,
                        date=bar.date,
                        close_price=round(bar.close_price, 4),
                        pct_change=round(bar.pct_change, 4),
                        turnover_rate=round(bar.turnover_rate, 4),
                        is_limit_up=bar.is_limit_up,
                        is_limit_down=bar.is_limit_down,
                        is_broken_board=bar.is_broken_board,
                        is_one_word_limit_up=bar.is_one_word_limit_up,
                    ))
                    created += 1

            if updated + created > 0:
                filled_stocks += 1
                filled_snaps += updated
                created_snaps += created

            if (i + 1) % BATCH_COMMIT == 0:
                db.commit()
                print(f"  进度: {i+1}/{len(needs_fill)} 只，新建 {created_snaps} 条，更新 {filled_snaps} 条")

        db.commit()

        # ── 验证结果 ─────────────────────────────────────────────────
        enough_count = 0
        for stock in needs_fill:
            cnt = db.query(StockDailySnapshot).filter(
                StockDailySnapshot.stock_id == stock.id,
                StockDailySnapshot.close_price.isnot(None),
            ).count()
            if cnt >= _MIN_SNAPSHOTS_FOR_DB_REBUILD:
                enough_count += 1

        print(f"{'─'*60}")
        print(f"  填充股票: {filled_stocks} 只")
        print(f"  新建快照: {created_snaps} 条，更新 close_price: {filled_snaps} 条")
        print(f"  填充后 ≥{_MIN_SNAPSHOTS_FOR_DB_REBUILD} 条历史的股票: {enough_count}/{len(needs_fill)} 只")
        if enough_count < len(needs_fill):
            print(f"  ⚠️  {len(needs_fill)-enough_count} 只快照仍不足（可能是上市时间短的新股）")
        print(f"{'─'*60}")
        print("\n✅ 填充完成，DB 重建路径已可用\n")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 失败: {e}")
        import traceback; traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    main()
