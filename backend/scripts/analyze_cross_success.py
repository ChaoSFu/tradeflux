"""
穿越成功之后会怎么走 —— 日线级别的分歧时长与走势分类。

    cd backend && .venv/bin/python -m scripts.analyze_cross_success
    cd backend && .venv/bin/python -m scripts.analyze_cross_success --min-peak 5

## 口径（先说死，否则数出来的东西没法解释）

事件 = **一次「进入穿越成功」**。同一只票同一段周期里反复 成功→走弱→成功，
每次重新进入都算一次事件——因为每一次都是一个真实的交易决策点。

「**分歧**」= 进入成功之后，**第一段「成功后走弱」持续的交易日数**。状态机里
CROSS_WEAKENING 的入口就是「单日跌破 MA10」或「连续两日收在 MA5 下」，那正是
日线级别"结构开始松动"的那一段，不另造一个分歧定义。

走势按**归宿**分四类。不是拍的——状态机里穿越成功**只有这几条出路**：

    分歧后转强       走弱 → 回到穿越成功
    分歧后结束       走弱 → 周期结束（没再回来）
    直接结束         成功 → 周期结束，中间没经过走弱
    开启新一轮连板   这段周期还没结束，又走出新的 ≥4 连板（最强的那种归宿）

**数据末尾还没走完的事件单独列**（右删失），不混进上面四类——把"还没发生"
当成"不会发生"，统计就是假的。

## 必须知道的偏差

1. **幸存者偏差**：快照只有「当时在池子里的票」。当时进不了池的根本没有行，
   所以这里的收益偏高，分类占比也只在"今天的幸存者"这个宇宙里成立。
2. **同一只票贡献多次事件**，窗口还高度重叠，有效样本远小于打印的 N。
3. 状态判不出来的那天（未结算 / 陈值）退回最近一次有效状态，跟界面同口径；
   这类天**不计入**分歧天数，因为那天没有新事实。
"""
import argparse
import os
import statistics
import sys
from collections import Counter, defaultdict
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal                                  # noqa: E402
from app.models.leader_cycle import LeaderCycleSnapshot                 # noqa: E402
from app.services.leader_cycle_state_service import (                   # noqa: E402
    CROSS_FAILED, CROSS_SUCCESS, CROSS_WEAKENING, FADED, replay_series,
)
from app.services.suspension_service import (                           # noqa: E402
    load_suspensions_by_code, stock_calendar,
)
from app.services.trading_calendar import get_trading_days              # noqa: E402

# 分歧天数分档。1~2 天是"一次正常分歧"，5 天以上基本就是另一回事了
BUCKETS = ((1, "1天"), (2, "2天"), (3, "3天"), (5, "4-5天"), (10, "6-10天"), (10 ** 6, "10天以上"))


def _bucket(n: int) -> str:
    for hi, label in BUCKETS:
        if n <= hi:
            return label
    return BUCKETS[-1][1]


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if not a or not b or a <= 0 else (b / a - 1) * 100


