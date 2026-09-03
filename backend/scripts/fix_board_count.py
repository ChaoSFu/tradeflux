"""
用东财连板天梯 + 腾讯K线三源裁决，修历史 board_count。**默认只读，--apply 才动手。**

## 病灶

拿东财天梯逐日对账，66 个交易日里 7 条 board_count 对不上，形态高度一致：

    06-10  002354 天娱数科   我们2板 vs 东财4板
    06-22  600353 旭光电子   我们2板 vs 东财5板
    06-22  603989 艾华集团   我们2板 vs 东财4板
    06-22  600397 江钨装备   我们2板 vs 东财4板
    06-22  000811 冰轮环境   我们2板 vs 东财3板
    07-03  603559 中通国脉   我们2板 vs 东财3板
    06-22  300522 世名科技   我们3板 vs 东财2板   ← 唯一反向

六条**全被封顶在 2**，而主链路对 DB 重建组是「拉近2日」——只从接口拉 2 根新 bar
再跟 DB 历史合并。合并要是没接上，连板就恰好只能数到 2。**07-03 之后一条都没有**，
说明这个坑已经被后来某次修复堵上了，剩下的是历史数据。

## 为什么不能只信东财

东财天梯**也会漏**。实测至少 3 例真实连板股它没收录（002827 08-04、600272 08-12、
600540 09-02），每一例都用腾讯 K 线按交易所精确涨停价核对过：收盘价分毫不差等于
涨停价。09-02 那天正是这个漏记让"东财 4 板 vs 我们 5 板"看起来像我们错——其实
我们是对的。

所以三源裁决，谁也不当真理：

    我们的 board_count  ×  东财天梯  ×  腾讯K线重算连板

  · 东财与腾讯一致、跟我们不同  → 我们错，修
  · 我们与腾讯一致、跟东财不同  → **东财漏记**，不动
  · 三方两两不同 / 腾讯拿不到   → 无法裁决，如实报出，不动

腾讯那一路的连板用真实 bar 序列重算：停牌那几天本来就没有 bar，不打断连板，这跟
交易所口径一致（爱丽家居停牌三天复牌接力算第 10 板，不是重新算第 1 板）。
"""
import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import (
    market_int, get_limit_pct, _fetch_kline_tencent,
)
from app.services.limit_up_detail_fetcher import fetch_limit_up_ladder


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=100)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--apply", action="store_true", help="真正执行；不加则只报告")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        since = date.today() - timedelta(days=args.days)
        rows = (
            db.query(StockDailySnapshot, Stock.code, Stock.name,
                     Stock.market, Stock.is_st)
            .join(Stock, Stock.id == StockDailySnapshot.stock_id)
            .filter(StockDailySnapshot.date >= since,
                    StockDailySnapshot.board_count >= 2)
            .all()
        )
        ours: dict = defaultdict(dict)
        meta: dict = {}
        snap_at: dict = {}
        for snap, code, name, market, is_st in rows:
            if is_st or "ST" in (name or "").upper() or "退" in (name or ""):
                continue
            ours[snap.date][code] = snap.board_count
            snap_at[(code, snap.date)] = snap
            meta[code] = (name, market, is_st)
        days = sorted(ours)
        if not days:
            print("库里没有可比的连板记录")
            return
        print(f"核对 {len(days)} 个交易日：{days[0]} ~ {days[-1]}\n")

        # ── 1. 找出与东财不一致的 ──────────────────────────────────────────
        suspects = []      # (date, code, ours, em)
        for d in days:
            em = fetch_limit_up_ladder(d)
            time.sleep(args.sleep)
            if em is None:
                print(f"  {d}: 东财未返回，跳过（不知道，不是没有）")
                continue
            for code, n in ours[d].items():
                if code in em and em[code] != n:
                    suspects.append((d, code, n, em[code]))
        if not suspects:
            print("与东财天梯完全一致，无需修复")
            return
        print(f"与东财不一致 {len(suspects)} 条，逐一用腾讯K线裁决…\n")

        # ── 2. 腾讯重算连板做第三方裁决 ────────────────────────────────────
        streaks: dict = {}
        for _d, code, _o, _e in suspects:
            if code in streaks:
                continue
            name, market, is_st = meta[code]
            try:
                bars = _fetch_kline_tencent(code, market_int(market, code), 200,
                                            bool(is_st), get_limit_pct(code, bool(is_st)), 20)
            except Exception as e:  # noqa: BLE001
                print(f"  {code} {name}: 腾讯拉取失败（{type(e).__name__}）")
                streaks[code] = None
                continue
            run, st = 0, {}
            for b in sorted(bars, key=lambda x: x.date):
                run = run + 1 if b.is_limit_up else 0
                st[b.date] = run
            streaks[code] = st

        fixes, em_wrong, undecided = [], [], []
        for d, code, mine, em_n in suspects:
            st = streaks.get(code)
            tx = st.get(d) if st else None
            name = meta[code][0]
            if tx is None:
                undecided.append((d, code, name, mine, em_n, "腾讯无该日数据"))
            elif tx == em_n and tx != mine:
                fixes.append((d, code, name, mine, tx))
            elif tx == mine and tx != em_n:
                em_wrong.append((d, code, name, mine, em_n))
            else:
                undecided.append((d, code, name, mine, em_n, f"腾讯说{tx}板，三方都不同"))

        print(f"我们错（东财+腾讯一致）：{len(fixes)} 条 ← 待修")
        for d, code, name, old, new in fixes:
            print(f"    {d}  {code} {name:<8} {old} → {new}")
        print(f"\n东财漏记（我们+腾讯一致）：{len(em_wrong)} 条 ← **不动**")
        for d, code, name, mine, em_n in em_wrong:
            print(f"    {d}  {code} {name:<8} 我们{mine}板/腾讯{mine}板，东财{em_n}板")
        if undecided:
            print(f"\n无法裁决：{len(undecided)} 条 ← **不动**")
            for d, code, name, mine, em_n, why in undecided:
                print(f"    {d}  {code} {name:<8} 我们{mine} 东财{em_n} —— {why}")

        if not args.apply:
            print(f"\n（试运行，未改动。加 --apply 修 {len(fixes)} 条）")
            return
        for d, code, _n, _o, new in fixes:
            snap_at[(code, d)].board_count = new
        db.commit()
        print(f"\n✅ 已修 {len(fixes)} 条 board_count")
    finally:
        db.close()


if __name__ == "__main__":
    main()
