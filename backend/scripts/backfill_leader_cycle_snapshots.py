"""
回填 LeaderCycleSnapshot 历史 —— **零外部请求**，只从库里已有的 K 线重建。

## 为什么必须做

Price Lifecycle 状态机是 replay 引擎：它靠**逐日**的 ma5/10/20/30、
new_post_break_high/low、latest_close 序列往前推状态。而这些只存在于
LeaderCycleSnapshot，那张表 2026-09-03 才建，生产上只有 2026-09-04 一天 60 行。

一天历史意味着：一条 transition 都跑不出来，所有"连续两个有效 observation"的规则
永远不会触发，previous_lifecycle_state / state_since_date 全是空。状态机上线就是
一屏 UNKNOWN，也就没法用真实数据检验规则对不对。

而 K 线本身在 StockDailySnapshot 里已经有 ~65 个交易日，`build_snapshots` 是
klines_map 的纯函数——把 bar 序列按日期截断到 T，就能重建 T 那天的快照。

## 三条必须如实标注的偏差

1. **幸存者偏差**：遍历的是**今天**的强势池。回填出来的历史行，是"今天在池子里的
   这些股票，当时长什么样"，**不是"当时的池子"**。当时还没进池的股票不会有行。
   做趋势研究可以，当成历史成分股用就是错的。

2. **口径是今天的**：连板判定、均线、RS 全部按今天的代码算。这正是"事实层不落状态
   判定"的好处——口径变了可以重算；但也意味着回填行和当时真跑出来的行可能不同。

3. **停牌信息拿不到**：daily_update 当时也没传 suspended_map，所以 suspended_days
   恒为 0、停牌算作数据缺口。回填保持同一口径，不额外造一套。

## 结算标记

回填的每一天都是**历史交易日**，那天早收盘了，所以 `settled=True`。
唯一的例外是"今天"——脚本默认不碰今天那一行，交给 daily_update 写。
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.stock import Stock, StockDailySnapshot
from app.services.leader_cycle_snapshot_service import build_snapshots
from app.services.screening_service import compute_window_stats
from app.services.trading_calendar import get_trading_days

# daily_update 里的 K 线→bar 转换，直接复用，不另写一套
from daily_update import _snapshots_to_klinebars    # noqa: E402

# 至少要几根 bar 才值得重建那一天。少于这个数，均线大多是 None，
# 状态机也推不动，写进去只是噪声
MIN_BARS = 10

# **必须跟 daily_update 用同一个窗口**（daily_update.py:462 取最近 65 条快照）。
# identify_leader_cycle 扫的是传进来的整段 bar，窗口更长就会找到更早的连板周期
# ——回填出来的历史会跟线上真跑出来的行口径不同，两者拼在一起就是一条口径混杂的
# 序列，而状态机正是靠这条序列往前推。
# 这跟 2026-09-04 修的"连板段被 bars[-60:] 切开"是同一个家族：**窗口口径必须
# 只有一处定义**，第二处迟早跟第一处分叉。
KLINE_WINDOW = 65


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=60, help="往回补多少个交易日")
    ap.add_argument("--overwrite", action="store_true",
                    help="覆盖已存在的行。默认跳过——不动 daily_update 真跑出来的行")
    ap.add_argument("--include-today", action="store_true",
                    help="连今天也重建。默认不碰，今天那行归 daily_update 管")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        pool = db.query(Stock).filter(Stock.in_strong_pool.is_(True)).all()
        if not pool:
            print("强势池为空，无可回填")
            return
        ids = {st.id: st.code for st in pool}
        by_id = {st.id: st for st in pool}
        by_code = {st.code: st for st in pool}
        print(f"强势池 {len(pool)} 只（**今天的**成分，不是历史成分）")

        tdays = get_trading_days(db, need_through=date.today())
        if not tdays:
            print("拿不到交易日历，中止——没有日历就分不清"
                  "'那天没数据'和'那天不开市'")
            return
        today = date.today()
        cand = [d for d in tdays if d <= today]
        if not args.include_today and cand and cand[-1] == today:
            cand = cand[:-1]
        targets = cand[-args.days:]
        if not targets:
            print("没有可回填的交易日")
            return

        have = {d for (d,) in db.query(LeaderCycleSnapshot.date).distinct().all()}
        todo = targets if args.overwrite else [d for d in targets if d not in have]
        print(f"候选 {len(targets)} 个交易日（{targets[0]} ~ {targets[-1]}），"
              f"其中 {len(todo)} 个需要重建"
              + ("" if args.overwrite else f"（已有 {len(targets) - len(todo)} 个，跳过）"))
        if not todo:
            print("无需操作")
            return

        # K 线一次全部读进内存：60 只 × ~65 天，几千行，比逐日查库快得多
        snaps_by_stock: dict = {}
        for snap in (db.query(StockDailySnapshot)
                     .filter(StockDailySnapshot.stock_id.in_(list(ids)),
                             StockDailySnapshot.close_price.isnot(None))
                     .order_by(StockDailySnapshot.stock_id,
                               StockDailySnapshot.date).all()):
            snaps_by_stock.setdefault(snap.stock_id, []).append(snap)
        print(f"读入 K 线 {sum(len(v) for v in snaps_by_stock.values())} 行\n")

        total_written = total_nocycle = 0
        for d in todo:
            klines_map = {}
            for sid, code in ids.items():
                # **按日期截断**——这是防 look-ahead 的关键：重建 T 那天，
                # 只能看到 <= T 的 bar
                # **按日期截断**——这是防 look-ahead 的关键：重建 T 那天，
                # 只能看到 <= T 的 bar。再截到 KLINE_WINDOW 根，跟线上同口径
                cut = [s for s in snaps_by_stock.get(sid, []) if s.date <= d]
                cut = cut[-KLINE_WINDOW:]
                if len(cut) < MIN_BARS:
                    continue
                st = by_id[sid]
                klines_map[code] = _snapshots_to_klinebars(cut, code, st.is_st)
            if not klines_map:
                print(f"  {d}  无足够K线，跳过")
                continue
            # 均线必须一起算。漏了它 build_snapshots 会把 ma5/10/20/30 全留成
            # None，写出来的行状态机一步都推不动——2026-09-04 第一次回填就是
            # 这么跑出 1639 行废数据的
            stats_map = {}
            for code, bars in klines_map.items():
                st = by_code[code]
                stat = compute_window_stats(
                    code, st.name, st.is_st, bars,
                    trading_days=[x for x in tdays if x <= d])
                if stat is not None:
                    stats_map[code] = stat
            r = build_snapshots(
                db, d, klines_map,
                trading_days=[x for x in tdays if x <= d],
                stats_map=stats_map,
                # 回填的每一天都是历史交易日，那天早收盘了
                settled=True,
            )
            total_written += r["written"]
            total_nocycle += r["no_cycle"]
            print(f"  {d}  写入 {r['written']} 只"
                  + (f"，{r['no_cycle']} 只无周期" if r["no_cycle"] else "")
                  + (f"，清理 {r['cleaned']} 行旧结论" if r.get("cleaned") else "")
                  + (f"，{r['skipped']} 只跳过" if r["skipped"] else ""))

        # 均线覆盖率必须打出来。上次回填漏传 stats_map，1639 行的均线全是 None，
        # 而输出里只报"写入多少只"，看不出行是残的
        from sqlalchemy import func as _f
        n_all, n_ma = db.query(
            _f.count(LeaderCycleSnapshot.id),
            _f.count(LeaderCycleSnapshot.ma5)).one()
        print(f"\n共写入 {total_written} 行，覆盖 {len(todo)} 个交易日")
        print(f"全表 {n_all} 行，其中 MA5 有值 {n_ma} 行"
              + ("" if n_ma else "  ← **全是空的，状态机推不动，检查 stats_map**"))
        print("⚠️ 这些行是「今天在池子里的股票，当时长什么样」，**不是当时的池子成分**。"
              "做趋势/状态轨迹研究可以，当历史成分股用就是错的。")
    finally:
        db.close()


if __name__ == "__main__":
    main()
