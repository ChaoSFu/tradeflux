"""
修停牌日被写入的假快照，并重算受影响股票的连板数。**默认只读，--apply 才动手。**

## 病灶

停牌那几天，快照里被写进了一行"当天的"数据，收盘价和涨跌幅原样顺延前一交易日：

    07-31 | board=9 | 涨停  | 22.54 | 10.00     ← 603221 爱丽家居，真实 9 板
    08-03 | board=9 | 非涨停| 22.54 | 10.00     ← 停牌，价格原样顺延
    08-04 | board=0 | 非涨停| 22.54 | 10.00
    08-05 | board=0 | 非涨停| 22.54 | 10.00
    08-06 | board=1 | 涨停  | 24.79 |  9.98     ← 复牌涨停，却记成 1 板（真实 10 板）

这些假行是 `is_limit_up=False`，而主链路优先走 **DB 重建**（从快照重建 K 线序列
再算指标），于是连板链断在停牌日，复牌后从 1 重新算。东财连板天梯 08-06 显示
爱丽家居 10 板，我们系统那天的市场最高只有 5 板——整段行情的最高点整个漏掉。

根子上还是同一类错：**停牌是"那天没交易"，不是"那天没涨停"。** 写一个
is_limit_up=False 进去，等于用一个值断言了我们并不知道的事。

## 影响面不止破局雷达

board_count 还喂着强势池打分、龙头分、涨停板块雷达的 board_height。

## 已经不再新增

按"收盘价与涨跌幅同时跟前一交易日完全相同"这个指纹全库扫描，最后一次发生是
2026-08-24；08-26 的 bar_is_settled + require_date + 补结算修复之后 8 个交易日
一次都没有。所以这是历史修复，不是止血。

## 为什么不能只靠指纹就删

指纹只是**嫌疑**。正常交易日两天收盘价和涨跌幅完全相同虽然罕见但不是不可能。
所以每一条都要向腾讯确认"那天确实没有 bar"（停牌的硬证据）才删——跟
verify_board_history 一个规矩：分不清故障和事实，就不要动数据。

## 删除而不是置空

置空等于说"这天它存在但我们不知道它涨没涨"，而事实是"这天它根本没交易"。
没有行才是对停牌最诚实的表达。
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
    market_int, get_limit_pct, _fetch_kline_tencent,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-06-01", help="从哪天开始扫（默认2026-06-01）")
    ap.add_argument("--apply", action="store_true", help="真正执行；不加则只报告")
    args = ap.parse_args()
    since = date.fromisoformat(args.since)

    db = SessionLocal()
    try:
        rows = (
            db.query(StockDailySnapshot, Stock.code, Stock.name, Stock.market, Stock.is_st)
            .join(Stock, Stock.id == StockDailySnapshot.stock_id)
            .filter(StockDailySnapshot.date >= since)
            .order_by(Stock.code, StockDailySnapshot.date)
            .all()
        )
        by_code: dict[str, list] = defaultdict(list)
        meta: dict[str, tuple] = {}
        for snap, code, name, market, is_st in rows:
            by_code[code].append(snap)
            meta[code] = (name, market, is_st)

        # ── 1. 按指纹找嫌疑：收盘价与涨跌幅同时跟前一交易日完全相同 ──────────
        suspects: dict[str, list] = defaultdict(list)
        for code, snaps in by_code.items():
            prev = None
            for s in snaps:
                if (prev is not None and s.close_price is not None
                        and s.close_price == prev.close_price
                        and s.pct_change == prev.pct_change
                        and (s.pct_change or 0) != 0):
                    suspects[code].append(s)
                prev = s
        if not suspects:
            print(f"{since} 起没有发现停牌顺延嫌疑行")
            return
        n_suspect = sum(len(v) for v in suspects.values())
        print(f"指纹命中 {n_suspect} 行 / {len(suspects)} 只股票，逐一向腾讯确认是否真停牌…\n")

        # ── 2. 向腾讯确认：那天真的没有 bar 才算停牌 ─────────────────────────
        confirmed: list = []      # (code, name, snap)
        rejected: list = []
        recompute: dict[str, dict] = {}    # code -> {date: KLineBar}
        for code, snaps in sorted(suspects.items()):
            name, market, is_st = meta[code]
            try:
                bars = _fetch_kline_tencent(code, market_int(market, code), 200,
                                            bool(is_st), get_limit_pct(code, bool(is_st)), 20)
            except Exception as e:  # noqa: BLE001
                print(f"  {code} {name}: 腾讯拉取失败（{type(e).__name__}），跳过不动")
                continue
            if not bars:
                print(f"  {code} {name}: 腾讯无数据，跳过不动")
                continue
            recompute[code] = {b.date: b for b in bars}
            have = recompute[code]
            for s in snaps:
                if s.date in have:
                    rejected.append((code, name, s, have[s.date]))
                else:
                    confirmed.append((code, name, s))

        print(f"\n确认停牌（腾讯无 bar）：{len(confirmed)} 行 ← 待删除")
        for code, name, s in confirmed:
            print(f"    {s.date}  {code} {name}  收{s.close_price} {s.pct_change:+.2f}% "
                  f"board={s.board_count}")
        if rejected:
            print(f"\n指纹命中但腾讯有 bar：{len(rejected)} 行 ← **不动**，可能是真行情")
            for code, name, s, b in rejected:
                print(f"    {s.date}  {code} {name}  库{s.close_price} vs 腾讯{b.close_price}")

        # ── 3. 删掉假行之后，重算这些股票的连板数 ────────────────────────────
        # 连板从腾讯的真实 bar 序列重算：停牌那几天本来就没有 bar，不会打断连板，
        # 这正是"复牌后应该是 10 板而不是 1 板"的依据
        del_dates = defaultdict(set)
        for code, _n, s in confirmed:
            del_dates[code].add(s.date)
        fixes: list = []    # (code, name, snap, old, new)
        for code in del_dates:
            bars = sorted(recompute[code].values(), key=lambda b: b.date)
            streak, run = {}, 0
            for b in bars:
                run = run + 1 if b.is_limit_up else 0
                streak[b.date] = run
            for s in by_code[code]:
                if s.date in del_dates[code]:
                    continue
                new = streak.get(s.date)
                if new is not None and new != (s.board_count or 0):
                    fixes.append((code, meta[code][0], s, s.board_count, new))

        print(f"\n连板数需要重算：{len(fixes)} 行")
        for code, name, s, old, new in fixes:
            mark = "  ← 关键" if new - (old or 0) >= 3 else ""
            print(f"    {s.date}  {code} {name}  {old} → {new}{mark}")

        if not args.apply:
            print(f"\n（试运行，未改动。加 --apply 执行：删 {len(confirmed)} 行、"
                  f"改 {len(fixes)} 行连板数）")
            return

        for _c, _n, s in confirmed:
            db.delete(s)
        for _c, _n, s, _o, new in fixes:
            s.board_count = new
        db.commit()
        print(f"\n✅ 已删除 {len(confirmed)} 行停牌假快照，重算 {len(fixes)} 行连板数")
    finally:
        db.close()


if __name__ == "__main__":
    main()
