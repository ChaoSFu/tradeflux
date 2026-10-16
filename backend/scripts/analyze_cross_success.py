"""
穿越成功前后 —— 日线级别的分歧时长与走势分类。

两份报告，共用同一份数据加载和状态重放：

    # 默认：从「进入穿越成功」往后看——成功之后分歧几天、最后走成哪种
    cd backend && .venv/bin/python -m scripts.analyze_cross_success

    # --from-break：从「断板」往后看——最终成功和没成功的两批，断板后的分歧差在哪
    cd backend && .venv/bin/python -m scripts.analyze_cross_success --from-break

    --min-peak N   只看本轮连板 >= N 的周期（0=不限）
    --horizon H    （--from-break）断板后多少个交易日内走到穿越成功才算"成功"，默认 20

## 默认报告的口径

事件 = **一次「进入穿越成功」**。同一段周期里反复 成功→走弱→成功，每次重新进入
都算一次——每一次都是一个真实的交易决策点。

「分歧」= 进入成功之后，**第一段「成功后走弱」持续的交易日数**。状态机里
CROSS_WEAKENING 的入口就是「单日跌破 MA10」或「连续两日收在 MA5 下」，那正是
日线级别"结构开始松动"的那一段，不另造一个分歧定义。

走势按**归宿**分四类。不是拍的——状态机里穿越成功**只有这几条出路**：

    分歧后转强       走弱 → 回到穿越成功
    分歧后结束       走弱 → 周期结束（没再回来）
    直接结束         成功 → 周期结束，中间没经过走弱
    开启新一轮连板   这段周期还没结束，又走出新的 ≥4 连板（最强的那种归宿）

## --from-break 的口径

单位 = **一段周期的断板**。按断板后 H 个交易日内有没有走到穿越成功分两组：

    成功     H 日内进入过穿越成功
    不成功   H 日内没进入过：要么已经周期结束，要么观察满了 H 天
    未定     观察不满 H 天、也还没结局——**单独列，不进统计**

断板后的分歧期量三样（D+ 都取快照里的 days_since_break，跟界面同一个数）：

    首次修复   断板后第几个交易日首次进入「修复中」
    见底       断板后前 10 个交易日里，最低收盘出现在 D+几
    回调深度   那个最低收盘相对周期峰值价跌了多少

**「从未修复」那档的成功率必然是 0**：状态机里穿越成功必须先经过修复中。那是
规则推出来的，不是数据里的发现，别拿它当结论。

## 两份报告都要知道的偏差

1. **幸存者偏差**：快照只有「当时在池子里的票」，当时进不了池的根本没有行。
   收益偏高，比例也只在"今天的幸存者"这个宇宙里成立。
2. **同一只票贡献多次事件**，窗口还高度重叠，有效样本远小于打印的 N。
3. 状态判不出来的那天（未结算 / 陈值）退回最近一次有效状态，跟界面同口径；
   这类天不计入分歧天数，因为那天没有新事实。
4. **数据末尾还没走完的单独列**（右删失）。把"还没发生"当成"不会发生"，统计
   就是假的。
"""
import argparse
import os
import statistics
import sys
from collections import Counter, defaultdict
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal                                  # noqa: E402
from app.models.leader_cycle import LeaderCycleSnapshot                 # noqa: E402
from app.services.leader_cycle_state_service import (                   # noqa: E402
    CROSS_FAILED, CROSS_SUCCESS, CROSS_WEAKENING, FADED, REPAIRING, replay_series,
)
from app.services.suspension_service import (                           # noqa: E402
    load_suspensions_by_code, stock_calendar,
)
from app.services.trading_calendar import get_trading_days              # noqa: E402

# 成功之后的分歧天数分档。1~2 天是"一次正常分歧"，5 天以上基本就是另一回事了
GAP_BUCKETS = ((1, "1天"), (2, "2天"), (3, "3天"), (5, "4-5天"), (10, "6-10天"),
               (10 ** 6, "10天以上"))
# 断板后第几天的分档（D+0 是断板当天，修复最早 D+1）
DPLUS_BUCKETS = ((1, "D+1"), (2, "D+2"), (3, "D+3"), (5, "D+4~5"), (10, "D+6~10"),
                 (10 ** 6, "D+11以后"))
EARLY_WINDOW = 10            # 见底 / 回调深度只看断板后前 10 个交易日——两组才可比


def _bucket(n: int, buckets) -> str:
    for hi, label in buckets:
        if n <= hi:
            return label
    return buckets[-1][1]


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if not a or not b or a <= 0 else (b / a - 1) * 100


def _med(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.1f}%"


# ── 共用：加载快照 + 逐只重放 ──────────────────────────────────────────────

