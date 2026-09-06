"""
生命周期状态的**前瞻收益评估** —— 回答"这套状态到底有没有交易信息增益"。

不改任何规则，只积累证据。在它给出答案之前，争论 MAX_BROKEN_DAYS 是 2 还是 3
都只是换个说法。

## 它回答什么

对每一次**状态转入**（BROKEN→REPAIRING、REPAIRING→CROSS_SUCCESS、…），记录转入
当日收盘之后 T+1/3/5/10 的收益、区间内最大有利/不利偏移（MFE/MAE）、以及是否
再次涨停。然后按事件类型聚合，直接看：

    修复中 到底有没有 Edge？
    第一次转强 是否明显优于长期停留在修复中？
    穿越成功 是确认增强，还是已经太晚？

## 五条纪律，每条都是为了防止这个工具自证

1. **必须有 baseline。** 「修复中 T+5 平均 +3%」单独看毫无意义——同期整个池子
   可能平均 +4%。所以永远同时输出 ALL_STOCK_DAYS 基线，只看**超额**。

2. **窗口不完整就是 None，不进统计。** T+10 还没走完的事件必须排除并计数，
   不能用"目前为止"的收益顶替——那会系统性偏向近期，而近期正好是样本最多的。

3. **T+N 用交易日历数，不是数组下标。** 这个仓库今天为这条栽过三次。

4. **幸存者偏差要喊出来。** 快照是"今天在池子里的股票，当时长什么样"，不是
   历史成分股。当时进不了池的票根本没有行，而它们大概率是走得差的那批。
   **所以这里所有的绝对收益都偏高，只有组间对比和超额有意义。**

5. **不给评分、不给结论。** 只输出分布：样本数、中位数、胜率、MFE/MAE。
   把"有没有 Edge"留给看的人判断——一个自动打分的评估器等于又造一个黑箱。

## 用法

    python scripts/evaluate_lifecycle.py                 # 全部事件
    python scripts/evaluate_lifecycle.py --event REPAIRING
    python scripts/evaluate_lifecycle.py --csv out.csv   # 逐条明细
"""
import argparse
import csv
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.stock import Stock, StockDailySnapshot
from app.services.leader_cycle_state_service import (
    FORMULA_VERSION, replay_price_lifecycle,
)
from app.services.trading_calendar import get_trading_days

HORIZONS = (1, 3, 5, 10)


class Bars:
    """某只股票的日线，按交易日历索引。前瞻窗口全靠它。"""

    def __init__(self, rows, calendar: List[date]):
        self.close = {r.date: r.close_price for r in rows if r.close_price}
        self.high = {r.date: (r.high_price or r.close_price) for r in rows
                     if r.close_price}
        self.low = {r.date: (r.low_price or r.close_price) for r in rows
                    if r.close_price}
        self.lu = {r.date: bool(r.is_limit_up) for r in rows}
        self._cal = calendar
        self._pos = {d: i for i, d in enumerate(calendar)}

    def forward(self, anchor: date, n: int) -> Optional[List[date]]:
        """
        anchor 之后 n 个交易日的日期列表。**窗口不完整返回 None**，不返回残缺的
        前几天——用"目前为止"的收益顶替 T+10，会系统性偏向近期。
        """
        i = self._pos.get(anchor)
        if i is None or i + n >= len(self._cal):
            return None
        days = self._cal[i + 1:i + 1 + n]
        return days if all(d in self.close for d in days) else None


def _ret(a: float, b: float) -> float:
    return (b / a - 1) * 100


