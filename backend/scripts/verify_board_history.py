"""
核对 stock_daily_snapshots 里的历史「涨停 / 连板」标记是否可信。**只读，不改数据。**

## 为什么要有这个脚本

破局雷达要用「每日最高连板」画 20 日高度上沿、判定「首次突破」。这条曲线的数据源
是 StockDailySnapshot.board_count，实测能回溯到 2026-06-02（约 65 个交易日）。

问题在于：**盘中价冒充收盘价那个 bug 是 2026-08-26 才修的**，65 天里有 59 天在修
复之前。而这个 bug 的偏向不是随机的——

    盘中封涨停 → is_limit_up=True 写进快照 → 实际收盘炸板 → 连板计数没断，继续累加

也就是说污染方向是**系统性高估最高连板**。拿一条可能被高估的曲线去画上沿、再判定
"突破"，等于把错误直接喂给结论。先证明观测可信，再用它。

## 方法

把库里 board_count >= N 的 (日期, 股票) 全取出来，用 **主链路同一条取数路径**
（fetch_klines_batch → build_kline_bar）重新拉一遍历史，逐日对账。

不自己写涨停判定，也不自己写 market 映射——2026-09-02 刚踩过：补结算手写的
`0 if market == "SH" else 1` 是反的，SH 股被拼成 sz600xxx 全部取不到数。校验脚本
如果也手写一遍，会得出"全市场历史都对不上"这种由脚本自身 bug 造成的假结论。

## 三类分歧要分开，不能混成一个"污染率"

  CONTAMINATED  重拉的收盘价与库里一致，但涨停判定不一致
                → 库里那个 is_limit_up 是盘中写的，真污染
  REPRICED      重拉的收盘价与库里差异明显
                → 腾讯 K 线是 qfq 前复权，期间发生除权会把整段历史重新缩放，
                  绝对价对不上是必然的，不算污染。单独列出，人工确认
  MISSING       重拉不到那一天的 bar（停牌 / 退市 / 超出拉取窗口）
                → 是"不知道"，不是"不一致"。绝不能算进分母，也不能算作通过

## 已知局限（必须写在这里，免得把结论用过头）

1. 只核对**库里认为有 >=N 连板**的那些股票。如果库里**漏记**了某只真实高板股，
   这个脚本查不出来——它只能证伪"高估"，不能证伪"低估"。而已知 bug 的方向是高估，
   所以这一遍先够用。
2. 重算的"真实最高连板"同样只在这个采样集合内取最大值，是**下界**不是精确值。
3. 候选池两个 prompt 都写了「非ST」，所以全链路的市场高度**不含 ST 股**。
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import (
    StockBasicInfo, fetch_klines_batch, market_int,
)

# 收盘价差多少算"被重新缩放过"（除权），而不是"同一天的数对不上"。
# 用相对差：低价股 0.01 元的取整差不该被判成除权。
_REPRICE_TOL = 0.02      # 2%


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=70, help="回溯多少个自然日（默认70）")
    ap.add_argument("--min-board", type=int, default=3, help="只核对连板数>=该值的记录（默认3）")
    ap.add_argument("--workers", type=int, default=5, help="K线并发（默认5，跟主链路一致）")
    ap.add_argument("--limit-stocks", type=int, default=0, help="只抽查前N只（0=全部），用于快速试跑")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        since = date.today() - timedelta(days=args.days)
        rows = (
            db.query(StockDailySnapshot.date, Stock.code, Stock.name, Stock.market,
                     Stock.is_st, StockDailySnapshot.board_count,
                     StockDailySnapshot.close_price, StockDailySnapshot.is_limit_up)
            .join(Stock, Stock.id == StockDailySnapshot.stock_id)
            .filter(StockDailySnapshot.date >= since,
                    StockDailySnapshot.board_count >= args.min_board)
            .order_by(StockDailySnapshot.date)
            .all()
        )
        if not rows:
            print(f"近 {args.days} 天没有 board_count >= {args.min_board} 的记录，无可核对")
            return

        codes = {}
        for _, code, name, market, is_st, *_ in rows:
            codes[code] = StockBasicInfo(code=code, name=name,
                                         market=market_int(market, code),
                                         is_st=bool(is_st), pct_change=0.0, turnover_rate=0.0)
        infos = list(codes.values())
        if args.limit_stocks:
            infos = infos[:args.limit_stocks]
            keep = {i.code for i in infos}
            rows = [r for r in rows if r[1] in keep]

        d0, d1 = rows[0][0], rows[-1][0]
        print(f"待核对：{len(rows)} 条记录 / {len(infos)} 只股票 / {d0} ~ {d1}")
        print(f"重新拉取 K 线（days={args.days + 5}，并发 {args.workers}）…")
        fetched = fetch_klines_batch(infos, days=args.days + 5,
                                     max_workers=args.workers, delay_between=0.1)
        got = sum(1 for c in codes if fetched.get(c))
        print(f"拉到 {got}/{len(infos)} 只\n")

        # (code, date) -> KLineBar
        bar_at = {(c, b.date): b for c, bars in fetched.items() for b in bars}

        # ── 逐条对账 ────────────────────────────────────────────────────────
        verdicts = []      # (date, code, name, kind, detail)
        stat = defaultdict(int)
        for d, code, name, _mkt, _st, bc, db_close, db_lu in rows:
            bar = bar_at.get((code, d))
            if bar is None:
                stat["MISSING"] += 1
                verdicts.append((d, code, name, "MISSING", f"{bc}板，重拉无此日bar"))
                continue
            if db_close and bar.close_price and \
                    abs(bar.close_price - db_close) / db_close > _REPRICE_TOL:
                stat["REPRICED"] += 1
                verdicts.append((d, code, name, "REPRICED",
                                 f"{bc}板，库{db_close:.2f} vs 重拉{bar.close_price:.2f}"))
                continue
            if bool(db_lu) != bool(bar.is_limit_up):
                stat["CONTAMINATED"] += 1
                verdicts.append((d, code, name, "CONTAMINATED",
                                 f"{bc}板，库记涨停={bool(db_lu)}，"
                                 f"重拉涨停={bar.is_limit_up} 收{bar.close_price:.2f} "
                                 f"({bar.pct_change:+.2f}%)"
                                 + ("，炸板" if bar.is_broken_board else "")))
                continue
            stat["OK"] += 1

        # ── 用重拉的 bar 重算连板序列，得到"真实最高连板"的下界 ──────────────
        true_streak = {}   # (code, date) -> 连续涨停数
        for code, bars in fetched.items():
            bars = sorted(bars, key=lambda b: b.date)
            run = 0
            for b in bars:
                run = run + 1 if b.is_limit_up else 0
                true_streak[(code, b.date)] = run

        # 显式解包，不用 *_rest 按下标取——第一版就是这么写的，_rest[2] 取到的是
        # is_st 而不是 board_count，于是"库里最高板"整列显示 0
        db_max, re_max = defaultdict(int), defaultdict(int)
        for d, code, _name, _mkt, _st, bc, _close, _lu in rows:
            db_max[d] = max(db_max[d], bc or 0)
            re_max[d] = max(re_max[d], true_streak.get((code, d), 0))

        print("─" * 64)
        print(f"  {'日期':<12}{'库里最高板':>10}{'重算最高板':>12}{'差':>6}")
        print("─" * 64)
        gap_days = 0
        for d in sorted(db_max):
            gap = re_max[d] - db_max[d]
            if gap:
                gap_days += 1
            print(f"  {str(d):<12}{db_max[d]:>10}{re_max[d]:>12}{gap:>+6}"
                  + ("   ← 不一致" if gap else ""))
        print("─" * 64)

        total = sum(stat.values())
        checkable = stat["OK"] + stat["CONTAMINATED"]
        print(f"\n对账结果（共 {total} 条）")
        print(f"  一致            {stat['OK']:>5}")
        print(f"  污染(涨停判定不符) {stat['CONTAMINATED']:>5}")
        print(f"  除权嫌疑(价被缩放) {stat['REPRICED']:>5}   ← 不计入污染率，需人工确认")
        print(f"  重拉不到         {stat['MISSING']:>5}   ← 是「不知道」，不计入分母")
        if checkable:
            print(f"\n  污染率 = {stat['CONTAMINATED']}/{checkable} "
                  f"= {stat['CONTAMINATED'] / checkable * 100:.1f}%（分母只含真正可比的）")
        print(f"  最高板对不上的交易日：{gap_days}/{len(db_max)}")

        bad = [v for v in verdicts if v[3] != "OK"]
        if bad:
            print(f"\n明细（{len(bad)} 条）")
            for d, code, name, kind, detail in bad:
                print(f"  {d}  {code} {name or '':<8} [{kind}] {detail}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