def _timelines(db) -> tuple:
    """
    每只票一条时间线：{code, days, st, row}。**两份报告共用这一份**——同一批事实
    重放出来的状态不能有两套。

    st[d] 是展示状态：判不出来（UNKNOWN）的那天退回最近一次有效状态，跟界面同口径。
    """
    rows = db.query(LeaderCycleSnapshot).order_by(LeaderCycleSnapshot.date).all()
    if not rows:
        return None, []
    by_code = defaultdict(list)
    for r in rows:
        by_code[r.stock_code].append(r)
    cal = get_trading_days(db) or []
    susp = load_suspensions_by_code(db, list(by_code), since=cal[0] if cal else None)
    out = []
    for code, srows in by_code.items():
        days = sorted({r.date for r in srows})
        # 按这只票自己的交易日走（停牌日不算），跟龙头周期页面同一个口径
        series = replay_series(srows, days,
                               trading_days=stock_calendar(cal, susp.get(code)))
        st = {d: (series[d].state if series[d].state != "UNKNOWN"
                  else series[d].last_valid_state) for d in days}
        out.append({"code": code, "days": days, "st": st,
                    "row": {r.date: r for r in srows}})
    all_days = sorted({r.date for r in rows})
    return (len(rows), len(by_code), all_days), out


# ── 报告一：进入穿越成功之后 ─────────────────────────────────────────────

def _follow(fwd, st, close, cycle, d0) -> dict:
    """
    从进入成功那天往后走，直到这段周期有结论。

    **一旦 cycle_start_date 变了就停**：那是新的一轮，上一轮的结论不能跨周期延续。
    """
    base, c0 = close.get(d0), cycle.get(d0)
    success_days = 0          # 在成功里待了几天才松动
    gap_days = None           # 第一段走弱持续几天
    kind, last = None, st.get(d0)
    phase = "success"
    new_cycle = False
    for d in fwd[1:]:
        if cycle.get(d) != c0:            # 新周期，这段到此为止
            new_cycle = True
            break
        s = st.get(d)
        last = s
        if phase == "success":
            if s == CROSS_SUCCESS:
                success_days += 1
                continue
            if s == CROSS_WEAKENING:
                phase, gap_days = "weak", 1
                continue
            if s in (FADED, CROSS_FAILED):
                kind = "直接结束"
                break
            continue                       # 其余状态不改变阶段
        if phase == "weak":
            if s == CROSS_WEAKENING:
                gap_days += 1
                continue
            if s == CROSS_SUCCESS:
                kind = "分歧后转强"
                break
            if s in (FADED, CROSS_FAILED):
                kind = "分歧后结束"
                break
    else:
        # 走到数据末尾都没有结论（还在成功里，或还在走弱里）——右删失，单独列
        kind = "未走完"
    if new_cycle:
        # 这段还没结束就又走出新一轮 ≥4 连板。有没有经过走弱都记在 gap_days 里
        kind = "开启新一轮连板"

    def _ret(n: int) -> Optional[float]:
        days = [d for d in fwd[1:] if cycle.get(d) == c0][:n]
        return _pct(base, close.get(days[-1])) if len(days) == n else None

    return {"kind": kind, "gap_days": gap_days, "success_days": success_days,
            "r5": _ret(5), "r10": _ret(10), "last_state": last}


