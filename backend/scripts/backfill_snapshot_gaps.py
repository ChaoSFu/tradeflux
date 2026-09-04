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
    ap.add_argument("--verify-only", action="store_true",
                    help="只跑复权口径校验，不拉数据不写库")
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
        if args.dry_run:
            print("\n--dry-run：未写库")
            return
        if args.verify_only:
            holed = {}          # 跳过补数，直接进校验

        by_code = {st.code: st for st in pool}
        added = 0
        infos = [StockBasicInfo(code=c, name=by_code[c].name,
                                market=market_int(by_code[c].market, c),
                                is_st=by_code[c].is_st, pct_change=0.0,
                                turnover_rate=0.0)
                 for c in holed]
        print(f"\n拉 {len(infos)} 只的 K 线（腾讯优先，前复权）...")
        klines = fetch_klines_batch(infos, days=args.days + 20,
                                    max_workers=6) if infos else {}
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
        still = total_gaps - added
        print(f"新建 {added} 行" + (f"，仍缺 {still} 行（数据源也没有那几天，"
                                     "多半是停牌或未上市）" if still else ""))

        # ── 复权口径校验 ────────────────────────────────────────────────────
        # 补完必须查，不能写完就算。相邻两行的 close 推出的涨幅跟存储的
        # pct_change 对不上，说明中间有复权跳变，两种口径拼在了一条序列上
        print("\n复权口径校验（相邻**交易日**两行 close 推涨幅 vs 存储 pct_change）...")
        # **必须先确认两行是相邻交易日**。首版只按"相邻两行"比，而序列里还有
        # 没补上的空洞，跨着空洞算 close[i]/close[i-1] 得到的是几天的累计涨幅，
        # 当然对不上单日 pct_change ——首测 21 处"疑似复权跳变"里就混着这种。
        #
        # 这跟 2026-09-04 早上在状态机里修的「连续两个 observation 不能靠
        # 过滤后相邻」是同一个错，当天又犯了一次：**时间轴上的相邻关系必须用
        # 日历证明，不能用数组下标推断。**
        pos = {d: i for i, d in enumerate(tdays)}
        suspect, skipped_gap = [], 0
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
                ia, ib = pos.get(a.date), pos.get(b.date)
                if ia is None or ib is None or ib - ia != 1:
                    skipped_gap += 1      # 中间还有空洞，比不了
                    continue
                derived = (b.close_price / a.close_price - 1) * 100
                if abs(derived - b.pct_change) > _PCT_TOLERANCE:
                    # 记下两行各自的来源：如果分歧全都发生在"源不同"的接缝上，
                    # 那才是复权口径混了；发生在同源连续段中间就是别的问题
                    suspect.append((st.code, b.date, round(derived, 2),
                                    round(b.pct_change, 2),
                                    f"{a.volume_source or '?'}→{b.volume_source or '?'}"))
        print(f"（跳过 {skipped_gap} 对：中间仍有空洞，不是相邻交易日，比不了）")
        if suspect:
            seam = sum(1 for x in suspect if x[4].split("→")[0] != x[4].split("→")[1])
            print(f"⚠️ {len(suspect)} 处对不上，其中 {seam} 处发生在两个来源的接缝上：")
            for code, d, derived, stored, src in suspect[:20]:
                print(f"   {code} {d}  close推算 {derived:+.2f}%  存储 {stored:+.2f}%"
                      f"  来源 {src}")
            if len(suspect) > 20:
                print(f"   …还有 {len(suspect) - 20} 处")
            if seam == len(suspect):
                print("   → 全部在接缝上，**确认是复权口径混了**")
            elif seam == 0:
                print("   → 一处都不在接缝上，**不是复权问题**，另查")
            else:
                print("   → 接缝内外都有，两种原因混在一起，逐只看")
            print("   处置：这些股票的窗口内发生过分红除权，dump(未复权) 和"
                  "腾讯(前复权) 的价格不能拼。要么整只重拉成同一口径，要么把"
                  "这几只标注为不可信，**不要当没看见**。")
        else:
            print("✅ 未发现口径跳变")
    finally:
        db.close()


if __name__ == "__main__":
    main()
