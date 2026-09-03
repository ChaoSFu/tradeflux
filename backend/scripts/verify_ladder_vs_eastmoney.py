"""
拿东财「连板天梯」权威接口逐日核对我们算出来的连板梯队。**只读，不改数据。**

## 为什么必须有这一步

verify_board_history.py 是**自指的**：它只对库里已经标记 board_count>=3 的那些
(日期,股票) 重新拉 K 线对账。所以它能证伪"我们说有、实际没有"（高估），却**完全
看不见"实际有、我们没记"**（低估）。

2026-08-06 就是活证据：那天校验报「0 分歧」，而东财天梯是 10 板、我们只有 5 板——
整段行情的最高点漏掉了，校验却全绿。用一个单向检查当作"数据可信"的结论，是这轮
里我犯的最严重的判断错误。

东财 RPT_INTSELECTION_MONITORHIS 是独立外部标准，且 TRADE_DATE 是入参，任意历史
日期都能取，正好补上那个方向。

## 口径对齐（不对齐就会得出一堆假差异）

  · 东财只返回 **2 板及以上**，不含首板 → 我们这边也只比 2 板及以上
  · 东财 filter 里 IS_ST="0" 已排除 ST → 我们的选股 prompt 也是「非ST」，一致
  · 双方都是"当日仍在连板"的口径

## 三类差异分开报，不揉成一个准确率

  MISS      东财有、我们没有        → **漏记**，最危险，会低估市场高度
  EXTRA     我们有、东财没有        → 多记
  MISMATCH  都有但板数不同          → 连板链算错
"""
import argparse
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.limit_up_detail_fetcher import fetch_limit_up_ladder

MIN_BOARD = 2      # 东财只给 2 板及以上，比这个低没得比


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=100, help="回溯多少自然日")
    ap.add_argument("--sleep", type=float, default=0.3, help="每次请求间隔秒（默认0.3）")
    ap.add_argument("--verbose", action="store_true", help="逐条打印差异明细")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        since = date.today() - timedelta(days=args.days)
        rows = (
            db.query(StockDailySnapshot.date, Stock.code, Stock.name,
                     StockDailySnapshot.board_count)
            .join(Stock, Stock.id == StockDailySnapshot.stock_id)
            .filter(StockDailySnapshot.date >= since,
                    StockDailySnapshot.board_count >= MIN_BOARD)
            .all()
        )
        ours: dict = defaultdict(dict)
        names: dict = {}
        for d, code, name, bc in rows:
            ours[d][code] = bc
            names[code] = name
        days = sorted(ours)
        if not days:
            print("库里没有可比的连板记录")
            return
        print(f"核对 {len(days)} 个交易日：{days[0]} ~ {days[-1]}\n")

        stat = Counter()
        per_day = []
        details = []
        unknown_days = []
        for d in days:
            em = fetch_limit_up_ladder(d)
            time.sleep(args.sleep)
            if em is None:
                unknown_days.append(d)
                continue
            mine = ours[d]
            miss = {c: n for c, n in em.items() if c not in mine}
            extra = {c: n for c, n in mine.items() if c not in em}
            mism = {c: (mine[c], em[c]) for c in set(mine) & set(em) if mine[c] != em[c]}
            stat["MISS"] += len(miss); stat["EXTRA"] += len(extra)
            stat["MISMATCH"] += len(mism); stat["OK"] += len(set(mine) & set(em)) - len(mism)
            em_h = max(em.values(), default=0)
            my_h = max(mine.values(), default=0)
            per_day.append((d, my_h, em_h, len(mine), len(em), len(miss), len(extra), len(mism)))
            for c, n in sorted(miss.items()):
                details.append((d, c, names.get(c, em and "?"), "MISS", f"东财 {n}板，我们没有"))
            for c, n in sorted(extra.items()):
                details.append((d, c, names.get(c), "EXTRA", f"我们 {n}板，东财没有"))
            for c, (a, b) in sorted(mism.items()):
                details.append((d, c, names.get(c), "MISMATCH", f"我们 {a}板 vs 东财 {b}板"))

        print("─" * 72)
        print(f"  {'日期':<12}{'我们最高':>8}{'东财最高':>8}{'我们只数':>8}{'东财只数':>8}"
              f"{'漏':>5}{'多':>5}{'板数不符':>9}")
        print("─" * 72)
        for d, mh, eh, mn, en, ms, ex, mm in per_day:
            flag = "  ← 最高板不符" if mh != eh else ""
            print(f"  {str(d):<12}{mh:>8}{eh:>8}{mn:>8}{en:>8}{ms:>5}{ex:>5}{mm:>9}{flag}")
        print("─" * 72)

        bad_h = sum(1 for _d, mh, eh, *_r in per_day if mh != eh)
        total = sum(stat.values())
        print(f"\n对账结果（{len(per_day)} 个交易日，{total} 个股票日）")
        print(f"  一致        {stat['OK']:>5}")
        print(f"  漏记 MISS   {stat['MISS']:>5}   ← 东财有我们没有，**会低估市场高度**")
        print(f"  多记 EXTRA  {stat['EXTRA']:>5}")
        print(f"  板数不符    {stat['MISMATCH']:>5}")
        print(f"\n  最高板不符的交易日：{bad_h}/{len(per_day)}")
        if unknown_days:
            print(f"  东财未返回（不知道，不计入分母）：{len(unknown_days)} 天 {unknown_days}")

        if details and (args.verbose or len(details) <= 40):
            print(f"\n明细（{len(details)} 条）")
            for d, c, n, kind, msg in details:
                print(f"  {d}  {c} {n or '':<8} [{kind}] {msg}")
        elif details:
            print(f"\n明细 {len(details)} 条，加 --verbose 查看")
    finally:
        db.close()


if __name__ == "__main__":
    main()
