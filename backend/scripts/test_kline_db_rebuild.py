"""
测试 K 线 DB 重建路径的正确性（只读，不写入任何数据）。

模拟"明日运行"场景：把今日作为 target_date 的次日，
验证 _build_klines_from_db 能正确分组，并验证重建出的 KLineBar 与
全量 API 拉取结果在关键统计指标上一致。

用法：
    cd backend && .venv/bin/python scripts/test_kline_db_rebuild.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
from sqlalchemy import func as sqlfunc

from app.database import SessionLocal, init_db
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import (
    StockBasicInfo, KLineBar, fetch_klines_batch,
)
from app.services.screening_service import compute_window_stats
from scripts.daily_update import (
    _build_klines_from_db,
    _snapshots_to_klinebars,
    _MIN_SNAPSHOTS_FOR_DB_REBUILD,
)

SAMPLE_SIZE = 5          # 抽取多少只股票做 API 对比验证
VERIFY_API  = True       # 是否真正调 API 验证（False 则只看分组统计）


def main():
    init_db()
    db = SessionLocal()

    try:
        # ── 1. 确认今日快照 close_price 写入情况 ──────────────────────────
        latest_date = db.query(sqlfunc.max(StockDailySnapshot.date)).scalar()
        if not latest_date:
            print("❌ DB 中无快照数据")
            return

        total_snaps = db.query(StockDailySnapshot).filter(
            StockDailySnapshot.date == latest_date
        ).count()
        with_close = db.query(StockDailySnapshot).filter(
            StockDailySnapshot.date == latest_date,
            StockDailySnapshot.close_price.isnot(None),
        ).count()

        print(f"\n{'='*60}")
        print(f"  K 线 DB 重建路径测试")
        print(f"{'='*60}")
        print(f"\n[快照现状] 最新日期: {latest_date}")
        print(f"  当日快照总数:  {total_snaps}")
        print(f"  有 close_price: {with_close} ({with_close/total_snaps*100:.1f}%)")

        if with_close == 0:
            print("\n⚠️  今日快照尚无 close_price，请先完整运行一次 daily_update.py 再测试")
            return

        # ── 2. 模拟"明日"的 target_date ───────────────────────────────────
        # 取下一个工作日（跳过周末）
        mock_target = latest_date + timedelta(days=1)
        while mock_target.weekday() >= 5:
            mock_target += timedelta(days=1)
        print(f"\n[模拟场景] target_date = {mock_target}（模拟明日运行）")

        # ── 3. 构造候选股列表（取今日强势池 + 涨跌停，模拟真实候选）─────────
        candidate_stocks = (
            db.query(Stock)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(
                StockDailySnapshot.date == latest_date,
                Stock.is_st == False,  # noqa
            )
            .limit(300)
            .all()
        )

        candidates = [
            StockBasicInfo(
                code=s.code,
                name=s.name,
                market=1 if s.market == "SH" else 0,
                is_st=s.is_st,
                pct_change=0.0,
                turnover_rate=0.0,
            )
            for s in candidate_stocks
        ]
        print(f"\n[候选股] 共 {len(candidates)} 只")

        # ── 4. 运行分组逻辑 ────────────────────────────────────────────────
        db_klines_map, db_group, full_group = _build_klines_from_db(
            candidates, db, mock_target
        )

        print(f"\n[分组结果]")
        print(f"  DB 重建组（只拉今日）: {len(db_group)} 只  ({len(db_group)/len(candidates)*100:.1f}%)")
        print(f"  全量拉取组（65日）:   {len(full_group)} 只  ({len(full_group)/len(candidates)*100:.1f}%)")

        # 展示全量组的原因（快照数不足）
        if full_group:
            print(f"\n  全量拉取组样本（前10只）:")
            stock_id_map = {
                row[0]: row[1]
                for row in db.query(Stock.code, Stock.id)
                .filter(Stock.code.in_([s.code for s in full_group[:10]])).all()
            }
            for info in full_group[:10]:
                sid = stock_id_map.get(info.code)
                cnt = 0
                if sid:
                    cnt = db.query(StockDailySnapshot).filter(
                        StockDailySnapshot.stock_id == sid,
                        StockDailySnapshot.date < mock_target,
                        StockDailySnapshot.close_price.isnot(None),
                    ).count()
                print(f"    {info.code} {info.name}: 有效历史快照 {cnt} 条")

        # ── 5. 验证 DB 重建组的历史 KLineBar 质量 ─────────────────────────
        print(f"\n[DB 重建质量检查] 随机抽取 {min(SAMPLE_SIZE, len(db_group))} 只:")
        import random
        sample = random.sample(db_group, min(SAMPLE_SIZE, len(db_group)))

        for info in sample:
            bars = db_klines_map[info.code]
            has_close = sum(1 for b in bars if b.close_price > 0)
            limit_ups = sum(1 for b in bars if b.is_limit_up)
            print(f"  {info.code} {info.name}: {len(bars)} 根历史K线，"
                  f"有收盘价 {has_close} 根，涨停 {limit_ups} 次")

        # ── 6. 可选：调 API 验证关键指标一致性 ────────────────────────────
        if not VERIFY_API:
            print(f"\n[API 对比] 已跳过（VERIFY_API=False）")
        else:
            print(f"\n[API 对比验证] 拉取 {len(sample)} 只股票的完整 65 日 K 线...")
            api_klines = fetch_klines_batch(sample, days=65, max_workers=5)

            print(f"\n  {'代码':<8} {'名称':<10} {'指标':<20} {'DB重建':>8} {'API全量':>8} {'差异':>6}")
            print(f"  {'-'*62}")

            all_pass = True
            for info in sample:
                db_bars  = db_klines_map[info.code]
                api_bars = api_klines.get(info.code, [])
                if not api_bars:
                    print(f"  {info.code}  {info.name:<10} API 无数据，跳过")
                    continue

                # 用 API 的今日 bar 拼接到 DB 历史末尾（模拟真实合并）
                today_bar = api_bars[-1]
                if db_bars and db_bars[-1].date == today_bar.date:
                    merged = db_bars[:-1] + [today_bar]
                else:
                    merged = db_bars + [today_bar]

                db_stats  = compute_window_stats(info.code, info.name, info.is_st, merged)
                api_stats = compute_window_stats(info.code, info.name, info.is_st, api_bars)

                if not db_stats or not api_stats:
                    print(f"  {info.code}  stats 计算失败，跳过")
                    continue

                checks = [
                    ("60日涨停天数", db_stats.limit_up_days_60d,  api_stats.limit_up_days_60d),
                    ("60日最高连板", db_stats.board_count_60d,    api_stats.board_count_60d),
                    ("10日涨停天数", db_stats.limit_up_days_10d,  api_stats.limit_up_days_10d),
                    ("阶段",        db_stats.phase,               api_stats.phase),
                ]
                for metric, db_val, api_val in checks:
                    match = "✅" if db_val == api_val else "❌"
                    if db_val != api_val:
                        all_pass = False
                    print(f"  {info.code}  {info.name:<10} {metric:<20} {str(db_val):>8} {str(api_val):>8} {match:>6}")
                print()

            print(f"\n{'  ✅ 全部指标一致' if all_pass else '  ❌ 存在差异，需排查'}")

        # ── 7. 总结 ────────────────────────────────────────────────────────
        print(f"\n{'─'*60}")
        print(f"  预计明日拉取K线耗时估算：")
        print(f"    DB重建组 {len(db_group)} 只 × 今日2根 ≈ 并发拉取，预计 5-10s")
        print(f"    全量拉取 {len(full_group)} 只 × 65日    ≈ 串行分批，预计 {len(full_group)*0.2:.0f}-{len(full_group)*0.4:.0f}s")
        print(f"  （今日全量拉取 {len(candidates)} 只耗时约 40s，对比参考）")
        print(f"{'─'*60}\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