def report_after_success(lines, min_peak: int) -> None:
    events, censored = [], []
    for t in lines:
        days, st, row = t["days"], t["st"], t["row"]
        close = {d: row[d].latest_close for d in days}
        cycle = {d: row[d].cycle_start_date for d in days}
        prev = None
        for i, d in enumerate(days):
            cur = st[d]
            if cur == CROSS_SUCCESS and prev != CROSS_SUCCESS \
                    and (row[d].peak_board_count or 0) >= min_peak:
                ev = _follow(days[i:], st, close, cycle, d)
                ev.update(code=t["code"], date=d, peak=row[d].peak_board_count or 0)
                (censored if ev["kind"] == "未走完" else events).append(ev)
            prev = cur

    if not events and not censored:
        print("没有「进入穿越成功」的事件"); return
    print(f"\n事件 {len(events) + len(censored)} 次"
          f"（{len({e['code'] for e in events + censored})} 只票）："
          f"走完 {len(events)} 次，数据末尾还没走完 {len(censored)} 次（不进下面的统计）")

    print("\n走势分类（只统计走完的）：")
    print(f"  {'类型':<14}{'次数':>5}{'占比':>7}{'分歧天数中位':>14}{'5日':>8}{'10日':>8}")
    for kind, n in Counter(e["kind"] for e in events).most_common():
        es = [e for e in events if e["kind"] == kind]
        gap = _med([e["gap_days"] for e in es])
        print(f"  {kind:<14}{n:>5}{n / len(events) * 100:>6.0f}%"
              f"{('—' if gap is None else f'{gap:.1f}天'):>14}"
              f"{_fmt(_med([e['r5'] for e in es])):>8}{_fmt(_med([e['r10'] for e in es])):>8}")

    gaps = [e["gap_days"] for e in events if e["gap_days"]]
    if gaps:
        print(f"\n出现过分歧的 {len(gaps)} 次，分歧持续几个交易日：")
        _histogram(Counter(_bucket(g, GAP_BUCKETS) for g in gaps), GAP_BUCKETS, len(gaps))
        print(f"  中位 {statistics.median(gaps):.1f} 天 / 均值 {statistics.mean(gaps):.1f} 天 / "
              f"最长 {max(gaps)} 天")
        back = [e["gap_days"] for e in events if e["gap_days"] and e["kind"] == "分歧后转强"]
        if back:
            print(f"  其中 {len(back)} 次分歧后重新转强，这些的分歧中位 "
                  f"{statistics.median(back):.1f} 天")

    hold = [e["success_days"] for e in events if e["success_days"] is not None]
    if hold:
        print(f"\n进入穿越成功后，在成功状态里待了几个交易日才发生变化："
              f"中位 {statistics.median(hold):.1f} 天，均值 {statistics.mean(hold):.1f} 天")

    if censored:
        print(f"\n还没走完的 {len(censored)} 次（数据末尾仍在进行）：")
        for e in sorted(censored, key=lambda x: x["date"])[-8:]:
            print(f"  {e['date']} {e['code']} 本轮{e['peak']}板，当前仍是 {e['last_state']}")


# ── 报告二：从断板往后，成功组 vs 不成功组 ─────────────────────────────────

def _break_cycles(t, horizon: int) -> List[dict]:
    """
    一只票每段周期的断板 → H 日内的结局，以及断板后的分歧期三个量。

    D+ 一律取快照里的 days_since_break——跟界面那一列同一个数，而它本来就按
    这只票自己的交易日算（停牌日不算）。
    """
    days, st, row = t["days"], t["st"], t["row"]
    seg = defaultdict(list)
    for d in days:
        if row[d].cycle_start_date is not None:
            seg[row[d].cycle_start_date].append(d)

    out = []
    for c, ds in seg.items():
        after = [d for d in ds if row[d].break_date is not None
                 and row[d].days_since_break is not None]
        if not after:
            continue                       # 这段还在连板中，没断过
        dsb = {d: row[d].days_since_break for d in after}
        if min(dsb.values()) > 0:
            # **看不到断板那天**：周期在快照历史开始之前就断了，第一次出现时已经是
            # D+几十。断板后头几天的修复、见底我们根本没看到，那几个量都是残缺的；
            # 而它按"看满 H 天没成功"又会被算成不成功——两头都错。单独计数，不进统计
            out.append({"code": t["code"], "peak": row[after[0]].peak_board_count or 0,
                        "outcome": "看不到断板日"})
            continue
        inside = [d for d in after if dsb[d] <= horizon]

        def first(state) -> Optional[int]:
            return next((dsb[d] for d in inside if st.get(d) == state), None)

        succ_d, repair_d = first(CROSS_SUCCESS), first(REPAIRING)
        if succ_d is not None:
            outcome = "成功"
        elif any(st.get(d) == FADED for d in inside) or max(dsb.values()) >= horizon:
            # 周期结束是 terminal；或者已经看满了 H 天还没成功——两种都是确定的"没成"
            outcome = "不成功"
        else:
            outcome = "未定"

        early = [d for d in after if 1 <= dsb[d] <= EARLY_WINDOW and row[d].latest_close]
        low_d = dd = None
        if early:
            lo = min(early, key=lambda d: row[d].latest_close)
            low_d = dsb[lo]
            dd = _pct(row[after[0]].peak_price, row[lo].latest_close)
        out.append({"code": t["code"], "break": row[after[0]].break_date,
                    "peak": row[after[0]].peak_board_count or 0, "outcome": outcome,
                    "repair_d": repair_d, "success_d": succ_d,
                    "low_d": low_d, "drawdown": dd})
    return out


