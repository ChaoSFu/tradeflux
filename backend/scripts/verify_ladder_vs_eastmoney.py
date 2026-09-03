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

  MISS_STOCK   东财有，而我们**库里根本没有这只股票** → 股票池覆盖缺失
  MISS_RECORD  股票在库里，但那天没有连板记录          → 当日漏记
  EXTRA        我们有、东财没有                        → 多记
  MISMATCH     都有但板数不同                          → 连板链算错

MISS 拆成两种是因为成因和修法完全不同：前者要去补股票池（北交所 920305 连走
5 板我们一条记录都没有），后者是当日采集或连板计算的问题。混在一起报，看到
一个"漏记 N 条"根本不知道该去修哪儿。

## 两个必须排除的伪影

  · **尚未收盘的交易日整天跳过**。东财天梯当天盘中还没发布，会把我们所有记录
    都算成 EXTRA（2026-09-03 首跑就冒出 9 条假 EXTRA）。收没收盘用 bar_is_settled
    判，跟主链路同一套函数。
  · **ST 股要从我们这边剔掉**。东财 filter 里写死 IS_ST="0"，我们不剔就会把
    *ST威领 这类记成 EXTRA——那是口径差异，不是数据错误。
"""
import argparse
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.limit_up_detail_fetcher import fetch_limit_up_ladder
from app.services.eastmoney_fetcher import bar_is_settled, probe_market_now, SH_TZ

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
                     StockDailySnapshot.board_count, Stock.is_st)
            .join(Stock, Stock.id == StockDailySnapshot.stock_id)
            .filter(StockDailySnapshot.date >= since,
                    StockDailySnapshot.board_count >= MIN_BOARD)
            .all()
        )
        # 我们库里有哪些股票——用来区分"池子里没这只票"和"有票但那天没记"
        known = {c for (c,) in db.query(Stock.code).all()}
        ours: dict = defaultdict(dict)
        names: dict = {}
        n_st = 0
        for d, code, name, bc, is_st in rows:
            # 东财 filter 写死 IS_ST="0"，我们不剔 ST 就会凭空多出一批假 EXTRA
            if is_st or "ST" in (name or "").upper():
                n_st += 1
                continue
            ours[d][code] = bc
            names[code] = name
        if n_st:
            print(f"（剔除 {n_st} 条 ST 记录以对齐东财 IS_ST=\"0\" 口径）")
        # 未收盘的当天整天跳过：东财天梯盘中还没发布，会把我们所有记录算成 EXTRA
        market_now = probe_market_now() or datetime.now(SH_TZ)
        days = [d for d in sorted(ours) if bar_is_settled(d, market_now)]
        skipped = [d for d in sorted(ours) if not bar_is_settled(d, market_now)]
        if skipped:
            print(f"跳过尚未收盘的交易日 {skipped}（东财天梯当天未发布，比了必然全是假差异）")
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
            miss_stock = {c: n for c, n in miss.items() if c not in known}
            miss_record = {c: n for c, n in miss.items() if c in known}
            extra = {c: n for c, n in mine.items() if c not in em}
            mism = {c: (mine[c], em[c]) for c in set(mine) & set(em) if mine[c] != em[c]}
            stat["MISS_STOCK"] += len(miss_stock)
            stat["MISS_RECORD"] += len(miss_record)
            stat["EXTRA"] += len(extra)
            stat["MISMATCH"] += len(mism); stat["OK"] += len(set(mine) & set(em)) - len(mism)
            em_h = max(em.values(), default=0)
            my_h = max(mine.values(), default=0)
            per_day.append((d, my_h, em_h, len(mine), len(em), len(miss), len(extra), len(mism)))
            for c, n in sorted(miss_stock.items()):
                details.append((d, c, "—", "MISS_STOCK", f"东财 {n}板，**库里没有这只股票**"))
            for c, n in sorted(miss_record.items()):
                details.append((d, c, names.get(c), "MISS_RECORD", f"东财 {n}板，我们当日无记录"))
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
        print(f"  一致              {stat['OK']:>5}")
        print(f"  股票池缺失 MISS_STOCK  {stat['MISS_STOCK']:>5}   ← 库里没这只票，要补池子")
        print(f"  当日漏记 MISS_RECORD   {stat['MISS_RECORD']:>5}   ← 有票但那天没记")
        print(f"  多记 EXTRA        {stat['EXTRA']:>5}")
        print(f"  板数不符 MISMATCH  {stat['MISMATCH']:>5}   ← 连板链算错")
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
