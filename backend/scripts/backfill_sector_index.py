"""
回填板块指数日线，作为相对强度 RS_sector 的基准。**可重复执行，跑一半被掐断可续跑。**

## 为什么只补一部分板块

库里 888 个板块，但 RS_sector 只需要**强势池股票的主板块**——去重后通常几十个。
而数据源 push2his **限流很凶**：2026-09-03 实测约 15 次快速请求就把开发机 IP 打进
限流，连既有代码依赖的上证指数也一起拉不到。所以能少打就少打。

默认 `--scope strong` 只补强势池主板块。`--scope watched` 补所有"关注板块"（308 个），
只在明确需要时用，并且要接受它会打几百次。

## 断点续跑

每只板块独立提交、独立判断"补够没有"（阈值 MIN_BARS_FOR_RS=70 根，够 60 日窗口 +
锚点 + 余量）。被限流掐断后原样重跑，已补够的自动跳过。

连续失败到阈值会**主动停手**——继续打只会加深封锁，还会连累依赖同一域名的指数同步。

## 历史与增量的分工

这个脚本只管**历史**（一次性 300 根）。往后每天那一根由 sync_boards 从它已有的
clist 调用里顺手写（f2 字段，零新增请求），不再需要 push2his。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.sector import Sector
from app.models.stock import Stock
from app.services.sector_index_service import (
    backfill_sector_index, existing_bar_counts, MIN_BARS_FOR_RS,
)


def _strong_pool_sector_codes(db) -> list[str]:
    """强势池股票的主板块去重。Stock.primary_sector_id 由 daily_update 每天维护。"""
    q = (db.query(Sector.code)
         .join(Stock, Stock.primary_sector_id == Sector.id)
         .filter(Stock.in_strong_pool.is_(True))
         .distinct())
    return [c for (c,) in q.all() if c]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", choices=["strong", "watched"], default="strong")
    ap.add_argument("--days", type=int, default=300)
    ap.add_argument("--delay", type=float, default=1.5, help="每次请求间隔秒（限流很凶，别调小）")
    ap.add_argument("--stop-after-failures", type=int, default=5)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.scope == "strong":
            codes = _strong_pool_sector_codes(db)
            label = "强势池主板块"
        else:
            codes = [c for (c,) in db.query(Sector.code)
                     .filter(Sector.is_watched.is_(True)).all() if c]
            label = "关注板块"
        if not codes:
            print(f"没有找到{label}，无可回填")
            return

        have = existing_bar_counts(db, codes)
        need = [c for c in codes if have.get(c, 0) < MIN_BARS_FOR_RS]
        print(f"{label} {len(codes)} 个，其中 {len(need)} 个需要回填"
              f"（已补够 {len(codes) - len(need)} 个）")
        if not need:
            print("全部已补够，无需操作")
            return
        print(f"预计耗时 ≈ {len(need) * args.delay / 60:.1f} 分钟（间隔 {args.delay}s）\n")

        r = backfill_sector_index(db, codes, days=args.days, delay=args.delay,
                                  stop_after_failures=args.stop_after_failures)
        print(f"\n补齐 {r['filled']} 个板块，写入 {r['bars']} 行；跳过 {r['skipped']} 个")
        if r["failed"]:
            print(f"失败 {len(r['failed'])} 个：{'、'.join(r['failed'][:12])}"
                  + ("…" if len(r["failed"]) > 12 else ""))
        if r["aborted"]:
            print("⚠️ 因连续失败提前停手（多半已被限流）。**直接重跑本脚本即可续跑**，"
                  "已补够的会自动跳过；建议隔一段时间或加大 --delay")
    finally:
        db.close()


if __name__ == "__main__":
    main()
