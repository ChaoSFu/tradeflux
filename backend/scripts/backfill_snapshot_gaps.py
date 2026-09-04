"""
一次性补齐强势池股票的 StockDailySnapshot 空洞 —— 治 RS 和连板可信度的根因。

## 问题

2026-09-04 实测：61 只强势池里 14 只算不出 RS_market_20，17 只 peak_board_confident
为 False。查下来是同一个根因——**它们在某些交易日压根没有快照行**。

RS 的锚点由指数交易日定，20 日窗口的锚点是 2026-08-07；那 14 只全都在 08-07 没有
bar，于是 `_interval_return` 返回 None（**不用邻近日期近似**，那会把两段不同区间
的收益相减，而且不会报错）。连板可信度同理：计数循环分不清"那天没涨停"和"那天
我们没记录"。

注意这不是"历史行数少"：82 行的票和 125 行的票都在缺失名单里，中位数只差 9。
**RS 要的是那一个特定日子有没有数据，聚合统计量回答不了逐点的问题。**

## 为什么现有的两条路都不行

  _backfill_history_from_dump   dump 是 daily-k-10d，只有最近 10 个交易日，
                                够不着 28 天前。它保证的是**往后**不再出现
                                >10 天的缺口（2026-08-27 上线），补不了存量
  seed_kline_history.py         只给已有行填 close_price，**不新建行**，
                                而我们缺的正是整行

## 复权口径：这个脚本最需要小心的地方

dump 未复权，腾讯前复权。补进来的行若是腾讯口径，而相邻已有行是 dump 口径，
那么窗口内只要发生过分红除权，这条序列就是两种价格拼起来的——均线、RS、连板
判定全会错，**而且不会报错**。

所以补完必须校验，不能写完就算：对每只股票检查相邻两行的
`close[i] / close[i-1] - 1` 跟存储的 `pct_change` 对不对得上。对不上说明中间有
复权跳变，如实报出来交给人判断，**不静默写入**。

## 三条约束（跟 _backfill_history_from_dump 同一套）

1. **绝不覆盖已有行**。已有行带着选股 API 的权威涨跌停标记，推算出来的不该盖掉它。
2. **只写 K 线原始字段**（OHLC / 收盘 / 涨跌幅 / 涨跌停标志 / 量额），不写连板数、
   涨停天数、评分、阶段——那些要完整窗口才算得准，这里没有。
3. 历史交易日 `is_settled=True`；**当日那一行不碰**，永远归主流程写。
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import (
    StockBasicInfo, fetch_klines_batch, market_int,
)
from app.services.trading_calendar import get_trading_days

# 相邻两日 close 推出的涨幅 与 存储 pct_change 差多少算"对不上"（百分点）。
# 0.5 是给浮点和四舍五入留的余量；真正的复权跳变通常差几个到几十个点
_PCT_TOLERANCE = 0.5


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=70, help="往回补多少个交易日")
    ap.add_argument("--dry-run", action="store_true", help="只报缺多少，不写库")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        pool = db.query(Stock).filter(Stock.in_strong_pool.is_(True)).all()
        if not pool:
            print("强势池为空")
            return
        tdays = get_trading_days(db, need_through=date.today())
        today = date.today()
        window = [d for d in tdays if d < today][-args.days:]
        if not window:
            print("拿不到交易日历")
            return
        lo, hi = window[0], window[-1]
        print(f"强势池 {len(pool)} 只，窗口 {lo} ~ {hi}（{len(window)} 个交易日）")

        have = {}
        for sid, d in (db.query(StockDailySnapshot.stock_id, StockDailySnapshot.date)
                       .filter(StockDailySnapshot.stock_id.in_([s.id for s in pool]),
                               StockDailySnapshot.date >= lo,
                               StockDailySnapshot.date <= hi).all()):
            have.setdefault(sid, set()).add(d)

        need = {st.code: sorted(set(window) - have.get(st.id, set())) for st in pool}
        total_gaps = sum(len(v) for v in need.values())
        holed = {c: v for c, v in need.items() if v}
        print(f"缺 {total_gaps} 行，涉及 {len(holed)} 只")
        for c, v in sorted(holed.items(), key=lambda x: -len(x[1]))[:10]:
            print(f"  {c}  缺 {len(v)} 天：{v[0]} ~ {v[-1]}")
        if args.dry_run or not holed:
            print("\n--dry-run：未写库" if args.dry_run else "无需操作")
            return

        by_code = {st.code: st for st in pool}
        infos = [StockBasicInfo(code=c, name=by_code[c].name,
                                market=market_int(by_code[c].market, c),
                                is_st=by_code[c].is_st, pct_change=0.0,
                                turnover_rate=0.0)
                 for c in holed]
        print(f"\n拉 {len(infos)} 只的 K 线（腾讯优先，前复权）...")
        klines = fetch_klines_batch(infos, days=args.days + 20, max_workers=6)

        added = 0
        for code, gaps in holed.items():
            st = by_code[code]
            bars = {b.date: b for b in (klines.get(code) or [])}
            for d in gaps:
                b = bars.get(d)
                if b is None or not b.close_price or b.close_price <= 0:
                    continue
                db.add(StockDailySnapshot(
                    stock_id=st.id, date=d, is_settled=True,
                    open_price=b.open_price, high_price=b.high_price,
                    low_price=b.low_price, close_price=b.close_price,
                    pct_change=b.pct_change,
                    is_limit_up=b.is_limit_up, is_limit_down=b.is_limit_down,
                    is_broken_board=b.is_broken_board,
                    volume=b.volume, amount=b.amount,
                    volume_source=b.volume_source,
                ))
                added += 1
            db.flush()
        db.commit()
        print(f"新建 {added} 行")

        # ── 复权口径校验 ────────────────────────────────────────────────────
        # 补完必须查，不能写完就算。相邻两行的 close 推出的涨幅跟存储的
        # pct_change 对不上，说明中间有复权跳变，两种口径拼在了一条序列上
        print("\n复权口径校验（相邻两行 close 推涨幅 vs 存储 pct_change）...")
        suspect = []
        for st in pool:
            rows = (db.query(StockDailySnapshot)
                    .filter(StockDailySnapshot.stock_id == st.id,
                            StockDailySnapshot.date >= lo,
                            StockDailySnapshot.date <= hi,
                            StockDailySnapshot.close_price.isnot(None))
                    .order_by(StockDailySnapshot.date).all())
            for a, b in zip(rows, rows[1:]):
                if not a.close_price or a.close_price <= 0 or b.pct_change is None:
                    continue
                derived = (b.close_price / a.close_price - 1) * 100
                if abs(derived - b.pct_change) > _PCT_TOLERANCE:
                    suspect.append((st.code, b.date, round(derived, 2),
                                    round(b.pct_change, 2)))
        if suspect:
            print(f"⚠️ {len(suspect)} 处对不上——**很可能是复权口径混了**，"
                  "这条序列上的均线/RS/连板判定都不可信：")
            for code, d, derived, stored in suspect[:20]:
                print(f"   {code} {d}  close推算 {derived:+.2f}%  存储 {stored:+.2f}%")
            if len(suspect) > 20:
                print(f"   …还有 {len(suspect) - 20} 处")
            print("   处置：这些股票的窗口内发生过分红除权，dump(未复权) 和"
                  "腾讯(前复权) 的价格不能拼。要么整只重拉成同一口径，要么把"
                  "这几只标注为不可信，**不要当没看见**。")
        else:
            print("✅ 未发现口径跳变")
    finally:
        db.close()


if __name__ == "__main__":
    main()