def report_from_break(lines, min_peak: int, horizon: int) -> None:
    evs = [e for t in lines for e in _break_cycles(t, horizon) if e["peak"] >= min_peak]
    if not evs:
        print("没有断板事件"); return
    ok = [e for e in evs if e["outcome"] == "成功"]
    bad = [e for e in evs if e["outcome"] == "不成功"]
    pend = [e for e in evs if e["outcome"] == "未定"]
    blind = [e for e in evs if e["outcome"] == "看不到断板日"]
    print(f"\n断板 {len(evs)} 段周期（{len({e['code'] for e in evs})} 只票）："
          f"{horizon} 日内穿越成功 {len(ok)} / 不成功 {len(bad)} / "
          f"未定 {len(pend)}（观察不满 {horizon} 天）/ "
          f"看不到断板日 {len(blind)}（快照开始前就断了）——后两类不进统计")
    if not ok or not bad:
        print("成功组或不成功组是空的，没法对照"); return
    base = len(ok) / (len(ok) + len(bad)) * 100
    print(f"基准成功率 {base:.0f}%")

    never = sum(1 for e in bad if e["repair_d"] is None)
    print(f"\n成功组 vs 不成功组（断板后，中位数）：")
    print(f"  {'':<24}{'成功(' + str(len(ok)) + ')':>12}{'不成功(' + str(len(bad)) + ')':>14}")
    _row("首次修复在 D+几", ok, bad, "repair_d", "")
    print(f"  {'':<24}{'':>12}{f'（另有 {never} 段从未修复）':>14}")
    _row(f"前{EARLY_WINDOW}日见底在 D+几", ok, bad, "low_d", "")
    _row(f"前{EARLY_WINDOW}日回调深度(相对峰值)", ok, bad, "drawdown", "%")
    s = _med([e["success_d"] for e in ok])
    print(f"  {'断板→穿越成功 D+几':<24}{('—' if s is None else f'{s:.1f}'):>12}{'—':>14}")

    print(f"\n按「断板后第几天首次修复」分档，{horizon} 日内穿越成功的比例"
          f"（基准 {base:.0f}%）：")
    _cond_table(ok + bad, "repair_d", DPLUS_BUCKETS,
                none_label="从未修复",
                none_note="（规则推出来的：穿越成功必须先经过修复中，这档必然是 0）")

    print(f"\n按「断板后前{EARLY_WINDOW}日最低收盘在 D+几」分档，成功率：")
    _cond_table(ok + bad, "low_d", DPLUS_BUCKETS[:-1], none_label="没有数据")


def _row(label, ok, bad, key, unit) -> None:
    a, b = _med([e[key] for e in ok]), _med([e[key] for e in bad])
    f = (lambda v: "—" if v is None else f"{v:+.1f}%") if unit == "%" else \
        (lambda v: "—" if v is None else f"{v:.1f}")
    print(f"  {label:<24}{f(a):>12}{f(b):>14}")


def _cond_table(evs, key, buckets, none_label, none_note="") -> None:
    groups = defaultdict(list)
    for e in evs:
        v = e[key]
        groups[none_label if v is None else _bucket(v, buckets)].append(e)
    for label in [b for _, b in buckets] + [none_label]:
        g = groups.get(label)
        if not g:
            continue
        k = sum(1 for e in g if e["outcome"] == "成功")
        rate = k / len(g) * 100
        note = none_note if label == none_label else ""
        print(f"  {label:<10}{len(g):>4} 段  成功 {k:>3}  {rate:>4.0f}%  "
              + "█" * round(rate / 5) + note)


def _histogram(dist: Counter, buckets, total: int) -> None:
    for _, label in buckets:
        if dist.get(label):
            n = dist[label]
            print(f"  {label:<10}{n:>4} 次 {n / total * 100:>5.0f}%  "
                  + "█" * round(n / total * 40))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-break", action="store_true",
                    help="从断板往后看：成功组 vs 不成功组的分歧期差异")
    ap.add_argument("--min-peak", type=int, default=0,
                    help="只看本轮连板 >= 这个数的周期（0=不限）")
    ap.add_argument("--horizon", type=int, default=20,
                    help="--from-break 时，断板后多少个交易日内走到穿越成功才算成功")
    args = ap.parse_args()

    meta, lines = _timelines(SessionLocal())
    if meta is None:
        print("没有龙头周期快照"); return
    n_rows, n_codes, all_days = meta
    print(f"快照 {n_rows:,} 行 / {n_codes} 只票 / {all_days[0]} ~ {all_days[-1]}"
          f"（{len(all_days)} 个交易日）")

    if args.from_break:
        report_from_break(lines, args.min_peak, args.horizon)
    else:
        report_after_success(lines, args.min_peak)

    print("\n⚠️ 幸存者偏差：快照只有「当时在池子里的票」，当时进不了池的根本没有行，"
          "\n   所以收益偏高、比例也只在「今天的幸存者」这个宇宙里成立。"
          "\n   同一只票贡献多次事件、窗口高度重叠，有效样本远小于打印的 N。"
          "\n   这里不给结论也不打分。")


if __name__ == "__main__":
    main()