def _med(xs) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-peak", type=int, default=0,
                    help="只看本轮连板 >= 这个数的周期（0=不限）")
    args = ap.parse_args()

    db = SessionLocal()
    rows = db.query(LeaderCycleSnapshot).order_by(LeaderCycleSnapshot.date).all()
    if not rows:
        print("没有龙头周期快照"); return
    by_code = defaultdict(list)
    for r in rows:
        by_code[r.stock_code].append(r)
    cal = get_trading_days(db) or []
    susp = load_suspensions_by_code(db, list(by_code), since=cal[0] if cal else None)
    all_days = sorted({r.date for r in rows})
    print(f"快照 {len(rows):,} 行 / {len(by_code)} 只票 / {all_days[0]} ~ {all_days[-1]}"
          f"（{len(all_days)} 个交易日）")

    events, censored = [], []
    for code, srows in by_code.items():
        days = sorted({r.date for r in srows})
        scal = stock_calendar(cal, susp.get(code))
        series = replay_series(srows, days, trading_days=scal)
        close = {r.date: r.latest_close for r in srows}
        cycle = {r.date: r.cycle_start_date for r in srows}
        peak = {r.date: (r.peak_board_count or 0) for r in srows}

        # 判不出来的那天退回最近一次有效状态——跟界面同口径
        st = {}
        for d in days:
            s = series[d]
            st[d] = s.state if s.state != "UNKNOWN" else s.last_valid_state

        prev = None
        for i, d in enumerate(days):
            cur = st[d]
            if cur == CROSS_SUCCESS and prev != CROSS_SUCCESS:
                if peak.get(d, 0) < args.min_peak:
                    prev = cur
                    continue
                ev = _follow(days[i:], st, close, cycle, d)
                ev.update(code=code, date=d, peak=peak.get(d, 0))
                (censored if ev["kind"] == "未走完" else events).append(ev)
            prev = cur

    if not events and not censored:
        print("没有「进入穿越成功」的事件"); return

    print(f"\n事件 {len(events) + len(censored)} 次"
          f"（{len({e['code'] for e in events + censored})} 只票）："
          f"走完 {len(events)} 次，数据末尾还没走完 {len(censored)} 次（不进下面的统计）")

    # ── 走势分类 ──────────────────────────────────────────────────────────
    print("\n走势分类（只统计走完的）：")
    print(f"  {'类型':<14}{'次数':>5}{'占比':>7}{'分歧天数中位':>14}{'5日':>8}{'10日':>8}")
    kinds = Counter(e["kind"] for e in events)
    for kind, n in kinds.most_common():
        es = [e for e in events if e["kind"] == kind]
        gap = _med([e["gap_days"] for e in es if e["gap_days"] is not None])
        print(f"  {kind:<14}{n:>5}{n / len(events) * 100:>6.0f}%"
              f"{('—' if gap is None else f'{gap:.1f}天'):>14}"
              f"{_fmt(_med([e['r5'] for e in es])):>8}{_fmt(_med([e['r10'] for e in es])):>8}")

    # ── 分歧天数分布 ──────────────────────────────────────────────────────
    gaps = [e["gap_days"] for e in events if e["gap_days"]]
    if gaps:
        print(f"\n出现过分歧的 {len(gaps)} 次，分歧持续几个交易日：")
        dist = Counter(_bucket(g) for g in gaps)
        for _, label in BUCKETS:
            if dist.get(label):
                n = dist[label]
                print(f"  {label:<10}{n:>4} 次 {n / len(gaps) * 100:>5.0f}%  "
                      + "█" * round(n / len(gaps) * 40))
        print(f"  中位 {statistics.median(gaps):.1f} 天 / 均值 {statistics.mean(gaps):.1f} 天 / "
              f"最长 {max(gaps)} 天")
        back = [e for e in events if e["gap_days"] and e["kind"] == "分歧后转强"]
        if back:
            print(f"  其中 {len(back)} 次分歧后重新转强，这些的分歧中位 "
                  f"{statistics.median([e['gap_days'] for e in back]):.1f} 天")

    # ── 进入成功后先稳几天才松动 ──────────────────────────────────────────
    hold = [e["success_days"] for e in events if e["success_days"] is not None]
    if hold:
        print(f"\n进入穿越成功后，在成功状态里待了几个交易日才发生变化："
              f"中位 {statistics.median(hold):.1f} 天，均值 {statistics.mean(hold):.1f} 天")

    if censored:
        print(f"\n还没走完的 {len(censored)} 次（数据末尾仍在进行）：")
        for e in sorted(censored, key=lambda x: x["date"])[-8:]:
            print(f"  {e['date']} {e['code']} 本轮{e['peak']}板，当前仍是 {e['last_state']}")

    print("\n⚠️ 幸存者偏差：快照只有「当时在池子里的票」，当时进不了池的根本没有行，"
          "\n   所以收益偏高、分类占比也只在「今天的幸存者」这个宇宙里成立。"
          "\n   同一只票贡献多次事件、窗口高度重叠，有效样本远小于打印的 N。"
          "\n   这里不给结论也不打分。")


def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.1f}%"


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


if __name__ == "__main__":
    main()
