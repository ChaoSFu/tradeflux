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

## 限速（2026-09-04 重做）

  分类   区分"被限流"和"这个板块本来就没指数日线"——后者退避没有意义
  退避   指数退避 + 抖动 + 缓慢恢复；每 20 次长歇 30 秒
  冷却   连续被拦到阈值 → 停手并把"30 分钟内不许再打"写进库

**冷却是跨进程的**：真正打死 IP 的不是单次跑得太快，是"跑挂了→立刻重跑"的循环。
所以本脚本被冷却挡下时不会发任何请求，也不要用 --ignore-cooldown 硬来，除非你
确实知道上次的失败不是限流。`--status` 可以只看状态不发请求。

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
from app.services.rate_limiter import cooldown_remaining
from app.services.sector_index_service import (
    backfill_sector_index, existing_bar_counts, MIN_BARS_FOR_RS, THROTTLE_DOMAIN,
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
    ap.add_argument("--delay", type=float, default=2.0,
                    help="请求间隔基准秒。实际间隔带 ±35%% 抖动，被拦后指数退避")
    ap.add_argument("--stop-after-failures", type=int, default=4,
                    help="连续被拦几次判定为限流并写冷却")
    ap.add_argument("--max-requests", type=int, default=80,
                    help="单次运行请求硬上限；剩下的下次重跑（可重入）")
    ap.add_argument("--status", action="store_true", help="只看进度和冷却状态，不发请求")
    ap.add_argument("--ignore-cooldown", action="store_true",
                    help="无视冷却强行开打。只在确知上次失败不是限流时用")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        left = cooldown_remaining(db, THROTTLE_DOMAIN)
        if left is not None:
            print(f"⏳ {THROTTLE_DOMAIN} 冷却中，还需 {left.total_seconds() / 60:.0f} 分钟"
                  + ("（--ignore-cooldown 可强行开打）" if not args.ignore_cooldown else ""))
        else:
            print(f"✅ {THROTTLE_DOMAIN} 无冷却")
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
        plan = min(len(need), args.max_requests)
        # 每 20 次长歇 30 秒，估时要算进去
        est = (plan * args.delay + (plan // 20) * 30) / 60
        print(f"本次最多打 {plan} 次（上限 {args.max_requests}），预计 ≈ {est:.1f} 分钟"
              f"（基准间隔 {args.delay}s + 抖动，被拦会指数退避到更慢）")
        if args.status:
            print("\n--status：只看状态，未发任何请求")
            return
        print()

        r = backfill_sector_index(db, codes, days=args.days, delay=args.delay,
                                  stop_after_failures=args.stop_after_failures,
                                  max_requests=args.max_requests,
                                  ignore_cooldown=args.ignore_cooldown)
        if r.get("cooling_down"):
            return
        print(f"\n补齐 {r['filled']} 个板块，写入 {r['bars']} 行；跳过 {r['skipped']} 个"
              f"；实发请求 {r['requests']} 次，等待 {r['slept'] / 60:.1f} 分钟")
        if r["no_data"]:
            # 这不是失败——是这些板块确实没有指数日线。它们下面股票的 RS_sector
            # 会一直是 None，如实标注，不用别的板块顶替
            print(f"无指数日线 {len(r['no_data'])} 个（非失败）："
                  f"{'、'.join(r['no_data'][:12])}"
                  + ("…" if len(r["no_data"]) > 12 else ""))
        if r["failed"]:
            print(f"被拦 {len(r['failed'])} 个：{'、'.join(r['failed'][:12])}"
                  + ("…" if len(r["failed"]) > 12 else ""))
        if r["cooldown_until"]:
            print(f"⚠️ 判定已被限流，已写入冷却至 {r['cooldown_until']:%H:%M}（UTC）。"
                  "**这段时间内不要重跑**——重跑会被自动挡下，硬来只会加深封锁")
        elif r["aborted"]:
            print("⚠️ 达到本次请求上限提前停手。**直接重跑即可续跑**，已补够的自动跳过")
    finally:
        db.close()


if __name__ == "__main__":
    main()