def evaluate(db, only_event: Optional[str] = None) -> tuple:
    cal_all = get_trading_days(db, need_through=date.today())
    if not cal_all:
        raise SystemExit("拿不到交易日历。T+N 必须按交易日数，不能用数组下标——"
                         "这个仓库今天为这条栽过三次")

    snaps = defaultdict(list)
    for r in db.query(LeaderCycleSnapshot).all():
        snaps[r.stock_code].append(r)
    if not snaps:
        raise SystemExit("没有快照，先跑 backfill_leader_cycle_snapshots.py")

    dates = sorted({r.date for rows in snaps.values() for r in rows})
    cal = [d for d in cal_all if d <= dates[-1]]

    sid = {s.code: s.id for s in db.query(Stock).all()}
    bars: Dict[str, Bars] = {}
    for code in snaps:
        rows = (db.query(StockDailySnapshot)
                .filter(StockDailySnapshot.stock_id == sid.get(code, -1))
                .order_by(StockDailySnapshot.date).all())
        bars[code] = Bars(rows, cal)

    events, skipped_incomplete = [], 0
    baseline = []
    for code, rows in snaps.items():
        b = bars[code]
        prev_state = None
        for d in dates:
            s = replay_price_lifecycle(rows, d, trading_days=cal)
            cur = s.state
            # 每个"有价格事实的交易日"都进基线池——组间比较必须有同期对照
            if d in b.close:
                baseline.append(("ALL_STOCK_DAYS", code, d, s.entry_reason_codes))
            # **从 UNKNOWN 转出不是转移，是"我们开始有记录了"。** 首版只排除了
            # 转入 UNKNOWN，于是一只票第一次出现可用快照时的 UNKNOWN→BROKEN
            # 被记成一次"转入 BROKEN"，把样本和收益都污染了。
            # NO_CYCLE→STREAKING 保留：那是真事件（第 4 个板刚成立）。
            real = (prev_state is not None and cur != prev_state
                    and cur not in ("UNKNOWN", "NO_CYCLE")
                    and prev_state != "UNKNOWN")
            if real and (only_event is None or cur == only_event):
                events.append((cur, code, d, s.entry_reason_codes))
            prev_state = cur

    # ── 同日同池对照 ──────────────────────────────────────────────────────
    # **逐事件对照，不是两组中位数相减。** 事件集中在特定时段，而基线摊在全部
    # 3662 个股票日上，两组根本不在同一段行情里——那样算出来的"超额"测的是
    # 行情差异，不是状态的信息量。
    # 每个事件用"当天整个池子的同期收益中位数"做对照，市场本身的涨跌被消掉。
    cohort: Dict[date, Dict[int, List[float]]] = defaultdict(lambda: defaultdict(list))
    for _ev, code, d, _c in baseline:
        b = bars[code]
        base_px = b.close.get(d)
        if not base_px:
            continue
        for h in HORIZONS:
            days = b.forward(d, h)
            if days is not None:
                cohort[d][h].append(_ret(base_px, b.close[days[-1]]))

    def _measure(sample):
        """给一组 (事件, 代码, 日期, 原因) 算前瞻指标。"""
        out = defaultdict(list)
        nonlocal skipped_incomplete
        for _ev, code, d, _codes in sample:
            b = bars[code]
            base = b.close.get(d)
            if not base:
                continue
            for h in HORIZONS:
                days = b.forward(d, h)
                if days is None:
                    skipped_incomplete += 1
                    continue          # 窗口没走完，**不进统计**
                r = _ret(base, b.close[days[-1]])
                out[f"ret{h}"].append(r)
                peers = cohort.get(d, {}).get(h) or []
                # 同日至少要有 3 只同类才算得出对照，否则这个"超额"没有意义
                if len(peers) >= 3:
                    out[f"exc{h}"].append(r - st.median(peers))
                out[f"mfe{h}"].append(max(_ret(base, b.high[x]) for x in days))
                out[f"mae{h}"].append(min(_ret(base, b.low[x]) for x in days))
                if h == 5:
                    out["lu5"].append(1.0 if any(b.lu.get(x) for x in days) else 0.0)
        return out

    groups = defaultdict(list)
    for e in events:
        groups[e[0]].append(e)
    groups["ALL_STOCK_DAYS"] = baseline
    return {k: (len(v), _measure(v)) for k, v in groups.items()}, skipped_incomplete, events


def _fmt(vals: List[float]) -> str:
    return f"{st.median(vals):>+6.1f}" if vals else "    —"


def _rate(vals: List[float]) -> str:
    """
    发生比例。**必须用均值,不能用中位数** —— 0/1 列表的中位数只会是 0/0.5/1，
    首版就是这么把「5日再涨停」印成了一列 100%/0%，而基线显示 0% 更是明显荒谬。
    """
    return f"{sum(vals) / len(vals) * 100:>5.0f}%" if vals else "    —"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", help="只看某一种转入，如 REPAIRING")
    ap.add_argument("--csv", help="逐条明细写到这个文件")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        res, skipped, events = evaluate(db, args.event)
        print(f"口径 {FORMULA_VERSION}\n")
        print("⚠️ 幸存者偏差：快照是「今天在池子里的股票，当时长什么样」，不是历史")
        print("   成分股。当时进不了池的票根本没有行，而它们大概率走得更差。")
        print("   **所有绝对收益都偏高，只有组间对比和超额有意义。**\n")

        hdr = (f"{'事件':<18}{'样本':>5}" +
               "".join(f"{'T+' + str(h):>8}" for h in HORIZONS) +
               f"{'MFE5':>8}{'MAE5':>8}{'5日再涨停':>10}")
        print(hdr)
        print("-" * len(hdr.encode('gbk', 'ignore')))
        base = res.get("ALL_STOCK_DAYS", (0, {}))[1]
        order = ["REPAIRING", "CROSS_SUCCESS", "CROSS_WEAKENING", "CROSS_FAILED",
                 "FADED", "BROKEN", "STREAKING", "ALL_STOCK_DAYS"]
        for ev in order:
            if ev not in res:
                continue
            n, m = res[ev]
            row = f"{ev:<18}{n:>5}"
            for h in HORIZONS:
                row += f"{_fmt(m.get(f'ret{h}', [])):>8}"
            row += f"{_fmt(m.get('mfe5', [])):>8}{_fmt(m.get('mae5', [])):>8}"
            row += f"{_rate(m.get('lu5', [])):>10}"
            print(row)

        # 超额：**逐事件对同日同池比较后的中位数**，不是两组中位数相减。
        # 只有这一行能说明"这个状态本身有没有信息"——市场涨跌已经被消掉
        print("\n同日同池超额（逐事件对照后取中位数，单位百分点）：")
        for ev in order[:-1]:
            if ev not in res:
                continue
            _n, m = res[ev]
            parts = []
            for h in HORIZONS:
                a = m.get(f"exc{h}", [])
                parts.append(f"T+{h} {st.median(a):+5.1f}({len(a)})" if a
                             else f"T+{h}   —")
            print(f"  {ev:<18}" + "  ".join(parts))
        print("  括号里是能算出对照的样本数。同日不足 3 只同类就没有对照，不计入。")

        if skipped:
            print(f"\n窗口未走完而排除的 {skipped} 次测量（不用'目前为止'的收益顶替，"
                  "那会系统性偏向近期）")
        print("\n**这里不给结论也不打分**——一个自动判定'有没有 Edge'的评估器，"
              "等于又造一个黑箱。")

        if args.csv:
            with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["event", "code", "date", "entry_reasons"])
                for ev, code, d, codes in events:
                    w.writerow([ev, code, d, "|".join(codes)])
            print(f"\n明细写入 {args.csv}（{len(events)} 条）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
